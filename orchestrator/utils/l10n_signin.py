#!/usr/bin/env python3
"""
Texts of the Security menu's windows (W1' of 2026-10-16: every action of `llmcf secrets` and `llmcf ditto login|
logout|auth` also by mouse), English and German: Security > Secrets..., Security > Sign-in Overview... (every
provider, Check all, the sign-in methods, the key from an environment variable, the server address, Forget) and
Security > Key Storage... (the vault's protection, switching it, Rotate key...). LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_signin_gui.py). Where the CLI asks or reports,
the English text uses the CLI's words. A text names a place with
{path:<objectName>} (wave 6: the menus are translated). German terms follow the sign-in panel: "Tresor für Zugangsdaten"
(credential vault), "Tresorschlüssel" (vault key), "Anmeldung" (sign-in). This module imports nothing from the
framework.
"""

TEXTS = {
    "en": {
        # the menu entries (Security)
        "signin.menu.secrets": "Secrets...",
        "signin.menu.overview": "Sign-in Overview...",
        "signin.menu.key_storage": "Key Storage...",
        "signin.menu.no_vault": "The credential vault is not available in this process.",

        # Security > Secrets...
        "secrets.title": "Secrets",
        "secrets.intro": "The secrets in the encrypted credential vault ({file}): each name with 'stored' or 'not "
                         "stored'. Values are never shown. Ditto's AI provider keys are signed in under "
                         "{path:act_signin_overview}, where they are checked first.",
        "secrets.col.name": "Name",
        "secrets.col.state": "State",
        "secrets.col.what": "What it is",
        "secrets.stored": "stored",
        "secrets.not_stored": "not stored",
        "secrets.what.hf_token": "Hugging Face token: gated models in builds and the Hugging Face tools of AI clients "
                                 "(read inside the framework only, never passed to an AI)",
        "secrets.what.qdrant_api_key": "API key of a remote Qdrant server for the knowledge base (setting "
                                       "qdrant_url)",
        "secrets.what.community_signing_key": "Your signing key for community packs (created on first use; set it "
                                              "by hand only to replace a damaged key)",
        "secrets.what.community_age_identity": "Your key for receiving encrypted community packs (created on first "
                                               "use; set it by hand only to replace a damaged key)",
        "secrets.what.provider": "Sign-in of the AI provider {provider} ({path:act_signin_overview})",
        "secrets.what.other": "Stored under this name",
        "secrets.set_title": "Set a secret",
        "secrets.name": "Name:",
        "secrets.value": "Value:",
        "secrets.repeat": "Repeat:",
        "secrets.name_placeholder": "a name from the list or any other name",
        "secrets.value_placeholder": "never shown",
        "secrets.store": "Store",
        "secrets.refresh": "Refresh",
        "secrets.replaces": "A value is stored under this name; Store replaces it.",
        "secrets.name_needed": "Enter a name.",
        "secrets.value_needed": "Enter the value.",
        "secrets.mismatch": "The two entered values do not match.",
        "secrets.stored_ok": "Secret '{name}' stored in the encrypted credential vault.",
        "secrets.memory_only": "Secret '{name}' is kept in memory for this run only (secret_storage: ephemeral); "
                               "nothing was saved.",
        "secrets.failed": "Failed to store secret '{name}'. A name is printable text of 1 to 256 characters; the log "
                          "says why.",
        "secrets.loading": "Reading the names...",
        "secrets.count": "{n} names, {stored} stored.",
        "secrets.list_failed": "The names cannot be read: {reason}",
        "secrets.working": "Storing...",

        # Security > Sign-in Overview...
        "signin.overview.title": "Sign-in Overview",
        "signin.overview.intro": "Every AI provider Ditto can use, with its sign-in. A key is never shown, at most "
                                 "its last four characters. Select a provider to sign in, store its key from an "
                                 "environment variable, set its server address or sign out. The framework never "
                                 "reads, stores or forwards Claude subscription credentials and never starts the "
                                 "claude command.",
        "signin.overview.col.provider": "Provider",
        "signin.overview.col.method": "Method",
        "signin.overview.col.source": "Source",
        "signin.overview.col.account": "Account / key",
        "signin.overview.col.status": "Status",
        "signin.overview.col.check": "Check",
        "signin.overview.primary": "* Ditto's provider (the AI settings choose it: {path:act_ditto_settings})",
        "signin.overview.vault": "Credential vault: {mode}, {file}",
        "signin.overview.vault_mode": "Credential vault: {mode}",
        "signin.overview.vault_problem": "The credential vault cannot be used: {reason}",
        "signin.overview.storage_state": "credential vault unavailable (see below)",
        "signin.overview.check_all": "Check all",
        "signin.overview.refresh": "Refresh",
        "signin.overview.reading": "Reading the sign-ins...",
        "signin.overview.checking": "Checking every provider with a credential (the free check, over the "
                                    "network)...",
        "signin.overview.checked": "Checked: {ok} valid, {failed} failed.",
        "signin.overview.failed": "The sign-ins cannot be read: {reason}",
        "signin.overview.tab_sign_in": "Sign in to {provider}",
        "signin.overview.tab_methods": "All sign-in methods",
        "signin.overview.pick": "Select a provider above.",
        "signin.source.vault": "credential vault",
        "signin.source.env": "environment variable {name}",
        "signin.source.profile": "ant profile {name}",
        "signin.source.runtime": "managed local server",
        "signin.methods.col.provider": "Provider",
        "signin.methods.col.method": "Method",
        "signin.methods.col.type": "Type",
        "signin.methods.col.tags": "Tags",
        "signin.methods.col.env": "Environment variables",
        "signin.methods.col.key_page": "Key page",
        "signin.methods.col.where": "Where to find it",
        "signin.methods.col.options": "Options",
        "signin.methods.col.note": "Note",

        # the sign-in panel's additions
        "signin.address": "Server address:",
        "signin.address_placeholder": "http:// or https:// address of the server",
        "signin.address_hint": "Remembered for this provider only; it does not make it Ditto's provider.",
        "signin.env_names": "Environment variables: {names}",
        "signin.from_env": "Store the key from {name} in the vault",
        "signin.from_env_check": "Check it first (free)",
        "signin.from_env_done": "The key from {name} is now stored in the vault; the variable is no longer needed.",
        "signin.from_env_memory": "The key is kept in memory for this run only (secret_storage: ephemeral); nothing "
                                  "was saved.",
        "signin.from_env_not_set": "Not set: {names}.",
        "signin.from_env_malformed": "{name} has a space, a line break or another character that is never part of "
                                     "a key.",
        "signin.from_env_no_variable": "This sign-in method reads no environment variable.",
        "signin.from_env_checked": "Checked: {message}",
        "signin.forget": "Forget",
        "signin.confirm_forget": "Forget the remembered server address and method of {provider}?",
        "signin.forgotten": "{provider}: the remembered server address and method were forgotten.",
        "signin.nothing_remembered": "{provider} needs no sign-in; nothing was stored.",

        # Security > Key Storage...
        "signin.keystore.title": "Key Storage",
        "signin.keystore.intro": "Every secret in the credential vault is encrypted with one vault key; this is how "
                                 "that key is protected. Switching re-encrypts every stored credential. Nothing is "
                                 "chosen automatically.",
        "signin.keystore.mode": "Protected with: {mode} ({text})",
        "signin.keystore.file": "Vault file: {file}",
        "signin.keystore.problem": "The vault cannot be used: {reason}",
        "signin.keystore.choose": "Protect it another way",
        "signin.keystore.keyring": "The OS keyring (default)",
        "signin.keystore.current": "{label} (current)",
        "signin.keystore.insecure_title": "Unencrypted key file",
        "signin.keystore.insecure_confirm": "The vault key will be kept in a file next to the vault, NOT encrypted "
                                            "at rest. Continue?",
        "signin.keystore.cancelled": "Cancelled: the protection was not changed.",
        "signin.keystore.rotate": "Rotate key...",
        "signin.keystore.rotate_title": "Rotate the vault key",
        "signin.keystore.rotate_confirm": "Create a new vault key and re-encrypt every stored credential with it? The "
                                          "old key is removed.",
        "signin.keystore.rotated": "Vault key rotated: every stored credential is re-encrypted with the new key, and "
                                   "the old key was removed.",
        "signin.keystore.rotated_memory": "The vault is kept in memory for this run only (secret_storage: "
                                          "ephemeral); the new key is not saved.",
        "signin.keystore.rotate_cancelled": "Cancelled: the vault key was not changed.",
        "signin.keystore.reading": "Reading the protection...",
        "signin.keystore.choice.keyring": "the OS keyring (default)",
        "signin.keystore.choice.helper": "a credential helper command (run without a shell)",
        "signin.keystore.choice.passphrase": "a passphrase you enter once per start",
        "signin.keystore.choice.insecure": "a key file next to the vault (NOT encrypted at rest)",
        "signin.keystore.choice.ephemeral": "memory only, nothing is stored (CI; keys from environment variables)",
    },
    "de": {
        # die Menüeinträge (Security)
        "signin.menu.secrets": "Geheimnisse...",
        "signin.menu.overview": "Anmeldeübersicht...",
        "signin.menu.key_storage": "Schlüsselspeicher...",
        "signin.menu.no_vault": "Der Tresor für Zugangsdaten ist in diesem Prozess nicht verfügbar.",

        # Security > Geheimnisse...
        "secrets.title": "Geheimnisse",
        "secrets.intro": "Die Geheimnisse im verschlüsselten Tresor für Zugangsdaten ({file}): jeder Name mit "
                         "'gespeichert' oder 'nicht gespeichert'. Werte werden nie angezeigt. Dittos Schlüssel für "
                         "KI-Anbieter meldest du unter {path:act_signin_overview} an, wo sie zuerst geprüft werden.",
        "secrets.col.name": "Name",
        "secrets.col.state": "Zustand",
        "secrets.col.what": "Wofür",
        "secrets.stored": "gespeichert",
        "secrets.not_stored": "nicht gespeichert",
        "secrets.what.hf_token": "Hugging-Face-Token: zugangsbeschränkte Modelle in Builds und die Hugging-Face-"
                                 "Werkzeuge der KI-Clients (nur im Framework gelesen, nie an eine KI gegeben)",
        "secrets.what.qdrant_api_key": "API-Schlüssel eines entfernten Qdrant-Servers für die Wissensbasis "
                                       "(Einstellung qdrant_url)",
        "secrets.what.community_signing_key": "Dein Signaturschlüssel für Community-Pakete (beim ersten Gebrauch "
                                              "erzeugt; setze ihn nur von Hand, um einen beschädigten Schlüssel zu "
                                              "ersetzen)",
        "secrets.what.community_age_identity": "Dein Schlüssel zum Empfangen verschlüsselter Community-Pakete (beim "
                                               "ersten Gebrauch erzeugt; setze ihn nur von Hand, um einen "
                                               "beschädigten Schlüssel zu ersetzen)",
        "secrets.what.provider": "Anmeldung beim KI-Anbieter {provider} ({path:act_signin_overview})",
        "secrets.what.other": "Unter diesem Namen gespeichert",
        "secrets.set_title": "Ein Geheimnis setzen",
        "secrets.name": "Name:",
        "secrets.value": "Wert:",
        "secrets.repeat": "Wiederholen:",
        "secrets.name_placeholder": "ein Name aus der Liste oder ein anderer Name",
        "secrets.value_placeholder": "wird nie angezeigt",
        "secrets.store": "Speichern",
        "secrets.refresh": "Aktualisieren",
        "secrets.replaces": "Unter diesem Namen ist ein Wert gespeichert; Speichern ersetzt ihn.",
        "secrets.name_needed": "Gib einen Namen ein.",
        "secrets.value_needed": "Gib den Wert ein.",
        "secrets.mismatch": "Die beiden eingegebenen Werte stimmen nicht überein.",
        "secrets.stored_ok": "Geheimnis '{name}' im verschlüsselten Tresor für Zugangsdaten gespeichert.",
        "secrets.memory_only": "Geheimnis '{name}' wird nur für diesen Lauf im Speicher gehalten (secret_storage: "
                               "ephemeral); gespeichert wurde nichts.",
        "secrets.failed": "Geheimnis '{name}' konnte nicht gespeichert werden. Ein Name ist druckbarer Text mit 1 bis "
                          "256 Zeichen; das Protokoll nennt den Grund.",
        "secrets.loading": "Die Namen werden gelesen...",
        "secrets.count": "{n} Namen, {stored} gespeichert.",
        "secrets.list_failed": "Die Namen können nicht gelesen werden: {reason}",
        "secrets.working": "Wird gespeichert...",

        # Security > Anmeldeübersicht...
        "signin.overview.title": "Anmeldeübersicht",
        "signin.overview.intro": "Jeder KI-Anbieter, den Ditto nutzen kann, mit seiner Anmeldung. Ein Schlüssel wird "
                                 "nie angezeigt, höchstens seine letzten vier Zeichen. Wähle einen Anbieter, um dich "
                                 "anzumelden, seinen Schlüssel aus einer Umgebungsvariable zu speichern, seine "
                                 "Serveradresse zu setzen oder dich abzumelden. Das Framework liest, speichert und "
                                 "übergibt nie Zugangsdaten eines Claude-Abos und startet nie den Befehl claude.",
        "signin.overview.col.provider": "Anbieter",
        "signin.overview.col.method": "Verfahren",
        "signin.overview.col.source": "Quelle",
        "signin.overview.col.account": "Konto / Schlüssel",
        "signin.overview.col.status": "Zustand",
        "signin.overview.col.check": "Prüfung",
        "signin.overview.primary": "* Dittos Anbieter (die KI-Einstellungen wählen ihn: {path:act_ditto_settings})",
        "signin.overview.vault": "Tresor für Zugangsdaten: {mode}, {file}",
        "signin.overview.vault_mode": "Tresor für Zugangsdaten: {mode}",
        "signin.overview.vault_problem": "Der Tresor für Zugangsdaten kann nicht genutzt werden: {reason}",
        "signin.overview.storage_state": "Tresor für Zugangsdaten nicht nutzbar (siehe unten)",
        "signin.overview.check_all": "Alle prüfen",
        "signin.overview.refresh": "Aktualisieren",
        "signin.overview.reading": "Die Anmeldungen werden gelesen...",
        "signin.overview.checking": "Jeder Anbieter mit Zugangsdaten wird geprüft (die kostenlose Prüfung, über das "
                                    "Netz)...",
        "signin.overview.checked": "Geprüft: {ok} gültig, {failed} fehlgeschlagen.",
        "signin.overview.failed": "Die Anmeldungen können nicht gelesen werden: {reason}",
        "signin.overview.tab_sign_in": "Bei {provider} anmelden",
        "signin.overview.tab_methods": "Alle Anmeldeverfahren",
        "signin.overview.pick": "Wähle oben einen Anbieter.",
        "signin.source.vault": "Tresor für Zugangsdaten",
        "signin.source.env": "Umgebungsvariable {name}",
        "signin.source.profile": "ant-Profil {name}",
        "signin.source.runtime": "verwalteter lokaler Server",
        "signin.methods.col.provider": "Anbieter",
        "signin.methods.col.method": "Verfahren",
        "signin.methods.col.type": "Art",
        "signin.methods.col.tags": "Hinweise",
        "signin.methods.col.env": "Umgebungsvariablen",
        "signin.methods.col.key_page": "Schlüsselseite",
        "signin.methods.col.where": "Wo du ihn findest",
        "signin.methods.col.options": "Optionen",
        "signin.methods.col.note": "Anmerkung",

        # die Ergänzungen des Anmeldefelds
        "signin.address": "Serveradresse:",
        "signin.address_placeholder": "http://- oder https://-Adresse des Servers",
        "signin.address_hint": "Nur für diesen Anbieter gemerkt; er wird dadurch nicht zu Dittos Anbieter.",
        "signin.env_names": "Umgebungsvariablen: {names}",
        "signin.from_env": "Den Schlüssel aus {name} im Tresor speichern",
        "signin.from_env_check": "Vorher prüfen (kostenlos)",
        "signin.from_env_done": "Der Schlüssel aus {name} ist jetzt im Tresor gespeichert; die Variable wird nicht "
                                "mehr gebraucht.",
        "signin.from_env_memory": "Der Schlüssel wird nur für diesen Lauf im Speicher gehalten (secret_storage: "
                                  "ephemeral); gespeichert wurde nichts.",
        "signin.from_env_not_set": "Nicht gesetzt: {names}.",
        "signin.from_env_malformed": "{name} enthält ein Leerzeichen, einen Zeilenumbruch oder ein anderes Zeichen, "
                                     "das nie zu einem Schlüssel gehört.",
        "signin.from_env_no_variable": "Dieses Anmeldeverfahren liest keine Umgebungsvariable.",
        "signin.from_env_checked": "Geprüft: {message}",
        "signin.forget": "Vergessen",
        "signin.confirm_forget": "Die gemerkte Serveradresse und das gemerkte Verfahren von {provider} vergessen?",
        "signin.forgotten": "{provider}: Die gemerkte Serveradresse und das gemerkte Verfahren wurden vergessen.",
        "signin.nothing_remembered": "{provider} braucht keine Anmeldung; gespeichert war nichts.",

        # Security > Schlüsselspeicher...
        "signin.keystore.title": "Schlüsselspeicher",
        "signin.keystore.intro": "Jedes Geheimnis im Tresor für Zugangsdaten ist mit einem Tresorschlüssel "
                                 "verschlüsselt; so ist dieser Schlüssel geschützt. Ein Wechsel verschlüsselt jede "
                                 "gespeicherte Anmeldung neu. Nichts wird automatisch gewählt.",
        "signin.keystore.mode": "Geschützt mit: {mode} ({text})",
        "signin.keystore.file": "Tresordatei: {file}",
        "signin.keystore.problem": "Der Tresor kann nicht genutzt werden: {reason}",
        "signin.keystore.choose": "Anders schützen",
        "signin.keystore.keyring": "Schlüsselbund des Betriebssystems (Standard)",
        "signin.keystore.current": "{label} (aktuell)",
        "signin.keystore.insecure_title": "Unverschlüsselte Schlüsseldatei",
        "signin.keystore.insecure_confirm": "Der Tresorschlüssel wird in einer Datei neben dem Tresor gespeichert, "
                                            "NICHT verschlüsselt. Fortfahren?",
        "signin.keystore.cancelled": "Abgebrochen: Der Schutz wurde nicht geändert.",
        "signin.keystore.rotate": "Schlüssel erneuern...",
        "signin.keystore.rotate_title": "Tresorschlüssel erneuern",
        "signin.keystore.rotate_confirm": "Einen neuen Tresorschlüssel erzeugen und jede gespeicherte Anmeldung damit "
                                          "neu verschlüsseln? Der alte Schlüssel wird entfernt.",
        "signin.keystore.rotated": "Tresorschlüssel erneuert: Jede gespeicherte Anmeldung ist mit dem neuen "
                                   "Schlüssel neu verschlüsselt, der alte Schlüssel wurde entfernt.",
        "signin.keystore.rotated_memory": "Der Tresor wird nur für diesen Lauf im Speicher gehalten (secret_storage: "
                                          "ephemeral); der neue Schlüssel wird nicht gespeichert.",
        "signin.keystore.rotate_cancelled": "Abgebrochen: Der Tresorschlüssel wurde nicht geändert.",
        "signin.keystore.reading": "Der Schutz wird gelesen...",
        "signin.keystore.choice.keyring": "der Schlüsselbund des Betriebssystems (Standard)",
        "signin.keystore.choice.helper": "der Befehl eines Credential-Helpers (ohne Shell ausgeführt)",
        "signin.keystore.choice.passphrase": "eine Passphrase, die du einmal pro Start eingibst",
        "signin.keystore.choice.insecure": "eine Schlüsseldatei neben dem Tresor (NICHT verschlüsselt gespeichert)",
        "signin.keystore.choice.ephemeral": "nur im Speicher, nichts wird gespeichert (CI; Schlüssel aus "
                                            "Umgebungsvariablen)",
    },
}
