#!/usr/bin/env python3
"""
Mojo in the GUI (Tools > Mojo Health Check...; W1' of 2026-10-16: every action of `llmcf mojo` also by mouse).

MojoWindow runs Mojo Oracle off the GUI thread (Mojo(framework).run() and offers()) and shows every finding as
`llmcf mojo` prints it: the severity as text with an icon and the CLI's mark (never colour alone), the area, the
message and, for each problem, where it is (the full path), why it matters, the next step, what Mojo Miracle can do
(views.miracle_can) with the steps of its plan; then the summary sentence (views.oracle_summary) and the overall line
(the exit code: 0 ok, 1 warnings, 2 errors).

[Run Mojo Miracle...] is enabled under the CLI's rule (views.miracle_acts). It asks first with the CLI's words ("Run
Mojo Miracle now?", No the default) and runs engine.miracle off the GUI thread with the person present: the plans
shown bind the repairs (a problem that changed meanwhile is refused as plan_changed), and the framework's build images
open as the person's list in the deletion dialog (docker_objects.offer_image_list with ask_deletion: the full list,
nothing ticked, No the default). Then it shows what was repaired, what was not and why, and the check afterwards.

Boards (W1): Mojo Oracle contacts no board. Each board a recorded fact points to (boards.boards_to_offer) gets a
[Check <board> now] button; [Check a board...] lets the person pick any trusted board (label, address). A check is
one connection to the board's SSH port, no login, off the GUI thread (boards.check_board), and shows the board's
finding with its next step. BoardCheck does the same for the Deployment tab's [Check this board].

The Mojo package loads with the first check, not with the main window (lazy loading, RG1). Every text is plain text;
nothing here reaches an AI.
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QProgressBar, QPushButton, QStyle, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from orchestrator.gui.deletion_dialog import ask_deletion
from orchestrator.gui.plain import ask, button_text, display_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

# The CLI's marks next to the severity's name and icon: the severity reads the same without colour.
SEVERITY_MARKS = {"ok": "✔", "info": "ℹ", "warn": "!", "error": "✘"}
SEVERITY_ICONS = {"ok": QStyle.SP_DialogApplyButton, "info": QStyle.SP_MessageBoxInformation,
                  "warn": QStyle.SP_MessageBoxWarning, "error": QStyle.SP_MessageBoxCritical}
# What became of a problem after Mojo Miracle (views.outcome_kind): the CLI's mark and the icon of its severity.
OUTCOME_MARKS = {"repaired": ("✔", "ok"), "already_fixed": ("✔", "ok"), "still_present": ("!", "warn"),
                 "declined": ("-", "warn"), "not_repaired": ("✘", "error")}
WAIT_STEP = 0.2   # how often a worker waiting for the person's answer looks whether the window was closed


def severity_text(severity: str, lang: Optional[str] = None) -> str:
    """'✘ Error', '! Warning', 'ℹ Information', '✔ OK': the mark and the name of a severity."""
    return f"{SEVERITY_MARKS.get(severity, '?')} {ui_text('mojo.sev.' + severity, lang)}"


def board_check_text(finding, label: str, problem: str = "", lang: Optional[str] = None) -> str:
    """What the check of one board found, as the CLI prints it: the severity and the board's finding, and its next
    step unless all is well; why the check could not run; or that the board is no longer trusted."""
    if problem:
        return ui_text("mojo.boards.failed", lang, label=one_line(label), reason=one_line(problem))
    if finding is None:
        return ui_text("mojo.boards.gone", lang, label=one_line(label))
    severity = finding.severity.value
    lines = [ui_text("mojo.boards.result", lang, severity=severity_text(severity, lang),
                     message=display_text(finding.message))]
    if severity != "ok" and finding.fix_hint:
        lines.append(ui_text("mojo.boards.result_hint", lang, hint=display_text(finding.fix_hint)))
    return "\n".join(lines)


def run_off_gui_thread(kind: str, work: Callable[[object, Any], Any], name: str) -> object:
    """Runs work(token, relay) in a plain thread and hands (kind, result, problem) to the GUI thread through the
    WorkerRelay with a new token, which it returns (the receiver picks its own results by it)."""
    token, relay = object(), worker_relay()

    def target() -> None:
        try:
            payload: Tuple[str, Any, str] = (kind, work(token, relay), "")
        except Exception as e:  # noqa: BLE001 - shown to the person; nothing is left half done by the GUI
            payload = (kind, None, str(e) or type(e).__name__)
        relay.done.emit(token, payload)

    threading.Thread(target=target, name=name, daemon=True).start()
    return token


class DeletionRequest:
    """Mojo Miracle's worker asks the person for the list of the framework's build images: the GUI thread shows the
    deletion dialog and answers here (the accepted plan, or None)."""

    def __init__(self, plan, root: str):
        self.plan = plan
        self.root = root
        self.accepted = None
        self.answered = threading.Event()


class YesRequest:
    """Mojo Miracle's worker asks the person's own yes to one repair of class person (its plan with what it costs):
    the GUI thread asks with "No" as the default and answers here."""

    def __init__(self, offer):
        self.offer = offer
        self.accepted = False
        self.answered = threading.Event()


class BoardCheck(QObject):
    """The check of one trusted board for a window, off the GUI thread (boards.check_board: one connection to its SSH
    port, no login). `on_done(device_id, label, text, finding)` runs in the GUI thread with the person's text."""

    def __init__(self, parent: QWidget, framework, on_done: Callable[[str, str, str, Any], None],
                 lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.on_done = on_done
        self.lang = lang
        self._tokens: Dict[object, Tuple[str, str]] = {}
        worker_relay().done.connect(self._relayed, Qt.QueuedConnection)

    def running(self, device_id: str) -> bool:
        return device_id in {d for d, _ in self._tokens.values()}

    def start(self, device_id: str, label: str) -> bool:
        """Starts the check of `device_id` (False while that board's check still runs)."""
        if self.running(device_id):
            return False
        framework = self.framework

        def work(_token, _relay):
            from orchestrator.Core.mojo.boards import check_board
            return check_board(framework, device_id)

        self._tokens[run_off_gui_thread("board", work, "mojo-board-check")] = (device_id, label)
        return True

    def _relayed(self, token: object, payload: Any) -> None:
        if token not in self._tokens:
            return
        device_id, label = self._tokens.pop(token)
        _kind, finding, problem = payload
        self.on_done(device_id, label, board_check_text(finding, label, problem, self.lang), finding)


class MojoWindow(QDialog):
    """Tools > Mojo Health Check...: Mojo Oracle's findings, Mojo Miracle on the person's yes, and the check of one
    board on the person's click."""

    def __init__(self, framework, parent: Optional[QWidget] = None, lang: Optional[str] = None, *,
                 start: bool = True):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.setObjectName("mojo_window")
        self.setWindowTitle(self._t("mojo.title"))
        self.resize(1100, 760)
        self.report = None
        self.offered: List[Any] = []
        self.boards: List[Any] = []
        self.images = None   # the outcome of the person's image list in the last Mojo Miracle
        self._token: Optional[object] = None   # the running Mojo Oracle or Mojo Miracle (one at a time)
        self._closed = threading.Event()
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("mojo.intro")))
        self.status = plain_label("")
        layout.addWidget(self.status)
        self.busy = QProgressBar(self)
        self.busy.setRange(0, 0)   # busy: Mojo Oracle cannot say how far it is
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)
        layout.addWidget(self.busy)
        self.overall = plain_label("")
        bold = QFont(self.overall.font())
        bold.setBold(True)
        self.overall.setFont(bold)
        layout.addWidget(self.overall)
        self.tree = self._tree(("mojo.col.severity", "mojo.col.area", "mojo.col.finding"))
        self.tree.setObjectName("tree_mojo_findings")
        layout.addWidget(self.tree, stretch=3)
        self.summary = plain_label("")
        layout.addWidget(self.summary)
        layout.addWidget(self._boards_group())
        self.results_heading = plain_label(self._t("mojo.miracle.heading"))
        self.results_heading.setFont(bold)
        self.results = self._tree(("mojo.col.outcome", "mojo.col.problem", "mojo.col.what"))
        self.results.setObjectName("tree_mojo_miracle")
        self.results_summary = plain_label("")
        for widget in (self.results_heading, self.results, self.results_summary):
            widget.setVisible(False)
        layout.addWidget(self.results_heading)
        layout.addWidget(self.results, stretch=2)
        layout.addWidget(self.results_summary)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_check = QPushButton(button_text(self._t("mojo.check_again")), self)
        self.btn_check.setObjectName("btn_mojo_check")
        self.btn_miracle = QPushButton(button_text(self._t("mojo.miracle.button")), self)
        self.btn_miracle.setObjectName("btn_mojo_miracle")
        for button, slot in ((self.btn_check, self.check), (self.btn_miracle, self.run_miracle)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, s=slot: s())
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.board_check = BoardCheck(self, framework, self._board_checked, self.lang)
        worker_relay().done.connect(self._relayed, Qt.QueuedConnection)
        self._update_buttons()
        if start:
            self.check()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _tree(self, columns) -> QTreeWidget:
        tree = QTreeWidget(self)
        tree.setColumnCount(len(columns))
        tree.setHeaderLabels([self._t(c) for c in columns])
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        tree.setWordWrap(True)
        tree.setUniformRowHeights(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tree.header().setStretchLastSection(True)
        return tree

    def _boards_group(self) -> QGroupBox:
        group = QGroupBox(self._t("mojo.boards.title"), self)
        box = QVBoxLayout(group)
        self.boards_note = plain_label("")
        box.addWidget(self.boards_note)
        self.board_rows = QVBoxLayout()
        box.addLayout(self.board_rows)
        row = QHBoxLayout()
        self.btn_pick_board = QPushButton(button_text(self._t("mojo.boards.pick")), group)
        self.btn_pick_board.setObjectName("btn_mojo_check_board")
        self.btn_pick_board.setToolTip(plain_tooltip(self._t("mojo.boards.check_tip")))
        self.btn_pick_board.setAutoDefault(False)
        self.btn_pick_board.clicked.connect(lambda _=False: self.pick_board())
        row.addWidget(self.btn_pick_board)
        row.addStretch(1)
        box.addLayout(row)
        self.board_result = plain_label("")
        box.addWidget(self.board_result)
        self.board_buttons: Dict[str, QPushButton] = {}
        return group

    # --- running Mojo off the GUI thread ---

    def _start(self, kind: str, work: Callable[[object, Any], Any], status: str) -> None:
        self.status.setText(status)
        self.busy.setVisible(True)
        self._token = run_off_gui_thread(kind, work, f"mojo-{kind}")
        self._update_buttons()

    def check(self) -> bool:
        """[Check again] (and the window's start): Mojo Oracle with the offers and the boards to offer, off the GUI
        thread. It changes nothing and contacts no board."""
        if self._token is not None:
            return False
        framework = self.framework

        def work(_token, _relay):
            from orchestrator.Core.mojo import Mojo, offers
            from orchestrator.Core.mojo.boards import boards_to_offer
            report = Mojo(framework).run()
            return report, offers(framework, report), boards_to_offer(framework, report)

        self._start("oracle", work, self._t("mojo.running"))
        return True

    def run_miracle(self) -> bool:
        """[Run Mojo Miracle...]: asks first (No the default), then runs Mojo Miracle on the problems shown, with the
        person present (the GUI is the person's channel), off the GUI thread."""
        if self._token is not None or self.report is None or not self._miracle_can_act():
            return False
        if not ask(self, self._t("mojo.title"),
                   self._t("mojo.miracle.ask") + "\n\n" + self._t("mojo.miracle.ask_detail")):
            self.status.setText(self._t("mojo.miracle.not_run"))
            return False
        framework, report, offered = self.framework, self.report, self.offered
        closed = self._closed = threading.Event()

        def work(token, relay):
            from orchestrator.Core.mojo import Mojo, offers
            from orchestrator.Core.mojo.boards import boards_to_offer
            from orchestrator.Core.mojo.docker_objects import offer_image_list
            images = []

            def ask_person(plan, root: str):
                request = DeletionRequest(plan, root)
                relay.done.emit(token, ("ask", request, ""))
                while not request.answered.wait(WAIT_STEP):
                    if closed.is_set():   # the window was closed before the person answered: nothing is deleted
                        return None
                return request.accepted

            def confirm(offer) -> bool:
                request = YesRequest(offer)
                relay.done.emit(token, ("ask", request, ""))
                while not request.answered.wait(WAIT_STEP):
                    if closed.is_set():   # the window was closed before the person answered: nothing is changed
                        return False
                return request.accepted

            def free_space() -> Tuple[int, str]:
                outcome = offer_image_list(framework, ask_person, actor="gui")
                images.append(outcome)
                return outcome.count, outcome.why

            # The plans shown bind the repairs: a problem that changed since then is not repaired (plan_changed).
            result = Mojo(framework).miracle(actor="gui", at_terminal=True, before=report, offered=offered,
                                             decision="person", answered_by="person", confirm_channel="framework-gui",
                                             free_space=free_space, confirm=confirm)
            return (result, offers(framework, result.after), boards_to_offer(framework, result.after),
                    images[-1] if images else None)

        self._start("miracle", work, self._t("mojo.miracle.running"))
        return True

    def _relayed(self, token: object, payload: Any) -> None:
        if token is not self._token:
            return
        kind, value, problem = payload
        if kind == "ask":
            (self._answer_yes if isinstance(value, YesRequest) else self._answer)(value)
            return
        self._token = None
        self.busy.setVisible(False)
        if problem:
            self.status.setText(self._t("mojo.failed", reason=one_line(problem)))
        elif kind == "oracle":
            self.report, self.offered, self.boards = value
            self.status.setText("")
            self._show_report()
        elif kind == "miracle":
            self.status.setText("")
            self._show_miracle(*value)
        self._update_buttons()

    def _answer(self, request: DeletionRequest) -> None:
        """The person's list of the framework's build images, for Mojo Miracle's worker: the deletion dialog with the
        full list (name, full id, size, what each is), nothing ticked, No the default."""
        if self._closed.is_set():   # the window was closed meanwhile: nothing is shown, nothing is deleted
            request.answered.set()
            return
        try:
            intro = (self._t("images.intro", root=one_line(request.root)) if request.root
                     else self._t("images.intro_no_root"))
            request.accepted = ask_deletion(self, request.plan, multiple=True, title=self._t("images.title"),
                                            intro=intro, lang=self.lang)
        except Exception as e:  # noqa: BLE001 - nothing is deleted without the person's yes
            request.accepted = None
            self.status.setText(self._t("mojo.failed", reason=one_line(str(e))))
        finally:
            request.answered.set()

    def _answer_yes(self, request: YesRequest) -> None:
        """The person's own yes to one repair of class person, for Mojo Miracle's worker: its plan (each step with its
        full path and what it costs), "No" the default."""
        if self._closed.is_set():   # the window was closed meanwhile: nothing is asked, nothing is changed
            request.answered.set()
            return
        try:
            from orchestrator.Core.mojo.views import plan_steps
            steps = "\n".join(self._plan_step(step) for step in plan_steps(request.offer))
            request.accepted = ask(self, self._t("mojo.yes.title"),
                                   self._t("mojo.yes.text", summary=one_line(request.offer.plan.summary),
                                           steps=display_text(steps)))
        except Exception as e:  # noqa: BLE001 - nothing is changed without the person's yes
            request.accepted = False
            self.status.setText(self._t("mojo.failed", reason=one_line(str(e))))
        finally:
            request.answered.set()

    def done(self, result: int) -> None:
        self._closed.set()   # a worker waiting for the person's list gets "nothing chosen"
        super().done(result)

    def closeEvent(self, event) -> None:
        self._closed.set()   # also when the window was not shown (QDialog rejects only a visible one)
        super().closeEvent(event)

    # --- showing what Mojo found and did ---

    def _miracle_can_act(self) -> bool:
        from orchestrator.Core.mojo.views import miracle_acts
        return bool(miracle_acts(self.offered))

    def _update_buttons(self) -> None:
        running = self._token is not None
        can = self.report is not None and self._miracle_can_act()
        self.btn_check.setEnabled(not running)
        self.btn_miracle.setEnabled(not running and can)
        self.btn_miracle.setToolTip(plain_tooltip(self._t("mojo.miracle.tip" if can or self.report is None
                                                          else "mojo.miracle.nothing")))

    def _icon(self, severity: str):
        return self.style().standardIcon(SEVERITY_ICONS.get(severity, QStyle.SP_MessageBoxQuestion))

    def _plan_step(self, step) -> str:
        path = self._t("mojo.plan.path", path=step.full_path) if step.full_path else ""
        text = self._t("mojo.plan.step", target=step.target_label, path=path, before=step.before, after=step.after)
        return text + (self._t("mojo.plan.undoable") if step.undoable else "")

    def _board_label(self, device_id: str) -> str:
        return next((b.label for b in self.boards if b.device_id == device_id), device_id)

    def _show_report(self) -> None:
        """Every finding as `llmcf mojo` prints it, the summary sentence, the overall line and the boards."""
        from orchestrator.Core.mojo.views import miracle_can, oracle_summary, person_findings, plan_steps
        self.tree.clear()
        for finding, offer in person_findings(self.report, self.offered):
            severity = finding.severity.value
            item = QTreeWidgetItem([severity_text(severity, self.lang), one_line(finding.subsystem),
                                    display_text(finding.message)])
            item.setIcon(0, self._icon(severity))
            details: List[Tuple[str, str]] = []
            if offer is None:   # no problem: an information's hint is shown, as the CLI shows it
                if severity == "info" and finding.fix_hint:
                    details.append(("mojo.hint", finding.fix_hint))
            else:
                if finding.where:
                    details.append(("mojo.where", finding.where))
                if finding.why:
                    details.append(("mojo.why", finding.why))
                if finding.fix_hint:
                    details.append(("mojo.hint", finding.fix_hint))
                kind, values = miracle_can(offer, True)   # the person is present: the list is offered here
                details.append(("mojo.can", self._t("mojo.can." + kind, **values)))
                details += [("mojo.plan", self._plan_step(step)) for step in plan_steps(offer)]
            if finding.board_check:
                details.append(("mojo.board", self._t("mojo.board.offer",
                                                      label=one_line(self._board_label(finding.board_check)))))
            for key, text in details:
                item.addChild(QTreeWidgetItem(["", self._t(key), display_text(text)]))
            self.tree.addTopLevelItem(item)
            item.setExpanded(bool(details))
        self.summary.setText(display_text(oracle_summary(self.offered, person=True)))
        self.overall.setText(self._t(f"mojo.overall.{self.report.exit_code}"))
        self._show_boards()

    def _show_boards(self) -> None:
        while self.board_rows.count():
            widget = self.board_rows.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.board_buttons = {}
        self.boards_note.setText("" if self.boards else self._t("mojo.boards.none"))
        for offer in self.boards:
            row = QWidget(self)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(plain_label(self._t("mojo.board.offer", label=one_line(offer.label))), stretch=1)
            button = QPushButton(button_text(self._t("mojo.boards.check"), label=offer.label), row)
            button.setObjectName(f"btn_mojo_check_{offer.device_id}")
            button.setToolTip(plain_tooltip(self._t("mojo.boards.check_tip")))
            button.setAutoDefault(False)
            button.setEnabled(not self.board_check.running(offer.device_id))
            button.clicked.connect(lambda _=False, o=offer: self.check_board(o.device_id, o.label))
            line.addWidget(button)
            self.board_buttons[offer.device_id] = button
            self.board_rows.addWidget(row)

    def _show_miracle(self, result, offered_after, boards_after, images) -> None:
        """What Mojo Miracle did, as `llmcf mojo --miracle` prints it; the window then shows Mojo Oracle after it."""
        from orchestrator.Core.deletion import size_text
        from orchestrator.Core.mojo.views import entry_next, entry_text, miracle_summary, outcome_kind
        self.results.clear()
        self.images = images
        if images is not None and images.result is not None:
            item = QTreeWidgetItem(["", self._t("mojo.result.images"),
                                    self._t("images.done", n=len(images.result.deleted),
                                            size=size_text(images.result.freed_bytes))])
            for name in images.result.deleted:
                item.addChild(QTreeWidgetItem(["", "", self._t("deletion.done.permanent", name=one_line(name))]))
            for name, reason in images.result.failed:
                item.addChild(QTreeWidgetItem(["", "", self._t("deletion.kept", name=one_line(name),
                                                               reason=one_line(reason))]))
            self.results.addTopLevelItem(item)
            item.setExpanded(True)
        for entry in result.entries:
            kind = outcome_kind(entry)
            mark, severity = OUTCOME_MARKS[kind]
            text = (self._t("mojo.result.repaired", summary=entry.summary) if entry.outcome == "repaired"
                    else entry_text(entry))
            item = QTreeWidgetItem([f"{mark} {self._t('mojo.outcome.' + kind)}",
                                    f"{one_line(entry.finding.subsystem)} ({one_line(entry.finding.code)})",
                                    display_text(text)])
            item.setIcon(0, self._icon(severity))
            step, hint = entry_next(entry)
            if step == "again":
                item.addChild(QTreeWidgetItem(["", self._t("mojo.hint"), self._t("mojo.result.again")]))
            elif step == "hint":
                item.addChild(QTreeWidgetItem(["", self._t("mojo.hint"), display_text(hint)]))
            self.results.addTopLevelItem(item)
            item.setExpanded(True)
        for finding in result.new_problems:
            severity = finding.severity.value
            item = QTreeWidgetItem([f"{SEVERITY_MARKS.get(severity, '?')} {self._t('mojo.outcome.new')}",
                                    f"{one_line(finding.subsystem)} ({one_line(finding.code)})",
                                    display_text(finding.message)])
            item.setIcon(0, self._icon(severity))
            if finding.fix_hint:
                item.addChild(QTreeWidgetItem(["", self._t("mojo.hint"), display_text(finding.fix_hint)]))
            self.results.addTopLevelItem(item)
            item.setExpanded(True)
        self.results_summary.setText(display_text(miracle_summary(result)))
        for widget in (self.results_heading, self.results, self.results_summary):
            widget.setVisible(True)
        self.report, self.offered, self.boards = result.after, offered_after, boards_after
        self._show_report()

    # --- boards (W1: only on the person's click) ---

    def check_board(self, device_id: str, label: str) -> bool:
        """[Check <board> now] and [Check a board...]: one connection to that board's SSH port, no login."""
        if not self.board_check.start(device_id, label):
            return False
        self.board_result.setText(self._t("mojo.boards.checking", label=one_line(label)))
        if device_id in self.board_buttons:
            self.board_buttons[device_id].setEnabled(False)
        return True

    def _board_checked(self, device_id: str, label: str, text: str, finding) -> None:
        self.board_result.setText(text)
        if device_id in self.board_buttons:
            self.board_buttons[device_id].setEnabled(True)

    def pick_board(self) -> Optional[str]:
        """[Check a board...]: the person picks any trusted board (label, address); returns its device ID."""
        from orchestrator.Core.mojo.boards import trusted_boards
        boards = trusted_boards(self.framework)
        if not boards:
            plain_box(self, "information", self._t("mojo.boards.pick_title"), self._t("mojo.boards.no_trusted"))
            return None
        items = [self._t("mojo.boards.pick_item", label=one_line(d.label), address=one_line(d.address))
                 for d in boards]
        # A board set up again keeps its address and often its name, but its new host key makes it a second trusted
        # board (a new device ID): boards that read the same also name their device ID, so each one can be picked.
        items = [self._t("mojo.boards.pick_item_id", label=one_line(d.label), address=one_line(d.address),
                         device_id=d.device_id) if items.count(text) > 1 else text for d, text in zip(boards, items)]
        choice, ok = QInputDialog.getItem(self, self._t("mojo.boards.pick_title"), self._t("mojo.boards.pick_text"),
                                          items, 0, False)
        if not ok or choice not in items:
            return None
        device = boards[items.index(choice)]
        self.check_board(device.device_id, device.label)
        return device.device_id


__all__ = ["BoardCheck", "DeletionRequest", "MojoWindow", "board_check_text", "run_off_gui_thread",
           "severity_text"]
