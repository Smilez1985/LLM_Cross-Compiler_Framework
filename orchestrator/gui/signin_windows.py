#!/usr/bin/env python3
"""
The Security menu's windows (W1' of 2026-10-16: every action of `llmcf secrets` and `llmcf ditto login|logout|auth`
also by mouse). Each calls the same core function as its CLI command; every vault, keyring or network call runs in a
worker thread (orchestrator/gui/sign_in.py Worker); a secret is never shown, only "stored" / "not stored" or the last
four characters of a key.

- SecretsWindow (Security > Secrets...): the known names and every stored name with "stored" / "not stored" and what
  it is for (`llmcf secrets list`, Core/secret_entries.secret_entries), and Set a secret: a name from the list or any
  other name, the value twice in masked fields (`llmcf secrets set KEY`, Core/secret_entries.store_secret; in the
  ephemeral storage it says that nothing was saved).
- SignInOverviewWindow (Security > Sign-in Overview...): every provider with its sign-in at once (`llmcf ditto auth
  status`, SignIn.status_all and SignIn.storage), Check all (`--check`), every provider's sign-in methods with their
  environment variables and notes (`llmcf ditto auth methods`, method_infos), and the selected provider's sign-in
  panel with its own server address field: sign in, store the key from its environment variable (`login
  --from-env`), remember its address without making it Ditto's provider (`login --base-url`), sign out or Forget
  (`logout`, also for a server without sign-in).
- KeyStorageWindow (Security > Key Storage...): how the vault is protected and its file while it works (`llmcf ditto
  auth storage`, SignIn.storage), switching between keyring, helper (with its command), passphrase and insecure
  (insecure only after the CLI's warning, "No" is the default; `--use`, SignIn.choose_storage), and Rotate key...
  (`llmcf ditto auth rotate-key`, SignIn.rotate_key; asks first, "No" is the default).

The framework never reads, stores or forwards Claude subscription credentials and never starts the claude command.
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from orchestrator.Core.secret_entries import (KIND_KNOWN, KIND_PROVIDER, KNOWN_SECRETS, SecretNotStored,
                                              SecretsUnavailable, secret_entries, store_secret)
from orchestrator.gui.plain import ask, button_text, one_line, plain_label
from orchestrator.gui.sign_in import (ERROR_STYLE, OK_STYLE, KeyForm, SignInPanel, StorageChooser, Worker,
                                      error_text)
from orchestrator.llm.auth.signin import STORAGE, SignIn, method_infos, status_line
from orchestrator.llm.auth.spec import TYPE_LABELS
from orchestrator.utils.localization import ui_text

SECRET_COLUMNS = ("secrets.col.name", "secrets.col.state", "secrets.col.what")
STATUS_COLUMNS = ("signin.overview.col.provider", "signin.overview.col.method", "signin.overview.col.source",
                  "signin.overview.col.account", "signin.overview.col.status", "signin.overview.col.check")
METHOD_COLUMNS = ("signin.methods.col.provider", "signin.methods.col.method", "signin.methods.col.type",
                  "signin.methods.col.tags", "signin.methods.col.env", "signin.methods.col.key_page",
                  "signin.methods.col.where", "signin.methods.col.options", "signin.methods.col.note")


def _table(columns, lang: Optional[str], name: str, parent=None) -> QTableWidget:
    table = QTableWidget(0, len(columns), parent)
    table.setObjectName(name)
    table.setHorizontalHeaderLabels([ui_text(c, lang) for c in columns])
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _item(text: Any, data: Any = None) -> QTableWidgetItem:
    item = QTableWidgetItem(one_line(text if text is not None else ""))  # table items are plain text
    if data is not None:
        item.setData(Qt.UserRole, data)
    return item


def _button(text: str, name: str, parent=None) -> QPushButton:
    button = QPushButton(button_text(text), parent)
    button.setObjectName(name)
    button.setAutoDefault(False)
    return button


def _service(framework: Any) -> SignIn:
    return SignIn(getattr(framework, "config", None), getattr(framework, "secrets_manager", None))


class _Window(QDialog):
    """A window of the Security menu: a worker for the vault, plain texts in the chosen language."""

    def __init__(self, framework: Any, parent: Optional[QWidget], lang: Optional[str], title_key: str, name: str):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang
        self.setObjectName(name)
        self.setWindowTitle(self._t(title_key))
        self.worker = Worker(self._set_busy, self)

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _translate(self, key: str, default: str) -> str:
        text = ui_text(key, self.lang)
        return default if text == key else text

    @property
    def busy(self) -> bool:
        return self.worker.busy

    def _set_busy(self, busy: bool) -> None:
        for button in self._action_buttons():
            button.setEnabled(not busy)

    def _action_buttons(self) -> List[QPushButton]:
        return []

    def done(self, result: int) -> None:
        """Close, Escape or the window's X: what was typed into a secret field and not stored is forgotten, so the
        window (kept for the next click on its menu entry) never holds it while it is hidden."""
        for edit in self._secret_fields():
            edit.clear()
        super().done(result)

    def _secret_fields(self) -> List[QLineEdit]:
        return []

    def _error(self, error: BaseException) -> str:
        if isinstance(error, SecretsUnavailable):
            return self._t("signin.menu.no_vault")
        return error_text(error)

    @staticmethod
    def _show(label, text: str, ok: Optional[bool] = None) -> None:
        label.setStyleSheet(OK_STYLE if ok else ERROR_STYLE if ok is False else "")
        label.setText(text)

    def _close_row(self, layout) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.button(QDialogButtonBox.Close).setText(self._translate("signin.close", "Close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ======================================================================================================
# Security > Secrets...
# ======================================================================================================

class SecretsWindow(_Window):
    """`llmcf secrets list` and `llmcf secrets set KEY` by mouse; values are never shown."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(framework, parent, lang, "secrets.title", "dlg_signin_secrets")
        self.resize(900, 560)
        self.entries = []
        layout = QVBoxLayout(self)
        sm = getattr(framework, "secrets_manager", None)
        vault = getattr(sm, "secrets_file", None)
        self.intro = plain_label(self._t("secrets.intro", file=str(vault) if vault else "-"))
        layout.addWidget(self.intro)
        self.table = _table(SECRET_COLUMNS, lang, "tbl_secrets", self)
        self.table.itemSelectionChanged.connect(self._row_selected)
        layout.addWidget(self.table)
        self.lbl_count = plain_label(self._t("secrets.loading"))
        layout.addWidget(self.lbl_count)

        box = QGroupBox(self._t("secrets.set_title"), self)
        form = QFormLayout(box)
        self.cb_name = QComboBox(box)
        self.cb_name.setObjectName("cb_secrets_name")
        self.cb_name.setEditable(True)
        self.cb_name.addItems(list(KNOWN_SECRETS))
        self.cb_name.setCurrentIndex(-1)
        self.cb_name.lineEdit().setPlaceholderText(self._t("secrets.name_placeholder"))
        self.cb_name.currentTextChanged.connect(self._name_changed)
        self.txt_value = QLineEdit(box)
        self.txt_value.setObjectName("txt_secrets_value")
        self.txt_repeat = QLineEdit(box)
        self.txt_repeat.setObjectName("txt_secrets_repeat")
        for edit in (self.txt_value, self.txt_repeat):
            edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText(self._t("secrets.value_placeholder"))
        form.addRow(self._t("secrets.name"), self.cb_name)
        form.addRow(self._t("secrets.value"), self.txt_value)
        form.addRow(self._t("secrets.repeat"), self.txt_repeat)
        self.lbl_replace = plain_label("")
        form.addRow(self.lbl_replace)
        row = QHBoxLayout()
        self.btn_store = _button(self._t("secrets.store"), "btn_secrets_store", box)
        self.btn_store.clicked.connect(self.store)
        row.addWidget(self.btn_store)
        row.addStretch(1)
        form.addRow(row)
        self.lbl_result = plain_label("")
        form.addRow(self.lbl_result)
        layout.addWidget(box)

        actions = QHBoxLayout()
        self.btn_refresh = _button(self._t("secrets.refresh"), "btn_secrets_refresh", self)
        self.btn_refresh.clicked.connect(self.reload)
        actions.addWidget(self.btn_refresh)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._close_row(layout)

    def _action_buttons(self) -> List[QPushButton]:
        return [self.btn_store, self.btn_refresh]

    def _secret_fields(self) -> List[QLineEdit]:
        return [self.txt_value, self.txt_repeat]

    def reload(self) -> None:
        """Reads the names in a worker (the vault may wait for its lock or the keyring)."""
        sm = getattr(self.framework, "secrets_manager", None)
        self.lbl_count.setText(self._t("secrets.loading"))
        self.worker.run("secrets-list", lambda: secret_entries(sm), self._on_entries)

    def _what(self, entry) -> str:
        if entry.kind == KIND_KNOWN and entry.what_key:
            return self._t(entry.what_key)
        if entry.kind == KIND_PROVIDER:
            return self._t("secrets.what.provider", provider=entry.provider or "-")
        return self._t("secrets.what.other")

    def _on_entries(self, entries, error) -> None:
        if error is not None:
            self.entries = []
            self.table.setRowCount(0)
            self.lbl_count.setText(self._t("secrets.list_failed", reason=self._error(error)))
            return
        self.entries = list(entries)
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            state = self._t("secrets.stored" if entry.stored else "secrets.not_stored")
            for column, value in enumerate((entry.name, state, self._what(entry))):
                self.table.setItem(row, column, _item(value, entry.name if column == 0 else None))
        self.lbl_count.setText(self._t("secrets.count", n=len(self.entries),
                                       stored=sum(1 for e in self.entries if e.stored)))
        self._name_changed(self.cb_name.currentText())

    def _row_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) == 1:
            item = self.table.item(rows.pop(), 0)
            if item is not None:
                self.cb_name.setCurrentText(str(item.data(Qt.UserRole) or item.text()))

    def _name_changed(self, text: str) -> None:
        name = (text or "").strip()
        stored = any(e.name == name and e.stored for e in self.entries)
        self.lbl_replace.setText(self._t("secrets.replaces") if stored else "")

    def store(self) -> None:
        """Set a secret: the name and the value twice, as `llmcf secrets set KEY` asks; stored in a worker."""
        name, value, repeat = self.cb_name.currentText().strip(), self.txt_value.text(), self.txt_repeat.text()
        if not name:
            self._show(self.lbl_result, self._t("secrets.name_needed"), False)
            return
        if not value:
            self._show(self.lbl_result, self._t("secrets.value_needed"), False)
            return
        if value != repeat:
            self._show(self.lbl_result, self._t("secrets.mismatch"), False)
            return
        sm = getattr(self.framework, "secrets_manager", None)
        self._show(self.lbl_result, self._t("secrets.working"))
        self.worker.run("secrets-set", lambda: store_secret(sm, name, value), self._on_stored)

    def _on_stored(self, stored, error) -> None:
        if isinstance(error, SecretNotStored):
            self._show(self.lbl_result, self._t("secrets.failed", name=one_line(error.name)), False)
            return
        if error is not None:
            self._show(self.lbl_result, self._error(error), False)
            return
        self.txt_value.clear()
        self.txt_repeat.clear()
        key = "secrets.memory_only" if stored.memory_only else "secrets.stored_ok"
        self._show(self.lbl_result, self._t(key, name=one_line(stored.name)), not stored.memory_only)
        self.reload()


# ======================================================================================================
# Security > Sign-in Overview...
# ======================================================================================================

class SignInOverviewWindow(_Window):
    """`llmcf ditto auth status [--check]`, `ditto auth methods`, and per provider `ditto login` (also --from-env and
    --base-url) and `ditto logout` (also for a server without sign-in) by mouse."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 service: Optional[SignIn] = None):
        super().__init__(framework, parent, lang, "signin.overview.title", "dlg_signin_overview")
        self.resize(1100, 820)
        self.service = service or _service(framework)
        self.presets = list(self.service.catalog.providers.values())
        self.statuses: Dict[str, Any] = {}
        self.panel: Optional[SignInPanel] = None
        self._request = 0
        self._checking = False          # Check all is running
        self._read_after_check = False  # something changed meanwhile: read every provider again once it is back
        self._checked: Dict[str, Any] = {}  # provider -> (its credential, the check's text) of the last Check all
        self._summary = ""
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("signin.overview.intro")))
        self.table = _table(STATUS_COLUMNS, lang, "tbl_signin_status", self)
        self.table.setRowCount(len(self.presets))
        for row, preset in enumerate(self.presets):
            self.table.setItem(row, 0, _item(preset.label, preset.id))
            for column in range(1, len(STATUS_COLUMNS)):
                self.table.setItem(row, column, _item("-"))
        self.table.itemSelectionChanged.connect(self._row_selected)
        layout.addWidget(self.table)
        layout.addWidget(plain_label(self._t("signin.overview.primary")))
        self.lbl_vault = plain_label("")
        self.lbl_vault.setObjectName("lbl_signin_vault")
        layout.addWidget(self.lbl_vault)
        row = QHBoxLayout()
        self.btn_check_all = _button(self._t("signin.overview.check_all"), "btn_signin_check_all", self)
        self.btn_check_all.clicked.connect(self.check_all)
        self.btn_refresh = _button(self._t("signin.overview.refresh"), "btn_signin_refresh", self)
        self.btn_refresh.clicked.connect(self.reload)
        row.addWidget(self.btn_check_all)
        row.addWidget(self.btn_refresh)
        self.lbl_state = plain_label("")
        row.addWidget(self.lbl_state, 1)
        layout.addLayout(row)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("tabs_signin")
        self.panel_scroll = QScrollArea(self.tabs)
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setWidget(plain_label(self._t("signin.overview.pick")))
        self.tabs.addTab(self.panel_scroll, self._t("signin.overview.tab_sign_in", provider="-"))
        self.methods = _table(METHOD_COLUMNS, lang, "tbl_signin_methods", self.tabs)
        self.tabs.addTab(self.methods, self._t("signin.overview.tab_methods"))
        layout.addWidget(self.tabs, 1)
        self._close_row(layout)
        self._fill_methods()

    def _action_buttons(self) -> List[QPushButton]:
        return [self.btn_check_all, self.btn_refresh]

    def _secret_fields(self) -> List[QLineEdit]:
        if self.panel is None:
            return []
        return [form.txt_key for form in self.panel.forms.values() if isinstance(form, KeyForm)]

    # --- the methods (the catalog only: no vault, no network) -----------------------------------------------
    def _fill_methods(self) -> None:
        rows = []
        for preset in self.presets:
            for info in method_infos(preset):
                spec = info.spec
                tags = []
                if info.recommended:
                    tags.append(self._translate("signin.recommended", "Recommended"))
                if spec.opt_in:
                    tags.append(self._translate("signin.advanced", "Advanced: only when chosen"))
                if not info.built:
                    tags.append(self._translate("signin.not_built", "Not available in this version"))
                rows.append((f"{preset.label} [{preset.id}]", f"{spec.label} [{spec.id}]",
                             self._translate(f"signin.type.{spec.type}", TYPE_LABELS.get(spec.type, spec.type)),
                             ", ".join(tags), ", ".join(spec.env), spec.key_page or "", spec.where or "",
                             ", ".join(f.label for f in spec.fields), spec.note or ""))
        self.methods.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.methods.setItem(row, column, _item(value))

    # --- the status of every provider ------------------------------------------------------------------------
    def reload(self) -> None:
        """`llmcf ditto auth status`: every provider and the vault, read in a worker."""
        self._run(False)

    def check_all(self) -> None:
        """`llmcf ditto auth status --check`: also the free check of every provider with a credential (network)."""
        self._run(True)

    def _run(self, check: bool) -> None:
        if self._checking and not check:
            # Check all is running and brings every provider's status too: its result is not thrown away; what
            # changed meanwhile (a key saved in the panel) is read once it is back.
            self._read_after_check = True
            return
        service = self.service
        self._request += 1
        self._checking = check
        self.lbl_state.setText(self._t("signin.overview.checking" if check else "signin.overview.reading"))
        self.worker.run("overview-check" if check else "overview",
                        lambda: (service.status_all(check=check), service.storage()),
                        lambda result, error, request=self._request: self._on_status(request, check, result, error))

    def _source(self, st) -> str:
        source = st.source or ""
        if source == "vault":
            return self._t("signin.source.vault")
        if source.startswith("env:"):
            return self._t("signin.source.env", name=source[4:])
        if source.startswith("profile:"):
            return self._t("signin.source.profile", name=st.profile or source[8:])
        if source == "runtime":
            return self._t("signin.source.runtime")
        return "-"

    @staticmethod
    def _credential(st) -> tuple:
        """What a check result belongs to: the method, where the credential comes from, its last four characters
        and the state. A later read keeps the check of a credential that did not change."""
        return st.method, st.source, st.last4, st.state

    def _on_status(self, request: int, check: bool, result, error) -> None:
        if request != self._request:
            return  # an older read that finished late
        read_again = check and self._read_after_check
        if check:
            self._checking = self._read_after_check = False
        try:
            self._show_status(check, result, error)
        finally:
            if read_again:
                self._run(False)

    def _show_status(self, check: bool, result, error) -> None:
        if error is not None:
            self.lbl_state.setText(self._t("signin.overview.failed", reason=error_text(error)))
            return
        statuses, storage = result
        self.statuses = {st.provider: st for st in statuses}
        for row, preset in enumerate(self.presets):
            st = self.statuses.get(preset.id)
            if st is None:
                continue
            key = " · ".join(p for p in (st.account, f"...{st.last4}" if st.last4 else None,
                                         st.fields.get("workspace_id")) if p)
            state = self._t("signin.overview.storage_state") if st.state == STORAGE else status_line(
                st, self._translate)
            credential = self._credential(st)
            if check:
                checked = st.check.message if st.check is not None else "-"
                self._checked[preset.id] = (credential, checked)
            else:
                last = self._checked.get(preset.id)
                checked = last[1] if last is not None and last[0] == credential else "-"
            values = (("* " if st.primary else "") + st.label, st.method_label or "-", self._source(st), key or "-",
                      state, checked)
            for column, value in enumerate(values):
                self.table.setItem(row, column, _item(value, preset.id if column == 0 else None))
        if storage.get("usable"):
            file = storage.get("file")
            self.lbl_vault.setText(self._t("signin.overview.vault", mode=storage["mode"], file=file)
                                   if file and storage["mode"] != "ephemeral"
                                   else self._t("signin.overview.vault_mode", mode=storage["mode"]))
        else:
            problem = storage.get("problem") or {}
            self.lbl_vault.setText(self._t("signin.overview.vault_problem",
                                           reason=problem.get("message") or problem.get("code") or "-"))
        if check:
            done = [st.check for st in statuses if st.check is not None]
            self._summary = self._t("signin.overview.checked", ok=sum(1 for c in done if c.ok),
                                    failed=sum(1 for c in done if not c.ok))
            self.lbl_state.setText(self._summary)
        else:
            kept = any(self.table.item(row, 5) is not None and self.table.item(row, 5).text() != "-"
                       for row in range(self.table.rowCount()))
            self.lbl_state.setText(self._summary if kept else "")

    # --- the selected provider's sign-in ----------------------------------------------------------------------
    def selected_provider(self) -> Optional[str]:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        item = self.table.item(rows.pop(), 0)
        return str(item.data(Qt.UserRole)) if item is not None and item.data(Qt.UserRole) else None

    def select_provider(self, provider: str) -> Optional[SignInPanel]:
        for row, preset in enumerate(self.presets):
            if preset.id == provider:
                self.table.selectRow(row)
                return self.panel
        return None

    def _row_selected(self) -> None:
        provider = self.selected_provider()
        preset = self.service.catalog.get(provider) if provider else None
        if preset is None or (self.panel is not None and self.panel.preset.id == preset.id):
            return
        panel = SignInPanel(self.framework, preset, service=self.service, address_field=True, lang=self.lang)
        panel.setObjectName("panel_signin")
        panel.changed.connect(self.reload)
        old = self.panel_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.panel = panel
        self.panel_scroll.setWidget(panel)
        self.tabs.setTabText(0, self._t("signin.overview.tab_sign_in", provider=preset.label))
        self.tabs.setCurrentIndex(0)


# ======================================================================================================
# Security > Key Storage...
# ======================================================================================================

class KeyStorageWindow(_Window):
    """`llmcf ditto auth storage [--use MODE [--helper COMMAND]]` and `llmcf ditto auth rotate-key` by mouse."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 service: Optional[SignIn] = None):
        super().__init__(framework, parent, lang, "signin.keystore.title", "dlg_signin_key_storage")
        self.resize(760, 560)
        self.service = service or _service(framework)
        self.info: Dict[str, Any] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("signin.keystore.intro")))
        self.lbl_mode = plain_label(self._t("signin.keystore.reading"))
        self.lbl_mode.setObjectName("lbl_keystore_mode")
        self.lbl_file = plain_label("")
        self.lbl_file.setObjectName("lbl_keystore_file")
        self.lbl_problem = plain_label("")
        self.lbl_problem.setStyleSheet(ERROR_STYLE)
        for label in (self.lbl_mode, self.lbl_file, self.lbl_problem):
            layout.addWidget(label)
        self.chooser = StorageChooser(self, with_keyring=True, title=self._t("signin.keystore.choose"), lang=lang)
        self.chooser.chosen.connect(self.choose)
        layout.addWidget(self.chooser)
        row = QHBoxLayout()
        self.btn_rotate = _button(self._t("signin.keystore.rotate"), "btn_keystore_rotate", self)
        self.btn_rotate.clicked.connect(self.rotate)
        row.addWidget(self.btn_rotate)
        row.addStretch(1)
        layout.addLayout(row)
        self.lbl_result = plain_label("")
        self.lbl_result.setObjectName("lbl_keystore_result")
        layout.addWidget(self.lbl_result)
        layout.addStretch(1)
        self._close_row(layout)

    def _action_buttons(self) -> List[QPushButton]:
        return [self.chooser.btn_use, self.btn_rotate]

    def reload(self) -> None:
        """`llmcf ditto auth storage`: the protection, whether the vault works, its file (read in a worker)."""
        service = self.service
        self.worker.run("keystore", service.storage, self._on_info)

    def _on_info(self, info, error) -> None:
        if error is not None:
            self.lbl_problem.setText(error_text(error))
            return
        self.info = dict(info)
        mode = str(info.get("mode") or "")
        text = self._translate(f"signin.keystore.choice.{mode}", info.get("choices", {}).get(mode, ""))
        self.lbl_mode.setText(self._t("signin.keystore.mode", mode=mode, text=text))
        file = info.get("file")
        self.lbl_file.setText(self._t("signin.keystore.file", file=file) if file and mode != "ephemeral" else "")
        problem = info.get("problem") or {}
        self.lbl_problem.setText("" if info.get("usable") else self._t(
            "signin.keystore.problem", reason=problem.get("message") or problem.get("code") or "-"))
        self.chooser.mark_current(mode if mode != "ephemeral" else None)

    def choose(self, mode: str, helper: str = "") -> None:
        """`--use MODE [--helper COMMAND]`: the insecure key file only after the CLI's question ("No" is the
        default); the vault is re-encrypted in a worker (a passphrase is asked on the GUI thread)."""
        if mode == "insecure" and not ask(self, self._t("signin.keystore.insecure_title"),
                                          self._t("signin.keystore.insecure_confirm")):
            self._show(self.lbl_result, self._t("signin.keystore.cancelled"))
            return
        service = self.service
        self._show(self.lbl_result, self._translate("signin.working", "Working..."))
        self.worker.run("keystore-use", lambda: service.choose_storage(mode, helper or None),
                        lambda result, error: self._on_chosen(mode, error))

    def _on_chosen(self, mode: str, error) -> None:
        if error is not None:
            self._show(self.lbl_result, error_text(error), False)
        else:
            self._show(self.lbl_result, self._translate("signin.storage.done", "The credential vault is now "
                                                        "protected with '{mode}'.").format(mode=mode), True)
        self.reload()

    def rotate(self) -> None:
        """Rotate key...: asks first ("No" is the default), then a new vault key in a worker."""
        if not ask(self, self._t("signin.keystore.rotate_title"), self._t("signin.keystore.rotate_confirm")):
            self._show(self.lbl_result, self._t("signin.keystore.rotate_cancelled"))
            return
        service = self.service

        def work() -> bool:
            service.rotate_key()
            return getattr(service.secrets, "storage_mode", None) == "ephemeral"

        self._show(self.lbl_result, self._translate("signin.working", "Working..."))
        self.worker.run("keystore-rotate", work, self._on_rotated)

    def _on_rotated(self, memory_only, error) -> None:
        if error is not None:
            self._show(self.lbl_result, error_text(error), False)
        elif memory_only:
            self._show(self.lbl_result, self._t("signin.keystore.rotated_memory"))
        else:
            self._show(self.lbl_result, self._t("signin.keystore.rotated"), True)
        self.reload()


# ======================================================================================================
# Opening them from the main window (one window each, fresh data each time)
# ======================================================================================================

WINDOWS = {"secrets": ("signin_secrets_window", SecretsWindow),
           "overview": ("signin_overview_window", SignInOverviewWindow),
           "key_storage": ("signin_key_storage_window", KeyStorageWindow)}


def _open(window: Any, which: str):
    attribute, cls = WINDOWS[which]
    dialog = getattr(window, attribute, None)
    if dialog is None:
        dialog = cls(window.framework, window)
        setattr(window, attribute, dialog)
    if not dialog.busy:
        # Fresh data each time it opens; while its own work runs (Check all, Store, Rotate key...), that work
        # brings fresh data when it is done, and a new read would only throw its result away.
        dialog.reload()
    dialog.show()
    dialog.raise_()
    return dialog


def open_secrets(window: Any) -> SecretsWindow:
    """Security > Secrets...: the window of `window` (the main window)."""
    return _open(window, "secrets")


def open_overview(window: Any) -> SignInOverviewWindow:
    """Security > Sign-in Overview...: the window of `window`, Ditto's provider selected."""
    dialog = _open(window, "overview")
    if dialog.selected_provider() is None and dialog.presets:
        primary = next((p.id for p in dialog.presets if dialog.service.client.is_primary(p)), dialog.presets[0].id)
        dialog.select_provider(primary)
    elif dialog.panel is not None and not dialog.panel.busy:
        dialog.panel.refresh()  # the provider shown since the last time: its sign-in may have changed meanwhile
    return dialog


def open_key_storage(window: Any) -> KeyStorageWindow:
    """Security > Key Storage...: the window of `window`."""
    return _open(window, "key_storage")


__all__ = ["KeyStorageWindow", "METHOD_COLUMNS", "SECRET_COLUMNS", "STATUS_COLUMNS", "SecretsWindow",
           "SignInOverviewWindow", "open_key_storage", "open_overview", "open_secrets"]
