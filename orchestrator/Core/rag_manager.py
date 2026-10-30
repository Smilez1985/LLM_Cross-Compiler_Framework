#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - RAG Manager (local knowledge base)

Ingests documentation (crawled pages, PDFs, community packs, the framework's own documents) and serves
searches for Ditto and the MCP server. Everything runs locally: FastEmbed/ONNX embeddings, and Qdrant Edge (the Rust
engine of Qdrant) in the process with its storage in the user state directory (no Docker, no open port). A Qdrant
server is used only when the configuration sets qdrant_url. A knowledge base in the storage of earlier versions
(qdrant-client's local mode) is converted to Qdrant Edge on first use and checked; its folder is kept until the person
deletes it (orchestrator/rag/migration.py).

Search results are plain data from external sources; callers wrap them as untrusted before a model sees them.
A cross-encoder can rerank them and drop the irrelevant ones; it is quality on top and never a reason to fail.
"""

import contextlib
import functools
import hashlib
import ipaddress
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Tuple, Union
from urllib.parse import urlparse

from orchestrator.Core.knowledge_snapshots import (SUFFIX, SnapshotNoRoom, delete_snapshots,
                                                   require_room, snapshot_files, snapshot_plan, snapshot_room)
from orchestrator.rag import licences
from orchestrator.rag.chunking import PREAMBLE_RESERVE_TOKENS, chunk_document, passage_text, point_id
from orchestrator.rag.embeddings import (DenseEmbedder, EmbeddingModelSpec, FastEmbedDenseEmbedder,
                                         FastEmbedSparseEmbedder, SparseEmbedder, check_vectors, recommend_model,
                                         resolve_spec, spec_for_slug)
from orchestrator.rag.errors import EmbeddingError, EmbeddingModelNotCached, KnowledgeBaseError
from orchestrator.rag.retrieval import FastEmbedReranker, RerankerSpec, select_reranker
from orchestrator.utils.helpers import redact_secrets, sanitize_filename
from orchestrator.utils.logging import get_logger

if TYPE_CHECKING:  # the storage engine (and FastEmbed) loads on first use, not at framework boot
    from orchestrator.rag.store import KnowledgeStore

# The crawler (Optional): imported with the first Deep Ingest, not with the knowledge base (lazy loading: BeautifulSoup,
# pypdf, langdetect). rag_manager.CrawlerManager is None when it cannot be imported.
def __getattr__(name: str) -> Any:
    if name != "CrawlerManager":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        from orchestrator.Core.crawler_manager import CrawlerManager as crawler_class
    except ImportError:
        crawler_class = None
    globals()["CrawlerManager"] = crawler_class
    return crawler_class


def _crawler_class() -> Any:
    """CrawlerManager (a value set on this module wins, e.g. a test's stand-in), or None without the crawler."""
    return globals()["CrawlerManager"] if "CrawlerManager" in globals() else __getattr__("CrawlerManager")

COLLECTION_NAME = "framework_knowledge"  # alias of the active collection (one collection per embedding model)
MIN_SPARSE_QUERY_TOKENS = 3  # shorter queries use the dense ranking only, unless they hold an identifier
_IDENTIFIER = re.compile(r"\d|_|[A-Z]{2,}|\w[.-]\w")  # E2041, GGML_CUDA, RKNN, librkllmrt.so
# How long a process waits for the local storage while another framework process (GUI, CLI, MCP server) holds it; that
# one releases it after IDLE_CLOSE_SECONDS without use.
LOCK_WAIT_SECONDS = 5.0
# Points written or deleted in the local storage after which it is optimized when it is released (segments merged,
# deleted points dropped; without a graph index this takes moments).
OPTIMIZE_AFTER_WRITES = 1000
RERANK_POOL = 20  # most candidates a search reranks (about 1-2.5 s on one free CPU core)
RERANK_INPUT_CHARS = 1500  # of each candidate; about what fits next to the query into 512 tokens
# Longest idle time before the models are released (7 days); threading.Timer overflows on huge timeouts (on
# Windows above about 49 days).
MAX_MODEL_IDLE_SECONDS = 7 * 24 * 3600.0
MAX_CODE_FILE_BYTES = 512 * 1024
# The framework's own documents (DECISIONS round 8, N12): its documentation always, its code and configuration only when
# asked for. Indexed only by an explicit action of the person or of an AI they confirmed, never by itself.
FRAMEWORK_DOC_PATTERNS = ("README*.md", "CONTRIBUTING*.md", "docs/*.md")
_CODE_FOLDERS = ("orchestrator", "targets", "scripts", "configs", "Docker Setup")
_CODE_SUFFIXES = frozenset({".py", ".sh", ".yml", ".yaml", ".json", ".md"})
_CODE_NAMES = frozenset({"Dockerfile", "dockerfile"})
# Tooling caches and generated files (and every file or folder whose name starts with a dot) are never read.
_CODE_IGNORE = frozenset({"__pycache__", "venv", "node_modules", "dist", "build", "models", "output", "cache", "logs",
                          "backups"})
# The person's settings, which the installation's configs/ holds when it is the config folder; of configs/ only the
# files at its top are read (never the folders the advanced mode keeps there: secrets, audit, backups, ...).
_USER_CONFIG_NAMES = frozenset({"config.yml", "framework.yml"})
SOURCES_TTL = 60.0  # seconds a list of the knowledge sources is reused (cleared at once by every write)
SOURCE_ID_PREFIX = "ks_"
# Provenance of outside text (a crawled page or PDF, a knowledge pack's page): how much hidden text was left out before
# it was stored, {kind: count} with the kinds of rag/html_text.REMOVED_KINDS. Written by the framework only (a pack's
# metadata cannot set it), on every excerpt of what one ingest stored; shown to the person in Knowledge Sources and
# never given to an AI (search results and source listings for AI callers have fixed fields without it).
REMOVED_FIELD = "hidden_removed"
# Payload fields a source listing reads (never the chunk text): provenance, and what the page says about its licence.
_SOURCE_FIELDS = ["source", "title", "content_type", "type", "origin_pack", "root_url", "ingested_at",
                  *licences.META_KEYS, REMOVED_FIELD, "chunk_index", "topic", "target"]
NOT_EXPORTED = "not_exported"   # a source no knowledge pack carries (an installed pack's page, the framework's docs)
# Who holds an excerpt (one stored point): two installed packs, or a pack and your own crawl, that carry the same page
# share its points (a point's id is its source and chunk hash, never the pack). HOLDERS lists every holder (a pack's
# origin_pack, "crawl:<start address>" for your own crawl, "own" for anything else you stored), HOLDER_INFO keeps each
# holder's own fields (OWNER_FIELDS: its digest, topic or target, start address). A newer version of one pack, a
# re-crawl or an uninstall releases only its own hold: a point goes when no holder is left (_release).
HOLDERS = "holders"
HOLDER_INFO = "holder_info"
OWN_HOLDER = "own"
CRAWL_HOLDER = "crawl:"
OWNER_FIELDS = ("origin_pack", "origin_sha256", "topic", "target", "root_url")
_RESERVED_PAYLOAD = {"content", "source", "section", "chunk_index", "chunk_hash", "embedding_model", "ingested_at",
                     "fused_score", "reranker", REMOVED_FIELD, HOLDERS, HOLDER_INFO}


def holder_of(payload: Dict[str, Any]) -> str:
    """The holder an excerpt's own fields name: its pack (origin_pack), your crawl of a start address, or "own"."""
    if payload.get("origin_pack"):
        return str(payload["origin_pack"])
    root = payload.get("root_url")
    return f"{CRAWL_HOLDER}{root}" if root else OWN_HOLDER


def holder_info(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{holder: its own fields} of a stored excerpt; an excerpt stored before holders has one, the one its fields
    name."""
    info = payload.get(HOLDER_INFO)
    if isinstance(info, dict) and info and all(isinstance(v, dict) for v in info.values()):
        return {str(k): dict(v) for k, v in info.items()}
    return {holder_of(payload): {k: payload[k] for k in OWNER_FIELDS if payload.get(k) not in (None, "")}}


def _with_holders(payload: Dict[str, Any], info: Dict[str, Dict[str, Any]], lead: Optional[str] = None) -> dict:
    """The payload with these holders; its own fields are those of `lead` (default: the holder they name now, or the
    first other one: your own pages first)."""
    payload = dict(payload)
    lead = lead if lead in info else holder_of(payload)
    if lead not in info:
        lead = sorted(info, key=lambda h: (h != OWN_HOLDER and not h.startswith(CRAWL_HOLDER), h))[0]
        for key in OWNER_FIELDS:
            payload.pop(key, None)
        payload.update(info[lead])
    payload[HOLDERS] = sorted(info)
    payload[HOLDER_INFO] = info
    return payload


_LOCAL = threading.local()


@contextlib.contextmanager
def local_models_only():
    """Within this block (in this thread) the knowledge base never downloads a model: an embedding or reranking model
    that is not on this computer yet fails with EmbeddingModelNotCached instead. AI tool calls read the knowledge base
    this way, so a search stays read-only and closed-world and never starts a download of about a gigabyte."""
    depth = getattr(_LOCAL, "depth", 0)
    _LOCAL.depth = depth + 1
    try:
        yield
    finally:
        _LOCAL.depth = depth


def models_local_only() -> bool:
    return getattr(_LOCAL, "depth", 0) > 0


def source_id(source: str) -> str:
    """The id of a knowledge source (its address, pack entry or file label): stable across processes."""
    return SOURCE_ID_PREFIX + hashlib.sha256(str(source).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def source_kind(payload: Dict[str, Any]) -> str:
    """Where a chunk came from: an installed knowledge pack, the framework's own code and documentation, a crawled
    web page or PDF, or something else (an evaluation corpus)."""
    if payload.get("origin_pack"):
        return "pack"
    source = str(payload.get("source") or "")
    if payload.get("type") == "internal_code" or source.startswith("code:"):
        return "docs"
    if str(payload.get("content_type") or "").lower() in ("html", "pdf") or source.startswith(("http://", "https://")):
        return "web"
    return "other"


def licence_view(kind: str, meta: Dict[str, Any], repos: Dict[Tuple[str, str], str]) -> Dict[str, str]:
    """A source's licence as the knowledge base shows it, and what a knowledge pack would carry of it (rag/licences.py):
    licence (a recognised id, "other" or ""), copyright (the page's notice, site text), licence_state, pack_share
    (full_text, link_and_facts or not_exported) and share_reason. A licence a pack's metadata names outside the
    recognised ones counts as "other"."""
    licence = str(meta.get("licence") or "")
    if licence not in licences.LICENCES:
        licence = licences.OTHER if licence else ""
    view = {"licence": licence, "copyright": licences.clean(meta.get("copyright") or "")}
    if kind == "pack":
        return {**view, "licence_state": "", "pack_share": NOT_EXPORTED,
                "share_reason": "a page of an installed knowledge pack: its author shares it, it is not exported again"}
    if kind == "docs":
        return {**view, "licence_state": "", "pack_share": NOT_EXPORTED,
                "share_reason": "the framework's own documents: every installation indexes them itself"}
    plan = licences.plan_for({**meta, "licence": licence}, repos)
    return {**view, "licence": plan.licence or licence, "licence_state": plan.state, "pack_share": plan.shared_as,
            "share_reason": plan.reason}


def shipped_target(folder: Path) -> bool:
    """A target the framework ships: the template, or an official target without a pack's provenance file that no AI
    made. A target the person or an AI created, or an installed pack, is not the framework's own (its licence is its
    author's, and it can hold the person's own data), so the index never reads it."""
    if folder.name == "_template":
        return True
    import yaml
    from orchestrator.Core.community_manager import PROVENANCE_NAME, ai_generated_target
    try:
        meta = yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    caps = meta.get("capabilities") if isinstance(meta, dict) else None
    return (isinstance(caps, dict) and caps.get("support_level") == "official"
            and not (folder / PROVENANCE_NAME).exists() and not ai_generated_target(folder))


def framework_documents(root: Union[str, Path], include_code: bool = False) -> Tuple[List[Path], List[Path]]:
    """(documentation, code) the index of the framework's own documents reads below `root`, each sorted: README*,
    CONTRIBUTING* and docs/*.md; with include_code also its code and configuration (the shell scripts at the top and
    the folders of _CODE_FOLDERS, without caches, generated files and names that start with a dot; of targets/ only
    the targets the framework ships, of configs/ only the files at its top without the person's settings). Files
    above MAX_CODE_FILE_BYTES and links are left out. The order is the same on every system: by the names of the
    parts below `root`, with letter case (Windows compares paths without it, which put docs/ before README.md)."""
    root = Path(root)

    def by_name(paths) -> List[Path]:
        return sorted(paths, key=lambda path: path.relative_to(root).parts)

    def usable(path: Path) -> bool:
        try:
            return path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_CODE_FILE_BYTES
        except OSError:
            return False

    docs = by_name({p for pattern in FRAMEWORK_DOC_PATTERNS for p in root.glob(pattern) if usable(p)})
    code: List[Path] = []
    if include_code:
        code = by_name(p for p in root.glob("*.sh") if usable(p))
        for top in _CODE_FOLDERS:
            base = root / top
            if not base.is_dir() or base.is_symlink():
                continue
            if top == "configs":
                code += by_name(p for p in base.iterdir() if p.suffix in _CODE_SUFFIXES and usable(p)
                                and p.name not in _USER_CONFIG_NAMES and not p.name.startswith("."))
                continue
            for folder, dirs, names in os.walk(base):  # never follows a linked folder
                here = Path(folder)
                dirs[:] = sorted(d for d in dirs if d not in _CODE_IGNORE and not d.startswith(".")
                                 and not d.endswith(".egg-info")
                                 and not (top == "targets" and here == base and not shipped_target(here / d)))
                for name in sorted(names):
                    path = here / name
                    if (not name.startswith(".") and (path.suffix in _CODE_SUFFIXES or name in _CODE_NAMES)
                            and usable(path)):
                        code.append(path)
    return docs, code


@dataclass
class SearchResult:
    score: float
    content: str
    source: str
    metadata: Dict[str, Any]


def fetch_model_files(cache_dir: Path, repo: str, required, optional=(), *, offline: bool = False):
    """The knowledge base's models come from Hugging Face through the framework's own fetcher into fastembed's cache
    (DECISIONS round 25 Q2): hf_hub.fetch_snapshot, without any token; in offline mode only a copy that is there."""
    from orchestrator.Core.hf_hub import fetch_snapshot
    return fetch_snapshot(cache_dir, repo, required, optional, offline=offline)


def create_dense_embedder(spec: EmbeddingModelSpec, cache_dir: Path, offline: Callable[[], bool]) -> DenseEmbedder:
    return FastEmbedDenseEmbedder(spec, cache_dir, offline, fetch=fetch_model_files)


def create_sparse_embedder(cache_dir: Path, offline: Callable[[], bool]) -> SparseEmbedder:
    return FastEmbedSparseEmbedder(cache_dir, offline, fetch=fetch_model_files)


def create_reranker(spec: RerankerSpec, cache_dir: Path, offline: Callable[[], bool]):
    return FastEmbedReranker(spec, cache_dir, offline, fetch=fetch_model_files)


def _clean_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Caller metadata for the payload (community packs are untrusted): bounded scalars, no reserved keys."""
    clean: Dict[str, Any] = {}
    for key, value in list((metadata or {}).items())[:50]:
        key = str(key)[:64]
        if key in _RESERVED_PAYLOAD:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:2000]
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
            clean[key] = [v[:200] if isinstance(v, str) else v for v in list(value)[:50]]
    return clean


def _add_counts(total: Dict[str, int], found: Dict[str, Any]) -> Dict[str, int]:
    for kind, count in (found or {}).items():
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            total[str(kind)] = total.get(str(kind), 0) + count
    return total


def clean_outside_text(content: str, markup: bool = True,
                       removed: Optional[Dict[str, int]] = None) -> Tuple[str, str, Dict[str, int]]:
    """Outside text the way the knowledge base stores it (recon P1). With markup (Markdown or text that may carry
    HTML, e.g. a knowledge pack's page): a whole HTML page is read like a crawled one (rag/html_text.page_markdown),
    and the hidden elements of HTML inside Markdown are cut out (strip_hidden_markup). Then always the untrusted-text
    cleaning that a model's view of the text gets too (utils/untrusted.clean_untrusted: NFKC, invisible characters,
    HTML comments), without a length limit. By then the text is what its reader sees (real comments are gone), so a
    "<!--" that nothing closes is visible text: it loses only those characters, not the rest of the page. Returns (the
    title of a whole HTML page or "", the text, the counts by kind: `removed`, e.g. what the crawler left out of the
    page, plus what was found here)."""
    from orchestrator.utils.untrusted import clean_untrusted
    counts = _add_counts({}, removed or {})
    title = ""
    if markup and "<" in content:   # bs4 loads only for text that may carry HTML
        from orchestrator.rag.html_text import is_html_document, page_markdown, strip_hidden_markup
        if is_html_document(content):
            title, content, found = page_markdown(content)
        else:
            content, found = strip_hidden_markup(content)
        _add_counts(counts, found)
    content, found = clean_untrusted(content, keep_after_open_comment=True)
    return title, content, _add_counts(counts, found)


def _restart_timer(timer: Optional[threading.Timer], seconds: float, function=None) -> Optional[threading.Timer]:
    """Cancels `timer`; for seconds > 0 returns a started daemon timer that calls `function`. The function checks
    that it is still the current timer: a cancel comes too late for a timer that has already fired."""
    if timer is not None:
        timer.cancel()
    if seconds <= 0:
        return None
    timer = threading.Timer(seconds, function)
    timer.daemon = True
    timer.start()
    return timer


def _uses_models(method):
    """Marks a call that uses the models: no idle countdown while one runs; it starts when the last one ends."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._model_lock:
            self._models_in_use += 1
            self._model_timer = _restart_timer(self._model_timer, 0)
        try:
            return method(self, *args, **kwargs)
        finally:
            with self._model_lock:
                self._models_in_use -= 1
                if not self._models_in_use:
                    self._model_timer = _restart_timer(None, self._model_idle(), self._release_if_idle)
    return wrapper


def _kind_of(meta: Dict[str, Any]) -> str:
    content_type = str(meta.get("content_type", "")).lower()
    if content_type in ("pdf", "text"):
        return "text"
    if content_type == "code" or (meta.get("type") == "internal_code"
                                  and str(meta.get("extension", "")).lower() not in (".md", ".markdown")):
        return "code"
    return "markdown"


class RAGManager:
    """
    The framework's semantic memory. Storage is opened and models are loaded on first use; the embedded
    storage is released again after a short idle time, so the GUI and the MCP server can take turns, and the
    models after rag_model_idle_minutes (reloading them takes seconds, so not with the storage).
    """

    IDLE_CLOSE_SECONDS = 30.0

    def __init__(self, framework_manager, embedder: Optional[DenseEmbedder] = None,
                 sparse_embedder: Optional[SparseEmbedder] = None, reranker=None):
        self.logger = get_logger("RAGManager")
        self.framework = framework_manager
        self.config = getattr(framework_manager, "config", None)
        installation = getattr(getattr(framework_manager, "info", None), "installation_path", None)
        self.app_root = Path(installation) if installation else Path(".").resolve()

        self.client = None
        self._connected = False
        self._server_mode = False
        self._server_offline: Optional[bool] = None  # offline mode when the Qdrant server client was opened
        self._file_lock = None  # the local storage's cross-process lock while it is open (edge_store.storage_lock)
        self._convert_now = False  # the person asked to try the conversion of the old storage again
        self._conversion_checked = False  # the conversion was looked at (and tried when due) in this process
        self.storage_format = ""  # qdrant-edge, old-local or server, once opened
        self.storage_reason = ""  # why the old local storage is still in use (its conversion failed)
        self._store: Optional["KnowledgeStore"] = None
        self._embedder = embedder
        self._sparse = sparse_embedder
        self.model_reason = "provided by the caller" if embedder is not None else ""
        self.last_error = ""
        self.last_snapshot_room = None  # the disk room of the last snapshot looked at (knowledge_snapshots.SnapshotRoom)
        self.idle_close_seconds = self.IDLE_CLOSE_SECONDS
        self._lock = threading.RLock()
        self._idle_timer: Optional[threading.Timer] = None
        # Reranker: chosen from rag_reranker on first use (or provided); scores(query, texts) -> 0..1 each.
        self._reranker = reranker
        self._reranker_setting: Any = None if reranker is None else "provided"  # (setting, revision) last seen
        self._reranker_pending = False  # the setting changed since the last attempt to create the reranker
        self.reranker_reason = ""  # why reranker() returns None
        self._model_lock = threading.Lock()
        self._model_timer: Optional[threading.Timer] = None
        self._models_in_use = 0
        self._sources: Optional[Tuple[float, List[Dict[str, Any]]]] = None  # (time, list_sources()) for SOURCES_TTL

    # --- configuration ---

    def _cfg(self, key: str, default: Any = None) -> Any:
        cfg = self.config
        if cfg is None:
            return default
        value = cfg.get(key, default) if hasattr(cfg, "get") else getattr(cfg, key, default)
        return default if value is None else value

    def _offline(self) -> bool:
        # Ditto's own switch counts too: offline mode must never download a model.
        # So does a tool call of an AI (local_models_only): it never downloads a model either.
        from orchestrator.utils.locality import offline_mode
        ditto = getattr(self.framework, "ditto_manager", None)
        return (offline_mode(self.config) or getattr(ditto, "offline_mode", False) is True
                or models_local_only())

    def _state_dir(self) -> Path:
        try:
            state = getattr(self.config, "user_state_dir", None)
        except Exception:
            state = None
        if state is None:
            state = Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")
            state.mkdir(parents=True, exist_ok=True)
        return Path(state)

    @property
    def storage_dir(self) -> Path:
        """The local knowledge base: the Qdrant Edge storage folder."""
        from orchestrator.rag.migration import EDGE_FOLDER
        return self._state_dir() / EDGE_FOLDER

    @property
    def old_storage_dir(self) -> Path:
        """The storage folder of earlier versions (qdrant-client's local mode), converted on first use."""
        from orchestrator.rag.migration import OLD_FOLDER
        return self._state_dir() / OLD_FOLDER

    @property
    def snapshot_dir(self) -> Path:
        return self._state_dir() / "knowledge_snapshots"

    @property
    def models_cache_dir(self) -> Path:
        models_dir = str(self._cfg("models_dir", "models"))
        resolve = getattr(self.framework, "resolve_path", None)
        if callable(resolve):
            base = Path(resolve(models_dir))
        else:
            base = Path(models_dir) if Path(models_dir).is_absolute() else self.app_root / models_dir
        return base / "embeddings"

    def _server_url(self) -> str:
        return str(self._cfg("qdrant_url", "") or "").strip()

    def _server_api_key(self, url: str) -> Optional[str]:
        """The API key for a Qdrant server; remote servers need one and https, only loopback may go without."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            raise KnowledgeBaseError("qdrant_url must be an http(s) URL such as https://qdrant.example:6333.")
        if parsed.username is not None or parsed.password is not None:
            # The URL is stored in the plain configuration file and appears in connection errors.
            raise KnowledgeBaseError("qdrant_url must not contain credentials; store the API key as the secret "
                                     "'qdrant_api_key'.")
        try:
            _ = parsed.port  # raises for a non-numeric or out-of-range port
        except ValueError:
            raise KnowledgeBaseError("qdrant_url has an invalid port.") from None
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and ip.is_unspecified:
            raise KnowledgeBaseError("qdrant_url must name the server, not the listen address 0.0.0.0 / ::.")
        key = None
        secrets_manager = getattr(self.framework, "secrets_manager", None)
        if secrets_manager is not None:
            try:
                key = secrets_manager.get_secret("qdrant_api_key")
            except Exception:
                key = None
        if not (host == "localhost" or (ip is not None and ip.is_loopback)):
            if not key:
                raise KnowledgeBaseError(f"The Qdrant server at {host} needs an API key: store it as the secret "
                                         "'qdrant_api_key'. Only a server on this machine is used without one.")
            if parsed.scheme != "https":
                raise KnowledgeBaseError(f"Use https for the Qdrant server at {host}, so the API key is not "
                                         "sent in clear text.")
        return key or None

    def _offline_now(self) -> bool:
        """Offline mode (the setting offline_mode) as a Qdrant server client is opened under it."""
        from orchestrator.utils.locality import offline_mode
        return offline_mode(self.config)

    def _offline_server_options(self, url: str) -> Dict[str, Any]:
        """In offline mode (round 24 L-NET: every reach-out on the rule) the Qdrant server must be on this computer or in
        the person's own network by the network-locality rule with the person's opt-outs (a cloud Qdrant is refused
        with the plain text, KnowledgeBaseError); qdrant-client then connects only to the checked local addresses,
        through no proxy (security/http.pinned_transport), and skips its own version request (an unpinned request in
        a thread of its own). {} when offline mode is off."""
        from orchestrator.utils.locality import check_reach, offline_mode
        if not offline_mode(self.config):
            return {}
        verdict = check_reach(url, self.config)
        if not verdict.local:
            raise KnowledgeBaseError(verdict.refusal("the Qdrant server (setting qdrant_url)"))
        import httpcore
        import httpx

        from orchestrator.security.http import pinned_transport
        return {"check_compatibility": False, "trust_env": False,
                "transport": pinned_transport(self.config, "the Qdrant server", httpx=httpx, httpcore=httpcore)}

    # --- storage lifecycle ---

    def _open_client(self) -> Tuple[Any, bool, type]:
        """(client, server mode, store class): a Qdrant server when qdrant_url is set, else the local storage."""
        url = self._server_url()
        if url:
            from qdrant_client import QdrantClient

            from orchestrator.rag.client_store import ClientKnowledgeStore
            pinned = self._offline_server_options(url)   # offline mode: only a Qdrant server in the own network
            client = QdrantClient(url=url, api_key=self._server_api_key(url), timeout=10, **pinned)
            client.get_collections()  # fail now, not on the first search
            return client, True, ClientKnowledgeStore
        return self._open_local()

    def _busy_error(self) -> KnowledgeBaseError:
        return KnowledgeBaseError(
            f"The knowledge base storage {self.storage_dir} is in use by another framework process (GUI, CLI or MCP "
            f"server). It is released after {int(self.IDLE_CLOSE_SECONDS)} s without use: try again shortly, or set "
            "qdrant_url to share a Qdrant server.")

    def _open_local(self) -> Tuple[Any, bool, type]:
        """The local knowledge base under the storage lock: the Qdrant Edge storage, after converting the old storage
        the first time (migration.run); the old storage itself while its conversion failed."""
        from orchestrator.rag import migration
        from orchestrator.rag.storage_lock import storage_lock, try_lock
        lock = storage_lock(self.storage_dir)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while not try_lock(lock):  # another process holds the storage: it releases it after its idle time
            if time.monotonic() > deadline:
                raise self._busy_error()
            time.sleep(0.25)
        try:
            force, self._convert_now = self._convert_now, False
            if self._conversion_checked and not force:  # tried once in this process; the next start tries again
                use, reason = migration.current(self._state_dir())
            else:
                use, reason = migration.run(self._state_dir(), force=force, log=self.logger.info)
                self._conversion_checked = True
            if use == "old":
                try:
                    from qdrant_client import QdrantClient
                except ImportError:
                    raise KnowledgeBaseError("The knowledge base is in the storage of earlier versions, which needs "
                                             "the 'qdrant-client' package.") from None

                from orchestrator.rag.client_store import ClientKnowledgeStore
                try:
                    client = QdrantClient(path=str(self.old_storage_dir), force_disable_check_same_thread=True)
                except RuntimeError as e:
                    if "already accessed" in str(e):
                        raise self._busy_error() from None
                    raise
                self.storage_reason = reason
                store_cls = ClientKnowledgeStore
            else:
                from orchestrator.rag.edge_store import EdgeKnowledgeStore, EdgeStorage
                client, store_cls = EdgeStorage(self.storage_dir), EdgeKnowledgeStore
                self.storage_reason = ""
        except BaseException:
            lock.release()
            raise
        self._file_lock = lock
        return client, False, store_cls

    def convert_storage(self) -> Dict[str, Any]:
        """Tries the conversion of the old local storage to Qdrant Edge again now (the person asked: `llm-cli
        knowledge storage convert`), also after a failure that would not be retried by itself. Returns
        migration.summary plus "used" (qdrant-edge or old-local) and "error" when the knowledge base cannot be
        opened."""
        from orchestrator.rag import migration
        with self._lock:
            self._close_client()
            self._convert_now = True
            try:
                self._ensure_store()
                error = ""
            except KnowledgeBaseError as e:
                self._fail(e)
                error = self.last_error
            finally:
                self._convert_now = False
            result = migration.summary(self._state_dir())
            result.update(used=self.storage_format, error=error)
            self._schedule_idle_close()
            return result

    def _choose_model(self, client) -> Tuple[str, str]:
        configured = str(self._cfg("rag_embedding_model", "auto") or "auto").strip()
        if configured.lower() != "auto":
            return configured, "set in the configuration (rag_embedding_model)"
        from orchestrator.rag.store import KnowledgeStore  # its alias look-up works on every backend

        target = KnowledgeStore.alias_target(client, COLLECTION_NAME)
        if target:
            model = KnowledgeStore.model_of(client, target)
            if not model:
                spec = spec_for_slug(target.split("__", 1)[-1])
                model = spec.model_id if spec else None
            if model:
                return model, "the model the existing knowledge base was built with"
        return recommend_model()

    def _ensure_store(self) -> "KnowledgeStore":
        with self._lock:
            if self._store is not None:
                if not getattr(self, "_server_mode", False) \
                        or getattr(self, "_server_offline", None) == self._offline_now():
                    return self._store
                # offline mode was switched since the Qdrant server was opened: the client is not used again, the
                # server is judged by the locality rule anew (round 24 L-NET: every reach-out on the rule)
                self._close_client()
            try:
                client, server, store_cls = self._open_client()
            except KnowledgeBaseError:
                raise
            except ImportError as e:
                package = "qdrant-client" if self._server_url() else "qdrant-edge-py"
                raise KnowledgeBaseError(f"The knowledge base needs the '{package}' package.") from e
            except Exception as e:
                raise KnowledgeBaseError(f"Could not open the knowledge base: {redact_secrets(str(e))}") from e
            try:
                if self._embedder is None:
                    model_id, reason = self._choose_model(client)
                    self._embedder = create_dense_embedder(resolve_spec(model_id), self.models_cache_dir,
                                                           self._offline)
                    self.model_reason = reason
                if self._sparse is None:
                    self._sparse = create_sparse_embedder(self.models_cache_dir, self._offline)
                store = store_cls(client, COLLECTION_NAME, self._embedder.model_id, self._embedder.dim,
                                  server_mode=server)
                store.ensure()
            except Exception as e:
                try:
                    client.close()
                except Exception:
                    pass
                self._release_file_lock()
                if isinstance(e, KnowledgeBaseError):
                    raise
                raise KnowledgeBaseError(f"Could not prepare the knowledge base: {redact_secrets(str(e))}") from e
            self.client, self._store, self._connected, self._server_mode = client, store, True, server
            self._server_offline = self._offline_now() if server else None   # how the Qdrant server was opened
            self.storage_format = store.store_format
            self.last_error = ""
            kind = "Qdrant Edge" if store.store_format == "qdrant-edge" else "old format: " + self.storage_reason
            where = "Qdrant server" if server else f"embedded storage {self._local_folder()} ({kind})"
            self.logger.info(f"Knowledge base ready: {store.collection} in {where}; "
                             f"model {store.model_id} ({self.model_reason})")
            return store

    def _with_store(self, operation: Callable[["KnowledgeStore"], Any]) -> Any:
        with self._lock:
            store = self._ensure_store()
            try:
                return operation(store)
            except KnowledgeBaseError:
                raise
            except Exception as e:
                raise KnowledgeBaseError(f"Knowledge base operation failed: {redact_secrets(str(e))}") from e
            finally:
                self._schedule_idle_close()

    def _schedule_idle_close(self) -> None:
        if not self._server_mode:
            self._idle_timer = _restart_timer(self._idle_timer, self.idle_close_seconds or 0, self._close_if_idle)

    def _close_if_idle(self) -> None:
        with self._lock:
            if self._idle_timer is threading.current_thread():  # else used, closed or rescheduled meanwhile
                self._idle_timer = None
                self._close_client()

    def _close_client(self) -> None:
        store = self._store
        if store is not None and getattr(store, "written", 0) >= OPTIMIZE_AFTER_WRITES:
            try:  # Qdrant Edge: merge segments and drop deleted points before the storage is released
                started = time.monotonic()
                store.optimize()
                self.logger.info(f"Knowledge base storage optimized in {time.monotonic() - started:.1f} s.")
            except Exception as e:  # noqa: BLE001 - only housekeeping: the data is written already
                self.logger.warning(f"Knowledge base: optimizing the storage failed ({type(e).__name__}).")
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client, self._store, self._connected = None, None, False
        self._release_file_lock()

    def _release_file_lock(self) -> None:
        lock, self._file_lock = self._file_lock, None
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001 - a lock file that vanished: nothing is held any more
                pass

    def _local_folder(self) -> Path:
        """The local storage folder in use: Qdrant Edge's, or the old one while its conversion failed."""
        return self.old_storage_dir if self.storage_format == "old-local" else self.storage_dir

    def close(self) -> None:
        """Releases the storage (the embedded one is locked by this process while open). Models stay loaded
        (release_models() frees them); their idle release starts again with the next use."""
        with self._lock:
            self._idle_timer = _restart_timer(self._idle_timer, 0)
            self._close_client()
        with self._model_lock:
            self._model_timer = _restart_timer(self._model_timer, 0)

    # --- model memory ---

    def _model_idle(self) -> float:
        """Seconds after the last use until the models are released (rag_model_idle_minutes; 0 = never), at most
        MAX_MODEL_IDLE_SECONDS: threading.Timer fails in its own thread on a huge timeout, and the models would
        then never be released."""
        try:
            seconds = float(self._cfg("rag_model_idle_minutes", 10)) * 60
        except OverflowError:  # an integer too large for a float
            return MAX_MODEL_IDLE_SECONDS
        except (TypeError, ValueError):
            return 600.0
        if math.isnan(seconds):
            return 600.0
        return min(seconds, MAX_MODEL_IDLE_SECONDS)

    def _release_if_idle(self) -> None:
        with self._model_lock:  # a use that starts now waits until the models are released
            if self._model_timer is threading.current_thread():  # else a use started meanwhile, or close()
                self._model_timer = None
                self.release_models()

    def release_models(self) -> List[str]:
        """Frees the memory of the loaded models (embeddings, reranker); the next search loads them again."""
        released = []
        for model in (self._embedder, self._sparse, self._reranker):
            release = getattr(model, "release", None)
            if callable(release) and release():
                released.append(str(model.model_id))
        if released:
            self.logger.info(f"Knowledge base: released the idle models {', '.join(released)}.")
        return released

    def _connect(self) -> bool:
        try:
            self._with_store(lambda store: None)
            return True
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    def _fail(self, error: Exception) -> None:
        self.last_error = redact_secrets(str(error))
        self.logger.warning(f"Knowledge base: {self.last_error}")

    def _models(self) -> Tuple[DenseEmbedder, SparseEmbedder]:
        return self._with_store(lambda store: (self._embedder, self._sparse))

    def embedding_download(self) -> Tuple[Optional[bool], float]:
        """(needed, about how many GB) of the models the next indexing loads first and downloads when they are not on
        this computer yet: the embedding model and the keyword model. needed is True or False, None when it cannot be
        told (then the GB are those of all of them). Reads only: never downloads, never loads a model, and never opens
        the knowledge base for this; a model not created yet is the one first use would choose (the setting
        rag_embedding_model, the open knowledge base's model, else the one recommended for this computer)."""
        with self._lock:
            dense, sparse, store = self._embedder, self._sparse, self._store
        try:
            if dense is None:
                configured = str(self._cfg("rag_embedding_model", "auto") or "auto").strip()
                model_id = (configured if configured.lower() != "auto" else
                            store.model_id if store is not None else recommend_model()[0])
                dense = create_dense_embedder(resolve_spec(model_id), self.models_cache_dir, lambda: True)
            if sparse is None:
                sparse = create_sparse_embedder(self.models_cache_dir, lambda: True)
        except Exception:  # noqa: BLE001 - an unknown model or no fastembed: it cannot be told
            return None, 0.0
        missing, unknown, every = 0.0, False, 0.0
        for model in (dense, sparse):
            cached = getattr(model, "cached", None)
            if not callable(cached):
                continue   # a model the caller gave the knowledge base: it is there
            size = float(getattr(model, "size_gb", 0) or 0)
            every += size
            state = cached()
            if state is None:
                unknown = True
            elif not state:
                missing += size
        if missing:
            return True, missing
        return (None, every) if unknown else (False, 0.0)

    @_uses_models
    def prepare_models(self) -> bool:
        """Downloads (unless offline) and loads the embedding models, and the reranker unless rag_reranker
        rules it out, now instead of on first use."""
        try:
            models = list(self._models())
            reranker = self.reranker()
            if reranker is not None:
                models.append(reranker)
            for model in models:
                prepare = getattr(model, "prepare", None)
                if callable(prepare):
                    prepare()
            return True
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    # --- ingestion ---

    def ingest_document(self, source_name: str, content: str, metadata: Optional[Dict[str, Any]] = None,
                        replace: bool = False, *, outside: bool = False) -> bool:
        """
        Indexes a document; re-ingesting the same content updates it in place. replace=True also removes
        chunks only its previous version had - only for complete documents (a page, a file), never for
        callers that ingest one document piece by piece (community packs).
        outside=True: outside text is stored cleaned (clean_outside_text: hidden HTML elements, invisible characters
        and HTML comments left out), with how much was left out (REMOVED_FIELD). A knowledge pack's page (metadata
        with origin_pack) always counts as outside text, whoever stores it.
        """
        try:
            return self._ingest(source_name, content, metadata, replace, outside=outside) > 0
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    def remove_pack_leftovers(self, origin_pack: str, keep_sha256: str) -> bool:
        """After every page of an installed pack was imported (CommunityManager.sync_knowledge_base): removes the
        excerpts of that pack (origin_pack) that this import did not write, the ones of an older version (another
        origin_sha256, or none: imported before excerpts carried it). A newer version of a knowledge pack, or of a
        target pack's knowledge, thus replaces the old one without stale excerpts, per pack and page source rather than
        per piece. Excerpts of other packs and your own pages stay. False when the knowledge base cannot be written."""
        if not origin_pack or not keep_sha256:
            return False
        return self.release_holder(origin_pack, keep=lambda own: own.get("origin_sha256") == keep_sha256)

    def release_holder(self, holder: str, keep: Optional[Callable[[Dict[str, Any]], bool]] = None,
                       sources: Optional[Callable[[str], bool]] = None) -> bool:
        """Lets `holder` (an installed pack's origin_pack, "crawl:<start address>") go of its excerpts: of every one,
        or only of those whose holder fields `keep` does not accept, or only of the sources `sources` accepts. An
        excerpt another holder still has stays (only this holder is taken off it); one nobody holds any more is
        removed. False when the knowledge base cannot be written."""
        if not holder:
            return False
        try:
            def remove(store: "KnowledgeStore"):
                self._sources = None
                found = {r.id: r for r in store.records_where(HOLDERS, holder, with_vectors=True)}
                legacy = "root_url" if holder.startswith(CRAWL_HOLDER) else "origin_pack"   # stored before holders
                value = holder[len(CRAWL_HOLDER):] if holder.startswith(CRAWL_HOLDER) else holder
                if holder != OWN_HOLDER:
                    found.update({r.id: r for r in store.records_where(legacy, value, with_vectors=True)
                                  if r.id not in found and holder in holder_info(r.payload)})
                records = [r for r in found.values()
                           if (sources is None or sources(str(r.payload.get("source") or "")))
                           and not (keep and keep(holder_info(r.payload).get(holder, {})))]
                self._release(store, records, holder)
            self._with_store(remove)
            return True
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    @staticmethod
    def _release(store: "KnowledgeStore", records, holder: str) -> None:
        """Takes `holder` off these stored points (read with their vectors): a point nobody else holds is removed, one
        another holder has keeps that holder's fields."""
        from orchestrator.rag.store import DENSE, sparse_of
        gone, kept = [], []
        for record in records:
            info = holder_info(record.payload)
            if holder not in info:
                continue
            info.pop(holder)
            vector = record.vector if isinstance(record.vector, dict) else {}
            if not info or vector.get(DENSE) is None:
                gone.append(record.id)
                continue
            kept.append(store.point(record.id, list(vector[DENSE]), sparse_of(vector),
                                    _with_holders(record.payload, info)))
        if kept:
            store.upsert(kept)
        if gone:
            store.delete_ids(gone)

    def ingest_page(self, doc: Any, root_url: str = "") -> int:
        """Stores one page the crawler extracted (a crawler Document: page_content, metadata and hidden_removed, the
        hidden elements it left out), replacing its earlier version, as outside text: cleaned like every outside text,
        with what was left out in REMOVED_FIELD. root_url: the address the crawl started at. Returns the number of
        excerpts stored; raises KnowledgeBaseError. Deep Ingest (ingest_url) stores every page through this."""
        meta = dict(getattr(doc, "metadata", None) or {})
        if root_url:
            meta["root_url"] = root_url
        return self._ingest(meta.get("source", root_url), doc.page_content, meta, replace=True, outside=True,
                            markup=False, removed=getattr(doc, "hidden_removed", None))

    @_uses_models
    def _ingest(self, source_name: str, content: str, metadata: Optional[Dict[str, Any]], replace: bool,
                outside: bool = False, markup: bool = True, removed: Optional[Dict[str, int]] = None) -> int:
        """outside: clean the text first (clean_outside_text; markup: it may carry HTML) and record what was left out
        (`removed`, e.g. by the crawler, plus what the cleaning found) in REMOVED_FIELD of every excerpt."""
        if not isinstance(content, str) or not content.strip():
            return 0
        if not isinstance(metadata, dict):
            metadata = {}  # community packs are untrusted JSON
        source = str(source_name or "").strip()[:500] or "unknown"
        base_section = str(metadata.get("section") or "")
        meta = _clean_metadata(metadata)
        if outside or meta.get("origin_pack"):   # a pack's page is outside text, whoever stores it
            from orchestrator.utils.untrusted import clean_untrusted
            page_title, content, removed = clean_outside_text(content, markup and _kind_of(meta) == "markdown",
                                                              removed)
            if not content.strip():
                return 0
            title = clean_untrusted(str(meta.get("title") or page_title))[0]
            meta.update({"title": title} if title else {})
            base_section = clean_untrusted(base_section)[0]
            meta[REMOVED_FIELD] = removed
        embedder, sparse = self._models()
        spec = getattr(embedder, "spec", None)
        budget = int(getattr(spec, "chunk_tokens", 384))
        context = getattr(spec, "max_tokens", None)  # code blocks and tables beyond it are split
        hard = max(int(context) - PREAMBLE_RESERVE_TOKENS, budget // 2) if context else None
        chunks = chunk_document(content, count_tokens=embedder.count_tokens, max_tokens=budget,
                                overlap_tokens=budget // 8, kind=_kind_of(meta), base_section=base_section,
                                hard_tokens=hard)
        unique = {}
        for chunk in chunks:  # identical chunks of one document collapse into one point
            unique.setdefault(point_id(source, chunk.hash), chunk)
        if not unique:
            return 0
        ids, items = list(unique), list(unique.values())
        title = str(meta.get("title") or "")
        texts = [passage_text(source, title, c.section, c.text) for c in items]
        dense = check_vectors(embedder.embed_passages(texts), len(texts), embedder.dim, embedder.model_id)
        sparse_vectors = list(sparse.embed_passages(texts))
        if len(sparse_vectors) != len(texts):
            raise EmbeddingError(f"{sparse.model_id} returned {len(sparse_vectors)} vectors for {len(texts)} texts.")
        now = datetime.now().isoformat(timespec="seconds")
        payloads = []
        for chunk in items:
            payload = dict(meta)
            payload.update(content=chunk.text, source=source, section=chunk.section, chunk_index=chunk.index,
                           chunk_hash=chunk.hash, embedding_model=embedder.model_id, ingested_at=now)
            payloads.append(payload)

        me = holder_of(meta)

        def write(store: "KnowledgeStore"):
            self._sources = None
            # the holders the points have already (another pack, your own crawl of the same page) stay
            before = {r.id: r.payload for r in store.retrieve(ids, with_vectors=False)}
            for pid, payload in zip(ids, payloads):
                info = holder_info(before[pid]) if pid in before else {}
                info[me] = {k: payload[k] for k in OWNER_FIELDS if payload.get(k) not in (None, "")}
                payload.update(_with_holders(payload, info, me))
            store.upsert([store.point(*row) for row in zip(ids, dense, sparse_vectors, payloads)])
            if replace:   # what this source's new version no longer has: only this holder lets go of it
                keep = set(ids)
                self._release(store, [r for r in store.source_records(source, with_vectors=True) if r.id not in keep],
                              me)

        self._with_store(write)
        return len(ids)

    # --- DEEP INGEST (WEB/PDF) ---
    def ingest_url(self, url: str, crawler_options: Optional[Dict[str, Any]] = None,
                   stop: Optional[threading.Event] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None, *, actor: str = "") -> Dict[str, Any]:
        """
        Crawls a URL and stores each page as soon as it is read. A snapshot is taken first, so a bad crawl can
        be rolled back. crawler_options apply to this crawl only (see CrawlerManager). Setting stop ends the
        crawl after the current page and keeps what is stored; progress gets (page, max pages, url). `actor`: who
        starts it ("gui" or "cli" for the person: in offline mode the crawl may then read the own network;
        CrawlerManager); an AI's crawl (crawl_jobs) passes none.

        "outcome" is "success" (pages stored, nothing failed or reserved), "partial" (pages stored, but others
        failed, were reserved by the site, or the crawl was stopped) or "failure" (nothing stored). "skipped"
        and "failed" list pages as {"url", "reason", "opt_out"}.
        """
        crawler_class = _crawler_class()
        if not crawler_class:
            return {"success": False, "outcome": "failure", "message": "Crawler module missing"}
        if not self._connect():
            return {"success": False, "outcome": "failure",
                    "message": f"Knowledge base unavailable: {self.last_error}"}
        try:
            snapshot = self.create_snapshot(f"pre_ingest_{int(time.time())}", "a Deep Ingest")  # None while empty
        except SnapshotNoRoom as e:  # no snapshot, so no crawl either: there is no "continue anyway"
            return dict(self._crawl_result(None, 0, 0, str(e)), snapshot=None, blocker=e.blocker)

        crawler, pages, chunks, error = None, 0, 0, None
        try:
            # the person's actor only when there is one: a stand-in crawler class of earlier tests takes none
            crawler = crawler_class(self.framework, crawler_options, **({"actor": actor} if actor else {}))
            self.logger.info(f"Invoking Crawler for {url}...")
            for doc in crawler.iter_crawl(url, stop=stop, progress=progress):
                added = self.ingest_page(doc, url)
                if added:
                    pages += 1
                    chunks += added
        except KnowledgeBaseError as e:
            self._fail(e)
            error = self.last_error
        except Exception as e:
            error = redact_secrets(str(e))
            self.logger.error(f"Deep Ingest failed: {error}")
        return dict(self._crawl_result(crawler, pages, chunks, error), snapshot=snapshot)

    @staticmethod
    def _crawl_result(crawler: Any, pages: int, chunks: int, error: Optional[str]) -> Dict[str, Any]:
        """The outcome of one crawl; it reports only what was really stored (the writes have returned)."""
        skipped = [asdict(page) for page in getattr(crawler, "skipped", [])]
        failed = [asdict(page) for page in getattr(crawler, "failed", [])]
        stopped = bool(getattr(crawler, "stopped", False))
        notes = [f"{len(skipped)} pages skipped"] if skipped else []
        notes += [f"{len(failed)} failed"] if failed else []
        note = f", {', '.join(notes)} (see the log)" if notes else ""
        note += ", stopped on request" if stopped else ""
        if not pages:
            outcome = "failure"
            message = (error or ("Stopped before a page was stored" if stopped else "No documents found")) + note
        else:
            reserved = any(page["opt_out"] for page in skipped)
            outcome = "partial" if (error or failed or reserved or stopped) else "success"
            message = f"Ingested {pages} pages ({chunks} chunks){note}" + (f"; then: {error}" if error else "")
        return {"success": outcome != "failure", "outcome": outcome, "message": message, "count": chunks,
                "documents": pages, "skipped": skipped, "failed": failed, "stopped": stopped}

    # --- THE FRAMEWORK'S OWN DOCUMENTS (N12) ---
    def ingest_codebase(self, root_path: Union[str, Path, None] = None, include_code: bool = False,
                        stop: Optional[threading.Event] = None,
                        progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
        """Indexes the framework's own documentation (README*, CONTRIBUTING*, docs/*.md) and, with include_code, its
        code and configuration, so Ditto and every AI caller (search_knowledge, kind docs) can answer questions about
        the framework itself. An explicit action (GUI, `llm-cli knowledge index-docs`, the tool index_framework_docs),
        never automatic. A snapshot is taken first, as before a Deep Ingest, so the index can be undone with a restore;
        a file indexed again replaces its earlier excerpts. When the knowledge base holds excerpts and that snapshot
        cannot be made, nothing is indexed (snapshot_failed). Every file read but not stored is named with why
        (not_indexed). Setting stop ends it after the current file and keeps what is stored; progress gets (file,
        files, relative path)."""
        root = Path(root_path) if root_path else self.app_root
        if not root.is_dir():
            return {"success": False, "message": "The framework's folder was not found.", "count": 0}
        docs, code = framework_documents(root, include_code)
        files = docs + code
        if not files:
            return {"success": False, "message": "No documentation was found in the framework's folder.", "count": 0}
        if not self._connect():
            return {"success": False, "message": f"Knowledge base unavailable: {self.last_error}", "count": 0}
        try:
            held = self.chunk_count()
        except KnowledgeBaseError as e:
            self._fail(e)
            return {"success": False, "message": str(e), "count": 0}
        try:  # None while the base is empty
            snapshot = self.create_snapshot(f"pre_codebase_{int(time.time())}", "the framework's own documents are "
                                                                                  "indexed")
        except SnapshotNoRoom as e:  # no room for the undo point on the disk: nothing is indexed (eighth round, 2)
            return {"success": False, "count": 0, "chunks": 0, "files": len(files), "snapshot": None,
                    "snapshot_failed": True, "stopped": False, "message": str(e), "blocker": e.blocker}
        if held and not snapshot:  # no undo point (not written): nothing is indexed, never "continue anyway"
            return {"success": False, "count": 0, "chunks": 0, "files": len(files), "snapshot": None,
                    "snapshot_failed": True, "stopped": False,
                    "message": "The snapshot of the knowledge base taken first could not be made, so nothing was "
                               f"indexed ({self.last_error or 'see the log'}). Free disk space and try again."}
        self.logger.info(f"Indexing the framework's own documents: {len(docs)} documentation files, "
                         f"{len(code)} code files.")
        stored = chunks = 0
        stopped = False
        not_indexed: List[Dict[str, str]] = []  # every file read but not stored, with why
        for n, file_path in enumerate(files, 1):
            if stop is not None and stop.is_set():
                stopped = True
                break
            # POSIX form: the source is part of the point id, which must not depend on the OS.
            rel_path = file_path.relative_to(root).as_posix()
            if progress is not None:
                progress(n, len(files), rel_path)
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                if not content.strip():
                    not_indexed.append({"file": rel_path, "reason": "empty"})
                    continue
                meta = {"type": "internal_code", "filepath": rel_path, "filename": file_path.name,
                        "extension": file_path.suffix, "title": rel_path}
                added = self._ingest(f"code:{rel_path}", content, meta, replace=True)
                if added:
                    stored += 1
                    chunks += added
                else:
                    not_indexed.append({"file": rel_path, "reason": "no text to store"})
            except KnowledgeBaseError as e:
                self._fail(e)
                return {"success": False, "message": str(e), "count": stored, "chunks": chunks, "files": len(files),
                        "snapshot": snapshot, "stopped": False, "not_indexed": not_indexed}
            except Exception as e:
                self.logger.warning(f"Failed to index {rel_path}: {type(e).__name__}")
                not_indexed.append({"file": rel_path, "reason": type(e).__name__})
        what = "documentation and code files" if include_code else "documentation files"
        msg = f"Indexed {stored} of {len(files)} {what} ({chunks} excerpts)"
        if not_indexed:
            shown = ", ".join(f"{item['file']} ({item['reason']})" for item in not_indexed[:5])
            msg += f"; not indexed: {shown}" + (f" and {len(not_indexed) - 5} more" if len(not_indexed) > 5 else "")
        if stopped:
            msg += f"; then stopped, {len(files) - n + 1} files were not read"
        self.logger.info(msg)
        return {"success": True, "message": msg + ".", "count": stored, "chunks": chunks, "files": len(files),
                "snapshot": snapshot, "stopped": stopped, "not_indexed": not_indexed}

    # --- SNAPSHOT & ROLLBACK ---

    def create_snapshot(self, name: Optional[str] = None, before: str = "the change") -> Optional[str]:
        """Saves the active collection (vectors included) to a file; returns its name, None if empty or failed.
        No snapshot is ever deleted here, on any channel, and there is no limit (DECISIONS, sixth round: as many as
        the disk holds); the person deletes or exports them, with the full list (`llm-cli knowledge snapshots`).
        Its size is estimated first (eighth round, answer 2): when it does not fit on the disk, SnapshotNoRoom (the
        caller does not make its change either, there is no "continue anyway"); when it fits but leaves little free
        space, last_snapshot_room says so (verdict tight) and the snapshot is made. `before`: the change it backs up,
        as the refusal names it. A name that is taken (two snapshots in one second) gets "-2", "-3": an earlier
        snapshot is never written over."""
        name = sanitize_filename(name or f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}") or "snapshot"
        base, n = name, 1
        while os.path.lexists(self.snapshot_dir / f"{name}{SUFFIX}"):
            n += 1
            name = f"{base}-{n}"
        path = self.snapshot_dir / f"{name}{SUFFIX}"
        self.last_snapshot_room = None

        def export(store) -> int:
            if not store.count():
                return 0
            room = self.snapshot_room(store.export_estimate())
            self.last_snapshot_room = room
            require_room(room, before)
            if room.verdict == "tight":
                self.logger.warning(f"Knowledge snapshot {name}: the disk is tight ({room.sentence()}).")
            return store.export_file(path)

        try:
            count = self._with_store(export)
        except SnapshotNoRoom as e:
            self.logger.warning(str(e))
            raise
        except KnowledgeBaseError as e:
            self._fail(e)
            return None
        if not count:
            return None
        self.logger.info(f"Knowledge base snapshot {name}: {count} chunks.")
        return name

    def snapshot_room(self, estimate: Optional[int] = None):
        """The room on the disk for the next snapshot (knowledge_snapshots.SnapshotRoom): of `estimate` bytes, or as
        estimated from the knowledge base itself (0 when it is empty). Never deletes anything."""
        if estimate is None:
            estimate = self.snapshot_estimate()
        return snapshot_room(self.snapshot_dir, int(estimate or 0), self._state_dir())

    def snapshot_estimate(self) -> int:
        """The bytes the next snapshot would take (0 when the knowledge base is empty); KnowledgeBaseError when it
        cannot be read."""
        return int(self._with_store(lambda store: store.export_estimate()) or 0)

    def _snapshot_files(self) -> List[Path]:
        return snapshot_files(self.snapshot_dir)

    def snapshot_plan(self, names=None):
        """The deletion plan of the snapshots `names` (default: all of them, for the person's multiple choice)."""
        return snapshot_plan(self.snapshot_dir, names)

    def delete_snapshots(self, accepted, trash=None, audit=None):
        """Deletes exactly the snapshots of the plan the person accepted (knowledge_snapshots.delete_snapshots): no
        count, no age, nothing chosen by the framework. There is no limit of the framework's own (DECISIONS, sixth
        round); only the person's deletion dialog and `llm-cli knowledge snapshots delete` call this."""
        return delete_snapshots(self.snapshot_dir, accepted, trash=trash, audit=audit)

    def list_snapshots(self) -> List[str]:
        return [p.name[:-len(".jsonl.gz")] for p in self._snapshot_files()]

    def restore_snapshot(self, snapshot_name: str, backup: Optional[str] = None,
                         proceed: Optional[Callable[[], bool]] = None) -> bool:
        """Replaces the active collection with a snapshot of the same embedding model. The current content is saved
        first as the snapshot `backup` (default pre_restore_<time>); without that backup nothing is restored (an
        empty knowledge base needs none). last_backup, last_restored_chunks and last_restore_problem tell what
        happened. `proceed` is asked after the backup, right before the knowledge base is replaced: False (the job was
        cancelled) leaves the knowledge base as it was."""
        self.last_backup, self.last_restored_chunks, self.last_restore_problem = None, 0, ""
        self.last_restore_blocker = ""  # snapshot_no_room when the backup did not fit on the disk
        path = self.snapshot_dir / f"{sanitize_filename(snapshot_name)}.jsonl.gz"
        if not path.is_file():
            self.logger.error(f"Snapshot {snapshot_name} not found.")
            self.last_restore_problem = "the snapshot does not exist"
            return False
        try:
            current = self._with_store(lambda store: store.count())
        except KnowledgeBaseError as e:
            self._fail(e)
            self.last_restore_problem = "the knowledge base could not be read"
            return False
        if current:
            name = backup or f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                self.last_backup = self.create_snapshot(name, "the restore")
            except SnapshotNoRoom as e:
                self.last_restore_problem, self.last_restore_blocker = str(e), e.blocker
                return False
            if not self.last_backup:
                self.last_restore_problem = "the backup of the current knowledge base could not be written"
                self.logger.error(f"Snapshot {snapshot_name} not restored: {self.last_restore_problem}.")
                return False
        if proceed is not None and not proceed():
            self.last_restore_problem = "cancelled before the knowledge base was replaced"
            return False
        try:
            self._sources = None
            count = self._with_store(lambda store: store.restore_file(path))
        except (KnowledgeBaseError, OSError, ValueError) as e:
            self._fail(e)
            self.last_restore_problem = "the snapshot could not be read into the knowledge base"
            return False
        self.last_restored_chunks = int(count or 0)
        self.logger.warning(f"Knowledge base restored from snapshot {snapshot_name} ({count} chunks; backup "
                            f"{self.last_backup or 'not needed'}).")
        return True

    # --- SEARCH ---

    @_uses_models
    def search(self, query: str, limit: int = 3, score_threshold: Optional[float] = None,
               strict: bool = False, rerank: Optional[bool] = None) -> List[SearchResult]:
        """
        Hybrid search (dense + BM25, fused with RRF). score is the fused rank score, comparable within one
        result list only. score_threshold optionally drops dense candidates below that cosine similarity;
        there is no default because similarities are model-specific (E5 rates unrelated text 0.7-0.8).

        rerank (None: the setting rag_rerank_default) lets a cross-encoder score each candidate: score is then
        its 0..1 relevance, metadata["fused_score"] keeps the rank score, and results below the reranker's floor
        are dropped, so the list may be empty (no excerpt is better than an irrelevant one). A reranker that
        cannot run is logged, and searches keep the fused order until the next start or a change of rag_reranker.

        strict=True raises KnowledgeBaseError for storage and embedding errors instead of returning [].
        """
        query = (query or "").strip()
        if not query:
            return []
        reranker = self.reranker(rerank)
        pool = max(limit, min(max(limit * 3, 12), RERANK_POOL)) if reranker is not None else max(limit * 2, limit + 4)
        try:
            embedder, sparse = self._models()
            dense = check_vectors([embedder.embed_query(query)], 1, embedder.dim, embedder.model_id)[0]
            keyword = None
            if len(re.findall(r"\w+", query)) >= MIN_SPARSE_QUERY_TOKENS or _IDENTIFIER.search(query):
                keyword = sparse.embed_query(query)
            hits = self._with_store(lambda store: store.query(dense, keyword, pool, score_threshold))
        except KnowledgeBaseError as e:
            if strict:
                raise
            self._fail(e)
            return []

        results: List[SearchResult] = []
        seen = set()
        for hit in hits:  # deduplicated before reranking: the cross-encoder scores each text once
            payload = hit.payload or {}
            key = payload.get("chunk_hash") or str(hit.id)
            if key in seen:
                continue  # the same text stored for several sources is returned once
            seen.add(key)
            results.append(SearchResult(score=float(hit.score), content=str(payload.get("content", "")),
                                        source=str(payload.get("source", "unknown")), metadata=payload))
        if reranker is not None and results:
            results = self._rerank(reranker, query, results)
        return results[:limit]

    # --- reranking ---

    def reranker(self, rerank: Optional[bool] = True):
        """The reranker of a search (rerank None: the setting rag_rerank_default), or None: not asked for, turned
        off, failed, or none suits this machine (reranker_reason says why)."""
        if rerank is None:
            rerank = bool(self._cfg("rag_rerank_default", False))
        setting = str(self._cfg("rag_reranker", "auto") or "auto").strip()
        # Any change of rag_reranker allows a new attempt, also one back to a value that failed: the revision
        # counts every change, and changes are noted before a search that does not rerank returns.
        current = (setting, self._setting_revision("rag_reranker"))
        if self._reranker_setting not in (current, "provided"):
            with self._lock:
                if self._reranker_setting not in (current, "provided"):
                    self._reranker_setting, self._reranker_pending = current, True
        if not rerank:
            return None
        if setting.lower() == "off":
            self.reranker_reason = "turned off (rag_reranker: off)"
            return None
        with self._lock:
            if self._reranker_pending:
                self._reranker_pending = False
                try:
                    spec, self.reranker_reason = select_reranker(setting)
                    self._reranker = create_reranker(spec, self.models_cache_dir, self._offline) if spec else None
                except KnowledgeBaseError as e:  # e.g. an unknown model id in rag_reranker
                    self._rerank_failed(e)
            return self._reranker

    def _setting_revision(self, key: str) -> Optional[int]:
        """ConfigManager.revision(key); None for settings objects without one (the value alone then counts)."""
        revision = getattr(self.config, "revision", None)
        try:
            return revision(key) if callable(revision) else None
        except Exception:
            return None

    def rerank_floor(self, reranker) -> float:
        """rag_rerank_min_score (0: no floor), or the reranker's calibrated floor when it is not set."""
        try:
            return float(self._cfg("rag_rerank_min_score"))
        except (TypeError, ValueError):
            return float(getattr(getattr(reranker, "spec", None), "min_score", 0.0))

    def _rerank(self, reranker, query: str, results: List[SearchResult]) -> List[SearchResult]:
        def text(result: SearchResult) -> str:
            title, section = str(result.metadata.get("title") or ""), str(result.metadata.get("section") or "")
            head = section if title and section.startswith(title) else " > ".join(p for p in (title, section) if p)
            return (head + "\n" if head else "") + result.content[:RERANK_INPUT_CHARS]

        try:
            scores = reranker.scores(query, [text(r) for r in results])
        except EmbeddingModelNotCached as e:
            if models_local_only():  # an AI's tool call never downloads it; Ditto's next search still may
                self.logger.info(f"Reranking skipped for a tool call: {e}")
                return results
            self._rerank_failed(e)
            return results
        except KnowledgeBaseError as e:
            self._rerank_failed(e)
            return results
        floor = self.rerank_floor(reranker)
        ranked = []
        for result, score in sorted(zip(results, scores), key=lambda pair: -pair[1]):
            if score < floor:
                break
            result.metadata = {**result.metadata, "fused_score": result.score, "reranker": reranker.model_id}
            result.score = float(score)
            ranked.append(result)
        return ranked

    def _rerank_failed(self, error: Exception) -> None:
        """The reranker is quality on top, never a gate: it is dropped (logged once) and searches keep the fused
        order; it is tried again after the next start or a change of rag_reranker."""
        self._reranker = None
        self._fail(KnowledgeBaseError(f"reranking is off until the next start or a change of rag_reranker: {error}"))
        self.reranker_reason = self.last_error

    # --- sources (what the knowledge base holds, with provenance) ---

    def chunk_count(self) -> int:
        """How many excerpts (chunks) the knowledge base holds."""
        return int(self._with_store(lambda store: store.count()))

    def list_sources(self) -> List[Dict[str, Any]]:
        """Every source of the knowledge base with its provenance: source_id, source (address, pack entry or file
        label), title, kind (web, pack, docs, other), origin_pack, root (the crawl's start address or the pack),
        topic and target (the topic or target a knowledge pack's or a target pack's excerpts belong to, "" otherwise),
        ingested_at (the newest chunk) and chunks; its licence (licence_view: licence, copyright, licence_state,
        pack_share, share_reason); and hidden_removed: how much hidden text was left out before it was stored, by kind
        (the counts of every stored piece added up: a crawled page is one piece, a pack page may be several), None
        for a source stored without that check (the framework's documents, pages stored before it existed). Reads
        payload fields only, never the chunk text; the list is reused for SOURCES_TTL seconds and dropped by every
        write. Raises KnowledgeBaseError."""
        with self._lock:
            cached = self._sources
            if cached is not None and time.monotonic() - cached[0] < SOURCES_TTL:
                return [dict(s) for s in cached[1]]
            payloads = self._with_store(lambda store: list(store.payloads(_SOURCE_FIELDS)))
            sources: Dict[str, Dict[str, Any]] = {}
            metas: Dict[str, Dict[str, Any]] = {}
            for payload in payloads:
                source = str(payload.get("source") or "unknown")
                entry = sources.get(source)
                if entry is None:
                    kind = source_kind(payload)
                    entry = sources[source] = {
                        "source_id": source_id(source), "source": source, "title": "", "kind": kind,
                        "origin_pack": str(payload.get("origin_pack") or ""),
                        "root": str(payload.get("origin_pack") or payload.get("root_url") or ""),
                        "ingested_at": "", "chunks": 0, REMOVED_FIELD: None, "topic": "", "target": ""}
                removed = payload.get(REMOVED_FIELD)
                if isinstance(removed, dict):
                    # Each stored piece carries its counts on every excerpt: its first excerpt counts them once.
                    total = entry[REMOVED_FIELD] if entry[REMOVED_FIELD] is not None else {}
                    entry[REMOVED_FIELD] = _add_counts(total, removed) if payload.get("chunk_index") in (0, None) \
                        else total
                meta = metas.setdefault(source, {})
                for key in (*licences.META_KEYS, "root_url"):
                    if key not in meta and payload.get(key) not in (None, ""):
                        meta[key] = payload[key]
                entry["chunks"] += 1
                for key in ("topic", "target"):   # the topic or target a pack's excerpts belong to
                    if not entry[key] and isinstance(payload.get(key), str):
                        entry[key] = payload[key][:64]
                if not entry["title"] and payload.get("title"):
                    entry["title"] = str(payload["title"])[:500]
                stamp = str(payload.get("ingested_at") or "")
                if stamp > entry["ingested_at"]:
                    entry["ingested_at"] = stamp
            # Only your own crawls name a repository's licence file: a pack's page could claim one for your code.
            repos = licences.repo_licences(m for s, m in metas.items() if sources[s]["kind"] != "pack")
            for source, entry in sources.items():
                entry.update(licence_view(entry["kind"], metas.get(source, {}), repos))
            listed = sorted(sources.values(), key=lambda e: (e["kind"], e["source"]))
            self._sources = (time.monotonic(), listed)
            return [dict(s) for s in listed]

    def find_source(self, sid: str) -> Optional[Dict[str, Any]]:
        """The source with this id (list_sources), or None."""
        return next((s for s in self.list_sources() if s["source_id"] == sid), None)

    def read_source(self, source: str, start: int = 0, count: int = 5) -> Tuple[List[Dict[str, Any]], int]:
        """The chunks of one source in their order, from chunk position `start` (at most `count`), each as
        {index, section, content}; and how many chunks the source has. Raises KnowledgeBaseError."""
        records = self._with_store(lambda store: list(store.source_records(source)))
        chunks = sorted(({"index": int(p.get("chunk_index") or 0), "section": str(p.get("section") or ""),
                          "content": str(p.get("content") or "")} for p in (r.payload or {} for r in records)),
                        key=lambda c: c["index"])
        start, count = max(0, int(start)), max(0, int(count))
        return chunks[start:start + count], len(chunks)

    def export_records(self) -> Iterator[Dict[str, Any]]:
        """All stored chunks as plain records (source, content, metadata), e.g. for a community export."""
        records = self._with_store(lambda store: list(store.records()))
        for record in records:
            payload = dict(record.payload or {})
            for key in (HOLDERS, HOLDER_INFO):   # this installation's bookkeeping, never part of a page
                payload.pop(key, None)
            yield {"source": payload.pop("source", ""), "content": payload.pop("content", ""), "metadata": payload}

    def clear_knowledge_base(self) -> bool:
        """Deletes all stored vectors (of every embedding model used so far)."""
        try:
            self._sources = None
            self._with_store(lambda store: (store.drop_all(), store.ensure()))
        except KnowledgeBaseError as e:
            self._fail(e)
            return False
        self.logger.info("Knowledge Base cleared.")
        return True

    def _storage_label(self) -> str:
        """Where the knowledge base lives, for display: never the userinfo part of a server URL."""
        url = self._server_url()
        if not url:
            return str(self._local_folder())
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        try:
            port = parsed.port or 6333
        except ValueError:  # an invalid port is reported by the connection attempt
            port = "?"
        return f"{parsed.scheme}://{'[' + host + ']' if ':' in host else host}:{port}"

    def get_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "connected": False, "vector_count": 0, "mode": "server" if self._server_url() else "embedded",
            "storage": self._storage_label(),
        }

        def info(store: "KnowledgeStore") -> Dict[str, Any]:
            return {"connected": True, "vector_count": store.count(), "status": store.status(),
                    "store_format": store.store_format,
                    "collection": store.collection, "embedding_model": store.model_id, "dimensions": store.dim,
                    "other_collections": [c for c in store.knowledge_collections() if c != store.collection]}

        try:
            status.update(self._with_store(info))
        except KnowledgeBaseError as e:
            status["error"] = redact_secrets(str(e))
        status["storage"] = self._storage_label()
        if status["mode"] == "embedded":
            from orchestrator.rag.migration import summary
            status["conversion"] = summary(self._state_dir())
            if self.storage_reason:
                status["old_format_reason"] = self.storage_reason
        status["model_reason"] = self.model_reason
        return status
