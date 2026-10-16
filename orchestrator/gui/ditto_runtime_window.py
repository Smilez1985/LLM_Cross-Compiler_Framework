#!/usr/bin/env python3
"""
Ditto > Local Runtime...: everything `llm-cli ditto runtime ...` does, by mouse (W1').

One window, the same core calls as the CLI:

- Status: what `ditto runtime status` shows (runtime_overview.overview: hardware, mode, the build in use with its
  verification, problems and the rebuild advice, the self-built plan, the prebuilt option, the server with its URL,
  model and context, the recommended and the chosen model, the log file), in the person's language.
- Ditto's local server (gui/runtime_mode_box.py): install, check or switch the mode with `ensure`'s options (mode,
  GPU backend, where the build runs, compile jobs, the model offer afterwards); W2 unchanged.
- Build llama-server...: `ditto runtime install` with the same build options (plan, question, CPU build, GPU build);
  Install the official prebuilt archive...: `install --prebuilt` (with "Download it again": --force).
- Server: Start with a model tier (or the recommendation, which forgets an earlier choice), a context length and the
  idle unload; Stop.
- Builds and source: Import a build... / Export the build... (file dialogs), Remove older builds... (`prune`, the
  deletion dialog with the full list, nothing ticked, "No" by default), Export the source... with an optional mirror
  URL and Import the source... for a computer without network.

Long work (probes, builds, downloads, Docker, the server start, git) runs in a plain worker thread whose progress and
result come back on the application's worker relay; Cancel stops a build before its next step and a download between
two chunks. A question the core asks in the middle of a job (fetch the source? run the imported programs?) is asked
here on the GUI thread with "No" as the default, while the worker waits. Every text is plain text; the person sees
full paths; nothing here goes to an AI.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.runtime_mode_box import RuntimeModeBox, gib, throttled
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.runtime.runtime_overview import overview
from orchestrator.utils.localization import current_language, ui_text

CTX_MIN, CTX_MAX = 256, 1048576           # --ctx of `ditto runtime start` (click.IntRange(256, 1048576))
IDLE_MAX = 7 * 24 * 3600                  # --sleep-idle: any number of seconds; a week is the field's limit
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz")    # what `ditto runtime export` accepts


def _open_file(parent: QWidget, title: str, filters: str) -> str:
    """The operating system's open dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getOpenFileName(parent, title, str(Path.home()), filters)[0]


def _save_file(parent: QWidget, title: str, name: str, filters: str) -> str:
    """The operating system's save dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getSaveFileName(parent, title, str(Path.home() / name), filters)[0]


def archive_path(chosen: str) -> Path:
    """The export file as `ditto runtime export` requires it: ending in .tar.gz (added when the name has neither
    .tar.gz nor .tgz)."""
    path = Path(chosen)
    return path if path.name.endswith(ARCHIVE_SUFFIXES) else path.with_name(path.name + ".tar.gz")


def origin_text(inst: Any, lang: Optional[str] = None) -> str:
    if inst.origin == "imported":
        return ui_text("ditto_runtime.origin.imported", lang, on=one_line(inst.built_on or "?"))
    return ui_text("ditto_runtime.origin.prebuilt" if inst.origin == "prebuilt" else "ditto_runtime.origin.self-built",
                   lang)


def installed_text(inst: Any, lang: Optional[str] = None) -> str:
    """The build in use in the person's words: origin, build, backend (CPU fallback), llama.cpp build and commit, or
    the official archive."""
    if inst.build_id:
        backend = inst.backend + (ui_text("ditto_runtime.status.fallback", lang) if inst.role == "fallback" else "")
        return ui_text("ditto_runtime.status.build", lang, origin=origin_text(inst, lang), build=inst.build_id[:16],
                       backend=one_line(backend), llama=one_line(inst.build), commit=(inst.commit or "")[:8])
    return ui_text("ditto_runtime.status.prebuilt", lang, origin=origin_text(inst, lang),
                   asset=one_line(inst.asset_id), backend=one_line(inst.backend))


def status_text(view: Any, lang: Optional[str] = None) -> str:
    """`ditto runtime status` as plain text (runtime_overview decides which rows there are)."""
    status, inst = view.status, view.installed
    rows: List[tuple] = []

    def row(key: str, value: str) -> None:
        rows.append((ui_text(f"ditto_runtime.row.{key}", lang), value))
    row("llama", one_line(status.build))
    row("hardware", one_line(view.hardware.summary()))
    row("mode", ui_text(f"ditto_runtime.status.mode.{'docker' if status.mode == 'docker' else 'bare-metal'}", lang))
    if status.mode == "docker":
        image = status.docker or {}
        row("in_use", ui_text("ditto_runtime.status.image", lang, tag=one_line(image.get("tag") or "?"),
                              id=str(image.get("image_id") or "")[7:19], build=str(image.get("build_id") or "")[:16],
                              backend=one_line(image.get("backend") or "?"),
                              made_by=one_line(image.get("made_by") or "?"),
                              base=one_line(image.get("base") or "?"), at=one_line(image.get("verified_at") or "?")))
    if inst is not None:
        row("bare_metal" if status.mode == "docker" else "in_use", installed_text(inst, lang))
        facts = view.build_facts()
        if facts is not None:
            parts = [ui_text("ditto_runtime.status.placement", lang, placement=one_line(facts["placement"] or "?"))]
            if facts["duration_s"] is not None:
                parts.append(ui_text("ditto_runtime.status.seconds", lang, n=f"{facts['duration_s']:.0f}"))
            if facts["built_on"]:
                parts.append(ui_text("ditto_runtime.status.on", lang, on=one_line(facts["built_on"])))
            if facts["built_at"]:
                parts.append(one_line(facts["built_at"]))
            row("built", ", ".join(parts))
            devices = ", ".join(inst.devices) or ui_text("ditto_runtime.status.cpu_only", lang)
            row("verified", ui_text("ditto_runtime.status.verified", lang, at=one_line(inst.verified_at or "?"),
                                    devices=one_line(devices)))
        for note in inst.notes:
            row("note", one_line(note))
    else:
        row("in_use", ui_text("ditto_runtime.status.nothing", lang))
    for problem in view.extra_problems():
        row("problem", one_line(problem))
    if status.rebuild:
        row("rebuild", one_line(status.rebuild))
    plan_key = "ditto_runtime.status.plan.gpu" if view.gpu_after_cpu else "ditto_runtime.status.plan.only"
    row("plan", ui_text(plan_key, lang, backend=one_line(view.backend), reason=one_line(view.backend_reason)))
    prebuilt = view.prebuilt
    row("prebuilt", ui_text("ditto_runtime.status.prebuilt_option", lang,
                            asset=one_line(prebuilt.asset.id if prebuilt.asset else "-"),
                            reason=one_line(prebuilt.reason)))
    if status.running:
        where = (ui_text("ditto_runtime.status.container", lang, name=one_line(status.container)) if status.container
                 else ui_text("ditto_runtime.status.pid", lang, pid=status.pid))
        key = "ditto_runtime.status.server.ready" if status.healthy else "ditto_runtime.status.server.starting"
        row("server", ui_text(key, lang, url=one_line(status.base_url), where=where, model=one_line(status.model),
                              ctx=status.ctx))
    else:
        row("server", ui_text("ditto_runtime.status.server.stopped", lang))
    row("recommended", ui_text("ditto_runtime.status.recommended", lang, file=one_line(view.model.file),
                               why=one_line(view.tier_reason)))
    if view.chosen_differs:
        row("chosen", ui_text("ditto_runtime.status.chosen", lang, tier=one_line(view.preferred)))
    row("log", str(status.log_file))
    return "\n".join(f"{label}: {value}" for label, value in rows)


class LocalRuntimeWindow(QDialog):
    """Ditto > Local Runtime...: status, install and mode, builds, server, and the source for offline computers."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None):
        super().__init__(parent)
        self.setObjectName("dlg_ditto_runtime")
        self.framework = framework
        self.lang = current_language()
        self._factory = runtime_factory or self._runtime
        self._job: Optional[object] = None          # the token of the running job (one at a time)
        self._progress: Optional[object] = None
        self._done: Optional[Callable[[bool, Any], None]] = None
        self._status_token: Optional[object] = None
        self._status_again = False
        self._question = object()                   # the token of a question a worker waits for
        self._cancel = threading.Event()
        self._state: Dict[str, bool] = {"closed": False}
        state = self._state
        self.destroyed.connect(lambda *_: state.update(closed=True))   # a waiting worker gets "No"
        self.setWindowTitle(self._t("ditto_runtime.title"))
        self.resize(900, 820)

        inner = QWidget()
        body = QVBoxLayout(inner)
        body.addWidget(self._status_group())
        self.box = RuntimeModeBox(framework, inner, runtime_factory=self._factory, details=False)
        self.box.is_busy = lambda: self._job is not None
        self.box.changed.connect(self.refresh_status)
        body.addWidget(self.box)
        body.addWidget(self._build_group())
        body.addWidget(self._server_group())
        body.addWidget(self._files_group())
        body.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll, stretch=1)
        busy = QHBoxLayout()
        self.lbl_busy = plain_label("", self)
        busy.addWidget(self.lbl_busy, stretch=1)
        self.btn_cancel = self._button("ditto_runtime.cancel", "btn_ditto_runtime_cancel", self._cancel_job)
        self.btn_cancel.setVisible(False)
        busy.addWidget(self.btn_cancel)
        self.btn_close = self._button("ditto_runtime.close", "btn_ditto_runtime_close", self.close)
        busy.addWidget(self.btn_close)
        outer.addLayout(busy)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.fill_tiers()
        self.refresh_status()

    # --- building blocks --------------------------------------------------------------------------------------------

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)

    def _button(self, key: str, name: str, handler: Callable[[], None], tip: str = "") -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: handler())
        if tip:
            button.setToolTip(plain_tooltip(self._t(tip)))
        return button

    def _box(self, kind: str, text: str, details: str = "") -> None:
        plain_box(self, kind, self._t("ditto_runtime.title"), text, details=details)

    def _status_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_runtime.status.title"), self)
        layout = QVBoxLayout(group)
        self.txt_status = QPlainTextEdit(group)
        self.txt_status.setObjectName("txt_ditto_runtime_status")
        self.txt_status.setReadOnly(True)
        self.txt_status.setMinimumHeight(220)
        layout.addWidget(self.txt_status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_refresh = self._button("ditto_runtime.status.refresh", "btn_ditto_runtime_refresh",
                                        self.refresh_status)
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)
        return group

    def _build_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_runtime.build.title"), self)
        layout = QVBoxLayout(group)
        layout.addWidget(plain_label(self._t("ditto_runtime.build.intro"), group))
        row = QHBoxLayout()
        self.btn_build = self._button("ditto_runtime.build.button", "btn_ditto_runtime_build", self.build,
                                      "ditto_runtime.build.tip")
        row.addWidget(self.btn_build)
        self.btn_prebuilt = self._button("ditto_runtime.prebuilt.button", "btn_ditto_runtime_prebuilt",
                                         self.install_prebuilt, "ditto_runtime.prebuilt.tip")
        row.addWidget(self.btn_prebuilt)
        self.chk_force = QCheckBox(self._t("ditto_runtime.prebuilt.force"), group)
        self.chk_force.setObjectName("chk_ditto_runtime_force")
        row.addWidget(self.chk_force)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _server_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_runtime.server.title"), self)
        layout = QVBoxLayout(group)
        form = QFormLayout()
        self.cb_tier = QComboBox(group)
        self.cb_tier.setObjectName("cb_ditto_runtime_tier")
        form.addRow(self._t("ditto_runtime.server.tier"), self.cb_tier)
        self.sp_ctx = QSpinBox(group)
        self.sp_ctx.setObjectName("sp_ditto_runtime_ctx")
        self.sp_ctx.setRange(0, CTX_MAX)
        self.sp_ctx.setSingleStep(1024)
        self.sp_ctx.setSpecialValueText(self._t("ditto_runtime.automatic"))   # 0: from the free memory
        self.sp_ctx.setToolTip(plain_tooltip(self._t("ditto_runtime.server.ctx.tip")))
        form.addRow(self._t("ditto_runtime.server.ctx"), self.sp_ctx)
        self.sp_idle = QSpinBox(group)
        self.sp_idle.setObjectName("sp_ditto_runtime_idle")
        self.sp_idle.setRange(-1, IDLE_MAX)
        self.sp_idle.setValue(-1)
        self.sp_idle.setSuffix(" s")
        self.sp_idle.setSpecialValueText(self._t("ditto_runtime.server.idle.default"))   # -1: the catalog's default
        self.sp_idle.setToolTip(plain_tooltip(self._t("ditto_runtime.server.idle.tip")))
        form.addRow(self._t("ditto_runtime.server.idle"), self.sp_idle)
        layout.addLayout(form)
        self.lbl_tier = plain_label("", group)
        layout.addWidget(self.lbl_tier)
        row = QHBoxLayout()
        self.btn_start = self._button("ditto_runtime.server.start", "btn_ditto_runtime_start", self.start_server)
        row.addWidget(self.btn_start)
        self.btn_stop = self._button("ditto_runtime.server.stop", "btn_ditto_runtime_stop", self.stop_server)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _files_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_runtime.files.title"), self)
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.btn_import = self._button("ditto_runtime.import.button", "btn_ditto_runtime_import", self.import_build,
                                       "ditto_runtime.import.tip")
        row.addWidget(self.btn_import)
        self.btn_export = self._button("ditto_runtime.export.button", "btn_ditto_runtime_export", self.export_build,
                                       "ditto_runtime.export.tip")
        row.addWidget(self.btn_export)
        self.btn_prune = self._button("ditto_runtime.prune.button", "btn_ditto_runtime_prune", self.prune,
                                      "ditto_runtime.prune.tip")
        row.addWidget(self.btn_prune)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(plain_label(self._t("ditto_runtime.source.intro"), group))
        source = QHBoxLayout()
        source.addWidget(plain_label(self._t("ditto_runtime.source.mirror"), group))
        self.txt_mirror = QLineEdit(group)
        self.txt_mirror.setObjectName("txt_ditto_runtime_mirror")
        self.txt_mirror.setPlaceholderText(self._t("ditto_runtime.source.mirror.placeholder"))
        source.addWidget(self.txt_mirror, stretch=1)
        self.btn_export_source = self._button("ditto_runtime.export_source.button", "btn_ditto_runtime_export_source",
                                              self.export_source)
        source.addWidget(self.btn_export_source)
        self.btn_import_source = self._button("ditto_runtime.import_source.button", "btn_ditto_runtime_import_source",
                                              self.import_source)
        source.addWidget(self.btn_import_source)
        layout.addLayout(source)
        return group

    def _actions(self) -> List[QPushButton]:
        return [self.btn_build, self.btn_prebuilt, self.btn_start, self.btn_stop, self.btn_import, self.btn_export,
                self.btn_prune, self.btn_export_source, self.btn_import_source]

    # --- jobs off the GUI thread ------------------------------------------------------------------------------------

    def busy(self) -> bool:
        return self._job is not None or self.box._token is not None

    def _run(self, name: str, work: Callable[..., Any], done: Callable[[bool, Any], None], *, cancellable: bool = False,
             busy_key: str = "ditto_runtime.working") -> bool:
        """work(say, cancel, ask) in a worker thread; done(ok, value) on the GUI thread. One job at a time (with the
        mode box's)."""
        if self.busy():
            self._box("information", self._t("ditto_runtime.busy"))
            return False
        token, progress = object(), object()
        self._job, self._progress, self._done = token, progress, done
        self._cancel = cancel = threading.Event()
        relay = worker_relay()
        say = throttled(lambda text: relay.done.emit(progress, str(text)), every=0.1)
        for button in self._actions():
            button.setEnabled(False)
        self.lbl_busy.setText(self._t(busy_key))
        self.btn_cancel.setVisible(cancellable)
        self.btn_cancel.setEnabled(cancellable)

        def run() -> None:
            try:
                value = work(say, cancel, self._ask_from_worker)
                relay.done.emit(token, ("ok", value))
            except Exception as e:  # noqa: BLE001 - shown to the person (plain text)
                relay.done.emit(token, ("error", e))
        threading.Thread(target=run, name=f"ditto-runtime-{name}", daemon=True).start()
        return True

    def _cancel_job(self) -> None:
        """Cancel: a build stops before its next step, a download between two chunks (its part is kept)."""
        self._cancel.set()
        self.btn_cancel.setEnabled(False)
        self.lbl_busy.setText(self._t("ditto_runtime.cancelling"))

    def _ask_from_worker(self, text: str) -> bool:
        """A question of the core in the middle of a job: asked on the GUI thread ("No" by default) while the worker
        waits; "No" when the window is gone."""
        if threading.current_thread() is threading.main_thread():
            return ask(self, self._t("ditto_runtime.title"), text)
        event, answer = threading.Event(), []
        worker_relay().done.emit(self._question, (text, event, answer))
        while not event.wait(0.2):
            if self._state["closed"]:
                return False
        return bool(answer and answer[0])

    def _on_relay(self, token: object, result: Any) -> None:
        if token is self._question and isinstance(result, tuple) and len(result) == 3:
            text, event, answer = result
            try:
                answer.append(ask(self, self._t("ditto_runtime.title"), str(text)))
            finally:
                event.set()
            return
        if token is self._status_token and self._status_token is not None:
            self._status_done(result)
            return
        if token is self._progress and self._job is not None:
            self.lbl_busy.setText(one_line(str(result))[:300])
            return
        if token is not self._job or not isinstance(result, tuple) or len(result) != 2:
            return
        done = self._done
        self._job = self._progress = self._done = None
        for button in self._actions():
            button.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.lbl_busy.setText("")
        kind, value = result
        try:
            if done is not None:
                done(kind == "ok", value)
        finally:
            self.refresh_status()

    def _failed(self, error: Any) -> None:
        """A job's error as the CLI prints it: the message, and the server log's last lines when there are any."""
        self._box("warning", self._t("ditto_runtime.failed", reason=one_line(str(error))[:800]),
                  details=str(getattr(error, "log_tail", "") or ""))

    # --- status -----------------------------------------------------------------------------------------------------

    def refresh_status(self) -> None:
        """`ditto runtime status` in a worker thread (it probes the hardware and plans the build)."""
        if self._status_token is not None:
            self._status_again = True
            return
        self._status_token = token = object()
        self.txt_status.setPlainText(self._t("ditto_runtime.status.checking"))
        relay, factory, lang = worker_relay(), self._factory, self.lang

        def run() -> None:
            try:
                view = overview(factory())
                unusable = view.installed is None and view.status.mode != "docker"   # the box names why
                relay.done.emit(token, ("ok", (status_text(view, lang), list(view.status.problems) if unusable
                                               else [])))
            except Exception as e:  # noqa: BLE001 - e.g. no per-user folder
                relay.done.emit(token, ("error", str(e)))
        threading.Thread(target=run, name="ditto-runtime-status", daemon=True).start()

    def _status_done(self, result: Any) -> None:
        self._status_token = None
        kind, value = result if isinstance(result, tuple) and len(result) == 2 else ("error", "")
        if kind == "ok":
            text, problems = value
            self.txt_status.setPlainText(text)
            self.box.note_problems(problems)
        else:
            self.txt_status.setPlainText(self._t("ditto_runtime.status.failed", reason=one_line(value)[:600]))
        self.fill_tiers()
        if self._status_again:
            self._status_again = False
            self.refresh_status()

    def fill_tiers(self) -> None:
        """The model tiers of the catalog for Start; the tier chosen earlier (`start --model`) is named."""
        current = self.cb_tier.currentData() if self.cb_tier.count() else None
        try:
            rt = self._factory()
            preferred, models = rt.preferred_tier(), list(rt.catalog.models)
        except Exception as e:  # noqa: BLE001 - shown; Start then reports the reason itself
            preferred, models = None, []
            self.lbl_tier.setText(one_line(str(e))[:300])
        self.cb_tier.blockSignals(True)
        self.cb_tier.clear()
        self.cb_tier.addItem(self._t("ditto_runtime.tier.keep"), None)
        self.cb_tier.addItem(self._t("ditto_runtime.tier.auto"), "auto")
        for model in models:
            self.cb_tier.addItem(self._t("ditto_runtime.tier.model", tier=model.tier, file=one_line(model.file),
                                         size=gib(model.size, self.lang), licence=one_line(model.license)),
                                 model.tier)
        self.cb_tier.setCurrentIndex(max(0, self.cb_tier.findData(current)))
        self.cb_tier.blockSignals(False)
        if models:
            self.lbl_tier.setText(self._t("ditto_runtime.tier.chosen", tier=preferred) if preferred
                                  else self._t("ditto_runtime.tier.none"))

    # --- build (`ditto runtime install`) ----------------------------------------------------------------------------

    def build(self) -> None:
        """`ditto runtime install` with the build options of the mode box: the plan, the question before a build
        (the CLI's words, "No" by default), the question before the source is fetched, then the CPU and GPU build."""
        options, lang, factory = self.box.options(), self.lang, self._factory

        def work(say, cancel, ask_from_worker):
            rt = factory()
            plan = rt.plan_install(backend=options["backend"], placement=options["placement"])
            lines = list(plan.describe()) + list(plan.notes)
            for line in lines:
                say(line)
            if plan.needs_source and not ask_from_worker("\n".join(lines) + "\n\n"
                                                         + ui_text("ditto_runtime.build.question", lang)):
                return None
            pin = rt.catalog.llama_cpp.source

            def fetch_ok() -> bool:
                return ask_from_worker(ui_text("ditto_runtime.source.fetch_question", lang, tag=pin.tag,
                                               commit=pin.commit[:12]))
            result = rt.install(plan, jobs=options["jobs"], log=say, fetch_source=fetch_ok, cancelled=cancel.is_set)
            return {"result": result, "running": rt.endpoint() is not None, "docker": rt.mode() == "docker"}

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            elif value is None:
                self._box("information", self._t("ditto_runtime.build.nothing"))
            else:
                self._box("warning" if value["result"].requested_missing else "information", self.build_text(value))
        self._run("build", work, done, cancellable=True, busy_key="ditto_runtime.build.working")

    def build_text(self, value: Dict[str, Any]) -> str:
        result = value["result"]
        active, fallback = result.active, result.fallback
        lines = [self._t("ditto_runtime.build.ready", backend=one_line(active.backend), build=active.build_id[:16])]
        if fallback.build_id != active.build_id:
            lines.append(self._t("ditto_runtime.build.fallback", build=fallback.build_id[:16]))
        lines += [one_line(problem) for problem in result.problems]
        if result.older:
            lines.append(self._t("ditto_runtime.build.older", n=len(result.older)))
        if value["running"]:
            lines.append(self._t("ditto_runtime.build.running"))
        if value["docker"]:
            lines.append(self._t("ditto_runtime.build.docker"))
        return "\n".join(lines)

    def install_prebuilt(self) -> None:
        """`ditto runtime install --prebuilt [--force]`: the archive for this machine (or the chosen backend), why,
        and the CLI's question ("No" by default) before it is downloaded or used; Cancel stops the download."""
        backend, force, lang, factory = self.box.options()["backend"], self.chk_force.isChecked(), self.lang, \
            self._factory

        def work(say, cancel, ask_from_worker):
            from orchestrator.runtime.llama_server import LocalRuntimeError
            rt = factory()
            choice = rt.select_backend(backend)
            if choice.asset is None:
                raise LocalRuntimeError(choice.reason)
            head = ui_text("ditto_runtime.prebuilt.what", lang, build=rt.build, asset=choice.asset.id,
                           reason=choice.reason)
            if rt.prebuilt_installed(choice.asset) and not force:
                question = ui_text("ditto_runtime.prebuilt.use_question", lang, root=str(rt.root))
            else:
                question = ui_text("ditto_runtime.prebuilt.download_question", lang,
                                   size=gib(choice.asset.download_size, lang), root=str(rt.root))
            if not ask_from_worker(head + "\n\n" + question):
                return None
            return rt.install_prebuilt(prefer=backend, force=force, stop=cancel, progress=lambda name, got, total: say(
                ui_text("ditto_runtime.progress.download", lang, name=name, done=gib(got, lang),
                        total=gib(total, lang))))

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            elif value is not None:
                self._box("information", self._t("ditto_runtime.prebuilt.done", asset=one_line(value.asset_id),
                                                 path=str(value.path)))
        self._run("prebuilt", work, done, cancellable=True)

    # --- server (`ditto runtime start` / `stop`) --------------------------------------------------------------------

    def start_values(self) -> Optional[Dict[str, Any]]:
        """--model, --ctx and --sleep-idle from the fields (None for "automatic"/"default"); None when the context is
        below the CLI's minimum (the person is told)."""
        ctx = self.sp_ctx.value() or None
        if ctx is not None and ctx < CTX_MIN:
            self._box("warning", self._t("ditto_runtime.server.ctx.too_small", n=CTX_MIN))
            return None
        idle = self.sp_idle.value()
        return {"tier": self.cb_tier.currentData(), "ctx": ctx, "sleep_idle_seconds": None if idle < 0 else idle}

    def start_server(self) -> None:
        values, factory = self.start_values(), self._factory
        if values is None:
            return

        def work(say, cancel, ask_from_worker):
            rt = factory()
            endpoint = rt.start(tier=values["tier"], ctx=values["ctx"],
                                sleep_idle_seconds=values["sleep_idle_seconds"])
            return endpoint, rt.status()

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
                return
            endpoint, status = value
            where = (self._t("ditto_runtime.status.container", name=one_line(status.container)) if status.container
                     else self._t("ditto_runtime.status.pid", pid=endpoint.pid))
            self._box("information", self._t("ditto_runtime.server.started", url=one_line(endpoint.base_url),
                                             model=one_line(endpoint.model), where=where, ctx=status.ctx))
        self._run("start", work, done, busy_key="ditto_runtime.server.starting")

    def stop_server(self) -> None:
        factory = self._factory

        def work(say, cancel, ask_from_worker):
            return factory().stop()

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            else:
                self._box("information", self._t("ditto_runtime.server.stopped", n=value) if value
                          else self._t("ditto_runtime.server.not_running"))
        self._run("stop", work, done)

    # --- builds (`import`, `export`, `prune`) -----------------------------------------------------------------------

    def import_build(self) -> None:
        """`ditto runtime import FILE`: checked against its manifest and the pin; its programs run here only after the
        person's yes ("No" by default, as in the CLI)."""
        chosen = _open_file(self, self._t("ditto_runtime.import.choose"), self._t("ditto_runtime.archive.filter"))
        if not chosen:
            return
        path, lang, factory = Path(chosen), self.lang, self._factory

        def confirm_text(info: Dict[str, Any]) -> str:
            built_on = info.get("built_on") or {}
            return ui_text("ditto_runtime.import.question", lang, build=str(info.get("build_id", ""))[:16],
                           tag=one_line(info.get("tag")), commit=str(info.get("commit", ""))[:12],
                           os=one_line(built_on.get("os")), arch=one_line(built_on.get("arch")),
                           at=one_line(info.get("built_at")), files=one_line(", ".join(info.get("files") or [])))

        def work(say, cancel, ask_from_worker):
            return factory().import_build(path, lambda info: ask_from_worker(confirm_text(info)))

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            else:
                self._box("information", self._t("ditto_runtime.import.done", what=installed_text(value, self.lang)))
        self._run("import", work, done, busy_key="ditto_runtime.import.working")

    def export_build(self) -> None:
        """`ditto runtime export FILE`: the self-built build in use as a .tar.gz for another machine of the same
        kind."""
        chosen = _save_file(self, self._t("ditto_runtime.export.choose"), "llama-server-build.tar.gz",
                            self._t("ditto_runtime.archive.filter"))
        if not chosen:
            return
        path, factory = archive_path(chosen), self._factory

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            else:
                self._box("information", self._t("ditto_runtime.export.done", path=str(value)))
        self._run("export", lambda say, cancel, ask_from_worker: factory().export_build(path), done)

    def prune(self) -> None:
        """`ditto runtime prune`: the older builds (never the active build, the CPU fallback or the running one) in the
        deletion dialog, nothing ticked and "No" by default; exactly the ticked ones go to the trash or for good,
        audited, as the person chose."""
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        if self.busy():
            self._box("information", self._t("ditto_runtime.busy"))
            return
        try:
            rt = self._factory()
            plan = rt.cleanup_plan()
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder
            self._failed(e)
            return
        if plan is None:
            self._box("information", self._t("ditto_runtime.prune.none"))
            return
        accepted = ask_deletion(self, plan, multiple=True, title=self._t("ditto_runtime.prune.title"),
                                intro=self._t("ditto_runtime.prune.intro"), lang=self.lang)
        if accepted is None:
            return
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        try:
            result = rt.delete_old_builds(accepted, trash=Trash.for_framework(self.framework), audit=audit)
        except PlanChanged:
            self._box("warning", self._t("deletion.changed"))
            return
        except (PermissionError, RuntimeError, AttributeError) as e:
            self._box("warning", self._t("deletion.failed", reason=one_line(str(e))))
            return
        done = "deletion.done.trash" if accepted.mode == "trash" else "deletion.done.permanent"
        lines = [self._t(done, name=one_line(name)) for name in result.done]
        lines += [self._t("deletion.kept", name=one_line(name), reason=one_line(why)) for name, why in result.failed]
        self._box("information", "\n".join(lines) or self._t("ditto_runtime.prune.nothing"))
        self.refresh_status()

    # --- the source for a computer without network (`export-source`, `import-source`) -------------------------------

    def export_source(self) -> None:
        """`ditto runtime export-source FILE [--mirror URL]`: the pinned source as a git bundle plus FILE.json; when it
        is not cached yet, the CLI's question ("No" by default) comes before it is fetched."""
        from orchestrator.runtime.build import source as llama_source
        chosen = _save_file(self, self._t("ditto_runtime.export_source.choose"), "llama.cpp-source.bundle",
                            self._t("ditto_runtime.bundle.filter"))
        if not chosen:
            return
        path, mirror = Path(chosen), self.txt_mirror.text().strip() or None
        lang, factory = self.lang, self._factory

        def work(say, cancel, ask_from_worker):
            rt = factory()
            pin = llama_source.catalog_pin(rt.catalog)
            if llama_source.cached_source(pin, rt.data_dir) is None and not ask_from_worker(
                    ui_text("ditto_runtime.source.fetch_question", lang, tag=pin.tag, commit=pin.commit[:12])):
                return None
            manifest = llama_source.export_source(pin, rt.data_dir, path, url=mirror, log=say)
            return pin, manifest

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            elif value is not None:
                pin, manifest = value
                self._box("information", self._t("ditto_runtime.export_source.done", path=str(path),
                                                 size=gib(int(manifest["bundle_size"]), self.lang), tag=pin.tag,
                                                 commit=pin.commit[:12], name=path.name))
        self._run("export-source", work, done, busy_key="ditto_runtime.source.working")

    def import_source(self) -> None:
        """`ditto runtime import-source FILE`: the pinned source from a bundle of export-source, checked by its
        commit, so a computer without network can build."""
        from orchestrator.runtime.build import source as llama_source
        chosen = _open_file(self, self._t("ditto_runtime.import_source.choose"), self._t("ditto_runtime.bundle.filter"))
        if not chosen:
            return
        path, factory = Path(chosen), self._factory

        def work(say, cancel, ask_from_worker):
            rt = factory()
            pin = llama_source.catalog_pin(rt.catalog)
            return pin, llama_source.import_source(pin, rt.data_dir, path, log=say)

        def done(ok: bool, value: Any) -> None:
            if not ok:
                self._failed(value)
            else:
                pin, folder = value
                self._box("information", self._t("ditto_runtime.import_source.done", tag=pin.tag,
                                                 commit=pin.commit[:12], path=str(folder)))
        self._run("import-source", work, done, busy_key="ditto_runtime.source.working")


__all__ = ["LocalRuntimeWindow", "archive_path", "installed_text", "status_text"]
