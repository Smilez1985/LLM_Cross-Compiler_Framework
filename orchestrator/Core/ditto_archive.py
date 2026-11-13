#!/usr/bin/env python3
"""
Ditto's archive, phase 1 (DESIGN, Ditto, the archive's rows; maintainer's decisions of 2026-11-13).

A searchable archive of what Ditto learned about llmcf and about building, kept in its own collection (alias
`ditto_archive`) of the knowledge base's storage, so it has the storage, the embedding model, the locks and the tests the
knowledge base has. Everyone who uses the harness may read it; only the person confirms, withdraws, deletes or undoes.

- An entry (`ArchiveEntry`) is one plain sentence with a key (kind:subject/property), a ground and its evidence. It
  enters only with one of three grounds: several independent sources, the manufacturer's website, or a passed test.
- Nothing from the person's conversation and nothing about the person's own devices enters. The sentence and its
  evidence go through the framework's cleaners; an entry that still holds an address, an e-mail address, a home folder,
  a path or a secret afterwards is dropped and counted, never stored cleaned.
- A newer entry with the same key replaces the older one (a real upsert, the point's id is a hash of the key). An entry
  the person confirmed is replaced too, but its text is first kept as a copy, with a notice and Undo.
- Ditto confirms an entry only when enough independent sources (different registrable domains) say the same: 3, 5 or 7
  by the person's level `ditto_archive_security`. A manufacturer's page or a passed test lets an entry in and never
  confirms it alone.
- A change of the knowledge base's embedding model opens a new, empty collection for the archive, which is filled
  again from the texts the old one stores.
"""

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.rag.embeddings import check_vectors
from orchestrator.rag.errors import EmbeddingModelNotCached, KnowledgeBaseError
from orchestrator.schemas.archive import (
    ARCHIVE_ALIAS, DEFAULT_LEVEL, EVIDENCE_MAX, GROUNDS, SETTING, TEXT_MAX, ArchiveEntry, confirm_needed,
    normalize_level, valid_evidence, valid_key)
from orchestrator.security import personal_data, secret_patterns
from orchestrator.security.paths import scrub_for_ai
from orchestrator.utils import archive_sources
from orchestrator.utils.logging import get_logger

ENTRY_NAMESPACE = uuid.UUID("5d6c3c43-9a0e-4a52-9a43-5f0f5c6f1d77")
# Which ground wins when the same sentence arrives with another one (the strongest stays the entry's ground).
GROUND_RANK = {"sources": 0, "vendor": 1, "tested": 2}
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?:(?<![\w/:.-])|^)(?:~/|/[A-Za-z0-9_.-]+/|[A-Za-z]:\\|\\\\)")
_SPACE = re.compile(r"\s+")


class ArchiveError(KnowledgeBaseError):
    """The archive cannot do what was asked (an unknown id, a copy where an entry is needed, ...). A kind of
    KnowledgeBaseError, so the storage's wrapper passes it on as it is."""


@dataclass
class Candidate:
    """What an extractor or a caller offers the archive."""
    key: str
    text: str
    ground: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class RecordResult:
    """How an offer ended: created, merged (more evidence for the same sentence), replaced (a newer sentence for the
    key), unchanged, dropped (with the reason) or unavailable (the archive cannot be opened now)."""
    outcome: str
    reason: str = ""
    entry: Optional[ArchiveEntry] = None


@dataclass
class SearchHit:
    entry: ArchiveEntry
    score: float


def entry_id(key: str, stamp: str = "") -> str:
    """The id of the entry of `key` (a hash of the key, so the same key overwrites in place); with `stamp` the id of
    a copy of it."""
    return str(uuid.uuid5(ENTRY_NAMESPACE, key if not stamp else f"{key}\x1fcopy\x1f{stamp}"))


def is_id(value: object) -> bool:
    """Whether `value` can be the id of a stored entry (a UUID); anything else is no entry, whatever is stored."""
    try:
        return str(uuid.UUID(str(value))) == str(value).lower()
    except ValueError:
        return False


def normalized(text: str) -> str:
    return _SPACE.sub(" ", str(text or "")).strip()


def private_reason(text: str) -> str:
    """Why `text` cannot be stored ("" when it can): it holds a host or e-mail address, a home folder, a path or a
    secret (a web address is part of an entry and is not looked at as a path)."""
    for line in text.splitlines() or [text]:
        if personal_data.find_personal_data(line, personal_data.BLUEPRINT):
            return "it holds an address, an e-mail address or a home folder"
        if secret_patterns.find_secrets(line) or secret_patterns.find_exact_formats(line):
            return "it holds a secret"
        if _PATH.search(_URL.sub(" ", line)):
            return "it holds a path"
    return ""


class DittoArchive:
    """The archive. `rag_source` gives the knowledge base's RAGManager (None while the knowledge base is switched off:
    the archive is then unavailable), `config` the person's settings."""

    def __init__(self, rag_source: Callable[[], Optional[Any]], config: Any = None,
                 table: Optional[Dict[str, Any]] = None, clock: Optional[Callable[[], str]] = None):
        self._rag_source = rag_source
        self.config = config
        self.table = table if table is not None else archive_sources.load_table()
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.logger = get_logger("DittoArchive")
        self.dropped = 0
        self.last_drop = ""
        self._lock = threading.RLock()
        self._carried: Optional[Any] = None   # the store whose old collections were carried over already

    # --- settings and sources ----------------------------------------------------------------------------------

    def level(self) -> str:
        """The archive's security level, the person's setting (low, mid or high; mid when unset)."""
        getter = getattr(self.config, "get", None)
        return normalize_level(getter(SETTING, DEFAULT_LEVEL) if callable(getter) else DEFAULT_LEVEL)

    def needed(self) -> int:
        """How many independent sources Ditto needs to confirm an entry now."""
        return confirm_needed(self.level())

    @property
    def suffixes(self) -> set:
        return archive_sources.suffixes_of(self.table)

    def independent(self, evidence: List[str]) -> int:
        return archive_sources.independent_sources(evidence, self.suffixes)

    def _rag(self) -> Any:
        rag = self._rag_source()
        if rag is None:
            raise KnowledgeBaseError("The knowledge base is switched off (setting enable_rag_knowledge), so the "
                                     "archive cannot be opened.")
        return rag

    def _run(self, operation: Callable[[Any], Any]) -> Any:
        """Runs `operation(store)` on the archive's collection, after carrying texts over from an older embedding
        model's collection into an empty new one."""
        rag = self._rag()

        def with_store(store: Any) -> Any:
            self._carry_over(rag, store)
            return operation(store)
        return rag.with_collection(ARCHIVE_ALIAS, with_store)

    # --- reading a stored point ----------------------------------------------------------------------------------

    def _entry(self, record: Any) -> ArchiveEntry:
        payload = dict(getattr(record, "payload", None) or {})
        evidence = [e for e in (payload.get("evidence") or []) if valid_evidence(e)]
        return ArchiveEntry(
            id=str(record.id), key=str(payload.get("key") or ""), text=str(payload.get("content") or ""),
            ground=payload.get("ground") if payload.get("ground") in GROUNDS else "sources", evidence=evidence,
            status="confirmed" if payload.get("status") == "confirmed" else "unconfirmed",
            confirmed_by=payload.get("confirmed_by") if payload.get("confirmed_by") in ("person", "ditto") else None,
            kind="copy" if payload.get("kind") == "copy" else "entry", created=str(payload.get("created") or ""),
            updated=str(payload.get("updated") or ""), copied_from=payload.get("copied_from") or None,
            copied_at=payload.get("copied_at") or None, independent_sources=self.independent(evidence))

    @staticmethod
    def _payload(entry: ArchiveEntry, model_id: str) -> Dict[str, Any]:
        return {"key": entry.key, "content": entry.text, "ground": entry.ground, "evidence": list(entry.evidence),
                "status": entry.status, "confirmed_by": entry.confirmed_by, "kind": entry.kind,
                "created": entry.created, "updated": entry.updated, "copied_from": entry.copied_from,
                "copied_at": entry.copied_at, "embedding_model": model_id}

    @staticmethod
    def _passage(key: str, text: str) -> str:
        return f"{key.replace(':', ' ').replace('/', ' ')} {text}"

    def _put(self, rag: Any, store: Any, entry: ArchiveEntry) -> None:
        """Writes `entry` under its id: its sentence is embedded again, so a changed sentence is found by its new words."""
        embedder, sparse = rag.models()
        passage = [self._passage(entry.key, entry.text)]
        dense = check_vectors(embedder.embed_passages(passage), 1, embedder.dim, embedder.model_id)[0]
        keyword = list(sparse.embed_passages(passage))[0]
        store.upsert([store.point(entry.id, dense, keyword, self._payload(entry, embedder.model_id))])

    def _carry_over(self, rag: Any, store: Any) -> None:
        """A new, empty collection (the knowledge base's embedding model changed) is filled again from the texts of the
        archive's collections of other models, which stay as they are."""
        if self._carried is store:
            return
        self._carried = store
        if store.count():
            return
        from orchestrator.rag.store import KnowledgeStore
        others = [name for name in store.knowledge_collections() if name != store.collection]
        entries: List[ArchiveEntry] = []
        for name in others:
            model = KnowledgeStore.model_of(store.client, name)
            if not model:
                continue
            old = type(store)(store.client, store.alias, model, store.dim, server_mode=store.server_mode)
            if old.collection != name:
                continue
            entries.extend(self._entry(r) for r in old.records(False) if (r.payload or {}).get("key"))
        for entry in entries:
            self._put(rag, store, entry)
        if entries:
            self.logger.info(f"The archive was filled again from {len(entries)} stored text(s) for the new embedding "
                             "model.")

    # --- offering an entry ---------------------------------------------------------------------------------------

    def _drop(self, reason: str) -> RecordResult:
        self.dropped += 1
        self.last_drop = reason
        return RecordResult("dropped", reason)

    def check(self, candidate: Candidate) -> Tuple[Optional[Candidate], str]:
        """(the candidate to store, "") or (None, why it cannot be): the key, the ground and its proof, the evidence, and
        the sentence, which must come through the cleaners unchanged and without private data."""
        if not valid_key(candidate.key):
            return None, "the key is not kind:subject/property"
        if candidate.ground not in GROUNDS:
            return None, "an entry needs one of the three grounds"
        text = normalized(candidate.text)
        if not text or len(text) > TEXT_MAX:
            return None, "the sentence is empty or too long"
        if scrub_for_ai(text, limit=TEXT_MAX * 4).strip() != text:
            return None, "the sentence needed cleaning"
        reason = private_reason(text)
        if reason:
            return None, reason
        evidence = list(dict.fromkeys(str(e) for e in candidate.evidence))[:EVIDENCE_MAX]
        if not evidence or not all(valid_evidence(e) for e in evidence):
            return None, "the evidence is not a public https address or a build or test label"
        if any(private_reason(e) for e in evidence if not e.startswith("https://")):
            return None, "the evidence holds private data"
        if candidate.ground == "vendor" and not any(archive_sources.vendor_of(e, self.table) for e in evidence):
            return None, "no evidence is a listed manufacturer's website"
        if candidate.ground == "tested" and not any(e.startswith(("build:", "test:")) for e in evidence):
            return None, "the ground 'tested' needs the label of the build or test"
        return Candidate(candidate.key, text, candidate.ground, evidence), ""

    def record(self, candidate: Candidate) -> RecordResult:
        """Offers an entry: checked, then stored as a new entry, as more evidence for the same sentence, or as the
        newer sentence of its key. Ditto confirms it when enough independent sources are named; a person-confirmed
        sentence a newer one replaces is kept as a copy first. Never raises for an archive that cannot be opened."""
        ready, reason = self.check(candidate)
        if ready is None:
            return self._drop(reason)
        try:
            return self._run(lambda store: self._write(store, ready))
        except (KnowledgeBaseError, EmbeddingModelNotCached) as e:
            return RecordResult("unavailable", str(e)[:200])

    def _write(self, store: Any, candidate: Candidate) -> RecordResult:
        rag = self._rag()
        with self._lock:
            now = self._clock()
            pid = entry_id(candidate.key)
            found = store.retrieve([pid], with_vectors=False)
            old = self._entry(found[0]) if found and (found[0].payload or {}).get("kind") != "copy" else None
            if old is not None and normalized(old.text) == candidate.text:
                evidence = list(dict.fromkeys(old.evidence + candidate.evidence))[:EVIDENCE_MAX]
                ground = max(old.ground, candidate.ground, key=GROUND_RANK.get)
                entry = old.model_copy(update={"evidence": evidence, "ground": ground})
                outcome = "merged" if evidence != old.evidence or ground != old.ground else "unchanged"
            else:
                if old is not None and old.status == "confirmed" and old.confirmed_by == "person":
                    copy = old.model_copy(update={"id": entry_id(old.key, now), "kind": "copy", "copied_from": old.id,
                                                  "copied_at": now})
                    self._put(rag, store, copy)
                entry = ArchiveEntry(id=pid, key=candidate.key, text=candidate.text, ground=candidate.ground,
                                     evidence=candidate.evidence, created=now)
                outcome = "replaced" if old is not None else "created"
            sources = self.independent(entry.evidence)
            if entry.ground == "sources" and sources < 2:
                return self._drop("the ground 'sources' needs at least two independent sources")
            if sources >= self.needed() and not (entry.status == "confirmed" and entry.confirmed_by == "person"):
                entry = entry.model_copy(update={"status": "confirmed", "confirmed_by": "ditto"})
            if outcome == "unchanged" and entry.status == old.status and entry.confirmed_by == old.confirmed_by:
                return RecordResult("unchanged", entry=old)
            entry = entry.model_copy(update={"updated": now, "independent_sources": sources})
            self._put(rag, store, entry)
            return RecordResult(outcome if outcome != "unchanged" else "merged", entry=entry)

    # --- reading ---------------------------------------------------------------------------------------------------

    def entries(self, *, status: str = "", kind: str = "entry") -> List[ArchiveEntry]:
        """Every entry (or copy, kind "copy"; "" for both), newest first; status "confirmed" or "unconfirmed" narrows."""
        def read(store: Any) -> List[ArchiveEntry]:
            out = [self._entry(r) for r in store.records(False) if (r.payload or {}).get("key")]
            return [e for e in out if (not kind or e.kind == kind) and (not status or e.status == status)]
        return sorted(self._run(read), key=lambda e: (e.updated, e.key), reverse=True)

    def get(self, entry_id_: str) -> Optional[ArchiveEntry]:
        def read(store: Any) -> Optional[ArchiveEntry]:
            found = store.retrieve([str(entry_id_)], with_vectors=False) if is_id(entry_id_) else []
            return self._entry(found[0]) if found and (found[0].payload or {}).get("key") else None
        return self._run(read)

    def copies(self, key: str) -> List[ArchiveEntry]:
        """The copies kept of `key`, newest first."""
        return [e for e in self.entries(kind="copy") if e.key == key]

    def search(self, query: str, limit: int = 5, *, confirmed_only: bool = False) -> List[SearchHit]:
        """Hybrid search (dense and keywords, fused) over the entries; a copy is never returned."""
        query = normalized(query)
        if not query:
            return []
        rag = self._rag()
        embedder, sparse = rag.models()
        dense = check_vectors([embedder.embed_query(query)], 1, embedder.dim, embedder.model_id)[0]
        keyword = sparse.embed_query(query)
        pool = max(limit * 3, 12)
        hits = self._run(lambda store: store.query(dense, keyword, pool))
        out: List[SearchHit] = []
        for hit in hits:
            if (hit.payload or {}).get("kind") == "copy" or not (hit.payload or {}).get("key"):
                continue
            entry = self._entry(type("Record", (), {"id": hit.id, "payload": hit.payload})())
            if confirmed_only and entry.status != "confirmed":
                continue
            out.append(SearchHit(entry, float(hit.score)))
            if len(out) >= limit:
                break
        return out

    def overview(self) -> Dict[str, Any]:
        """The numbers the CLI and the window show: entries, confirmed, copies, the level and what it needs."""
        everything = self.entries(kind="")
        entries = [e for e in everything if e.kind == "entry"]
        return {"entries": len(entries), "confirmed": sum(1 for e in entries if e.status == "confirmed"),
                "copies": len(everything) - len(entries), "level": self.level(), "needed": self.needed(),
                "dropped": self.dropped, "last_drop": self.last_drop}

    # --- the person's own actions (no tool of an AI calls these) --------------------------------------------------------

    def _change(self, entry_id_: str, change: Callable[[ArchiveEntry], ArchiveEntry]) -> ArchiveEntry:
        rag = self._rag()

        def write(store: Any) -> ArchiveEntry:
            found = store.retrieve([str(entry_id_)], with_vectors=False) if is_id(entry_id_) else []
            if not found or not (found[0].payload or {}).get("key"):
                raise ArchiveError(f"There is no archive entry {entry_id_}.")
            entry = change(self._entry(found[0]))
            entry = entry.model_copy(update={"updated": self._clock(),
                                             "independent_sources": self.independent(entry.evidence)})
            self._put(rag, store, entry)
            return entry
        return self._run(write)

    def confirm(self, entry_id_: str) -> ArchiveEntry:
        """The person confirms an entry (whatever its ground and its sources)."""
        def apply(entry: ArchiveEntry) -> ArchiveEntry:
            if entry.kind != "entry":
                raise ArchiveError("A copy cannot be confirmed; undo the replacement to bring its text back.")
            return entry.model_copy(update={"status": "confirmed", "confirmed_by": "person"})
        return self._change(entry_id_, apply)

    def unconfirm(self, entry_id_: str) -> ArchiveEntry:
        """The person takes a confirmation back (the entry stays, unconfirmed)."""
        def apply(entry: ArchiveEntry) -> ArchiveEntry:
            if entry.kind != "entry":
                raise ArchiveError("A copy has no confirmation to take back.")
            return entry.model_copy(update={"status": "unconfirmed", "confirmed_by": None})
        return self._change(entry_id_, apply)

    def delete(self, entry_id_: str) -> bool:
        """The person deletes an entry or a copy; True when it was there. Nothing else deletes an entry but a newer one."""
        def remove(store: Any) -> bool:
            found = store.retrieve([str(entry_id_)], with_vectors=False) if is_id(entry_id_) else []
            if not found or not (found[0].payload or {}).get("key"):
                return False
            store.delete_ids([str(entry_id_)])
            return True
        return self._run(remove)

    def undo(self, key: str) -> ArchiveEntry:
        """Undoes the newest replacement of `key`: the person's confirmed text becomes the entry again, as it was, and
        the newer text becomes the copy (with its own status). Without an entry (it was deleted since), the copy simply
        becomes the entry again."""
        rag = self._rag()

        def swap(store: Any) -> ArchiveEntry:
            copies = sorted((self._entry(r) for r in store.records(False)
                             if (r.payload or {}).get("key") == key and (r.payload or {}).get("kind") == "copy"),
                            key=lambda e: e.copied_at or "", reverse=True)
            if not copies:
                raise ArchiveError(f"There is nothing to undo for {key}.")
            copy, now = copies[0], self._clock()
            pid = entry_id(key)
            found = store.retrieve([pid], with_vectors=False)
            current = self._entry(found[0]) if found and (found[0].payload or {}).get("kind") != "copy" else None
            restored = copy.model_copy(update={"id": pid, "kind": "entry", "copied_from": None, "copied_at": None,
                                               "updated": now})
            self._put(rag, store, restored)
            if current is None:
                store.delete_ids([copy.id])
            else:
                newer = current.model_copy(update={"id": copy.id, "kind": "copy", "copied_from": pid,
                                                   "copied_at": now})
                self._put(rag, store, newer)
            return restored
        return self._run(swap)


__all__ = ["ArchiveError", "Candidate", "DittoArchive", "RecordResult", "SearchHit", "entry_id", "is_id", "normalized",
           "private_reason"]
