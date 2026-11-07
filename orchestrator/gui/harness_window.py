#!/usr/bin/env python3
"""
Boards > Board harness... (act_boards_harness): the person's own agent harness on a board, by mouse; the same steps as
`llmcf bundle harness` (orchestrator/runtime/bundle/harness_run.py), with the same questions and audit rows.

    Take in a harness...               bundle harness take-in ID LOCATION --kind --installer ...
    Bundle and harness                 BUNDLE and --component
    Dry run...                         bundle harness dry-run (its question first, "No" the default)
    Run... (+ the two check boxes)     bundle harness run [--assume-yes] [--skip-dry-run]
    Runs, Re-attach                    bundle harness attach BUNDLE RUN
    Answer field, Hide, Send           bundle harness answer BUNDLE RUN [--hidden]
    Stop...                            bundle harness stop BUNDLE RUN
    Report                             bundle harness report BUNDLE RUN

The run is one long step in a worker thread (BoardSteps of gui/boards_window.py, the one step runner): its output
reaches the output pane live, as plain text (harness_run.plain_output: nothing the installer prints steers the
window), while the GUI thread stays free; Send and Stop go over a board session of their own, beside the step. Detach
(the step's Cancel) closes the step's session: the installer goes on on the board, and Re-attach reads on from the
byte this computer read last. An AI never starts, answers or stops a run here (no tool reaches this window's steps).
"""

import threading
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from orchestrator.gui.boards_window import BoardSteps, _exec, _form_dialog
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

# object name -> (text key, slot, what it needs: "" nothing, "bundle" a harness bundle, "run" a chosen run)
BUTTONS = (
    ("btn_harness_take_in", "harness.btn.take_in", "_on_take_in", ""),
    ("btn_harness_refresh", "harness.btn.refresh", "reload", ""),
    ("btn_harness_dry_run", "harness.btn.dry_run", "_on_dry_run", "bundle"),
    ("btn_harness_run", "harness.btn.run", "_on_run", "bundle"),
    ("btn_harness_attach", "harness.btn.attach", "_on_attach", "run"),
    ("btn_harness_stop", "harness.btn.stop", "_on_stop", "run"),
    ("btn_harness_report", "harness.btn.report", "_on_report", "run"),
)


class OutputChunk(str):
    """A piece of the installer's output on its way to the output pane (appended as it is, no line of its own)."""


class HarnessWindow(BoardSteps, QDialog):
    """The person's harness runs; the run's step off the GUI thread, Send and Stop beside it."""

    TITLE_KEY = "harness.title"

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_harness")
        self.framework = framework
        self.lang = lang or current_language()
        self.setWindowTitle(self._t("harness.title"))
        self.resize(1100, 820)
        self.entries: List[Tuple[str, str]] = []     # (bundle id, component) of the stored bundles' harnesses
        self.following: Optional[Any] = None          # the RunState the running step follows
        self._beside_token = object()                  # the lines of an answer or a stop beside the step
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("harness.intro")))
        self.buttons: Dict[str, QPushButton] = {}
        for name, key, slot, _need in BUTTONS:
            button = QPushButton(button_text(self._t(key)), self)
            button.setObjectName(name)
            button.setAutoDefault(False)
            button.clicked.connect(getattr(self, slot))
            self.buttons[name] = button
        top = QHBoxLayout()
        top.addWidget(plain_label(self._t("harness.bundle")))
        self.cmb_bundle = QComboBox(self)
        self.cmb_bundle.setObjectName("cmb_harness_bundle")
        top.addWidget(self.cmb_bundle, stretch=1)
        top.addWidget(self.buttons["btn_harness_take_in"])
        top.addWidget(self.buttons["btn_harness_refresh"])
        layout.addLayout(top)
        self.lbl_none = plain_label("")
        self.lbl_none.setObjectName("lbl_harness_none")
        layout.addWidget(self.lbl_none)
        options = QHBoxLayout()
        self.chk_assume_yes = QCheckBox(self._t("harness.assume_yes"), self)
        self.chk_assume_yes.setObjectName("chk_harness_assume_yes")
        self.chk_skip_dry = QCheckBox(self._t("harness.skip_dry"), self)
        self.chk_skip_dry.setObjectName("chk_harness_skip_dry")
        options.addWidget(self.chk_assume_yes)
        options.addWidget(self.chk_skip_dry)
        options.addStretch(1)
        layout.addLayout(options)
        row = QHBoxLayout()
        for name in ("btn_harness_dry_run", "btn_harness_run"):
            row.addWidget(self.buttons[name])
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(plain_label(self._t("harness.runs")))
        self.lst_runs = QListWidget(self)
        self.lst_runs.setObjectName("lst_harness_runs")
        self.lst_runs.setMaximumHeight(120)
        layout.addWidget(self.lst_runs)
        runs_row = QHBoxLayout()
        for name in ("btn_harness_attach", "btn_harness_stop", "btn_harness_report"):
            runs_row.addWidget(self.buttons[name])
        runs_row.addStretch(1)
        layout.addLayout(runs_row)
        busy = QHBoxLayout()
        self.lbl_busy = plain_label("")
        self.lbl_busy.setObjectName("lbl_harness_busy")
        self.btn_cancel = QPushButton(button_text(self._t("harness.btn.cancel")), self)
        self.btn_cancel.setObjectName("btn_harness_cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        busy.addWidget(self.lbl_busy, stretch=1)
        busy.addWidget(self.btn_cancel)
        layout.addLayout(busy)
        layout.addWidget(plain_label(self._t("harness.output")))
        self.output = QPlainTextEdit(self)   # the installer's output and the window's lines: plain text only
        self.output.setObjectName("txt_harness_output")
        self.output.setReadOnly(True)
        layout.addWidget(self.output, stretch=3)
        answer = QHBoxLayout()
        self.edt_answer = QLineEdit(self)
        self.edt_answer.setObjectName("edt_harness_answer")
        self.edt_answer.setPlaceholderText(self._t("harness.answer_ph"))
        self.edt_answer.returnPressed.connect(self._on_send)
        self.chk_hidden = QCheckBox(self._t("harness.hidden"), self)
        self.chk_hidden.setObjectName("chk_harness_hidden")
        self.chk_hidden.toggled.connect(lambda on: self.edt_answer.setEchoMode(QLineEdit.Password if on
                                                                               else QLineEdit.Normal))
        self.btn_send = QPushButton(button_text(self._t("harness.btn.send")), self)
        self.btn_send.setObjectName("btn_harness_send")
        self.btn_send.setAutoDefault(False)
        self.btn_send.clicked.connect(self._on_send)
        answer.addWidget(self.edt_answer, stretch=1)
        answer.addWidget(self.chk_hidden)
        answer.addWidget(self.btn_send)
        layout.addLayout(answer)
        layout.addWidget(plain_label(self._t("harness.report_label")))
        self.txt_report = QPlainTextEdit(self)
        self.txt_report.setObjectName("txt_harness_report")
        self.txt_report.setReadOnly(True)
        layout.addWidget(self.txt_report, stretch=2)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._init_steps()
        self.cmb_bundle.currentIndexChanged.connect(self._fill_runs)
        self.lst_runs.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    # --- what the window reads (files on this computer only) ---------------------------------------------------------

    def reload(self) -> None:
        from orchestrator.runtime.bundle import generator as gen
        try:
            bundles = gen.BundleStore(self.state_dir).list()
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: plain text
            bundles = []
            self._say(self._t("harness.failed", reason=one_line(str(e))))
        chosen = self.cmb_bundle.currentData()
        self.entries = [(b.bundle_id, c.id) for b in bundles for c in b.manifest.components if c.harness is not None]
        self.cmb_bundle.blockSignals(True)
        self.cmb_bundle.clear()
        for bundle_id, comp in self.entries:
            built = next(b for b in bundles if b.bundle_id == bundle_id)
            part = next(c for c in built.manifest.components if c.id == comp).harness
            self.cmb_bundle.addItem(f"{bundle_id} - {comp} ({part.id}, board {built.manifest.board.device_id})",
                                    (bundle_id, comp))
        if chosen is not None and self.cmb_bundle.findData(chosen) >= 0:
            self.cmb_bundle.setCurrentIndex(self.cmb_bundle.findData(chosen))
        self.cmb_bundle.blockSignals(False)
        self.lbl_none.setText("" if self.entries else self._t("harness.none"))
        self._fill_runs()

    def _fill_runs(self, *_args) -> None:
        from orchestrator.runtime.bundle import harness_run as hr
        chosen = self.selected_run()
        self.lst_runs.clear()
        entry = self.cmb_bundle.currentData()
        if entry is not None:
            try:
                runs = [s for s in hr.list_runs(self.state_dir) if (s.bundle_id, s.component) == tuple(entry)]
            except Exception:  # noqa: BLE001 - no per-user folder: no runs
                runs = []
            for s in runs:
                item = QListWidgetItem(one_line(f"{s.run}  {s.mode}  {s.status}  {s.outcome or '-'}  {s.started}"))
                item.setData(Qt.UserRole, s.run)
                self.lst_runs.addItem(item)
                if s.run == chosen:
                    item.setSelected(True)
        self._update_buttons()

    def selected_run(self) -> Optional[str]:
        items = self.lst_runs.selectedItems() if hasattr(self, "lst_runs") else []
        return items[0].data(Qt.UserRole) if items else None

    def _update_buttons(self) -> None:
        busy = self.running
        has_bundle = self.cmb_bundle.currentData() is not None
        has_run = self.selected_run() is not None
        for name, _key, _slot, need in BUTTONS:
            ok = {"": True, "bundle": has_bundle, "run": has_bundle and has_run}[need]
            if name in ("btn_harness_stop",):
                self.buttons[name].setEnabled(ok)        # a stop also while a run is followed
            else:
                self.buttons[name].setEnabled(ok and not busy)
        # Send stays enabled: a run that is followed may ask at any moment (_current says when there is none)

    # --- the chosen harness --------------------------------------------------------------------------------------------

    def _chosen(self):
        """(built, component, opener) of the chosen bundle's harness; None after telling the person why not."""
        from orchestrator.runtime.bundle import harness_run as hr
        from orchestrator.runtime.bundle.generator import BundleError, BundleStore
        from orchestrator.gui.bundle_window import BundleWindow
        entry = self.cmb_bundle.currentData()
        if entry is None:
            self._info(self._t("harness.need_bundle"))
            return None
        bundle_id, comp_id = entry
        try:
            built = BundleStore(self.state_dir).get(bundle_id)
            comp = hr.harness_component(built, comp_id)
        except (BundleError, hr.HarnessRunError) as e:
            self._info(self._t("harness.failed", reason=one_line(str(e))), "warning")
            return None
        opener = BundleWindow._opener(self, built)   # the person's login with the framework's key only
        if opener is None:
            return None
        return built, comp, opener

    def _state(self, built: Any, comp: Any, run: str):
        from orchestrator.runtime.bundle import harness_run as hr
        state = hr.load_state(self.state_dir, comp.harness.id, built.bundle_id, comp.id, run)
        if state is None:
            state = hr.RunState(bundle_id=built.bundle_id, component=comp.id, run=run,
                                mode="dry" if run.startswith("dry-") else "run", harness=comp.harness.id,
                                tree_sha256=comp.harness.tree_sha256, device_id=built.manifest.board.device_id,
                                status="running")
            hr.save_state(self.state_dir, state)
        return state

    def _failed_text(self, error: BaseException) -> Optional[str]:
        from orchestrator.runtime.bundle import harness_run as hr
        if isinstance(error, hr.HarnessRunError):
            return str(error) + (f"\n\n{error.next_step}" if error.next_step else "")
        return None

    def _harness_failed(self, opener):
        def failed(error: BaseException) -> bool:
            self.following = None
            self._fill_runs()
            text = self._failed_text(error)
            if text is not None:
                self._say(text)
                self._info(text, "warning")
                return True
            _open, host, port, user, _label = opener
            self._show_error(error, address=host, port=port, user=user)
            return True
        return failed

    # --- the run as one step: start (or attach), follow, report ------------------------------------------------------

    def _finished(self, token: object, result: Any) -> None:
        if token is not None and token is self._beside_token and isinstance(result, str):
            self._say(result)
            return
        if token is not None and token is self._progress_token and isinstance(result, OutputChunk):
            self.output.moveCursor(QTextCursor.End)
            self.output.insertPlainText(str(result))
            self.output.moveCursor(QTextCursor.End)
            return
        super()._finished(token, result)

    def _say(self, text: str) -> None:
        """A line of the window between the installer's output: on a line of its own."""
        self.output.moveCursor(QTextCursor.End)
        current = self.output.toPlainText()
        if current and not current.endswith("\n"):
            self.output.insertPlainText("\n")
        self.output.insertPlainText((one_line(text) if "\n" not in text else text) + "\n")
        self.output.moveCursor(QTextCursor.End)

    def _chunk(self, data: bytes) -> None:
        from orchestrator.runtime.bundle import harness_run as hr
        from orchestrator.gui.worker_relay import worker_relay
        token = self._progress_token
        text = hr.plain_output(data)
        if token is not None and text:
            worker_relay().done.emit(token, OutputChunk(text))

    def _follow_step(self, built: Any, comp: Any, opener: Any, *, mode: str = "", run: str = "",
                     then=None) -> None:
        """Starts the run (`mode`) or attaches to `run`, follows it to its end in the worker thread and reads its
        report; `then(report)` afterwards in the GUI thread."""
        from orchestrator.runtime.bundle import harness_run as hr
        open_session = opener[0]
        state_dir = self.state_dir
        audit = self.audit
        holder: Dict[str, Any] = {}

        def work():
            if run:
                state = self._state(built, comp, run)
            else:
                state = hr.on_board(open_session, lambda shell: hr.start_run(
                    shell, built, comp.id, mode, state_dir=state_dir, device_id=built.manifest.board.device_id,
                    audit=audit, actor="gui"))
                self._progress(self._t("harness.started", run=state.run))
            holder["state"] = state
            self.following = state
            if state.status != "ended":
                try:
                    hr.follow_on_board(open_session, built, state, state_dir=state_dir, on_output=self._chunk,
                                       cancelled=self._cancel.is_set, say=self._progress)
                except Exception:  # noqa: BLE001 - Detach closed the session: no failure, the run goes on
                    if not self._cancel.is_set():
                        raise
            if state.status != "ended":   # Detach: the installer goes on on the board
                return state, None
            self._progress(self._t("harness.ended", code=state.exit_code))
            return state, hr.on_board(open_session, lambda shell: hr.collect_report(
                shell, built, state, state_dir=state_dir, audit=audit, actor="gui"))

        def done(result: Any) -> None:
            state, rep = result
            self.following = None
            if state.status != "ended":
                self._say(self._t("harness.detached"))
                self._fill_runs()
                return
            self.txt_report.setPlainText("\n".join(hr.report_lines(state, rep, self.lang)))
            self._fill_runs()
            if then is not None:
                then(rep)

        def failed(error: BaseException) -> bool:
            if self._cancel.is_set():
                self.following = None
                self._say(self._t("harness.detached"))
                self._fill_runs()
                return True
            return self._harness_failed(opener)(error)

        what = self._t("harness.running", mode=mode or ("dry run" if run.startswith("dry-") else "run"))
        self._start(what, work, done, failed=failed)

    def _on_dry_run(self, then=None) -> None:
        chosen = self._chosen()
        if chosen is None:
            return
        built, comp, opener = chosen
        part = comp.harness
        if not part.dry_run_args:
            self._say(self._t("harness.no_dry", harness=part.id, installer=part.installer, tree=part.tree_sha256))
            if then is not None:
                then(None)
            return
        if not ask(self, self._t("harness.title"), self._t("harness.ask.dry", harness=part.id, board=opener[4],
                                                           args=" ".join(part.dry_run_args))):
            return
        self._follow_step(built, comp, opener, mode="dry", then=then)

    def _on_run(self) -> None:
        def go(_report: Any = None) -> None:
            chosen = self._chosen()
            if chosen is None:
                return
            built, comp, opener = chosen
            if not ask(self, self._t("harness.title"), self._t("harness.ask.run", harness=comp.harness.id,
                                                               board=opener[4])):
                return
            self._follow_step(built, comp, opener, mode="run-yes" if self.chk_assume_yes.isChecked() else "run")
        if self.chk_skip_dry.isChecked():
            go()
        else:
            self._on_dry_run(then=go)

    def _on_attach(self) -> None:
        run = self.selected_run()
        chosen = self._chosen()
        if chosen is None or run is None:
            if run is None:
                self._info(self._t("harness.need_run"))
            return
        built, comp, opener = chosen
        self._follow_step(built, comp, opener, run=run)

    def _on_report(self) -> None:
        from orchestrator.runtime.bundle import harness_run as hr
        run = self.selected_run()
        chosen = self._chosen()
        if chosen is None or run is None:
            return
        built, comp, opener = chosen
        state = self._state(built, comp, run)
        rep = hr.stored_report(self.state_dir, state)
        if rep is not None or state.status != "ended":
            self.txt_report.setPlainText("\n".join(hr.report_lines(state, rep, self.lang)))
            return
        state_dir, audit = self.state_dir, self.audit
        self._start(self._t("harness.reading"), lambda: hr.on_board(opener[0], lambda shell: hr.collect_report(
            shell, built, state, state_dir=state_dir, audit=audit, actor="gui")),
            lambda r: (self.txt_report.setPlainText("\n".join(hr.report_lines(state, r, self.lang))),
                       self._fill_runs()),
            failed=self._harness_failed(opener))

    # --- beside the step: an answer and a stop over a session of their own ------------------------------------------

    def _beside(self, work, ok_text: str, failed_key: str) -> None:
        """`work()` in a thread of its own (the running step keeps following); its result as a line of the pane,
        through the relay with the window's own token for these lines."""
        from orchestrator.gui.worker_relay import worker_relay
        token = self._beside_token

        def tell(text: str) -> None:
            worker_relay().done.emit(token, text)

        def run() -> None:
            try:
                work()
                tell(ok_text)
            except Exception as e:  # noqa: BLE001 - the person's words, the run goes on
                tell(self._t(failed_key, reason=one_line(self._failed_text(e) or str(e))))
        threading.Thread(target=run, name="harness-beside", daemon=True).start()

    def _current(self):
        """(built, comp, opener, state) of the run that is followed, else of the chosen run."""
        chosen = self._chosen()
        if chosen is None:
            return None
        built, comp, opener = chosen
        if self.following is not None and (self.following.bundle_id, self.following.component) == \
                (built.bundle_id, comp.id):
            return built, comp, opener, self.following
        run = self.selected_run()
        if run is None:
            self._info(self._t("harness.need_run"))
            return None
        return built, comp, opener, self._state(built, comp, run)

    def _on_send(self) -> None:
        from orchestrator.runtime.bundle import harness_run as hr
        line = self.edt_answer.text()
        current = self._current()
        if current is None:
            return
        built, _comp, opener, state = current
        self.edt_answer.clear()   # never kept in the window
        self._beside(lambda: hr.on_board(opener[0], lambda shell: hr.answer_run(shell, built, state, line)),
                     self._t("harness.answered"), "harness.answer_failed")

    def _on_stop(self) -> None:
        from orchestrator.runtime.bundle import harness_run as hr
        current = self._current()
        if current is None:
            return
        built, comp, opener, state = current
        if not ask(self, self._t("harness.title"), self._t("harness.ask.stop", harness=comp.harness.id,
                                                           board=opener[4])):
            return
        state_dir, audit = self.state_dir, self.audit
        self._beside(lambda: hr.on_board(opener[0], lambda shell: hr.stop_run(
            shell, built, state, state_dir=state_dir, audit=audit, actor="gui")),
            self._t("harness.stopped"), "harness.failed")

    # --- taking a harness in ---------------------------------------------------------------------------------------------

    def _on_take_in(self) -> None:
        from orchestrator.runtime.bundle import harness as hz
        dialog, form = _form_dialog(self, self._t("harness.take.title"), "")
        fields: Dict[str, QLineEdit] = {}
        for key in ("id", "title_field", "location", "installer", "ref", "pin", "dry_args", "assume_yes",
                    "exit_codes"):
            edit = QLineEdit(dialog)
            edit.setObjectName(f"edt_harness_take_{key}")
            fields[key] = edit
            if key == "location":
                kind = QComboBox(dialog)
                kind.setObjectName("cmb_harness_take_kind")
                for k in hz.KINDS:
                    kind.addItem(k, k)
                form.addRow(self._t("harness.take.kind"), kind)
            form.addRow(self._t(f"harness.take.{key}"), edit)
        fields["exit_codes"].setText("0=done")
        if not _exec(dialog):
            return
        codes: Dict[int, str] = {}
        for item in fields["exit_codes"].text().split():
            code, _, meaning = item.partition("=")
            if not code.isdigit() or meaning not in hz.OUTCOMES:
                self._info(self._t("harness.take.bad_codes"), "warning")
                return
            codes[int(code)] = meaning
        hid = fields["id"].text().strip()
        spec = hz.HarnessSpec(id=hid, title=fields["title_field"].text().strip() or hid, kind=kind.currentData(),
                              location=fields["location"].text().strip(), installer=fields["installer"].text().strip(),
                              ref=fields["ref"].text().strip() or None, pin=fields["pin"].text().strip() or None,
                              dry_run_args=tuple(fields["dry_args"].text().split()),
                              assume_yes=fields["assume_yes"].text().strip() or None, exit_codes=codes or {0: "done"})
        state_dir = self.state_dir
        config = getattr(self.framework, "config", None)

        def work():
            from orchestrator.utils.locality import offline_mode
            return hz.take_in(spec, state_dir, offline=bool(offline_mode(config)))

        def done(taken: Any) -> None:
            text = self._t("harness.take.done", id=taken.spec.id, pin=taken.pin, files=taken.files,
                           tree=taken.tree_sha256)
            self._say(text)
            self._info(text)

        def failed(error: BaseException) -> bool:
            self._info(self._t("harness.failed", reason=one_line(str(error))), "warning")
            return True
        self._start(self._t("harness.take.title"), work, done, failed=failed)


def open_harness(window: Any) -> Optional[HarnessWindow]:
    """Boards > Board harness...: shows the window of `window` (the main window), with fresh data."""
    dialog = getattr(window, "harness_window", None)
    if dialog is None:
        dialog = HarnessWindow(window.framework, window)
        window.harness_window = dialog
    try:
        dialog.reload()
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text
        plain_box(window, "critical", ui_text("harness.title"), str(e))
        return None
    dialog.show()
    dialog.raise_()
    return dialog


__all__ = ["BUTTONS", "HarnessWindow", "OutputChunk", "open_harness"]
