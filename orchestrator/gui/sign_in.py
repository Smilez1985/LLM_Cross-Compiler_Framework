#!/usr/bin/env python3
"""
Sign-in in the GUI (AI settings > Sign in...): SignInPanel shows a provider's methods as cards in the catalog's
order, the first one this version can sign in with marked "Recommended", and one form per type (key form with
Check, Save and Sign out; no sign-in; the `ant` CLI profile; the managed local server; methods that come later).
The status line says "Signed in as ...", "Not signed in", "Using key from environment variable X", ...

When no keyring is usable, StorageChooser offers the explicit choices (passphrase, credential helper, an
unencrypted key file with a confirmation); nothing is picked automatically. Every vault, keyring or network call
runs in a worker thread (orchestrator/llm/auth/signin.py does the work); the worker never holds a widget.
"""

import threading
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from orchestrator.gui.worker_relay import worker_relay
from orchestrator.llm.auth.signin import STORAGE, SignIn, method_infos, plain_http_to_remote, status_line
from orchestrator.llm.auth.spec import KEY_TYPES, TYPE_LABELS
from orchestrator.llm.auth.store import display_origin
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.localization import get_instance as get_i18n
from orchestrator.utils.logging import get_logger

logger = get_logger("SignIn")

# Storage problems another protection solves (the panel then shows the chooser).
STORAGE_CHOICE_CODES = ("storage.keyring_unavailable", "storage.passphrase_needed", "storage.helper_failed",
                        "storage.unavailable")
OK_STYLE, ERROR_STYLE, MUTED_STYLE = "color: #4caf50;", "color: #f44336;", "color: #888;"


def _t(key: str, default: str, **values: Any) -> str:
    i18n = get_i18n()
    text = i18n.t(key, default) if i18n else default
    return text.format(**values) if values else text


def _translate(key: str, default: str) -> str:
    return _t(key, default)


class Worker(QObject):
    """Runs blocking calls off the GUI thread and hands the result to a callback on it. The work function must
    not reference a widget: a widget released in a worker thread would be destroyed there. The thread holds no Qt
    object of its own either: it answers on the relay, which lives as long as the application (worker_relay.py)."""

    def __init__(self, on_busy: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self._on_busy = on_busy
        self._pending: Dict[object, Callable[[Any, Optional[BaseException]], None]] = {}  # by task token
        self.threads = []
        worker_relay().done.connect(self._finish, Qt.QueuedConnection)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def run(self, name: str, work: Callable[[], Any], done: Callable[[Any, Optional[BaseException]], None]) -> None:
        token, relay = object(), worker_relay()
        self._pending[token] = done
        self._on_busy(True)

        def target():
            try:
                result, error = work(), None
            except Exception as e:  # shown to the user; LLM errors are already redacted
                result, error = None, e
            relay.done.emit(token, (result, error))

        thread = threading.Thread(target=target, name=f"sign-in-{name}", daemon=True)
        self.threads = [t for t in self.threads if t.is_alive()] + [thread]
        thread.start()

    def _finish(self, token: object, outcome) -> None:
        if token not in self._pending:
            return  # another worker's task
        done = self._pending.pop(token)
        if not self._pending:
            self._on_busy(False)
        result, error = outcome
        done(result, error)


def error_text(error: BaseException) -> str:
    text = redact_secrets(str(error)).strip()
    return text or type(error).__name__


def field_example(pattern: Optional[str]) -> str:
    """"wrkspc_..." for ^wrkspc_[A-Za-z0-9]+$: the literal start of a field's pattern as a placeholder."""
    if not pattern:
        return ""
    body = pattern[1:] if pattern.startswith("^") else pattern
    prefix = ""
    for char in body:
        if not (char.isalnum() or char in "_-"):
            break
        prefix += char
    return f"{prefix}..." if prefix else ""


class MethodCard(QFrame):
    """One sign-in method: label, type and badges ("Recommended", "Advanced", "Not available")."""

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.spec = info.spec
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.radio = QRadioButton(info.spec.label)
        self.radio.setEnabled(info.built)
        layout.addWidget(self.radio)
        badges = [_t(f"signin.type.{info.spec.type}", TYPE_LABELS.get(info.spec.type, info.spec.type))]
        if info.recommended:
            badges.append(_t("signin.recommended", "Recommended"))
        if info.spec.opt_in:
            badges.append(_t("signin.advanced", "Advanced: only when chosen"))
        if not info.built:
            badges.append(_t("signin.not_built", "Not available in this version"))
        self.badges = QLabel(" · ".join(badges))
        self.badges.setStyleSheet(MUTED_STYLE)
        layout.addWidget(self.badges)
        self.recommended = info.recommended
        self.built = info.built

    def mousePressEvent(self, event):
        if self.built:
            self.radio.setChecked(True)
        super().mousePressEvent(event)


class KeyForm(QWidget):
    """api_key and bearer_optional: key page, password field, the catalog's extra fields, Check, Save, Sign out."""

    def __init__(self, preset, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QVBoxLayout(self)
        hint = spec.where
        self.lbl_where = QLabel(_t("signin.key_where", "Where to find it: {where}", where=hint) if hint else "")
        self.lbl_where.setWordWrap(True)
        self.lbl_where.setVisible(bool(hint))
        layout.addWidget(self.lbl_where)
        self.btn_key_page = QPushButton(_t("signin.open_key_page", "Open key page"))
        self.btn_key_page.setVisible(bool(spec.key_page))
        self.btn_key_page.setToolTip(spec.key_page or "")
        self.btn_key_page.clicked.connect(self.open_key_page)
        layout.addWidget(self.btn_key_page, alignment=Qt.AlignLeft)
        form = QFormLayout()
        self.txt_key = QLineEdit()
        self.txt_key.setEchoMode(QLineEdit.Password)
        self.optional = spec.type == "bearer_optional"
        self.set_stored(None)
        form.addRow(_t("signin.key", "API key:"), self.txt_key)
        self.fields: Dict[str, QLineEdit] = {}
        for item in spec.fields:
            edit = QLineEdit()
            edit.setPlaceholderText(field_example(item.pattern))
            edit.setToolTip(item.label)
            form.addRow(item.label + ":", edit)
            self.fields[item.id] = edit
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.btn_check = QPushButton(_t("signin.check", "Check"))
        self.btn_save = QPushButton(_t("signin.save", "Save"))
        self.btn_sign_out = QPushButton(_t("signin.sign_out", "Sign out"))
        for button in (self.btn_check, self.btn_save, self.btn_sign_out):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def open_key_page(self):
        if self.spec.key_page:  # an https URL from the catalog
            QDesktopServices.openUrl(QUrl(self.spec.key_page))

    def set_stored(self, last4: Optional[str]) -> None:
        if last4:
            self.txt_key.setPlaceholderText(_t("signin.key_stored", "Stored key ...{last4} (paste a new one to "
                                                                    "replace it)", last4=last4))
        elif self.optional:
            self.txt_key.setPlaceholderText(_t("signin.key_optional", "Optional key (empty: no key)"))
        else:
            self.txt_key.setPlaceholderText(_t("signin.key_placeholder", "Paste the key"))

    def key(self) -> str:
        return self.txt_key.text().strip()

    def field_values(self) -> Dict[str, Optional[str]]:
        return {fid: (edit.text().strip() or None) for fid, edit in self.fields.items()}

    def fill_fields(self, values: Dict[str, str]) -> None:
        for fid, edit in self.fields.items():
            if not edit.isModified():
                edit.setText(values.get(fid, ""))

    def buttons(self):
        return (self.btn_check, self.btn_save, self.btn_sign_out)


class NoneForm(QWidget):
    """none: no sign-in; Check lists the models, the method can be chosen explicitly."""

    def __init__(self, preset, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QVBoxLayout(self)
        info = QLabel(_t("signin.none_info", "This server needs no sign-in. Check lists its models."))
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        self.btn_check = QPushButton(_t("signin.check", "Check"))
        self.btn_use = QPushButton(_t("signin.use_method", "Use this method"))
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_use)
        row.addStretch(1)
        layout.addLayout(row)

    def buttons(self):
        return (self.btn_check, self.btn_use)


class ManagedForm(QWidget):
    """The managed local server: its per-start key is passed automatically."""

    def __init__(self, preset, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QVBoxLayout(self)
        info = QLabel(_t("signin.managed_info", "The managed local server gets a new key at every start; the "
                                                "framework passes it and never shows it."))
        info.setWordWrap(True)
        layout.addWidget(info)
        self.btn_check = QPushButton(_t("signin.check", "Check"))
        layout.addWidget(self.btn_check, alignment=Qt.AlignLeft)

    def buttons(self):
        return (self.btn_check,)


class CliSessionForm(QWidget):
    """cli_session (the `ant` profile): the official command to run, the profile name, Check, Use, Stop using.
    The framework never starts the CLI or reads its files; the SDK checks the profile."""

    def __init__(self, preset, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QVBoxLayout(self)
        if spec.note:
            note = QLabel(spec.note)
            note.setWordWrap(True)
            note.setStyleSheet("color: #ff9800;")
            layout.addWidget(note)
        layout.addWidget(QLabel(_t("signin.cli_info", "Uses the profile of the official command-line tool. Sign in "
                                                      "there first:")))
        self.command = " ".join(spec.cli.login) if spec.cli and spec.cli.login else ""
        row = QHBoxLayout()
        self.lbl_command = QLabel(self.command)
        self.lbl_command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_command.setStyleSheet("font-family: monospace;")
        row.addWidget(self.lbl_command)
        self.btn_copy = QPushButton(_t("signin.copy_command", "Copy command"))
        self.btn_copy.clicked.connect(self.copy_command)
        row.addWidget(self.btn_copy)
        self.btn_install = QPushButton(_t("signin.install_cli", "Install the CLI"))
        self.btn_install.setVisible(bool(spec.cli and spec.cli.install))
        self.btn_install.clicked.connect(self.open_install)
        row.addWidget(self.btn_install)
        row.addStretch(1)
        layout.addLayout(row)
        form = QFormLayout()
        self.txt_profile = QLineEdit()
        self.txt_profile.setPlaceholderText(_t("signin.profile_placeholder", "empty: the active profile"))
        form.addRow(_t("signin.profile", "Profile:"), self.txt_profile)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.btn_check = QPushButton(_t("signin.check", "Check"))
        self.btn_use = QPushButton(_t("signin.use_profile", "Use this profile"))
        self.btn_sign_out = QPushButton(_t("signin.stop_using", "Stop using"))
        for button in (self.btn_check, self.btn_use, self.btn_sign_out):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def copy_command(self):
        QGuiApplication.clipboard().setText(self.command)

    def open_install(self):
        if self.spec.cli and self.spec.cli.install:
            QDesktopServices.openUrl(QUrl(self.spec.cli.install))

    def profile(self) -> Optional[str]:
        return self.txt_profile.text().strip() or None

    def buttons(self):
        return (self.btn_check, self.btn_use, self.btn_sign_out)


class LaterForm(QWidget):
    """A type without a sign-in in this version (OAuth flows, cloud credentials come later)."""

    def __init__(self, preset, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_t("signin.later_info", "This sign-in method comes in a later version.")))

    def buttons(self):
        return ()


class StorageChooser(QGroupBox):
    """The explicit choices when no keyring is usable. Emits chosen(mode, helper); ephemeral is for CI only."""

    chosen = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(_t("signin.storage.title", "Choose how to protect your credentials"), parent)
        layout = QVBoxLayout(self)
        self.lbl_problem = QLabel("")
        self.lbl_problem.setWordWrap(True)
        self.lbl_problem.setTextFormat(Qt.PlainText)
        layout.addWidget(self.lbl_problem)
        self.group = QButtonGroup(self)
        self.rb_passphrase = QRadioButton(_t("signin.storage.passphrase", "Passphrase, asked once per start"))
        self.rb_helper = QRadioButton(_t("signin.storage.helper", "Credential helper command, e.g. pass show "
                                                                  "llmcc/vault-key"))
        self.rb_insecure = QRadioButton(_t("signin.storage.insecure", "Key file next to the vault (NOT encrypted "
                                                                      "at rest)"))
        self.rb_insecure.setStyleSheet(ERROR_STYLE)
        self.txt_helper = QLineEdit()
        self.txt_helper.setPlaceholderText(_t("signin.storage.helper_placeholder", "command that prints the vault "
                                                                                   "key"))
        self.chk_insecure = QCheckBox(_t("signin.storage.insecure_ack", "I understand that this key file is not "
                                                                        "encrypted"))
        for button in (self.rb_passphrase, self.rb_helper, self.rb_insecure):
            self.group.addButton(button)
        layout.addWidget(self.rb_passphrase)
        layout.addWidget(self.rb_helper)
        layout.addWidget(self.txt_helper)
        layout.addWidget(self.rb_insecure)
        layout.addWidget(self.chk_insecure)
        self.btn_use = QPushButton(_t("signin.storage.use", "Use this storage"))
        self.btn_use.clicked.connect(self._choose)
        layout.addWidget(self.btn_use, alignment=Qt.AlignLeft)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setTextFormat(Qt.PlainText)
        layout.addWidget(self.lbl_result)

    def show_problem(self, text: str) -> None:
        self.lbl_problem.setText(text)

    def _choose(self):
        if self.rb_passphrase.isChecked():
            self.chosen.emit("passphrase", "")
        elif self.rb_helper.isChecked():
            command = self.txt_helper.text().strip()
            if not command:
                self.lbl_result.setText(_t("signin.storage.helper_needed", "Enter the helper command."))
                return
            self.chosen.emit("helper", command)
        elif self.rb_insecure.isChecked():
            if not self.chk_insecure.isChecked():
                self.lbl_result.setText(_t("signin.storage.ack_needed", "Confirm that the key file is not "
                                                                        "encrypted."))
                return
            self.chosen.emit("insecure", "")


class StorageChooserDialog(QDialog):
    """At startup without a usable keyring: the explicit choices; the caller saves the choice and starts again."""

    def __init__(self, problem: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("signin.storage.title", "Choose how to protect your credentials"))
        self.mode: Optional[str] = None
        self.helper: Optional[str] = None
        layout = QVBoxLayout(self)
        self.chooser = StorageChooser(self)
        self.chooser.show_problem(problem)
        self.chooser.chosen.connect(self._chosen)
        layout.addWidget(self.chooser)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _chosen(self, mode: str, helper: str):
        self.mode, self.helper = mode, helper or None
        self.accept()


class UserConfigSnapshot:
    """config.yml before a storage choice at startup. The choice is saved so that the framework can start again
    with it; when no choice opens the vault (an existing vault protected another way), the file is put back, so a
    failed choice never keeps the next start from working."""

    def __init__(self, config):
        folder = getattr(config, "config_dir", None)
        self.path = Path(folder) / "config.yml" if folder else None
        self.content = self.path.read_bytes() if self.path is not None and self.path.is_file() else None

    def restore(self) -> None:
        if self.path is None:
            return
        try:
            if self.content is None:
                self.path.unlink(missing_ok=True)
            else:
                self.path.write_bytes(self.content)
        except OSError as e:
            logger.warning(f"The user config {self.path} could not be put back: {type(e).__name__}")


def choose_storage_at_start(parent, config, problem: BaseException) -> bool:
    """Shows the chooser for a vault that cannot be opened; saves secret_storage (and secret_helper) on a choice."""
    dlg = StorageChooserDialog(error_text(problem), parent)
    if not dlg.exec() or not dlg.mode:
        return False
    config.set("secret_storage", dlg.mode)
    if dlg.helper:
        config.set("secret_helper", dlg.helper)
    save = getattr(config, "save_user_config", None)
    return not (callable(save) and save() is False)


class SignInPanel(QWidget):
    """The sign-in of one provider. `base_url` is the address typed in the AI settings (presets whose address the
    user sets); `service` is a SignIn (tests pass one)."""

    changed = Signal()  # a credential, method or storage was stored or removed

    def __init__(self, framework, preset, *, base_url: Optional[str] = None, service: Optional[SignIn] = None,
                 parent=None):
        super().__init__(parent)
        self.framework = framework
        self.preset = preset
        self.base_url = base_url or None
        self._service = service
        self.status = None
        self._status_request = 0
        self._http_confirmed = set()  # origins the user agreed to send a key to over plain http
        self.worker = Worker(self._set_busy, self)
        self.forms: Dict[str, QWidget] = {}
        self.cards: Dict[str, MethodCard] = {}

        layout = QVBoxLayout(self)
        title = QLabel(_t("signin.title", "Sign in to {provider}", provider=preset.label))
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)
        self.lbl_status = QLabel(_t("signin.status.loading", "Reading the sign-in..."))
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.lbl_status)

        self.storage = StorageChooser(self)
        self.storage.setVisible(False)
        self.storage.chosen.connect(self.choose_storage)
        layout.addWidget(self.storage)

        cards_box = QGroupBox(_t("signin.methods", "Sign-in methods"))
        cards_layout = QHBoxLayout(cards_box)
        self.card_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        for info in method_infos(preset):
            card = MethodCard(info)
            self.cards[info.spec.id] = card
            self.card_group.addButton(card.radio)
            card.radio.toggled.connect(partial(self._card_toggled, info.spec.id))
            cards_layout.addWidget(card)
            form = self._make_form(info)
            self.forms[info.spec.id] = form
            self.stack.addWidget(form)
        layout.addWidget(cards_box)
        layout.addWidget(self.stack)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setTextFormat(Qt.PlainText)
        self.lbl_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_result)
        layout.addStretch(1)

        first = next((c for c in self.cards.values() if c.recommended), None) \
            or next((c for c in self.cards.values() if c.built), None)
        if first is not None:
            first.radio.setChecked(True)
        self.refresh()

    # --- building -------------------------------------------------------------------------------------------
    def _make_form(self, info) -> QWidget:
        spec = info.spec
        if not info.built:
            return LaterForm(self.preset, spec)
        if self.preset.runtime_endpoint:
            form = ManagedForm(self.preset, spec)
            form.btn_check.clicked.connect(partial(self.check, spec.id))
            return form
        if spec.type in KEY_TYPES:
            form = KeyForm(self.preset, spec)
            form.btn_check.clicked.connect(partial(self.check, spec.id))
            form.btn_save.clicked.connect(partial(self.save, spec.id))
            form.btn_sign_out.clicked.connect(partial(self.sign_out, spec.id))
            return form
        if spec.type == "cli_session":
            form = CliSessionForm(self.preset, spec)
            form.btn_check.clicked.connect(partial(self.check, spec.id))
            form.btn_use.clicked.connect(partial(self.use, spec.id))
            form.btn_sign_out.clicked.connect(partial(self.sign_out, spec.id))
            return form
        form = NoneForm(self.preset, spec)
        form.btn_check.clicked.connect(partial(self.check, spec.id))
        form.btn_use.clicked.connect(partial(self.use, spec.id))
        return form

    def service(self) -> SignIn:
        if self._service is None:
            self._service = SignIn(getattr(self.framework, "config", None),
                                   getattr(self.framework, "secrets_manager", None))
        return self._service

    # --- state ----------------------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.worker.busy

    def current_method(self) -> Optional[str]:
        return next((mid for mid, card in self.cards.items() if card.radio.isChecked()), None)

    def select_method(self, method_id: str) -> None:
        card = self.cards.get(method_id)
        if card is not None and card.built:
            card.radio.setChecked(True)

    def _card_toggled(self, method_id: str, checked: bool):
        if checked:
            self.stack.setCurrentWidget(self.forms[method_id])
            self.lbl_result.setText("")

    def _set_busy(self, busy: bool):
        for form in self.forms.values():
            for button in form.buttons():
                button.setEnabled(not busy)
        self.storage.btn_use.setEnabled(not busy)

    def _show(self, text: str, ok: Optional[bool] = None):
        self.lbl_result.setStyleSheet(OK_STYLE if ok else ERROR_STYLE if ok is False else "")
        self.lbl_result.setText(text)

    def _spec(self, method_id: str):
        return self.preset.method(method_id)

    def refresh(self) -> None:
        """Reads the sign-in state (vault and settings) in a worker; the status line follows."""
        service, preset, base_url = self.service(), self.preset, self.base_url
        self._status_request += 1
        self.worker.run("status", lambda: (service.status(preset, base_url), service.storage_problem(preset.id)),
                        partial(self._on_status, self._status_request))

    def _on_status(self, request: int, result, error):
        if request != self._status_request:
            return  # an older read that finished late (the vault was slow): the newer one is shown
        if error is not None:
            self.lbl_status.setText(error_text(error))
            return
        status, problem = result
        self.status = status
        self.lbl_status.setText(status_line(status, _translate))
        self.lbl_status.setStyleSheet(ERROR_STYLE if status.state == STORAGE else "")
        choose = problem is not None and getattr(problem, "code", None) in STORAGE_CHOICE_CODES
        self.storage.setVisible(choose)
        if choose:
            self.storage.show_problem(error_text(problem))
        for method_id, form in self.forms.items():
            if isinstance(form, KeyForm):
                same = status.method == method_id
                form.set_stored(status.last4 if same and status.source == "vault" else None)
                if same:
                    form.fill_fields(status.fields)
            elif isinstance(form, CliSessionForm) and status.method == method_id and status.profile:
                if not form.txt_profile.isModified():
                    form.txt_profile.setText(status.profile)
        if status.method in self.cards and status.chosen:
            self.select_method(status.method)

    # --- actions --------------------------------------------------------------------------------------------
    def _plain_http_confirmed(self) -> bool:
        """A typed key would travel unencrypted to another computer (http:// to a host that is not this one): only
        after the user agrees, once per address (the CLI's --yes). Settings only, no vault or network."""
        try:
            url = self.service().endpoint(self.preset, self.base_url)
        except Exception:  # a bad or missing address: the worker reports it
            return True
        origin = display_origin(url)
        if not plain_http_to_remote(url) or origin in self._http_confirmed:
            return True
        answer = QMessageBox.question(
            self, _t("signin.plain_http.title", "Unencrypted connection"),
            _t("signin.plain_http", "{origin} uses plain http://, so the key travels unencrypted to another "
                                    "computer. Send it anyway?", origin=origin),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            self._show(_t("signin.cancelled", "Cancelled: the key was not sent."), False)
            return False
        self._http_confirmed.add(origin)
        return True

    def check(self, method_id: str) -> None:
        spec, form = self._spec(method_id), self.forms[method_id]
        service, preset, base_url = self.service(), self.preset, self.base_url
        kwargs: Dict[str, Any] = {}
        if isinstance(form, KeyForm):
            if form.key() and not self._plain_http_confirmed():
                return
            kwargs = {"key": form.key() or None, "fields": form.field_values() or None}
        elif isinstance(form, CliSessionForm):
            kwargs = {"profile": form.profile() or ""}
        self._show(_t("signin.checking", "Checking..."))
        self.worker.run("check", partial(service.check, preset, spec, base_url, **kwargs), self._on_checked)

    def _on_checked(self, result, error):
        if error is not None:
            self._show(error_text(error), False)
        else:
            self._show(result.message, result.ok)

    def save(self, method_id: str) -> None:
        spec, form = self._spec(method_id), self.forms[method_id]
        service, preset, base_url = self.service(), self.preset, self.base_url
        key, fields = form.key(), form.field_values()
        signed_in = self.status is not None and self.status.method == method_id and self.status.state == "signed_in"
        if key:
            if not self._plain_http_confirmed():
                return
            work = partial(service.save_key, preset, spec, key, base_url, fields=fields)
        elif signed_in and fields:
            work = partial(service.save_fields, preset, spec, fields, base_url)
        elif spec.type == "bearer_optional":
            work = partial(service.use_method, preset, spec, base_url)
        else:
            self._show(_t("signin.key_needed", "Paste the key first."), False)
            return
        self._show(_t("signin.working", "Working..."))
        self.worker.run("save", work, partial(self._on_saved, method_id))

    def _on_saved(self, method_id: str, result, error):
        if error is not None:
            self._show(error_text(error), False)
            return
        form = self.forms[method_id]
        if isinstance(form, KeyForm):
            form.txt_key.clear()
        warnings = list(getattr(result, "warnings", []) or [])
        self._show(" ".join([_t("signin.saved", "Saved."), *warnings]), True)
        self.changed.emit()
        self.refresh()

    def use(self, method_id: str) -> None:
        spec, form = self._spec(method_id), self.forms[method_id]
        service, preset, base_url = self.service(), self.preset, self.base_url
        profile = form.profile() if isinstance(form, CliSessionForm) else None
        self._show(_t("signin.working", "Working..."))
        self.worker.run("use", partial(service.use_method, preset, spec, base_url, profile=profile),
                        partial(self._on_saved, method_id))

    def sign_out(self, method_id: str, confirm: bool = True) -> None:
        spec = self._spec(method_id)
        if confirm and QMessageBox.question(
                self, _t("signin.sign_out", "Sign out"),
                _t("signin.confirm_sign_out", "Delete the stored sign-in of {provider} from this computer?",
                   provider=self.preset.label), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        service, preset, base_url = self.service(), self.preset, self.base_url
        self._show(_t("signin.working", "Working..."))
        self.worker.run("sign-out", partial(service.sign_out, preset, spec, base_url), self._on_signed_out)

    def _on_signed_out(self, result, error):
        if error is not None:
            self._show(error_text(error), False)
            return
        parts = [_t("signin.signed_out", "Signed out: the stored key was deleted from this computer.")
                 if result.deleted else _t("signin.nothing_stored", "Nothing was stored here.")]
        if result.revoke_url:
            parts.append(_t("signin.revoke", "The key itself keeps working until you revoke it on the key page: "
                                             "{url}", url=result.revoke_url))
        if result.env_variable:
            parts.append(_t("signin.env_left", "{name} is still set, so Ditto keeps using the key from it.",
                            name=result.env_variable))
        if result.note:
            parts.append(result.note)
        self._show(" ".join(parts), True)
        self.changed.emit()
        self.refresh()

    def choose_storage(self, mode: str, helper: str = "") -> None:
        service = self.service()
        self.storage.lbl_result.setText(_t("signin.working", "Working..."))
        self.worker.run("storage", partial(service.choose_storage, mode, helper or None),
                        partial(self._on_storage, mode))

    def _on_storage(self, mode: str, result, error):
        if error is not None:
            self.storage.lbl_result.setText(error_text(error))
            return
        self.storage.lbl_result.setText(_t("signin.storage.done", "The credential vault is now protected with "
                                                                  "'{mode}'.", mode=mode))
        self.changed.emit()
        self.refresh()


class SignInDialog(QDialog):
    """AI settings > Sign in...: the panel in its own window."""

    def __init__(self, framework, preset, *, base_url: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("signin.title", "Sign in to {provider}", provider=preset.label))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.panel = SignInPanel(framework, preset, base_url=base_url, parent=self)
        layout.addWidget(self.panel)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(_t("signin.close", "Close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PassphrasePrompt(QObject):
    """The passphrase of the credential vault, asked in a dialog on the GUI thread, also when the vault opens in a
    worker thread (vault_key.set_passphrase_prompt installs it). Returns None when the user cancels."""

    _ask = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answer: Optional[str] = None
        self._lock = threading.Lock()
        self._ask.connect(self._on_ask, Qt.BlockingQueuedConnection)

    def __call__(self, new: bool) -> Optional[str]:
        if QThread.currentThread() == self.thread():
            return self.dialog(new)
        with self._lock:  # one question at a time
            self._answer = None
            self._ask.emit(new)
            return self._answer

    def _on_ask(self, new: bool) -> None:
        self._answer = self.dialog(new)

    def dialog(self, new: bool) -> Optional[str]:
        title = _t("signin.passphrase.title", "Credential vault")
        label = (_t("signin.passphrase.new", "New passphrase for the credential vault (8 characters or more):") if new
                 else _t("signin.passphrase.ask", "Passphrase of the credential vault:"))
        first, ok = QInputDialog.getText(None, title, label, QLineEdit.Password)
        if not ok or not first:
            return None
        if new:
            again, ok = QInputDialog.getText(None, title, _t("signin.passphrase.repeat", "Repeat the passphrase:"),
                                             QLineEdit.Password)
            if not ok:
                return None
            if again != first:
                QMessageBox.warning(None, title, _t("signin.passphrase.mismatch", "The passphrases do not match."))
                return None
        return first
