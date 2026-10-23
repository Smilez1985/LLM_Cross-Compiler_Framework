#!/usr/bin/env python3
"""
Hugging Face for AI callers (MCP design 2.4, work package T6): search with the gated filter, one lookup per model
(licence, gating, access of the stored token, files, size, commit), and one cancellable, resumable fetcher checked
against the Hub's sha256. The person's own downloads in the GUI's Hugging Face browser go through the same fetcher into
the same folders (download_for_person, DECISIONS round 11 A6): one code path for both.

Rules that hold for every call here:

- Only the token the person stored in the framework (`hf_token` in the vault) is used, and it is read here, inside the
  framework. Without one every call passes `token=False`, so huggingface_hub never falls back to an implicit token
  (`HF_TOKEN`, the Hugging Face home folder's token file, an OIDC exchange) the person never gave the framework. The
  token never appears in a result, a note, an error or the audit log, and it is sent only to huggingface.co: a
  redirect to the Hub's file storage carries no token (requests drops the header on a cross-host redirect, and the
  fetcher refuses any other host).
- Offline mode refuses before any network request; a network error is an error, never an empty list.
- A licence is "permissive" only on a fixed allowlist; `other`, custom, list-valued and unknown licences are
  "unknown", and the base model's licence and gating are reported too (a GGUF re-upload of a gated model shows as such).
- The person's own download (download_for_person) sends the token the person typed in the browser's token question
  or stored in the framework, never an implicit one either; like the rest of the browser it does not read offline mode.
- A download lands in `<models_dir>/hf/<owner>--<name>/<commit[:12]>/<filename>`: one folder per commit (Hugging Face
  forbids `--` in repository ids, so the encoding cannot collide). The file is written to a `.part` file that a
  later run resumes, under a lock per file, and gets its name only after its size and sha256 matched what the Hub
  lists for that commit. A stop event ends the transfer between two chunks and keeps the `.part` file.
"""

import os
import re
import shutil  # noqa: F401 - tests and tools_hf replace or read shutil.disk_usage through this module
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from orchestrator.tools.grants import PERMISSIVE_LICENSES
from orchestrator.utils.download import DownloadError, IntegrityError, PinnedDownloader, _Retry, sha256_file
from orchestrator.utils.helpers import digest_matches

HUB_HOST = "huggingface.co"
HUB_URL = f"https://{HUB_HOST}"
REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_FILES = 2000
# Licence names that are known to restrict use (the old keyword check of ModelManager, kept and widened).
RESTRICTIVE_KEYWORDS = ("non-commercial", "noncommercial", "cc-by-nc", "research-only", "llama", "gemma",
                        "creativeml-openrail", "openrail", "gpl", "agpl", "deepseek", "qwen-research")
GATING = {False: "false", None: "false", "false": "false", True: "auto", "auto": "auto", "manual": "manual"}
TOKEN_NEXT = ("Ask the person to accept the model's conditions on huggingface.co with their account, then to store a "
              "token with 'llm-cli secrets set hf_token' or in the GUI.")
OFFLINE_NEXT = "Offline mode is on; only the person can switch it off (setting offline_mode)."


class HubError(ValueError):
    """A refusal or failure meant for the person and the calling model, with a reason code and the next step. It
    never names the token or a folder of this computer. `blocker`: the facts catalogue's code of a hard fact that
    refuses the call before any question (offline_mode, disk_full, ...), "" otherwise."""

    def __init__(self, message: str, code: str = "failed", next: str = "", blocker: str = ""):
        super().__init__(message)
        self.code, self.next, self.blocker = code, next, blocker


class DownloadStopped(DownloadError):
    """The download was stopped (cancel_job); its .part file is kept for a later run."""


def classify_license(value: Any) -> Tuple[str, str]:
    """(licence as shown, restrictive: "false" only on the permissive allowlist, "true" for a known restrictive one,
    "unknown" otherwise: missing, `other`, custom, a list)."""
    if isinstance(value, (list, tuple)):
        shown = ", ".join(str(v) for v in value)[:200]
        return shown or "unknown", "unknown"
    if not isinstance(value, str) or not value.strip():
        return "unknown", "unknown"
    name = value.strip()[:200]
    lowered = name.lower()
    if lowered in PERMISSIVE_LICENSES:
        return name, "false"
    if any(k in lowered for k in RESTRICTIVE_KEYWORDS):
        return name, "true"
    return name, "unknown"


def gating(value: Any) -> str:
    """The Hub's gating mode: false (downloads without conditions), auto (conditions accepted at once) or manual
    (the repository owner approves each request)."""
    return GATING.get(value if not isinstance(value, str) else value.lower(), "manual" if value else "false")


def folder_name(repo: str) -> str:
    """`<owner>--<name>`: Hugging Face forbids `--` in repository ids, so two repositories never share a folder."""
    owner, _, name = repo.partition("/")
    return f"{owner}--{name}"


def safe_filename(name: str) -> bool:
    """A repository file name that is a plain relative path (never resolved outside its commit folder)."""
    if not name or len(name) > 300 or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    return bool(parts) and all(p not in ("", ".", "..") for p in parts)


def download_path(models_dir: Path, repo: str, commit: str, name: str) -> Path:
    """Where file `name` of `repo` at `commit` lands: `<models_dir>/hf/<owner>--<name>/<commit[:12]>/<name>`. The one
    layout of every Hugging Face file the framework downloads for a person or an AI (download_hf_file, the GUI's
    Hugging Face browser, the Take over of the old cache, Core/hf_takeover.py)."""
    return Path(models_dir) / "hf" / folder_name(repo) / commit[:12] / name


@dataclass
class HubFile:
    name: str
    size: Optional[int]
    sha256: Optional[str] = None  # the LFS sha256; None for a small file stored without LFS


def hub_file(sibling: Any) -> HubFile:
    """One entry of model_info(files_metadata=True).siblings with its size and LFS sha256 (what the fetcher checks a
    download against)."""
    lfs = getattr(sibling, "lfs", None)
    sha = getattr(lfs, "sha256", None) if lfs is not None and not isinstance(lfs, dict) else \
        (lfs.get("sha256") if isinstance(lfs, dict) else None)
    size = getattr(sibling, "size", None)
    return HubFile(str(sibling.rfilename), int(size) if isinstance(size, int) else None,
                   str(sha).lower() if sha else None)


@dataclass
class ModelFacts:
    """What the framework looked up for one model at one commit (repository text marked by the caller)."""
    repo: str
    commit: str
    license: str
    license_restrictive: str
    license_name: str = ""
    license_link: str = ""
    gated: str = "false"
    token_access: str = "not_needed"
    parameters: Optional[int] = None
    files: List[HubFile] = field(default_factory=list)
    total_bytes: int = 0
    files_truncated: bool = False
    base_model: str = ""
    base_license: str = ""
    base_license_restrictive: str = ""
    base_gated: Optional[str] = None  # None: no base model named, or it could not be looked up

    def file(self, name: str) -> Optional[HubFile]:
        return next((f for f in self.files if f.name == name), None)


def _error_name(e: BaseException) -> str:
    return e.__class__.__name__


def hub_error(e: BaseException, what: str, with_token: Any) -> "HubError":
    """A failed Hub call as the HubError a person and an AI read: a gated repository the token cannot read, a missing
    revision or repository, or the Hub not reachable. `with_token`: whether a token was sent (permission_denied rather
    than needs_person), or a callable that tells it, asked only for a gated repository. Never names the token."""
    try:
        from huggingface_hub.errors import (GatedRepoError, RepositoryNotFoundError, RevisionNotFoundError)
    except ImportError:  # pragma: no cover - huggingface_hub is a dependency
        GatedRepoError = RepositoryNotFoundError = RevisionNotFoundError = ()  # type: ignore
    if isinstance(e, HubError):
        return e
    if GatedRepoError and isinstance(e, GatedRepoError):
        return HubError(f"{what}: the repository is gated and the stored token has no access.",
                        code="permission_denied" if (with_token() if callable(with_token) else with_token)
                        else "needs_person", next=TOKEN_NEXT)
    if RevisionNotFoundError and isinstance(e, RevisionNotFoundError):
        return HubError(f"{what}: that revision does not exist.", code="unknown_id",
                        next="Call get_hf_model_info without a revision for the current commit.")
    if RepositoryNotFoundError and isinstance(e, RepositoryNotFoundError):
        return HubError(f"{what}: no such repository (or a private one the stored token cannot read).",
                        code="unknown_id", next="Check the repository id; search_hf_models finds models.")
    return HubError(f"{what}: Hugging Face could not be reached or answered with an error ({_error_name(e)}).",
                    code="failed", next="Try again later; tell the person if it keeps failing.")


class HubClient:
    """The Hub as AI callers use it. `api_factory(token)` gives an HfApi (tests replace it)."""

    def __init__(self, framework, api_factory: Optional[Callable[[Any], Any]] = None):
        self.framework = framework
        self._api_factory = api_factory

    # --- the stored token, never an implicit one ---------------------------------------------------------------

    def token(self) -> Optional[str]:
        sm = getattr(self.framework, "secrets_manager", None)
        try:
            value = sm.get_secret("hf_token") if sm is not None else None
        except Exception:  # noqa: BLE001 - a locked vault is "no token"
            value = None
        return value if isinstance(value, str) and value.strip() else None

    def token_arg(self):
        """The stored token, or False: huggingface_hub then sends no token at all (never one from HF_TOKEN or the
        Hugging Face home folder)."""
        return self.token() or False

    def api(self):
        if self._api_factory is not None:
            return self._api_factory(self.token_arg())
        from huggingface_hub import HfApi
        return HfApi(token=self.token_arg())

    def offline(self) -> bool:
        config = getattr(self.framework, "config", None)
        try:
            return bool(config.get("offline_mode", False)) if config is not None else False
        except Exception:  # noqa: BLE001
            return False

    def check_online(self) -> None:
        if self.offline():
            raise HubError("Hugging Face is not used in offline mode.", code="not_supported", next=OFFLINE_NEXT,
                           blocker="offline_mode")

    def _failed(self, e: BaseException, what: str) -> HubError:
        return hub_error(e, what, lambda: bool(self.token()))

    # --- search -------------------------------------------------------------------------------------------------

    def search(self, query: str, limit: int = 20, gated_mode: str = "free") -> List[Dict[str, Any]]:
        """Models matching `query`, most downloaded first. gated_mode free (only models that download without
        accepting conditions), gated (only gated ones) or any."""
        self.check_online()
        flag = {"free": False, "gated": True, "any": None}[gated_mode]
        try:
            models = list(self.api().list_models(search=query, limit=limit, sort="downloads", gated=flag,
                                                 expand=["gated", "downloads", "likes", "tags"],
                                                 token=self.token_arg()))
        except Exception as e:  # noqa: BLE001 - every failure is an error, never an empty list
            raise self._failed(e, "The search failed") from None
        found = []
        for m in models:
            mode = gating(getattr(m, "gated", False))
            if gated_mode == "free" and mode != "false":
                continue  # the Hub filters already; a model it lets through anyway is still left out
            licence = next((t.split(":", 1)[1] for t in (getattr(m, "tags", None) or []) if
                            isinstance(t, str) and t.startswith("license:")), "unknown")
            found.append({"id": str(getattr(m, "id", "") or getattr(m, "modelId", "")), "gated": mode,
                          "downloads": int(getattr(m, "downloads", 0) or 0), "likes": int(getattr(m, "likes", 0) or 0),
                          "license": licence})
        return found

    # --- one model ----------------------------------------------------------------------------------------------

    def access(self, repo: str, mode: str) -> str:
        """Whether the stored token can read a gated repository, checked here: yes, no, no_token or not_needed."""
        if mode == "false":
            return "not_needed"
        if not self.token():
            return "no_token"
        try:
            self.api().auth_check(repo, token=self.token_arg())
            return "yes"
        except Exception as e:  # noqa: BLE001
            try:
                from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
                if isinstance(e, (GatedRepoError, RepositoryNotFoundError)):
                    return "no"
            except ImportError:  # pragma: no cover
                pass
            raise self._failed(e, "The access check failed") from None

    def info(self, repo: str, revision: Optional[str] = None, with_files: bool = True) -> ModelFacts:
        self.check_online()
        if not REPO_ID.match(repo or ""):
            raise HubError(f"'{repo}' is not a Hugging Face repository id (owner/name).", code="invalid_value",
                           next="Give owner/name, e.g. Qwen/Qwen2.5-0.5B-Instruct.")
        if revision is not None and not COMMIT.match(revision):
            raise HubError("A revision is a 40-character commit hash.", code="invalid_value",
                           next="Leave revision out for the current commit.")
        try:
            info = self.api().model_info(repo, revision=revision, files_metadata=with_files, token=self.token_arg())
        except Exception as e:  # noqa: BLE001
            raise self._failed(e, f"The lookup of {repo} failed") from None
        card = getattr(info, "card_data", None)
        card_get = (lambda k: card.get(k)) if hasattr(card, "get") else (lambda k: getattr(card, k, None))
        raw_licence = card_get("license") if card is not None else None
        if raw_licence is None:
            raw_licence = next((t.split(":", 1)[1] for t in (getattr(info, "tags", None) or [])
                                if isinstance(t, str) and t.startswith("license:")), None)
        licence, restrictive = classify_license(raw_licence)
        facts = ModelFacts(repo=repo, commit=str(getattr(info, "sha", "") or ""), license=licence,
                           license_restrictive=restrictive, gated=gating(getattr(info, "gated", False)))
        if licence.lower() == "other" and card is not None:
            facts.license_name = str(card_get("license_name") or "")[:200]
            facts.license_link = str(card_get("license_link") or "")[:500]
        safetensors = getattr(info, "safetensors", None)
        total = getattr(safetensors, "total", None) if safetensors is not None else None
        facts.parameters = int(total) if isinstance(total, (int, float)) and total > 0 else None
        siblings = list(getattr(info, "siblings", None) or [])
        facts.files_truncated = len(siblings) > MAX_FILES
        for s in siblings[:MAX_FILES]:
            facts.files.append(hub_file(s))
        facts.total_bytes = sum(f.size or 0 for f in facts.files)
        facts.token_access = self.access(repo, facts.gated)
        base = card_get("base_model") if card is not None else None
        base = base[0] if isinstance(base, (list, tuple)) and base else base
        if isinstance(base, str) and REPO_ID.match(base) and base != repo:
            facts.base_model = base
            try:
                base_info = self.api().model_info(base, token=self.token_arg())
                base_card = getattr(base_info, "card_data", None)
                base_raw = (base_card.get("license") if hasattr(base_card, "get")
                            else getattr(base_card, "license", None)) if base_card is not None else None
                facts.base_license, facts.base_license_restrictive = classify_license(base_raw)
                facts.base_gated = gating(getattr(base_info, "gated", False))
            except Exception:  # noqa: BLE001 - unknown is shown as unknown, and keeps the file out of every grant
                facts.base_license, facts.base_license_restrictive, facts.base_gated = "unknown", "unknown", None
        return facts


# --- the fetcher ---------------------------------------------------------------------------------------------------

def _is_hub(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == HUB_HOST or host.endswith(".huggingface.co") or host.endswith(".hf.co")


class HubFetcher(PinnedDownloader):
    """One file of one commit, resumable and stoppable. The token goes only into the request to huggingface.co."""

    def __init__(self, token: Optional[str] = None, stop: Optional[threading.Event] = None, session=None,
                 sleep=None, logger=None):
        kwargs = {"session": session, "logger": logger}
        if sleep is not None:
            kwargs["sleep"] = sleep
        super().__init__(**kwargs)
        self._token = token
        self.stop = stop or threading.Event()

    @staticmethod
    def url(repo: str, commit: str, filename: str) -> str:
        return f"{HUB_URL}/{repo}/resolve/{commit}/{quote(filename)}"

    def _download_range(self, url: str, part: Path, have: int, size: int, name: str, progress) -> None:
        import requests
        if self.stop.is_set():
            raise DownloadStopped(f"The download of {name} was stopped; the part already downloaded is kept.")
        headers = {"Accept-Encoding": "identity", **({"Range": f"bytes={have}-"} if have else {})}
        if self._token and urlparse(url).hostname == HUB_HOST:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            with self.session.get(url, headers=headers, stream=True, timeout=(15, 60), allow_redirects=True) as r:
                if not str(r.url).startswith("https://") or not _is_hub(str(r.url)):
                    raise self.error("Refusing a download that left Hugging Face's own servers.")
                if r.status_code in (401, 403):
                    raise HubError(f"Hugging Face refused {name} (HTTP {r.status_code}).", code="permission_denied",
                                   next=TOKEN_NEXT)
                if r.status_code == 416:
                    part.unlink(missing_ok=True)
                    raise _Retry("HTTP 416")
                if r.status_code == 429 or r.status_code >= 500:
                    raise _Retry(f"HTTP {r.status_code}")
                if r.status_code not in (200, 206):
                    raise self.error(f"Download of {name} failed: HTTP {r.status_code}")
                resumed = bool(have) and r.status_code == 206
                if resumed:
                    match = re.match(r"bytes (\d+)-\d+/(\d+|\*)", str(r.headers.get("Content-Range", "")))
                    if not match or int(match.group(1)) != have:
                        part.unlink(missing_ok=True)
                        raise _Retry("unexpected Content-Range")
                else:
                    have = 0
                with open(part, "ab" if resumed else "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if self.stop.is_set():
                            raise DownloadStopped(f"The download of {name} was stopped; the part already "
                                                  "downloaded is kept.")
                        if not chunk:
                            continue
                        f.write(chunk)
                        f.flush()  # what is written stays in the .part file when the download stops
                        have += len(chunk)
                        if have > size:
                            break
                        if progress:
                            progress(name, have, size)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            raise _Retry(e.__class__.__name__) from e

    def fetch_file(self, repo: str, commit: str, file: HubFile, dest: Path, progress=None,
                   attempts: int = 5) -> Tuple[Path, str]:
        """Downloads `file` of `repo` at `commit` to `dest` (resuming its .part file); returns the path and its
        sha256. A file with an LFS sha256 must match it; a small file without one is pinned by its size and commit,
        its sha256 computed after the download."""
        if not COMMIT.match(commit or ""):
            raise IntegrityError("No commit pinned; refusing to download.")
        if not isinstance(file.size, int) or file.size < 0:
            raise IntegrityError(f"The Hub lists no size for {file.name}; refusing to download it.")
        url = self.url(repo, commit, file.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_class(dest.with_name(f".{dest.name}.lock"), sleep=self._sleep):
            if dest.is_file() and dest.stat().st_size == file.size:
                digest = sha256_file(dest)
                if file.sha256 is None or digest_matches(digest, file.sha256):
                    return dest, digest  # complete and verified by an earlier run
            part = dest.with_name(dest.name + ".part")
            if file.size == 0:
                part.write_bytes(b"")
            for attempt in range(1, attempts + 1):
                have = part.stat().st_size if part.exists() else 0
                if have > file.size:
                    part.unlink()
                    have = 0
                if have == file.size and part.exists():
                    break
                try:
                    self._download_range(url, part, have, file.size, file.name, progress)
                    if part.exists() and part.stat().st_size >= file.size:
                        break
                except _Retry as e:
                    if self.logger:
                        self.logger.warning(f"Download of {file.name} interrupted ({e}), attempt {attempt}/{attempts}")
                if attempt == attempts:
                    raise self.error(f"Download of {file.name} did not complete after {attempts} attempts; start it "
                                     "again to resume.")
                self._sleep(min(2 ** attempt, 15))
            actual = part.stat().st_size
            digest = sha256_file(part)
            if actual != file.size or (file.sha256 is not None and not digest_matches(digest, file.sha256)):
                part.unlink(missing_ok=True)
                raise IntegrityError(f"{file.name} does not match what Hugging Face lists for commit {commit[:12]} "
                                     f"(expected {file.size} bytes" + (f", sha256 {file.sha256}" if file.sha256 else "")
                                     + f"; got {actual} bytes, sha256 {digest}). It was deleted and is not used.")
            os.replace(part, dest)
            return dest, digest


# --- disk space ----------------------------------------------------------------------------------------------------

_RESERVED: Dict[str, int] = {}
_RESERVED_LOCK = threading.Lock()


def reserve_bytes(total: int) -> int:
    """The reserve a file system keeps free: 2 GiB or 2 % of its size, whichever is larger (design 6.5; the one rule
    of disk_plan, shared with the build tools)."""
    from orchestrator.Core.disk_plan import reserve_for
    return reserve_for(int(total))


def standing_reserve_bytes(total: int) -> int:
    """The larger reserve a call under a standing approval must leave: 20 GiB or 10 %, whichever is larger (design
    6.5, disk_plan's rule); a call that does not leave it asks the person instead."""
    from orchestrator.Core.disk_plan import reserve_for
    return reserve_for(int(total), standing=True)


def disk_check(folder: Path, needed: int, ledger: Any = None) -> Dict[str, int]:
    """Free space, reserve and space other accepted calls reserved on the file system of `folder`: with `ledger`
    (disk_plan.ReservationLedger in the per-user folder) every process's downloads and builds, the same ledger builds
    use; without one only this process's downloads. `fits`: the normal reserve is left; `fits_standing`: the larger
    reserve of a standing approval is left too. The numbers are disk_plan.room's (the one disk rule); OSError when the
    disk cannot be measured."""
    from orchestrator.Core.disk_plan import room
    check = room(Path(folder), int(needed), ledger=ledger)
    if ledger is None:
        with _RESERVED_LOCK:
            check.reserved_by_others = sum(_RESERVED.values())
    # `after`: what stays free after this and every reservation; `tight`: it fits, but with less than twice the
    # reserve left (DiskCheck's verdict, a situation warning of the facts catalogue)
    return {"free": check.free, "reserve": check.reserve, "reserved": check.reserved_by_others,
            "needed": check.needed, "fits": check.fits, "after": check.after, "tight": check.verdict == "tight",
            "fits_standing": check.after >= standing_reserve_bytes(check.total)}


class Reservation:
    """Space reserved for one accepted download until it ends (in this process)."""

    def __init__(self, key: str, size: int):
        self.key = key
        with _RESERVED_LOCK:
            _RESERVED[key] = int(size)

    def release(self) -> None:
        with _RESERVED_LOCK:
            _RESERVED.pop(self.key, None)


class LedgerReservation:
    """Space reserved in the ledger every process of this user shares (disk_plan.ReservationLedger), until release()."""

    def __init__(self, ledger: Any, rid: str):
        self.ledger, self.rid = ledger, rid

    def release(self) -> None:
        self.ledger.release(self.rid)


def reserve_space(ledger: Any, folder: Path, size: int, owner: str, label: str, *, need_label: str, what: str,
                  key: str, standing: bool = False, disk_usage: Any = None, call: str = "the download",
                  estimate: bool = False):
    """The space an accepted call (a download, a package) will write into `folder`, held until it ends: in the ledger
    (checked again under its lock, so two calls prepared together cannot both take the same space; a call under a
    standing approval must leave the larger reserve), or, without a ledger or bytes, this process's Reservation under
    `key`. Released with release(). When the space is gone meanwhile: disk_plan.InsufficientSpace as the one disk
    rule refuses (disk_plan.refusal): `call` named ("the download", "the deployment package"), its size marked as an
    estimate with `estimate`, the plain next step in the message and as `next`, and the person's `steps`."""
    if ledger is None or not size:
        return Reservation(key, int(size))
    from orchestrator.Core.disk_plan import DiskNeed, InsufficientSpace, refusal
    try:
        rid, _ = ledger.reserve([DiskNeed(need_label, Path(folder), int(size), what)], owner, label,
                                standing=standing, disk_usage=disk_usage)
    except InsufficientSpace as e:
        raise (refusal(e.checks, call, estimate=estimate) or e) from None
    return LedgerReservation(ledger, rid)


# --- the person's own download (the GUI's Hugging Face browser, DECISIONS round 11 A6) -------------------------------

def file_at_commit(api: Any, repo: str, filename: str, token: Any = False,
                   revision: Optional[str] = None) -> Tuple[str, HubFile]:
    """The commit (`revision`, else the current one) of `repo` and its file `filename` with the size and LFS sha256 the
    Hub lists for that commit: what HubFetcher checks the download against. `token`: the person's token or False (never
    an implicit one). HubError when the repository, the commit or the file is not there or the Hub lists no size."""
    if not REPO_ID.match(repo or ""):
        raise HubError(f"'{repo}' is not a Hugging Face repository id (owner/name).", code="invalid_value")
    if not safe_filename(filename):
        raise HubError("That file name cannot be used as a file on this computer.", code="invalid_value")
    try:
        info = api.model_info(repo, revision=revision, files_metadata=True, token=token)
    except Exception as e:  # noqa: BLE001 - every failure is the person's error message
        raise hub_error(e, f"The lookup of {repo} failed", bool(token)) from None
    commit = str(getattr(info, "sha", "") or "")
    if not COMMIT.match(commit):
        raise HubError(f"The commit of {repo} could not be found out on Hugging Face; nothing was downloaded.")
    file = next((hub_file(s) for s in getattr(info, "siblings", None) or [] if str(s.rfilename) == filename), None)
    if file is None:
        raise HubError(f"{repo} has no file '{filename}' at commit {commit[:12]}.", code="unknown_id")
    if file.size is None:
        raise HubError(f"Hugging Face lists no size for {filename}; it is not downloaded.", code="invalid_value")
    return commit, file


def download_for_person(models_dir: Path, repo: str, filename: str, *, api: Any, token: Optional[str] = None,
                        ledger: Any = None, stop: Optional[threading.Event] = None,
                        progress: Optional[Callable[[str, int, int], None]] = None, owner: str = "gui",
                        disk_usage: Any = None) -> Path:
    """One file the person chose in the GUI's Hugging Face browser, through the same verified fetcher as the AI's
    download_hf_file (one code path): pinned to the commit looked up first (file_at_commit), into
    download_path(models_dir, ...), resumable from its `.part` file, checked against the Hub's size and sha256 before
    it gets its name, stopped between two chunks by `stop` (DownloadStopped; the part stays). The one disk rule comes
    first (Core/disk_plan room with the reserve and what other calls reserved in `ledger`): disk_plan.InsufficientSpace
    with its checks when the file does not fit, so the window shows disk_plan.person_reason in the person's language.
    While it runs the download holds its own reservation in the ledger (released when it ends, fails or stops), so a
    build or another download started meanwhile counts its space. Returns the file's path."""
    from orchestrator.Core.disk_plan import refusal, room
    commit, file = file_at_commit(api, repo, filename, token or False)
    dest = download_path(models_dir, repo, commit, filename)
    part = dest.with_name(dest.name + ".part")
    try:
        done = dest.is_file() and dest.stat().st_size == file.size
        have = part.stat().st_size if part.is_file() else 0
    except OSError:
        done, have = False, 0
    need = 0 if done else max(int(file.size) - have, 0)
    what = f"download {filename}"
    problem = refusal([room(Path(models_dir), need, label="models", what=what, ledger=ledger, disk_usage=disk_usage)],
                      "the download")
    if problem is not None:
        raise problem
    reservation = reserve_space(ledger, Path(models_dir), need, owner, f"{repo}/{filename}", need_label="models",
                                what=what, key=f"{repo}/{commit}/{filename}", disk_usage=disk_usage)
    try:
        path, _digest = HubFetcher(token=token or None, stop=stop).fetch_file(repo, commit, file, dest,
                                                                              progress=progress)
    finally:
        reservation.release()
    return path

