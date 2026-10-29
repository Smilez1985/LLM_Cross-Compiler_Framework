Version 3.2.0 · Stand 2026-10-29

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
llmcf mojo --oracle --board "Kitchen board"   # dieses eine Board auch sofort prüfen
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

Endet Mojo Miracle nach Mojo Oracle im Terminal (dein `y` hier oder `llmcf mojo --miracle`, während ein früheres
`llmcf mojo` im Terminal noch offen ist), zeigt die Konsole Mojos Kristallkugel und darunter „[Mojo] Miracle managed.
The oracle is pleased.“, wenn es mindestens ein Problem repariert hat, keines übrig ist und nichts Neues dazukam, oder
„[Mojo] 404 - miracle not found“, wenn es nichts zu reparieren gab. Jedes andere Ergebnis zeigt kein Logo, ebenso
wenig `--json`, ein Lauf ohne Terminal, die KI-Tools oder die GUI. Der offene Oracle-Lauf ist eine kleine private
Notiz neben der Sperre von Mojo Miracle in deinem persönlichen Ordner (`mojo/oracle_open.json`, ohne Pfad und ohne
Kontonamen darin). Sie verfällt nach einer Stunde (eine ältere Notiz oder eine, die sich nicht lesen lässt, zählt
als nicht offen und wird entfernt), und jeder deiner Läufe von Mojo Miracle entfernt sie an seinem Ende: im Terminal,
und ohne Logo auch `llmcf mojo --miracle --json`, `llm-cli doctor --fix` (mit oder ohne `--json`) und ein Lauf von
Mojo Miracle in der GUI. Ein Lauf von Mojo Miracle durch eine KI lässt sie stehen.

In der GUI: **Tools > Mojo-Gesundheitscheck...** startet Mojo Oracle beim Öffnen des Fensters und
zeigt dieselben Befunde mit einer Gesamtzeile für den Exit-Code; **Mojo Miracle ausführen...** stellt dieselbe Frage
(Nein ist die Vorgabe), öffnet die Build-Images des Frameworks im Löschdialog und zeigt, was repariert wurde, was nicht
und warum. Fenster und Befehl rufen dieselben Funktionen auf.

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

Es prüft Python, Docker und buildx (und nennt die Container-Engine: Unter Podman, dem Ersatzweg unter Rocky Linux,
AlmaLinux und Oracle Linux, nennt `docker.engine`, was Podman nicht kann, und den Weg zu Docker CE; siehe
[Funktionen](Features_DE.md#podman-statt-docker)), wo Docker seine Images ablegt, den freien Speicher für die Ordner
cache, output und den Build-Ordner, die Targets, den Zustandsordner, den Secrets-Speicher und den Schlüsselbund, die
Tool-Berechtigungen, die MCP-Token und das Audit-Log, den Chat-Feed, die Kalibrierdaten, die Community-Pakete, die
Wissens-Snapshots im Vergleich zum freien Platz ihrer Platte, den Speicher der Wissensbasis, die lokale
llama.cpp-Laufzeit (im Docker-Modus: ob Dittos Image da ist, auf diesem Rechner gebaut und geprüft) und die Boards,
denen du vertraust.

### Der Speicher der Wissensbasis

Die Wissensbasis liegt in Qdrant Edge; eine im Speicher früherer Versionen wird bei ihrer ersten Nutzung umgewandelt.
Mojo liest den Eintrag dieser Umwandlung und öffnet nichts (`knowledge.storage`):

- eine Umwandlung, die fehlgeschlagen ist, ist eine Warnung: Die Wissensbasis nutzt noch den alten Speicher und
  arbeitet weiter. Die Zeile nennt den Grund und wie du es erneut versuchst: **Tools > Knowledge Snapshots... >
  Umwandlung erneut versuchen** in der GUI oder `llm-cli knowledge storage convert` im Terminal;
- eine Wissensbasis in Qdrant Edge, deren Engine sich nicht laden lässt (ein fehlendes oder kaputtes Paket), ist ein
  Fehler: Sie wird nicht geöffnet, und der alte Ordner springt nie für sie ein. Installiere die Abhängigkeiten des
  Frameworks erneut;
- der alte Speicherordner, der nach einer Umwandlung bleibt, ist eine Information mit vollem Pfad und Größe. Nichts
  löscht ihn von selbst, auch Mojo Miracle nicht: **Tools > Knowledge Snapshots... > Alten Speicher löschen...** in der
  GUI oder `llm-cli knowledge storage delete-old` zeigen ihn und fragen dich vorher;
- auf einem Rechner ohne Build von Qdrant Edge (Windows auf ARM) sagt eine Information, dass die Wissensbasis den
  älteren Speicher behält.

### Die Boards, denen du vertraust

Mojo nimmt keinen Netzwerkkontakt zu einem Board auf, wenn du es nicht darum bittest. Eine Prüfung hat nur Sinn, wo es
ein Problem zu finden gibt, also meldet Mojo, was das Framework aufgezeichnet hat, nur aus Dateien:

- einen Host-Schlüssel in den `known_hosts` des Frameworks, der sich geändert hat (`device_host_key_changed`, eine
  Warnung) oder der fehlt (eine Warnung: vertraue dem Board noch einmal);
- den letzten Kontakt mit einem Board, der gescheitert ist: ein Deployment, ein Laufzeit-Deployment, eine Prüfung, ein
  Benchmark oder eine Hardware-Prüfung, die das Board nicht erreicht oder sich nicht angemeldet haben, mit dem
  Grund-Code und dem nächsten Schritt: `device_host_down` (Information: nichts hat geantwortet, das Board war
  vielleicht ausgeschaltet), `device_port_closed`, `device_auth_failed` oder `device_host_key_changed` (Warnungen),
  und wann es war;
- was das letzte Deployment auf dem Board gesehen hat (einen alten Qdrant-Sidecar).

Jeder Kontakt des Frameworks mit einem Board hält fest, was er gefunden hat; ein späterer Kontakt, der das Board
erreicht und sich anmeldet, beendet die Aufzeichnung, ebenso wenn du dem Board noch einmal vertraust. Boards ohne
aufgezeichnetes Problem bekommen eine Zeile, die das sagt.

Für ein Board, auf das ein aufgezeichneter Fakt zeigt, bietet Mojo die Prüfung an. Im Terminal fragt es:

```
Check Kitchen board now? (one connection to its SSH port, no login) [y/N]:
```

Mit `y` öffnet Mojo eine Verbindung zum SSH-Port des Boards und zeigt das Ergebnis. Nein ist die Vorgabe: Dann wird
nichts kontaktiert, und Mojo zeigt den Befehl für später. Ohne Terminal zeigt es nur den Befehl.

`llmcf mojo --oracle --board NAME` prüft ein Board sofort. NAME ist die Bezeichnung des Boards, seine Geräte-ID oder
seine Adresse. Mojo meldet sich nie an, schickt kein Passwort und nennt kein Gerätekonto. Ein falsches Passwort oder
ein fehlender Schlüssel des Frameworks (`device_auth_failed`) zeigt sich erst beim nächsten Deployment oder
Benchmark; ist so ein Fehler aufgezeichnet und antwortet das Board jetzt, sagt Mojo, dass es die Anmeldung nicht prüfen
kann. Ein geprüftes Board behält sein aufgezeichnetes Problem, bis ein Deployment, eine Prüfung oder ein Benchmark es
wieder erreicht, denn Mojo Oracle schaut nur und schreibt nichts.

Auch in der GUI nimmt das Mojo-Fenster von sich aus keinen Kontakt zu einem Board auf: Ein Board, auf das ein
aufgezeichneter Fakt zeigt, bekommt einen Knopf **<Board> jetzt prüfen**, und **Ein Board prüfen...** prüft ein
beliebiges vertrautes Board, das du an Bezeichnung und Adresse auswählst (zwei Boards, die gleich aussehen, etwa ein
neu aufgesetztes Board mit neuem Host-Schlüssel, zeigen auch ihre Geräte-ID). **Dieses Board prüfen** im
Deployment-Tab (Gruppe „Benchmark auf einem Board“) prüft das dort gewählte Board. Jedes ist dieselbe einzelne Verbindung zum
SSH-Port des Boards, ohne Anmeldung, und zeigt den Befund des Boards mit seinem nächsten Schritt.

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
| braucht dich | Nur du kannst das: deine Schlüssel, die Tool-Berechtigungen, das Audit-Log, deine Einstellungen und Dateien. Eine KI ändert sie nie. | `secrets.store`, `secrets.storage`, `tools.permissions`, `tools.tokens`, `tools.audit`, `chat_feed.store`, `targets.none`, `devices.<Geräte-ID>` (ein Board, das SSH ablehnt oder dessen Host-Schlüssel sich geändert hat), `knowledge.storage` (eine fehlgeschlagene Umwandlung der Wissensbasis oder Qdrant Edge, das sich nicht laden lässt), ein zurückgezogenes Community-Paket (seinen Ordner löschst du selbst) |
| braucht einen Administrator | Es ändert das System außerhalb des Frameworks. | `docker.daemon`, `docker.buildx`, `docker.engine`, `python.version`, `secrets.keyring`, `runtime.docker.unavailable` |
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
  Es nimmt keinen Kontakt zu einem Board auf. Für ein Board, auf das ein aufgezeichneter Fakt zeigt, nennt
  `check_board` die Anfrage, die es prüft. `{"board": "Kitchen board"}` prüft dieses eine vertraute Board sofort:
  eine Verbindung zu seinem SSH-Port, keine Anmeldung. Eine KI nennt ein Board mit seiner Bezeichnung oder Geräte-ID
  (`list_devices` zeigt beide), nie mit einer Adresse. Ein Board, dem du keinen eigenen Namen gegeben hast, hat seine
  Adresse als Bezeichnung; eine KI nennt es mit seiner Geräte-ID, und `check_board` tut das auch.
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
danach. Der Befund eines Boards, auf das ein aufgezeichneter Fakt zeigt, hat `check_board` (das Board, seine Geräte-ID
und den Befehl, der es prüft); `--json` nimmt keinen Kontakt zu einem Board auf, außer `--board` nennt eines. Dann bedeuten `fixed` und `still_present` dasselbe wie bei `llm-cli doctor --fix --json`: die Reparaturen, die
Mojo Miracle gemacht hat, und die, die es versucht hat und die noch da sind.
