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
import yaml
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

# GUI Imports
# Note: These will fail if files are missing. We wrap them for better error messages.
try:
    from orchestrator.gui.main_window import MainOrchestrator
    from orchestrator.gui.dialogs import LanguageSelectionDialog
    from orchestrator.utils.localization import get_instance as get_i18n
except ImportError as e:
    print(f"CRITICAL ERROR: Missing GUI components.\n{e}")
    print("Please ensure 'orchestrator/gui/main_window.py', 'dialogs.py' and 'utils/localization.py' exist.")
    sys.exit(1)

def main():
    # High DPI Scaling for modern screens
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    app = QApplication(sys.argv)
    app.setApplicationName("LLM Cross-Compiler")
    
    # Determine App Root
    app_root = root_dir
    
    # 1. Load Config (User Preferences)
    # We look for config.yml in the 'configs' folder
    configs_dir = app_root / "configs"
    config_file = configs_dir / "config.yml"
    
    # Initialize ConfigManager (v2.3.0 API: takes path string or None)
    # If file doesn't exist yet, it loads defaults
    cfg_mgr = ConfigManager(str(config_file) if config_file.exists() else None)
    
    language = cfg_mgr.get("language")
    
    # 2. First Run / Language Selection
    if not language:
        # Initialize I18n with default English for the dialog
        get_i18n("en")
        
        dlg = LanguageSelectionDialog()
        if dlg.exec() == QDialog.Accepted:
            language = dlg.selected_lang
            
            # Persist Language Selection manually to ensure it's saved immediately
            try:
                configs_dir.mkdir(parents=True, exist_ok=True)
                data = {}
                
                if config_file.exists():
                    with open(config_file, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f) or {}
                
                data["language"] = language
                
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f)
                    
                # Update runtime config manager
                cfg_mgr.set("language", language)
                
            except Exception as e:
                print(f"Warning: Could not save language preference: {e}")
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
