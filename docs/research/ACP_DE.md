Version 3.3.0 · Stand 2026-11-02

# ACP-Recherche

[English version](ACP.md)

Recherchedatum: 2026-11-02. Alle Quellen wurden an diesem Tag abgerufen. Sie stehen am Ende und werden als [n]
zitiert. Fakten stammen aus der zitierten Quelle. Text mit **[Schluss]** ist unsere eigene Folgerung. Text mit
**[Unbestätigt]** ließ sich nicht an einer Primärquelle prüfen.

## Inhalt

1. [Welches ACP?](#1-welches-acp)
2. [Agent Client Protocol im Detail](#2-agent-client-protocol-im-detail)
3. [Was ACP-Unterstützung für llmcf bedeutet](#3-was-acp-unterstützung-für-llmcf-bedeutet)
4. [Offene Punkte](#4-offene-punkte)
5. [Quellen](#5-quellen)

---

## 1. Welches ACP?

Die Anfrage nennt „ACP (Agent Context Protocol)“. Mehrere Protokolle tragen die Buchstaben ACP. Keines der
verbreiteten heißt „Agent Context Protocol“.

| Name | Träger | Zweck | Stand am 2026-11-02 |
|---|---|---|---|
| **Agent Client Protocol** | Zed Industries, mitentwickelt von JetBrains | Verbindet Code-Editoren (Clients) mit Coding-Agenten. JSON-RPC 2.0, meist über stdio [1][2] | Aktiv. Stabile Version 1, Version 2 im Entwurf [4][21] |
| Agent Communication Protocol | IBM Research / BeeAI | Kommunikation von Agent zu Agent über REST (OpenAPI 0.2.0) [56][58] | Im August 2025 in **A2A** aufgegangen. Repository am 2025-08-27 archiviert; SDK seit 2025-08-21 bei 1.0.3 eingefroren [59][60][61][63] |
| Agent Connect Protocol | AGNTCY (von Cisco gespendet, Linux-Foundation-Projekt seit 2025-07-29 [78]) | REST-Schnittstelle zum Aufrufen und Konfigurieren entfernter Agenten [69] | Repository am 2026-04-11 archiviert [69] |
| Agentic Commerce Protocol | OpenAI und Stripe | Kauf und Bestellungen zwischen Käufern, ihren Agenten und Shops [68] | Beta. Hat nichts mit Entwicklerwerkzeugen zu tun |
| „Agent Context Protocol“ | Kleine akademische oder private Projekte | Koordination mehrerer Agenten oder eine Dokumentationskonvention [70][71][72][73] | Je unter 130 GitHub-Sternen; kein Editor und kein Client setzt sie um |

**Dieses Dokument behandelt das Agent Client Protocol.** Gründe:

- llmcf ist ein Entwicklerwerkzeug mit einem MCP-Server. Das Agent Client Protocol ist das einzige ACP, das im
  Werkzeug-Stack von Entwicklern neben MCP steht. Es übernimmt die JSON-Typen von MCP und reicht MCP-Server an
  Agenten weiter [2][5].
- Es wird wirklich genutzt: 49 Agenten sind gelistet, und die Client-Seite nennt 21 Editoren und IDEs sowie etwa 130
  weitere Clients (CLI, Desktop, Web, Notebook, Mobil, Messenger) [30][31]. Beteiligt sind Zed, JetBrains, Google
  (Gemini CLI), GitHub Copilot und Cursor [30][47].
- IBMs Agent Communication Protocol wird nicht mehr entwickelt. Das Team ist zu A2A gewechselt, und BeeAI nutzt
  jetzt A2A [60][61][62]. A2A 1.0 ist stabil (v1.0.1 am 2026-05-28) [64][65]. **[Schluss]** Seit der Fusion meint
  „ACP“ im Umfeld von Entwicklerwerkzeugen meist das Agent Client Protocol; keine Quelle sagt das direkt ([67]
  beschreibt nur, wie IBMs ACP in A2A aufging). IBM ACP selbst sah MCP als Schicht zwischen Agent und Werkzeug
  und ACP als Schicht zwischen Agenten [57]. Falls je Arbeit von Agent zu Agent gewünscht ist, ist das Ziel A2A,
  nicht IBM ACP. Das Begleitdokument [MCP vs ACP](MCP_vs_ACP_DE.md) behandelt A2A.
- **[Schluss]** „Context“ ist vermutlich aus „Model *Context* Protocol“ hineingerutscht. Der Nutzer sollte diese
  Lesart vor der Entwurfsarbeit bestätigen.

Hinweis: Ab hier meint „ACP“ in diesem Dokument das Agent Client Protocol.

---

## 2. Agent Client Protocol im Detail

### 2.1 Zweck

- ACP vereinheitlicht die Kommunikation zwischen Code-Editoren oder IDEs („Clients“) und Coding-Agenten. Vorbild
  ist das Language Server Protocol (LSP): Jeder ACP-Agent soll in jedem ACP-Client laufen [2].
- „ACP assumes that the user is primarily in their editor.“ Der **Agent** führt die LLM-Schleife und seine Werkzeuge
  aus. Der **Client** stellt Oberfläche, Freigabe-Dialoge, Dateien und Terminals bereit [2].
- ACP übernimmt, wo es geht, die JSON-Darstellungen von MCP. Für Programmierarbeit kommen eigene Typen dazu, etwa
  Diffs. Text für den Menschen ist Markdown [2].
- Zed hat ACP am 2025-08-27 angekündigt. Gemini CLI war die erste Referenzimplementierung, gebaut mit Google [47].
  Die Lizenz ist Apache-2.0 [46][47].

### 2.2 Rollen

| Rolle | Wer | Setzt um |
|---|---|---|
| Client | Der Editor (Zed, eine JetBrains-IDE, Neovim, ...) | Oberfläche, Freigabe-Dialoge, optional Dateisystem- und Terminal-Dienste |
| Agent | Der Coding-Agent, meist ein CLI-Programm | Die LLM-Schleife, Sessions, Tool-Calls und die JSON-RPC-Methoden, die der Client aufruft |

Der Client startet den Agenten meist als Unterprozess [18]. Beide Seiten senden einander Requests.

### 2.3 Transport

- Nachrichtenformat: JSON-RPC 2.0 [3].
- **stdio ist der einzige Transport, den die Spezifikation festlegt** [18]. Nachrichten werden durch einen
  Zeilenumbruch getrennt und dürfen keine eingebetteten Zeilenumbrüche enthalten. Der Agent darf UTF-8-Text auf
  stderr loggen. Auf stdout darf er nichts schreiben, was keine gültige ACP-Nachricht ist.
- Eigene Transporte sind erlaubt. Sie müssen JSON-RPC und den ACP-Ablauf beibehalten und sollen ihre Sicherheit
  dokumentieren, auch die Authentifizierung der Gegenseite [16][18].
- Entfernte Agenten: „Full support for remote agents is a work in progress“ [2]. Das RFD „Streamable HTTP &
  WebSocket Transport“ ist **seit 2026-07-02 Active** (letzte Überarbeitung 2026-10-30) [24]. Der Entwurf: ein
  Endpunkt `/acp`; GET mit `Accept: text/event-stream` öffnet einen SSE-Stream; GET mit `Upgrade: websocket`
  öffnet einen WebSocket; POST sendet Nachrichten; DELETE beendet die Verbindung. Streamable HTTP muss HTTP/2
  nutzen. Header `Acp-Connection-Id` und `Acp-Session-Id`. Clients, die entferntes ACP unterstützen, müssen
  Streamable HTTP und WebSocket beide können [24].
- Am 2026-04-22 wurde eine Transports Working Group gegründet [28].
- **[Schluss]** Der entfernte Transport gehört noch nicht zur stabilen Spezifikation. Wer heute darauf baut, baut auf
  ein bewegliches Ziel.

### 2.4 Ablauf

1. **Initialisieren.** Der Client sendet `initialize` mit der neuesten `protocolVersion`, die er kann, und seinen
   Fähigkeiten. `protocolVersion` ist eine ganze Zahl, die Hauptversion. Die aktuelle stabile Version ist **1**. Der
   Agent antwortet mit derselben Version, wenn er sie kann, sonst mit seiner eigenen neuesten. Kann der Client diese
   Version nicht, schließt er die Verbindung [4].
2. **Anmelden** (nur wenn der Agent Anmeldeverfahren nennt) [15].
3. **Session anlegen oder wiederherstellen.** `session/new` nimmt `cwd` (absoluter Pfad), `mcpServers` und optional
   `additionalDirectories`. Zurück kommt eine `sessionId` [5]. `session/load` spielt den Verlauf erneut ab;
   `session/resume` stellt den Kontext ohne Wiedergabe her [5].
4. **Prompt-Turns.** Der Client sendet `session/prompt`. Der Agent streamt `session/update`-Notifications (Text,
   Gedanken, Tool-Calls, Pläne). Er darf um Freigabe bitten oder Client-Dienste aufrufen. Der Turn endet, wenn der
   Agent den Prompt-Request mit einem Stop-Grund beantwortet: `end_turn`, `max_tokens`, `max_turn_requests`,
   `refusal` oder `cancelled` [7].
5. **Abbrechen** jederzeit mit der Notification `session/cancel` [7].
6. **Schließen** mit `session/close` (idempotent) [5].

Ein Request vor `initialize` bekommt den Fehler -32600 [19].

### 2.5 Methoden (Version 1)

Client an Agent (der Agent setzt sie um) [3][5][6]:

| Methode | Art | Pflicht? |
|---|---|---|
| `initialize` | Request | ja |
| `authenticate` | Request | ja, wenn der Agent Anmeldeverfahren nennt |
| `session/new` | Request | ja |
| `session/prompt` | Request | ja |
| `session/cancel` | Notification | ja |
| `session/load` | Request | optional (`agentCapabilities.loadSession`) |
| `session/resume` | Request | optional (stabil seit 2026-04-22) |
| `session/close` | Request | optional (stabil seit 2026-04-23) |
| `session/list` | Request | optional (stabil seit 2026-03-09) |
| `session/delete` | Request | optional (stabil seit 2026-06-05) |
| `session/set_mode` | Request | optional, läuft zugunsten der Config-Optionen aus |
| `session/set_config_option` | Request | optional |
| `logout` | Request | optional (stabil seit 2026-05-21) |

Agent an Client (der Client setzt sie um) [3][12][13][14]:

| Methode | Art | Braucht Client-Fähigkeit |
|---|---|---|
| `session/update` | Notification | keine (Grundumfang) |
| `session/request_permission` | Request | keine (Grundumfang) |
| `fs/read_text_file` | Request | `fs.readTextFile` |
| `fs/write_text_file` | Request | `fs.writeTextFile` |
| `terminal/create`, `terminal/output`, `terminal/wait_for_exit`, `terminal/kill`, `terminal/release` | Request | `terminal` |
| `elicitation/create` | Request | `elicitation.form` oder `elicitation.url` |
| `elicitation/complete` | Notification | URL-Modus |

Arten von `session/update` [7]: `user_message_chunk`, `agent_message_chunk`, `agent_thought_chunk`, `tool_call`,
`tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `config_option_update`,
`session_info_update`, `usage_update`, `notice`, `compaction_update`, `compaction_summary_chunk` (die letzten drei
nur, wenn der Client sie anmeldet).

Fehlercodes [19]: die Standardcodes von JSON-RPC, dazu -32800 Request abgebrochen, -32000 Anmeldung nötig, -32002
Ressource nicht gefunden.

Eigene Methoden beginnen mit `_`. Eigene Daten gehören in `_meta`. Die Schlüssel `traceparent`, `tracestate` und
`baggage` sind für W3C Trace Context reserviert [17].

### 2.6 Fähigkeiten (Capabilities)

Werden einmal in `initialize` ausgehandelt. Was nicht angemeldet ist, gilt als nicht unterstützt [4].

- **Client:** `fs.readTextFile`, `fs.writeTextFile`, `terminal`, `auth.terminal`, `elicitation` (`form` und/oder
  `url`; ein leeres Objekt meldet nichts an), `session.configOptions.boolean`, `session.compaction`,
  `session.notices` [4].
- **Agent:** `loadSession`; `promptCapabilities` (`image`, `audio`, `embeddedContext`; Text und Resource-Links gehen
  immer); `mcpCapabilities` (`http`, `sse`); `auth.logout`; `sessionCapabilities` mit `delete` und
  `additionalDirectories` [4] sowie `resume` und `close` [5].
- Beide Seiten sollen `clientInfo` bzw. `agentInfo` senden (`name`, `title`, `version`). Das sind Selbstauskünfte und
  sollen keine Sicherheitsentscheidungen steuern [4][16].

### 2.7 Inhalte und Meldung von Tool-Calls

- ACPs `ContentBlock` „is compatible with, and a superset of“ den `ContentBlock` von MCP 2025-06-18. Ausgaben von
  MCP-Tools lassen sich unverändert durchreichen [8].
- Blocktypen: `text` (Markdown, Pflicht), `image` und `audio` (base64, brauchen Fähigkeiten), `resource`
  (eingebettet, braucht `embeddedContext`), `resource_link` (Pflicht) [8].
- Ein **Tool-Call** wird mit `tool_call` gemeldet (Pflicht: `toolCallId`, `title`). Spätere Änderungen kommen als
  `tool_call_update`, nur mit den geänderten Feldern [9].
  - `kind`: `read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `switch_mode`, `other`.
  - `status`: `pending`, `in_progress`, `completed`, `failed`.
  - `content`: ein Content-Block, ein **Diff** (`path`, `oldText`, `newText`) oder ein eingebettetes **Terminal**.
  - `locations`: `{path, line?}` mit Zeilen ab 1. Nur zur Anzeige; sie gewähren keinen Zugriff.
  - `name` (stabil seit 2026-09-17) ist nur informativ und gewährt nichts [9][28].
- **Plan:** eine Liste von Einträgen mit Priorität und Status. Jede Aktualisierung ersetzt den ganzen Plan [10].
- **Slash-Befehle:** Der Agent kündigt sie mit `available_commands_update` an. Ein Befehl wird als normaler
  Prompt-Text gesendet, `/name args` [11].
- **Config-Optionen** ersetzen Session-Modi. Kategorien: `mode`, `model`, `model_config`, `thought_level`, eigene.
  Typen: `select`, und `boolean`, wenn der Client es kann [6].

### 2.8 Freigaben (Permissions)

- `session/request_permission` sendet `sessionId`, den `toolCall` und eine Liste von Optionen. Jede Option hat eine
  Art: `allow_once`, `allow_always`, `reject_once`, `reject_always`. Die Antwort ist `selected` mit einer `optionId`
  oder `cancelled` [9].
- **Der Agent speichert gemerkte Entscheidungen** („immer erlauben“). Die Art der Option ist nur ein Hinweis für die
  Oberfläche [9].
- Bei `session/cancel` beantwortet der Client offene Freigabe-Anfragen mit `cancelled` [7].
- Freigabe-Anfragen sind nur beratend. Der Client muss zusätzlich selbst prüfen: „A permission request from the
  Agent does not replace the Client's own checks“ [12][16].

### 2.9 Dateisystem, Terminals und Elicitation

- `fs/read_text_file` liest eine Datei, **auch ungespeicherte Änderungen im Editor**. `fs/write_text_file` schreibt
  eine. Pfade sind absolut. Der Client prüft jeden Pfad gegen die Wurzeln der Session (`cwd` plus
  `additionalDirectories`) [5][12].
- `terminal/create` startet einen Befehl im Client und gibt sofort eine `terminalId` zurück. Ausgabe, Exit-Status,
  Beenden und Freigeben sind eigene Aufrufe [13].
- **Elicitation** (stabil seit 2026-07-22): Modus `form` (ein flaches Schema) oder `url` (für heikle Abläufe wie
  OAuth). Der Form-Modus darf nicht für Geheimnisse genutzt werden. Fehlt der URL-Modus, darf ein Agent nicht auf
  den Form-Modus ausweichen. Grundlage ist die Elicitation-Spezifikation aus dem Release Candidate von MCP
  2026-07-28 [14].

### 2.10 Zusammenspiel von ACP und MCP

- Der Client übergibt **`mcpServers`** in `session/new` (und `load` / `resume`). **Der Agent verbindet sich dann als
  MCP-Client mit diesen MCP-Servern** [5].
- Arten von Einträgen: **stdio, das jeder Agent können muss** (`name`, `command` als absoluter Pfad, `args`, `env`);
  `http` (braucht `mcpCapabilities.http`); `sse` (braucht `mcpCapabilities.sse`; MCP hat SSE abgekündigt) [4][5].
- **MCP-over-ACP** (RFD-Entwurf): Der Client meldet einen MCP-Server mit `"type": "acp"` an. MCP-Nachrichten laufen
  dann über die bestehende ACP-Verbindung, über eine einzige Methode `mcp/message`. Kein zusätzlicher Prozess und
  kein Listener nötig. Ziel ist das zustandslose MCP 2026-07-28 [25].
- **Proxy-Ketten** (RFD, im Rust-SDK umgesetzt): `proxy/initialize` und `proxy/successor`. Ein Proxy kann einem
  Agenten über MCP-over-ACP einen MCP-Server unterschieben [26].

### 2.11 Anmeldung und Sicherheit

- Der Agent nennt `authMethods`. Typ `agent` (Standard): Der Agent meldet sich selbst an, nachdem der Client
  `authenticate` aufgerufen hat. Typ `terminal`: Der Client startet das Agentenprogramm interaktiv zur Anmeldung
  (braucht `auth.terminal`) [15]. Ein gesperrter Request liefert -32000 [19].
- Sicherheitsmodell [16]:
  - „The Agent runs with whatever permissions its process is given.“ ACP ist **keine Sandbox**.
  - Jeder String, Pfad, jede URI und jedes Tool-Ergebnis vom Agenten, MCP-Ergebnisse eingeschlossen, ist
    nicht vertrauenswürdig.
  - Clients sollen Terminal-Steuersequenzen entschärfen, Bidi- und Nullbreiten-Zeichen sichtbar machen und nie ohne
    Zutun des Nutzers Links öffnen oder Skripte ausführen.
  - Entfernte URIs vom Agenten: nur `https` oder `data`, keine Zugangsdaten, keine Loopback- oder privaten Ziele.
  - Agenten sollen fragen, bevor sie Dateien schreiben, die Ausführung oder Freigaben steuern (Hooks, Einstellungen,
    MCP-Konfiguration), und bevor sie nach dem Lesen nicht vertrauenswürdiger Inhalte Daten vom Rechner senden.
  - Keine Zugangsdaten in `_meta`. Session- und Tool-Call-IDs sind keine Zugangsdaten.
  - Agenten für mehrere Nutzer beschränken Sessions, Freigaben und Elicitations auf den Besitzer der Session.
- Sicherheitsmeldungen gehen an die Sicherheitsadresse von Zed, die auf der Governance-Seite steht [29].

### 2.12 SDKs und Versionen

| Sprache | Paket | Version | Quelle |
|---|---|---|---|
| Python | `agent-client-protocol` (Import `acp`) | stabil 0.12.1 (2026-08-16); Vorabversion 1.0.0rc3 (2026-10-12); Python 3.10 bis 3.14 | [42] |
| TypeScript | `@agentclientprotocol/sdk` | 2.2.0 (2026-10-30); 1.0.0 veröffentlicht am 2026-06-24, angekündigt am 2026-06-25 | [43][22] |
| Rust | `agent-client-protocol` | 3.6.0 (2026-10-28); 1.0.0 veröffentlicht am 2026-06-24, angekündigt am 2026-06-25 | [45][22] |
| Rust-Schema | `agent-client-protocol-schema` | 1.15.0 (2026-11-01) | [46] |
| Kotlin | `com.agentclientprotocol:acp` | 0.34.0 auf Maven Central (Metadaten aktualisiert 2026-10-21); die Doku-Seite zeigt noch `0.1.0-SNAPSHOT`; nur JVM | [35][79] |
| Java | `com.agentclientprotocol:acp-core` (dazu `acp-json-jackson2` / `acp-json-jackson3`) | 0.80.0 (2026-10-08) | [34][80][81] |

- Neuestes Schema-Release: v1.29.0 (2026-11-01), daneben v2.0.0-alpha.12 [39].
- Das Python-SDK wird aus dem Upstream-Schema erzeugt. Es bietet eine Basisklasse `Agent`, `run_agent()`, einen
  Client-Helfer, der Agentenprozesse startet, und Helfer zum Sammeln von Sessions, Verfolgen von Tool-Calls und
  Vermitteln von Freigaben [37][40]. Die Doku hat eine Seite zum Testen [36] (nicht im Detail gelesen).
- **ACP v2** ist **seit 2026-07-20 ein Entwurf**. Er streicht `session/load`, `session/set_mode`, `fs/*` und alle
  `terminal/*`-Methoden (ein eigenes RFD begründet die Streichung: die Schnittstelle wurde kaum genutzt), benennt
  `authenticate` in `auth/login` um und ersetzt `tool_call` durch das erste `tool_call_update`. Die Ankündigung sagt,
  Teile „can, and will, change“ und „Adding v2 support should not mean dropping v1“ [20][21][27].

### 2.13 Nutzer

- **Editoren und Clients** [31]: Zed, JetBrains-IDEs, Neovim (mehrere Plugins), Emacs, VS Code (über Erweiterungen),
  Visual Studio, Sublime Text, Qt Creator, Pulsar, Obsidian, Unity, ein Chrome-Client und Notebook-Werkzeuge wie
  marimo, Jupyter und DuckDB.
- **Agenten** [30]: 49 gelistet, darunter Gemini CLI, Goose, GitHub Copilot (Public Preview), Cursor, Junie
  (JetBrains), Cline, OpenCode, OpenHands, Kiro CLI, Qwen Code, Kimi CLI, Mistral Vibe, Augment Code, Docker cagent.
  Claude Agent [41][44], Codex CLI und Pi kommen über Adapter.
- **ACP Registry** [32][33]: eine kuratierte Liste, aus der JetBrains-IDEs (ab 2025.3.2, mit JetBrains AI
  253.30387.147) installieren können [52]. Sie listet nur Agenten, die Anmeldung unterstützen. Sie ging am
  2026-01-28 live (Zed-Blog [49]; JetBrains-Blog Januar 2026 [52]; heise 2026-01-30 [53]). Die ACP-Updates-Seite
  datiert „ACP Registry is Released“ (RFD auf Completed) auf 2026-03-09 [28]. Die Registry zeigte 43 Agenten [33];
  ihr JSON nennt die Verteilungsarten `npx` (23 Agenten), `binary` (20) und `uvx` (2) [82].
- Das Haupt-Repository hatte etwa 4,6k Sterne [38].
- Zed 1.0 erschien am 2026-04-29; die Ankündigung nennt ACP als den Weg, auf dem Zed Claude Agent, Codex, OpenCode
  und Cursor betreibt [55].
- Microsoft plante im Oktober 2025 nicht, ACP „right away“ nativ in VS Code einzubauen [51]. VS Code unterstützt ACP
  heute über Erweiterungen [31].

### 2.14 Governance, Reife und offene Fragen

- **Governance:** Zed und JetBrains führen ACP gemeinsam in einem Übergangsmodell. Erklärtes Ziel ist später eine
  unabhängige Stiftung. Die Lead Maintainer kommen von Zed und JetBrains [29][28]. Änderungen laufen über RFDs mit
  den Stufen Draft, Active, Preview, Completed; nur Completed trägt ein Stabilitätsversprechen [23]. ACP ist kein
  Projekt der Agentic AI Foundation; MCP und A2A sind es [66][77].
- **Reife:** v1 ist stabil, und viele Funktionen wurden 2026 stabilisiert (Session-Liste, Resume, Close, Delete,
  Elicitation, Tool-Call-Namen) [28]. Die SDKs für Rust und TypeScript erreichten Version 1.0 (angekündigt am 2026-06-25) [22]. Das
  Python-SDK liegt noch unter 1.0, mit Migrationsanleitungen für 0.7, 0.8 und 0.11 [37][42].
- **Offene Fragen:**
  - Kein stabiler entfernter Transport [2][24].
  - v2 ist ein Entwurf und streicht die Dateisystem- und Terminal-Methoden von v1 [20].
  - MCP-over-ACP und Proxy-Ketten sind Entwürfe [25][26].
  - Session-Modi werden durch Config-Optionen ersetzt [6].
  - Tempo des Protokolls: Ende Oktober 2026 mehrere Schema-Releases pro Woche [39].

---

## 3. Was ACP-Unterstützung für llmcf bedeutet

### 3.1 Ausgangslage im Repository

- `orchestrator/mcp_server.py` bietet die Tool-Registry des Frameworks über MCP an (Spezifikation 2026-07-28
  [74][75], `mcp` 2.3.0 [76]). stdio ist der Standard. Streamable HTTP bindet nur an Loopback und braucht pro Client
  ein benanntes Token.
- Jeder Aufruf geht durch `ToolRegistry.call()`: Eingabeschema, die gespeicherten Freigaben der Person,
  Bestätigung, Audit-Log, Ausgabefilter. Eine „Fragen“-Freigabe wird zur MCP-Elicitation oder zu einer offenen
  Freigabe im Framework.
- Ditto ist der eingebaute Assistent (`orchestrator/Core/ditto_manager.py`, `DittoManager.chat`). Er ist optional und
  als experimentell markiert. Seine Hände sind dieselben Registry-Tools.
- Ditto hat heute keinen MCP-Client: Kein Modul in `orchestrator/` nutzt das Paket `mcp` außer Server, CLI und GUI.
- Entwurfsregeln, die hier zählen (docs/DESIGN.md):
  - Eine KI sieht nie Host-Pfade, Kontonamen auf Geräten oder Geheimnisse; Ordner bekommen Labels, Geräte
    sprechende Namen.
  - Dauerfreigaben („nicht mehr fragen“) entstehen nur im eigenen Fenster des Frameworks.
  - Das Framework startet nie selbst ein Client-Programm.
  - Es gibt kein „frag Ditto“-Tool; ein MCP-Client übernimmt Dittos Aufgabe selbst.

**Kernpunkt:** ACP ist kein zweiter Dialekt von MCP. Bei ACP ist der Editor der Client, und der Agent antwortet
ihm. Ein reiner Tool-Server nach MCP kann nicht auf demselben Stream „auch ACP sprechen“: Ein ACP-Agent darf auf
stdout nur ACP-Nachrichten schreiben [18], und von ihm wird ein Prompt-Turn mit LLM erwartet [2]. **[Schluss]**
„llmcf unterstützt ACP“ heißt daher eine der folgenden Optionen.

### 3.2 Geht schon ohne Code: llmcf als MCP-Server für ACP-Agenten

Jeder ACP-Agent muss stdio-MCP-Server vom Editor annehmen [5]. Wer Claude Agent, Gemini CLI oder einen anderen
ACP-Agenten in Zed oder einer JetBrains-IDE nutzt, kann llmcfs MCP-Server (`llm-cli mcp serve`) schon heute als
stdio-Eintrag hinzufügen. Der Agent nutzt dann llmcfs Tools mit allen bestehenden Prüfungen. **[Schluss]** Dafür
reicht Dokumentation: ein Konfigurationsbeispiel pro Editor und der Hinweis, dass der Befehl ein absoluter Pfad
sein muss.

### 3.3 Option A: llmcf als ACP-Agent (Ditto im Editor)

Worum es geht: ein neuer Einstiegspunkt, zum Beispiel `llm-cli acp`, der ACP über stdio spricht. Der Editor startet
ihn. Die Person chattet in Zed, JetBrains, Neovim oder Emacs mit Ditto. Ditto arbeitet mit den eigenen Tools des
Frameworks.

Zuordnung (**[Schluss]**, aus Spezifikation und Code):

| ACP | llmcf |
|---|---|
| `session/prompt` | `DittoManager.chat(question, history)` |
| `agent_message_chunk` / `agent_thought_chunk` | gestreamter Text aus der LLM-Schicht |
| `tool_call` / `tool_call_update` | ein Registry-Aufruf mit Audit und Job-ID; Build-Fortschritt als `in_progress`, Log-Auszüge als Inhalt |
| `session/request_permission` | die „Fragen“-Freigabe der Tool-Policy |
| `elicitation/create` (form) | die heutigen Bestätigungsfragen; nie für Geheimnisse |
| `plan` | die Build-Stufen (source, config, convert, target) |
| `available_commands_update` | zum Beispiel `/build`, `/status`, `/deploy`, `/mojo` |
| `session/set_config_option` | Dittos Wahl von Anbieter und Modell |
| `session/cancel` | das bestehende Abbruch-Token des Turns und seiner Jobs |
| `authMethods` Typ `terminal` | die bestehende Anmeldung (`llm-cli ditto login`) |
| `session/new.mcpServers` | braucht einen neuen MCP-Client in Ditto (siehe Risiken) |

Aufwand **[Schluss]**: mittel. Ein neues Adaptermodul in der Adapter-Schicht, neben `cli.py` und `mcp_server.py`.
Das Python-SDK bringt JSON-RPC und typisierte Modelle mit. Die meiste Arbeit ist die Abbildung von Dittos
Turn-Ereignissen auf `session/update`, dazu Tests. Ein schlanker MCP-Client für stdio-Server kommt extra.

Risiken:
- **Pflicht zu stdio-MCP.** Laut Spezifikation muss jeder Agent stdio-`mcpServers` können [5]. Ditto hat heute keinen
  MCP-Client. **[Schluss]** Vom Editor gelieferte Server bringen fremde Tools mit, die nicht durch
  `ToolRegistry.call()` laufen. Sie sollten gelistet, aber erst angeboten werden, wenn die Person sie in llmcf
  einschaltet, und ihre Ergebnisse gelten als nicht vertrauenswürdig. Das passt zur geplanten Bridge-Regel für Boards
  in DESIGN.md.
- **Doppelter Nutzen.** Laut Entwurf soll ein MCP-Client Dittos Aufgabe übernehmen. In einem Editor, der schon
  Claude Agent oder Gemini CLI nutzt, kann die Person llmcf über MCP verwenden (3.2). **[Schluss]** Option A nützt
  vor allem Leuten, die Dittos lokales Modell wollen oder keinen anderen Agenten haben.
- **Bewegliches Protokoll.** v2 ist ein Entwurf, und das Python-SDK liegt unter 1.0 [21][42].
- **Experimenteller Hinweis.** Dittos einmaliger Hinweis „Nutzung auf eigene Gefahr“ muss auch Editor-Nutzer
  erreichen.

Sicherheit (**[Schluss]**, auf Basis von [16] und DESIGN.md):
- **Freigaben:** Der ACP-Adapter nutzt denselben Weg `ToolRegistry.call()`. In Freigabe-Anfragen nur `allow_once` und
  `reject_once` anbieten. Dauerfreigaben bleiben wie heute im eigenen Fenster des Frameworks. Entscheidungen, die
  allein der Person gehören (Löschen, Lizenzen, erstes Vertrauen in einen Host-Key, die eigenen Rechte der KI),
  behalten ihre heutigen Regeln. Eine Frage, die im Editor nicht gestellt werden kann, wird wie bei MCP zu einer
  offenen Freigabe.
- **Pfade bereinigen:** `cwd` und `additionalDirectories` vom Editor sind absolute Host-Pfade. Sie dürfen das Modell
  nicht unverändert erreichen. Die bestehenden Labels nutzen (`security/paths.scrub_for_ai`, Ordner-Labels).
  `locations` in Tool-Calls dienen nur der Anzeige, aber derselbe Filter gilt für alles, was auch in den Kontext des
  Modells geht.
- **Keine Gerätekonten an die KI:** Die bestehende Regel (Kontonamen und Pfade auf Geräten erreichen nie die KI) gilt
  unverändert. Boards tragen sprechende Namen.
- **Dateisystem und Terminal des Editors:** `fs/*` und `terminal/*` nicht für Builds oder Deploys nutzen. Sie würden
  die Registry umgehen. Wenn überhaupt, dann nur zum Lesen von Dateien innerhalb der Wurzeln, nachdem die Person es
  erlaubt hat. v2 streicht diese Methoden ohnehin [20].
- **Geheimnisse:** nie per Form-Elicitation [14]; Anmeldung über die Methode `terminal` oder das Framework.
- **Labels bleiben Labels:** `clientInfo` ist ein Label für Menschen, wie heute beim MCP-ClientTracker [4][16].
- **Disziplin auf stdout:** Der ACP-Prozess loggt nur auf stderr [18].

### 3.4 Option B: llmcf als ACP-Client (fremde Agenten in der llmcf-GUI)

Worum es geht: Das Chat-Panel der llmcf-GUI beherbergt fremde ACP-Agenten (Claude Agent, Gemini CLI, Codex über
Adapter) neben Ditto oder statt Ditto. llmcf würde ihre Ausgabe zeigen und `request_permission`, `fs/*` und
`terminal/*` beantworten. Den eigenen MCP-Server würde es in `mcpServers` übergeben.

Aufwand **[Schluss]**: hoch. llmcf muss die Client-Seite umsetzen, einen Dateisystem- und Terminal-Dienst unter
seinen eigenen Sicherheitsregeln und das Finden von Agenten (Registry oder Konfiguration).

Risiken:
- Es widerspricht der Entwurfsregel, dass das Framework nie selbst ein Client-Programm startet. Vorher wäre eine
  Entscheidung des Maintainers nötig.
- llmcf würde fremde Agenten-Programme mit den Rechten der Person ausführen. ACP ist keine Sandbox [16].
- Anmeldung und Abos der Agenten liefen über llmcfs Fenster.

### 3.5 Option C: eine Brücke von ACP zu MCP

Zwei Lesarten:
1. **Ein ACP-Agent nur mit Tools**, der Prompts ohne LLM auf MCP-Tool-Calls abbildet. **[Schluss]** Nicht sinnvoll:
   ACP erwartet einen Agenten mit Prompt-Turn [2], und Editoren reichen MCP-Server ohnehin an echte Agenten weiter
   (3.2).
2. **MCP-over-ACP** [25]: Der Editor oder ein Proxy trägt llmcfs MCP-Verkehr innerhalb der ACP-Verbindung
   (`mcp/message`). Ziel ist MCP 2026-07-28, die Version, die llmcfs Server umsetzt. **[Schluss]** Das könnte später
   den zweiten Prozess überflüssig machen. Heute ist es ein RFD-Entwurf, und in diesem Modell sitzt die Brücke im
   Editor oder Proxy, nicht in llmcf. llmcf müsste nur MCP-Nachrichten über einen Kanal außer stdio annehmen.

Aufwand **[Schluss]**: jetzt keiner; das RFD beobachten. Risiko: Bauen auf einem Entwurf.

### 3.6 Empfehlung

**[Schluss]**

1. **Jetzt:** 3.2 dokumentieren. llmcf funktioniert über seinen MCP-Server schon mit jedem ACP-Agenten. Getestete
   Konfigurationsbeispiele für Zed und eine JetBrains-IDE ergänzen. Kein Protokollcode.
2. **Danach, wenn der Maintainer Ditto in Editoren will:** Option A, nur über stdio, auf ACP v1. Das Python-SDK
   pinnen (`agent-client-protocol` 0.12.1, oder 1.0.0, sobald final). Das Paket nur im neuen Adapter halten, wie
   heute `mcp` und uvicorn. Jede Aktion durch `ToolRegistry.call()` leiten. Keine „immer“-Optionen anbieten. Pfade
   bereinigen. `mcpServers` annehmen, aber nur mit Servern verbinden, die die Person eingeschaltet hat.
3. **Später oder nie:** Option B erst nach einer Entscheidung des Maintainers zum Starten von Client-Programmen.
   Option C erst, wenn MCP-over-ACP kein Entwurf mehr ist.
4. **Nicht IBM ACP.** Für Arbeit von Agent zu Agent stattdessen A2A ansehen (siehe [MCP vs ACP](MCP_vs_ACP_DE.md)).
5. Vor jedem Entwurf: mit dem Nutzer klären, dass „ACP“ das Agent Client Protocol meint.

---

## 4. Offene Punkte

- Ob der Nutzer das Agent Client Protocol meinte (sehr wahrscheinlich) oder Kommunikation von Agent zu Agent (dann
  A2A).
- Ob sich die Inhaltstypen von `mcp` 2.3.0 direkt in Typen des ACP-SDK umwandeln lassen. Die Spezifikation sagt, ACP-
  Inhalte seien eine Obermenge von MCP 2025-06-18 [8]; nicht getestet.
- Die Versionen der SDKs für Kotlin und Java stammen von Maven Central und aus der README des Java-SDK; ihre
  Release Notes wurden nicht gelesen.
- JetBrains' eigene Ankündigung vom Oktober 2025 wurde nicht gelesen; der Zed-Blog (2025-10-06) [48] und eine
  Sekundärquelle [50] bestätigen, dass JetBrains ACP mitentwickelt.

---

## 5. Quellen

Alle abgerufen am 2026-11-02.

1. https://agentclientprotocol.com/
2. https://agentclientprotocol.com/overview/introduction
3. https://agentclientprotocol.com/protocol/overview
4. https://agentclientprotocol.com/protocol/v1/initialization.md
5. https://agentclientprotocol.com/protocol/v1/session-setup.md
6. https://agentclientprotocol.com/protocol/v1/session-config-options.md
7. https://agentclientprotocol.com/protocol/v1/prompt-turn.md
8. https://agentclientprotocol.com/protocol/v1/content.md
9. https://agentclientprotocol.com/protocol/v1/tool-calls.md
10. https://agentclientprotocol.com/protocol/v1/agent-plan.md
11. https://agentclientprotocol.com/protocol/v1/slash-commands.md
12. https://agentclientprotocol.com/protocol/v1/file-system.md
13. https://agentclientprotocol.com/protocol/v1/terminals.md
14. https://agentclientprotocol.com/protocol/v1/elicitation.md
15. https://agentclientprotocol.com/protocol/v1/authentication.md
16. https://agentclientprotocol.com/protocol/v1/security.md
17. https://agentclientprotocol.com/protocol/v1/extensibility.md
18. https://agentclientprotocol.com/protocol/v1/transports.md
19. https://agentclientprotocol.com/protocol/v1/error.md
20. https://agentclientprotocol.com/protocol/v2/migration.md
21. https://agentclientprotocol.com/announcements/acp-v2-draft.md
22. https://agentclientprotocol.com/announcements/sdk-1-0-releases.md
23. https://agentclientprotocol.com/rfds/about.md
24. https://agentclientprotocol.com/rfds/streamable-http-websocket-transport.md
25. https://agentclientprotocol.com/rfds/mcp-over-acp.md
26. https://agentclientprotocol.com/rfds/proxy-chains.md
27. https://agentclientprotocol.com/rfds/v2/client-filesystem-terminal-capabilities.md
28. https://agentclientprotocol.com/updates.md
29. https://agentclientprotocol.com/community/governance.md
30. https://agentclientprotocol.com/get-started/agents.md
31. https://agentclientprotocol.com/get-started/clients.md
32. https://agentclientprotocol.com/get-started/registry.md
33. https://agentclientprotocol.com/registry
34. https://agentclientprotocol.com/llms.txt
35. https://agentclientprotocol.com/libraries/kotlin.md
36. https://agentclientprotocol.com/libraries/testing.md
37. https://agentclientprotocol.github.io/python-sdk/quickstart/
38. https://github.com/agentclientprotocol/agent-client-protocol
39. https://github.com/agentclientprotocol/agent-client-protocol/releases
40. https://github.com/agentclientprotocol/python-sdk
41. https://github.com/agentclientprotocol/claude-agent-acp
42. https://pypi.org/pypi/agent-client-protocol/json
43. https://registry.npmjs.org/@agentclientprotocol/sdk/latest
44. https://registry.npmjs.org/@agentclientprotocol/claude-agent-acp/latest
45. https://crates.io/api/v1/crates/agent-client-protocol
46. https://crates.io/api/v1/crates/agent-client-protocol-schema
47. https://zed.dev/blog/bring-your-own-agent-to-zed
48. https://zed.dev/blog/jetbrains-on-acp
49. https://zed.dev/blog/acp-registry
50. https://tessl.io/blog/jetbrains-joins-agent-client-protocol-to-open-ai-coding-agents-to-any-ide/
51. https://www.theregister.com/2025/10/07/jetbrains_acp_vs_code/
52. https://blog.jetbrains.com/ai/2026/01/acp-agent-registry/
53. https://www.heise.de/en/news/Integrate-AI-Agents-into-the-Editor-JetBrains-and-Zed-Release-ACP-Registry-11160709.html
54. https://www.morphllm.com/agent-client-protocol
55. https://zed.dev/blog/zed-1-0
56. https://agentcommunicationprotocol.dev/introduction/welcome.md
57. https://agentcommunicationprotocol.dev/about/mcp-and-a2a.md
58. https://raw.githubusercontent.com/i-am-bee/acp/main/docs/spec/openapi.yaml
59. https://github.com/i-am-bee/acp
60. https://github.com/orgs/i-am-bee/discussions/5
61. https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/
62. https://framework.beeai.dev/integrations/a2a
63. https://pypi.org/pypi/acp-sdk/json
64. https://a2a-protocol.org/latest/specification/
65. https://github.com/a2aproject/A2A/releases
66. https://aaif.io/blog/a2a-joins-aaif
67. https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026
68. https://github.com/agentic-commerce-protocol/agentic-commerce-protocol
69. https://github.com/agntcy/acp-spec
70. https://github.com/agent-context-protocol/agent-context-protocol
71. https://huggingface.co/papers/2505.14569
72. https://github.com/prmichaelsen/agent-context-protocol
73. https://github.com/agx-dev/acp
74. https://modelcontextprotocol.io/specification/latest
75. https://modelcontextprotocol.io/specification/2026-07-28/changelog
76. https://pypi.org/pypi/mcp/json
77. https://aaif.io/projects/
78. https://www.linuxfoundation.org/press/linux-foundation-welcomes-the-agntcy-project-to-standardize-open-multi-agent-system-infrastructure-and-break-down-ai-agent-silos
79. https://repo1.maven.org/maven2/com/agentclientprotocol/acp/maven-metadata.xml
80. https://repo1.maven.org/maven2/com/agentclientprotocol/acp-core/maven-metadata.xml
81. https://github.com/agentclientprotocol/java-sdk
82. https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json
