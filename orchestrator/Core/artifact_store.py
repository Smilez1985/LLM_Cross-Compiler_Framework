#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deleting a built model (MCP design T10, tool delete_artifact)

A built model is named by its artifact id (BuildService: `a_` and 16 hex characters of its folder below the output
folder), never by a path. Deleting it removes exactly that folder and its "golden" ZIP next to it, and only when:

- the folder lies inside the output folder (resolved; a link that points elsewhere is refused, and so is the output
  folder itself),
- no unfinished build request writes into it (this process's requests, and the shared build state and build records
  of every other framework process),
- its primary file still has the sha256 the caller saw (the stale-view guard: a build that was rebuilt meanwhile is
  another build),
- and the folder is still the same folder (device and inode) the question was about.

Nothing here follows a link inside the folder: shutil.rmtree removes links, never their targets.
"""

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ArtifactRefused(ValueError):
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
    """Built models of one output folder. `artifacts()` gives (id, label, manifest) like BuildService._artifacts;
    `busy_folders()` the output folders unfinished build requests write into (every process)."""

    def __init__(self, output_dir: Path, artifacts, busy_folders):
        self.output_dir = Path(output_dir)
        self._artifacts = artifacts
        self._busy = busy_folders

    def resolve(self, artifact_id: str, expect_sha256: Optional[str] = None) -> ArtifactTarget:
        found = next((item for item in self._artifacts() if item[0] == artifact_id), None)
        if found is None:
            raise ArtifactRefused(f"Unknown built model '{artifact_id}'.", code="unknown_id",
                                  next="Call list_artifacts for the ids you can use.")
        aid, label, info = found
        folder = Path(info["path"])
        try:
            out = self.output_dir.resolve(strict=True)
            real = folder.resolve(strict=True)
        except OSError:
            raise ArtifactRefused(f"The built model {label} is no longer on disk.", code="unknown_id",
                                  next="Call list_artifacts again.") from None
        if folder.is_symlink() or real == out or out not in real.parents:
            raise ArtifactRefused(f"The built model {label} does not lie inside the output folder (a link or the "
                                  "output folder itself); it is not deleted.", code="not_supported",
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

    def delete(self, artifact_id: str, expect_sha256: Optional[str], identity: List[int]) -> int:
        """Deletes the built model the question was about; returns the bytes freed."""
        target = self.resolve(artifact_id, expect_sha256)
        if list(target.identity) != list(identity):
            raise ArtifactRefused(f"{target.label} is another folder than the one you were asked about; nothing was "
                                  "deleted.", code="failed", next="Call list_artifacts and ask again.")
        shutil.rmtree(target.folder)
        for z in target.zips:
            try:
                z.unlink()
            except OSError:
                pass
        return target.size
