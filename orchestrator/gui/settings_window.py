#!/usr/bin/env python3
"""
File > Settings... (GUI parity W1': `llm-cli config show` and `llm-cli config repos [--add NAME URL] [--remove NAME]`
by mouse).

Effective settings: every setting with its value and where the value comes from (settings_view.effective_settings,
the same view as `config show`: secrets redacted). Read-only. The output folder is set with its own entry
(File > Output Folder..., also offered here).

Source repositories: the repository overrides builds receive as <NAME>_REPO_OVERRIDE (settings_view.repo_overrides /
add_repo_override / remove_repo_override, the functions of `config repos`). A URL is shown with any credential in it
redacted. Removing one asks first with exactly what goes and where it is saved ("No" is the default); nothing else is
removed. Every text is plain text.

Local network (`config local-network`, Core/local_network.py; DECISIONS round 24, "L-NET final"): what offline mode
counts as local besides this computer and the own network, the person's opt-outs (a VPN, a host, an address range),
each added only after the person read what can then leave the network ("No" is the default), the VPN check of this
computer and a check of any address. Both checks look names up and may run the tailscale command: in a worker thread,
never on the GUI thread. No field here is one Ditto's GUI tools may fill (no form binding): only the person decides.
"""

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.utils.localization import current_language, ui_text


def source_text(source: str, lang: Optional[str] = None) -> str:
    """Where a value comes from, in the person's language (a settings file with its full path)."""
    if source.startswith("file "):
        return ui_text("bld.settings.source.file", lang, path=source[5:])
    if source in ("default", "environment", "override"):
        return ui_text(f"bld.settings.source.{source}", lang)
    return source


def value_text(value: Any) -> str:
    """A setting's value on one line (lists and maps as JSON)."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _variable(name: str) -> str:
    from orchestrator.Core.builder import repo_override_names
    names = repo_override_names(name)
    return names[0] if names else "-"


class SettingsDialog(QDialog):
    """The effective settings (read-only) and the source repository overrides of `framework`.
    `choose_output_folder`: the main window's File > Output Folder... (offered here too)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 choose_output_folder: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.framework = framework
        self.config = framework.config
        self.lang = lang or current_language()
        self._choose_output_folder = choose_output_folder
        self.setWindowTitle(self._t("bld.settings.title"))
        self.resize(1100, 560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._general_tab(), self._t("words.settings.tab.general"))
        self.tabs.addTab(self._effective_tab(), self._t("bld.settings.tab.effective"))
        self.tabs.addTab(self._repos_tab(), self._t("bld.settings.tab.repos"))
        self.page_net = self._network_tab()
        self.tabs.addTab(self.page_net, self._t("net.tab"))
        from orchestrator.gui.doorbell_settings import DoorbellSettings
        self.page_bell = DoorbellSettings(framework, self, self.lang)
        self.tabs.addTab(self.page_bell, self._t("bell.tab"))
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _config_file(self) -> str:
        return str(Path(getattr(self.config, "config_dir", ".")) / "config.yml")

    # --- General: the German form of address (Q1); the shell chain adds language, theme and the rest here ---------

    def _general_tab(self) -> QWidget:
        from orchestrator.gui.form_of_address import FormOfAddressChooser
        page = QWidget(self)
        page.setObjectName("tab_settings_general")
        layout = QVBoxLayout(page)
        self.chooser_form = FormOfAddressChooser(str(self.config.get("form_of_address", "du") or "du"), page,
                                                 self.lang)
        self.chooser_form.changed.connect(self._form_changed)
        layout.addWidget(self.chooser_form)
        self.lbl_general_result = plain_label("", page)
        self.lbl_general_result.setObjectName("lbl_settings_general_result")
        layout.addWidget(self.lbl_general_result)
        layout.addStretch(1)
        return page

    def _form_changed(self, form: str) -> None:
        """The person picked du or Sie: saved in config.yml and used at once (windows that retranslate follow)."""
        from orchestrator.gui.form_of_address import apply_form
        saved = apply_form(self.config, form)
        self.lbl_general_result.setText(self._t("words.address.saved" if saved else "words.address.not_saved"))
        self.refresh_effective()

    # --- effective settings (config show) -------------------------------------------------------------------

    def _effective_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(plain_label(self._t("bld.settings.effective.intro"), page))
        row = QHBoxLayout()
        self.lbl_output = plain_label("", page)
        row.addWidget(self.lbl_output, 1)
        self.btn_output = QPushButton(button_text(self._t("bld.settings.output_folder")), page)
        self.btn_output.setObjectName("btn_settings_output_folder")
        self.btn_output.setAutoDefault(False)
        self.btn_output.clicked.connect(self._output_folder)
        self.btn_output.setVisible(self._choose_output_folder is not None)
        row.addWidget(self.btn_output)
        layout.addLayout(row)
        self.table_effective = QTableWidget(0, 3, page)
        self.table_effective.setObjectName("table_settings_effective")
        self.table_effective.setHorizontalHeaderLabels([self._t("bld.settings.col.name"),
                                                        self._t("bld.settings.col.value"),
                                                        self._t("bld.settings.col.source")])
        self.table_effective.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_effective.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_effective.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_effective.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_effective)
        return page

    def refresh_effective(self) -> None:
        from orchestrator.Core.settings_view import effective_settings
        shown = effective_settings(self.config)   # secrets redacted, as `config show`
        self.effective = shown
        self.table_effective.setRowCount(len(shown))
        for row, (name, entry) in enumerate(shown.items()):
            for column, text in enumerate((name, value_text(entry["value"]),
                                           source_text(str(entry["source"]), self.lang))):
                self.table_effective.setItem(row, column, QTableWidgetItem(one_line(text)))
        try:
            from orchestrator.Core.artifact_output import output_folder
            self.lbl_output.setText(self._t("bld.settings.output_now", path=one_line(str(output_folder(
                self.framework)))))
        except Exception:  # noqa: BLE001 - a stand-in framework: the line stays empty
            self.lbl_output.setText("")

    def _output_folder(self) -> None:
        if self._choose_output_folder is not None:
            self._choose_output_folder()
        self.refresh_effective()

    # --- source repositories (config repos) -----------------------------------------------------------------

    def _repos_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.lbl_repos = plain_label("", page)
        layout.addWidget(self.lbl_repos)
        self.table_repos = QTableWidget(0, 3, page)
        self.table_repos.setObjectName("table_settings_repos")
        self.table_repos.setHorizontalHeaderLabels([self._t("bld.settings.col.repo"), self._t("bld.settings.col.url"),
                                                    self._t("bld.settings.col.variable")])
        self.table_repos.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_repos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_repos.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_repos.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_repos.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_repos)
        form = QFormLayout()
        self.edit_repo_name = QLineEdit(page)
        self.edit_repo_name.setObjectName("edit_repo_name")
        self.edit_repo_name.setPlaceholderText(self._t("bld.settings.repos.name_hint"))
        self.edit_repo_url = QLineEdit(page)
        self.edit_repo_url.setObjectName("edit_repo_url")
        self.edit_repo_url.setPlaceholderText(self._t("bld.settings.repos.url_hint"))
        form.addRow(self._t("bld.settings.repos.name"), self.edit_repo_name)
        form.addRow(self._t("bld.settings.repos.url"), self.edit_repo_url)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.btn_repo_add = QPushButton(button_text(self._t("bld.settings.repos.add")), page)
        self.btn_repo_add.setObjectName("btn_repo_add")
        self.btn_repo_remove = QPushButton(button_text(self._t("bld.settings.repos.remove")), page)
        self.btn_repo_remove.setObjectName("btn_repo_remove")
        for button, slot in ((self.btn_repo_add, self.add_repo), (self.btn_repo_remove, self.remove_repo)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.lbl_repo_result = plain_label("", page)
        layout.addWidget(self.lbl_repo_result)
        return page

    def refresh_repos(self) -> None:
        from orchestrator.Core.settings_view import repo_overrides, shown_repo_url
        self.repos = repo_overrides(self.config)
        path = one_line(self._config_file())
        self.lbl_repos.setText(self._t("bld.settings.repos.intro", path=path) + "\n" +
                               ("" if self.repos else self._t("bld.settings.repos.empty")))
        self.table_repos.setRowCount(len(self.repos))
        for row, (name, url) in enumerate(self.repos.items()):
            for column, text in enumerate((name, shown_repo_url(url), _variable(name))):
                self.table_repos.setItem(row, column, QTableWidgetItem(one_line(text)))
        self.btn_repo_remove.setEnabled(bool(self.repos))

    def selected_repo(self) -> Optional[str]:
        rows = self.table_repos.selectionModel().selectedRows() if self.table_repos.selectionModel() else []
        if not rows:
            return None
        item = self.table_repos.item(rows[0].row(), 0)
        return item.text() if item is not None else None

    def add_repo(self) -> None:
        from orchestrator.Core.settings_view import (CREDENTIAL_IN_URL, RepoOverrideError, add_repo_override,
                                                     shown_repo_url)
        name, url = self.edit_repo_name.text().strip(), self.edit_repo_url.text().strip()
        if not name or not url:
            plain_box(self, "information", self._t("bld.settings.tab.repos"), self._t("bld.settings.repos.need_both"))
            return
        try:
            variable = add_repo_override(self.config, name, url)
        except RepoOverrideError as e:
            if e.code == CREDENTIAL_IN_URL:   # the same refusal as `config repos --add`, in the GUI's words
                what = self._t("bld.settings.repos.password" if e.kind == "password" else "bld.settings.repos.token")
                plain_box(self, "warning", self._t("bld.settings.tab.repos"),
                          self._t("bld.settings.repos.credential", what=what, url=one_line(shown_repo_url(url))))
            else:
                plain_box(self, "warning", self._t("bld.settings.tab.repos"), one_line(str(e)))
            return
        self.lbl_repo_result.setText(self._t("bld.settings.repos.added", name=one_line(name.lower()),
                                             url=one_line(shown_repo_url(url)), variable=variable))
        self.edit_repo_name.clear()
        self.edit_repo_url.clear()
        self.refresh()

    def remove_repo(self) -> None:
        from orchestrator.Core.settings_view import RepoOverrideError, remove_repo_override, shown_repo_url
        name = self.selected_repo()
        if not name:
            plain_box(self, "information", self._t("bld.settings.tab.repos"), self._t("bld.settings.repos.pick"))
            return
        question = self._t("bld.settings.repos.remove_question", name=one_line(name),
                           url=one_line(shown_repo_url(self.repos.get(name, "-"))), variable=_variable(name),
                           path=one_line(self._config_file()))
        if not ask(self, self._t("bld.settings.tab.repos"), question):   # "No" is the default
            return
        try:
            removed = remove_repo_override(self.config, name)
        except RepoOverrideError as e:
            plain_box(self, "warning", self._t("bld.settings.tab.repos"), one_line(str(e)))
            return
        self.lbl_repo_result.setText(self._t("bld.settings.repos.removed" if removed
                                             else "bld.settings.repos.not_found", name=one_line(name)))
        self.refresh()

    # --- local network (config local-network) -----------------------------------------------------------------

    def _network_tab(self) -> QWidget:
        from orchestrator.gui.worker_relay import worker_relay
        page = QWidget(self)
        page.setObjectName("tab_settings_local_network")
        layout = QVBoxLayout(page)
        layout.addWidget(plain_label(self._t("net.intro"), page))
        self.lbl_net_vpn = plain_label(self._t("net.vpn.checking"), page)
        self.lbl_net_vpn.setObjectName("lbl_net_vpn")
        layout.addWidget(self.lbl_net_vpn)
        self.lbl_net_empty = plain_label(self._t("net.empty"), page)
        layout.addWidget(self.lbl_net_empty)
        self.table_net = QTableWidget(0, 3, page)
        self.table_net.setObjectName("table_settings_local_network")
        self.table_net.setHorizontalHeaderLabels([self._t("net.col.kind"), self._t("net.col.value"),
                                                  self._t("net.col.means")])
        self.table_net.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_net.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_net.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_net.setWordWrap(True)
        self.table_net.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_net.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_net)
        form = QFormLayout()
        self.combo_net_kind = QComboBox(page)
        self.combo_net_kind.setObjectName("combo_net_kind")
        for kind in ("host", "range", "vpn"):
            self.combo_net_kind.addItem(self._t(f"net.kind.{kind}"), kind)
        self.combo_net_kind.currentIndexChanged.connect(self._net_kind_changed)
        self.edit_net_value = QLineEdit(page)
        self.edit_net_value.setObjectName("edit_net_value")
        self.edit_net_value.setPlaceholderText(self._t("net.value.hint"))
        form.addRow(self._t("net.kind.label"), self.combo_net_kind)
        form.addRow(self._t("net.value.label"), self.edit_net_value)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.btn_net_add = QPushButton(button_text(self._t("net.add")), page)
        self.btn_net_add.setObjectName("btn_net_add")
        self.btn_net_remove = QPushButton(button_text(self._t("net.remove")), page)
        self.btn_net_remove.setObjectName("btn_net_remove")
        for button, slot in ((self.btn_net_add, self.add_opt_out), (self.btn_net_remove, self.remove_opt_out)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        check_row = QHBoxLayout()
        check_row.addWidget(plain_label(self._t("net.check.label"), page))
        self.edit_net_check = QLineEdit(page)
        self.edit_net_check.setObjectName("edit_net_check")
        self.edit_net_check.setPlaceholderText(self._t("net.check.hint"))
        check_row.addWidget(self.edit_net_check, 1)
        self.btn_net_check = QPushButton(button_text(self._t("net.check")), page)
        self.btn_net_check.setObjectName("btn_net_check")
        self.btn_net_check.setAutoDefault(False)
        self.btn_net_check.clicked.connect(self.check_address)
        check_row.addWidget(self.btn_net_check)
        layout.addLayout(check_row)
        self.lbl_net_result = plain_label("", page)
        self.lbl_net_result.setObjectName("lbl_net_result")
        layout.addWidget(self.lbl_net_result)
        layout.addStretch(1)
        self._net_requests = {}
        self._relay = worker_relay()
        self._relay.done.connect(self._on_net_result, Qt.QueuedConnection)
        self._vpn_checked = False   # checked when the tab is first shown (it may run the tailscale command)
        return page

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is getattr(self, "page_net", None) and not self._vpn_checked:
            self._start_vpn_check()

    def _net_kind_changed(self) -> None:
        vpn = self.combo_net_kind.currentData() == "vpn"
        self.edit_net_value.setEnabled(not vpn)
        if vpn:
            self.edit_net_value.setText("tailscale")
        elif self.edit_net_value.text() == "tailscale":
            self.edit_net_value.clear()

    def _in_worker(self, kind: str, work: Callable[[], str]) -> None:
        """Runs `work()` in a worker thread (a lookup or the tailscale command never runs on the GUI thread); its
        result comes back through the relay to _on_net_result."""
        token = object()
        self._net_requests[kind] = token
        relay = self._relay

        def run():
            try:
                result = ("ok", work())
            except Exception as e:  # noqa: BLE001 - shown as plain text
                result = ("error", str(e))
            relay.done.emit(token, result)

        threading.Thread(target=run, name=f"local-network-{kind}", daemon=True).start()

    def _start_vpn_check(self) -> None:
        from orchestrator.Core.local_network import vpn_status, vpn_text
        lang = self.lang
        self._vpn_checked = True
        self.lbl_net_vpn.setText(self._t("net.vpn.checking"))
        self._in_worker("vpn", lambda: vpn_text(vpn_status(), lang))

    def check_address(self) -> None:
        """Checks the typed address, host or URL by the rule with the person's opt-outs (in a worker thread)."""
        from orchestrator.Core.local_network import check, check_text
        target = self.edit_net_check.text().strip()
        if not target:
            return
        config, lang = self.config, self.lang
        self.lbl_net_result.setText(self._t("net.check.running", host=one_line(target)))
        self._in_worker("check", lambda: check_text(check(config, target), lang))

    def _on_net_result(self, token: object, result) -> None:
        kind = next((k for k, t in self._net_requests.items() if t is token), None)
        if kind is None or not isinstance(result, tuple) or len(result) != 2:
            return   # another window's result, or an older check
        self._net_requests.pop(kind, None)
        label = self.lbl_net_vpn if kind == "vpn" else self.lbl_net_result
        try:
            label.setText(one_line(result[1]) if result[0] == "error" else str(result[1]))
        except RuntimeError:   # the window was closed meanwhile
            pass

    def refresh_network(self) -> None:
        from orchestrator.Core.local_network import opt_outs
        self.net_rows = opt_outs(self.config)
        self.lbl_net_empty.setVisible(not self.net_rows)
        self.table_net.setRowCount(len(self.net_rows))
        for row, entry in enumerate(self.net_rows):
            for column, text in enumerate((entry.kind_label(self.lang), entry.label(self.lang),
                                           entry.explanation(self.lang))):
                item = QTableWidgetItem(one_line(text))
                item.setToolTip(plain_tooltip(text))
                self.table_net.setItem(row, column, item)
        self.table_net.resizeRowsToContents()
        self.btn_net_remove.setEnabled(bool(self.net_rows))

    def add_opt_out(self) -> None:
        """Counts a VPN, host or range as local: only after the person read what can then leave the network."""
        from orchestrator.Core.local_network import LocalNetworkError, add_opt_out, explain, normalize
        kind = self.combo_net_kind.currentData() or "host"
        value = self.edit_net_value.text().strip()
        if not value:
            plain_box(self, "information", self._t("net.tab"), self._t("net.value.hint"))
            return
        try:
            stored = normalize(kind, value, self.lang)
        except LocalNetworkError as e:
            plain_box(self, "warning", self._t("net.tab"), one_line(str(e)))
            return
        question = self._t("net.ask", explanation=explain(kind, stored, self.lang))
        if not ask(self, self._t("net.ask.title"), question):   # "No" is the default
            return
        try:
            added = add_opt_out(self.config, kind, stored, self.lang)
        except LocalNetworkError as e:
            plain_box(self, "warning", self._t("net.tab"), one_line(str(e)))
            return
        self.lbl_net_result.setText(self._t("net.added", value=one_line(added.label(self.lang))))
        if kind != "vpn":
            self.edit_net_value.clear()
        self.refresh()
        self._start_vpn_check()

    def selected_opt_out(self):
        rows = self.table_net.selectionModel().selectedRows() if self.table_net.selectionModel() else []
        if not rows or rows[0].row() >= len(getattr(self, "net_rows", [])):
            return None
        return self.net_rows[rows[0].row()]

    def remove_opt_out(self) -> None:
        """Stops counting the chosen row as local, after a question with what changes and where ("No" default)."""
        from orchestrator.Core.local_network import LocalNetworkError, remove_opt_out
        entry = self.selected_opt_out()
        if entry is None:
            plain_box(self, "information", self._t("net.tab"), self._t("net.pick"))
            return
        question = self._t("net.remove_question", value=one_line(entry.label(self.lang)),
                           path=one_line(self._config_file()))
        if not ask(self, self._t("net.tab"), question):   # "No" is the default
            return
        try:
            removed = remove_opt_out(self.config, entry.kind, entry.value, self.lang)
        except LocalNetworkError as e:
            plain_box(self, "warning", self._t("net.tab"), one_line(str(e)))
            return
        self.lbl_net_result.setText(self._t("net.removed" if removed else "net.not_found",
                                            value=one_line(entry.label(self.lang))))
        self.refresh()
        self._start_vpn_check()

    def refresh(self) -> None:
        self.refresh_effective()
        self.refresh_repos()
        self.refresh_network()


__all__ = ["SettingsDialog", "source_text", "value_text"]
