Version 3.3.0 · Stand 2026-11-02

# MCP vs ACP

[English version](MCP_vs_ACP.md) · Details zu ACP: [ACP-Recherche](ACP_DE.md)

Recherchedatum: 2026-11-02. Alle Quellen wurden an diesem Tag abgerufen. Sie stehen am Ende und werden als [n]
zitiert. Text mit **[Schluss]** ist unsere eigene Folgerung. Text mit **[Unbestätigt]** ließ sich nicht an einer
Primärquelle prüfen.

Verglichen werden drei Protokolle:

- **MCP**, das Model Context Protocol, Revision 2026-07-28 (aktuell) [1][2].
- **ACP**, das Agent Client Protocol von Zed und JetBrains, Version 1, Version 2 im Entwurf [12][27].
- **A2A**, das Agent2Agent-Protokoll, Version 1.0.x [32][33]. IBMs *Agent Communication Protocol*, ebenfalls ACP
  genannt, ist im August 2025 **in A2A aufgegangen** und archiviert [38][39][40]. Es wird nicht eigens verglichen.
  Vor der Fusion sah IBM ACP selbst MCP als Verbindung Agent–Werkzeug und ACP als Verbindung Agent–Agent [41].
  Nur IBMs ACP ist in A2A aufgegangen; das Agent Client Protocol ist von dieser Fusion nicht betroffen [40][42].

## Inhalt

1. [Vergleich nebeneinander](#1-vergleich-nebeneinander)
2. [Wie sie zusammenpassen](#2-wie-sie-zusammenpassen)
3. [Überschneidungen und Lücken](#3-überschneidungen-und-lücken)
4. [Was das für llmcf bedeutet](#4-was-das-für-llmcf-bedeutet)
5. [Quellen](#5-quellen)

---

## 1. Vergleich nebeneinander

| Aspekt | MCP (2026-07-28) | ACP, Agent Client Protocol (v1) | A2A (1.0.x; hat IBM ACP aufgenommen) |
|---|---|---|---|
| **Zweck** | Einer KI-Anwendung Tools, Ressourcen und Prompts geben [1] | Einen Editor einen Coding-Agenten steuern lassen: Prompts, Diffs, Terminals, Freigaben [12] | Unabhängige, oft undurchsichtige Agenten Arbeit aneinander abgeben lassen [32] |
| **Beteiligte** | Host und sein Client (in der KI-App) ↔ Server (Tools und Daten) [1] | Client (Editor oder IDE) ↔ Agent [12] | Client-Agent ↔ entfernter Agent (A2A-Server) [32] |
| **Richtung** | Requests vom Client zum Server; vom Server zum Client nur Antworten und Notifications. Server senden in dieser Revision keine Requests [3] | In beide Richtungen: Der Editor ruft den Agenten auf (`session/prompt`); der Agent ruft den Editor auf (`request_permission`, `fs/*`, `terminal/*`) [13] | Requests vom Client-Agenten zum entfernten Agenten; zurück über Streams und Push-Webhooks [32] |
| **Transport** | stdio und Streamable HTTP; eigene Transporte erlaubt; HTTP+SSE abgekündigt [2][3] | Nur stdio ist festgelegt; entferntes Streamable HTTP und WebSocket in einem Active-RFD [17][28] | HTTP(S) mit Bindings für JSON-RPC, gRPC oder HTTP+JSON/REST; SSE fürs Streaming; Webhooks [32] |
| **Nachrichtenformat** | JSON-RPC 2.0 [1] | JSON-RPC 2.0; übernimmt MCP-JSON-Typen; Inhalte sind eine Obermenge der Inhalte von MCP 2025-06-18 [12][19] | Abstraktes Datenmodell (Task, Message, Part, Artifact), auf jedes Binding abgebildet; Medientyp `application/a2a+json` [32][33] |
| **Session und Zustand** | **Zustandslos** seit 2026-07-28: kein `initialize`-Handshake, keine Session-ID; Zustand über vom Server ausgegebene Handles in Tool-Argumenten [2] | **Zustandsbehaftet**: `session/new`, `load`, `resume`, `list`, `close`, `delete`; der Agent hält Verlauf und Kontext [15] | Um Tasks gebaut: Task-IDs plus `contextId`; der entfernte Agent hält den Task-Zustand [32] |
| **Streaming** | SSE-Antworten je Request; `subscriptions/listen` für Listenänderungen; Fortschritts-Notifications [2][3] | `session/update`-Notifications: Nachrichtenteile, Gedanken, Tool-Calls, Plan, Verbrauch [13] | `SendStreamingMessage` und Task-Abo über SSE; Push-Benachrichtigungen [32] |
| **Lang laufende Arbeit** | Erweiterung `io.modelcontextprotocol/tasks` (`tasks/get`, `tasks/update`, `tasks/cancel`) [5] | Ein Prompt-Turn endet mit einem Stop-Grund; `session/cancel` [13] | Eingebauter Task-Lebenszyklus mit `INPUT_REQUIRED` und `AUTH_REQUIRED` [32] |
| **Freigabe durch Menschen** | Der Host muss vor jedem Tool-Aufruf die Zustimmung des Nutzers einholen, MCP kann das aber nicht erzwingen; Tool-Annotations sind Hinweise; Elicitation über Requests mit mehreren Runden (`input_required`, dann Wiederholung) [1][2] | Fester Bestandteil: `session/request_permission` mit `allow_once`, `allow_always`, `reject_once`, `reject_always`; der Agent speichert „immer“-Entscheidungen; Elicitation (Form, URL) stabil seit 2026-07-22 [16][20][22] | Task-Zustände `INPUT_REQUIRED` und `AUTH_REQUIRED`; kein eigener Baustein für einen Freigabe-Dialog **[Schluss]** [32] |
| **Dateisystem und Terminal** | Nichts im Kern. Roots sind abgekündigt; Pfade gehen in Tool-Parameter [2] | Optionale Client-Dienste `fs/read_text_file` (mit ungespeicherten Editor-Änderungen), `fs/write_text_file`, `terminal/*`; im v2-Entwurf gestrichen [27][43][44] | Nichts; Agenten tauschen nur Nachrichten und Artefakte [32] |
| **Anmeldung** | Optional. HTTP: OAuth-2.1-Resource-Server mit RFC 8707, RFC 9728, RFC 9207, Client-ID-Metadaten-Dokumenten; kein Durchreichen von Tokens. stdio: Zugangsdaten aus der Umgebung [4] | `authMethods` in `initialize` [14]; `authenticate` (Agent meldet sich selbst an) oder `terminal` (interaktive Anmeldung); `logout` [21] | Sicherheitsschemata in der Agent Card: API-Key, HTTP-Auth, OAuth 2.0 (Authorization Code mit PKCE, Client Credentials, Device Code), OpenID Connect, mTLS [32] |
| **Auffinden (Discovery)** | `server/discover` (Versionen, Fähigkeiten, Identität); Konfiguration im Client [2] | ACP Registry für Agenten; Konfiguration im Editor [26] | Agent Card unter `/.well-known/agent-card.json`, optional signiert; erweiterte Card nach Anmeldung [32] |
| **SDKs** | Python `mcp` 2.3.0 (2026-10-02); eigenständiges FastMCP 4.1.5 (2026-11-01); weitere Sprachen vorhanden [9][10][11] | Python `agent-client-protocol` 0.12.1 (Vorabversion 1.0.0rc3); TypeScript und Rust 1.0 (angekündigt am 2026-06-25); Kotlin `acp` 0.34.0, Java `acp-core` 0.80.0 [30][31][49][50] | Python `a2a-sdk` 1.3.0 (2026-10-27); fünf produktive Sprachen: Python, JavaScript, Java, Go, .NET [35][37] |
| **Governance** | Agentic AI Foundation (AAIF) unter der Linux Foundation seit 2025-12-09 [7][8] | Zed und JetBrains, Übergangsmodell, Stiftung geplant; kein AAIF-Projekt [23][51] | Linux-Foundation-Projekt seit 2025-06-23; AAIF-Projekt seit 2026-08-17 [34][36] |
| **Reife** | Fünf Revisionen seit 2024-11-05; formale Abkündigungsregeln; 2026-07-28 war ein Umbau mit Brüchen [2][6][45] | v1 stabil, viele Funktionen 2026 stabilisiert; v2-Entwurf seit 2026-07-20 [22][27] | v1.0.0 am 2026-03-12, v1.0.1 am 2026-05-28 [33] |
| **Verbreitung** | Breit; llmcf wird aus Claude Code, Claude Desktop und VS Code genutzt (Repo-Doku). Keine Zahl recherchiert | 49 Agenten; 21 Editoren und IDEs sowie etwa 130 weitere Clients gelistet; etwa 4,6k GitHub-Sterne [24][25][46] | Über 150 Organisationen (2026-04-09) [35] |

---

## 2. Wie sie zusammenpassen

Die drei Protokolle sitzen an verschiedenen Nahtstellen. Sie ersetzen einander nicht.

```
   Person
     |
  +--------------------+
  | Editor / IDE       |   Zed, JetBrains, Neovim ...
  +--------------------+
     |  ACP  (JSON-RPC über stdio; Prompts, Diffs, Freigaben)
  +--------------------+         A2A  (HTTP; Tasks)        +------------------+
  | Coding-Agent       | <-------------------------------> | Entfernter Agent |
  | (LLM-Schleife)     |                                   +------------------+
  +--------------------+
     |  MCP  (JSON-RPC über stdio oder HTTP; Tools, Ressourcen)
  +--------------------+
  | MCP-Server         |   zum Beispiel llmcf: Targets, Builds, Logs, Deploy
  +--------------------+
```

- **ACP über MCP.** Der Editor übergibt MCP-Server-Einträge in `session/new.mcpServers`. Der Agent verbindet sich
  dann als MCP-Client mit diesen Servern. stdio-Einträge sind für jeden Agenten Pflicht [15]. Ein MCP-Server erreicht
  so jeden ACP-Agenten, dessen Editor ihn einträgt.
- **A2A neben MCP.** Die A2A-Spezifikation nennt MCP eine Ergänzung: Ein Client-Agent bittet einen entfernten
  Agenten um eine Aufgabe, und der entfernte Agent darf für seine Tools MCP nutzen [32]. Die AAIF ordnet MCP der
  Schicht Agent–Werkzeug zu und A2A der Schicht Agent–Agent [36].
- **ACP und A2A** berühren sich kaum. ACP verbindet eine Person im Editor mit einem Agenten. A2A verbindet Agenten
  über ein Netz. **[Schluss]** Ein Coding-Agent kann ACP zum Editor sprechen, A2A zu anderen Agenten und MCP zu
  Tools.

---

## 3. Überschneidungen und Lücken

Überschneidungen:

- **JSON-RPC 2.0** in MCP, ACP und einem A2A-Binding [1][13][32].
- **Inhaltstypen.** ACP-Inhalte sind eine Obermenge der Inhalte von MCP 2025-06-18; Ausgaben von MCP-Tools lassen
  sich unverändert durchreichen [19].
- **Elicitation.** Die ACP-Elicitation beruht auf dem Release Candidate von MCP 2026-07-28, mit Form- und URL-Modus
  [20]. Beide haben einen URL-Modus für heikle Abläufe (MCP seit 2025-11-25) [20][45]. ACP sagt, der Form-Modus darf
  nicht für Geheimnisse genutzt werden [20].
- **Pause für Eingaben.** MCP `input_required`, der Tasks-Zustand `input_required` in MCP und A2A `INPUT_REQUIRED`
  folgen einem ähnlichen Muster [2][5][32]. **[Schluss]**
- **Trace Context.** MCP und ACP reservieren beide `traceparent`, `tracestate` und `baggage` in `_meta` [2][48].
- **MCP-over-ACP** (RFD-Entwurf) würde MCP-Nachrichten innerhalb der ACP-Verbindung über `mcp/message` tragen [29].

Unterschiede, die in der Praxis zählen:

- **Zustand.** MCP hat in 2026-07-28 Sessions auf Protokollebene abgeschafft [2]. ACP ist um Sessions gebaut, die der
  Agent hält [15].
- **Wer die Person fragt.** MCP überlässt die Zustimmung dem Host und kann sie nicht erzwingen [1]. ACP gibt dem
  Agenten eine Freigabe-Anfrage, die der Editor zeigt [16]. Beide sehen das als beratend; Server bzw. Client müssen
  trotzdem selbst prüfen [1][18][43].
- **Requests von der Gegenseite.** MCP-Server senden in 2026-07-28 keine Requests [3]. ACP-Agenten rufen den Editor
  für Freigaben, Dateien und Terminals auf [13].
- **Kleine Abweichungen.** MCP 2026-07-28 hat `notifications/elicitation/complete` gestrichen [2]. ACP behält
  `elicitation/complete` für den URL-Modus [20]. MCP hat „Ressource nicht gefunden“ von -32002 auf -32602 verlegt [2];
  ACP v1 nutzt weiter -32002 [47]. **[Schluss]** Eine Brücke muss das übersetzen.

Lücken:

- **Entferntes ACP.** Noch kein stabiler entfernter Transport [12][28].
- **Auffinden.** MCP hat `server/discover` je Server; ACP hat eine kuratierte Registry; A2A hat Agent Cards. Ein
  gemeinsames Format gibt es nicht [2][26][32].
- **Anmeldung bei lokalem stdio.** MCP sagt, stdio soll kein OAuth nutzen und Zugangsdaten aus der Umgebung nehmen
  [4]. ACP überlässt die Anmeldung dem Agenten [21]. Keines der Protokolle authentifiziert den lokalen Gegenprozess.
  **[Schluss]**
- **Keine Sandbox.** ACP sagt es offen: Der Agent läuft mit den Rechten seines Prozesses [18]. Auch MCP kann die
  Zustimmung nicht erzwingen [1]. Die Sicherheit liegt in den Implementierungen.
- **Governance.** MCP und A2A liegen bei einer neutralen Stiftung (AAIF). ACP noch nicht [23][36][51].
- **IBM ACP** ist archiviert, das Python-SDK `acp-sdk` ist seit 2025-08-21 bei 1.0.3 eingefroren; der Link zur
  Migrationsanleitung in seiner README liefert 404 [38][39][52]. **[Schluss]** Keine neue Arbeit darauf beginnen.

---

## 4. Was das für llmcf bedeutet

**[Schluss]** für diesen ganzen Abschnitt, auf Basis der Quellen und des Repositorys.

- **MCP bleibt die Tool-Schnittstelle.** llmcfs MCP-Server (`orchestrator/mcp_server.py`, Spezifikation 2026-07-28,
  `mcp` 2.3.0) ist die richtige Schicht für Tools. Jeder Aufruf geht weiter durch `ToolRegistry.call()` mit Policy,
  Bestätigung, Audit und Ausgabefilter.
- **ACP-Unterstützung beginnt kostenlos.** ACP-Agenten in Zed, JetBrains und anderen Editoren können llmcf schon
  heute nutzen, weil jeder ACP-Agent stdio-MCP-Server annehmen muss [15]. Der erste Schritt ist Dokumentation: eine
  getestete Editor-Konfiguration, die `llm-cli mcp serve` startet.
- **Ein ACP-Agent-Endpunkt ist ein eigener Prozess.** Soll Ditto in Editoren laufen, braucht er einen eigenen
  Einstiegspunkt (zum Beispiel `llm-cli acp`). Ein stdio-Stream trägt entweder ACP oder MCP, nie beides [17]. Dieser
  Endpunkt muss:
  - Dittos Turns auf `session/update` abbilden;
  - jede Aktion durch die Registry leiten;
  - in Freigabe-Anfragen nur Einmal-Optionen anbieten, damit Dauerfreigaben in llmcfs eigenem Fenster bleiben;
  - die absoluten Pfade des Editors (`cwd`, Wurzeln) bereinigen, bevor sie das Modell erreichen, wie heute bei jedem
    KI-Leser;
  - nie Kontonamen von Geräten oder Geheimnisse an die KI geben;
  - vom Editor gelieferte MCP-Server annehmen, sich aber nur mit denen verbinden, die die Person eingeschaltet hat,
    und ihre Ergebnisse als nicht vertrauenswürdig markieren.
- **A2A nur, wenn Arbeit von Agent zu Agent gewünscht ist.** Es passte neben MCP, zum Beispiel mit llmcfs Tools als
  A2A-Skills in einer Agent Card. Der heutige Entwurf verlangt das nicht. Es braucht Erreichbarkeit im Netz, die llmcf
  heute vermeidet (nur Loopback).
- **Fremde Agenten in der llmcf-GUI beherbergen** (llmcf als ACP-Client) widerspricht der Entwurfsregel, dass das
  Framework nie selbst ein Client-Programm startet. Vorher braucht es eine Entscheidung des Maintainers.
- **Pinnen und abwarten.** ACP v2 ist ein Entwurf, das Python-SDK liegt unter 1.0, und MCP-over-ACP ist ein Entwurf
  [27][29][30]. Auf ACP v1 nur über stdio bauen und das ACP-Paket im neuen Adapter halten.

---

## 5. Quellen

Alle abgerufen am 2026-11-02.

1. https://modelcontextprotocol.io/specification/latest
2. https://modelcontextprotocol.io/specification/2026-07-28/changelog
3. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
4. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
5. https://modelcontextprotocol.io/extensions/tasks/overview
6. https://blog.modelcontextprotocol.io/posts/2026-07-28/
7. https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/
8. https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation
9. https://pypi.org/pypi/mcp/json
10. https://github.com/modelcontextprotocol/python-sdk/releases
11. https://pypi.org/pypi/fastmcp/json
12. https://agentclientprotocol.com/overview/introduction
13. https://agentclientprotocol.com/protocol/overview
14. https://agentclientprotocol.com/protocol/v1/initialization.md
15. https://agentclientprotocol.com/protocol/v1/session-setup.md
16. https://agentclientprotocol.com/protocol/v1/tool-calls.md
17. https://agentclientprotocol.com/protocol/v1/transports.md
18. https://agentclientprotocol.com/protocol/v1/security.md
19. https://agentclientprotocol.com/protocol/v1/content.md
20. https://agentclientprotocol.com/protocol/v1/elicitation.md
21. https://agentclientprotocol.com/protocol/v1/authentication.md
22. https://agentclientprotocol.com/updates.md
23. https://agentclientprotocol.com/community/governance.md
24. https://agentclientprotocol.com/get-started/clients.md
25. https://agentclientprotocol.com/get-started/agents.md
26. https://agentclientprotocol.com/get-started/registry.md
27. https://agentclientprotocol.com/announcements/acp-v2-draft.md
28. https://agentclientprotocol.com/rfds/streamable-http-websocket-transport.md
29. https://agentclientprotocol.com/rfds/mcp-over-acp.md
30. https://pypi.org/pypi/agent-client-protocol/json
31. https://agentclientprotocol.com/announcements/sdk-1-0-releases.md
32. https://a2a-protocol.org/latest/specification/
33. https://github.com/a2aproject/A2A/releases
34. https://linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents
35. https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year
36. https://aaif.io/blog/a2a-joins-aaif
37. https://pypi.org/pypi/a2a-sdk/json
38. https://github.com/i-am-bee/acp
39. https://github.com/orgs/i-am-bee/discussions/5
40. https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/
41. https://agentcommunicationprotocol.dev/about/mcp-and-a2a.md
42. https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026
43. https://agentclientprotocol.com/protocol/v1/file-system.md
44. https://agentclientprotocol.com/protocol/v1/terminals.md
45. https://modelcontextprotocol.io/specification/2025-11-25/changelog
46. https://github.com/agentclientprotocol/agent-client-protocol
47. https://agentclientprotocol.com/protocol/v1/error.md
48. https://agentclientprotocol.com/protocol/v1/extensibility.md
49. https://repo1.maven.org/maven2/com/agentclientprotocol/acp/maven-metadata.xml
50. https://repo1.maven.org/maven2/com/agentclientprotocol/acp-core/maven-metadata.xml
51. https://aaif.io/projects/
52. https://pypi.org/pypi/acp-sdk/json
