#!/usr/bin/env python3
"""
The portable model archive `llmcf-model/1` (docs/DESIGN.md, Hugging Face, the row "A model for a computer without a
network travels as a portable model archive").

One Hugging Face model at its exact commit, for a computer without a network: written on a computer with a network,
carried by the person, checked on import before anything uses it, and built from by repository and commit. It is the
person's own: never uploaded, never a pack, and no AI tool writes or takes one in.

- The file: a zip with fixed dates whose members are stored as they are: MANIFEST_NAME, SIGNATURE_NAME and exactly the
  files the manifest lists, at their paths in the repository.
- The manifest (ModelManifest): format, kind, repository and commit, creation time, the lowest llmcf version, the
  model's licence and gating (and whether the writer's account had passed the gate), the base models' licences and
  gating, every file with its size and sha256, and files_sha256 over that list (community_pack's canonical form).
- It reuses the pack format of Core/community_pack.py: check_relpath, the canonical JSON, the Ed25519 signature with
  its own purpose prefix (sign_model_archive), the person's own key (UserKeys), and the quarantine's rule that nothing
  of a failed check stays.
- plan_archive() looks the model up (hf_lookup.look_up, the Builder's file selection); write_archive() fetches the
  files into the Builder's own copy of the commit (HubFetcher), hashes them, signs the manifest and writes the zip
  through a .part file.
- prepare_take() checks an archive file in a quarantine folder of the store; finish_take() puts it in place after the
  person accepted its licence (actor gui or cli only); discard() removes the quarantine.
- stored() and copy_of() read the store (models/archives/<owner>--<name>/<commit>/); local_models.local_copy uses
  copy_of, so a build of repo@commit takes the archive's files without any request to Hugging Face.
- deletion_plan() and delete(): the person's accepted plan, into the trash or for good, audited.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence

from pydantic import Field, ValidationError, field_validator, model_validator

from orchestrator import __version__
from orchestrator.Core.community_pack import (RESERVED_NAMES as PACK_RESERVED, SHA256_PATTERN, UTC_TIME_PATTERN,
                                              VERSION_PATTERN, PackError, PackSignature, Strict, _canonical,
                                              _zip_info, check_relpath, key_id, sign_model_archive, utc_now,
                                              verify_model_archive)
from orchestrator.utils.atomic import remove_tree, replace_file
from orchestrator.utils.next_steps import WithSteps

FORMAT = "llmcf-model/1"
KIND = "model"
SUFFIX = ".llmcfmodel"
MANIFEST_NAME = "llmcf-model.json"
SIGNATURE_NAME = "llmcf-model.sig"
TAKEN_NAME = "taken.json"            # beside the model folder: when, by whom, the signer, the licence accepted
STORE = "archives"                   # below the models folder
MODEL_FOLDER = "model"
QUARANTINE = ".quarantine"
RESERVED_NAMES = frozenset({MANIFEST_NAME, SIGNATURE_NAME, TAKEN_NAME}) | PACK_RESERVED
# The archive's own limits (a pack's are for scripts and text, never weights).
MAX_FILES = 2000
MAX_FILE_BYTES = 1024 ** 4           # 1 TiB for one file
MAX_TOTAL_BYTES = 2 * 1024 ** 4      # 2 TiB in all
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SIGNATURE_BYTES = 4096
MAX_BASE_MODELS = 8
CHUNK = 8 * 1024 * 1024
ACTORS = ("gui", "cli")
REPO_PATTERN = r"^[A-Za-z0-9][\w.-]*/[\w.-]+$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_REPO = re.compile(REPO_PATTERN)
_COMMIT = re.compile(COMMIT_PATTERN)
_TEXT = r"^[^\x00-\x1f\x7f]*$"


class ArchiveError(WithSteps, ValueError):
    """A model archive that cannot be written, read or used; the message is for the person (never a token). `code`:
    invalid_value, mismatch, needs_person, exists, not_found, failed."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


# --- the manifest ------------------------------------------------------------------------------------------------

class ArchiveFile(Strict):
    path: str
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def _plain(cls, value: str) -> str:
        check_relpath(value)
        if any(part.startswith(".") for part in value.split("/")):
            raise ValueError(f"a hidden name is not carried: {value!r}")
        return value


class LicenceFacts(Strict):
    license: str = Field(max_length=200, pattern=_TEXT)
    license_class: Literal["permissive", "restrictive", "unknown"] = "unknown"
    gating: Literal["false", "auto", "manual", "unknown"] = "unknown"


class ModelLicence(LicenceFacts):
    gate_passed: bool = False         # the writer's account was let through the gate when the archive was written


class BaseModelLicence(LicenceFacts):
    repo: str = Field(max_length=200, pattern=_TEXT)


class ModelManifest(Strict):
    format: Literal["llmcf-model/1"] = FORMAT
    kind: Literal["model"] = KIND
    repo: str = Field(pattern=REPO_PATTERN, max_length=200)
    commit: str = Field(pattern=COMMIT_PATTERN)
    created: str = Field(pattern=UTC_TIME_PATTERN)
    lcf_min_version: str = Field(default=__version__, pattern=VERSION_PATTERN)
    licence: ModelLicence
    base_models: List[BaseModelLicence] = Field(default_factory=list, max_length=MAX_BASE_MODELS)
    files: List[ArchiveFile] = Field(min_length=1, max_length=MAX_FILES)
    files_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _consistent(self):
        seen, folders = set(), set()
        for f in self.files:
            key = f.path.casefold()   # one file on Windows and macOS
            if key in seen:
                raise ValueError(f"file listed twice: {f.path}")
            if key in {n.casefold() for n in RESERVED_NAMES}:
                raise ValueError(f"reserved name in the file list: {f.path}")
            seen.add(key)
            parts = key.split("/")
            folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
        if seen & folders:
            raise ValueError(f"a path is both a file and a folder: {sorted(seen & folders)[0]}")
        if sum(f.size for f in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("the files are larger than a model archive may be")
        self.files = sorted(self.files, key=lambda f: f.path)
        if files_digest(self.files) != self.files_sha256:
            raise ValueError("files_sha256 does not match the file list")
        return self

    def canonical_bytes(self) -> bytes:
        """The bytes the signature covers: sorted keys, no whitespace, UTF-8."""
        return _canonical(self.model_dump(mode="json"))

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def files_digest(files: Iterable[ArchiveFile]) -> str:
    """The one sha256 over the whole: the canonical JSON of the sorted file list (path, size, sha256)."""
    listed = sorted(files, key=lambda f: f.path)
    return hashlib.sha256(_canonical([f.model_dump(mode="json") for f in listed])).hexdigest()


def selected_files(names: Sequence[str]) -> List[str]:
    """What a conversion needs of a repository: the Builder's own selection (hf_lookup.staged_files: no GGUF, ONNX or
    TensorFlow copies, no pickle weights beside safetensors) without hidden files (.gitattributes and the like)."""
    from orchestrator.Core.hf_lookup import staged_files
    return [n for n in staged_files(list(names)) if not any(part.startswith(".") for part in n.split("/"))]


def convertible(names: Iterable[str]) -> bool:
    """A model folder a conversion can use: config.json and weights (local_models' rule)."""
    from orchestrator.Core.local_models import WEIGHT_SUFFIXES
    names = list(names)
    return "config.json" in names and any(n.lower().endswith(WEIGHT_SUFFIXES) for n in names)


def _sha256_file(path: Path, stop: Optional[threading.Event] = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if stop is not None and stop.is_set():
                raise ArchiveError("Stopped; nothing was written.", code="failed")
            chunk = fh.read(CHUNK)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


# --- folders -----------------------------------------------------------------------------------------------------

def models_dir(framework: Any) -> Path:
    from orchestrator.Core.disk_plan import folder
    return folder(framework, "models_dir", "models")


def store_root(models: Path) -> Path:
    """<models>/archives: the archives taken in on this computer."""
    return Path(models) / STORE


def archive_folder(models: Path, repo: str, commit: str) -> Path:
    owner, _, name = repo.partition("/")
    return store_root(models) / f"{owner}--{name}" / commit


# --- the person's own key ----------------------------------------------------------------------------------------

def _secrets(framework: Any):
    sm = getattr(framework, "secrets_manager", None)
    if sm is None and hasattr(framework, "get_component"):
        try:
            sm = framework.get_component("secrets_manager")
        except Exception:  # noqa: BLE001 - no secrets store: no key
            sm = None
    return sm


def signing_key(framework: Any):
    """The person's own Ed25519 key (community_pack.UserKeys, the key packs are signed with; made on first use)."""
    from orchestrator.Core.community_pack import UserKeys
    return UserKeys(_secrets(framework)).signing_key()


def own_public_key(framework: Any):
    """This computer's own signing key's public half when one exists, never making one; None otherwise."""
    from orchestrator.Core.community_pack import UserKeys
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import base64
    sm = _secrets(framework)
    if sm is None:
        return None
    try:
        stored = sm.get_secret(UserKeys.SIGNING_SECRET)
        if not stored:
            return None
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(str(stored).strip(), validate=True)).public_key()
    except Exception:  # noqa: BLE001 - unreadable: not known as this computer's
        return None


# --- writing on a computer with a network ------------------------------------------------------------------------

@dataclass
class ArchivePlan:
    """What write_archive() would write: the commit, the files with the Hub's sizes and checksums, the licences."""
    repo: str
    commit: str
    files: List[Any]                       # hf_hub.HubFile of every selected file
    licence: ModelLicence
    base_models: List[BaseModelLicence]
    needs_token: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(int(f.size or 0) for f in self.files)

    @property
    def ask_licence(self) -> bool:
        """A gated, restrictive or unknown licence of the model or a base model is shown and confirmed first."""
        facts = [self.licence] + list(self.base_models)
        return any(f.gating != "false" or f.license_class != "permissive" for f in facts)


def _licence_of(info: Any) -> LicenceFacts:
    from orchestrator.Core.hf_hub import gating
    from orchestrator.Core.hf_lookup import license_class
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    get = card.get if hasattr(card, "get") else (lambda k, d=None: getattr(card, k, d))
    raw = get("license", None)
    if raw is None:
        raw = next((t.split(":", 1)[1] for t in (getattr(info, "tags", None) or [])
                    if isinstance(t, str) and t.startswith("license:")), None)
    shown, klass = license_class(raw)
    return LicenceFacts(license=_clean(shown), license_class=klass, gating=gating(getattr(info, "gated", False)))


def _clean(text: Any) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "unknown"))[:200] or "unknown"


def _base_names(info: Any, repo: str) -> List[str]:
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    get = card.get if hasattr(card, "get") else (lambda k, d=None: getattr(card, k, d))
    base = get("base_model", None)
    bases = base if isinstance(base, (list, tuple)) else [base]
    return [str(b)[:200] for b in bases if isinstance(b, str) and b and b != repo]


def plan_archive(framework: Any, repo: str, revision: Optional[str] = None, *, api: Any = None) -> ArchivePlan:
    """Looks the model up on Hugging Face (offline mode refuses first): its commit (the revision's, or the default
    branch's now), the files a conversion needs (selected_files) with the Hub's sizes and checksums, and the licence
    and gating of the model and of every base model its card names. ArchiveError when it cannot be archived."""
    from orchestrator.Core.hf_hub import HubClient, hub_api, offline, offline_refusal, tokenless_first
    from orchestrator.Core.hf_lookup import HubLookupError, look_up
    if offline(getattr(framework, "config", None)):
        raise offline_refusal()
    repo = str(repo or "").strip()
    if not _REPO.match(repo) or ".." in repo:
        raise ArchiveError(f"'{repo}' is not a Hugging Face model id (owner/name).", code="invalid_value")
    token = HubClient(framework).token() if framework is not None else None
    factory = getattr(framework, "hf_api_factory", None)   # a stand-in framework's Hub (tests), as ModelManager's
    if api is None and callable(factory):
        api = factory(False)
    api_for = (lambda _t: api) if api is not None else hub_api
    try:
        facts = look_up(repo, revision or None, token, api=api)
    except HubLookupError as e:
        raise ArchiveError(str(e), code=e.code, next=e.next) from None
    if not facts.commit:
        raise ArchiveError(f"The commit of {repo} could not be found out on Hugging Face; nothing was written.")
    names = selected_files([f.name for f in facts.files])
    if not convertible(names):
        raise ArchiveError(f"{repo} at commit {facts.commit[:12]} has no config.json and weights a conversion could "
                           "use; nothing was written.", code="invalid_value")
    by_name = {f.name: f for f in facts.files}
    files = [by_name[n] for n in names]
    if any(not isinstance(f.size, int) or f.size < 0 for f in files):
        raise ArchiveError(f"Hugging Face lists no size for a file of {repo}; nothing was written.")
    try:
        info = tokenless_first(lambda t: api_for(t).model_info(repo, revision=facts.commit, token=t), token or None)
        model = _licence_of(info)
        bases_named = _base_names(info, repo)
    except Exception:  # noqa: BLE001 - the facts look_up read: its licence and gating
        model = LicenceFacts(license=_clean(facts.license), license_class=facts.license_class, gating=facts.gated)
        bases_named = [facts.base_model] if facts.base_model else []
    bases = []
    for name in bases_named[:MAX_BASE_MODELS]:
        try:
            base_info = tokenless_first(lambda t, n=name: api_for(t).model_info(n, token=t), token or None)
            found = _licence_of(base_info)
            bases.append(BaseModelLicence(repo=_clean(name), **found.model_dump()))
        except Exception:  # noqa: BLE001 - not looked up: said as unknown
            bases.append(BaseModelLicence(repo=_clean(name), license="unknown", license_class="unknown",
                                          gating="unknown"))
    passed = model.gating != "false" and facts.token_access == "yes"
    return ArchivePlan(repo, facts.commit, files, ModelLicence(**model.model_dump(), gate_passed=passed), bases,
                       needs_token=facts.gated != "false" or facts.token_access == "yes")


@dataclass
class WrittenArchive:
    path: Path
    sha256: str
    size_bytes: int
    key_id: str
    manifest: ModelManifest


def _copy_folder(framework: Any, repo: str, commit: str) -> Path:
    """The Builder's own copy of the commit (BuildEngine.staging_folder): the online computer can build from it too."""
    from orchestrator.Core.disk_plan import folder
    owner, _, name = repo.partition("/")
    return folder(framework, "cache_dir", "cache") / "models" / "hf" / f"{owner}--{name}" / commit


def _ledger(framework: Any):
    try:
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.disk_plan import ReservationLedger
        folder = state_dir(framework)
        return ReservationLedger(folder) if folder is not None else None
    except Exception:  # noqa: BLE001 - no per-user folder: the disk alone
        return None


def write_archive(framework: Any, plan: ArchivePlan, dest: Path, *, licence_confirmed: bool = False,
                  key: Any = None, fetcher: Any = None, stop: Optional[threading.Event] = None,
                  progress: Optional[Callable[[str, int, int], None]] = None,
                  disk_usage: Any = None) -> WrittenArchive:
    """Writes the archive of `plan` to `dest` (a new file): the files fetched into the Builder's copy of the commit
    (each checked against the Hub's size and sha256, a complete copy used as it is), hashed, the manifest signed with
    the person's own key (`key` stands in for it in tests), the zip written into dest's .part file with every file
    hashed again and renamed when complete. A licence that plan.ask_licence marks must be confirmed by the person
    first (`licence_confirmed`). `progress(stage, done, total)`: fetch, hash, write."""
    from orchestrator.Core.disk_plan import refusal, room
    from orchestrator.Core.hf_hub import HubClient, HubFetcher, offline, offline_refusal
    from orchestrator.Core.local_models import write_complete
    from orchestrator.utils.download import FileLock
    if offline(getattr(framework, "config", None)):
        raise offline_refusal()
    if plan.ask_licence and not licence_confirmed:
        raise ArchiveError(f"The licence of {plan.repo} must be shown to the person and confirmed before the archive "
                           "is written.", code="needs_person")
    dest = Path(dest).expanduser()
    if dest.suffix != SUFFIX:
        dest = dest.with_name(dest.name + SUFFIX)
    if os.path.lexists(dest):
        raise ArchiveError(f"{dest} exists already; choose another file name.", code="exists")
    if not dest.parent.is_dir():
        raise ArchiveError(f"The folder {dest.parent} does not exist.", code="invalid_value")
    stop = stop or threading.Event()
    copy = _copy_folder(framework, plan.repo, plan.commit)
    need = 0
    for f in plan.files:
        target = copy / f.name
        if not (target.is_file() and target.stat().st_size == f.size):
            need += int(f.size or 0)
    ledger = _ledger(framework)
    checks = [room(dest.parent, plan.total_bytes, label="archive", what="write the model archive", ledger=ledger,
                   disk_usage=disk_usage)]
    if need:
        checks.append(room(copy, need, label="models",
                           what="download the model for its archive", ledger=ledger, disk_usage=disk_usage))
    problem = refusal(checks, "the model archive")
    if problem is not None:
        raise problem
    token = HubClient(framework).token() if framework is not None else None
    fetcher = fetcher or HubFetcher(token if plan.needs_token else None, stop, token_needed=plan.needs_token)
    copy.mkdir(parents=True, exist_ok=True)
    total = len(plan.files)
    with FileLock(copy.parent / f".{copy.name}.lock"):
        for i, f in enumerate(plan.files):
            if progress:
                progress("fetch", i, total)
            fetcher.fetch_file(plan.repo, plan.commit, f, copy / f.name)
        try:   # every file is there: the Builder's copy is complete (local_models.local_copy)
            write_complete(copy, plan.repo, plan.commit, {f.name: f.size for f in plan.files})
        except OSError:
            pass
        entries = []
        for i, f in enumerate(plan.files):
            if progress:
                progress("hash", i, total)
            path = copy / f.name
            if path.is_symlink() or not path.is_file():
                raise ArchiveError(f"{f.name} is not a plain file in the Builder's copy; nothing was written.")
            entries.append(ArchiveFile(path=f.name, size=path.stat().st_size, sha256=_sha256_file(path, stop)))
        manifest = ModelManifest(repo=plan.repo, commit=plan.commit, created=utc_now(), licence=plan.licence,
                                 base_models=plan.base_models, files=entries, files_sha256=files_digest(entries))
        private = key if key is not None else signing_key(framework)
        signature = sign_model_archive(manifest.canonical_bytes(), private)
        part = dest.with_name(dest.name + ".part")
        try:
            _write_zip(part, copy, manifest, signature, stop, progress)
            replace_file(part, dest)
        except BaseException:
            try:
                part.unlink()
            except OSError:
                pass
            raise
    digest = _sha256_file(dest)
    return WrittenArchive(dest, digest, dest.stat().st_size, signature.key_id, manifest)


def _write_zip(part: Path, root: Path, manifest: ModelManifest, signature: PackSignature,
               stop: threading.Event, progress) -> None:
    total = len(manifest.files)
    if part.is_file() and not part.is_symlink():
        part.unlink()   # the .part file of an earlier attempt that stopped
    with open(part, "xb") as raw, zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr(_zip_info(MANIFEST_NAME, 0o644, zipfile.ZIP_STORED),
                    json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False))
        zf.writestr(_zip_info(SIGNATURE_NAME, 0o644, zipfile.ZIP_STORED), signature.model_dump_json())
        for i, f in enumerate(manifest.files):
            if progress:
                progress("write", i, total)
            info = _zip_info(f.path, 0o644, zipfile.ZIP_STORED)
            info.file_size = f.size
            digest = hashlib.sha256()
            with open(root.joinpath(*f.path.split("/")), "rb") as fh, \
                    zf.open(info, "w", force_zip64=f.size >= 0x7FFFFFFF) as out:
                while True:
                    if stop.is_set():
                        raise ArchiveError("Stopped; nothing was written.")
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != f.sha256:
                raise ArchiveError(f"{f.path} changed while the archive was written; nothing was written.",
                                   code="mismatch")


# --- taking an archive in ----------------------------------------------------------------------------------------

@dataclass
class PreparedArchive:
    """An archive checked in quarantine, waiting for the person's licence decision (finish_take or discard)."""
    manifest: ModelManifest
    signature: PackSignature
    signer: str
    own_key: bool
    trusted_key: bool
    quarantine: Path
    source: str
    archive_sha256: str
    destination: Path

    def summary(self, lang: str = "en") -> str:
        return summary_text(self, lang)


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ArchiveError(f"{info.filename} is too large.", code="invalid_value")
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except Exception as e:  # noqa: BLE001 - damaged member
        raise ArchiveError(f"{info.filename} cannot be read: {e}", code="invalid_value") from None
    if len(data) > limit:
        raise ArchiveError(f"{info.filename} is too large.", code="invalid_value")
    return data


def read_manifest(zf: zipfile.ZipFile) -> ModelManifest:
    infos = {i.filename: i for i in zf.infolist()}
    info = infos.get(MANIFEST_NAME)
    if info is None:
        raise ArchiveError(f"Not a model archive: it has no {MANIFEST_NAME}.", code="invalid_value")
    try:
        return ModelManifest.model_validate_json(_read_member(zf, info, MAX_MANIFEST_BYTES))
    except ValidationError as e:
        raise ArchiveError(f"The archive's manifest is not valid: {_first_error(e)}", code="invalid_value") from None


def _first_error(e: ValidationError) -> str:
    errors = e.errors()
    if not errors:
        return str(e)[:300]
    first = errors[0]
    where = ".".join(str(p) for p in first.get("loc", ()))
    return f"{where}: {first.get('msg')}"[:300]


def _check_layout(zf: zipfile.ZipFile, manifest: ModelManifest) -> None:
    """Exactly the manifest, the signature and the listed files; no member twice, no link, folder or encrypted
    member, only stored or deflated members, each the listed size."""
    members = zf.infolist()
    names = [i.filename for i in members]
    problems = []
    if len(set(names)) != len(names) or len({n.casefold() for n in names}) != len(names):
        problems.append("a member is listed twice")
    listed = {f.path: f for f in manifest.files}
    for info in members:
        name = info.filename
        mode = info.external_attr >> 16
        if name.endswith("/") or info.is_dir():
            problems.append(f"{name}: a folder member")
        elif stat.S_ISLNK(mode):
            problems.append(f"{name}: symbolic link")
        elif mode and not stat.S_ISREG(mode):
            problems.append(f"{name}: special file")
        elif info.flag_bits & 0x1:
            problems.append(f"{name}: encrypted")
        elif info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            problems.append(f"{name}: compressed with a method that is not read here")
        elif name in (MANIFEST_NAME, SIGNATURE_NAME):
            continue
        elif name not in listed:
            problems.append(f"{name}: not in the manifest")
        elif info.file_size != listed[name].size:
            problems.append(f"{name}: size differs from the manifest")
    problems += [f"{n}: missing" for n in sorted(set(listed) - set(names))]
    if problems:
        raise ArchiveError("The archive does not match its manifest:\n- " + "\n- ".join(problems[:20]),
                           code="mismatch")


def prepare_take(framework: Any, path: Path, *, sha256: str = "", trusted: Sequence[Any] = (),
                 stop: Optional[threading.Event] = None, progress: Optional[Callable[[str, int, int], None]] = None,
                 models: Optional[Path] = None, disk_usage: Any = None) -> PreparedArchive:
    """Checks the archive file `path` into a quarantine folder of the store, before anything uses it: its size and
    (when given) its sha256, the zip's layout, the manifest, the signature (required; with `trusted` public keys, one
    of them must have signed it), then every file streamed out with its sha256 checked. On any problem the quarantine
    is removed and ArchiveError (or the disk rule's refusal) is raised; nothing stays."""
    from orchestrator.Core.disk_plan import refusal, room
    path = Path(path).expanduser()
    stop = stop or threading.Event()
    try:
        st = os.stat(path)
    except OSError:
        raise ArchiveError(f"{path} cannot be read.", code="not_found") from None
    if not stat.S_ISREG(st.st_mode):
        raise ArchiveError(f"{path} is not a file.", code="invalid_value")
    if st.st_size > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + 64 * 1024 * 1024:
        raise ArchiveError(f"{path.name} is larger than a model archive may be.", code="invalid_value")
    digest = _sha256_file(path, stop)
    wanted = str(sha256 or "").strip().lower()
    if wanted and wanted != digest:
        raise ArchiveError(f"{path.name} has sha256 {digest}, not the {wanted} you gave; it is not taken in.",
                           code="mismatch")
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ArchiveError(f"Not a model archive (zip file): {e}", code="invalid_value") from None
    models = Path(models) if models is not None else models_dir(framework)
    with zf:
        manifest = read_manifest(zf)
        _check_layout(zf, manifest)
        sig_info = zf.getinfo(SIGNATURE_NAME) if SIGNATURE_NAME in zf.namelist() else None
        if sig_info is None:
            raise ArchiveError("The archive is not signed; an unsigned model archive is not taken in.",
                               code="mismatch")
        try:
            signature = PackSignature.model_validate_json(_read_member(zf, sig_info, MAX_SIGNATURE_BYTES))
            carried = signature.embedded_key()
        except (ValidationError, PackError, ValueError) as e:
            raise ArchiveError(f"The archive's signature cannot be read: {e}", code="mismatch") from None
        if carried is None:
            raise ArchiveError("The archive's signature carries no public key, so it cannot be checked.",
                               code="mismatch")
        try:
            signer = verify_model_archive(manifest.canonical_bytes(), signature, [carried])
        except PackError as e:
            raise ArchiveError(str(e), code="mismatch") from None
        trusted = list(trusted or ())
        if trusted and signer not in {key_id(k) for k in trusted}:
            raise ArchiveError(f"The archive is signed with key {signer}, which is not one of the keys you gave.",
                               code="mismatch")
        own = own_public_key(framework)
        destination = archive_folder(models, manifest.repo, manifest.commit)
        if os.path.lexists(destination):
            raise ArchiveError(f"The archive of {manifest.repo} at commit {manifest.commit[:12]} is taken in already.",
                               code="exists")
        root = store_root(models)
        problem = refusal([room(root, manifest.total_bytes, label="models", what="take in the model archive",
                                ledger=_ledger(framework), disk_usage=disk_usage)], "the model archive")
        if problem is not None:
            raise problem
        holder = root / QUARANTINE
        holder.mkdir(parents=True, exist_ok=True)
        quarantine = Path(tempfile.mkdtemp(prefix=f"{manifest.commit[:12]}.", dir=holder))
        try:
            _extract(zf, manifest, quarantine / MODEL_FOLDER, stop, progress)
            (quarantine / MANIFEST_NAME).write_bytes(
                json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8"))
            (quarantine / SIGNATURE_NAME).write_bytes(signature.model_dump_json().encode("utf-8"))
        except BaseException:
            remove_tree(quarantine)
            raise
    return PreparedArchive(manifest, signature, signer, own is not None and key_id(own) == signer,
                           bool(trusted), quarantine, path.name, digest, destination)


def _extract(zf: zipfile.ZipFile, manifest: ModelManifest, dest: Path, stop: threading.Event, progress) -> None:
    dest.mkdir(parents=True)
    total = len(manifest.files)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for i, f in enumerate(manifest.files):
        if progress:
            progress("check", i, total)
        target = dest.joinpath(*f.path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        try:
            with zf.open(f.path) as src, os.fdopen(os.open(target, flags, 0o644), "wb") as out:
                while True:
                    if stop.is_set():
                        raise ArchiveError("Stopped; nothing was taken in.")
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > f.size:
                        raise ArchiveError(f"{f.path}: larger than the manifest says", code="mismatch")
                    digest.update(chunk)
                    out.write(chunk)
        except (zipfile.BadZipFile, OSError, EOFError) as e:   # a damaged member (its CRC) or a full disk
            raise ArchiveError(f"{f.path} cannot be extracted: {e}", code="mismatch") from None
        if size != f.size or digest.hexdigest() != f.sha256:
            raise ArchiveError(f"{f.path}: content differs from the manifest; nothing was taken in.", code="mismatch")


def finish_take(prepared: PreparedArchive, *, actor: str, licence_accepted: bool) -> Path:
    """Puts a checked archive in place after the person accepted its licence: only the person's own windows (actor gui
    or cli), never an AI. The quarantine folder is renamed into the store; an archive of the same commit already there
    is refused."""
    if actor not in ACTORS:
        raise PermissionError("Only the person takes a model archive in and accepts its licence, in the GUI or at a "
                              "terminal.")
    if not licence_accepted:
        discard(prepared)
        raise ArchiveError("The licence was not accepted; the archive was not taken in.", code="needs_person")
    record = {"taken": utc_now(), "actor": actor, "signer": prepared.signer, "own_key": prepared.own_key,
              "source": prepared.source[:200], "archive_sha256": prepared.archive_sha256, "licence_accepted": True}
    (prepared.quarantine / TAKEN_NAME).write_bytes(json.dumps(record, indent=2, sort_keys=True).encode("utf-8"))
    destination = prepared.destination
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(destination):
            raise ArchiveError(f"The archive of {prepared.manifest.repo} at commit {prepared.manifest.commit[:12]} is "
                               "taken in already.", code="exists")
        os.rename(prepared.quarantine, destination)
    except BaseException:
        discard(prepared)
        raise
    return destination


def discard(prepared: PreparedArchive) -> None:
    """Removes a prepared archive's quarantine folder (the person said no, or it failed)."""
    if prepared.quarantine.exists():
        remove_tree(prepared.quarantine)


def take_archive(framework: Any, path: Path, *, actor: str, decide: Callable[[PreparedArchive], bool],
                 sha256: str = "", trusted: Sequence[Any] = (), models: Optional[Path] = None,
                 stop: Optional[threading.Event] = None, progress=None) -> Optional[Path]:
    """prepare_take, then the person's decision on the summary (`decide`, which shows the licence), then finish_take
    or discard. None when the person said no."""
    prepared = prepare_take(framework, path, sha256=sha256, trusted=trusted, models=models, stop=stop,
                            progress=progress)
    try:
        accepted = bool(decide(prepared))
    except BaseException:
        discard(prepared)
        raise
    if not accepted:
        discard(prepared)
        return None
    return finish_take(prepared, actor=actor, licence_accepted=True)


# --- the store ---------------------------------------------------------------------------------------------------

@dataclass
class StoredArchive:
    repo: str
    commit: str
    folder: Path
    manifest: ModelManifest
    taken: Dict[str, Any] = field(default_factory=dict)

    @property
    def model_folder(self) -> Path:
        return self.folder / MODEL_FOLDER

    @property
    def size_bytes(self) -> int:
        return self.manifest.total_bytes

    @property
    def label(self) -> str:
        return f"{self.repo}@{self.commit}"

    def complete(self) -> bool:
        """Every listed file is there with its size and nothing else is (the quick check before every use; the full
        sha256 check ran when it was taken in)."""
        listed = {f.path: f.size for f in self.manifest.files}
        found = {}
        root = self.model_folder
        try:
            if root.is_symlink() or not root.is_dir():
                return False
            for current, dirs, files in os.walk(root):
                for d in dirs:
                    if os.path.islink(os.path.join(current, d)):
                        return False
                for name in files:
                    full = Path(current) / name
                    if full.is_symlink():
                        return False
                    found[full.relative_to(root).as_posix()] = full.stat().st_size
        except OSError:
            return False
        return found == listed


def _read_stored(folder: Path) -> Optional[StoredArchive]:
    try:
        manifest = ModelManifest.model_validate_json((folder / MANIFEST_NAME).read_bytes())
    except (OSError, ValidationError, ValueError):
        return None
    if archive_folder(folder.parents[2], manifest.repo, manifest.commit) != folder:
        return None   # moved by hand: not the folder of its own repository and commit
    try:
        taken = json.loads((folder / TAKEN_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        taken = {}
    return StoredArchive(manifest.repo, manifest.commit, folder, manifest, taken if isinstance(taken, dict) else {})


def stored(models: Path) -> List[StoredArchive]:
    """Every archive taken in on this computer (readable manifests only), newest first."""
    root = store_root(models)
    found: List[StoredArchive] = []
    try:
        repos = [d for d in root.iterdir() if d.is_dir() and not d.is_symlink() and "--" in d.name
                 and not d.name.startswith(".")]
    except OSError:
        return found
    for repo_dir in repos:
        try:
            commits = [d for d in repo_dir.iterdir() if d.is_dir() and not d.is_symlink() and _COMMIT.match(d.name)]
        except OSError:
            continue
        for folder in commits:
            entry = _read_stored(folder)
            if entry is not None:
                found.append(entry)
    found.sort(key=lambda a: str(a.taken.get("taken") or ""), reverse=True)
    return found


def copy_of(models: Path, repo: str, commit: Optional[str] = None) -> Optional[StoredArchive]:
    """The complete archive copy of `repo` (at `commit`, a 40-character commit; else the newest), or None. Reads the
    disk only."""
    if not _REPO.match(repo or "") or ".." in repo:
        return None
    wanted = commit if commit and _COMMIT.match(commit) else None
    if wanted:
        entry = _read_stored(archive_folder(models, repo, wanted))
        return entry if entry is not None and entry.repo == repo and entry.complete() else None
    return _newest(models, repo)


def _newest(models: Path, repo: str) -> Optional[StoredArchive]:
    for entry in stored(models):
        if entry.repo == repo and entry.complete():
            return entry
    return None


# --- the person's deletion ---------------------------------------------------------------------------------------

def deletion_plan(models: Path):
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    items = [DeletionItem(kind="model_archive", speaking_name=f"Model archive of {a.repo} at {a.commit[:12]}",
                          full_path=str(a.folder), file_name=a.folder.name, size_bytes=a.size_bytes,
                          what=(f"the files of {a.repo} at commit {a.commit[:12]} taken in from a model archive "
                                f"{str(a.taken.get('taken') or '')[:10]}; a build of this commit no longer finds "
                                "them")[:300])
             for a in stored(models)]
    return plan_of(items, ("trash", "permanent"))


def delete(models: Path, accepted: Any, trash: Any = None, audit: Any = None):
    """Deletes exactly the archives the person accepted (Core/deletion.py): only the person, the list built again
    first, audited before anything goes, into the trash or for good."""
    from orchestrator.Core.deletion import DeletionResult, note_kept, require_person, require_same, same_items
    accepted = require_person(accepted)
    if accepted.actor not in ACTORS:
        raise PermissionError("Only the person deletes model archives, in the GUI or at a terminal.")
    require_same(accepted, same_items(deletion_plan(models), accepted.plan), "the list of model archives")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; nothing was deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    names = [i.speaking_name for i in accepted.plan.items]
    try:
        audit.record(accepted.actor, "model_archives", "framework", "person", "started", approved=True,
                     arguments={"entries": names, "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                     detail=f"{len(names)} model archives "
                            + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
    result = DeletionResult()
    root = store_root(models)
    for item in accepted.plan.items:
        path = Path(item.full_path)
        try:
            if accepted.mode == "trash":
                entry = trash.move("model_archive", path, root, "models", item.speaking_name, accepted,
                                   accepted.actor)
                result.trash_ids.append(entry.id)
            else:
                if note_kept(result, item.speaking_name, path, remove_tree(path)):
                    continue
                result.freed_bytes += item.size_bytes
        except Exception as e:  # noqa: BLE001 - kept and reported
            result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
            continue
        result.done.append(item.speaking_name)
    return result


# --- what the person reads ---------------------------------------------------------------------------------------

def _licence_line(facts: LicenceFacts, lang: str) -> str:
    from orchestrator.utils.localization import ui_text
    return ui_text("models.archive.licence_line", lang, license=facts.license,
                   klass=ui_text(f"models.archive.class.{facts.license_class}", lang),
                   gating=ui_text(f"models.archive.gating.{facts.gating}", lang))


def licence_text(licence: ModelLicence, bases: Sequence[BaseModelLicence], lang: str = "en") -> str:
    """The licence and gating of the model and of its base models, as the person reads them before a write or a
    take-in."""
    from orchestrator.utils.localization import ui_text
    lines = [ui_text("models.archive.licence", lang, line=_licence_line(licence, lang))]
    if licence.gating != "false":
        lines.append(ui_text("models.archive.gate_passed" if licence.gate_passed else "models.archive.gate_unknown",
                             lang))
    for base in bases:
        lines.append(ui_text("models.archive.base", lang, repo=base.repo, line=_licence_line(base, lang)))
    return "\n".join(lines)


def plan_text(plan: ArchivePlan, lang: str = "en") -> str:
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    return "\n".join([ui_text("models.archive.plan", lang, repo=plan.repo, commit=plan.commit, count=len(plan.files),
                              size=format_size(plan.total_bytes, lang)),
                      licence_text(plan.licence, plan.base_models, lang)])


def written_text(written: WrittenArchive, lang: str = "en") -> str:
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    return ui_text("models.archive.written", lang, file=str(written.path), size=format_size(written.size_bytes, lang),
                   sha256=written.sha256, key_id=written.key_id, files_sha256=written.manifest.files_sha256)


def summary_text(prepared: PreparedArchive, lang: str = "en") -> str:
    """What the person reads before accepting the licence: repository, commit, files, size, signer, licences."""
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    m = prepared.manifest
    signer = ui_text("models.archive.signer.own" if prepared.own_key else
                     "models.archive.signer.given" if prepared.trusted_key else "models.archive.signer.other",
                     lang, key_id=prepared.signer)
    return "\n".join([ui_text("models.archive.summary", lang, repo=m.repo, commit=m.commit, count=len(m.files),
                              size=format_size(m.total_bytes, lang), created=m.created, sha256=prepared.archive_sha256,
                              files_sha256=m.files_sha256),
                      signer, licence_text(m.licence, m.base_models, lang),
                      ui_text("models.archive.accept", lang)])


__all__ = ["ArchiveError", "ArchiveFile", "ArchivePlan", "FORMAT", "KIND", "MANIFEST_NAME", "ModelManifest",
           "PreparedArchive", "SIGNATURE_NAME", "STORE", "SUFFIX", "StoredArchive", "WrittenArchive", "copy_of",
           "delete", "deletion_plan", "discard", "files_digest", "finish_take", "licence_text", "plan_archive",
           "plan_text", "prepare_take", "selected_files", "store_root", "stored", "summary_text", "take_archive",
           "write_archive", "written_text"]
