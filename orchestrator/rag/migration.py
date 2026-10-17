#!/usr/bin/env python3
"""
Conversion of the local knowledge base from the old storage to Qdrant Edge (DECISIONS RG5).

Earlier versions kept the local knowledge base in qdrant-client's local mode (`<state>/qdrant`): a pure-Python
re-implementation of Qdrant meant for development. This version keeps it in Qdrant Edge (`<state>/qdrant_edge`,
orchestrator/rag/edge_store.py). On the first use of an old store (RAGManager, under the storage lock):

1. The disk is checked first: the new storage needs about the old one's size beside it.
2. Every collection of the knowledge base (one per embedding model) is copied with its vectors and payloads; nothing
   is embedded again. The copy is built in a separate folder (`qdrant_edge.converting`) and moved into place only
   when it is complete and checked.
3. The check: every collection holds as many points as before, and a sample of points is compared one by one (payload
   and vectors) and searched for in both stores (exact dense search, and for some of them the keyword search with its
   IDF), which must give the same results.
4. The conversion is recorded (`<state>/knowledge_conversion.json`: when, what, how many points, what was checked).
   The old folder is KEPT: only the person deletes it, through the deletion list (round 5 f; `old_store_plan` and
   `delete_old_store`; `llm-cli knowledge storage delete-old`, Tools > Knowledge Snapshots... in the GUI). Never
   automatic.

A failed conversion leaves the old store as it was and in use (the knowledge base keeps working), removes its own
half-built copy, and records why. A failure that would happen again the same way (a check that did not match, an
error while copying) is not tried again on every start; the person tries again (Tools > Knowledge Snapshots... > Try
the conversion again in the GUI, `llm-cli knowledge storage convert` in a terminal), and so does a new version of the
Qdrant Edge engine. A failure of the moment (not enough disk space, the old store in use by an older framework) is
tried again on the next start.

Once the knowledge base is in Qdrant Edge, the old folder never stands in for it again: when the engine cannot be
loaded later (a missing or broken package), the knowledge base is refused with the reason (EDGE_UNAVAILABLE) instead of
quietly using the old folder, which lacks what was added since the conversion.
"""

import json
import math
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.rag.errors import KnowledgeBaseError

OLD_FOLDER = "qdrant"
EDGE_FOLDER = "qdrant_edge"
RECORD_NAME = "knowledge_conversion.json"
CONVERTING_SUFFIX = ".converting"
ALIAS = "framework_knowledge"
# The sample of the check: points compared one by one and searched for in both stores, per collection.
VERIFY_SAMPLES = 16
# Of those, this many are also searched by their keyword vector: the old storage computes a keyword search in pure Python
# (seconds each for tens of thousands of points), and every sampled point's keyword vector is compared exactly anyway.
VERIFY_KEYWORD_SEARCHES = 4
VERIFY_TOP = 5
SCORE_TOLERANCE = 1e-3
COPY_BATCH = 1024
# Room the conversion needs on the disk: the old store's size again, plus a margin and the engine's write-ahead log.
DISK_FACTOR = 1.3
DISK_FIXED = 64 * 1024 * 1024
FREE_AFTER = 512 * 1024 * 1024
# Failures of the moment, tried again on the next start; every other failure waits for a change or for the person.
TRANSIENT = ("disk_space", "busy")
# Where the person tries a failed conversion again: the GUI (DECISIONS W1': never the CLI only) and the terminal.
GUI_RETRY = "Tools > Knowledge Snapshots... > Try the conversion again in the GUI"
CLI_RETRY = "'llm-cli knowledge storage convert' in a terminal"

Log = Optional[Callable[[str], None]]


class ConversionFailed(KnowledgeBaseError):
    """The old store was not converted; it stays as it was and in use. `code`: disk_space, busy, verify_mismatch,
    unreadable or error."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


@dataclass
class Conversion:
    """What a conversion did (also the record's content)."""
    collections: List[Dict[str, Any]] = field(default_factory=list)
    points: int = 0
    checked_points: int = 0
    checked_searches: int = 0
    seconds: float = 0.0


def old_folder(state_dir: Path) -> Path:
    return Path(state_dir) / OLD_FOLDER


def edge_folder(state_dir: Path) -> Path:
    return Path(state_dir) / EDGE_FOLDER


def record_path(state_dir: Path) -> Path:
    return Path(state_dir) / RECORD_NAME


def old_store_present(state_dir: Path) -> bool:
    """An old local store (qdrant-client's local mode keeps its list of collections in meta.json)."""
    folder = old_folder(state_dir)
    return folder.is_dir() and not folder.is_symlink() and (folder / "meta.json").is_file()


def edge_in_use(state_dir: Path) -> bool:
    """The Qdrant Edge storage holds at least one collection (then it is the knowledge base, whatever else lies
    beside it)."""
    folder = edge_folder(state_dir) / "collections"
    try:
        return folder.is_dir() and any(p.suffix == ".json" for p in folder.iterdir())
    except OSError:
        return False


ENGINE_MISSING = ("Qdrant Edge is not available on this computer (the package qdrant-edge-py has no build for it, for "
                  "example Windows on ARM), so the knowledge base uses the older local storage of qdrant-client")
# The knowledge base is in Qdrant Edge already, but the engine cannot be loaded (a missing or broken package). The old
# folder is never used in its place: it lacks what was added since the conversion, and what would be written into it
# would never reach Qdrant Edge.
EDGE_UNAVAILABLE = ("The knowledge base is stored in Qdrant Edge, but Qdrant Edge cannot be loaded on this computer "
                    "(the package qdrant-edge-py is missing or broken). Nothing was changed. The storage of earlier "
                    "versions is not used in its place: it lacks what was added since the conversion. Install the "
                    "framework's requirements again (the installer, or pip install -r requirements.txt)")


# Why the old storage is still in use before its conversion was tried (migration.current).
NOT_CONVERTED = "the old storage has not been converted yet"
# Why the old storage is not converted while a Qdrant server is set: the knowledge base opens the server, never the
# local storage (RAGManager._open_client), so a conversion would convert nothing.
SERVER_IN_USE = ("The knowledge base uses the Qdrant server (setting qdrant_url), so the local storage is not "
                 "converted while it is set")


def engine_available() -> bool:
    """Qdrant Edge can be loaded here (its package is installed and has a build for this computer)."""
    try:
        import qdrant_edge  # noqa: F401
    except ImportError:
        return False
    return True


def engine_version() -> str:
    try:
        from importlib.metadata import version
        return f"qdrant-edge-py {version('qdrant-edge-py')}"
    except Exception:  # noqa: BLE001 - not installed: the conversion fails with its own reason
        return "qdrant-edge-py (unknown version)"


def read_record(state_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads(record_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_record(state_dir: Path, record: Dict[str, Any]) -> None:
    path = record_path(state_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def folder_bytes(folder: Path) -> int:
    from orchestrator.Core.trash import tree_size
    return tree_size(Path(folder))


def should_try(state_dir: Path, force: bool = False) -> Tuple[bool, str]:
    """(convert now, why not): an old store and no Qdrant Edge storage yet, unless the last attempt failed in a way
    that would fail again with the same engine (a check that did not match, an error while copying) and the person
    did not ask to try again (`force`)."""
    if not old_store_present(state_dir) or edge_in_use(state_dir):
        return False, ""
    record = read_record(state_dir)
    if force or record.get("state") != "failed" or record.get("code") in TRANSIENT:
        return True, ""
    if record.get("engine") != engine_version():
        return True, ""
    return False, str(record.get("reason") or "the last conversion failed")


def retry_sentence(code: str) -> str:
    """How a failed conversion (its `code`) is tried again, in plain words for the CLI and Mojo (the GUI has its own
    texts in both languages, l10n_knowledge)."""
    if code in TRANSIENT:
        return f"It is tried again on the next start, or now with {GUI_RETRY} or {CLI_RETRY}."
    return (f"It is not tried again by itself, because it would fail the same way (only a new version of Qdrant Edge "
            f"tries it again): try it again with {GUI_RETRY} or {CLI_RETRY}.")


# --- the conversion ---------------------------------------------------------------------------------------------------

def _check_room(state_dir: Path, need: int, disk_usage: Any = None) -> None:
    """The new storage must fit beside the old one: about the old one's size again (DISK_FACTOR, DISK_FIXED) and
    FREE_AFTER left free afterwards. The conversion uses no reserve of its own beyond that: refusing it costs nothing
    (the old store keeps working), and the person frees the space again by deleting the old folder."""
    import shutil as _shutil

    from orchestrator.Core.disk_plan import gb
    usage = disk_usage or _shutil.disk_usage
    folder = Path(state_dir)
    while not folder.exists() and folder.parent != folder:
        folder = folder.parent
    try:
        free = int(usage(str(folder)).free)
    except OSError:
        return
    if free - need < FREE_AFTER:
        raise ConversionFailed(f"Not enough disk space to convert the knowledge base to Qdrant Edge: it needs about "
                               f"{gb(need)} beside the old storage and {gb(FREE_AFTER)} left free, {gb(free)} are "
                               "free. It keeps working in the old storage; the conversion is tried again on the next "
                               "start.", code="disk_space")


def _open_old(folder: Path):
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        raise ConversionFailed("Reading the old knowledge base storage needs the package qdrant-client, which is not "
                               "installed.", code="error") from None
    try:
        return QdrantClient(path=str(folder), force_disable_check_same_thread=True)
    except RuntimeError as e:
        if "already accessed" in str(e):
            raise ConversionFailed("The old knowledge base storage is in use by another program (an older framework "
                                   "version?); the conversion is tried again on the next start.",
                                   code="busy") from None
        raise ConversionFailed(_unreadable(e), code="unreadable") from None
    except Exception as e:  # noqa: BLE001 - a broken old store is a recorded failure, never a crash of run()
        raise ConversionFailed(_unreadable(e), code="unreadable") from None


def _unreadable(error: Exception) -> str:
    """Why an old store cannot be opened, without its text (it can name host paths): the error's kind only."""
    return (f"The old knowledge base storage cannot be read ({type(error).__name__}), so it was not converted and was "
            "not changed. A backup of the folder or a knowledge snapshot can replace it")


def _old_collections(client) -> List[Tuple[str, int, Optional[str]]]:
    """(name, dimension, embedding model) of every knowledge collection of the old store."""
    from orchestrator.rag.embeddings import spec_for_slug
    from orchestrator.rag.store import DENSE
    found = []
    for c in client.get_collections().collections:
        if not c.name.startswith(f"{ALIAS}__"):
            continue  # a legacy collection of the cloud-embedding version: not of this knowledge base
        config = client.get_collection(c.name).config
        vectors = config.params.vectors
        dense = vectors.get(DENSE) if isinstance(vectors, dict) else None
        if dense is None:
            raise ConversionFailed(f"The old collection {c.name} has no '{DENSE}' vector; it was not made by this "
                                   "framework and cannot be converted.", code="unreadable")
        model = (config.metadata or {}).get("embedding_model")
        if not model:
            spec = spec_for_slug(c.name.split("__", 1)[-1])
            model = spec.model_id if spec else None
        found.append((c.name, int(dense.size), model))
    return found


def _close_enough(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=SCORE_TOLERANCE, abs_tol=SCORE_TOLERANCE)


def same_ranking(old: List[Tuple[str, float]], new: List[Tuple[str, float]]) -> bool:
    """Two result lists agree: the same scores in order, and the same points wherever a score is not tied with the
    last one (points with equal scores may come in any order, and the last place may be shared)."""
    if len(old) != len(new):
        return False
    if any(not _close_enough(a[1], b[1]) for a, b in zip(old, new)):
        return False
    if not old:
        return True
    boundary = old[-1][1]
    above_old = {pid for pid, score in old if not _close_enough(score, boundary)}
    above_new = {pid for pid, score in new if not _close_enough(score, boundary)}
    return above_old == above_new


def _same_point(old, new) -> bool:
    import numpy as np

    from orchestrator.rag.store import DENSE, sparse_of
    if new is None or dict(old.payload or {}) != dict(new.payload or {}):
        return False
    ov, nv = old.vector or {}, new.vector or {}
    if ov.get(DENSE) is None or nv.get(DENSE) is None:
        return False
    a_dense, b_dense = np.asarray(ov[DENSE], dtype="float64"), np.asarray(nv[DENSE], dtype="float64")
    if a_dense.shape != b_dense.shape or not a_dense.size:
        return False
    # Cosine distance: the direction is what is stored and searched (the old storage may keep a vector as it was given,
    # Qdrant Edge keeps it normalized).
    a_norm, b_norm = np.linalg.norm(a_dense), np.linalg.norm(b_dense)
    if not a_norm or not b_norm or not np.allclose(a_dense / a_norm, b_dense / b_norm, atol=1e-5):
        return False
    a, b = sparse_of(ov), sparse_of(nv)
    if (a is None) != (b is None):
        return False
    if a is not None and (a.indices != b.indices or not np.allclose(a.values, b.values, atol=1e-6)):
        return False
    return True


def _verify(client, name: str, store, samples: List[Any]) -> Tuple[int, int]:
    """Compares the sample points of one collection in both stores: payload and vectors, and the exact dense search and
    the keyword search with each point's own vectors. ConversionFailed(verify_mismatch) on the first difference.
    Returns (points compared, searches compared)."""
    import qdrant_edge as qe
    from qdrant_client import models

    from orchestrator.rag.store import DENSE, SPARSE, sparse_of
    by_id = {r.id: r for r in store.retrieve([s.id for s in samples], with_vectors=True)} if samples else {}
    searches = keyword_searches = 0
    for sample in samples:
        if not _same_point(sample, by_id.get(str(sample.id))):
            raise ConversionFailed(f"The converted collection {name} does not hold point {sample.id} exactly as the "
                                   "old store does.", code="verify_mismatch")
        dense = [float(x) for x in sample.vector[DENSE]]
        old_hits = client.query_points(name, query=dense, using=DENSE, limit=VERIFY_TOP).points  # always exact
        new_hits = store._shard.query(qe.QueryRequest(limit=VERIFY_TOP, query=qe.Query.Nearest(dense, using=DENSE),
                                                      params=qe.SearchParams(exact=True)))
        if not same_ranking([(str(h.id), h.score) for h in old_hits], [(str(h.id), h.score) for h in new_hits]):
            raise ConversionFailed(f"A search in the converted collection {name} did not give the same results as in "
                                   "the old store.", code="verify_mismatch")
        searches += 1
        sparse = sparse_of(sample.vector)
        if sparse is not None and sparse.indices and keyword_searches < VERIFY_KEYWORD_SEARCHES:
            keyword_searches += 1
            old_hits = client.query_points(name, query=models.SparseVector(indices=sparse.indices,
                                                                           values=sparse.values),
                                           using=SPARSE, limit=VERIFY_TOP).points
            new_hits = store._shard.query(qe.QueryRequest(limit=VERIFY_TOP, query=qe.Query.Nearest(
                qe.SparseVector(indices=sparse.indices, values=sparse.values), using=SPARSE)))
            if not same_ranking([(str(h.id), h.score) for h in old_hits], [(str(h.id), h.score) for h in new_hits]):
                raise ConversionFailed(f"A keyword search in the converted collection {name} did not give the same "
                                       "results as in the old store.", code="verify_mismatch")
            searches += 1
    return len(samples), searches


def _copy_collection(client, name: str, dim: int, model: Optional[str], storage, log: Log) -> Tuple[int, int, int]:
    """Copies one collection into the Edge storage and checks it. Returns (points, compared, searches)."""
    from orchestrator.rag.client_store import _record
    from orchestrator.rag.edge_store import EdgeKnowledgeStore
    from orchestrator.rag.store import DENSE, sparse_of
    expected = client.count(collection_name=name, exact=True).count
    storage.create(name, dim, model or "", {"converted_from": OLD_FOLDER})
    store = EdgeKnowledgeStore(storage, ALIAS, model or "", dim)
    store.collection = name  # this collection, whatever model is active now
    step = max(1, expected // VERIFY_SAMPLES) if expected else 1
    samples, copied, offset = [], 0, None
    while True:
        batch, offset = client.scroll(collection_name=name, limit=COPY_BATCH, offset=offset, with_payload=True,
                                      with_vectors=True)
        records = [_record(r) for r in batch]
        points = []
        for record in records:
            vector = record.vector or {}
            if vector.get(DENSE) is None:
                raise ConversionFailed(f"A point of the old collection {name} has no vector.", code="unreadable")
            points.append(store.point(record.id, vector[DENSE], sparse_of(vector), record.payload))
            if copied % step == 0 and len(samples) < VERIFY_SAMPLES:
                samples.append(record)
            copied += 1
        if points:
            store.upsert(points, flush=False)
        if offset is None:
            break
    store.client.flush()
    store.optimize()
    count = store.count()
    if count != expected or copied != expected:
        raise ConversionFailed(f"The converted collection {name} holds {count} points, the old one {expected}.",
                               code="verify_mismatch")
    compared, searches = _verify(client, name, store, samples)
    if log:
        log(f"Knowledge base conversion: {name}: {count} points copied, {compared} compared, {searches} searches "
            "checked.")
    return count, compared, searches


def convert(state_dir: Path, log: Log = None, disk_usage: Any = None) -> Conversion:
    """Converts the old local store of `state_dir` to Qdrant Edge (the caller holds the storage lock). The old store is
    only read. Raises ConversionFailed (the old store is untouched and stays in use; the half-built copy is removed)."""
    from orchestrator.rag.edge_store import ALIASES, COLLECTIONS, EdgeStorage
    from orchestrator.rag.storage_lock import remove_tree
    state_dir = Path(state_dir)
    old, final = old_folder(state_dir), edge_folder(state_dir)
    work = final.with_name(final.name + CONVERTING_SUFFIX)
    started = time.monotonic()
    _check_room(state_dir, int(folder_bytes(old) * DISK_FACTOR) + DISK_FIXED, disk_usage)
    try:
        import qdrant_edge  # noqa: F401 - the engine must be there before anything is copied
    except ImportError:
        raise ConversionFailed("The Qdrant Edge engine (package qdrant-edge-py) is not installed.",
                               code="error") from None
    if work.exists():  # the half-built copy of an attempt that was cut off (never the person's data)
        remove_tree(work)
    client = _open_old(old)
    storage = EdgeStorage(work)
    result = Conversion()
    try:
        alias_target = None
        try:
            alias_target = next((a.collection_name for a in client.get_aliases().aliases if a.alias_name == ALIAS),
                                None)
        except Exception:  # noqa: BLE001 - no alias: the active collection is chosen again on first use
            alias_target = None
        for name, dim, model in _old_collections(client):
            points, compared, searches = _copy_collection(client, name, dim, model, storage, log)
            result.collections.append({"name": name, "embedding_model": model or "", "dim": dim, "points": points})
            result.points += points
            result.checked_points += compared
            result.checked_searches += searches
        if alias_target in {c["name"] for c in result.collections}:
            storage.set_alias(ALIAS, alias_target)
        storage.close()
        if not result.collections:  # an old store without any collection: an empty Edge storage stands for it
            (work / COLLECTIONS).mkdir(parents=True, exist_ok=True)
        _move_into_place(work, final, COLLECTIONS, ALIASES)
    except ConversionFailed:
        _discard(storage, work)
        raise
    except Exception as e:  # noqa: BLE001 - any other error: the old store stays in use, the reason is recorded
        _discard(storage, work)
        from orchestrator.utils.helpers import redact_secrets
        raise ConversionFailed(f"Converting the knowledge base failed: {type(e).__name__}: "
                               f"{redact_secrets(str(e))[:300]}", code="error") from None
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    result.seconds = round(time.monotonic() - started, 2)
    return result


def _discard(storage, work: Path) -> None:
    from orchestrator.rag.storage_lock import remove_tree
    try:
        storage.close()
    except Exception:  # noqa: BLE001
        pass
    if work.exists():
        remove_tree(work)


def _move_into_place(work: Path, final: Path, collections: str, aliases: str) -> None:
    """The checked copy becomes the Edge storage: renamed into place, or (when an empty Edge folder is there already)
    its collections and alias moved into that folder."""
    if not final.exists():
        os.replace(work, final)
        return
    (final / collections).mkdir(parents=True, exist_ok=True)
    moved = []
    try:
        for item in sorted((work / collections).iterdir()):
            target = final / collections / item.name
            if target.exists():
                raise KnowledgeBaseError(f"The Qdrant Edge storage already holds {item.name}.")
            os.replace(item, target)
            moved.append((item, target))
        if (work / aliases).is_file():
            os.replace(work / aliases, final / aliases)
    except Exception:
        for item, target in reversed(moved):  # never a half-moved storage: what was moved goes back to the copy
            os.replace(target, item)
        raise
    shutil.rmtree(work, ignore_errors=True)


def _record(state_dir: Path, record: Dict[str, Any], log: Log) -> None:
    """Writes the conversion record; a record that cannot be written is logged, never a reason to lose the converted
    storage (without the record the old folder is only not offered for deletion)."""
    try:
        write_record(state_dir, record)
    except OSError as e:
        if log:
            log(f"The record of the knowledge base conversion could not be written ({type(e).__name__}).")


def _without_conversion(state_dir: Path) -> Optional[Tuple[str, str]]:
    """The storage to use when there is nothing to convert, else None. A knowledge base in Qdrant Edge stays there:
    without the engine it cannot be opened (KnowledgeBaseError), and the old folder never stands in for it. Without the
    engine and without a Qdrant Edge storage, the older local storage is the knowledge base (Windows on ARM)."""
    if edge_in_use(state_dir):
        if not engine_available():
            raise KnowledgeBaseError(EDGE_UNAVAILABLE + ".")
        return "edge", ""
    if not engine_available():
        return "old", ENGINE_MISSING
    if not old_store_present(state_dir):
        return "edge", ""
    return None


def current(state_dir: Path) -> Tuple[str, str]:
    """(storage to use, reason) as things are, without converting anything: what a process uses after it already
    looked at the conversion once (it is tried again on the next start, or when the person asks). KnowledgeBaseError
    when the knowledge base is in Qdrant Edge and the engine cannot be loaded."""
    state_dir = Path(state_dir)
    settled = _without_conversion(state_dir)
    if settled is not None:
        return settled
    record = read_record(state_dir)
    reason = str(record.get("reason") or "") if record.get("state") == "failed" else ""
    return "old", reason or NOT_CONVERTED


def run(state_dir: Path, force: bool = False, log: Log = None, disk_usage: Any = None) -> Tuple[str, str]:
    """Converts the old store when it should be (should_try) and records the outcome. Returns (storage to use,
    reason): ("edge", "") or ("old", why the old store is still in use). Never raises for a failed conversion. Without
    the Qdrant Edge engine (no build for this computer) the older local storage is used (and made when there is none),
    and nothing is recorded: it is converted once the engine is there. A knowledge base that is in Qdrant Edge already
    is never swapped for the old folder: without the engine it raises KnowledgeBaseError (EDGE_UNAVAILABLE)."""
    state_dir = Path(state_dir)
    settled = _without_conversion(state_dir)
    if settled is not None:
        return settled
    go, why_not = should_try(state_dir, force)
    if not go:
        return "old", why_not
    now = datetime.now().isoformat(timespec="seconds")
    if log:
        log("Converting the knowledge base from the old storage to Qdrant Edge (vectors and texts are copied, "
            "nothing is embedded again; the old folder is kept).")
    try:
        done = convert(state_dir, log, disk_usage)
    except ConversionFailed as e:
        _record(state_dir, {"state": "failed", "failed_at": now, "code": e.code, "reason": str(e),
                            "old_folder": str(old_folder(state_dir)), "engine": engine_version(),
                            "retry": ("on the next start" if e.code in TRANSIENT else
                                      "llm-cli knowledge storage convert")}, log)
        if log:
            log(f"The knowledge base was not converted and keeps working in the old storage: {e}")
        return "old", str(e)
    _record(state_dir, {"state": "converted", "converted_at": now, "old_folder": str(old_folder(state_dir)),
                        "old_folder_kept": True, "collections": done.collections, "points": done.points,
                        "verified": {"counts": True, "points_compared": done.checked_points,
                                     "searches_compared": done.checked_searches},
                        "seconds": done.seconds, "engine": engine_version()}, log)
    if log:
        log(f"Knowledge base converted to Qdrant Edge: {done.points} points in {len(done.collections)} collections, "
            f"{done.checked_points} points and {done.checked_searches} searches compared, {done.seconds} s. The old "
            f"folder {old_folder(state_dir)} is kept until you delete it (Tools > Knowledge Snapshots... > Delete the "
            "old storage... in the GUI, or 'llm-cli knowledge storage delete-old').")
    return "edge", ""


# --- the old folder after the conversion: only the person deletes it --------------------------------------------------

OLD_STORE_NAME = "old knowledge base storage"


def old_store_plan(state_dir: Path):
    """The deletion plan of the old store's folder: offered only after it was converted to Qdrant Edge (before that,
    or when the conversion failed, it IS the knowledge base). None when there is nothing to list."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    state_dir = Path(state_dir)
    folder = old_folder(state_dir)
    record = read_record(state_dir)
    if record.get("state") != "converted" or not edge_in_use(state_dir):
        return None
    if not folder.is_dir() or folder.is_symlink():
        return None
    when = str(record.get("converted_at") or "")[:16].replace("T", " ")
    item = DeletionItem(kind="knowledge_store", speaking_name=OLD_STORE_NAME, full_path=str(folder),
                        file_name=folder.name, size_bytes=folder_bytes(folder),
                        what=("the knowledge base in the storage of earlier versions, kept after it was converted to "
                              f"Qdrant Edge{' on ' + when if when else ''}"))
    return plan_of([item], ("trash", "permanent"))


@dataclass
class OldStoreDeletion:
    done: bool = False
    freed_bytes: int = 0
    trash_id: str = ""
    problem: str = ""


def delete_old_store(state_dir: Path, accepted: Any, trash: Any = None, audit: Any = None,
                     lock_wait: float = 5.0) -> OldStoreDeletion:
    """Deletes the old store's folder exactly as the person accepted it (orchestrator/Core/deletion.py): into the
    trash or for good, in the GUI or at a terminal. The plan is built again first and must be the accepted one
    (PlanChanged otherwise); the deletion is audited before anything goes; the storage lock is held, so no conversion
    runs meanwhile. The record notes that the old folder is gone."""
    from orchestrator.Core.deletion import require_person, require_same
    from orchestrator.rag.storage_lock import storage_lock
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person deletes the old knowledge base storage, in the GUI or at a terminal.")
    state_dir = Path(state_dir)
    require_same(accepted, old_store_plan(state_dir), "the old knowledge base storage")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; nothing was deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    lock = storage_lock(edge_folder(state_dir))
    from filelock import Timeout
    try:
        lock.acquire(timeout=lock_wait)
    except Timeout:
        raise RuntimeError("The knowledge base is in use by another framework process; nothing was deleted. Try "
                           "again shortly.") from None
    try:
        item = accepted.plan.items[0]
        try:
            audit.record(accepted.actor, "knowledge", "framework", "person", "old_store_deleted", approved=True,
                         arguments={"bytes": item.size_bytes, "mode": accepted.mode},
                         detail="the old knowledge base storage " + ("moved to the trash" if accepted.mode == "trash"
                                                                     else "deleted for good"),
                         extra=accepted.audit_extra())
        except Exception:  # noqa: BLE001 - the reason could name a host path
            raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
        result = OldStoreDeletion()
        folder = Path(item.full_path)
        try:
            if accepted.mode == "trash":
                entry = trash.move("knowledge_store", folder, state_dir, "state", OLD_STORE_NAME, accepted,
                                   accepted.actor)
                result.trash_id = entry.id
            else:
                if folder.is_symlink() or not folder.is_dir():
                    raise OSError("not a plain folder")
                from orchestrator.rag.storage_lock import remove_tree
                remove_tree(folder)
                if folder.exists():
                    raise OSError("a file in it could not be deleted")
                result.freed_bytes = item.size_bytes
        except Exception as e:  # noqa: BLE001 - a TrashError or an OSError: the folder stays and is reported
            result.problem = type(e).__name__ if isinstance(e, OSError) else str(e)
            return result
        result.done = True
        record = read_record(state_dir)
        record.update(old_folder_kept=False, old_folder_removed_at=datetime.now().isoformat(timespec="seconds"),
                      old_folder_removed_how=accepted.mode)
        write_record(state_dir, record)
        return result
    finally:
        lock.release()


def summary(state_dir: Path) -> Dict[str, Any]:
    """The storage of the local knowledge base as the person's status shows it: the record's state (converted,
    failed or "" when there was nothing to convert), when, the reason of a failure, and whether the old folder is
    still kept (with its full path and size: for the person, never for an AI)."""
    state_dir = Path(state_dir)
    record = read_record(state_dir)
    out: Dict[str, Any] = {"state": str(record.get("state") or ""), "old_store": old_store_present(state_dir)}
    if record.get("state") == "converted":
        out.update(converted_at=record.get("converted_at", ""), points=record.get("points", 0),
                   old_folder_kept=bool(old_folder(state_dir).is_dir()))
    elif record.get("state") == "failed":
        out.update(failed_at=record.get("failed_at", ""), reason=record.get("reason", ""), code=record.get("code", ""),
                   retry=record.get("retry", ""))
    if out["old_store"] or old_folder(state_dir).is_dir():
        out.update(old_folder=str(old_folder(state_dir)), old_folder_bytes=folder_bytes(old_folder(state_dir)))
    return out


# --- what the person's status shows (llm-cli knowledge storage, Tools > Knowledge Snapshots...) ----------------------

@dataclass
class StorageStatus:
    """The storage of the local knowledge base as the person's status shows it (`storage_status`): the storage really
    in use (`used`: qdrant-edge, old-local, server, or "" when it cannot be opened, with the reason), why the old
    storage is still in use (`reason`, and `reason_code`: engine_missing, not_converted, failed, edge_unavailable,
    server or ""), the folder in use, the conversion record (converted or failed, when, how many excerpts, why it
    failed), the old folder with its size, and whether `llm-cli knowledge storage convert` would convert now
    (`can_convert`). Full paths: for the person, never for an AI."""
    used: str = ""
    reason: str = ""
    reason_code: str = ""
    folder: str = ""
    state: str = ""
    converted_at: str = ""
    points: int = 0
    failed_at: str = ""
    failure: str = ""
    code: str = ""
    old_folder: str = ""
    old_folder_bytes: int = 0
    can_convert: bool = False


def server_configured(config: Any) -> bool:
    """A Qdrant server is set (setting qdrant_url, as RAGManager reads it): the knowledge base then uses the server and
    never the local storage. `config`: the framework's configuration (anything with get(key, default))."""
    get = getattr(config, "get", None)
    if not callable(get):
        return False
    try:
        return bool(str(get("qdrant_url", "") or "").strip())
    except Exception:  # noqa: BLE001 - a configuration that cannot be read names no server
        return False


def convert_refusal(state_dir: Path, server: bool = False) -> str:
    """Why `llm-cli knowledge storage convert` would not convert now: "nothing" (no old store, or Qdrant Edge is in use
    already), "server" (a Qdrant server is set: `server`, see server_configured; the knowledge base opens the server,
    so nothing would be converted), "engine_missing" (no Qdrant Edge for this computer), or "" when it converts (also
    before any attempt and after a failed one)."""
    state_dir = Path(state_dir)
    if not old_store_present(state_dir) or edge_in_use(state_dir):
        return "nothing"
    if server:
        return "server"
    if not engine_available():
        return "engine_missing"
    return ""


def _reason_code(reason: str, state: str) -> str:
    if not reason:
        return ""
    if reason == ENGINE_MISSING:
        return "engine_missing"
    if reason == NOT_CONVERTED:
        return "not_converted"
    if reason == SERVER_IN_USE:
        return "server"
    return "failed" if state == "failed" else ""


def storage_status(state_dir: Path, info: Optional[Dict[str, Any]] = None, used: Optional[str] = None,
                   reason: str = "", server: bool = False) -> StorageStatus:
    """The storage status of the knowledge base in `state_dir`. `info`: migration.summary (read when not given);
    `used`: the storage an open knowledge base names (RAGManager.storage_format, or convert_storage's "used"), with its
    `reason`; without it, a Qdrant server when `server` (setting qdrant_url, see server_configured), else as the
    records say (migration.current: nothing is opened or changed)."""
    state_dir = Path(state_dir)
    info = summary(state_dir) if info is None else info
    state = str(info.get("state") or "")
    code = ""
    if not used and server:
        used = "server"
    server = server or used == "server"
    if used == "server" and not reason and old_store_present(state_dir) and not edge_in_use(state_dir):
        reason = SERVER_IN_USE   # the old storage stays as it is while the server is set
    if not used:
        try:
            use, reason = current(state_dir)
            used = "qdrant-edge" if use == "edge" else "old-local"
        except KnowledgeBaseError as e:
            used, reason, code = "", str(e), "edge_unavailable"
    folder = {"qdrant-edge": str(edge_folder(state_dir)), "old-local": str(old_folder(state_dir))}.get(used, "")
    return StorageStatus(
        used=used, reason=reason, reason_code=code or _reason_code(reason, state), folder=folder, state=state,
        converted_at=str(info.get("converted_at", "")), points=int(info.get("points") or 0),
        failed_at=str(info.get("failed_at", "")), failure=str(info.get("reason", "")), code=str(info.get("code") or ""),
        old_folder=str(info.get("old_folder") or ""), old_folder_bytes=int(info.get("old_folder_bytes") or 0),
        can_convert=convert_refusal(state_dir, server) == "")


def storage_lines(status: StorageStatus) -> List[Tuple[str, str]]:
    """The status in plain English lines for the command line, each with its style ("", "yellow" or "red"): the
    storage in use, the conversion and the old folder (the GUI says the same in the person's language,
    l10n_knowledge)."""
    from orchestrator.Core.knowledge_snapshots import size_text
    lines: List[Tuple[str, str]] = []
    if status.used == "qdrant-edge":
        lines.append(("", f"Storage: Qdrant Edge in {status.folder}."))
    elif status.used == "old-local":
        lines.append(("", f"Storage: the storage of earlier versions (qdrant-client's local mode) in {status.folder}."))
        if status.reason and status.state != "failed":
            lines.append(("", f"{status.reason}."))
    elif status.used == "server":
        lines.append(("", "Storage: a Qdrant server (setting qdrant_url); the local storage is not used."))
        if status.reason:
            lines.append(("", f"{status.reason}."))
    else:
        lines.append(("red", status.reason))
    if status.state == "converted":
        lines.append(("", f"Converted from the old storage on {status.converted_at} ({status.points} excerpts, counts "
                          "and sample searches checked)."))
    elif status.state == "failed":
        lines.append(("yellow", f"The old storage is still in use: its conversion failed on {status.failed_at}: "
                                f"{status.failure}"))
        lines.append(("", retry_sentence(status.code)))
    if status.old_folder:
        kept = size_text(status.old_folder_bytes)
        if status.state == "converted":
            lines.append(("", f"The old folder {status.old_folder} ({kept}) is kept until you delete it: 'llm-cli "
                              "knowledge storage delete-old' (or Tools > Knowledge Snapshots... > Delete the old "
                              "storage... in the GUI) lists it and asks you first."))
        else:
            lines.append(("", f"Old storage folder: {status.old_folder} ({kept})."))
    return lines
