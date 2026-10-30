#!/usr/bin/env python3
"""
Boards > Boards and Devices... (W1' of 2026-10-16: every action of the CLI also by mouse).

One window for every board the person has: the stored (probed) boards and the trusted boards, merged by their device
ID (person_steps.boards_overview). The person sees everything about their own boards: the account on the board, the
full host-key fingerprint and whether known_hosts still holds it, when and how the board was trusted, the last deploy,
the system of its probe, its deploy settings and the deployed runtime. Nothing of this goes to an AI.

Each button calls the same core function as its CLI command (orchestrator/runtime/device/person_steps.py and
steps.py), with the same checks, refusals, questions and audit records:

    Add a trusted board...               llmcf deploy devices add       (fingerprint shown, "No" is the default)
    Edit address or key...               llmcf deploy devices edit      (Q5: host name or IP address, port, the host key
                                                                         pasted or read from the board; the old and the
                                                                         new fingerprint side by side, "No", audited)
    Remove trust...                      llmcf deploy devices remove    (exactly what goes, "No", audited)
    Settings...                          llmcf device settings          (the sudo question and its audit record)
    Runtime plan / Status                llmcf device runtime plan / status
    Build runtime...                     llmcf device runtime build     (jobs, mirror, the source question, Cancel)
    Remove older runtime packages...     llmcf device runtime prune     (ask_deletion with the full list)
    Remove older builds on the board...  llmcf device runtime prune --on-device   (ask_deletion, for good)
    Deploy runtime...                    llmcf device runtime deploy    (password asked each time, never stored)
    Verify...                            llmcf device runtime verify    (also without a benchmark model)
    Check this board (Mojo)              llmcf mojo --oracle --board    (one connection to its SSH port, no login)

Every contact with a board, every build and every git or Docker step runs in a plain worker thread (the relay of
gui/worker_relay.py hands the result back); one step at a time, with a busy line and Cancel (it closes the SSH session;
a build stops after its current step). Every text is plain text (gui/plain.py) in the person's language
(utils/l10n_boards.py); passwords are never stored or shown.
"""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.remote.session import CancelToken
from orchestrator.gui.worker_relay import WorkerQuestion, ask_from_worker
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("boards.col.board", "boards.col.id", "boards.col.address", "boards.col.user", "boards.col.key",
           "boards.col.paired", "boards.col.trusted", "boards.col.last", "boards.col.system", "boards.col.settings",
           "boards.col.runtime")
KV_CHOICES = ("auto", "f16", "q8_0", "q4_0")
# object name -> (text key, slot name, what the selected board needs: "" nothing, "trusted", "stored")
BUTTONS = (
    ("btn_boards_add", "boards.btn.add", "_on_add", ""),
    ("btn_boards_edit", "boards.btn.edit", "_on_edit", "trusted"),
    ("btn_boards_remove_trust", "boards.btn.remove_trust", "_on_remove_trust", "trusted"),
    ("btn_boards_settings", "boards.btn.settings", "_on_settings", "stored"),
    ("btn_boards_ssh", "boards.btn.ssh", "_on_ssh", ""),
    ("btn_boards_check", "boards.btn.check", "_on_check", "trusted"),
    ("btn_boards_refresh", "boards.btn.refresh", "reload", ""),
    ("btn_boards_plan", "boards.btn.plan", "_on_plan", "stored"),
    ("btn_boards_status", "boards.btn.status", "_on_status", "stored"),
    ("btn_boards_build", "boards.btn.build", "_on_build", "stored"),
    ("btn_boards_deploy", "boards.btn.deploy", "_on_deploy", "stored"),
    ("btn_boards_verify", "boards.btn.verify", "_on_verify", "stored"),
    ("btn_boards_prune", "boards.btn.prune", "_on_prune", "stored"),
    ("btn_boards_prune_board", "boards.btn.prune_board", "_on_prune_board", "stored"),
)
ROWS = (("btn_boards_add", "btn_boards_edit", "btn_boards_remove_trust", "btn_boards_settings", "btn_boards_ssh",
         "btn_boards_check", "btn_boards_refresh"),
        ("btn_boards_plan", "btn_boards_status", "btn_boards_build", "btn_boards_deploy", "btn_boards_verify"),
        ("btn_boards_prune", "btn_boards_prune_board"))


# The refusals of Edit address or key... with their own text (boards.edit.err.<kind>); a pasted key: key_<detail>.
EDIT_ERRORS = ("no_board", "address", "unchanged", "no_key", "stale", "audit", "not_moved")


class Cancelled(Exception):
    """The person cancelled the running step."""


def _form_dialog(parent: QWidget, title: str, intro: str) -> tuple:
    """(dialog, form layout) with OK and Cancel; the caller adds its fields."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    if intro:
        layout.addWidget(plain_label(intro))
    form = QFormLayout()
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.buttons = buttons
    return dialog, form


def _exec(dialog: QDialog) -> bool:
    """Shows a form modally (tests replace this to fill and accept it without a window)."""
    return bool(dialog.exec())


class BoardsWindow(QDialog):
    """The person's boards; one step at a time, each off the GUI thread."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_boards")
        self.framework = framework
        self.lang = lang or current_language()
        self.setWindowTitle(self._t("boards.title"))
        self.resize(1200, 620)
        layout = QVBoxLayout(self)
        self.intro = plain_label(self._t("boards.intro"))
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("tbl_boards")
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, stretch=2)
        self.buttons: Dict[str, QPushButton] = {}
        for name, key, slot, _need in BUTTONS:
            button = QPushButton(button_text(self._t(key)), self)
            button.setObjectName(name)
            button.setAutoDefault(False)
            button.clicked.connect(getattr(self, slot))
            self.buttons[name] = button
        for row in ROWS:
            line = QHBoxLayout()
            for name in row:
                line.addWidget(self.buttons[name])
            line.addStretch(1)
            layout.addLayout(line)
        busy = QHBoxLayout()
        self.lbl_busy = plain_label("")
        self.lbl_busy.setObjectName("lbl_boards_busy")
        self.btn_cancel = QPushButton(button_text(self._t("boards.btn.cancel")), self)
        self.btn_cancel.setObjectName("btn_boards_cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        busy.addWidget(self.lbl_busy, stretch=1)
        busy.addWidget(self.btn_cancel)
        layout.addLayout(busy)
        self.output = QPlainTextEdit(self)   # plain text by nature: never rich text
        self.output.setObjectName("txt_boards_output")
        self.output.setReadOnly(True)
        layout.addWidget(self.output, stretch=1)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.entries: List[Dict[str, Any]] = []
        self._token: Optional[object] = None
        self._progress_token: Optional[object] = None
        self._done: Optional[Callable[[Any], None]] = None
        self._failed: Optional[Callable[[BaseException], bool]] = None
        # the one cancel of the running step (remote/session.py CancelToken): it holds the step's board session
        self._cancel = CancelToken()
        self._worker: Optional[threading.Thread] = None
        from orchestrator.gui.worker_relay import worker_relay
        worker_relay().done.connect(self._finished, Qt.QueuedConnection)
        self._update_buttons()

    # --- basics ---------------------------------------------------------------------------------------------------

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    @property
    def state_dir(self) -> Path:
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        if not state:
            raise RuntimeError("no per-user folder")
        return Path(state)

    @property
    def audit(self) -> Any:
        """The audit log of the person's board decisions (trust, records, consents): the one sink of every surface
        (DECISIONS round 25 Q3, person_steps.device_audit): the registry's log, else the per-user audit.db, so a
        consent here works without a registry, as in the CLI."""
        from orchestrator.runtime.device.person_steps import device_audit
        return device_audit(self.framework, self.state_dir)

    def _info(self, text: str, kind: str = "information") -> None:
        plain_box(self, kind, self._t("boards.title"), text)

    def _say(self, text: str) -> None:
        self.output.appendPlainText(one_line(text) if "\n" not in text else text)

    # --- the table ------------------------------------------------------------------------------------------------

    def reload(self) -> None:
        """Reads the boards again (files only; no board is contacted)."""
        from orchestrator.runtime.device.person_steps import boards_overview
        try:
            self.entries = boards_overview(self.state_dir)
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: shown as plain text
            self.entries = []
            self.intro.setText(f"{self._t('boards.intro')}\n\n{self._t('boards.err.failed', reason=one_line(str(e)))}")
        else:
            self.intro.setText(self._t("boards.intro") if self.entries else
                               f"{self._t('boards.intro')}\n\n{self._t('boards.empty')}")
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            for column, value in enumerate(self._cells(entry)):
                cell = QTableWidgetItem(one_line(value))   # plain text, never rich text
                if column == 0:
                    cell.setData(Qt.UserRole, entry["device_id"])
                self.table.setItem(row, column, cell)
        self._update_buttons()

    def _cells(self, entry: Dict[str, Any]) -> List[str]:
        t, dash = self._t, self._t("boards.dash")
        trusted, stored = entry.get("trusted"), entry.get("stored")
        address = (trusted or {}).get("address") or (stored or {}).get("address") or dash
        if trusted:
            key = t(f"boards.key.{trusted['host_key']}", key_type=trusted["key_type"],
                    fingerprint=trusted["fingerprint"])
            last = trusted.get("last_deploy") or {}   # may hold only a recorded contact for Mojo: no deploy then
            last_text = t("boards.last", at=last.get("at"), result=t("boards.last.ok" if last.get("ok") else
                                                                     "boards.last.failed")) if last.get("at") else dash
            trust = [trusted["user"], key, t("boards.yes" if trusted["paired"] else "boards.no"), trusted["trusted"],
                     last_text]
        else:
            trust = [dash, t("boards.not_trusted"), dash, dash, dash]
        if stored:
            s = stored.get("system") or {}
            system = t("boards.system", os=s.get("OS_ID") or s.get("OS") or "?", version=s.get("OS_VERSION_ID") or "",
                       arch=s.get("ARCH") or "", libc=s.get("LIBC") or "?", libc_version=s.get("LIBC_VERSION") or "",
                       init=s.get("INIT_SYSTEM") or "?") if s else t("boards.system.missing")
            deploy = stored.get("deploy") or {}
            settings = t("boards.settings.damaged") if deploy.get("problem") else t(
                "boards.settings.row", install=deploy.get("install", "user"),
                user=deploy.get("ssh_user") or t("boards.settings.not_set"))
            deployed = stored.get("deployed")
            runtime = t("boards.runtime.row", build=str(deployed.get("build_id") or "")[:16],
                        install=deployed.get("install"), at=deployed.get("deployed_at")) if deployed else dash
            probe = [system, settings, runtime]
        else:
            probe = [t("boards.not_probed"), dash, dash]
        return [entry["label"], entry["device_id"], address, *trust, *probe]

    def select(self, device_id: str) -> bool:
        for row, entry in enumerate(self.entries):
            if entry["device_id"] == device_id:
                self.table.selectRow(row)
                return True
        return False

    def selected(self) -> Optional[Dict[str, Any]]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _update_buttons(self) -> None:
        entry, running = self.selected(), self._token is not None
        for name, _key, _slot, need in BUTTONS:
            ok = not need or (entry is not None and entry.get(need) is not None)
            self.buttons[name].setEnabled(ok and (not running or name == "btn_boards_refresh"))

    def _need(self, what: str) -> Optional[Dict[str, Any]]:
        """The selected board when it has `what` (trusted or stored), else the reason as a message."""
        entry = self.selected()
        if entry is None:
            self._info(self._t("boards.need_selection"))
            return None
        if entry.get(what) is None:
            self._info(self._t("boards.need_trusted" if what == "trusted" else "boards.need_stored"))
            return None
        return entry

    # --- one step at a time, off the GUI thread ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._token is not None

    def _start(self, what: str, work: Callable[[], Any], done: Callable[[Any], None],
               failed: Optional[Callable[[BaseException], bool]] = None) -> bool:
        """Runs `work()` in a worker thread; `done(result)` or the error runs in the GUI thread afterwards (`failed`
        may handle an error itself and return True). False when another step is running."""
        if self._token is not None:
            self._info(self._t("boards.busy_already"))
            return False
        from orchestrator.gui.worker_relay import worker_relay
        token, progress, relay = object(), object(), worker_relay()
        self._token, self._progress_token, self._done, self._failed = token, progress, done, failed
        self._cancel = CancelToken()

        def run() -> None:
            try:
                result: Any = ("ok", work())
            except Exception as e:  # noqa: BLE001 - shown to the person, the window keeps running
                result = ("error", e)
            relay.done.emit(token, result)

        self.lbl_busy.setText(self._t("boards.busy", what=what))
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self._update_buttons()
        self._worker = threading.Thread(target=run, name="boards-step", daemon=True)
        self._worker.start()
        return True

    def _progress(self, text: str) -> None:
        """A line from the worker thread for the output pane (through the relay)."""
        from orchestrator.gui.worker_relay import worker_relay
        token = self._progress_token
        if token is not None:
            worker_relay().done.emit(token, str(text))

    def _ask_from_worker(self, title: str, text: str) -> bool:
        """A yes/no question the running step needs answered before it goes on (the CLI asks it at that point too):
        asked in the GUI thread with "No" as the default while the worker waits; Cancel ends the wait."""
        token = self._progress_token
        if token is None:
            raise Cancelled()
        return ask_from_worker(token, title, text, stop=self._check_cancel)

    def _keep_client(self, client: Any) -> None:
        """The board session of the running step, so Cancel can close it (at once when Cancel came first); closing it
        also ends a wait for the board."""
        self._cancel.hold(client)

    def _board_lines(self, host: str) -> Callable[[Any], None]:
        """The running step's wait for the board (N5) as lines of the output pane: when the network drops and when
        the board answers again (remote/text.py wait_lines, in the window's language)."""
        from orchestrator.remote.text import wait_lines
        return wait_lines(self._progress, host, self.lang)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _on_cancel(self) -> None:
        self._cancel.cancel()   # closes the step's board session: a running command and a wait for the board end
        self.btn_cancel.setEnabled(False)
        self.lbl_busy.setText(self._t("boards.cancelling"))

    def _finished(self, token: object, result: Any) -> None:
        if token is not None and token is self._progress_token and isinstance(result, str):
            self._say(result)
            return
        if token is not None and token is self._progress_token and isinstance(result, WorkerQuestion):
            result.reply(lambda: ask(self, result.title, result.text))
            return
        if token is None or token is not self._token:
            return
        done, failed, cancelled = self._done, self._failed, self._cancel.is_set()
        self._token = self._progress_token = self._done = self._failed = None
        self.lbl_busy.setText("")
        self.btn_cancel.setVisible(False)
        self._update_buttons()
        kind, value = result
        if kind == "ok":
            if done is not None:
                done(value)
            return
        if cancelled or isinstance(value, Cancelled):
            self._say(self._t("boards.cancelled"))
            return
        if failed is not None and failed(value):
            return
        self._show_error(value)

    def _error_text(self, error: BaseException, address: str = "", port: int = 22, user: str = "",
                    device_id: str = "") -> str:
        """The person's words for a failed step (an SSH reason code with its next step when there is one)."""
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device.person_steps import BoardStepError
        t = self._t
        if isinstance(error, BoardStepError):
            if error.reason in reasons.CODES:   # a local problem also names what failed (the file's name)
                text = reasons.person_text(error.reason, self.lang, address=address, port=port, error=error.error)
                if error.kind == "host_key_changed":
                    text += "\n\n" + str(error)
                if error.kind == "login_failed":
                    text += "\n\n" + t("boards.err.pair")
                return text
            if error.kind == "key_text":
                return t(f"boards.edit.err.key_{error.facts.get('detail') or 'not_a_key'}")
            if error.kind == "other_board":   # the new key is another trusted board's
                return t("boards.edit.err.other_board", label=error.facts.get("label", ""),
                         device_id=error.facts.get("device_id", ""))
            if error.kind in EDIT_ERRORS:
                return t(f"boards.edit.err.{error.kind}")
            simple = {"no_paramiko": t("boards.err.no_paramiko"),
                      "not_trusted": t("boards.err.not_trusted", address=address),
                      "other_device": t("boards.err.other_device", address=address, device_id=device_id),
                      "key_install": t("boards.err.key_install", reason=str(error.error or error))}
            if error.kind in simple:
                return simple[error.kind]
            if error.kind == "unreachable":
                return t("boards.err.unreachable", address=address, reason=str(error.error or error))
            if error.kind == "login_failed":
                return t("boards.err.login_failed", user=user, address=address, reason=str(error.error or error)) \
                    + "\n\n" + t("boards.err.pair")
        return t("boards.err.failed", reason=str(error))

    def _show_error(self, error: BaseException, **where: Any) -> None:
        text = self._error_text(error, **where)
        self._say(text)
        self._info(text, "warning")

    # --- helpers for the stored board ---------------------------------------------------------------------------

    def _store(self):
        from orchestrator.runtime.profile import ProfileStore
        return ProfileStore(self.state_dir)

    def _profile(self, entry: Dict[str, Any]):
        """The board's stored probe profile, or None after telling the person why."""
        from orchestrator.runtime.profile import ProfileError
        try:
            profile = self._store().load_device(entry["device_id"])
        except ProfileError as e:
            self._info(self._t("boards.err.failed", reason=one_line(str(e))), "warning")
            return None
        if profile is None:
            self._info(self._t("boards.need_stored"), "warning")
        return profile

    def _settings(self, entry: Dict[str, Any]):
        from orchestrator.runtime.device.person_steps import load_settings
        from orchestrator.runtime.device.settings import SettingsError
        try:
            return load_settings(self._store(), entry["device_id"])
        except (SettingsError, ValueError) as e:
            self._info(self._t("boards.settings.damaged_text", reason=one_line(str(e))), "warning")
            return None

    def _address(self, entry: Dict[str, Any]):
        from orchestrator.runtime.device.person_steps import board_address
        try:
            return board_address(self._store(), entry["device_id"])
        except ValueError as e:
            self._info(self._t("boards.need_address", reason=one_line(str(e))), "warning")
            return None

    def _pin(self):
        from orchestrator.runtime.catalog import load_catalog
        source = load_catalog().llama_cpp.source
        if source is None:
            self._info(self._t("boards.no_pin"), "warning")
        return source

    def _kv_text(self, choice: Any) -> str:
        if not choice.automatic:
            return ui_text("device.kv.chosen", self.lang, kind=choice.kind)
        why = ui_text(f"device.kv.why_{choice.why}", self.lang, free=choice.free_gib)
        return ui_text("device.kv.auto", self.lang, kind=choice.kind, why=why)

    def _check_lines(self, report: Any) -> List[str]:
        result = {True: "boards.check.passed", False: "boards.check.failed", None: "boards.check.skipped"}
        lines = [self._t("boards.checks")]
        for check in (report.checks if report is not None else []):
            lines.append("  " + self._t("boards.check_line", name=check.name, result=self._t(result[check.ok]),
                                        detail=one_line(check.detail)))
        # the board's temperature at these checks, as the CLI prints it (round 23 item 16)
        from orchestrator.runtime.device.steps import heat_lines
        return lines + heat_lines(report, self.lang)

    def _may_run_untrusted(self, user: str) -> Callable[[str], bool]:
        """The CLI's question before a model that is not a catalog model runs in the short benchmark, asked from the
        worker once the board named the model's sha256 ("No" is the default); a catalog model is never asked about."""
        return lambda path: self._ask_from_worker(self._t("boards.title"),
                                                  self._t("boards.untrusted_question", path=path, user=user))

    @staticmethod
    def _board_label(entry: Dict[str, Any]) -> str:
        """The board's name in a 'tested on' record: its stored label, as the CLI writes it."""
        return (entry.get("stored") or {}).get("label") or entry["device_id"]

    def _record_tested_on(self, entry: Dict[str, Any], profile: Any, report: Any, digest: Optional[str],
                          build_id: str, source: Any, kv: Optional[str]) -> Optional[int]:
        """'tested on' this board for the builds of the benchmarked model (N3), as the CLI records it; it walks every
        golden-artifact folder, so it runs in the worker thread. The number of builds it was recorded with, or None
        when no short benchmark ran."""
        from orchestrator.Core.artifact_output import artifact_folders
        from orchestrator.runtime.device.service import record_board_test
        smoke = report.check("smoke") if report is not None else None
        if smoke is None or smoke.ok is None or not digest:
            return None
        try:
            builds = record_board_test(artifact_folders(self.framework),
                                       SimpleNamespace(device_id=entry["device_id"], label=self._board_label(entry)),
                                       profile, report, digest, build_id, source, kv)
        except Exception:  # noqa: BLE001 - the check itself ran; the record is a convenience
            builds = []
        return len(builds)

    def _tested_on_lines(self, entry: Dict[str, Any], profile: Any, count: Optional[int]) -> List[str]:
        if count is None:
            return []
        board = profile.get("BOARD_MODEL") or self._board_label(entry)
        return [ui_text("device.bench.recorded", self.lang, board=board, count=count) if count else
                ui_text("device.bench.not_recorded", self.lang)]

    # --- trusted boards ------------------------------------------------------------------------------------------

    def _on_add(self) -> None:
        """deploy devices add: host, user, port, label; the fingerprint is shown and asked ("No" by default)."""
        dialog, form = _form_dialog(self, self._t("boards.add.title"), self._t("boards.add.intro"))
        host, user, label = QLineEdit(dialog), QLineEdit(dialog), QLineEdit(dialog)
        host.setObjectName("txt_boards_add_host")
        user.setObjectName("txt_boards_add_user")
        label.setObjectName("txt_boards_add_label")
        port = QSpinBox(dialog)
        port.setObjectName("spn_boards_add_port")
        port.setRange(1, 65535)
        port.setValue(22)
        form.addRow(self._t("boards.add.host"), host)
        form.addRow(self._t("boards.add.user"), user)
        form.addRow(self._t("boards.add.port"), port)
        form.addRow(self._t("boards.add.label"), label)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (host.text().strip(), user.text().strip(), int(port.value()), label.text().strip() or None)
        dialog.deleteLater()
        self.add_board(*values)

    def add_board(self, host: str, user: str, port: int, label: Optional[str]) -> None:
        from orchestrator.runtime.device import person_steps
        try:
            label = person_steps.check_board_entry(host, user, label)
        except person_steps.BoardStepError as e:
            self._info(self._t("boards.add.bad_entry" if e.kind == "host_user" else "boards.add.bad_label"),
                       "warning")
            return
        from orchestrator.remote.address import Endpoint
        address = Endpoint(host, port).store_form
        state = self.state_dir

        def read():
            return person_steps.read_host_key(state, host, port)

        def done(found):
            key, status, fp = found
            if status != "known":
                self._info(self._t("boards.add.not_known", address=address, status=status), "warning")
                return
            if not ask(self, self._t("boards.add.title"),
                       self._t("boards.add.question", address=address, key_type=key.get_name(), fingerprint=fp,
                               user=user, host=host)):
                self._say(self._t("boards.add.nothing"))
                return
            audit = self.audit

            def pair():
                return person_steps.add_trusted_board(state, host, port, user, key, label=label, via="gui",
                                                      audit=audit, actor="gui")

            self._start(self._t("boards.add.pairing"), pair, lambda out: self._added(out, state))

        self._start(self._t("boards.add.reading"), read, done,
                    failed=lambda e: self._show_error(e, address=host, port=port, user=user) or True)

    def _added(self, out: Any, state: Path) -> None:
        from orchestrator.runtime.device.trusted import TrustedDevices
        device, paired = out
        if device is None:
            self._info(self._t("boards.add.failed"), "warning")
            return
        lines = [self._t("boards.add.done", device_id=device.device_id, label=device.label)]
        if not paired:
            lines.append(self._t("boards.add.not_paired"))
        elif TrustedDevices(state).key_status(device) != "known":
            lines.append(self._t("boards.add.key_gone"))
        self._say("\n".join(lines))
        self._info("\n\n".join(lines))
        self.reload()
        self.select(device.device_id)

    def _on_edit(self) -> None:
        """deploy devices edit (Q5): the board's host name or IP address, its SSH port and its host key, pasted (copy
        and paste: nobody types a key) or read from the board; the change is asked with the old and the new fingerprint
        side by side ("No" is the default) and audited."""
        from orchestrator.runtime.device.trusted import TrustedDevices
        entry = self._need("trusted")
        if entry is None:
            return
        device = TrustedDevices(self.state_dir).get(entry["device_id"])
        if device is None:
            self._info(self._t("boards.edit.err.no_board"), "warning")
            self.reload()
            return
        dialog, form = _form_dialog(self, self._t("boards.edit.title"),
                                    self._t("boards.edit.intro", label=device.label, device_id=device.device_id))
        dialog.setObjectName("dlg_boards_edit")
        host = QLineEdit(device.host, dialog)
        host.setObjectName("txt_boards_edit_host")
        port = QSpinBox(dialog)
        port.setObjectName("spn_boards_edit_port")
        port.setRange(1, 65535)
        port.setValue(device.port)
        key = QPlainTextEdit(dialog)   # plain text by nature: what is pasted stays text, never rich text
        key.setObjectName("txt_boards_edit_key")
        key.setPlaceholderText(self._t("boards.edit.key_hint"))
        key.setTabChangesFocus(True)
        key.setFixedHeight(80)
        read = QCheckBox(self._t("boards.edit.read"), dialog)
        read.setObjectName("cb_boards_edit_read")
        read.toggled.connect(lambda on: key.setEnabled(not on))
        form.addRow(self._t("boards.edit.host"), host)
        form.addRow(self._t("boards.edit.port"), port)
        form.addRow(self._t("boards.edit.key"), key)
        form.addRow("", read)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (host.text().strip(), int(port.value()), key.toPlainText(), read.isChecked())
        dialog.deleteLater()
        self.edit_board(device.device_id, *values)

    def edit_board(self, device_id: str, host: str, port: int, key_text: str, read_key: bool) -> None:
        """The change of the board's address and key: read and checked off the GUI thread, then asked side by side,
        then made off the GUI thread (person_steps.plan_board_change, change_board: the CLI's steps)."""
        from orchestrator.runtime.device import person_steps
        state = self.state_dir

        def plan():
            return person_steps.plan_board_change(state, device_id, host=host or None, port=port,
                                                  key_text=None if read_key else (key_text or None),
                                                  read_key=read_key)

        def planned(change) -> None:
            if not self._confirm_change(change):
                self._say(self._t("boards.edit.nothing"))
                return
            audit, framework = self.audit, self.framework

            def apply():
                output_dirs = []
                if change.plan.key_changed:   # the board's "tested on" records move with its data (P6)
                    from orchestrator.Core.artifact_output import artifact_folders
                    try:
                        output_dirs = artifact_folders(framework)
                    except Exception:  # noqa: BLE001 - no output folder: there are no build records to move
                        output_dirs = []
                return person_steps.change_board(state, change, via="gui", audit=audit, actor="gui",
                                                 output_dirs=output_dirs)

            self._start(self._t("boards.edit.changing"), apply, lambda out: self._changed(out, change),
                        failed=lambda e: self._change_failed(e, change.plan.new.host, change.plan.new.port))

        self._start(self._t("boards.edit.reading" if read_key else "boards.edit.checking"), plan, planned,
                    failed=lambda e: self._change_failed(e, host, port) or self._show_error(e, address=host,
                                                                                           port=port) or True)

    def _change_text(self, error: BaseException, address: str, port: int) -> Optional[str]:
        """The person's words for a key or address change that something on this computer stopped, as `deploy devices
        edit` prints them (cli._board_change_failed): the plain local_problem reason, then known_hosts held by another
        part of the framework (LockTimeout: the data move was undone, nothing changed), what failed on this computer
        (an OSError, its file's name) or the error itself. None for a BoardStepError, which keeps the shared step
        text (_error_text), as every other step's error does."""
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device.person_steps import BoardStepError
        from orchestrator.utils.file_lock import LockTimeout
        if isinstance(error, BoardStepError):
            return None
        t = self._t
        if isinstance(error, LockTimeout):
            detail = t("boards.edit.err.lock")
        elif isinstance(error, OSError):
            detail = t("boards.edit.err.local", detail=reasons.local_detail(error))
        else:
            detail = t("boards.edit.err.stopped", reason=str(error) or error.__class__.__name__)
        return reasons.person_text(reasons.LOCAL_PROBLEM, self.lang, address=address, port=port) + "\n\n" + detail

    def _change_failed(self, error: BaseException, address: str, port: int) -> bool:
        text = self._change_text(error, address, port)
        if text is None:
            return False
        self._say(text)
        self._info(text, "warning")
        self.reload()
        return True

    def _confirm_change(self, change: Any) -> bool:
        """The question before the change: the address, the host key and the device ID now and after the change side
        by side, a key of another board the new address held, and "No" as the default (and the Escape answer)."""
        plan, device, t = change.plan, change.device, self._t
        dialog = QDialog(self)
        dialog.setObjectName("dlg_boards_edit_confirm")
        dialog.setWindowTitle(t("boards.edit.title"))
        dialog.setMinimumWidth(640)
        layout = QVBoxLayout(dialog)
        layout.addWidget(plain_label(t("boards.edit.question", label=device.label, device_id=device.device_id)))
        grid = QGridLayout()
        cells = [("", t("boards.edit.now"), t("boards.edit.after")),
                 (t("boards.edit.row.address"), plan.old.display, plan.new.display),
                 (t("boards.edit.row.key"), f"{plan.old_key_type}\n{plan.old_fingerprint}",
                  f"{plan.new_key_type}\n{plan.new_fingerprint}"),
                 (t("boards.edit.row.device"), plan.old_device_id, plan.new_device_id)]
        names = ("", "now", "after")
        for row, values in enumerate(cells):
            for column, value in enumerate(values):
                label = plain_label(value, wrap=False)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if row and column:
                    label.setObjectName(f"lbl_boards_edit_{('address', 'key', 'device')[row - 1]}_{names[column]}")
                grid.addWidget(label, row, column)
        layout.addLayout(grid)
        notes = []
        if plan.displaced:
            notes.append(t("boards.edit.displaced", address=plan.new.display, key_type=plan.new_key_type,
                           fingerprint=plan.displaced))
        if plan.key_changed:
            notes.append(t("boards.edit.new_id", new_id=plan.new_device_id, old_id=plan.old_device_id))
        notes.append(t("boards.edit.compare"))
        layout.addWidget(plain_label("\n\n".join(notes)))
        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No, dialog)
        yes, no = buttons.button(QDialogButtonBox.Yes), buttons.button(QDialogButtonBox.No)
        yes.setObjectName("btn_boards_edit_yes")
        no.setObjectName("btn_boards_edit_no")
        yes.setAutoDefault(False)
        no.setAutoDefault(True)
        no.setDefault(True)
        no.setFocus()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        yes.clicked.connect(dialog.accept)
        no.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            return _exec(dialog)
        finally:
            dialog.deleteLater()

    def _changed(self, out: Any, change: Any) -> None:
        device, paired = out
        plan, t = change.plan, self._t
        moved_lines = []
        data = getattr(change, "data", None)
        if data is not None:   # where the board's data went (P6, the CLI's moved_lines)
            def kinds(names):
                return ", ".join(t(f"boards.edit.data.{name}") for name in names)
            for key, names in (("boards.edit.moved", data.moved), ("boards.edit.kept", data.kept),
                               ("boards.edit.left", data.left)):
                if names:
                    moved_lines.append(t(key, old_id=data.old_id, new_id=data.new_id, what=kinds(names)))
        if device is None:   # the data moved all the same (the CLI prints moved_lines before it says so)
            self._info("\n\n".join([t("boards.edit.record_failed")] + moved_lines), "warning")
            self.reload()
            return
        lines = [t("boards.edit.done", label=device.label, device_id=device.device_id, address=plan.new.display,
                   key_type=plan.new_key_type, fingerprint=plan.new_fingerprint)] + moved_lines
        if plan.key_changed:
            lines.append(t("boards.edit.probe_again", old_id=plan.old_device_id))
            if not paired:
                lines.append(t("boards.add.not_paired"))
        self._say("\n".join(lines))
        self._info("\n\n".join(lines))
        self.reload()
        self.select(device.device_id)

    def _on_remove_trust(self) -> None:
        """deploy devices remove: asks with exactly what is removed ("No" by default); audited as in the CLI."""
        from orchestrator.runtime.device.person_steps import remove_trust
        entry = self._need("trusted")
        if entry is None:
            return
        row = entry["trusted"]
        if not ask(self, self._t("boards.remove.title"),
                   self._t("boards.remove.question", label=row["label"], device_id=row["device_id"],
                           address=row["address"], user=row["user"], key_type=row["key_type"],
                           fingerprint=row["fingerprint"])):
            return
        audit = self.audit
        if remove_trust(self.state_dir, row["device_id"], audit=audit, actor="gui", channel="framework-gui"):
            text = self._t("boards.remove.done", label=row["label"], device_id=row["device_id"])
        else:
            text = self._t("boards.remove.gone", device_id=row["device_id"])
        self._say(text)
        self.reload()

    # --- deploy settings ---------------------------------------------------------------------------------------

    # --- the framework's own SSH settings (round 25 Q4: llmcf deploy ssh-config show|edit|import) -----------------

    def _on_ssh(self) -> None:
        """deploy ssh-config: the framework's own OpenSSH settings file (every OpenSSH call reads it instead of
        ~/.ssh/config), shown whole; one board's block set (its lines checked: refused ones with the reason), or copied
        from the person's ~/.ssh/config after showing it. A ProxyJump or ProxyCommand needs its own yes after reading
        what it does; the change needs a yes too ("No" is the default each time). Local files only, on the GUI
        thread."""
        from orchestrator.runtime.device import person_steps
        state = self.state_dir
        try:
            path, text = person_steps.ssh_settings(state)
        except OSError as e:
            self._ssh_file_failed(e)
            return
        from orchestrator.remote import ssh_config
        from orchestrator.runtime.device.trusted import TrustedDevices
        entry = self.selected()
        device = TrustedDevices(state).get(entry["device_id"]) if entry and entry.get("trusted") else None
        host_now = device.host if device is not None else ""   # the selected board's address as OpenSSH gets it
        dialog, form = _form_dialog(self, self._t("boards.ssh.title"), self._t("boards.ssh.intro", file=str(path)))
        dialog.setObjectName("dlg_boards_ssh")
        shown = QPlainTextEdit(dialog)   # plain text by nature: the file is never read as rich text
        shown.setObjectName("txt_boards_ssh_file")
        shown.setReadOnly(True)
        shown.setPlainText(text)
        shown.setFixedHeight(120)
        host = QLineEdit(host_now, dialog)
        host.setObjectName("txt_boards_ssh_host")
        lines = QPlainTextEdit(dialog)
        lines.setObjectName("txt_boards_ssh_lines")
        lines.setTabChangesFocus(True)
        lines.setFixedHeight(100)

        def fill() -> None:
            lines.setPlainText("\n".join(ssh_config.block_lines(text, host.text().strip()) or []))

        fill()
        host.editingFinished.connect(fill)
        imported = QPushButton(button_text(self._t("boards.ssh.import")), dialog)
        imported.setObjectName("btn_boards_ssh_import")
        imported.setAutoDefault(False)
        imported.clicked.connect(lambda: self._import_ssh(host.text().strip(), lines))
        form.addRow(self._t("boards.ssh.file"), shown)
        form.addRow(self._t("boards.ssh.host"), host)
        form.addRow(self._t("boards.ssh.lines"), lines)
        form.addRow("", imported)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (host.text().strip(), lines.toPlainText().splitlines())
        dialog.deleteLater()
        self.set_ssh_block(*values)

    def _import_ssh(self, host: str, lines: Any) -> None:
        """The person-only import: `host`'s block of ~/.ssh/config, shown, then put into the lines field (nothing is
        written before the person confirms the change)."""
        from orchestrator.runtime.device import person_steps
        try:
            found = person_steps.import_ssh_block(host)
        except person_steps.BoardStepError as e:
            reason = str(getattr(e.error, "strerror", "") or e.error or e)
            self._info(self._t(f"boards.ssh.err.{e.kind}", host=host, reason=reason), "warning")
            return
        lines.setPlainText("\n".join(found))
        self._info(self._t("boards.ssh.imported", host=host, lines="\n".join(found)))

    def _ssh_file_failed(self, error: BaseException) -> None:
        from orchestrator.runtime.device import reasons
        self._info(self._t("boards.ssh.err.file", reason=reasons.local_detail(error)), "warning")

    def set_ssh_block(self, host: str, lines: List[str]) -> bool:
        """The change of `host`'s block (person_steps.plan_ssh_block and save_ssh_block, the CLI's steps): refused
        lines are named with their reason and nothing is written; a ProxyJump or ProxyCommand is asked about on its own
        with what it does; then the change itself is asked with the block now and after. True when it was written."""
        from orchestrator.runtime.device import person_steps
        t, state = self._t, self.state_dir
        try:
            plan = person_steps.plan_ssh_block(state, host, lines)
        except person_steps.BoardStepError as e:
            self._info(t(f"boards.ssh.err.{e.kind}"), "warning")
            return False
        except OSError as e:
            self._ssh_file_failed(e)
            return False
        if plan.refused:
            problems = "\n".join(t(f"boards.ssh.why.{p.kind}", line=self._ssh_line(plan.new, p.number))
                                 for p in plan.refused)
            self._info(t("boards.ssh.refused", problems=problems), "warning")
            return False
        if plan.unchanged and not plan.pending_yes:
            self._info(t("boards.ssh.unchanged"))
            return False
        if plan.knowingly:
            what = "\n\n".join(t(f"boards.ssh.what.{k.keyword.lower()}", line=k.line) for k in plan.knowingly)
            if not ask(self, t("boards.ssh.title"), t("boards.ssh.knowingly", lines=what)):
                self._say(t("boards.ssh.nothing"))
                return False
        none = t("boards.ssh.none")
        if not ask(self, t("boards.ssh.title"), t("boards.ssh.question", host=plan.host,
                                                   old="; ".join(plan.old) or none, new="; ".join(plan.new) or none)):
            self._say(t("boards.ssh.nothing"))
            return False
        try:
            person_steps.save_ssh_block(state, plan, via="gui", knowingly=bool(plan.knowingly), audit=self.audit,
                                        actor="gui")
        except person_steps.BoardStepError as e:
            self._info(t(f"boards.ssh.err.{e.kind}") if e.kind == "ssh_stale" else str(e), "warning")
            return False
        except OSError as e:
            self._ssh_file_failed(e)
            return False
        self._say(t("boards.ssh.done", host=plan.host))
        return True

    @staticmethod
    def _ssh_line(lines: Any, number: int) -> str:
        try:
            return str(lines[number - 1])
        except (IndexError, TypeError):
            return ""

    def _on_settings(self) -> None:
        """device settings: the full current settings, and install, user, port, model and KV cache to change."""
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        found = self._settings_to_change(entry) if profile is not None else None
        if found is None:
            return
        current, replaced = found
        dialog, form = _form_dialog(self, self._t("boards.settings.title", label=entry["label"]),
                                    self._settings_text(current, profile))
        install = QComboBox(dialog)
        install.setObjectName("cmb_boards_install")
        install.addItem(self._t("boards.settings.opt_user"), "user")
        install.addItem(self._t("boards.settings.opt_systemd"), "systemd")
        install.setCurrentIndex(max(0, install.findData(current.install)))
        user = QLineEdit(current.ssh_user or "", dialog)
        user.setObjectName("txt_boards_ssh_user")
        port = QSpinBox(dialog)
        port.setObjectName("spn_boards_port")
        port.setRange(1024, 65535)
        port.setValue(current.port)
        model = QLineEdit(current.model or "", dialog)
        model.setObjectName("txt_boards_model")
        model.setPlaceholderText(self._t("boards.settings.model_hint"))
        kv = QComboBox(dialog)
        kv.setObjectName("cmb_boards_kv")
        auto = current.changed(kv_cache=None).kv_choice(profile)
        for kind in KV_CHOICES:
            text = self._t("boards.settings.kv_auto", kind=auto.kind, why=ui_text(
                f"device.kv.why_{auto.why}", self.lang, free=auto.free_gib)) if kind == "auto" else \
                ui_text(f"device.kv.{kind}", self.lang)
            kv.addItem(text, kind)
        kv.setCurrentIndex(max(0, kv.findData(current.kv_cache or "auto")))
        form.addRow(self._t("boards.settings.install"), install)
        form.addRow(self._t("boards.settings.user"), user)
        form.addRow(self._t("boards.settings.port"), port)
        form.addRow(self._t("boards.settings.model"), model)
        form.addRow(self._t("boards.settings.kv"), kv)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        wanted = {"install": install.currentData(), "ssh_user": user.text().strip() or None,
                  "port": int(port.value()), "model": model.text().strip() or None,
                  "kv_cache": None if kv.currentData() == "auto" else kv.currentData()}
        dialog.deleteLater()
        self.change_settings(entry, profile, current, wanted, replace=replaced)

    def _settings_to_change(self, entry: Dict[str, Any]):
        """(settings, replaced) for Settings...: the stored ones, or the defaults after the person's yes to the
        question about damaged settings (with the file's full path and size; "No" is the default)."""
        from orchestrator.runtime.device import person_steps
        title = self._t("boards.settings.title", label=entry["label"])

        def may_replace(reason: str, path: str, size: str) -> bool:
            return ask(self, title, self._t("boards.settings.damaged_question", device_id=entry["device_id"],
                                            reason=reason, path=path, size=size))

        try:
            return person_steps.settings_to_change(self._store(), entry["device_id"], may_replace=may_replace)
        except person_steps.BoardStepError as e:
            self._info(self._t("boards.settings.damaged_text", reason=one_line(e.facts.get("detail") or str(e))),
                       "warning")
            return None

    def _settings_text(self, current: Any, profile: Any) -> str:
        t = self._t
        install = t("boards.settings.install_user") if current.install == "user" else \
            t("boards.settings.install_systemd", at=current.sudo_confirmed_at or t("boards.settings.not_set"))
        lines = [t("boards.settings.current", install=install, user=current.ssh_user or t("boards.settings.not_set"),
                   port=current.port, model=current.model or t("boards.settings.not_set"),
                   kv=self._kv_text(current.kv_choice(profile)))]
        problems = current.problems(profile)
        if problems:
            lines.append(t("boards.settings.problems"))
            lines += [f"- {p}" for p in problems]
        return "\n".join(lines)

    def change_settings(self, entry: Dict[str, Any], profile: Any, current: Any, wanted: Dict[str, Any],
                        replace: bool = False) -> bool:
        """Saves exactly the fields that differ from the current settings, through the CLI's core step; with
        `replace` (damaged settings set again from the defaults) every field, so the damaged file is overwritten."""
        from orchestrator.runtime.device import person_steps
        from orchestrator.runtime.device.settings import SettingsError
        from orchestrator.runtime.device.steps import utc_stamp
        changes = dict(wanted) if replace else {k: v for k, v in wanted.items() if getattr(current, k) != v}
        if not changes:
            self._say(self._t("boards.settings.unchanged"))
            return False
        device_id, audit = entry["device_id"], self.audit
        tool, detail = person_steps.SYSTEMD_CONSENT
        try:
            new = person_steps.change_settings(
                self._store(), device_id, profile, current, changes,
                may_use_sudo=lambda: ask(self, self._t("boards.settings.title", label=entry["label"]),
                                         self._t("boards.settings.sudo_question")),
                record_sudo_consent=lambda: person_steps.record_consent(audit, tool, {"device": device_id}, detail,
                                                                       channel="framework-gui"),
                stamp=utc_stamp)
        except SettingsError as e:
            self._info(self._t("boards.settings.bad_value", reason=one_line(str(e))), "warning")
            return False
        except person_steps.BoardStepError as e:
            text = {"no_systemd": self._t("boards.settings.no_systemd", init=e.facts.get("init") or
                                          self._t("boards.settings.no_init")),
                    "sudo_declined": self._t("boards.settings.sudo_declined"),
                    }.get(e.kind) or self._t("boards.settings.not_saved", reason=one_line(str(e.error or e)))
            self._info(text, "warning")
            return False
        except Exception as e:  # noqa: BLE001 - the consent could not be written: nothing was changed
            self._info(self._t("boards.settings.consent_failed", reason=one_line(str(e))), "warning")
            return False
        self._say(self._t("boards.settings.saved", label=entry["label"]) + "\n" + self._settings_text(new, profile))
        self.reload()
        self.select(device_id)
        return True

    # --- plan and status ----------------------------------------------------------------------------------------

    def _on_plan(self) -> None:
        """device runtime plan."""
        from orchestrator.runtime.build.store import BuildStore
        from orchestrator.runtime.device import steps
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        if profile is None:
            return
        state = self.state_dir

        def work():
            plan = steps.plan_runtime(profile)
            return steps.plan_view(plan, BuildStore(state).find(plan))

        self._start(self._t("boards.btn.plan"), work, lambda view: self._show_plan(entry, view),
                    failed=lambda e: self._plan_failed(e))

    def _plan_failed(self, error: BaseException) -> bool:
        from orchestrator.runtime.build.recipe import PlanError
        if isinstance(error, PlanError):
            self._show_text(self._t("boards.plan.none", reason=str(error)), warn=True)
            return True
        return False

    def _show_text(self, text: str, warn: bool = False) -> None:
        self._say(text)
        if warn:
            self._info(text, "warning")

    def _show_plan(self, entry: Dict[str, Any], view: Dict[str, Any]) -> None:
        t = self._t
        target, builder = view["target"], view["builder"]
        cpu = view["cpu_arm_arch"] or t("boards.plan.cpu_all" if view["cpu_all_variants"] else "boards.plan.cpu_x86")
        programs = ", ".join(view["programs"]) + (f"; {', '.join(view['runtime_libs'])}" if view["runtime_libs"]
                                                  else "")
        lines = [t("boards.plan.title", label=entry["label"]),
                 t("boards.plan.text", os=target.get("os"), arch=target.get("arch"), libc=target.get("libc"),
                   libc_version=target.get("libc_version"), base=builder.get("base"), blibc=builder.get("libc"),
                   blibc_version=builder.get("libc_version"), gcc=builder.get("gcc"), cpu=cpu,
                   openmp=view["openmp"], static=t("boards.yes" if view["static_libstdcxx"] else "boards.no"),
                   programs=programs, build_id=view["build_id"]),
                 t("boards.plan.features_used", features=", ".join(view["cpu_features_used"])
                   or t("boards.plan.features_base")),
                 *(t("boards.plan.feature_unusable", feature=u["feature"], reason=u["reason"])
                   for u in view["cpu_features_unusable"]),
                 *view["notes"],
                 t("boards.plan.built", path=view["built_path"]) if view["built"] else t("boards.plan.not_built")]
        self._say("\n".join(lines))

    def _on_status(self) -> None:
        """device runtime status."""
        from orchestrator.runtime.device import steps
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        if profile is None:
            return
        state, device_id = self.state_dir, entry["device_id"]
        self._start(self._t("boards.btn.status"),
                    lambda: steps.runtime_status(state, device_id, steps.plan_runtime(profile)),
                    lambda status: self._show_status(entry, status), failed=self._plan_failed)

    def _show_status(self, entry: Dict[str, Any], status: Dict[str, Any]) -> None:
        t = self._t
        lines = [t("boards.status.title", label=entry["label"]),
                 t("boards.status.planned", build=status["planned_build_id"][:16],
                   built=t("boards.status.built" if status["built"] else "boards.status.not_built"))]
        record = status["deployed"]
        if not record:
            lines.append(t("boards.status.nothing"))
        else:
            report = record.get("verification") or {}
            lines.append(t("boards.status.deployed", build=str(record.get("build_id"))[:16],
                           at=record.get("deployed_at"), install=record.get("install"),
                           checks=t("boards.status.passed" if report.get("ok") else "boards.status.failed"),
                           verified=record.get("verified_at")))
            if record.get("kv_cache"):
                lines.append(t("boards.status.kv", kv=record["kv_cache"]))
            if not status["current"]:
                lines.append(t("boards.status.old"))
        self._say("\n".join(lines))

    # --- build -------------------------------------------------------------------------------------------------------

    def _on_build(self) -> None:
        """device runtime build: jobs and source mirror; the source question; progress; Cancel between steps."""
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        if profile is None:
            return
        dialog, form = _form_dialog(self, self._t("boards.build.title", label=entry["label"]),
                                    self._t("boards.build.intro"))
        jobs = QSpinBox(dialog)
        jobs.setObjectName("spn_boards_jobs")
        jobs.setRange(0, 256)
        jobs.setValue(0)
        mirror = QLineEdit(dialog)
        mirror.setObjectName("txt_boards_mirror")
        mirror.setPlaceholderText(self._t("boards.build.mirror_hint"))
        form.addRow(self._t("boards.build.jobs"), jobs)
        form.addRow(self._t("boards.build.mirror"), mirror)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (int(jobs.value()) or None, mirror.text().strip() or None)
        dialog.deleteLater()
        self.build(entry, profile, *values)

    def build(self, entry: Dict[str, Any], profile: Any, jobs: Optional[int], mirror: Optional[str]) -> None:
        from orchestrator.runtime.build.source import cached_source
        source = self._pin()
        if source is None:
            return
        state = self.state_dir

        def asked(cached: bool) -> None:
            allowed = cached or ask(self, self._t("boards.build.title", label=entry["label"]),
                                    self._t("boards.build.source_question", tag=source.tag,
                                            commit=source.commit[:12]))
            if not allowed:
                self._show_text(self._t("boards.build.source_needed"), warn=True)
                return
            # the source is fetched only after the person's yes: when it was cached at the look, nobody was asked,
            # and a cache that is gone by the time the build runs stops the build (SourceDeclined) instead
            self._run_build(entry, profile, source, jobs, mirror, may_fetch=not cached)

        self._start(self._t("boards.build.checking"), lambda: cached_source(source, state) is not None, asked)

    def _run_build(self, entry: Dict[str, Any], profile: Any, source: Any, jobs: Optional[int],
                   mirror: Optional[str], may_fetch: bool = False) -> None:
        from orchestrator.runtime.device import steps
        state, device_id, label = self.state_dir, entry["device_id"], entry["label"]

        def on_step(kind: str, build: Any) -> None:
            texts = {"fetch": self._t("boards.build.fetching"), "build": self._t("boards.build.building", label=label),
                     "stored": self._t("boards.build.stored", build=build.build_id[:16]) if build else ""}
            if texts.get(kind):
                self._progress(texts[kind])

        def work():
            plan = steps.plan_runtime(profile)
            built = steps.build_runtime(state, device_id, profile, plan, source, jobs=jobs, mirror=mirror,
                                        may_fetch=lambda: may_fetch, on_step=on_step, log=self._progress,
                                        cancelled=self._cancel.is_set,
                                        settings=getattr(self.framework, "config", None))
            from orchestrator.runtime.device.package import DeviceRuntimeStore
            return built, steps.older_package_count(DeviceRuntimeStore(state, device_id), built.build)

        self._start(self._t("boards.build.running"), work, self._built, failed=self._build_failed)

    def _built(self, out: Any) -> None:
        built, older = out
        lines = [self._t("boards.build.done", path=str(built.packaged.path), sha256=built.packaged.sha256)]
        if older:
            lines.append(self._t("boards.build.older", n=older))
        self._say("\n".join(lines))
        self.reload()

    def _build_failed(self, error: BaseException) -> bool:
        from orchestrator.runtime.build.store import BuildFailed
        from orchestrator.runtime.device import steps
        if isinstance(error, steps.BuildCancelled):
            self._say(self._t("boards.build.cancelled"))
            return True
        if isinstance(error, steps.SourceDeclined):
            self._show_text(self._t("boards.build.source_needed"), warn=True)
            return True
        if self._plan_failed(error):
            return True
        text = self._t("boards.build.failed", reason=str(error))
        if isinstance(error, BuildFailed) and error.log:
            text += "\n" + self._t("boards.build.logs", path=str(error.log))
        self._show_text(text, warn=True)
        return True

    # --- older packages on this computer -----------------------------------------------------------------------

    def _on_prune(self) -> None:
        """device runtime prune: the full list through ask_deletion; into the trash or for good, as the CLI. The
        folder is read and the ticked files are deleted in the background (the trash hashes each file it takes); only
        the question runs in the GUI thread."""
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        entry = self._need("stored")
        if entry is None:
            return
        title = self._t("boards.prune.title", label=entry["label"])
        devices = DeviceRuntimeStore(self.state_dir, entry["device_id"])

        def read():
            plan = devices.cleanup_plan()
            self._check_cancel()   # a cancelled look asks nothing
            return plan

        self._start(self._t("boards.prune.reading"), read, lambda plan: self._ask_packages(devices, plan, title))

    def _ask_packages(self, devices: Any, plan: Any, title: str) -> None:
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        if plan is None:
            self._info(self._t("boards.prune.none"))
            return
        accepted = ask_deletion(self, plan, multiple=True, title=title, intro=self._t("boards.prune.intro"),
                                lang=self.lang)
        if accepted is None:
            return
        state, framework, audit = self.state_dir, self.framework, self.audit

        def delete():
            return devices.delete_packages(accepted, state, trash=Trash.for_framework(framework), audit=audit)

        def failed(error: BaseException) -> bool:
            if isinstance(error, PlanChanged):
                self._info(self._t("deletion.changed"), "warning")
                return True
            if isinstance(error, (PermissionError, RuntimeError)):
                self._info(self._t("deletion.failed", reason=one_line(str(error))), "warning")
                return True
            return False

        self._start(self._t("boards.prune.deleting"), delete,
                    lambda result: self._deletion_result(result, accepted.mode, title), failed=failed)

    def _deletion_result(self, result: Any, mode: str, title: str) -> None:
        from orchestrator.Core.deletion import size_text
        key = "deletion.done.trash" if mode == "trash" else "deletion.done.permanent"
        lines = [self._t(key, name=one_line(n)) for n in result.done]
        lines += [self._t("deletion.kept", name=one_line(n), reason=one_line(r)) for n, r in result.failed]
        if mode != "trash" and result.done:
            lines.append(self._t("boards.prune_board.freed", size=size_text(result.freed_bytes)))
        text = "\n".join(lines)
        self._say(text)
        plain_box(self, "information", title, text)
        self.reload()

    # --- older builds on the board --------------------------------------------------------------------------------

    def _board_contact(self, entry: Dict[str, Any], user_override: Optional[str] = None):
        """(host, port, settings, user) for a contact with a stored board, or None after telling why."""
        settings = self._settings(entry)
        if settings is None:
            return None
        user = user_override or settings.ssh_user
        if not user:
            self._info(self._t("boards.need_user"), "warning")
            return None
        address = self._address(entry)
        if address is None:
            return None
        return address[0], address[1], settings, user

    def _on_prune_board(self) -> None:
        """device runtime prune --on-device: the board's list through ask_deletion; nothing goes by itself."""
        from orchestrator.runtime.device import person_steps
        from orchestrator.runtime.device.remote import RemoteShell
        entry = self._need("stored")
        contact = self._board_contact(entry) if entry is not None else None
        if contact is None:
            return
        host, port, _settings, user = contact
        state, device_id = self.state_dir, entry["device_id"]

        def read():
            client, _key = person_steps.connect_stored(state, device_id, host, port, user, None,
                                                       on_client=self._keep_client, cancel=self._cancel,
                                                       on_event=self._board_lines(host))
            try:
                self._check_cancel()
                return person_steps.list_board_builds(RemoteShell(client), state, device_id)
            finally:
                client.close()

        self._start(self._t("boards.prune_board.reading"), read,
                    lambda prune: self._ask_board_builds(entry, contact, prune),
                    failed=lambda e: self._show_error(e, address=host, port=port, user=user,
                                                      device_id=device_id) or True)

    def _ask_board_builds(self, entry: Dict[str, Any], contact: tuple, prune: Any) -> None:
        from orchestrator.gui.deletion_dialog import ask_deletion
        from orchestrator.runtime.device import person_steps
        from orchestrator.runtime.device.remote import RemoteShell
        title = self._t("boards.prune_board.title", label=entry["label"])
        if prune.busy:
            self._say(self._t("boards.prune_board.busy", builds=", ".join(prune.busy)))
        if prune.plan is None:
            self._info(self._t("boards.prune_board.none"))
            return
        accepted = ask_deletion(self, prune.plan, multiple=True, title=title,
                                intro=self._t("boards.prune_board.intro"), lang=self.lang)
        if accepted is None:
            return
        host, port, _settings, user = contact
        state, device_id, audit = self.state_dir, entry["device_id"], self.audit

        def remove():
            client, _key = person_steps.connect_stored(state, device_id, host, port, user, None,
                                                       on_client=self._keep_client, cancel=self._cancel,
                                                       on_event=self._board_lines(host))
            try:
                self._check_cancel()
                return person_steps.remove_board_builds(RemoteShell(client), accepted, prune, device_id, audit)
            finally:
                client.close()

        def failed(error: BaseException) -> bool:
            from orchestrator.Core.deletion import PlanChanged
            if isinstance(error, PlanChanged):
                self._info(self._t("deletion.changed"), "warning")
                return True
            if isinstance(error, (PermissionError, RuntimeError)) and not isinstance(
                    error, person_steps.BoardStepError):
                self._info(self._t("deletion.failed", reason=one_line(str(error))), "warning")
                return True
            self._show_error(error, address=host, port=port, user=user, device_id=device_id)
            return True

        self._start(self._t("boards.prune_board.removing"), remove,
                    lambda result: self._deletion_result(result, accepted.mode, title), failed=failed)

    # --- deploy and verify -----------------------------------------------------------------------------------------

    def _runtime_form(self, title: str, intro: str, user: str, with_password: bool):
        dialog, form = _form_dialog(self, title, intro)
        txt_user = QLineEdit(user or "", dialog)
        txt_user.setObjectName("txt_boards_run_user")
        form.addRow(self._t("boards.deploy.user"), txt_user)
        txt_password = None
        if with_password:
            txt_password = QLineEdit(dialog)
            txt_password.setObjectName("txt_boards_password")
            txt_password.setEchoMode(QLineEdit.Password)   # never shown, never stored
            txt_password.setPlaceholderText(self._t("boards.deploy.password_hint"))
            form.addRow(self._t("boards.deploy.password"), txt_password)
        txt_model = QLineEdit(dialog)
        txt_model.setObjectName("txt_boards_bench_model")
        txt_model.setPlaceholderText(self._t("boards.bench.hint"))
        form.addRow(self._t("boards.bench.model"), txt_model)
        return dialog, txt_user, txt_password, txt_model

    def _on_deploy(self) -> None:
        """device runtime deploy: user, the password asked each time (never stored), a benchmark model and, only
        after an explicit warning, an untrusted one."""
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        settings = self._settings(entry) if profile is not None else None
        if settings is None:
            return
        dialog, user, password, model = self._runtime_form(self._t("boards.deploy.title", label=entry["label"]),
                                                           self._t("boards.deploy.intro"), settings.ssh_user, True)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (user.text().strip() or None, password.text() or None, model.text().strip() or None)
        password.clear()
        dialog.deleteLater()
        self.deploy(entry, profile, *values)

    def deploy(self, entry: Dict[str, Any], profile: Any, user: Optional[str], password: Optional[str],
               bench_model: Optional[str]) -> None:
        from orchestrator.runtime.device import person_steps, steps
        from orchestrator.runtime.device.settings import SettingsError
        settings = self._settings(entry)
        if settings is None:
            return
        try:
            settings = settings.changed(ssh_user=user) if user else settings
        except SettingsError as e:
            self._info(self._t("boards.settings.bad_value", reason=one_line(str(e))), "warning")
            return
        if settings.ssh_user is None:
            self._info(self._t("boards.need_user"), "warning")
            return
        problems = settings.problems(profile)
        if problems:
            self._info("\n".join([self._t("boards.deploy.problems"), *[f"- {p}" for p in problems]]), "warning")
            return
        address = self._address(entry)
        source = self._pin() if address is not None else None
        if source is None:
            return
        host, port = address
        state, device_id, audit = self.state_dir, entry["device_id"], self.audit
        kv = settings.kv_choice(profile)
        tool, detail = person_steps.UNTRUSTED_CONSENT

        def prepare():
            # the stored build is checked file by file and its package hashed: off the GUI thread, and before the
            # question about the benchmark model, so a board without a build is refused first
            try:
                return steps.stored_runtime(state, device_id, profile, lambda: source)
            except steps.NotBuilt:
                raise _Refusal(self._t("boards.deploy.not_built")) from None
            except steps.PackageNeedsSource:
                raise _Refusal(self._t("boards.deploy.needs_source")) from None

        def prepared(found: Any) -> None:
            self._start(self._t("boards.deploy.running"),
                        lambda: work(found),
                        lambda out: self._deployed(entry, profile, settings, kv, out),
                        failed=lambda e: self._runtime_failed(e, host, port, settings.ssh_user, device_id))

        def work(found: Any):
            # the one flow of the device runtime's deploy (steps.deploy_runtime_flow, also the CLI's and the AI's),
            # with this window's login, the board's trust record, the CLI's question about an untrusted benchmark
            # model (asked before anything changes on the board, decision D23) and its Cancel
            hooks = steps.DeployHooks(
                open_board=lambda: person_steps.connect_stored(state, device_id, host, port, settings.ssh_user,
                                                               password, step="runtime_deploy",
                                                               on_client=self._keep_client, cancel=self._cancel,
                                                               on_event=self._board_lines(host)),
                remember=lambda key: person_steps.remember_board(state, host, port, settings.ssh_user, key, via="gui",
                                                                 audit=audit, actor="gui"),
                choose_bench=lambda shell, home: steps.choose_bench_model(
                    shell, home, bench_model, may_run_untrusted=self._may_run_untrusted(settings.ssh_user),
                    record_consent=lambda digest: person_steps.record_consent(
                        audit, tool, {"device": device_id, "model_sha256": digest}, detail, channel="framework-gui")),
                tested_on=lambda report, digest, build_id, kind: self._record_tested_on(entry, profile, report, digest,
                                                                                        build_id, source, kind),
                progress=self._progress, check_cancel=self._check_cancel)
            try:
                flow = steps.deploy_runtime_flow(state, device_id, profile, settings, hooks=hooks, build=found.build,
                                                 packaged=found.packaged, source=source,
                                                 arch=str(found.plan.target["arch"]))
            except steps.RootUser:
                raise _Refusal(self._t("boards.deploy.root")) from None
            older = steps.older_package_count(found.devices, found.build)
            return found.build, flow.bench, flow.outcome, older, flow.tested

        self._start(self._t("boards.deploy.preparing"), prepare, prepared,
                    failed=lambda e: self._runtime_failed(e, host, port, settings.ssh_user, device_id))

    def _deployed(self, entry: Dict[str, Any], profile: Any, settings: Any, kv: Any, out: Any) -> None:
        build, bench, outcome, older, tested = out
        t, result = self._t, outcome.result
        lines = []
        if bench.missing:
            lines.append(t("boards.bench.missing", path=bench.missing))
        if not outcome.ok:
            lines.append(t("boards.deploy.failed", build=build.build_id[:16]))
            lines += self._check_lines(outcome.report)
            lines += [one_line(n) for n in result.notes]
            self._show_text("\n".join(lines), warn=True)
            self.reload()
            return
        lines.append(t("boards.deploy.done", build=build.build_id[:16], root=result.root))
        if result.previous:
            lines.append(t("boards.deploy.previous", previous=result.previous))
        lines += self._check_lines(outcome.report)
        lines += self._tested_on_lines(entry, profile, tested)
        lines += [one_line(n) for n in result.notes]
        if older:
            lines.append(t("boards.build.older", n=older))
        lines.append(t("boards.kv", kv=self._kv_text(kv)))
        # where the server listens and where its key is, as the CLI says it (the note above names the start command)
        if settings.install == "systemd":
            started = getattr(result, "unit_started", None)
            lines.append(t("boards.deploy.server_systemd", port=settings.port,
                           state=t("boards.deploy.unit_started" if started else "boards.deploy.unit_enabled")))
        else:
            lines.append(t("boards.deploy.server_user", port=settings.port))
        self._say("\n".join(lines))
        self.reload()

    def _runtime_failed(self, error: BaseException, host: str, port: int, user: str, device_id: str) -> bool:
        if isinstance(error, _Refusal):
            self._show_text(str(error), warn=True)
            return True
        if self._plan_failed(error):
            return True
        self._show_error(error, address=host, port=port, user=user, device_id=device_id)
        return True

    def _on_verify(self) -> None:
        """device runtime verify: also without a benchmark model, with a user override."""
        entry = self._need("stored")
        profile = self._profile(entry) if entry is not None else None
        settings = self._settings(entry) if profile is not None else None
        if settings is None:
            return
        dialog, user, _password, model = self._runtime_form(self._t("boards.verify.title", label=entry["label"]),
                                                            self._t("boards.verify.intro"), settings.ssh_user, False)
        if not _exec(dialog):
            dialog.deleteLater()
            return
        values = (user.text().strip() or None, model.text().strip() or None)
        dialog.deleteLater()
        self.verify(entry, profile, *values)

    def verify(self, entry: Dict[str, Any], profile: Any, user: Optional[str], bench_model: Optional[str]) -> None:
        from orchestrator.runtime.device import person_steps, steps
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        from orchestrator.runtime.device.settings import SettingsError
        settings = self._settings(entry)
        if settings is None:
            return
        try:
            user = settings.changed(ssh_user=user).ssh_user if user else settings.ssh_user
        except SettingsError as e:
            self._info(self._t("boards.settings.bad_value", reason=one_line(str(e))), "warning")
            return
        if user is None:
            self._info(self._t("boards.need_user"), "warning")
            return
        state, device_id, audit = self.state_dir, entry["device_id"], self.audit
        record = DeviceRuntimeStore(state, device_id).record()
        if not record:
            self._info(self._t("boards.verify.nothing"), "warning")
            return
        address = self._address(entry)
        source = self._pin() if address is not None else None
        if source is None:
            return
        host, port = address
        tool, detail = person_steps.UNTRUSTED_CONSENT

        def work():
            plan_verify = steps.deployed_verify_plan(state, record, lambda: steps.plan_runtime(profile))
            client, _key = person_steps.connect_stored(state, device_id, host, port, user, None, step="verify",
                                                       on_client=self._keep_client, cancel=self._cancel,
                                                       on_event=self._board_lines(host))
            try:   # a verify only checks: it never rewrites the board's trust record
                from orchestrator.runtime.device.remote import RemoteShell, read_facts
                shell = RemoteShell(client)
                facts = read_facts(shell)
                if facts.uid == 0:
                    raise _Refusal(self._t("boards.verify.root"))
                bench = steps.choose_bench_model(
                    shell, facts.home, bench_model, may_run_untrusted=self._may_run_untrusted(user),
                    record_consent=lambda digest: person_steps.record_consent(
                        audit, tool, {"device": device_id, "model_sha256": digest}, detail, channel="framework-gui"))
                self._check_cancel()
                report = steps.check_runtime(shell, state_dir=state, device_id=device_id, home=facts.home,
                                             record=record, plan_verify=plan_verify, source=source,
                                             port=settings.port, bench=bench)
            finally:
                client.close()
            tested = self._record_tested_on(entry, profile, report, bench.digest if bench.allowed else None,
                                            record["build_id"], source, settings.kv_choice(profile).kind)
            return bench, report, tested

        def done(out: Any) -> None:
            bench, report, tested = out
            lines = [self._t("boards.bench.missing", path=bench.missing)] if bench.missing else []
            lines.append(self._t("boards.verify.result", device_id=device_id, build=record["build_id"][:16]))
            lines += self._check_lines(report)
            lines += self._tested_on_lines(entry, profile, tested)
            self._show_text("\n".join(lines), warn=not report.ok)
            self.reload()

        self._start(self._t("boards.verify.running"), work, done,
                    failed=lambda e: self._runtime_failed(e, host, port, user, device_id))

    # --- Mojo's check of this board ---------------------------------------------------------------------------------

    def _on_check(self) -> None:
        """'Check this board (Mojo)': Mojo's board check for exactly this board (W1, W1'): one connection to its SSH
        port, no login. A thin call of the same check as `llmcf mojo --oracle --board`."""
        entry = self._need("trusted")
        if entry is not None:
            self.check_board(entry["device_id"])

    def check_board(self, device_id: str) -> None:
        """One core call and one text for every 'Check this board' of the GUI: Core/mojo/boards.check_board (the CLI's
        `mojo --oracle --board` check) and mojo_window.board_check_text, as in the Mojo window and the Deployment
        tab."""
        from orchestrator.Core.mojo.boards import check_board
        framework = self.framework
        label = next((e.get("label") for e in self.entries if e.get("device_id") == device_id), "") or device_id

        def failed(error: BaseException) -> bool:
            self._checked(label, None, str(error) or type(error).__name__)
            return True

        self._start(self._t("boards.check.running"), lambda: check_board(framework, device_id),
                    lambda finding: self._checked(label, finding), failed=failed)

    def _checked(self, label: str, finding: Any, problem: str = "") -> None:
        from orchestrator.gui.mojo_window import board_check_text
        severity = getattr(getattr(finding, "severity", None), "value", "")
        self._show_text(board_check_text(finding, label, problem, self.lang),
                        warn=bool(problem) or severity not in ("ok", "info"))

    # --- closing ----------------------------------------------------------------------------------------------------

    def reject(self) -> None:
        """Closing the window cancels a running step (its SSH session closes); the window is kept for next time."""
        if self._token is not None:
            self._on_cancel()
        super().reject()


class _Refusal(Exception):
    """A step the board's state refuses (not built, root, ...): its text is the person's message."""


def open_boards(window: Any) -> Optional[BoardsWindow]:
    """Boards > Boards and Devices...: shows the window of `window` (the main window), with fresh data."""
    dialog = getattr(window, "boards_window", None)
    if dialog is None:
        dialog = BoardsWindow(window.framework, window)
        window.boards_window = dialog
    try:
        dialog.reload()
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text
        plain_box(window, "critical", ui_text("boards.title"), str(e))
        return None
    dialog.show()
    dialog.raise_()
    return dialog


__all__ = ["BUTTONS", "BoardsWindow", "COLUMNS", "open_boards"]
