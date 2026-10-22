#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Manager (the model zoo)

Community packs (targets, recipes, knowledge; format in community_pack.py) are listed from the community index,
installed through a quarantine and exported from your own work for a pull request.

Trust root: index.json plus index.json.sig, signed by a key from configs/community_keys.yml. An index whose
signature does not verify is not used (fail closed). The last verified index is cached in the per-user folder
and is never replaced by an older one (rollback). Author signatures in packs are provenance only: the author's
public key is pinned on first use and a change is shown.

Install: download pinned by the index (size and sha256), extract into <install folder>/.quarantine/ (exactly the
listed files, every hash checked), validators (target: validate_target_folder; recipe: schema and the target's
capabilities; knowledge: shape), a summary with the trust level for the user to confirm, then an atomic rename
into place with .lcf-provenance.json. Anything that fails is removed.

Trust levels: verified and community (signed index), local (a file without a signed index entry: always
confirmed, a warning on every build) and revoked (the signed revoked list: install blocked; installed copies are
flagged by zoo list and verify, doctor and the hub, and never built). Shipped targets carry no provenance and are
not packs.

The framework never uploads anything and never asks for or reads a repository token: a contribution is handed to
the browser or to an installed GitHub CLI, which keeps its own credentials.

Knowledge leaves this computer only as the licences allow (eighth round, Q-1 and Q-1'; share_pages): a page goes into
a knowledge pack, or into a target's knowledge snapshot, with its full text only when its licence allows passing the
text on, and keeps the attribution its licence requires. A page with a copyright notice and no such licence, code
without a licence, and a page whose licence is unknown go out as their address and short facts; the recipient crawls
them under their own checks. The export lists every page with what it carries and why.

A pack that carries pages in full under a licence that asks for its text or permission notice (MIT, BSD, ISC,
Apache-2.0, MPL-2.0, CC BY, CC BY-SA) ships, next to its knowledge file, LICENSES/<SPDX id>.txt with the canonical
text of each such licence and a NOTICE listing every page with its source address, its copyright line as found and
its licence (rag/pack_licences.py). The install, `zoo verify` and verify_file check them; the canonical texts are never
changed by the privacy scrub.
"""

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

import yaml
from packaging.version import InvalidVersion, Version
from pydantic import Field, ValidationError, model_validator

from orchestrator.Core.community_pack import (ID_PATTERN, LICENSE_PATTERN, MAX_PACK_BYTES, PACK_KINDS,
                                              PROVENANCE_NAME, SHA256_PATTERN, UTC_TIME_PATTERN, VERSION_PATTERN,
                                              Kind, PackError, PackManifest, PackSignature, Strict, UserKeys,
                                              build_manifest, decrypt_pack, encrypt_pack, is_encrypted_pack,
                                              list_files, load_trusted_keys, read_manifest, read_pack, scrub,
                                              sign_manifest, tree_sha256, utc_now, verify_index, verify_manifest,
                                              write_pack)
from orchestrator.rag import licence_texts, licences, pack_licences
from orchestrator.rag.licences import FULL_TEXT, LINK_AND_FACTS, SharePlan
from orchestrator.utils.download import DownloadError, PinnedDownloader, sha256_file
from orchestrator.utils.file_lock import file_lock
from orchestrator.utils.helpers import ensure_directory, redact_secrets
from orchestrator.utils.logging import get_logger

KINDS = PACK_KINDS
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
INDEX_MAX_BYTES = 8 * 1024 * 1024
# A cached index older than this is fetched again when it is used, so revocations reach every user.
INDEX_MAX_AGE = 24 * 3600
KNOWLEDGE_FILE = "knowledge.json"
RECIPE_FILE = "recipe.yml"


class TrustLevel(str, Enum):
    VERIFIED = "verified"    # signed index: reviewed, reproduced independently
    COMMUNITY = "community"  # signed index: reviewed
    LOCAL = "local"          # a file without a signed index entry
    REVOKED = "revoked"      # in the signed revoked list


class IndexEntry(Strict):
    kind: Kind
    id: str = Field(pattern=ID_PATTERN)
    version: str = Field(pattern=VERSION_PATTERN)
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    author: str = Field(min_length=1, max_length=100)
    license: str = Field(pattern=LICENSE_PATTERN)
    trust: Literal["verified", "community"] = "community"
    url: str = Field(pattern=r"^https://\S+$", max_length=1000)
    size: int = Field(gt=0, le=MAX_PACK_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)


class Revocation(Strict):
    """A revoked pack: by its sha256, or by kind and id (one version, or every version)."""
    kind: Kind
    id: str = Field(pattern=ID_PATTERN)
    version: Optional[str] = Field(default=None, pattern=VERSION_PATTERN)
    sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    reason: str = Field(min_length=1, max_length=300)

    def matches(self, kind: str, pack_id: str, version: Optional[str], sha256: Optional[str]) -> bool:
        if self.sha256 is not None:
            return self.sha256 == sha256
        return (self.kind == kind and self.id.casefold() == pack_id.casefold()
                and self.version in (None, version))


def _version_key(version: str):
    try:
        return Version(version)
    except InvalidVersion:
        return Version("0")


class CommunityIndex(Strict):
    schema_version: Literal[1] = 1
    version: int = Field(ge=1)  # grows with every signed index
    generated: str = Field(pattern=UTC_TIME_PATTERN)
    entries: List[IndexEntry] = Field(default_factory=list, max_length=20_000)
    revoked: List[Revocation] = Field(default_factory=list, max_length=20_000)

    @model_validator(mode="after")
    def _unique(self):
        keys = [(e.kind, e.id.casefold(), e.version) for e in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("an entry is listed twice")
        return self

    def revocation(self, kind: str, pack_id: str, version: Optional[str], sha256: Optional[str]) -> Optional[Revocation]:
        return next((r for r in self.revoked if r.matches(kind, pack_id, version, sha256)), None)

    def latest(self, kind: str, pack_id: str) -> Optional[IndexEntry]:
        found = [e for e in self.entries if e.kind == kind and e.id.casefold() == pack_id.casefold()]
        return max(found, key=lambda e: _version_key(e.version)) if found else None

    def by_sha256(self, sha256: str) -> Optional[IndexEntry]:
        return next((e for e in self.entries if e.sha256 == sha256), None)


class Provenance(Strict):
    """.lcf-provenance.json next to an installed pack."""
    kind: Kind
    id: str = Field(pattern=ID_PATTERN)
    version: str = Field(pattern=VERSION_PATTERN)
    trust: Literal["verified", "community", "local"]
    source: str = Field(max_length=1000)
    pack_sha256: str = Field(pattern=SHA256_PATTERN)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    index_version: Optional[int] = None
    author: str = Field(max_length=100)
    signer: Optional[str] = None   # author key id (provenance only)
    sender: Optional[str] = None   # key id of the sender of an encrypted pack
    installed_at: str
    # "grant:<id>" or "delegation:<id>" when an AI client installed it without the person answering the question
    # (MCP add_pack_from_index under a standing approval or a delegation): builds of it stay outside every build
    # standing approval until the person confirmed one build of it.
    installed_via: Optional[str] = Field(default=None, pattern=r"^(grant:g_|delegation:d_)[0-9a-f]{16}$")


@dataclass
class IndexState:
    index: CommunityIndex
    source: str     # "fetched" or "cached"
    note: str = ""
    signer: str = ""  # key id of the trusted key that verified a fetched index ("" for a cached one)


@dataclass
class ZooItem:
    kind: str
    id: str
    version: str
    title: str
    author: str
    license: str
    trust: TrustLevel
    installed_version: Optional[str] = None
    revoked: Optional[str] = None  # reason
    path: Optional[Path] = None
    entry: Optional[IndexEntry] = None
    description: str = ""


@dataclass
class ZooDetails:
    """One zoo entry as `llm-cli zoo show` and the Community Hub's Details... show it: the item (licence, trust and
    revocation, installed version and folder, download URL, size and sha256, description) and an installed recipe's
    recipe.yml as written."""
    item: ZooItem
    recipe_text: Optional[str] = None

    def lines(self) -> List[str]:
        i = self.item
        lines = [f"{i.kind} {i.id} {i.version} by {i.author} ({i.license or 'licence unknown'})",
                 f"Trust: {i.trust.value}" + (f" - revoked: {i.revoked}" if i.revoked else ""),
                 f"Installed: {i.installed_version or 'no'}" + (f" in {i.path}" if i.path else "")]
        if i.entry:
            lines += [f"Download: {i.entry.url} ({i.entry.size} bytes, sha256 {i.entry.sha256})"]
        if i.description:
            lines.append(i.description)
        if self.recipe_text is not None:
            lines += ["", self.recipe_text]
        return lines


@dataclass
class PreparedInstall:
    """A pack extracted and validated in quarantine, waiting for the user's decision (finish or discard)."""
    manifest: PackManifest
    trust: TrustLevel
    quarantine: Path
    destination: Path
    provenance: Provenance
    notes: List[str] = field(default_factory=list)
    author_key: Optional[str] = None  # the checked author signature's public key (base64), pinned on first use

    def summary(self) -> str:
        m = self.manifest
        warning = ("\nWARNING: this pack is not in the signed community index. Nobody reviewed it; install it "
                   "only if you trust where it came from. Every build of it shows a warning."
                   if self.trust == TrustLevel.LOCAL else "")
        lines = [f"{m.kind} {m.id} {m.version} by {m.author} (licence {m.license})",
                 f"Trust: {self.trust.value}{warning}", f"Source: {self.provenance.source}",
                 f"Files: {len(m.files)} ({sum(f.size for f in m.files)} bytes)",
                 f"Installs to: {self.destination}"]
        return "\n".join(lines + [f"Note: {n}" for n in self.notes])


@dataclass(frozen=True)
class PageShare:
    """One page of a knowledge export: its (scrubbed) address and title, and how it is shared and why."""
    source: str
    title: str
    plan: SharePlan

    def line(self) -> str:
        carried = "full text" if self.plan.shared_as == FULL_TEXT else "link and short facts"
        licence = f", licence {self.plan.licence}" if self.plan.licence else ""
        return f"{self.source}: {carried}{licence} ({self.plan.reason})"


def share_counts(shares: Sequence[PageShare]) -> str:
    """'N page(s) with their full text, M page(s) as link and short facts'."""
    full = sum(1 for s in shares if s.plan.shared_as == FULL_TEXT)
    return (f"{full} page(s) with their full text (their licence allows passing it on), {len(shares) - full} page(s) "
            "only as their address and short facts (no licence that allows passing the text on)")


def _page_meta(records: Sequence[dict]) -> Dict[str, Any]:
    """The metadata of one page from its chunks: the first value of each field."""
    meta: Dict[str, Any] = {}
    for record in records:
        for key, value in (record.get("metadata") or {}).items():
            if key not in meta and value not in (None, ""):
                meta[key] = value
    return meta


def _repo_copyrights(groups: Mapping[str, Sequence[dict]],
                     metas: Mapping[str, Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
    """{(crawl start address, repository): the copyright line of the repository's licence file} from the licence files
    the crawls read: its notice when one was recorded, otherwise the first one in its text. Code without a notice of
    its own goes out with this line, as MIT, BSD and ISC ask."""
    found: Dict[Tuple[str, str], str] = {}
    for source, meta in metas.items():
        if meta.get("licence_file") and meta.get("code_repo"):
            line = licences.clean(meta.get("copyright") or "") or \
                licences.copyright_notice(pack_licences.joined_text(groups[source]))
            if line:
                found.setdefault((str(meta.get("root_url") or ""), str(meta["code_repo"])), line)
    return found


def _scrubbed(source: str, content: str, metadata: Dict[str, Any]) -> dict:
    return {"source": scrub(source), "content": scrub(content),
            "metadata": {k: scrub(v) if isinstance(v, str) else v for k, v in metadata.items()}}


def share_pages(records: Iterable[dict]) -> Tuple[List[dict], List[PageShare]]:
    """Knowledge entries ({source, content, metadata}, one per chunk) as a pack may carry them, privacy-scrubbed
    (home folders, addresses, host and user names, secrets), and per page what is carried and why (Q-1'):
    * full text only when the page's licence allows passing the text on (rag/licences.py), each chunk with its licence
      and, when the licence requires it, the attribution (title, address, copyright notice, licence, the changes made);
      code without a notice of its own takes the copyright line of its repository's licence file (copyright_found
      says so), and a licence that asks for its text names its file in the pack (licence_text_file: LICENSES/<id>.txt,
      written by pack_licences.write next to the knowledge file);
    * otherwise one entry with the address and short facts (title, section names, technical terms), the licence and
      copyright notice the page shows, and why; the recipient crawls the page under their own checks.
    Code takes its repository's licence only from a licence file the same crawl read. A page without its source is
    never shared. Entries that are already address and short facts (a target's snapshot) stay as they are."""
    groups: Dict[str, List[dict]] = {}
    for record in records:
        source, content = record.get("source"), record.get("content")
        if isinstance(source, str) and source.strip() and isinstance(content, str) and content.strip():
            groups.setdefault(source, []).append(record)
    metas = {source: _page_meta(chunks) for source, chunks in groups.items()}
    repos = licences.repo_licences(metas.values())
    repo_notices = _repo_copyrights(groups, metas)
    pages: List[dict] = []
    shares: List[PageShare] = []
    for source, chunks in groups.items():
        meta = metas[source]
        title = str(meta.get("title") or "")
        if meta.get("shared_as") == LINK_AND_FACTS:
            plan = SharePlan(LINK_AND_FACTS, str(meta.get("licence_state") or "unknown"),
                             str(meta.get("licence") or ""), str(meta.get("share_reason") or "shared as its link "
                                                                                              "and short facts"))
            pages += [_scrubbed(source, c["content"], dict(c.get("metadata") or {})) for c in chunks]
        else:
            plan = licences.plan_for(meta, repos)
            if plan.shared_as == FULL_TEXT:
                extra: Dict[str, Any] = {"licence": plan.licence, "shared_as": FULL_TEXT, "share_reason": plan.reason}
                if not licences.clean(meta.get("copyright") or "") and meta.get("code_repo"):
                    if meta.get("licence_file"):   # the licence file itself: its own line, found on the page
                        line = licences.copyright_notice(pack_licences.joined_text(chunks))
                        extra.update({"copyright": line} if line else {})
                    else:
                        line = repo_notices.get((str(meta.get("root_url") or ""), str(meta["code_repo"])))
                        extra.update({"copyright": line, licences.COPYRIGHT_FOUND_KEY: licences.FROM_LICENCE_FILE}
                                     if line else {})
                if licence_texts.needs_text(plan.licence):
                    extra[licence_texts.TEXT_FILE_KEY] = licence_texts.text_path(plan.licence)
                if plan.attribution_required:
                    extra["attribution"] = licences.attribution(source, title, {**meta, **extra}, plan)
                pages += [_scrubbed(source, c["content"], {**(c.get("metadata") or {}), **extra}) for c in chunks]
            else:
                facts = licences.short_facts(source, title, [str((c.get("metadata") or {}).get("section") or "")
                                                             for c in chunks], [c["content"] for c in chunks], plan)
                kept = {key: meta[key] for key in ("title", "root_url", "licence", "licence_url", "licence_found",
                                                   "licence_text", "copyright", "code_repo") if meta.get(key)}
                kept.update(shared_as=LINK_AND_FACTS, licence_state=plan.state, share_reason=plan.reason)
                pages.append(_scrubbed(source, facts, kept))
        shares.append(PageShare(scrub(source), scrub(title), plan))
    pages = [page for page in pages if page["source"] and page["content"]]
    return pages, shares


@dataclass
class ExportResult:
    path: Path
    manifest: PackManifest
    signer: str
    changes: List[Tuple[str, str]]   # (file, diff of what the privacy scrub changed)
    notes: List[str] = field(default_factory=list)
    encrypted: bool = False
    shares: List[PageShare] = field(default_factory=list)   # a knowledge pack: every page, what it carries and why
    # The licence texts the pack ships (LICENSES/, with a NOTICE), each with its pages and why
    licence_texts: List[pack_licences.IncludedText] = field(default_factory=list)

    def summary(self) -> str:
        m = self.manifest
        lines = [f"{'Encrypted pack' if self.encrypted else 'Contribution'}: {self.path}",
                 f"{m.kind} {m.id} {m.version} by {m.author} (licence {m.license}), {len(m.files)} files, "
                 f"signed with key {self.signer}"]
        lines.append(f"Privacy scrub changed {len(self.changes)} file(s):" if self.changes else
                     "Privacy scrub: nothing to change.")
        for name, diff in self.changes:
            lines += [f"--- {name}", diff]
        if self.shares:
            lines.append("What the pack carries of each page, and why:")
            lines += [f"- {share.line()}" for share in self.shares]
        if self.licence_texts:
            lines.append("Licence texts in the pack (unchanged from the SPDX License List "
                         f"{licence_texts.list_version()}), and why:")
            lines += [f"- {text.line()}" for text in self.licence_texts]
            lines.append("- NOTICE (next to them) lists every page with its source address, its copyright line as "
                         "found and its licence.")
        elif any(share.plan.shared_as == FULL_TEXT for share in self.shares):
            lines.append("Licence texts in the pack: none; no page carried in full is under a licence that asks for "
                         "its text.")
        return "\n".join(lines + [f"Note: {n}" for n in self.notes])


def read_provenance(folder: Path) -> Optional[Provenance]:
    path = Path(folder) / PROVENANCE_NAME
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return Provenance.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def read_recipe(folder: Path):
    """The one reader of a recipe pack's recipe.yml (installed, or staged for its install check): the Recipe.
    Raises OSError (FileNotFoundError when it is missing), yaml.YAMLError or pydantic.ValidationError; each caller
    says what that means for it (the install check names it, the listings show no values, BuildService.installed_recipe
    refuses the build)."""
    from orchestrator.schemas.recipes import Recipe
    return Recipe.model_validate(yaml.safe_load((Path(folder) / RECIPE_FILE).read_text(encoding="utf-8")))


def trust_of_folder(folder: Path, index: Optional[CommunityIndex]) -> Tuple[Optional[TrustLevel], str]:
    """(trust, reason) of an installed pack folder; (None, "") for a folder without provenance (shipped or own)."""
    prov = read_provenance(folder)
    if prov is None:
        return None, ""
    revoked = index.revocation(prov.kind, prov.id, prov.version, prov.pack_sha256) if index else None
    if revoked is not None:
        return TrustLevel.REVOKED, revoked.reason
    return TrustLevel(prov.trust), ""


def build_trust_check(target_path: Path, cache_dir: Optional[Path]) -> Optional[str]:
    """For the builder: raises PackError for a revoked target; returns a warning for a local one."""
    index = CommunityManager.read_cached_index(cache_dir, revocations=True) if cache_dir else None
    trust, reason = trust_of_folder(target_path, index)
    if trust == TrustLevel.REVOKED:
        raise PackError(f"Target {target_path.name} was revoked by the community index ({reason}); it is not "
                        f"built. Remove the folder {target_path}.")
    if trust == TrustLevel.LOCAL:
        return (f"Target {target_path.name} was imported from a file that is not in the signed community index "
                "(trust: local); nobody reviewed its scripts.")
    if trust is None and ai_generated_target(target_path):
        return (f"Target {target_path.name} was created by an AI and is untested on real hardware (support level "
                "community); its build image is built with network access on this computer, from the base image and "
                "vendor sources its Dockerfile names.")
    return None


def ai_generated_target(target_path: Path) -> bool:
    """Whether a target folder's target.yml marks it as AI-generated (create_target_module, Ditto's proposal)."""
    try:
        meta = yaml.safe_load((Path(target_path) / "target.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(meta, dict) and isinstance(meta.get("metadata"), dict) and \
        meta["metadata"].get("provenance") == "ai-generated"


def _file_stamp(path: Path) -> str:
    """Size and modification time of a knowledge pack's file: another file (a pack installed again) is another
    stamp."""
    try:
        st = Path(path).stat()
    except OSError:
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def _read_text(path: Path) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


AUTHORS_FILE = "authors.json"  # the author keys pinned on first use (community cache folder)


class AuthorKeysDamaged(Exception):
    """The store of pinned author keys exists but cannot be read; it is kept as authors.json.bad, never replaced."""


class CommunityManager:

    def __init__(self, framework_manager, session=None, keys=None):
        self.logger = get_logger("CommunityManager")
        self.framework = framework_manager
        if hasattr(framework_manager, 'info') and hasattr(framework_manager.info, 'installation_path'):
            self.app_root = Path(framework_manager.info.installation_path)
        else:
            self.app_root = Path(".").resolve()
        self.config = getattr(framework_manager, 'config', None)
        self.community_dir = self.app_root / "community"
        self.knowledge_dir = self.community_dir / "knowledge"
        self.recipes_dir = self.community_dir / "recipes"
        self.contributions_dir = self.community_dir / "contributions"
        # The same folder the build engine and the target manager use.
        resolve = getattr(framework_manager, "resolve_path", None)
        targets = self._cfg("targets_dir", "targets")
        self.targets_dir = resolve(targets) if callable(resolve) else self.app_root / str(targets)
        self._session = session
        self._keys = keys  # tests pass their own; otherwise configs/community_keys.yml
        # The last knowledge pack import (sync_knowledge_base): the snapshot taken first, or why nothing was imported
        self.last_sync_problem, self.last_sync_snapshot = "", None
        # Directories are created when something is written, not at every CLI/GUI start.

    def _cfg(self, key: str, default: Any = None) -> Any:
        get = getattr(self.config, "get", None)
        return get(key, default) if callable(get) else default

    @property
    def cache_dir(self) -> Path:
        state = getattr(self.config, "user_state_dir", None)
        return (Path(state) if state else self.community_dir / ".state") / "community"

    def _downloads(self) -> PinnedDownloader:
        from orchestrator.Core.disk_plan import download_rule
        return PinnedDownloader(self._session, logger=self.logger, space=download_rule())

    def _install_root(self, kind: str) -> Path:
        return {"target": self.targets_dir, "recipe": self.recipes_dir, "knowledge": self.knowledge_dir}[kind]

    # --- index ------------------------------------------------------------------------------------------------
    @classmethod
    def _verify_index(cls, data: bytes, sig: bytes, keys, now: Optional[datetime] = None) -> CommunityIndex:
        return cls._verify_signed_index(data, sig, keys, now)[0]

    @staticmethod
    def _verify_signed_index(data: bytes, sig: bytes, keys,
                             now: Optional[datetime] = None) -> Tuple[CommunityIndex, str]:
        """The verified index and the key id of the trusted key that verified it."""
        try:
            signature = PackSignature.model_validate_json(sig)
        except ValidationError:
            raise PackError("index.json.sig is not a valid signature file.") from None
        signer = verify_index(data, signature, keys, now=now)  # before anything of the index is parsed
        try:
            return CommunityIndex.model_validate_json(data), str(signer or "")
        except ValidationError as e:
            raise PackError(f"The community index is invalid: {e}") from None

    @classmethod
    def read_cached_index(cls, cache_dir: Path, keys=None, *, revocations: bool = False) -> Optional[CommunityIndex]:
        """The cached index, verified again (an expired or removed key invalidates it); None if absent or bad.
        For revocations only, a key that expired after the index was fetched still counts: a revocation never
        stops applying because the framework was not updated."""
        try:
            path = cache_dir / "index.json"
            data, sig = path.read_bytes(), (cache_dir / "index.json.sig").read_bytes()
            fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if revocations else None
            return cls._verify_index(data, sig, load_trusted_keys() if keys is None else keys, now=fetched)
        except (OSError, PackError):
            return None

    def cached_index(self, *, revocations: bool = False) -> Optional[CommunityIndex]:
        return self.read_cached_index(self.cache_dir, self._keys, revocations=revocations)

    def _cache_is_fresh(self) -> bool:
        try:
            return time.time() - (self.cache_dir / "index.json").stat().st_mtime < INDEX_MAX_AGE
        except OSError:
            return False

    def load_index(self, refresh: bool = False) -> IndexState:
        """The verified community index: fetched when refresh is set, nothing is cached or the cache is older than
        a day, otherwise the cache. Without network the cached index is used. A fetched index that does not
        verify, or is older than the cached one, raises PackError and changes nothing."""
        cached = self.cached_index()
        if cached is not None and not refresh and self._cache_is_fresh():
            return IndexState(cached, "cached")
        url = str(self._cfg("community_index_url", "") or "")
        if not url:
            if cached is not None:
                return IndexState(cached, "cached", "community_index_url is empty")
            raise PackError("No community index is configured (setting community_index_url).")
        keys = load_trusted_keys() if self._keys is None else self._keys
        if not keys:
            raise PackError("This version ships no trusted community keys (configs/community_keys.yml), so no "
                            "community index can be verified.")
        downloads = self._downloads()
        try:
            data = downloads.get_bytes(url, INDEX_MAX_BYTES, "index.json")
            sig = downloads.get_bytes(url + ".sig", 4096, "index.json.sig")
        except DownloadError as e:
            if cached is not None:
                return IndexState(cached, "cached", f"offline, using the cached index: {e}")
            raise PackError(str(e)) from None
        index, signer = self._verify_signed_index(data, sig, keys)
        if cached is not None and index.version < cached.version:
            raise PackError(f"The downloaded community index (version {index.version}) is older than the cached one "
                            f"(version {cached.version}); it was refused.")
        ensure_directory(self.cache_dir)
        for name, content in (("index.json", data), ("index.json.sig", sig)):
            tmp = self.cache_dir / f".{name}.{secrets.token_hex(4)}"
            tmp.write_bytes(content)
            os.replace(tmp, self.cache_dir / name)
        return IndexState(index, "fetched", signer=signer)

    # --- installed packs --------------------------------------------------------------------------------------
    def installed(self) -> List[Tuple[Path, Provenance]]:
        found = []
        for kind in KINDS:
            root = self._install_root(kind)
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                prov = None if folder.is_symlink() or not folder.is_dir() else read_provenance(folder)
                if prov is not None and prov.kind == kind:
                    found.append((folder, prov))
        return found

    def listing(self, refresh: bool = False) -> Tuple[List[ZooItem], str]:
        """list_items() of the current index, and a note on which index was used (or why there is none)."""
        try:
            state = self.load_index(refresh)
        except PackError as e:
            return self.list_items(None), f"No community index: {e}"
        return self.list_items(state.index), state.note or f"Signed index version {state.index.version} " \
                                                           f"({state.source})."

    def list_items(self, index: Optional[CommunityIndex] = None) -> List[ZooItem]:
        """Index entries (latest version each) and installed packs, with their trust level. Installed packs are
        checked against the cached revocations when no index is given (also after its key expired)."""
        items: Dict[Tuple[str, str], ZooItem] = {}
        for entry in index.entries if index else []:
            key = (entry.kind, entry.id.casefold())
            if key in items and _version_key(items[key].version) >= _version_key(entry.version):
                continue
            revoked = index.revocation(entry.kind, entry.id, entry.version, entry.sha256)
            items[key] = ZooItem(entry.kind, entry.id, entry.version, entry.title or entry.id, entry.author,
                                 entry.license, TrustLevel.REVOKED if revoked else TrustLevel(entry.trust),
                                 revoked=revoked.reason if revoked else None, entry=entry,
                                 description=entry.description)
        revocations = index if index is not None else self.cached_index(revocations=True)
        for folder, prov in self.installed():
            trust, reason = trust_of_folder(folder, revocations)
            item = items.setdefault((prov.kind, prov.id.casefold()), ZooItem(
                prov.kind, prov.id, prov.version, prov.id, prov.author, "", trust))
            item.installed_version, item.path = prov.version, folder
            if trust == TrustLevel.REVOKED:
                item.trust, item.revoked = trust, reason
        return sorted(items.values(), key=lambda i: (i.kind, i.id.casefold()))

    def details(self, pack_id: str, kind: Optional[str] = None) -> List[ZooDetails]:
        """Every entry `pack_id` (any case) of the cached index and the installed packs, only of `kind` when given,
        with an installed recipe's recipe.yml (`llm-cli zoo show`, the Community Hub's Details...). Reads only; an
        empty list when there is none."""
        found = []
        for item in self.list_items(self.cached_index()):
            if item.id.casefold() != str(pack_id).casefold() or (kind and item.kind != kind):
                continue
            recipe = item.path / RECIPE_FILE if item.kind == "recipe" and item.path else None
            text = recipe.read_text(encoding="utf-8") if recipe is not None and recipe.is_file() else None
            found.append(ZooDetails(item, text))
        return found

    def exportable_targets(self) -> List[str]:
        """The target ids in the targets folder that `llm-cli zoo export target` can be asked for (a folder with a
        target.yml; the template and hidden folders left out), sorted. An installed community pack is listed too: the
        export refuses it with the reason (its author shares it already)."""
        if not self.targets_dir.is_dir():
            return []
        return sorted((p.name for p in self.targets_dir.iterdir()
                       if p.is_dir() and not p.is_symlink() and not p.name.startswith((".", "_"))
                       and re.fullmatch(ID_PATTERN, p.name) and (p / "target.yml").is_file()), key=str.casefold)

    # --- install ----------------------------------------------------------------------------------------------
    def _new_quarantine(self, kind: str, pack_id: str) -> Path:
        # Inside the install folder, so the final rename stays on one file system (atomic).
        folder = ensure_directory(self._install_root(kind) / ".quarantine")
        return Path(tempfile.mkdtemp(prefix=f"{pack_id}.", dir=folder))

    def prepare_install(self, kind: str, pack_id: str, progress=None) -> PreparedInstall:
        """Downloads the pack the index pins and checks it in quarantine."""
        if kind not in KINDS or not re.fullmatch(ID_PATTERN, str(pack_id)):
            raise PackError(f"Invalid {kind} id {pack_id!r}.")
        # The download needs the network anyway: always the current index, so a revocation is never missed.
        state = self.load_index(refresh=True)
        if state.source != "fetched":
            raise PackError(f"The community index could not be updated ({state.note}); nothing was installed.")
        entry = state.index.latest(kind, pack_id)
        if entry is None:
            raise PackError(f"There is no {kind} '{pack_id}' in the community index.")
        revoked = state.index.revocation(kind, entry.id, entry.version, entry.sha256)
        if revoked is not None:
            raise PackError(f"{kind} {entry.id} {entry.version} was revoked: {revoked.reason}")
        quarantine = self._new_quarantine(kind, entry.id)
        try:
            archive = self._downloads().fetch(entry.url, quarantine / "pack.zip", entry.size, entry.sha256, progress)
            return self._prepare(archive, quarantine, TrustLevel(entry.trust), entry.url, state.index,
                                 expected=(kind, entry.id, entry.version))
        except BaseException as e:
            shutil.rmtree(quarantine, ignore_errors=True)
            if isinstance(e, DownloadError):
                raise PackError(str(e)) from None
            raise

    def prepare_install_file(self, path: Path, *, identity=None, passphrase: Optional[str] = None,
                             trusted_senders=None) -> PreparedInstall:
        """A pack from a file, or an encrypted pack shared privately (opened with the passphrase if one is given,
        otherwise with identity or your own age identity). Its trust comes from the cached index when the index
        lists its sha256; otherwise it is local."""
        path = Path(path)
        if path.stat().st_size > MAX_PACK_BYTES + 2 * 1024 * 1024:
            raise PackError(f"{path.name} is larger than a pack may be.")
        data = path.read_bytes()
        sender = None
        if is_encrypted_pack(data):
            # Your own age identity is read (and made on first use) only for a pack encrypted to keys, after its
            # signature was checked: a passphrase pack opened without the passphrase asks for it.
            own_identity = UserKeys(self._secrets()).age_identity if passphrase is None and identity is None else None
            data, sender = decrypt_pack(data, identity=identity, passphrase=passphrase,
                                        trusted_senders=trusted_senders, identity_loader=own_identity)
        elif trusted_senders is not None:
            raise PackError("Trusted senders apply only to encrypted packs; this pack is not encrypted (its author "
                            "signature is shown in the summary).")
        try:
            index = self.load_index().index  # updated when older than a day, for the latest revocations
        except PackError:  # offline, no index configured, or a download that does not verify
            index = self.cached_index()
        digest = hashlib.sha256(data).hexdigest()
        entry = index.by_sha256(digest) if index else None
        probe = read_manifest(data)
        revoked = index.revocation(probe.kind, probe.id, probe.version, digest) if index else None
        if revoked is not None:
            raise PackError(f"{probe.kind} {probe.id} {probe.version} was revoked: {revoked.reason}")
        quarantine = self._new_quarantine(probe.kind, probe.id)
        try:
            archive = quarantine / "pack.zip"
            archive.write_bytes(data)
            trust = TrustLevel(entry.trust) if entry else TrustLevel.LOCAL
            return self._prepare(archive, quarantine, trust, path.name, index if entry else None, sender=sender,
                                 expected=(entry.kind, entry.id, entry.version) if entry else None)
        except BaseException:
            shutil.rmtree(quarantine, ignore_errors=True)
            raise

    def _prepare(self, archive: Path, quarantine: Path, trust: TrustLevel, source: str,
                 index: Optional[CommunityIndex], expected: Optional[tuple] = None,
                 sender: Optional[str] = None) -> PreparedInstall:
        contents = read_pack(archive, quarantine / "pack")
        m = contents.manifest
        if expected is not None and (m.kind, m.id.casefold(), m.version) != (expected[0], expected[1].casefold(),
                                                                              expected[2]):
            raise PackError("The pack does not match its index entry (kind, id or version differ).")
        notes = [n for n in (self._author_note(m, contents.signature),
                             f"sent by key {sender} (encrypted pack)" if sender else None) if n]
        problems = self._validate(m, quarantine / "pack", notes)
        if problems:
            raise PackError(f"The {m.kind} pack {m.id} was rejected and removed:\n- " + "\n- ".join(problems[:20]))
        root = self._install_root(m.kind)
        if any(p.name.casefold() == m.id.casefold() for p in root.iterdir() if p.name != ".quarantine"):
            raise PackError(f"{root / m.id} already exists; remove it first to install {m.id} {m.version}.")
        if m.kind == "target":
            # Target names resolve to the first match ignoring case (G43): a pack never captures another target's
            # name, also not with other separators (my_board next to My-Board).
            from orchestrator.Core.module_generator import target_name_collision
            names = [m.id]
            try:
                meta = (yaml.safe_load((quarantine / "pack" / "target.yml").read_text(encoding="utf-8")) or {})
                declared = (meta.get("metadata") or {}).get("id") if isinstance(meta, dict) else None
                if isinstance(declared, str) and declared:
                    names.append(declared)
            except (OSError, yaml.YAMLError, AttributeError):
                pass
            taken = target_name_collision(root, *names)
            if taken is not None:
                raise PackError(f"The target pack {m.id} has the same name as the existing target {taken} (ignoring "
                                "case and separators); it is not installed.")
        signature = contents.signature
        checked = signature is not None and signature.pubkey is not None  # verified in _author_note
        prov = Provenance(kind=m.kind, id=m.id, version=m.version, trust=trust.value, source=source[:1000],
                          pack_sha256=sha256_file(archive),  # in pieces: an archive may have 512 MB
                          content_sha256=m.content_sha256(), index_version=index.version if index else None,
                          author=m.author, signer=signature.key_id if checked else None, sender=sender,
                          installed_at=utc_now())
        return PreparedInstall(m, trust, quarantine, root / m.id, prov, notes,
                               signature.pubkey if checked else None)

    def _author_note(self, manifest: PackManifest, signature: Optional[PackSignature]) -> Optional[str]:
        """Author signatures are provenance: checked when the public key is included, the public key pinned per
        author on first use (finish_install) and a different key reported."""
        if signature is None:
            return "not signed by its author"
        key = signature.embedded_key()
        if key is None:
            return f"signed by author key {signature.key_id} (public key not included, not checked)"
        verify_manifest(manifest, signature, [key])  # a broken signature means a changed pack: refused
        try:
            pinned = self._authors().get(manifest.author)
        except AuthorKeysDamaged:
            return (f"WARNING: signed by key {signature.key_id}, but the store of pinned author keys is damaged (kept "
                    f"as {AUTHORS_FILE}.bad), so this key could not be compared with the one '{manifest.author}' used "
                    "before")
        if pinned and pinned != signature.pubkey:  # the whole key: a 64-bit key id can be matched on purpose
            return (f"WARNING: signed by key {signature.key_id}, but '{manifest.author}' signed with another key "
                    "before")
        return f"signed by author key {signature.key_id}" + ("" if pinned else " (first pack seen from this author)")

    def _authors(self) -> Dict[str, str]:
        """The pinned author keys ({} when none was pinned yet). A store that exists but cannot be read is never
        taken for an empty one (the next pin would overwrite every key pinned on first use): it is kept as
        authors.json.bad and AuthorKeysDamaged is raised."""
        path = self.cache_dir / AUTHORS_FILE
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:  # unreadable, or not text
            self._keep_damaged(path)
            raise AuthorKeysDamaged(f"{AUTHORS_FILE} cannot be read ({type(e).__name__})") from None
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            self._keep_damaged(path)
            raise AuthorKeysDamaged(f"{AUTHORS_FILE} is damaged (not a map of author names to keys)")
        return dict(data)

    def _keep_damaged(self, path: Path) -> None:
        """A copy of a damaged key store next to it (authors.json.bad; an older, different copy is kept too, as
        authors.json.<time>.bad). The same damage is kept once: an install reads the store twice (the summary, then
        the pin), and a copy named by the time alone was made again when the second read fell into the next second."""
        bad = path.with_name(path.name + ".bad")
        try:
            if bad.exists():
                damage = path.read_bytes()
                if any(old.read_bytes() == damage for old in path.parent.glob(f"{path.name}*.bad") if old.is_file()):
                    return
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                bad = path.with_name(f"{path.name}.{stamp}.bad")
                count = 1
                while bad.exists():   # another damage in the same second: never overwrite a kept copy
                    count += 1
                    bad = path.with_name(f"{path.name}.{stamp}-{count}.bad")
            shutil.copy2(path, bad)
            self.logger.warning(f"The store of pinned author keys is damaged; a copy is kept as {bad.name}.")
        except OSError as e:
            self.logger.warning(f"The damaged store of pinned author keys could not be copied: {e}")

    def _validate(self, manifest: PackManifest, root: Path, notes: List[str], exporting: bool = False) -> List[str]:
        """The problems of a pack in `root` (empty: none); notes for the person's summary. exporting: the export's own
        check, whose summary names the licence texts already (no second note about them)."""
        from orchestrator.Core.module_generator import validate_target_folder
        names = {f.path for f in manifest.files}
        licence_notes = [] if exporting else notes
        if manifest.kind == "target":
            return validate_target_folder(root) + self._check_licence_files(root, "target", licence_notes)
        allowed = {RECIPE_FILE if manifest.kind == "recipe" else KNOWLEDGE_FILE, "README.md"}
        if manifest.kind == "knowledge":   # the licence texts and the NOTICE its pages need (_check_licence_files)
            allowed |= {licence_texts.NOTICE_NAME} | {n for n in names if n.startswith(licence_texts.LICENSES_DIR + "/")}
        problems = [f"{name}: not allowed in a {manifest.kind} pack" for name in sorted(names - allowed)]
        if manifest.kind == "recipe":
            return problems + self._check_recipe(root, manifest, notes)
        knowledge = self._check_knowledge(root)
        return problems + knowledge + ([] if knowledge else self._check_licence_files(root, "knowledge",
                                                                                    licence_notes))

    @staticmethod
    def _check_licence_files(root: Path, kind: str, notes: List[str]) -> List[str]:
        """The licence texts and the NOTICE of a knowledge pack, or of a target pack's knowledge snapshot
        (pack_licences.check): its problems; a note names the texts kept with the pack."""
        from orchestrator.Core.module_generator import KNOWLEDGE_SNAPSHOT
        rel = KNOWLEDGE_FILE if kind == "knowledge" else KNOWLEDGE_SNAPSHOT
        path = Path(root) / rel
        try:
            entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError, UnicodeDecodeError):
            entries = None
        if not isinstance(entries, list):
            # No pages to check (a knowledge pack's file: _check_knowledge says why); the licence texts next to it are
            # still checked, so a text that is not the canonical one never comes in with a target pack.
            entries = []
        folder = path.parent
        prefix = "" if folder == Path(root) else f"{folder.relative_to(root).as_posix()}/"
        problems, found = pack_licences.check(folder, [e for e in entries if isinstance(e, dict)], prefix)
        notes += found
        return problems

    def _target_folder(self, target_id: str) -> Optional[Path]:
        if not self.targets_dir.is_dir():
            return None
        return next((p for p in self.targets_dir.iterdir() if p.name.casefold() == target_id.casefold()
                     and p.is_dir() and not p.is_symlink()), None)

    def _check_recipe(self, root: Path, manifest: PackManifest, notes: List[str]) -> List[str]:
        from orchestrator.schemas.targets import TargetCapabilities
        try:
            recipe = read_recipe(root)
        except FileNotFoundError:
            return [f"{RECIPE_FILE}: missing"]
        except (yaml.YAMLError, ValidationError) as e:
            return [f"{RECIPE_FILE}: invalid ({str(e).splitlines()[0][:200]})"]
        problems = [] if recipe.id == manifest.id else [f"{RECIPE_FILE}: id {recipe.id} differs from the pack id"]
        folder = self._target_folder(recipe.target.id)
        if folder is None:
            notes.append(f"target {recipe.target.id} is not installed; install it before building this recipe")
            return problems
        try:
            caps = TargetCapabilities.model_validate((yaml.safe_load((folder / "target.yml").read_text(
                encoding="utf-8")) or {}).get("capabilities"))
            problems += recipe.check_target(caps)
        except (OSError, yaml.YAMLError, ValidationError, AttributeError):
            notes.append(f"target {recipe.target.id} declares no valid capabilities; the recipe was not checked")
        try:
            if tree_sha256(folder) != recipe.target.pack_sha256:
                notes.append(f"your copy of target {recipe.target.id} differs from the one the recipe was built "
                             "with; results may differ")
        except PackError:
            pass
        return problems

    @staticmethod
    def _check_knowledge(root: Path) -> List[str]:
        try:
            data = json.loads((root / KNOWLEDGE_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [f"{KNOWLEDGE_FILE}: missing"]
        except (ValueError, UnicodeDecodeError):
            return [f"{KNOWLEDGE_FILE}: not valid JSON"]
        # Every page keeps where it came from: shared knowledge is never without its source.
        good = isinstance(data, list) and all(
            isinstance(e, dict) and isinstance(e.get("source"), str) and e["source"].strip()
            and isinstance(e.get("content"), str) and isinstance(e.get("metadata", {}), dict) for e in data)
        return [] if good else [f"{KNOWLEDGE_FILE}: expected a list of {{source, content, metadata}} entries, "
                                "each with its source"]

    def finish_install(self, prepared: PreparedInstall) -> Path:
        """Moves the checked pack into place (atomic rename) with its provenance; the quarantine is removed."""
        try:
            pack = prepared.quarantine / "pack"
            (pack / PROVENANCE_NAME).write_text(prepared.provenance.model_dump_json(indent=2), encoding="utf-8")
            dest = prepared.destination
            if any(p.name.casefold() == dest.name.casefold() for p in dest.parent.iterdir()):
                raise PackError(f"{dest} already exists.")
            os.rename(pack, dest)  # never merges into an existing folder
        finally:
            shutil.rmtree(prepared.quarantine, ignore_errors=True)
        if prepared.author_key:
            self._pin_author(prepared.manifest.author, prepared.author_key)
        if prepared.manifest.kind == "knowledge":
            # The marker of an earlier version (its folder was deleted to remove it) would skip this one.
            (self.knowledge_dir / f"{dest.name}.imported").unlink(missing_ok=True)
            self.sync_knowledge_base()
        return dest

    def _pin_author(self, author: str, pubkey: str) -> None:
        """Pins an author's public key on first use. The GUI, the CLI and another CLI call may install at the same
        time: read and write under the shared lock, so no process drops a key another one pinned."""
        target = self.cache_dir / AUTHORS_FILE
        with file_lock(self.cache_dir / "authors.json.lock"):
            try:
                authors = self._authors()
            except AuthorKeysDamaged as e:
                # Refused rather than starting a new store: every key pinned on first use would be lost.
                self.logger.warning(f"The key of author '{author}' was not pinned: {e} (kept as {AUTHORS_FILE}.bad). "
                                    f"Repair or remove {AUTHORS_FILE} in the community cache folder; until then no "
                                    "author key is pinned.")
                return
            if author in authors:
                return
            authors[author] = pubkey
            tmp = self.cache_dir / f".authors.json.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(authors, indent=2), encoding="utf-8")
            os.replace(tmp, target)  # readers without the lock never see half a file

    @staticmethod
    def discard(prepared: PreparedInstall) -> None:
        shutil.rmtree(prepared.quarantine, ignore_errors=True)

    # --- verify -----------------------------------------------------------------------------------------------
    def verify_installed(self) -> List[Tuple[Path, Provenance, Optional[TrustLevel], List[str]]]:
        """Each installed pack with its trust and problems: revoked, changed since it was installed, or licence files
        that do not match its pages (pack_licences.check)."""
        index = self.cached_index(revocations=True)
        results = []
        for folder, prov in self.installed():
            trust, reason = trust_of_folder(folder, index)
            problems = [f"revoked: {reason}"] if trust == TrustLevel.REVOKED else []
            try:
                if tree_sha256(folder) != prov.content_sha256:
                    problems.append("changed since it was installed")
            except PackError as e:
                problems.append(str(e))
            if prov.kind in ("knowledge", "target"):   # its licence texts and NOTICE (pack_licences.check)
                problems += self._check_licence_files(folder, prov.kind, [])
            results.append((folder, prov, trust, problems))
        return results

    def verify_file(self, path: Path) -> Tuple[PackManifest, List[str]]:
        """Checks a pack file without installing it (community repository CI): format, hashes, the validators
        and the privacy scrub. Returns the manifest and the problems (empty: the pack is acceptable)."""
        work = Path(tempfile.mkdtemp(prefix="lcf-verify-"))
        try:
            contents = read_pack(Path(path), work / "pack")
            notes: List[str] = []
            problems = self._validate(contents.manifest, work / "pack", notes)
            if contents.signature is not None and contents.signature.embedded_key() is not None:
                verify_manifest(contents.manifest, contents.signature, [contents.signature.embedded_key()])
            for rel, file in list_files(work / "pack"):
                if licence_texts.canonical_file(rel, file.read_bytes()):
                    continue  # a canonical licence text is checked byte for byte instead (_check_licence_files)
                text = file.read_text(encoding="utf-8", errors="replace")
                if scrub(text) != text:
                    problems.append(f"{rel}: contains personal data the privacy scrub would remove")
            return contents.manifest, problems
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # --- export -----------------------------------------------------------------------------------------------
    def _secrets(self):
        sm = getattr(self.framework, "secrets_manager", None)
        if sm is None and hasattr(self.framework, "get_component"):
            sm = self.framework.get_component("secrets_manager")
        return sm

    def export_pack(self, kind: str, source: Optional[str] = None, *, author: str, license: Optional[str] = None,
                    pack_id: Optional[str] = None, model_license: str = "unknown",
                    root_urls: Optional[Iterable[str]] = None, recipients: Sequence = (),
                    passphrase: Optional[str] = None, out_dir: Optional[Path] = None) -> ExportResult:
        """A scrubbed pack signed with your key: kind target (source: target id), recipe (source: a build's
        build_info.json) or knowledge (optionally only the pages of root_urls). With recipients or a passphrase
        it is encrypted for private sharing. Nothing is uploaded. The licence defaults to MIT for your own work;
        knowledge holds other people's pages, so it is NOASSERTION there, every page keeps its source, and a page goes
        in with its full text only where its licence allows it (share_pages; the result lists every page)."""
        if kind not in KINDS:
            raise PackError(f"Unknown pack kind {kind!r}.")
        license = license or ("NOASSERTION" if kind == "knowledge" else "MIT")
        out_dir = ensure_directory(Path(out_dir) if out_dir else self.contributions_dir)
        staging = Path(tempfile.mkdtemp(prefix=".export-", dir=out_dir))
        root, notes, shares, texts = staging / "pack", [], [], []
        try:
            root.mkdir()
            version = "1.0.0"
            if kind == "target":
                pack_id, version = self._stage_target(str(source or ""), root, notes, shares, texts)
            elif kind == "recipe":
                pack_id = self._stage_recipe(Path(str(source or "")), root, author, model_license, notes)
            else:
                pack_id = pack_id or f"knowledge-{datetime.now():%Y%m%d}"
                pages, left_out, shares = self.knowledge_share(root_urls)
                if left_out:
                    notes.append(f"{left_out} page(s) imported from installed community knowledge packs were left "
                                 "out; they are shared by their authors already")
                if not pages:
                    raise PackError("The knowledge base has nothing to export.")
                (root / KNOWLEDGE_FILE).write_text(json.dumps(pages, indent=2), encoding="utf-8")
                files = pack_licences.write(root, pages)
                texts += files.texts
                notes.append("every page keeps its source address; the pages stay under their owners' terms")
                notes.append(share_counts(shares))
                notes += files.notes()
            changes = self._scrub_tree(root)
            manifest = build_manifest(root, kind=kind, id=pack_id, version=version, author=author, license=license)
            problems = self._validate(manifest, root, notes, exporting=True)
            if problems:
                raise PackError("The pack would be rejected:\n- " + "\n- ".join(problems[:20]))
            key = UserKeys(self._secrets()).signing_key()
            signature = sign_manifest(manifest, key)
            data = write_pack(root, manifest, signature)
            encrypted = bool(recipients) or passphrase is not None
            if encrypted:
                data = encrypt_pack(data, sender=key, recipients=recipients, passphrase=passphrase)
            name = f"{kind}-{pack_id}-{version}.lcfpack" if encrypted else f"contribution-{kind}-{pack_id}-{version}.zip"
            out = out_dir / name
            tmp = staging / name
            tmp.write_bytes(data)
            os.replace(tmp, out)
            return ExportResult(out, manifest, signature.key_id, changes, notes, encrypted, shares, texts)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _stage_target(self, target_id: str, root: Path, notes: List[str], shares: List[PageShare],
                      texts: List[pack_licences.IncludedText]) -> Tuple[str, str]:
        folder = self._target_folder(target_id) if re.fullmatch(ID_PATTERN, target_id) else None
        if folder is None:
            raise PackError(f"Target '{target_id}' not found in {self.targets_dir}.")
        prov = read_provenance(folder)
        if prov is not None:  # re-signing it would replace its author and licence with yours
            raise PackError(f"Target {folder.name} is {prov.author}'s community pack (from {prov.source}) and is "
                            "shared already; a target of your own made from it keeps its author and licence.")
        if (folder / "knowledge").is_dir():
            notes.append("knowledge/ holds pages from the web: each keeps its source address, and the pack licence "
                         "does not cover them")
        for rel, path in list_files(folder):  # links and special files are refused
            dest = root.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest, follow_symlinks=False)  # a link appearing meanwhile is refused below
        snapshot_shares, snapshot_texts = self._share_snapshot(root, notes)
        shares += snapshot_shares
        texts += snapshot_texts
        try:
            meta = (yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8")) or {}).get("metadata") or {}
        except (OSError, yaml.YAMLError, AttributeError):
            meta = {}
        version = str(meta.get("version", ""))
        return folder.name, version if re.fullmatch(VERSION_PATTERN, version) else "1.0.0"

    @staticmethod
    def _share_snapshot(root: Path, notes: List[str]) -> Tuple[List[PageShare], List[pack_licences.IncludedText]]:
        """The knowledge snapshot of a staged target as the licences allow (share_pages): a snapshot made before this
        rule, with every page's full text, goes out with full text only where a page's licence allows it; the licence
        texts and the NOTICE it needs are written next to it (knowledge/LICENSES/, knowledge/NOTICE), made anew from
        the snapshot as the pack carries it. Returns every page with what the pack carries of it and why, and the
        licence texts."""
        from orchestrator.Core.module_generator import KNOWLEDGE_SNAPSHOT
        snapshot = root / KNOWLEDGE_SNAPSHOT
        folder = snapshot.parent
        if not snapshot.is_file():
            # Licence files left from a removed snapshot would carry its pages' addresses and copyright lines: they
            # stay out of the pack (only the export's temporary copy is changed, never the target folder).
            stale = [f"{folder.relative_to(root).as_posix()}/{name}" for name in
                     (licence_texts.NOTICE_NAME, licence_texts.LICENSES_DIR) if (folder / name).exists()]
            shutil.rmtree(folder / licence_texts.LICENSES_DIR, ignore_errors=True)
            (folder / licence_texts.NOTICE_NAME).unlink(missing_ok=True)
            if stale:
                notes.append(f"{' and '.join(stale)} left out: there is no knowledge snapshot ({KNOWLEDGE_SNAPSHOT}) "
                             "they belong to")
            return [], []
        try:
            entries = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            raise PackError(f"{KNOWLEDGE_SNAPSHOT} cannot be read; repair or remove it before the export.") from None
        if not isinstance(entries, list):
            raise PackError(f"{KNOWLEDGE_SNAPSHOT} is not a list of pages; repair or remove it before the export.")
        pages, shares = share_pages(e for e in entries if isinstance(e, dict))
        text = json.dumps(pages, indent=2)
        if text != snapshot.read_text(encoding="utf-8"):
            snapshot.write_text(text, encoding="utf-8")
        # The staged copy's licence files are made anew (the folder's own copy of them may be older than the rule);
        # only the export's temporary copy is changed, never the target folder.
        shutil.rmtree(folder / licence_texts.LICENSES_DIR, ignore_errors=True)
        (folder / licence_texts.NOTICE_NAME).unlink(missing_ok=True)
        files = pack_licences.write(folder, pages, prefix=f"{folder.relative_to(root).as_posix()}/")
        notes.append(f"{KNOWLEDGE_SNAPSHOT}: {share_counts(shares)}")
        notes += [f"{KNOWLEDGE_SNAPSHOT}: {note}" for note in files.notes()]
        return shares, files.texts

    def _stage_recipe(self, build_info: Path, root: Path, author: str, model_license: str,
                      notes: List[str]) -> str:
        from orchestrator.schemas.recipes import Recipe
        try:
            info = json.loads(build_info.read_text(encoding="utf-8"))
            recipe = Recipe.from_build_info(info, author=author, license=model_license)
        except (OSError, ValueError, ValidationError) as e:
            raise PackError(f"No recipe from {build_info}: {e}") from None
        folder = self._target_folder(recipe.target.id)
        if folder is not None and read_provenance(folder) is None:
            try:
                official = (yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8")) or {}).get(
                    "capabilities", {}).get("support_level") == "official"
            except (OSError, yaml.YAMLError, AttributeError):
                official = False
            if not official:
                notes.append(f"target {recipe.target.id} is your own: export it too (zoo export target "
                             f"{recipe.target.id}), or others cannot build this recipe")
        (root / RECIPE_FILE).write_text(recipe.to_yaml(), encoding="utf-8")
        return recipe.id

    @staticmethod
    def _scrub_tree(root: Path) -> List[Tuple[str, str]]:
        """Applies the privacy scrub to every file in place; returns a diff per changed file. Binary files are
        refused: packs carry scripts, recipes and text."""
        changes = []
        for rel, path in list_files(root):
            data = path.read_bytes()
            if licence_texts.canonical_file(rel, data):
                continue  # a canonical licence text stays byte for byte (no personal data; the check compares it)
            try:
                if b"\0" in data:
                    raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte")
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise PackError(f"{rel} is a binary file; packs carry text only.") from None
            cleaned = scrub(text)
            if cleaned != text:
                path.write_bytes(cleaned.encode("utf-8"))
                diff = list(difflib.unified_diff(text.splitlines(), cleaned.splitlines(), lineterm="", n=0))[2:]
                changes.append((rel, "\n".join(diff[:40] + (["..."] if len(diff) > 40 else []))))
        return changes

    # --- knowledge base ---------------------------------------------------------------------------------------
    def sync_knowledge_base(self, rag_manager=None) -> int:
        """Imports the knowledge packs not imported yet (also those installed while the knowledge base was off;
        the framework calls this when the knowledge base starts). Returns the number of pages imported.

        Before the first page is imported, the knowledge base is saved as a snapshot (pre_pack_<time>), so the import
        can be undone (eighth round, N13). When that snapshot does not fit on the disk, nothing is imported (the packs
        stay installed and are imported once there is room); `last_sync_problem` says why, `last_sync_snapshot` names
        the snapshot taken. A pack that cannot be imported yet is tried again at the next start without another
        snapshot; a pack installed again gets one."""
        self.last_sync_problem, self.last_sync_snapshot = "", None
        if rag_manager is None and hasattr(self.framework, "get_component"):
            rag_manager = self.framework.get_component("rag_manager")
        if not rag_manager:
            return 0

        imported_count = 0
        files = sorted(self.knowledge_dir.glob("*.json")) + sorted(self.knowledge_dir.glob(f"*/{KNOWLEDGE_FILE}"))
        # An installed pack folder stays unchanged (its content is verified); its marker lives next to it.
        pending = [(f, f.with_suffix(".imported") if f.parent == self.knowledge_dir else
                    self.knowledge_dir / f"{f.parent.name}.imported") for f in files]
        pending = [(f, marker) for f, marker in pending if not marker.exists()]
        # A pack that cannot be imported yet stays pending and is tried again at every start: its snapshot is taken
        # once (<pack>.snapshotted names the file it was taken for), never one per start (none is deleted by itself)
        fresh = [(f, marker.with_suffix(".snapshotted"), _file_stamp(f)) for f, marker in pending]
        fresh = [(f, mark, stamp) for f, mark, stamp in fresh if _read_text(mark) != stamp]
        if fresh and callable(getattr(rag_manager, "create_snapshot", None)):
            from orchestrator.Core.knowledge_snapshots import SnapshotNoRoom
            try:
                self.last_sync_snapshot = rag_manager.create_snapshot(f"pre_pack_{int(time.time())}",
                                                                      "a knowledge pack is imported")
            except SnapshotNoRoom as e:  # no snapshot, no import: there is no "continue anyway"
                self.last_sync_problem = str(e)
                self.logger.warning(f"Knowledge packs not imported: {e}")
                return 0
            for _f, mark, stamp in fresh:
                try:
                    mark.write_text(stamp, encoding="utf-8")
                except OSError as e:
                    self.logger.warning(f"Could not note the snapshot taken before {mark.stem}: {e}")
        for json_file, marker in pending:
            try:
                if marker.exists():
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
                    failed = 0
                    for entry in data:
                        meta = entry.get("metadata", {})
                        meta["origin_pack"] = json_file.name if json_file.parent == self.knowledge_dir else \
                            json_file.parent.name
                        # A page with origin_pack is outside text: the knowledge base stores it cleaned (hidden HTML,
                        # invisible characters and HTML comments left out; RAGManager.ingest_document)
                        if rag_manager.ingest_document(entry.get("source", ""), entry.get("content", ""), meta):
                            imported_count += 1
                        elif str(entry.get("content") or "").strip():
                            failed += 1  # refused, e.g. offline without the embedding model
                    if failed:  # importing again updates the pages already stored in place
                        self.logger.warning(f"{json_file.name}: {failed} page(s) not imported; trying again when "
                                            "the knowledge base starts next time.")
                        continue

                with open(marker, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                self.logger.error(f"Sync failed {json_file.name}: {e}")
        return imported_count

    def _knowledge_pages(self, root_urls: Optional[Iterable[str]] = None) -> Tuple[List[dict], int]:
        """knowledge_share without the list of pages."""
        pages, left_out, _ = self.knowledge_share(root_urls)
        return pages, left_out

    def knowledge_share(self, root_urls: Optional[Iterable[str]] = None) -> Tuple[List[dict], int, List[PageShare]]:
        """Your pages of the knowledge base as a pack may carry them (share_pages: privacy-scrubbed, full text only
        where a page's licence allows it), with root_urls only those crawled from these URLs; how many pages imported
        from installed community knowledge packs were left out (re-sharing them would sign them under your name); and
        every page with what it carries and why. The framework's own documents are never exported either: every
        installation indexes them itself (llm-cli knowledge index-docs). The knowledge base keeps a page as several
        chunks: pages are counted by their source."""
        rag_manager = self.framework.get_component("rag_manager") if hasattr(self.framework, "get_component") \
            else None
        try:
            records = list(rag_manager.export_records()) if rag_manager else []
        except Exception as e:
            self.logger.error(f"Knowledge export failed: {e}")
            records = []
        if root_urls is not None:
            roots = {str(u) for u in root_urls}
            records = [r for r in records if (r.get("metadata") or {}).get("root_url") in roots]
        from orchestrator.Core.rag_manager import REMOVED_FIELD, source_kind
        records = [r for r in records
                   if source_kind({**(r.get("metadata") or {}), "source": r.get("source", "")}) != "docs"]
        # What this installation left out of a page (REMOVED_FIELD) is its own provenance, not part of the page.
        own = [{**r, "metadata": {k: v for k, v in (r.get("metadata") or {}).items() if k != REMOVED_FIELD}}
               for r in records if not (r.get("metadata") or {}).get("origin_pack")]
        pages, shares = share_pages(own)   # scrubbed, also URLs with tokens in the query
        left_out = {str(r.get("source") or "") for r in records if (r.get("metadata") or {}).get("origin_pack")}
        return pages, len(left_out), shares

    def export_knowledge_base(self, output_name_prefix: str = "knowledge_export",
                              root_urls: Optional[Iterable[str]] = None) -> Optional[str]:
        """Writes your pages of the knowledge base (_knowledge_pages) as JSON into the knowledge folder and returns
        its path, or None when there is nothing. It stays on this computer; sharing it is a separate step."""
        pages, _ = self._knowledge_pages(root_urls)
        if not pages:
            return None
        export_path = ensure_directory(self.knowledge_dir) / f"{output_name_prefix}_{datetime.now():%Y%m%d_%H%M%S}.json"
        export_path.write_text(json.dumps(pages, indent=2), encoding="utf-8")
        return str(export_path)


# --- Pull request handoff -------------------------------------------------------------------------------------

def submit_contribution(path: Path, repo: str, kind: str, *, runner: Callable = subprocess.run,
                        which: Callable = shutil.which, opener: Callable = webbrowser.open) -> str:
    """Hands an exported pack to a pull request on the community repository (owner/name). With an installed and
    logged-in GitHub CLI: fork, commit on a new branch and `gh pr create` (gh and git use their own stored
    credentials). Otherwise, or if that fails, the repository's upload page opens in the browser, where GitHub
    forks for you. The framework never sees a token. Returns what happened."""
    path = Path(path)
    if not REPO_RE.fullmatch(str(repo or "")):
        raise PackError("No community repository is configured (setting community_repo, owner/name); the pack is "
                        f"ready at {path}.")
    if kind not in KINDS:
        raise PackError(f"Unknown pack kind {kind!r}.")
    upload = f"https://github.com/{repo}/upload/main/contributions/{kind}"
    gh, git = which("gh"), which("git")
    note = ""
    if gh and git:
        try:
            if runner([gh, "auth", "status"], capture_output=True, text=True, timeout=60,
                      stdin=subprocess.DEVNULL).returncode == 0:
                return _submit_with_gh(path, repo, kind, gh, git, runner)
            note = "The GitHub CLI is not logged in ('gh auth login'). "
        except (subprocess.SubprocessError, OSError) as e:
            # git and gh output can name a remote with credentials in it: never shown
            detail = redact_secrets(re.sub(r"(\w+://)[^/\s@'\"]+@", r"\1", str(getattr(e, "stderr", "") or e)))
            note = f"The GitHub CLI could not open the pull request ({detail.strip()[-300:]}). "
    opener(upload)
    return (f"{note}Opened {upload} in your browser: drop {path.name} (in {path.parent}) onto the page and "
            "propose the change; GitHub forks the repository for you.")


def _submit_with_gh(path: Path, repo: str, kind: str, gh: str, git: str, runner: Callable) -> str:
    def run(cmd, cwd=None):
        # stdin=DEVNULL: over stdio the MCP server's stdin is the protocol stream; gh and git never read it (G25)
        return runner(cmd, cwd=cwd, capture_output=True, text=True, timeout=600, check=True, stdin=subprocess.DEVNULL)

    work = Path(tempfile.mkdtemp(prefix="lcf-contribution-"))
    try:
        login = run([gh, "api", "user", "--jq", ".login"]).stdout.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            raise subprocess.SubprocessError("unexpected answer from 'gh api user'")
        run([gh, "repo", "fork", repo, "--clone", "--", "--depth=1"], cwd=work)  # origin: your fork
        clone = work / repo.split("/", 1)[1]
        branch = re.sub(r"[^A-Za-z0-9._-]+", "-", f"contribution-{path.stem}")[:80]
        rel = f"contributions/{kind}/{path.name}"
        ensure_directory(clone / "contributions" / kind)
        shutil.copy2(path, clone / rel)
        run([git, "-C", str(clone), "switch", "-c", branch])
        run([git, "-C", str(clone), "add", "--", rel])
        run([git, "-C", str(clone), "commit", "-m", f"Add {kind} pack {path.name}"])
        run([git, "-C", str(clone), "push", "--set-upstream", "origin", branch])
        body = (f"Community {kind} pack exported with LLM Cross-Compiler Framework (signed, privacy scrub "
                "applied). Please review.")
        pr = run([gh, "pr", "create", "--repo", repo, "--head", f"{login}:{branch}", "--title",
                  f"Add {kind} pack {path.stem}", "--body", body], cwd=clone)
        return f"Pull request opened: {pr.stdout.strip()}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
