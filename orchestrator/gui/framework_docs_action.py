#!/usr/bin/env python3
"""
Tools > Index the Framework's Documentation... (DECISIONS round 8, N12).

The person adds the framework's own documentation (README, CONTRIBUTING, docs) to the knowledge base, and with the
box ticked also its code and configuration, so Ditto can search the framework's internals. The question says how many
files it reads, that a snapshot is taken first and whether the disk has room for it ("No" is the default); without room
nothing is asked or indexed. The index runs in a worker thread and reports through the one WorkerRelay. Every text is
plain text.
"""

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QCheckBox, QMessageBox

from orchestrator.gui.plain import plain_box
from orchestrator.gui.worker_relay import worker_relay

TITLE = "Index the Framework's Documentation"


def _size(paths: List[Any]) -> str:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return f"{total / 1000 ** 2:.1f} MB" if total >= 10 ** 5 else f"{max(1, total // 1000)} kB"


def question_text(docs: List[Any], code: List[Any], disk: str = "") -> str:
    return (f"Add the framework's own documentation to the knowledge base, so Ditto can answer questions about the "
            f"framework itself?\n\nDocumentation: {len(docs)} files (README, CONTRIBUTING, the docs folder), "
            f"{_size(docs)}.\nCode and configuration the framework ships: {len(code)} files, {_size(code)}, only "
            "with the box below (takes minutes; never your settings, your own targets or installed packs).\n\nA "
            "snapshot of the knowledge base is taken first, so this can be undone with a restore. A file indexed "
            "before is replaced. The embedding model is downloaded once (about a GB) if it is not on this computer "
            "yet." + (f"\n\nDisk space for the snapshot: {disk}." if disk else ""))


class FrameworkDocsAction(QObject):
    """The action of the Tools menu; one index at a time."""

    def __init__(self, window: Any, framework: Any):
        super().__init__(window)
        self.window, self.framework = window, framework
        self._token: Optional[object] = None
        worker_relay().done.connect(self._finished, Qt.QueuedConnection)

    @property
    def running(self) -> bool:
        return self._token is not None

    def run(self) -> bool:
        """Asks, then starts the index in a worker thread; False when nothing was started."""
        if self.running:
            plain_box(self.window, "information", TITLE, "The framework's documentation is being indexed already.")
            return False
        ensure = getattr(self.framework, "ensure_rag_manager", None)
        rag = ensure() if callable(ensure) else None
        if rag is None:
            plain_box(self.window, "warning", TITLE, "The knowledge base is off. Switch it on in Tools > Configure AI "
                                                    "Agent (Ditto) first.")
            return False
        from orchestrator.Core.rag_manager import framework_documents
        from orchestrator.Core.knowledge_snapshots import snapshot_space
        docs, code = framework_documents(rag.app_root, True)
        service = getattr(self.framework, "build_service", None)
        checks = snapshot_space(rag, getattr(service, "disk_usage", None))
        short = [c for c in checks if not c.fits]
        if short:  # no snapshot, no index: never "continue anyway"
            plain_box(self.window, "warning", TITLE, "Not enough disk space for the snapshot taken first, so nothing "
                      "is indexed: " + "; ".join(c.sentence() for c in short) + ".\n\nFree space first; Tools > "
                      "Knowledge Snapshots... lists the snapshots with their sizes.")
            return False
        box = QMessageBox(QMessageBox.Question, TITLE, "", QMessageBox.Yes | QMessageBox.No, self.window)
        box.setTextFormat(Qt.PlainText)
        box.setText(question_text(docs, code, "; ".join(c.sentence() for c in checks)))
        check = QCheckBox("Also index the code and configuration", box)
        box.setCheckBox(check)
        box.setDefaultButton(QMessageBox.No)
        box.setEscapeButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return False
        self.start(rag, check.isChecked())
        return True

    def start(self, rag: Any, include_code: bool) -> None:
        token, relay = object(), worker_relay()
        self._token = token

        def work():
            try:
                result: Dict[str, Any] = rag.ingest_codebase(include_code=include_code)
            except Exception as e:  # noqa: BLE001 - reported to the person, the window keeps running
                result = {"success": False, "message": f"The index failed ({type(e).__name__})."}
            relay.done.emit(token, result)

        threading.Thread(target=work, name="framework-docs-index", daemon=True).start()
        status = getattr(self.window, "status", None)
        if status is not None:
            status.showMessage("Indexing the framework's documentation...", 10000)

    def _finished(self, token: object, result: Any) -> None:
        if token is not self._token:
            return
        self._token = None
        result = result if isinstance(result, dict) else {}
        text = str(result.get("message") or "The index ended without a result.")
        if result.get("snapshot"):
            text += f"\n\nSnapshot taken first: {result['snapshot']} (Tools > Knowledge Snapshots)."
        plain_box(self.window, "information" if result.get("success") else "warning", TITLE, text)


__all__ = ["FrameworkDocsAction", "question_text", "TITLE"]
