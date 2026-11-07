"""The person's harness taken in on the host (orchestrator/runtime/bundle/harness.py): spec checks, the three
sources (folder, archive, git), the pin, the one tree checksum, and the refusals (links out, a missing installer, a
wrong sha256, a credential in the link)."""

import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import harness as h


def make_tree(root: Path) -> Path:
    (root / "installer").mkdir(parents=True)
    inst = root / "installer" / "install.sh"
    inst.write_text("#!/bin/sh\necho ok\n")
    inst.chmod(0o755)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    return root


def spec(**kw):
    base = dict(id="demo", title="Demo", kind="folder", location="", installer="installer/install.sh",
                dry_run_args=("--dry-run",), assume_yes="DEMO_YES=1", exit_codes={0: "done", 1: "findings"})
    base.update(kw)
    return h.HarnessSpec(**base)


def test_a_folder_is_taken_in_with_one_tree_checksum_and_reused(tmp_path):
    src = make_tree(tmp_path / "src")
    taken = h.take_in(spec(location=str(src)), tmp_path / "state")
    assert taken.files == 2 and taken.pin == taken.tree_sha256 and len(taken.tree_sha256) == 64
    assert (taken.path / "installer" / "install.sh").stat().st_mode & 0o100
    again = h.take_in(spec(location=str(src)), tmp_path / "state")
    assert again.path == taken.path and again.tree_sha256 == taken.tree_sha256
    record = h.stored(tmp_path / "state", "demo")[0]
    assert record["schema"] == "llmcf-harness/1" and record["spec"]["exit_codes"] == {"0": "done", "1": "findings"}


def test_the_checksum_follows_content_and_the_executable_bit(tmp_path):
    src = make_tree(tmp_path / "src")
    first = h.tree_sha256(h.tree_listing(src))
    (src / "src" / "app.py").write_text("print('changed')\n")
    second = h.tree_sha256(h.tree_listing(src))
    (src / "src" / "app.py").chmod(0o755)
    third = h.tree_sha256(h.tree_listing(src))
    assert len({first, second, third}) == 3


def test_a_link_out_of_the_harness_is_refused(tmp_path):
    src = make_tree(tmp_path / "src")
    os.symlink("/etc/passwd", src / "src" / "out")
    with pytest.raises(h.HarnessError, match="leaves the harness"):
        h.take_in(spec(location=str(src)), tmp_path / "state")
    assert not any((tmp_path / "state" / "harnesses" / "demo").glob("*/tree"))


def test_a_missing_installer_is_refused(tmp_path):
    src = make_tree(tmp_path / "src")
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
    assert (taken.path / "installer" / "install.sh").stat().st_mode & 0o100
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.sh", "x")
        zf.writestr("installer/install.sh", "x")
    with pytest.raises(h.HarnessError, match="outside"):
        h.take_in(spec(kind="archive", location=str(bad)), tmp_path / "state2")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")
def test_a_git_repository_is_resolved_to_its_commit_and_checked_against_a_pin(tmp_path):
    repo = make_tree(tmp_path / "repo")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-qm", "one"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
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
