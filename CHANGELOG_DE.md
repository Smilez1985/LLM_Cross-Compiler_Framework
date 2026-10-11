# Changelog

Alle nennenswerten Änderungen an diesem Projekt stehen in dieser Datei.
Englische Fassung: [CHANGELOG.md](CHANGELOG.md).

Das Format basiert auf [Keep a Changelog 1.1.0](https://keepachangelog.com/de/1.1.0/),
und dieses Projekt hält sich an [Semantic Versioning 2.0.0](https://semver.org/lang/de/spec/v2.0.0.html).
Geplante Arbeiten stehen in der [Roadmap](docs/BLUEPRINT_DE.md#roadmap), nicht hier.

## [Unreleased]

### Hinzugefügt
- **Ditto nutzt die Tools des Frameworks**: Im Chat (GUI) und mit `llm-cli ditto ask "<Frage>"` kann Ditto
  Targets auflisten, Capabilities prüfen, Builds starten und abbrechen, Logs lesen, die Wissensbasis durchsuchen
  und den Doctor ausführen, immer im Rahmen deiner Tool-Berechtigungen. Tools, die eine Bestätigung brauchen,
  zeigen ihren Titel und jedes Argument (ein Dialog in der GUI, eine Abfrage in der CLI; ohne Terminal wird
  abgelehnt).
- **KI-Einstellungen**: Anbieter aus `configs/llm_providers.yml`, eine Modellliste, die live beim Anbieter
  abgerufen wird (freie Eingabe geht weiterhin), Aufwand, Datenschutzstufe für Cloud-Anbieter, Offline-Modus und
  eine Ersatzkette.
- **Anbieterschicht für KI** (`orchestrator/llm`): ein Client für alle Anbieter, gebaut auf den offiziellen SDKs
  `anthropic` und `openai` (OpenAI-kompatible Server über `openai`). Anbieter sind Daten
  (`configs/llm_providers.yml`): Modell und Aufwand je Aufgabe, Wiederholungen, eine Ersatzkette, strukturierte
  Antworten, die gegen Schemas geprüft werden, und ein Datenschutzfilter für Cloud-Anbieter (Einstellung
  `ai_security_level`): Secrets werden immer geschwärzt; Adressen, E-Mail-Adressen, Logins, Benutzernamen,
  Hostnamen und Seriennummern werden durch stabile Platzhalter ersetzt, die in der Antwort lokal zurückübersetzt
  werden.
- **Lokale Wissensbasis** auf lokalen Embeddings (FastEmbed, ONNX auf der CPU) und einem eingebetteten
  Qdrant-Speicher: kein Vektordatenbank-Server und keine Cloud-Embeddings nötig.
- **Verwaltetes lokales Modell** für Dittos Offline-Modus: `llm-cli ditto runtime install|start|stop|status`
  betreibt einen gepinnten llama.cpp-`llama-server` auf 127.0.0.1 (Build passend zur Hardware: CUDA, Vulkan,
  Metal oder CPU, ROCm auf Wunsch; API-Schlüssel je Start in einer privaten Datei), und `llm-cli ditto models list|download`
  lädt Qwen3.5-Modelle in gepinnten Revisionen und prüft sie anhand von Größe und sha256. Die Modellstufen reichen
  bis zu 2-Bit-Modellen für schwache Hardware (3 GB RAM). Der Anbieter „local“ folgt dem laufenden Server ohne
  Konfiguration.
- **Ditto-Evaluation** (`llm-cli ditto eval`): feste Fälle (Analyse von Build-Logs, Build-Empfehlungen,
  Modulvorschläge, Chat mit Tools, Prompt-Injection), nach Regeln bewertet, gegen den konfigurierten Anbieter oder
  je lokaler Modellstufe (`--tier small --tier small-q2`). Es wird nichts gebaut; `--json` schreibt den Bericht.
- **Claude-Workspace-ID** (Feld „Workspace ID“ in den KI-Einstellungen, Einstellung `ai_anthropic_workspace_id`):
  Claude-API-Schlüssel, die in jedem Workspace der Organisation gelten, funktionieren jetzt. Die ID wird als
  Header `anthropic-workspace-id` bei jeder Anfrage mitgeschickt, die mit dem API-Schlüssel an die Claude-API
  geht, auch beim Abruf der Modellliste. Ein `ant auth login`-Profil behält seinen eigenen Workspace, und andere
  Server bekommen die ID nie.
- **Anmeldeverfahren je Anbieter** (`configs/llm_providers.yml` Schema 2, `orchestrator/llm/auth`): Jeder
  Anbieter nennt seine Anmeldeverfahren in der Reihenfolge, in der sie bevorzugt werden, mit der Seite, auf der
  der Schlüssel erstellt wird, den Umgebungsvariablen, die er liest, einer kostenlosen Prüfung und zusätzlichen
  Feldern wie der Claude-Workspace-ID oder der OpenAI-Organisation und dem Projekt. Kataloge mit Schema 1 in
  deinem Einstellungsordner werden weiterhin geladen; einer mit einem neueren Schema wird mit einer Meldung
  abgelehnt, statt falsch gelesen zu werden. Angaben zu einer Anmeldung, die nicht geheim sind (Konto
  oder Workspace, die letzten vier Zeichen eines Schlüssels, zusätzliche Felder, Zeitpunkt der Prüfung), liegen
  in `auth.json` neben dem Tresor. Tokens der Anmeldeabläufe, die später kommen, werden unter einer Sperre
  erneuert, die GUI, CLI und MCP-Server teilen; das neue Refresh-Token wird gespeichert, bevor es genutzt wird,
  und eine abgelehnte Erneuerung markiert die Anmeldung als abgelaufen.
- **Prüfung der Zugangsdaten** (die Schaltfläche **Prüfen** im Anmeldebereich und
  `llm-cli ditto auth status --check`): Eine kostenlose GET-Anfrage (die Modellliste, bei OpenRouter der
  Schlüssel-Endpunkt) zeigt, ob der gespeicherte Schlüssel funktioniert, zu welchem Workspace oder Schlüsselnamen
  er gehört und wie viele Modelle er nutzen kann; ein Schlüssel, der funktioniert, aber keine Modelle auflisten darf, wird als solcher gemeldet, auch von
  der Live-Modellliste in den KI-Einstellungen. Weiterleitungen werden nicht verfolgt, der Schlüssel geht also
  nur an den Anbieter.
- **API-Schlüssel aus Umgebungsvariablen**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` und
  `OPENROUTER_API_KEY` werden genutzt, wenn für den Anbieter kein Schlüssel gespeichert ist (nur die Variablen,
  die der Anbieterkatalog nennt; gelesen vom Framework, nie von den SDKs). Die KI-Einstellungen nennen die
  Variable, nichts wird in den Tresor kopiert, und ein gespeicherter Schlüssel hat immer Vorrang. Lässt sich der
  Tresor nicht lesen, springt keine Variable für den Schlüssel ein, der dort gespeichert sein kann: Die
  Fehlermeldung sagt, was mit dem Tresor nicht stimmt.
- **Wie Zugangsdaten geschützt werden, entscheidest du** (Einstellung `secret_storage`, oder
  `LLM_SECRET_STORAGE` für einen einzelnen Lauf): der Schlüsselbund des Betriebssystems (Standard), ein Befehl als
  Credential-Helper (`secret_helper`, z. B. `pass show llmcc/vault-key`, ohne Shell ausgeführt), eine Passphrase,
  die einmal je Start abgefragt wird (scrypt), eine Schlüsseldatei neben dem Tresor (`insecure`, im Ruhezustand
  nicht verschlüsselt) oder nur der Arbeitsspeicher (`ephemeral`, für CI). Nichts wird automatisch gewählt: Ohne
  nutzbaren Schlüsselbund und ohne Wahl hält das Framework an und nennt die Möglichkeiten. `llm-cli doctor` warnt
  vor der Schlüsseldatei und meldet die reine Speicherung im Arbeitsspeicher.
- **`--debug-http`** (`llm-cli --debug-http` und dieselbe Option beim Start der GUI) zeigt die Debug-Ausgabe
  der HTTP-Bibliotheken und der SDKs der KI-Anbieter auf stderr, mit geschwärzten Zugangsdaten.
- **Dokumentation auf Englisch und Deutsch** für jedes Dokument (README, Blaupause mit Design und Roadmap,
  CLI-Anleitung, Funktionen, Mitwirken, Leitfaden für Modulentwickler, SSOT, Changelog), geprüft durch
  `tests/test_docs.py`: Jedes Dokument hat sein Gegenstück, Links und Anker funktionieren, beide Changelogs nennen
  dieselben Versionen, und die Roadmap nennt nur, was kommt.
- **Anmeldung auf der Kommandozeile**: `llm-cli ditto login <anbieter>` meldet Ditto mit den Verfahren des
  Anbieters an (ein API-Schlüssel, eine lokale Engine ohne Schlüssel, ein Profil aus `ant auth login` mit
  `--method ant_profile`), prüft einen Schlüssel kostenlos, bevor es ihn speichert, und merkt sich das Verfahren
  (`ai_auth_<anbieter>`); `--workspace-id`, `--organization`, `--project` und `--base-url` legen fest, was zum
  Schlüssel gehört. `llm-cli ditto logout` löscht die lokale Kopie und nennt die Schlüsselseite, auf der der
  Schlüssel selbst widerrufen wird. `llm-cli ditto auth status [--json] [--check]` zeigt die Anmeldung aller
  Anbieter (Verfahren, Quelle, Konto, die letzten vier Zeichen eines Schlüssels), `auth methods` die Verfahren
  mit Hinweisen zu den Nutzungsbedingungen, `auth storage [--use ...]`, wie der Tresor geschützt ist (eine Wahl,
  die scheitert, ändert nichts, auch nicht `config.yml`), und `auth rotate-key` ersetzt den Schlüssel des
  Tresors. Nach einer geprüften Anmeldung nennt der Status das Konto oder den Workspace. Exit-Codes:
  2 Bedienfehler (auch ein leeres stdin, eine fehlende Serveradresse, ein fehlerhafter Optionswert oder ein
  Schlüssel mit Leerzeichen oder Zeilenumbruch darin), 3 abgelehnt, 4 Netz, 5 Tresor nicht nutzbar, 6 nicht
  erlaubt oder nicht verfügbar.
- **Anmeldebereich in den KI-Einstellungen** (**Anmelden...**): die Verfahren des Anbieters als Karten in der
  Reihenfolge des Katalogs, das erste nutzbare als „Empfohlen“ markiert; ein Schlüsselformular mit
  Schlüsselseite, den zusätzlichen Feldern, **Prüfen**, **Speichern** und **Abmelden**; eine Statuszeile
  („Angemeldet als ...“, „Nicht angemeldet“, „Schlüssel aus der Umgebungsvariable X“); und ohne nutzbaren
  Schlüsselbund die Auswahl für den Schutz, die die GUI auch beim Start zeigt, bevor sie aufgibt (eine Wahl dort,
  die den vorhandenen Tresor nicht öffnen kann, wird nicht behalten, damit der nächste Start noch funktioniert).
  Ein mit Passphrase geschützter Tresor fragt sie in einem Dialog ab. Jeder Zugriff auf Tresor, Schlüsselbund und Netz
  läuft in einem Hintergrund-Thread, und ein älteres Lesen, das spät fertig wird, ersetzt nie einen neueren
  Stand. Der Kopf von Dittos Chat zeigt den genutzten Zugang, zum Beispiel „Lokal · Ollama“. Ein Schlüssel im
  eigenen Schlüsselfeld der KI-Einstellungen ersetzt ein Verfahren, das sich eine Anmeldung gemerkt hat (etwa
  das `ant`-Profil), damit er nicht ungenutzt bleibt.
- `llm-cli secrets set --with-token` liest den Wert von stdin.

### Geändert
- **Ditto läuft auf der neuen Anbieterschicht** (offizielle SDKs anthropic und openai) statt auf LiteLLM, und das
  eingebaute Transformers-Modell für den Offline-Modus ist entfernt (der Offline-Modus nutzt lokale Anbieter:
  Ollama, den verwalteten llama-server, einen Server in deinem Netzwerk). Healing-Analysen, Modulvorschläge und
  Build-Empfehlungen sind geprüfte strukturierte Antworten; eine ungültige Antwort ist ein Fehler und wird nie
  angewendet.
- Der **Chatverlauf** wird nur noch ergänzt, nie überschrieben, und je Benutzer geführt; ein langes Gespräch wird
  in einem neuen zusammengefasst. Verläufe früherer Versionen werden übernommen.
- Fehler der Anbieter werden in einfacher Sprache erklärt, z. B. ein Claude-API-Schlüssel, der eine Workspace-ID
  braucht, ein Workspace, den der Schlüssel nicht nutzen darf, ein falscher, widerrufener oder abgelaufener
  Schlüssel oder ein `ant auth login`-Profil, das eine neue Anmeldung braucht.
- Mehr Fehler der Anbieter sagen, was passiert ist und was zu tun ist, mit einem Code für GUI und CLI: kein
  Guthaben oder ein Ausgabenlimit in der Claude Console, ein abgelehnter OpenAI-Schlüssel oder ein aufgebrauchtes
  OpenAI-Kontingent, ein Gemini-Schlüssel, den Google ablehnt, ein OpenRouter-Schlüssel an seinem Kreditlimit, ein
  lokaler Server, der nicht läuft oder einen Schlüssel verlangt, Ollama ohne seine Anmeldung bei ollama.com, ein
  gesperrter Schlüsselbund und ein unlesbarer Tresor. Ein Claude-Ausgabenlimit wird nicht mehr wie ein
  Ratenlimit wiederholt.
- **Anmeldung bei der Claude-API**: Ein gespeicherter API-Schlüssel wird immer zuerst genutzt. Ein
  `ant auth login`-Profil wird nur verwendet, wenn du es auswählst (`ai_auth_anthropic: ant_profile`); bisher
  ersetzte ein Profil auf der Festplatte stillschweigend den gespeicherten Schlüssel und damit das Konto, das
  abgerechnet wird. Der frühere Name `oauth` bedeutet weiterhin das Profil, in der Einstellung und in einem
  Anbieterkatalog in deinem Konfigurationsordner.
- Dittos Prompts: Build-Fehler werden richtig der Build-Maschine oder dem Gerät zugeordnet, Build-Empfehlungen
  wissen, welche Eingaben jedes Format annimmt (ein ONNX-Modell wird nicht an GGUF geschickt), Fragen zu Targets
  und Builds werden aus den Tools beantwortet, und Modulvorschläge verwenden Compiler-Flags statt der Liste der
  CPU-Features.
- Lokales Modell: Aufwand „low“ schaltet das Nachdenken (Thinking) ab (Qwen3.5 auf llama-server denkt sonst in
  voller Länge, was auf einer CPU Minuten dauert); ab „medium“ bleibt es an.
- Abhängigkeiten: `anthropic` 1.13.0, `openai` 3.28.0 (vorher 2.54.0) und `fastembed` 0.8.1 (lokale Embeddings,
  ONNX auf der CPU) sind exakt gepinnt; pip-audit meldet keine bekannten Schwachstellen.
- **Tresor für Zugangsdaten im Format v2**: Jeder Eintrag trägt eine Schlüssel-ID, und der Schlüssel des Tresors
  lässt sich wechseln (jeder Eintrag wird neu verschlüsselt, der alte Schlüssel entfernt; ein unterbrochener
  Wechsel wird beim nächsten Start abgeschlossen). Jeder Tresor hält seinen Schlüssel in einem eigenen Eintrag im
  Schlüsselbund, sodass ein Schlüsselwechsel oder ein anderer Schutz nie einen anderen Tresor berührt (etwa den
  eines anderen `LLM_FRAMEWORK_HOME`-Ordners); ein Speicher einer früheren Version zieht beim ersten
  Schlüsselwechsel in seinen eigenen Eintrag um. Anbieterschlüssel werden als Datensätze unter
  `cred/<anbieter>/api_key` gespeichert; Speicher und Schlüsselnamen früherer Versionen werden wie bisher gelesen
  und bei der nächsten Änderung umgestellt.
- Das Lesen eines Geheimnisses wird einmal je Prozess und Name ins Audit-Log der Secrets geschrieben, nicht bei
  jedem Zugriff.
- Befehle, die den Tresor brauchen, enden mit Exit-Code 5, wenn er sich nicht nutzen lässt (kein nutzbarer
  Schlüsselbund, keine Passphrase, ein beschädigter Tresor), statt mit 1, und nennen die Möglichkeiten, auch
  `llm-cli ditto auth storage --use ...`.
- Fehlermeldungen und Hinweise nennen `llm-cli ditto login <anbieter>` statt
  `llm-cli secrets set <Schlüsselname>`; `llm-cli module generate-ai` nutzt die gespeicherte Anmeldung und fragt
  nie selbst nach einem Schlüssel.

### Veraltet
- `llm-cli secrets set --password`: Ein Wert auf der Kommandozeile landet im Shell-Verlauf und in der
  Prozessliste. Nutze die verdeckte Abfrage oder `--with-token`.

### Entfernt
- Dittos generierte Füll-Kalibrierungsdaten (Builds nutzen den mitgelieferten Kalibrierungstext oder deinen
  eigenen Datensatz).
- `litellm` und `tiktoken` und mit ihnen 19 indirekte Pakete (aiohttp, boto3, jinja2 und andere).
- **Der Claude-Abo-Anbieter `claude_code`** (Ditto über `claude -p` mit den Limits eines Claude-Pro/Max-Abos):
  Anthropic erlaubt anderen Apps nicht, die Limits eines Claude-Abos ohne Genehmigung zu nutzen. Ein
  gespeichertes `ai_provider: claude_code` (oder `ClaudeCode`, `Claude Code`), derselbe Name in der Ersatzkette
  oder bei `--provider` bricht jetzt mit einer Meldung ab, die in den KI-Einstellungen einmal angezeigt wird und
  die Alternativen nennt: einen Claude-API-Schlüssel (Max- und Team-Abos enthalten monatliches API-Guthaben für
  eine verknüpfte Console-Organisation), Claude über Bedrock, Vertex oder Foundry, oder das Framework aus Claude
  Code selbst heraus nutzen (`llm-cli mcp config`). Es wechselt nie von sich aus zur Claude-API. Ein
  `claude_code`-Eintrag in einem Anbieterkatalog in deinem Konfigurationsordner wird mit einer Warnung
  übersprungen; die anderen Anbieter funktionieren weiter.

### Behoben
- Secrets-Speicher: Ein Speicher einer früheren Version, der sich nicht entschlüsseln lässt, wird von
  `llm-cli doctor` gemeldet, statt unlesbar in den Speicher im Benutzerordner übernommen zu werden.
- Lesen oder Speichern eines Geheimnisses scheitert nicht mehr in einem Container, dessen Benutzer keinen Namen hat
  (keine Variable `USER` und kein Eintrag in passwd).
- Unter Windows scheitert das Speichern eines Geheimnisses nicht mehr, wenn ein anderer Prozess oder ein
  Virenscanner den Speicher kurz geöffnet hat.
- `llm-cli secrets set` sagt, wohin das Geheimnis ging (in den verschlüsselten Tresor oder mit `ephemeral` nur in
  den Arbeitsspeicher), statt immer „Keyring“.
- Die CLI und die CLI-Anleitungen raten nicht mehr dazu, das GitHub-Token auf der Kommandozeile zu übergeben, wo
  der Shell-Verlauf es behält (diese Form funktionierte auch nicht). `llm-cli secrets set <name>` fragt den Wert
  verdeckt ab.

### Sicherheit
- Ditto verwirft einen Healing-Fix oder eine Build-Empfehlung, die einen gefährlichen Befehl enthält (einen
  Download, der direkt in eine Shell geleitet wird, das Löschen des Wurzel- oder Home-Ordners, Schreibrechte für
  alle, das Überschreiben eines Datenträgers), ganz gleich, welche Anweisungen ein Build-Log oder ein
  Modellname dem Modell gegeben hat.
- Anfragen an lokale Modellserver auf 127.0.0.1 laufen nie über einen HTTP(S)-Proxy aus der Umgebung.
- Downloads lokaler Modelle werden abgelehnt, wenn Größe und sha256 nicht gepinnt sind; das Archiv mit dem
  Server-Programm wird genauso geprüft und atomar installiert.
- `OPENAI_CUSTOM_HEADERS` und `ANTHROPIC_CUSTOM_HEADERS` werden beim Start der CLI, der GUI und des MCP-Servers
  mit einer Warnung verworfen, außer die Einstellung `ai_allow_sdk_env_headers` ist wahr (ein `"false"` in
  Anführungszeichen gilt als falsch; Einstellungen, die sich nicht lesen lassen, gelten als falsch): Sie könnten
  den gespeicherten API-Schlüssel ersetzen und jeden Server erreichen, auch lokale. Die Adapter für OpenAI und
  OpenAI-kompatible Server schicken den gespeicherten Schlüssel außerdem als ausdrücklichen
  `Authorization`-Header.
- Die Debug-Ausgabe der SDKs der Anbieter und der HTTP-Bibliotheken (Optionen der Anfragen, Prompts, Header)
  bleibt aus, außer `--debug-http` ist angegeben: Ihre Logger gehen nie unter INFO, auch nicht mit
  `--log-level DEBUG`, und `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` werden auf `info` gesenkt.
- Admin-API-Schlüssel von Claude (`sk-ant-admin01-`) werden abgelehnt, bevor eine Anfrage gesendet wird, auch
  wenn sie mit Leerzeichen davor oder danach gespeichert wurden.
- Der Tresor für Zugangsdaten wird unter einer Sperre über Prozesse hinweg geändert: GUI, CLI und ein MCP-Server,
  die gleichzeitig schreiben, verlieren nicht mehr die Änderungen der anderen.
- Ein Schlüsselbund, der nichts behält (etwa `keyring.backends.null`) oder nicht innerhalb von 3 Sekunden
  antwortet, wird nicht mehr verwendet; bisher waren damit gespeicherte Geheimnisse nach dem nächsten Start
  unlesbar. Ein Schlüsselbund-Backend, das ohne Verschlüsselung speichert (`keyrings.alt`), wird ebenfalls nicht
  verwendet: Das wäre `secret_storage: insecure`, und das kannst nur du wählen.
- Unter Windows bleibt der Schlüssel des Tresors auf dem lokalen Rechner, statt mit dem Benutzerprofil zu wandern.
- Der Secrets-Speicher lehnt einen Ort innerhalb einer Git-Arbeitskopie ab (setze `LLM_FRAMEWORK_HOME` woanders
  hin).
- Namen von Geheimnissen mit Zeilenumbrüchen oder Steuerzeichen werden abgelehnt, und das Audit-Log der Secrets
  maskiert sie, sodass ein Name (oder ein Benutzername) keine Audit-Einträge fälschen kann.
- **Zugangsdaten werden in jedem Log geschwärzt**: in den eigenen Logs des Frameworks, in denen der SDKs der
  Anbieter und der HTTP-Bibliotheken (anthropic, openai, httpx, httpcore, urllib3, botocore, google.auth, azure,
  msal), in Tracebacks und in den Zusatzfeldern des JSON-Logformats. Geschwärzt werden die Werte von Headern mit
  Zugangsdaten (`Authorization`, `x-api-key`, `api-key`, `x-goog-api-key`, `Cookie`, `Set-Cookie`, auch in JSON
  mit maskierten Anführungszeichen), Anmelde- und Schlüsselparameter in URLs und in formularkodierten
  Token-Anfragen (`code`, `state`, `key`, `access_token`, `token`, …) und jedes bekannte Schlüsselformat.
- Die Schwärzung findet Zugangsdaten auch an ihrem Kontext, gleich in welchem Format: ein Bearer-Token, das eine
  Fehlermeldung eines Servers zitiert, Werte in Token-Form in Feldern wie `api_key`, `secret`, `token` oder
  `password` und Umgebungszeilen wie `HF_TOKEN=...` oder `export OPENAI_API_KEY=...`. Tokenizer-Einstellungen in
  Build-Logs (zum Beispiel `"pad_token": "<|...|>"`) bleiben lesbar.
- Die Schwärzung lässt sich nicht mehr durch präparierten Text ausbremsen: Eine Fehlermeldung oder ein Build-Log
  mit einer langen Folge von `sk-` brauchte vorher Minuten.
- **Eine Tabelle der Secret-Formate** (`orchestrator/security/secret_patterns.py`) für den Secret-Scan und die
  Schwärzung zur Laufzeit, die vorher nur fünf Formate kannte. Neu: Schlüssel von Unsloth Studio (`sk-unsloth-`),
  kurzlebige Bedrock-Schlüssel (`bedrock-api-key-`), LocalAI-Schlüssel (`lai-`), Zeilen mit
  `Authorization: Bearer` und Werte in Anführungszeichen, die `api_key`, `secret` oder `token` zugewiesen werden
  (ein Name in Kleinbuchstaben wie `"community_encryption_key"` wird nicht gemeldet). Die Schwärzung zur Laufzeit
  erfasst jetzt auch JWTs, Blöcke privater Schlüssel und Passwörter in URLs.
- API-Schlüssel, die Dittos Anbieterschicht im Speicher hält, erscheinen in `str()` und `repr()` als
  `<redacted>` und lassen sich nicht mit pickle serialisieren.
- Ein Schlüssel eines eigenen OpenAI-kompatiblen Servers gehört zu der Adresse, für die er gespeichert wurde
  (Schema, Host und Port): Nach einer Änderung der Basis-URL wird er nicht mehr mitgeschickt. Ein Schlüssel, den
  eine frühere Version gespeichert hat, wird an die Adresse gebunden, mit der er als Nächstes genutzt wird.
  Claude-Admin-API-Schlüssel werden auch für eine Claude-API-Vorlage in deinem eigenen Anbieterkatalog
  abgelehnt, die sie nicht nennt.
- Härtung der Anmeldung: Der Hinweis zum Schlüssel in den KI-Einstellungen bindet einen Schlüssel einer früheren
  Version nie an eine Adresse, die gerade noch eingetippt wird; Abmelden entfernt auch einen solchen Schlüssel;
  die Prüfung der Zugangsdaten schickt einen an eine Adresse gebundenen Schlüssel nur an diese Adresse; eine
  Schlüsselvariable einer lokalen Engine in deinem eigenen Katalog geht nur an die Adresse, die der Katalog
  nennt. Ein nicht geheimes Feld (Workspace-ID, Projekt) lässt sich nicht mehr mit einem Header für
  Zugangsdaten wie `Authorization` oder `x-api-key` angeben, wo es den gespeicherten Schlüssel ersetzen würde.
  Eine gescheiterte Token-Erneuerung nennt nur die Art des Fehlers, und eine Meldung des Anbieters, die das
  Refresh-Token zitiert, wird ohne es angezeigt. Ein Schlüssel einer früheren Version wird an die Adresse
  gebunden, bevor eine Prüfung mit eingetippten Feldern ihn dorthin schickt.
- Die Anmeldung nimmt nie ein Geheimnis von der Kommandozeile (`ditto login` hat keine Option für einen
  Schlüssel, und ein unbekannter Anbietername oder `--method`-Wert, der wie ein Schlüssel aussieht, wird nicht
  ausgegeben), zeigt einen Schlüssel nur mit seinen letzten vier Zeichen (auch in `auth status --json`, und als
  `REDACTED`, wenn ein Fehlertext oder Kontoname des Servers ihn wiederholt, auch in Fehlern von Dittos Chat),
  fragt nach, bevor ein Schlüssel über reines `http://` an einen anderen Rechner geht (auch im Anmeldebereich und
  im Schlüsselfeld der KI-Einstellungen; `--yes` in Skripten), und wählt nie selbst, wie der Tresor geschützt
  wird: `--insecure-storage` ersetzt nie einen Schutz, der funktioniert, und auch keinen Credential-Helper und
  keine Passphrase, die du gewählt hast. Ein Schlüssel mit einem Leerzeichen, einem Zeilenumbruch oder einem
  unsichtbaren Zeichen darin wird abgelehnt, bevor etwas gesendet oder gespeichert wird.

## [3.0.0] - 2026-10-10
**Release für Reparatur, Sicherheit und MCP.** Die Build-Pipeline funktioniert wieder von Anfang bis Ende (mit
echten Konvertierungen geprüft), jede bekannte Schwachstelle in den Abhängigkeiten ist beseitigt, KI-Clients
können das Framework über das Model Context Protocol steuern, und Konvertierungs-Container laufen in einer
Sandbox.

### Hinzugefügt
- **MCP-Server** (`llm-cli mcp serve`, offizielles SDK `mcp` 2.3.0, Spezifikation 2026-07-28): Claude Code,
  Claude Desktop oder VS Code können Targets auflisten, Capabilities prüfen, Builds starten und abbrechen, Logs und
  Artefakte lesen, die Wissensbasis durchsuchen, den Doctor ausführen und über Self-Healing-Vorschläge
  entscheiden. Standardmäßig über stdio; optional Streamable HTTP auf Loopback mit einem Bearer-Token je
  Benutzer. `llm-cli mcp config` gibt die Client-Einrichtung aus, `--write claude-desktop` trägt sie in Claude
  Desktop ein.
- **Tool-Registry mit Berechtigungen und Audit-Log**, gemeinsam genutzt von Ditto und dem MCP-Server: typisierte
  Tools mit Risikostufen (Lesen, lokales Schreiben, extern, verboten), Berechtigungen, die einmal bei Dittos erster
  Einrichtung abgefragt werden (GUI-Dialog, `llm-cli ditto setup`, `llm-cli tools permissions`), Bestätigungen
  über MCP-Elicitation und ein HMAC-verkettetes Audit-Log (`llm-cli tools audit --verify`).
- **Target-Capabilities** (Block `capabilities:` in `target.yml`, pydantic-Schema): Boards, Formate,
  Quantisierungen je Board, Standardwerte und Kalibrierungsbedarf. GUI, CLI, Build-Dienst und MCP-Tools bieten
  genau das an, was ein Target angibt, und prüfen genau das.
- **Build-Dienst**: ein typisierter Einstiegspunkt für Builds (prüfen, starten, Status, abbrechen, Logs,
  Artefakte).
- **`llm-cli doctor`**: deterministische Gesundheitsprüfungen (Docker, Speicherplatz, Targets, Sicherheit,
  Tool-Berechtigungen, Kalibrierungsdaten) mit Hinweisen zur Behebung, gestuften automatischen Korrekturen und
  JSON-Ausgabe (angelehnt an „Mojo“ in rag4ll).
- **Kuratierter Kalibrierungstext** (`data/calibration`, MIT, mit Prüfsummen gesicherte Textsätze auf Deutsch,
  Englisch und zur Tool-Nutzung) für Importance Matrices.
- **Build-Manifeste**: Jeder Build ergänzt `build_info.json` um seine Modelldateien und deren SHA256-Hashes; das
  Deployment-Fenster listet Builds anhand dieser Manifeste.
- **Echter Build-Abbruch und Zeitlimits je Stufe**.
- **SSH-Vertrauen bei der ersten Verbindung** (Trust on First Use): Fingerabdrücke von Host-Schlüsseln bestätigt
  der Benutzer, gespeichert werden sie in der eigenen `known_hosts` des Frameworks; ein Ed25519-Schlüssel des
  Frameworks wird einmalig auf dem Gerät installiert („Pairing“).
- **Echte CI-Workflows** (Lint, Tests unter Ubuntu mit 3.11 bis 3.14 und unter Windows, bandit, pip-audit,
  Docker-Build und Smoke-Tests einschließlich der Sandbox) und Dependabot.
- Unterstützung für Python 3.14; Zustandsordner je Benutzer `~/.llm-framework` (`LLM_FRAMEWORK_HOME`).

### Geändert
- **Rockchip-Pipeline neu aufgebaut** und von Anfang bis Ende geprüft: llama.cpp v0.6.0 (GGUF einschließlich
  IQ-Typen und Importance Matrix), RKLLM-Toolkit 1.3.1 und RKNN-Toolkit2 2.3.2, jedes in einer eigenen venv;
  Modelle werden auf dem Host bereitgestellt (Hugging-Face-Repo oder lokaler Pfad), Verteilung nach Format
  `gguf`/`rkllm`/`rknn`, Standardwerte passend zum Board.
- Image-Builds nutzen BuildKit, wenn es verfügbar ist, und melden die echte Ursache von Fehlern; die Images
  einzelner Builds werden nach dem Build entfernt.
- Der Kalibrierungsdatensatz ist von der Importance Matrix getrennt (IMatrix nur für GGUF, der Datensatz geht
  direkt an RKLLM/RKNN).
- Das gespeicherte Hugging-Face-Token erreicht die Builds (eine gemeinsame Build-Engine).
- Relative Datenpfade werden gegen das Installationsverzeichnis aufgelöst, nicht gegen das Arbeitsverzeichnis.
- Self-Healing: Vorschläge warten auf eine ausdrückliche Entscheidung (GUI-Dialog, CLI-Abfrage oder MCP-Tool);
  angewendet werden nur feste, vom Framework definierte Korrekturen (weniger Compile-Jobs, nachdem einem
  Image-Build der Arbeitsspeicher ausging, Aufräumen von Framework-Images); KI-Vorschläge sind nur Ratschläge.
- Ditto: Fehler und Ablehnungen lösen Ausnahmen aus, statt als Antworten angezeigt zu werden; API-Schlüssel
  werden je Aufruf übergeben; Text aus der Wissensbasis, Logs und Probe-Dateien kommen als bereinigte, als nicht
  vertrauenswürdig markierte Blöcke in die Benutzernachricht.
- Abhängigkeiten mit Hash-Pins aktualisiert (Poetry-2-Lock, `requirements*.txt`); `pyside6-essentials` statt des
  vollständigen PySide6; `pydantic` und `mcp` hinzugefügt.
- Installer und Smoke-Test verlangen Python 3.11 bis 3.14.
- Eine einzige Versionsquelle (`orchestrator.__version__`).
- **Die Konfiguration wird nie mehr aus dem Arbeitsverzeichnis gelesen**: `LLM_FRAMEWORK_CONFIG_DIR`, dann
  `configs/` der Installation, dann `~/.llm-framework` (`framework.yml` im aktuellen Verzeichnis wird ignoriert).
- Der Secrets-Speicher liegt im Ordner des Benutzers (`~/.llm-framework/secrets.store`, einmalig vom alten Ort
  herüberkopiert); der Schlüssel des Audit-Logs hat einen eigenen Keyring-Eintrag.
- Capabilities: `default_board` (das Board, das die Build-Skripte verwenden, wenn keins angegeben ist), Aliasse
  für Quantisierungen je Format (z. B. `INT8` -> `Q8_0`), Board-Namen ohne Beachtung der Groß- und
  Kleinschreibung; CLI, Build-Dienst und GUI übergeben genau die Werte, die sie geprüft haben. IMatrix ohne eigenen
  Datensatz ist wieder gültig (mitgelieferter Kalibrierungstext).
- `build start -o` wird gegen das Arbeitsverzeichnis aufgelöst, der konfigurierte Standard gegen die
  Installation; `deploy list-artifacts` und `deploy package` lesen die Build-Manifeste im konfigurierten
  Ausgabeordner.

### Entfernt
- Die Abhängigkeiten LangChain, python-dotenv, pywin32 und winshell.
- Das gespeicherte SSH-Passwort (`target_password`) und sein Menüeintrag: Geräte nutzen den Schlüssel des
  Frameworks.
- Dittos generierte Füll-Kalibrierung (sie lud eine URL, die 404 liefert, und fiel auf wiederholte Sätze
  zurück).

### Behoben
- Viele Abstürze und tote Pfade: Enum für den Build-Status, GUI-Builds, die stoppten, sobald ein Slot
  zurückkehrte, Healing-Dialog, Chatfenster (schickte jede Frage zweimal, stürzte mit PySide6 6.12 bei jeder
  Nachricht ab), Import des Modul-Assistenten, harte Abbrüche und Log-Stufen der CLI, Deployment von Ordnern und
  Archiven.
- RKNN: Datensatz-Mounts ersetzen nicht mehr den Modell-Mount; JSON-Datensätze behalten ihre Endung; veraltete
  Artefakte anderer Boards werden nie ausgeliefert.
- Neustartschleife des Entrypoints, wenn der Host-Benutzer root ist; Prüfsummen mit Windows-Zeilenenden.
- Der Abbruch eines Builds kann nicht mehr von einer späteren Stufe rückgängig gemacht werden (Sicherheitsscan,
  Image-Build und Stufen-Container werden gestoppt); nur echte Lese-Zeitüberschreitungen zählen als
  Zeitüberschreitung; ein erneuter Versuch nach einem Healing-Fix behält seine eigene Build-ID und sein Log; eine
  beschädigte Audit-Datenbank oder ein beschädigter Benutzerordner hindert das Framework nicht mehr am Start.
- Audit-Log: entfernte neueste Einträge bleiben auch nach späteren Aufrufen erkennbar; keine falschen
  Manipulationsalarme nach einem fehlgeschlagenen Commit, einem fehlgeschlagenen Schreiben der Ankerdatei oder
  einem Eintrag mit einem anderen Schlüssel; das Audit-Log wird weitergeführt, nachdem die Datenbank beiseitegelegt
  wurde; `llm-cli tools audit --archive` beginnt eine neue Kette und lässt die alte prüfbar.
- Secrets: der Speicher einer früheren Version wird genau einmal übernommen (auch in einen bestehenden Speicher im
  Benutzerordner), und ein beschädigter wird gemeldet; ein laufender MCP-Server sieht Secrets, die ein anderer
  Prozess hinzugefügt oder gelöscht hat; nichts wird mehr in das Arbeitsverzeichnis geschrieben (Audit-Log der
  Secrets, Modellordner).
- Tool-Berechtigungen: eine tief verschachtelte Berechtigungsdatei oder ein Link, dessen Ziel fehlt, verweigert
  jedes Tool, wie andere Schäden auch.
- MCP: ein ausdrückliches „Nein“ bleibt endgültig, auch wenn sich die Berechtigungen inzwischen ändern; jede
  Ablehnung wird im Audit-Log festgehalten; Fehler beim Schreiben der Claude-Desktop-Konfiguration werden ohne
  Traceback gemeldet; `XDG_CONFIG_HOME` wird unter Linux beachtet; `-v` schreibt keine Log-Zeilen mehr in den
  Protokollkanal.
- Builds: ein abgebrochener Build hinterlässt kein Manifest und kein Archiv; ein fertiger Build gibt seinen Slot
  frei, bevor sein Endzustand sichtbar wird; Links im Ausgabeordner werden nie verfolgt; die GUI sendet für ein
  Target ohne Capabilities kein Board, außer es wurde eines eingetippt.

### Sicherheit
- **Konvertierungs-Container in der Sandbox**: Host-Benutzer statt root, kein Netzwerk, keine Capabilities,
  no-new-privileges, schreibgeschütztes Image-Dateisystem, Grenzen für Prozesse und Arbeitsspeicher.
- Crawler: SSRF-Schutz mit Verbindungs-Pinning, das gegen DNS-Rebinding sicher ist, robots.txt nach RFC 9309,
  Grenzen für Weiterleitungen und Anfragen.
- Modulerzeugung: strenge Prüfung von Namen, Images, Paketen, Flags und Shell-Code (Sperrliste, `bash -n`),
  Beschränkung auf `targets/`, kein Überschreiben bestehender Targets.
- Chat-Ausgaben werden maskiert; Links öffnen sich erst nach Bestätigung.
- Das Entpacken von Archiven blockiert Path Traversal, Links und Gerätedateien; Secrets werden in Logs
  geschwärzt.
- pip-audit meldet keine bekannten Schwachstellen (vorher 279); GitHub Actions sind per Commit-SHA gepinnt.
- Ein Projektordner kann die Einstellungen eines darin gestarteten MCP-Servers nicht mehr ändern (z. B. die
  Quell-Repositories der Build-Toolchain).
- Ein beschädigter Secrets-Speicher wird nie überschrieben; Secrets werden atomar mit den Rechten 0600
  geschrieben.
- Audit-Log: eine Schreibtransaktion je Eintrag (keine verzweigten Ketten bei mehreren Prozessen), eine
  Schlüssel-ID je Log, eine Ankerdatei, die entfernte neueste Einträge aufdeckt.
- Eine beschädigte Datei mit Tool-Berechtigungen verweigert jedes Tool, statt auf lockerere Standardwerte
  zurückzufallen.
- MCP: Bestätigungen zeigen jedes Argument vollständig (zu lang: abgelehnt), nur eine echte Antwort des Benutzers
  zählt als Zustimmung, abgelehnte und abgebrochene Aufrufe werden im Audit-Log festgehalten, Client-Snippets
  sind korrekt gequotet, und die Sicherungskopie der Claude-Desktop-Konfiguration ist privat.
- Der Startbefehl des MCP-Servers, der in Client-Konfigurationen geschrieben wird, importiert nie Code aus dem
  Ordner, in dem der Client ihn startet (`python -P`); Snippets für Windows sind für cmd und PowerShell gequotet;
  unsichtbare und kombinierende Zeichen in Bestätigungstexten werden maskiert angezeigt; Fehlermeldungen von Tools
  werden geschwärzt, bevor sie einen Client erreichen.
- **Secret-Scan** (`scripts/secret_scan.py`, nur Standardbibliothek): API-Schlüssel von Anbietern, Tokens,
  private Schlüssel, Zertifikate und Dateien mit Zugangsdaten werden von einem pre-commit- und einem
  pre-push-Hook (`make hooks`) und von einem CI-Job über die gesamte Historie abgewiesen. Persönliche
  Zeichenketten (E-Mail-Adresse, Benutzername) kommen in die nie committete Datei `.git/info/secret-patterns`;
  Funde werden geschwärzt ausgegeben.

## [2.4.0] - 2026-01-14
**Rekonstruierter Eintrag.** Die Versionen 2.0 bis 2.4 entstanden ohne Changelog-Einträge; diese Zusammenfassung
stammt aus den Kopfzeilen der Quelldateien und der Git-Historie (2025-12-13 bis 2026-01-14).

### Hinzugefügt
- Self-Healing-Manager (Regex-Heuristiken plus KI-Analyse von Build-Logs).
- Offline-Modus für Ditto mit kleinen lokalen Modellen, Telemetrie (Opt-in), Updater, Community-Hub.
- IMatrix „Smart Calibration“ für die GGUF-Quantisierung.
- Eine einzige Quelle der Wahrheit für Projektquellen (`configs/project_sources.yml`, `SSOT_ARCHITECTURE.md`,
  `validate_ssot.py`, `sync_ssot.py`).
- Deutsche Stimmmodelle (GLaDOS, Thorsten) im Quellenkatalog; Transparenz-Manager (experimentell).

### Geändert
- Container-Images zentral konfigurierbar; Sicherheitshärtung der GUI-Fenster.

## [1.7.0] - 2025-12-10
**Hardware- und Deployment-Update.** Schließt die Lücke zwischen Build und Betrieb mit einem Deployment-Manager
ohne zusätzliche Abhängigkeiten und voller Unterstützung für Intel-Hardware.

### Hinzugefügt
- **Deployment-Manager:** Integrierter SSH/SCP-Client, um Artefakte direkt auf Edge-Geräte zu übertragen. Mit
  einem Modus „Zero-Dependency“ (nutzt Programme des Systems oder Paramiko) und einer Ping-Schleife „Network
  Guard“ für eine stabile Verbindung.
- **Intel-Ökosystem:** Volle Unterstützung für Intel IPEX-LLM und OpenVINO. `hardware_probe` erkennt jetzt
  Intel-Arc-GPUs, iGPUs und NPU-Beschleuniger (Meteor Lake).
- **Live-Überwachung:** Anzeige der CPU- und RAM-Auslastung des Build-Containers in Echtzeit direkt in der GUI
  (ersetzt externe Werkzeuge wie `ctop`).
- **Visuelle Identität:** „Ditto“ erwacht zum Leben! Dynamische Avatar-Zustände im Assistenten (Denken, Lesen,
  Erfolg, Fehler) verbessern das Benutzererlebnis.
- **Automatische Dokumentation:** Der Builder erzeugt jetzt automatisch eine `Model_Card.md` mit SHA256-Hashes
  und einer Gebrauchsanleitung, gebündelt im ZIP „Golden Artifact“.

### Geändert
- **Hardware-Probe:** `hardware_probe.sh` und `.ps1` stark erweitert, um die Flags AVX512-VNNI und AMX zu
  erkennen, die für die Intel-Optimierung wesentlich sind.
- **GUI:** Schaltfläche „Deploy“ im Hauptfenster (aktiv nach einem erfolgreichen Build) und ein sicherer
  `DeploymentDialog`, der Zugangsdaten nur im RAM hält.
- **Builder:** Behandlung der Artefakte aktualisiert, um Modelldateien deterministisch zu erkennen und ein
  standardisiertes ZIP-Paket („Golden Artifact“) zu erzeugen.

## [1.6.0] - 2025-12-06
**Deep-Ingest-Release.** Befähigt den KI-Agenten „Ditto“, externe Dokumentations-Websites und PDFs rekursiv zu
crawlen, zu filtern und daraus zu lernen.

### Hinzugefügt
- **Deep-Crawler-Engine:** Integrierter rekursiver URL-Loader auf Basis von `LangChain`, um ganze
  Dokumentations-Websites zu verarbeiten (nicht nur einzelne Seiten).
- **PDF-Intelligenz:** Natives Einlesen von PDFs mit `pypdf`, damit Ditto Datenblätter und technische Handbücher
  lesen kann.
- **Intelligentes Filtern:** Spracherkennung (nicht englische Inhalte werden ignoriert) und Entfernen von
  Navigation und Fußzeilen für saubereres Wissen.
- **Wissens-Snapshots:** Der Modulgenerator extrahiert gelerntes Wissen jetzt automatisch aus Qdrant und bündelt
  es als `knowledge.json` im Target-Modul, damit es geteilt werden kann.
- **GUI-Dialoge:** Neuer `URLInputDialog`, um Dokumentations-Links gesammelt hinzuzufügen, mit Einstellungen für
  Crawl-Tiefe und Seitenlimit.
- **Rechtliche Vorgaben:** Verpflichtende Beachtung von „Robots.txt“ und ein Kontrollkästchen mit Haftungshinweis
  vor dem Crawlen.

### Geändert
- **Ablauf im Assistenten:** Schritt „Deep Ingest“ mit Live-Fortschrittsprotokoll in den Assistenten zur
  Modulerstellung integriert.
- **RAG-Manager:** `ingest_url` erweitert, um Ergebnisse des rekursiven Crawlens und PDF-Streams zu verarbeiten.
- **Config-Manager:** Einstellungen für `crawler_max_depth`, `crawler_max_pages` und den Eingabeverlauf
  hinzugefügt.

## [1.5.0] - 2025-12-01
**Expert-Knowledge-Release.** Macht den KI-Agenten „Ditto“ mit lokaler RAG-Technik von einem passiven Leser zu
einem aktiven Expertensystem.

### Hinzugefügt
- **Lokales RAG (experimentell):** Einbindung der Vektordatenbank Qdrant, um semantische Suche über
  Hardware-Dokumentation statt naivem Scraping zu ermöglichen.
- **Dynamische Sidecar-Architektur:** Qdrant läuft als Container bei Bedarf. Er wird nur heruntergeladen und
  gestartet, wenn der Benutzer die „Knowledge Base“ ausdrücklich einschaltet, um Ressourcen zu sparen.
- **Community Brain:** Neuer Ablauf, um indexierte Wissens-Snapshots über Git zu teilen. Benutzer können von der
  Community kuratierte Wissenspakete (`community/knowledge/*.json`) für Wissen ohne Internetverbindung
  importieren.
- **RAGManager:** Neue Kernkomponente (`orchestrator/Core/rag_manager.py`) für das Einlesen von Dokumenten, das
  Zerlegen in Abschnitte (Chunking) und die Vektorsuche.

### Geändert
- **Ditto-Agent:** Logik von `_fetch_documentation` aktualisiert, damit Ergebnisse der Vektorsuche Vorrang vor dem
  Herunterladen roher URLs haben, für mehr Präzision bei der Modulerzeugung.
- **DockerManager:** Logik ergänzt, um den Container `llm-qdrant` je nach Benutzerkonfiguration dynamisch zu
  starten und zu stoppen.
- **GUI:** Schalter „Enable Local Knowledge Base“ im Dialog der KI-Einstellungen hinzugefügt.

### Sicherheit
- **Datenschutz:** Vektordaten bleiben streng lokal (127.0.0.1). Es wird kein externer Vektorspeicher in der Cloud
  verwendet.
- **Bereinigung:** Export-Werkzeuge für Community-Wissen entfernen automatisch mögliche sensible Pfade oder
  API-Schlüssel, bevor Snapshots erstellt werden.

## [1.4.0] - 2025-11-30
**Smart-Calibration-Release.** Führt eine kontextbewusste Quantisierungs-Pipeline mit Human-in-the-Loop-Prüfung
ein, um auf NPU-Targets eine Modellqualität auf Enterprise-Niveau sicherzustellen.

### Hinzugefügt
- **Smart Calibration:** Automatischer Ablauf, der erkennt, ob ein Modell Kalibrierungsdaten braucht (z. B. INT8),
  und sie erzeugt oder auswählt.
- **DatasetManager:** Neue Kernkomponente (`orchestrator/Core/dataset_manager.py`) für deterministische
  Domänenerkennung und die Prüfung von Datensätzen.
- **Human-in-the-Loop:** `DatasetReviewDialog` hinzugefügt, damit Benutzer KI-erzeugte Kalibrierungsdaten vor dem
  Build prüfen und bearbeiten können.
- **Ditto-KI-Upgrade:** Dittos Fähigkeiten erweitert, synthetische domänenspezifische Trainingsdaten (Code, Chat,
  Medizin) auf Basis des SSOT-Wissens zu erzeugen.

### Geändert
- **Build-Pipeline:** `builder.py` und `docker_manager.py` aktualisiert, um Kalibrierungsdatensätze
  (`dataset.json`) in den Build-Container einzubringen.
- **Rockchip-Module:** `rkllm_module.sh` und `rknn_module.sh` (und ihre Python-Wrapper) aktualisiert, damit sie
  externe Kalibrierungsdatensätze annehmen und für eine genauere Quantisierung nutzen.
- **GUI:** Das `MainWindow` fragt jetzt gezielt nach Datensätzen, wenn empfindliche Quantisierungsmodi
  (INT8/W8A8) gewählt werden.
- **Richtlinie:** Heuristisches Raten von Modell-Domänen entfernt; stattdessen strenge Metadatenprüfungen oder
  Rückfragen beim Benutzer.

## [1.3.0] - 2025-11-29
**Production-Ready-Release.** Konzentriert sich auf vollständige Internationalisierung, Sicherheitshärtung und
darauf, den Kreis zwischen GUI und Build-Engine zu schließen.

### Hinzugefügt
- **I18n-Unterstützung:** Vollständige englische und deutsche Lokalisierung für GUI und Assistent.
- **LocalizationManager:** Zentraler Dienst für den dynamischen Sprachwechsel
  (`orchestrator/utils/localization.py`).
- **GUI-Erweiterungen:**
    - Neue Auswahllisten für **Task** (LLM, Voice, VLM) und **Quantisierung** (INT8, INT4, FP16).
    - Kontrollkästchen **„Use GPU“** für NVIDIA-Passthrough hinzugefügt.
    - Dialog zur Sprachauswahl beim ersten Start integriert.
    - **SecretsManager:** AES-256-Verschlüsselung für sensible API-Schlüssel.

### Geändert
- **Refactoring:** Textressourcen im `LocalizationManager` zentralisiert.
- **Abhängigkeiten:** `litellm`, `rich`, `psutil` und `cryptography` zu den Kernabhängigkeiten hinzugefügt.
- **Docker:** Statische Builder-Dienste zugunsten dynamisch gestarteter Container entfernt.

## [1.2.0] - 2025-11-29
**AI-Integration-Release.** Führt den Ditto-Agenten für automatische Hardware-Erkennung und Target-Erzeugung ein.

### Hinzugefügt
- **Ditto-KI-Agent:** Automatische Analyse von Hardware-Probes und Abruf von Dokumentation aus dem SSOT.
- **Smart Dispatcher:** Dynamische `build.sh`-Vorlage, die nach Task und Hardware verzweigt.
- **Vorlagen:** Vorlagen `rkllm_module` und `rknn_module` für die Unterstützung der Rockchip-NPU hinzugefügt.
- **Sicherheit:** `trivy-infra-scanner` für die automatische Schwachstellenprüfung der Container hinzugefügt.
- **NetworkGuard:** Robuste Verbindungsprüfungen und sichere Downloads.

### Geändert
- **Hardware-Probe:** Erkennung unter Windows über die native API (C#-Injection) gehärtet, für zuverlässige
  CPU-Flags.
- **Pipeline:** Erzeugungslogik in Python von den Bash-Skripten entkoppelt.

## [1.1.0] - 2025-11-28
**Architektur-Refactoring.** Entkoppelt den Kern-Builder von targetspezifischer Logik, um universelle
Cross-Kompilierung zu ermöglichen.

### Geändert
- **Architektur:** Fest codierte `TargetArch`-Enums entfernt; der Builder ist jetzt unabhängig vom Target.
- **Clean Code:** Eingebetteten Python-Code aus Bash-Skripten in eigenständige Dateien ausgelagert.
- **Builder:** Auf dynamisches Einhängen von Volumes für die Modulausführung umgestellt.

### Behoben
- **Stabilität:** Typfehler mit list/set im Windows-Installer behoben.
- **Cross-Kompilierung:** Fehlende Cross-Compiler (`gcc-aarch64`) in den Docker-Vorlagen ergänzt.

## [1.0.0] - 2025-11-20
**Erstes Release.** Legt die Grundstruktur des Frameworks und eine grundlegende Rockchip-Unterstützung an.

### Hinzugefügt
- Grundstruktur des Frameworks (Orchestrator, Builder, Docker-Manager).
- Grundlegende Unterstützung für die Cross-Kompilierung für Rockchip RK3588/RK3566.
- Einstiegspunkte für CLI (`llm-cli`) und GUI (`llm-gui`).
