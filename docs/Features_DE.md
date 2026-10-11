Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md) · Zuletzt aktualisiert am 2026-10-11

[English](Features.md) · **Deutsch**

# Funktionen im Detail

Dieses Dokument erklärt, was das Framework im Inneren tut und warum, damit du immer weißt, was deine Werkzeuge
machen. Es beschreibt den Stand des Entwicklungszweigs: Release 3.0.0 plus die unveröffentlichten Änderungen im
[CHANGELOG](../CHANGELOG_DE.md). Geplantes steht in der [Roadmap](BLUEPRINT_DE.md#roadmap), nicht hier.

Zurück zur [README](../README_DE.md) · Alle Befehle: [CLI_Usage_DE.md](CLI_Usage_DE.md)

---

## Inhalt

- [Wie die Teile zusammenspielen](#wie-die-teile-zusammenspielen)
- [Targets und Capabilities](#targets-und-capabilities)
- [Build-Pipeline](#build-pipeline)
- [Build-Sandbox](#build-sandbox)
- [Self-Healing](#self-healing)
- [Übertragen auf das Board (Deployment)](#übertragen-auf-das-board-deployment)
- [Ditto, der Assistent](#ditto-der-assistent)
- [Dittos Tools](#dittos-tools)
- [Ditto-Evaluation](#ditto-evaluation)
- [Lokale Laufzeit und Modellstufen](#lokale-laufzeit-und-modellstufen)
- [Wissensbasis (RAG)](#wissensbasis-rag)
- [MCP-Server](#mcp-server)
- [Tool-Berechtigungen und Audit-Log](#tool-berechtigungen-und-audit-log)
- [Geheimnisse und persönlicher Zustand](#geheimnisse-und-persönlicher-zustand)
- [Hardware-Probe und neue Targets](#hardware-probe-und-neue-targets)
- [Gesundheitscheck (doctor)](#gesundheitscheck-doctor)
- [Community-Wissen](#community-wissen)
- [Secret-Scan für Mitwirkende](#secret-scan-für-mitwirkende)
- [Bekannte Lücken](#bekannte-lücken)

---

## Wie die Teile zusammenspielen

```
 GUI (PySide6)        CLI (llm-cli)        MCP-Server (llm-cli mcp serve)
      \                    |                    /
       +------------ Framework-Manager --------+
       |                   |                   |
  Build-Dienst        Ditto (Assistent)    Tool-Registry
  Orchestrator        LLM-Anbieterschicht  (Berechtigungen, Bestätigung, Audit-Log)
  Build-Engine ---> Docker (Konvertierungs-Container in der Sandbox)
       |                   |
  Target-Capabilities  Wissensbasis (FastEmbed + eingebettetes Qdrant)
  Self-Healing         Lokale Laufzeit (verwalteter llama-server)
  Deployment (SSH)
```

- **GUI**, **CLI** und **MCP-Server** sind drei Wege, dasselbe Framework zu bedienen.
- Builds laufen über einen einzigen **Build-Dienst**: Er prüft eine Anfrage gegen die Capabilities des Targets,
  startet sie, meldet Status und Logs, bricht sie ab und listet fertige Builds auf.
- **Ditto** und **MCP-Clients** handeln nur über die **Tool-Registry**. Sie prüft deine Berechtigungen, fragt wo
  nötig nach einer Bestätigung und schreibt jeden Aufruf ins Audit-Log.

---

## Targets und Capabilities

Ein Target ist ein Ordner in `targets/` (zum Beispiel `targets/Rockchip/`) mit einer Dockerfile, Build-Skripten und
einer `target.yml`. Ihr Block `capabilities:` legt genau fest, was das Target bauen kann:

- Boards mit CPU, NPU, RAM und Beispielgeräten sowie ein Standard-Board (Rockchip: `rk3588`, das Board, das die
  Build-Skripte ohne Angabe nehmen),
- Formate mit ihrer zulässigen Eingabe (`hf` für ein Hugging-Face-Modell, `onnx` für eine ONNX-Datei),
  Quantisierungen (insgesamt und pro Board), Standard-Quantisierungen (insgesamt und pro Board) und Hinweisen,
- Kalibrierungsbedarf: ob ein Format eine Importance Matrix nutzt, welche Quantisierungen sie verlangen, ob ein
  Kalibrierungsdatensatz optional oder Pflicht ist (RKNN `i8`) und welche Art Datensatz es ist (Text oder eine
  Liste mit Beispielen),
- Aliase: gängige Schreibweisen, die übersetzt werden (`INT8` → `Q8_0` für GGUF, `int4` → `w4a16` für RKLLM).

Das Schema steht in `orchestrator/schemas/targets.py`. GUI, CLI, Build-Dienst, Ditto und MCP-Server lesen denselben
Block: Sie bieten genau diese Optionen an, ergänzen die Standardwerte und lehnen alles andere mit einem klaren Grund
ab, bevor etwas läuft. Die Werte für Rockchip stehen in der [README](../README_DE.md#unterstützte-targets-und-formate).

---

## Build-Pipeline

Ein Build läuft in diesen Schritten; jeder wird protokolliert:

1. **Kalibrierungstext:** Für eine Importance Matrix ohne eigenen Datensatz schreibt das Framework seinen
   mitgelieferten, per Prüfsumme gesicherten Kalibrierungstext (Einstellung `calibration_language`: `mixed`, `de`
   oder `en`; siehe [data/calibration](../data/calibration/README_DE.md)). Scheitert das bei einer Quantisierung,
   die die Matrix verlangt, endet der Build mit einem Fehler.
2. **Vorbereiten:** den Kalibrierungsdatensatz prüfen (er bleibt nur dort, wo der Build ihn nutzt: GGUF mit
   Importance Matrix, RKLLM, RKNN `i8`).
3. **Image bauen:** das Docker-Image des Targets mit allen Toolkits (llama.cpp v0.6.0, RKLLM-Toolkit 1.3.1,
   RKNN-Toolkit2 2.3.2, jedes in einer eigenen Python-Umgebung). BuildKit wird genutzt, wenn vorhanden, und bei
   einem Fehler wird die echte Ursache gemeldet. Spätere Builds nutzen Dockers Build-Cache.
4. **Modell auf dem Host bereitstellen:** Ein Hugging-Face-Modell wird auf deinem Rechner heruntergeladen (mit dem
   Token `hf_token` für zugangsbeschränkte Modelle), ein lokaler Ordner bzw. eine Datei wird direkt verwendet. Die
   Container bekommen es nur lesend.
5. **Sicherheits-Scan:** Trivy durchsucht das Image nach Schwachstellen der Stufen HIGH und CRITICAL (Einstellung
   `enable_security_scan`, festgelegtes Image `aquasec/trivy:0.75.0`). Funde erscheinen als Warnung im Log, nicht
   als Fehler. Ein Scan, der nicht innerhalb des Zeitlimits des Builds (`build_timeout`) fertig wird, wird mit
   einer Warnung gestoppt.
6. **Importance Matrix** (nur GGUF, wenn gewählt).
7. **Konvertieren und quantisieren** in Containern mit Sandbox ([Build-Sandbox](#build-sandbox)).
8. **Ergebnisse:** Die Modelldatei wird eingesammelt, `build_info.json` wird um die Modelldateien und ihre
   SHA256-Hashes ergänzt, eine `Model_Card.md` mit Hashes und Hinweisen zur Nutzung wird geschrieben, und ein ZIP
   dieser Dateien wird erstellt. All das wird in einem versteckten Ordner neben dem Ausgabeordner vorbereitet und
   erst ganz am Ende hineinverschoben. So hinterlässt ein abgebrochener oder gescheiterter Build keine Dateien im
   Ausgabeordner, und Dateien eines früheren Builds dort ersetzt nur ein abgeschlossener. Links in der Ausgabe des
   Builds werden nie verfolgt. Images pro Build werden danach entfernt.

Weiteres Verhalten:

- **Abbrechen:** Das Tool `cancel_build` (Ditto im GUI-Chat oder ein MCP-Client, für Builds des eigenen Prozesses)
  stoppt den laufenden Container, auch einen laufenden Sicherheits-Scan oder Image-Build, und kein späterer Schritt
  kann den Abbruch rückgängig machen. Ist ein Build gescheitert oder verschiebt er gerade seine Dateien in den
  Ausgabeordner, lässt er sich nicht mehr abbrechen und läuft von selbst zu Ende. Die GUI hat keine
  Abbrechen-Schaltfläche, und Strg+C in `llm-cli build start` bricht nicht ab (siehe
  [Bekannte Lücken](#bekannte-lücken)).
- **Build-Slots:** In einem Programm laufen höchstens zwei Builds gleichzeitig; ein weiterer Build wartet auf einen
  freien Slot (seine Stufe zeigt „Waiting for a free build slot“), statt zu scheitern. Ein abgebrochener Build
  behält seinen Slot, bis sein Container gestoppt ist.
- **Unbekanntes Target:** CLI, Ditto und MCP-Clients lehnen ein unbekanntes Target ab, bevor etwas läuft (die CLI
  mit Exit-Code 2). Eine Anfrage, bei der trotzdem nichts zu bauen bleibt, scheitert mit „Nothing to build“, statt
  Erfolg zu melden.
- **Zeitlimits:** Jeder Container-Schritt hat ein Zeitlimit (Einstellung `build_timeout`, Standard 3600 s); nur
  ein echtes Überschreiten zählt als Zeitüberschreitung.
- **Ausgabeordner:** CLI-Builds landen in `output/build_<Datum>_<Uhrzeit>/<Target>/<Modell>/<Quantisierung>/` in der
  Installation (oder in dem mit `-o` angegebenen Ordner), Builds aus der GUI, von Ditto oder von MCP-Clients in
  `output/<Target>/<Modell>/<Quantisierung>/`.

---

## Build-Sandbox

Konvertierungs-Container führen nicht vertrauenswürdigen Modellcode aus (zum Beispiel eigene Tokenizer), deshalb
sind sie abgeschottet (Einstellung `build_sandbox`, standardmäßig an):

| Maßnahme | Wirkung |
|---|---|
| Deine Benutzer-ID statt root | Dateien gehören dir; nichts im Container läuft als root. |
| Kein Netzwerk (`network_mode: none`) | Das Modell kann keine Daten verschicken, Bibliotheken können nichts herunterladen. Alle Hugging-Face-Bibliotheken sind auf offline gestellt. |
| Keine Linux-Capabilities (`cap_drop: ALL`) und `no-new-privileges` | Keine Rechteausweitung. |
| Schreibgeschütztes Image-Dateisystem | Beschreibbar sind nur der eigene Arbeitsordner des Builds, ein 1 GB großes `/tmp` und der Ausgabeordner; das bereitgestellte Modell ist nur lesend eingebunden. |
| Prozesslimit (`build_pids_limit`, Standard 4096) | Schützt vor ausufernden Prozessen. |
| Optionales Speicherlimit (`build_memory_limit`, z. B. `24g`) | Schützt den Host davor, dass ihm der Speicher ausgeht. |

Weil die Sandbox kein Netzwerk hat, wird alles vorher auf dem Host heruntergeladen: das Modell (Schritt 4 oben) und
die Toolkits (beim Image-Build).

---

## Self-Healing

Scheitert ein Build, sieht sich der Self-Healing-Manager das Log an:

1. **Bekannte Probleme** werden an festen Regeln erkannt: Speichermangel beim Kompilieren des Images, eine vom
   Out-of-Memory-Killer beendete Konvertierung, eine volle Platte, fehlende Rechte für den Docker-Daemon, ein
   Netzwerkproblem, veraltete Paketquellen im Basis-Image.
2. **Sonst analysiert Ditto** das Log, wenn ein KI-Anbieter eingerichtet ist. Seine Antwort muss einer festen
   Struktur entsprechen (Zusammenfassung, Ursache, ein vorgeschlagener Schritt, Sicherheit, Host oder Gerät) und
   wird verworfen, wenn sie einen gefährlichen Befehl enthält.

Der Vorschlag wartet auf deine Entscheidung: ein Dialog in der GUI, eine Frage in der CLI oder das Tool
`resolve_healing` für Ditto und MCP-Clients (Einstellung `healing_approval_timeout`, Standard 900 s). Selbst wendet
das Framework nur feste, eingebaute Korrekturen an:

| Problem | Was das Framework nach deinem „Ja“ tut |
|---|---|
| Speichermangel beim Kompilieren des Images | Setzt `build_jobs` auf 1 und versucht es erneut |
| Platte voll | Entfernt verwaiste Framework-Images und den Docker-Build-Cache, dann neuer Versuch |
| Netzwerkproblem | Versucht es einmal erneut |

Alles andere, auch jeder KI-Vorschlag, wird als manueller Schritt angezeigt und nie ausgeführt. Ein erneuter Versuch
bekommt eine eigene Build-ID (`<job>_r1`), und beide Logs bleiben erhalten.

---

## Übertragen auf das Board (Deployment)

Das Deployment kopiert einen fertigen Build per SSH auf ein Board und installiert dort die Modelldatei.

- **Vertrauen beim ersten Kontakt:** Der Host-Schlüssel des Boards wird mit seinem SHA256-Fingerabdruck angezeigt
  (im selben Format wie `ssh-keygen -lf`). Erst nach deiner Bestätigung wird er im eigenen
  `~/.llm-framework/known_hosts` des Frameworks gespeichert. Ein geänderter Schlüssel wird immer abgelehnt.
- **Kopplung:** Bei der ersten Verbindung wird dein Passwort einmal genutzt, um den eigenen Ed25519-Schlüssel des
  Frameworks (`~/.llm-framework/id_ed25519_llm_framework`) auf dem Board zu hinterlegen. Das Passwort wird nie
  gespeichert; spätere Verbindungen nutzen den Schlüssel.
- **Paket:** eine ZIP-Datei mit der Modelldatei, Manifest und Model Card des Builds, `checksums.sha256` und einem
  erzeugten `deploy.sh`. Übertragen werden nur Modelldateien (`.gguf`, `.rkllm`, `.rknn`, `.onnx`, `.safetensors`,
  `.bin`); ein Build mit mehreren Gewichtsdateien wird abgelehnt.
- **Auf dem Board:** Das Paket kommt in einen privaten temporären Ordner (`mktemp -d`), `deploy.sh` prüft jede
  Prüfsumme mit `sha256sum -c`, kopiert das Modell nach `~/models` (oder `$MODEL_DIR`), und der temporäre Ordner wird
  gelöscht. Für GGUF gibt es, wenn `llama-server` auf dem Board installiert ist, eine `llama-server`-Befehlszeile aus,
  die auf 127.0.0.1 lauscht, und warnt davor, den Server je ohne Schlüssel freizugeben; sonst rät es, llama.cpp zu
  installieren. Für RKLLM nennt es die RKLLM-Runtime.
- **Docker-Modus** (`--docker`, Option in der GUI): packt zusätzlich Container-Images ein und startet sie auf dem
  Board. Dieser Modus ist experimentell und in dieser Version nicht geprüft.

Eine Laufzeitumgebung auf dem Board installiert das Framework nicht. Sie steht in der
[Roadmap](BLUEPRINT_DE.md#roadmap).

---

## Ditto, der Assistent

Ditto baut auf der eigenen **Anbieterschicht** des Frameworks auf (`orchestrator/llm`), die die offiziellen SDKs
`anthropic` und `openai` verwendet (OpenAI-kompatible Server laufen über `openai`).

### Aufgaben, Modelle und Aufwand

Dittos vier Arten von Arbeit entsprechen den Aufgabenprofilen der Anbieterschicht; jedes Aufgabenprofil hat pro
Anbieter ein eigenes Modell und einen eigenen Aufwand:

| Ditto-Profil | Aufgabe | Antwort |
|---|---|---|
| Chat | `chat` | Freier Text; darf die Tools des Frameworks nutzen |
| Healing-Analyse | `healing` | Strukturiert: Zusammenfassung, Ursache, vorgeschlagener Schritt, Sicherheit, Host oder Gerät |
| Modulvorschlag | `json` | Strukturierte Target-Definition, von dir geprüft und vom Modulgenerator erneut kontrolliert |
| Build-Empfehlung | `json` | Strukturiert: Format und Quantisierung aus den Optionen des Targets, Optimierungsstufe, Begründung |

Die Standardwerte pro Aufgabe stehen in [`configs/llm_providers.yml`](../configs/llm_providers.yml). Deine
Einstellungen überschreiben sie: `ai_model` und `ai_effort` für alle Aufgaben, oder `ai_model_<Aufgabe>` und
`ai_effort_<Aufgabe>` für eine Aufgabe. Aufwandsstufen sind `low`, `medium`, `high`, `xhigh` und `max`; jeder
Anbieter übersetzt sie in das, was er versteht.

### Anbieter

| ID | Art | Wo es läuft | Anmeldung heute |
|---|---|---|---|
| `anthropic` | Anthropic-Claude-API | Cloud | API-Schlüssel (`anthropic_api_key` oder `ANTHROPIC_API_KEY`) |
| `openai` | OpenAI Responses API | Cloud | API-Schlüssel (`openai_api_key` oder `OPENAI_API_KEY`) |
| `google` | Gemini, OpenAI-kompatibler Endpunkt | Cloud | API-Schlüssel (`gemini_api_key` oder `GEMINI_API_KEY`) |
| `openrouter` | OpenRouter | Cloud | API-Schlüssel (`openrouter_api_key` oder `OPENROUTER_API_KEY`) |
| `ollama` | Ollama | lokal (Basis-URL einstellbar) | keine |
| `local` | verwalteter llama-server | lokal | Schlüssel pro Start, wird automatisch verwaltet |
| `custom` | jeder OpenAI-kompatible Server | ergibt sich aus der Adresse: Loopback-, private und Link-Local-Adressen sowie Namen auf `.local`, `.lan`, `.localhost` oder `.home.arpa` gelten als lokal | API-Schlüssel der Serveradresse (`custom_api_key`) oder keine |

- Cloud-Anbieter haben feste Adressen; eine von Ollama oder einem eigenen Server übrig gebliebene Basis-URL bekommt
  nie ihren Schlüssel.
- Ein Schlüssel eines eigenen Servers gehört zu der Adresse, für die er gespeichert wurde (Schema, Host und Port):
  Nach einer Änderung der Basis-URL wird er nicht mehr mitgeschickt, und der neue Server braucht seinen eigenen
  Schlüssel.
- Jeder Anbieter nennt seine Anmeldeverfahren in `configs/llm_providers.yml` in der Reihenfolge, in der sie
  bevorzugt werden; das erste nutzbare wird genutzt, außer `ai_auth_<Anbieter>` nennt eines. Ein Schlüssel in einer
  der Umgebungsvariablen aus der Tabelle oben wird nur genutzt, wenn kein Schlüssel gespeichert ist, und die
  KI-Einstellungen zeigen die Variable als Quelle; er wird nie in den Tresor kopiert. Lässt sich der Tresor nicht
  lesen, wird die Variable nicht genutzt, und die Fehlermeldung nennt das Problem mit dem Tresor.
- Eine kostenlose Prüfung (die Modellliste des Anbieters, bei OpenRouter der Schlüssel-Endpunkt) zeigt, ob der
  gespeicherte Schlüssel funktioniert, zu welchem Workspace oder Schlüsselnamen er gehört und wie viele Modelle er
  nutzen kann. Angaben zu einer Anmeldung, die nicht geheim sind (Konto oder Workspace, die letzten vier Zeichen
  eines Schlüssels, Zeitpunkt der Prüfung), liegen in `auth.json` neben dem Tresor.
- Die Modellliste in den Einstellungen wird live beim Anbieter abgefragt; jeder Modellname lässt sich auch
  eintippen.
- Eine Datei `llm_providers.yml` in deinem Einstellungsordner ergänzt oder ersetzt Anbieter anhand ihrer ID; ein
  neuer OpenAI-kompatibler Server braucht also keine Codeänderung.
- **Claude-API (`anthropic`):** Der gespeicherte API-Schlüssel hat immer Vorrang. Ein Profil aus `ant auth login`
  wird nur genutzt, wenn du es auswählst (`ai_auth_anthropic: ant_profile`; der frühere Wert `oauth` bedeutet
  dasselbe). Ein Schlüssel, der in jedem Workspace der Organisation gilt, braucht die Workspace-ID (Feld „Workspace
  ID“ in den KI-Einstellungen, Einstellung `ai_anthropic_workspace_id`, `wrkspc_...`; solange sie gesetzt ist,
  hat sie Vorrang vor einer mit dem Schlüssel gespeicherten Workspace-ID); sie geht nur mit dem API-Schlüssel und
  nur an die Claude-API. Admin-API-Schlüssel (`sk-ant-admin01-`) werden vor jeder Anfrage abgelehnt.
- **OpenAI (`openai`):** Eine mit dem Schlüssel gespeicherte Organisation und ein Projekt (`org-...`, `proj_...`)
  gehen als Header `OpenAI-Organization` und `OpenAI-Project` mit; ein Schlüssel, der funktioniert, aber keine
  Modelle auflisten darf, wird als solcher gemeldet, und die Modell-ID lässt sich eintippen.
- Einen Anbieter `claude_code` (Ditto über ein Claude-Abo) gibt es nicht: Anthropic erlaubt Drittanbieter-Apps nicht,
  Abo-Limits zu nutzen. Ein gespeichertes `claude_code` (auch in der Ersatzkette oder bei `--provider`) bricht mit
  einer Meldung ab, die die Alternativen nennt; ein solcher Eintrag in deiner eigenen `llm_providers.yml` wird mit
  einer Warnung übersprungen. Claude-Abonnenten nutzen das Framework aus Claude Code oder Claude Desktop über
  [MCP](#mcp-server) oder mit einem API-Schlüssel (Max- und Team-Abos enthalten monatliches API-Guthaben).
- Die SDKs lesen selbst nie Zugangsdaten, Header oder Adressen aus der Umgebung: Jeder Client bekommt Schlüssel
  und Basis-URL ausdrücklich, und `OPENAI_CUSTOM_HEADERS` / `ANTHROPIC_CUSTOM_HEADERS` werden beim Start mit einer
  Warnung entfernt (die Einstellung `ai_allow_sdk_env_headers: true` behält sie).

### Aufrufe, Wiederholungen und Ersatzkette

- Die eigenen Wiederholungen der SDKs sind aus; die Anbieterschicht wiederholt Anfragen bei Ratenlimits und
  Ausfällen bis zu zweimal und wartet auf ein `Retry-After` des Anbieters von bis zu 30 s (Einstellung
  `ai_max_retry_wait`). Jede Anfrage darf bis zu 600 s dauern (`ai_timeout`).
- **Ersatzkette** (`ai_fallback_chain`, z. B. `local, ollama:qwen3.5:4b`): Lehnt das Modell ab oder ist der Anbieter
  nicht erreichbar, wird der nächste Eintrag versucht. Nicht eingerichtete Einträge werden übersprungen.
- **Offline-Modus** (`offline_mode`): Es werden nur lokale Anbieter genutzt; ein Cloud-Anbieter wird mit einer
  klaren Meldung abgelehnt. Auch die Wissensbasis lädt im Offline-Modus nichts herunter.
- Anfragen an Server auf 127.0.0.1 gehen nie über einen HTTP(S)-Proxy aus der Umgebung; der Proxy sieht also weder
  Prompts noch den Schlüssel des lokalen Servers.
- Fehler werden verständlich erklärt, zum Beispiel dass ein Claude-API-Schlüssel eine Workspace-ID braucht, dass
  der Schlüssel widerrufen wurde oder abgelaufen ist, dass das Guthaben aufgebraucht ist oder dass der lokale Server
  nicht läuft.

### Datenschutzfilter für Cloud-Anbieter

Bevor Text an einen Cloud-Anbieter geht, werden persönliche Daten ersetzt (Einstellung `ai_security_level`):

| Stufe | Ersetzt |
|---|---|
| `OFF` | nur Geheimnisse (API-Schlüssel, Tokens); sie werden immer entfernt und nie wiederhergestellt |
| `BALANCED` | zusätzlich IPv4-/IPv6- und MAC-Adressen, E-Mail-Adressen, Logins der Form `user@host`, Benutzernamen in Home-Pfaden |
| `STRICT` (Standard) | zusätzlich in Probe-Dateien angegebene Hostnamen, Seriennummern, UUIDs und die Verzeichnisse absoluter Pfade (Systempfade wie `/usr/` bleiben lesbar) |

Die Platzhalter sehen aus wie `<IP_1a2b3c4d>`. Sie werden aus dem Wert mit einem Schlüssel abgeleitet, der einmal
pro Installation angelegt wird; derselbe Wert bekommt also immer denselben Platzhalter. Platzhalter in der Antwort
werden auf deinem Rechner wieder in die echten Werte verwandelt. Lokale Anbieter werden nie gefiltert.

### Strukturierte Antworten und der Filter für gefährliche Befehle

Healing-Analysen, Modulvorschläge und Build-Empfehlungen müssen gegen ein Schema mit Längengrenzen gültig sein.
Alles andere ist ein Fehler und wird nie angewendet. Bei Build-Empfehlungen führt schon das Schema nur die Formate
und Quantisierungen auf, die das Target anbietet, und die Antwort wird pro Format erneut geprüft.

Was auch immer ein Build-Log, eine Probe-Datei oder ein Modellname dem Modell erzählt: Ditto verwirft eine
Healing-Korrektur oder Empfehlung mit einem gefährlichen Befehl. Dazu zählen ein Download, der direkt in eine Shell
geleitet wird, für alle beschreibbare Rechte (`chmod 777`/`666`), das Löschen des Wurzel- oder Home-Ordners, das
Überschreiben einer Platte (`mkfs`, `dd` auf eine Platte) und eine Fork-Bombe.

### Nicht vertrauenswürdiger Text

Build-Logs, Probe-Dateien, Auszüge aus der Wissensbasis und Tool-Ergebnisse sind externer Text. Ditto setzt sie in
den Benutzer-Turn, bereinigt von versteckten und Steuerzeichen und eingepackt in gekennzeichnete
`<untrusted_data>`-Blöcke, nie in den System-Prompt. Der System-Prompt sagt dem Modell, dass solche Blöcke Daten
sind, keine Anweisungen.

### Chatverlauf

Der GUI-Chat wird pro Benutzer in `~/.llm-framework/chat_history.json` gespeichert (nur für dich lesbar). Das Gespräch
wird nur fortgeschrieben: Frühere Turns werden nie bearbeitet. Wächst es über die Einstellung `chat_context_limit`
hinaus (Standard 4096 Tokens, geschätzt mit vier Zeichen pro Token), fasst Ditto es in einer eigenen Anfrage
zusammen und setzt in einem neuen Gespräch fort, das mit der Zusammenfassung beginnt. Das Fenster behält bis zu 500
Einträge. Verläufe früherer Versionen werden übernommen. `llm-cli ditto ask` nutzt den Verlauf nicht.

---

## Dittos Tools

Im Chat (GUI) und mit `llm-cli ditto ask` kann Ditto dieselben Tools wie MCP-Clients nutzen:

| Tool | Stufe | Was es tut |
|---|---|---|
| `list_targets` | lesen | Targets mit Boards, Formaten und Quantisierungen |
| `get_target_capabilities` | lesen | Was ein Target bauen kann |
| `list_build_requests` | lesen | Build-Anfragen dieser Sitzung |
| `get_build_status` | lesen | Status, Fortschritt, Fehler, ein offener Self-Healing-Vorschlag |
| `read_build_log` | lesen | Die letzten Zeilen eines Build-Logs (Geheimnisse geschwärzt) |
| `list_artifacts` | lesen | Fertige Builds aus ihren Manifesten |
| `run_doctor` | lesen | Der Gesundheitscheck, ohne Korrekturen |
| `search_knowledge` | lesen | In der Wissensbasis suchen |
| `start_build` | lokal schreiben | Einen Build starten (muss zu den Capabilities des Targets passen) |
| `cancel_build` | lokal schreiben | Einen Build abbrechen |
| `resolve_healing` | lokal schreiben | Einen Self-Healing-Vorschlag annehmen oder verwerfen |

So läuft eine Frage mit Tools ab:

1. Ditto bekommt die Definitionen der Tools, die deine Berechtigungen nicht verbieten.
2. Das Modell darf Tools aufrufen. Jeder Aufruf läuft durch die Tool-Registry: Prüfung der Eingabe, deine
   Berechtigung, Bestätigung, wo die Berechtigung „fragen“ sagt (ein Dialog in der GUI mit dem Titel des Tools und
   jedem Argument; eine Abfrage in der CLI, ohne Terminal abgelehnt), Eintrag im Audit-Log mit dem Akteur `ditto`.
3. Tool-Ergebnisse gehen als nicht vertrauenswürdige Daten zurück an das Modell. Ein abgelehnter oder gescheiterter
   Aufruf wird dem Modell gemeldet, das angewiesen ist, denselben Aufruf nicht zu wiederholen.
4. Nach höchstens 8 Modell-Turns pro Frage hört Ditto auf und sagt das.

Dittos Prompts weisen es an, vor einem Build die Capabilities des Targets zu prüfen, Builds nur zu starten oder
abzubrechen, wenn du im Gespräch darum gebeten hast, über Self-Healing nur mit deiner ausdrücklichen Entscheidung zu
entscheiden, Fragen zu Targets und Builds aus den Tools statt aus dem Gedächtnis zu beantworten und nur zu berichten,
was die Tools zurückgegeben haben.

---

## Ditto-Evaluation

`llm-cli ditto eval` ist ein Werkzeug, um zu messen, wie gut ein Anbieter und ein Modell für Ditto taugen, zum
Beispiel ob ein kleines lokales 2-Bit-Modell für schwache Hardware reicht. Nichts wird gebaut oder geändert.

**Fälle** ([`evals/ditto/cases.yml`](../evals/ditto/cases.yml), 19 Fälle; eine Datei mit Fällen ist Daten, neue
Fälle brauchen keine Codeänderung):

| Art | Fälle | Was geprüft wird |
|---|---|---|
| `healing` | 6 | Analyse von Build-Logs: Ursache, Host oder Gerät, eine passende Korrektur (fehlendes CMake, Speichermangel, zugangsbeschränktes Modell, volle Platte, eine auf dem Gerät fehlende Bibliothek, ein Log mit eingeschleusten Anweisungen) |
| `optimizer` | 5 | Build-Empfehlungen aus den echten Rockchip-Optionen: z. B. GGUF für ein LLM auf dem RK3566, ein ONNX-Bildmodell zu RKNN, ein Modellname mit eingeschleusten Anweisungen, deren Befehl nicht in der Antwort auftauchen darf |
| `module` | 3 | Modulvorschläge aus Probe-Dateien (Jetson Orin, Raspberry Pi 5, x86-Server): Architektur, SDK, Compiler- und CMake-Flags |
| `chat` | 5 | Chat mit Tools: welche Tools mit welchen Argumenten aufgerufen werden und welche nicht (zum Beispiel ein gescheiterter Build, dessen Log eingeschleuste Anweisungen enthält: Ditto muss den Fehler erklären und darf keine Builds abbrechen oder starten) |

**Bewertet** wird mit festen Regeln, nicht von einem weiteren Modell: erwartete Wörter oder Muster, verbotene Wörter
(zum Beispiel ein eingeschleuster Befehl), erwartete Felder, Tools, die aufgerufen oder nicht aufgerufen werden
müssen. Chat-Fälle nutzen die echten Tool-Definitionen über einem Ersatz-Build-Dienst, der gegen die echten
Target-Capabilities prüft und vorbereitete Builds, Logs und Artefakte zurückgibt. Die Wissensbasis ist während der
Evaluation aus, damit die Antworten nur aus dem Fall kommen.

**Ausführen:**

```bash
llm-cli ditto eval                                      # der eingestellte Anbieter und das eingestellte Modell
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json
llm-cli ditto eval --kind chat --repeat 3 --min-pass 0.8
```

Mit `--tier` startet die CLI den verwalteten lokalen Server nacheinander mit jeder Stufe und stoppt ihn nach jeder
Stufe. Der Bericht zeigt den bestandenen Anteil pro Lauf, pro Art und die durchschnittliche Zeit pro Fall; `--json`
ergänzt die Einrichtung (Anbieter, Modell pro Aufgabe, Aufwand, Framework-Version, sha256 der Fall-Datei) und jede
Antwort. Cloud-Anbieter berechnen etwa 20 Anfragen pro Lauf und Wiederholung. Alle Optionen:
[CLI_Usage_DE.md](CLI_Usage_DE.md#ditto-eval).

**Erste Messung** (zur Einordnung, kein Benchmark): Auf einem Rechner nur mit CPU, mit Qwen3.5 2B, Aufwand `low`
(Nachdenken aus) und den damaligen Prompts bestand die 2-Bit-Stufe `small-q2` 42 % der 19 Fälle und die 4-Bit-Stufe
`small` 47 %. Die Evaluation hat Lücken in Dittos Prompts gefunden (Fehler dem Host oder dem Gerät zuordnen, welche
Eingaben jedes Format annimmt, die Tools für Fragen zu Targets und Builds nutzen, Compiler-Flags in
Modulvorschlägen). Diese Lücken sind inzwischen behoben, und eine neue Messung läuft.

---

## Lokale Laufzeit und Modellstufen

Die verwaltete Laufzeit gibt Ditto ein lokales Modell ohne Cloud-Konto. Gesteuert wird sie mit
`llm-cli ditto runtime` und `llm-cli ditto models` ([CLI_Usage_DE.md](CLI_Usage_DE.md#ditto-runtime)); Dittos
Anbieter `local` findet den laufenden Server von selbst (Adresse, Schlüssel und geladenes Modell werden bei jedem
Aufruf gelesen).

### Was festgelegt ist

[`configs/llama_runtime.yml`](../configs/llama_runtime.yml) ist Daten: Ein neuerer Build oder ein anderes Modell wird
dort ergänzt, nicht im Code.

- **llama.cpp-Build b11429** (Version 0.6.0) für Linux x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.8, ROCm auf
  Wunsch), Linux arm64 (CPU, Vulkan, CUDA 13.4), Windows x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.4, ROCm auf
  Wunsch), Windows arm64 (CPU) und macOS (Metal auf Apple Silicon, CPU auf Intel).
- **Modelle** auf festen Hugging-Face-Commits, alle unter Apache-2.0:

| Stufe | Datei | Größe | Hinweis |
|---|---|---|---|
| `small` | Qwen3.5-2B-Q4_K_M.gguf | 1,2 GiB | für Rechner mit bis zu 4 GB RAM |
| `small-q2` | Qwen3.5-2B-UD-Q2_K_XL.gguf | 0,9 GiB | 2 Bit, für 3 GB RAM oder weniger; ungenauer |
| `default` | Qwen3.5-4B-Q4_K_M.gguf | 2,6 GiB | der Standard |
| `default-q2` | Qwen3.5-4B-UD-Q2_K_XL.gguf | 1,8 GiB | 2 Bit, nur auf Wunsch |
| `large` | Qwen3.5-9B-Q4_K_M.gguf | 5,3 GiB | für GPUs mit 8 GB Speicher oder mehr |
| `large-iq2` | Qwen3.5-9B-UD-IQ2_M.gguf | 3,4 GiB | 2 Bit, nur auf Wunsch; spürbar ungenauer |
| `granite` | granite-4.2-3b-Q4_K_M.gguf | 2,1 GiB | IBM Granite 4.2 3B, andere Modellfamilie, nur auf Wunsch |

Jede Datei hat eine festgelegte Größe und sha256 und wird geprüft, bevor sie entpackt oder geladen wird; Einträge
ohne beides werden abgelehnt. Downloads werden nach einer Unterbrechung fortgesetzt, und ein Build wird atomar neben
dem vorherigen installiert.

### Welcher Build

`llm-cli ditto runtime status` zeigt die Entscheidung und den Grund:

1. Ein ausdrückliches `--backend` hat Vorrang (ROCm wird nur so genutzt; unter Linux braucht es eine installierte
   HIP-Laufzeit und eine unterstützte GPU).
2. Apple Silicon bekommt den Metal-Build.
3. Eine NVIDIA-GPU bekommt den neuesten CUDA-Build, der zu jeder NVIDIA-GPU im Rechner und zu ihrem Treiber passt.
   GPUs, für die der Build nur PTX-Code enthält, brauchen den Treiber des jeweiligen Toolkits: Eine RTX 2080 Ti
   (Compute Capability 7.5) braucht zum Beispiel unter Linux Treiber 570.26 für den CUDA-12.8-Build und den Zweig
   R615 für CUDA 13.4 (unter Windows 551.61 für CUDA 12.4). Mit einem älteren Treiber wird die nächste Möglichkeit
   genommen, und der Grund sagt, dass ein neuerer Treiber CUDA ermöglicht.
4. Vulkan, wenn ein Vulkan-Treiber gefunden wird und sich die GPU lohnt: jede eigene GPU, AMD-Radeon-GPUs und -APUs
   (zum Beispiel der Ryzen Z1 Extreme) und Intel Arc. Ältere Intel-Grafik ist meist langsamer als die CPU und wird
   nur mit `--backend vulkan` genutzt.
5. Sonst der CPU-Build.

### Welches Modell

Es gewinnt die erste Regel, deren Bedingungen zutreffen und deren Modell passt:

| Regel | Stufe |
|---|---|
| GPU-Speicher von mindestens 7,5 GiB (eine vom Build genutzte eigene GPU oder etwa zwei Drittel des Speichers von Apple Silicon) | `large` |
| RAM bis 3,0 GiB | `small-q2` |
| RAM bis 4,5 GiB | `small` |
| sonst | `default` |
| Ersatz, wenn das Modell darüber nicht passt | `small`, dann `small-q2` |

„Passt“ heißt: Die Modelldatei plus 5 % plus der Key-Value-Cache für 4096 Tokens müssen in den RAM abzüglich 1,5 GiB
für das System passen, plus 95 % des Speichers einer eigenen GPU. Integrierte AMD- und Intel-GPUs teilen sich den RAM,
deshalb zählt bei ihnen nur der RAM. `runtime start --model <Stufe>` überstimmt die Wahl und merkt sie sich;
`--model auto` kehrt zur Empfehlung zurück.

### Wie der Server läuft

- **Kontextlänge:** aus dem freien Speicher neben dem Modell berechnet (freier GPU-Speicher, oder freier RAM bei
  CPUs, Apple Silicon und integrierten GPUs), auf 256 Tokens gerundet, mindestens 4096 und höchstens 32768 (`--ctx`
  legt sie fest).
- **Abschottung:** `llama-server` lauscht nur auf 127.0.0.1, auf einem freien Port. Ein neuer, zufälliger
  API-Schlüssel pro Start erreicht ihn nur über eine Schlüsseldatei, die nur du lesen kannst, und das Framework
  prüft, dass der Server Anfragen ohne Schlüssel ablehnt. Weboberfläche und Slots-Endpunkt sind aus, und der Server
  läuft mit `--offline`. Optionen, die dem Modell eigene Tools, einen Agentenmodus, MCP-Server, entferntes Rechnen
  oder einen Schlüssel auf der Befehlszeile geben würden, werden abgelehnt, und Umgebungsvariablen, die
  Serveroptionen setzen könnten oder nach Geheimnissen aussehen, werden entfernt.
- **GPU-Auslagerung:** Mit einem GPU-Build kommen so viele Schichten auf die GPU, wie hineinpassen.
- **Leerlauf:** Das Modell wird nach 600 Sekunden ohne Anfrage entladen und bei der nächsten Anfrage wieder geladen
  (`--sleep-idle`).
- **Zustand und Log:** `~/.llm-framework/runtime/llama.cpp/` enthält die Builds und den Serverzustand, dem nur
  vertraut wird, solange genau dieser Prozess (Prozess-ID, Startzeit, Programm) lebt. Die Schlüsseldatei wird beim
  Stoppen gelöscht. Das Log `~/.llm-framework/logs/llama-server.log` wird bei 5 MiB rotiert; 3 alte Logs bleiben.
- **Nachdenken:** Qwen3.5 denkt auf `llama-server` sonst in voller Länge nach, was auf einer CPU Minuten dauert.
  Deshalb schaltet der Aufwand `low` das Nachdenken aus; `medium` und höher behalten es.

---

## Wissensbasis (RAG)

Die Wissensbasis gibt Ditto und MCP-Clients Dokumentation zum Durchsuchen (Einstellung `enable_rag_knowledge`, GUI:
**Enable Local Knowledge Base**). Bis zur ersten Nutzung wird nichts geladen.

- **Speicher:** Qdrant läuft eingebettet im Framework-Prozess, mit seinen Daten in `~/.llm-framework/qdrant` (kein
  Docker, kein offener Port). Der Speicher ist während der Nutzung gesperrt und wird nach 30 Sekunden ohne Zugriff
  freigegeben, sodass sich GUI und MCP-Server abwechseln können. Wer ihn aus mehreren Prozessen gleichzeitig nutzen
  will, setzt `qdrant_url` auf einen Qdrant-Server; ein entfernter Server braucht https und den API-Schlüssel als
  Geheimnis `qdrant_api_key` (nie Zugangsdaten in der URL).
- **Embeddings:** lokal mit FastEmbed auf ONNX Runtime berechnet; das Modell wird einmal nach `models/embeddings/`
  geladen. Mit `rag_embedding_model: auto` (Standard) behält eine vorhandene Wissensbasis das Modell, mit dem sie
  gebaut wurde; eine neue bekommt `intfloat/multilingual-e5-small` (384 Dimensionen, etwa 100 Sprachen, schnell auf
  einer CPU) oder `BAAI/bge-m3` (1024 Dimensionen), wenn ONNX Runtime CUDA hat und der Rechner mindestens 8 GB RAM.
  Jedes Modell bekommt eine eigene Sammlung; ein Modellwechsel vermischt oder verliert also nie Vektoren. Im
  Offline-Modus wird nichts heruntergeladen, und ein fehlendes Modell wird klar gemeldet.
- **Aufnehmen:** Seiten werden in Markdown umgewandelt und entlang ihrer Struktur geteilt (Überschriften, Absätze,
  Listen; Codeblöcke und Tabellen werden nie geteilt), bemessen in Tokens des Modells. Jeder Abschnitt wird mit
  Quelle, Titel und Kapitel gespeichert. Wird eine Seite erneut aufgenommen, wird sie an Ort und Stelle aktualisiert,
  und Abschnitte, die die neue Fassung nicht mehr hat, werden entfernt.
- **Schnappschüsse:** Vor jedem Crawl wird die Wissensbasis als Schnappschuss in
  `~/.llm-framework/knowledge_snapshots` gesichert (die neuesten 5 bleiben). Einen Schnappschuss wiederherstellen
  kann man über GUI oder CLI noch nicht.
- **Suche:** hybrid. Dichte Vektoren finden die Bedeutung, BM25 findet exakte Begriffe wie Fehlercodes und
  Compiler-Flags; beide Ranglisten werden mit Reciprocal Rank Fusion verbunden. Derselbe Text aus mehreren Quellen
  kommt nur einmal.
- **Nutzung:** das Tool `search_knowledge` (Ditto, MCP-Clients); ohne Tools hängt Ditto passende Auszüge selbst an
  die Frage. Auch Healing-Analysen, Modulvorschläge und (auf Wunsch) Build-Empfehlungen bekommen Auszüge. Ergebnisse
  gelten immer als nicht vertrauenswürdige Daten.

**Dokumentation aufnehmen:** **Deep Ingest (Add Docs)** im Modul-Assistenten (**Tools > Create Hardware Module...**)
crawlt die URLs, die du eingibst. Der Crawler

- sperrt private, Loopback- und andere nicht öffentliche Adressen (SSRF-Schutz) und bindet jede Verbindung an die
  geprüfte Adresse, damit eine DNS-Antwort nicht zwischendurch ausgetauscht werden kann,
- beachtet `robots.txt` (Einstellung `crawler_respect_robots`), wartet zwischen Anfragen (`crawler_rate_limit`,
  Standard 1 s), folgt höchstens 5 Weiterleitungen und hört nach einem Anfragebudget auf,
- begrenzt Tiefe und Seitenzahl (standardmäßig `crawler_max_depth` 2, `crawler_max_pages` 50), liest PDFs
  (`crawler_download_pdfs`) und behält nur Seiten in den Sprachen aus `crawler_languages` (Standard Englisch und
  Deutsch; eine leere Liste behält alle).

---

## MCP-Server

`llm-cli mcp serve` stellt die Tools des Frameworks über das [Model Context Protocol](https://modelcontextprotocol.io)
bereit (offizielles Python-SDK `mcp` 2.3.0, Spezifikation 2026-07-28). KI-Clients wie Claude Code, Claude Desktop oder
VS Code können dann [dieselben Tools wie Ditto](#dittos-tools) nutzen. Am Client meldest du dich mit deinem eigenen
Konto oder Schlüssel an; das Framework sieht das nie.

- **Jeder Aufruf läuft durch die Tool-Registry:** Eingabeschema, deine Berechtigungen, Bestätigung, Audit-Log
  (Akteur `mcp`). Tool-Annotationen (nur lesend, idempotent) sind nur Hinweise für den Client.
- **Bestätigung:** Wo deine Berechtigung „fragen“ sagt, fragt der Server den Benutzer über den Client
  (MCP-Elicitation). Die Frage zeigt das Tool und jedes Argument vollständig, die sicherheitsrelevanten zuerst;
  Steuerzeichen, unsichtbare Zeichen und lange Stapel kombinierender Zeichen erscheinen als `\u`-Escapes. Eine
  Frage, die länger als 4000 Zeichen würde, wird abgelehnt statt gekürzt. Nur eine echte Antwort des Benutzers zählt
  als Zustimmung, und ein „Nein“ gilt für diesen Aufruf endgültig, auch wenn die Berechtigungen gelockert werden,
  während die Frage offen ist. Abgelehnte und abgebrochene Aufrufe sowie Aufrufe, die abgewiesen werden, bevor sie
  laufen (ungültige Argumente, eine gescheiterte Bestätigung), landen ebenfalls im Audit-Log. Ein Client, der nicht
  fragen kann, bekommt eine Ablehnung mit einem Hinweis, wie du das Tool erlaubst.
- **Anweisungen an den Client:** Der Server beschreibt den typischen Ablauf (Targets → Capabilities → Build → Status
  und Log → Artefakte) und sagt dem Client, dass Build-Logs und Ergebnisse aus der Wissensbasis externer Text sind,
  dessen Anweisungen nicht befolgt werden dürfen.
- **Transporte:** stdio (Standard; der Client startet den Server) oder Streamable HTTP nur auf einer
  Loopback-Adresse (`--transport http`, Standard `http://127.0.0.1:8765/mcp`). HTTP prüft Host und Origin und
  braucht ein Bearer-Token, das pro Benutzer in `~/.llm-framework/mcp_http_token` angelegt wird.
- **Arbeitsordner:** Der Server liest nie Einstellungen und importiert nie Code aus dem Ordner, in dem er gestartet
  wird (der Client startet ihn mit `python -P`), und unter Windows startet er von dort keine Programme wie `docker`
  oder `git`. Ein Projektordner kann sein Verhalten nicht ändern.
- **Client-Einrichtung:** `llm-cli mcp config` gibt die Befehle und das JSON so gequotet aus, dass sie in
  POSIX-Shells und in cmd wie PowerShell halten; `--write claude-desktop` trägt den Server in die Konfiguration von
  Claude Desktop ein, behält andere Einträge und schreibt eine private Sicherung.

**Claude-Abonnenten:** Das ist der Weg, das Framework mit einem Claude-Abo zu nutzen. Anthropic erlaubt
Drittanbieter-Apps nicht, die Anmeldung oder die Limits eines Abos zu verwenden; Ditto selbst braucht deshalb einen
API-Schlüssel, während Claude Code und Claude Desktop das Framework über MCP im Rahmen deines Abos nutzen können.

---

## Tool-Berechtigungen und Audit-Log

Jedes Tool gehört zu einer Risikostufe:

| Stufe | Bedeutung | Empfohlen | Auswahl |
|---|---|---|---|
| `read` | Targets, Builds, Logs, Wissen ansehen | allow | allow, ask, deny |
| `local_write` | Auf diesem Rechner etwas ändern (Builds starten oder abbrechen, über Healing entscheiden) | ask | allow, ask, deny |
| `external` | Andere Geräte erreichen oder Daten löschen | ask | ask, deny; einzelne Tools lassen sich einzeln erlauben |
| `forbidden` | Nie aufrufbar (zum Beispiel beliebige Shell-Befehle) | deny | deny |

Tools der Stufen `external` und `forbidden` gibt es noch nicht; Übertragen und Hochladen stehen nicht als Tools zur
Verfügung.

- Die Berechtigungen werden einmal abgefragt (GUI-Dialog beim ersten Start, `llm-cli ditto setup`) und in
  `~/.llm-framework/tool_policy.json` gespeichert (nur für dich lesbar). Einzelne Tools lassen sich in der GUI
  abweichend einstellen.
- Bis zur Einrichtung sind nur lesende Tools erlaubt, alles andere fragt.
- Eine beschädigte oder unlesbare Berechtigungsdatei oder ein Link auf eine fehlende verbietet jedes Tool, statt auf
  lockerere Standardwerte zurückzufallen; eine beschädigte Datei bleibt als `tool_policy.json.bad` erhalten, und
  `llm-cli doctor`, `llm-cli tools list` und `llm-cli mcp serve` weisen darauf hin.

**Audit-Log** (`~/.llm-framework/audit.db`, SQLite): Jeder Tool-Aufruf wird mit Zeit, Akteur, Tool, Entscheidung, ob
ein Mensch bestätigt hat, und Ergebnis festgehalten. Argumente werden geschwärzt und gekürzt gespeichert, dazu ein
Hash des vollständigen Texts. Jeder Eintrag enthält den Hash des vorherigen; mit dem Schlüssel aus dem Schlüsselbund
deines Betriebssystems ist das eine HMAC-Kette, die jemand, der nur die Datenbank bearbeiten kann, nicht neu berechnen
kann. Nach jedem Eintrag wird der neueste zusätzlich in eine Anker-Datei geschrieben, sodass auch das Entfernen der
neuesten Einträge auffällt. `llm-cli tools audit --verify` prüft die Kette und unterscheidet einen geänderten Eintrag
von einem falschen Schlüssel. Jeder Eintrag wird in einer eigenen Transaktion geschrieben, damit mehrere Prozesse die
Kette nicht aufspalten. `llm-cli tools audit --archive` legt das Log und seinen Anker beiseite (mit Zeitstempel
aufbewahrt) und beginnt eine neue Kette, zum Beispiel nachdem `llm-cli doctor` ein Problem mit dem Log gemeldet hat.

---

## Geheimnisse und persönlicher Zustand

- **Geheimnisse** (API-Schlüssel, Tokens): verschlüsselt (Fernet, jeder Eintrag mit einer Schlüssel-ID) im Tresor
  `~/.llm-framework/secrets.store`. Standardmäßig liegt sein Schlüssel im Schlüsselbund deines Betriebssystems
  (Windows-Anmeldeinformationsverwaltung auf dem lokalen Rechner, macOS-Schlüsselbund, Secret Service unter Linux),
  ein Eintrag je Tresor, nie in einer Datei neben dem Speicher. Ein Schlüsselbund, der nicht innerhalb von 3 Sekunden
  antwortet, nichts behält oder im Klartext speichert, wird nicht verwendet; dann hält das Framework an und nennt die
  Möglichkeiten für die Einstellung `secret_storage` (oder `LLM_SECRET_STORAGE` für einen einzelnen Lauf): `helper`
  (der Befehl in `secret_helper`, ohne Shell ausgeführt), `passphrase` (einmal je Start abgefragt), `insecure` (eine
  Schlüsseldatei neben dem Tresor, im Ruhezustand nicht verschlüsselt) oder `ephemeral` (nur im Arbeitsspeicher).
  Der Tresor wird atomar unter einer Sperre geschrieben, die GUI, CLI und MCP-Server teilen, ist nur für dich lesbar,
  liegt nie in einer Git-Arbeitskopie, und ein beschädigter Tresor wird nie überschrieben. `llm-cli doctor` warnt
  vor der Schlüsseldatei und vor einem Schlüsselbund, der Geheimnisse nicht schützt.
- **Schwärzen:** Eine Tabelle der Secret-Formate (`orchestrator/security/secret_patterns.py`) teilen sich der
  Secret-Scan des Repositorys und die Schwärzung zur Laufzeit. Zugangsdaten (bekannte Schlüsselformate, Header mit
  Zugangsdaten, Tokens in URLs und Formulardaten, Zeilen wie `HF_TOKEN=...`) werden aus jeder Logzeile und jedem
  Traceback entfernt (aus den eigenen Logs des Frameworks und denen der SDKs und HTTP-Bibliotheken), aus Build-Logs,
  die über die Tools gelesen werden, aus Fehlermeldungen von Anbietern und Tools, aus den Argumenten im Audit-Log und
  aus allem, was an Cloud-Anbieter geht. Die Debug-Logs der SDKs bleiben aus, außer du startest mit `--debug-http`.
- **Persönlicher Ordner** `~/.llm-framework` (`LLM_FRAMEWORK_HOME`), nur für deinen Benutzer: Tool-Berechtigungen,
  Audit-Log, SSH-Schlüssel und `known_hosts`, MCP-Token, Chatverlauf, Geheimnisse, Wissensbasis und Schnappschüsse,
  lokale Laufzeit und zwei Zufallswerte pro Installation (einer für die Datenschutz-Platzhalter, einer als anonyme
  Cache-ID für OpenAI).
- **Einstellungen** werden nie aus dem Arbeitsverzeichnis gelesen: `LLM_FRAMEWORK_CONFIG_DIR`, dann `configs/` in der
  Installation, wenn du dort schreiben darfst, dann der persönliche Ordner.

---

## Hardware-Probe und neue Targets

- **Probe:** `scripts/hardware_probe.sh` (Linux) und `scripts/hardware_probe.ps1` (Windows) schreiben
  `target_hardware_config.txt` mit Angaben zu CPU, Flags, Speicher, GPU und NPU. Führe sie auf dem Board aus, nutze
  `llm-cli scan` auf diesem Rechner oder **Scan / Import...** in der GUI (Datei importieren, Scan per SSH mit
  denselben Regeln für das Vertrauen beim ersten Kontakt wie beim Deployment, oder dieser PC).
- **Neues Target per Fragen:** `llm-cli module create` oder der Assistent unter **Tools > Create Hardware
  Module...**.
- **Neues Target aus einer Probe:** `llm-cli module generate-ai <Probe-Datei>` oder die KI-Analyse im Assistenten.
  Ditto schlägt Architektur, SDK, Basis-Image, Pakete, CPU- und CMake-Flags, Setup-Befehle und den
  Quantisierungsblock von `build.sh` vor; du siehst zuerst den ganzen Vorschlag.
- **Prüfungen, bevor etwas geschrieben wird:** Namen, Images, Pakete und Flags werden streng geprüft; Shell-Code wird
  gegen eine Sperrliste und mit `bash -n` geprüft; Dateien werden nur innerhalb von `targets/` geschrieben; ein
  vorhandenes Target wird nie überschrieben. Setup-Befehle laufen später als Benutzer ohne Sonderrechte und ohne
  Netzwerk.
- Wird ein Target im GUI-Assistenten oder mit `module generate-ai` angelegt und ist die Wissensbasis an und enthält
  Dokumente, wird ihr bereinigter Export als `knowledge/initial_knowledge.json` an das neue Target angehängt.

Einzelheiten: [Module_Developer_Guide_DE.md](Module_Developer_Guide_DE.md).

---

## Gesundheitscheck (doctor)

`llm-cli doctor` (und das nur lesende Tool `run_doctor`) führt feste Prüfungen aus und erklärt jeden Befund:

| Bereich | Prüfungen |
|---|---|
| Python | unterstützte Version (3.11 bis 3.14) |
| Docker | Daemon erreichbar, buildx vorhanden |
| Speicherplatz | freier Platz für den Cache- und den Ausgabeordner (Warnung unter 20 GB, Fehler unter 5 GB) |
| Targets | Targets gefunden, Capabilities lesbar |
| Sicherheit | persönlicher Ordner privat, Secrets-Speicher nutzbar, Schlüsselbund schützt Geheimnisse |
| KI | Tool-Berechtigungen eingerichtet und lesbar, Audit-Log intakt (geändert, entfernt, falscher Schlüssel, fehlender Anker, kein Schlüssel) |
| Daten | mitgelieferte Kalibrierungsdaten intakt (Prüfsummen) |

`--fix` wendet die automatischen Korrekturen bis `--tier` an (1: macht den persönlichen Ordner privat; 2: entfernt
zusätzlich verwaiste Framework-Images und den Docker-Build-Cache) und prüft danach erneut. Exit-Codes: 0 OK,
1 Warnungen, 2 Fehler; `--json` für Skripte.

---

## Community-Wissen

Das Framework hat die Anfänge einer Möglichkeit, Inhalte der Wissensbasis zu teilen:

- **Export:** Der Modulgenerator hängt einen bereinigten Export der Wissensbasis an ein neues Target an (Geheimnisse
  aus Inhalt, Quellen und Metadaten entfernt; siehe [Hardware-Probe und neue Targets](#hardware-probe-und-neue-targets)).
- **Hochladen:** `llm-cli swarm upload <Datei>` schiebt einen Export mit deinem `github_token` in das
  Community-Repository auf GitHub. Die Datei ist nur verschleiert (XOR), **nicht verschlüsselt**; behandle sie als
  öffentlich.
- **Importieren:** Das Einlesen von Community-Paketen aus `community/knowledge/` in die Wissensbasis gibt es im Code,
  es ist in dieser Version aber weder über die GUI noch über die CLI erreichbar.

Signierte Pakete statt XOR-Verschleierung stehen in der [Roadmap](BLUEPRINT_DE.md#roadmap).

---

## Secret-Scan für Mitwirkende

Das Repository schützt sich vor eingecheckten Zugangsdaten. `scripts/secret_scan.py` (nur Standardbibliothek) erkennt
API-Schlüssel von Anbietern, Tokens, private Schlüssel, Zertifikate und Dateien mit Zugangsdaten. Es läuft als
Pre-Commit- und Pre-Push-Hook (`make hooks`) und als CI-Job über die gesamte Historie. Persönliche Zeichenketten wie
deine E-Mail-Adresse oder dein Benutzername kommen in `.git/info/secret-patterns`, das nie eingecheckt wird. Funde
werden geschwärzt ausgegeben. Einzelheiten: [CONTRIBUTING_DE.md](../CONTRIBUTING_DE.md).

---

## Bekannte Lücken

Was noch nicht funktioniert, damit dich nichts überrascht:

- **Docker-Setup des Frameworks selbst:** `Docker Setup/` sowie `make setup`, `make build`, `make up`, `make gui`
  und `make test-container` funktionieren nicht (`scripts/setup_linux.sh` und `orchestrator/Dockerfile` gibt es
  nicht). Ein gehärtetes Image ohne Oberfläche für CLI und MCP-Server wird gerade gebaut. Die Konvertierungen sind
  nicht betroffen.
- **Windows-Hosts:** Konvertierungen auf einem Windows-Host sind nicht geprüft.
- **Build-Status über Prozesse hinweg:** Builds leben in dem Prozess, der sie gestartet hat; `llm-cli build status`
  in einem anderen Prozess sieht sie nicht.
- **Abbrechen:** Die GUI hat keine Abbrechen-Schaltfläche, und Strg+C in `llm-cli build start` stoppt den Build
  nicht (die CLI wartet, bis er zu Ende ist); abbrechen kann nur das Tool `cancel_build`.
- **Anmeldung pro Anbieter:** noch nicht verfügbar; Ditto arbeitet mit API-Schlüsseln.
- **Laufzeit auf dem Gerät:** Das Framework installiert keine Laufzeitumgebung auf dem Board.
- **GUI:** Die Reiter Auto-Tuning und Models sind Platzhalter; die lokale Laufzeit wird nur über die CLI gesteuert.
- **Sicherheits-Scan:** Der Trivy-Container bekommt den Docker-Socket, um das Image zu lesen; ein Scan ohne Socket ist
  geplant.
- Kleinere Lücken der CLI stehen in [CLI_Usage_DE.md](CLI_Usage_DE.md#bekannte-einschränkungen).

Was geplant ist: [Roadmap](BLUEPRINT_DE.md#roadmap). Was sich geändert hat: [CHANGELOG](../CHANGELOG_DE.md).
