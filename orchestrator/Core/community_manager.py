#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Manager

Lists and installs community target modules (community/<id>/), imports knowledge packs
(community/knowledge/*.json) and exports the local knowledge base for sharing.

The framework never uploads anything and never asks for a repository token. Community packs are
signed (Ed25519), and private sharing is encrypted, through orchestrator/Core/community_pack.py.
"""

import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

from orchestrator.utils.logging import get_logger

# A plain folder name: no separators, no '..', at most 64 characters.
MODULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def ensure_directory(path: Path) -> Path:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    return path


def _links_and_special_files(root: Path) -> List[str]:
    """Paths below root (by lstat, nothing followed) that are links or neither files nor folders."""
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            st = os.lstat(path)
            if (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400
                    or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode))):
                found.append(str(path.relative_to(root)))
    return found


@dataclass
class CommunityModule:
    id: str
    name: str
    description: str
    architecture: str
    author: str
    version: str
    path: Path
    is_installed: bool = False


class CommunityManager:

    def __init__(self, framework_manager):
        self.logger = get_logger("CommunityManager")
        self.framework = framework_manager

        if hasattr(framework_manager, 'info') and hasattr(framework_manager.info, 'installation_path'):
            self.app_root = Path(framework_manager.info.installation_path)
        else:
            self.app_root = Path(".").resolve()

        self.config = getattr(framework_manager, 'config', None)

        self.community_dir = self.app_root / "community"
        self.knowledge_dir = self.community_dir / "knowledge"
        self.targets_dir = self.app_root / "targets"
        # Directories are created when something is written, not at every CLI/GUI start.

    # --- MODULE MANAGEMENT ---
    def scan_modules(self) -> List[CommunityModule]:
        modules = []
        if not self.community_dir.is_dir() or not self.targets_dir.exists():
            return []

        installed_targets = {p.name.casefold() for p in self.targets_dir.iterdir() if p.is_dir()}

        for item in self.community_dir.iterdir():
            # Links are never listed (or installed): they could point anywhere on this computer.
            if not MODULE_ID_RE.fullmatch(item.name) or item.is_symlink() or not item.is_dir():
                continue
            target_yml = item / "target.yml"
            if target_yml.is_symlink() or not target_yml.is_file():
                continue
            try:
                with open(target_yml, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                meta = data.get('metadata', {}) if isinstance(data, dict) else {}
                modules.append(CommunityModule(
                    id=item.name, name=str(meta.get('name', item.name)),
                    description=str(meta.get('description', 'No description')),
                    architecture=str(meta.get('architecture_family', 'unknown')),
                    author=str(meta.get('maintainer', 'Community')),
                    version=str(meta.get('version', '1.0.0')),
                    path=item, is_installed=item.name.casefold() in installed_targets,
                ))
            except Exception as e:
                self.logger.error(f"Error loading module {item.name}: {e}")
        return sorted(modules, key=lambda x: x.name)

    def install_module(self, module_id: str) -> Path:
        """Copies community/<id> to targets/<id>. Refuses ids that are not plain folder names, links and special
        files anywhere in the module, and existing targets (also when only the case differs). The copy appears
        under its final name only when it is complete."""
        if not isinstance(module_id, str) or not MODULE_ID_RE.fullmatch(module_id):
            raise ValueError(f"Invalid module id {module_id!r}: letters, digits, '.', '_' and '-' only.")
        source = self.community_dir / module_id
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"Community module '{module_id}' not found (links are not accepted).")
        unsafe = _links_and_special_files(source)
        if unsafe:
            raise ValueError(f"Community module '{module_id}' contains links or special files, which are never "
                             f"installed: {', '.join(unsafe[:5])}")
        dest = self.targets_dir / module_id
        ensure_directory(self.targets_dir)
        if any(p.name.casefold() == module_id.casefold() for p in self.targets_dir.iterdir()):
            raise FileExistsError(f"Target '{module_id}' already exists.")
        staging = Path(tempfile.mkdtemp(prefix=f".install-{module_id}.", dir=self.targets_dir))
        try:
            # symlinks=True: should a link appear after the check, it is copied as a link, never followed.
            shutil.copytree(source, staging / module_id, symlinks=True)
            if _links_and_special_files(staging / module_id):
                raise ValueError(f"Community module '{module_id}' changed while it was installed.")
            os.rename(staging / module_id, dest)  # fails if dest appeared meanwhile; never merges
        except Exception as e:
            self.logger.error(f"Install of {module_id} failed: {e}")
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return dest

    # --- KNOWLEDGE BASE ---
    def sync_knowledge_base(self) -> int:
        if not self.framework or not hasattr(self.framework, 'get_component'):
            return 0

        rag_manager = self.framework.get_component("rag_manager")
        if not rag_manager:
            return 0

        imported_count = 0

        for json_file in self.knowledge_dir.glob("*.json"):
            try:
                if json_file.with_suffix(".imported").exists():
                    continue
                self.logger.info(f"Syncing {json_file.name}...")
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if isinstance(data, dict) and data.get("swarm_encrypted", False):
                    # The old obfuscated format (no real encryption, no authenticity) is no longer read.
                    self.logger.warning(f"Skipped {json_file.name}: the old 'swarm' format is no longer "
                                        f"supported; ask for a signed community pack instead.")
                    continue

                if isinstance(data, list):
                    for entry in data:
                        meta = entry.get("metadata", {})
                        meta["origin_pack"] = json_file.name
                        if rag_manager.ingest_document(entry.get("source", ""), entry.get("content", ""), meta):
                            imported_count += 1

                with open(json_file.with_suffix(".imported"), 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                self.logger.error(f"Sync failed {json_file.name}: {e}")
        return imported_count

    def export_knowledge_base(self, output_name_prefix: str = "knowledge_export") -> Optional[str]:
        """Writes the local knowledge base as JSON with the privacy scrub applied (home folders, addresses,
        host and user names, secrets). It stays on this computer; sharing it is a separate, explicit step."""
        if not self.framework or not hasattr(self.framework, 'get_component'):
            return None

        rag_manager = self.framework.get_component("rag_manager")
        if not rag_manager:
            return None

        try:
            records = list(rag_manager.export_records())
        except Exception as e:
            self.logger.error(f"Knowledge export failed: {e}")
            return None

        export_data = []
        for record in records:
            c = self._sanitize_content(record.get("content", ""))
            s = self._sanitize_content(record.get("source", ""))
            meta = {k: self._sanitize_content(v) if isinstance(v, str) else v
                    for k, v in (record.get("metadata") or {}).items()}  # e.g. URLs with tokens in the query
            if c:
                export_data.append({"source": s, "content": c, "metadata": meta})
        if not export_data:
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = ensure_directory(self.knowledge_dir) / f"{output_name_prefix}_{ts}.json"
        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2)
        return str(export_path)

    def _sanitize_content(self, text: str) -> str:
        from orchestrator.Core.community_pack import scrub
        return scrub(text)
