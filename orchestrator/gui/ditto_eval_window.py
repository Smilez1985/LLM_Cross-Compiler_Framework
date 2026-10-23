#!/usr/bin/env python3
"""
Ditto > Evaluation...: `llm-cli ditto eval` by mouse (W1').

The window offers the CLI's options: the kinds of cases (and for retrieval the search variants fused and rerank),
single cases, another cases file, the runs per case, the managed local model tiers (several), or a provider, model and
reasoning effort for this run only (the saved AI settings stay unchanged: Core/ai_override.ditto_with). Before a run
it says what will be called and whether that costs money (a cloud provider bills every request), with "No" as the
default button. The run is evals/eval_run.run_evaluation, the same call as the CLI, in a plain worker thread; each
finished case, each started run and each busy step come back on the application's worker relay. Cancel stops before
the next attempt (the case that runs finishes first; a tier's server is stopped as after a normal run). The results are
the CLI's tables; "Save report..." writes the CLI's JSON report (`--json FILE`) to a file the person chooses.

A run of local tiers is a task of Ditto's local runtime (it stops your server and starts its own): it waits while
another one runs in Ditto > Local Runtime..., Ditto > Local Models... or the AI settings, and they wait for it
(runtime_mode_box.job_begins). Every text is plain text; the person sees full paths; nothing here goes to an AI
except the evaluation's own cases.
"""

import contextlib
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from orchestrator.evals import eval_run
from orchestrator.evals.ditto_eval import KINDS, RAG_VARIANTS
from orchestrator.gui.ai_choice import catalog_of, chosen, fill_models, fill_providers, is_local
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.runtime_mode_box import job_begins, job_ends, job_running
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.runtime.ditto_models import gib
from orchestrator.utils.localization import current_language, ui_text

EFFORTS = ("low", "medium", "high")   # `ditto eval --effort`
REPEAT_MAX = 20                       # click.IntRange(1, 20)


def _save_file(parent: QWidget, title: str, name: str, filters: str) -> str:
    """The operating system's save dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getSaveFileName(parent, title, str(Path.home() / name), filters)[0]


def _open_file(parent: QWidget, title: str, filters: str) -> str:
    """The operating system's open dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getOpenFileName(parent, title, str(Path.home()), filters)[0]


class _RelayEvents(eval_run.EvalEvents):
    """Hands what the run tells to the GUI thread (the worker never touches a widget)."""

    def __init__(self, token: object):
        self.token = token
        self.relay = worker_relay()

    def _emit(self, *payload: Any) -> None:
        self.relay.done.emit(self.token, payload)

    def busy(self, step: str, **values: Any):
        self._emit("busy", step, values)
        return contextlib.nullcontext()

    def run_started(self, label, name, model, cases, repeat):
        self._emit("run", label, name, model, cases, repeat)

    def result(self, result):
        self._emit("result", result.case.id, result.pass_rate, result.seconds,
                   result.first_problem() if result.pass_rate < 1 else "")

    def server_replaced(self, model):
        self._emit("replaced", model)

    def unused_options(self):
        self._emit("unused")


class EvaluationWindow(QDialog):
    """Ditto > Evaluation...: the choices of `ditto eval`, a run with progress and Cancel, the results, the report."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_ditto_eval")
        self.framework = framework
        self.ditto = getattr(framework, "ditto_manager", None)
        self.lang = lang or current_language()
        self._factory = runtime_factory or self._runtime
        self.catalog = catalog_of(self.ditto, getattr(framework, "config", None))
        self.cases_file: Optional[Path] = None
        self.runs: List[Any] = []                   # the reports of the last run (what "Save report..." writes)
        self.kinds_shown: List[str] = []
        self._job: Optional[object] = None          # the token of the running evaluation
        self._events: Optional[object] = None
        self._stop = threading.Event()
        self._done_cases = 0
        self.setWindowTitle(self._t("ditto_eval.title"))
        self.resize(980, 860)

        inner = QWidget()
        body = QVBoxLayout(inner)
        body.addWidget(plain_label(self._t("ditto_eval.intro"), inner))
        body.addWidget(self._cases_group())
        body.addWidget(self._model_group())
        self.lbl_cost = plain_label(self._t("ditto_eval.cost"), inner)
        self.lbl_cost.setObjectName("lbl_ditto_eval_cost")
        self.lbl_cost.setStyleSheet("color: #ffd27f;")
        body.addWidget(self.lbl_cost)
        body.addWidget(self._results_group())
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll, stretch=1)
        self.progress = QProgressBar(self)
        self.progress.setObjectName("pb_ditto_eval")
        self.progress.setVisible(False)
        outer.addWidget(self.progress)
        self.lbl_busy = plain_label("", self)
        self.lbl_busy.setObjectName("lbl_ditto_eval_busy")
        outer.addWidget(self.lbl_busy)
        row = QHBoxLayout()
        self.btn_run = self._button("ditto_eval.run", "btn_ditto_eval_run", self.start)
        self.btn_save = self._button("ditto_eval.save", "btn_ditto_eval_save", self.save_report)
        self.btn_save.setEnabled(False)
        self.btn_cancel = self._button("ditto_eval.cancel", "btn_ditto_eval_cancel", self.cancel)
        self.btn_cancel.setVisible(False)
        self.btn_close = self._button("ditto_eval.close", "btn_ditto_eval_close", self.close)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_close)
        outer.addLayout(row)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.fill_cases()
        self.fill_tiers()

    # --- building blocks --------------------------------------------------------------------------------------------

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)

    def _tell(self, kind: str, text: str, details: str = "") -> None:
        plain_box(self, kind, self._t("ditto_eval.title"), text, details=details)

    def _cases_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_eval.group.cases"), self)
        group.setObjectName("grp_ditto_eval_cases")
        box = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("ditto_eval.cases_file"), group, wrap=False))
        self.txt_cases_file = QLineEdit(group)
        self.txt_cases_file.setObjectName("txt_ditto_eval_cases_file")
        self.txt_cases_file.setReadOnly(True)
        self.txt_cases_file.setText(self._t("ditto_eval.cases_file.default"))
        row.addWidget(self.txt_cases_file, stretch=1)
        self.btn_cases_file = self._button("ditto_eval.cases_file.choose", "btn_ditto_eval_cases_file",
                                           self.choose_cases_file)
        self.btn_cases_default = self._button("ditto_eval.cases_file.reset", "btn_ditto_eval_cases_default",
                                              lambda: self.set_cases_file(None))
        row.addWidget(self.btn_cases_file)
        row.addWidget(self.btn_cases_default)
        box.addLayout(row)

        self.grp_kinds = QGroupBox(self._t("ditto_eval.kinds"), group)
        self.grp_kinds.setObjectName("grp_ditto_eval_kinds")
        kinds = QHBoxLayout(self.grp_kinds)
        self.chk_kinds: Dict[str, QCheckBox] = {}
        for kind in KINDS:
            check = QCheckBox(button_text(self._t(f"ditto_eval.kind.{kind}")), self.grp_kinds)
            check.setObjectName(f"chk_ditto_eval_kind_{kind}")
            kinds.addWidget(check)
            self.chk_kinds[kind] = check
        box.addWidget(self.grp_kinds)

        self.grp_variants = QGroupBox(self._t("ditto_eval.variants"), group)
        self.grp_variants.setObjectName("grp_ditto_eval_variants")
        variants = QHBoxLayout(self.grp_variants)
        self.chk_variants: Dict[str, QCheckBox] = {}
        for variant in RAG_VARIANTS:
            check = QCheckBox(button_text(self._t(f"ditto_eval.variant.{variant}")), self.grp_variants)
            check.setObjectName(f"chk_ditto_eval_variant_{variant}")
            check.setChecked(variant == "fused")
            variants.addWidget(check)
            self.chk_variants[variant] = check
        variants.addStretch(1)
        box.addWidget(self.grp_variants)

        box.addWidget(plain_label(self._t("ditto_eval.cases_label"), group))
        self.lst_cases = QListWidget(group)
        self.lst_cases.setObjectName("lst_ditto_eval_cases")
        self.lst_cases.setSelectionMode(QAbstractItemView.NoSelection)
        self.lst_cases.setMinimumHeight(150)
        box.addWidget(self.lst_cases)
        form = QFormLayout()
        self.sp_repeat = QSpinBox(group)
        self.sp_repeat.setObjectName("sp_ditto_eval_repeat")
        self.sp_repeat.setRange(1, REPEAT_MAX)
        self.sp_repeat.setValue(1)
        form.addRow(plain_label(self._t("ditto_eval.repeat"), group, wrap=False), self.sp_repeat)
        box.addLayout(form)
        return group

    def _model_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_eval.group.model"), self)
        group.setObjectName("grp_ditto_eval_model")
        box = QVBoxLayout(group)
        box.addWidget(plain_label(self._t("ditto_eval.tiers"), group))
        self.lst_tiers = QListWidget(group)
        self.lst_tiers.setObjectName("lst_ditto_eval_tiers")
        self.lst_tiers.setSelectionMode(QAbstractItemView.NoSelection)
        self.lst_tiers.setMaximumHeight(130)
        self.lst_tiers.itemChanged.connect(lambda _item: self._tiers_changed())
        box.addWidget(self.lst_tiers)
        form = QFormLayout()
        self.cb_provider = QComboBox(group)
        self.cb_provider.setObjectName("cb_ditto_eval_provider")
        fill_providers(self.cb_provider, self.catalog, self._t("ditto_eval.settings"))
        self.cb_model = QComboBox(group)
        self.cb_model.setObjectName("cb_ditto_eval_model")
        self.cb_model.setEditable(True)
        self.cb_model.lineEdit().setPlaceholderText(self._t("ditto_eval.model_placeholder"))
        fill_models(self.cb_model, self.catalog, "")
        self.cb_provider.currentIndexChanged.connect(
            lambda _i: fill_models(self.cb_model, self.catalog, self.cb_provider.currentData() or ""))
        self.cb_effort = QComboBox(group)
        self.cb_effort.setObjectName("cb_ditto_eval_effort")
        self.cb_effort.addItem(self._t("ditto_eval.settings"), "")
        for effort in EFFORTS:
            self.cb_effort.addItem(self._t(f"ditto_eval.effort.{effort}"), effort)
        form.addRow(plain_label(self._t("ditto_eval.provider"), group, wrap=False), self.cb_provider)
        form.addRow(plain_label(self._t("ditto_eval.model"), group, wrap=False), self.cb_model)
        form.addRow(plain_label(self._t("ditto_eval.effort"), group, wrap=False), self.cb_effort)
        box.addLayout(form)
        return group

    def _results_group(self) -> QGroupBox:
        group = QGroupBox(self._t("ditto_eval.group.results"), self)
        group.setObjectName("grp_ditto_eval_results")
        box = QVBoxLayout(group)
        self.log = QPlainTextEdit(group)
        self.log.setObjectName("log_ditto_eval")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(130)
        box.addWidget(self.log)
        self.tbl_results = self._table(group, "tbl_ditto_eval_results")
        box.addWidget(self.tbl_results)
        self.lbl_retrieval = plain_label(self._t("ditto_eval.retrieval"), group)
        self.lbl_retrieval.setVisible(False)
        box.addWidget(self.lbl_retrieval)
        self.tbl_retrieval = self._table(group, "tbl_ditto_eval_retrieval")
        self.tbl_retrieval.setVisible(False)
        box.addWidget(self.tbl_retrieval)
        return group

    @staticmethod
    def _table(parent: QWidget, name: str) -> QTableWidget:
        table = QTableWidget(0, 0, parent)
        table.setObjectName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(110)
        return table

    # --- the choices ------------------------------------------------------------------------------------------------

    def fill_cases(self) -> bool:
        """Lists the cases of the cases file (default: the installation's Ditto and retrieval cases), none ticked."""
        try:
            cases = eval_run.all_cases(self.cases_file)
        except eval_run.EvalRunError as e:
            self._tell("warning", self._t("ditto_eval.cases_file.failed", reason=one_line(e)))
            return False
        self.lst_cases.clear()
        for case in cases:
            item = QListWidgetItem(one_line(self._t("ditto_eval.case_item", id=case.id, kind=case.kind,
                                                    description=case.description)))
            item.setData(Qt.UserRole, case.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.lst_cases.addItem(item)
        return True

    def fill_tiers(self) -> None:
        """The managed local model tiers with file, size and whether they are downloaded, none ticked."""
        self.lst_tiers.clear()
        try:
            rt = self._factory()
            models = [(m, rt.model_state(m)) for m in rt.catalog.models]
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: the provider fields still work
            self.lst_tiers.addItem(QListWidgetItem(self._t("ditto_eval.tiers_failed", reason=one_line(e))))
            return
        self.lst_tiers.blockSignals(True)
        for model, state in models:
            item = QListWidgetItem(one_line(self._t("ditto_eval.tier_item", tier=model.tier, file=model.file,
                                                    size=gib(model.size),
                                                    state=self._t(f"ditto_models.state.{state}"))))
            item.setData(Qt.UserRole, model.tier)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.lst_tiers.addItem(item)
        self.lst_tiers.blockSignals(False)

    def choose_cases_file(self) -> bool:
        chosen_file = _open_file(self, self._t("ditto_eval.cases_file.dialog"), self._t("ditto_eval.cases_file.filter"))
        if not chosen_file:
            return False
        return self.set_cases_file(Path(chosen_file))

    def set_cases_file(self, path: Optional[Path]) -> bool:
        before = self.cases_file
        self.cases_file = path
        if not self.fill_cases():
            self.cases_file = before
            self.fill_cases()
            return False
        self.txt_cases_file.setText(str(path) if path else self._t("ditto_eval.cases_file.default"))
        return True

    @staticmethod
    def _checked(widget: QListWidget) -> List[str]:
        return [widget.item(i).data(Qt.UserRole) for i in range(widget.count())
                if widget.item(i).data(Qt.UserRole) and widget.item(i).checkState() == Qt.Checked]

    def tiers(self) -> List[str]:
        return self._checked(self.lst_tiers)

    def _tiers_changed(self) -> None:
        """A tier runs the managed local model: provider and model go back to the AI settings and are off."""
        has_tiers = bool(self.tiers())
        if has_tiers:
            self.cb_provider.setCurrentIndex(0)
            self.cb_model.setEditText("")
        self.cb_provider.setEnabled(not has_tiers)
        self.cb_model.setEnabled(not has_tiers)

    def choice(self) -> Dict[str, Any]:
        """The run's options as `ditto eval` takes them."""
        provider, model = chosen(self.cb_provider, self.cb_model)
        return {"cases_file": self.cases_file, "kinds": tuple(k for k, c in self.chk_kinds.items() if c.isChecked()),
                "rag_variants": tuple(v for v, c in self.chk_variants.items() if c.isChecked()),
                "case_ids": tuple(self._checked(self.lst_cases)), "repeat": self.sp_repeat.value(),
                "tiers": tuple(self.tiers()), "provider": provider, "model": model,
                "effort": self.cb_effort.currentData() or None}

    # --- the question before a run ----------------------------------------------------------------------------------

    def question(self, choice: Dict[str, Any], cases: Sequence[Any]) -> str:
        """What will be called, and whether it costs money (asked with "No" as the default)."""
        ai = any(c.kind != "retrieval" for c in cases)
        retrieval = any(c.kind == "retrieval" for c in cases)
        default = self._t("ditto_eval.target.default")
        parts, cost = [], self._t("ditto_eval.cost.none")
        if ai:
            if choice["tiers"]:
                parts.append(self._t("ditto_eval.target.tiers", tiers=", ".join(one_line(t) for t in choice["tiers"])))
                cost = self._t("ditto_eval.cost.local")
            else:
                if choice["provider"] or choice["model"]:
                    parts.append(self._t("ditto_eval.target.provider", provider=one_line(choice["provider"] or default),
                                         model=one_line(choice["model"] or default)))
                else:
                    parts.append(self._t("ditto_eval.target.settings"))
                provider = choice["provider"] or self._configured_provider()
                local = is_local(self.catalog, provider)
                cost = self._t({True: "ditto_eval.cost.local", False: "ditto_eval.cost.cloud"}.get(
                    local, "ditto_eval.cost.unknown"))
            if cost == self._t("ditto_eval.cost.local"):
                elsewhere = self._fallback_elsewhere(choice)
                if elsewhere:   # the fallback chain applies to the run too: not free when it moves on
                    cost = self._t("ditto_eval.cost.local_fallback", providers=", ".join(elsewhere))
        if retrieval:
            parts.append(self._t("ditto_eval.target.retrieval"))
        target = parts[0] if len(parts) == 1 else self._t("ditto_eval.target.and", first=parts[0], second=parts[1])
        return self._t("ditto_eval.question", cases=len(cases), repeat=choice["repeat"], target=target, cost=cost)

    def _missing_tiers(self, tiers: Sequence[str]) -> List[str]:
        """The chosen tiers whose model file is not there (LocalLLMRuntime.model_state "missing", as in Ditto > Local
        Models...); a tier that cannot be looked up is left to the run, which reports it."""
        if not tiers:
            return []
        try:
            rt = self._factory()
            states = {m.tier: rt.model_state(m) for m in rt.catalog.models}
        except Exception:  # noqa: BLE001 - the run reports a runtime that cannot be read
            return []
        return [one_line(t) for t in tiers if states.get(t) == "missing"]

    def _fallback_elsewhere(self, choice: Dict[str, Any]) -> List[str]:
        """The providers of the AI settings' fallback chain (the run's Ditto reads it like every call: LLMClient.
        fallback_targets) that are not known to run on this computer; [] when there are none or it cannot be read."""
        from orchestrator.Core.ai_override import ditto_with, override_values
        if self.ditto is None:
            return []
        try:
            values = override_values("local" if choice["tiers"] else choice["provider"], choice["model"],
                                     choice["effort"], ditto=self.ditto)
            chain = ditto_with(self.ditto, values).client.fallback_targets("chat")
        except Exception:  # noqa: BLE001 - no chain to name; the run reports a broken setting itself
            return []
        resolve = getattr(self.catalog, "resolve_id", None)
        names: List[str] = []
        for provider, _model in chain:
            try:
                pid = (resolve(provider) if resolve else None) or provider
            except Exception:  # noqa: BLE001
                pid = provider
            if is_local(self.catalog, pid) is not True and one_line(pid) not in names:
                names.append(one_line(pid))
        return names

    def _configured_provider(self) -> Optional[str]:
        try:
            return self.framework.config.get("ai_provider") or None
        except Exception:  # noqa: BLE001
            return None

    # --- a run ------------------------------------------------------------------------------------------------------

    def start(self) -> bool:
        """Checks the choice like the CLI, asks (No by default), then runs the evaluation in a worker thread. True
        when it was started."""
        from orchestrator.Core.ai_override import override_values, provider_problem
        if self._job is not None:
            self._tell("information", self._t("ditto_eval.busy"))
            return False
        choice = self.choice()
        try:
            eval_run.check_choice(choice["tiers"], choice["provider"], choice["model"])
        except eval_run.EvalRunError:
            self._tell("warning", self._t("ditto_eval.tier_conflict"))
            return False
        try:
            cases = eval_run.select_cases(choice["cases_file"], choice["kinds"], choice["case_ids"])
        except eval_run.EvalRunError as e:
            self._tell("warning", self._t("ditto_eval.failed", reason=one_line(e)))
            return False
        ai = any(c.kind != "retrieval" for c in cases)
        if ai and self.ditto is None:
            self._tell("warning", self._t("ditto_eval.no_ditto"))
            return False
        local_runs = ai and bool(choice["tiers"])   # a tier's server is started: a task of Ditto's local runtime
        if local_runs and job_running():
            self._tell("information", self._t("ditto_runtime.busy"))
            return False
        missing = self._missing_tiers(choice["tiers"]) if ai else []
        if missing:   # starting one would stop your server first and then name a CLI command
            self._tell("warning", self._t("ditto_eval.tiers_missing", tiers=", ".join(missing)))
            return False
        problem = provider_problem(self.catalog, choice["provider"]) if (ai and choice["provider"]
                                                                         and self.catalog is not None) else None
        if problem:
            self._tell("warning", self._t("ditto_eval.failed", reason=one_line(problem)))
            return False
        if not ask(self, self._t("ditto_eval.title"), self.question(choice, cases)):
            return False
        guard = object() if local_runs else None
        if guard is not None and not job_begins(guard):   # another runtime task began while the question was open
            self._tell("information", self._t("ditto_runtime.busy"))
            return False

        from orchestrator.Core.ai_override import ditto_with
        base, framework, factory = self.ditto, self.framework, self._factory
        values = override_values("local" if choice["tiers"] else choice["provider"], choice["model"], choice["effort"],
                                 ditto=base)

        def make_ditto():
            return ditto_with(base, values)   # this run only; the saved settings stay unchanged

        self._stop = threading.Event()
        stop = self._stop
        token = object()
        self._events = token
        events = _RelayEvents(token)
        ai_runs = (len(choice["tiers"]) or 1) if ai else 0
        variants = choice["rag_variants"] or ("fused",)
        total = ai_runs * sum(c.kind != "retrieval" for c in cases) + len(variants) * sum(
            c.kind == "retrieval" for c in cases)
        self.kinds_shown = sorted({c.kind for c in cases})

        def work():
            return eval_run.run_evaluation(framework, cases, make_ditto=make_ditto, make_runtime=factory,
                                           tiers=choice["tiers"], provider=choice["provider"], model=choice["model"],
                                           effort=choice["effort"], cases_file=choice["cases_file"],
                                           rag_variants=choice["rag_variants"], repeat=choice["repeat"],
                                           events=events, stop=stop)

        self.log.clear()
        self._done_cases = 0
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self._set_busy(True, self._t("ditto_eval.working"))
        job, relay = object(), worker_relay()
        self._job = job

        def run() -> None:
            try:
                result: Any = (True, work())
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            finally:
                if guard is not None:
                    job_ends(guard)   # by the worker thread, also when this window is gone
            relay.done.emit(job, result)

        threading.Thread(target=run, name="ditto-eval", daemon=True).start()
        return True

    def cancel(self) -> None:
        if self._job is not None:
            self._stop.set()
            self.lbl_busy.setText(self._t("ditto_eval.cancelling"))

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.btn_run.setEnabled(not busy)
        self.btn_save.setEnabled(not busy and bool(self.runs))
        self.btn_cancel.setVisible(busy)
        self.progress.setVisible(busy)
        self.lbl_busy.setText(text)

    @property
    def busy(self) -> bool:
        return self._job is not None

    # --- what comes back --------------------------------------------------------------------------------------------

    def _on_relay(self, token: object, payload: Any) -> None:
        if token is self._events and self._job is not None:
            self._event(payload)
        elif token is self._job:
            self._job = self._events = None
            self._finished(*payload)

    def _event(self, payload: Sequence[Any]) -> None:
        kind = payload[0]
        if kind == "busy":
            _, step, values = payload
            self.lbl_busy.setText(self._t(f"ditto_eval.step.{step}", **{k: one_line(v) for k, v in values.items()}))
        elif kind == "run":
            _, label, name, model, count, repeat = payload
            what = f"{one_line(name)} / {one_line(model)}" if name is not None else one_line(model)
            self.log.appendPlainText(self._t("ditto_eval.log.run", label=one_line(label), what=what, cases=count,
                                             repeat=repeat))
            self.lbl_busy.setText(self._t("ditto_eval.working"))
        elif kind == "result":
            _, case_id, rate, seconds, problem = payload
            key = "pass" if rate == 1 else ("fail" if rate == 0 else "partial")
            self.log.appendPlainText(self._t(f"ditto_eval.log.{key}", id=one_line(case_id), seconds=f"{seconds:.1f}",
                                             rate=f"{rate:.0%}", problem=one_line(problem[:160])))
            self._done_cases += 1
            self.progress.setValue(min(self._done_cases, self.progress.maximum()))
        elif kind == "replaced":
            self.log.appendPlainText(self._t("ditto_eval.log.replaced", model=one_line(payload[1])))
        elif kind == "unused":
            self.log.appendPlainText(self._t("ditto_eval.log.unused"))

    def _finished(self, ok: bool, value: Any) -> None:
        from orchestrator.evals.ditto_eval import EvalCancelled
        details = ""
        if ok:
            self.runs = list(value)
            text = self._t("ditto_eval.done", runs=len(self.runs))
        elif isinstance(value, EvalCancelled):
            self.runs = list(value.runs)
            text = self._t("ditto_eval.cancelled")
        else:
            self.runs = []
            details = str(getattr(value, "log_tail", "") or "")
            text = self._t("ditto_eval.failed", reason=one_line(value))
        self.show_results(self.runs)
        self._set_busy(False, text)
        self.log.appendPlainText(text)
        if not ok and not isinstance(value, EvalCancelled):
            self._tell("warning", text, details=details)

    def show_results(self, runs: Sequence[Any]) -> None:
        """The CLI's tables: one row per run, and the retrieval figures when there are any."""
        columns, rows = eval_run.summary_table(runs, self.kinds_shown)
        header = [self._t("ditto_eval.col.run"), self._t("ditto_eval.col.model"), self._t("ditto_eval.col.passed"),
                  *columns[3:-1], self._t("ditto_eval.col.avg")]
        self._fill(self.tbl_results, header, rows)
        retrieval = eval_run.retrieval_table(runs)
        self.lbl_retrieval.setVisible(retrieval is not None)
        self.tbl_retrieval.setVisible(retrieval is not None)
        if retrieval is not None:
            columns, rows = retrieval
            header = [self._t("ditto_eval.col.run"), *columns[1:5], self._t("ditto_eval.col.unrelated"),
                      self._t("ditto_eval.col.p50")]
            self._fill(self.tbl_retrieval, header, rows)

    @staticmethod
    def _fill(table: QTableWidget, header: List[str], rows: List[List[str]]) -> None:
        table.setColumnCount(len(header))
        table.setHorizontalHeaderLabels(header)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(one_line(value)))

    # --- the report (`--json FILE`) ---------------------------------------------------------------------------------

    def save_report(self) -> Optional[Path]:
        """Writes the CLI's JSON report of the last run to a file the person chooses (the save dialog)."""
        if not self.runs:
            self._tell("information", self._t("ditto_eval.no_runs"))
            return None
        chosen_path = _save_file(self, self._t("ditto_eval.save_dialog"), "ditto-eval.json",
                                 self._t("ditto_eval.save_filter"))
        if not chosen_path:
            return None
        path = Path(chosen_path)
        if not path.suffix:
            path = path.with_suffix(".json")
        try:
            eval_run.write_report(path, self.runs)
        except OSError as e:
            self._tell("warning", self._t("ditto_eval.save_failed", reason=one_line(e)))
            return None
        self.lbl_busy.setText(self._t("ditto_eval.saved", path=str(path)))
        self.lbl_busy.setToolTip(plain_tooltip(str(path)))
        return path


__all__ = ["EvaluationWindow"]
