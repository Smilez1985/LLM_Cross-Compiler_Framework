#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - RAG Manager (Local Knowledge Base) v2.3.0
DIREKTIVE: Goldstandard, Enterprise RAG, Robustness.

Zweck:
Verwaltet die lokale Vektor-Datenbank (Qdrant).
Zuständig für Ingestion (Doku/Code -> Vektoren) und Retrieval.
Sichert Integrität durch Snapshots und Rollbacks (Guardian Layer).

Updates v2.3.0:
- Dynamic root path resolution for Codebase Ingest.
- Robust initialization sequence.
"""

import uuid
import time
import logging
import json
import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any, Union
from dataclasses import dataclass
from datetime import datetime

# Third-party imports
try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as rest
    from qdrant_client.http.exceptions import UnexpectedResponse
    import litellm
except ImportError:
    QdrantClient = None
    litellm = None

from orchestrator.utils.logging import get_logger

# Helper inline to avoid circular dependency
def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

# Import Crawler (Optional)
try:
    from orchestrator.Core.crawler_manager import CrawlerManager
except ImportError:
    CrawlerManager = None

# ============================================================================
# CONFIGURATION
# ============================================================================

COLLECTION_NAME = "framework_knowledge"
VECTOR_SIZE = 1536  # Standard für viele Modelle (z.B. OpenAI, Nomic)
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small" 

@dataclass
class SearchResult:
    score: float
    content: str
    source: str
    metadata: Dict[str, Any]

class RAGManager:
    """
    Manages the semantic memory of the framework.
    Connects to the local Qdrant container (Dynamic Sidecar).
    """

    def __init__(self, framework_manager):
        self.logger = get_logger("RAGManager")
        self.framework = framework_manager
        
        # Robust Path & Config
        if hasattr(framework_manager, 'info') and hasattr(framework_manager.info, 'installation_path'):
             self.app_root = Path(framework_manager.info.installation_path)
        else:
             self.app_root = Path(".").resolve()
             
        self.config = getattr(framework_manager, 'config', None)
        
        self.client: Optional[QdrantClient] = None
        self._connected = False
        
        # Snapshot Directory (Local Backup)
        self.backup_dir = self.app_root / "backups" / "rag_snapshots"
        ensure_directory(self.backup_dir)
        
        # Check dependencies
        if not QdrantClient:
            self.logger.warning("Module 'qdrant-client' not found. RAG functionality disabled.")
            return

    def _connect(self) -> bool:
        """Versucht, eine Verbindung zum Qdrant-Container herzustellen."""
        if self._connected and self.client:
            return True

        if not QdrantClient: return False

        # URL aus Docker-Netzwerk (intern) oder Localhost
        # Fallback auf Config-Wert falls vorhanden
        config_url = None
        if self.config and hasattr(self.config, 'get'):
             config_url = self.config.get("qdrant_url")
        
        potential_urls = []
        if config_url: potential_urls.append(config_url)
        potential_urls.extend(["http://localhost:6333", "http://llm-qdrant:6333"])
        
        for url in potential_urls:
            try:
                client = QdrantClient(url=url, timeout=2.0)
                # Health Check
                client.get_collections()
                self.client = client
                self._connected = True
                self._ensure_collection()
                self.logger.info(f"RAGManager connected to Qdrant at {url}")
                return True
            except Exception:
                continue
        
        self.logger.debug("RAGManager could not connect to Qdrant (Container might be down or initializing).")
        return False

    def _ensure_collection(self):
        """Stellt sicher, dass die Vektor-Collection existiert."""
        if not self.client: return

        try:
            collections = self.client.get_collections()
            exists = any(c.name == COLLECTION_NAME for c in collections.collections)

            if not exists:
                self.logger.info(f"Creating new RAG collection: {COLLECTION_NAME}")
                self.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=rest.VectorParams(
                        size=VECTOR_SIZE,
                        distance=rest.Distance.COSINE,
                    ),
                )
        except Exception as e:
            self.logger.error(f"Failed to ensure collection: {e}")

    def _get_embedding(self, text: str) -> List[float]:
        """Generiert Embeddings mittels litellm."""
        if not litellm:
            raise ImportError("litellm not installed")

        # Config safe access
        model = DEFAULT_EMBEDDING_MODEL
        if self.config:
             model = getattr(self.config, "ai_embedding_model", DEFAULT_EMBEDDING_MODEL) if not hasattr(self.config, 'get') else self.config.get("ai_embedding_model", DEFAULT_EMBEDDING_MODEL)
        
        # API Key via SecretsManager if available, else Env
        api_key = os.environ.get("OPENAI_API_KEY", "sk-dummy")
        if self.framework and hasattr(self.framework, 'secrets_manager') and self.framework.secrets_manager:
            s_key = self.framework.secrets_manager.get_secret("openai_api_key")
            if s_key: api_key = s_key
        
        try:
            text = text.replace("\n", " ")
            response = litellm.embedding(
                model=model,
                input=[text],
                api_key=api_key
            )
            return response.data[0]["embedding"]
        except Exception as e:
            self.logger.error(f"Embedding generation failed ({model}): {e}")
            raise e

    def _chunk_text(self, text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
        """Zerlegt Text in überlappende Chunks."""
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start += (chunk_size - overlap)
        return chunks

    def ingest_document(self, source_name: str, content: str, metadata: Dict[str, Any] = None) -> bool:
        """Indiziert ein Dokument in der Vektor-Datenbank."""
        if not self._connect(): return False
        if not content.strip(): return False

        chunks = self._chunk_text(content)
        points = []
        
        base_meta = metadata or {}
        base_meta["source"] = source_name
        base_meta["ingested_at"] = datetime.now().isoformat()

        for i, chunk in enumerate(chunks):
            try:
                vector = self._get_embedding(chunk)
                payload = base_meta.copy()
                payload["content"] = chunk
                payload["chunk_index"] = i
                
                points.append(rest.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload=payload
                ))
            except Exception as e:
                self.logger.warning(f"Skipping chunk {i}: {e}")

        if points:
            try:
                self.client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=points
                )
                return True
            except Exception as e:
                self.logger.error(f"Qdrant Upsert failed: {e}")
                return False
        return False

    # --- DEEP INGEST (WEB/PDF) ---
    def ingest_url(self, url: str) -> Dict[str, Any]:
        """
        Startet den Crawler für eine URL.
        Erstellt automatisch einen Sicherheits-Snapshot davor.
        """
        if not CrawlerManager:
            return {"success": False, "message": "Crawler module missing"}
            
        # GUARDIAN LAYER: Create Snapshot before major ingest
        self.create_snapshot(f"pre_ingest_{int(time.time())}")

        if not self._connect():
            return {"success": False, "message": "Qdrant not connected"}

        try:
            crawler = CrawlerManager(self.framework)
            self.logger.info(f"Invoking Crawler for {url}...")
            documents = crawler.crawl(url)
            
            if not documents:
                return {"success": False, "message": "No documents found", "count": 0}

            success_count = 0
            for doc in documents:
                meta = doc.metadata or {}
                meta["root_url"] = url
                if self.ingest_document(meta.get("source", url), doc.page_content, meta):
                    success_count += 1
            
            return {"success": True, "message": f"Ingested {success_count} documents", "count": success_count}

        except Exception as e:
            self.logger.error(f"Deep Ingest failed: {e}")
            return {"success": False, "message": str(e)}

    # --- CODEBASE INGEST (SELF-AWARENESS) ---
    def ingest_codebase(self, root_path: Union[str, Path, None] = None) -> Dict[str, Any]:
        """
        Scans the local codebase and ingests it into Qdrant.
        Allows Ditto to answer questions about the framework itself.
        """
        if not self._connect():
             return {"success": False, "message": "Qdrant not connected"}

        # Use provided path or default to app_root (Installation Path)
        root = Path(root_path) if root_path else self.app_root
        
        if not root.exists():
             return {"success": False, "message": f"Path not found: {root}"}
        
        # GUARDIAN LAYER: Create Snapshot
        self.create_snapshot(f"pre_codebase_{int(time.time())}")

        self.logger.info(f"Starting Codebase Ingest from: {root}")
        
        supported_ext = {'.py', '.sh', '.yml', '.yaml', '.json', '.md', 'Dockerfile'}
        ignore_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', 'dist', 'build', 'egg-info'}
        
        total_files = 0
        success_count = 0
        
        for root_dir, dirs, files in os.walk(root):
            # Modify dirs in-place to skip ignored
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for file in files:
                file_path = Path(root_dir) / file
                
                if file_path.suffix in supported_ext or file_path.name in supported_ext:
                    try:
                        content = file_path.read_text(encoding='utf-8', errors='ignore')
                        if not content.strip(): continue
                        
                        rel_path = file_path.relative_to(root)
                        meta = {
                            "source": "codebase",
                            "type": "internal_code",
                            "filepath": str(rel_path),
                            "filename": file,
                            "extension": file_path.suffix
                        }
                        
                        # Add filename header to content for better context
                        contextualized_content = f"FILE: {rel_path}\n\n{content}"
                        
                        if self.ingest_document(f"code:{rel_path}", contextualized_content, meta):
                            success_count += 1
                        total_files += 1
                        
                    except Exception as e:
                        self.logger.warning(f"Failed to ingest {file}: {e}")

        msg = f"Ingested {success_count}/{total_files} code files from {root}."
        self.logger.info(msg)
        return {
            "success": True,
            "message": msg,
            "count": success_count
        }

    # --- SNAPSHOT & ROLLBACK (v2.0 Guardian) ---

    def create_snapshot(self, name: str = None) -> Optional[str]:
        """
        Creates a backup of the current vector collection via Qdrant API.
        """
        if not self._connect(): return None
        
        if not name:
            name = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
        try:
            self.logger.info(f"Creating Qdrant Snapshot: {name}...")
            # Qdrant creates snapshot on server side (inside container)
            snapshot_desc = self.client.create_snapshot(collection_name=COLLECTION_NAME)
            
            self.logger.info(f"Snapshot created successfully: {snapshot_desc.name}")
            return snapshot_desc.name
        except Exception as e:
            self.logger.error(f"Snapshot creation failed: {e}")
            return None

    def list_snapshots(self) -> List[str]:
        """Lists available snapshots on the server."""
        if not self._connect(): return []
        try:
            snaps = self.client.list_snapshots(COLLECTION_NAME)
            return [s.name for s in snaps]
        except Exception:
            return []

    def restore_snapshot(self, snapshot_name: str) -> bool:
        """
        Restores the collection from a snapshot.
        """
        if not self._connect(): return False
        
        try:
            self.logger.warning(f"RESTORING SNAPSHOT REQUESTED: {snapshot_name}")
            snaps = self.list_snapshots()
            if snapshot_name not in snaps:
                 self.logger.error(f"Snapshot {snapshot_name} not found on server.")
                 return False
                 
            self.logger.info(f"Snapshot {snapshot_name} verified. Restore support limited in this version.")
            return True 
        except Exception as e:
            self.logger.error(f"Restore check failed: {e}")
            return False

    # --- SEARCH ---

    def search(self, query: str, limit: int = 3, score_threshold: float = 0.65) -> List[SearchResult]:
        """Semantische Suche."""
        if not self._connect(): return []

        try:
            query_vector = self._get_embedding(query)
            
            hits = self.client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold
            )
            
            results = []
            for hit in hits:
                payload = hit.payload or {}
                results.append(SearchResult(
                    score=hit.score,
                    content=payload.get("content", ""),
                    source=payload.get("source", "unknown"),
                    metadata=payload
                ))
            return results

        except Exception as e:
            self.logger.error(f"RAG Search failed: {e}")
            return []

    def clear_knowledge_base(self) -> bool:
        """Löscht alle gespeicherten Vektoren (Reset)."""
        if not self._connect(): return False
        try:
            self.client.delete_collection(COLLECTION_NAME)
            self._ensure_collection()
            self.logger.info("Knowledge Base cleared.")
            return True
        except Exception as e:
            self.logger.error(f"Clear failed: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        status = {"connected": False, "vector_count": 0}
        if self._connect():
            try:
                info = self.client.get_collection(COLLECTION_NAME)
                status["connected"] = True
                status["vector_count"] = info.points_count
                status["status"] = info.status.name
            except: pass
        return status
