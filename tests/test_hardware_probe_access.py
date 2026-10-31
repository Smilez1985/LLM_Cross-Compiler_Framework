"""
The access facts of hardware probe 3.3.0 (the group access of configs/hardware_probe_schema.yml): the SSH server
(OpenSSH, Dropbear) and its version, sudo (present, and whether the account may use it without a password prompt,
asked only with `sudo -n true`), ssh-keygen and its version, the init system and the package manager with DietPi's
dietpi-software. Most private DietPi boards run Dropbear, so the pairing takes its protection tier from these facts
and the board installer derives its plan from them.

The fixture boards are sysroots (HW_PROBE_SYSROOT) with fake programs: DietPi with Dropbear and sudo, Debian with
OpenSSH and no sudo, Alpine with OpenRC and Dropbear, and Windows. The .py and the .sh run on the same boards and must
agree; the .ps1 carries the same words. Only fixed words and versions come out: never a path, host name or user name.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from test_hardware_probe import (
    FIXTURES,
    MADE_UP_HOST,
    POSIX_ONLY,
    PROBE_PS1,
    PROBE_PY,
    PROBE_SH,
    RAW_SCHEMA,
    FakeWindows,
    available_shells,
    comparable,
    make_sysroot,
    probe,
    run_emitter,
    tool_dir,
    windows_runner,
    write,
)

from orchestrator.runtime.profile import (
    AccessFacts,
    ProfileStore,
    parse_profile,
    parse_profile_json,
)

SHELLS = available_shells()
NEEDS_SHELL = pytest.mark.skipif(not SHELLS, reason="no POSIX shell")
ACCESS_KEYS = [k for k, v in RAW_SCHEMA["keys"].items() if v["group"] == "access"]
MADE_UP_USER = "probe-fixture-user"   # a made-up account name that must never come out

DEBIAN_OS_RELEASE = ('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\n'
                     'VERSION_CODENAME=bookworm\nID=debian\n')
TCP_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n")


def program(root: Path, rel: str, script: str) -> None:
    """A fake program of the board (a shell script; the probe runs it by its sysroot path)."""
    write(root, rel, "#!/bin/sh\n" + script + "\n")
    os.chmod(root / rel.lstrip("/"), 0o755)


def fake_id(bindir: Path, uid: int) -> None:
    """`id -u` of the probe's account (the tool link of tool_dir replaced)."""
    path = bindir / "id"
    if path.exists() or path.is_symlink():
        path.unlink()
    path.write_text(f"#!/bin/sh\necho {uid}\n")
    path.chmod(0o755)


def process(root: Path, pid: int, comm: str, sockets=()) -> None:
    write(root, f"/proc/{pid}/comm", comm + "\n")
    (root / f"proc/{pid}/fd").mkdir(parents=True, exist_ok=True)
    write(root, f"/proc/{pid}/cmdline", f"/usr/sbin/{comm}\0-u\0{MADE_UP_USER}\0")
    for n, inode in enumerate(sockets, start=3):
        os.symlink(f"socket:[{inode}]", root / f"proc/{pid}/fd/{n}")


def dangling(root: Path, rel: str, target: str) -> None:
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def board(tmp_path: Path, kind: str) -> Path:
    """The fixture boards of the access facts."""
    if kind == "dietpi-dropbear":   # DietPi's default: Dropbear, the account with passwordless sudo
        root = make_sysroot(tmp_path, "rk3566")
        process(root, 1, "systemd")
        process(root, 412, "dropbear")
        program(root, "/usr/sbin/dropbear", 'echo "Dropbear v2022.83" >&2; exit 0')
        dangling(root, "/etc/systemd/system/multi-user.target.wants/dropbear.service",
                 "/lib/systemd/system/dropbear.service")
        program(root, "/usr/bin/sudo", '[ "$1 $2" = "-n true" ] && exit 0; exit 1')
        write(root, f"/etc/sudoers.d/{MADE_UP_USER}", f"{MADE_UP_USER} ALL=(ALL:ALL) NOPASSWD: ALL\n")
        program(root, "/usr/bin/apt-get", "exit 0")
        program(root, "/boot/dietpi/dietpi-software", "exit 0")
    elif kind == "debian-openssh":   # OpenSSH listening on port 22, Dropbear installed and running too, no sudo
        root = make_sysroot(tmp_path, "intel")
        write(root, "/etc/os-release", DEBIAN_OS_RELEASE)
        write(root, "/proc/net/tcp", TCP_HEADER +
              "   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 31337 1\n"
              "   1: 0100007F:0277 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4242 1\n")
        write(root, "/proc/net/tcp6", TCP_HEADER)
        process(root, 1, "systemd")
        process(root, 600, "sshd", sockets=(31337,))
        process(root, 601, "dropbear", sockets=(4242,))
        program(root, "/usr/sbin/sshd", 'echo "unknown option -- V" >&2; '
                                        'echo "OpenSSH_9.2p1 Debian-2+deb12u3, OpenSSL 3.0.15 3 Sep 2024" >&2; exit 1')
        program(root, "/usr/sbin/dropbear", 'echo "Dropbear v2022.83" >&2')
        program(root, "/usr/bin/ssh-keygen", "exit 1")
        program(root, "/usr/bin/ssh", 'echo "OpenSSH_9.2p1 Debian-2+deb12u3, OpenSSL 3.0.15 3 Sep 2024" >&2')
        program(root, "/usr/bin/apt-get", "exit 0")
    elif kind == "alpine-dropbear":   # OpenRC enables Dropbear; processes hidden (hidepid); sudo wants a password
        root = make_sysroot(tmp_path, "amd")
        dangling(root, "/etc/runlevels/default/dropbear", "/etc/init.d/dropbear")
        program(root, "/usr/sbin/dropbear", 'echo "Dropbear server v2024.85 https://matt.ucc.asn.au/dropbear" >&2')
        program(root, "/usr/bin/sudo", 'echo "sudo: a password is required" >&2; exit 1')
        program(root, "/sbin/apk", "exit 0")
    else:
        raise ValueError(kind)
    return root


EXPECTED = {
    "dietpi-dropbear": {"SSH_SERVER": "dropbear", "SSH_SERVER_VERSION": "2022.83", "SSH_SERVER_SOURCE": "process",
                        "SSH_SERVERS_INSTALLED": "dropbear", "SUDO": "nopasswd", "SSH_KEYGEN": "none",
                        "PACKAGE_MANAGER": "apt", "DIETPI_SOFTWARE": "present", "INIT_SYSTEM": "systemd"},
    "debian-openssh": {"SSH_SERVER": "openssh", "SSH_SERVER_VERSION": "9.2", "SSH_SERVER_SOURCE": "socket",
                       "SSH_SERVERS_INSTALLED": "openssh,dropbear", "SUDO": "absent", "SSH_KEYGEN": "openssh",
                       "SSH_KEYGEN_VERSION": "9.2", "PACKAGE_MANAGER": "apt", "INIT_SYSTEM": "systemd"},
    "alpine-dropbear": {"SSH_SERVER": "dropbear", "SSH_SERVER_VERSION": "2024.85", "SSH_SERVER_SOURCE": "service",
                        "SSH_SERVERS_INSTALLED": "dropbear", "SUDO": "password", "SSH_KEYGEN": "none",
                        "PACKAGE_MANAGER": "apk", "INIT_SYSTEM": "openrc"},
}


def access_values(profile):
    return {k: profile.get(k) for k in ACCESS_KEYS + ["INIT_SYSTEM"] if profile.get(k) is not None}


def py_run(root: Path, bindir: Path, tmp_path: Path) -> str:
    return run_emitter([sys.executable, "-I", str(PROBE_PY), "--no-toolchain"], root, bindir, tmp_path)


# --- the fixture boards --------------------------------------------------------------------------------------------

@POSIX_ONLY
class TestBoards:

    @pytest.mark.parametrize("kind", sorted(EXPECTED))
    def test_python_probe(self, tmp_path, kind):
        root = board(tmp_path, kind)
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        profile = parse_profile(py_run(root, bindir, tmp_path))
        assert access_values(profile) == EXPECTED[kind]
        assert profile.get("PROBE_TOOL_VERSION") == "3.3.0"
        assert not [w for w in profile.warnings if w.endswith(tuple(ACCESS_KEYS))]

    @NEEDS_SHELL
    @pytest.mark.parametrize("shell", SHELLS)
    @pytest.mark.parametrize("kind", sorted(EXPECTED))
    def test_sh_and_py_agree(self, tmp_path, kind, shell):
        root = board(tmp_path, kind)
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        sh_text = run_emitter(shell.split() + [str(PROBE_SH), "--no-toolchain"], root, bindir, tmp_path)
        py_text = py_run(root, bindir, tmp_path)
        assert access_values(parse_profile(sh_text)) == EXPECTED[kind]
        assert comparable(sh_text) == comparable(py_text)

    def test_the_access_record_of_the_dietpi_board(self, tmp_path):
        root = board(tmp_path, "dietpi-dropbear")
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        access = parse_profile(py_run(root, bindir, tmp_path)).access()
        assert access == AccessFacts(ssh_server="dropbear", ssh_server_version="2022.83", ssh_server_source="process",
                                     ssh_servers_installed=("dropbear",), sudo="nopasswd", ssh_keygen="none",
                                     init_system="systemd", package_manager="apt", dietpi_software=True)
        assert access.sudo_present is True and access.sudo_without_password is True
        assert access.keygen_signatures is False

    def test_the_access_record_of_the_debian_board(self, tmp_path):
        root = board(tmp_path, "debian-openssh")
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        access = parse_profile(py_run(root, bindir, tmp_path)).access()
        assert (access.ssh_server, access.sudo_present, access.sudo_without_password) == ("openssh", False, False)
        assert access.keygen_signatures is True and access.dietpi_software is False

    @pytest.mark.parametrize("kind", sorted(EXPECTED))
    def test_root_needs_no_sudo(self, tmp_path, kind):
        root = board(tmp_path, kind)
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 0)
        access = parse_profile(py_run(root, bindir, tmp_path)).access()
        assert access.sudo == "root" and access.sudo_without_password is True and access.sudo_present is None

    def test_without_the_socket_the_running_servers_decide_dropbear_first(self, tmp_path):
        # Both run and no listener can be told apart: Dropbear wins (OpenSSH key options would break Dropbear;
        # the other way round only costs protection).
        root = board(tmp_path, "debian-openssh")
        (root / "proc/net/tcp").unlink()
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        profile = parse_profile(py_run(root, bindir, tmp_path))
        assert (profile.get("SSH_SERVER"), profile.get("SSH_SERVER_SOURCE")) == ("dropbear", "process")
        assert profile.get("SSH_SERVER_VERSION") == "2022.83"

    def test_nothing_installed(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        profile = parse_profile(py_run(root, bindir, tmp_path))
        assert access_values(profile) == {"SSH_SERVER": "none", "SUDO": "absent", "SSH_KEYGEN": "none",
                                          "INIT_SYSTEM": "systemd"}
        assert profile.access().package_manager is None

    def test_old_ssh_keygen_cannot_check_signatures(self, tmp_path):
        root = board(tmp_path, "debian-openssh")
        program(root, "/usr/bin/ssh", 'echo "OpenSSH_7.9p1 Raspbian-10+deb10u2, OpenSSL 1.1.1d" >&2')
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        access = parse_profile(py_run(root, bindir, tmp_path)).access()
        assert access.ssh_keygen_version == "7.9" and access.keygen_signatures is False

    def test_ssh_keygen_without_a_version(self, tmp_path):
        root = board(tmp_path, "debian-openssh")
        (root / "usr/bin/ssh").unlink()
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        profile = parse_profile(py_run(root, bindir, tmp_path))
        assert profile.get("SSH_KEYGEN") == "openssh" and "SSH_KEYGEN_VERSION" not in profile
        assert "not_determined:SSH_KEYGEN_VERSION" in profile.warnings
        assert profile.access().keygen_signatures is None

    def test_sudo_is_asked_only_with_n_and_never_reads_stdin(self, tmp_path):
        root = board(tmp_path, "dietpi-dropbear")
        log = tmp_path / "sudo-args.txt"
        program(root, "/usr/bin/sudo", f'echo "$*" >> "{log}"; read -r line && echo READ >> "{log}"; exit 0')
        bindir = tool_dir(tmp_path)
        fake_id(bindir, 1000)
        py_run(root, bindir, tmp_path)
        if SHELLS:
            run_emitter(SHELLS[0].split() + [str(PROBE_SH), "--no-toolchain"], root, bindir, tmp_path)
        assert set(log.read_text().splitlines()) == {"-n true"}

    def test_no_account_host_or_path_comes_out(self, tmp_path):
        for kind in EXPECTED:
            root = board(tmp_path / kind, kind)
            bindir = tool_dir(tmp_path / kind)
            fake_id(bindir, 1000)
            text = py_run(root, bindir, tmp_path)
            assert MADE_UP_USER not in text and MADE_UP_HOST not in text
            for key in ACCESS_KEYS:
                value = parse_profile(text).get(key) or ""
                assert "/" not in value and "\\" not in value, (key, value)


# --- Windows -------------------------------------------------------------------------------------------------------

def windows_access_runner(sshd_state=None, keygen=True, ssh_banner="OpenSSH_for_Windows_9.5p1, LibreSSL 3.8.2"):
    base = windows_runner()

    def run(cmd, timeout):
        if cmd[:3] == ["sc.exe", "query", "sshd"]:
            if sshd_state is None:
                return 1060, "[SC] EnumQueryServicesStatus:OpenService FAILED 1060", ""
            return 0, f"SERVICE_NAME: sshd\n        STATE              : 4  {sshd_state}\n", ""
        if cmd[0] == "ssh-keygen":
            return (1, "", "unknown option -- ?\nusage: ssh-keygen ...") if keygen else (-1, "", "not found")
        if cmd[:2] == ["ssh", "-V"]:
            return (0, "", ssh_banner) if keygen else (-1, "", "not found")
        return base(cmd, timeout)
    return run


class TestWindows:

    def test_openssh_server_running(self):
        values = probe.collect(system="Windows", windows_api=FakeWindows(), env={}, root=os.sep,
                               run=windows_access_runner("RUNNING"))
        profile = parse_profile(probe.render_text(values))
        assert access_values(profile) == {"SSH_SERVER": "openssh", "SSH_SERVER_SOURCE": "process",
                                          "SSH_SERVERS_INSTALLED": "openssh", "SUDO": "other",
                                          "SSH_KEYGEN": "openssh", "SSH_KEYGEN_VERSION": "9.5",
                                          "PACKAGE_MANAGER": "other", "INIT_SYSTEM": "scm"}
        access = profile.access()
        assert access.sudo_present is None and access.keygen_signatures is True

    def test_mostly_other_without_the_openssh_features(self):
        values = probe.collect(system="Windows", windows_api=FakeWindows(), env={}, root=os.sep,
                               run=windows_access_runner(None, keygen=False))
        profile = parse_profile(probe.render_text(values))
        assert access_values(profile) == {"SSH_SERVER": "none", "SUDO": "other", "SSH_KEYGEN": "none",
                                          "PACKAGE_MANAGER": "other", "INIT_SYSTEM": "scm"}

    def test_ps1_writes_the_same_words(self):
        text = PROBE_PS1.read_text(encoding="utf-8")
        for line in ("Put 'SSH_SERVER' 'openssh'", "Put 'SSH_SERVER' 'none'", "Put 'SSH_SERVER_SOURCE' 'process'",
                     "Put 'SSH_SERVER_SOURCE' 'service'", "Put 'SSH_SERVER_SOURCE' 'binary'",
                     "Put 'SSH_SERVERS_INSTALLED' 'openssh'", "Put 'SUDO' 'other'", "Put 'SSH_KEYGEN' 'openssh'",
                     "Put 'SSH_KEYGEN' 'none'", "Put 'PACKAGE_MANAGER' 'other'",
                     "Get-Service -Name 'sshd'", "System32\\OpenSSH\\sshd.exe",
                     "OpenSSH_(?:for_Windows_)?([0-9]{1,2}\\.[0-9]{1,2})"):
            assert line in text, line
        assert "Invoke-Tool 'sudo'" not in text and "sudo -n" not in text   # Windows: no sudo is ever run


# --- schema, tables, old profiles ----------------------------------------------------------------------------------

def sh_list(name):
    text = PROBE_SH.read_text(encoding="utf-8")
    items = []
    for line in text.splitlines():
        if line.startswith(name + '="'):
            items += line.split('"', 1)[1].rsplit('"', 1)[0].replace("$" + name, "").split()
    return items


class TestSchema:

    def test_the_access_keys_are_never_required_or_booleans(self):
        assert ACCESS_KEYS == ["SSH_SERVER", "SSH_SERVER_VERSION", "SSH_SERVER_SOURCE", "SSH_SERVERS_INSTALLED",
                               "SUDO", "SSH_KEYGEN", "SSH_KEYGEN_VERSION", "PACKAGE_MANAGER", "DIETPI_SOFTWARE"]
        for key in ACCESS_KEYS:
            spec = RAW_SCHEMA["keys"][key]
            assert spec["type"] != "bool" and not spec.get("required") and spec["since"] == "3.3.0", key
            assert spec["type"] in ("enum", "version", "list"), key   # fixed words and versions only

    def test_python_and_sh_tables_are_the_schema(self):
        t = RAW_SCHEMA["tables"]
        assert probe.SSH_SERVER_NAMES == t["ssh_server_names"]
        assert probe.SSH_SERVER_BINARIES == t["ssh_server_binaries"]
        assert probe.SSH_SERVER_SERVICES == t["ssh_server_services"]
        assert probe.SUDO_PATHS == tuple(t["sudo_paths"])
        assert probe.SSH_KEYGEN_PATHS == tuple(t["ssh_keygen_paths"])
        assert probe.SSH_CLIENT_PATHS == tuple(t["ssh_client_paths"])
        assert probe.PACKAGE_MANAGERS == t["package_managers"]
        assert probe.DIETPI_SOFTWARE_PATH == t["dietpi_software_path"]
        from test_hardware_probe import sh_table
        assert sh_table("ssh_server_name") == t["ssh_server_names"]
        pairs = lambda name: dict(item.rsplit(":", 1) for item in sh_list(name))
        assert pairs("SSH_SERVER_BINARIES") == t["ssh_server_binaries"]
        assert list(pairs("SSH_SERVER_BINARIES")) == list(t["ssh_server_binaries"])
        assert pairs("SSH_SERVER_SERVICES") == t["ssh_server_services"]
        assert pairs("PACKAGE_MANAGERS") == t["package_managers"]
        assert list(pairs("PACKAGE_MANAGERS")) == list(t["package_managers"])
        assert sh_list("SUDO_PATHS") == t["sudo_paths"]
        assert sh_list("SSH_KEYGEN_PATHS") == t["ssh_keygen_paths"]
        assert sh_list("SSH_CLIENT_PATHS") == t["ssh_client_paths"]
        assert f"DIETPI_SOFTWARE_PATH={t['dietpi_software_path']}" in PROBE_SH.read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("schema2_*.txt")))
    def test_older_profiles_stay_readable_without_access(self, tmp_path, name):
        profile = parse_profile((FIXTURES / name).read_text(encoding="utf-8"))
        assert profile.access() is None
        assert not [k for k in profile.values if k in ACCESS_KEYS]
        store = ProfileStore(tmp_path / "state")
        store.save_device("0123456789abcdef", profile)
        assert store.load_device("0123456789abcdef").access() is None

    def test_a_3_2_0_profile_has_no_access_record(self, dietpi_text):
        text = "".join(line for line in dietpi_text.splitlines(keepends=True)
                       if line.split("=", 1)[0] not in ACCESS_KEYS)
        profile = parse_profile(text.replace("PROBE_TOOL_VERSION=3.3.0", "PROBE_TOOL_VERSION=3.2.0"))
        assert profile.get("PROBE_TOOL_VERSION") == "3.2.0" and profile.access() is None

    def test_a_3_3_0_profile_round_trips(self, dietpi_text):
        profile = parse_profile(dietpi_text)
        assert parse_profile_json(json.dumps(profile.as_dict())).access() == profile.access()
        assert profile.stable().access() == profile.access()   # the access keys are stable: kept in the store

    def test_values_outside_the_schema_are_refused(self, dietpi_text):
        from orchestrator.runtime.profile import ProfileError
        for bad in ("SSH_SERVER=telnet", "SUDO=yes", "PACKAGE_MANAGER=/usr/bin/apt", "SSH_SERVERS_INSTALLED=lsh"):
            with pytest.raises(ProfileError):
                parse_profile(dietpi_text.replace("SSH_SERVER=dropbear", bad))

    def test_the_quick_probe_reads_no_access_facts(self, tmp_path):
        values = probe.collect(root=str(make_sysroot(tmp_path, "rk3566")), sections=("memory", "gpu"),
                               run=lambda cmd, timeout: (-1, "", "not found"))
        assert not [k for k in values if k in ACCESS_KEYS]
        assert "access" in probe.SECTIONS


@pytest.fixture
def dietpi_text(tmp_path):
    """A probe 3.3.0 file of the DietPi board with Dropbear and sudo (written by the .py)."""
    if os.name != "posix":
        pytest.skip("the fixture boards are POSIX sysroots")
    root = board(tmp_path / "board", "dietpi-dropbear")
    bindir = tool_dir(tmp_path / "board")
    fake_id(bindir, 1000)
    return py_run(root, bindir, tmp_path)


# --- the facts as labels: llmcf scan, the Builder tab, MCP -----------------------------------------------------------

class TestLabels:

    def test_scan_prints_one_access_line(self, dietpi_text):
        from orchestrator import cli as cli_module
        lines = cli_module._profile_summary(parse_profile(dietpi_text))
        access = [line for line in lines if line.startswith("Access:")]
        assert access == [("Access: SSH server Dropbear 2022.83, running, sudo without a password, no ssh-keygen, "
                           "init systemd, packages through apt, dietpi-software")]

    def test_an_old_profile_prints_no_access_line(self):
        from orchestrator import cli as cli_module
        lines = cli_module._profile_summary(parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text()))
        assert not [line for line in lines if line.startswith("Access:")]

    def test_every_access_text_has_its_german_twin(self):
        from orchestrator.utils.l10n_deploy import TEXTS
        en = {k for k in TEXTS["en"] if k.startswith("deploy.access.")}
        assert en == {k for k in TEXTS["de"] if k.startswith("deploy.access.")}
        facts = [AccessFacts(ssh_server=s, ssh_server_source=src, ssh_servers_installed=("openssh", "dropbear"),
                             sudo=sudo, ssh_keygen=kg, ssh_keygen_version=v, init_system="openrc",
                             package_manager="apk", dietpi_software=True)
                 for s, src, sudo, kg, v in (("openssh", "socket", "root", "openssh", "9.2"),
                                             ("dropbear", "service", "password", "openssh", "8.0"),
                                             ("other", "binary", "absent", "openssh", None),
                                             ("none", None, "other", "none", None),
                                             ("openssh", "process", "nopasswd", None, None))]
        used = {key for f in facts for key, _ in f.parts()} | {"deploy.access.line"}
        assert used == en

    def test_the_german_line(self, dietpi_text):
        from orchestrator.runtime.probe_service import access_line
        line = access_line(parse_profile(dietpi_text), "de")
        assert line.startswith("Zugang: SSH-Server Dropbear 2022.83, läuft, sudo ohne Passwort")

    def test_the_builder_tab_logs_the_line(self):
        source = (Path(__file__).resolve().parents[1] / "orchestrator" / "gui" / "builder_tab.py").read_text(
            encoding="utf-8")
        load = source.split("def _load_probe_config", 1)[1].split("\n    def ", 1)[0]
        assert "probe_access_line(profile)" in load and "self._log(access_line)" in load

    def test_the_mcp_probe_tool_shows_the_facts(self, make_framework, tmp_path, dietpi_text):
        from orchestrator.runtime.probe_service import HardwareProbes
        from orchestrator.tools.policy import ToolPolicy
        from orchestrator.tools.registry import ToolContext
        fm = make_framework(initialize=False)
        assert fm.initialize()
        fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fm.tool_registry.list()]))
        path = tmp_path / "board.txt"
        path.write_text(dietpi_text, encoding="utf-8")
        probe_id = HardwareProbes(Path(fm.config.user_state_dir)).import_file(path, "Zero 3W")
        out = fm.tool_registry.call("get_hardware_probe", {"probe_id": probe_id}, ToolContext("ditto", session="s"))
        assert out.access.ssh_server == "dropbear" and out.access.sudo == "nopasswd"
        assert out.access.sudo_without_password is True and out.access.keygen_signatures is False
        assert out.access.dietpi_software is True and out.access.package_manager == "apt"
        assert out.access.summary.startswith("Access: SSH server Dropbear 2022.83")
        schema = fm.tool_registry.get("get_hardware_probe").output_model.model_json_schema()
        assert "access" in schema["properties"]
