"""The one removal of the framework's own folders (chain win-hygiene: orchestrator/utils/atomic.remove_tree).

shutil.rmtree(..., ignore_errors=True) gave up on a read-only file without a word: on Windows (where a read-only file
cannot be deleted) a git checkout's .git, an unpacked archive or a quarantine copy stayed behind, also in caches that
feed a hash. Linux deletes such a file from a writable folder, so the hazard never showed here. `windows_read_only`
makes it visible on any host: while it is on, deleting a file without its owner's write bit fails as it does on NTFS.

- the helper: read-only trees go, a link is removed and never followed, what stays is returned and logged (label and
  relative paths only), Windows repeats a pass, an empty path is nothing;
- the old names (runtime/build/source.remove_tree, rag/storage_lock.remove_tree) delegate to it;
- callers: best-effort clean-ups now remove read-only trees, and callers that need the folder gone refuse to go on;
- the ratchet: no shutil.rmtree(ignore_errors=True) and no swallowing error handler in orchestrator/ or scripts/.
"""

import ast
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.utils import atomic

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def windows_read_only(monkeypatch):
    """Deleting a file whose owner write bit is off fails with PermissionError, as on NTFS (the read-only flag), also
    for root and through the descriptor-relative calls shutil.rmtree makes."""
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        st = os.lstat(path, dir_fd=dir_fd)
        if not stat.S_ISLNK(st.st_mode) and not st.st_mode & stat.S_IWUSR:
            raise PermissionError(13, "Access is denied (read-only file)", os.fsdecode(path))
        return real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "remove", unlink)


def _read_only_tree(root: Path) -> Path:
    """A folder like a git checkout or an unpacked archive: read-only files in nested folders."""
    (root / "objects" / "ab").mkdir(parents=True)
    for rel in ("objects/ab/cdef", "HEAD", "pack.idx"):
        f = root / rel
        f.write_bytes(b"x")
        os.chmod(f, stat.S_IRUSR)
    return root


def _symlink(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test make a symbolic link")


# --- the helper ------------------------------------------------------------------------------------------------------

class TestRemoveTree:

    def test_the_emulation_shows_the_windows_hazard(self, tmp_path, windows_read_only):
        """What the base did: rmtree(ignore_errors=True) leaves the read-only files without a word."""
        import shutil
        tree = _read_only_tree(tmp_path / "checkout")
        shutil.rmtree(tree, ignore_errors=True)
        assert (tree / "HEAD").exists()

    def test_a_read_only_tree_is_removed(self, tmp_path, windows_read_only):
        tree = _read_only_tree(tmp_path / "checkout")
        assert atomic.remove_tree(tree) == []
        assert not tree.exists()

    def test_nothing_there_is_nothing_to_do(self, tmp_path):
        assert atomic.remove_tree(tmp_path / "missing") == []

    @pytest.mark.parametrize("empty", ["", None])
    def test_an_empty_path_is_never_the_working_folder(self, tmp_path, monkeypatch, empty):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "keep.txt").write_text("k")
        assert atomic.remove_tree(empty) == []
        assert (tmp_path / "keep.txt").exists()

    def test_a_link_is_removed_and_never_followed(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        (target / "f").write_text("kept")
        tree = tmp_path / "tree"
        tree.mkdir()
        _symlink(target, tree / "link")
        assert atomic.remove_tree(tree) == []
        assert (target / "f").read_text() == "kept"
        top = tmp_path / "top-link"
        _symlink(target, top)
        assert atomic.remove_tree(top) == []
        assert not os.path.lexists(top) and (target / "f").exists()

    def test_a_file_is_removed_as_a_file(self, tmp_path):
        f = tmp_path / "f.tar"
        f.write_bytes(b"1")
        assert atomic.remove_tree(f) == [] and not f.exists()

    def test_what_stays_is_returned_and_logged_without_the_folder_path(self, tmp_path, monkeypatch, caplog):
        tree = _read_only_tree(tmp_path / "secret-account" / "staging")
        monkeypatch.setattr(atomic.shutil, "rmtree", lambda path, **kw: None)   # a file still open (Windows)
        with caplog.at_level(logging.WARNING, logger="orchestrator.utils.atomic"):
            left = atomic.remove_tree(tree, label="build staging")
        assert {p.relative_to(tree).as_posix() for p in left} == {"objects/ab/cdef", "objects/ab", "objects", "HEAD",
                                                                    "pack.idx"}
        text = caplog.text
        assert "build staging: 5 item(s) could not be removed" in text
        assert "secret-account" not in text and str(tmp_path) not in text

    def test_an_empty_folder_that_stays_is_reported_as_itself(self, tmp_path, monkeypatch):
        tree = tmp_path / "staging"
        tree.mkdir()
        monkeypatch.setattr(atomic.shutil, "rmtree", lambda path, **kw: None)
        assert atomic.remove_tree(tree) == [tree]

    def test_windows_repeats_a_pass_that_left_something(self, tmp_path, monkeypatch):
        tree = _read_only_tree(tmp_path / "staging")
        real, calls, sleeps = atomic.shutil.rmtree, [], []

        def busy_twice(path, **kw):
            calls.append(path)
            if len(calls) == 3:
                real(path, **kw)
        monkeypatch.setattr(atomic.shutil, "rmtree", busy_twice)
        monkeypatch.setattr(atomic.time, "sleep", sleeps.append)
        monkeypatch.setattr(atomic, "REMOVE_PASSES", 3)
        assert atomic.remove_tree(tree) == [] and len(calls) == 3 and len(sleeps) == 2

    def test_one_pass_elsewhere(self):
        assert atomic.REMOVE_PASSES == (3 if os.name == "nt" else 1)


def test_the_old_names_delegate_to_the_one_helper(tmp_path, monkeypatch):
    """runtime/build/source.remove_tree and rag/storage_lock.remove_tree stay (deprecate and delegate)."""
    from orchestrator.rag import storage_lock
    from orchestrator.runtime.build import source
    seen = []
    monkeypatch.setattr(atomic, "remove_tree", lambda path, **kw: seen.append((path, kw)) or ["left"])
    assert source.remove_tree(tmp_path / "a", label="cache") == ["left"]
    assert storage_lock.remove_tree(tmp_path / "b") == ["left"]
    assert seen == [(tmp_path / "a", {"label": "cache"}), (tmp_path / "b", {})]


def test_the_old_names_clear_read_only_trees(tmp_path, windows_read_only):
    from orchestrator.rag import storage_lock
    from orchestrator.runtime.build import source
    for remove, name in ((source.remove_tree, "a"), (storage_lock.remove_tree, "b")):
        tree = _read_only_tree(tmp_path / name)
        assert remove(tree) == [] and not tree.exists()


# --- callers ---------------------------------------------------------------------------------------------------------

class TestBestEffortCallers:
    """Clean-ups that used rmtree(ignore_errors=True): on Windows they left read-only files behind."""

    def test_a_discarded_community_quarantine_goes(self, tmp_path, windows_read_only):
        from orchestrator.Core.community_manager import CommunityManager
        quarantine = _read_only_tree(tmp_path / "quarantine")
        CommunityManager.discard(SimpleNamespace(quarantine=quarantine))
        assert not quarantine.exists()

    def test_a_maintainer_review_folder_goes_and_a_named_one_stays(self, tmp_path, windows_read_only):
        from orchestrator.Core.community_maintainer import Review
        own = _read_only_tree(tmp_path / "review")
        Review(item=None, head="", packs=[], folder=own, temporary=True).discard()
        assert not own.exists()
        named = _read_only_tree(tmp_path / "named")
        Review(item=None, head="", packs=[], folder=named, temporary=False).discard()
        assert (named / "HEAD").exists()

    def test_a_failed_build_drops_its_staging(self, tmp_path, windows_read_only):
        from orchestrator.Core.builder import BuildEngine
        staging = _read_only_tree(tmp_path / ".out.b1.staging")
        progress = SimpleNamespace(artifacts=["x"])
        BuildEngine._discard_staged(SimpleNamespace(_staging_root=lambda config: staging), None, progress)
        assert not staging.exists() and progress.artifacts == []

    def test_the_tool_quarantine_fallback_never_touches_the_working_folder(self, tmp_path, monkeypatch):
        """Quarantine._discard falls back to the quarantine attribute, '' when there is none: rmtree('') did
        nothing, and Path('') would be the working folder."""
        from orchestrator.tools.tools_community import Quarantine
        monkeypatch.chdir(tmp_path)
        (tmp_path / "keep.txt").write_text("k")

        def broken():
            raise RuntimeError("no community manager")
        Quarantine._discard(SimpleNamespace(_community=broken), SimpleNamespace())
        assert (tmp_path / "keep.txt").exists()


class TestCallersThatNeedTheFolderGone:

    def test_a_stale_build_staging_that_stays_stops_the_build(self, tmp_path, monkeypatch):
        """Leftovers of an interrupted run would be published with this build's artifacts."""
        from orchestrator.Core import builder
        staging = _read_only_tree(tmp_path / ".out.b1.staging")
        monkeypatch.setattr(builder, "remove_tree", lambda path, **kw: [path / "HEAD"])
        engine = SimpleNamespace(cache_dir=tmp_path / "cache", _staging_root=lambda c: staging,
                                 _staged_output=lambda c: staging / "out")
        progress = SimpleNamespace(add_log=lambda *a: None, current_stage="", progress_percent=0)
        config = SimpleNamespace(build_id="b1", output_dir=str(tmp_path / "out"))
        with pytest.raises(RuntimeError, match="staging folder of an earlier attempt could not be removed"):
            builder.BuildEngine._extract_artifacts(engine, config, progress)
        assert not (staging / "out").exists()

    def test_a_stale_build_staging_is_cleared_first(self, tmp_path, windows_read_only):
        from orchestrator.Core import builder
        staging = _read_only_tree(tmp_path / ".out.b1.staging")
        engine = SimpleNamespace(cache_dir=tmp_path / "cache", _staging_root=lambda c: staging,
                                 _staged_output=lambda c: staging / "out")
        logs = []
        progress = SimpleNamespace(add_log=logs.append, current_stage="", progress_percent=0)
        config = SimpleNamespace(build_id="b1", output_dir=str(tmp_path / "out"))
        builder.BuildEngine._extract_artifacts(engine, config, progress)
        assert sorted(p.name for p in staging.iterdir()) == ["out"]

    def test_licence_files_that_stay_stop_the_export(self, tmp_path, monkeypatch):
        """A stale knowledge/LICENSES folder in the export's copy would carry another snapshot's addresses."""
        from orchestrator.Core import community_manager
        from orchestrator.rag import licence_texts
        from orchestrator.Core.community_pack import PackError
        root = tmp_path / "staged"
        licences = root / "knowledge" / licence_texts.LICENSES_DIR
        licences.mkdir(parents=True)
        (licences / "old.txt").write_text("old")
        monkeypatch.setattr(community_manager, "remove_tree", lambda path, **kw: [path / "old.txt"])
        with pytest.raises(PackError, match="could not be removed from the export's copy"):
            community_manager.CommunityManager._share_snapshot(root, [])

    def test_a_half_built_conversion_copy_that_stays_stops_the_conversion(self, tmp_path, monkeypatch):
        pytest.importorskip("qdrant_edge")
        # edge_store binds storage_lock.remove_tree when first imported: imported before the patch below, so the
        # patch never outlives this test there (it made a later test_windows_host test fail when this ran first)
        from orchestrator.rag import edge_store, migration, storage_lock  # noqa: F401
        state = tmp_path / "state"
        work = migration.edge_folder(state).with_name(migration.edge_folder(state).name + migration.CONVERTING_SUFFIX)
        work.mkdir(parents=True)
        (work / "half").write_text("1")
        monkeypatch.setattr(migration, "_check_room", lambda *a, **k: None)
        monkeypatch.setattr(storage_lock, "remove_tree", lambda path: [path / "half"])
        monkeypatch.setattr(migration, "_open_old", lambda old: pytest.fail("the old store was opened"))
        with pytest.raises(migration.ConversionFailed, match="half-built copy of an earlier conversion"):
            migration.convert(state)


# --- Ratchet: no silent rmtree beside the one helper ----------------------------------------------------------------

# The one implementation: its error handler makes a read-only entry writable and tries once more, and what still stays
# is returned and logged. The allowlist of other places is empty and stays so.
THE_HELPER = ("orchestrator/utils/atomic.py", "_one_pass")   # remove_tree's one rmtree pass
ALLOWED = set()


def _silent_rmtree(call: ast.Call) -> bool:
    f = call.func
    if not ((isinstance(f, ast.Attribute) and f.attr == "rmtree") or (isinstance(f, ast.Name) and f.id == "rmtree")):
        return False
    if len(call.args) > 1 or any(isinstance(a, ast.Starred) for a in call.args):   # rmtree(path, True), a
        return True                                                                  # positional handler or *args
    for kw in call.keywords:
        if kw.arg == "ignore_errors" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
            return True
        if kw.arg in ("onerror", "onexc") or kw.arg is None:   # a handler (or **kwargs that may carry one)
            return True
    return False


def silent_rmtrees(root: Path):
    """(file, qualified function) of every shutil.rmtree in orchestrator/ and scripts/ that ignores errors or passes
    an error handler."""
    found = set()
    files = sorted((root / "orchestrator").rglob("*.py")) + sorted((root / "scripts").rglob("*.py"))
    for path in files:
        rel = path.relative_to(root).as_posix()
        stack = []

        class Visitor(ast.NodeVisitor):
            def generic_visit(self, node):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    stack.append(node.name)
                    super().generic_visit(node)
                    stack.pop()
                    return
                if isinstance(node, ast.Call) and _silent_rmtree(node):
                    found.add((rel, ".".join(stack) or "<module>"))
                super().generic_visit(node)
        Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_no_silent_rmtree_beside_the_one_helper():
    found = silent_rmtrees(REPO) - {THE_HELPER}
    assert found - ALLOWED == set(), ("shutil.rmtree that ignores errors: use orchestrator.utils.atomic.remove_tree "
                                      f"and check what it returns: {sorted(found - ALLOWED)}")
    assert THE_HELPER in silent_rmtrees(REPO), "the helper moved: update THE_HELPER"


def test_the_ratchet_sees_every_silent_form(tmp_path):
    pkg = tmp_path / "orchestrator"
    pkg.mkdir()
    (tmp_path / "scripts").mkdir()
    (pkg / "m.py").write_text(
        "import shutil\nfrom shutil import rmtree\n"
        "def a(p):\n    shutil.rmtree(p, ignore_errors=True)\n"
        "def b(p):\n    rmtree(p, True)\n"
        "class K:\n    def c(self, p):\n        shutil.rmtree(p, onexc=lambda *a: None)\n"
        "def d(p):\n    shutil.rmtree(p, onerror=print)\n"
        "def fine(p):\n    shutil.rmtree(p)\n"
        "def also_fine(p):\n    shutil.rmtree(p, ignore_errors=False)\n"
        "def e(a):\n    shutil.rmtree(*a)\n", encoding="utf-8")
    (tmp_path / "scripts" / "s.py").write_text("import shutil\nshutil.rmtree('x', ignore_errors=1)\n",
                                               encoding="utf-8")
    assert silent_rmtrees(tmp_path) == {("orchestrator/m.py", "a"), ("orchestrator/m.py", "b"),
                                        ("orchestrator/m.py", "K.c"), ("orchestrator/m.py", "d"),
                                        ("orchestrator/m.py", "e"),
                                        ("scripts/s.py", "<module>")}


# --- the helper never changes what is around the tree, and never raises on a folder it cannot open ------------------

@pytest.fixture
def still_open(monkeypatch):
    """A file named 'open.log' cannot be deleted, as a file another process still holds open on Windows."""
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        if os.path.basename(os.fsdecode(path)) == "open.log":
            raise PermissionError(13, "The process cannot access the file (in use)", os.fsdecode(path))
        return real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "remove", unlink)


@pytest.fixture
def unreadable_folders(monkeypatch):
    """A folder without its owner's read and search bits cannot be opened or listed, as for an account that is not
    root (CI runners, every person's machine), also through the descriptor-relative calls shutil.rmtree makes."""
    real_open, real_scandir = os.open, os.scandir
    locked = stat.S_IRUSR | stat.S_IXUSR

    def refuse(path, dir_fd=None):
        st = os.stat(path, dir_fd=dir_fd, follow_symlinks=False) if dir_fd is not None else os.lstat(path)
        if stat.S_ISDIR(st.st_mode) and st.st_mode & locked != locked:
            raise PermissionError(13, "Permission denied", os.fsdecode(path))

    def open_(path, flags, mode=0o777, *, dir_fd=None):
        refuse(path, dir_fd)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def scandir(path="."):
        if not isinstance(path, int):
            refuse(path)
        return real_scandir(path)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "scandir", scandir)


class TestAroundTheTree:

    def test_the_folder_around_the_tree_keeps_its_mode_when_something_stays(self, tmp_path, still_open):
        """The base's best-effort callers never touched the folder that holds the removed one (often a person's
        folder: the model folder of a bench copy, the Hugging Face cache, targets/)."""
        around = tmp_path / "models"
        tree = around / ".bench-copy"
        tree.mkdir(parents=True)
        (tree / "open.log").write_text("busy")
        os.chmod(around, 0o755)
        before = stat.S_IMODE(os.stat(around).st_mode)   # 0o755 on POSIX; Windows reports its own mode bits
        left = atomic.remove_tree(tree)
        assert [p.name for p in left] == ["open.log"]
        assert stat.S_IMODE(os.stat(around).st_mode) == before

    def test_a_folder_that_vanished_meanwhile_leaves_the_folder_around_alone(self, tmp_path, monkeypatch):
        around = tmp_path / "models"
        tree = around / ".bench-copy"
        tree.mkdir(parents=True)
        os.chmod(around, 0o755)
        before = stat.S_IMODE(os.stat(around).st_mode)   # 0o755 on POSIX; Windows reports its own mode bits
        real = atomic.shutil.rmtree

        def gone_first(path, **kw):   # another clean-up removed it between the check and the removal
            real(path)
            real(path, **kw)
        monkeypatch.setattr(atomic.shutil, "rmtree", gone_first)
        assert atomic.remove_tree(tree) == []
        assert stat.S_IMODE(os.stat(around).st_mode) == before

    def test_a_folder_that_cannot_be_opened_is_made_accessible_and_removed(self, tmp_path, unreadable_folders):
        """rmtree's error handler got os.open for such a folder; calling os.open(path) again raised TypeError (a
        missing argument), which escaped every best-effort clean-up (rmtree(ignore_errors=True) never raised)."""
        tree = tmp_path / "staging"
        (tree / "locked" / "inner").mkdir(parents=True)
        (tree / "locked" / "inner" / "f").write_text("1")
        os.chmod(tree / "locked" / "inner", 0o200)
        os.chmod(tree / "locked", 0o000)
        assert atomic.remove_tree(tree) == []
        assert not tree.exists()

    def test_a_folder_that_stays_unopenable_is_reported_not_raised(self, tmp_path, unreadable_folders, monkeypatch):
        tree = tmp_path / "staging"
        (tree / "locked").mkdir(parents=True)
        os.chmod(tree / "locked", 0o000)
        real_chmod = os.chmod

        def chmod(p, m, **kw):   # not the owner of that folder
            if os.path.basename(os.fsdecode(p)) == "locked":
                raise PermissionError(1, "Operation not permitted", os.fsdecode(p))
            return real_chmod(p, m, **kw)
        monkeypatch.setattr(os, "chmod", chmod)
        left = atomic.remove_tree(tree)
        assert [p.name for p in left] == ["locked"]
        real_chmod(tree / "locked", 0o700)
