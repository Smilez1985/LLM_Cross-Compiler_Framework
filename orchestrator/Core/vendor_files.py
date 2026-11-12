#!/usr/bin/env python3
"""
The guided fetch of vendor files (docs/DESIGN.md, Targets, hardware and templates: the guided fetch of vendor files,
and the guided install of vendor toolchains): a file a format of a target's template variables names under
`vendor_files` (schemas/target_template.VendorFile) may not be passed on, so llmcf never ships it and never puts it
into a module, a pack, a package, a preload or a bundle. The person fetches it under their own licence acceptance:

- question(): the licence text (the target folder's licences/NAME.txt, else read from the licence address) with its
  sha256, the vendor's download page and what the file is for. No text, nothing to accept.
- accept(): the person's own yes, only from the person's own windows (actor gui or cli), never an AI: recorded in the
  person's state folder (vendor-files/acceptances.json: the file, the target, the channel, the account, the time and
  the sha256 of the licence text shown).
- add_file(): the file the person downloaded, checked by its sha256 (the published one; without one the person
  confirms the file's own), kept in the person's vendor store (vendor-files/<sha256>), never anywhere else.
- download(): only for a file whose variables mark the address as the file itself (`direct`, pinned by sha256 and
  size), after the acceptance; the pinned HTTPS download (utils/download.py).
- stage(): a build puts each vendor file with a `dest` from the store into the build context's image files
  (sources/image-files/DEST), checked again by its sha256; a missing one stops the build with the fetch as its next
  step.
- status(): what a target names and whether each file is in the store and accepted (also the AI's read-only facts).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from orchestrator.utils.next_steps import NextStep, WithSteps

STORE = "vendor-files"
ACCEPTANCES = "acceptances.json"
INDEX = "index.json"
ACTORS = ("gui", "cli")   # the person's own windows; an AI never accepts a licence
LICENCE_MAX = 512 * 1024  # the most of a licence text read from its address
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VendorProblem(WithSteps, ValueError):
    """A vendor file that cannot be accepted, taken or staged; `code`: needs_person, mismatch, not_found, invalid_value
    or failed; `next` the plain next step."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _account() -> str:
    try:
        return getpass.getuser()[:64]
    except Exception:  # noqa: BLE001 - no account name on this system: the channel says who
        return ""


# --- what a target names -----------------------------------------------------------------------------------------

@dataclass
class VendorEntry:
    """A vendor file of a target: the target's id, the format that names it, the file's declared values."""
    target: str
    format_id: str
    file: Any   # schemas/target_template.VendorFile

    @property
    def key(self) -> str:
        return f"{self.target}/{self.file.name}"


def _meta(folder: Path) -> Dict[str, Any]:
    try:
        meta = yaml.safe_load((Path(folder) / "target.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return meta if isinstance(meta, dict) else {}


def target_id(folder: Path) -> str:
    meta = (_meta(folder).get("metadata") or {}) if isinstance(_meta(folder).get("metadata"), dict) else {}
    return str(meta.get("id") or Path(folder).name)


def entries(folder: Path) -> List[VendorEntry]:
    """Every vendor file the formats of a target's template variables name, in their order (one name once)."""
    from orchestrator.schemas.target_template import VendorFile
    record = _meta(folder).get("template_variables")
    formats = record.get("formats") if isinstance(record, dict) else None
    tid = target_id(folder)
    out: List[VendorEntry] = []
    seen = set()
    for fmt in formats or []:
        if not isinstance(fmt, dict):
            continue
        for item in fmt.get("vendor_files") or []:
            try:
                vf = VendorFile.model_validate(item)
            except ValueError:
                continue   # the folder check names it (module_generator.template_variable_problems)
            if vf.name in seen:
                continue
            seen.add(vf.name)
            out.append(VendorEntry(tid, str(fmt.get("id") or ""), vf))
    return out


def find(folder: Path, name: str) -> VendorEntry:
    for entry in entries(folder):
        if entry.file.name == name:
            return entry
    names = ", ".join(e.file.name for e in entries(folder)) or "none"
    raise VendorProblem(f"{Path(folder).name} names no vendor file {name!r} (it names: {names}).", "not_found")


# --- the person's store ------------------------------------------------------------------------------------------

def store_dir(state_dir: Any) -> Path:
    return Path(state_dir) / STORE


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    from orchestrator.utils.atomic import write_private
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(data, indent=2, sort_keys=True))


def acceptances(state_dir: Any) -> Dict[str, Any]:
    """The recorded acceptances by target/name (the newest one each)."""
    return _read_json(store_dir(state_dir) / ACCEPTANCES)


def acceptance(state_dir: Any, entry: VendorEntry, licence_sha256: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The person's recorded acceptance of the entry's licence (of exactly that text when `licence_sha256` is given);
    None when there is none (or the text changed since)."""
    record = acceptances(state_dir).get(entry.key)
    if not isinstance(record, dict) or record.get("licence_url") != entry.file.licence_url:
        return None
    if licence_sha256 and record.get("licence_sha256") != licence_sha256:
        return None
    return record


def stored(state_dir: Any, entry: VendorEntry) -> Optional[Path]:
    """The entry's file in the store (by its published sha256, else the one the person confirmed), None when absent."""
    sha = entry.file.sha256 or str((_read_json(store_dir(state_dir) / INDEX).get(entry.key) or {}).get("sha256") or "")
    if not SHA256.fullmatch(sha):
        return None
    path = store_dir(state_dir) / sha
    return path if path.is_file() else None


# --- the licence -------------------------------------------------------------------------------------------------

@dataclass
class LicenceQuestion:
    """What the person reads before saying yes: the file, what it is for, the download page, the licence's address
    and its whole text with the text's sha256."""
    entry: VendorEntry
    text: str
    text_sha256: str

    def facts(self) -> List[str]:
        vf = self.entry.file
        lines = [f"Vendor file {vf.name} of target {self.entry.target} (format {self.entry.format_id})"]
        if vf.purpose:
            lines.append(f"For: {vf.purpose}")
        lines.append(("The vendor's file: " if vf.direct else "The vendor's download page: ") + vf.url)
        lines.append(f"Licence: {vf.licence_url}")
        lines.append(f"Published sha256: {vf.sha256}" if vf.sha256 else
                     "The vendor publishes no sha256: you confirm the file's own after downloading it.")
        lines.append(f"In the build image at {vf.dest}" if vf.dest else "Only for the board (you install it there).")
        lines.append("The framework never passes this file on: it stays in your own vendor store, never in a module, pack, "
                     "package, preload or bundle.")
        return lines


def licence_text(folder: Path, entry: VendorEntry, fetch_text: Optional[Callable[[str], str]] = None) -> str:
    """The licence text: the target folder's licences/NAME.txt when the variables name one, else read from the licence
    address with `fetch_text` (default_fetch_text; None: not read). VendorProblem when there is no text to show."""
    vf = entry.file
    if vf.licence_file:
        path = Path(folder) / vf.licence_file
        try:
            if path.is_file() and not path.is_symlink():
                text = path.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    return text
        except OSError:
            pass
    if fetch_text is not None:
        try:
            text = fetch_text(vf.licence_url)
        except Exception as e:  # noqa: BLE001 - every failure means the same: no text to show
            raise VendorProblem(f"The licence text of {vf.name} could not be read from {vf.licence_url}: {e}",
                                "failed", "Read it on the vendor's page; without the text shown here nothing can "
                                          "be accepted.") from None
        if text and text.strip():
            return text
    raise VendorProblem(f"The licence text of {vf.name} is not in the target folder and was not read from "
                        f"{vf.licence_url}; without the text there is nothing to accept.", "needs_person",
                        "Connect to the network (or switch offline mode off) and fetch it again.")


def default_fetch_text(url: str) -> str:
    """A licence text from its https address (at most LICENCE_MAX bytes; a web page read as text the way Deep Ingest
    reads one)."""
    from orchestrator.utils.download import PinnedDownloader
    data = PinnedDownloader().get_bytes(url, LICENCE_MAX, name="the licence text")
    text = data.decode("utf-8", errors="replace")
    from orchestrator.rag.html_text import is_html_document, page_markdown
    if is_html_document(text):
        _title, text, _removed = page_markdown(text)
    return text


def question(folder: Path, name: str, fetch_text: Optional[Callable[[str], str]] = None) -> LicenceQuestion:
    entry = find(folder, name)
    text = licence_text(folder, entry, fetch_text)
    return LicenceQuestion(entry, text, _sha256_bytes(text.encode("utf-8")))


def _person(actor: str, what: str) -> None:
    if actor not in ACTORS:
        raise PermissionError(f"Only the person {what}, in the GUI or at a terminal; an AI never accepts a licence or "
                              "fetches a file that may not be passed on.")


def accept(state_dir: Any, asked: LicenceQuestion, *, actor: str, accepted: bool) -> Dict[str, Any]:
    """Records the person's own yes to the licence text they were shown (actor gui or cli only). VendorProblem when
    the answer was no."""
    _person(actor, "accepts a vendor's licence")
    if not accepted:
        raise VendorProblem(f"The licence of {asked.entry.file.name} was not accepted; nothing was fetched.",
                            "needs_person")
    record = {"target": asked.entry.target, "name": asked.entry.file.name, "format": asked.entry.format_id,
              "licence_url": asked.entry.file.licence_url, "licence_sha256": asked.text_sha256, "channel": actor,
              "account": _account(), "accepted": _now()}
    data = acceptances(state_dir)
    data[asked.entry.key] = record
    _write_json(store_dir(state_dir) / ACCEPTANCES, data)
    return record


# --- the file ----------------------------------------------------------------------------------------------------

@dataclass
class StoredFile:
    name: str
    sha256: str
    size: int
    path: Path
    lines: List[str] = field(default_factory=list)


def _needs_acceptance(state_dir: Any, entry: VendorEntry) -> None:
    if acceptance(state_dir, entry) is None:
        raise VendorProblem(f"The licence of {entry.file.name} is not accepted yet; its text is shown first and your "
                            "own yes accepts it.", "needs_person").with_steps(
            NextStep("build.vendor_fetch", {"target": entry.target, "name": entry.file.name}))


def _keep(state_dir: Any, entry: VendorEntry, part: Path, digest: str, size: int, actor: str,
          confirm_sha256: Optional[Callable[[str], bool]]) -> StoredFile:
    """The checked file `part` (in the store folder) under its sha256, or removed when it does not match."""
    from orchestrator.utils.atomic import remove_file
    vf = entry.file
    problem = ""
    if vf.sha256 and digest != vf.sha256:
        problem = (f"{vf.name}: its sha256 is {digest}, not the {vf.sha256} the target names; the file was not "
                   "kept. Download it again from the vendor's page.")
    elif vf.size and size != vf.size:
        problem = f"{vf.name}: it has {size} bytes, not the {vf.size} the target names; the file was not kept."
    elif not vf.sha256 and not (confirm_sha256 is not None and confirm_sha256(digest)):
        problem = (f"{vf.name}: the vendor publishes no sha256, and you did not confirm the file's own ({digest}); "
                   "the file was not kept.")
    if problem:
        remove_file(part)
        raise VendorProblem(problem, "mismatch")
    dest = store_dir(state_dir) / digest
    os.replace(part, dest)
    if os.name == "posix":
        os.chmod(dest, 0o600)
    index = _read_json(store_dir(state_dir) / INDEX)
    index[entry.key] = {"sha256": digest, "size": size, "added": _now(), "channel": actor,
                        "confirmed_by_person": not bool(vf.sha256)}
    _write_json(store_dir(state_dir) / INDEX, index)
    return StoredFile(vf.name, digest, size, dest, [f"{vf.name} is in your vendor store (sha256 {digest})."])


def add_file(state_dir: Any, folder: Path, name: str, path: Path, *, actor: str,
             confirm_sha256: Optional[Callable[[str], bool]] = None) -> StoredFile:
    """Takes the file the person downloaded into their vendor store, after the licence was accepted: copied, its
    sha256 checked (a mismatch is refused and nothing is kept)."""
    _person(actor, "gives a vendor file")
    entry = find(folder, name)
    _needs_acceptance(state_dir, entry)
    source = Path(path)
    if not source.is_file():
        raise VendorProblem(f"{source.name} is not a file.", "invalid_value")
    folder_store = store_dir(state_dir)
    folder_store.mkdir(parents=True, exist_ok=True)
    import secrets
    from orchestrator.utils.helpers import copy_hashed
    part = folder_store / f".part-{secrets.token_hex(8)}"
    try:
        digest = copy_hashed(source, part, follow_links=True, mode=0o600)
    except BaseException:
        from orchestrator.utils.atomic import remove_file
        remove_file(part)
        raise
    size = part.stat().st_size
    return _keep(state_dir, entry, part, digest, size, actor, confirm_sha256)


def download(state_dir: Any, folder: Path, name: str, *, actor: str, downloader: Any = None,
             progress: Any = None) -> StoredFile:
    """llmcf downloads the file itself: only when the variables mark its address as the file (`direct`, pinned by
    sha256 and size) and after the licence was accepted; otherwise the person downloads it on the vendor's page."""
    _person(actor, "lets the framework download a vendor file")
    entry = find(folder, name)
    vf = entry.file
    if not vf.direct:
        raise VendorProblem(f"{vf.name} is behind the vendor's page ({vf.url}): download it there yourself and "
                            "give the file.", "needs_person")
    _needs_acceptance(state_dir, entry)
    from orchestrator.utils.download import DownloadError, PinnedDownloader
    folder_store = store_dir(state_dir)
    folder_store.mkdir(parents=True, exist_ok=True)
    part = folder_store / f".download-{vf.sha256}"
    try:
        (downloader or PinnedDownloader()).fetch(vf.url, part, vf.size, vf.sha256, progress=progress)
    except DownloadError as e:
        raise VendorProblem(f"{vf.name} could not be downloaded: {e}", "failed") from None
    return _keep(state_dir, entry, part, vf.sha256, part.stat().st_size, actor, None)


# --- the build ---------------------------------------------------------------------------------------------------

def status(state_dir: Optional[Any], folder: Path) -> List[Dict[str, Any]]:
    """Each vendor file of a target with whether it is accepted and in the store (read-only facts, also for AIs)."""
    out = []
    for entry in entries(folder):
        vf = entry.file
        record = acceptance(state_dir, entry) if state_dir is not None else None
        path = stored(state_dir, entry) if state_dir is not None else None
        out.append({"name": vf.name, "format": entry.format_id, "purpose": vf.purpose, "url": vf.url,
                    "licence_url": vf.licence_url, "sha256_published": bool(vf.sha256),
                    "for": "build image" if vf.dest else "board", "dest": vf.dest or "", "direct": vf.direct,
                    "accepted": record.get("accepted") if record else "", "in_store": path is not None})
    return out


def stage(state_dir: Optional[Any], folder: Path, context: Path, log: Optional[Callable[[str], None]] = None) -> int:
    """Puts each vendor file with a `dest` from the person's store into the build context (sources/image-files/DEST,
    which the target's Dockerfile copies into the image), its sha256 checked again. VendorProblem naming the fetch
    when one is missing or changed; returns how many were staged."""
    needed = [e for e in entries(folder) if e.file.dest]
    if not needed:
        return 0
    missing = []
    count = 0
    for entry in needed:
        path = stored(state_dir, entry) if state_dir is not None else None
        if path is None or acceptance(state_dir, entry) is None:
            missing.append(entry)
            continue
        from orchestrator.utils.helpers import sha256_file
        if sha256_file(path) != path.name:
            raise VendorProblem(f"{entry.file.name} in your vendor store no longer matches its sha256; give the "
                                "file again.", "mismatch").with_steps(
                NextStep("build.vendor_fetch", {"target": entry.target, "name": entry.file.name}))
        dest = Path(context) / "sources" / "image-files" / entry.file.dest.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        os.chmod(dest, 0o755 if entry.file.executable else 0o644)  # nosec B103 - a vendor program, no secret
        count += 1
        if log:
            log(f"Vendor file {entry.file.name} staged from your vendor store into the build image at "
                f"{entry.file.dest} (never passed on).")
    if missing:
        first = missing[0]
        names = ", ".join(e.file.name for e in missing)
        raise VendorProblem(f"The build image of {first.target} needs vendor files that are not in your vendor store "
                            f"yet: {names}. They may not be passed on, so you fetch them yourself under their "
                            "licence; nothing was built.", "needs_person",
                            "Fetch each of them under its licence (the next steps say where).").with_steps(
            *[NextStep("build.vendor_fetch", {"target": e.target, "name": e.file.name}) for e in missing])
    return count
