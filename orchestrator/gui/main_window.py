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

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget, QStatusBar,
    QMessageBox, QToolBar,
    QSizePolicy
)
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtCore import Qt, QSize, QTimer

# Core Framework
from orchestrator.Core.framework import FrameworkManager
from orchestrator.Core.orchestrator import OrchestrationStatus
from orchestrator.utils import input_history
from orchestrator.utils.announce import SETTING as AUDIO_SETTING
from orchestrator.utils.localization import get_instance as get_i18n

# GUI Components
from orchestrator.gui.dialogs import SecretInputDialog, HealingConfirmDialog, AIConfigurationDialog, ToolPermissionsDialog
from orchestrator.gui.wizards import ModuleCreationWizard
from orchestrator.gui.builder_tab import BuilderTab
from orchestrator.gui.chat_window import ChatWindow
from orchestrator.gui.sign_in import STORAGE_CHOICE_CODES, UserConfigSnapshot, choose_storage_at_start
from orchestrator.gui.deployment_window import DeploymentWindow

# ============================================================================
# DITTO AVATAR WIDGET
# ============================================================================

class DittoAvatar(QLabel):
    """Displays the AI state using sprite assets."""
    def __init__(self, asset_dir: Path):
        super().__init__()
        self.asset_dir = asset_dir
        self.setFixedSize(64, 64)
        self.setScaledContents(True)
        
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
            QMessageBox.critical(self, "Fatal Error", f"Framework initialization failed: {cause}" if cause
                                 else "Framework initialization failed. Check logs.")
            sys.exit(1)
            
        self.setWindowTitle(f"LLM Cross-Compiler Framework v{self.framework.info.version} (Enterprise)")
        self.resize(1280, 850)
        
        # Setup UI
        self._setup_menu()
        self._setup_toolbar()
        self._setup_central_widget()
        self._setup_statusbar()
        self._apply_styles()
        
        # Start State Polling (Live Feedback Loop)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_system_state)
        self.update_timer.start(1000) # Every second

        # Ditto's first setup: ask for the tool permissions once, right after the window is shown.
        registry = getattr(self.framework, "tool_registry", None)
        if registry is not None and not registry.policy().configured:
            QTimer.singleShot(0, lambda: self._open_tool_permissions(first_run=True))

    def _setup_menu(self):
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu(self.i18n.t("menu.file", "File"))
        exit_action = QAction(self.i18n.t("menu.exit", "Exit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        sec_menu = menubar.addMenu("Security")
        act_openai = QAction("Set OpenAI API Key", self)
        act_openai.triggered.connect(lambda: self._open_secret_dialog("openai_api_key", "OpenAI API Key"))
        sec_menu.addAction(act_openai)
        

        tools_menu = menubar.addMenu("Tools")
        act_ai = QAction("Configure AI Agent (Ditto)...", self)
        act_ai.triggered.connect(self._open_ai_config)
        tools_menu.addAction(act_ai)

        act_wizard = QAction("Create Hardware Module...", self)
        act_wizard.triggered.connect(self._open_module_wizard)
        tools_menu.addAction(act_wizard)

        act_hf = QAction("Hugging Face Model Browser...", self)
        act_hf.triggered.connect(self._open_hf_browser)
        tools_menu.addAction(act_hf)

        act_hub = QAction(self.i18n.t("menu.open_hub", "Community Hub"), self)
        act_hub.triggered.connect(self._open_community_hub)
        tools_menu.addAction(act_hub)

        act_perms = QAction("Tool Permissions (Ditto / MCP)...", self)
        act_perms.triggered.connect(self._open_tool_permissions)
        tools_menu.addAction(act_perms)

        act_folders = QAction("Folders the AI May Use...", self)
        act_folders.triggered.connect(self._open_ai_folders)
        tools_menu.addAction(act_folders)

        act_approvals = QAction("Pending Approvals...", self)  # calls of AI clients that could not ask you
        act_approvals.triggered.connect(self._open_approvals)
        tools_menu.addAction(act_approvals)

        act_proposals = QAction("Fix Proposals from AI Clients...", self)  # propose_healing_fix, never run
        act_proposals.triggered.connect(self._open_client_proposals)
        tools_menu.addAction(act_proposals)

        # Spoken announcements are extra (everything is also shown); e.g. screen-reader users switch them off.
        self.act_audio = QAction(self.i18n.t("menu.audio", "Spoken Announcements"), self)
        self.act_audio.setCheckable(True)
        self.act_audio.setChecked(bool(self.framework.config.get(AUDIO_SETTING, True)))
        self.act_audio.toggled.connect(self._set_audio_announcements)
        tools_menu.addAction(self.act_audio)

        # Recent models, targets, boards, URLs, devices and datasets offered in the input fields (per user)
        self.act_clear_history = QAction(self.i18n.t("menu.clear_history", "Clear Input History..."), self)
        self.act_clear_history.triggered.connect(self._clear_input_history)
        tools_menu.addAction(self.act_clear_history)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(32, 32))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Spacer to push Ditto to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        
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
        self.tabs.addTab(self.builder_tab, "🏗️ Builder")
        
        # 2. Chat Window
        self.chat_window = ChatWindow(self.framework)
        self.tabs.addTab(self.chat_window, "💬 AI Chat")
        
        # 3. Deployment Window
        self.deployment_window = DeploymentWindow(self.framework)
        self.tabs.addTab(self.deployment_window, "🚀 Deployment")
        
        # 4. NEW: Hyperparameter Tuning (Roadmap)
        self.tuning_tab = QLabel("\n\n🚧 Hyperparameter Auto-Tuning Module\n\nComing soon: Automated quantization matrix benchmarking.")
        self.tuning_tab.setAlignment(Qt.AlignCenter)
        self.tabs.addTab(self.tuning_tab, "🛠️ Auto-Tuning")

        # 5. Model Manager Placeholder
        self.tabs.addTab(QLabel("Model Manager (Local Library)"), "🧠 Models")

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
        covers = ""
        try:
            found = registry.policy().grant_by_id(grant_id)
            covers = f" ({found[1].summary})" if found else ""
        except Exception:
            pass
        self.status.showMessage(f"{actor_label(actor)} ran '{tool_title(registry, tool)}' under your standing "
                                f"approval{covers}{detail}.", 30000)
        self.btn_revoke_grant.show()
        QTimer.singleShot(30000, self.btn_revoke_grant.hide)

    def _revoke_used_grant(self):
        registry = getattr(self.framework, "tool_registry", None)
        if registry is None or not self._used_grant:
            return
        try:
            registry.revoke_grant(self._used_grant, "gui")
        except Exception as e:
            QMessageBox.critical(self, "Standing approval", f"Could not revoke it: {e}")
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
        QMessageBox.information(self, "Standing approvals created on the command line",
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
                tray.showMessage("LLM Cross-Compiler Framework",
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
        
        if not active_flows:
            self.ditto_avatar.set_state("idle")
            self.lbl_ditto_status.setText("Idle")
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

    def _open_ai_folders(self):
        from orchestrator.gui.dialogs import AIFoldersDialog
        try:
            AIFoldersDialog(self.framework, getattr(self.framework, "tool_registry", None), self).exec()
        except Exception as e:  # e.g. no per-user folder
            QMessageBox.critical(self, "Folders the AI may use", str(e))

    def _set_audio_announcements(self, enabled: bool):
        cfg = self.framework.config
        cfg.set(AUDIO_SETTING, bool(enabled))
        if not cfg.save_user_config():
            QMessageBox.warning(self, self.i18n.t("settings.not_saved.title"), self.i18n.t("settings.not_saved"))

    def _clear_input_history(self):
        t = self.i18n.t
        answer = QMessageBox.question(self, t("history.clear.title"), t("history.clear.question"),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        history = input_history.for_config(self.framework.config)
        if history is None or not history.clear():
            QMessageBox.warning(self, t("history.clear.title"), t("history.clear.failed"))
            return
        self.builder_tab.reload_history()
        self.deployment_window.reload_history()
        self.status.showMessage(t("history.cleared"), 5000)

    def _open_module_wizard(self):
        targets_dir = self.app_root / self.framework.config.get("targets_dir", "targets")
        wizard = ModuleCreationWizard(targets_dir, self)
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
            latest = new[-1]
            self.status.showMessage(f"An AI client proposed a fix for build {latest.request_id} (proposed by "
                                    f"{latest.proposed_by}, AI-generated): Tools > Fix Proposals from AI Clients.",
                                    15000)

    def _open_client_proposals(self):
        from orchestrator.gui.proposals_window import ClientProposalsDialog
        store = self._proposal_store()
        if store is None:
            QMessageBox.critical(self, "Error", "Fix proposals are not available.")
            return
        ClientProposalsDialog(store, self).exec()

    def trigger_healing_dialog(self, request_id, proposal):
        """Shows the proposal; the orchestrator applies an approved fix and retries the build."""
        dlg = HealingConfirmDialog(proposal, self)
        approved = bool(dlg.exec()) and dlg.automatic
        if not self.framework.orchestrator.resolve_healing(request_id, approved):
            self.status.showMessage("The healing proposal is no longer pending.", 5000)
        elif approved:
            self.status.showMessage("✅ Fix approved; applying it.", 5000)

    def _show_about(self):
        ver = self.framework.info.version if hasattr(self.framework, 'info') else "Unknown"
        QMessageBox.about(self, "About", f"LLM Cross-Compiler Framework\nVersion: {ver}\n\n(c) 2025 Framework Team")

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
        chat = getattr(self, "chat_window", None)
        if chat is not None:
            chat.confirmation.close()  # a tool call waiting for a confirmation is answered "no"
        lock = getattr(self, "_gui_lock", None)
        if lock is not None:  # the command line may offer "do not ask again" again
            lock.release()
        if self.framework:
            self.framework.shutdown()
        event.accept()
