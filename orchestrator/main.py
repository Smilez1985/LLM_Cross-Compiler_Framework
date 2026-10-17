#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Main Entry Point (v2.3.0)
DIREKTIVE: Goldstandard, Startup Logic, I18n.

Launches the GUI Application.
- Sets up Python Path.
- Initializes Configuration.
- Handles First-Run Language Selection.
- Boots the Main Window.
"""

import sys
import os
from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

# Adjust path to ensure modules are found
# Structure: root/orchestrator/main.py -> resolve -> parent is orchestrator -> parent is root
current_dir = Path(__file__).resolve().parent
root_dir = current_dir.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Core Imports
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.vault_key import set_passphrase_prompt
from orchestrator.llm.sdk_env import guard_sdk_environment
from orchestrator.utils.logging import setup_logging

# GUI Imports
# Note: These will fail if files are missing. We wrap them for better error messages.
try:
    from orchestrator.gui.main_window import MainOrchestrator
    from orchestrator.gui.dialogs import LanguageSelectionDialog
    from orchestrator.gui.sign_in import PassphrasePrompt
    from orchestrator.utils.localization import get_instance as get_i18n
except ImportError as e:
    print(f"CRITICAL ERROR: Missing GUI components.\n{e}")
    print("Please ensure 'orchestrator/gui/main_window.py', 'dialogs.py' and 'utils/localization.py' exist.")
    sys.exit(1)

def save_language(cfg_mgr: ConfigManager, language: str) -> bool:
    """Saves the first-run language choice to config.yml in the effective config folder (other settings there
    are kept). False if it could not be written."""
    return bool(cfg_mgr.set("language", language)) and bool(cfg_mgr.save_user_config())


def main():
    # High DPI Scaling for modern screens
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    # Determine App Root
    app_root = root_dir
    from orchestrator.Core.request_store import set_program
    set_program("gui")  # shown with this window's builds in the shared build state

    # 1. Load Config (User Preferences) from the effective config folder: LLM_FRAMEWORK_CONFIG_DIR, else the
    # installation's configs/ if this user may write there, else the per-user folder (C:\Program Files is
    # read-only for standard users). The framework started below uses the same folder.
    cfg_mgr = ConfigManager()

    # Before Qt or the framework start threads: SDK environment guard (orchestrator/llm/sdk_env.py).
    debug_http = "--debug-http" in sys.argv[1:]
    for warning in guard_sdk_environment(cfg_mgr.get("ai_allow_sdk_env_headers", False), debug_http):
        print(f"Warning: {warning}", file=sys.stderr)
    # The SDK logger levels follow the flag too; the framework's later setup_logging() keeps the choice.
    setup_logging(debug_http=debug_http)

    app = QApplication([arg for arg in sys.argv if arg != "--debug-http"])
    app.setApplicationName("LLM Cross-Compiler")
    # A vault protected by a passphrase asks for it in a dialog (also when it opens in a worker thread).
    set_passphrase_prompt(PassphrasePrompt(app))

    language = cfg_mgr.get("language")
    # The schema default 'en' is always loaded, so a first run is detected by where the value came from.
    language_value = cfg_mgr.config_values.get("language")
    first_run = language_value is None or language_value.source.source_type == "default"

    # 2. First Run / Language Selection
    if first_run:
        # Initialize I18n with default English for the dialog
        get_i18n("en")
        
        dlg = LanguageSelectionDialog()
        if dlg.exec() == QDialog.Accepted:
            language = dlg.selected_lang
            if not save_language(cfg_mgr, language):
                print(f"Warning: Could not save language preference to {cfg_mgr.config_dir}", file=sys.stderr)
        else:
            # User cancelled setup
            sys.exit(0)
    
    # 3. Initialize Global Localization with selected language
    get_i18n(language)
    
    # 4. Start Main GUI
    try:
        # MainOrchestrator usually initializes the full FrameworkManager internally
        # We pass app_root so it knows where it runs
        window = MainOrchestrator(app_root)
        window.show()
        
        sys.exit(app.exec())
    except Exception as e:
        # Fallback error dialog if crash during boot
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("Fatal Error during startup")
        msg.setInformativeText(str(e))
        msg.setWindowTitle("Error")
        msg.exec()
        raise e

if __name__ == "__main__":
    main()
