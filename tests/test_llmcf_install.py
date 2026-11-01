"""llmcf-install, the Rust root helper of the board bundle (rust/llmcf-install), and its wiring: the manifest rules both
sides share (tests/data/bundle_manifest_vectors.json, checked here against manifest.py and by the Rust tests against
src/manifest.rs), the helper built and cached per board architecture, carried in the signed bundle, and run on a fake
board through llmcf's real gate (operation bundle-root) with a fake sudo, the report lines read by installer.report.

The tests that run the helper build its test binary with cargo (feature test-root: the --root PREFIX test mode a
release never has) and skip with the reason when cargo is not there; the fake boards need a POSIX shell and skip on
Windows. Every name, address and key here is a test value."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.manifest import (RE_ARG, RE_FILE, RE_ID, RE_LICENCE, RE_SHA, RE_URL, RE_VERSION,
                                                  RE_WORD, ManifestError, check_value, load_manifest)

VECTORS = json.loads((Path(__file__).parent / "data" / "bundle_manifest_vectors.json").read_text(encoding="utf-8"))


def _rule(name):
    import re
    from orchestrator.runtime.bundle import manifest as mf

    def by_check(kind):
        def check(value):
            try:
                check_value(kind, value)
                return True
            except ManifestError:
                return False
        return check
    rules = {
        "id": lambda v: bool(RE_ID.fullmatch(v)), "word": lambda v: bool(RE_WORD.fullmatch(v)),
        "file": by_check("file"), "var": lambda v: bool(re.fullmatch(r"[a-z][a-z0-9_]{0,31}", v, re.ASCII)),
        "sha": lambda v: bool(RE_SHA.fullmatch(v)), "version": lambda v: bool(RE_VERSION.fullmatch(v)),
        "licence": lambda v: bool(RE_LICENCE.fullmatch(v)),
        "arg": lambda v: bool(RE_ARG.fullmatch(v)) and ".." not in v.split("/"),
        "url": lambda v: bool(RE_URL.fullmatch(v)), "user": by_check("user"),
        "unit": lambda v: bool(mf.RE_UNIT.fullmatch(v)), "root_file": lambda v: bool(mf.RE_ROOT_FILE.fullmatch(v)),
        "fact": lambda v: bool(mf.RE_FACT.fullmatch(v)),
    }
    assert RE_FILE.pattern
    return rules[name]


@pytest.mark.parametrize("name", sorted(VECTORS["values"]))
def test_the_shared_value_vectors_hold_for_manifest_py(name):
    check = _rule(name)
    for good in VECTORS["values"][name]["accept"]:
        assert check(good), f"{name} should take {good!r}"
    for bad in VECTORS["values"][name]["reject"]:
        assert not check(bad), f"{name} should refuse {bad!r}"


def _patched(case):
    doc = copy.deepcopy(VECTORS["manifests"]["base"])
    for patch in case["patches"]:
        at = doc
        for step in patch["path"][:-1]:
            at = at[step]
        last = patch["path"][-1]
        if "value" in patch:
            at[last] = patch["value"]
        else:
            del at[last]
    return doc


def test_the_shared_manifest_vectors_hold_for_manifest_py():
    assert len(load_manifest(json.dumps(VECTORS["manifests"]["base"])).root_actions) == 3
    for case in VECTORS["manifests"]["reject"]:
        with pytest.raises(ManifestError):
            load_manifest(json.dumps(_patched(case)))
            pytest.fail(f"should refuse: {case['why']}")
    for case in VECTORS["manifests"]["accept"]:
        load_manifest(json.dumps(_patched(case)))


# --- the helper's build, cached per architecture ---------------------------------------------------------------------

def _static_elf(machine: int = 0xB7, klass: int = 2) -> bytes:
    """A tiny ELF executable header with one PT_LOAD program header and no interpreter (a static program)."""
    import struct
    if klass == 2:
        head = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0, 64, 0,
                                                                        0, 64, 56, 1, 0, 0, 0)
        return head + struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 0, 0, 0)
    head = b"\x7fELF" + bytes([1, 1, 1]) + b"\0" * 9 + struct.pack("<HHIIIIIHHHHHH", 2, machine, 1, 0, 52, 0, 0, 52,
                                                                    32, 1, 0, 0, 0)
    return head + struct.pack("<IIIIIIII", 1, 0, 0, 0, 0, 0, 5, 0)


def test_the_helper_is_built_once_per_target_kept_and_used_offline(tmp_path):
    from orchestrator.runtime.bundle import root_helper as rh
    rust = tmp_path / "rust"
    (rust / "llmcf-install" / "src").mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[workspace]\n")
    (rust / "llmcf-install" / "Cargo.toml").write_text('[package]\nname = "llmcf-install"\nversion = "0.1.0"\n')
    (rust / "llmcf-install" / "src" / "main.rs").write_text("fn main() {}\n")
    calls = []

    def build(work, target):
        calls.append(target)
        out = Path(work) / "llmcf-install"
        out.write_bytes(_static_elf(0xB7))
        return out
    with pytest.raises(rh.HelperMissing, match="offline mode is on"):
        rh.ensure_helper(tmp_path / "cache", "aarch64", offline=True, build=build, rust_dir=rust)
    made = rh.ensure_helper(tmp_path / "cache", "aarch64", offline=False, build=build, rust_dir=rust)
    assert made.target == "aarch64-unknown-linux-musl" and made.version == "0.1.0" and calls == [made.target]
    again = rh.ensure_helper(tmp_path / "cache", "arm64", offline=True, build=build, rust_dir=rust)
    assert again == made and calls == [made.target]      # the cached one, also offline
    (rust / "llmcf-install" / "src" / "main.rs").write_text("fn main() { }\n")
    rh.ensure_helper(tmp_path / "cache", "aarch64", offline=False, build=build, rust_dir=rust)
    assert len(calls) == 2                               # changed sources: built again
    with pytest.raises(rh.HelperMissing, match="not a static program"):
        rh.ensure_helper(tmp_path / "c2", "x86_64", offline=False, build=build, rust_dir=rust)   # an arm64 program
    with pytest.raises(rh.HelperMissing, match="not for"):
        rh.target_of("riscv64")


def test_only_a_static_program_of_the_boards_machine_is_kept():
    from orchestrator.runtime.bundle.root_helper import is_static_elf_for
    assert is_static_elf_for(_static_elf(0xB7), "aarch64-unknown-linux-musl")
    assert is_static_elf_for(_static_elf(0x3E), "x86_64-unknown-linux-musl")
    assert is_static_elf_for(_static_elf(0x28, 1), "armv7-unknown-linux-musleabihf")
    assert not is_static_elf_for(_static_elf(0x3E), "aarch64-unknown-linux-musl")
    dynamic = bytearray(_static_elf(0xB7))
    dynamic[64:68] = (3).to_bytes(4, "little")           # PT_INTERP: a dynamic program
    assert not is_static_elf_for(bytes(dynamic), "aarch64-unknown-linux-musl")
    assert not is_static_elf_for(b"#!/bin/sh\n", "aarch64-unknown-linux-musl")


def test_a_bundle_with_root_actions_is_built_only_with_the_helper_and_a_changed_one_is_noticed(tmp_path):
    from test_board_bundle import _helper, _plan, _selection, _signer
    from orchestrator.runtime.bundle import generator as gen
    plan = _plan(tmp_path, _selection(tmp_path, values={"stt": {"autostart": "yes"}}))
    store = gen.BundleStore(tmp_path / "state")
    with pytest.raises(gen.BundleError, match="need llmcf-install"):
        gen.build_bundle(plan, store, _signer())
    built = gen.build_bundle(plan, store, _signer(), helper=_helper(tmp_path, plan))
    assert built.needs_root and built.root_helper["target"] == "aarch64-unknown-linux-musl"
    assert store.check(built) == [] and (built.path / "llmcf-install.sig").is_file()
    (built.path / "llmcf-install").write_bytes(b"changed")
    assert any("llmcf-install changed" in p for p in store.check(built))


def test_the_report_parser_reads_llmcf_installs_lines():
    text = ("llmcf-bundle: helper llmcf-install 0.1.0\nllmcf-bundle: would create_user stt user=llmcf\n"
            "llmcf-bundle: result=plan changed=0 pending=1 root=1\n")
    report = inst.report(text)
    assert report.helper == "0.1.0" and report.result == "plan" and report.count("would") == 1 and report.ok
    assert inst.report("llmcf-bundle: result=needs-password changed=0 sudo asks\n").needs_password


# --- the real helper behind llmcf's gate on a fake board ------------------------------------------------------------

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the fake board runs llmcf's gate in POSIX sh and llmcf-install is a Linux program")
RUST_DIR = Path(__file__).resolve().parents[1] / "rust"
FAKE_USERADD = r'''#!/bin/sh
user=""; home=""; comment=""; shell=""
while [ $# -gt 0 ]; do
  case $1 in --home-dir) home=$2; shift ;; --comment) comment=$2; shift ;; --shell) shell=$2; shift ;;
    --*) ;; *) user=$1 ;; esac
  shift
done
grep -q "^$user:" "$LLMCF_TEST_ROOT/etc/passwd" && exit 9
echo "$user:x:990:990:$comment:$home:$shell" >> "$LLMCF_TEST_ROOT/etc/passwd"
echo "$user:x:990:" >> "$LLMCF_TEST_ROOT/etc/group"
'''
FAKE_USERDEL = r'''#!/bin/sh
grep -v "^$1:" "$LLMCF_TEST_ROOT/etc/passwd" > "$LLMCF_TEST_ROOT/etc/passwd.new"
mv "$LLMCF_TEST_ROOT/etc/passwd.new" "$LLMCF_TEST_ROOT/etc/passwd"
'''
FAKE_SYSTEMCTL = r'''#!/bin/sh
echo "systemctl $*" >> "$LLMCF_TEST_ROOT/calls.log"
mkdir -p "$LLMCF_TEST_ROOT/fake-enabled"
case $1 in
  is-enabled) [ -e "$LLMCF_TEST_ROOT/fake-enabled/$2" ] && { echo enabled; exit 0; }; echo disabled; exit 1 ;;
  enable) : > "$LLMCF_TEST_ROOT/fake-enabled/$3" ;;
  disable) rm -f "$LLMCF_TEST_ROOT/fake-enabled/$3" ;;
esac
'''
# sudo on the fake board: -n, or -S with the password on stdin when the test sets one; then the command with the
# account in SUDO_USER and the test build's --root PREFIX in front (the board's / is the test's folder)
FAKE_SUDO = r'''#!/bin/sh
nopass=; list=
while [ $# -gt 0 ]; do case $1 in -n) nopass=1; shift ;; -S) shift ;; -l) list=1; shift ;; -p) shift 2 ;;
  *) break ;; esac; done
fixed="@ROOT@/usr/local/libexec/llmcf/llmcf-install"; dropin="@ROOT@/etc/sudoers.d/llmcf-install"
allowed=
if [ "$1" = "$fixed" ] && [ -f "$dropin" ] && grep -q "NOPASSWD: .*$fixed $2 \*" "$dropin"; then allowed=1; fi
if [ -n "$list" ]; then
  [ -n "$allowed" ] && exit 0
  [ -s "@ROOT@/sudo-password" ] && exit 1
  exit 0
fi
if [ -z "$allowed" ] && [ -s "@ROOT@/sudo-password" ]; then
  [ -z "$nopass" ] || { echo "sudo: a password is required" >&2; exit 1; }
  IFS= read -r pw || pw=""
  [ "$pw" = "$(cat "@ROOT@/sudo-password")" ] || { echo "sudo: wrong password" >&2; exit 1; }
fi
[ "$1" = true ] && exit 0
case $1 in */llmcf-install) exec env SUDO_USER=board "$1" --root "@ROOT@" "$2" "$3" ;; esac
exec "$@"
'''
# install, visudo and su on the fake board: install drops -o/-g (the test's own user stands in for root), visudo
# checks that the rule names only llmcf-install, su takes root's password on stdin from @ROOT@/root-password
FAKE_INSTALL = r'''#!/bin/sh
for a in "$@"; do shift; case $skip in 1) skip=; continue ;; esac
  case $a in -o|-g) skip=1 ;; *) set -- "$@" "$a" ;; esac; done
exec @INSTALL@ "$@"
'''
FAKE_VISUDO = r'''#!/bin/sh
echo "visudo $*" >> "@ROOT@/calls.log"
[ "$1" = -cf ] || exit 0
grep -q "^[A-Za-z0-9._-]* ALL=(root) NOPASSWD: " "$2" && ! grep -q "NOPASSWD: ALL" "$2"
'''
FAKE_SU = r'''#!/bin/sh
[ "$1" = root ] && [ "$2" = -c ] || exit 1
IFS= read -r pw || pw=""
[ -s "@ROOT@/root-password" ] && [ "$pw" = "$(cat "@ROOT@/root-password")" ] || { echo "su: Authentication failure" >&2; exit 1; }
echo "su -c" >> "@ROOT@/calls.log"
exec sh -c "$3"
'''


@pytest.fixture(scope="module")
def helper_binary():
    """The test build of llmcf-install (cargo feature test-root), built once for this module."""
    given = os.environ.get("LLMCF_INSTALL_TEST_BIN")
    if given:
        return Path(given)
    if os.name == "nt":
        pytest.skip("llmcf-install is a Linux program; its tests run on Linux")
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo is not on this computer: these tests build llmcf-install's test binary with it")
    if not os.environ.get("RUSTUP_TOOLCHAIN") and shutil.which("rustup"):
        listed = subprocess.run(["rustup", "toolchain", "list"], capture_output=True, text=True).stdout
        pinned = next((line.split('"')[1] for line in (RUST_DIR / "rust-toolchain.toml").read_text().splitlines()
                       if line.startswith("channel")), "")
        if pinned and pinned not in listed:
            pytest.skip(f"the pinned Rust toolchain {pinned} (rust/rust-toolchain.toml) is not installed")
    done = subprocess.run([cargo, "build", "--locked", "-p", "llmcf-install", "--features", "test-root"],
                          cwd=RUST_DIR, capture_output=True, text=True, timeout=1800)
    if done.returncode != 0:
        pytest.fail("cargo could not build llmcf-install's test binary:\n" + done.stderr[-2000:])
    return RUST_DIR / "target" / "debug" / "llmcf-install"


def _root_board(tmp_path, helper_binary, tier="root"):
    """A fake board (test_board_bundle._fake_board) whose root is the test folder: its passwd names the board account
    (the test's own user, home /home), the board's programs are fakes, sudo runs the real llmcf-install test build."""
    from test_board_bundle import _fake_board
    board = _fake_board(tmp_path, tier)
    uid, gid = os.getuid(), os.getgid()
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc" / "passwd").write_text(f"root:x:0:0:root:/root:/bin/sh\nboard:x:{uid}:{gid}::/home:/bin/sh\n")
    (tmp_path / "etc" / "group").write_text(f"root:x:0:\nboard:x:{gid}:\n")
    for rel, text in (("usr/sbin/useradd", FAKE_USERADD), ("usr/sbin/userdel", FAKE_USERDEL),
                      ("usr/bin/systemctl", FAKE_SYSTEMCTL), ("usr/sbin/nologin", "#!/bin/sh\nexit 1\n"),
                      ("shim/sudo", FAKE_SUDO.replace("@ROOT@", str(tmp_path))),
                      ("shim/install", FAKE_INSTALL.replace("@INSTALL@", shutil.which("install") or "/usr/bin/install")),
                      ("shim/visudo", FAKE_VISUDO.replace("@ROOT@", str(tmp_path))),
                      ("shim/su", FAKE_SU.replace("@ROOT@", str(tmp_path))),
                      # the board account's name (a CI runner or sandbox may run the tests as root)
                      ("shim/id", '#!/bin/sh\n[ "$*" = -un ] && { echo board; exit 0; }\nexec '
                                  + (shutil.which("id") or "/usr/bin/id") + ' "$@"\n')):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        path.chmod(0o755)
    board.root = tmp_path
    return board


def _ready(shell, built, password="", mode="private"):
    """The board's root readiness for the bundle, set up through the gate (what the person's yes does)."""
    from orchestrator.runtime.bundle import readiness
    from orchestrator.runtime.bundle.board import helper_sha256
    sha = helper_sha256(built)
    found = readiness.check(shell.run, sha)
    return readiness.setup(shell.run, built.bundle_id, sha, found, mode=mode, password=password)


@pytest.fixture
def sudo_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path / 'shim'}{os.pathsep}{tmp_path / 'h' / 'shim'}{os.pathsep}"
                               f"{os.environ.get('PATH', '/usr/bin:/bin')}")


def _autostart_bundle(tmp_path, board, helper_binary):
    from test_board_bundle import _built, _selection
    return _built(tmp_path, board, selection=_selection(tmp_path, values={"stt": {"autostart": "yes"}}),
                  helper_program=helper_binary.read_bytes())


@POSIX
def test_the_root_actions_run_through_llmcf_install_behind_the_gate(tmp_path, helper_binary, sudo_on_path):
    from test_board_bundle import DEVICE, GateShell
    from orchestrator.runtime.bundle.board import run_bundle, run_root
    board = _root_board(tmp_path, helper_binary)
    store, built = _autostart_bundle(tmp_path, board, helper_binary)
    shell = GateShell(board)
    first = run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    assert first.report.result == "stopped-root", first.lines()
    before = run_root(shell, built, "plan")
    assert before.report.result == "not-ready" and not before.ok, before.lines()
    done = _ready(shell, built)
    assert done.ok and "helper" in done.changed, done
    fixed = tmp_path / "usr/local/libexec/llmcf/llmcf-install"
    assert fixed.read_bytes() == helper_binary.read_bytes() and oct(fixed.stat().st_mode & 0o777) == "0o755"
    plan = run_root(shell, built, "plan")
    assert plan.ok and plan.report.result == "plan" and plan.report.helper == "0.1.0", plan.lines()
    unit_path = tmp_path / "etc/systemd/system/llmcf-stt.service"
    assert plan.report.count("would") == 3 and not unit_path.exists()
    applied = run_root(shell, built, "apply")
    assert applied.ok and applied.report.result == "ok", applied.lines()
    unit = unit_path.read_text()
    assert "User=llmcf\n" in unit and "ProtectSystem=strict\n" in unit and "NoNewPrivileges=yes\n" in unit
    copy = tmp_path / "var/lib/llmcf/bundle/voice-test/stt"
    assert (copy / "start.sh").is_file() and (copy / "model.bin").is_file()
    assert "llmcf bundle account" in (tmp_path / "etc/passwd").read_text()
    again = run_root(shell, built, "apply")
    assert again.report.changed == 0 and "nothing changed" in again.report.detail, again.lines()
    assert run_root(shell, built, "status").report.result == "installed"
    gone = run_root(shell, built, "uninstall")
    assert gone.ok and gone.report.result == "ok", gone.lines()
    assert not unit_path.exists() and not copy.exists()
    assert "llmcf:" not in (tmp_path / "etc/passwd").read_text()
    assert run_bundle(shell, built, "uninstall").report.result == "ok"


@POSIX
def test_a_changed_helper_or_a_home_signer_never_runs_as_root(tmp_path, helper_binary, sudo_on_path):
    from test_board_bundle import GateShell
    from orchestrator.runtime.bundle.board import run_bundle, run_root
    board = _root_board(tmp_path, helper_binary)
    _store, built = _autostart_bundle(tmp_path, board, helper_binary)
    shell = GateShell(board)
    assert run_bundle(shell, built, "apply").report.result == "stopped-root"
    kept = board.home / inst.BUNDLE_REL / "voice-test" / ".kept" / "llmcf-install"
    kept.write_bytes(kept.read_bytes() + b"\0")
    done = _ready(shell, built)
    assert (done.ok, done.reason) == (False, "not-signed"), done
    assert not (tmp_path / "usr/local/libexec/llmcf/llmcf-install").exists()
    run = run_root(shell, built, "apply")
    assert run.report.result == "not-ready", run.lines()
    assert not (tmp_path / "etc/systemd/system/llmcf-stt.service").exists()
    home_board = _root_board(tmp_path / "h", helper_binary, tier="home")
    _store2, built2 = _autostart_bundle(tmp_path / "h", home_board, helper_binary)
    shell2 = GateShell(home_board)
    assert run_bundle(shell2, built2, "apply").report.result == "stopped-root"
    run2 = run_root(shell2, built2, "apply")
    assert run2.report.result == "refused" and "root-owned signing key" in run2.report.detail, run2.lines()


@POSIX
def test_sudo_with_a_password_runs_only_with_the_persons_password(tmp_path, helper_binary, sudo_on_path):
    from test_board_bundle import GateShell
    from orchestrator.runtime.bundle.board import run_bundle, run_root
    board = _root_board(tmp_path, helper_binary)
    (tmp_path / "sudo-password").write_text("correct horse")
    _store, built = _autostart_bundle(tmp_path, board, helper_binary)
    shell = GateShell(board)
    assert run_bundle(shell, built, "apply").report.result == "stopped-root"
    assert _ready(shell, built).reason == "needs-password"
    assert _ready(shell, built, password="correct horse").ok
    (tmp_path / "etc/sudoers.d/llmcf-install").unlink()   # without the drop-in, sudo asks for the password again
    asked = run_root(shell, built, "plan")
    assert asked.report.needs_password and not asked.ok, asked.lines()
    wrong = run_root(shell, built, "plan", password="wrong")
    assert wrong.report.result == "sudo-refused", wrong.lines()
    right = run_root(shell, built, "plan", password="correct horse")
    assert right.ok and right.report.result == "plan", right.lines()


def test_the_root_operation_takes_only_its_actions_and_ids():
    from orchestrator.remote import gate
    from orchestrator.runtime.device import board_ops  # noqa: F401 - defines every board operation
    for args in (("run-shell", "voice-test"), ("apply", "../x"), ("apply", "Voice"), ("apply; id", "x")):
        with pytest.raises(gate.GateRefused):
            gate.script("bundle-root", *args)
    assert "bundle-root" in {op.verb for op in gate.gate_operations()}
    with pytest.raises(gate.GateRefused):
        gate.script("bundle-root", "apply", "voice-test")   # the sha256 of the bundle's llmcf-install is needed
    text = gate.script("bundle-root", "apply", "voice-test", "a" * 64)
    assert "sudo -n" in text and "sudo -S" in text and "LLMCF_HOME_GATE=; llmcf_trusted_signers" in text


def test_an_ai_never_reaches_a_root_action():
    """bundle_deploy (the one MCP tool that changes a board) runs the unprivileged installer only: no tool module
    calls llmcf-install's run or names its gate operation, and its facts say where root actions run."""
    root = Path(__file__).resolve().parents[1] / "orchestrator" / "tools"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "run_root" not in text and "bundle-root" not in text, path.name
        # the root readiness: an AI may read it, never set it up or remove it
        assert "ready_setup" not in text and "ready_remove" not in text and "bundle-ready-" not in text, path.name
    text = (root / "tools_bundle.py").read_text(encoding="utf-8")
    assert "never from here" in text and "never through this tool" in text


# --- the person's channels: `llmcf bundle ...` with the real helper on a fake board --------------------------------

@pytest.fixture
def cli_root_board(tmp_path, monkeypatch, helper_binary, sudo_on_path):
    """`llmcf bundle` with its per-user folder in tmp_path/state, a stored board behind llmcf's gate whose sudo runs
    the real llmcf-install test build (tests/test_bundle_ui.cli_board, plus the helper)."""
    return _cli_root_board(tmp_path, monkeypatch, helper_binary)


def _cli_root_board(tmp_path, monkeypatch, helper_binary, tier="root"):
    from rich.console import Console
    from test_board_bundle import GateShell, _files
    from test_bundle_ui import _stored_board
    from orchestrator import cli as cli_module
    from orchestrator.runtime.bundle import service
    from orchestrator.runtime.bundle.root_helper import HelperBuild
    from orchestrator.runtime.device import remote
    from orchestrator.utils.helpers import sha256_file
    state = tmp_path / "state"
    _stored_board(state)
    board = _root_board(tmp_path, helper_binary, tier=tier)
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(cli_module, "_open_device", lambda *a, **kw: (GateShell(board), "key"))
    monkeypatch.setattr(cli_module, "_bundle_signer", lambda ctx: board.signer)
    monkeypatch.setattr(cli_module, "console", Console(width=1000, color_system=None))
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    made = HelperBuild(helper_binary, sha256_file(helper_binary), "0.1.0", "aarch64-unknown-linux-musl", "test")
    monkeypatch.setattr(service, "helper_for", lambda framework, arch, say=None: made)
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    _model, wmodel, prog = _files(tmp_path)
    board.state = state
    # the block with a whisper-server the person picks (the stt preset's block takes the framework's own build)
    board.choice = ["--block", "stt-whisper-cpp-program", "--file", f"stt.model={wmodel}", "--file", f"stt.program={prog}",
                    "--licence", "stt.model=MIT", "--set", "stt.autostart=yes"]
    board.root = tmp_path
    return board


def _cli_bundle(board):
    from test_cli_builds import invoke_plain
    from orchestrator.runtime.bundle import generator as gen
    res = invoke_plain("bundle", "build", "ZERO 3W", *board.choice)
    assert res.code == 0 and "Built bundle" in res.out, res.out
    [built] = gen.BundleStore(board.state).list()
    assert built.needs_root and built.root_helper["sha256"]
    return built.bundle_id


@POSIX
def test_the_cli_deploy_asks_a_second_time_before_the_root_actions(cli_root_board):
    from test_cli_builds import invoke_plain
    bid = _cli_bundle(cli_root_board)
    unit = cli_root_board.root / "etc/systemd/system/llmcf-stt.service"
    res = invoke_plain("bundle", "deploy", bid, input="y\n\n")     # yes to the apply, the default No to the readiness
    assert res.code == 1 and "Set up the root readiness" in res.out and "prepare-root" in res.out, res.out
    assert not unit.exists()
    res = invoke_plain("bundle", "deploy", bid, input="y\ny\n\n")  # the readiness yes, the default No to root
    assert res.code == 0 and "stopped-root" in res.out and "Run the 3 root action(s)" in res.out, res.out
    assert "root readiness is set up" in res.out, res.out
    assert "would create_user" in res.out and not unit.exists()
    res = invoke_plain("bundle", "deploy", bid, input="y\ny\n")
    assert res.code == 0 and "done install_unit" in res.out and unit.is_file(), res.out
    res = invoke_plain("bundle", "status", bid)
    assert res.code == 0 and "already install_unit" in res.out and "result: installed" in res.out, res.out
    res = invoke_plain("bundle", "rollback", bid, "--root", input="y\n")
    assert res.code == 0 and not unit.exists(), res.out
    res = invoke_plain("bundle", "deploy", bid, "--skip-root", input="y\n")
    assert res.code == 0 and "--skip-root" in res.out and not unit.exists(), res.out
    assert invoke_plain("bundle", "deploy", bid, input="y\ny\n").code == 0 and unit.is_file()
    res = invoke_plain("bundle", "uninstall", bid, input="y\ny\n")
    assert res.code == 0 and "would-remove user-created llmcf" in res.out and not unit.exists(), res.out
    assert "llmcf:" not in (cli_root_board.root / "etc/passwd").read_text()


@POSIX
def test_the_cli_asks_for_the_sudo_password_in_private_mode_only(cli_root_board, monkeypatch):
    from test_cli_builds import invoke_plain
    from orchestrator import cli as cli_module
    (cli_root_board.root / "sudo-password").write_text("correct horse")
    bid = _cli_bundle(cli_root_board)
    unit = cli_root_board.root / "etc/systemd/system/llmcf-stt.service"
    monkeypatch.setattr(cli_module, "_framework_mode", lambda ctx: "enterprise")
    # the one-time setup takes the password (in enterprise mode too); the root actions then run with sudo -n only
    res = invoke_plain("bundle", "deploy", bid, input="y\ny\ncorrect horse\nn\n")
    assert res.code == 0 and "root readiness is set up" in res.out and not unit.exists(), res.out
    (cli_root_board.root / "etc/sudoers.d/llmcf-install").unlink()
    res = invoke_plain("bundle", "deploy", bid, input="y\n")
    assert res.code == 1, res.out
    assert "correct horse" not in res.out
    assert "enterprise mode runs root actions only with sudo -n" in res.out and "root readiness" in res.out, res.out
    monkeypatch.setattr(cli_module, "_framework_mode", lambda ctx: "private")
    # private mode: without the drop-in, the person's password for this run (sent on stdin, never kept)
    res = invoke_plain("bundle", "deploy", bid, input="y\ncorrect horse\ny\n")
    assert res.code == 0 and unit.is_file(), res.out
    assert "correct horse" not in res.out


@POSIX
def test_a_board_where_llmcf_install_cannot_run_as_root_keeps_its_uninstall(tmp_path, monkeypatch, helper_binary,
                                                                            sudo_on_path):
    """A full bundle deployed to a board without root-owned signers (the home tier: root actions never run there)
    stops before the root actions as before; its status, uninstall and rollback still work, because llmcf-install
    can have made nothing there (bundle-root says absent for them; plan and apply stay refused)."""
    from test_cli_builds import invoke_plain
    from orchestrator.runtime.bundle.board import run_root
    from test_board_bundle import GateShell
    board = _cli_root_board(tmp_path, monkeypatch, helper_binary, tier="home")
    bid = _cli_bundle(board)
    res = invoke_plain("bundle", "deploy", bid, input="y\n")
    assert res.code == 1 and "stopped-root" in res.out and "root-owned signing key" in res.out, res.out
    from orchestrator.runtime.bundle import generator as gen
    built = gen.BundleStore(board.state).get(bid)
    for action in ("status", "plan-uninstall", "uninstall", "rollback"):
        run = run_root(GateShell(board), built, action)
        assert run.ok and run.report.result == "absent", (action, run.lines())
    assert run_root(GateShell(board), built, "apply").report.result == "refused"
    res = invoke_plain("bundle", "status", bid)
    assert res.code == 0 and ("result: partial" in res.out or "result: installed" in res.out), res.out
    res = invoke_plain("bundle", "uninstall", bid, input="y\n")
    assert res.code == 0, res.out
    kept = board.home / inst.BUNDLE_REL / bid / ".kept"
    assert not kept.exists(), res.out


@POSIX
def test_the_cli_status_of_the_root_part_never_fails_the_status_for_a_missing_password(cli_root_board,
                                                                                         monkeypatch):
    """bundle status changes nothing: when the board's sudo asks for a password that enterprise mode never types
    (or nobody is at a terminal), the installer's status stands with its exit code and a note says the root part's
    status was not read."""
    from test_cli_builds import invoke_plain
    from orchestrator import cli as cli_module
    (cli_root_board.root / "sudo-password").write_text("correct horse")
    bid = _cli_bundle(cli_root_board)
    res = invoke_plain("bundle", "deploy", bid, input="y\ny\ncorrect horse\ny\n")
    assert res.code == 0, res.out
    (cli_root_board.root / "etc/sudoers.d/llmcf-install").unlink()   # sudo asks for the password again
    monkeypatch.setattr(cli_module, "_framework_mode", lambda ctx: "enterprise")
    res = invoke_plain("bundle", "status", bid)
    assert res.code == 0 and "result: installed" in res.out, res.out
    assert "status of the root actions was not read" in res.out, res.out
    monkeypatch.setattr(cli_module, "_framework_mode", lambda ctx: "private")
    monkeypatch.setattr(cli_module, "_interactive", lambda: False)
    res = invoke_plain("bundle", "status", bid)
    assert res.code == 0 and "status of the root actions was not read" in res.out, res.out
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "status", bid, input="\n")
    assert res.code == 0 and "status of the root actions was not read" in res.out, res.out
    res = invoke_plain("bundle", "status", bid, input="correct horse\n")
    assert res.code == 0 and "already install_unit" in res.out, res.out
