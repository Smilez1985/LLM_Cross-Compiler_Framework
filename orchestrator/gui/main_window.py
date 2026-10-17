#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Main Window (v2.4.0)
DIREKTIVE: Goldstandard GUI.

Features:
- Visual AI Avatar (Ditto) reacting to system state.
- Live Status Polling from Orchestrator.
- Integrated Self-Healing notifications.
- New Tabs: Auto-Tuning & Quality Regression.

Updates v2.4.0:
- Added Ditto Sprites integration.
- Added QTimer for Orchestrator state polling.
- Added Hyperparameter Tuning Tab placeholder.
"""

import os
import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget, QStatusBar,
    QMessageBox, QToolBar,
    QSizePolicy
)
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtCore import Qt, QSize, QTimer, Signal

# Core Framework
from orchestrator.Core.framework import FrameworkManager
from orchestrator.Core.orchestrator import OrchestrationStatus
from orchestrator.utils import input_history
from orchestrator.utils.announce import SETTING as AUDIO_SETTING
from orchestrator.utils.localization import current_language
from orchestrator.utils.localization import get_instance as get_i18n
from orchestrator.utils.localization import ui_text

# GUI Components
from orchestrator.gui.dialogs import SecretInputDialog, HealingConfirmDialog, AIConfigurationDialog, ToolPermissionsDialog
from orchestrator.gui.wizards import ModuleCreationWizard
from orchestrator.gui.builder_tab import BuilderTab
from orchestrator.gui.chat_window import ChatWindow
from orchestrator.gui.plain import ask, button_text, notify, one_line, plain_box, plain_tooltip
from orchestrator.gui.sign_in import STORAGE_CHOICE_CODES, UserConfigSnapshot, choose_storage_at_start
from orchestrator.gui.deployment_window import DeploymentWindow
from orchestrator.gui.form_surface import close_gui, open_gui
from orchestrator.gui.menus import boards as menu_boards
from orchestrator.gui.menus import build as menu_build
from orchestrator.gui.menus import ditto_models as menu_ditto_models
from orchestrator.gui.menus import ditto_runtime as menu_ditto_runtime
from orchestrator.gui.menus import knowledge as menu_knowledge
from orchestrator.gui.menus import mojo as menu_mojo
from orchestrator.gui.menus import place as menu_place
from orchestrator.gui.menus import signin as menu_signin
from orchestrator.gui.menus import tools_mcp as menu_tools_mcp

# ============================================================================
# DITTO AVATAR WIDGET
# ============================================================================

class DittoAvatar(QLabel):
    """Displays the AI state using sprite assets; a click shows the chat panel."""
    clicked = Signal()

    def __init__(self, asset_dir: Path):
        super().__init__()
        self.asset_dir = asset_dir
        self.setTextFormat(Qt.PlainText)
        self.setFixedSize(64, 64)
        self.setScaledContents(True)
        self.setCursor(Qt.PointingHandCursor)
        
        # Load Sprites
        self.sprites = {
            "idle": str(asset_dir / "Ditto.png"),
            "think": str(asset_dir / "ditto_think.png"),
            "success": str(asset_dir / "ditto_success.png"),
            "fail": str(asset_dir / "ditto_fail.png"),
            "read": str(asset_dir / "ditto_read.png")
        }
        self.set_state("idle")

    def set_state(self, state: str):
        path = self.sprites.get(state, self.sprites["idle"])
        if Path(path).exists():
            self.setPixmap(QPixmap(path))
        else:
            self.setText(f"[{state.upper()}]") # Fallback text

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _app_sender(caller: str, client: str = ""):
    """The sender of a chat notice about what an AI app (or Ditto) did: the app by its trusted caller id, its own name
    only as its claim, so the chat names it in the person's language and in its own look (design 5.2 R4, R5). None
    for a caller that is no app (the notice is then the framework's)."""
    from orchestrator.Core.chat_feed import FeedWriter
    try:
        if caller == "ditto":
            return FeedWriter.sender("ditto", "ditto")
        if caller == "mcp" or caller.startswith("mcp:"):
            return FeedWriter.sender("client", caller, client=client or "")
    except ValueError:
        pass
    return None

# ============================================================================
# MAIN WINDOW CLASS
# ============================================================================

class MainOrchestrator(QMainWindow):
    def __init__(self, app_root: Path):
        super().__init__()
        self.app_root = app_root
        self.i18n = get_i18n()
        self._healing_prompted = set()  # (request_id, proposal id) already shown to the user
        
        # Init Framework
        self.framework = FrameworkManager()
        before_choice = None  # the user config before a storage choice here; put back if no choice works
        while not self.framework.initialize():
            # The cause (e.g. no usable keyring, with the storage choices), not only "failed".
            cause = getattr(self.framework, "init_error", None)
            config = getattr(self.framework, "config", None)
            if getattr(cause, "code", None) in STORAGE_CHOICE_CODES and config is not None:
                before_choice = before_choice or UserConfigSnapshot(config)
                if choose_storage_at_start(self, config, cause):
                    self.framework = FrameworkManager()  # starts again with the protection the user chose
                    continue
            if before_choice is not None:
                before_choice.restore()
            plain_box(self, "critical", "Fatal Error", f"Framework initialization failed: {cause}" if cause
                      else "Framework initialization failed. Check logs.")
            sys.exit(1)
            
        self.setWindowTitle(f"LLM Cross-Compiler Framework v{self.framework.info.version} (Enterprise)")
        self.resize(1280, 850)
        
        # Setup UI
        self._setup_menu()
        self._setup_toolbar()
        self._setup_central_widget()
        self._setup_statusbar()
        self._setup_chat_dock()
        self._apply_styles()
        self.gui_surface = open_gui(self)  # Ditto's hands in this window: the GUI tools on its forms
        
        # Start State Polling (Live Feedback Loop)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_system_state)
        self.update_timer.start(1000) # Every second

        # Ditto's first setup: ask for the tool permissions once, right after the window is shown.
        registry = getattr(self.framework, "tool_registry", None)
        if registry is not None and not registry.policy().configured:
            QTimer.singleShot(0, lambda: self._open_tool_permissions(first_run=True))

    def _menu(self, menubar, name: str, key: str):
        """A menu of the menu bar with its object name and its translated title (MENU_LAYOUT names its place)."""
        menu = menubar.addMenu(ui_text(key))
        menu.setObjectName(name)
        return menu

    def _entry(self, menu, name: str, key: str, slot) -> QAction:
        """An entry with its object name (the CLI_PATHS name, found in any language) and its translated label."""
        action = menu_place.new_action(self, name, ui_text(key), slot)
        menu.addAction(action)
        return action

    def _setup_menu(self):
        """The menu bar (wave 6, DECISIONS round 10 Q2): every entry is created with its object name and a translated
        label; the order of the menus and their entries is one table, MENU_LAYOUT (orchestrator/utils/next_steps/
        common.py), applied by place.arrange() once the area modules and the chat dock added theirs. The AI
        administration lives in the Ditto menu ("Set up Ditto..." first, Ditto's local runtime, models and evaluation
        under Ditto > Advanced), Tools has the submenus Knowledge and Maintenance."""
        menubar = self.menuBar()

        # The menus carry object names, so the area modules (orchestrator/gui/menus/place.py) find them in any language
        file_menu = self._menu(menubar, menu_place.MENU_FILE, "main.menu.file")
        self._add_trash_action(file_menu)
        self._add_output_folder_action(file_menu)
        self.act_exit = self._entry(file_menu, "act_file_exit", "menu.exit", self.close)

        ditto_menu = self._menu(menubar, menu_place.MENU_DITTO, "main.menu.ditto")
        self.menu_ditto = ditto_menu
        # Set up Ditto... opens the AI settings until the setup guide exists (chain ditto-setup: menus/ditto_setup.py
        # connects the guide to act_ditto_setup in its install()).
        self.act_ditto_setup = self._entry(ditto_menu, "act_ditto_setup", "main.ditto.setup", self._open_ditto_setup)
        self.act_ditto_settings = self._entry(ditto_menu, "act_ditto_settings", "main.ditto.settings",
                                              self._open_ai_config)
        self.act_tools_permissions = self._entry(ditto_menu, "act_tools_permissions", "main.ditto.permissions",
                                                 self._open_tool_permissions)
        self.act_tools_folders = self._entry(ditto_menu, "act_tools_folders", "main.ditto.folders",
                                             self._open_ai_folders)
        # calls of AI apps that could not ask you
        self.act_tools_approvals = self._entry(ditto_menu, "act_tools_approvals", "main.ditto.approvals",
                                               self._open_approvals)
        # propose_healing_fix: manual steps an AI app proposed, never run
        self.act_tools_proposals = self._entry(ditto_menu, "act_tools_proposals", "main.ditto.proposals",
                                               self._open_client_proposals)
        advanced = ditto_menu.addMenu(ui_text("main.menu.ditto_advanced"))
        advanced.setObjectName("menu_ditto_advanced")   # Local Runtime..., Local Models..., Evaluation... (menus/)

        sec_menu = self._menu(menubar, menu_place.MENU_SECURITY, "main.menu.security")
        self.act_signin_openai_key = self._entry(
            sec_menu, "act_signin_openai_key", "main.security.openai",
            lambda: self._open_secret_dialog("openai_api_key", "OpenAI API Key"))

        self.view_menu = self._menu(menubar, menu_place.MENU_VIEW, "main.menu.view")  # filled by _setup_chat_dock

        tools_menu = self._menu(menubar, menu_place.MENU_TOOLS, "main.menu.tools")
        self.act_tools_module_wizard = self._entry(tools_menu, "act_tools_module_wizard", "main.tools.module_wizard",
                                                   self._open_module_wizard)
        self.act_tools_hf_browser = self._entry(tools_menu, "act_tools_hf_browser", "main.tools.hf_browser",
                                                self._open_hf_browser)
        knowledge = tools_menu.addMenu(ui_text("main.menu.tools_knowledge"))
        knowledge.setObjectName("menu_tools_knowledge")
        self.act_knowledge_hub = self._entry(knowledge, "act_knowledge_hub", "menu.open_hub", self._open_community_hub)
        self._add_knowledge_snapshots_action(knowledge)
        self._add_framework_docs_action(knowledge)
        maintenance = tools_menu.addMenu(ui_text("main.menu.tools_maintenance"))
        maintenance.setObjectName("menu_tools_maintenance")
        self._add_framework_images_action(maintenance)

        # Spoken announcements are extra (everything is also shown); e.g. screen-reader users switch them off.
        self.act_audio = QAction(ui_text("menu.audio"), self)
        self.act_audio.setObjectName("act_tools_audio")
        self.act_audio.setCheckable(True)
        self.act_audio.setChecked(bool(self.framework.config.get(AUDIO_SETTING, True)))
        self.act_audio.toggled.connect(self._set_audio_announcements)
        tools_menu.addAction(self.act_audio)

        # Recent models, targets, boards, URLs, devices and datasets offered in the input fields (per user)
        self.act_clear_history = self._entry(tools_menu, "act_tools_clear_history", "menu.clear_history",
                                             self._clear_input_history)

        help_menu = self._menu(menubar, menu_place.MENU_HELP, "main.menu.help")
        # shows the version, as `llm-cli --version` (menus/existing.py)
        self.act_help_about = self._entry(help_menu, "act_help_about", "main.help.about", self._show_about)

        # GUI parity (W1'): one install line per area. Each module finds its menu by its object name and adds its
        # entries; place.arrange() then puts every menu and entry where MENU_LAYOUT says (File, Build, Boards, Ditto,
        # Security, View, Tools, Help). The deploy area's paths are buttons of the Deployment and Builder tabs
        # (menus/deploy.py).
        menu_build.install(self, menubar)  # File > Settings...; Build > Targets..., Build History...
        menu_boards.install(self, menubar)  # Boards > Boards and Devices...
        menu_ditto_runtime.install(self, menubar)  # Ditto > Advanced > Local Runtime...
        menu_ditto_models.install(self, menubar)  # Ditto > Advanced > Local Models..., Evaluation...
        menu_signin.install(self, menubar)  # Security > Secrets..., Sign-in Overview..., Key Storage...
        menu_tools_mcp.install(self, menubar)  # Security > Audit Log..., AI App Connections...; Deep Ingest Notice...
        menu_knowledge.install(self, menubar)  # Tools > Knowledge > Knowledge Sources...
        menu_mojo.install(self, menubar)  # Tools > Maintenance > Mojo Health Check...
        # Optional install hooks of later chains (no shared list to edit): Set up Ditto's guide and the Help menu.
        for optional in ("ditto_setup", "help"):
            module = menu_place.optional_module(optional)
            if module is not None:
                module.install(self, menubar)
        menu_place.arrange(menubar)
        if self.i18n is not None:   # the menus and the tab titles follow the language (the shell chain's switch)
            self.i18n.language_changed.connect(lambda lang: menu_place.retranslate_menus(self, lang))

    def _open_ditto_setup(self):
        """Ditto > Set up Ditto...: the AI settings until chain ditto-setup's guide takes this entry over."""
        self._open_ai_config()

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setObjectName("mainToolbar")  # saveState() remembers the window layout by object names
        toolbar.setIconSize(QSize(32, 32))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Spacer to push Ditto to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        
        # The chat panel on the right: unread messages plus questions that wait, as text (no colour alone)
        from PySide6.QtWidgets import QPushButton
        self.btn_chat = QPushButton(button_text(ui_text("chat.toolbar.button")))
        self.btn_chat.setToolTip(plain_tooltip(ui_text("chat.toolbar.tip")))
        self.btn_chat.setAutoDefault(False)
        toolbar.addWidget(self.btn_chat)

        # Ditto Avatar
        assets_path = self.app_root / "assets"
        self.ditto_avatar = DittoAvatar(assets_path)
        toolbar.addWidget(self.ditto_avatar)
        
        self.lbl_ditto_status = QLabel("Ready")
        self.lbl_ditto_status.setStyleSheet("color: #aaa; margin-right: 15px; margin-left: 5px;")
        toolbar.addWidget(self.lbl_ditto_status)

    def _setup_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        
        # 1. Builder Tab (Modified for IMatrix)
        self.builder_tab = BuilderTab(self.framework)
        self.tabs.addTab(self.builder_tab, "")

        # 2. Deployment Window (the AI chat moved into the panel on the right: _setup_chat_dock)
        self.deployment_window = DeploymentWindow(self.framework)
        self.tabs.addTab(self.deployment_window, "")

        # 3. NEW: Hyperparameter Tuning (Roadmap)
        self.tuning_tab = QLabel("\n\n🚧 Hyperparameter Auto-Tuning Module\n\nComing soon: Automated quantization matrix benchmarking.")
        self.tuning_tab.setAlignment(Qt.AlignCenter)
        self.tabs.addTab(self.tuning_tab, "")

        # 4. Model Manager Placeholder
        self.models_tab = QLabel("Model Manager (Local Library)")
        self.tabs.addTab(self.models_tab, "")
        # The tab titles from ui_text keys (menus/place.py TAB_TITLES): the emoji in front, the text alone for screen
        # readers; they follow a language change with the menus (retranslate_menus).
        menu_place.retitle_tabs(self)

    # --- the chat panel on the right (delegation design 5.1, 5.9) ---------------------------------------------------

    def _setup_chat_dock(self):
        """The former AI Chat tab as a dock on the right: F9 and the View menu show or hide it, Ctrl+Shift+C writes
        in it, the toolbar button counts new messages and questions that wait, a click on Ditto shows it. Where it
        sits and whether it is shown are remembered in gui_state.json in the per-user folder."""
        from orchestrator.gui.chat_panel.dock import DEFAULT_WIDTH, ChatDock
        from orchestrator.gui.gui_state import GuiState
        self.chat_window = ChatWindow(self.framework)
        self.chat_dock = ChatDock(self.chat_window, self, lang=self.chat_window.lang)
        self.addDockWidget(Qt.RightDockWidgetArea, self.chat_dock)
        self.resizeDocks([self.chat_dock], [DEFAULT_WIDTH], Qt.Horizontal)
        self.chat_dock.toggleViewAction().setObjectName("act_view_chat")   # mouse paths name it (MENU_LAYOUT)
        self.view_menu.addAction(self.chat_dock.toggleViewAction())
        self.act_focus_chat = QAction(ui_text("chat.view.focus"), self)
        self.act_focus_chat.setObjectName("act_view_focus_chat")
        self.act_focus_chat.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.act_focus_chat.setShortcutContext(Qt.ApplicationShortcut)
        self.act_focus_chat.triggered.connect(self._focus_chat)
        self.view_menu.addAction(self.act_focus_chat)

        panel = self.chat_window
        panel.counts_changed.connect(self._update_chat_button)
        panel.question_arrived.connect(self._auto_show_chat)
        panel.open_approvals_requested.connect(lambda _approval_id: self._open_approvals())
        panel.permissions_requested.connect(self._open_tool_permissions)
        panel.auto_show_changed.connect(self._set_chat_auto_show)
        panel.set_auto_show(bool(self.framework.config.get("chat_panel_auto_show", True)))
        self.btn_chat.clicked.connect(self._toggle_chat)
        self.ditto_avatar.clicked.connect(self._show_chat)
        notifier = getattr(self, "approval_notifier", None)
        if notifier is not None:  # questions that wait come from the pending approvals, never from feed rows alone
            panel.link_approvals(notifier.store, notifier.changed)
        if self.i18n is not None:  # the dock, its View menu entries and the toolbar button follow the language too
            self.i18n.language_changed.connect(self._chat_language_changed)

        self.gui_state = GuiState.load(getattr(self.framework.config, "user_state_dir", None))
        if self.gui_state.geometry:
            self.restoreGeometry(GuiState.decode(self.gui_state.geometry))
        if self.gui_state.dock_state:
            self.restoreState(GuiState.decode(self.gui_state.dock_state))
        self.chat_dock.setVisible(self.gui_state.chat_panel_visible)
        panel.apply_state(self.gui_state)
        from orchestrator.gui.chat_panel.panel import show_ditto_notice
        show_ditto_notice(self.chat_dock, panel)   # Ditto is experimental: the notice is read once (round 8, Q-2)

    def _chat_language_changed(self, lang: str):
        self.chat_dock.retranslate(lang)
        self.view_menu.setTitle(ui_text("chat.view.menu", lang))
        self.act_focus_chat.setText(ui_text("chat.view.focus", lang))
        self.btn_chat.setToolTip(plain_tooltip(ui_text("chat.toolbar.tip", lang)))
        self._update_chat_button(self.chat_window.unread_count(), self.chat_window.pending_count())

    def _toggle_chat(self):
        if self.chat_dock.isHidden():
            self._show_chat()
        else:
            self.chat_dock.hide()

    def _show_chat(self):
        self.chat_dock.show()
        self.chat_dock.raise_()
        self.chat_window.mark_seen()

    def _focus_chat(self):
        self._show_chat()
        self.chat_dock.activateWindow()
        self.chat_window.focus_composer()

    def _auto_show_chat(self):
        """A new question shows a hidden panel without taking the focus (unless the person turned that off)."""
        if self.framework.config.get("chat_panel_auto_show", True):
            self.chat_dock.show_without_focus()

    def _set_chat_auto_show(self, enabled: bool):
        cfg = self.framework.config
        cfg.set("chat_panel_auto_show", bool(enabled))
        if not cfg.save_user_config():
            QMessageBox.warning(self, self.i18n.t("settings.not_saved.title"), self.i18n.t("settings.not_saved"))

    def _update_chat_button(self, unread: int, pending: int):
        count = unread + pending
        self.btn_chat.setText(button_text(ui_text("chat.toolbar.button_count", n=count) if count
                                          else ui_text("chat.toolbar.button")))

    def _save_gui_state(self):
        state = getattr(self, "gui_state", None)
        if state is None:
            return
        from orchestrator.gui.gui_state import GuiState
        state.dock_state = GuiState.encode(self.saveState())
        state.geometry = GuiState.encode(self.saveGeometry())
        state.chat_panel_visible = not self.chat_dock.isHidden()
        state.last_seen_entry = self.chat_window.last_seen
        state.filter = self.chat_window.header.current_filter()
        state.save()

    def _setup_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        
        # Components Indicators
        if getattr(self.framework, 'secrets_manager', None):
            self.lbl_sec = QLabel("🛡️ Secrets Secured")
            self.lbl_sec.setStyleSheet("color: #4CAF50; font-weight: bold; margin-right: 10px;")
            self.status.addPermanentWidget(self.lbl_sec)
        
        if getattr(self.framework, 'self_healing_manager', None):
            self.lbl_heal = QLabel("🏥 Healing Active")
            self.lbl_heal.setStyleSheet("color: #2196F3; font-weight: bold; margin-right: 10px;")
            self.status.addPermanentWidget(self.lbl_heal)

        self._setup_approval_notice()
        self.status.showMessage("System Ready.")

    def _setup_approval_notice(self):
        """A notice while calls of AI clients wait for your approval (design 6.9); a click opens the window."""
        registry = getattr(self.framework, "tool_registry", None)
        approvals = getattr(registry, "approvals", None)
        self.approval_notifier = None
        if approvals is None:
            return
        from PySide6.QtWidgets import QPushButton
        from orchestrator.gui.approvals_window import ApprovalNotifier
        self.btn_approvals = QPushButton("")
        self.btn_approvals.setFlat(True)
        self.btn_approvals.setStyleSheet("color: #E65100; font-weight: bold;")
        self.btn_approvals.clicked.connect(self._open_approvals)
        self.btn_approvals.hide()
        self.status.addPermanentWidget(self.btn_approvals)
        self.approval_notifier = ApprovalNotifier(approvals.store, self)
        self.approval_notifier.changed.connect(self._approvals_changed)
        self.approval_notifier.check()
        # While this window runs, "do not ask again" is refused on the command line (this window is the stronger
        # channel); a standing approval made there before is shown once now, and every use shows a notice.
        from orchestrator.gui.approvals_window import GrantUseWatcher
        from orchestrator.tools.grants import hold_gui_lock
        self._gui_lock = hold_gui_lock(registry.policy_store.path.parent)
        self.btn_revoke_grant = QPushButton("Revoke")
        self.btn_revoke_grant.hide()
        self.btn_revoke_grant.clicked.connect(self._revoke_used_grant)
        self.status.addPermanentWidget(self.btn_revoke_grant)
        self._used_grant = ""
        if registry.audit is not None:
            self.grant_watcher = GrantUseWatcher(registry.audit, self)
            self.grant_watcher.used.connect(self._grant_used)
        QTimer.singleShot(0, self._announce_cli_grants)

    def _grant_used(self, tool: str, grant_id: str, actor: str, detail: str):
        """A notice in speaking names: who acted, the tool's title and what the standing approval covers."""
        from orchestrator.tools.approvals import actor_label, tool_title
        self._used_grant = grant_id
        registry = getattr(self.framework, "tool_registry", None)
        summary = ""
        try:
            found = registry.policy().grant_by_id(grant_id)
            summary = found[1].summary if found else ""
        except Exception:
            pass
        covers = f" ({summary})" if summary else ""
        self.status.showMessage(f"{actor_label(actor)} ran '{tool_title(registry, tool)}' under your standing "
                                f"approval{covers}{detail}.", 30000)
        self.btn_revoke_grant.show()
        QTimer.singleShot(30000, self.btn_revoke_grant.hide)
        chat = getattr(self, "chat_window", None)
        if chat is not None:  # the same notice in the chat (what it covers is quoted: it names packs and models)
            sender, title = _app_sender(actor), tool_title(registry, tool)
            if sender is not None:  # under the app's own bubble: its name in the person's language (R4)
                if summary:
                    chat.post_notice("chat.notice.grant_used_by", {"title": title, "covers": summary},
                                     external=("covers",), sender=sender)
                else:
                    chat.post_notice("chat.notice.grant_used_by_plain", {"title": title}, sender=sender)
            elif summary:
                chat.post_notice("chat.notice.grant_used", {"who": actor_label(actor), "title": title,
                                                            "covers": summary}, external=("covers",))
            else:
                chat.post_notice("chat.notice.grant_used_plain", {"who": actor_label(actor), "title": title})

    def _revoke_used_grant(self):
        registry = getattr(self.framework, "tool_registry", None)
        if registry is None or not self._used_grant:
            return
        try:
            registry.revoke_grant(self._used_grant, "gui")
        except Exception as e:
            plain_box(self, "critical", "Standing approval", f"Could not revoke it: {e}")
            return
        self.btn_revoke_grant.hide()
        self.status.showMessage(f"Standing approval {self._used_grant} revoked; those calls ask again.", 8000)

    def _announce_cli_grants(self):
        """Standing approvals created from a command line are shown here once (and then marked as shown)."""
        registry = getattr(self.framework, "tool_registry", None)
        try:
            rows = [r for r in registry.grant_rows() if not r["grant"].announced]
        except Exception:
            return
        if not rows:
            return
        from orchestrator.tools.approvals import caller_label, tool_title
        listing = "\n".join(f"- {r['grant'].id}: {tool_title(registry, r['grant'].tool)} for "
                             f"{caller_label(r['caller'])}: {r['grant'].summary} (created {r['grant'].created} via "
                             f"{r['grant'].created_via})" for r in rows)
        plain_box(self, "information", "Standing approvals created on the command line",
                  "These standing approvals were created on a command line. Revoke any you do not want "
                  f"in Tools > Tool Permissions.\n\n{listing}")
        shown = {r["grant"].id for r in rows}
        try:
            registry.policy_store.update(lambda p: p.model_copy(update={"grants": {
                c: [g.model_copy(update={"announced": True}) if g.id in shown else g for g in gs]
                for c, gs in p.grants.items()}}), actor="gui")
        except Exception as e:
            self.status.showMessage(f"Could not mark the standing approvals as shown: {e}", 8000)

    def _approvals_changed(self, count: int):
        self.btn_approvals.setText(f"{count} call(s) wait for your approval" if count else "")
        self.btn_approvals.setVisible(bool(count))
        if count and self.approval_notifier.new_ids:
            from PySide6.QtWidgets import QSystemTrayIcon
            if QSystemTrayIcon.isSystemTrayAvailable():  # a desktop notification where the system offers one
                tray = getattr(self, "_approval_tray", None)
                if tray is None:
                    tray = self._approval_tray = QSystemTrayIcon(self.windowIcon(), self)
                    tray.show()
                notify(tray, "LLM Cross-Compiler Framework",
                       f"{count} call(s) of AI clients wait for your approval (Tools > Pending Approvals).")

    def _open_approvals(self):
        registry = getattr(self.framework, "tool_registry", None)
        if getattr(registry, "approvals", None) is None:
            QMessageBox.critical(self, "Error", "Pending approvals are not available.")
            return
        from orchestrator.gui.approvals_window import ApprovalsDialog
        ApprovalsDialog(registry, self).exec()
        if self.approval_notifier is not None:
            self.approval_notifier.check()

    def _update_system_state(self):
        """
        Polls the Orchestrator for active workflows and updates the UI (Ditto, Status).
        """
        self._notice_client_proposals()
        orch = self.framework.orchestrator
        if not orch: return

        # Since orchestrator methods are async, we can't await them directly in QTimer.
        # We assume the Orchestrator exposes a synchronous state property or we peek into internal dicts for GUI.
        # Ideally, Orchestrator should have a thread-safe property. 
        # Accessing private _workflows for GUI speed (Python is single process here usually).
        active_flows = list(orch._workflows.values())
        self._announce_saved_builds(active_flows)
        
        if not active_flows:
            self.ditto_avatar.set_state("idle")
            self.lbl_ditto_status.setText("Idle")
            self._show_other_builds()
            return

        # A proposal waiting for the user's decision takes precedence over the most recent flow.
        for flow in active_flows:
            if flow.status == OrchestrationStatus.AWAITING_APPROVAL and flow.healing_proposal is not None:
                key = (flow.request_id, id(flow.healing_proposal))
                if key not in self._healing_prompted:
                    self._healing_prompted.add(key)
                    self.ditto_avatar.set_state("think")
                    self.lbl_ditto_status.setText("Waiting for your decision")
                    self.trigger_healing_dialog(flow.request_id, flow.healing_proposal)
                return

        # Get most recent flow
        latest = active_flows[-1]
        
        # Map Status to Ditto Sprite
        if latest.status == OrchestrationStatus.BUILDING:
            self.ditto_avatar.set_state("think")
            self.lbl_ditto_status.setText(f"Building... ({latest.progress_percent}%)")
            self.status.showMessage(f"Working on {latest.request_id} - {latest.current_stage}")
            
        elif latest.status == OrchestrationStatus.PREPARING:
            self.ditto_avatar.set_state("read") # Reading config
            self.lbl_ditto_status.setText("Preparing...")
            
        elif latest.status == OrchestrationStatus.HEALING:
            self.ditto_avatar.set_state("think") # Thinking hard
            self.lbl_ditto_status.setText("Self-Healing Active!")
            self.status.showMessage(f"🚑 Analyzing Error: {latest.current_stage}")

        elif latest.status == OrchestrationStatus.COMPLETED:
            self.ditto_avatar.set_state("success")
            self.lbl_ditto_status.setText("Done!")
            
        elif latest.status == OrchestrationStatus.ERROR:
            self.ditto_avatar.set_state("fail")
            self.lbl_ditto_status.setText("Error")

    def _announce_saved_builds(self, flows):
        """A build an AI started went to the output folder without a dialog (eighth round, answer 3): the person's
        status bar names where, with the full path (the AI read a label)."""
        announced = self.__dict__.setdefault("_saved_announced", set())
        for flow in flows:
            if flow.status == OrchestrationStatus.COMPLETED and getattr(flow, "owner", "") and flow.artifacts \
                    and flow.request_id not in announced:
                announced.add(flow.request_id)
                self.status.showMessage(ui_text("save.ai_saved", paths=", ".join(str(a) for a in flow.artifacts)),
                                        30000)

    def _open_secret_dialog(self, key, name):
        if not getattr(self.framework, 'secrets_manager', None):
            QMessageBox.critical(self, "Error", "SecretsManager not available!")
            return
            
        dlg = SecretInputDialog(self.framework, key, name, self)
        dlg.exec()

    def _open_ai_config(self):
        dlg = AIConfigurationDialog(self.framework, self)
        if dlg.exec():
            cfg = dlg.get_config()
            provider = f"{cfg.get('provider_label') or cfg.get('provider')} / {cfg.get('model')}"
            mode = f"Offline, {provider}" if cfg.get("offline_mode") else provider
            self.status.showMessage(f"AI Agent configured: {mode}", 5000)
        chat = getattr(self, "chat_window", None)
        if chat is not None:
            chat.refresh_access()  # also after a sign-in that was cancelled with the settings

    def _open_tool_permissions(self, first_run: bool = False):
        registry = getattr(self.framework, "tool_registry", None)
        if registry is None:
            QMessageBox.critical(self, "Error", "Tool registry not available.")
            return
        if ToolPermissionsDialog(registry, self, first_run=first_run).exec():
            self.status.showMessage("Tool permissions saved.", 5000)

    def _add_knowledge_snapshots_action(self, menu):
        """Tools > Knowledge Snapshots...: the snapshots with full path, name and size; it deletes nothing."""
        self.act_snapshots = QAction(ui_text("main.tools.snapshots"), self)
        self.act_snapshots.setObjectName("act_knowledge_snapshots")
        self.act_snapshots.triggered.connect(self._open_knowledge_snapshots)
        menu.addAction(self.act_snapshots)

    def _add_framework_docs_action(self, menu):
        """Tools > Index the Framework's Documentation...: the framework's own documents into the knowledge base, after
        a question (N12)."""
        from orchestrator.gui.framework_docs_action import FrameworkDocsAction
        self.framework_docs = FrameworkDocsAction(self, self.framework)
        self.act_framework_docs = QAction(ui_text("main.tools.index_docs"), self)
        self.act_framework_docs.setObjectName("act_knowledge_index_docs")
        self.act_framework_docs.triggered.connect(lambda: self.framework_docs.run())
        menu.addAction(self.act_framework_docs)

    def _open_knowledge_snapshots(self):
        from orchestrator.gui.knowledge_snapshots_window import KnowledgeSnapshotsDialog
        try:
            KnowledgeSnapshotsDialog(self.framework.config.user_state_dir, self, framework=self.framework).exec()
        except Exception as e:  # e.g. no per-user folder; the error's text is shown as plain text (SEC-M2)
            plain_box(self, "critical", "Knowledge Snapshots", str(e))

    def _add_trash_action(self, menu):
        """File > Trash...: what deletions moved to the trash; only the person restores or empties it."""
        self.act_trash = QAction(ui_text("main.file.trash"), self)
        self.act_trash.setObjectName("act_file_trash")   # `llm-cli trash` by mouse (menus/existing.py)
        self.act_trash.triggered.connect(self._open_trash)
        menu.addAction(self.act_trash)

    def _open_trash(self):
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.trash_window import TrashDialog
        try:
            TrashDialog(Trash.for_framework(self.framework), self).exec()
        except Exception as e:  # e.g. no per-user folder; plain text (SEC-M2)
            plain_box(self, "critical", "Trash", str(e))

    def _add_output_folder_action(self, menu):
        """File > Output Folder...: where finished models are saved (eighth round, answer 3); set once by the person,
        never by an AI. Models in the old folder stay where they are and stay listed."""
        self.act_output_folder = QAction(ui_text("main.file.output_folder"), self)
        self.act_output_folder.setObjectName("act_file_output_folder")
        self.act_output_folder.triggered.connect(self._choose_output_folder)
        menu.addAction(self.act_output_folder)

    def _choose_output_folder(self):
        from orchestrator.Core.artifact_output import DestinationRefused, output_folder, set_output_folder
        from orchestrator.gui.artifact_save import choose_folder
        current = output_folder(self.framework)
        chosen = choose_folder(self, ui_text("output.title", path=str(current)), str(current))
        if not chosen:
            return
        try:
            folder = set_output_folder(self.framework, chosen)
        except DestinationRefused as e:
            plain_box(self, "warning", "Output Folder", ui_text("output.refused", reason=f"{e} {e.next}".strip()))
            return
        except OSError:
            plain_box(self, "warning", "Output Folder", ui_text("output.not_saved"))
            return
        self.status.showMessage(ui_text("output.set", path=str(folder)), 10000)

    def _add_framework_images_action(self, menu):
        """Tools > Framework Build Images...: the framework's Docker images, deleted only as the person ticks them."""
        self.act_images = QAction(ui_text("main.tools.images"), self)
        self.act_images.setObjectName("act_framework_images")
        self.act_images.triggered.connect(self._open_framework_images)
        menu.addAction(self.act_images)

    def _open_framework_images(self):
        from orchestrator.gui.deletion_dialog import free_framework_images
        try:
            free_framework_images(self, self.framework)
        except Exception as e:  # plain text (SEC-M2)
            plain_box(self, "critical", "Framework Build Images", str(e))

    def _open_ai_folders(self):
        from orchestrator.gui.dialogs import AIFoldersDialog
        try:
            AIFoldersDialog(self.framework, getattr(self.framework, "tool_registry", None), self).exec()
        except Exception as e:  # e.g. no per-user folder
            plain_box(self, "critical", "Folders the AI may use", str(e))

    def _set_audio_announcements(self, enabled: bool):
        cfg = self.framework.config
        cfg.set(AUDIO_SETTING, bool(enabled))
        if not cfg.save_user_config():
            QMessageBox.warning(self, self.i18n.t("settings.not_saved.title"), self.i18n.t("settings.not_saved"))

    def _clear_input_history(self):
        """Asks with the full list of what goes (full path, file name, size, what it is; "No" is the default) and
        deletes only the files it listed: every deletion the person can see lists exactly what it deletes."""
        from orchestrator.gui.chat_panel.panel import size_text
        t = self.i18n.t
        history = input_history.for_config(self.framework.config)
        if history is None:
            QMessageBox.warning(self, t("history.clear.title"), t("history.clear.failed"))
            return
        files = history.files()
        if not files:
            QMessageBox.information(self, t("history.clear.title"), ui_text("chat.clear.input_history.empty"))
            return
        lang, items = current_language(), []
        for path, size in files:
            kind = "damaged_" if path != history.path else ""
            items.append("\n".join((
                ui_text(f"chat.clear.input_history.{kind}name"),
                ui_text("chat.clear.path", path=one_line(str(path.resolve()))),
                ui_text("chat.clear.file", name=one_line(path.name)),
                ui_text("chat.clear.size", size=size_text(size, lang)),
                ui_text("chat.clear.what_label", what=ui_text(f"chat.clear.input_history.{kind}what")))))
        if not ask(self, t("history.clear.title"), t("history.clear.question") + "\n\n" + "\n\n".join(items)):
            return
        listed = {path for path, _ in files}
        if [f for f in history.files() if f[0] in listed] != files:   # changed while asked: the list is not true
            QMessageBox.warning(self, t("history.clear.title"), ui_text("chat.clear.input_history.changed"))
            return
        if not history.clear(only=listed):
            QMessageBox.warning(self, t("history.clear.title"), t("history.clear.failed"))
            return
        self.builder_tab.reload_history()
        self.deployment_window.reload_history()
        self.status.showMessage(t("history.cleared"), 5000)

    def _open_module_wizard(self, *_args, probe_path=None):
        targets_dir = self.app_root / self.framework.config.get("targets_dir", "targets")
        wizard = ModuleCreationWizard(targets_dir, self)
        if probe_path:  # the Builder's offer when no target fits a probe: the wizard starts from that probe
            wizard.import_probe_file(str(probe_path))
        wizard.exec()
        if self.framework.target_manager:
            self.framework.target_manager.initialize()
            self.builder_tab._refresh_targets()
            self.deployment_window.refresh_data()

    def _open_hf_browser(self):
        """Search Hugging Face, check a model's licence and download single files into the models folder."""
        from orchestrator.gui.huggingface_window import HuggingFaceWindow
        if getattr(self, "hf_browser", None) is None:
            self.hf_browser = HuggingFaceWindow(self.framework, self)
        self.hf_browser.show()
        self.hf_browser.raise_()

    def _open_community_hub(self):
        from orchestrator.gui.community_hub import CommunityHubWindow
        if getattr(self, "community_hub", None) is None:
            self.community_hub = CommunityHubWindow(self.framework, self, on_build_recipe=self._build_recipe)
            self.community_hub.installed.connect(self._on_community_installed)
        self.community_hub.show()
        self.community_hub.raise_()

    def _build_recipe(self, recipe):
        """'Build' on a recipe in the Community Hub: the Builder is filled in, the user starts the build."""
        self.tabs.setCurrentWidget(self.builder_tab)
        self.builder_tab.apply_recipe(recipe)
        self.raise_()

    def _on_community_installed(self, kind: str):
        if kind == "target":
            self.builder_tab._refresh_targets()
            self.deployment_window.refresh_data()

    def _proposal_store(self):
        from orchestrator.Core.client_proposals import ClientProposalStore
        state_dir = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        return ClientProposalStore(state_dir) if isinstance(state_dir, (str, Path)) else None

    OTHER_BUILDS_EVERY = 10.0  # seconds between two reads of the shared build state

    def _show_other_builds(self) -> None:
        """Builds that another framework process (a CLI call, an MCP server) runs, from the shared build state in the
        per-user folder, in the status bar: the person sees every build, wherever it was started."""
        import time as _time
        now = _time.monotonic()
        if now - getattr(self, "_other_builds_at", -1e9) < self.OTHER_BUILDS_EVERY:
            return
        self._other_builds_at = now
        store = getattr(getattr(self.framework, "orchestrator", None), "request_store", None)
        try:
            running = [r for r in (store.read() if store is not None else [])
                       if r.get("pid") != os.getpid()
                       and r.get("status") in ("queued", "preparing", "building", "healing", "awaiting_approval")]
        except OSError:
            return
        if running:
            latest = running[0]
            summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
            self.status.showMessage(
                f"{len(running)} build(s) in other framework processes; newest: {summary.get('model') or '?'} for "
                f"{summary.get('target') or '?'} ({latest.get('status')}, {latest.get('program')}). "
                + ui_text("bld.history.status_hint"), int(self.OTHER_BUILDS_EVERY * 1000) + 1000)

    def _notice_client_proposals(self):
        """A status bar notice when an AI client proposed a new fix (also from an MCP server in another process)."""
        store = self._proposal_store()
        try:
            proposals = store.list() if store is not None else []
        except Exception:
            return
        seen = getattr(self, "_proposals_seen", None)
        ids = {p.proposal_id for p in proposals}
        self._proposals_seen = ids
        if seen is None:  # the ones from before this window opened are not news
            return
        new = [p for p in proposals if p.proposal_id not in seen]
        if new:
            from orchestrator.gui.proposals_window import proposal_notice
            self.status.showMessage(proposal_notice(new[-1]), 15000)
            chat = getattr(self, "chat_window", None)
            for p in new if chat is not None else ():
                # Under the proposing app's bubble (its trusted name; its own name only as its claim, R4, R5); the
                # build's label is quoted: it names a model, which is not the framework's word.
                sender = _app_sender(p.proposed_by, p.client)
                if sender is not None:
                    chat.post_notice("chat.notice.proposal_by", {"build": p.request or p.request_id},
                                     external=("build",), sender=sender)
                else:
                    chat.post_notice("chat.notice.proposal", {"who": p.who(), "build": p.request or p.request_id},
                                     external=("who", "build"))

    def _open_client_proposals(self):
        from orchestrator.gui.proposals_window import ClientProposalsDialog
        store = self._proposal_store()
        if store is None:
            QMessageBox.critical(self, "Error", "Fix proposals are not available.")
            return
        ClientProposalsDialog(store, self).exec()

    def trigger_healing_dialog(self, request_id, proposal):
        """Shows the proposal; the orchestrator applies an approved fix and retries the build."""
        dlg = HealingConfirmDialog(proposal, self, free_docker_space=self._open_framework_images)
        approved = bool(dlg.exec()) and dlg.automatic
        if not self.framework.orchestrator.resolve_healing(request_id, approved):
            self.status.showMessage("The healing proposal is no longer pending.", 5000)
        elif approved:
            self.status.showMessage("✅ Fix approved; applying it.", 5000)

    def _show_about(self):
        ver = self.framework.info.version if hasattr(self.framework, 'info') else "Unknown"
        plain_box(self, "information", "About",
                  f"LLM Cross-Compiler Framework\nVersion: {ver}\n\n(c) 2025 Framework Team")

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; color: #ffffff; }
            QTabWidget::pane { border: 1px solid #444; }
            QTabBar::tab { background: #333; color: #fff; padding: 8px; }
            QTabBar::tab:selected { background: #505050; border-bottom: 2px solid #2196F3; }
            QLabel { color: #fff; }
            QMenuBar { background-color: #333; color: #fff; }
            QMenuBar::item:selected { background-color: #555; }
            QMenu { background-color: #333; color: #fff; border: 1px solid #555; }
            QMenu::item:selected { background-color: #555; }
            QToolBar { background-color: #333; border-bottom: 1px solid #555; }
            QStatusBar { background-color: #333; color: #aaa; }
            QTextEdit, QLineEdit { background-color: #1e1e1e; color: #eee; border: 1px solid #555; }
            QPushButton { background-color: #444; color: #fff; padding: 5px; border-radius: 3px; }
            QPushButton:hover { background-color: #555; }
        """)

    def closeEvent(self, event):
        close_gui(getattr(self, "gui_surface", None))  # the GUI tools are hidden again
        chat = getattr(self, "chat_window", None)
        if chat is not None:
            self._save_gui_state()
            chat.close()  # a tool call waiting for a confirmation is answered "no"
        lock = getattr(self, "_gui_lock", None)
        if lock is not None:  # the command line may offer "do not ask again" again
            lock.release()
        if self.framework:
            self.framework.shutdown()
        event.accept()
