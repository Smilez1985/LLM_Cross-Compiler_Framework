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

from typing import Any, Dict, Optional, Sequence

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
            "wiz.page.flags.sub": "Advisory: kept in the module, never applied (llama.cpp is built by the recipe)",
            "wiz.lbl.cpu_flags": "CPU Flags (GCC, advisory):",
            "wiz.lbl.cmake_flags": "CMake Flags (advisory):",
            
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
            "secrets.dialog_info": "This value is stored in the encrypted credential vault.",

            # Sign-in (AI settings > Sign in..., Ditto's chat header)
            "dlg.ai.sign_in": "Sign in...",
            "dlg.ai.sign_in_tip": "Sign in, check or sign out for this provider (API key, ant CLI profile, ...)",
            "signin.title": "Sign in to {provider}",
            "signin.methods": "Sign-in methods",
            "signin.type.none": "No sign-in",
            "signin.type.bearer_optional": "API key (optional)",
            "signin.type.api_key": "API key",
            "signin.type.cli_session": "Command-line sign-in",
            "signin.type.oauth_pkce_loopback": "Sign in with the browser",
            "signin.type.oauth_other": "Sign in with a code",
            "signin.type.oauth_device_code": "Sign in on another device",
            "signin.type.cloud_credential_chain": "Cloud credentials",
            "signin.type.short_lived_token": "Short-lived token",
            "signin.recommended": "Recommended",
            "signin.advanced": "Advanced: only when chosen",
            "signin.not_built": "Not available in this version",
            "signin.check": "Check",
            "signin.save": "Save",
            "signin.sign_out": "Sign out",
            "signin.close": "Close",
            "signin.open_key_page": "Open key page",
            "signin.key": "API key:",
            "signin.key_placeholder": "Paste the key",
            "signin.key_optional": "Optional key (empty: no key)",
            "signin.key_stored": "Stored key ...{last4} (paste a new one to replace it)",
            "signin.key_where": "Where to find it: {where}",
            "signin.key_needed": "Paste the key first.",
            "signin.checking": "Checking...",
            "signin.working": "Working...",
            "signin.plain_http.title": "Unencrypted connection",
            "signin.plain_http": "{origin} uses plain http://, so the key travels unencrypted to another computer. "
                                 "Send it anyway?",
            "signin.cancelled": "Cancelled: the key was not sent.",
            "signin.saved": "Saved.",
            "signin.signed_out": "Signed out: the stored key was deleted from this computer.",
            "signin.nothing_stored": "Nothing was stored here.",
            "signin.revoke": "The key itself keeps working until you revoke it on the key page: {url}",
            "signin.env_left": "{name} is still set, so Ditto keeps using the key from it.",
            "signin.confirm_sign_out": "Delete the stored sign-in of {provider} from this computer?",
            "signin.none_info": "This server needs no sign-in. Check lists its models.",
            "signin.use_method": "Use this method",
            "signin.managed_info": "The managed local server gets a new key at every start; the framework passes it and never shows it.",
            "signin.later_info": "This sign-in method comes in a later version.",
            "signin.cli_info": "Uses the profile of the official command-line tool. Sign in there first:",
            "signin.copy_command": "Copy command",
            "signin.install_cli": "Install the CLI",
            "signin.profile": "Profile:",
            "signin.profile_placeholder": "empty: the active profile",
            "signin.use_profile": "Use this profile",
            "signin.stop_using": "Stop using",
            "signin.status.signed_in_as": "Signed in as {account}",
            "signin.status.signed_in_key": "Signed in (key ...{last4})",
            "signin.status.signed_in": "Signed in",
            "signin.status.env": "Using key from environment variable {name}",
            "signin.status.profile": "Using the ant CLI profile '{name}'",
            "signin.status.profile_active": "Using the active ant CLI profile",
            "signin.status.managed": "The key of the managed local server is set automatically",
            "signin.status.no_sign_in": "No sign-in needed",
            "signin.status.not_signed_in": "Not signed in",
            "signin.status.expired": "Expired: sign in again",
            "signin.status.loading": "Reading the sign-in...",
            "signin.storage.title": "Choose how to protect your credentials",
            "signin.storage.passphrase": "Passphrase, asked once per start",
            "signin.storage.helper": "Credential helper command, e.g. pass show llmcc/vault-key",
            "signin.storage.helper_placeholder": "command that prints the vault key",
            "signin.storage.insecure": "Key file next to the vault (NOT encrypted at rest)",
            "signin.storage.insecure_ack": "I understand that this key file is not encrypted",
            "signin.storage.use": "Use this storage",
            "signin.storage.helper_needed": "Enter the helper command.",
            "signin.storage.ack_needed": "Confirm that the key file is not encrypted.",
            "signin.storage.done": "The credential vault is now protected with '{mode}'.",
            "signin.passphrase.title": "Credential vault",
            "signin.passphrase.ask": "Passphrase of the credential vault:",
            "signin.passphrase.new": "New passphrase for the credential vault (8 characters or more):",
            "signin.passphrase.repeat": "Repeat the passphrase:",
            "signin.passphrase.mismatch": "The passphrases do not match.",
            "chat.access.local": "Local",
            "chat.access.env": "key from {name}",
            "chat.access.profile": "ant profile {name}",
            "chat.access.profile_active": "active ant profile",
            "chat.access.not_signed_in": "not signed in",
            "chat.access.not_set_up": "AI not set up",
            
            # Messages
            "msg.success": "Success",
            "msg.failed": "Failed",
            "msg.restart_required": "Restart might be required for some changes.",
            "msg.import_success": "Import Successful",
            "msg.ai_ready": "AI Ready",
            "msg.ai_thinking": "🤖 Ditto is thinking...",
            "msg.ai_complete": "✅ Analysis complete!",
            "msg.module_created": "Module created at:",

            # Deep Ingest: one-time notice (information, not a legal confirmation), progress and outcomes
            "menu.audio": "Spoken Announcements",
            "settings.not_saved.title": "Setting Not Saved",
            "settings.not_saved": ("The change applies until the program ends, but it could not be saved to "
                                   "config.yml. See the log."),
            "crawl.notice.title": "Before your first Deep Ingest",
            "crawl.notice.text": (
                "Deep Ingest reads web pages into your knowledge base so that Ditto can use them.\n\n"
                "• What is read: only publicly reachable pages (no logins), starting from the addresses you "
                "enter, within the page and depth limits.\n"
                "• Opt-outs are honoured: robots.txt, the X-Robots-Tag header and robots meta tags (noindex, "
                "none, noai, noimageai; nofollow stops following links) and the TDM Reservation Protocol (TDMRep). "
                "Pages whose owner reserved text and data mining are skipped and named in the crawl log.\n"
                "• The knowledge stays on this computer: pages are stored as text in your local knowledge base "
                "(or on your own Qdrant server, if you set one). When Ditto answers, matching excerpts go to the "
                "AI provider you selected; with a local provider nothing leaves this computer.\n"
                "• Sharing needs your release: a knowledge snapshot in a target or a community pack keeps the "
                "source and licence of every page and is only shared after you release it explicitly. It carries a "
                "page's full text only where the page's licence allows passing it on; every other page goes out as "
                "its address and short facts.\n\n"
                "Text and data mining of publicly accessible content is permitted unless the rightsholder reserved "
                "it in machine-readable form (EU Directive 2019/790, Art. 4; German UrhG § 44b). That is why "
                "these signals decide. This notice is information, not a legal confirmation."),
            "crawl.notice.ok": "&Understood",
            "crawl.notice.spoken": "Please read the notice before your first Deep Ingest.",
            "crawl.btn.stop": "⏹ Stop",
            "crawl.stopping": "Stopping (a page that is still arriving is abandoned; stored pages stay)...",
            "crawl.progress.start": "Starting Deep Ingest...",
            "crawl.progress.page": "Page {n} of {m}: {url}",
            "crawl.done.success": "Deep Ingest finished: {pages} pages ({chunks} chunks) are now searchable.",
            "crawl.done.partial": ("Deep Ingest partly finished: {pages} pages ({chunks} chunks) are searchable; "
                                   "other pages were left out or failed. See the log."),
            "crawl.done.stopped": "Deep Ingest stopped: {pages} pages ({chunks} chunks) are kept and searchable.",
            "crawl.done.stopped_empty": "Deep Ingest stopped: nothing was stored.",
            "crawl.done.failure": "Deep Ingest failed: nothing was stored. See the log.",
            "crawl.finish_waits": "Deep Ingest is still running. The module can be created once it has finished or "
                                  "was stopped, so that a knowledge snapshot holds every page.",
            "crawl.snapshot_missing": ("The knowledge snapshot was not attached: no stored page comes from the crawled "
                                       "URLs, or the knowledge base could not be read. See the log."),

            # Input history (recent values in the input fields, per user)
            "menu.clear_history": "Clear Input History...",
            "history.clear.title": "Input History",
            "history.clear.question": ("Forget the recently used models, targets, boards, URLs, devices and "
                                       "datasets that the input fields offer?"),
            "history.clear.failed": "The input history could not be cleared. See the log.",
            "history.cleared": "Input history cleared."
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
            "wiz.page.flags.sub": "Nur Hinweis: im Modul vermerkt, nie angewendet (llama.cpp wird nach dem Rezept gebaut)",
            "wiz.lbl.cpu_flags": "CPU-Flags (GCC, nur Hinweis):",
            "wiz.lbl.cmake_flags": "CMake-Flags (nur Hinweis):",
            
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
            "secrets.dialog_info": "Dieser Wert wird im verschlüsselten Tresor für Zugangsdaten gespeichert.",

            # Sign-in (AI settings > Sign in..., Ditto's chat header)
            "dlg.ai.sign_in": "Anmelden...",
            "dlg.ai.sign_in_tip": "Für diesen Anbieter anmelden, prüfen oder abmelden (API-Schlüssel, Profil der ant-CLI, ...)",
            "signin.title": "Bei {provider} anmelden",
            "signin.methods": "Anmeldeverfahren",
            "signin.type.none": "Keine Anmeldung",
            "signin.type.bearer_optional": "API-Schlüssel (optional)",
            "signin.type.api_key": "API-Schlüssel",
            "signin.type.cli_session": "Anmeldung über die Kommandozeile",
            "signin.type.oauth_pkce_loopback": "Anmeldung im Browser",
            "signin.type.oauth_other": "Anmeldung mit einem Code",
            "signin.type.oauth_device_code": "Anmeldung auf einem anderen Gerät",
            "signin.type.cloud_credential_chain": "Zugangsdaten der Cloud",
            "signin.type.short_lived_token": "Kurzlebiges Token",
            "signin.recommended": "Empfohlen",
            "signin.advanced": "Erweitert: nur wenn gewählt",
            "signin.not_built": "In dieser Version nicht verfügbar",
            "signin.check": "Prüfen",
            "signin.save": "Speichern",
            "signin.sign_out": "Abmelden",
            "signin.close": "Schließen",
            "signin.open_key_page": "Schlüsselseite öffnen",
            "signin.key": "API-Schlüssel:",
            "signin.key_placeholder": "Schlüssel einfügen",
            "signin.key_optional": "Optionaler Schlüssel (leer: ohne Schlüssel)",
            "signin.key_stored": "Gespeicherter Schlüssel ...{last4} (zum Ersetzen einen neuen einfügen)",
            "signin.key_where": "Wo du ihn findest: {where}",
            "signin.key_needed": "Füge zuerst den Schlüssel ein.",
            "signin.checking": "Wird geprüft...",
            "signin.working": "Wird ausgeführt...",
            "signin.plain_http.title": "Unverschlüsselte Verbindung",
            "signin.plain_http": "{origin} nutzt einfaches http://, der Schlüssel geht also unverschlüsselt an einen "
                                 "anderen Computer. Trotzdem senden?",
            "signin.cancelled": "Abgebrochen: Der Schlüssel wurde nicht gesendet.",
            "signin.saved": "Gespeichert.",
            "signin.signed_out": "Abgemeldet: Der gespeicherte Schlüssel wurde von diesem Computer gelöscht.",
            "signin.nothing_stored": "Hier war nichts gespeichert.",
            "signin.revoke": "Der Schlüssel selbst funktioniert weiter, bis du ihn auf der Schlüsselseite widerrufst: {url}",
            "signin.env_left": "{name} ist noch gesetzt, Ditto nutzt also weiter den Schlüssel daraus.",
            "signin.confirm_sign_out": "Die gespeicherte Anmeldung bei {provider} von diesem Computer löschen?",
            "signin.none_info": "Dieser Server braucht keine Anmeldung. Prüfen listet seine Modelle.",
            "signin.use_method": "Dieses Verfahren verwenden",
            "signin.managed_info": "Der verwaltete lokale Server bekommt bei jedem Start einen neuen Schlüssel; das Framework übergibt ihn und zeigt ihn nie an.",
            "signin.later_info": "Dieses Anmeldeverfahren kommt in einer späteren Version.",
            "signin.cli_info": "Nutzt das Profil des offiziellen Kommandozeilenwerkzeugs. Melde dich dort zuerst an:",
            "signin.copy_command": "Befehl kopieren",
            "signin.install_cli": "CLI installieren",
            "signin.profile": "Profil:",
            "signin.profile_placeholder": "leer: das aktive Profil",
            "signin.use_profile": "Dieses Profil verwenden",
            "signin.stop_using": "Nicht mehr verwenden",
            "signin.status.signed_in_as": "Angemeldet als {account}",
            "signin.status.signed_in_key": "Angemeldet (Schlüssel ...{last4})",
            "signin.status.signed_in": "Angemeldet",
            "signin.status.env": "Schlüssel aus der Umgebungsvariable {name}",
            "signin.status.profile": "Profil '{name}' der ant-CLI",
            "signin.status.profile_active": "Aktives Profil der ant-CLI",
            "signin.status.managed": "Der Schlüssel des verwalteten lokalen Servers wird automatisch gesetzt",
            "signin.status.no_sign_in": "Keine Anmeldung nötig",
            "signin.status.not_signed_in": "Nicht angemeldet",
            "signin.status.expired": "Abgelaufen: bitte erneut anmelden",
            "signin.status.loading": "Anmeldung wird gelesen...",
            "signin.storage.title": "Wähle, wie deine Zugangsdaten geschützt werden",
            "signin.storage.passphrase": "Passphrase, einmal pro Start abgefragt",
            "signin.storage.helper": "Befehl eines Credential-Helpers, z. B. pass show llmcc/vault-key",
            "signin.storage.helper_placeholder": "Befehl, der den Tresorschlüssel ausgibt",
            "signin.storage.insecure": "Schlüsseldatei neben dem Tresor (NICHT verschlüsselt gespeichert)",
            "signin.storage.insecure_ack": "Mir ist klar, dass diese Schlüsseldatei nicht verschlüsselt ist",
            "signin.storage.use": "Diesen Schutz verwenden",
            "signin.storage.helper_needed": "Gib den Befehl des Helpers ein.",
            "signin.storage.ack_needed": "Bestätige, dass die Schlüsseldatei nicht verschlüsselt ist.",
            "signin.storage.done": "Der Tresor ist jetzt mit '{mode}' geschützt.",
            "signin.passphrase.title": "Tresor für Zugangsdaten",
            "signin.passphrase.ask": "Passphrase des Tresors:",
            "signin.passphrase.new": "Neue Passphrase für den Tresor (8 Zeichen oder mehr):",
            "signin.passphrase.repeat": "Passphrase wiederholen:",
            "signin.passphrase.mismatch": "Die Passphrasen stimmen nicht überein.",
            "chat.access.local": "Lokal",
            "chat.access.env": "Schlüssel aus {name}",
            "chat.access.profile": "ant-Profil {name}",
            "chat.access.profile_active": "aktives ant-Profil",
            "chat.access.not_signed_in": "nicht angemeldet",
            "chat.access.not_set_up": "KI nicht eingerichtet",
            
            # Messages
            "msg.success": "Erfolg",
            "msg.failed": "Fehlgeschlagen",
            "msg.restart_required": "Neustart für einige Änderungen erforderlich.",
            "msg.import_success": "Import Erfolgreich",
            "msg.ai_ready": "AI Bereit",
            "msg.ai_thinking": "🤖 Ditto denkt nach...",
            "msg.ai_complete": "✅ Analyse abgeschlossen!",
            "msg.module_created": "Modul erstellt in:",

            # Deep Ingest: einmaliger Hinweis (Information, keine rechtliche Bestätigung), Fortschritt, Ergebnis
            "menu.audio": "Gesprochene Ansagen",
            "settings.not_saved.title": "Einstellung nicht gespeichert",
            "settings.not_saved": ("Die Änderung gilt bis zum Programmende, konnte aber nicht in config.yml "
                                   "gespeichert werden. Siehe Protokoll."),
            "crawl.notice.title": "Vor Ihrem ersten Deep Ingest",
            "crawl.notice.text": (
                "Deep Ingest liest Webseiten in Ihre Wissensdatenbank ein, damit Ditto sie nutzen kann.\n\n"
                "• Was gelesen wird: nur öffentlich erreichbare Seiten (ohne Anmeldung), ausgehend von den "
                "Adressen, die Sie eingeben, innerhalb der Seiten- und Tiefengrenzen.\n"
                "• Opt-outs werden beachtet: robots.txt, der X-Robots-Tag-Header und robots-Meta-Tags (noindex, "
                "none, noai, noimageai; bei nofollow werden Links nicht verfolgt) sowie das TDM Reservation "
                "Protocol (TDMRep). Seiten, deren Inhaber sich Text- und Data-Mining vorbehalten hat, werden "
                "übersprungen und im Crawl-Protokoll genannt.\n"
                "• Das Wissen bleibt auf diesem Computer: Seiten werden als Text in Ihrer lokalen "
                "Wissensdatenbank gespeichert (oder auf Ihrem eigenen Qdrant-Server, falls Sie einen eingetragen "
                "haben). Wenn Ditto antwortet, gehen passende Auszüge an den gewählten KI-Anbieter; mit einem "
                "lokalen Anbieter verlässt nichts diesen Computer.\n"
                "• Teilen nur mit Ihrer Freigabe: Ein Wissens-Snapshot in einem Target oder ein Community-Pack "
                "behält Quelle und Lizenz jeder Seite und wird erst geteilt, wenn Sie es ausdrücklich freigeben. Er "
                "enthält den vollen Text einer Seite nur, wo ihre Lizenz die Weitergabe erlaubt; jede andere Seite "
                "geht als ihre Adresse mit kurzen Fakten hinaus.\n\n"
                "Text- und Data-Mining öffentlich zugänglicher Inhalte ist erlaubt, solange der Rechteinhaber es "
                "sich nicht maschinenlesbar vorbehalten hat (EU-Richtlinie 2019/790, Art. 4; § 44b UrhG). "
                "Deshalb entscheiden diese Signale. Dieser Hinweis ist eine Information, keine rechtliche "
                "Bestätigung."),
            "crawl.notice.ok": "&Verstanden",
            "crawl.notice.spoken": "Bitte lesen Sie den Hinweis vor Ihrem ersten Deep Ingest.",
            "crawl.btn.stop": "⏹ Stoppen",
            "crawl.stopping": "Wird gestoppt (eine Seite, die noch ankommt, wird verworfen; gespeicherte Seiten bleiben)...",
            "crawl.progress.start": "Deep Ingest startet...",
            "crawl.progress.page": "Seite {n} von {m}: {url}",
            "crawl.done.success": "Deep Ingest abgeschlossen: {pages} Seiten ({chunks} Abschnitte) sind jetzt durchsuchbar.",
            "crawl.done.partial": ("Deep Ingest teilweise abgeschlossen: {pages} Seiten ({chunks} Abschnitte) sind "
                                   "durchsuchbar; andere Seiten wurden ausgelassen oder schlugen fehl. Siehe Protokoll."),
            "crawl.done.stopped": ("Deep Ingest gestoppt: {pages} Seiten ({chunks} Abschnitte) bleiben erhalten und "
                                   "sind durchsuchbar."),
            "crawl.done.stopped_empty": "Deep Ingest gestoppt: Es wurde nichts gespeichert.",
            "crawl.done.failure": "Deep Ingest fehlgeschlagen: Es wurde nichts gespeichert. Siehe Protokoll.",
            "crawl.finish_waits": "Deep Ingest läuft noch. Das Modul kann erstellt werden, sobald er beendet oder "
                                  "gestoppt ist, damit ein Wissens-Snapshot jede Seite enthält.",
            "crawl.snapshot_missing": ("Der Wissens-Snapshot wurde nicht angehängt: Keine gespeicherte Seite "
                                       "stammt von den gecrawlten URLs, oder die Wissensbasis ließ sich nicht "
                                       "lesen. Siehe Protokoll."),

            # Eingabeverlauf (zuletzt verwendete Werte in den Eingabefeldern, je Benutzer)
            "menu.clear_history": "Eingabeverlauf löschen...",
            "history.clear.title": "Eingabeverlauf",
            "history.clear.question": ("Die zuletzt verwendeten Modelle, Targets, Boards, URLs, Geräte und "
                                       "Datensätze vergessen, die die Eingabefelder anbieten?"),
            "history.clear.failed": "Der Eingabeverlauf konnte nicht gelöscht werden. Siehe Protokoll.",
            "history.cleared": "Eingabeverlauf gelöscht."
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


def current_language() -> str:
    """The UI language; English before the GUI set one up."""
    return _instance.current_lang if _instance else "en"


def ui_text(key: str, lang: Optional[str] = None, **values: Any) -> str:
    """The text of key in lang (default: the UI language; English when missing) with {placeholders} filled.
    Unlike tr() it also works before the GUI created the LocalizationManager, e.g. in worker threads."""
    table = LocalizationManager.TRANSLATIONS
    text = table.get(lang or current_language(), {}).get(key) or table["en"].get(key, key)
    return text.format(**values) if values else text


# The texts of newer parts live in modules of their own, so parallel work never edits this file at the same place:
# warnings and blockers (l10n_facts), the chat panel (l10n_chat), deleting and the trash (l10n_deletion), Ditto's
# runtime mode in the AI settings (l10n_runtime), golden artifacts and the snapshot disk check (l10n_artifacts), the
# board deployment (l10n_device), the GUI tools, the board recommendation and the build progress (l10n_gui), the
# knowledge base's storage and its old folder (l10n_knowledge). Each
# holds a plain `TEXTS = {"en": {...}, "de": {...}}` and imports nothing from the framework. A module that does not
# exist yet is skipped.
TEXT_MODULES = ("orchestrator.utils.l10n_facts", "orchestrator.utils.l10n_chat", "orchestrator.utils.l10n_deletion",
                "orchestrator.utils.l10n_runtime", "orchestrator.utils.l10n_artifacts",
                "orchestrator.utils.l10n_device", "orchestrator.utils.l10n_gui", "orchestrator.utils.l10n_knowledge")
TEXT_MODULES += ("orchestrator.utils.l10n_signin",)   # Security > Secrets..., Sign-in Overview..., Key Storage...


def merge_text_modules(table: Optional[Dict[str, Dict[str, str]]] = None,
                       modules: Sequence[str] = TEXT_MODULES) -> Dict[str, Dict[str, str]]:
    """Merges the TEXTS of `modules` into `table` (default: LocalizationManager.TRANSLATIONS) and returns it. A key
    that already exists, a language the table does not have or a value that is not text is refused (ValueError): a
    text is never replaced silently."""
    import importlib
    table = LocalizationManager.TRANSLATIONS if table is None else table
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name != name:  # the module exists, but something it imports does not
                raise
            continue
        texts = getattr(module, "TEXTS", None)
        if not isinstance(texts, dict):
            raise ValueError(f"{name} has no TEXTS table")
        for lang, entries in texts.items():  # checked completely before anything is merged
            if lang not in table or not isinstance(entries, dict):
                raise ValueError(f"{name}: unknown language {lang!r}")
            for key, text in entries.items():
                if not isinstance(key, str) or not isinstance(text, str):
                    raise ValueError(f"{name}: {key!r} in {lang} is not text")
                if key in table[lang]:
                    raise ValueError(f"{name}: the text {key!r} in {lang} exists already")
        for lang, entries in texts.items():
            table[lang].update(entries)
    return table


merge_text_modules()
