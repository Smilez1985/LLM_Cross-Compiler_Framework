#!/usr/bin/env python3
"""
File > Settings...: the one settings window (docs/DESIGN.md "GUI and the person's path"). Its pages, in a list on the
left (settings_pages.PageList): General, Ditto, Language model, Update, Local network, Build doorbell, Ditto in
editors, Source repositories, Effective settings. menus/build.open_settings(window, page, section) opens it at a page;
the old menu entries (Ditto > AI Settings..., Tool Permissions..., Folders the AI May Use..., Security > Secrets...,
Sign-in Overview..., Key Storage..., Set OpenAI API Key..., Tools > Maintenance > Update Framework...) open their page.

General: the language (`llmcf config set language`) beside the German form of address (`config set
form_of_address`), saved and applied the moment they are chosen (form_of_address.apply_language_and_form /
apply_form); the window then opens again at the same page in the new texts (`reopen`), and the page says what else
switches at once and what waits for the next start. Also the framework mode and the build defaults.

Ditto (sections AI settings, Tool permissions, Folders the AI may use) and Language model (Local runtime, Sign-in,
Secrets, Key storage) are the existing dialogs moved here as pages (settings_pages.embed: AIConfigurationDialog without
its local-runtime boxes, ToolPermissionsDialog, AIFoldersDialog, RuntimeModeBox and PromptCacheBox,
SignInOverviewWindow, SecretsWindow, KeyStorageWindow), each built when first shown. Update is gui/update_page.py
(`llm-cli update [--check] [--offline]`).

Also (GUI parity W1': `llm-cli config show` and `llm-cli config repos [--add NAME URL] [--remove NAME]` by mouse):

Effective settings: every setting with its value and where the value comes from (settings_view.effective_settings,
the same view as `config show`: secrets redacted). Read-only. The output folder is set with its own entry
(File > Output Folder..., also offered here).

Source repositories: the repository overrides builds receive as <NAME>_REPO_OVERRIDE (settings_view.repo_overrides /
add_repo_override / remove_repo_override, the functions of `config repos`). A URL is shown with any credential in it
redacted. Removing one asks first with exactly what goes and where it is saved ("No" is the default); nothing else is
removed. Every text is plain text.

Local network (`config local-network`, Core/local_network.py; DESIGN round 24, "L-NET final"): what offline mode
counts as local besides this computer and the own network, the person's opt-outs (a VPN, a host, an address range),
each added only after the person read what can then leave the network ("No" is the default), the VPN check of this
computer and a check of any address. Both checks look names up and may run the tailscale command: in a worker thread,
never on the GUI thread. No field here is one Ditto's GUI tools may fill (no form binding): only the person decides.

Ditto in editors (`llm-cli acp config` and `llm-cli acp servers`, orchestrator/gui/acp_settings.py): the editor entry
with Copy buttons and the MCP servers editors passed to Ditto, with the person's consent per entry.
"""

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from orchestrator.gui import settings_pages
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.settings_pages import LazyPage, PageList
from orchestrator.utils.localization import current_language, ui_text

# The pages by name (open_settings(window, page, section)) and the sections of the pages that have some.
PAGES = ("general", "ditto", "language_model", "update", "local_network", "doorbell", "acp", "repos", "effective")
SECTIONS = {"ditto": ("ai", "permissions", "folders"),
            "language_model": ("runtime", "context", "sign_in", "secrets", "key_storage")}


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
    """Every setting of `framework` on its pages (see the module text). `choose_output_folder`: the main window's
    File > Output Folder... (offered here too). `page` and `section` name where it opens (PAGES, SECTIONS); `window`
    is the main window (the update closes it on Windows; it hears about saved AI settings), by default `parent`;
    `notice` is shown on the General page (what a language change did). After a language change `reopen` holds
    (page, section, notice) and the window closes: open_settings opens it again in the new language."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 choose_output_folder: Optional[Callable[[], None]] = None, *, page: Optional[str] = None,
                 section: Optional[str] = None, window: Any = None, notice: str = ""):
        super().__init__(parent)
        self.framework = framework
        self.config = framework.config
        self.lang = lang or current_language()
        self.main_window = window if window is not None else parent
        self._choose_output_folder = choose_output_folder
        self.reopen: Optional[tuple] = None
        self.embedded: list = []          # the dialogs moved here as pages (released when the window closes)
        self.sections: dict = {}          # (page, section) -> LazyPage
        self.section_tabs: dict = {}      # page -> its QTabWidget of sections
        self.setWindowTitle(self._t("bld.settings.title"))
        self.resize(1180, 760)
        layout = QVBoxLayout(self)
        self.tabs = PageList(self)
        self.page_general = self._general_tab()
        self.page_ditto = LazyPage("tab_settings_ditto", lambda page: self._sections_page(page, "ditto"), self)
        self.page_model = LazyPage("tab_settings_language_model",
                                   lambda page: self._sections_page(page, "language_model"), self)
        self.page_update = LazyPage("tab_settings_update", self._update_page, self)
        self.page_net = self._network_tab()
        from orchestrator.gui.doorbell_settings import DoorbellSettings
        self.page_bell = DoorbellSettings(framework, self, self.lang)
        from orchestrator.gui.acp_settings import AcpSettings
        self.page_acp = AcpSettings(framework, self, self.lang)   # Ditto in editors (`llm-cli acp`)
        self.page_repos = self._repos_tab()
        self.page_effective = self._effective_tab()
        self.pages = {"general": self.page_general, "ditto": self.page_ditto, "language_model": self.page_model,
                      "update": self.page_update, "local_network": self.page_net, "doorbell": self.page_bell,
                      "acp": self.page_acp, "repos": self.page_repos, "effective": self.page_effective}
        titles = {"general": "words.settings.tab.general", "ditto": "bld.settings.tab.ditto",
                  "language_model": "bld.settings.tab.model", "update": "bld.settings.tab.update",
                  "local_network": "net.tab", "doorbell": "bell.tab", "acp": "acp.tab", "repos": "bld.settings.tab.repos",
                  "effective": "bld.settings.tab.effective"}
        for name in PAGES:
            self.tabs.addTab(self.pages[name], self._t(titles[name]))
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        self.lbl_status = plain_label("", self)
        self.lbl_status.setObjectName("lbl_settings_status")
        self.lbl_status.setWordWrap(True)
        bottom.addWidget(self.lbl_status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self.refresh()
        if notice:
            self.lbl_general_result.setText(notice)
        if page:
            self.show_page(page, section)

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    # --- pages and sections -------------------------------------------------------------------------------------

    def page_name(self, widget: Optional[QWidget] = None) -> str:
        widget = widget if widget is not None else self.tabs.currentWidget()
        return next((name for name, page in self.pages.items() if page is widget), "general")

    def section_name(self, page: str) -> Optional[str]:
        tabs = self.section_tabs.get(page)
        if tabs is None:
            return None
        current = tabs.currentWidget()
        return next((section for (p, section), lazy in self.sections.items() if p == page and lazy is current), None)

    def show_page(self, page: str, section: Optional[str] = None) -> QWidget:
        """Shows `page` (PAGES), and on a page with sections `section` (SECTIONS); returns the shown widget (built)."""
        widget = self.pages.get(page)
        if widget is None:
            raise KeyError(f"no settings page {page!r} (pages: {', '.join(PAGES)})")
        self.tabs.setCurrentWidget(widget)
        if isinstance(widget, LazyPage):
            widget.ensure()
        if section is not None and page in self.section_tabs:
            lazy = self.sections.get((page, section))
            if lazy is None:
                raise KeyError(f"no section {section!r} on the page {page!r} ({', '.join(SECTIONS[page])})")
            self.section_tabs[page].setCurrentWidget(lazy)
            return lazy.ensure()
        return widget

    def show_section(self, widget: QWidget) -> bool:
        """Shows the page and section that hold `widget` (an AI's request to show the AI settings form)."""
        for (page, section), lazy in self.sections.items():
            if lazy.content is not None and (lazy.content is widget or lazy.content.isAncestorOf(widget)):
                self.show_page(page, section)
                return True
        return False

    def _sections_page(self, page: LazyPage, name: str) -> QWidget:
        """The Ditto or Language model page: its sections as tabs, each built when first shown."""
        content = QWidget(page)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        if name == "language_model":
            intro = plain_label(self._t("bld.settings.model.intro"), content)
            intro.setWordWrap(True)
            layout.addWidget(intro)
        tabs = QTabWidget(content)
        tabs.setObjectName(f"tabs_settings_{name}")
        builders = {"ai": self._ai_section, "permissions": self._permissions_section,
                    "folders": self._folders_section, "runtime": self._runtime_section,
                    "context": self._context_section,
                    "sign_in": self._sign_in_section, "secrets": self._secrets_section,
                    "key_storage": self._key_storage_section}
        for section in SECTIONS[name]:
            lazy = LazyPage(f"section_settings_{section}", builders[section], tabs)
            self.sections[(name, section)] = lazy
            tabs.addTab(lazy, self._t(f"bld.settings.section.{section}"))
        tabs.currentChanged.connect(lambda index: self._ensure(tabs.widget(index)))
        self.section_tabs[name] = tabs
        layout.addWidget(tabs, 1)
        self._ensure(tabs.currentWidget())
        return content

    @staticmethod
    def _ensure(widget: Optional[QWidget]) -> None:
        if isinstance(widget, LazyPage):
            widget.ensure()

    def _scrolled(self, page: QWidget, inner: QWidget) -> QScrollArea:
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        return scroll

    def _embed(self, page: QWidget, dialog: QDialog, name: str) -> QScrollArea:
        """An existing dialog as this section (settings_pages.embed), in a scroll area."""
        settings_pages.embed(dialog, lambda result, d=dialog, n=name: self._page_done(n, d, result))
        self.embedded.append(dialog)
        inner = QWidget(page)   # the page keeps its own height; the rest of the section stays empty below it
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(dialog)
        layout.addStretch(1)
        return self._scrolled(page, inner)

    def _registry(self) -> Any:
        registry = getattr(self.framework, "tool_registry", None)
        if registry is None:
            raise RuntimeError(self._t("bld.settings.no_registry"))
        return registry

    def _ai_section(self, page: QWidget) -> QWidget:
        """Ditto > AI Settings...: provider, model, key, address, effort, privacy, fallback chain, knowledge base,
        offline mode (the local server's boxes are on Language model > Local runtime)."""
        from orchestrator.gui.dialogs import AIConfigurationDialog
        self.ai_settings = AIConfigurationDialog(self.framework, page, local_runtime=False)
        return self._embed(page, self.ai_settings, "ai")

    def _permissions_section(self, page: QWidget) -> QWidget:
        """Ditto > Tool Permissions... (`llm-cli tools permissions`)."""
        from orchestrator.gui.dialogs import ToolPermissionsDialog
        self.tool_permissions = ToolPermissionsDialog(self._registry(), page, lang=self.lang)
        return self._embed(page, self.tool_permissions, "permissions")

    def _folders_section(self, page: QWidget) -> QWidget:
        """Ditto > Folders the AI May Use... (`llm-cli tools folders`)."""
        from orchestrator.gui.dialogs import AIFoldersDialog
        self.ai_folders = AIFoldersDialog(self.framework, self._registry(), page)
        return self._embed(page, self.ai_folders, "folders")

    def _runtime_section(self, page: QWidget) -> QWidget:
        """Where Ditto's local server runs and its memory budget (`llm-cli ditto runtime ensure`, `ditto cache`)."""
        from orchestrator.gui.prompt_cache_box import PromptCacheBox
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        inner = QWidget(page)
        layout = QVBoxLayout(inner)
        self.runtime_mode_box = RuntimeModeBox(self.framework, inner)
        self.prompt_cache_box = PromptCacheBox(self.framework, inner, lang=self.lang)
        layout.addWidget(self.runtime_mode_box)
        layout.addWidget(self.prompt_cache_box)
        layout.addStretch(1)
        return self._scrolled(page, inner)

    def _context_section(self, page: QWidget) -> QWidget:
        """The context window of Ditto's chat model, the counting and the compactions (`llm-cli ditto context`)."""
        from orchestrator.gui.context_window_box import ContextWindowBox
        inner = QWidget(page)
        layout = QVBoxLayout(inner)
        self.context_box = ContextWindowBox(self.framework, inner, lang=self.lang)
        layout.addWidget(self.context_box)
        layout.addStretch(1)
        return self._scrolled(page, inner)

    def _sign_in_section(self, page: QWidget) -> QWidget:
        """Security > Sign-in Overview... (`llm-cli ditto auth`, `ditto login`, `ditto logout`)."""
        from orchestrator.gui.signin_windows import SignInOverviewWindow, select_primary
        self.sign_in = SignInOverviewWindow(self.framework, page, self.lang)
        scroll = self._embed(page, self.sign_in, "sign_in")
        self.sign_in.reload()
        select_primary(self.sign_in)
        return scroll

    def _secrets_section(self, page: QWidget) -> QWidget:
        """Security > Secrets... (`llm-cli secrets`)."""
        from orchestrator.gui.signin_windows import SecretsWindow
        self.secrets = SecretsWindow(self.framework, page, self.lang)
        scroll = self._embed(page, self.secrets, "secrets")
        self.secrets.reload()
        return scroll

    def _key_storage_section(self, page: QWidget) -> QWidget:
        """Security > Key Storage... (`llm-cli ditto auth storage`, `ditto auth rotate-key`)."""
        from orchestrator.gui.signin_windows import KeyStorageWindow
        self.key_storage = KeyStorageWindow(self.framework, page, self.lang)
        scroll = self._embed(page, self.key_storage, "key_storage")
        self.key_storage.reload()
        return scroll

    def _update_page(self, page: QWidget) -> QWidget:
        """Tools > Maintenance > Update Framework... (`llm-cli update`)."""
        from orchestrator.gui.update_page import UpdatePage
        self.update_page = UpdatePage(self.main_window, page, self.lang)
        return self.update_page

    def _page_done(self, name: str, dialog: QDialog, result: int) -> None:
        """An embedded page finished: Save keeps it open and says so on the status line; Escape closes the window."""
        if result != QDialog.Accepted:
            self.reject()
            return
        if name == "ai":
            config = dialog.get_config()
            self.lbl_status.setText(self._t("bld.settings.ai_saved", provider=one_line(
                str(config.get("provider_label") or config.get("provider") or "-")),
                model=one_line(str(config.get("model") or "-"))))
            saved = getattr(self.main_window, "ai_settings_saved", None)
            if callable(saved):
                saved(config)
        elif name == "permissions":
            self.lbl_status.setText(self._t("bld.settings.permissions_saved"))
        self.refresh_effective()

    def select_provider(self, provider: str) -> None:
        """Language model > Sign-in with `provider` selected (Security > Set OpenAI API Key... selects openai)."""
        self.show_page("language_model", "sign_in")
        self.sign_in.select_provider(provider)

    @property
    def panel(self) -> Any:
        """The sign-in panel of the selected provider (None before the Sign-in section was shown)."""
        sign_in = getattr(self, "sign_in", None)
        return getattr(sign_in, "panel", None)

    def done(self, result: int) -> None:
        """Closing the window ends every moved page as its dialog ended (typed secrets forgotten, forms released)."""
        for dialog in self.embedded:
            settings_pages.release(dialog)
        self.embedded = []
        super().done(result)

    def _reopen_at(self, notice: str) -> None:
        """The texts changed (language or form of address): the window closes and open_settings opens it again at
        the same page in the new texts, with `notice` on the General page."""
        page = self.page_name()
        self.reopen = (page, self.section_name(page), notice)
        self.accept()

    def _config_file(self) -> str:
        return str(Path(getattr(self.config, "config_dir", ".")) / "config.yml")

    # --- General: the German form of address (Q1); the shell chain adds language, theme and the rest here ---------

    def _general_tab(self) -> QWidget:
        from orchestrator.gui.form_of_address import FormOfAddressChooser
        page = QWidget(self)
        page.setObjectName("tab_settings_general")
        layout = QVBoxLayout(page)
        from orchestrator.gui.form_of_address import LANGUAGES
        from PySide6.QtWidgets import QLabel
        rows = QFormLayout()
        self.cmb_language = QComboBox(page)
        self.cmb_language.setObjectName("cmb_settings_language")
        for code in LANGUAGES:
            self.cmb_language.addItem(ui_text(f"words.lang.{code}", code), code)
        self.cmb_language.setCurrentIndex(max(0, self.cmb_language.findData(self.lang)))
        self.cmb_language.currentIndexChanged.connect(self._language_changed)
        label = QLabel(self._t("words.address.language"), page)
        label.setBuddy(self.cmb_language)
        self.cmb_language.setAccessibleName(self._t("words.address.language").replace("&", "").rstrip(":"))
        rows.addRow(label, self.cmb_language)
        layout.addLayout(rows)
        self.chooser_form = FormOfAddressChooser(str(self.config.get("form_of_address", "du") or "du"), page,
                                                 self.lang)
        self.chooser_form.changed.connect(self._form_changed)
        layout.addWidget(self.chooser_form)
        self.lbl_language_note = plain_label(self._t("words.language.at_once"), page)
        self.lbl_language_note.setObjectName("lbl_settings_language_note")
        self.lbl_language_note.setWordWrap(True)
        layout.addWidget(self.lbl_language_note)
        # the framework mode (llmcf config set framework_mode): the person's alone; an AI may read it, never change it
        from orchestrator.Core.config_manager import FRAMEWORK_MODES, framework_mode
        layout.addWidget(plain_label(self._t("deploy.mode.title"), page))
        self.cmb_mode = QComboBox(page)
        self.cmb_mode.setObjectName("cmb_settings_framework_mode")
        for mode in FRAMEWORK_MODES:
            self.cmb_mode.addItem(self._t(f"deploy.mode.{mode}"), mode)
        self.cmb_mode.setCurrentIndex(FRAMEWORK_MODES.index(framework_mode(self.config)))
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        layout.addWidget(self.cmb_mode)
        explain = plain_label(self._t("deploy.mode.explain"), page)
        explain.setWordWrap(True)
        layout.addWidget(explain)
        self.lbl_mode_result = plain_label("", page)
        self.lbl_mode_result.setObjectName("lbl_settings_mode_result")
        layout.addWidget(self.lbl_mode_result)
        self.lbl_general_result = plain_label("", page)
        self.lbl_general_result.setObjectName("lbl_settings_general_result")
        layout.addWidget(self.lbl_general_result)
        layout.addWidget(self._build_defaults(page))
        layout.addStretch(1)
        return page

    # --- the build defaults (`llmcf config set`, config_manager.set_build_default, Core/build_defaults.py) -------

    def _build_defaults(self, page: QWidget) -> QWidget:
        """The importance matrix and calibration text a build gets when it names none; saved at once to config.yml.
        Only the person sets the dataset here; an AI may change the importance matrix's default with its settings
        tool, never the file."""
        from PySide6.QtWidgets import QCheckBox, QGroupBox
        from orchestrator.Core.build_defaults import dataset_setting, imatrix_setting
        from orchestrator.Core.config_manager import CALIBRATION_LANGUAGES
        box = QGroupBox(self._t("bld.settings.defaults.title"), page)
        box.setObjectName("group_settings_build_defaults")
        form = QFormLayout(box)
        self.chk_default_imatrix = QCheckBox(self._t("bld.settings.defaults.imatrix"), box)
        self.chk_default_imatrix.setObjectName("chk_settings_default_imatrix")
        self.chk_default_imatrix.setToolTip(plain_tooltip(self._t("bld.settings.defaults.imatrix_tip")))
        self.chk_default_imatrix.setChecked(imatrix_setting(self.config))
        self.chk_default_imatrix.toggled.connect(
            lambda checked: self._save_build_default("default_enable_imatrix", bool(checked)))
        form.addRow(self.chk_default_imatrix)
        row = QHBoxLayout()
        self.edit_default_dataset = QLineEdit(dataset_setting(self.config), box)
        self.edit_default_dataset.setObjectName("edit_settings_default_dataset")
        self.edit_default_dataset.setPlaceholderText(self._t("bld.settings.defaults.dataset_hint"))
        self.edit_default_dataset.setToolTip(plain_tooltip(self._t("bld.settings.defaults.dataset_tip")))
        self.edit_default_dataset.editingFinished.connect(self._dataset_edited)
        row.addWidget(self.edit_default_dataset, 1)
        self.btn_default_dataset = QPushButton(button_text(self._t("bld.settings.defaults.browse")), box)
        self.btn_default_dataset.setObjectName("btn_settings_default_dataset")
        self.btn_default_dataset.setAutoDefault(False)
        self.btn_default_dataset.clicked.connect(self._choose_default_dataset)
        row.addWidget(self.btn_default_dataset)
        self.btn_shipped_dataset = QPushButton(button_text(self._t("bld.settings.defaults.shipped")), box)
        self.btn_shipped_dataset.setObjectName("btn_settings_shipped_dataset")
        self.btn_shipped_dataset.setAutoDefault(False)
        self.btn_shipped_dataset.clicked.connect(lambda: self._set_default_dataset(""))
        row.addWidget(self.btn_shipped_dataset)
        form.addRow(self._t("bld.settings.defaults.dataset"), row)
        self.cmb_calibration_language = QComboBox(box)
        self.cmb_calibration_language.setObjectName("cmb_settings_calibration_language")
        for language in CALIBRATION_LANGUAGES:
            self.cmb_calibration_language.addItem(self._t(f"bld.settings.defaults.lang.{language}"), language)
        current = str(self.config.get("calibration_language", "mixed") or "mixed")
        self.cmb_calibration_language.setCurrentIndex(max(0, self.cmb_calibration_language.findData(current)))
        self.cmb_calibration_language.currentIndexChanged.connect(
            lambda _i: self._save_build_default("calibration_language", self.cmb_calibration_language.currentData()))
        form.addRow(self._t("bld.settings.defaults.language"), self.cmb_calibration_language)
        self.lbl_defaults_result = plain_label("", box)
        self.lbl_defaults_result.setObjectName("lbl_settings_defaults_result")
        self.lbl_defaults_result.setWordWrap(True)
        form.addRow(self.lbl_defaults_result)
        return box

    def _save_build_default(self, key: str, value: Any) -> bool:
        from orchestrator.Core.config_manager import set_build_default
        try:
            set_build_default(self.config, key, value)
        except (ValueError, OSError) as e:
            self.lbl_defaults_result.setText(self._t("bld.settings.defaults.not_saved", reason=one_line(str(e))))
            return False
        self.lbl_defaults_result.setText(self._t("bld.settings.defaults.saved", name=key))
        self.refresh_effective()
        return True

    def _dataset_edited(self) -> None:
        from orchestrator.Core.build_defaults import dataset_setting
        text = self.edit_default_dataset.text().strip()
        if text != dataset_setting(self.config):
            self._set_default_dataset(text)

    def _set_default_dataset(self, value: str) -> None:
        """Saves the default calibration file ("" is the shipped text); a file that is not there is saved with a
        note, since every build that would read it is refused until it exists."""
        from orchestrator.Core.build_defaults import dataset_setting, default_dataset_path
        if not self._save_build_default("default_calibration_dataset", value):
            self.edit_default_dataset.setText(dataset_setting(self.config))
            return
        self.edit_default_dataset.setText(dataset_setting(self.config))
        path = default_dataset_path(self.config, self._datasets_folder())
        if path is not None and not path.is_file():
            self.lbl_defaults_result.setText(self._t("bld.settings.defaults.missing", name=one_line(str(path))))

    def _datasets_folder(self) -> Path:
        resolve = getattr(self.framework, "resolve_path", None)
        value = self.config.get("datasets_dir", "datasets") or "datasets"
        return Path(resolve(value)) if callable(resolve) else Path(str(value)).expanduser()

    def _choose_default_dataset(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        folder = self._datasets_folder()
        path, _ = QFileDialog.getOpenFileName(self, self._t("bld.settings.defaults.dataset"),
                                              str(folder if folder.is_dir() else Path.home()),
                                              "Text (*.txt *.json);;All Files (*)")
        if not path:
            return
        chosen = Path(path)
        try:  # a file inside the datasets folder by its name there (the setting stays valid if the folder moves)
            value = str(chosen.resolve().relative_to(folder.resolve()))
        except (ValueError, OSError):
            value = str(chosen)
        self._set_default_dataset(value)

    def _mode_changed(self, _index: int) -> None:
        """The person picked private or enterprise mode: saved to config.yml (Core/config_manager.py
        set_framework_mode, also `llmcf config set framework_mode`); a failed save keeps the mode as it was."""
        from orchestrator.Core.config_manager import FRAMEWORK_MODES, framework_mode, set_framework_mode
        try:
            now = set_framework_mode(self.config, self.cmb_mode.currentData())
            self.lbl_mode_result.setText(self._t("deploy.mode.saved", mode=self._t(f"deploy.mode.{now}")))
        except (ValueError, OSError):
            self.cmb_mode.blockSignals(True)
            self.cmb_mode.setCurrentIndex(FRAMEWORK_MODES.index(framework_mode(self.config)))
            self.cmb_mode.blockSignals(False)
            self.lbl_mode_result.setText(self._t("deploy.mode.not_saved"))
        self.refresh_effective()

    def _form_changed(self, form: str) -> None:
        """The person picked du or Sie: saved in config.yml and used at once (windows that retranslate follow); a
        German window opens again in the chosen form."""
        from orchestrator.gui.form_of_address import apply_form
        saved = apply_form(self.config, form)
        self.lbl_general_result.setText(self._t("words.address.saved" if saved else "words.address.not_saved"))
        self.refresh_effective()
        if self.lang == "de":
            self._reopen_at(self.lbl_general_result.text())

    def _language_changed(self, _index: int) -> None:
        """The person picked a language: saved in config.yml and applied at once (form_of_address.
        apply_language_and_form: the menus, tab titles, chat panel and Qt's buttons follow language_changed); this
        window opens again in it, the note says what waits for the next start."""
        from orchestrator.gui.form_of_address import apply_language_and_form
        language = str(self.cmb_language.currentData() or "en")
        if language == self.lang:
            return
        saved = apply_language_and_form(self.config, language, self.chooser_form.form())
        self.lbl_general_result.setText(ui_text("words.language.saved" if saved else "words.address.not_saved",
                                                language))
        self.refresh_effective()
        self._reopen_at(self.lbl_general_result.text())

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
        widget = self.tabs.widget(index)
        if isinstance(widget, LazyPage):
            widget.ensure()
        if widget is getattr(self, "page_net", None) and not self._vpn_checked:
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


__all__ = ["PAGES", "SECTIONS", "SettingsDialog", "source_text", "value_text"]
