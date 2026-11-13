#!/usr/bin/env python3
"""
Ditto's archive learns from tool results (DESIGN, Ditto, the archive's rows; decisions of 2026-11-13).

`ArchiveRecorder` subscribes to the tool registry's `ResultObservers`, so every tool call of every caller that ended ok
(Ditto's turns, an AI client over MCP, the CLI) is offered to the archive: the listed tool's extractor (Core/archive_
extractors.py) takes typed fields out of the result, and the archive stores an entry for each candidate that has a
ground. The subscriber only queues the work; one worker thread does it, so a call is never slowed or changed and a
failure is logged once and ignored. The work never downloads a model (`local_models_only`): without the embedding model
on this computer the archive is simply not offered anything.
"""

import queue
import threading
from typing import Any, Callable, Optional

from orchestrator.Core.archive_extractors import ExtractContext, extract
from orchestrator.Core.ditto_archive import DittoArchive
from orchestrator.utils import archive_sources
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger


class ArchiveRecorder:
    def __init__(self, archive: DittoArchive, capabilities: Callable[[str], Any] = lambda target: None):
        self.archive = archive
        self.context = ExtractContext(archive.table, capabilities)
        self.logger = get_logger("ArchiveRecorder")
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._unsubscribe: Optional[Callable[[], None]] = None
        self.offered = 0
        self.stored = 0
        self._listened = archive_sources.listened_tools(archive.table)

    def attach(self, registry: Any) -> "ArchiveRecorder":
        """Subscribes to the registry's result observers (nothing happens for a registry without them)."""
        results = getattr(registry, "results", None)
        if callable(getattr(results, "subscribe", None)):
            self._unsubscribe = results.subscribe(self.on_result)
        return self

    def on_result(self, tool: str, inp: Any, result: Any) -> None:
        """The registry's callback: queues a listed tool's result; every other result is not looked at."""
        if tool not in self._listened:
            return
        self._queue.put((tool, result))
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="archive-recorder", daemon=True)
                self._worker.start()

    def _work(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=5)
            except queue.Empty:
                return    # nothing came for a while: the worker ends, the next result starts a new one
            try:
                if item is not None:
                    self._offer(*item)
            except Exception as e:  # noqa: BLE001 - the archive never fails a call
                self.logger.warning(f"The archive could not take a result: {redact_secrets(str(e))}")
            finally:
                self._queue.task_done()
            if item is None:
                return

    def _offer(self, tool: str, result: Any) -> None:
        from orchestrator.Core.rag_manager import local_models_only
        candidates = extract(tool, result, self.context)
        with local_models_only():
            for candidate in candidates:
                self.offered += 1
                outcome = self.archive.record(candidate)
                if outcome.outcome in ("created", "merged", "replaced"):
                    self.stored += 1

    def flush(self, timeout: float = 30.0) -> bool:
        """Waits until everything queued is done (tests, and the process's end); False when the time ran out."""
        done = threading.Event()

        def wait() -> None:
            self._queue.join()
            done.set()
        threading.Thread(target=wait, daemon=True).start()
        return done.wait(timeout)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self.flush(5.0)


def attach_archive(framework: Any) -> Optional[ArchiveRecorder]:
    """The process's archive and recorder, subscribed to the framework's tool registry (None without one). The
    archive uses the knowledge base the framework creates on demand; a target's capabilities come from the build
    service."""
    registry = getattr(framework, "tool_registry", None)
    if registry is None:
        return None
    archive = DittoArchive(lambda: framework.ensure_rag_manager(), framework.config)
    framework.ditto_archive = archive
    service = getattr(framework, "build_service", None)

    def capabilities(target: str) -> Any:
        try:
            return service.capabilities(target) if service is not None else None
        except Exception:  # noqa: BLE001 - an unknown target declares nothing
            return None
    return ArchiveRecorder(archive, capabilities).attach(registry)


__all__ = ["ArchiveRecorder", "attach_archive"]
