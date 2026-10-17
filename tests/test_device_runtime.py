"""
The device runtime (orchestrator/runtime/device/): cross plans from a device's own probe profile, the tarball, the
start script and the systemd unit, the deploy settings, the deployment over SSH with the checks on the device, the
CLI around it, and the GUI's SSH probe.

No network, no Docker and no real board. The fake device runs every command for real, the way sshd does (the login
shell with -c and the exact command string), in a temporary home folder, with stand-ins for id, uname and sudo and
with the absolute systemd paths moved into a fake root. The programs in its tarball are small shell scripts that
answer like llama-server and llama-bench. The fixture profiles are an RK3566 board on DietPi (systemd), the same board
on Alpine Linux (musl, OpenRC) and an RK3588 board on Devuan (glibc, SysV init, no systemd).
"""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.runtime import server_policy
from orchestrator.runtime.build.executor import BuildExecutor, BuildResult
from orchestrator.runtime.build.recipe import PlanError
from orchestrator.runtime.build.store import BuildStore
from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.device import launcher
from orchestrator.runtime.device.devices import DeviceNotFound, resolve_device, split_address
from orchestrator.runtime.device.package import (DeviceRuntimeStore, PackageError, package_runtime,
                                                 plan_device_runtime)
from orchestrator.runtime.device.remote import (RemoteError, RemoteShell, board_cleanup_plan, deploy_runtime,
                                                older_builds, read_facts, remove_older_builds, verify_on_device)
from orchestrator.runtime.device.settings import DeploySettings, SettingsError
from orchestrator.runtime.profile import ProfileStore, parse_profile, read_profile

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "hardware_probe"
GOLDEN = ROOT / "tests" / "fixtures" / "build_plans"
CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source
HOST = ("linux", "x86_64")
RK3566 = read_profile(PROBES / "schema2_rk3566_dietpi.txt")            # glibc 2.36, systemd
ALPINE = read_profile(PROBES / "schema2_rk3566_alpine_musl.txt")       # musl 1.2.5, OpenRC
DEVUAN = read_profile(PROBES / "schema2_rk3588_devuan_sysvinit.txt")   # glibc 2.36, SysV init
POSIX = pytest.mark.skipif(os.name != "posix", reason="the start script and the fake device need a POSIX shell")
SHELL = shutil.which("dash") or shutil.which("sh")
# The start script runs under every POSIX shell a board may have: dash (Debian, DietPi), bash, BusyBox ash (Alpine).
SHELLS = [s for s in (shutil.which("dash"), shutil.which("bash")) if s] + \
    ([f"{shutil.which('busybox')} sh"] if shutil.which("busybox") else [])
CONFIRMED = "2026-10-11T10:00:00Z"
LICENCE = "MIT License\n\nCopyright (c) 2023-2026 The ggml authors\n\nPermission is hereby granted, free of charge...\n"


def changed(profile, **values):
    data = dict(profile.values)
    data.update(values)
    return parse_profile("".join(f"{k}={v}\n" for k, v in data.items() if v is not None))


def plan_for(profile=RK3566, **kw):
    return plan_device_runtime(profile, catalog=CATALOG, host=kw.pop("host", HOST), **kw)


# --- 1. plans from the device's own profile -------------------------------------------------------------------

class TestPlans:

    def test_rk3566_on_dietpi_gets_a_glibc_matched_cross_build(self):
        plan = plan_for(RK3566)
        assert (plan.purpose, plan.placement, plan.backend) == ("device-runtime", "cross", "cpu")
        assert plan.builder["base"] == "debian-12" and plan.builder["libc_version"] == "2.36"
        assert "@sha256:" in plan.builder["image"]
        options = plan.options()
        assert options["GGML_CPU_ARM_ARCH"] == "armv8.2-a+dotprod+fp16" and options["GGML_NATIVE"] == "OFF"
        assert options["CMAKE_EXE_LINKER_FLAGS"] == "-static-libstdc++ -static-libgcc"
        assert options["LLAMA_SUBPROCESS"] == "OFF" and options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
        assert options["CMAKE_INSTALL_RPATH"] == "$ORIGIN;$ORIGIN/../lib"
        assert plan.verify["glibc_max"] == "2.36" and plan.verify["static_libstdcxx"] is True
        assert plan.verify["runtime_libs"] == ["libgomp.so.1"] and plan.verify["expect_sdot"] is True
        assert {"llama-server", "llama-bench"} <= set(plan.targets)

    def test_the_toolchain_file_and_options_match_the_golden_plan(self):
        golden = json.loads((GOLDEN / "rk3566_dietpi_cross.json").read_text())
        step = plan_for(RK3566).steps[0]
        assert step.toolchain_file == golden["steps"][0]["toolchain_file"]
        assert "set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)" in step.toolchain_file
        assert dict(step.options) == golden["steps"][0]["options"]

    def test_an_alpine_board_gets_a_musl_build(self):
        plan = plan_for(ALPINE)
        assert plan.builder["base"].startswith("alpine-") and plan.builder["libc"] == "musl"
        assert plan.target["triple"] == "aarch64-alpine-linux-musl" and plan.steps[0].compiler == "clang"
        assert "set(CMAKE_SYSROOT /opt/sysroot)" in plan.steps[0].toolchain_file
        assert plan.options()["GGML_OPENMP"] == "OFF" and "runtime_libs" not in plan.verify
        assert "glibc_max" not in plan.verify and plan.verify["static_libstdcxx"] is True

    def test_the_init_system_does_not_change_the_build(self):
        assert plan_for(DEVUAN).options() == plan_for(RK3566).options()

    @pytest.mark.parametrize("libc_version, base", [("2.31", "debian-11"), ("2.35", "ubuntu-22.04"),
                                                    ("2.39", "ubuntu-24.04"), ("2.41", "debian-13")])
    def test_the_glibc_floor_picks_the_newest_base_not_newer_than_the_board(self, libc_version, base):
        assert plan_for(changed(RK3566, LIBC_VERSION=libc_version)).builder["base"] == base

    def test_a_board_older_than_every_base_or_without_libc_version_gets_no_plan(self):
        with pytest.raises(PlanError, match="older than every builder base"):
            plan_for(changed(RK3566, LIBC_VERSION="2.28"))
        with pytest.raises(PlanError, match="no LIBC_VERSION"):
            plan_for(changed(RK3566, LIBC_VERSION=None))
        with pytest.raises(PlanError, match="older than every builder base"):
            plan_for(changed(ALPINE, LIBC_VERSION="1.2.3"))

    def test_only_linux_devices_and_cpu_builds(self):
        with pytest.raises(PlanError, match="Linux devices"):
            plan_for(read_profile(PROBES / "schema2_windows_apu.txt"))
        with pytest.raises(PlanError, match="CPU build"):
            plan_for(RK3566, backend="vulkan")

    def test_the_build_host_does_not_matter_for_the_board_options(self):
        arm_host = plan_for(RK3566, host=("linux", "arm64"))
        assert arm_host.steps[0].toolchain_file is None  # the builder compiles for its own architecture
        assert arm_host.options()["GGML_CPU_ARM_ARCH"] == plan_for(RK3566).options()["GGML_CPU_ARM_ARCH"]


# --- 2. the shared policy ---------------------------------------------------------------------------------------

class TestPolicy:

    def test_underscore_spellings_of_forbidden_options_are_caught(self):
        """llama.cpp reads --a_b as --a-b (common/arg.cpp), so the shared check must too."""
        assert server_policy.forbidden_in(["x", "--tools_runtime", "y", "--mcp_servers_config", "f"]) == [
            "--mcp-servers-config", "--tools-runtime"]
        assert server_policy.forbidden_in(["x", "--media_path=/"]) == ["--media-path"]
        with pytest.raises(server_policy.PolicyError):
            server_policy.check_command(["llama-server", "--slot_save_path", "/tmp"])
        assert server_policy.forbidden_in(["x", "-c", "4096", "--ctx_size", "2"]) == []

    @pytest.mark.parametrize("spelling", [
        # Other names llama.cpp gives the forbidden download option -hf/--hf-repo at the pin (common/arg.cpp)
        "-hfr",
        # and the options next to it that download a model or projector, or put a token on the command line
        "-hff", "--hf-file", "-hft", "--hf-token", "-dr", "--docker-repo", "-mmu", "--mmproj-url",
        "--spec-draft-hf", "-hfd", "-hfrd", "--hf-repo-draft"])
    def test_every_spelling_of_a_download_option_is_forbidden(self, spelling):
        """The shared list says no forbidden option reaches a command line "in any spelling llama.cpp accepts"; a
        launcher that only checked -hf and --hf-repo would let -hfr download a model from the network."""
        assert server_policy.forbidden_in(["llama-server", spelling, "x"]) == [spelling]
        assert spelling in launcher.render_start_script()

    def test_the_tuning_options_are_never_forbidden_ones(self):
        allowed = {n for names in server_policy.START_SCRIPT_OPTIONS for n in names}
        assert not allowed & server_policy.FORBIDDEN_FLAGS
        assert not allowed & {"--host", "--port", "--api-key-file", "-m", "--model", "--api-key"}

    def test_the_cleared_environment_is_the_same_everywhere(self):
        assert server_policy.posix_cleared_env("/home/u") == {"HOME": "/home/u", "PATH": "/usr/bin:/bin",
                                                              "LANG": "C", "LC_ALL": "C"}

    def test_the_start_scripts_server_line_keeps_the_rules_of_dittos_server(self, monkeypatch):
        """One policy for every launcher: the start script's server line is built from the same constants as Ditto's
        server and held to the same check_server_command; a policy the line would break stops the generation."""
        line = next(ln for ln in launcher.render_start_script().splitlines() if '"$SERVER" -m' in ln)
        assert line.strip().endswith(" ".join(server_policy.SERVER_FIXED) + ' "$@"')
        assert f"--host {server_policy.LOOPBACK} " in line and '--api-key-file "$KEY"' in line
        monkeypatch.setattr(launcher, "SERVER_FIXED", ("--no-ui", "--offline"))
        with pytest.raises(server_policy.PolicyError, match="--no-slots"):
            launcher.render_start_script()

    def test_the_tools_check_on_the_board_is_the_shared_one(self):
        """The checks on a board build their --tools check with the policy's tools_check_args, never their own
        argument list."""
        import inspect
        from orchestrator.runtime.device import remote
        source = inspect.getsource(remote.verify_on_device)
        assert "tools_check_args(" in source and "SERVER_TOOLS_CHECK" not in inspect.getsource(remote)


# --- 3. the start script ------------------------------------------------------------------------------------------

FAKE_SERVER = """#!/bin/sh
case "$1" in
  --version) echo "version: 0.6.0 (build @BUILD@, commit @COMMIT@)" >&2; echo "built with fake cc" >&2; exit 0 ;;
  --tools) echo "error: subprocess is not enabled on this build" >&2; exit 1 ;;
esac
for a in "$@"; do printf 'ARG=%s\\n' "$a"; done
env | sort | sed 's/^/ENV=/'
"""
FAKE_BENCH = """#!/bin/sh
case "$1" in
  --version) echo "version: 0.6.0 (build @BUILD@, commit @COMMIT@)" >&2; exit 0 ;;
  --list-devices) echo "Available devices:"; echo "  (none)"; exit 0 ;;
  -m) [ -f "$2" ] || { echo "error: failed to load model $2" >&2; exit 1; }
      echo "llama_model_load: loading" >&2
      echo '[{"n_prompt": 64, "n_gen": 0, "avg_ts": 21.5}, {"n_prompt": 0, "n_gen": 16, "avg_ts": 4.25}]'
      exit 0 ;;
esac
exit 2
"""


def fake_program(text, build=None, commit=None):
    return text.replace("@BUILD@", str(build or PIN.build_number)).replace("@COMMIT@", commit or PIN.short_commit)


def install_folder(home: Path, id16="0123456789abcdef", server=None) -> Path:
    folder = home / launcher.INSTALL_REL / id16
    (folder / "bin").mkdir(parents=True)
    (folder / launcher.START_SCRIPT).write_text(launcher.render_start_script())
    (folder / "bin" / "llama-server").write_text(server or fake_program(FAKE_SERVER))
    (folder / "bin" / "llama-server").chmod(0o755)
    os.symlink(id16, home / launcher.INSTALL_REL / "current")
    return folder


@POSIX
class TestStartScript:

    @pytest.fixture(params=SHELLS or ["sh"], autouse=True)
    def shell(self, request):
        self.shell_cmd = request.param.split()
        return self.shell_cmd

    @pytest.fixture
    def home(self, tmp_path):
        home = tmp_path / "board-home"
        home.mkdir()
        (home / "model.gguf").write_bytes(b"GGUF")
        return home

    def run(self, home, *args, env=None):
        script = home / launcher.INSTALL_REL / "current" / launcher.START_SCRIPT
        full = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LLAMA_ARG_TOOLS": "all", "LLAMA_ARG_HOST": "0.0.0.0",
                "GGML_BACKEND_PATH": "/tmp/evil.so", "HTTPS_PROXY": "http://proxy.invalid:3128"}
        full.update(env or {})
        return subprocess.run([*self.shell_cmd, str(script), *args], env=full, capture_output=True, text=True,
                              timeout=60, cwd=str(home))

    @staticmethod
    def args_env(out):
        args = [line[4:] for line in out.splitlines() if line.startswith("ARG=")]
        env = dict(line[4:].split("=", 1) for line in out.splitlines() if line.startswith("ENV="))
        return args, env

    def test_the_script_is_posix_sh_and_names_the_policy(self, tmp_path):
        text = launcher.render_start_script()
        path = tmp_path / "s.sh"
        path.write_text(text)
        assert subprocess.run([*self.shell_cmd, "-n", str(path)], capture_output=True).returncode == 0
        assert text.startswith("#!/bin/sh\n") and "set -eu" in text and "umask 077" in text
        for flag in server_policy.FORBIDDEN_FLAGS:
            assert re.search(rf"(^|[|(\s]){re.escape(flag)}([|)]|$)", text, re.M), flag

    def test_serve_binds_loopback_with_the_key_and_a_cleared_environment(self, home):
        install_folder(home)
        res = self.run(home, "serve", str(home / "model.gguf"), "-c", "4096", "--threads", "4", "-C", "0xf0",
                       "--cpu-strict", "1", "-ngl", "auto", "-fa", "on", "--alias", "qwen-2b")
        assert res.returncode == 0, res.stderr
        args, env = self.args_env(res.stdout)
        key = str(home / launcher.KEY_REL)
        assert args[:9] == ["-m", str(home / "model.gguf"), "--host", "127.0.0.1", "--port", "8080",
                            "--api-key-file", key, "--no-ui"]
        assert args[9:12] == ["--no-slots", "--jinja", "--offline"]
        assert args[12:] == ["-c", "4096", "--threads", "4", "-C", "0xf0", "--cpu-strict", "1", "-ngl", "auto",
                             "-fa", "on", "--alias", "qwen-2b"]
        assert args.count("--host") == 1 and args.count("--api-key-file") == 1
        assert env["HOME"] == str(home) and env["PATH"] == "/usr/bin:/bin" and env["LANG"] == env["LC_ALL"] == "C"
        assert set(env) <= {"HOME", "PATH", "LANG", "LC_ALL", "PWD", "OLDPWD", "SHLVL", "_"}, env
        # The key was created on first use: only the user can read it, never anyone else.
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(Path(key).parent).st_mode) == 0o700
        assert re.fullmatch(r"[0-9a-f]{64}\n", Path(key).read_text())

    def test_the_port_can_be_changed_but_only_to_an_unprivileged_one(self, home):
        install_folder(home)
        res = self.run(home, "serve", str(home / "model.gguf"), "--port", "9090")
        args, _env = self.args_env(res.stdout)
        assert res.returncode == 0 and args[args.index("--port") + 1] == "9090" and args.count("--port") == 1
        res = self.run(home, "serve", str(home / "model.gguf"), "--port", "80")
        assert res.returncode == 2 and "not a valid value" in res.stderr

    @pytest.mark.parametrize("extra", [["--tools", "all"], ["--tools_runtime", "x"], ["--tools=all"],
                                       ["--api-key", "k"], ["-hf", "a/b"], ["--rpc", "h:1"], ["--agent"],
                                       ["--mcp-servers-json", "{}"], ["--webui"], ["--slot-save-path", "/tmp"],
                                       ["--media_path", "/"]])
    def test_forbidden_options_are_refused_by_the_shared_list(self, home, extra):
        install_folder(home)
        res = self.run(home, "serve", str(home / "model.gguf"), *extra)
        assert res.returncode == 2 and "forbidden by the framework's server policy" in res.stderr
        assert "ARG=" not in res.stdout

    @pytest.mark.parametrize("extra, message", [
        (["--host", "0.0.0.0"], "not an option this script passes on"),
        (["-m", "/etc/passwd"], "not an option this script passes on"),
        (["--api-key-file", "/tmp/k"], "not an option this script passes on"),
        (["--log-file", "/tmp/x"], "not an option this script passes on"),
        (["--ctx-size=4096"], "separate argument"),
        (["-c", "many"], "not a valid value"),
        (["-c"], "needs a value"),
        (["--alias", "-x"], "not a valid value"),
        (["--alias", "a b"], "not a valid value"),
        (["-ngl", "99999"], "not a valid value"),
        (["-fa", "yes"], "not a valid value"),
    ])
    def test_other_options_and_bad_values_are_refused(self, home, extra, message):
        install_folder(home)
        res = self.run(home, "serve", str(home / "model.gguf"), *extra)
        assert res.returncode == 2 and message in res.stderr, res.stderr
        assert "ARG=" not in res.stdout

    def test_the_model_must_be_a_readable_file_not_an_option(self, home):
        install_folder(home)
        for model, message in (("--tools", "start with '-'"), (str(home / "missing.gguf"), "no model file"),
                               ("", "start with '-'")):
            res = self.run(home, "serve", model)
            assert res.returncode == 2 and message in res.stderr
        res = self.run(home, "serve")
        assert res.returncode == 2 and "usage" in res.stderr

    def test_keygen_creates_the_key_once_under_umask_077(self, home):
        install_folder(home)
        res = self.run(home, "keygen", env={"LLAMA_ARG_API_KEY": "leaked"})
        key = home / launcher.KEY_REL
        assert res.returncode == 0 and res.stdout.strip() == str(key)
        first = key.read_text()
        assert self.run(home, "keygen").returncode == 0 and key.read_text() == first
        assert stat.S_IMODE(key.stat().st_mode) == 0o600 and "leaked" not in first

    def test_version_runs_the_server_of_its_own_folder_with_a_cleared_environment(self, home, tmp_path):
        install_folder(home)
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        (decoy / "llama-server").write_text("#!/bin/sh\necho DECOY\n")
        (decoy / "llama-server").chmod(0o755)
        res = self.run(home, "version", env={"PATH": f"{decoy}:/usr/bin:/bin"})
        assert res.returncode == 0 and f"build {PIN.build_number}" in res.stderr and "DECOY" not in res.stdout

    def test_home_must_be_an_absolute_path(self, home):
        install_folder(home)
        assert self.run(home, "keygen", env={"HOME": "relative"}).returncode == 2

    @pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("cc"),
                        reason="needs /proc and a C compiler for a stand-in server binary")
    def test_start_status_stop(self, home, tmp_path):
        """start runs the server in the background; status and stop recognise it by /proc/<pid>/exe."""
        source = tmp_path / "sleeper.c"
        source.write_text("#include <unistd.h>\nint main(void) { sleep(60); return 0; }\n")
        folder = install_folder(home)
        built = subprocess.run(["cc", str(source), "-o", str(folder / "bin" / "llama-server")], capture_output=True)
        assert built.returncode == 0, built.stderr
        assert self.run(home, "status").returncode == 3
        res = self.run(home, "start", str(home / "model.gguf"), "-c", "2048")
        assert res.returncode == 0 and "started" in res.stdout, res.stderr
        pidfile = home / launcher.STATE_REL / "llama-server.pid"
        for _ in range(50):
            if self.run(home, "status").returncode == 0:
                break
            time.sleep(0.1)
        status = self.run(home, "status")
        assert status.returncode == 0 and "running" in status.stdout
        assert stat.S_IMODE(pidfile.stat().st_mode) & 0o077 == 0
        again = self.run(home, "start", str(home / "model.gguf"))
        assert again.returncode == 2 and "already running" in again.stderr
        assert self.run(home, "start", str(home / "model.gguf"), "--tools", "all").returncode == 2
        stopped = self.run(home, "stop")
        assert stopped.returncode == 0 and "stopped" in stopped.stdout and not pidfile.exists()
        assert self.run(home, "status").returncode == 3


# --- 4. the systemd unit --------------------------------------------------------------------------------------------

class TestSystemdUnit:

    def unit(self, **kw):
        values = dict(user="dietpi", home="/home/dietpi", model="/home/dietpi/models/qwen.gguf", port=8080)
        values.update(kw)
        return launcher.render_systemd_unit(**values)

    def test_the_unit_runs_the_start_script_as_the_user_on_loopback(self):
        text = self.unit()
        assert "User=dietpi\n" in text and "WorkingDirectory=/home/dietpi\n" in text
        assert ("ExecStart=/bin/sh /home/dietpi/.local/share/llm-framework/llama.cpp/current/llama-server.sh serve "
                "/home/dietpi/models/qwen.gguf --port 8080\n") in text
        assert "Environment" not in text, "no Environment= or EnvironmentFile=: the script clears it anyway"
        for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=read-only", "IPAddressDeny=any",
                     "IPAddressAllow=localhost", "WantedBy=multi-user.target"):
            assert line in text.splitlines()
        assert "0.0.0.0" not in text and "--tools" not in text

    @pytest.mark.parametrize("kw, message", [
        (dict(user="root"), "never root"), (dict(user="-oProxy"), "never root"),
        (dict(home="relative"), "home path"), (dict(home="/home/a b"), "home path"),
        (dict(model="/home/x/../../etc/shadow"), "model path"), (dict(model="m.gguf"), "model path"),
        (dict(port=80), "port"), (dict(port=True), "port"),
    ])
    def test_every_value_is_checked(self, kw, message):
        with pytest.raises(launcher.LauncherError, match=message):
            self.unit(**kw)


# --- 5. deploy settings -------------------------------------------------------------------------------------------

class TestSettings:

    def test_the_default_is_the_user_level_start_script(self):
        s = DeploySettings()
        assert s.install == "user" and s.port == 8080 and s.problems(RK3566) == [] and s.problems(ALPINE) == []

    def test_systemd_needs_systemd_on_the_device_a_sudo_confirmation_and_a_model(self):
        s = DeploySettings(install="systemd", ssh_user="dietpi")
        problems = " ".join(s.problems(RK3566))
        assert "confirmation" in problems and "model" in problems and "INIT_SYSTEM" not in problems
        ok = DeploySettings(install="systemd", ssh_user="dietpi", model="models/q.gguf", sudo_confirmed_at=CONFIRMED)
        assert ok.problems(RK3566) == []
        assert "INIT_SYSTEM=systemd" in " ".join(ok.problems(DEVUAN)) and "sysvinit" in " ".join(ok.problems(DEVUAN))
        assert "openrc" in " ".join(ok.problems(ALPINE))
        assert ok.model_on_device("/home/dietpi") == "/home/dietpi/models/q.gguf"

    def test_leaving_systemd_drops_the_sudo_confirmation(self):
        ok = DeploySettings(install="systemd", model="/m.gguf", sudo_confirmed_at=CONFIRMED)
        assert ok.changed(port=9000).sudo_confirmed_at == CONFIRMED
        assert ok.changed(install="user").sudo_confirmed_at is None

    @pytest.mark.parametrize("values, message", [
        (dict(install="docker"), "install"), (dict(ssh_user="root"), "never runs as root"),
        (dict(ssh_user="a;b"), "account name"), (dict(port=22), "port"), (dict(port="8080"), "port"),
        (dict(model="../x.gguf"), "'..'"), (dict(model="-m.gguf"), "start with"), (dict(model="a b.gguf"), "letters"),
        (dict(sudo_confirmed_at="yesterday"), "time stamp"),
    ])
    def test_bad_values_are_errors(self, values, message):
        with pytest.raises(SettingsError, match=message):
            DeploySettings(**values)

    def test_the_stored_file_is_checked_when_it_is_read(self, tmp_path):
        store = ProfileStore(tmp_path)
        device = "0123456789abcdef"
        assert DeploySettings.from_dict(store.load_deploy_settings(device)) == DeploySettings()
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="m.gguf", sudo_confirmed_at=CONFIRMED)
        path = store.save_deploy_settings(device, settings.as_dict())
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 if os.name == "posix" else True
        assert DeploySettings.from_dict(store.load_deploy_settings(device)) == settings
        path.write_text('{"install": "user", "shell": "rm -rf ~"}')
        with pytest.raises(SettingsError, match="unknown fields"):
            DeploySettings.from_dict(store.load_deploy_settings(device))
        path.write_text("not json")
        with pytest.raises(ValueError, match="damaged"):
            store.load_deploy_settings(device)
        store.save_device(device, RK3566)
        assert store.remove_device(device) and not path.exists()


# --- 6. the tarball -----------------------------------------------------------------------------------------------

class FakeDeviceExecutor:
    """Builds the device plan's programs as small scripts that answer like llama.cpp, plus libgomp.so.1."""

    def __init__(self, server=None, bench=None):
        self.server, self.bench = server, bench

    def build(self, plan, *, source, work, jobs, toolchain=None, ccache_dir=None):
        dirs = BuildExecutor._prepare(work)
        (dirs["logs"] / "build.log").write_text("built\n")
        files = {}
        programs = {"llama-server": self.server or fake_program(FAKE_SERVER),
                    "llama-bench": self.bench or fake_program(FAKE_BENCH)}
        for name in plan.targets:
            path = dirs["bin"] / name
            path.write_text(programs.get(name, f"#!/bin/sh\necho {name}\n"))
            path.chmod(0o755)
        for lib in plan.verify.get("runtime_libs") or []:
            (dirs["bin"] / lib).write_bytes(b"\x7fELF fake libgomp")
        for path in dirs["bin"].iterdir():
            files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return BuildResult(build_id=plan.build_id, work=Path(work), bin_dir=dirs["bin"], files=files, links={},
                           duration_s=1.0, jobs=jobs, placement=plan.placement)


class FormatOnlyVerifier:
    """A cross build is format-checked on the build host; its programs run on the device."""

    def __init__(self, ok=True):
        self.ok = ok

    def verify(self, plan, bin_dir, **kw):
        return VerifyReport([Check("binaries", True), Check("format", self.ok, "fake ELF")], ran_binaries=False)


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("project(llama.cpp)\n")
    (src / "LICENSE").write_bytes(LICENCE.encode("utf-8"))  # the pinned source as git checks it out: LF
    return src


def stored_build(state, source, profile=RK3566, **kw):
    store = BuildStore(state)
    build = store.build(plan_for(profile), source=source, executor=FakeDeviceExecutor(**kw),
                        verifier=FormatOnlyVerifier(), jobs=1, host=HOST, perf_key=profile.perf_key())
    return store, build


class TestOneStoreForDittoAndTheDevices:
    """Ditto's builds and the devices' builds live in the same build store (one recipe, one executor, one store):
    Ditto's retention must keep to its own builds."""

    def test_dittos_prune_never_lists_a_device_build(self, tmp_path, source):
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        store, build = stored_build(tmp_path / "state", source)
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models", catalog=CATALOG)
        assert rt.cleanup_plan() is None
        assert store.remove([build.build_id], purpose="host-runtime")[0] == []
        assert store.get(build.build_id) is not None, "the device's build stays for its next deployment"
        assert [b.build_id for b in store.removable()] == [build.build_id], "without a purpose every build is listed"


class TestPackage:

    def test_the_tarball_holds_the_programs_libgomp_the_start_script_and_the_licence(self, tmp_path, source):
        store, build = stored_build(tmp_path / "state", source)
        packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "out")
        id16 = build.build_id[:16]
        assert packaged.path.name == f"llama-runtime-arm64-{id16}.tar.gz"
        assert packaged.path.with_name(packaged.path.name + ".sha256").read_text() == \
            f"{packaged.sha256}  {packaged.path.name}\n"
        with tarfile.open(packaged.path) as tar:
            members = {m.name: m for m in tar.getmembers()}
            assert all(name == id16 or name.startswith(id16 + "/") for name in members)
            for name in ("llama-server", "llama-cli", "llama-bench", "llama-perplexity"):
                assert members[f"{id16}/bin/{name}"].mode == 0o755
            assert members[f"{id16}/lib/libgomp.so.1"].isfile() and f"{id16}/bin/libgomp.so.1" not in members
            assert members[f"{id16}/{launcher.START_SCRIPT}"].mode == 0o755
            assert tar.extractfile(f"{id16}/{launcher.START_SCRIPT}").read().decode() == \
                launcher.render_start_script()
            assert tar.extractfile(f"{id16}/LICENSE-llama.cpp").read().decode() == LICENCE
            assert json.loads(tar.extractfile(f"{id16}/build.json").read())["build_id"] == build.build_id
            assert all(m.uid == 0 and m.gid == 0 and not m.uname for m in members.values())
            sums = tar.extractfile(f"{id16}/SHA256SUMS").read().decode().splitlines()
            for line in sums:
                digest, rel = line.split("  ", 1)
                assert hashlib.sha256(tar.extractfile(f"{id16}/{rel}").read()).hexdigest() == digest
            assert {line.split("  ", 1)[1] for line in sums} == {
                n[len(id16) + 1:] for n, m in members.items() if m.isfile() and not n.endswith("SHA256SUMS")}

    def test_the_tarball_is_deterministic(self, tmp_path, source):
        store, build = stored_build(tmp_path / "state", source)
        first = package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "a")
        second = package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "b")
        assert first.sha256 == second.sha256

    def test_an_alpine_build_has_no_lib_folder(self, tmp_path, source):
        store, build = stored_build(tmp_path / "state", source, profile=ALPINE)
        packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "out")
        with tarfile.open(packaged.path) as tar:
            assert not any("/lib/" in n or n.endswith("/lib") for n in tar.getnames())

    def test_only_checked_device_builds_with_llama_cpps_licence_are_packaged(self, tmp_path, source):
        store, build = stored_build(tmp_path / "state", source)
        (source / "OTHER").write_text("GPL")
        with pytest.raises(PackageError, match="MIT licence"):
            package_runtime(store, build, licence=source / "OTHER", out_dir=tmp_path / "out")
        with pytest.raises(PackageError, match="licence text cannot be read"):
            package_runtime(store, build, licence=source / "missing", out_dir=tmp_path / "out")
        (build.bin_dir / "llama-server").write_text("#!/bin/sh\necho changed\n")
        with pytest.raises(PackageError, match="does not match its manifest"):
            package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "out")
        assert not list((tmp_path / "out").glob("*.tar.gz")) if (tmp_path / "out").exists() else True

    def test_the_device_store_finds_and_lists_tarballs(self, tmp_path, source):
        store, build = stored_build(tmp_path / "state", source)
        devices = DeviceRuntimeStore(tmp_path / "state", "0123456789abcdef")
        assert devices.tarball(build) is None
        packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=devices.root)
        assert devices.tarball(build).sha256 == packaged.sha256
        old = devices.root / "llama-runtime-arm64-ffffffffffffffff.tar.gz"
        old.write_bytes(b"old")
        devices.save_record({"build_id": build.build_id})
        plan = devices.cleanup_plan()   # the person's list: never the package of the build deployed last
        assert [i.full_path for i in plan.items] == [str(old)] and plan.items[0].kind == "deploy_package"
        assert sorted(devices.tarballs()) == sorted([old, packaged.path]), "listing deletes nothing"
        packaged.path.write_bytes(b"damaged")
        assert devices.tarball(build) is None
        with pytest.raises(PackageError):
            DeviceRuntimeStore(tmp_path, "../x")


# --- 7. the fake device -------------------------------------------------------------------------------------------

class FakeChannel:
    def __init__(self, job):
        self.job = job
        self.write_closed = False

    def shutdown_write(self):
        self.write_closed = True

    def recv_exit_status(self):
        return self.job.run()[0]

    def close(self):
        pass


class FakeExec:
    def __init__(self, device, command):
        self.device, self.command = device, command
        self.sent, self.result = b"", None
        self.channel = FakeChannel(self)

    def run(self):
        if self.result is None:
            assert self.channel.write_closed, "stdin must be closed before the output is read"
            self.result = self.device.answer(self.command, self.sent)
        return self.result


class FakeStdin:
    def __init__(self, job):
        self.job, self.channel = job, job.channel

    def write(self, data):
        self.job.sent += data

    def flush(self):
        pass


class FakeStream:
    def __init__(self, job, index):
        self.job, self.index, self.channel, self.buffer = job, index, job.channel, None

    def read(self, size=-1):
        if self.buffer is None:
            self.buffer = io.BytesIO(self.job.run()[self.index])
        return self.buffer.read(size)


STUB_ID = '#!/bin/sh\n[ "$1" = "-u" ] && { echo @UID@; exit 0; }\nexit 1\n'
STUB_UNAME = '#!/bin/sh\n[ "$1" = "-m" ] && { echo @MACHINE@; exit 0; }\nexit 1\n'
STUB_SUDO = """#!/bin/sh
echo "$*" >> "$FAKE_LOG/sudo.log"
[ "$1" = "-n" ] || exit 1
shift
[ -f "$FAKE_LOG/sudo-denied" ] && { echo "sudo: a password is required" >&2; exit 1; }
case "$1" in
  true) exit 0 ;;
  systemctl) exit 0 ;;
  tee) cat > "$2" ;;
  *) exit 1 ;;
esac
"""


class FakeDevice:
    """A board behind SSH. Commands run for real with a POSIX shell (as sshd runs them: the login shell with -c and
    the exact command string) in a temporary home, with stand-ins for id, uname and sudo; the absolute systemd paths
    are moved into a fake root. The caller's environment carries LLAMA_ARG_* variables, as a careless login profile
    could: none of them may reach a program of the runtime."""

    def __init__(self, base: Path, *, machine="aarch64", uid=1000, systemd=True, sudo=True):
        self.home = base / "home" / "dietpi"
        self.fakeroot = base / "root"
        self.stubs = base / "stubs"
        self.log = base / "log"
        for folder in (self.home, self.fakeroot / "etc" / "systemd" / "system", self.stubs, self.log):
            folder.mkdir(parents=True, exist_ok=True)
        if systemd:
            (self.fakeroot / "run" / "systemd" / "system").mkdir(parents=True)
        if not sudo:
            (self.log / "sudo-denied").write_text("")
        for name, text in (("id", STUB_ID.replace("@UID@", str(uid))),
                           ("uname", STUB_UNAME.replace("@MACHINE@", machine)), ("sudo", STUB_SUDO)):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        self.commands = []

    def exec_command(self, command, timeout=None):
        assert timeout, "every remote command has a time limit"
        assert command.startswith("sh -c '"), "one fixed sh -c per command, whatever the login shell is"
        self.commands.append(command)
        job = FakeExec(self, command)
        return FakeStdin(job), FakeStream(job, 1), FakeStream(job, 2)

    def answer(self, command, stdin):
        moved = command.replace("/run/systemd/system", f"{self.fakeroot}/run/systemd/system") \
                       .replace("/etc/systemd/system/", f"{self.fakeroot}/etc/systemd/system/")
        env = {"HOME": str(self.home), "PATH": f"{self.stubs}:/usr/bin:/bin", "LANG": "C",
               "FAKE_LOG": str(self.log), "LLAMA_ARG_TOOLS": "all", "LLAMA_ARG_HOST": "0.0.0.0"}
        res = subprocess.run([SHELL, "-c", moved], input=stdin, env=env, capture_output=True, timeout=120,
                             cwd=str(self.home))
        return res.returncode, res.stdout, res.stderr

    @property
    def root(self) -> Path:
        return self.home / launcher.INSTALL_REL

    def sudo_calls(self):
        log = self.log / "sudo.log"
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def device_base(tmp_path_factory):
    # A folder name without brackets: the home folder must be a plain path (letters, digits, ._/-).
    return tmp_path_factory.mktemp("board")


def packaged_build(tmp_path, source, profile=RK3566, **kw):
    store, build = stored_build(tmp_path / "state", source, profile=profile, **kw)
    return build, package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "out")


USER = DeploySettings(ssh_user="dietpi")


def checks_of(dev, build, folders=None, **kw):
    """The check a deployment runs on the new build before it becomes current (design 3.8, P6)."""
    def check(home, folder):
        if folders is not None:
            folders.append(folder)
        return verify_on_device(RemoteShell(dev), home=home, folder=folder, plan_verify=build.plan["verify"],
                                pin_build=PIN.build_number, pin_commit=PIN.commit, port=8080, **kw)
    return check


def deploy(dev, build, packaged, settings=USER, **kw):
    check = kw.pop("check", None) or checks_of(dev, build)
    return deploy_runtime(RemoteShell(dev), tarball=packaged.path, tarball_sha256=kw.pop("sha", packaged.sha256),
                          build_id=build.build_id, arch="arm64", settings=settings, check=check, **kw)


def verify(dev, build, **kw):
    return verify_on_device(RemoteShell(dev), home=str(dev.home), plan_verify=build.plan["verify"],
                            pin_build=PIN.build_number, pin_commit=PIN.commit, port=8080, **kw)


@POSIX
class TestDeploy:

    def test_the_runtime_is_installed_in_the_users_home_with_the_key(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        result = deploy(dev, build, packaged)
        id16 = build.build_id[:16]
        assert result.root == str(dev.root) and result.previous is None and result.install == "user"
        assert os.readlink(dev.root / "current") == id16
        folder = dev.root / id16
        assert (folder / "bin" / "llama-server").is_file() and (folder / "lib" / "libgomp.so.1").is_file()
        assert (folder / launcher.START_SCRIPT).is_file() and (folder / "LICENSE-llama.cpp").read_text() == LICENCE
        # umask 077 on the device: nothing of the runtime is readable by other users.
        for path in [folder, *folder.rglob("*")]:
            assert stat.S_IMODE(path.lstat().st_mode) & 0o077 == 0 or path.is_symlink(), path
        key = dev.home / launcher.KEY_REL
        assert re.fullmatch(r"[0-9a-f]{64}\n", key.read_text()) and stat.S_IMODE(key.stat().st_mode) == 0o600
        assert not list(dev.root.glob(".staging-*")), "the staging folder is removed"
        assert dev.sudo_calls() == [], "the user-level install never runs sudo"
        assert not any("/tmp" in c and str(dev.home) not in c for c in dev.commands), "nothing is put into /tmp"
        assert not (dev.fakeroot / "etc" / "systemd" / "system" / launcher.UNIT_NAME).exists()

    def test_the_checks_run_on_the_device_as_the_user_with_a_cleared_environment(self, tmp_path, source,
                                                                                 device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        deploy(dev, build, packaged)
        start = len(dev.commands)
        report = verify(dev, build)
        assert report.ok and report.ran_binaries
        checks = {c.name: c for c in report.checks}
        assert checks["version"].ok and f"b{PIN.build_number}" in checks["version"].detail
        assert checks["devices"].ok and checks["devices"].detail == "CPU only"
        assert checks["server-tools"].ok
        assert checks["smoke"].ok is None and "--bench-model" in checks["smoke"].detail
        runs = [c for c in dev.commands[start:] if "env -i" in c]
        assert runs and all("HOME=" in c and "PATH=/usr/bin:/bin" in c and "LC_ALL=C" in c for c in runs)
        assert not any("sudo" in c for c in dev.commands[start:])

    def test_a_short_benchmark_runs_only_when_allowed(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        deploy(dev, build, packaged)
        model = dev.home / "models" / "qwen.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF")
        skipped = verify(dev, build, bench_model=str(model), bench_allowed=False)
        assert skipped.check("smoke").ok is None and "not allowed" in skipped.check("smoke").detail
        assert not any("-o json" in c for c in dev.commands), "the model was never loaded"
        ran = verify(dev, build, bench_model=str(model), bench_allowed=True)
        smoke = ran.check("smoke")
        assert smoke.ok and "pp 21.5 t/s" in smoke.detail and "tg 4.2 t/s" in smoke.detail
        assert smoke.data["bench"][0]["n_prompt"] == 64

    def test_a_wrong_version_fails_the_checks(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source, server=fake_program(FAKE_SERVER, build=1234))
        dev = FakeDevice(device_base)
        result = deploy(dev, build, packaged)
        assert not result.activated and not result.report.ok
        assert "reports build 1234" in result.report.check("version").detail

    def test_a_server_that_accepts_tools_fails_the_checks(self, tmp_path, source, device_base):
        accepting = fake_program(FAKE_SERVER).replace('echo "error: subprocess is not enabled on this build" >&2; '
                                                      "exit 1", "echo serving tools; exit 0")
        build, packaged = packaged_build(tmp_path, source, server=accepting)
        dev = FakeDevice(device_base)
        result = deploy(dev, build, packaged)
        assert not result.activated and result.report.check("server-tools").ok is False

    def test_the_checks_run_on_the_new_build_before_it_becomes_current(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        folders = []
        result = deploy(dev, build, packaged, check=checks_of(dev, build, folders))
        assert folders == [f"{dev.root}/{build.build_id[:16]}"] and result.activated and result.report.ok
        assert os.readlink(dev.root / "current") == build.build_id[:16]

    def test_a_build_that_fails_its_checks_on_the_device_never_becomes_current(self, tmp_path, source,
                                                                                device_base):
        """Design 3.8 and P6: a failed build is never activated, and the previous one stays active. A wrong CPU
        baseline (SIGILL) or a wrong C library only shows when the programs run on the board."""
        dev = FakeDevice(device_base)
        good, packaged = packaged_build(tmp_path / "good", source)
        assert deploy(dev, good, packaged).activated
        bad, packaged = packaged_build(tmp_path / "bad", source, profile=changed(RK3566, LIBC_VERSION="2.41"),
                                       server=fake_program(FAKE_SERVER, build=1234))
        result = deploy(dev, bad, packaged)
        assert not result.activated and not result.report.ok and result.previous is None
        assert os.readlink(dev.root / "current") == good.build_id[:16], "the previous build stays active"
        assert (dev.root / good.build_id[:16] / "bin" / "llama-server").is_file()
        assert not (dev.root / bad.build_id[:16]).exists(), "the failed build is removed from the board"
        assert any("stays active" in n for n in result.notes)
        # A first deployment that fails leaves no `current` behind.
        other = FakeDevice(device_base / "first")
        assert not deploy(other, bad, packaged).activated and not (other.root / "current").exists()

    def test_a_check_that_breaks_off_leaves_the_active_build_alone(self, tmp_path, source, device_base):
        dev = FakeDevice(device_base)
        good, packaged = packaged_build(tmp_path / "good", source)
        deploy(dev, good, packaged)
        new, packaged = packaged_build(tmp_path / "new", source, profile=changed(RK3566, LIBC_VERSION="2.41"))

        def lost_connection(home, folder):
            raise RemoteError("the SSH connection failed: reset")

        with pytest.raises(RemoteError, match="connection failed"):
            deploy(dev, new, packaged, check=lost_connection)
        assert os.readlink(dev.root / "current") == good.build_id[:16]
        assert not (dev.root / new.build_id[:16]).exists()
        with pytest.raises(RemoteError, match="needs the checks"):
            deploy_runtime(RemoteShell(dev), tarball=packaged.path, tarball_sha256=packaged.sha256,
                           build_id=new.build_id, arch="arm64", settings=USER, check=None)

    def test_a_failing_build_never_reaches_the_systemd_unit(self, tmp_path, source, device_base):
        dev = FakeDevice(device_base)
        (dev.home / "models").mkdir()
        (dev.home / "models" / "qwen.gguf").write_bytes(b"GGUF")
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="models/qwen.gguf",
                                  sudo_confirmed_at=CONFIRMED)
        bad, packaged = packaged_build(tmp_path, source, server=fake_program(FAKE_SERVER, build=1234))
        result = deploy(dev, bad, packaged, settings=settings)
        assert not result.activated and result.unit_started is None
        assert dev.sudo_calls() == ["-n true"], "no unit is written, enabled or restarted for a failed build"
        assert not (dev.fakeroot / "etc" / "systemd" / "system" / launcher.UNIT_NAME).exists()

    def test_redeploying_keeps_every_older_build_and_lists_them(self, tmp_path, source, device_base):
        """Round 5 f: a deployment removes no older build from the board (it did after every deploy before); it
        lists them, and only the person removes them from the full list."""
        dev = FakeDevice(device_base)
        builds = []
        for index, libc in enumerate(("2.36", "2.39", "2.41")):
            build, packaged = packaged_build(tmp_path / str(index), source, profile=changed(RK3566, LIBC_VERSION=libc))
            builds.append(build)
            result = deploy(dev, build, packaged)
        ids = [b.build_id[:16] for b in builds]
        assert len(set(ids)) == 3
        assert sorted(p.name for p in dev.root.iterdir() if p.is_dir() and not p.is_symlink()) == sorted(ids)
        assert os.readlink(dev.root / "current") == ids[2] and result.previous == ids[1]
        assert sorted(b.build_id16 for b in result.older) == sorted(ids[:2])
        assert all(b.size_bytes > 0 and not b.in_use for b in result.older)
        assert any("2 older runtime build(s) stay on the device" in n and "--on-device" in n for n in result.notes)
        assert not any("rm -rf" in c and '"$d"' in c for c in dev.commands), "no clean-up loop runs on the board"

    def test_a_failed_redeploy_of_an_older_build_keeps_that_build_on_the_board(self, tmp_path, source, device_base):
        """Round 5 f: an older build the person kept on the board is theirs. Deploying it again replaces its files
        with the same, checked files; when the checks then fail or break off, the build stays (it was there before),
        and only a build this deployment brought is removed again."""
        dev = FakeDevice(device_base)
        old, old_packaged = packaged_build(tmp_path / "old", source)
        assert deploy(dev, old, old_packaged).activated
        new, new_packaged = packaged_build(tmp_path / "new", source, profile=changed(RK3566, LIBC_VERSION="2.41"))
        assert deploy(dev, new, new_packaged).activated
        kept = dev.root / old.build_id[:16]
        assert kept.is_dir() and os.readlink(dev.root / "current") == new.build_id[:16]

        def lost_connection(home, folder):
            raise RemoteError("the SSH connection failed: reset")

        with pytest.raises(RemoteError, match="connection failed"):
            deploy(dev, old, old_packaged, check=lost_connection)
        assert (kept / "bin" / "llama-server").is_file(), "the person's older build is never removed by a deploy"

        def failed(home, folder):
            return verify_on_device(RemoteShell(dev), home=home, folder=folder, plan_verify=old.plan["verify"],
                                    pin_build=PIN.build_number + 1, pin_commit=PIN.commit, port=8080)

        result = deploy(dev, old, old_packaged, check=failed)
        assert not result.activated and not result.report.ok
        assert (kept / "bin" / "llama-server").is_file() and os.readlink(dev.root / "current") == new.build_id[:16]
        assert any("was on the device before" in n for n in result.notes), result.notes
        assert not any("was removed from the device" in n for n in result.notes), result.notes

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
    def test_a_build_a_running_program_uses_is_kept_and_never_offered(self, tmp_path, source, device_base):
        dev = FakeDevice(device_base)
        first, packaged = packaged_build(tmp_path / "a", source)
        deploy(dev, first, packaged)
        sleeper = dev.root / first.build_id[:16] / "bin" / "sleeper"
        shutil.copy2(shutil.which("sleep"), sleeper)
        proc = subprocess.Popen([str(sleeper), "30"])
        try:
            for index, libc in enumerate(("2.39", "2.41")):
                build, packaged = packaged_build(tmp_path / f"b{index}", source,
                                                 profile=changed(RK3566, LIBC_VERSION=libc))
                result = deploy(dev, build, packaged)
            assert (dev.root / first.build_id[:16]).is_dir()
            used = {b.build_id16: b.in_use for b in result.older}
            assert used[first.build_id[:16]] is True and list(used.values()).count(True) == 1
            plan = board_cleanup_plan(result.older, result.root, result.previous)
            assert [i.file_name for i in plan.items] == [result.previous], "a build in use is not offered"
        finally:
            proc.kill()
            proc.wait()

    def test_the_systemd_unit_is_installed_with_sudo_n_as_the_settings_say(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        (dev.home / "models").mkdir()
        (dev.home / "models" / "qwen.gguf").write_bytes(b"GGUF")
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="models/qwen.gguf",
                                  sudo_confirmed_at=CONFIRMED)
        result = deploy(dev, build, packaged, settings=settings)
        unit = (dev.fakeroot / "etc" / "systemd" / "system" / launcher.UNIT_NAME).read_text()
        assert unit == launcher.render_systemd_unit(user="dietpi", home=str(dev.home),
                                                    model=f"{dev.home}/models/qwen.gguf", port=8080)
        calls = dev.sudo_calls()
        assert calls[0] == "-n true" and all(c.startswith("-n ") for c in calls)
        assert [c for c in calls if "systemctl" in c] == [
            "-n systemctl daemon-reload", f"-n systemctl enable {launcher.UNIT_NAME}",
            f"-n systemctl restart {launcher.UNIT_NAME}"]
        assert result.unit_started is True and result.install == "systemd"

    def test_the_unit_is_only_enabled_while_the_model_is_missing(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="models/later.gguf",
                                  sudo_confirmed_at=CONFIRMED)
        result = deploy(dev, build, packaged, settings=settings)
        assert result.unit_started is False and any("starts once the model" in n for n in result.notes)
        assert not any("restart" in c for c in dev.sudo_calls())

    def test_without_passwordless_sudo_nothing_is_changed_and_nothing_is_asked(self, tmp_path, source,
                                                                                device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base, sudo=False)
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="m.gguf", sudo_confirmed_at=CONFIRMED)
        with pytest.raises(RemoteError, match="never asks"):
            deploy(dev, build, packaged, settings=settings)
        assert not dev.root.exists() and dev.sudo_calls() == ["-n true"]

    def test_a_device_without_systemd_refuses_the_unit(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base, systemd=False)
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="m.gguf", sudo_confirmed_at=CONFIRMED)
        with pytest.raises(RemoteError, match="does not run systemd"):
            deploy(dev, build, packaged, settings=settings)
        assert not dev.root.exists()
        # The start script works there (an RK3588 on Devuan, an Alpine board with OpenRC).
        assert deploy(dev, build, packaged).install == "user"

    def test_a_unit_of_an_earlier_deployment_is_reported(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        (dev.fakeroot / "etc" / "systemd" / "system" / launcher.UNIT_NAME).write_text("[Unit]\n")
        result = deploy(dev, build, packaged)
        assert any("still installed" in n for n in result.notes) and dev.sudo_calls() == []

    def test_root_and_another_architecture_are_refused_before_anything_changes(self, tmp_path, source,
                                                                                 device_base):
        build, packaged = packaged_build(tmp_path, source)
        with pytest.raises(RemoteError, match="never runs as root"):
            deploy(FakeDevice(device_base / "r", uid=0), build, packaged)
        dev = FakeDevice(device_base / "x", machine="x86_64")
        with pytest.raises(RemoteError, match="runs x86_64, but the build is for arm64"):
            deploy(dev, build, packaged)
        assert not dev.root.exists()

    def test_a_damaged_upload_is_never_installed(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        with pytest.raises(RemoteError, match="arrived damaged"):
            deploy(dev, build, packaged, sha="0" * 64)
        assert not (dev.root / "current").exists() and not list(dev.root.glob(".staging-*"))

    def test_a_changed_file_inside_the_tarball_is_refused(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        tampered = tmp_path / "tampered.tar.gz"
        with tarfile.open(packaged.path) as src, tarfile.open(tampered, "w:gz") as out:
            for member in src.getmembers():
                data = src.extractfile(member).read() if member.isfile() else None
                if member.name.endswith("/bin/llama-server"):
                    data = b"#!/bin/sh\necho evil\n"
                    member.size = len(data)
                out.addfile(member, io.BytesIO(data) if data is not None else None)
        digest = hashlib.sha256(tampered.read_bytes()).hexdigest()
        dev = FakeDevice(device_base)
        with pytest.raises(RemoteError, match="does not match SHA256SUMS"):
            deploy_runtime(RemoteShell(dev), tarball=tampered, tarball_sha256=digest, build_id=build.build_id,
                           arch="arm64", settings=USER, check=checks_of(dev, build))
        assert not (dev.root / "current").exists()

    def test_every_command_is_one_sh_c_with_quoted_values(self, tmp_path, source, device_base):
        build, packaged = packaged_build(tmp_path, source)
        dev = FakeDevice(device_base)
        deploy(dev, build, packaged)
        verify(dev, build)
        import shlex
        for command in dev.commands:
            words = shlex.split(command)
            assert words[:2] == ["sh", "-c"] and len(words) == 3


class BoardAudit:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fail:
            raise OSError("audit log full")
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


def board_builds(dev, *ids, current=None):
    """Build folders on the fake board (a file of 4 kB each) and `current` pointing at one of them."""
    dev.root.mkdir(parents=True, exist_ok=True)
    for id16 in ids:
        (dev.root / id16 / "bin").mkdir(parents=True)
        (dev.root / id16 / "bin" / "llama-server").write_bytes(b"x" * 4096)
    if current:
        os.symlink(current, dev.root / "current")
    return str(dev.root)


@POSIX
class TestOlderBuildsOnTheBoard:
    """Round 5 f: older runtime builds on a board are the person's objects. The framework lists them (full path on the
    device, folder name, size, what it is) and removes exactly the ones the person ticked at a terminal; never an
    AI, never the active build, never one a running program uses."""

    A, B, C = "a" * 16, "b" * 16, "c" * 16

    def test_the_list_names_each_older_build_with_its_path_size_and_what_it_is(self, device_base):
        dev = FakeDevice(device_base)
        root = board_builds(dev, self.A, self.B, self.C, current=self.C)
        builds = older_builds(RemoteShell(dev), root)
        assert sorted(b.build_id16 for b in builds) == [self.A, self.B], "the active build is never listed"
        plan = board_cleanup_plan(builds, root, previous=self.B)
        assert plan.mode_offered == ("permanent",), "a board has no trash"
        items = {i.file_name: i for i in plan.items}
        assert items[self.A].full_path == f"{root}/{self.A}" and items[self.A].size_bytes >= 4096
        assert items[self.A].kind == "runtime_build" and "not the active one" in items[self.A].what
        assert "kept to go back by hand" in items[self.B].what and root not in items[self.B].speaking_name
        assert board_cleanup_plan([], root) is None

    @pytest.mark.parametrize("actor", ["mcp", "ditto", "framework", "delegation:d_0123456789abcdef", ""])
    def test_only_the_person_removes_them(self, device_base, actor):
        from orchestrator.Core.deletion import AcceptedDeletion
        dev = FakeDevice(device_base)
        root = board_builds(dev, self.A, self.B, current=self.B)
        plan = board_cleanup_plan(older_builds(RemoteShell(dev), root), root)
        with pytest.raises(PermissionError):
            remove_older_builds(RemoteShell(dev), AcceptedDeletion(plan, "permanent", actor), root=root,
                                device_id="0" * 16, audit=BoardAudit())
        with pytest.raises(PermissionError):   # a plain list nobody accepted
            remove_older_builds(RemoteShell(dev), plan, root=root, device_id="0" * 16, audit=BoardAudit())
        assert (dev.root / self.A).is_dir()

    def test_exactly_the_ticked_builds_go_after_the_audit_entry(self, device_base):
        from orchestrator.Core.deletion import accept, choose
        dev = FakeDevice(device_base)
        root = board_builds(dev, self.A, self.B, self.C, current=self.C)
        plan = board_cleanup_plan(older_builds(RemoteShell(dev), root), root)
        ticked = choose(plan, [[i.file_name for i in plan.items].index(self.A)])
        audit = BoardAudit()
        result = remove_older_builds(RemoteShell(dev), accept(ticked, "cli", "permanent"), root=root,
                                     device_id="0" * 16, audit=audit)
        assert result.done == [f"runtime build {self.A}"] and not result.failed and result.freed_bytes > 0
        assert not (dev.root / self.A).exists() and (dev.root / self.B).is_dir() and (dev.root / self.C).is_dir()
        assert [r["outcome"] for r in audit.rows] == ["started", "ok"]
        started = audit.rows[0]
        assert started["arguments"]["builds"] == [self.A] and started["extra"]["answered_by"] == "person"
        assert root not in json.dumps(audit.rows), "the device's folders never reach the audit log (D0)"

    def test_a_changed_list_or_no_audit_log_removes_nothing(self, device_base):
        from orchestrator.Core.deletion import PlanChanged, accept
        dev = FakeDevice(device_base)
        root = board_builds(dev, self.A, self.B, current=self.B)
        plan = board_cleanup_plan(older_builds(RemoteShell(dev), root), root)
        for audit in (None, BoardAudit(fail=True)):
            with pytest.raises(RuntimeError):
                remove_older_builds(RemoteShell(dev), accept(plan, "cli", "permanent"), root=root,
                                    device_id="0" * 16, audit=audit)
        (dev.root / "current").unlink()
        os.symlink(self.A, dev.root / "current")   # the listed build became the active one meanwhile
        with pytest.raises(PlanChanged):
            remove_older_builds(RemoteShell(dev), accept(plan, "cli", "permanent"), root=root, device_id="0" * 16,
                                audit=BoardAudit())
        assert (dev.root / self.A).is_dir() and (dev.root / self.B).is_dir()

    def test_no_tool_and_no_deployment_removes_a_build_from_a_board(self):
        """Only the person removes builds from a board: the removal is reached through the person's own step
        (person_steps.remove_board_builds), which only the prune command and the GUI's Boards window call after the
        person's yes to the full list (W1'); no tool module, no deployment."""
        def callers(name):
            return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "orchestrator").rglob("*.py")
                          if name in p.read_text(encoding="utf-8"))
        assert callers("remove_older_builds(") == ["orchestrator/runtime/device/person_steps.py",
                                                   "orchestrator/runtime/device/remote.py"]
        assert set(callers("remove_board_builds(")) <= {"orchestrator/cli.py", "orchestrator/gui/boards_window.py",
                                                        "orchestrator/runtime/device/person_steps.py"}
        import inspect
        from orchestrator.runtime.device import remote
        source = inspect.getsource(remote.deploy_runtime)
        assert "remove_older_builds" not in source and '"$d"' not in source

    def test_the_cli_lists_them_and_removes_only_after_the_persons_yes(self, monkeypatch, device_base):
        from orchestrator import cli as cli_module
        from test_cli_builds import invoke_plain
        _store, device_id = stored_device(ssh_user="dietpi")
        dev = FakeDevice(device_base)
        board_builds(dev, self.A, self.B, current=self.B)
        TestCli.fake_ssh(None, monkeypatch, dev)
        from rich.console import Console
        monkeypatch.setattr(cli_module, "console", Console(width=400, color_system=None))  # one line per row
        res = invoke_plain("device", "runtime", "prune", "ZERO 3W", "--on-device")
        assert res.code == 0 and f"{dev.root}/{self.A}" in res.out and self.B not in res.out
        assert "Nothing was deleted" in res.out and "kept to go back" not in res.out
        assert (dev.root / self.A).is_dir(), "without a terminal nothing is removed"
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        monkeypatch.setattr(cli_module, "_device_audit", lambda ctx, state: BoardAudit())
        res = invoke_plain("device", "runtime", "prune", "ZERO 3W", "--on-device", input="\n")
        assert res.code == 0 and (dev.root / self.A).is_dir(), "nothing is ticked by default"
        res = invoke_plain("device", "runtime", "prune", "ZERO 3W", "--on-device", input="1\n\n")
        assert res.code == 0 and (dev.root / self.A).is_dir(), "No is the default"
        res = invoke_plain("device", "runtime", "prune", "ZERO 3W", "--on-device", input="1\ny\n")
        assert res.code == 0, res.out
        assert not (dev.root / self.A).exists() and (dev.root / self.B).is_dir() and "Deleted" in res.out


class ScriptedShell:
    """A device that answers the facts script with a fixed text."""

    def __init__(self, out, status=0):
        self.out, self.status = out, status

    def run(self, script, **kw):
        from orchestrator.runtime.device.remote import Result
        return Result(self.status, self.out, "")


class TestFacts:

    def test_values_from_the_device_are_validated(self):
        good = "uid=1000\nhome=/home/dietpi\nmachine=aarch64\ninit=systemd\nunit=no\n"
        facts = read_facts(ScriptedShell(good))
        assert (facts.uid, facts.home, facts.arch, facts.init, facts.missing) == (1000, "/home/dietpi", "arm64",
                                                                                  "systemd", ())
        assert facts.key == "/home/dietpi/.config/llm-framework/llama-server.key"
        for bad, message in (("uid=x\nhome=/h\nmachine=aarch64\n", "user ID"),
                             ("uid=1\nhome=/home/a b\nmachine=aarch64\n", "home folder"),
                             ("uid=1\nhome=/home/../etc\nmachine=aarch64\n", "home folder"),
                             ("uid=1\nhome=$(reboot)\nmachine=aarch64\n", "home folder"),
                             ("uid=1\nhome=/home/u\nmachine=riscv64\n", "does not support")):
            with pytest.raises(RemoteError, match=message):
                read_facts(ScriptedShell(bad))
        missing = read_facts(ScriptedShell(good + "missing=sha256sum\nmissing=$(x)\n"))
        assert missing.missing == ("sha256sum",)

    def test_missing_tools_stop_the_deployment(self, tmp_path):
        shell = ScriptedShell("uid=1000\nhome=/home/u\nmachine=aarch64\ninit=other\nunit=no\nmissing=tar\n")
        tar = tmp_path / "t.tar.gz"
        tar.write_bytes(b"x")
        with pytest.raises(RemoteError, match="lacks tar"):
            deploy_runtime(shell, tarball=tar, tarball_sha256="a" * 64, build_id="b" * 64, arch="arm64",
                           settings=USER, check=lambda home, folder: VerifyReport())


# --- 8. finding devices -------------------------------------------------------------------------------------------

class TestDevices:

    def test_a_device_is_found_by_id_prefix_label_or_address(self, tmp_path):
        store = ProfileStore(tmp_path)
        with pytest.raises(DeviceNotFound, match="no device is stored"):
            resolve_device(store, "x")
        store.save_device("0123456789abcdef", RK3566, label="ZERO 3W", address="192.0.2.10")
        store.save_device("fedcba9876543210", DEVUAN, label="rock5", address="[fe80::1]:2222")
        assert resolve_device(store, "0123456789abcdef") == "0123456789abcdef"
        assert resolve_device(store, "0123") == "0123456789abcdef"
        assert resolve_device(store, "ZERO 3W") == "0123456789abcdef"
        assert resolve_device(store, "[fe80::1]:2222") == "fedcba9876543210"
        with pytest.raises(DeviceNotFound, match="no stored device"):
            resolve_device(store, "012")

    @pytest.mark.parametrize("address, expected", [("192.0.2.10", ("192.0.2.10", 22)),
                                                   ("board.local:2222", ("board.local", 2222)),
                                                   ("[fe80::1]:2222", ("fe80::1", 2222)), ("fe80::1", ("fe80::1", 22))])
    def test_addresses(self, address, expected):
        assert split_address(address) == expected

    def test_bad_addresses(self):
        for bad in (None, "", "host:99999", "host:x"):
            with pytest.raises(ValueError):
                split_address(bad)


# --- 9. the CLI ---------------------------------------------------------------------------------------------------

class FakeHostKey:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"made-up host key of a fixture board"


def cli_state():
    from test_cli_builds import state_dir
    return state_dir()


def stored_device(profile=RK3566, label="ZERO 3W", **settings):
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
    store = ProfileStore(cli_state())
    store.save_device(device_id, profile, label=label, address="192.0.2.10")
    if settings:
        store.save_deploy_settings(device_id, DeploySettings(**settings).as_dict())
    return store, device_id


@pytest.fixture
def consents(monkeypatch):
    from orchestrator import cli as cli_module
    recorded = []
    monkeypatch.setattr(cli_module, "_record_consent", lambda ctx, tool, args, detail: recorded.append((tool, args)))
    return recorded


class TestCli:

    @pytest.fixture(autouse=True)
    def keep_logger_levels(self):
        """`llm-cli -q` sets every existing logger to ERROR (set_global_level); later tests read WARNING logs."""
        import logging
        saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
        root = logging.getLogger().level
        yield
        for name in list(logging.Logger.manager.loggerDict):
            logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
        logging.getLogger().setLevel(root)

    def test_plan_and_list(self):
        from test_cli_builds import invoke_plain
        _store, device_id = stored_device()
        res = invoke_plain("device", "runtime", "plan", "ZERO 3W", "--json")
        assert res.code == 0, res.out
        plan = json.loads(res.stdout)
        assert plan["purpose"] == "device-runtime" and plan["placement"] == "cross"
        assert plan["builder"]["base"] == "debian-12" and plan["target"]["libc_version"] == "2.36"
        text = invoke_plain("device", "runtime", "plan", device_id)
        assert text.code == 0 and "armv8.2-a+dotprod+fp16" in text.out and "Built: no" in text.out
        listed = json.loads(invoke_plain("device", "list", "--json").stdout)
        assert listed[0]["device_id"] == device_id and listed[0]["system"]["INIT_SYSTEM"] == "systemd"
        assert listed[0]["deploy"] == {"install": "user", "port": 8080}

    def test_build_runs_in_the_builder_container_and_packs_the_tarball(self, monkeypatch, source):
        import orchestrator.runtime.build.executor as executor_module
        import orchestrator.runtime.build.source as source_module
        import orchestrator.runtime.build.verify as verify_module
        from test_cli_builds import invoke_plain
        from test_runtime_build import FakeEngine
        _store, device_id = stored_device()
        engines = []
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state: source)
        monkeypatch.setattr(executor_module, "DockerEngine", lambda: engines.append(FakeEngine()) or engines[-1])
        monkeypatch.setattr(verify_module, "Verifier", FormatOnlyVerifier)
        res = invoke_plain("device", "runtime", "build", device_id)
        assert res.code == 0, res.out
        (engine,) = engines
        assert all(run["options"]["network_mode"] == "none" for run in engine.runs)
        assert engine.built[0]["args"]["CROSS_TRIPLE"] == "aarch64-linux-gnu"
        (tarball,) = DeviceRuntimeStore(cli_state(), device_id).tarballs()
        assert tarball.name.startswith("llama-runtime-arm64-")
        with tarfile.open(tarball) as tar:
            assert any(name.endswith("/lib/libgomp.so.1") for name in tar.getnames())
        again = invoke_plain("device", "runtime", "build", device_id)
        assert again.code == 0 and "already stored" in again.out and len(engines) == 1

    def test_build_without_docker_says_so(self, monkeypatch, source):
        import orchestrator.runtime.build.executor as executor_module
        import orchestrator.runtime.build.source as source_module
        from test_cli_builds import invoke_plain
        from test_runtime_build import FakeEngine
        _store, device_id = stored_device()
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state: source)
        monkeypatch.setattr(executor_module, "DockerEngine",
                            lambda: FakeEngine(reason="the Docker daemon is not reachable (FileNotFoundError)"))
        res = invoke_plain("device", "runtime", "build", device_id)
        assert res.code == 1 and "Start Docker" in " ".join(res.out.split())
        assert DeviceRuntimeStore(cli_state(), device_id).tarballs() == []

    def test_an_unknown_device_or_an_old_board_exits_with_1(self):
        from test_cli_builds import invoke_plain
        assert invoke_plain("device", "runtime", "plan", "nothing").code == 1
        stored_device(changed(RK3566, LIBC_VERSION="2.28"))
        res = invoke_plain("device", "runtime", "plan", "ZERO 3W")
        assert res.code == 1 and "older than every builder base" in res.out

    def test_systemd_is_chosen_before_the_deployment_with_a_recorded_sudo_confirmation(self, consents):
        from test_cli_builds import invoke_plain
        store, device_id = stored_device()
        res = invoke_plain("device", "settings", device_id, "--install", "systemd", "--model", "models/q.gguf")
        assert res.code == 1 and "needs your confirmation" in res.out and consents == []
        assert store.load_deploy_settings(device_id) == {}
        res = invoke_plain("device", "settings", device_id, "--install", "systemd", "--model", "models/q.gguf",
                           "--user", "dietpi", "--confirm-sudo")
        assert res.code == 0, res.out
        saved = DeploySettings.from_dict(store.load_deploy_settings(device_id))
        assert saved.install == "systemd" and saved.sudo_confirmed_at and saved.ssh_user == "dietpi"
        assert consents == [("device_runtime_systemd", {"device": device_id})]
        back = invoke_plain("device", "settings", device_id, "--install", "user")
        assert back.code == 0 and DeploySettings.from_dict(store.load_deploy_settings(device_id)).sudo_confirmed_at \
            is None

    def test_a_board_without_systemd_cannot_choose_the_unit(self, consents):
        from test_cli_builds import invoke_plain
        store, device_id = stored_device(DEVUAN, label="rock5")
        res = invoke_plain("device", "settings", "rock5", "--install", "systemd", "--confirm-sudo")
        assert res.code == 1 and "does not run systemd" in res.out and "sysvinit" in res.out
        assert consents == [] and store.load_deploy_settings(device_id) == {}

    def test_bad_settings_are_usage_errors(self):
        from test_cli_builds import invoke_plain
        stored_device()
        assert invoke_plain("device", "settings", "ZERO 3W", "--user", "root").code == 2
        assert invoke_plain("device", "settings", "ZERO 3W", "--port", "80").code == 2
        assert invoke_plain("device", "settings", "ZERO 3W", "--model", "../x").code == 2

    def fake_ssh(self, monkeypatch, device, status="known", key=None):
        from orchestrator.utils import ssh_trust
        connected = []

        class Client:
            def __init__(self):
                self.closed = False

            def exec_command(self, command, timeout=None):
                return device.exec_command(command, timeout=timeout)

            def close(self):
                connected.append("closed")

        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: key or FakeHostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: status)
        monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: (
            connected.append((host, user, port, password)) or Client()))
        return connected

    @POSIX
    def test_deploy_installs_and_checks_and_records(self, monkeypatch, device_base, source):
        from orchestrator import cli as cli_module
        from test_cli_builds import invoke_plain
        _store, device_id = stored_device(ssh_user="dietpi")
        state = cli_state()
        builds = BuildStore(state)
        build = builds.build(plan_for(RK3566), source=source, executor=FakeDeviceExecutor(),
                             verifier=FormatOnlyVerifier(), jobs=1, host=HOST)
        package_runtime(builds, build, licence=source / "LICENSE", out_dir=DeviceRuntimeStore(state, device_id).root)
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: PIN)
        dev = FakeDevice(device_base)
        connected = self.fake_ssh(monkeypatch, dev)
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        assert res.code == 0, res.out
        out = " ".join(res.out.split())
        assert connected[0] == ("192.0.2.10", "dietpi", 22, None) and connected[-1] == "closed"
        assert "Deployed" in out and "server-tools: passed" in out and "start MODEL.gguf" in out
        record = DeviceRuntimeStore(state, device_id).record()
        assert record["build_id"] == build.build_id and record["install"] == "user"
        assert record["verification"]["ok"] is True
        status = json.loads(invoke_plain("device", "runtime", "status", device_id, "--json").stdout)
        assert status["current"] is True and status["built"] is True

    @POSIX
    def test_a_build_that_fails_on_the_device_is_not_recorded_and_the_previous_one_stays(self, monkeypatch,
                                                                                        device_base, source):
        from orchestrator import cli as cli_module
        from test_cli_builds import invoke_plain
        _store, device_id = stored_device(ssh_user="dietpi", port=9090)
        state = cli_state()
        builds = BuildStore(state)
        build = builds.build(plan_for(RK3566), source=source,
                             executor=FakeDeviceExecutor(server=fake_program(FAKE_SERVER, build=1234)),
                             verifier=FormatOnlyVerifier(), jobs=1, host=HOST)
        package_runtime(builds, build, licence=source / "LICENSE", out_dir=DeviceRuntimeStore(state, device_id).root)
        earlier = {"build_id": "e" * 64, "deployed_at": "2026-10-01T10:00:00Z", "install": "user"}
        DeviceRuntimeStore(state, device_id).save_record(earlier)
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: PIN)
        dev = FakeDevice(device_base)
        install_folder(dev.home, id16="e" * 16)  # what runs on the board now
        self.fake_ssh(monkeypatch, dev)
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        out = " ".join(res.out.split())
        assert res.code == 1 and "version: failed" in out and "Deployed" not in out
        assert f"the previous build {'e' * 16} stays active" in out
        assert os.readlink(dev.root / "current") == "e" * 16 and not (dev.root / build.build_id[:16]).exists()
        record = DeviceRuntimeStore(state, device_id).record()
        assert record["build_id"] == "e" * 64, "the record still names what runs on the board"

    @POSIX
    def test_the_start_hint_names_the_port_of_the_settings(self, monkeypatch, device_base, source):
        from orchestrator import cli as cli_module
        from test_cli_builds import invoke_plain
        _store, device_id = stored_device(ssh_user="dietpi", port=9090)
        state = cli_state()
        builds = BuildStore(state)
        build = builds.build(plan_for(RK3566), source=source, executor=FakeDeviceExecutor(),
                             verifier=FormatOnlyVerifier(), jobs=1, host=HOST)
        package_runtime(builds, build, licence=source / "LICENSE", out_dir=DeviceRuntimeStore(state, device_id).root)
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: PIN)
        self.fake_ssh(monkeypatch, FakeDevice(device_base))
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        out = " ".join(res.out.split())
        # The start script listens on 8080 unless --port is given: the hint must pass the port of the settings.
        assert res.code == 0 and "start MODEL.gguf --port 9090" in out and "127.0.0.1:9090" in out

    @POSIX
    def test_deploy_refuses_another_machine_at_the_stored_address(self, monkeypatch, device_base, source):
        from test_cli_builds import invoke_plain

        class OtherKey(FakeHostKey):
            def asbytes(self):
                return b"another board at the same address"

        _store, device_id = stored_device(ssh_user="dietpi")
        state = cli_state()
        builds = BuildStore(state)
        build = builds.build(plan_for(RK3566), source=source, executor=FakeDeviceExecutor(),
                             verifier=FormatOnlyVerifier(), jobs=1, host=HOST)
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        no_source = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        assert no_source.code == 1 and "needs the pinned source" in no_source.out
        package_runtime(builds, build, licence=source / "LICENSE", out_dir=DeviceRuntimeStore(state, device_id).root)
        dev = FakeDevice(device_base)
        connected = self.fake_ssh(monkeypatch, dev, key=OtherKey())
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        assert res.code == 1 and "host key of another device" in res.out and connected == []
        unknown = self.fake_ssh(monkeypatch, dev, status="unknown")
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W")
        assert res.code == 1 and "not trusted yet" in res.out and unknown == []

    def test_deploy_needs_a_build_and_settings_that_fit_the_device(self, monkeypatch):
        from test_cli_builds import invoke_plain
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        stored_device(DEVUAN, label="rock5", ssh_user="dietpi", install="systemd", model="m.gguf",
                      sudo_confirmed_at=CONFIRMED)
        res = invoke_plain("device", "runtime", "deploy", "rock5")
        assert res.code == 1 and "INIT_SYSTEM=systemd" in res.out and "Nothing was deployed" in res.out
        stored_device(RK3566, label="zero", ssh_user="dietpi")
        res = invoke_plain("device", "runtime", "deploy", "zero")
        assert res.code == 1 and "not built yet" in res.out

    def test_scan_keeps_the_ssh_user_for_later_deployments(self, monkeypatch, tmp_path):
        from orchestrator.runtime import remote_probe as rp
        from orchestrator.runtime.profile import device_id_for_host_key
        from orchestrator.utils import ssh_trust
        from test_cli_builds import invoke_plain

        class Client:
            def close(self):
                pass

        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: FakeHostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: Client())
        monkeypatch.setattr(rp, "probe_over_ssh", lambda client: rp.RemoteProbeResult(RK3566, "sh", []))
        monkeypatch.chdir(tmp_path)
        assert invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi").code == 0
        device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
        store = ProfileStore(cli_state())
        assert store.load_deploy_settings(device_id) == {"install": "user", "port": 8080, "ssh_user": "dietpi"}
        assert invoke_plain("scan", "--device", "192.0.2.10", "--user", "root").code == 0
        assert store.load_deploy_settings(device_id)["ssh_user"] == "dietpi", "root never becomes the deploy user"


# --- 10. deploy.sh of a model package points to the self-built runtime ----------------------------------------------

def test_deploy_script_never_points_to_a_llama_server_on_path():
    from orchestrator.Core.deployment_manager import DEVICE_START_SCRIPT, DeploymentManager
    script = DeploymentManager(SimpleNamespace())._generate_deploy_script("model.gguf", "", False)
    assert "command -v llama-server" not in script
    assert f'RUNTIME="$HOME/{DEVICE_START_SCRIPT}"' in script and "llm-cli device runtime deploy" in script
    assert DEVICE_START_SCRIPT == f"{launcher.INSTALL_REL}/current/{launcher.START_SCRIPT}"


# --- 11. the GUI's SSH probe uses the stdin-based remote probe ------------------------------------------------------

@POSIX
def test_gui_ssh_probe_sends_the_probe_on_stdin_and_stores_the_profile(qapp, tmp_path, monkeypatch):
    """Nothing is uploaded to /tmp (boards mount it noexec): the same remote probe as `llm-cli scan --device`."""
    from orchestrator.gui import builder_tab
    from orchestrator.runtime import remote_probe as rp
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    from test_hardware_probe import FakeDevice as ProbeDevice, make_sysroot, tool_dir

    board = ProbeDevice(make_sysroot(tmp_path, "rk3566"), tool_dir(tmp_path))
    board.get_transport = lambda: SimpleNamespace(get_remote_server_key=lambda: FakeHostKey())
    board.open_sftp = lambda: pytest.fail("the GUI probe must not upload a script")
    board.close = lambda: None
    monkeypatch.setattr(builder_tab, "PARAMIKO_AVAILABLE", True)
    monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: board)
    worker = builder_tab.SSHProbeWorker("192.0.2.10", "dietpi", None, tmp_path / "state", tmp_path / "probes")
    emitted = []
    worker.finished.connect(lambda ok, msg, path: emitted.append((ok, msg, path)))
    worker.run()
    ok, msg, path = emitted[0]
    assert ok, msg
    assert board.commands == [rp.PYTHON_CHECK, rp.PYTHON_COMMAND]
    assert not any("/tmp" in c or "chmod" in c or "mktemp" in c for c in board.commands)
    device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
    assert device_id in msg
    stored = ProfileStore(tmp_path / "state").load_device(device_id)
    assert stored is not None and stored.get("SOC_MODEL") == "rk3566"
    assert ProfileStore(tmp_path / "state").load_deploy_settings(device_id)["ssh_user"] == "dietpi"
    assert parse_profile(Path(path).read_text()).get("SOC_MODEL") == "rk3566"
    assert Path(path).parent == tmp_path / "probes"


@pytest.mark.parametrize("host", ["../../etc", "[fe80::1]"])
def test_gui_ssh_probe_refuses_a_bad_host(qapp, tmp_path, monkeypatch, host):
    """A bracketed IPv6 address is refused like in the CLI and the deploy, and the message says how to write it."""
    from orchestrator.gui import builder_tab
    monkeypatch.setattr(builder_tab, "PARAMIKO_AVAILABLE", True)
    worker = builder_tab.SSHProbeWorker(host, "u", None, tmp_path, tmp_path)
    emitted = []
    worker.finished.connect(lambda ok, msg, path: emitted.append((ok, msg)))
    worker.run()
    assert emitted == [(False, "Enter a host name or an IP address (an IPv6 address without brackets, e.g. "
                               "fe80::1).")]


def test_a_cli_consent_is_written_to_the_real_audit_log(tmp_path, monkeypatch):
    # The consent record must use the audit log's fixed outcomes and answers; a fake would hide a refused entry.
    from types import SimpleNamespace
    from orchestrator import cli as cli_module
    from orchestrator.tools.audit import AuditLog
    audit = AuditLog(tmp_path / "audit.db")
    monkeypatch.setattr(cli_module, "_tool_registry", lambda ctx: SimpleNamespace(audit=audit))
    cli_module._record_consent(None, "device_runtime_deploy", {"device": "dev-1"}, "systemd unit allowed")
    entry = audit.entries(1)[0]
    assert (entry["tool"], entry["outcome"], entry["approved"]) == ("device_runtime_deploy", "ok", 1)
    extra = entry["extra"] if isinstance(entry["extra"], dict) else json.loads(entry["extra"])
    assert extra["answer"] == "accepted" and extra["confirm_channel"] == "framework-cli"
