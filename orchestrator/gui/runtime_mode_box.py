#!/usr/bin/env python3
"""
Ditto's local server in the AI settings: where it runs (bare metal or Docker, DECISIONS round 7 D) and the switch.

The switch takes the same code path as `llm-cli ditto runtime ensure --mode ... --force`: runtime_mode.ensure() in a
worker thread (its progress and its result come back on the application's worker relay), with the person's question
first ("No" is the default). Afterwards the old mode's leftovers are listed as every deletion the person sees is
listed (the chat panel's "Clear chat" style: name, full path or ID, file name, size and what it is; "No" by default),
and runtime_mode.remove_leftovers() removes exactly that list after the person's yes. Every text is plain text.
"""

import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from orchestrator.gui.plain import ask, button_text, one_line, plain_box
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.runtime import runtime_mode
from orchestrator.utils.localization import current_language, ui_text


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def _size(size: int) -> str:
    from orchestrator.gui.chat_panel.panel import size_text
    return size_text(size, current_language())


def leftovers_text(plan: Any) -> str:
    """The list of the question: for each object its name, full path (or Docker's ID), file name, size, what it is
    and where it goes (the trash for a build folder, deleted for good for Docker's objects), with the labels of the
    chat panel's "Clear chat"."""
    mode = plan.mode_offered[0]
    items = []
    for item in plan.items:
        where = (ui_text("chat.clear.path", path=one_line(item.full_path)) if item.full_path
                 else ui_text("runtime.mode.item.id", id=one_line(item.file_name)))
        goes = "runtime.mode.goes.trash" if runtime_mode.goes_to_trash(item, mode) else "runtime.mode.goes.deleted"
        items.append("\n".join((one_line(item.speaking_name), where,
                                ui_text("chat.clear.file", name=one_line(item.file_name)),
                                ui_text("chat.clear.size", size=_size(item.size_bytes)),
                                ui_text("chat.clear.what_label", what=one_line(item.what)), ui_text(goes))))
    return "\n\n".join(items)


class RuntimeModeBox(QGroupBox):
    """Shows the mode of Ditto's server and switches it (or installs it) for the person."""

    def __init__(self, framework: Any = None, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None):
        super().__init__(ui_text("runtime.mode.title"), parent)
        self.framework = framework
        self._factory = runtime_factory or self._runtime
        self._token: Optional[object] = None
        self._progress: Optional[object] = None
        self.installed = False
        self.mode: Optional[str] = None
        layout = QVBoxLayout(self)
        layout.addWidget(_label(ui_text("runtime.mode.intro")))
        self.lbl_mode = _label()
        layout.addWidget(self.lbl_mode)
        row = QHBoxLayout()
        row.addWidget(_label(ui_text("runtime.mode.label")))
        self.cb_mode = QComboBox(self)
        for mode in runtime_mode.MODES:
            self.cb_mode.addItem(ui_text(f"runtime.mode.choice.{mode}"), mode)
        row.addWidget(self.cb_mode, stretch=1)
        self.btn_switch = QPushButton(self)
        self.btn_switch.clicked.connect(self._switch)
        row.addWidget(self.btn_switch)
        layout.addLayout(row)
        self.lbl_progress = _label()
        self.lbl_progress.setStyleSheet("color: #888;")
        layout.addWidget(self.lbl_progress)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.refresh()

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)

    def refresh(self) -> None:
        """The mode and the build as recorded: no Docker call and no hardware probe, so the window opens at once (the
        switch itself checks everything again)."""
        try:
            rt = self._factory()
            mode, record = rt.mode(), rt.mode_record()
            active = rt.store.references().get("active")
            build = rt.store.get(active) if active else None
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: shown, the switch stays off
            self.lbl_mode.setText(one_line(str(e))[:300])
            self.btn_switch.setEnabled(False)
            return
        image = record.get("docker") if mode == "docker" else None
        if mode == "docker" and image:
            what = f"{image.get('tag') or '?'}, {image.get('made_by') or '?'}"
            self.lbl_mode.setText(ui_text("runtime.mode.now.docker", what=one_line(what)))
        elif build is not None:
            what = f"build {build.build_id[:16]}, {build.backend}"
            self.lbl_mode.setText(ui_text("runtime.mode.now.bare-metal", what=one_line(what)))
        else:
            self.lbl_mode.setText(ui_text("runtime.mode.now.none"))
        self.installed = bool(image) or build is not None
        self.mode = mode if self.installed else None
        self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(mode)))
        self.btn_switch.setText(button_text(ui_text("runtime.mode.switch" if self.installed
                                                    else "runtime.mode.install")))
        self.btn_switch.setEnabled(self._token is None)

    def _switch(self) -> None:
        target = self.cb_mode.currentData()
        name = ui_text(f"runtime.mode.name.{target}")
        if self.installed and target == self.mode:
            plain_box(self, "information", ui_text("runtime.mode.title"), ui_text("runtime.mode.same", mode=name))
            return
        question = "runtime.mode.question" if self.installed else "runtime.mode.install_question"
        if not ask(self, ui_text("runtime.mode.title"), ui_text(question, mode=name)):
            return
        self._token, self._progress = object(), object()
        token, progress = self._token, self._progress
        relay, factory, force = worker_relay(), self._factory, self.installed
        self.btn_switch.setEnabled(False)
        self.lbl_progress.setText(ui_text("runtime.mode.working"))

        def work() -> None:
            try:
                result = runtime_mode.ensure(factory(), mode=target, force=force, fetch_source=lambda: True,
                                             log=lambda msg: relay.done.emit(progress, msg))
                relay.done.emit(token, ("ok", result))
            except Exception as e:  # noqa: BLE001 - shown to the person as it is (plain text)
                relay.done.emit(token, ("error", str(e)))
        threading.Thread(target=work, name="ditto-runtime-mode", daemon=True).start()

    def _on_relay(self, token: object, result: Any) -> None:
        if token is self._progress and self._token is not None:
            self.lbl_progress.setText(one_line(str(result))[:300])
            return
        if token is not self._token or not isinstance(result, tuple) or len(result) != 2:
            return
        self._token = self._progress = None
        kind, value = result
        self.lbl_progress.setText("")
        if kind != "ok":
            plain_box(self, "warning", ui_text("runtime.mode.title"), ui_text("runtime.mode.failed", reason=value))
            self.refresh()
            return
        plain_box(self, "information", ui_text("runtime.mode.title"), "\n".join(value.lines))
        if value.leftovers is not None:
            self._offer_removal(value.leftovers)
        self.refresh()

    def _offer_removal(self, plan: Any) -> None:
        """The old mode's leftovers, each listed; exactly that list is removed after the person's yes."""
        title = ui_text("runtime.mode.title")
        mode = plan.mode_offered[0]   # one confirmation (round 7 D): build folders to the trash, Docker's objects gone
        # where the listed objects go, and only for what the list holds (a list of Docker objects names no trash)
        trashed = [runtime_mode.goes_to_trash(item, mode) for item in plan.items]
        where = ((ui_text("runtime.mode.leftovers.trash") if any(trashed) else "")
                 + (ui_text("runtime.mode.leftovers.docker")
                    if any(i.kind in ("docker_image", "docker_container") for i in plan.items) else ""))
        if not ask(self, title, ui_text("runtime.mode.leftovers.question", n=len(plan.items),
                                        size=_size(plan.total_bytes), where=where, items=leftovers_text(plan))):
            plain_box(self, "information", title, ui_text("runtime.mode.nothing_removed"))
            return
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        try:
            removal = runtime_mode.remove_leftovers(self._factory(), plan, actor="gui", audit=audit,
                                                    trash=self._trash() if mode == "trash" else None, mode=mode)
        except (runtime_mode.LeftoversChanged, runtime_mode.RuntimeModeError, PermissionError) as e:
            plain_box(self, "warning", title, str(e))
            return
        lines = [ui_text("runtime.mode.removed", n=len(removal.deleted), size=_size(removal.freed_bytes))]
        if removal.trashed:
            lines.append(ui_text("runtime.mode.trashed", n=len(removal.trashed)))
        lines += [ui_text("runtime.mode.kept", name=name, reason=reason) for name, reason in removal.failed]
        plain_box(self, "information", title, "\n".join(lines))

    def _trash(self) -> Any:
        """The person's trash (File > Trash...), or None when it cannot be opened (then nothing is removed)."""
        from orchestrator.Core.trash import Trash
        try:
            return Trash.for_framework(self.framework)
        except Exception:  # noqa: BLE001 - no framework or no per-user folder: remove_leftovers refuses
            return None
