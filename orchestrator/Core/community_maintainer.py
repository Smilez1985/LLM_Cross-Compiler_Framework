#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the maintainer actions of the community repository (the person only)

The maintainer's work as a few clicks instead of a manual (decision 2026-10-22). One implementation for
Tools > Open Community Hub > Maintainer (gui/community_maintainer.py), `llmcf zoo maintainer key|review|revoke`
(cli.py) and scripts/zoo_sign_index.py (a thin wrapper over the first three functions here):

- create_signing_key: once, the Ed25519 index signing key in the OS keyring (service lcf-community-index; a name that
  exists is refused, a key is never replaced) and its public entry for configs/community_keys.yml (KeyEntry: shown,
  and saved as a text file the person chooses).
- make_entry / sign_index_file: an index entry for a checked pack, and index.json.sig for an index file (the script's
  `entry` and `sign`).
- Maintainer.contributions / review / publish: the open pull requests of community_repo that add packs under
  contributions/<kind>/*.zip (and packs merged there but not published yet); for one of them its packs fetched from the
  pull request's head, checked with `zoo verify` (CommunityManager.verify_file) and every file shown; after the
  person's "Yes" (asked by the CLI and the GUI, "No" the default): the contribution merged (only with the required
  check "validate" passed and its head unchanged since the review), the local clone in the per-user folder updated,
  the pack moved to packs/<kind>/, its index entry with the raw URL added, the index version and time raised, the
  index signed with the keyring key, committed on a new branch, pushed, a pull request opened, the required check
  waited for and the pull request merged.
- Maintainer.revoke: a revocation added to the signed index on the same route.

main is protected (merges only through pull requests with the required check): nothing here ever pushes to main.
Every change goes on a new branch lcf-publish-*/lcf-revoke-* (pushed by its explicit name), and each merge pins the
commit that was checked (--match-head-commit). Before anything is signed, the index on main must verify with the
maintainer's key or a trusted key of this framework: nothing unsigned is ever signed over. Every step is reported
(progress: a text key of utils/l10n_maintainer.py with its values) and written to the audit log (tool zoo_maintainer;
the operation is refused when its first row cannot be written), and the operation can be cancelled before each step
up to the last merge. A failure says what happened so far (zoo.maint.state.*) and the next step.

GitHub: gh and git with the person's own stored credentials. This module never reads, stores or shows a token,
passes no environment to them, never reads stdin through them (stdin=DEVNULL) and shows their output only through
community_manager.tool_error_detail (credentials in addresses removed, secrets redacted). Without gh or git, without a
login or offline, the refusal names the reason and the next step.

The person only: the actor must be "cli" or "gui", and no module an AI drives imports this one (the MCP server, the
tools, the provider layer and Ditto; tests/test_zoo_maintainer.py guards it).
"""

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from orchestrator.Core.community_manager import (KINDS, REPO_RE, CommunityIndex, CommunityManager, IndexEntry,
                                                 Revocation, tool_error_detail)
from orchestrator.Core.community_pack import (MANIFEST_NAME, RESERVED_NAMES, SIGNATURE_NAME, PackError, belongs_text,
                                              key_id, list_files, load_trusted_keys, read_pack, sign_index, utc_now,
                                              verify_index)
from orchestrator.utils import l10n_maintainer
from orchestrator.utils.helpers import ensure_directory

SERVICE = "lcf-community-index"   # the keyring service of the index signing key (the script's since its first version)
REQUIRED_CHECK = "validate"       # the job of the community repository's check workflow; main requires it
MAX_CONTRIBUTION_BYTES = 5_000_000   # the check workflow's limit for a pack
MAX_SHOWN_BYTES = 16 * 1024 * 1024   # a zip member outside the file list (pack.json, pack.sig, ...) the review shows
KEY_VALID_DAYS = 730
KEY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PACK_PATH = re.compile(r"^contributions/(target|recipe|knowledge)/([A-Za-z0-9][A-Za-z0-9._+-]{0,150}\.zip)$")
BRANCH = re.compile(r"^lcf-(publish|revoke)-[A-Za-z0-9._-]{1,90}$")
PERSON_ACTORS = ("cli", "gui")
CHANGE_KINDS = ("A", "M", "D", "T")   # git's added, modified, deleted, type changed (zoo.maint.change.*; X: other)
WAIT_TIMEOUT = 30 * 60
POLL_SECONDS = 10
REPORT_EVERY = 30
_OFFLINE = re.compile(r"could not resolve|resolve host|error connecting|connection (?:refused|reset|timed out)|"
                      r"network is unreachable|no route to host|timed? ?out|temporary failure|dial tcp|"
                      r"i/o timeout|offline", re.IGNORECASE)

Progress = Callable[[str, Dict[str, Any]], None]


def say(text_key: str, lang: str = "en", /, audience: str = "cli", **values: Any) -> str:
    """A text of utils/l10n_maintainer.py (Qt-free, so the CLI and the core use it without the GUI); a framework step
    it names reads as `audience` reads it (l10n_maintainer.STEP_PLACES: the command at a terminal, the place in the
    window)."""
    return l10n_maintainer.text(text_key, lang, audience, **values)


class MaintainerError(PackError):
    """A refusal with a plain reason (zoo.maint.err.<code>), what happened so far (zoo.maint.state.* keys: what
    happened to the contribution, then what happened to the publication's own branch and pull request) and the next
    step (zoo.maint.next.<code>). str() is English for the command line; text(lang) is the GUI's language, with the
    places the window shows (text(lang, "cli") names the commands)."""

    def __init__(self, code: str, /, **values: Any):
        self.code, self.values, self.states = code, dict(values), []
        self.unanswered = False   # a tool that did not answer in time: what it was doing may have happened
        super().__init__(self.text("en", "cli"))

    @property
    def state(self) -> str:
        """The last state key ("" when none is set)."""
        return self.states[-1][0] if self.states else ""

    def with_state(self, state_key: str, /, **values: Any) -> "MaintainerError":
        return self.with_states([(state_key, values)] if state_key else [])

    def with_states(self, states: Sequence[Tuple[str, Dict[str, Any]]]) -> "MaintainerError":
        if states and not self.states:
            self.states = [(key, dict(values)) for key, values in states if key]
            self.args = (self.text("en", "cli"),)
        return self

    def text(self, lang: str = "en", audience: str = "gui") -> str:
        lines = [say(f"zoo.maint.err.{self.code}", lang, audience, **self.values)]
        merged: Dict[str, Any] = {}
        for key, values in self.states:
            lines.append(say(key, lang, audience, **{**values, **self.values}))
            merged.update(values)
        merged.update(self.values)
        if f"zoo.maint.next.{self.code}" in l10n_maintainer.TEXTS["en"]:
            lines.append(say("zoo.maint.next_step", lang, audience,
                             step=say(f"zoo.maint.next.{self.code}", lang, audience, **merged)))
        return "\n".join(lines)


class Cancelled(MaintainerError):
    def __init__(self, **values: Any):
        super().__init__("cancelled", **values)


def _person(actor: str) -> str:
    if actor not in PERSON_ACTORS:
        raise PermissionError("The maintainer actions are the person's: in the GUI or at a terminal, never an AI.")
    return actor


# --- the index signing key (once) ------------------------------------------------------------------------------------

def _keyring(keyring: Any = None) -> Any:
    if keyring is not None:
        return keyring
    try:
        import keyring as module
    except ImportError as e:
        raise MaintainerError("keyring", detail=str(e)) from None
    return module


def _keyring_call(fn: Callable, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - every keyring backend has its own errors
        raise MaintainerError("keyring", detail=f"{type(e).__name__}: {e}"[:300]) from None


@dataclass(frozen=True)
class KeyEntry:
    """The public entry of an index signing key for configs/community_keys.yml (no secret in it)."""
    key_id: str
    pubkey_b64: str
    valid_until: date
    name: str

    def text(self) -> str:
        return ("# Add to configs/community_keys.yml (keys:) and ship it with a release:\n"
                f"  - key_id: {self.key_id}\n"
                f"    pubkey_b64: {self.pubkey_b64}\n"
                f"    valid_until: {self.valid_until}\n"
                f"    name: {self.name}\n")

    def save(self, path: Path, *, overwrite: bool = False) -> Path:
        """Writes text() to the file the person chose; an existing file is replaced only when the person said so in
        a file dialog that asked (overwrite)."""
        path = Path(path)
        if path.exists() and not overwrite:
            raise MaintainerError("file_exists", path=str(path))
        path.write_text(self.text(), encoding="utf-8")
        return path


def check_key_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise MaintainerError("no_key_name")
    if not KEY_NAME.fullmatch(name):
        raise MaintainerError("bad_key_name", name=name)
    return name


def create_signing_key(name: str, *, keyring: Any = None, today: Optional[date] = None,
                       audit: Any = None, actor: str = "cli") -> KeyEntry:
    """A new Ed25519 index signing key in the OS keyring under `name` (service lcf-community-index) and its public
    entry, valid for two years. A name that exists already is refused: a key is never replaced."""
    _person(actor)
    name = check_key_name(name)
    kr = _keyring(keyring)
    if _keyring_call(kr.get_password, SERVICE, name):
        raise MaintainerError("key_exists", name=name)
    key = Ed25519PrivateKey.generate()
    _keyring_call(kr.set_password, SERVICE, name, base64.b64encode(key.private_bytes_raw()).decode("ascii"))
    public = key.public_key()
    entry = KeyEntry(key_id(public), base64.b64encode(public.public_bytes_raw()).decode("ascii"),
                     (today or date.today()) + timedelta(days=KEY_VALID_DAYS), name)
    if audit is not None and callable(getattr(audit, "record", None)):
        try:   # the public key id only; the key itself never leaves the keyring
            audit.record(actor, "zoo_maintainer", "framework", "person", "ok", approved=True,
                         arguments={"step": "key", "name": name, "key_id": entry.key_id},
                         detail=say("zoo.maint.op.key", name=name))
        except Exception:  # noqa: BLE001 - the key exists; a missing audit row does not undo it
            pass
    return entry


def load_signing_key(name: str, *, keyring: Any = None) -> Ed25519PrivateKey:
    name = check_key_name(name)
    stored = _keyring_call(_keyring(keyring).get_password, SERVICE, name)
    if not stored:
        raise MaintainerError("no_key", name=name)
    try:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(stored))
    except ValueError as e:
        raise MaintainerError("keyring", detail=f"the entry {name} is not an Ed25519 key ({e})") from None


# --- index entries and signatures ------------------------------------------------------------------------------------

def entry_for(manifest, data: bytes, url: str, *, trust: str = "community", title: str = "",
              description: str = "") -> IndexEntry:
    """The index entry of a checked pack: its manifest's facts, the size and sha256 of exactly these bytes and the
    address it is published at."""
    return IndexEntry(kind=manifest.kind, id=manifest.id, version=manifest.version, title=title or manifest.id,
                      description=description, author=manifest.author, license=manifest.license, trust=trust, url=url,
                      size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      topic=getattr(manifest, "topic", None), target=getattr(manifest, "target", None))


def make_entry(pack: Path, url: str, *, trust: str = "community", title: str = "",
               description: str = "") -> IndexEntry:
    """The index entry of a pack file, after it was checked as `zoo verify` checks it (format, hashes, validators,
    privacy scrub; the script's `entry`)."""
    pack = Path(pack)
    manifest, problems = CommunityManager(None).verify_file(pack)
    if problems:
        raise MaintainerError("rejected", problems="\n".join(f"- {pack.name}: {p}" for p in problems))
    return entry_for(manifest, pack.read_bytes(), url, trust=trust, title=title, description=description)


def index_bytes(index: CommunityIndex) -> bytes:
    """index.json as it is published: the signature covers exactly these bytes."""
    return (json.dumps(index.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False)
            + "\n").encode("utf-8")


def signed(data: bytes, key: Ed25519PrivateKey) -> bytes:
    """index.json.sig for the index bytes, checked against the key before anything is written."""
    signature = sign_index(data, key)
    verify_index(data, signature, [key.public_key()])
    return signature.model_dump_json().encode("utf-8")


def next_index(previous: Optional[CommunityIndex], *, add: Sequence[IndexEntry] = (),
               revoke: Sequence[Revocation] = ()) -> CommunityIndex:
    """The next index: the previous one's entries and revocations plus the new ones, its version one higher (the
    first index has version 1) and the current UTC time. An entry listed already (kind, id and version, or the same
    file) and a revocation that exists already are refused."""
    entries = list(previous.entries) if previous else []
    revoked = list(previous.revoked) if previous else []
    for entry in add:
        if any(e.sha256 == entry.sha256 or (e.kind, e.id.casefold(), e.version)
               == (entry.kind, entry.id.casefold(), entry.version) for e in entries):
            raise MaintainerError("entry_exists", what=f"{entry.kind} {entry.id} {entry.version}")
        entries.append(entry)
    for revocation in revoke:
        if any(_scope(r) == _scope(revocation) for r in revoked):   # the same scope, whatever its reason
            raise MaintainerError("revoke_exists", what=describe_revocation(revocation))
        revoked.append(revocation)
    return CommunityIndex(version=previous.version + 1 if previous else 1, generated=utc_now(), entries=entries,
                          revoked=revoked)


def _scope(r: Revocation) -> Tuple[str, str, Optional[str], Optional[str]]:
    return r.kind, r.id.casefold(), r.version, r.sha256


def revocation(kind: str, pack_id: str, *, reason: str, version: Optional[str] = None,
               sha256: Optional[str] = None) -> Revocation:
    """A revocation by sha256 (exactly that file), or by kind and id with one version or every version."""
    if kind not in KINDS:
        raise MaintainerError("bad_revocation", detail=f"unknown kind {kind!r}")
    try:
        return Revocation(kind=kind, id=pack_id, version=version or None, sha256=sha256 or None,
                          reason=str(reason or "").strip())
    except ValidationError as e:
        raise MaintainerError("bad_revocation", detail="; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}"
                                                                 for x in e.errors())) from None


def describe_revocation(r: Revocation) -> str:
    if r.sha256:
        return f"{r.kind} {r.id} (file {r.sha256[:12]})"
    return f"{r.kind} {r.id} {r.version}" if r.version else f"{r.kind} {r.id} (every version)"


def sign_index_file(index_path: Path, name: str, previous: Optional[Path] = None, *,
                    keyring: Any = None) -> Tuple[CommunityIndex, str, Path]:
    """Checks index.json against the schema (with `previous` its version must be higher) and writes index.json.sig
    next to it with the keyring key `name`. Returns the index, the key id and the signature file."""
    data = Path(index_path).read_bytes()
    index = CommunityIndex.model_validate_json(data)
    if previous is not None:
        old = CommunityIndex.model_validate_json(Path(previous).read_bytes())
        if index.version <= old.version:
            raise MaintainerError("version_not_higher", new=index.version, old=old.version)
    key = load_signing_key(name, keyring=keyring)
    signature = signed(data, key)
    sig_path = Path(index_path).with_name(Path(index_path).name + ".sig")
    sig_path.write_bytes(signature)
    return index, key_id(key.public_key()), sig_path


def raw_url(repo: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/main/{path}"


# --- what the review shows -------------------------------------------------------------------------------------------

@dataclass
class Contribution:
    """An open pull request that adds packs (number set), or a pack merged under contributions/ on main but not
    published yet (number None)."""
    number: Optional[int]
    title: str
    author: str
    url: str
    head: str            # the commit whose files are reviewed
    packs: List[str]     # contributions/<kind>/<file>.zip
    check: str           # pass, fail, pending, none (not run), merged

    @property
    def label(self) -> str:
        return f"#{self.number}" if self.number is not None else f"main:{self.packs[0]}"


@dataclass
class ReviewFile:
    path: str             # inside the pack
    size: int
    text: Optional[str]   # None: not a text file


@dataclass
class ReviewedPack:
    path: str             # contributions/<kind>/<file>.zip
    kind: str
    file_name: str
    data: bytes
    sha256: str
    manifest: Any         # PackManifest, or None when the pack cannot be read
    problems: List[str]
    files: List[ReviewFile] = field(default_factory=list)

    @property
    def target(self) -> str:
        return f"packs/{self.kind}/{self.file_name}"

    def line(self) -> str:
        """One line for the question and the CLI: the manifest's author may hold a line separator or a direction
        override, so every value is shown with hidden characters as escapes."""
        m = self.manifest
        what = f"{m.kind} {m.id} {m.version} by {m.author} ({m.license})" if m is not None else self.file_name
        belongs = belongs_text(m.kind, m.topic, m.target) if m is not None else ""
        return one_line(f"{what}{f', belongs to {belongs}' if belongs else ''}: {self.file_name}, "
                        f"{len(self.data)} bytes, sha256 {self.sha256}")


@dataclass
class Review:
    item: Contribution
    head: str
    packs: List[ReviewedPack]
    folder: Path
    temporary: bool
    changes: List[Tuple[str, str]] = field(default_factory=list)   # (status, path): what else the PR changes

    @property
    def ok(self) -> bool:
        return (bool(self.packs) and not self.changes
                and all(p.manifest is not None and not p.problems for p in self.packs))

    def problems(self, lang: str = "en") -> List[str]:
        changes = [say("zoo.maint.problem.other_change", lang, path=one_line(path),
                       change=say(f"zoo.maint.change.{status if status in CHANGE_KINDS else 'X'}", lang))
                   for status, path in self.changes]
        return changes + [f"{p.file_name}: {problem}" for p in self.packs for problem in p.problems]

    def discard(self) -> None:
        """Removes the framework's own review folder (never a folder the person named)."""
        if self.temporary:
            shutil.rmtree(self.folder, ignore_errors=True)


@dataclass
class ChangeResult:
    version: int
    url: str          # the merged pull request
    branch: str
    index: CommunityIndex


def check_state(rollup: Any, name: str = REQUIRED_CHECK) -> str:
    """The state of the check `name` in gh's statusCheckRollup: pass, fail, pending or none (not reported yet)."""
    states = []
    for item in rollup if isinstance(rollup, list) else []:
        if not isinstance(item, dict) or name not in (item.get("name"), item.get("context")):
            continue
        if "conclusion" in item or "status" in item:   # a check run
            if str(item.get("status") or "").upper() != "COMPLETED":
                states.append("pending")
            else:
                states.append("pass" if str(item.get("conclusion") or "").upper() == "SUCCESS" else "fail")
        else:   # a commit status
            state = str(item.get("state") or "").upper()
            states.append("pass" if state == "SUCCESS" else "pending" if state in ("PENDING", "EXPECTED") else "fail")
    if not states:
        return "none"
    if "pending" in states:
        return "pending"
    return "pass" if all(s == "pass" for s in states) else "fail"


def _review_file(path: str, raw: bytes) -> ReviewFile:
    return ReviewFile(path, len(raw), None if b"\x00" in raw else raw.decode("utf-8", errors="replace"))


def one_line(text: Any) -> str:
    """A value on one line with every line break and hidden character shown as an escape (a PR title, an author)."""
    from orchestrator.tools.confirmation import visible
    return visible(str(text))


def shown_text(text: str) -> str:
    """Pack text for a terminal or a text view: lines and tabs kept, every other control or hidden character shown
    as an escape, so a pack can neither hide a line nor send terminal control sequences."""
    from orchestrator.tools.confirmation import visible
    return "\n".join("\t".join(visible(part) for part in line.split("\t"))
                     for line in str(text).replace("\r\n", "\n").split("\n"))


# --- the actions on GitHub -------------------------------------------------------------------------------------------

class _Run:
    """One operation: its progress, its audit rows, its cancel points and what has happened on GitHub so far."""

    def __init__(self, owner: "Maintainer", op: str, values: Dict[str, Any], cancelled: Optional[Callable[[], bool]]):
        self.owner, self.op, self.values, self.cancelled = owner, op, values, cancelled
        # what happened so far, per slot: "contribution" (its merge) and "change" (the publication's own branch and
        # pull request); the end text names both
        self.states: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        self.final = False

    def _audit(self, outcome: str, step: str, detail: str, **values: Any) -> None:
        audit = self.owner.audit
        if audit is None or not callable(getattr(audit, "record", None)):
            raise MaintainerError("audit")
        try:
            audit.record(self.owner.actor, "zoo_maintainer", "framework", "person", outcome, approved=True,
                         arguments={"op": self.op, "step": step, "repo": self.owner.repo, **self.values, **values},
                         detail=detail)
        except Exception:  # noqa: BLE001 - the reason can name a host path
            raise MaintainerError("audit") from None

    def start(self, what: str) -> None:
        self._audit("started", "started", say("zoo.maint.step.started", op=what))
        self.owner.report("started", op=what)

    def step(self, code: str, **values: Any) -> None:
        self.owner.report(code, **values)
        self._audit("ok", code, say(f"zoo.maint.step.{code}", **values),
                    **{k: v for k, v in values.items() if isinstance(v, (str, int))})

    def checkpoint(self) -> None:
        if not self.final and self.cancelled is not None and self.cancelled():
            raise Cancelled()

    def happened(self, state_key: str, /, *, slot: str = "change", **values: Any) -> None:
        self.states[slot] = (state_key, values)

    def forget(self, slot: str, before: Optional[Tuple[str, Dict[str, Any]]] = None) -> None:
        """A step that GitHub answered with a refusal did not happen: the slot is what it was before it."""
        if before is None:
            self.states.pop(slot, None)
        else:
            self.states[slot] = before

    def end(self, error: MaintainerError) -> MaintainerError:
        error.with_states(list(self.states.values()) or [("zoo.maint.state.nothing", {})])
        # the row names what happened, never a folder of this computer (the clone's place in the per-user folder; a
        # git message can name it too)
        shown = MaintainerError(error.code, **{**error.values, **({"folder": "the local clone"}
                                                                  if "folder" in error.values else {})})
        shown.with_states(error.states)
        detail = shown.text("en")
        places = [(self.owner.clone, "<the local clone>"), (self.owner.work_dir, "<the maintainer folder>")]
        try:
            places.append((Path.home(), "~"))
        except (RuntimeError, KeyError, OSError):   # no home folder known: nothing to replace
            pass
        for place, name in places:
            for spelling in sorted({str(place), Path(place).as_posix()}, key=len, reverse=True):
                if len(spelling) > 3:
                    detail = detail.replace(spelling, name)
        try:
            self._audit("cancelled" if isinstance(error, Cancelled) else "error", "end", detail, code=error.code)
        except MaintainerError:
            pass
        return error


class Maintainer:
    """The maintainer actions on the community repository `repo` (owner/name), with a local clone in `work_dir` (the
    per-user folder: for_framework puts it below the community cache). runner/which/keyring/sleep/clock are
    injectable for tests; progress(code, values) gets every step (a zoo.maint.step.* key)."""

    def __init__(self, repo: str, work_dir: Path, *, actor: str, audit: Any, runner: Callable = subprocess.run,
                 which: Callable = shutil.which, keyring: Any = None, trusted_keys: Optional[list] = None,
                 progress: Optional[Progress] = None, sleep: Callable = time.sleep, clock: Callable = time.monotonic):
        self.actor = _person(actor)
        if not REPO_RE.fullmatch(str(repo or "")):
            raise MaintainerError("no_repo")
        self.repo, self.work_dir, self.audit = str(repo), Path(work_dir), audit
        self.clone = self.work_dir / "clone"
        self._runner, self._which, self._keyring = runner, which, keyring
        self._trusted = trusted_keys
        self.progress, self._sleep, self._clock = progress, sleep, clock
        self.gh = self.git = None

    # -- small helpers --------------------------------------------------------------------------------------------
    def report(self, code: str, **values: Any) -> None:
        if self.progress is not None:
            self.progress(code, values)

    def _run(self, cmd: List[str], *, cwd: Optional[Path] = None, binary: bool = False, check: bool = True,
             timeout: int = 600) -> subprocess.CompletedProcess:
        # stdin=DEVNULL: gh and git never read the terminal or the MCP stream; no variables are passed or read.
        # Text is UTF-8 (what gh and git write), never the code page of Windows: a title with a character that the
        # code page lacks would stop the listing, or come out garbled.
        tool = Path(cmd[0]).stem   # gh or git, also gh.exe and git.exe
        text = {} if binary else {"encoding": "utf-8", "errors": "replace"}
        try:
            return self._runner(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=not binary,
                                timeout=timeout, check=check, stdin=subprocess.DEVNULL, **text)
        except subprocess.CalledProcessError as e:
            raise self._failure(tool, e) from None
        except subprocess.TimeoutExpired:
            error = MaintainerError("offline", detail=f"{tool} did not answer within {timeout} s")
            error.unanswered = True   # what it was doing may have reached GitHub
            raise error from None
        except OSError as e:
            raise MaintainerError("tool_failed", tool=tool, detail=tool_error_detail(e)) from None

    @staticmethod
    def _failure(tool: str, error: BaseException) -> MaintainerError:
        detail = tool_error_detail(error) or f"exit code {getattr(error, 'returncode', '?')}"
        if _OFFLINE.search(detail):
            return MaintainerError("offline", detail=detail)
        if tool == "gh" and re.search(r"gh auth login|not logged in|authentication required", detail, re.I):
            return MaintainerError("gh_login")
        return MaintainerError("tool_failed", tool=tool, detail=detail)

    def _git(self, *args: str, binary: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        return self._run([self.git, "-C", str(self.clone), *args], binary=binary, check=check)

    def _gh_json(self, *args: str, what: str) -> Any:
        out = self._run([self.gh, *args]).stdout
        try:
            return json.loads(out or "null")
        except (TypeError, ValueError):
            raise MaintainerError("bad_answer", what=what) from None

    def _blob(self, rev: str, path: str) -> Optional[bytes]:
        result = self._git("show", f"{rev}:{path}", binary=True, check=False)
        return result.stdout if result.returncode == 0 else None

    def _rev(self, rev: str) -> str:
        return self._git("rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()

    def remembered_key(self) -> str:
        """The signing key name of the last key creation or change here (a per-user convenience, not a secret)."""
        try:
            name = json.loads((self.work_dir / "maintainer.json").read_text(encoding="utf-8")).get("key_name", "")
            return name if KEY_NAME.fullmatch(str(name)) else ""
        except (OSError, ValueError, AttributeError):
            return ""

    def remember_key(self, name: str) -> None:
        try:
            ensure_directory(self.work_dir)
            (self.work_dir / "maintainer.json").write_text(json.dumps({"key_name": name}), encoding="utf-8")
        except OSError:
            pass

    def trusted_keys(self) -> list:
        if self._trusted is None:
            try:
                self._trusted = load_trusted_keys()
            except PackError:
                self._trusted = []
        return self._trusted

    # -- the tools and the clone ----------------------------------------------------------------------------------
    def check_tools(self) -> None:
        """gh and git installed and gh logged in (gh's own `auth status`; its token is never read)."""
        self.report("tools")
        self.gh, self.git = self._which("gh"), self._which("git")
        if not self.gh:
            raise MaintainerError("no_gh")
        if not self.git:
            raise MaintainerError("no_git")
        status = self._run([self.gh, "auth", "status", "--hostname", "github.com"], check=False, timeout=60)
        if status.returncode != 0:
            detail = tool_error_detail(subprocess.CalledProcessError(status.returncode, "gh", status.stdout,
                                                                     status.stderr))
            raise MaintainerError("offline", detail=detail) if _OFFLINE.search(detail) else MaintainerError("gh_login")

    def update_clone(self) -> None:
        """The local clone of the community repository (made with `gh repo clone` the first time, line endings never
        converted, so pack and index bytes stay exact) with origin/main fetched."""
        if not (self.clone / ".git").exists():
            if self.clone.exists() and any(self.clone.iterdir()):
                raise MaintainerError("clone_damaged", folder=str(self.clone))
            self.report("clone", repo=self.repo, folder=str(self.clone))
            ensure_directory(self.clone.parent)
            # core.autocrlf=false before the checkout: git never converts line endings in this clone (Windows)
            self._run([self.gh, "repo", "clone", self.repo, str(self.clone), "--", "--no-tags", "--config",
                       "core.autocrlf=false"])
        else:
            self.report("update", repo=self.repo)
        self._git("fetch", "--no-tags", "--prune", "origin", "+refs/heads/main:refs/remotes/origin/main")

    def _require_clean(self) -> None:
        if self._git("status", "--porcelain").stdout.strip():
            raise MaintainerError("clone_dirty", folder=str(self.clone))

    def _restore(self, branch: str = "") -> None:
        """Back to origin/main after a change that did not go through: only the framework's own changes of this run
        are dropped (the clone was clean when it started)."""
        self._git("reset", "--hard", "--quiet", check=False)
        self._git("switch", "--detach", "--quiet", "origin/main", check=False)
        if branch:
            self._git("branch", "-D", branch, check=False)

    # -- 2. review and publish ------------------------------------------------------------------------------------
    def contributions(self) -> List[Contribution]:
        """The open pull requests that add packs under contributions/<kind>/ (with the state of the required check),
        then the packs merged there on main but not published yet."""
        self.check_tools()
        self.update_clone()
        self.report("list", repo=self.repo)
        data = self._gh_json("pr", "list", "--repo", self.repo, "--state", "open", "--base", "main", "--limit", "100",
                             "--json", "number,title,author,url,headRefOid,baseRefName,files,statusCheckRollup",
                             what="gh pr list")
        found = []
        for pr in data if isinstance(data, list) else []:
            if not isinstance(pr, dict) or pr.get("baseRefName", "main") != "main":   # merged into main only
                continue
            packs = sorted({f.get("path", "") for f in pr.get("files") or [] if isinstance(f, dict)
                            and PACK_PATH.fullmatch(str(f.get("path", "")))})
            if not packs or not isinstance(pr.get("number"), int):
                continue
            found.append(Contribution(pr["number"], str(pr.get("title") or ""),
                                      str((pr.get("author") or {}).get("login") or ""), str(pr.get("url") or ""),
                                      str(pr.get("headRefOid") or ""), packs, check_state(pr.get("statusCheckRollup"))))
        head = self._rev("origin/main")
        listed = self._git("ls-tree", "-r", "--name-only", "origin/main", "--", "contributions").stdout.splitlines()
        for path in sorted(p for p in listed if PACK_PATH.fullmatch(p)):
            found.append(Contribution(None, path, "", "", head, [path], "merged"))
        return found

    def find(self, label: str) -> Contribution:
        """A contribution by its number ("7" or "#7") or as main:<path> / <path> of a merged pack."""
        text = str(label or "").strip()
        wanted_pr = int(text.lstrip("#")) if re.fullmatch(r"#?\d{1,9}", text) else None
        wanted_path = text[5:] if text.startswith("main:") else text
        for item in self.contributions():
            if (wanted_pr is not None and item.number == wanted_pr) or \
                    (item.number is None and wanted_pr is None and item.packs[0] == wanted_path):
                return item
        raise MaintainerError("not_found", item=text)

    def review(self, item: Contribution, folder: Optional[Path] = None) -> Review:
        """Fetches the packs of `item` from the commit that was listed (a pull request whose head moved since is
        refused), checks each with `zoo verify` and reads every file of it. The files go to `folder` (new or empty,
        the person's) or to a review folder of the framework's own that Review.discard() removes. Nothing is
        merged."""
        self.check_tools()
        self.update_clone()
        packs, changes = list(item.packs), []
        if item.number is not None:
            self._git("fetch", "--no-tags", "origin", f"+refs/pull/{item.number}/head:refs/lcf/pr/{item.number}")
            head = self._rev(f"refs/lcf/pr/{item.number}")
            if head != item.head:
                raise MaintainerError("changed", item=item.label, head=head[:12])
            packs, changes = self._pr_changes(head)
            if not packs:
                raise MaintainerError("no_packs", item=item.label)
        else:
            head = self._rev("origin/main")
        temporary = folder is None
        if folder is None:
            ensure_directory(self.work_dir / "review")
            folder = Path(tempfile.mkdtemp(prefix="review-", dir=self.work_dir / "review"))
        else:
            folder = Path(folder)
            if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
                raise MaintainerError("dir_not_empty", folder=str(folder))
            ensure_directory(folder)
        review = Review(item, head, [], folder, temporary, changes)
        try:
            for path in packs:
                review.packs.append(self._review_pack(item, head, path, folder))
        except BaseException:
            review.discard()
            raise
        return review

    def _pr_changes(self, head: str) -> Tuple[List[str], List[Tuple[str, str]]]:
        """What the pull request changes, as GitHub merges it (from its merge base with main): the packs it adds under
        contributions/<kind>/, and every other change (status, path). Only the first are reviewed, so a pull request
        with anything else is never merged from here: the person would not have seen that change. Read from the
        reviewed commit itself (gh lists at most 100 files of a pull request)."""
        out = self._git("diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
                        f"origin/main...{head}").stdout
        parts = out.split("\0")
        packs, other = [], []
        for status, path in zip(parts[0::2], parts[1::2]):
            status = status.strip()[:1]
            if status == "A" and PACK_PATH.fullmatch(path):
                packs.append(path)
            elif path:
                other.append((status, path))
        return sorted(packs), other

    def _review_pack(self, item: Contribution, head: str, path: str, folder: Path) -> ReviewedPack:
        match = PACK_PATH.fullmatch(path)
        if match is None:
            raise MaintainerError("not_a_pack", path=path, item=item.label)
        self.report("fetch", path=path, item=item.label)
        data = self._blob(head, path)
        if data is None:
            raise MaintainerError("not_a_pack", path=path, item=item.label)
        if len(data) > MAX_CONTRIBUTION_BYTES:
            raise MaintainerError("too_large", path=path, size=len(data), limit=MAX_CONTRIBUTION_BYTES)
        kind, name = match.group(1), match.group(2)
        pack_file = folder / name
        pack_file.write_bytes(data)
        self.report("verify", name=name)
        try:
            manifest, problems = CommunityManager(None).verify_file(pack_file)
        except (PackError, OSError) as e:
            manifest, problems = None, [str(e)]
        if manifest is not None and manifest.kind != kind:
            problems.append(f"the pack is a {manifest.kind} pack, but it was submitted under contributions/{kind}/")
        files = []
        if manifest is not None:
            unpacked = folder / Path(name).stem
            try:
                read_pack(pack_file, unpacked)   # the listed files, each checked against the manifest
                files = [_review_file(rel, file.read_bytes()) for rel, file in list_files(unpacked)]
                # and every other member of the file, which is published with it: the manifest, the author
                # signature and any other name read_pack lets through (.lcf-provenance.json, which no client reads)
                listed = {f.path for f in manifest.files}
                order = {MANIFEST_NAME: 0, SIGNATURE_NAME: 1}
                shown = []
                with zipfile.ZipFile(pack_file) as zf:
                    for info in sorted((i for i in zf.infolist() if i.filename not in listed),
                                       key=lambda i: (order.get(i.filename, 2), i.filename)):
                        if info.filename not in RESERVED_NAMES:   # read_pack refuses these already
                            problems.append(f"{one_line(info.filename)}: not in the manifest")
                        elif info.file_size > MAX_SHOWN_BYTES:
                            problems.append(f"{info.filename}: {info.file_size} bytes, too large to be shown in "
                                            "the review")
                        else:
                            raw = zf.read(info)
                            if not (unpacked / info.filename).exists():
                                (unpacked / info.filename).write_bytes(raw)
                            shown.append(_review_file(info.filename, raw))
                files = shown + files
            except (PackError, OSError, zipfile.BadZipFile) as e:
                problems.append(str(e))
        return ReviewedPack(path, kind, name, data, hashlib.sha256(data).hexdigest(), manifest, problems, files)

    def publish(self, review: Review, *, key_name: str, trust: str = "community", title: str = "",
                cancelled: Optional[Callable[[], bool]] = None) -> ChangeResult:
        """After the person's "Yes" to the review: merges the contribution (its check passed, its head unchanged),
        moves its packs to packs/<kind>/, adds their index entries, raises the index version and time, signs it and
        merges that as a pull request after the required check. See the module text."""
        item = review.item
        if not review.ok:
            raise MaintainerError("rejected", problems="\n".join(f"- {p}" for p in review.problems())
                                  or f"- {item.label}: no pack")
        if trust not in ("community", "verified"):
            raise ValueError(f"unknown trust {trust!r}")
        if title and len(review.packs) != 1:
            raise MaintainerError("title_one_pack", item=item.label, n=len(review.packs))
        run = _Run(self, "publish", {"item": item.label, "packs": [p.path for p in review.packs],
                                     "sha256": [p.sha256 for p in review.packs]}, cancelled)
        try:
            run.start(say("zoo.maint.op.publish", item=item.label))
            key = load_signing_key(key_name, keyring=self._keyring)
            self.check_tools()
            run.checkpoint()
            self.update_clone()
            self._require_clean()
            previous = self._current_index(key)
            for pack in review.packs:   # everything that can be refused is refused before the first merge
                if self._blob("origin/main", pack.target) is not None:
                    raise MaintainerError("exists", path=pack.target)
            # the bytes that were reviewed, never the file again (a review folder is the person's to look into)
            entries = [entry_for(p.manifest, p.data, raw_url(self.repo, p.target), trust=trust, title=title)
                       for p in review.packs]
            next_index(previous, add=entries)
            if item.number is not None:
                run.checkpoint()
                self._merge_contribution(run, review)
            run.checkpoint()
            self.update_clone()
            for pack in review.packs:
                merged = self._blob("origin/main", pack.path)
                if merged is None or hashlib.sha256(merged).hexdigest() != pack.sha256:
                    raise MaintainerError("merged_differs", path=pack.path)
            slug = "-".join(f"{p.manifest.kind}-{p.manifest.id}-{p.manifest.version}" for p in review.packs)
            what = ", ".join(f"{p.manifest.kind} pack {p.manifest.id} {p.manifest.version}" for p in review.packs)
            result = self._change_index(run, key, f"publish-{slug}", add=entries,
                                        moves=[(p.path, p.target) for p in review.packs],
                                        message=f"Publish {what}",
                                        body=f"Publishes {what} from {item.label} (reviewed with LLM Cross-Compiler "
                                             "Framework: zoo verify and every file read).")
            self.remember_key(key_name)
            return result
        except KeyboardInterrupt:
            raise run.end(Cancelled()) from None
        except MaintainerError as e:
            raise run.end(e) from None
        except PackError as e:
            raise run.end(MaintainerError("failed", detail=str(e))) from None
        except Exception as e:  # noqa: BLE001 - an error nobody foresaw still ends the run: audit row, state, reason
            raise run.end(MaintainerError("failed", detail=f"{type(e).__name__}: {e}"[:300])) from e

    def _merge_contribution(self, run: _Run, review: Review) -> None:
        item = review.item
        view = self._pr_view(item.number)
        if view.get("state") != "OPEN":
            raise MaintainerError("not_open", pr=item.number, state=view.get("state"))
        if view.get("baseRefName") != "main":   # gh would merge it into that branch, not into main
            raise MaintainerError("not_main", pr=item.number, base=one_line(view.get("baseRefName")))
        if view.get("headRefOid") != review.head:
            raise MaintainerError("changed", item=item.label, head=str(view.get("headRefOid"))[:12])
        state = check_state(view.get("statusCheckRollup"))
        run.step("check", check=REQUIRED_CHECK, item=item.label, state=state)
        if state != "pass":
            raise MaintainerError("check_not_passed", check=REQUIRED_CHECK, pr=item.number, state=state)
        run.checkpoint()
        run.step("merge_contribution", item=item.label)
        # Until gh answers, the merge may have happened (Ctrl+C, no answer in time): the state says to look.
        run.happened("zoo.maint.state.merging_contribution", slot="contribution", item=item.label)
        self._remote(run, "contribution", None, [self.gh, "pr", "merge", str(item.number), "--repo", self.repo,
                                                 "--squash", "--match-head-commit", review.head])
        run.happened("zoo.maint.state.merged_waiting", slot="contribution", item=item.label)
        run.step("merged_contribution", item=item.label)

    def _remote(self, run: _Run, slot: str, before: Optional[Tuple[str, Dict[str, Any]]],
                cmd: List[str]) -> subprocess.CompletedProcess:
        """A step that changes GitHub; when gh or git answers with a refusal it did not happen, and the state of
        `slot` is what it was before (an unanswered call keeps the "look whether it happened" state)."""
        try:
            return self._run(cmd)
        except MaintainerError as e:
            if not e.unanswered:
                run.forget(slot, before)
            raise

    def _pr_view(self, number: int) -> dict:
        view = self._gh_json("pr", "view", str(number), "--repo", self.repo, "--json",
                             "number,state,headRefOid,baseRefName,statusCheckRollup,url", what="gh pr view")
        if not isinstance(view, dict):
            raise MaintainerError("bad_answer", what="gh pr view")
        return view

    # -- 3. revoke ------------------------------------------------------------------------------------------------
    def published(self, key_name: str = "") -> Optional[CommunityIndex]:
        """The signed index on main (verified with the keyring key `key_name`, when given, or a trusted key), None
        before the first one."""
        key = load_signing_key(key_name, keyring=self._keyring) if key_name else None
        self.check_tools()
        self.update_clone()
        return self._current_index(key)

    def revoke(self, kind: str, pack_id: str, *, reason: str, key_name: str, version: Optional[str] = None,
               sha256: Optional[str] = None, cancelled: Optional[Callable[[], bool]] = None) -> ChangeResult:
        """After the person's "Yes": a revocation (by sha256, or kind and id with one version or every version) in the
        signed index, version and time raised, through the same pull request route as publish."""
        wanted = revocation(kind, pack_id, reason=reason, version=version, sha256=sha256)
        what = describe_revocation(wanted)
        run = _Run(self, "revoke", {"kind": kind, "id": pack_id, "version": version or "", "sha256": sha256 or ""},
                   cancelled)
        try:
            run.start(say("zoo.maint.op.revoke", what=what))
            key = load_signing_key(key_name, keyring=self._keyring)
            self.check_tools()
            run.checkpoint()
            self.update_clone()
            self._require_clean()
            previous = self._current_index(key)
            if previous is None:
                raise MaintainerError("no_index")
            next_index(previous, revoke=[wanted])
            if not any(wanted.matches(e.kind, e.id, e.version, e.sha256) for e in previous.entries):
                self.report("note_no_entry")
            version_part = wanted.version or (wanted.sha256 or "")[:12] or "all"
            result = self._change_index(run, key, f"revoke-{kind}-{pack_id}-{version_part}", revoke=[wanted],
                                        message=f"Revoke {what}", body=f"Revokes {what}: {wanted.reason}")
            self.remember_key(key_name)
            return result
        except KeyboardInterrupt:
            raise run.end(Cancelled()) from None
        except MaintainerError as e:
            raise run.end(e) from None
        except PackError as e:
            raise run.end(MaintainerError("failed", detail=str(e))) from None
        except Exception as e:  # noqa: BLE001 - an error nobody foresaw still ends the run: audit row, state, reason
            raise run.end(MaintainerError("failed", detail=f"{type(e).__name__}: {e}"[:300])) from e

    # -- the signed index on main and its change --------------------------------------------------------------------
    def _current_index(self, key: Optional[Ed25519PrivateKey]) -> Optional[CommunityIndex]:
        """index.json on origin/main, verified with the maintainer's key or a trusted key of this framework before
        anything is built on it (None: no index yet). Read from git's objects, never from the working tree."""
        data = self._blob("origin/main", "index.json")
        if data is None:
            return None
        sig = self._blob("origin/main", "index.json.sig")
        if sig is None:
            raise MaintainerError("bad_index", detail="index.json.sig is missing")
        keys = ([key.public_key()] if key is not None else []) + [t.public_key() for t in self.trusted_keys()]
        try:
            return CommunityManager._verify_index(data, sig, keys)
        except PackError as e:
            raise MaintainerError("bad_index", detail=str(e)) from None

    def _change_index(self, run: _Run, key: Ed25519PrivateKey, slug: str, *, message: str, body: str,
                      add: Sequence[IndexEntry] = (), revoke: Sequence[Revocation] = (),
                      moves: Sequence[Tuple[str, str]] = ()) -> ChangeResult:
        """The new signed index on a new branch, pushed, as a pull request, merged after the required check."""
        previous = self._current_index(key)   # main as it is now (it can have moved since the first look)
        new = next_index(previous, add=add, revoke=revoke)
        data = index_bytes(new)
        sig = signed(data, key)
        own = key_id(key.public_key())
        if own not in {t.key_id for t in self.trusted_keys()}:
            self.report("note_untrusted_key", key_id=own)
        # The time makes each try a new branch: a branch left from a try that opened no pull request never blocks one.
        suffix = "-v{}-{}".format(new.version, re.sub(r"[^0-9]", "", new.generated)[:14])
        branch = re.sub(r"[^A-Za-z0-9._-]+", "-", f"lcf-{slug}")[:100 - len(suffix)].rstrip("-.") + suffix
        if not BRANCH.fullmatch(branch) or branch == "main":
            raise ValueError(f"not a branch of the maintainer actions: {branch!r}")
        run.checkpoint()
        self._git("switch", "--quiet", "--force-create", branch, "origin/main")
        try:
            for source, target in moves:
                run.step("move", source=source, target=target)
                ensure_directory(self.clone / Path(target).parent)
                self._git("mv", "--", source, target)
            for entry in add:
                run.step("entry", kind=entry.kind, id=entry.id, version=entry.version, trust=entry.trust,
                         url=entry.url)
            for revocation in revoke:
                run.step("revocation", what=describe_revocation(revocation), reason=revocation.reason)
            run.step("version", old=previous.version if previous else 0, new=new.version, time=new.generated)
            (self.clone / "index.json").write_bytes(data)
            (self.clone / "index.json.sig").write_bytes(sig)
            run.step("sign", key_id=own)
            self._git("add", "--", "index.json", "index.json.sig")
            run.checkpoint()
            self._git("commit", "--quiet", "-m", message)
            commit = self._rev("HEAD")
            committed = {"index.json": data, "index.json.sig": sig}
            if any(self._blob(commit, name) != content for name, content in committed.items()):
                raise MaintainerError("failed", detail="git changed index.json or its signature while committing")
            for (_source, target), entry in zip(moves, add):
                blob = self._blob(commit, target)
                if blob is None or hashlib.sha256(blob).hexdigest() != entry.sha256:
                    raise MaintainerError("failed", detail=f"git changed {target} while committing")
            run.step("commit", branch=branch)
            run.checkpoint()
            # The branch by its explicit name: never main (it is protected, and nothing here pushes to it). Until git
            # answers, the push may have happened (the state says to look).
            run.happened("zoo.maint.state.pushing", branch=branch)
            try:
                self._git("push", "--quiet", "origin", f"{commit}:refs/heads/{branch}")
            except MaintainerError as e:
                if not e.unanswered:
                    run.forget("change")
                raise
        except BaseException:
            self._restore(branch)
            raise
        run.happened("zoo.maint.state.branch_pushed", branch=branch)
        run.step("push", branch=branch)
        self._restore(branch)
        number, url = None, ""
        try:
            run.checkpoint()
            lines = self._run([self.gh, "pr", "create", "--repo", self.repo, "--base", "main", "--head", branch,
                               "--title", message, "--body", body]).stdout.strip().splitlines()
            url = lines[-1].strip() if lines else ""
            found = re.search(r"/pull/(\d+)\b", url)
            if not found:
                raise MaintainerError("bad_answer", what="gh pr create")
            number = int(found.group(1))
            run.happened("zoo.maint.state.pr_open", url=url)
            run.step("pr", url=url)
            self._wait_for_check(run, number, commit, url)
            run.checkpoint()   # the last point to cancel: the merge below publishes
        except (Cancelled, KeyboardInterrupt):
            # A cancel never leaves the signed publication behind, one click on GitHub from being published.
            self._withdraw(run, branch, number, url)
            raise
        except MaintainerError as e:
            if e.code == "changed":   # someone else pushed to this branch: it is never left open to be merged
                self._withdraw(run, branch, number, url)
            raise
        run.final = True
        run.step("merge", pr=number)
        before = run.states.get("change")
        run.happened("zoo.maint.state.merging_pr", url=url)
        self._remote(run, "change", before, [self.gh, "pr", "merge", str(number), "--repo", self.repo, "--squash",
                                             "--match-head-commit", commit])
        run.happened("zoo.maint.state.pr_merged", url=url)
        self._run([self.gh, "api", "--method", "DELETE", f"repos/{self.repo}/git/refs/heads/{branch}"], check=False)
        self.update_clone()
        if self._blob("origin/main", "index.json") != data:
            raise MaintainerError("failed", detail=f"main does not carry the new index yet; look at {url}")
        run.step("published", version=new.version, url=url)
        return ChangeResult(new.version, url, branch, new)

    def _withdraw(self, run: _Run, branch: str, number: Optional[int], url: str) -> None:
        """After a cancel before the last merge: the publication's own pull request is closed and its branch deleted,
        so nothing half-published stays behind. Only what this run made is touched; the contribution's merge cannot
        be undone (its pack waits under contributions/ and is listed again)."""
        closed = False
        try:
            if number is not None:
                self._run([self.gh, "pr", "close", str(number), "--repo", self.repo])
                closed = True
                run.happened("zoo.maint.state.pr_closed", url=url, branch=branch)
                run.step("pr_closed", url=url)
            self._run([self.gh, "api", "--method", "DELETE", f"repos/{self.repo}/git/refs/heads/{branch}"])
            run.happened("zoo.maint.state.withdrawn" if closed else "zoo.maint.state.branch_deleted", url=url,
                         branch=branch)
            run.step("branch_deleted", branch=branch)
        except MaintainerError:
            pass   # the state names what is still on GitHub (the pull request or the branch)

    def _wait_for_check(self, run: _Run, number: int, commit: str, url: str) -> None:
        start, reported = self._clock(), 0.0
        while True:
            run.checkpoint()
            view = self._pr_view(number)
            if view.get("headRefOid") not in (None, commit):
                raise MaintainerError("changed", item=f"#{number}", head=str(view.get("headRefOid"))[:12])
            state = check_state(view.get("statusCheckRollup"))
            if state == "pass":
                run.step("check", check=REQUIRED_CHECK, item=f"#{number}", state=state)
                return
            if state == "fail":
                raise MaintainerError("check_failed", check=REQUIRED_CHECK, url=url)
            elapsed = self._clock() - start
            if elapsed > WAIT_TIMEOUT:
                raise MaintainerError("check_timeout", check=REQUIRED_CHECK, url=url, minutes=WAIT_TIMEOUT // 60)
            if elapsed - reported >= REPORT_EVERY or reported == 0.0:
                reported = max(elapsed, 0.001)
                self.report("wait", check=REQUIRED_CHECK, pr=number, elapsed=int(elapsed))
            for _ in range(POLL_SECONDS):
                run.checkpoint()
                self._sleep(1)


def maintainer_dir(cache_dir: Path, repo: str) -> Path:
    """The per-user folder of the maintainer actions for `repo`: below the community cache (user_state_dir)."""
    owner, _, name = str(repo).partition("/")
    return Path(cache_dir) / "maintainer" / owner / name


def for_framework(framework: Any, actor: str, **kwargs: Any) -> Maintainer:
    """A Maintainer for the framework's community_repo, its per-user folder and its audit log."""
    config = getattr(framework, "config", None)
    repo = config.get("community_repo", "") if config is not None and hasattr(config, "get") else ""
    if not REPO_RE.fullmatch(str(repo or "")):
        raise MaintainerError("no_repo")
    cm = getattr(framework, "community_manager", None)
    if cm is None and hasattr(framework, "get_component"):
        cm = framework.get_component("community_manager")
    cache = cm.cache_dir if cm is not None else CommunityManager(framework).cache_dir
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    return Maintainer(repo, maintainer_dir(cache, repo), actor=actor, audit=audit, **kwargs)
