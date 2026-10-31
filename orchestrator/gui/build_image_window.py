#!/usr/bin/env python3
"""
Build > Build Container Images... (DECISIONS round 36; the mouse path of `llm-cli build image plan / sources /
export / import`).

The build container image is the Docker image with a target's toolchain (Core/builder.py); the target artifact,
package or bundle is what goes to the board. The window shows, for the chosen target, the build container images on
this computer (build_image.target_images), checks whether an offline build could make one (build_image.target_plan,
the one plan the build and the CLI use; in a worker thread, because the locality rule resolves names and never does so
on the GUI thread), lets the person set the build sources in the own network (Core/local_network.set_build_source,
person-only) and moves an image to or from a file with its sha256 (build_image.export_image / import_image, actor
"gui"). Every text is plain text; nothing is deleted here.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

SOURCE_FIELDS = (("build_apt_mirror", "txt_bimg_apt_mirror", "bimg.sources.apt_mirror"),
                 ("build_apt_proxy", "txt_bimg_apt_proxy", "bimg.sources.apt_proxy"),
                 ("build_pip_index", "txt_bimg_pip_index", "bimg.sources.pip_index"))


def plan_text(plan: Any, lang: Optional[str] = None) -> str:
    """The plan as the person reads it (each source with its host and the locality rule's reason)."""
    if plan.ready:
        head = ui_text("bimg.plan.ready", lang)
    else:
        head = ui_text("bimg.plan.missing", lang, count=len(plan.missing))
    return "\n".join([head] + ["- " + line for line in plan.lines(person=True)])


class BuildImageDialog(QDialog):
    """The build container images of one target: list, offline plan, build sources, export and import."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 select: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.config = getattr(framework, "config", None)
        self.setObjectName("dlg_build_images")
        self.setWindowTitle(self._t("bimg.title"))
        self.resize(760, 620)
        self._requests = {}
        self._relay = worker_relay()
        self._relay.done.connect(self._on_result, Qt.QueuedConnection)

        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("bimg.intro"), self))
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("bimg.target"), self, wrap=False))
        self.combo_target = QComboBox(self)
        self.combo_target.setObjectName("combo_bimg_target")
        for tid in self._targets():
            self.combo_target.addItem(tid, tid)
        if select:
            index = self.combo_target.findData(select)
            if index >= 0:
                self.combo_target.setCurrentIndex(index)
        self.combo_target.currentIndexChanged.connect(self.refresh_images)
        row.addWidget(self.combo_target, 1)
        self.chk_gpu = QCheckBox(self._t("bimg.gpu"), self)
        self.chk_gpu.setObjectName("chk_bimg_gpu")
        row.addWidget(self.chk_gpu)
        layout.addLayout(row)

        layout.addWidget(plain_label(self._t("bimg.images"), self))
        self.list_images = QListWidget(self)
        self.list_images.setObjectName("list_bimg_images")
        self.list_images.setMaximumHeight(110)
        layout.addWidget(self.list_images)

        buttons = QHBoxLayout()
        self.btn_plan = self._button("btn_bimg_plan", "bimg.plan.button", self.check_plan)
        self.btn_export = self._button("btn_bimg_export", "bimg.export.button", self.export_image)
        self.btn_import = self._button("btn_bimg_import", "bimg.import.button", self.import_image)
        for button in (self.btn_plan, self.btn_export, self.btn_import):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        form_import = QFormLayout()
        self.txt_sha256 = QLineEdit(self)
        self.txt_sha256.setObjectName("txt_bimg_sha256")
        form_import.addRow(plain_label(self._t("bimg.import.sha256"), self), self.txt_sha256)
        layout.addLayout(form_import)

        group = QGroupBox(self._t("bimg.sources"), self)
        form = QFormLayout(group)
        self.source_edits = {}
        from orchestrator.Core.local_network import build_sources
        current = build_sources(self.config) if self.config is not None else {}
        for key, name, label in SOURCE_FIELDS:
            edit = QLineEdit(current.get(key, ""), group)
            edit.setObjectName(name)
            edit.setPlaceholderText(self._t("bimg.sources.hint"))
            self.source_edits[key] = edit
            form.addRow(plain_label(self._t(label), group, wrap=False), edit)
        self.btn_save_sources = QPushButton(button_text(self._t("bimg.sources.save")), group)
        self.btn_save_sources.setObjectName("btn_bimg_save_sources")
        self.btn_save_sources.clicked.connect(self.save_sources)
        form.addRow(self.btn_save_sources)
        layout.addWidget(group)

        group_layer = QGroupBox(self._t("bimg.layer"), self)
        form_layer = QFormLayout(group_layer)
        self.lbl_layer = plain_label("", group_layer)
        self.lbl_layer.setObjectName("lbl_bimg_layer")
        form_layer.addRow(self.lbl_layer)
        self.combo_toolbox = QComboBox(group_layer)
        self.combo_toolbox.setObjectName("combo_bimg_toolbox")
        for way in ("build", "fetch"):
            self.combo_toolbox.addItem(self._t(f"bimg.layer.way.{way}"), way)
        form_layer.addRow(plain_label(self._t("bimg.layer.toolbox"), group_layer, wrap=False), self.combo_toolbox)
        self.btn_save_toolbox = QPushButton(button_text(self._t("bimg.layer.save")), group_layer)
        self.btn_save_toolbox.setObjectName("btn_bimg_save_toolbox")
        self.btn_save_toolbox.clicked.connect(self.save_toolbox)
        form_layer.addRow(self.btn_save_toolbox)
        layout.addWidget(group_layer)

        self.txt_result = QPlainTextEdit(self)
        self.txt_result.setObjectName("txt_bimg_result")
        self.txt_result.setReadOnly(True)
        layout.addWidget(self.txt_result, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh_images()
        self.refresh_layer()

    # --- helpers ---

    def _t(self, key: str, **params: Any) -> str:
        return ui_text(key, self.lang, **params)

    def _button(self, name: str, key: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.clicked.connect(slot)
        return button

    def _targets(self):
        tm = getattr(self.framework, "target_manager", None)
        try:
            found = tm.list_targets() if tm is not None and hasattr(tm, "list_targets") else []
        except Exception:  # noqa: BLE001 - no targets readable: an empty list
            return []
        ids = [str(t.get("id") or "") if isinstance(t, dict) else str(t) for t in found or []]
        return [tid for tid in ids if tid]

    def target(self) -> str:
        return str(self.combo_target.currentData() or self.combo_target.currentText() or "")

    def docker_client(self):
        from orchestrator.Core.build_image import docker_client
        engine = getattr(getattr(self.framework, "orchestrator", None), "build_engine", None)
        return docker_client(self.framework, engine)

    def _in_worker(self, kind: str, work: Callable[[], str]) -> None:
        """Runs `work()` in a worker thread; its text comes back through the relay to _on_result."""
        token = object()
        self._requests[kind] = token
        relay = self._relay
        self.txt_result.setPlainText(self._t("bimg.working") if kind != "plan" else self._t("bimg.plan.running"))

        def run():
            try:
                result = ("ok", work())
            except Exception as e:  # noqa: BLE001 - shown as plain text
                result = ("error", f"{e} {getattr(e, 'next', '')}".strip())
            relay.done.emit(token, result)

        threading.Thread(target=run, name=f"build-image-{kind}", daemon=True).start()

    def _on_result(self, token: object, result) -> None:
        kind = next((k for k, t in self._requests.items() if t is token), None)
        if kind is None or not isinstance(result, tuple) or len(result) != 2:
            return
        self._requests.pop(kind, None)
        try:
            self.txt_result.setPlainText(str(result[1]))
            if kind in ("import", "export"):
                self.refresh_images()
        except RuntimeError:   # the window was closed meanwhile
            pass

    # --- actions ---

    def refresh_images(self) -> None:
        from orchestrator.Core.build_image import target_images
        from orchestrator.utils.sizes import format_size
        self.list_images.clear()
        images = target_images(self.docker_client(), self.target()) if self.target() else []
        if not images:
            self.list_images.addItem(self._t("bimg.images.none"))
        for info in images:
            self.list_images.addItem(self._t("bimg.images.row", tags=one_line(", ".join(info.tags) or info.image_id[:19]),
                                             size=format_size(info.size, self.lang), created=one_line(info.created)))

    def check_plan(self) -> None:
        from orchestrator.Core.build_image import target_plan
        framework, target, gpu, lang = self.framework, self.target(), self.chk_gpu.isChecked(), self.lang
        self._in_worker("plan", lambda: plan_text(target_plan(framework, target, gpu=gpu), lang))

    def save_sources(self) -> None:
        from orchestrator.Core.local_network import LocalNetworkError, set_build_source
        try:
            for key, edit in self.source_edits.items():
                set_build_source(self.config, key, edit.text().strip(), self.lang)
        except LocalNetworkError as e:
            self.txt_result.setPlainText(str(e))
            return
        self.txt_result.setPlainText(self._t("bimg.sources.saved"))

    def refresh_layer(self) -> None:
        """The runtime layer's verdict, the toolbox way and the end folder's protection on this computer."""
        from orchestrator.Core import runtime_layer
        if self.config is None:
            return
        status = runtime_layer.layer_status(runtime_layer.cache_dir_of(self.config), self.config)
        index = self.combo_toolbox.findData(status["toolbox_way"])
        if index >= 0:
            self.combo_toolbox.setCurrentIndex(index)
        lines = [status["text"], self._t("bimg.layer.made", way=status["toolbox_made"] or "-"),
                 runtime_layer.END_PROTECTED if status["end_folder"] == "protected" else runtime_layer.END_UNPROTECTED]
        if status["toolbox_way"] == "fetch" and not status["fetch_ready"]:
            lines.append(runtime_layer.PREBUILT_MISSING)
        self.lbl_layer.setText("\n".join(lines))

    def save_toolbox(self) -> None:
        """The person's choice of how the runtime layer's tools are made (person-only: runtime_layer_toolbox)."""
        from orchestrator.Core import runtime_layer
        try:
            way = runtime_layer.set_toolbox_way(self.config, str(self.combo_toolbox.currentData() or "build"))
        except runtime_layer.ToolboxWayError as e:
            self.txt_result.setPlainText(str(e))
            return
        self.txt_result.setPlainText(self._t("bimg.layer.saved", way=way))
        self.refresh_layer()

    def export_image(self, path: Optional[str] = None) -> None:
        from orchestrator.Core.build_image import export_image
        from orchestrator.utils.sizes import format_size
        target = self.target()
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, self._t("bimg.export.title"),
                                                  str(Path.home() / f"{target.lower()}-build-image.tar"))
        if not path:
            return
        framework, client, lang = self.framework, self.docker_client(), self.lang

        def work() -> str:
            result = export_image(framework, target, Path(path), actor="gui", client=client)
            return ui_text("bimg.export.done", lang, file=str(result.file), size=format_size(result.bytes, lang),
                           sha256=result.sha256)
        self._in_worker("export", work)

    def import_image(self, path: Optional[str] = None, confirmed: Optional[bool] = None) -> None:
        from orchestrator.Core.build_image import import_image
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self._t("bimg.import.title"), str(Path.home()))
        if not path:
            return
        if confirmed is None:
            confirmed = ask(self, self._t("bimg.import.title"), self._t("bimg.import.confirm", file=path))
        if not confirmed:
            return
        framework, client, lang = self.framework, self.docker_client(), self.lang
        sha256 = self.txt_sha256.text().strip() or None

        def work() -> str:
            result = import_image(framework, Path(path), actor="gui", sha256=sha256, client=client)
            return ui_text("bimg.import.done", lang, target=result.target, tags=", ".join(result.images) or "-")
        self._in_worker("import", work)


def open_build_images(window, select: Optional[str] = None) -> BuildImageDialog:
    """Build > Build Container Images...: opens the window for the Builder tab's target."""
    builder = getattr(window, "builder_tab", None)
    if select is None and builder is not None and hasattr(builder, "target_combo"):
        select = builder.target_combo.currentData()
    dialog = BuildImageDialog(window.framework, window, select=select)
    dialog.show()
    return dialog


__all__ = ["BuildImageDialog", "SOURCE_FIELDS", "open_build_images", "plan_text"]
