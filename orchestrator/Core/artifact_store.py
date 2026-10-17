#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deleting a built model (MCP design T10, tool delete_artifact)

A built model is named by its artifact id (BuildService: `a_` and 16 hex characters of its folder below the output
folder), never by a path. Deleting it removes exactly that folder and its "golden" ZIP next to it, and only when:

- the folder lies inside the output folder or the framework's build folder (resolved; a link that points elsewhere
  is refused, and so is a data folder itself; a model the person saved to another folder is theirs to remove),
- no unfinished build request writes into it (this process's requests, and the shared build state and build records
  of every other framework process),
- its primary file still has the sha256 the caller saw (the stale-view guard: a build that was rebuilt meanwhile is
  another build),
- and the folder is still the same folder (device and inode) the question was about.

Deleting is the person's alone (round 5 f): `delete` runs only with the deletion plan the person accepted in the
framework's own window (orchestrator/Core/deletion.py), built again right before and compared item by item. In the
mode "trash" (the default, and the only one an AI's request offers) the folder and the ZIP are moved into the output
folder's trash (orchestrator/Core/trash.py): the person can restore them, and the space stays used until the person
empties the trash. In the mode "permanent" they are deleted for good.

Nothing here follows a link inside the folder: shutil.rmtree removes links, never their targets.
"""

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.Core.deletion import AcceptedDeletion, require_person, require_same
from orchestrator.tools.spec import DeletionItem, DeletionPlan
from orchestrator.utils.next_steps import WithSteps


class ArtifactRefused(WithSteps, ValueError):
    """A deletion the store refuses, with a reason code and the next step (never a host path in the message)."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


@dataclass(frozen=True)
class ArtifactTarget:
    """What a deletion is about, as the question showed it."""
    artifact_id: str
    label: str            # output:<relative path>
    folder: Path
    zips: Tuple[Path, ...]
    sha256: Optional[str]
    size: int
    identity: Tuple[int, int]

    def describe(self) -> Dict[str, Any]:
        return {"artifact_id": self.artifact_id, "label": self.label, "sha256": self.sha256 or "",
                "size": self.size, "identity": list(self.identity), "zips": [z.name for z in self.zips]}


@dataclass
class ArtifactDeletion:
    """What a deletion did: the bytes freed now (0 when moved to the trash: the space stays used until the person
    empties it) and the trash entries it made."""
    freed_bytes: int = 0
    trash_ids: List[str] = field(default_factory=list)


def _tree_size(folder: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
                total += st.st_size
            except OSError:
                continue
    return total


def _sha256(path: Path) -> Optional[str]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class ArtifactStore:
    """Built models of one output folder. `artifacts()` gives (id, label, manifest) like BuildService.artifact_entries;
    `busy_folders()` the output folders unfinished build requests write into (every process)."""

    def __init__(self, output_dir: Path, artifacts, busy_folders, roots: Optional[List[Tuple[str, Path]]] = None,
                 aliases=None):
        self.output_dir = Path(output_dir)
        self._artifacts = artifacts
        self._busy = busy_folders
        # (label, folder) of every folder a built model may lie in: the output folder, and the framework's build folder
        # (eighth round, answer 3); a model elsewhere (saved by the person to another folder) is never deleted here
        self.roots = list(roots) if roots else [("output", self.output_dir)]
        # {earlier id: current id} of builds whose id changed with the output folder setting (BuildService)
        self._aliases = aliases

    @classmethod
    def for_service(cls, service: Any) -> "ArtifactStore":
        """The store of a BuildService, through its public methods only (output_folder, artifact_entries,
        busy_output_folders, artifact_roots, artifact_aliases), so a change inside the service cannot bypass what the
        store checks."""
        roots = service.artifact_roots() if hasattr(service, "artifact_roots") else None
        return cls(service.output_folder(), service.artifact_entries, service.busy_output_folders, roots,
                   getattr(service, "artifact_aliases", None))

    def _root_of(self, real: Path) -> Optional[Tuple[str, Path]]:
        """(label, resolved folder) of the data folder `real` lies inside (never the folder itself), or None."""
        for label, folder in self.roots:
            try:
                out = Path(folder).resolve(strict=True)
            except OSError:
                continue
            if real != out and out in real.parents:
                return label, out
        return None

    def resolve(self, artifact_id: str, expect_sha256: Optional[str] = None) -> ArtifactTarget:
        items = self._artifacts()
        found = next((item for item in items if item[0] == artifact_id), None)
        if found is None and callable(self._aliases):   # an id from before the output folder setting
            current = self._aliases().get(artifact_id)
            found = next((item for item in items if current and item[0] == current), None)
        if found is None:
            raise ArtifactRefused(f"Unknown built model '{artifact_id}'.", code="unknown_id",
                                  next="Call list_artifacts for the ids you can use.")
        aid, label, info = found
        folder = Path(info["path"])
        try:
            real = folder.resolve(strict=True)
        except OSError:
            raise ArtifactRefused(f"The built model {label} is no longer on disk.", code="unknown_id",
                                  next="Call list_artifacts again.") from None
        if folder.is_symlink() or self._root_of(real) is None:
            raise ArtifactRefused(f"The built model {label} does not lie inside the output folder or the "
                                  "framework's build folder (a link, a folder itself, or a folder the person saved "
                                  "it to); it is not deleted.", code="not_supported",
                                  next="Tell the person; they remove it themselves if they want.")
        for busy in self._busy():
            try:
                busy_real = Path(busy).resolve()
            except OSError:
                continue
            if busy_real == real or real in busy_real.parents or busy_real in real.parents:
                raise ArtifactRefused(f"A build request that has not finished writes into {label}; it is not "
                                      "deleted.", code="not_supported",
                                      next="Wait until that build ends (get_build_status), then ask again.")
        primary = info.get("primary")
        sha = _sha256(real / primary) if primary else None
        listed = next((a.get("sha256") for a in (info.get("artifacts") or [])
                       if isinstance(a, dict) and a.get("file") == primary), None)
        current = sha or listed
        if current and expect_sha256 is None:
            raise ArtifactRefused(f"Pass the sha256 of {label}'s model file as list_artifacts shows it "
                                  "(expect_sha256), so the deletion is about the build you saw.",
                                  code="missing_information", next="Call list_artifacts and pass its sha256.")
        if expect_sha256 is not None and current and expect_sha256.lower() != current.lower():
            raise ArtifactRefused(f"{label} changed since you looked (its model file has another sha256); it is not "
                                  "deleted.", code="failed", next="Call list_artifacts again and ask the person again.")
        st = os.stat(real)
        golden = real.parent / f"{real.name}.zip"  # the build engine's "golden" ZIP next to the folder
        zips = (golden,) if golden.is_file() and not golden.is_symlink() else ()
        size = _tree_size(real) + sum(z.stat().st_size for z in zips)
        return ArtifactTarget(aid, label, real, zips, current, size, (st.st_dev, st.st_ino))

    def plan(self, target: ArtifactTarget, modes: Tuple[str, ...] = ("trash", "permanent")) -> DeletionPlan:
        """What the person is shown in the framework's own window, item by item with the full path (an AI sees labels
        and sizes only): exactly the folder and the golden ZIP a deletion moves or deletes. OSError or ValueError
        when the folder changed while it was looked up."""
        zips = [(z, z.stat().st_size) for z in target.zips]
        folder_bytes = max(0, target.size - sum(s for _, s in zips))
        items = [DeletionItem(kind="artifact", speaking_name=target.label, full_path=str(target.folder),
                              file_name=target.folder.name, size_bytes=folder_bytes,
                              what="built model folder (the model files and build record of this build)")]
        items += [DeletionItem(kind="artifact", speaking_name=f"{target.label} (golden ZIP)", full_path=str(z),
                               file_name=z.name, size_bytes=size, what="the build's golden ZIP next to the folder")
                  for z, size in zips]
        return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=tuple(modes))

    def delete(self, artifact_id: str, expect_sha256: Optional[str], identity: List[int],
               accepted: AcceptedDeletion, trash: Any = None, deleted_by: str = "") -> ArtifactDeletion:
        """Deletes the built model the question was about, exactly as the person accepted it (`accepted`: the plan,
        the mode and where the person answered): into the trash (`trash`, a Trash) or for good. PermissionError
        without the person's accepted plan; PlanChanged when the folder or its ZIP changed since the plan was
        shown."""
        accepted = require_person(accepted)
        target = self.resolve(artifact_id, expect_sha256)
        if list(target.identity) != list(identity):
            raise ArtifactRefused(f"{target.label} is another folder than the one you were asked about; nothing was "
                                  "deleted.", code="failed", next="Call list_artifacts and ask again.")
        try:
            current = self.plan(target, accepted.plan.mode_offered)
        except (OSError, ValueError):
            current = None
        require_same(accepted, current, f"the built model {target.label}")
        result = ArtifactDeletion()
        if accepted.mode == "trash":
            if trash is None:
                raise ArtifactRefused("The trash is not available; nothing was deleted.", code="failed",
                                      next="Tell the person; run_mojo_oracle (or 'llmcf mojo' in a terminal) shows "
                                           "the cause.")
            from orchestrator.Core.trash import TrashError
            label, root = self._root_of(target.folder) or ("output", self.output_dir.resolve())
            for item, path in zip(current.items, (target.folder, *target.zips)):
                try:
                    entry = trash.move("artifact", path, root, label, item.speaking_name, accepted, deleted_by)
                except TrashError as e:
                    if not result.trash_ids:
                        raise
                    # Stopped half way: what went to the trash stays there, the rest where it was; both say so.
                    raise ArtifactRefused(f"{len(result.trash_ids)} of {len(current.items)} items of {target.label} "
                                          f"were moved to the trash, then this stopped: {e} The rest stays where it "
                                          "was.", code="failed",
                                          next="Tell the person; File > Trash... shows what was moved, and they can "
                                               "restore it there.") from None
                result.trash_ids.append(entry.id)
            return result
        shutil.rmtree(target.folder)
        for z in target.zips:
            try:
                z.unlink()
            except OSError:
                pass
        result.freed_bytes = target.size
        return result
