#!/usr/bin/env python3
"""
Ditto's local server in the AI settings and in Ditto > Local Runtime...: where it runs (bare metal or Docker, DECISIONS
round 7 D), its installation and the switch.

The button takes the same code path as `llm-cli ditto runtime ensure` with every option of it (W1'):
runtime_mode.ensure() in a worker thread with the mode (auto, bare metal or Docker), the build options (GPU backend,
where the build runs, compile jobs) and, unless the person unticks it, the model offer afterwards (--no-model when
unticked); its progress and its result come back on the application's worker relay, and Cancel stops the build
between two steps. The person's question comes first ("No" is the default):

- nothing installed: ensure installs the chosen mode (auto: bare metal when it can be built here, else Docker);
- the mode that is installed (or auto): ensure checks it and installs it again only when it cannot be used (an old
  llama.cpp commit after an update, files that no longer match their manifest, a Docker image that is gone); it never
  refuses the same mode;
- the other mode: the switch (`ensure --mode ... --force`). Bare metal and Docker are two alternatives (W2): ensure()
  checks first that the new mode fits next to the old one. When it does not, nothing is built, and the person sees the
  sizes and may delete the old mode first (its full list, one question; Ditto is unavailable until the new mode works,
  the models stay); when the new mode then fails, the person is told so and offered the old mode again. Once the new
  mode works, every object of the old mode is listed as every deletion the person sees is listed (the chat panel's
  "Clear chat" style: name, full path or ID, file name, size and what it is; "No" by default, no choice per item), and
  runtime_mode.remove_leftovers() deletes exactly that list for good after the person's yes.

Before the pinned llama.cpp source is fetched (about 215 MB, when it is not cached yet), the CLI's question is asked on
the GUI thread ("No" by default) while the build waits; with No the build stops and says how to import the source.
After a run, the model ensure offers (runtime_mode.model_offer: the tier chosen earlier or the recommendation) is
named with its source, size and licence, and downloaded only after the person's yes (size and sha256 checked; Cancel
keeps the part for a resume). Every text is plain text.

One job of Ditto's runtime at a time in the whole GUI: this box exists in the AI settings and in Ditto > Local
Runtime... (also when "Status and more..." opens that window over the settings), so every job is registered here
(job_begins / job_ends) and none starts while another one runs.
"""

import threading
import time
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_tooltip
from orchestrator.gui.worker_relay import WorkerQuestion, ask_from_worker, worker_relay
from orchestrator.runtime import runtime_mode
from orchestrator.utils.localization import current_language, ui_text

# The choices of `ditto runtime install` and `ditto runtime ensure` (cli.py), in the same order.
BACKENDS = ("auto", "cpu", "vulkan", "cuda", "rocm", "metal")
PLACEMENTS = ("auto", "native", "container")
MAX_JOBS = 256

# The runtime jobs (builds, downloads, server start, ...) running in this GUI process, by their token: the AI settings'
# box and the Local Runtime window are separate widgets, and none of them starts a job while another one runs.
_JOBS: set = set()
_JOBS_LOCK = threading.Lock()


def job_begins(token: object) -> bool:
    """Registers a runtime job; False (nothing registered) while another one runs."""
    with _JOBS_LOCK:
        if _JOBS:
            return False
        _JOBS.add(token)
        return True


def job_ends(token: object) -> None:
    """The job is over (called on the GUI thread with its result, and by its worker thread when it ends, so a job
    whose widget was closed meanwhile does not block the next one)."""
    with _JOBS_LOCK:
        _JOBS.discard(token)


def job_running() -> bool:
    with _JOBS_LOCK:
        return bool(_JOBS)


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def _size(size: int) -> str:
    from orchestrator.gui.chat_panel.panel import size_text
    return size_text(size, current_language())


def gib(size: Optional[int], lang: Optional[str] = None) -> str:
    """A download size as the CLI shows it ("4.5 GiB"), with the German decimal comma; "?" when it is not known."""
    if not size:
        return "?"
    number = f"{size / 1024 ** 3:.1f}"
    return f"{number.replace('.', ',') if (lang or current_language()) == 'de' else number} GiB"


def throttled(emit: Callable[[str], None], every: float = 0.25) -> Callable[[str], None]:
    """`emit` at most every `every` seconds (a download reports every chunk; the window only needs a few lines)."""
    last = [0.0]

    def say(text: str) -> None:
        now = time.monotonic()
        if now - last[0] >= every:
            last[0] = now
            emit(text)
    return say


def leftovers_text(plan: Any) -> str:
    """The list of a question: for each object its name, full path (or Docker's ID), file name, size and what it is,
    with the labels of the chat panel's "Clear chat". Everything on it is deleted for good (W2: no trash)."""
    items = []
    for item in plan.items:
        where = (ui_text("chat.clear.path", path=one_line(item.full_path)) if item.full_path
                 else ui_text("runtime.mode.item.id", id=one_line(item.file_name)))
        items.append("\n".join((one_line(item.speaking_name), where,
                                ui_text("chat.clear.file", name=one_line(item.file_name)),
                                ui_text("chat.clear.size", size=_size(item.size_bytes)),
                                ui_text("chat.clear.what_label", what=one_line(item.what)))))
    return "\n\n".join(items)


class RuntimeModeBox(QGroupBox):
    """Shows the mode of Ditto's server and installs, checks or switches it for the person (`ditto runtime ensure`)."""

    changed = Signal()   # a run of ensure or a model download ended (the Local Runtime window shows its status again)

    def __init__(self, framework: Any = None, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None, *, details: bool = True):
        """`details`: a button that opens Ditto > Local Runtime... (the full status, builds, server and source); the
        window itself embeds the box without it."""
        super().__init__(ui_text("runtime.mode.title"), parent)
        self.framework = framework
        self._factory = runtime_factory or self._runtime
        self._token: Optional[object] = None
        self._progress: Optional[object] = None
        self._pending: tuple = (None, None)   # (the mode being made, the old mode deleted before it, if any)
        self._kind: Optional[str] = None      # the job that runs: "ensure" or "model"
        self._force = False                   # the running ensure is a switch (--force)
        self._cancel = threading.Event()
        self._problems: list = []             # why the recorded build cannot be used (from the window's status)
        self.is_busy: Callable[[], bool] = lambda: False   # the Local Runtime window: another job of it runs
        self._question = object()             # the token of a question a worker waits for (fetch the source?)
        self._state = {"closed": False}
        state = self._state
        self.destroyed.connect(lambda *_: state.update(closed=True))   # a waiting worker gets "No"
        self.installed = False
        self.mode: Optional[str] = None
        self._shown: Optional[tuple] = None   # (installed, mode) the mode choice was last set for
        layout = QVBoxLayout(self)
        layout.addWidget(_label(ui_text("runtime.mode.intro")))
        self.lbl_mode = _label()
        layout.addWidget(self.lbl_mode)
        row = QHBoxLayout()
        row.addWidget(_label(ui_text("runtime.mode.label")))
        self.cb_mode = QComboBox(self)
        self.cb_mode.setObjectName("cb_ditto_runtime_mode")
        self.cb_mode.addItem(ui_text("ditto_runtime.mode.choice.auto"), "auto")
        for mode in runtime_mode.MODES:
            self.cb_mode.addItem(ui_text(f"runtime.mode.choice.{mode}"), mode)
        self.cb_mode.currentIndexChanged.connect(self._update_button)
        row.addWidget(self.cb_mode, stretch=1)
        self.btn_switch = QPushButton(self)
        self.btn_switch.setObjectName("btn_ditto_runtime_switch")
        self.btn_switch.clicked.connect(self._switch)
        row.addWidget(self.btn_switch)
        self.btn_cancel = QPushButton(button_text(ui_text("ditto_runtime.cancel")), self)
        self.btn_cancel.setObjectName("btn_ditto_runtime_mode_cancel")
        self.btn_cancel.clicked.connect(self._cancel_job)
        self.btn_cancel.setVisible(False)
        row.addWidget(self.btn_cancel)
        layout.addLayout(row)
        layout.addLayout(self._options_row())
        self.chk_model = QCheckBox(ui_text("ditto_runtime.offer_model"), self)
        self.chk_model.setObjectName("chk_ditto_runtime_offer_model")
        self.chk_model.setChecked(True)   # the CLI offers it unless --no-model
        self.chk_model.setToolTip(plain_tooltip(ui_text("ditto_runtime.offer_model.tip")))
        layout.addWidget(self.chk_model)
        self.btn_details: Optional[QPushButton] = None
        if details:
            more = QHBoxLayout()
            more.addStretch(1)
            self.btn_details = QPushButton(button_text(ui_text("ditto_runtime.details")), self)
            self.btn_details.setObjectName("btn_ditto_runtime_details")
            self.btn_details.setAutoDefault(False)
            self.btn_details.setToolTip(plain_tooltip(ui_text("ditto_runtime.details.tip")))
            self.btn_details.clicked.connect(self._open_details)
            more.addWidget(self.btn_details)
            layout.addLayout(more)
        self.lbl_progress = _label()
        self.lbl_progress.setStyleSheet("color: #888;")
        layout.addWidget(self.lbl_progress)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.refresh()

    def _options_row(self) -> QHBoxLayout:
        """--backend, --placement and --jobs of `ditto runtime ensure` (and of `install` in the Local Runtime
        window)."""
        row = QHBoxLayout()
        row.addWidget(_label(ui_text("ditto_runtime.options.backend")))
        self.cb_backend = QComboBox(self)
        self.cb_backend.setObjectName("cb_ditto_runtime_backend")
        for backend in BACKENDS:
            self.cb_backend.addItem(ui_text(f"ditto_runtime.backend.{backend}"), backend)
        self.cb_backend.setToolTip(plain_tooltip(ui_text("ditto_runtime.options.backend.tip")))
        row.addWidget(self.cb_backend)
        row.addWidget(_label(ui_text("ditto_runtime.options.placement")))
        self.cb_placement = QComboBox(self)
        self.cb_placement.setObjectName("cb_ditto_runtime_placement")
        for placement in PLACEMENTS:
            self.cb_placement.addItem(ui_text(f"ditto_runtime.placement.{placement}"), placement)
        self.cb_placement.setToolTip(plain_tooltip(ui_text("ditto_runtime.options.placement.tip")))
        row.addWidget(self.cb_placement)
        row.addWidget(_label(ui_text("ditto_runtime.options.jobs")))
        self.sp_jobs = QSpinBox(self)
        self.sp_jobs.setObjectName("sp_ditto_runtime_jobs")
        self.sp_jobs.setRange(0, MAX_JOBS)
        self.sp_jobs.setSpecialValueText(ui_text("ditto_runtime.automatic"))   # 0: from the CPUs and free memory
        self.sp_jobs.setToolTip(plain_tooltip(ui_text("ditto_runtime.options.jobs.tip")))
        row.addWidget(self.sp_jobs)
        row.addStretch(1)
        return row

    def options(self) -> dict:
        """The build options as `ensure` and `install` take them: backend, placement and jobs (None: automatic)."""
        return {"backend": self.cb_backend.currentData(), "placement": self.cb_placement.currentData(),
                "jobs": self.sp_jobs.value() or None}

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)

    def _open_details(self) -> Any:
        """Ditto > Local Runtime... from the AI settings: the full status (is the server running, which build, what is
        wrong) and every other runtime command; shown over the settings, which wait until it is closed."""
        from orchestrator.gui.ditto_runtime_window import LocalRuntimeWindow
        try:
            window = LocalRuntimeWindow(self.framework, self.window(), runtime_factory=self._factory)
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text (SEC-M2)
            plain_box(self, "critical", ui_text("ditto_runtime.title"), str(e))
            return None
        self._details_window = window
        window.finished.connect(lambda _result: self.refresh())
        window.open()   # window-modal over the AI settings: its own questions and jobs work as in the menu's window
        return window

    def refresh(self) -> None:
        """The mode and the build as recorded: no Docker call and no hardware probe, so the window opens at once. The
        button never refuses the recorded mode: ensure checks it and installs it again when it cannot be used."""
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
            text = ui_text("runtime.mode.now.docker", what=one_line(what))
        elif build is not None:
            what = f"build {build.build_id[:16]}, {build.backend}"
            text = ui_text("runtime.mode.now.bare-metal", what=one_line(what))
        else:
            text = ui_text("runtime.mode.now.none")
        if self._problems:
            text += "\n" + ui_text("ditto_runtime.mode.unusable", problems=one_line("; ".join(self._problems))[:600])
        self.lbl_mode.setText(text)
        self.installed = bool(image) or build is not None
        self.mode = mode if self.installed else None
        if self._shown != (self.installed, self.mode):   # the person's choice stays until the installation changes
            self._shown = (self.installed, self.mode)
            self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(mode if self.installed else "auto")))
        self._update_button()
        self.btn_switch.setEnabled(self._token is None)

    def note_problems(self, problems: list) -> None:
        """Why the recorded build cannot be used (the Local Runtime window's status): shown under the mode."""
        self._problems = list(problems or [])
        self.refresh()

    def _update_button(self, *_args) -> None:
        target = self.cb_mode.currentData()
        if not self.installed:
            key = "runtime.mode.install"
        elif target in ("auto", self.mode):
            key = "ditto_runtime.mode.repair"
        else:
            key = "runtime.mode.switch"
        self.btn_switch.setText(button_text(ui_text(key)))

    def _name(self, mode: Optional[str]) -> str:
        return ui_text("ditto_runtime.mode.name.auto" if mode == "auto" else f"runtime.mode.name.{mode}")

    def busy(self) -> bool:
        """A job of this box, of the window around it, or of any other runtime widget of this GUI runs."""
        return self._token is not None or self.is_busy() or job_running()

    def _switch(self) -> None:
        title = ui_text("runtime.mode.title")
        if self.busy():
            plain_box(self, "information", title, ui_text("ditto_runtime.busy"))
            return
        target = self.cb_mode.currentData()
        switch = self.installed and target not in ("auto", self.mode)
        if switch:
            question = ui_text("runtime.mode.question", mode=self._name(target))
        elif self.installed and target == "auto":   # ensure (auto): what works stays, else bare metal is preferred
            question = ui_text("ditto_runtime.mode.repair_auto_question", mode=self._name(self.mode))
        elif self.installed:   # the same mode: checked, and installed again only when it cannot be used
            question = ui_text("ditto_runtime.mode.repair_question", mode=self._name(self.mode))
        else:
            question = ui_text("runtime.mode.install_question", mode=self._name(target))
        if not ask(self, title, question):
            return
        self._start(target, switch)

    def _ask_from_worker(self, text: str) -> bool:
        """A question of the core in the middle of a job (fetch the source?): asked on the GUI thread ("No" by
        default) while the worker waits; "No" when the box is gone."""
        if threading.current_thread() is threading.main_thread():
            return ask(self, ui_text("runtime.mode.title"), text)
        return ask_from_worker(self._question, ui_text("runtime.mode.title"), text,
                               stop=lambda: self._state["closed"], interval=0.2)

    def _fetch_question(self, rt: Any) -> bool:
        """`ditto runtime ensure`'s question before the pinned source is fetched, in the person's language."""
        pin = rt.catalog.llama_cpp.source
        return self._ask_from_worker(ui_text("ditto_runtime.source.fetch_question", tag=pin.tag,
                                             commit=pin.commit[:12]))

    def _begin(self) -> tuple:
        """A new job of this box: its token (registered for the whole GUI), progress token and Cancel event."""
        self._token, self._progress = object(), object()
        job_begins(self._token)   # the button checked busy() before; a chained job follows its own finished one
        self._cancel = threading.Event()
        return self._token, self._progress, self._cancel

    def _working(self, on: bool) -> None:
        self.btn_switch.setEnabled(not on)
        self.btn_cancel.setVisible(on)
        self.btn_cancel.setEnabled(on)

    def _cancel_job(self) -> None:
        """Cancel: a build stops before its next step (the step running at that moment finishes, nothing more is
        activated); a download stops between two chunks and keeps its part for a resume."""
        self._cancel.set()
        self.btn_cancel.setEnabled(False)
        self.lbl_progress.setText(ui_text("ditto_runtime.cancelling"))

    def _start(self, target: str, force: bool, after_removal: Optional[str] = None) -> None:
        """runtime_mode.ensure() in a worker thread with the build options; `force`: the switch; `after_removal`: the
        old mode the person deleted before this mode is made (a failure then offers it again)."""
        token, progress, cancel = self._begin()
        self._pending = (target, after_removal)
        self._kind, self._force = "ensure", force
        relay, factory = worker_relay(), self._factory
        options, offer = self.options(), self.chk_model.isChecked()
        self._working(True)
        self.lbl_progress.setText(ui_text("runtime.mode.working"))

        def work() -> None:
            try:
                rt = factory()
                result = runtime_mode.ensure(rt, mode=target, force=force, placement=options["placement"],
                                             backend=options["backend"], jobs=options["jobs"],
                                             fetch_source=lambda: self._fetch_question(rt),
                                             log=lambda msg: relay.done.emit(progress, msg),
                                             cancelled=cancel.is_set,
                                             container_build=runtime_mode.container_build_requested())
                offered: Any = None
                if offer:
                    try:
                        offered = runtime_mode.model_offer(rt)
                    except Exception as e:  # noqa: BLE001 - shown; the runtime itself is installed
                        offered = str(e)
                relay.done.emit(token, ("ok", (result, offered)))
            except runtime_mode.SpaceShortage as e:   # nothing was built: the person decides (W2)
                relay.done.emit(token, ("space", e))
            except Exception as e:  # noqa: BLE001 - shown to the person as it is (plain text)
                relay.done.emit(token, ("error", str(e)))
            finally:
                job_ends(token)
        threading.Thread(target=work, name="ditto-runtime-mode", daemon=True).start()

    def _on_relay(self, token: object, result: Any) -> None:
        if token is self._question and isinstance(result, WorkerQuestion):
            result.reply(lambda: ask(self, result.title, result.text))
            return
        if token is self._progress and self._token is not None:
            self.lbl_progress.setText(one_line(str(result))[:300])
            return
        if token is not self._token or not isinstance(result, tuple) or len(result) != 2:
            return
        job = self._kind
        job_ends(token)
        self._token = self._progress = None
        self._kind = None
        self._working(False)
        self.lbl_progress.setText("")
        kind, value = result
        try:
            if job == "model":
                self._model_done(kind, value)
            else:
                self._ensure_done(kind, value)
        finally:
            self.changed.emit()

    def _ensure_done(self, kind: str, value: Any) -> None:
        target, after_removal = self._pending
        if kind == "space" and after_removal is None:
            self._space_shortage(value)
            self.refresh()
            return
        if kind != "ok":
            reason = "\n".join(value.estimate.lines()) if kind == "space" else value
            if after_removal is not None:
                self._offer_old_again(target, after_removal, str(reason))
            else:
                key = "runtime.mode.failed" if self._force else "ditto_runtime.mode.failed"
                plain_box(self, "warning", ui_text("runtime.mode.title"), ui_text(key, reason=reason))
            self.refresh()
            return
        result, offered = value if isinstance(value, tuple) else (value, None)
        lines = list(result.lines)
        if isinstance(offered, runtime_mode.ModelOffer) and not offered.needed:
            lines.append(ui_text("ditto_runtime.model.there", file=one_line(offered.model.file)))
        plain_box(self, "information", ui_text("runtime.mode.title"), "\n".join(lines))
        if result.leftovers is not None:
            self._offer_removal(result.leftovers)
        if isinstance(offered, str):
            plain_box(self, "warning", ui_text("runtime.mode.title"),
                      ui_text("ditto_runtime.model.unknown", reason=one_line(offered)[:600]))
        elif isinstance(offered, runtime_mode.ModelOffer) and offered.needed:
            self._offer_model(offered)
        self.refresh()

    # --- the model offer (as `ditto runtime ensure` makes it; --no-model: the box unticked) ---------------------------

    def _offer_model(self, offer: Any) -> None:
        """The model of the chosen or recommended tier: its source, size and licence first; downloaded only after the
        person's yes ("No" by default) and only with a pinned size and sha256."""
        model, title = offer.model, ui_text("runtime.mode.title")
        text = ui_text("ditto_runtime.model.offer", repo=one_line(model.repo), file=one_line(model.file),
                       size=gib(model.size), licence=one_line(model.license), why=one_line(offer.why))
        if not model.pinned:
            plain_box(self, "warning", title, text + "\n\n" + ui_text("ditto_runtime.model.unpinned"))
            return
        if not ask(self, title, text + "\n\n" + ui_text("ditto_runtime.model.question", size=gib(model.size),
                                                       folder=str(offer.folder), licence=one_line(model.license))):
            return
        self._download(model.tier)

    def _download(self, tier: str) -> None:
        """LocalLLMRuntime.download_model in a worker thread (size and sha256 checked); Cancel keeps the part."""
        token, progress, cancel = self._begin()
        self._kind = "model"
        relay, factory = worker_relay(), self._factory
        lang = current_language()
        say = throttled(lambda text: relay.done.emit(progress, text))
        self._working(True)
        self.lbl_progress.setText(ui_text("ditto_runtime.model.downloading"))

        def work() -> None:
            try:
                path = factory().download_model(tier, stop=cancel, progress=lambda name, done, total: say(ui_text(
                    "ditto_runtime.progress.download", lang, name=name, done=gib(done, lang), total=gib(total, lang))))
                relay.done.emit(token, ("ok", str(path)))
            except Exception as e:  # noqa: BLE001 - a stopped, failed or refused download: shown as it is
                relay.done.emit(token, ("error", str(e)))
            finally:
                job_ends(token)
        threading.Thread(target=work, name="ditto-model-download", daemon=True).start()

    def _model_done(self, kind: str, value: Any) -> None:
        title = ui_text("runtime.mode.title")
        if kind == "ok":
            plain_box(self, "information", title, ui_text("ditto_runtime.model.saved", path=str(value)))
        else:
            plain_box(self, "warning", title, ui_text("ditto_runtime.model.failed", reason=one_line(value)[:600]))
        self.refresh()

    # --- the old mode (W2) ------------------------------------------------------------------------------------------

    def _audit(self) -> Any:
        return getattr(getattr(self.framework, "tool_registry", None), "audit", None)

    def _removed_text(self, removal: Any) -> str:
        lines = [ui_text("runtime.mode.removed", n=len(removal.deleted), size=_size(removal.freed_bytes))]
        from orchestrator.Core.deletion import kept_lines
        lines += kept_lines(removal)   # each kept object with its reason and every path that stayed (Core/deletion)
        return "\n".join(lines)

    def _offer_removal(self, plan: Any) -> None:
        """The new mode works: every object of the old mode, each listed; exactly that list is deleted for good after
        the person's one yes (round 7 D, W2: no choice per item, no trash)."""
        title = ui_text("runtime.mode.title")
        if not ask(self, title, ui_text("runtime.mode.leftovers.question", n=len(plan.items),
                                        size=_size(plan.total_bytes), items=leftovers_text(plan))):
            plain_box(self, "information", title, ui_text("runtime.mode.nothing_removed"))
            return
        try:
            removal = runtime_mode.remove_leftovers(self._factory(), plan, actor="gui", audit=self._audit())
        except (runtime_mode.LeftoversChanged, runtime_mode.RuntimeModeError, PermissionError) as e:
            plain_box(self, "warning", title, str(e))
            return
        plain_box(self, "information", title, self._removed_text(removal))

    def _space_shortage(self, shortage: Any) -> None:
        """W2: the new mode does not fit next to the old one, so nothing was built: the sizes, and the person may
        delete the old mode first (its full list, one question, "No" by default); then the new mode is made. Deleting
        it first is offered only while the new mode may fit then (estimated again without the old mode's build);
        otherwise the person is told so and asked nothing."""
        title = ui_text("runtime.mode.title")
        new, old, plan = shortage.estimate.mode, shortage.old_mode, shortage.old_plan
        names = {"new": ui_text(f"runtime.mode.what.{new}"), "old": ui_text(f"runtime.mode.what.{old}")}
        sizes = "\n".join(shortage.estimate.lines())
        if plan is None:
            plain_box(self, "warning", title, ui_text("runtime.mode.space.nothing_left", sizes=sizes, **names))
            return
        after = shortage.after_removal
        if shortage.removal_helps is False:
            plain_box(self, "warning", title, ui_text("runtime.mode.space.no_help", sizes=sizes,
                                                      after="\n".join(after.lines()), **names))
            return
        if after is not None:
            sizes += "\n\n" + ui_text("runtime.mode.space.after", **names) + "\n" + "\n".join(after.lines())
        if not ask(self, title, ui_text("runtime.mode.space.question", sizes=sizes, n=len(plan.items),
                                        size=_size(plan.total_bytes), items=leftovers_text(plan), **names)):
            plain_box(self, "information", title, ui_text("runtime.mode.space.declined"))
            return
        try:
            removal = runtime_mode.remove_leftovers(self._factory(), plan, actor="gui", audit=self._audit(),
                                                    before_switch=True, removes=old)
        except (runtime_mode.LeftoversChanged, runtime_mode.RuntimeModeError, PermissionError) as e:
            plain_box(self, "warning", title, str(e))
            return
        plain_box(self, "information", title, ui_text("runtime.mode.space.removed", **names) + "\n"
                  + self._removed_text(removal))
        self._start(new, True, after_removal=old)

    def _offer_old_again(self, new: str, old: str, reason: str) -> None:
        """The new mode failed after the old one was deleted: Ditto has no local runtime now. Say so and offer the old
        mode again ("No" by default)."""
        title = ui_text("runtime.mode.title")
        names = {"new": ui_text(f"runtime.mode.what.{new}"), "old": ui_text(f"runtime.mode.what.{old}")}
        if ask(self, title, ui_text("runtime.mode.space.failed", reason=one_line(reason)[:600], **names)):
            self._start(old, True)
        else:
            plain_box(self, "information", title, ui_text("runtime.mode.space.later", mode=old))
