"""The board bundle, deploy way 3 (orchestrator/runtime/bundle/): the manifest schema and its checked variables, the
building blocks, the generator's plan and its signed store, and the unprivileged on-board installer behind llmcf's
real gate on fake boards (OpenSSH or Dropbear with sudo: root-owned signers; Dropbear without sudo: the home gate).

The fake boards run the gate and the installer under this computer's /bin/sh in a temporary home folder, never this
computer's own; they need a POSIX shell and ssh-keygen or openssl, and skip on Windows with their reason. Every name,
address and key here is a test value (documentation addresses, a key made for the test)."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.remote import gate
from orchestrator.remote.transport import Result
from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import generator as gen
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.manifest import (ManifestError, StartSpec, check_value,
                                                  load_manifest)
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines every board operation
from orchestrator.runtime.profile import parse_profile, read_profile

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the gate and the installer are POSIX sh programs; Windows has no /bin/sh")
VERIFIER = pytest.mark.skipif(shutil.which("ssh-keygen") is None and shutil.which("openssl") is None,
                              reason="the board checks the framework's signature with ssh-keygen or openssl")
PROBES = Path(__file__).resolve().parent / "fixtures" / "hardware_probe"
RK3566 = read_profile(PROBES / "schema2_rk3566_dietpi.txt")   # arm64, systemd, 3912 MB
DEVICE = "0123456789abcdef"
ID16 = "fedcba9876543210"
GGUF = b"GGUF" + b"\0" * (2 * 1024 * 1024)
WHISPER = b"lmgg" + b"\1" * (2 * 1024 * 1024)


def _signer():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from orchestrator.Core.community_pack import BoardSigner
    return BoardSigner(Ed25519PrivateKey.generate())


def _profile(**values):
    data = dict(RK3566.values)
    data.update(values)
    return parse_profile("".join(f"{k}={v}\n" for k, v in data.items() if v is not None))


def _elf(machine: int) -> bytes:
    head = bytearray(b"\x7fELF\x02\x01\x01" + b"\0" * 9)
    head += (2).to_bytes(2, "little") + machine.to_bytes(2, "little") + b"\0" * 46
    return bytes(head) + b"#!/bin/sh\n"


def _runtime_tarball(tmp: Path) -> gen.RuntimePackage:
    """A runtime tarball as deploy way 2 ships it: <id16>/ with its start script and SHA256SUMS (the start script's
    keygen writes the key file, as the real one does)."""
    script = (b"#!/bin/sh\ncase $1 in keygen) mkdir -p \"$HOME/.config/llm-framework\" && "
              b"umask 077 && echo key > \"$HOME/.config/llm-framework/llama-server.key\" ;; esac\n")
    files = {f"{ID16}/llama-server.sh": script, f"{ID16}/bin/llama-server": b"#!/bin/sh\necho server\n"}
    sums = "".join(f"{hashlib.sha256(v).hexdigest()}  {k.split('/', 1)[1]}\n" for k, v in files.items())
    files[f"{ID16}/SHA256SUMS"] = sums.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    path = tmp / "runtime.tar.gz"
    path.write_bytes(buf.getvalue())
    return gen.RuntimePackage(path, hashlib.sha256(buf.getvalue()).hexdigest(), ID16 + "0" * 48)


def _files(tmp: Path, *, program_machine: int = 0xB7):
    model = tmp / "model.gguf"
    model.write_bytes(GGUF)
    wmodel = tmp / "ggml-base.bin"
    wmodel.write_bytes(WHISPER)
    prog = tmp / "whisper-server"
    prog.write_bytes(_elf(program_machine))
    return model, wmodel, prog


def _selection(tmp: Path, **kw) -> gen.Selection:
    model, wmodel, prog = _files(tmp)
    values = kw.pop("values", {})
    return gen.Selection(blocks=kw.pop("blocks", ["llm-llama-server", "stt-whisper-cpp"]), values=values, files={
        "llm": {"model": gen.FileChoice(path=str(model), licence="apache-2.0")},
        "stt": {"model": gen.FileChoice(path=str(wmodel), licence="MIT"), "program": gen.FileChoice(path=str(prog))},
    }, **kw)


def _plan(tmp: Path, selection=None, **kw):
    kw.setdefault("runtime", _runtime_tarball(tmp))
    return gen.plan_bundle(selection or _selection(tmp), catalog=blk.load_catalog(kw.pop("app_root", None)),
                           profile=kw.pop("profile", RK3566), device_id=DEVICE, offline=kw.pop("offline", False),
                           bundle_id=kw.pop("bundle_id", "voice-test"), **kw)


# --- the manifest and its variables ----------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, value", [
    ("word", "a'b"), ("word", 'a"b'), ("word", "a\nb"), ("word", "$(id)"), ("word", "`id`"), ("word", "../x"),
    ("word", "-rf"), ("word", "a b"), ("word", "a;b"), ("file", "../../.ssh/authorized_keys"), ("file", ".hidden"),
    ("user", "root"), ("user", "Bad User"), ("port", "80"), ("port", "8080; rm -rf ~"), ("port", True),
    ("threads", "-1"), ("memory_mb", "1e9"), ("bool", "maybe"),
])
def test_a_variable_that_is_not_its_type_is_refused_never_escaped(kind, value):
    with pytest.raises(ManifestError):
        check_value(kind, value)


def test_variables_come_back_in_their_canonical_form():
    assert check_value("port", " 8080 ") == 8080
    assert check_value("bool", "yes") is True
    assert check_value("choice", "de", choices=("auto", "de")) == "de"
    assert check_value("user", "llmcf") == "llmcf"


@pytest.mark.parametrize("args", [["--port", "8080;id"], ["$(id)"], ["a'b"], ["@DIR@/../x"], ["a b"], ["x\ny"]])
def test_a_start_argument_with_shell_characters_is_refused(args):
    with pytest.raises(Exception):
        StartSpec(program="whisper-server", args=tuple(args))


def test_the_manifest_reads_json_and_yaml_and_refuses_what_its_schema_does_not_take(tmp_path):
    plan = _plan(tmp_path)
    assert plan.ok, plan.problems
    data = plan.manifest.as_json()
    assert load_manifest(json.dumps(data)) == plan.manifest
    import yaml
    assert load_manifest(yaml.safe_dump(data)) == plan.manifest
    for change in ({"schema": "llmcf-bundle/2"}, {"extra": 1}, {"bundle_id": "Bad Id"}, {"mode": "root"}):
        with pytest.raises(ManifestError):
            load_manifest({**data, **change})
    bad = json.loads(json.dumps(data))
    bad["components"][0]["files"][0]["url"] = "https://example.com/x"
    with pytest.raises(ManifestError):
        load_manifest(bad)   # a file of the bundle has no URL
    bad = json.loads(json.dumps(data))
    bad["root_actions"] = [{"action": "run_shell", "component": "llm"}]
    with pytest.raises(ManifestError):
        load_manifest(bad)   # root steps are typed actions only, never shell


@pytest.mark.parametrize("url", ["https://user:pw@example.com/m.gguf", "https://example.com/a b",
                                 "https://example.com/$(id)", "ftp://example.com/m", "https://example.com/'x'"])
def test_an_online_item_takes_only_a_plain_https_address(url):
    with pytest.raises(Exception):
        from orchestrator.runtime.bundle.manifest import FileEntry
        FileEntry(name="m.gguf", sha256="0" * 64, size=1, role="model", source="online", url=url)


# --- the building blocks ---------------------------------------------------------------------------------------------

def test_the_framework_ships_llm_and_stt_blocks_and_the_others_as_documented_templates():
    catalog = blk.load_catalog()
    assert catalog.notes == []
    assert {b.id for b in catalog.ready()} == {"llm-llama-server", "stt-whisper-cpp", "gateway-llmcf"}
    templates = {b.kind for b in catalog.blocks.values() if not b.ready}
    assert templates == {"tts", "vision", "harness"}
    for block in catalog.blocks.values():
        assert block.trust == "framework"
        assert blk.check_block_folder(block.folder) == []
        assert "## Models" in block.guide()
    assert "mmproj" in catalog.get("vision-mmproj").guide() and "sherpa-onnx" in catalog.get("tts-sherpa-onnx").guide()


def test_a_block_folder_holds_only_its_spec_and_guide_and_the_wizard_writes_one(tmp_path):
    made = blk.new_block(tmp_path, "stt-mine", kind="stt", title="My STT", template="stt-whisper-cpp")
    assert blk.check_block_folder(made) == []
    (made / "run.sh").write_text("id\n")
    assert any("run.sh" in p for p in blk.check_block_folder(made))
    with pytest.raises(blk.BlockError):
        blk.new_block(tmp_path, "llm-llama-server", kind="llm", title="x")   # the framework's own id
    with pytest.raises(blk.BlockError):
        blk.new_block(tmp_path, "../x", kind="llm", title="x")


@pytest.mark.parametrize("arg", ["$(id)", "a;b", "{nope}", "@FILE:nope@"])
def test_a_block_whose_start_holds_anything_but_checked_tokens_is_refused(tmp_path, arg):
    text = (blk.BUILTIN_DIR / "stt-whisper-cpp" / "block.yml").read_text().replace('"{port}"', json.dumps(arg))
    with pytest.raises(blk.BlockError):
        blk.parse_block(text, tmp_path)


# --- the generator's plan --------------------------------------------------------------------------------------------

def test_the_plan_resolves_every_file_and_names_its_needs(tmp_path):
    plan = _plan(tmp_path)
    assert plan.ok, plan.problems
    comps = {c.id: c for c in plan.manifest.components}
    assert comps["llm"].start.program == f"runtime/{ID16}/llama-server.sh"
    assert comps["llm"].start.args[:2] == ("serve", "@DIR@/model.gguf") and comps["llm"].keygen
    assert "--threads" not in comps["llm"].start.args          # 0: llama.cpp decides
    assert comps["stt"].start.args[:4] == ("--host", "127.0.0.1", "--port", "8090")
    assert {f.role: f.format for f in comps["stt"].files} == {"program": "elf", "model": "ggml-whisper"}
    assert plan.manifest.root_actions == () and plan.manifest.mode == "full"
    assert plan.board_memory_mb == 3912 - 391 and plan.memory_mb > 0
    labels = json.dumps(plan.labels())
    assert str(tmp_path) not in labels and "llmcf" not in json.dumps(plan.labels()["components"])


@pytest.mark.parametrize("change, expect", [
    ({"runtime": None}, "runtime package is not built"),
    ({"profile": _profile(RAM_TOTAL_MB="512")}, "memory"),
    ({"selection_values": {"llm": {"port": "8090"}}}, "both want port 8090"),
    ({"selection_values": {"llm": {"port": "8080; rm -rf ~"}}}, "whole number"),
    ({"selection_values": {"stt": {"language": "$(id)"}}}, "one of"),
    ({"selection_values": {"llm": {"nope": 1}}}, "has no variable nope"),
    ({"selection_values": {"llm": {"autostart": "yes"}}, "profile": _profile(INIT_SYSTEM="openrc")}, "systemd"),
])
def test_the_plan_refuses_what_does_not_fit_the_board_or_the_block(tmp_path, change, expect):
    selection = _selection(tmp_path, values=change.pop("selection_values", {}))
    plan = _plan(tmp_path, selection, **change)
    assert not plan.ok and any(expect in p for p in plan.problems), plan.problems


def test_a_file_of_another_format_architecture_or_without_its_licence_is_refused(tmp_path):
    selection = _selection(tmp_path)
    (tmp_path / "model.gguf").write_bytes(b"NOPE" + b"\0" * 2 * 1024 * 1024)
    (tmp_path / "whisper-server").write_bytes(_elf(0x3E))      # x86-64 on an arm64 board
    selection.files["stt"]["model"].licence = None
    problems = _plan(tmp_path, selection).problems
    assert any("no gguf file" in p for p in problems)
    assert any("built for x86_64" in p for p in problems)
    assert any("name the licence" in p for p in problems)


def test_a_template_block_is_listed_but_not_built(tmp_path):
    plan = _plan(tmp_path, gen.Selection(blocks=["tts-sherpa-onnx"]))
    assert any("documented template" in p for p in plan.problems)


def test_autostart_lists_typed_root_actions_for_llmcf_install(tmp_path):
    plan = _plan(tmp_path, _selection(tmp_path, values={"stt": {"autostart": "yes", "memory_limit_mb": "900"}}))
    assert plan.ok, plan.problems
    assert [a.action for a in plan.manifest.root_actions] == ["create_user", "install_unit", "set_cgroup"]
    assert plan.manifest.root_actions[2].memory_max_mb == 900
    assert any("llmcf-install" in n for n in plan.notes)


def test_an_online_item_in_offline_mode_must_be_in_the_own_network(tmp_path):
    selection = _selection(tmp_path)
    selection.files["llm"]["model"] = gen.FileChoice(url="https://models.example.com/m.gguf", sha256="a" * 64,
                                                     size=3 * 1024 * 1024, licence="mit")
    from orchestrator.utils.locality import Locality
    far = lambda url, config: Locality("models.example.com", False, "public", ("203.0.113.7",), ("203.0.113.7",))
    near = lambda url, config: Locality("models.example.com", True, "private", ("192.0.2.10",))
    assert any("offline mode" in p for p in _plan(tmp_path, selection, offline=True, locality=far).problems)
    plan = _plan(tmp_path, selection, offline=True, locality=near)
    assert plan.ok, plan.problems
    online = [f for c in plan.manifest.components for f in c.files if f.source == "online"]
    assert online and online[0].url.startswith("https://") and "url" not in json.dumps(plan.labels())


def test_enterprise_mode_takes_only_framework_and_indexed_blocks_private_mode_warns_for_own(tmp_path):
    app = tmp_path / "app"
    blk.new_block(app / "community" / "blocks", "stt-mine", kind="stt", title="Mine", template="stt-whisper-cpp")
    selection = _selection(tmp_path, blocks=["stt-mine"])
    del selection.files["llm"]
    enterprise = _plan(tmp_path, selection, app_root=app, mode="enterprise")
    assert any("enterprise mode takes only" in p for p in enterprise.problems)
    private = _plan(tmp_path, selection, app_root=app)
    assert private.own_blocks == ["stt-mine"] and not private.ok
    assert _plan(tmp_path, selection, app_root=app, allow_own=True).ok


def test_the_bundle_is_built_signed_and_a_changed_copy_is_noticed(tmp_path):
    plan = _plan(tmp_path)
    store = gen.BundleStore(tmp_path / "state")
    with pytest.raises(gen.BundleError):
        gen.build_bundle(plan, store, None)        # never unsigned
    built = gen.build_bundle(plan, store, _signer())
    assert store.check(built) == [] and store.get("voice-test").manifest == plan.manifest
    assert (built.path / "install.sh").read_text() == inst.installer_sh()
    payload = built.payload()
    payload[0][1].write_bytes(b"changed")
    assert any("changed since" in p for p in store.check(built))


# --- the installer behind the real gate on a fake board -------------------------------------------------------------

class GateShell:
    """A board session to a fake board: every command goes to the board's gate as the framework's restricted key
    sends it (gate_call), stdin as the session's uploads send it."""

    def __init__(self, board):
        self.board = board

    def _line(self, script):
        return gate.gate_call(script)

    def run(self, script, *, stdin=None, timeout=60, limit=None, again=False, step="command"):
        data = b"".join(stdin) if stdin is not None else b""
        done = self.board(self._line(script), data)
        if getattr(script, "verb", "") == "facts":   # a CI runner or sandbox may run the tests as root
            import re
            out = re.sub(r"(?m)^machine=.*$", "machine=aarch64", done.out.replace("uid=0\n", "uid=1000\n"))
            done = Result(done.status, out, done.err)
        return done

    def upload(self, path, remote, sha):
        done = self.board(self._line(gate.script("file-write", remote + ".part")), Path(path).read_bytes())
        assert done.status == 0, done
        assert self.board(self._line(gate.script("file-move", remote + ".part", remote))).status == 0

    def run_detached(self, folder, job, *, timeout, poll, step):
        started = self.board(self._line(gate.script("start-" + job.verb, folder, *job.args)))
        assert started.status == 0 and "started=" in started.out, started
        for _ in range(1200):
            done = self.board(self._line(gate.script("job-poll", folder)))
            if done.out.strip().isdigit():
                return int(done.out.strip())
            time.sleep(0.05)
        raise AssertionError("the job did not end")


def _fake_board(tmp_path, tier: str):
    """A board with llmcf's gate. tier root: the signers file root-owned (OpenSSH or Dropbear with sudo; this test's
    own user stands in for root); home: Dropbear without sudo, the home gate with the account's own signing folder."""
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the fake board runs llmcf's gate and installer in POSIX sh; Windows has no /bin/sh")
    home = tmp_path / "home"
    (home / gate.INSTALL_ROOT_REL).mkdir(parents=True)
    signer = _signer()
    if tier == "root":
        etc = tmp_path / "etc" / "llmcf"
        etc.mkdir(parents=True)
        for folder in (etc.parent, etc):
            folder.chmod(0o755)
        signers = etc / "allowed_signers"
        signers.write_text(signer.allowed_signers + "\n")
        (etc / "signing_key.pem").write_text(signer.public_pem)
        ours = gate.signed_sh(signers.as_posix(), os.getuid())
    else:
        folder = home / gate.HOME_SIGNING_REL
        folder.mkdir(parents=True)
        for path in (home, home / ".config", folder.parent, folder):
            path.chmod(0o700)
        (folder / "allowed_signers").write_text(signer.allowed_signers + "\n")
        (folder / "signing_key.pem").write_text(signer.public_pem)
        ours = gate.signed_sh((tmp_path / "nowhere" / "llmcf" / "allowed_signers").as_posix(), 4242)   # no root-owned gate
    quoted = "'\"'\"'"
    text = gate.render_gate().replace(gate.signed_sh(), ours)
    text = text.replace(gate.signed_sh().replace("'", quoted), ours.replace("'", quoted))
    # the root readiness's fixed places (llmcf-install and its sudoers drop-in) inside the test folder
    from orchestrator.runtime.bundle import gate_ops
    text = text.replace(gate_ops.places_sh(), gate_ops.places_sh(
        (tmp_path / "usr/local/libexec/llmcf/llmcf-install").as_posix(),
        (tmp_path / "etc/sudoers.d/llmcf-install").as_posix()))
    path = tmp_path / "llmcf-gate"
    path.write_text(text)

    def run(line, stdin=b""):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "SSH_ORIGINAL_COMMAND": line}
        done = subprocess.run(["sh", str(path)], input=stdin, env=env, capture_output=True, timeout=120)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    run.home, run.signer = home, signer
    run.components = home / inst.BUNDLE_REL / "voice-test" / "components"
    return run


def _helper(tmp_path, plan, program: bytes = b"#!/bin/sh\necho llmcf-install stand-in\n"):
    """llmcf-install for a plan with root actions (a bundle with them always carries it): a stand-in program, or the
    caller's (tests/test_llmcf_install.py gives the real test build)."""
    from orchestrator.runtime.bundle.root_helper import HelperBuild, target_of
    if not gen.needs_root(plan.manifest):
        return None
    path = tmp_path / "llmcf-install.stand-in"
    path.write_bytes(program)
    return HelperBuild(path, hashlib.sha256(program).hexdigest(), "0.1.0", target_of(plan.manifest.board.arch), "test")


def _built(tmp_path, board, **kw):
    helper_program = kw.pop("helper_program", None)
    plan = _plan(tmp_path, kw.pop("selection", None), **kw)
    assert plan.ok, plan.problems
    store = gen.BundleStore(tmp_path / "state")
    helper = _helper(tmp_path, plan, helper_program) if helper_program else _helper(tmp_path, plan)
    return store, gen.build_bundle(plan, store, board.signer, helper=helper)


@pytest.fixture(params=["root", "home"])
def board(tmp_path, request):
    return _fake_board(tmp_path, request.param)


@POSIX
@VERIFIER
def test_plan_changes_nothing_apply_installs_and_a_second_apply_is_a_no_op(tmp_path, board):
    from orchestrator.runtime.bundle.board import run_bundle
    store, built = _built(tmp_path, board)
    shell = GateShell(board)
    planned = run_bundle(shell, built, "plan", store=store, device_id=DEVICE)
    assert planned.ok and planned.report.result == "plan" and planned.report.count("would") == 10, planned.lines()
    assert not board.components.exists()
    applied = run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    assert applied.ok and applied.report.result == "ok" and applied.report.changed == 10, applied.lines()
    llm = board.components / "llm"
    assert (llm / "model.gguf").read_bytes() == GGUF
    assert (llm / "runtime" / ID16 / "bin" / "llama-server").is_file()
    assert (board.home / ".config" / "llm-framework" / "llama-server.key").is_file()
    start = (llm / "start.sh").read_text()
    assert f'"$llmcf_dir"/runtime/{ID16}/llama-server.sh \'serve\' "$llmcf_dir"/model.gguf \'--port\' \'8080\'' in start
    assert "env -i" in start
    assert store.boards("voice-test")[DEVICE]["staging"] == ""     # the upload folder went after the apply
    again = run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    assert again.ok and again.report.changed == 0 and again.report.count("already") == 10, again.lines()
    status = run_bundle(shell, built, "status")
    assert status.report.result == "installed"


@POSIX
@VERIFIER
def test_uninstall_and_rollback_undo_only_the_bundles_own_steps(tmp_path, board):
    from orchestrator.runtime.bundle.board import run_bundle
    store, built = _built(tmp_path, board)
    shell = GateShell(board)
    other = board.home / inst.BUNDLE_REL / "voice-test" / "components" / "stt"
    assert run_bundle(shell, built, "apply", store=store, device_id=DEVICE).ok
    (other / "my-notes.txt").write_text("the person's own file\n")
    listed = run_bundle(shell, built, "plan-uninstall")
    assert listed.report.count("would-remove") == 10 and (other / "model.bin").exists()
    removed = run_bundle(shell, built, "uninstall")
    assert removed.report.result == "ok", removed.lines()
    assert not (other / "model.bin").exists() and (other / "my-notes.txt").read_text()   # kept: not the bundle's
    assert any(line["state"] == "kept" for line in removed.report.lines)
    assert (board.home / ".config" / "llm-framework" / "llama-server.key").is_file()     # shared with way 2


@POSIX
@VERIFIER
@pytest.mark.parametrize("who", ["unsigned", "another key", "changed steps"])
def test_the_gate_refuses_a_bundle_the_framework_did_not_sign(tmp_path, who):
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    if who == "unsigned":
        (built.path / "steps.tsv.sig").write_text("")
    elif who == "another key":
        (built.path / "install.sh.sig").write_text(_signer().sign_bytes((built.path / "install.sh").read_bytes()))
    else:
        steps = built.path / "steps.tsv"
        steps.write_text(steps.read_text().replace("8090", "8091"))
    run = run_bundle(GateShell(board), built, "apply")   # no store: its own check would stop it before the board
    assert run.status == 3 and run.report.result == "refused", run.lines()
    assert not board.components.exists()


@POSIX
@VERIFIER
def test_a_file_that_arrived_changed_is_refused_before_anything_changes(tmp_path):
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    name, path, entry = built.payload()[-1]
    path.write_bytes(path.read_bytes()[:-1] + b"X")
    run = run_bundle(GateShell(board), built, "apply")
    assert run.report.result == "refused" and "does not match its sha256" in run.report.detail, run.lines()
    assert not board.components.exists()


@POSIX
@VERIFIER
def test_an_interrupted_apply_continues_where_it_stopped(tmp_path):
    """A run cut off in a step: the board continues with the steps not done yet, in the same upload folder."""
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    shell = GateShell(board)
    shim = tmp_path / "shim"
    shim.mkdir()
    real_cp = shutil.which("cp")
    (shim / "cp").write_text(f'#!/bin/sh\ncase "$*" in *whisper-server*) exit 1 ;; esac\nexec {real_cp} "$@"\n')
    (shim / "cp").chmod(0o755)
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{shim}{os.pathsep}{old}"
    try:
        cut = run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    finally:
        os.environ["PATH"] = old
    assert cut.report.result == "failed" and cut.staging, cut.lines()
    assert store.boards("voice-test")[DEVICE]["staging"] == cut.staging   # kept for the next deploy
    done_before = cut.report.count("done")
    resumed = run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    assert resumed.report.result == "ok", resumed.lines()
    assert resumed.report.count("already") == done_before and resumed.report.changed == 10 - done_before


@POSIX
@VERIFIER
def test_root_actions_stop_the_installer_unless_the_unprivileged_only_variant_was_chosen(tmp_path):
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    values = {"stt": {"autostart": "yes"}}
    store, built = _built(tmp_path, board, selection=_selection(tmp_path, values=values), bundle_id="voice-test")
    run = run_bundle(GateShell(board), built, "apply")
    assert run.status == inst.EXIT_ROOT and run.report.result == "stopped-root", run.lines()
    assert "llmcf-install" in run.report.detail and run.report.count("needs-root") == 3
    kept = board.home / inst.BUNDLE_REL / "voice-test" / ".kept"
    # the bundle carries llmcf-install for its root actions; the installer keeps it, signed, for bundle-root
    assert (kept / "llmcf-install").is_file() and (kept / "llmcf-install.sig").is_file()
    board2 = _fake_board(tmp_path / "b2", "root")
    sel = _selection(tmp_path / "b2", values=values, unprivileged_only=True)
    store2 = gen.BundleStore(tmp_path / "b2" / "state")
    built2 = gen.build_bundle(_plan(tmp_path / "b2", sel, bundle_id="voice-test"), store2, board2.signer)
    run2 = run_bundle(GateShell(board2), built2, "apply")
    assert run2.status == 0 and run2.report.result == "ok" and run2.report.count("skipped") == 3, run2.lines()


@POSIX
@pytest.mark.parametrize("args", [("plan; id", "voice-test"), ("rm", "voice-test"), ("plan", "../x"),
                                  ("apply", "Voice")])
def test_the_bundle_operations_take_only_their_actions_and_ids(tmp_path, args):
    root = f"/home/x/{gate.INSTALL_ROOT_REL}/{gate.STAGING_PREFIX}abcdEFGH"
    with pytest.raises(gate.GateRefused):
        gate.job("bundle-run", root, *args)
    board = _fake_board(tmp_path, "root")
    line = "llmcf-gate start-bundle-kept " + f"{board.home}/{gate.INSTALL_ROOT_REL}/{gate.STAGING_PREFIX}abcdEFGH " \
        + " ".join(args)
    done = board(line)
    assert gate.is_refusal(done.status, done.err) or "started=" not in done.out


def test_the_installer_and_the_steps_are_fixed_text_with_data_only(tmp_path):
    plan = _plan(tmp_path)
    steps = inst.render_steps(plan.manifest, "a" * 64)
    assert steps.startswith(inst.STEPS_MAGIC + "\n")
    for line in steps.splitlines()[1:]:
        assert not set("'\"$`;&|<>(){}\\ ") & set(line), line
    assert inst.installer_sh() == inst.installer_sh() and "@BUNDLE_REL@" not in inst.installer_sh()
    assert {op.verb for op in gate.gate_operations()} >= {"start-bundle-run", "start-bundle-kept"}


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="ShellCheck is not installed")
def test_the_installer_passes_shellcheck(tmp_path):
    path = tmp_path / "install.sh"
    path.write_bytes(inst.installer_sh().encode())   # LF as on the board (write_text would write CRLF on Windows)
    done = subprocess.run(["shellcheck", "-S", "warning", "-s", "sh", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


def test_a_board_run_report_reads_only_the_installers_own_lines():
    report = inst.report("noise\nllmcf-bundle: done file stt model.bin\nllmcf-bundle: result=ok changed=1 \n")
    assert report.result == "ok" and report.changed == 1 and report.lines == [
        {"state": "done", "step": "file", "what": "stt model.bin"}]
    assert inst.report("$(id)\n").result == ""


def test_the_root_login_and_another_architecture_are_refused_before_anything_is_sent(tmp_path):
    from orchestrator.runtime.bundle.board import run_bundle
    from orchestrator.runtime.device.remote import RemoteError
    plan = _plan(tmp_path)
    built = gen.build_bundle(plan, gen.BundleStore(tmp_path / "s"), _signer())

    class Facts:
        def __init__(self, out):
            self.out, self.sent = out, []

        def run(self, script, **kw):
            self.sent.append(script)
            return Result(0, self.out, "")
    root = Facts("uid=0\nhome=/root\nmachine=aarch64\ninit=systemd\nunit=no\n")
    with pytest.raises(RemoteError, match="root"):
        run_bundle(root, built, "apply")
    other = Facts("uid=1000\nhome=/home/x\nmachine=x86_64\ninit=systemd\nunit=no\n")
    with pytest.raises(RemoteError, match="bundle is for"):
        run_bundle(other, built, "apply")
    assert all(getattr(s, "verb", "") == "facts" for s in root.sent + other.sent)


_ = SimpleNamespace


def test_a_block_is_published_and_installed_as_a_signed_community_pack(tmp_path):
    """Building blocks are packs like target modules: exported scrubbed and signed, installed only after the block
    check, then found by the generator as a signed block (enterprise mode still takes only indexed ones)."""
    from orchestrator.Core.community_manager import CommunityManager, TrustLevel
    from orchestrator.Core.community_pack import PackError
    app = tmp_path / "app"
    fw = SimpleNamespace(info=SimpleNamespace(installation_path=str(app)), config=None)
    manager = CommunityManager(fw)
    blk.new_block(manager.blocks_dir, "stt-mine", kind="stt", title="Mine", template="stt-whisper-cpp")
    assert manager.exportable_blocks() == ["stt-mine"]
    from orchestrator.Core.community_pack import UserKeys
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    manager._secrets = lambda: None
    original = UserKeys.signing_key
    UserKeys.signing_key = lambda self: key
    try:
        result = manager.export_pack("block", "stt-mine", author="tester", out_dir=tmp_path / "out")
    finally:
        UserKeys.signing_key = original
    assert result.manifest.kind == "block" and result.manifest.id == "stt-mine"
    shutil.rmtree(manager.blocks_dir / "stt-mine")
    prepared = manager._prepare(result.path, manager._new_quarantine("block", "stt-mine"), TrustLevel.LOCAL,
                                "file", None)
    manager.finish_install(prepared)
    found = blk.load_catalog(app).get("stt-mine")
    assert found is not None and found.trust == "signed"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "block.yml").write_text((blk.BUILTIN_DIR / "stt-whisper-cpp" / "block.yml").read_text())
    (bad / "README.md").write_text("# no models section\n")
    (bad / "run.sh").write_text("id\n")
    assert len(blk.check_block_folder(bad)) == 2
    with pytest.raises(PackError):
        manager.export_pack("block", "nope", author="tester", out_dir=tmp_path / "out")


# --- review findings (bundle-gen-v): regression tests -----------------------------------------------------------------

def _indexed_block(app: Path, block_id: str = "stt-indexed") -> Path:
    """An own block made into one installed from the signed index (its provenance as the install writes it)."""
    from orchestrator.Core.community_manager import Provenance
    from orchestrator.Core.community_pack import PROVENANCE_NAME, tree_sha256
    folder = blk.new_block(app / "community" / "blocks", block_id, kind="stt", title="Indexed",
                           template="stt-whisper-cpp")
    prov = Provenance(kind="block", id=block_id, version="1.0.0", trust="verified", source="index",
                      pack_sha256="a" * 64, content_sha256=tree_sha256(folder), author="tester",
                      installed_at="2026-11-01T00:00:00Z")
    (folder / PROVENANCE_NAME).write_text(prov.to_json(), encoding="utf-8")
    return folder


def test_an_indexed_block_changed_after_its_install_is_no_longer_indexed(tmp_path):
    """Enterprise mode takes indexed blocks because the signed index checked them; a block.yml changed after the
    install is no longer what the index checked, so it counts as the person's own (and enterprise mode refuses it)."""
    app = tmp_path / "app"
    folder = _indexed_block(app)
    assert blk.load_catalog(app).get("stt-indexed").trust == "indexed"
    spec = folder / blk.BLOCK_FILE
    spec.write_text(spec.read_text().replace("default: 8090", "default: 8091"), encoding="utf-8")
    changed = blk.load_catalog(app)
    assert changed.get("stt-indexed").trust == "own"
    assert any("changed since" in n for n in changed.notes), changed.notes
    selection = _selection(tmp_path, blocks=["stt-indexed"])
    del selection.files["llm"]
    enterprise = _plan(tmp_path, selection, app_root=app, mode="enterprise")
    assert any("enterprise mode takes only" in p for p in enterprise.problems), enterprise.problems


def test_a_plans_labels_leave_out_every_account_variable_whatever_its_name(tmp_path):
    app = tmp_path / "app"
    folder = blk.new_block(app / "community" / "blocks", "stt-acct", kind="stt", title="Acct",
                           template="stt-whisper-cpp")
    spec = folder / blk.BLOCK_FILE
    spec.write_text(spec.read_text().replace("name: user", "name: account"), encoding="utf-8")
    selection = _selection(tmp_path, blocks=["stt-acct"], values={"stt": {"account": "jdoe"}})
    del selection.files["llm"]
    plan = _plan(tmp_path, selection, app_root=app, allow_own=True)
    assert plan.ok, plan.problems
    assert plan.manifest.components[0].variables["account"] == "jdoe"
    assert "jdoe" not in json.dumps(plan.labels())


@POSIX
@VERIFIER
def test_the_installers_words_name_no_path_of_the_board(tmp_path):
    """A stopped run's lock: the installer's report names the bundle's own folder, never the account's home."""
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    (board.home / inst.BUNDLE_REL / "voice-test" / ".state" / ".lock").mkdir(parents=True)
    run = run_bundle(GateShell(board), built, "apply")
    assert run.report.result == "refused" and ".lock" in run.report.detail, run.lines()
    assert str(board.home) not in "\n".join(run.lines()) and "/" + inst.BUNDLE_REL not in run.report.detail


def _cut_apply(tmp_path, board, store, built, shell):
    from orchestrator.runtime.bundle.board import run_bundle
    shim = tmp_path / "shim"
    shim.mkdir()
    real_cp = shutil.which("cp")
    (shim / "cp").write_text(f'#!/bin/sh\ncase "$*" in *whisper-server*) exit 1 ;; esac\nexec {real_cp} "$@"\n')
    (shim / "cp").chmod(0o755)
    old = os.environ["PATH"]
    os.environ["PATH"] = f"{shim}{os.pathsep}{old}"
    try:
        return run_bundle(shell, built, "apply", store=store, device_id=DEVICE)
    finally:
        os.environ["PATH"] = old


@POSIX
@VERIFIER
def test_a_first_apply_cut_off_can_be_uninstalled(tmp_path):
    """The board keeps the signed installer as soon as an apply starts changing things, so a first deploy that
    stopped in a step can still be rolled back or uninstalled (not only continued)."""
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    shell = GateShell(board)
    cut = _cut_apply(tmp_path, board, store, built, shell)
    assert cut.report.result == "failed" and cut.report.count("done") > 0, cut.lines()
    removed = run_bundle(shell, built, "uninstall")
    assert removed.report.result == "ok", removed.lines()
    assert not (board.home / inst.BUNDLE_REL / "voice-test").exists()


class CountingShell(GateShell):
    def __init__(self, board):
        super().__init__(board)
        self.uploaded = []

    def upload(self, path, remote, sha):
        self.uploaded.append(remote.rsplit("/", 1)[-1])
        super().upload(path, remote, sha)


@POSIX
@VERIFIER
def test_a_second_apply_does_not_send_the_files_the_board_holds_already(tmp_path):
    """One click again on an installed bundle: the board's installer says what is in place, and those files (models
    of several GB) are not sent again; the run changes nothing."""
    from orchestrator.runtime.bundle.board import run_bundle
    board = _fake_board(tmp_path, "root")
    store, built = _built(tmp_path, board)
    first = CountingShell(board)
    assert run_bundle(first, built, "apply", store=store, device_id=DEVICE).report.result == "ok"
    payload = {name for name, _path, _entry in built.payload()}
    assert payload <= set(first.uploaded)
    again = CountingShell(board)
    run = run_bundle(again, built, "apply", store=store, device_id=DEVICE)
    assert run.report.result == "ok" and run.report.changed == 0, run.lines()
    assert not payload & set(again.uploaded), again.uploaded


def test_a_block_the_signed_index_revoked_is_not_used(tmp_path):
    app = tmp_path / "app"
    _indexed_block(app)

    class Index:
        def revocation(self, kind, pack_id, version, sha256):
            return object() if (kind, pack_id) == ("block", "stt-indexed") else None
    catalog = blk.load_catalog(app, index=Index())
    assert catalog.get("stt-indexed") is None and any("revoked" in n for n in catalog.notes), catalog.notes
    assert blk.load_catalog(app).get("stt-indexed").trust == "indexed"
