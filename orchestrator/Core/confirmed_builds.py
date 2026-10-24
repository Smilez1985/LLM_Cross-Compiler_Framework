#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Builds the person confirmed (MCP design 6.10, T9 round 3; delegation design 3.9)

A target or recipe pack an AI client installed without the person answering the question itself (under a standing
approval, `installed_via: grant:<id>`, or later a delegation, `delegation:<id>`) stays outside every build standing
approval until the person has confirmed one build of it: "install third-party build code" and "run its image build
with network" never happen together without a question. This store records that confirmation, so the rule works in
both directions: before the first confirmed build a build standing approval does not cover the pack, after it it does.

A confirmation counts only when the person answered the framework's own question (Ditto's GUI dialog or CLI prompt,
or a pending approval in the framework's window). An MCP form elicitation does not count: an accepted elicitation is
the client's statement, and an automated client may answer by itself (the same reason it can never create a standing
approval). A standing approval's own use does not count either, nor a delegated AI answer, nor a tool the person set
to "allow".

An entry names the pack as installed (its `installed_via` and pack sha256): a reinstall under another standing
approval or an updated pack needs a new confirmation. It also keeps the digest-pinned base images of the target's
Dockerfile at the time of the confirmed build: a build standing approval covers a base image outside the Docker
Official Images only when the person confirmed a build with exactly that pinned image before. A module an AI created
is bound to its content (`definition_sha256`): one recreated under the same name needs a new confirmation.

The file lives in the per-user folder (`confirmed_builds.json`, mode 0600), written under a lock by framework code
only, after a confirmed build was accepted; no tool writes it from its arguments. It is signed like the permission
file (orchestrator/tools/signing.py): a file edited by hand, damaged, unsigned or signed with another key holds no
confirmation, so the person is asked again. Each save raises its revision, and the newest one is kept in the OS keyring
(signing.RevisionAnchor, one entry per framework home), as for the permissions and the tokens: an older signed copy put
back over the file would bring back a confirmation a later one replaced (a module recreated with its earlier content,
a pack reinstalled in an earlier version), so it holds no confirmation either and the person is asked again.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from orchestrator.tools.signing import RevisionAnchor, SignatureError, sign, verify
from orchestrator.utils.file_lock import file_lock

FILE_NAME = "confirmed_builds.json"
KIND = "confirmed_builds"   # the signed file kind
MAX_ENTRIES = 2000
MAX_IMAGES = 16
# What can be confirmed: a target or recipe as installed (pack sha256) and a module an AI created (its content). An
# entry of the kind `artifact` (a model fetched from another computer, planned for the removed remote mode) is never
# written and never matched.
KINDS = ("target", "recipe", "module")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Confirmation channels where the person answered the framework's own question (registry: ctx.confirm_channel).
PERSON_CHANNELS = ("framework", "framework-gui", "framework-cli")
# The channels of calls that ran without the person answering their question: a standing approval and a delegation.
UNATTENDED_PREFIXES = ("grant:", "delegation:")


def person_confirmed(channel: Optional[str]) -> bool:
    """Whether a call was confirmed by the person in the framework's own window (not an elicitation, not a standing
    approval, not a delegation, not a tool set to allow)."""
    channel = str(channel or "")
    return channel in PERSON_CHANNELS or channel.startswith("approval:")


def unattended_parts(channel: Optional[str]) -> Tuple[str, str]:
    """("grant", "g_...") or ("delegation", "d_...") for a call that ran without the person answering its question,
    ("", "") for any other channel. The one place that reads these prefixes (a source test keeps it so)."""
    text = str(channel or "")
    for prefix in UNATTENDED_PREFIXES:
        if text.startswith(prefix):
            return prefix[:-1], text[len(prefix):]
    return "", ""


def unattended(channel: Optional[str]) -> bool:
    """Whether a call ran unattended: under a standing approval (`grant:<id>`) or a delegation (`delegation:<id>`).
    Such a call keeps the larger disk reserve, marks a pack it installs, and never counts as the person's
    confirmation."""
    return bool(unattended_parts(channel)[0])


def pinned(image: str) -> bool:
    """A base image pinned by digest (name@sha256:<64 hex>): only such an image can be confirmed once for all."""
    name, sep, digest = str(image or "").partition("@sha256:")
    return bool(name) and bool(sep) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _key(kind: str, item_id: str) -> str:
    return f"{kind}:{str(item_id).casefold()}"


class ConfirmedBuilds:
    """The builds of a target or recipe (and the AI-made modules and fetched models) the person confirmed, per user.
    `key`: the audit key (OS keyring) the file is signed with, None without a keyring (an unkeyed signature, refused
    while `refuse_unkeyed()` says the audit log is kept with a key). `anchor`: where the newest saved revision is kept
    outside the per-user folder (the OS keyring); with it an older signed copy put back holds no confirmation."""

    def __init__(self, state_dir: Path, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None, anchor: Optional[RevisionAnchor] = None):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / FILE_NAME
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.anchor = anchor
        self.problem: Optional[str] = None  # set when the stored file exists but could not be used
        self._revision = 0                  # the revision the file named at the last read (0: none, or unreadable)

    @classmethod
    def for_framework(cls, framework, state_dir: Path) -> "ConfirmedBuilds":
        """The store with the signing key of the framework's permission file (the same key and the same refusal of
        unkeyed signatures), or unkeyed when the framework has no tool registry. Its revision is kept in the keyring
        when the permission file's is (a framework with a keyring), in the entry of this framework home."""
        policy_store = getattr(getattr(framework, "tool_registry", None), "policy_store", None)
        key = getattr(policy_store, "key", None)
        anchor = None
        if isinstance(key, bytes) and isinstance(getattr(policy_store, "anchor", None), RevisionAnchor):
            from orchestrator.Core.vault_key import revision_anchor
            anchor = revision_anchor(KIND, state_dir)
        return cls(state_dir, key=key if isinstance(key, bytes) else None,
                   refuse_unkeyed=getattr(policy_store, "refuse_unkeyed", None), anchor=anchor)

    def _anchored(self) -> int:
        """The newest revision the framework saved of this file (kept in the keyring), 0 without an anchor."""
        return self.anchor.current(self.path) if self.anchor is not None else 0

    def _load(self) -> Dict[str, dict]:
        # The anchor before the file, as for the token store: a confirmation saved meanwhile by another process is then
        # newer than the anchor read, never older.
        anchored = self._anchored()
        self._revision = 0
        try:
            if self.path.is_symlink():
                raise ValueError("it is a symbolic link")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            data = verify(KIND, raw, self.key, self.refuse_unkeyed)
        except FileNotFoundError:
            self.problem = None
            return {}
        except (OSError, ValueError, RecursionError, SignatureError) as e:
            first = (str(e).splitlines() or [type(e).__name__])[0][:200]
            self.problem = f"{FILE_NAME} holds no confirmation that counts ({first}); the person is asked again."
            return {}
        revision = data.get("revision", 0)
        self._revision = revision if isinstance(revision, int) and not isinstance(revision, bool) and \
            revision > 0 else 0
        if self._revision < anchored:
            # Genuinely signed, but older than what the framework saved last: put back to bring back a confirmation a
            # later one replaced.
            self.problem = (f"{FILE_NAME} holds no confirmation that counts (it is an older copy: revision "
                            f"{self._revision}; the framework saved revision {anchored} last); the person is asked "
                            "again.")
            return {}
        self.problem = None
        if not anchored and self.anchor is not None:  # no revision kept for this home yet: from now on this one
            self.anchor.adopt(self.path, self._revision)
        items = data.get("confirmed")
        return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}

    def _save(self, items: Dict[str, dict]) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        revision = max(self._revision, self._anchored()) + 1
        data = sign(KIND, {"version": 2, "revision": revision, "confirmed": items}, self.key)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
        self._revision = revision
        if self.anchor is not None:  # after the file: a reader in between sees a newer file, never an older one
            self.anchor.saved(self.path, revision)

    def get(self, kind: str, item_id: str) -> Optional[dict]:
        return self._load().get(_key(kind, item_id))

    def record(self, kind: str, item_id: str, *, installed_via: str = "", pack_sha256: str = "",
               base_images: Iterable[str] = (), channel: str = "", definition_sha256: str = "") -> bool:
        """Records a confirmed build of a target, a recipe or an AI-made module (bound to `definition_sha256`); False
        (nothing written) unless `channel` is a confirmation by the person in the framework's own window, or the
        content it binds to is missing."""
        if kind not in KINDS or not item_id or not person_confirmed(channel):
            return False
        if kind == "module" and not _SHA256.fullmatch(definition_sha256 or ""):
            return False
        images: List[str] = [i for i in dict.fromkeys(base_images) if pinned(i)][:MAX_IMAGES]
        with file_lock(self.state_dir / f".{FILE_NAME}.lock", timeout=10):
            items = self._load()  # a file that holds nothing that counts is replaced by this confirmation
            key = _key(kind, item_id)
            old = items.get(key) or {}
            same = all(old.get(f, "") == (v or "") for f, v in (("installed_via", installed_via),
                                                                  ("pack_sha256", pack_sha256),
                                                                  ("definition_sha256", definition_sha256)))
            if same:  # the same installed pack: keep the images confirmed earlier too
                images = list(dict.fromkeys(list(old.get("base_images") or []) + images))[-MAX_IMAGES:]
            items[key] = {"installed_via": installed_via or "", "pack_sha256": pack_sha256 or "",
                          "definition_sha256": definition_sha256 or "",
                          "base_images": images, "channel": str(channel)[:80],
                          "confirmed": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if len(items) > MAX_ENTRIES:  # the oldest confirmations go first
                for old_key in sorted(items, key=lambda k: str(items[k].get("confirmed")))[:len(items) - MAX_ENTRIES]:
                    items.pop(old_key, None)
            self._save(items)
        return True

    def confirmed(self, kind: str, item_id: str, *, installed_via: str = "", pack_sha256: str = "",
                  definition_sha256: str = "") -> bool:
        """Whether the person confirmed a build of exactly this installed pack (same standing approval it was
        installed under, same pack sha256) or of exactly this module content."""
        entry = self.get(kind, item_id) if kind in KINDS else None
        if entry is None:
            return False
        if kind == "module" and not definition_sha256:
            return False  # a module is only ever confirmed for its content
        return all(entry.get(f, "") == (v or "") for f, v in (("installed_via", installed_via),
                                                               ("pack_sha256", pack_sha256),
                                                               ("definition_sha256", definition_sha256)))

    def image_confirmed(self, target_id: str, image: str, *, installed_via: str = "", pack_sha256: str = "") -> bool:
        """Whether the person confirmed a build of this target with exactly this digest-pinned base image."""
        entry = self.get("target", target_id)
        return bool(entry) and pinned(image) and image in (entry.get("base_images") or []) and \
            entry.get("installed_via", "") == (installed_via or "") and \
            entry.get("pack_sha256", "") == (pack_sha256 or "")


__all__ = ["ConfirmedBuilds", "KINDS", "PERSON_CHANNELS", "person_confirmed", "pinned", "unattended",
           "unattended_parts"]
