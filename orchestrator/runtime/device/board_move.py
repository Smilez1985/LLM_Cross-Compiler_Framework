#!/usr/bin/env python3
"""
A board's data moves with its new host key (DECISIONS round 22, P6). The device ID derives from the host key
(remote/trust.py device_id_for), so when the person confirms a new host key for a board (Q5,
person_steps.change_board), the board's data moves from the old device ID to the new one:

- probe: its stored probe and the probe's label and address (<state dir>/hardware/devices/<id>.txt and .json)
- deploy_settings: its deploy settings (<state dir>/hardware/devices/<id>.deploy.json)
- runtime: its deployed runtime (<state dir>/runtime/llama.cpp/device/<id>/: the record of the last deployment with
  its checks and its last benchmark on the board, and the board's runtime packages)
- deploy_record: its deploy record (<state dir>/device_deploys.json: the last deploy, what a deploy saw there, the
  last contact's problem)
- tested_on: its benchmark results with the builds of the benchmarked models ("tested on", <build>/device_tests.json
  in every golden-artifact folder)

The move never loses anything and never deletes the old ID's data before its copy is checked:

1. stage(): every item is copied to the new ID and checked: a file is written atomically and read back, the runtime
   folder is made under a temporary name (each file a hard link of the original, else a copy whose sha256 is
   compared) and renamed to the new ID in one step, a record gets its copy next to it in one atomic write of its
   file. When anything fails, the copies made so far are removed again (BoardMoveError; a Ctrl+C goes on after
   that): nothing has changed then.
2. The caller makes the change itself (known_hosts and the board's trusted record); when known_hosts is not changed
   (refused, or the change ends any other way), undo() removes the copies, and the board's data is where it was.
3. finish(): once known_hosts holds the change, the old ID's items go (also when the record step after it ends early);
   one that cannot be removed stays and is named in the result (`left`; its checked copy is under the new ID).

What the new ID holds already is never overwritten: an item the new ID has with the same content counts as moved
(its old copy goes at the end), one with other content stays under the old ID (`kept`). The probe's two files (its
profile and its label and address) are one item: they move or stay together.

Only the person's board steps use this module (the GUI and the CLI behind person_steps.change_board); no module an AI
can reach imports it (tests/test_remote_trust.py follows the imports).
"""

import os
import secrets
import shutil
from orchestrator.utils.atomic import remove_tree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence

# The kinds of a board's data, in the order they are moved and named.
KINDS = ("probe", "deploy_settings", "runtime", "deploy_record", "tested_on")
_MOVING = ".moving-"   # the runtime folder's temporary name: .<new id>.moving-<random>


class BoardMoveError(RuntimeError):
    """The board's data could not be copied to its new device ID; nothing was changed. `what` names the kind."""

    def __init__(self, message: str, what: str = ""):
        super().__init__(message)
        self.what = what


@dataclass
class MovedData:
    """What the move did: the kinds now under the new ID only (`moved`), those that stay under the old ID because the
    new ID holds its own (`kept`), and those whose old copy could not be removed after the change (`left`: their
    checked copy is under the new ID)."""
    old_id: str
    new_id: str
    moved: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    left: List[str] = field(default_factory=list)


class _Item:
    """One part of the board's data: stage() copies and checks it (its state: none, staged, present or kept), undo()
    removes the copy it made, finish() removes the old one."""
    kind = ""
    state = "none"

    def stage(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError

    def finish(self) -> None:
        raise NotImplementedError


class _File(_Item):
    """The small text files of one kind in the profile store, given as (old, new) pairs, each written atomically for
    the new ID with the profile store's own writer (utils/atomic.py write_private: mode 0600) and read back. The files
    of one kind belong together (the probe: its profile and its label and address file): when the new ID holds one of
    them with other content, or one the old ID does not have, none of them is copied (kept)."""

    def __init__(self, kind: str, *pairs: Any):
        self.kind = kind
        self.pairs = [(Path(source), Path(target)) for source, target in pairs]
        self.written: List[Path] = []

    def stage(self) -> None:
        from orchestrator.utils.atomic import write_private
        texts = [(target, source.read_text(encoding="utf-8") if source.is_file() else None)
                 for source, target in self.pairs]
        if all(text is None for _target, text in texts):
            return
        if any(target.exists() and (text is None or target.read_text(encoding="utf-8") != text)
               for target, text in texts):
            self.state = "kept"
            return
        self.state = "present"
        for target, text in texts:
            if text is None or target.exists():
                continue
            write_private(target, text)
            self.written.append(target)
            self.state = "staged"
            if target.read_text(encoding="utf-8") != text:
                raise OSError("the copy does not read back")

    def undo(self) -> None:
        if self.state == "staged":
            for target in self.written:
                target.unlink(missing_ok=True)

    def finish(self) -> None:
        for source, _target in self.pairs:
            source.unlink(missing_ok=True)


def _same_file(a: Path, b: Path) -> bool:
    from orchestrator.utils.helpers import sha256_file
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        return False
    return a.stat().st_size == b.stat().st_size and sha256_file(a) == sha256_file(b)


def _same_tree(a: Path, b: Path) -> bool:
    """Whether the folders hold the same names with the same content (links are not followed)."""
    names = sorted(p.name for p in a.iterdir())
    if names != sorted(p.name for p in b.iterdir()):
        return False
    for name in names:
        x, y = a / name, b / name
        if x.is_symlink() or y.is_symlink():
            if not (x.is_symlink() and y.is_symlink() and os.readlink(x) == os.readlink(y)):
                return False
        elif x.is_dir() and y.is_dir():
            if not _same_tree(x, y):
                return False
        elif not (x.is_file() and y.is_file() and _same_file(x, y)):
            return False
    return True


def _link_or_copy_tree(source: Path, target: Path) -> None:
    """`target` (new) with every file of `source`: a hard link when the file system allows it (no space, the same
    bytes), else a copy whose sha256 must be the original's; folders alike, links as links."""
    from orchestrator.utils.helpers import copy_hashed, sha256_file
    target.mkdir(mode=0o700)
    for entry in sorted(source.iterdir()):
        dest = target / entry.name
        if entry.is_symlink():
            os.symlink(os.readlink(entry), dest)
        elif entry.is_dir():
            _link_or_copy_tree(entry, dest)
        elif entry.is_file():
            try:
                os.link(entry, dest)
                same = os.path.samefile(entry, dest)
            except OSError:
                same = copy_hashed(entry, dest) == sha256_file(entry)   # the bytes written are the bytes hashed
                shutil.copymode(entry, dest)
            if not same:
                raise OSError(f"the copy of {entry.name} differs from the original")


class _Folder(_Item):
    """The board's runtime folder: staged under a temporary name next to it and renamed to the new ID in one step."""

    def __init__(self, kind: str, source: Path, target: Path):
        self.kind, self.source, self.target = kind, Path(source), Path(target)

    def stage(self) -> None:
        if not self.source.is_dir() or self.source.is_symlink():
            return
        if self.target.exists():
            self.state = "present" if self.target.is_dir() and _same_tree(self.source, self.target) else "kept"
            return
        parent = self.source.parent
        for stale in parent.glob(f".{self.target.name}{_MOVING}*"):   # what an interrupted move left
            remove_tree(stale)
        temporary = parent / f".{self.target.name}{_MOVING}{secrets.token_hex(4)}"
        try:
            _link_or_copy_tree(self.source, temporary)
            if not _same_tree(self.source, temporary):
                raise OSError("the copy differs from the original")
            os.rename(temporary, self.target)   # one step; refused (OSError) when the target appeared meanwhile
        except BaseException:
            remove_tree(temporary)
            raise
        self.state = "staged"

    def undo(self) -> None:
        if self.state == "staged":
            remove_tree(self.target)   # hard links: only the new names go

    def finish(self) -> None:
        shutil.rmtree(self.source)


class _DeployRecord(_Item):
    """The board's row in device_deploys.json (runtime/device/trusted.py TrustedDevices, under its lock)."""
    kind = "deploy_record"

    def __init__(self, state_dir: Path, old_id: str, new_id: str):
        from orchestrator.runtime.device.trusted import TrustedDevices
        self.store, self.old_id, self.new_id = TrustedDevices(state_dir), old_id, new_id
        self.row: Any = None

    def stage(self) -> None:
        self.row = self.store.last_deploys().get(self.old_id)
        try:
            result = self.store.copy_deploys(self.old_id, self.new_id)
        except BaseException:
            self.state = "staged"   # what was written: undo() removes exactly this copy
            raise
        self.state = {"copied": "staged"}.get(result, result)

    def undo(self) -> None:
        if self.state == "staged":
            self.store.drop_deploys(self.new_id, only=self.row)

    def finish(self) -> None:
        self.store.drop_deploys(self.old_id)


class _TestedOn(_Item):
    """The board's records in one build's device_tests.json (runtime/device/tested.py, under its lock)."""
    kind = "tested_on"

    def __init__(self, build: Path, old_id: str, new_id: str):
        self.build, self.old_id, self.new_id = Path(build), old_id, new_id

    def stage(self) -> None:
        from orchestrator.runtime.device.tested import copy_board_rows
        try:
            copied = copy_board_rows(self.build, self.old_id, self.new_id)
        except BaseException:
            self.state = "staged"   # what was written: undo() removes exactly the copies
            raise
        if copied:
            self.state = "staged"

    def undo(self) -> None:
        from orchestrator.runtime.device.tested import drop_board_rows
        if self.state == "staged":
            drop_board_rows(self.build, self.new_id, copies_of=self.old_id)

    def finish(self) -> None:
        from orchestrator.runtime.device.tested import drop_board_rows
        drop_board_rows(self.build, self.old_id)


class BoardDataMove:
    """The move of one board's data from `old_id` to `new_id` (see the module docstring): stage(), then the caller's
    change, then finish() (or undo() when the change failed). `output_dirs`: the golden-artifact folders whose builds
    may hold the board's "tested on" records (Core/artifact_output.py artifact_folders)."""

    def __init__(self, state_dir: Path, old_id: str, new_id: str, *, output_dirs: Sequence[Path] = ()):
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        from orchestrator.runtime.device.tested import builds_with_board
        from orchestrator.runtime.profile import ProfileStore
        if old_id == new_id:
            raise ValueError("the board keeps its device ID: there is nothing to move")
        self.state_dir, self.old_id, self.new_id = Path(state_dir), old_id, new_id
        profiles = ProfileStore(self.state_dir)
        old_files, new_files = profiles.device_files(old_id), profiles.device_files(new_id)
        self.items: List[_Item] = [
            _File("probe", (old_files["probe"], new_files["probe"]),
                  (old_files["probe_info"], new_files["probe_info"])),
            _File("deploy_settings", (old_files["deploy_settings"], new_files["deploy_settings"])),
            _File("limits", (old_files["limits"], new_files["limits"])),
            _Folder("runtime", DeviceRuntimeStore(self.state_dir, old_id).root,
                    DeviceRuntimeStore(self.state_dir, new_id).root),
            _DeployRecord(self.state_dir, old_id, new_id),
        ] + [_TestedOn(build, old_id, new_id) for build in builds_with_board(output_dirs, old_id)]
        self.staged = False

    def stage(self) -> None:
        """Copies and checks every item for the new ID; BoardMoveError (with the copies removed again) when one
        cannot be copied: nothing has changed then. A Ctrl+C meanwhile also removes the copies before it goes on."""
        done: List[_Item] = []
        for item in self.items:
            try:
                item.stage()
            except BaseException as e:  # an OSError, a lock that timed out, a record that is too large; a Ctrl+C
                done.append(item)
                self._undo(done)
                if not isinstance(e, Exception):
                    raise
                raise BoardMoveError(f"The board's {_WORDS[item.kind]} could not be copied to its new device ID "
                                     f"{self.new_id} ({e.__class__.__name__}); nothing was changed.",
                                     item.kind) from None
            done.append(item)
        self.staged = True

    def undo(self) -> None:
        """Removes the copies stage() made (the change was not made): the board's data is where it was."""
        self._undo(self.items)
        self.staged = False

    @staticmethod
    def _undo(items: Iterable[_Item]) -> None:
        for item in items:
            try:
                item.undo()
            except Exception:  # noqa: BLE001 - a copy that stays is a copy; the original is untouched
                pass

    def finish(self) -> MovedData:
        """After the change: removes the old ID's items whose checked copy is under the new ID. Never raises; what
        could not be removed is named in `left`."""
        result = MovedData(self.old_id, self.new_id)
        for item in self.items:
            if item.state == "kept":
                _add(result.kept, item.kind)
            elif item.state in ("staged", "present"):
                try:
                    item.finish()
                    _add(result.moved, item.kind)
                except Exception:  # noqa: BLE001 - the copy is complete; the old one is named
                    _add(result.left, item.kind)
        result.moved = [k for k in result.moved if k not in result.left]
        return result


def _add(kinds: List[str], kind: str) -> None:
    if kind not in kinds:
        kinds.append(kind)


# The kinds in the person's words (English; the GUI shows its own texts, boards.edit.data.<kind>).
_WORDS = {"probe": "stored probe", "deploy_settings": "deploy settings", "limits": "measured limits", "runtime": "deployed runtime",
          "deploy_record": "deploy record", "tested_on": "benchmark results ('tested on')"}


def kind_words(kinds: Iterable[str], word: Optional[Callable[[str], str]] = None) -> str:
    """The kinds as one list for a sentence (the English words, or `word(kind)`)."""
    return ", ".join((word or _WORDS.get)(k) for k in kinds)


__all__ = ["BoardDataMove", "BoardMoveError", "KINDS", "MovedData", "kind_words"]
