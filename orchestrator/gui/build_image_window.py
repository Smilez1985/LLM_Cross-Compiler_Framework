#!/usr/bin/env python3
"""
Build > Build Container Images... (DESIGN round 36; the mouse path of `llm-cli build image plan / sources /
export / import`).

The build container image is the Docker image with a target's toolchain (Core/builder.py); the target artifact,
package or bundle is what goes to the board. The window shows, for the chosen target, the build container images on
this computer (build_image.target_images), checks whether an offline build could make one (build_image.target_plan,
the one plan the build and the CLI use; in a worker thread, because the locality rule resolves names and never does so
on the GUI thread), lets the person set the build sources in the own network (Core/local_network.set_build_source,
person-only) and moves an image to or from a file with its sha256 (build_image.export_image / import_image, actor
"gui"). It writes the build's preload scripts for what an offline build lacks and takes an archive in
(Core/build_preload.py), lists the target's source images and takes the files of one published only as a file from
the file the person chooses; PreloadDialog asks the same before the Builder starts an offline build. Every text is plain
text; nothing is deleted here.
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
        preload_row = QHBoxLayout()
        self.btn_preload_write = self._button("btn_bimg_preload_write", "bimg.preload.write", self.write_preload)
        self.btn_preload_take = self._button("btn_bimg_preload_take", "bimg.preload.take", self.take_preload)
        preload_row.addWidget(self.btn_preload_write)
        preload_row.addWidget(self.btn_preload_take)
        layout.addLayout(preload_row)
        source_row = QHBoxLayout()
        self.btn_source_list = self._button("btn_bimg_source_list", "bimg.source.list", self.list_source_images)
        self.btn_image_file = self._button("btn_bimg_image_file", "bimg.source.take", self.take_image_file)
        source_row.addWidget(self.btn_source_list)
        source_row.addWidget(self.btn_image_file)
        layout.addLayout(source_row)
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
        from orchestrator.Core.runtime_layer import TOOLBOX_WAYS
        for way in TOOLBOX_WAYS:
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
        # without its `checked` argument: a slot with an optional path would take False for it and do nothing
        button.clicked.connect(lambda _checked=False: slot())
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
            if kind in ("import", "export", "preload-take", "image-file"):
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
        made = status["toolbox_made"] or "-"
        if status["toolbox_setpriv"]:
            made = self._t("bimg.layer.made.setpriv", way=made, setpriv=status["toolbox_setpriv"])
        lines = [status["text"], self._t("bimg.layer.made", way=made),
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


    def write_preload(self) -> None:
        """Writes the build's preload scripts for what an offline build of the target lacks (Core/build_preload)."""
        framework, target, gpu, lang = self.framework, self.target(), self.chk_gpu.isChecked(), self.lang
        self._in_worker("preload-write", lambda: written_text(framework, target, gpu, lang))

    def take_preload(self, path: Optional[str] = None) -> None:
        """Takes in an archive the preload scripts wrote: checked first, then images loaded and sources kept."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self._t("bimg.preload.take.title"), str(Path.home()),
                                                  "Preload archive (*.tar.gz)")
        if not path:
            return
        framework, target = self.framework, self.target()
        sha256 = self.txt_sha256.text().strip()

        def work() -> str:
            from orchestrator.Core.build_preload import take_in
            return "\n".join(take_in(framework, target, Path(path), actor="gui", sha256=sha256).lines())
        self._in_worker("preload-take", work)

    def list_source_images(self) -> None:
        """The target's source images: by registry digest or by the file's sha256, and the files in the cache."""
        framework, target, lang = self.framework, self.target(), self.lang
        self._in_worker("source-list", lambda: source_images_text(framework, target, lang))

    def take_image_file(self, path: Optional[str] = None) -> None:
        """Takes the named files from an image its vendor publishes only as a file (Core/build_preload.take_image_file):
        the file's sha256 matched first, every layer checked, nothing loaded or run, the file not kept."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self._t("bimg.source.take.title"), str(Path.home()),
                                                  "Image archive (*.tar *.tar.gz *.tgz);;All files (*)")
        if not path:
            return
        framework, target = self.framework, self.target()

        def work() -> str:
            from orchestrator.Core.build_preload import image_file_in
            return "\n".join(image_file_in(framework, target, Path(path)).lines())
        self._in_worker("image-file", work)


def source_images_text(framework: Any, target: str, lang: Optional[str] = None) -> str:
    """The target's source images as the person reads them."""
    from orchestrator.Core.build_preload import source_images_of
    rows = source_images_of(framework, target)
    if not rows:
        return ui_text("bimg.source.none", lang, target=target)
    lines = []
    for row in rows:
        how = (ui_text("bimg.source.kind.file", lang, sha256=row["pin"]) if row["kind"] == "file"
               else ui_text("bimg.source.kind.registry", lang))
        lines.append(ui_text("bimg.source.row", lang, label=row["label"], how=how, platform=row["platform"],
                             cached=row["cached"], files=row["files"]))
        if row["kind"] == "file" and row["cached"] < row["files"]:
            lines.append(ui_text("bimg.source.give", lang))
    return "\n".join(lines)


def written_text(framework: Any, target: str, gpu: bool, lang: Optional[str] = None) -> str:
    """Writes the preload scripts when an offline build of `target` lacks sources; the person's text either way."""
    from orchestrator.Core import build_preload
    plan = build_preload.needed(framework, target, gpu=gpu)
    if plan is None:
        from orchestrator.Core.build_image import target_plan
        from orchestrator.Core.toolbox_preload import missing_for
        plan = target_plan(framework, target, gpu=gpu)
        if plan.ready and not missing_for(framework):
            return ui_text("bimg.preload.not_needed", lang, target=target)
    written = build_preload.write(framework, target, gpu=gpu, plan=plan)
    lines = [ui_text("bimg.preload.written", lang, folder=str(written.folder), what=written.what)]
    if written.not_covered:
        lines.append(ui_text("bimg.preload.not_covered", lang, items="; ".join(written.not_covered)))
    return "\n".join(lines)


class PreloadDialog(QDialog):
    """Before an offline build starts in the Builder: when its build container image is not here and sources are
    missing, the build's preload scripts are written, the dialog says where and what they fetch and waits for the
    archive's path; Take in and build checks and takes it in, Cancel starts nothing. The checks run in workers."""

    def __init__(self, framework: Any, target: str, gpu: bool = False, parent: Optional[QWidget] = None,
                 lang: Optional[str] = None):
        super().__init__(parent)
        self.framework, self.target, self.gpu = framework, target, gpu
        self.lang = lang or current_language()
        self.setObjectName("dlg_build_preload")
        self.setWindowTitle(ui_text("bimg.preload.dialog.title", self.lang))
        self.resize(640, 360)
        self.needed = None   # None: not known yet; False: nothing to do
        self._token = None
        self._relay = worker_relay()
        self._relay.done.connect(self._on_result, Qt.QueuedConnection)
        layout = QVBoxLayout(self)
        self.txt_info = QPlainTextEdit(self)
        self.txt_info.setObjectName("txt_preload_info")
        self.txt_info.setReadOnly(True)
        layout.addWidget(self.txt_info, 1)
        row = QHBoxLayout()
        row.addWidget(plain_label(ui_text("bimg.preload.path", self.lang), self, wrap=False))
        self.txt_path = QLineEdit(self)
        self.txt_path.setObjectName("txt_preload_path")
        row.addWidget(self.txt_path, 1)
        browse = QPushButton(button_text(ui_text("bimg.preload.browse", self.lang)), self)
        browse.setObjectName("btn_preload_browse")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.btn_go = self.buttons.addButton(button_text(ui_text("bimg.preload.go", self.lang)),
                                             QDialogButtonBox.AcceptRole)
        self.btn_go.setObjectName("btn_preload_go")
        self.btn_go.setEnabled(False)
        self.buttons.rejected.connect(self.reject)
        self.btn_go.clicked.connect(self.take)
        layout.addWidget(self.buttons)
        self.txt_info.setPlainText(ui_text("bimg.preload.checking", self.lang, target=target))
        self._run("check", self._check)

    def _check(self) -> str:
        from orchestrator.Core import build_preload
        try:
            plan = build_preload.needed(self.framework, self.target, gpu=self.gpu)
        except Exception:  # noqa: BLE001 - cannot be told here (no build engine): the build checks again itself
            return ""
        if plan is None:
            return ""
        try:
            return written_text(self.framework, self.target, self.gpu, self.lang)
        except build_preload.PreloadProblem as e:   # nothing a preload brings: the build's own stop says the rest
            return f"\x00{e} {e.next}".strip()

    def _run(self, kind: str, work: Callable[[], str]) -> None:
        token = (kind, object())
        self._token = token
        relay = self._relay

        def run():
            try:
                result = ("ok", work())
            except Exception as e:  # noqa: BLE001 - shown as plain text
                result = ("error", f"{e} {getattr(e, 'next', '')}".strip())
            relay.done.emit(token, result)

        threading.Thread(target=run, name=f"build-preload-{kind}", daemon=True).start()

    def _on_result(self, token: object, result) -> None:
        if token is not self._token or not isinstance(result, tuple):
            return
        kind, (state, text) = token[0], result
        try:
            if kind == "check":
                if state == "ok" and (not text or text.startswith("\x00")):
                    self.needed = False
                    self.accept()   # nothing a preload brings: the build goes on (and stops itself if it must)
                    return
                self.needed = True
                self.txt_info.setPlainText(text)
                self.btn_go.setEnabled(state == "ok")
            elif state == "ok":
                self.txt_info.setPlainText(text)
                self.accept()
            else:
                self.txt_info.appendPlainText("\n" + text)
                self.btn_go.setEnabled(True)
        except RuntimeError:   # closed meanwhile
            pass

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, ui_text("bimg.preload.take.title", self.lang), str(Path.home()),
                                              "Preload archive (*.tar.gz)")
        if path:
            self.txt_path.setText(path)

    def take(self) -> None:
        path = self.txt_path.text().strip()
        if not path:
            return
        self.btn_go.setEnabled(False)
        self.txt_info.appendPlainText("\n" + ui_text("bimg.preload.taking", self.lang))
        framework, target = self.framework, self.target

        def work() -> str:
            from orchestrator.Core.build_preload import take_in
            return "\n".join(take_in(framework, target, Path(path), actor="gui").lines())
        self._run("take", work)


def preload_before_build(parent: QWidget, framework: Any, target: str, gpu: bool = False) -> bool:
    """The Builder's Start Build in offline mode: True when the build may go on (nothing missing, or a preload was
    taken in), False when the person cancelled."""
    from orchestrator.utils.locality import offline_mode
    if not offline_mode(getattr(framework, "config", None)):
        return True
    dialog = PreloadDialog(framework, target, gpu, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


def open_build_images(window, select: Optional[str] = None) -> BuildImageDialog:
    """Build > Build Container Images...: opens the window for the Builder tab's target."""
    builder = getattr(window, "builder_tab", None)
    if select is None and builder is not None and hasattr(builder, "target_combo"):
        select = builder.target_combo.currentData()
    dialog = BuildImageDialog(window.framework, window, select=select)
    dialog.show()
    return dialog


__all__ = ["BuildImageDialog", "PreloadDialog", "SOURCE_FIELDS", "open_build_images", "plan_text",
           "preload_before_build", "source_images_text", "written_text"]
