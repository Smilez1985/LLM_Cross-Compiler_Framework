"""The person's harness taken in on the host (orchestrator/runtime/bundle/harness.py): spec checks, the three
sources (folder, archive, git), the pin, the one tree checksum, and the refusals (links out, a missing installer, a
wrong sha256, a credential in the link). The executable bit and links come from the source's own metadata where the
host's file system has none (Windows: harness.NO_EXEC_BIT, forced here on every system too): the same harness gives
the same tree checksum and pack everywhere; a plain folder on Windows takes the content rule, a git checkout's index
has the modes. Every other format: tests/test_harness_formats.py."""

import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import harness as h

POSIX = pytest.mark.skipif(os.name == "nt", reason="sets a file's mode with chmod, which Windows does not keep")
GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid")


def make_tree(root: Path) -> Path:
    (root / "installer").mkdir(parents=True)
    inst = root / "installer" / "install.sh"
    inst.write_text("#!/bin/sh\necho ok\n")
    inst.chmod(0o755)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    return root


def git_tree(root: Path) -> Path:
    """make_tree as a git checkout whose index marks the installer executable (on every system: Windows keeps no
    executable bit in its file system, git's index does)."""
    make_tree(root)
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["update-index", "--chmod=+x", "installer/install.sh"],
                 ["commit", "-qm", "one"]):
        subprocess.run(["git", *args], cwd=root, check=True, env=GIT_ENV, capture_output=True)
    return root


def source_tree(root: Path) -> Path:
    """A folder harness that can be taken in on this system: a plain folder (on Windows its modes come from the content
    rule: the installer starts with "#!")."""
    return make_tree(root)


def tar_source(path: Path, top: str = "") -> Path:
    """The same harness as an archive whose members carry the modes (0755 for the installer), on every system."""
    pre = f"{top}/" if top else ""
    with tarfile.open(path, "w:gz") as tf:
        for name, data, mode in ((f"{pre}installer/install.sh", b"#!/bin/sh\necho ok\n", 0o755),
                                 (f"{pre}src/app.py", b"print('hi')\n", 0o644)):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            import io
            tf.addfile(info, io.BytesIO(data))
    return path


def spec(**kw):
    base = dict(id="demo", title="Demo", kind="folder", location="", installer="installer/install.sh",
                dry_run_args=("--dry-run",), assume_yes="DEMO_YES=1", exit_codes={0: "done", 1: "findings"})
    base.update(kw)
    return h.HarnessSpec(**base)


def test_a_folder_is_taken_in_with_one_tree_checksum_and_reused(tmp_path):
    src = source_tree(tmp_path / "src")
    taken = h.take_in(spec(location=str(src)), tmp_path / "state")
    assert taken.files == 2 and taken.pin == taken.tree_sha256 and len(taken.tree_sha256) == 64
    assert ("installer/install.sh", "x") in [(r[0], r[1]) for r in h.stored_listing(taken.path)]
    again = h.take_in(spec(location=str(src)), tmp_path / "state")
    assert again.path == taken.path and again.tree_sha256 == taken.tree_sha256
    record = h.stored(tmp_path / "state", "demo")[0]
    assert record["schema"] == "llmcf-harness/1" and record["spec"]["exit_codes"] == {"0": "done", "1": "findings"}


def test_the_checksum_follows_content_and_the_executable_bit(tmp_path):
    src = make_tree(tmp_path / "src")
    modes = h.Modes(("installer/install.sh",))
    first = h.tree_sha256(h.tree_listing(src, modes))
    (src / "src" / "app.py").write_text("print('changed')\n")
    second = h.tree_sha256(h.tree_listing(src, modes))
    third = h.tree_sha256(h.tree_listing(src, h.Modes(("installer/install.sh", "src/app.py"))))
    assert len({first, second, third}) == 3


@POSIX
def test_on_posix_the_file_systems_own_bit_counts(tmp_path):
    src = make_tree(tmp_path / "src")
    first = h.tree_sha256(h.tree_listing(src))
    assert first == h.tree_sha256(h.tree_listing(src, h.Modes(("installer/install.sh",))))
    (src / "src" / "app.py").chmod(0o755)
    assert h.tree_sha256(h.tree_listing(src)) != first


def test_a_link_out_of_the_harness_is_refused(tmp_path):
    src = source_tree(tmp_path / "src")
    os.symlink("/etc/passwd", src / "src" / "out")
    with pytest.raises(h.HarnessError, match="leaves the harness"):
        h.take_in(spec(location=str(src)), tmp_path / "state")
    assert not any((tmp_path / "state" / "harnesses" / "demo").glob("*/tree"))


def test_a_missing_installer_is_refused(tmp_path):
    src = source_tree(tmp_path / "src")
    with pytest.raises(h.HarnessError, match="installer"):
        h.take_in(spec(location=str(src), installer="installer/nope.sh"), tmp_path / "state")


def test_an_archive_is_pinned_by_its_sha256_and_its_single_top_folder_unwrapped(tmp_path):
    src = make_tree(tmp_path / "pkg" / "demo-1.0")
    archive = tmp_path / "demo.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="demo-1.0")
    taken = h.take_in(spec(kind="archive", location=str(archive)), tmp_path / "state")
    assert taken.pin == h._sha256_file(archive) and (taken.path / "installer" / "install.sh").is_file()
    with pytest.raises(h.HarnessError, match="sha256"):
        h.take_in(spec(kind="archive", location=str(archive), pin="0" * 64), tmp_path / "state")


def test_a_zip_keeps_the_executable_bit_and_refuses_a_path_outside(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        info = zipfile.ZipInfo("installer/install.sh")
        info.external_attr = (0o100755 << 16)
        zf.writestr(info, "#!/bin/sh\n")
    taken = h.take_in(spec(kind="archive", location=str(good)), tmp_path / "state")
    assert h.stored_listing(taken.path)[0][:2] == ("installer/install.sh", "x")
    if os.name != "nt":
        assert (taken.path / "installer" / "install.sh").stat().st_mode & 0o100
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.sh", "x")
        zf.writestr("installer/install.sh", "x")
    with pytest.raises(h.HarnessError, match="outside"):
        h.take_in(spec(kind="archive", location=str(bad)), tmp_path / "state2")


def _local_git(repo):
    class LocalGit:
        def __call__(self, args, cwd=None, timeout=60, offline=False, check=True):
            args = [str(repo) if a == "https://example.invalid/demo" else a for a in args]
            res = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, capture_output=True,
                                 text=True, env=GIT_ENV)
            if check and res.returncode:
                from orchestrator.runtime.build.source import SourceError
                raise SourceError(res.stderr)
            return res
    return LocalGit()


@GIT
def test_a_git_repository_is_resolved_to_its_commit_and_checked_against_a_pin(tmp_path):
    repo = git_tree(tmp_path / "repo")
    env = GIT_ENV
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

    class LocalGit:
        def __call__(self, args, cwd=None, timeout=60, offline=False, check=True):
            args = [str(repo) if a == "https://example.invalid/demo" else a for a in args]
            res = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, capture_output=True,
                                 text=True, env=env)
            if check and res.returncode:
                from orchestrator.runtime.build.source import SourceError
                raise SourceError(res.stderr)
            return res

    s = spec(kind="git", location="https://example.invalid/demo", ref="main")
    taken = h.take_in(s, tmp_path / "state", git=LocalGit())
    assert taken.pin == commit and not (taken.path / ".git").exists()
    assert ("installer/install.sh", "x") in [(r[0], r[1]) for r in h.stored_listing(taken.path)]
    assert json.loads((taken.path.parent / h.MODES_FILE).read_text())["executable"] == ["installer/install.sh"]
    with pytest.raises(h.HarnessError, match="not"):
        h.take_in(replace(s, ref=None, pin="1" * 40), tmp_path / "state", git=LocalGit())


@pytest.mark.parametrize("change, field", [
    ({"id": "Bad Id"}, "id"),
    ({"kind": "git", "location": "https://user:pw@example.com/x"}, "location"),
    ({"kind": "git", "location": "http://example.com/x"}, "location"),
    ({"installer": "../install.sh"}, "installer"),
    ({"installer": "/abs/install.sh"}, "installer"),
    ({"assume_yes": "yes"}, "assume_yes"),
    ({"assume_yes": "X=$(rm -rf /)"}, "assume_yes"),
    ({"dry_run_args": ("; reboot",)}, "dry_run_args"),
    ({"exit_codes": {0: "maybe"}}, "exit_codes"),
    ({"kind": "folder", "pin": "a" * 64}, "folder"),
    ({"kind": "archive", "ref": "main"}, "ref"),
])
def test_bad_specs_name_their_field(change, field):
    with pytest.raises(h.HarnessError, match=field):
        h.check_spec(spec(**{"location": "/x", **change}))


def test_the_chimera_example_is_a_valid_spec():
    assert h.check_spec(h.CHIMERA_EXAMPLE).assume_yes == "CHIMERA_ASSUME_YES=1"
    assert h.CHIMERA_EXAMPLE.exit_codes == {0: "done", 1: "findings"}


# --- the harness in the bundle (generator, manifest) -----------------------------------------------------------------

def _taken(tmp_path, **kw):
    """The demo harness taken in from an archive whose members carry their modes (the same on every system)."""
    archive = tar_source(tmp_path / "demo.tar.gz", top="demo-1.0")
    return h.take_in(spec(kind="archive", location=str(archive), **kw), tmp_path / "state")


def _can_link(folder: Path) -> bool:
    try:
        os.symlink("app.py", folder / "link")
        return True
    except (OSError, NotImplementedError):   # Windows without the right to make links
        return False


def test_the_pack_is_the_same_bytes_every_time_and_holds_only_the_listing(tmp_path):
    taken = _taken(tmp_path)
    linked = _can_link(taken.path / "src")
    rows = h.stored_listing(taken.path)
    first, sha, size = h.pack(taken.path, h.tree_sha256(rows))
    data = first.read_bytes()
    first.unlink()
    again, sha2, _ = h.pack(taken.path, h.tree_sha256(rows))
    assert again.read_bytes() == data and sha == sha2 and size == len(data)
    with tarfile.open(again) as tf:
        members = tf.getmembers()
    assert [m.name for m in members] == [r[0] for r in rows]
    assert all(m.mtime == 0 and m.uid == 0 and m.gid == 0 for m in members)
    assert {m.name: m.mode for m in members if m.isfile()} == {"installer/install.sh": 0o755, "src/app.py": 0o644}
    if linked:
        assert next(m for m in members if m.name == "src/link").linkname == "app.py"
    with pytest.raises(h.HarnessError, match="does not match"):
        h.pack(taken.path, "0" * 64)


def test_an_ai_plan_never_packs(tmp_path):
    taken = _taken(tmp_path)
    with pytest.raises(h.HarnessError, match="not packed"):
        h.pack(taken.path, taken.tree_sha256, make=False)
    h.pack(taken.path, taken.tree_sha256)
    assert h.pack(taken.path, taken.tree_sha256, make=False)[1]


def _harness_plan(tmp_path, values, **kw):
    from orchestrator.runtime.bundle import blocks as blk
    from orchestrator.runtime.bundle import generator as gen
    from test_board_bundle import RK3566, DEVICE
    sel = gen.Selection(blocks=["harness-agent"], values={"harness": values}, **kw)
    return gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="harness-test", unmeasured_ok=True, state_dir=tmp_path / "state")


def test_the_plan_carries_the_tree_its_checksum_and_the_run_harness_action(tmp_path):
    taken = _taken(tmp_path)
    plan = _harness_plan(tmp_path, {"harness": "demo"})
    assert plan.ok, plan.problems
    comp = plan.manifest.components[0]
    assert comp.kind == "harness" and comp.start is None
    assert comp.harness.tree_sha256 == taken.tree_sha256 and comp.harness.installer == "installer/install.sh"
    assert comp.harness.dry_run_args == ("--dry-run",) and comp.harness.outcomes() == {0: "done", 1: "findings"}
    assert comp.harness.assume_yes is None   # the switch only after the person's opt-in
    assert {f.name: f.role for f in comp.files} == {"harness.tar": "data", "harness_watch.sh": "program"}
    watch = next(f for f in comp.files if f.name == "harness_watch.sh")
    assert watch.sha256 == h._sha256_file(h.WATCH_SH)
    assert [(a.action, a.component) for a in plan.manifest.root_actions] == [("run_harness", "harness")]
    assert comp.variables["tree"] == taken.tree_sha256
    from orchestrator.runtime.bundle.installer import render_steps
    assert render_steps(plan.manifest, "a" * 64).endswith("root\trun_harness\tharness\n")
    opted = _harness_plan(tmp_path, {"harness": "demo", "assume_yes": True})
    assert opted.manifest.components[0].harness.assume_yes == "DEMO_YES=1"
    assert any("opt-in" in line for line in opted.lines())


@pytest.mark.parametrize("values, kw, words", [
    ({"harness": "other"}, {}, "no harness other"),
    ({"harness": "demo", "tree": "f" * 64}, {}, "with tree ffffffffffff"),
    ({"harness": "demo"}, {"unprivileged_only": True}, "unprivileged-only variant cannot take it"),
    ({}, {}, "harness is needed"),
])
def test_a_harness_the_plan_cannot_take_says_why(tmp_path, values, kw, words):
    _taken(tmp_path)
    plan = _harness_plan(tmp_path, values, **kw)
    assert not plan.ok and any(words in p for p in plan.problems), plan.problems


def test_assume_yes_needs_a_switch_of_the_harness(tmp_path):
    _taken(tmp_path, assume_yes=None)
    plan = _harness_plan(tmp_path, {"harness": "demo", "assume_yes": True})
    assert any("names no assume-yes switch" in p for p in plan.problems)


def test_the_manifest_refuses_a_harness_that_is_not_whole(tmp_path):
    from orchestrator.runtime.bundle.manifest import ManifestError, load_manifest
    _taken(tmp_path)
    good = _harness_plan(tmp_path, {"harness": "demo"}).manifest.as_json()
    load_manifest(good)

    def changed(edit):
        doc = json.loads(json.dumps(good))
        edit(doc)
        return doc
    for edit in (lambda d: d["components"][0].pop("harness"),
                 lambda d: d["components"][0].__setitem__("files", d["components"][0]["files"][:1]),
                 lambda d: d["components"][0]["harness"].__setitem__("installer", "../x.sh"),
                 lambda d: d["components"][0]["harness"].__setitem__("assume_yes", "X=$(id)"),
                 lambda d: d["components"][0]["harness"].__setitem__("exit_codes", {"256": "done"}),
                 lambda d: d["components"][0]["harness"].__setitem__("exit_codes", {"0": "maybe"}),
                 lambda d: d["components"][0]["harness"].__setitem__("dry_run_args", ["; rm"]),
                 lambda d: d["components"][0]["harness"].__setitem__("tree_sha256", "x" * 64),
                 lambda d: d["components"][0].__setitem__("kind", "llm")):
        with pytest.raises(ManifestError):
            load_manifest(changed(edit))


# --- the tree checksum both sides compute (tests/data/harness_tree_vectors.json; rust/llmcf-install harness.rs) -----

TREE_VECTORS = json.loads((Path(__file__).parent / "data" / "harness_tree_vectors.json").read_text(encoding="utf-8"))


def _build(root: Path, entries) -> None:
    for e in entries:
        path = root / e["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if e["kind"] == "l":
            os.symlink(e["target"], path)
        else:
            path.write_text(e["data"], encoding="utf-8")
            path.chmod(0o755 if e["kind"] == "x" else 0o644)


@pytest.mark.skipif(os.name == "nt", reason="the vectors hold links and executable bits, as a board's tree does")
@pytest.mark.parametrize("index", range(len(TREE_VECTORS["trees"])))
def test_the_shared_tree_vectors_hold_for_harness_py(tmp_path, index):
    case = TREE_VECTORS["trees"][index]
    _build(tmp_path, case["entries"])
    rows = h.tree_listing(tmp_path)
    assert "".join(f"{k} {d} {p}\n" for p, k, d in rows) == case["listing"]
    assert h.tree_sha256(rows) == case["tree_sha256"]


@pytest.mark.skipif(os.name == "nt", reason="the vectors hold links and executable bits, as a board's tree does")
def test_the_vector_pack_unpacks_to_its_checksum_and_packing_again_gives_the_same_tree(tmp_path):
    import base64
    case = TREE_VECTORS["pack"]
    tar = tmp_path / "harness.tar"
    tar.write_bytes(base64.b64decode(case["tar_base64"]))
    with tarfile.open(tar) as tf:
        assert all(m.isfile() or m.issym() for m in tf.getmembers())
        tf.extractall(tmp_path / "tree", filter="tar")
    assert h.tree_sha256(h.tree_listing(tmp_path / "tree")) == case["tree_sha256"]
    tar.unlink()
    path, _sha, _size = h.pack(tmp_path / "tree", case["tree_sha256"])
    with tarfile.open(path) as tf:
        tf.extractall(tmp_path / "again", filter="tar")
    assert h.tree_sha256(h.tree_listing(tmp_path / "again")) == case["tree_sha256"]


def test_the_harness_gate_operation_takes_only_its_actions_and_never_an_answer_as_argument():
    from orchestrator.remote import gate
    from orchestrator.runtime.device import board_ops  # noqa: F401 - defines every board operation
    sha = "a" * 64
    text = gate.script("bundle-harness", "harness-answer", "voice-test", "agent", "run-1", "0", sha)
    assert "sudo -n" in text and "sudo -S" not in text and 'printf "%s\\n" "$llmcf_line" | sudo -n' in text
    assert "LLMCF_HOME_GATE=; llmcf_trusted_signers" in text
    gate.script("bundle-harness", "harness-start", "voice-test", "agent", "dry-1", "dry", sha)
    gate.script("bundle-harness", "harness-read", "voice-test", "agent", "run-1", "1024", sha)
    gate.script("bundle-harness", "harness-records", "voice-test", "agent", "run-1", "index", sha)
    gate.script("bundle-harness", "harness-records", "voice-test", "agent", "run-1", "13.4194304.4194304", sha)
    assert "harness-start|harness-read|harness-records) sudo -n" in text
    for args in (("harness-start", "voice-test", "agent", "run-1", "sh", sha),
                 ("harness-records", "voice-test", "agent", "run-1", "0", sha),
                 ("harness-records", "voice-test", "agent", "run-1", "1.0.0", sha),
                 ("harness-records", "voice-test", "agent", "run-1", "100.0.1", sha),
                 ("harness-records", "voice-test", "agent", "run-1", "1.-1.1", sha),
                 ("harness-read", "voice-test", "agent", "run-1", "x1", sha),
                 ("apply", "voice-test", "agent", "run-1", "0", sha),
                 ("harness-stop", "voice-test", "agent", "run.1", "0", sha),
                 ("harness-stop", "voice-test", "../agent", "run-1", "0", sha)):
        with pytest.raises(gate.GateRefused):
            gate.script("bundle-harness", *args)


def test_the_sudo_rule_names_the_harness_actions_and_an_older_rule_is_set_up_again():
    from orchestrator.runtime.bundle import gate_ops
    body = gate_ops._setup_body("sudo", "apt", "voice-test", "a" * 64)
    for action in ("harness-start", "harness-read", "harness-answer", "harness-stop", "harness-records"):
        assert f"$llmcf_fx {action} *" in body
    assert "llmcf_dropin_ok harness-read voice-test" in body
    ready = gate_ops._ready_body("a" * 64)
    assert "llmcf_dropin_ok harness-read llmcf-check" in ready and "echo dropin=old" in ready
    from orchestrator.runtime.bundle import readiness
    old = readiness.Readiness.parse("sudo=yes\nrootway=nopass\nsigners=root\nhelper=same\ndropin=old\n")
    assert old.missing() == ["dropin-update"] and not old.ready
    assert any(line.startswith("Replace the older sudo rule") for line in readiness.plan_lines(old))
    assert "older one" in readiness.declined_text("voice-test", "board", old)
    done = readiness.SetupDone(True, "", ("dropin",), ("dropin",))
    assert "older sudo rule" in done.text() and done.as_dict()["updated"] == ["dropin"]


# --- a host without executable bits (Windows; forced here on every system) ------------------------------------------

@pytest.fixture
def no_exec_bit(monkeypatch):
    monkeypatch.setattr(h, "NO_EXEC_BIT", True)


def test_without_exec_bits_a_plain_folder_is_taken_in_by_the_content_rule(tmp_path, no_exec_bit):
    src = make_tree(tmp_path / "src")
    taken = h.take_in(spec(location=str(src)), tmp_path / "state")
    assert taken.content_rule and taken.derived == ("installer/install.sh",)
    assert json.loads((taken.path.parent / h.MODES_FILE).read_text())["executable"] == ["installer/install.sh"]


@GIT
def test_without_exec_bits_a_git_folder_takes_its_modes_from_the_index(tmp_path, monkeypatch):
    src = git_tree(tmp_path / "src")
    posix = h.take_in(spec(location=str(src)), tmp_path / "a") if os.name != "nt" else None
    monkeypatch.setattr(h, "NO_EXEC_BIT", True)
    if os.name != "nt":
        (src / "installer" / "install.sh").chmod(0o644)   # what Windows would show: no bit in the file system
    taken = h.take_in(spec(location=str(src)), tmp_path / "b")
    assert ("installer/install.sh", "x") in [(r[0], r[1]) for r in h.stored_listing(taken.path)]
    if posix is not None:
        assert taken.tree_sha256 == posix.tree_sha256


def test_without_exec_bits_an_archive_gives_the_same_checksum_and_pack(tmp_path, monkeypatch):
    archive = tar_source(tmp_path / "demo.tar.gz", top="demo-1.0")
    plain = h.take_in(spec(kind="archive", location=str(archive)), tmp_path / "a")
    plain_pack = h.pack(plain.path, plain.tree_sha256)[0].read_bytes()
    monkeypatch.setattr(h, "NO_EXEC_BIT", True)
    taken = h.take_in(spec(kind="archive", location=str(archive)), tmp_path / "b")
    if os.name != "nt":
        (taken.path / "installer" / "install.sh").chmod(0o644)   # the file system says nothing; the record counts
    assert taken.tree_sha256 == plain.tree_sha256
    assert h.pack(taken.path, taken.tree_sha256)[0].read_bytes() == plain_pack
    assert json.loads((taken.path.parent / h.MODES_FILE).read_text()) == {"executable": ["installer/install.sh"],
                                                                         "links": {}}


def test_recorded_links_are_listed_as_links_and_never_leave_the_tree(tmp_path):
    src = make_tree(tmp_path / "src")
    (src / "src" / "link").write_text("app.py")   # a link Windows wrote as a file
    rows = h.tree_listing(src, h.Modes(("installer/install.sh",), {"src/link": "app.py", "src/gone": "app.py"}))
    assert ("src/link", "l", "app.py") in rows and ("src/gone", "l", "app.py") in rows
    for target in ("../../outside", "/etc/passwd", "C:/x"):
        with pytest.raises(h.HarnessError, match="leaves the harness"):
            h.tree_listing(src, h.Modes((), {"src/bad": target}))


@GIT
def test_the_checkouts_read_only_git_folder_goes_through_the_one_removal(tmp_path, monkeypatch):
    from orchestrator.utils import atomic
    repo = git_tree(tmp_path / "repo")
    removed = []
    real = atomic.remove_tree

    def watched(path, **kw):
        removed.append(Path(path).name)
        return real(path, **kw)
    monkeypatch.setattr(atomic, "remove_tree", watched)
    s = spec(kind="git", location="https://example.invalid/demo", ref="main")
    taken = h.take_in(s, tmp_path / "state", git=_local_git(repo))
    assert ".git" in removed and not (taken.path / ".git").exists()
