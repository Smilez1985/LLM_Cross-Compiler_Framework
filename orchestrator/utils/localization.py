#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Localization Manager (v2.3.0)
DIREKTIVE: Goldstandard, Internationalisierung (I18n).

Zweck:
Zentrale Verwaltung aller UI-Texte. Ermöglicht Umschaltung zur Laufzeit.
Hält Dictionaries für Deutsch und Englisch vor.

Updates v2.3.0:
- Updated version header.
- Robust lazy-loading singleton.
- Enhanced fallback mechanism.
"""

from PySide6.QtCore import QObject, Signal

class LocalizationManager(QObject):
    """
    Verwaltet Übersetzungen und Sprachstatus.
    Singelton-Pattern-Nutzung empfohlen.
    """
    language_changed = Signal(str) # Signalisiert Sprachwechsel an UI-Komponenten

    # Das Wörterbuch
    # Key-Format: "context.element"
    TRANSLATIONS = {
        "en": {
            # General
            "app.title": "LLM Cross-Compiler Framework",
            "btn.cancel": "Cancel",
            "btn.save": "Save",
            "btn.ok": "OK",
            "btn.close": "Close",
            "status.ready": "Ready",
            "status.error": "Error",
            
            # Main Window
            "menu.file": "File",
            "menu.tools": "Tools",
            "menu.community": "Community",
            "menu.language": "Language",
            "menu.exit": "Exit",
            "menu.import_profile": "Import Hardware Profile...",
            "menu.create_module": "Create New Module...",
            "menu.audit": "Audit Docker Image...",
            "menu.open_hub": "Open Community Hub",
            "menu.update": "Check for Updates",
            "tab.build": "Build & Monitor",
            "tab.sources": "Sources & Config",
            "grp.build_config": "Build Configuration",
            "lbl.model": "Model:",
            "lbl.target": "Target:",
            "lbl.task": "Task:",
            "lbl.quant": "Quant:",
            "btn.browse_hf": "Browse HF",
            "chk.gpu": "Use GPU",
            "chk.autobench": "Auto-Benchmark",
            "btn.start": "Start Build",
            "btn.bench": "Bench",
            "grp.progress": "Build Progress",
            
            # Wizard
            "wiz.title": "Module Creation Wizard",
            "wiz.intro.title": "Welcome",
            "wiz.intro.text": "This wizard helps you create a new hardware target definition.\nPlease choose how to proceed.",
            "wiz.grp.import": "📂 Import Hardware Probe",
            "wiz.btn.import_std": "⚡ Standard Import (Rule-Based)",
            "wiz.btn.import_ai": "🤖 AI Auto-Discovery (Ditto)",
            "wiz.btn.config_ai": "⚙️ Configure AI Agent",
            "wiz.status.waiting": "Waiting for input...",
            "wiz.lbl.manual": "Or simply click 'Next' to configure everything manually.",
            
            "wiz.page.hardware": "Hardware Information",
            "wiz.page.hardware.sub": "Define the target architecture",
            "wiz.lbl.name": "Module Name:",
            "wiz.lbl.arch": "Architecture:",
            "wiz.lbl.sdk": "SDK / Backend:",
            
            "wiz.page.docker": "Docker Environment",
            "wiz.page.docker.sub": "Configure the build container",
            "wiz.lbl.base_os": "Base OS:",
            "wiz.rad.debian": "Debian 12 (Bookworm) - Recommended",
            "wiz.rad.ubuntu": "Ubuntu 22.04 LTS - Recommended for CUDA",
            "wiz.rad.custom": "Custom Base Image",
            "wiz.lbl.packages": "System Packages (space separated):",
            
            "wiz.page.flags": "Compiler Flags",
            "wiz.page.flags.sub": "Set default optimization flags",
            "wiz.lbl.cpu_flags": "CPU Flags (GCC):",
            "wiz.lbl.cmake_flags": "CMake Flags:",
            
            "wiz.page.summary": "Summary & Generation",
            "wiz.page.summary.sub": "Review settings before generation",
            
            # Dialogs
            "dlg.lang.title": "Select Language / Sprache wählen",
            "dlg.lang.info": "Please select your preferred interface language.",
            "dlg.token.title": "Authentication Required",
            "dlg.token.info": "The model is gated. Please enter your Hugging Face Token.",
            "dlg.source.title": "Add New Source Repository",
            "dlg.source.category": "Category (Section):",
            "dlg.source.name": "Name (Key):",
            "dlg.source.url": "Git URL:",
            "dlg.source.btn_test": "Test URL",
            "dlg.source.err_url_empty": "Please enter a URL.",
            "dlg.source.testing": "Testing connection...",
            "dlg.source.success": "✅ URL is valid and reachable.",
            "dlg.source.err_status": "❌ URL returned status",
            "dlg.source.err_connect": "❌ Connection failed",
            "dlg.ai.title": "Configure AI Agent (Ditto)",
            "dlg.ai.provider": "AI Provider:",
            "dlg.ai.model": "Model:",
            "dlg.ai.api_key": "API Key:",
            "dlg.ai.base_url": "Base URL:",
            "dlg.token.get_key": "🔑 Get API Key from Hugging Face Settings",
            "dlg.token.label": "Access Token:",
            "dlg.token.btn_auth": "Authorize & Download",
            "dlg.token.err_empty": "Token cannot be empty!",
            
            # Secrets Dialog (NEW)
            "secrets.dialog_info": "This value will be encrypted and stored in your OS Keyring.",
            
            # Messages
            "msg.success": "Success",
            "msg.failed": "Failed",
            "msg.restart_required": "Restart might be required for some changes.",
            "msg.import_success": "Import Successful",
            "msg.ai_ready": "AI Ready",
            "msg.ai_thinking": "🤖 Ditto is thinking...",
            "msg.ai_complete": "✅ Analysis complete!",
            "msg.module_created": "Module created at:"
        },
        "de": {
            # General
            "app.title": "LLM Cross-Compiler Framework",
            "btn.cancel": "Abbrechen",
            "btn.save": "Speichern",
            "btn.ok": "OK",
            "btn.close": "Schließen",
            "status.ready": "Bereit",
            "status.error": "Fehler",
            
            # Main Window
            "menu.file": "Datei",
            "menu.tools": "Werkzeuge",
            "menu.community": "Community",
            "menu.language": "Sprache",
            "menu.exit": "Beenden",
            "menu.import_profile": "Hardware-Profil importieren...",
            "menu.create_module": "Neues Modul erstellen...",
            "menu.audit": "Docker Image Audit...",
            "menu.open_hub": "Community Hub öffnen",
            "menu.update": "Auf Updates prüfen",
            "tab.build": "Bauen & Überwachen",
            "tab.sources": "Quellen & Konfiguration",
            "grp.build_config": "Build Konfiguration",
            "lbl.model": "Modell:",
            "lbl.target": "Ziel (Target):",
            "lbl.task": "Aufgabe:",
            "lbl.quant": "Quantisierung:",
            "btn.browse_hf": "HF Durchsuchen",
            "chk.gpu": "GPU nutzen",
            "chk.autobench": "Auto-Benchmark",
            "btn.start": "Build starten",
            "btn.bench": "Benchmark",
            "grp.progress": "Fortschritt",
            
            # Wizard
            "wiz.title": "Modul-Erstellungs-Assistent",
            "wiz.intro.title": "Willkommen",
            "wiz.intro.text": "Dieser Assistent hilft beim Erstellen eines neuen Hardware-Ziels.\nBitte wählen Sie das Vorgehen.",
            "wiz.grp.import": "📂 Hardware Probe Importieren",
            "wiz.btn.import_std": "⚡ Standard Import (Regel-Basiert)",
            "wiz.btn.import_ai": "🤖 AI Auto-Discovery (Ditto)",
            "wiz.btn.config_ai": "⚙️ AI Konfigurieren",
            "wiz.status.waiting": "Warte auf Eingabe...",
            "wiz.lbl.manual": "Oder klicken Sie 'Weiter' für manuelle Konfiguration.",
            
            "wiz.page.hardware": "Hardware Informationen",
            "wiz.page.hardware.sub": "Zielarchitektur definieren",
            "wiz.lbl.name": "Modul Name:",
            "wiz.lbl.arch": "Architektur:",
            "wiz.lbl.sdk": "SDK / Backend:",
            
            "wiz.page.docker": "Docker Umgebung",
            "wiz.page.docker.sub": "Build-Container konfigurieren",
            "wiz.lbl.base_os": "Basis OS:",
            "wiz.rad.debian": "Debian 12 (Bookworm) - Empfohlen",
            "wiz.rad.ubuntu": "Ubuntu 22.04 LTS - Für CUDA",
            "wiz.rad.custom": "Benutzerdefiniert",
            "wiz.lbl.packages": "System Pakete (leerzeichengetrennt):",
            
            "wiz.page.flags": "Compiler Flags",
            "wiz.page.flags.sub": "Optimierungs-Flags setzen",
            "wiz.lbl.cpu_flags": "CPU Flags (GCC):",
            "wiz.lbl.cmake_flags": "CMake Flags:",
            
            "wiz.page.summary": "Zusammenfassung",
            "wiz.page.summary.sub": "Einstellungen vor Generierung prüfen",
            
            # Dialogs
            "dlg.lang.title": "Sprache wählen / Select Language",
            "dlg.lang.info": "Bitte wählen Sie Ihre bevorzugte Sprache.",
            "dlg.token.title": "Authentifizierung erforderlich",
            "dlg.token.info": "Das Modell ist geschützt. Bitte Hugging Face Token eingeben.",
            "dlg.source.title": "Neues Quell-Repository hinzufügen",
            "dlg.source.category": "Kategorie (Sektion):",
            "dlg.source.name": "Name (Key):",
            "dlg.source.url": "Git URL:",
            "dlg.source.btn_test": "URL Testen",
            "dlg.source.err_url_empty": "Bitte URL eingeben.",
            "dlg.source.testing": "Verbindung testen...",
            "dlg.source.success": "✅ URL ist gültig und erreichbar.",
            "dlg.source.err_status": "❌ URL Statusfehler",
            "dlg.source.err_connect": "❌ Verbindungsfehler",
            "dlg.ai.title": "AI Agent Konfigurieren (Ditto)",
            "dlg.ai.provider": "AI Anbieter:",
            "dlg.ai.model": "Modell:",
            "dlg.ai.api_key": "API Key:",
            "dlg.ai.base_url": "Basis URL:",
            "dlg.token.get_key": "🔑 API Key von Hugging Face holen",
            "dlg.token.label": "Zugriffs-Token:",
            "dlg.token.btn_auth": "Autorisieren & Laden",
            "dlg.token.err_empty": "Token darf nicht leer sein!",
            
            # Secrets Dialog (NEW)
            "secrets.dialog_info": "Dieser Wert wird verschlüsselt im OS Keyring gespeichert.",
            
            # Messages
            "msg.success": "Erfolg",
            "msg.failed": "Fehlgeschlagen",
            "msg.restart_required": "Neustart für einige Änderungen erforderlich.",
            "msg.import_success": "Import Erfolgreich",
            "msg.ai_ready": "AI Bereit",
            "msg.ai_thinking": "🤖 Ditto denkt nach...",
            "msg.ai_complete": "✅ Analyse abgeschlossen!",
            "msg.module_created": "Modul erstellt in:"
        }
    }

    def __init__(self, initial_lang="en"):
        super().__init__()
        self.current_lang = initial_lang if initial_lang in self.TRANSLATIONS else "en"

    def set_language(self, lang_code: str):
        """Setzt die Sprache und feuert das Change-Signal."""
        if lang_code in self.TRANSLATIONS:
            self.current_lang = lang_code
            self.language_changed.emit(lang_code)

    def t(self, key: str, default: str = None) -> str:
        """Alias für get_text (kürzer in GUI Code)."""
        return self.get_text(key, default)

    def get_text(self, key: str, default: str = None) -> str:
        """Gibt den übersetzten Text für einen Key zurück."""
        # Fallback auf Englisch, falls Key im Deutschen fehlt
        text = self.TRANSLATIONS.get(self.current_lang, {}).get(key)
        if text is None:
            text = self.TRANSLATIONS.get("en", {}).get(key)
        
        # Absolute Fallback: Default-Argument oder Key-Name
        if text is None:
            return default if default else f"[{key}]"
            
        return text

# Globale Instanz (wird in main.py initialisiert)
_instance = None

def get_instance(initial_lang="en") -> LocalizationManager:
    global _instance
    if _instance is None:
        _instance = LocalizationManager(initial_lang)
    return _instance

# Helper für einfachen Zugriff (wie Qt's tr())
def tr(key: str, default: str = None) -> str:
    if _instance:
        return _instance.get_text(key, default)
    return default if default else key
