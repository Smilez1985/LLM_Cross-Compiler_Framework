#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Builds the person confirmed (MCP design 6.10, T9 round 3)

A target or recipe pack an AI client installed under a standing approval (`installed_via: grant:<id>`) stays outside
every build standing approval until the person has confirmed one build of it: "install third-party build code" and
"run its image build with network" never happen together without a question. This store records that confirmation,
so the rule works in both directions: before the first confirmed build a build standing approval does not cover the
pack, after it it does.

A confirmation counts only when the person answered the framework's own question (Ditto's GUI dialog or CLI prompt,
or a pending approval in the framework's window). An MCP form elicitation does not count: an accepted elicitation is
the client's statement, and an automated client may answer by itself (the same reason it can never create a standing
approval). A standing approval's own use does not count either, nor does a tool the person set to "allow".

An entry names the pack as installed (its `installed_via` and pack sha256): a reinstall under another standing
approval or an updated pack needs a new confirmation. It also keeps the digest-pinned base images of the target's
Dockerfile at the time of the confirmed build: a build standing approval covers a base image outside the Docker
Official Images only when the person confirmed a build with exactly that pinned image before.

The file lives in the per-user folder (`confirmed_builds.json`, mode 0600), written under a lock by framework code
only, after a confirmed build was accepted; no tool writes it from its arguments.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from orchestrator.utils.file_lock import file_lock

FILE_NAME = "confirmed_builds.json"
MAX_ENTRIES = 2000
MAX_IMAGES = 16
# Confirmation channels where the person answered the framework's own question (registry: ctx.confirm_channel).
PERSON_CHANNELS = ("framework", "framework-gui", "framework-cli")


def person_confirmed(channel: Optional[str]) -> bool:
    """Whether a call was confirmed by the person in the framework's own window (not an elicitation, not a standing
    approval, not a tool set to allow)."""
    channel = str(channel or "")
    return channel in PERSON_CHANNELS or channel.startswith("approval:")


def pinned(image: str) -> bool:
    """A base image pinned by digest (name@sha256:<64 hex>): only such an image can be confirmed once for all."""
    name, sep, digest = str(image or "").partition("@sha256:")
    return bool(name) and bool(sep) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _key(kind: str, item_id: str) -> str:
    return f"{kind}:{str(item_id).casefold()}"


class ConfirmedBuilds:
    """The builds of a target or recipe the person confirmed, per user."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / FILE_NAME

    def _load(self) -> Dict[str, dict]:
        try:
            if self.path.is_symlink():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        items = data.get("confirmed") if isinstance(data, dict) else None
        return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}

    def _save(self, items: Dict[str, dict]) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "confirmed": items}, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def get(self, kind: str, item_id: str) -> Optional[dict]:
        return self._load().get(_key(kind, item_id))

    def record(self, kind: str, item_id: str, *, installed_via: str = "", pack_sha256: str = "",
               base_images: Iterable[str] = (), channel: str = "") -> bool:
        """Records a confirmed build of a target or a recipe; False (nothing written) unless `channel` is a
        confirmation by the person in the framework's own window."""
        if kind not in ("target", "recipe") or not item_id or not person_confirmed(channel):
            return False
        images: List[str] = [i for i in dict.fromkeys(base_images) if pinned(i)][:MAX_IMAGES]
        with file_lock(self.state_dir / f".{FILE_NAME}.lock", timeout=10):
            items = self._load()
            key = _key(kind, item_id)
            old = items.get(key) or {}
            same = old.get("installed_via", "") == (installed_via or "") and \
                old.get("pack_sha256", "") == (pack_sha256 or "")
            if same:  # the same installed pack: keep the images confirmed earlier too
                images = list(dict.fromkeys(list(old.get("base_images") or []) + images))[-MAX_IMAGES:]
            items[key] = {"installed_via": installed_via or "", "pack_sha256": pack_sha256 or "",
                          "base_images": images, "channel": str(channel)[:80],
                          "confirmed": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if len(items) > MAX_ENTRIES:  # the oldest confirmations go first
                for old_key in sorted(items, key=lambda k: str(items[k].get("confirmed")))[:len(items) - MAX_ENTRIES]:
                    items.pop(old_key, None)
            self._save(items)
        return True

    def confirmed(self, kind: str, item_id: str, *, installed_via: str = "", pack_sha256: str = "") -> bool:
        """Whether the person confirmed a build of exactly this installed pack (same standing approval it was
        installed under, same pack sha256)."""
        entry = self.get(kind, item_id)
        return entry is not None and entry.get("installed_via", "") == (installed_via or "") and \
            entry.get("pack_sha256", "") == (pack_sha256 or "")

    def image_confirmed(self, target_id: str, image: str, *, installed_via: str = "", pack_sha256: str = "") -> bool:
        """Whether the person confirmed a build of this target with exactly this digest-pinned base image."""
        entry = self.get("target", target_id)
        return bool(entry) and pinned(image) and image in (entry.get("base_images") or []) and \
            entry.get("installed_via", "") == (installed_via or "") and \
            entry.get("pack_sha256", "") == (pack_sha256 or "")


__all__ = ["ConfirmedBuilds", "PERSON_CHANNELS", "person_confirmed", "pinned"]
