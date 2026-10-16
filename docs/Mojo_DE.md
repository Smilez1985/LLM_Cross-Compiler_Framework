Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md#unreleased) · Zuletzt aktualisiert am 2026-10-16

[English](Mojo.md) · **Deutsch**

# Mojo: die Installation prüfen und reparieren

Mojo ist der Gesundheitscheck des Frameworks. Früher hieß er Doctor; der Name kommt aus dem Projekt rag4ll. Mojo hat
zwei Modi:

- **Mojo Oracle** schaut nur. Es sagt, was es gefunden hat, wo, warum das wichtig ist und ob Mojo Miracle es
  reparieren kann.
- **Mojo Miracle** repariert, was sich sicher reparieren lässt. Danach sagt es, was es repariert hat, was nicht, und
  warum.

Mojo fragt nie ein Modell, was falsch ist: Jede Prüfung ist eine feste Regel mit einem festen Code. Mojo löscht nichts,
was du nicht aus einer Liste gewählt hast, und zeigt nie ein Geheimnis.

Zurück zur [README](../README_DE.md) · Alle Befehle: [CLI_Usage_DE.md](CLI_Usage_DE.md#mojo)

## Inhalt

1. [Mojo starten](#1-mojo-starten)
2. [Was Mojo Oracle meldet](#2-was-mojo-oracle-meldet)
3. [Was Mojo Miracle repariert](#3-was-mojo-miracle-repariert)
4. [Was Mojo Miracle nicht repariert, und warum](#4-was-mojo-miracle-nicht-repariert-und-warum)
5. [Ohne Terminal](#5-ohne-terminal)
6. [Für KI-Clients](#6-für-ki-clients)
7. [Die alten Namen funktionieren weiter](#7-die-alten-namen-funktionieren-weiter)
8. [Exit-Codes und JSON](#8-exit-codes-und-json)

## 1. Mojo starten

```bash
llmcf mojo              # Mojo Oracle (Standard): nur schauen
llmcf mojo --oracle     # dasselbe
llmcf mojo --miracle    # reparieren, was geht, dann berichten
llmcf mojo --json       # maschinenlesbar; fragt nie und löscht nie
```

`llmcf` ist die Kommandozeile des Frameworks unter ihrem neuen Namen. `llm-cli` ist dasselbe Programm und
funktioniert weiter, `llm-cli mojo` tut also genau dasselbe. Die Entwicklungsumgebung und der Framework-Container
haben beide Befehle. Mit den Installern rufst du die Kommandozeile so auf, wie
[Die CLI aufrufen](CLI_Usage_DE.md#die-cli-aufrufen) zeigt. Im Container: `make mojo` oder
`docker exec -it lcf-framework llmcf mojo`.

Wenn Mojo Oracle ein Problem findet, bei dem Mojo Miracle etwas tun kann, fragt es im Terminal:

```
Run Mojo Miracle now? [y/N]:
```

Mit `y` läuft Mojo Miracle sofort; du tippst den Befehl nicht noch einmal. Nein ist die Vorgabe: Dann wird nichts
geändert.

## 2. Was Mojo Oracle meldet

Mojo Oracle schreibt eine Zeile pro Prüfung. Zu jeder Warnung und jedem Fehler schreibt es außerdem:

- **Was** es gefunden hat.
- **Wo**: den vollen Pfad oder das Objekt. Nur du siehst volle Pfade; eine KI liest eine Bezeichnung wie „the state
  folder“.
- **Warum es wichtig ist.**
- **Den Hinweis**: was hilft.
- **Mojo Miracle**: ob es das reparieren kann, oder warum nicht. Für eine Reparatur zeigt es den Plan: was sich
  ändert, den vollen Pfad, vorher und nachher und ob es sich rückgängig machen lässt.

Eine Informationszeile (ℹ) zeigt ihren Hinweis ebenfalls. Am Ende zählt ein Satz jedes Problem danach, was sich tun
lässt, zum Beispiel: „Mojo Oracle found 3 problems (1 error, 2 warnings): Mojo Miracle can repair 1; 1 needs your list
of what to delete; 1 needs you.“ Nichts wird ausgelassen. Mojo Oracle ändert nichts.

Es prüft Python, Docker und buildx, wo Docker seine Images ablegt, den freien Speicher für die Ordner cache, output
und den Build-Ordner, die Targets, den Zustandsordner, den Secrets-Speicher und den Schlüsselbund, die
Tool-Berechtigungen, die MCP-Token und das Audit-Log, den Chat-Feed, die Kalibrierdaten, die Community-Pakete, die
Wissens-Snapshots im Vergleich zum freien Platz ihrer Platte, die lokale llama.cpp-Laufzeit (im Docker-Modus: ob
Dittos Image da ist, auf diesem Rechner gebaut und geprüft) und die Boards, denen du vertraust.

Für jedes vertraute Board liest Mojo die `known_hosts` des Frameworks und öffnet eine Verbindung zum SSH-Port des
Boards. Es meldet sich nie an, schickt kein Passwort und nennt kein Gerätekonto. Ein Board, das sich nicht nutzen
lässt, bekommt seinen Grund-Code und den nächsten Schritt: `device_host_down` (Information: nichts antwortet, das
Board ist vielleicht ausgeschaltet), `device_port_closed` oder `device_host_key_changed` (Warnungen). Ein falsches
Passwort oder ein fehlender Schlüssel des Frameworks (`device_auth_failed`) zeigt sich erst beim nächsten Deployment
oder Benchmark, weil Mojo sich nicht anmeldet. Mojo prüft die 16 zuletzt vertrauten Boards.

## 3. Was Mojo Miracle repariert

Mojo Miracle macht nur harmlose Reparaturen. Eine harmlose Reparatur schränkt Zugriff ein oder wechselt zu einem
intakten Build, und sie lässt sich rückgängig machen. Heute gibt es zwei:

| Problem | Was Mojo Miracle tut |
|---|---|
| `state.permissions`: Andere Benutzer dieses Computers können deinen Zustandsordner lesen (Tool-Berechtigungen, Audit-Log, Chat) | Der Ordner verliert die Rechte anderer Benutzer, zum Beispiel wird Modus 0755 zu 0700. Deine eigenen Rechte bleiben. |
| `runtime.build.missing`: Der aktive llama.cpp-Build von Dittos Server fehlt | Dittos Server wechselt zum neuesten intakten, geprüften Build der festgelegten llama.cpp-Version, falls einer gespeichert ist. Nichts wird gebaut oder gelöscht. |

So läuft eine Reparatur:

1. Jede Reparatur ist zuerst ein Plan (ein Probelauf). Mojo Oracle zeigt ihn.
2. Direkt vor der Änderung prüft Mojo noch einmal. Ist das Problem weg, wird nichts geändert („already gone“). Ist
   der Plan nicht mehr der, den Mojo Oracle dir gezeigt hat, wird nichts geändert („the problem changed“).
3. Der Ordner wird geöffnet, ohne einem Link zu folgen, und sein Modus wird nur geändert, solange er noch der Modus
   aus dem Plan ist.
4. Das Audit-Log hält die Reparatur fest, bevor sie geschieht. Geht das nicht, wird nichts geändert.
5. Danach prüft Mojo noch einmal und sagt ehrlich, ob das Problem weg ist („Checked again: the problem is gone“) oder
   noch da.

Es läuft immer nur ein Mojo Miracle; ein zweites ändert nichts und sagt das. Findet die Prüfung nach den Reparaturen
ein Problem, das vorher nicht da war, nennt Mojo Miracle es als neu.

## 4. Was Mojo Miracle nicht repariert, und warum

Jedes Problem, das Mojo Miracle nicht repariert, bekommt einen dieser Gründe:

| Grund | Was er bedeutet | Beispiele |
|---|---|---|
| Löschen braucht deine Liste | Platz freigeben heißt löschen, und das Framework löscht nur, was du in seinem eigenen Fenster ankreuzt und bestätigst. Im Terminal zeigt Mojo Miracle dir die Build-Images des Frameworks (Name, ID, Größe, was jedes ist; nichts ist angekreuzt). Du tippst die Nummern der zu löschenden ein und bestätigst genau diese; Nein ist die Vorgabe. Die GUI hat dieselbe Liste unter **Tools > Framework Build Images...**. Docker-Images werden nie gelöscht, außer du tust es, und Dockers Build-Cache wird nie angefasst. Für Wissens-Snapshots ist die Liste **Tools > Knowledge Snapshots...** oder `llm-cli knowledge snapshots`. | `disk.cache`, `disk.output`, `disk.builds`, `knowledge.snapshots` |
| braucht dich | Nur du kannst das: deine Schlüssel, die Tool-Berechtigungen, das Audit-Log, deine Einstellungen und Dateien. Eine KI ändert sie nie. | `secrets.store`, `secrets.storage`, `tools.permissions`, `tools.tokens`, `tools.audit`, `chat_feed.store`, `targets.none`, `devices.<Geräte-ID>` (ein Board, das SSH ablehnt oder dessen Host-Schlüssel sich geändert hat), ein zurückgezogenes Community-Paket (seinen Ordner löschst du selbst) |
| braucht einen Administrator | Es ändert das System außerhalb des Frameworks. | `docker.daemon`, `docker.buildx`, `python.version`, `secrets.keyring`, `runtime.docker.unavailable` |
| nicht automatisch reparierbar | Nichts im Framework kann das von selbst. Der Hinweis sagt, was hilft. | `runtime.build.damaged`, `runtime.build.outdated`, `runtime.docker.image` (Dittos Image neu machen mit `llm-cli ditto runtime ensure --mode docker --force`), `data.calibration`, eine abgestürzte Prüfung |

Eine Reparatur kann auch so enden, immer mit ihrem Grund:

- **Noch da**: Die Änderung wurde gemacht, aber die Prüfung findet das Problem weiter.
- **Schon weg**: Das Problem war weg, bevor Mojo Miracle etwas geändert hat.
- **Das Problem hat sich geändert**: Nichts wurde geändert; `llmcf mojo` zeigt den neuen Plan.
- **Fehlgeschlagen**: zum Beispiel konnte das Audit-Log sie nicht festhalten, oder der Ordner hat sich inzwischen
  geändert.
- **Nicht gelöscht**: Du hast nichts aus der Liste gewählt.

## 5. Ohne Terminal

In einer Pipe, einem Skript oder der Shell einer KI-App kann niemand eine Frage beantworten. Dann gilt:

- `llmcf mojo` berichtet und sagt, bei wie vielen Problemen `llmcf mojo --miracle` etwas tun kann.
- `llmcf mojo --miracle` macht nur die harmlosen Reparaturen. Es schreibt jeden Plan aus und hält ihn im Audit-Log
  fest. Alles andere wird als nicht repariert gemeldet, mit dem Grund.
- Es wird nie etwas gelöscht.
- `--json` fragt nie und löscht nie. Mit `--miracle` macht es ebenfalls nur die harmlosen Reparaturen.

## 6. Für KI-Clients

Eine KI nutzt Mojo über den MCP-Server (und Ditto über dieselben Tools):

- **`run_mojo_oracle`** (nur lesen): jeder Befund mit `where` (eine Bezeichnung, nie ein Host-Pfad), `why` und
  `repair`. `repair.how` ist eines von `miracle`, `deletion_list`, `person`, `admin`, `not_automatic`;
  `repair.summary` sagt, was Mojo Miracle tun würde oder warum nicht; `repair.next` sagt, was die KI als Nächstes tut.
- **`run_mojo_miracle`** mit `{"code": "state.permissions"}`: repariert ein Problem, dessen `repair.how` `miracle`
  ist. Jeder Aufruf fragt zuerst dich; die Frage zeigt den Plan (den vollen Pfad nur im eigenen Fenster des
  Frameworks). Es gibt kein „nicht mehr fragen“ und keine Vollmacht dafür. Es löscht nie. Jedes andere Problem wird
  vor der Frage abgelehnt, mit dem Grund. Das Ergebnis ist `repaired`, `still_present`, `already_fixed`,
  `plan_changed`, `failed` oder `refused`, mit dem Grund.
- Der Prompt **`check_installation`** sagt einer KI, wie sie die Befunde erklärt und dass sie „repariert“ nur sagt,
  wenn das Ergebnis es sagt.

Eine Vollmacht für harmlose Reparaturen kommt später, zusammen mit den Vollmachten.

## 7. Die alten Namen funktionieren weiter

- `llm-cli doctor` behält seine Optionen, seine Ausgabe und seine Exit-Codes. Es schreibt einen kurzen Hinweis auf
  stderr, der `llmcf mojo` nennt. `doctor --fix` macht die harmlosen Reparaturen (dieselben protokollierten
  Reparaturen wie Mojo Miracle); ein Problem, das danach noch da ist, wird mit dem Grund gezeigt.
  `doctor --fix --tier 2` zeigt wie bisher auch die Liste der Build-Images des Frameworks.
- Das Tool `run_doctor` bleibt aufrufbar, wird aber nicht mehr aufgelistet. Eine Einstellung für `run_doctor` gilt
  auch für `run_mojo_oracle` und umgekehrt; die strengere gewinnt.
- In Python exportiert `orchestrator.Core.doctor` alles aus `orchestrator.Core.mojo` weiter.
- `make doctor` bleibt neben `make mojo`.

## 8. Exit-Codes und JSON

Der Exit-Code ist 0 (kein Problem), 1 (Warnungen) oder 2 (Fehler). Nach Mojo Miracle ist es das Ergebnis der Prüfung
danach.

`llmcf mojo --json` schreibt die Schlüssel von `llm-cli doctor --json` (`exit_code`, `findings`, `fixed`,
`still_present`) plus `mode` und `summary`. Jeder Befund hat außerdem `where`, `why`, `repair_id` und `cannot`, und
jedes Problem hat `miracle` (`can`, `summary` und die Schritte in `plan`). Mit `--miracle` kommen `repaired`,
`already_gone`, `not_repaired` (jeweils mit Grund) und `new_problems` dazu, und `findings` sind die der Prüfung
danach. Dann bedeuten `fixed` und `still_present` dasselbe wie bei `llm-cli doctor --fix --json`: die Reparaturen, die
Mojo Miracle gemacht hat, und die, die es versucht hat und die noch da sind.
