"""Private atomic file writes (consolidation chain c1-imports: orchestrator/utils/atomic.py).

Characterization first: the two writers the framework shares keep their own rules.
- Core/vault_key.write_private (the secrets store, profiles, trusted boards, notices, the input history, the GUI
  bridge): the link is resolved first (a link onto an encrypted volume keeps pointing there), the folder is made
  0700, a mkstemp file ".<name>.<random>.tmp" next to the target, fsync, 0600 (not on Windows), then replace_file.
- runtime/llama_server._write_private (the runtime's own state files): a ".<name>.<8 hex>.tmp" file created with
  O_EXCL and 0600, LF line ends on every system, no fsync, no link resolution (a link is replaced by the file),
  replace_file (port-runtime: it used a plain os.replace, which Windows refuses while LLMClient reads server.json).
Both leave no temporary file behind, also when the replace fails. remove_file deletes the runtime's state, key and
lock files and tries again on Windows while another process has the file open.
"""

import os
import re
import sys
from pathlib import Path

import pytest

from orchestrator.Core import vault_key
from orchestrator.runtime import llama_server

POSIX = os.name != "nt"


def _symlink(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test make a symbolic link")


class TestVaultKeyWritePrivate:

    def test_writes_the_text_through_a_temporary_file_with_fsync(self, tmp_path, monkeypatch):
        replaced, synced = [], []
        real_replace, real_fsync = os.replace, os.fsync
        monkeypatch.setattr(os, "replace", lambda s, d: replaced.append((Path(s), Path(d))) or real_replace(s, d))
        monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
        target = tmp_path / "state" / "notice.json"
        vault_key.write_private(target, "a\nb\n")
        assert target.read_bytes() == "a\nb\n".replace("\n", os.linesep).encode("utf-8")
        ((src, dst),) = replaced
        assert dst == Path(os.path.realpath(target)) and src.parent == dst.parent
        assert re.fullmatch(r"\.notice\.json\..+\.tmp", src.name), src.name
        assert synced, "the text is on the disk before the rename"
        assert list(target.parent.iterdir()) == [target]

    @pytest.mark.skipif(not POSIX, reason="POSIX file modes")
    def test_the_file_is_0600_and_a_new_folder_0700(self, tmp_path):
        target = tmp_path / "new" / "deeper" / "x.json"
        vault_key.write_private(target, "{}")
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.parent.stat().st_mode & 0o777 == 0o700

    def test_a_link_is_written_through_to_its_target(self, tmp_path):
        real = tmp_path / "volume" / "secrets.json"
        real.parent.mkdir()
        real.write_text("old", encoding="utf-8")
        link = tmp_path / "secrets.json"
        _symlink(real, link)
        vault_key.write_private(link, "new")
        assert link.is_symlink() and real.read_text(encoding="utf-8") == "new"

    def test_a_failed_replace_leaves_the_old_file_and_no_temporary_file(self, tmp_path, monkeypatch):
        target = tmp_path / "w" / "x.json"
        target.parent.mkdir()
        target.write_text("old", encoding="utf-8")

        def refuse(src, dst):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(os, "replace", refuse)
        with pytest.raises(OSError):
            vault_key.write_private(target, "new")
        assert target.read_text(encoding="utf-8") == "old" and list(target.parent.iterdir()) == [target]


class TestRuntimeWritePrivate:

    def test_writes_lf_through_an_exclusive_temporary_file_without_fsync(self, tmp_path, monkeypatch):
        replaced, synced, opened = [], [], []
        real_replace, real_open = os.replace, os.open
        monkeypatch.setattr(os, "replace", lambda s, d: replaced.append((Path(s), Path(d))) or real_replace(s, d))
        monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
        monkeypatch.setattr(os, "open", lambda p, flags, mode=0o777, **k: opened.append((Path(p), flags, mode))
                            or real_open(p, flags, mode, **k))
        target = tmp_path / "runtime" / "active.json"
        llama_server._write_private(target, "a\nb\n")
        assert target.read_bytes() == b"a\nb\n", "LF on every system (a Linux container reads some of these)"
        ((src, dst),) = replaced
        assert dst == target and re.fullmatch(r"\.active\.json\.[0-9a-f]{8}\.tmp", src.name), src.name
        ((path, flags, mode),) = [o for o in opened if o[0] == src]
        assert flags & os.O_EXCL and flags & os.O_CREAT and mode == 0o600
        assert synced == []
        assert list(target.parent.iterdir()) == [target]

    @pytest.mark.skipif(not POSIX, reason="POSIX file modes")
    def test_the_file_is_0600(self, tmp_path):
        target = tmp_path / "state.json"
        llama_server._write_private(target, "{}")
        assert target.stat().st_mode & 0o777 == 0o600

    def test_a_link_is_replaced_by_the_file(self, tmp_path):
        real = tmp_path / "elsewhere.json"
        real.write_text("old", encoding="utf-8")
        link = tmp_path / "state.json"
        _symlink(real, link)
        llama_server._write_private(link, "new")
        assert not link.is_symlink() and link.read_text(encoding="utf-8") == "new"
        assert real.read_text(encoding="utf-8") == "old"

    def test_a_failed_replace_leaves_the_old_file_and_no_temporary_file(self, tmp_path, monkeypatch):
        target = tmp_path / "w" / "x.json"
        target.parent.mkdir()
        target.write_text("old", encoding="utf-8")

        def refuse(src, dst):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(os, "replace", refuse)
        with pytest.raises(OSError):
            llama_server._write_private(target, "new")
        assert target.read_text(encoding="utf-8") == "old" and list(target.parent.iterdir()) == [target]


def test_replace_file_tries_again_while_a_reader_holds_the_target(tmp_path, monkeypatch):
    """Windows: os.replace fails while another process has the target open; replace_file tries again for a moment
    (REPLACE_RETRIES times on Windows, never elsewhere)."""
    module = sys.modules[vault_key.replace_file.__module__]
    assert module.REPLACE_RETRIES == (10 if os.name == "nt" else 0)
    monkeypatch.setattr(module, "REPLACE_RETRIES", 3)
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_replace, busy = os.replace, {"left": 2}

    def busy_twice(src, dst):
        if busy["left"]:
            busy["left"] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", busy_twice)
    (tmp_path / "a").write_text("x", encoding="utf-8")
    vault_key.replace_file(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b").read_text(encoding="utf-8") == "x"
    busy["left"] = 9
    (tmp_path / "a").write_text("y", encoding="utf-8")
    with pytest.raises(PermissionError):
        vault_key.replace_file(tmp_path / "a", tmp_path / "b")


def test_the_runtime_writer_tries_again_while_a_reader_holds_the_target(tmp_path, monkeypatch):
    """Windows refuses os.replace while another process (LLMClient in the GUI, a lock waiter) has server.json or
    active.json open; the runtime's writer (also the build store's and the runtime mode's) tries again like
    replace_file, and elsewhere a PermissionError is a real error that leaves no temporary file."""
    from orchestrator.utils import atomic
    monkeypatch.setattr(atomic, "REPLACE_RETRIES", 10)  # what a Windows host uses
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_replace, refused = os.replace, []

    def busy_twice(src, dst):
        if len(refused) < 2:
            refused.append(dst)
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", busy_twice)
    target = tmp_path / "runtime" / "server.json"
    llama_server._write_private(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}" and len(refused) == 2
    refused.clear()
    monkeypatch.setattr(atomic, "REPLACE_RETRIES", 0)  # Linux and macOS: no retry
    with pytest.raises(PermissionError):
        llama_server._write_private(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == "{}" and list(target.parent.iterdir()) == [target]


def test_remove_file_tries_again_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    """remove_file: a missing file is fine (missing_ok); a PermissionError (Windows: another process has the file
    open) is tried again REPLACE_RETRIES times, never elsewhere."""
    from orchestrator.utils import atomic
    assert atomic.REPLACE_RETRIES == (10 if os.name == "nt" else 0)
    monkeypatch.setattr(atomic, "REPLACE_RETRIES", 3)
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_unlink, busy = os.unlink, {"left": 2}

    def busy_twice(path, *args, **kwargs):
        if busy["left"]:
            busy["left"] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", busy_twice)
    target = tmp_path / "server-1.key"
    target.write_text("k", encoding="utf-8")
    atomic.remove_file(target)
    assert not target.exists() and busy["left"] == 0
    atomic.remove_file(target)  # already gone
    target.write_text("k", encoding="utf-8")
    busy["left"] = 9
    with pytest.raises(PermissionError):
        atomic.remove_file(target)
    assert target.exists()


def test_the_old_names_are_the_one_implementation():
    """Core/vault_key and runtime/llama_server keep their names (callers and tests use them); each is the object in
    orchestrator/utils/atomic.py, which the utils, the kernel and the runtime import."""
    from orchestrator.utils import atomic
    assert vault_key.write_private is atomic.write_private and vault_key.replace_file is atomic.replace_file
    assert vault_key.REPLACE_RETRIES == atomic.REPLACE_RETRIES
    assert llama_server._write_private is atomic.write_private_exclusive
