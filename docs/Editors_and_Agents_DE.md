Version 3.4.0 · Stand 2026-11-06

[English](Editors_and_Agents.md) · **Deutsch**

# Editoren und Agenten: Einrichtungsanleitung

Diese Anleitung verbindet das Framework mit Editoren, die das Agent Client Protocol (ACP) sprechen: Android Studio,
Zed und JetBrains-IDEs. Es gibt drei Wege, jeden davon kannst du für sich allein nutzen:

| Teil | Wer der KI-Agent ist | Wer was startet |
|---|---|---|
| [A: der MCP-Server des Frameworks im Agenten eines Editors](#a-der-mcp-server-des-frameworks-im-agenten-eines-editors) | Ein Agent des Editors (Claude Agent, Codex, Gemini CLI, ...) | Der Editor startet den Agenten; der Agent startet `llm-cli mcp serve` |
| [B: Ditto als Agent des Editors](#b-ditto-als-agent-des-editors) | Ditto, der eigene Assistent des Frameworks | Der Editor startet `llm-cli acp` |
| [C: Agenten im eigenen Fenster des Frameworks](#c-agenten-im-eigenen-fenster-des-frameworks) | Ein Agent, den du dem Framework hinzufügst | Das Framework startet den Agenten und gibt ihm seinen MCP-Server |

In jedem Fall handelt der Agent nur über die Werkzeuge des Frameworks: Jeder Aufruf geht durch deine
Tool-Berechtigungen, die Freigaben und das Audit-Log, wie bei jeder KI-App über MCP
([Features: MCP-Server](Features_DE.md#mcp-server)).

Fakten zu den Editoren stammen aus deren eigener Dokumentation (siehe [Quellen](#quellen), alle gelesen am
2026-11-06). Eine Aussage mit **[Unbestätigt]** ließ sich nicht an einer Primärquelle prüfen.

## Inhalt

- [Bevor du anfängst: Einträge kopieren, nie abtippen](#bevor-du-anfängst-einträge-kopieren-nie-abtippen)
- [A: der MCP-Server des Frameworks im Agenten eines Editors](#a-der-mcp-server-des-frameworks-im-agenten-eines-editors)
- [B: Ditto als Agent des Editors](#b-ditto-als-agent-des-editors)
- [C: Agenten im eigenen Fenster des Frameworks](#c-agenten-im-eigenen-fenster-des-frameworks)
- [Fehlersuche](#fehlersuche)
- [Quellen](#quellen)

## Bevor du anfängst: Einträge kopieren, nie abtippen

Ein Editor startet das Framework als eigenes Programm. Der Eintrag, den er braucht, nennt absolute Pfade dieser
Installation: den Python-Interpreter der virtuellen Umgebung des Frameworks, die Argumente
`-P -m orchestrator.cli -q`, danach `mcp serve` (Teil A) oder `acp` (Teil B), und die Variable `PYTHONPATH` mit dem
Installationsordner. `-P` hält den Ordner, in dem der Editor es startet, aus Pythons Importpfad heraus. Das Framework
erzeugt diese Einträge selbst, aus demselben Startbefehl, mit dem es den MCP-Server für einen eigenen Agenten startet
(Teil C):

- **In der GUI:** **Sicherheit > Verbindungen zu KI-Apps (MCP)...** zeigt die Einträge des MCP-Servers (Claude Code,
  Claude Desktop, VS Code, Zed), jeden mit einem Knopf **Kopieren**. **Datei > Einstellungen…**, Tab **Ditto in
  Editoren**, zeigt die Einträge für Ditto (den Befehl, den Zed-Eintrag, den JetBrains-Eintrag), jeden mit einem
  Knopf **Kopieren**.
- **Im Terminal**, mit dem Python des Frameworks (damit der ausgegebene Pfad der richtige Interpreter ist):

```bash
llm-cli mcp config
llm-cli acp config
llm-cli acp config --json
```

Kopiere den Eintrag neu, wenn du das Framework verschiebst oder neu installierst: Die Pfade ändern sich mit. Unter
Windows ist die Befehlszeile so gequotet, dass cmd und PowerShell sie beide unverändert lassen; enthält ein Pfad `%`,
`!`, `$`, ein Backtick oder ein doppeltes Anführungszeichen, stehen nur die JSON-Einträge da.

## A: der MCP-Server des Frameworks im Agenten eines Editors

In ACP übergibt der Editor dem Agenten MCP-Server, wenn er eine Sitzung öffnet (`session/new`, Feld `mcpServers`),
und der Agent verbindet sich dann als MCP-Client mit ihnen. Jeder ACP-Agent muss MCP-Server über stdio annehmen: einen
Namen, den Befehl als absoluten Pfad, die Argumente und die Umgebung ([ACP: Session Setup][acp-session]). Der
MCP-Server des Frameworks ist so ein stdio-Server. Der Agent startet ihn und nutzt die Werkzeuge des Frameworks
innerhalb deiner Berechtigungen; beim Agenten meldest du dich mit deinem eigenen Konto an, das Framework sieht es nie.

Was der Agent sieht: Der Eintrag nennt lokale Pfade (den Python-Interpreter und den Installationsordner des
Frameworks), wie jede MCP-Client-Konfiguration. Werkzeugergebnisse enthalten nie Host-Pfade oder Kontonamen von
Geräten: Ordner erreichen das Modell als Labels.

### Zed

1. Kopiere den Eintrag **Zed** (`context_servers`) aus **Sicherheit > Verbindungen zu KI-Apps (MCP)...** oder aus
   `llm-cli mcp config`.
2. Füge ihn in Zeds `settings.json` unter `context_servers` ein (zusammen mit Einträgen, die schon dort stehen).

Zeds Dokumentation sagt: "Zed can forward configured MCP servers over ACP; agents may also read native MCP config".
Sie sagt "may": Ob ein bestimmter externer Agent den Server bekommt, hängt von Zed und dem Agenten ab
([Zed: MCP][zed-mcp], [Zed: External Agents][zed-agents]).

### JetBrains-IDEs

1. Öffne **Settings | Tools | AI Assistant | Model Context Protocol (MCP)** und klicke **Add**
   ([JetBrains: MCP][jb-mcp]). Das dokumentierte JSON-Format ist `mcpServers` mit `command` und `args` für einen
   lokalen Server: Füge den Eintrag **Claude Desktop** (`mcpServers`) aus **Sicherheit > Verbindungen zu KI-Apps
   (MCP)...** oder `llm-cli mcp config` ein. Nutzt du Claude Desktop schon, macht **In Claude Desktop eintragen...**
   (oder `llm-cli mcp config --write claude-desktop`) und danach der Knopf **Import from Claude** der IDE dasselbe.
2. Lass die IDE die Server, die du eingerichtet hast, an ihre ACP-Agenten weitergeben ([JetBrains: ACP][jb-acp]):
   Für Agenten aus der Registry lass in **Settings | Tools | AI Assistant | Agents** die Option
   **Pass custom MCP servers** an; für einen eigenen Agenten in `~/.jetbrains/acp.json` macht das `use_custom_mcp`
   unter `default_mcp_settings` (standardmäßig an).

**[Unbestätigt]** Die JetBrains-Seite dokumentiert im JSON nur `command` und `args` und im Dialog ein eigenes Feld
**Working directory**. Ob die IDE das `env` des Eintrags (`PYTHONPATH`) behält, wurde nicht bestätigt. Ohne es startet
der Server nicht (Python meldet, dass es kein Modul namens `orchestrator` findet); prüfe dann die Variablen des
Eintrags im Dialog.

### Android Studio

Die eigenen MCP-Einstellungen von Android Studio (**File > Settings > Tools > AI > MCP Servers**, die Datei
`mcp.json`) nennen als Einschränkung: "Connection to MCP servers through `stdio` transport"
([Android Studio: MCP-Server hinzufügen][as-mcp]). Android Studio kann den stdio-Server des Frameworks also nicht an
seine Agenten weitergeben.

Stattdessen geht die eigene MCP-Konfiguration des Agenten: Claude Agent etwa liest die Konfiguration von Claude
Code, die der Befehl `claude mcp add ...` aus `llm-cli mcp config` (oder der Eintrag **Claude Code** in
**Sicherheit > Verbindungen zu KI-Apps (MCP)...**) schreibt. **[Unbestätigt]** Ob jeder Agent, den Android Studio
startet (Bring Your Own Agent), seine eigene MCP-Konfiguration liest, wurde für Android Studio nicht bestätigt; Zeds
Dokumentation sagt, dass Agenten das tun können ([Zed: External Agents][zed-agents]).

## B: Ditto als Agent des Editors

Der Editor startet `llm-cli acp`, und du chattest im Editor mit Ditto; Ditto arbeitet mit den eigenen Werkzeugen des
Frameworks, denselben wie im Chat-Bereich. Ditto ist **experimentell**, auch im Editor: Der Hinweis ist die erste
Nachricht jeder Editor-Sitzung ([Features: Ditto in Editoren](Features_DE.md#ditto-in-editoren-acp)).

### Zed

Kopiere den Eintrag **Zed** aus **Datei > Einstellungen…**, Tab **Ditto in Editoren**, oder aus
`llm-cli acp config` und füge ihn in Zeds `settings.json` unter `agent_servers` ein. Zed nimmt einen eigenen Agenten
als `"type": "custom"` mit `command`, `args` und `env`; der kopierte Eintrag hat genau diese Felder
([Zed: External Agents][zed-agents]).

### JetBrains-IDEs

Klicke im Werkzeugfenster AI Chat auf **More** und wähle **Add Custom Agent**: Die IDE legt `~/.jetbrains/acp.json` an
und öffnet sie ([JetBrains: ACP][jb-acp]). Füge dort den Eintrag **JetBrains** ein (`agent_servers`, aus demselben
Tab oder aus `llm-cli acp config --json`). Die Einstellungsseite der Agenten ist
**Settings | Tools | AI Assistant | Agents**.

### Android Studio

Android Studio (ab Rabbit 2) startet ACP-Agenten ("Bring Your Own Agent"): Du wählst einen Agenten im Agent-Fenster,
und "Additional agents can be found in the registry Settings > Tools > AI > Agents" ([Android Developers Blog,
2026-09-24][as-byoa]). Ditto ist kein Agent der ACP-Registry; du fügst es als eigenen Agenten hinzu. Googles
Einrichtungsseite für seinen Antigravity-Agenten sagt: "Android Studio uses the same `~/.jetbrains/acp.json` file as
other JetBrains IDEs" und "Custom agents are in preview" ([Antigravity: andere Editoren (ACP)][ag-acp]). Also:

1. Trage Dittos Eintrag in `~/.jetbrains/acp.json` unter `agent_servers` ein, wie in den JetBrains-Schritten oben
   (der Knopf **Kopieren** im Tab **Ditto in Editoren** oder `llm-cli acp config --json`).
2. Starte Android Studio neu und öffne das Werkzeugfenster **Agent**.
3. Wähle oben im Agenten-Auswahlfeld den Namen, den du dem Eintrag gegeben hast.

Die eigenen Seiten zu Android Studio (die für diese Anleitung gelesenen Blogbeiträge und Release Notes) beschreiben
eigene Agenten nicht; die Aussage oben stammt aus Googles Antigravity-Dokumentation.

### Ditto anmelden

Ditto antwortet mit dem KI-Anbieter deiner KI-Einstellungen. Solange keiner eingerichtet ist, bietet der Editor
**Sign Ditto in** an: Es startet die eigene Anmeldung des Frameworks (`llm-cli acp --login`) in einem Terminal, die
nach dem Anbieter fragt und dann `llm-cli ditto login PROVIDER` für ihn ausführt. Du kannst dich auch vorher anmelden, mit
**Sicherheit > Anmeldeübersicht...** oder im Terminal:

```bash
llm-cli ditto login PROVIDER
```

Kein Schlüssel geht je durch den Editor.

### Was der Editor kann und was nicht

- **Berechtigungsfragen** bieten nur **Allow once** und **Reject** (einmal erlauben, ablehnen). "Nicht mehr fragen"
  (eine dauerhafte Freigabe) gibt es nur im eigenen Freigabefenster des Frameworks. Eine Löschung oder ein Werkzeug,
  das du auf "im Framework bestätigen" gestellt hast, wird nie im Editor gefragt; eine Frage, die der Editor nicht
  beantwortet, wartet als offene Freigabe im Framework (**Ditto > Offene Freigaben...**).
- **Ordner**: Die Projektordner des Editors erreichen das Modell nur als Labels (`project`, `project-2`, ...), nie als
  Pfade. Der Datenschutzfilter gilt für jeden Cloud-Aufruf wie überall.
- **Die eigenen MCP-Server des Editors**: Ein Editor kann Ditto seine MCP-Server übergeben. Sie werden festgehalten
  und im Tab **Ditto in Editoren** sowie von `llm-cli acp servers list` gezeigt; keiner davon wird benutzt, weil ihre
  Werkzeuge die Berechtigungen und das Audit-Log des Frameworks umgehen würden. Nur du kannst einen Eintrag zulassen,
  in diesem Tab oder mit `llm-cli acp servers enable SERVER`; diese Version verbindet sich so oder so mit keinem.
- Die Datei- und Terminaldienste des Editors werden nie benutzt; Builds und Deployments laufen nur über die Werkzeuge
  des Frameworks. Anbieter und Modell lassen sich pro Sitzung wählen; Slash-Befehle `/status`, `/targets`, `/builds`,
  `/mojo`.

```bash
llm-cli acp servers list
llm-cli acp servers enable SERVER
llm-cli acp servers disable SERVER
```

## C: Agenten im eigenen Fenster des Frameworks

Das Framework ist auch ein ACP-Client, wie ein Editor: Du fügst externe Agenten hinzu und chattest mit ihnen neben
Ditto ([Features: ACP-Agenten, die du auswählst](Features_DE.md#acp-agenten-die-du-auswählst)). Einen Agenten
hinzufügen, anmelden und starten kannst nur du; eine KI kann es nie.

### Claude Agent, Codex oder Gemini CLI hinzufügen

- **Aus der ACP-Registry:** **Ditto > Agenten...**, **Registry abrufen** (nur bei diesem Klick, über HTTPS), den
  Agenten wählen und **Gewählten Agenten hinzufügen...**. Bevor du Ja sagst, siehst du Name, Beschreibung,
  Verteilung, Lizenz, den genauen Befehl, den Download und seine Prüfsumme; "Nein" ist vorgewählt. Im Terminal:

```bash
llm-cli agents search --refresh
llm-cli agents search claude
llm-cli agents add ID
llm-cli agents add ID --kind npx
```

- **Dein eigener Befehl:** im selben Fenster **Dein eigener Befehl** (das Programm, seine Argumente, ob es ohne
  Internet geht, die Variablen seiner Schlüssel), oder:

```bash
llm-cli agents add --command PATH --name NAME --arg ARG --key VARIABLE
llm-cli agents add --command PATH --name NAME --local
```

- **Die Prüfsummen-Regel:** npx- und uvx-Agenten laufen nur mit der genauen Version, die die Registry nennt. Ein
  Binärprogramm wird nur über HTTPS und nur mit der sha256 der Registry geladen; ohne sie wird es nicht geladen, und du
  kannst es selbst installieren und als eigenen Befehl hinzufügen.

### Anmelden

**Anmelden...** in **Ditto > Agenten...** (oder `llm-cli agents login NAME`) nutzt die eigene Anmeldung des Agenten:

- seine **Browser-Anmeldung**: die Anmeldeseite deines Anbieters mit ihrem eigenen zweiten Faktor (OAuth, 2FA); das
  Framework sieht dein Passwort nie;
- seine **Terminal-Anmeldung**: der Anmeldebefehl des Agenten in einem Konsolenfenster, das du siehst (in der GUI),
  oder in deinem Terminal;
- **API-Schlüssel**: **Schlüssel speichern...** (oder `llm-cli agents login NAME --key VARIABLE`) speichert einen
  Schlüssel, verdeckt und zweimal abgefragt, im Schlüsseltresor des Frameworks. Er geht nur an diesen Agenten, als
  diese Variable, wenn er startet.

```bash
llm-cli agents login NAME
llm-cli agents login NAME --method METHOD
llm-cli agents login NAME --key VARIABLE
```

### Chatten, Offline-Modus, was der Agent sieht

- **Chatten mit:** im Chat-Bereich wählt den Agenten; im Terminal `llm-cli agents run NAME`. Die
  Berechtigungsfragen des Agenten kommen im eigenen Dialog des Frameworks: **Einmal erlauben** oder **Ablehnen**; ein
  "immer" des Agenten wird nie gewählt.
- **Offline-Modus** (**Ditto > KI-Einstellungen...**, **Offline-Modus**) lehnt einen Agenten ab, der das Internet braucht.
  Ein eigener Befehl, den du als ohne Internet lauffähig hinzugefügt hast (`--local`), darf starten.
- **Was der Agent sieht:** die MCP-Werkzeuge des Frameworks und sonst nichts vom Framework. Seine Sitzung bekommt genau
  einen MCP-Server, den des Frameworks über stdio; dieser Eintrag nennt lokale Pfade (den Python-Interpreter, den
  Installationsordner, den eigenen Arbeitsordner des Agenten), wie jede MCP-Client-Konfiguration. Das Framework bietet
  ihm kein Dateisystem und kein Terminal an. Der Agent läuft mit einer sauberen Umgebung (die Grundlagen des Systems
  und seine eigenen Schlüssel) in seinem eigenen Ordner unter dem Framework-Home.
- **Entfernen:** **Entfernen...** (oder `llm-cli agents remove NAME`) listet zuerst die Ordner und den Eintrag des
  Agenten ("Nein" ist vorgewählt) und nimmt seine Schlüssel aus dem Tresor.

```bash
llm-cli agents list
llm-cli agents run NAME
llm-cli agents remove NAME
```

## Fehlersuche

Die Meldungen unten sind die eigenen Texte des Frameworks; `{name}` steht für den Namen des Agenten. Meldungen aus dem
Terminal und aus dem Editor sind englisch, die Texte der GUI deutsch.

| Meldung | Wo | Was tun |
|---|---|---|
| "Ditto has no AI provider set up yet. Sign Ditto in first" | Der Editor, Teil B | Nutze **Sign Ditto in** im Editor, **Sicherheit > Anmeldeübersicht...** oder `llm-cli ditto login PROVIDER`. |
| "The installation path ... cannot be put on PYTHONPATH safely" | Die Einträge, Teile A und B | Der Installationsordner ist relativ oder enthält das Pfadtrennzeichen (`:` oder `;`); installiere das Framework in einen anderen Ordner. |
| "Der Eintrag kann nicht gezeigt werden: {reason}" | Tab **Ditto in Editoren** | Der Grund ist die Zeile darüber oder ein anderes Problem mit den Pfaden; derselbe Eintrag aus `llm-cli acp config` sagt dasselbe. |
| "Offline mode is on, and {name} needs the internet" | Teil C | Schalte den Offline-Modus aus, oder nimm einen Agenten, den du als ohne Internet lauffähig hinzugefügt hast. |
| "The ACP registry gives no checksum for the download of {name}" | Teil C | Das Framework lädt ihn nicht. Installiere den Agenten selbst und füge ihn als eigenen Befehl hinzu. |
| "Node.js (npx) is not installed on this computer." / "uv (uvx) is not installed on this computer." | Teil C | Installiere Node.js oder uv, oder wähle eine andere Verteilung des Agenten. |
| "Die ACP-Registry wurde noch nicht abgerufen." | Teil C | **Registry abrufen**, oder `llm-cli agents search --refresh`. |
| "{name} startet zum ersten Mal und lädt dafür vielleicht erst sein Paket herunter" | Teil C | Warte: Der erste Start eines neuen npx- oder uvx-Agenten kann bis zu 5 Minuten dauern. |
| "{name} did not start the Agent Client Protocol" | Teil C | Das Programm hat nicht als ACP-Agent geantwortet: Prüfe Befehl und Argumente; das Ende seiner eigenen Ausgabe folgt der Meldung. |
| "The program of {name} is missing. Remove the agent and add it again." | Teil C | Sein installiertes Programm fehlt: Entferne den Agenten und füge ihn neu hinzu. |
| "Der Agent bietet hierfür kein „einmal erlauben“ an, nur „immer“ oder ablehnen." | Teil C | Das Framework wählt nie „immer“; nur Ablehnen ist möglich. Gib eine dauerhafte Freigabe im eigenen Fenster des Frameworks, wenn du eine willst. |
| "Diese Version verbindet sich so oder so mit keinem MCP-Server eines Editors." | Teil B | So gewollt: Einen MCP-Server des Editors zuzulassen hält nur deine Zustimmung fest. |
| Python: kein Modul namens `orchestrator` | Teile A und B | Der Editor hat das `PYTHONPATH` des Eintrags verloren, oder die Installation wurde verschoben: Kopiere den Eintrag neu und behalte seine Variablen. |

## Quellen

Alle abgerufen am 2026-11-06.

- [acp-session]: Agent Client Protocol, Session Setup: https://agentclientprotocol.com/protocol/v1/session-setup.md
- [zed-agents]: Zed, External Agents: https://zed.dev/docs/ai/external-agents
- [zed-mcp]: Zed, Model Context Protocol: https://zed.dev/docs/ai/mcp
- [jb-acp]: JetBrains AI Assistant, Agent Client Protocol: https://www.jetbrains.com/help/ai-assistant/acp.html
- [jb-mcp]: JetBrains AI Assistant, Model Context Protocol: https://www.jetbrains.com/help/ai-assistant/mcp.html
- [as-mcp]: Android Studio, MCP-Server hinzufügen: https://developer.android.com/studio/gemini/add-mcp-server
- [as-byoa]: Android Developers Blog, "Build your way: Use any AI agent of your choice in Android Studio"
  (2026-09-24): https://developer.android.com/blog/posts/build-your-way-use-any-ai-agent-of-your-choice-in-android-studio
- [ag-acp]: Google Antigravity, andere Editoren (ACP): https://antigravity.google/docs/ide/extensions/acp
- Die Recherche des Frameworks zu ACP: [ACP-Recherche](research/ACP_DE.md).

[acp-session]: https://agentclientprotocol.com/protocol/v1/session-setup.md
[zed-agents]: https://zed.dev/docs/ai/external-agents
[zed-mcp]: https://zed.dev/docs/ai/mcp
[jb-acp]: https://www.jetbrains.com/help/ai-assistant/acp.html
[jb-mcp]: https://www.jetbrains.com/help/ai-assistant/mcp.html
[as-mcp]: https://developer.android.com/studio/gemini/add-mcp-server
[as-byoa]: https://developer.android.com/blog/posts/build-your-way-use-any-ai-agent-of-your-choice-in-android-studio
[ag-acp]: https://antigravity.google/docs/ide/extensions/acp
