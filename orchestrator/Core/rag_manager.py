#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - RAG Manager (local knowledge base)

Ingests documentation (crawled pages, PDFs, community packs, the framework's own code) and serves
searches for Ditto and the MCP server. Everything runs locally: FastEmbed/ONNX embeddings, and Qdrant
embedded in the process with its storage in the user state directory (no Docker, no open port). A Qdrant
server is used only when the configuration sets qdrant_url.

Search results are plain data from external sources; callers wrap them as untrusted before a model sees them.
"""

import ipaddress
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Tuple, Union
from urllib.parse import urlparse

from orchestrator.rag.chunking import build_preamble, chunk_document, point_id
from orchestrator.rag.embeddings import (DenseEmbedder, EmbeddingModelSpec, FastEmbedDenseEmbedder,
                                         FastEmbedSparseEmbedder, SparseEmbedder, check_vectors, recommend_model,
                                         resolve_spec, spec_for_slug)
from orchestrator.rag.errors import EmbeddingError, KnowledgeBaseError
from orchestrator.utils.helpers import redact_secrets, sanitize_filename
from orchestrator.utils.logging import get_logger

if TYPE_CHECKING:  # qdrant-client (and the FastEmbed it pulls in) loads on first use, not at framework boot
    from orchestrator.rag.store import KnowledgeStore

# Import Crawler (Optional)
try:
    from orchestrator.Core.crawler_manager import CrawlerManager
except ImportError:
    CrawlerManager = None

COLLECTION_NAME = "framework_knowledge"  # alias of the active collection (one collection per embedding model)
MIN_SPARSE_QUERY_TOKENS = 3  # shorter queries use the dense ranking only
MAX_SNAPSHOTS = 5
MAX_CODE_FILE_BYTES = 512 * 1024
_RESERVED_PAYLOAD = {"content", "source", "section", "chunk_index", "chunk_hash", "embedding_model", "ingested_at"}


@dataclass
class SearchResult:
    score: float
    content: str
    source: str
    metadata: Dict[str, Any]


def create_dense_embedder(spec: EmbeddingModelSpec, cache_dir: Path, offline: Callable[[], bool]) -> DenseEmbedder:
    return FastEmbedDenseEmbedder(spec, cache_dir, offline)


def create_sparse_embedder(cache_dir: Path, offline: Callable[[], bool]) -> SparseEmbedder:
    return FastEmbedSparseEmbedder(cache_dir, offline)


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
    storage is released again after a short idle time, so the GUI and the MCP server can take turns.
    """

    IDLE_CLOSE_SECONDS = 30.0

    def __init__(self, framework_manager, embedder: Optional[DenseEmbedder] = None,
                 sparse_embedder: Optional[SparseEmbedder] = None):
        self.logger = get_logger("RAGManager")
        self.framework = framework_manager
        self.config = getattr(framework_manager, "config", None)
        installation = getattr(getattr(framework_manager, "info", None), "installation_path", None)
        self.app_root = Path(installation) if installation else Path(".").resolve()

        self.client = None
        self._connected = False
        self._server_mode = False
        self._store: Optional["KnowledgeStore"] = None
        self._embedder = embedder
        self._sparse = sparse_embedder
        self.model_reason = "provided by the caller" if embedder is not None else ""
        self.last_error = ""
        self.idle_close_seconds = self.IDLE_CLOSE_SECONDS
        self._lock = threading.RLock()
        self._idle_timer: Optional[threading.Timer] = None
        self._generation = 0

    # --- configuration ---

    def _cfg(self, key: str, default: Any = None) -> Any:
        cfg = self.config
        if cfg is None:
            return default
        value = cfg.get(key, default) if hasattr(cfg, "get") else getattr(cfg, key, default)
        return default if value is None else value

    def _offline(self) -> bool:
        # Ditto's own switch counts too: offline mode must never download a model.
        ditto = getattr(self.framework, "ditto_manager", None)
        return bool(self._cfg("offline_mode", False)) or getattr(ditto, "offline_mode", False) is True

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
        return self._state_dir() / "qdrant"

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

    # --- storage lifecycle ---

    def _open_client(self) -> Tuple[Any, bool]:
        from qdrant_client import QdrantClient

        url = self._server_url()
        if url:
            client = QdrantClient(url=url, api_key=self._server_api_key(url), timeout=10)
            client.get_collections()  # fail now, not on the first search
            return client, True
        path = self.storage_dir
        deadline = time.monotonic() + 5
        while True:
            try:
                # Safe across threads: every access goes through self._lock.
                return QdrantClient(path=str(path), force_disable_check_same_thread=True), False
            except RuntimeError as e:
                if "already accessed" not in str(e):
                    raise
                if time.monotonic() > deadline:
                    raise KnowledgeBaseError(
                        f"The knowledge base storage {path} is in use by another framework process (GUI or MCP "
                        "server). Try again shortly, or set qdrant_url to share a Qdrant server.") from e
                time.sleep(0.25)

    def _choose_model(self, client) -> Tuple[str, str]:
        configured = str(self._cfg("rag_embedding_model", "auto") or "auto").strip()
        if configured.lower() != "auto":
            return configured, "set in the configuration (rag_embedding_model)"
        from orchestrator.rag.store import KnowledgeStore

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
                return self._store
            try:
                from orchestrator.rag.store import KnowledgeStore
            except ImportError as e:
                raise KnowledgeBaseError("The knowledge base needs the 'qdrant-client' package.") from e
            try:
                client, server = self._open_client()
            except KnowledgeBaseError:
                raise
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
                store = KnowledgeStore(client, COLLECTION_NAME, self._embedder.model_id, self._embedder.dim,
                                       self._sparse.model_id, server_mode=server)
                store.ensure()
            except Exception as e:
                try:
                    client.close()
                except Exception:
                    pass
                if isinstance(e, KnowledgeBaseError):
                    raise
                raise KnowledgeBaseError(f"Could not prepare the knowledge base: {redact_secrets(str(e))}") from e
            self.client, self._store, self._connected, self._server_mode = client, store, True, server
            self.last_error = ""
            where = "Qdrant server" if server else f"embedded storage {self.storage_dir}"
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
        if self._server_mode or not self.idle_close_seconds or self.idle_close_seconds <= 0:
            return
        self._generation += 1
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self.idle_close_seconds, self._close_if_idle, args=(self._generation,))
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _close_if_idle(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._close_client()

    def _close_client(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client, self._store, self._connected = None, None, False

    def close(self) -> None:
        """Releases the storage (the embedded one is locked by this process while open). Models stay loaded."""
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._generation += 1
            self._close_client()

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

    def prepare_models(self) -> bool:
        """Downloads (unless offline) and loads the embedding models now instead of on first use."""
        try:
            for model in self._models():
                prepare = getattr(model, "prepare", None)
                if callable(prepare):
                    prepare()
            return True
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    # --- ingestion ---

    def ingest_document(self, source_name: str, content: str, metadata: Optional[Dict[str, Any]] = None,
                        replace: bool = False) -> bool:
        """
        Indexes a document; re-ingesting the same content updates it in place. replace=True also removes
        chunks only its previous version had - only for complete documents (a page, a file), never for
        callers that ingest one document piece by piece (community packs).
        """
        try:
            return self._ingest(source_name, content, metadata, replace) > 0
        except KnowledgeBaseError as e:
            self._fail(e)
            return False

    def _ingest(self, source_name: str, content: str, metadata: Optional[Dict[str, Any]], replace: bool) -> int:
        if not isinstance(content, str) or not content.strip():
            return 0
        if not isinstance(metadata, dict):
            metadata = {}  # community packs are untrusted JSON
        source = str(source_name or "").strip()[:500] or "unknown"
        base_section = str(metadata.get("section") or "")
        meta = _clean_metadata(metadata)
        embedder, sparse = self._models()
        budget = int(getattr(getattr(embedder, "spec", None), "chunk_tokens", 384))
        chunks = chunk_document(content, count_tokens=embedder.count_tokens, max_tokens=budget,
                                overlap_tokens=budget // 8, kind=_kind_of(meta), base_section=base_section)
        unique = {}
        for chunk in chunks:  # identical chunks of one document collapse into one point
            unique.setdefault(point_id(source, chunk.hash), chunk)
        if not unique:
            return 0
        ids, items = list(unique), list(unique.values())
        title = str(meta.get("title") or "")
        texts = [build_preamble(source, title, c.section) + "\n\n" + c.text for c in items]
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

        def write(store: "KnowledgeStore"):
            store.upsert([store.point(*row) for row in zip(ids, dense, sparse_vectors, payloads)])
            if replace:
                store.delete_stale(source, ids)

        self._with_store(write)
        return len(ids)

    # --- DEEP INGEST (WEB/PDF) ---
    def ingest_url(self, url: str, crawler_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Crawls a URL and indexes the pages. A snapshot is taken first, so a bad crawl can be rolled back.
        crawler_options apply to this crawl only (see CrawlerManager). "skipped" lists the pages the crawl
        left out ({"url", "reason", "opt_out"}), e.g. because the site reserved them.
        """
        if not CrawlerManager:
            return {"success": False, "message": "Crawler module missing"}
        if not self._connect():
            return {"success": False, "message": f"Knowledge base unavailable: {self.last_error}"}
        self.create_snapshot(f"pre_ingest_{int(time.time())}")

        try:
            crawler = CrawlerManager(self.framework, crawler_options)
            self.logger.info(f"Invoking Crawler for {url}...")
            documents = crawler.crawl(url)
        except Exception as e:
            self.logger.error(f"Deep Ingest failed: {redact_secrets(str(e))}")
            return {"success": False, "message": redact_secrets(str(e))}
        skipped = [asdict(page) for page in crawler.skipped]
        note = f", {len(skipped)} pages skipped (see the log)" if skipped else ""
        if not documents:
            return {"success": False, "message": f"No documents found{note}", "count": 0, "skipped": skipped}

        pages = chunks = 0
        for doc in documents:
            meta = dict(doc.metadata or {})
            meta["root_url"] = url
            try:
                added = self._ingest(meta.get("source", url), doc.page_content, meta, replace=True)
            except KnowledgeBaseError as e:
                self._fail(e)
                return {"success": False, "message": str(e), "count": chunks, "documents": pages,
                        "skipped": skipped}
            if added:
                pages += 1
                chunks += added
        return {"success": True, "message": f"Ingested {pages} pages ({chunks} chunks){note}", "count": chunks,
                "documents": pages, "skipped": skipped}

    # --- CODEBASE INGEST (SELF-AWARENESS) ---
    def ingest_codebase(self, root_path: Union[str, Path, None] = None) -> Dict[str, Any]:
        """Indexes the framework's own code and docs, so Ditto can answer questions about the framework."""
        root = Path(root_path) if root_path else self.app_root
        if not root.exists():
            return {"success": False, "message": f"Path not found: {root}"}
        if not self._connect():
            return {"success": False, "message": f"Knowledge base unavailable: {self.last_error}"}
        self.create_snapshot(f"pre_codebase_{int(time.time())}")
        self.logger.info(f"Starting Codebase Ingest from: {root}")

        supported_ext = {'.py', '.sh', '.yml', '.yaml', '.json', '.md', 'Dockerfile'}
        # Besides tooling caches, the framework's data directories hold large generated files.
        ignore_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', 'dist', 'build', 'egg-info',
                       '.pytest_cache', '.ruff_cache', '.mypy_cache', 'models', 'output', 'cache', 'logs',
                       'backups'}
        total_files = success_count = 0
        for root_dir, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.endswith('.egg-info')]
            for file in files:
                file_path = Path(root_dir) / file
                if not (file_path.suffix in supported_ext or file_path.name in supported_ext):
                    continue
                try:
                    if file_path.stat().st_size > MAX_CODE_FILE_BYTES:
                        continue
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    if not content.strip():
                        continue
                    # POSIX form: the source is part of the point id, which must not depend on the OS.
                    rel_path = file_path.relative_to(root).as_posix()
                    meta = {"type": "internal_code", "filepath": rel_path, "filename": file,
                            "extension": file_path.suffix, "title": rel_path}
                    total_files += 1
                    if self._ingest(f"code:{rel_path}", content, meta, replace=True):
                        success_count += 1
                except KnowledgeBaseError as e:
                    self._fail(e)
                    return {"success": False, "message": str(e), "count": success_count}
                except Exception as e:
                    self.logger.warning(f"Failed to ingest {file}: {e}")

        msg = f"Ingested {success_count}/{total_files} code files from {root}."
        self.logger.info(msg)
        return {"success": True, "message": msg, "count": success_count}

    # --- SNAPSHOT & ROLLBACK ---

    def create_snapshot(self, name: Optional[str] = None) -> Optional[str]:
        """Saves the active collection (vectors included) to a file; returns its name, None if empty or failed."""
        name = sanitize_filename(name or f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}") or "snapshot"
        path = self.snapshot_dir / f"{name}.jsonl.gz"
        try:
            count = self._with_store(lambda store: store.export_file(path) if store.count() else 0)
        except KnowledgeBaseError as e:
            self._fail(e)
            return None
        if not count:
            return None
        self._prune_snapshots()
        self.logger.info(f"Knowledge base snapshot {name}: {count} chunks.")
        return name

    def _snapshot_files(self) -> List[Path]:
        if not self.snapshot_dir.is_dir():
            return []
        return sorted(self.snapshot_dir.glob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)

    def _prune_snapshots(self) -> None:
        for old in self._snapshot_files()[:-MAX_SNAPSHOTS]:
            try:
                old.unlink()
            except OSError:
                pass

    def list_snapshots(self) -> List[str]:
        return [p.name[:-len(".jsonl.gz")] for p in self._snapshot_files()]

    def restore_snapshot(self, snapshot_name: str) -> bool:
        """Replaces the active collection with a snapshot of the same embedding model."""
        path = self.snapshot_dir / f"{sanitize_filename(snapshot_name)}.jsonl.gz"
        if not path.is_file():
            self.logger.error(f"Snapshot {snapshot_name} not found.")
            return False
        try:
            count = self._with_store(lambda store: store.restore_file(path))
        except (KnowledgeBaseError, OSError, ValueError) as e:
            self._fail(e)
            return False
        self.logger.warning(f"Knowledge base restored from snapshot {snapshot_name} ({count} chunks).")
        return True

    # --- SEARCH ---

    def search(self, query: str, limit: int = 3, score_threshold: Optional[float] = None,
               strict: bool = False) -> List[SearchResult]:
        """
        Hybrid search (dense + BM25, fused with RRF). score is the fused rank score, comparable within one
        result list only. score_threshold optionally drops dense candidates below that cosine similarity;
        there is no default because similarities are model-specific (E5 rates unrelated text 0.7-0.8).
        strict=True raises KnowledgeBaseError instead of returning an empty list.
        """
        query = (query or "").strip()
        if not query:
            return []
        try:
            embedder, sparse = self._models()
            dense = check_vectors([embedder.embed_query(query)], 1, embedder.dim, embedder.model_id)[0]
            keyword = None
            if len(re.findall(r"\w+", query)) >= MIN_SPARSE_QUERY_TOKENS:
                keyword = sparse.embed_query(query)
            hits = self._with_store(lambda store: store.query(dense, keyword, max(limit * 2, limit + 4),
                                                              score_threshold))
        except KnowledgeBaseError as e:
            if strict:
                raise
            self._fail(e)
            return []

        results: List[SearchResult] = []
        seen = set()
        for hit in hits:
            payload = hit.payload or {}
            key = payload.get("chunk_hash") or str(hit.id)
            if key in seen:
                continue  # the same text stored for several sources is returned once
            seen.add(key)
            results.append(SearchResult(score=float(hit.score), content=str(payload.get("content", "")),
                                        source=str(payload.get("source", "unknown")), metadata=payload))
            if len(results) >= limit:
                break
        return results

    def export_records(self) -> Iterator[Dict[str, Any]]:
        """All stored chunks as plain records (source, content, metadata), e.g. for a community export."""
        records = self._with_store(lambda store: list(store.records()))
        for record in records:
            payload = dict(record.payload or {})
            yield {"source": payload.pop("source", ""), "content": payload.pop("content", ""), "metadata": payload}

    def clear_knowledge_base(self) -> bool:
        """Deletes all stored vectors (of every embedding model used so far)."""
        try:
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
            return str(self.storage_dir)
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
            return {"connected": True, "vector_count": store.count(),
                    "status": store.client.get_collection(store.collection).status.name,
                    "collection": store.collection, "embedding_model": store.model_id, "dimensions": store.dim,
                    "other_collections": [c for c in store.knowledge_collections() if c != store.collection]}

        try:
            status.update(self._with_store(info))
        except KnowledgeBaseError as e:
            status["error"] = redact_secrets(str(e))
        status["model_reason"] = self.model_reason
        return status
