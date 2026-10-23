Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md) · Zuletzt aktualisiert am 2026-10-17

[English](CLI_Usage.md) · **Deutsch**

# Kommandozeile (`llm-cli`)

Die Kommandozeile kann alles, was du auf einem Server ohne Bildschirm, in Skripten und in der CI brauchst: die
Installation prüfen, Modelle konvertieren, sie übertragen, Ditto und sein lokales Modell einrichten,
Tool-Berechtigungen verwalten, den MCP-Server betreiben und Community-Pakete teilen oder installieren. Jeder Befehl
und jede Option unten stammt aus der echten `--help`-Ausgabe dieser Version.

Zurück zur [README](../README_DE.md) · Funktionen im Detail: [Features_DE.md](Features_DE.md)

---

## Inhalt

- [Die CLI aufrufen](#die-cli-aufrufen)
- [Globale Optionen, Ausgabe und Exit-Codes](#globale-optionen-ausgabe-und-exit-codes)
- [Überblick über alle Befehle](#überblick-über-alle-befehle)
- [mojo](#mojo): Gesundheitscheck (Mojo Oracle) und Reparaturen (Mojo Miracle)
- [doctor](#doctor): der alte Name von `mojo`
- [knowledge](#knowledge): `snapshots` (`list`, `prune`, `delete`, `export`)
- [targets](#targets): `list`, `validate`
- [build](#build): `start`, `save`, `status`
- [deploy](#deploy): `list-artifacts`, `package`, `run`
- [ditto](#ditto): `login`, `logout`, `auth`, `setup`, `ask`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [mcp](#mcp): `serve`, `config`, `token add|list|rotate|revoke`
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): Hardware-Probe
- [device](#device): `list`, `settings`, `runtime`
- [config](#config): `show`, `output-folder`, `repos`
- [zoo](#zoo): `list`, `show`, `install`, `verify`, `export`, `keys`
- [trash](#trash): `list`, `restore`, `empty`
- [Bekannte Einschränkungen](#bekannte-einschränkungen)

---

## Die CLI aufrufen

| Installation | Befehl |
|---|---|
| Linux-Installer (`install.sh`) | `cd /opt/llm-cross-compiler-framework`, dann `.venv/bin/python -m orchestrator.cli ...` |
| Windows-Installer (`install.bat`) | `cd /d "C:\Program Files\LLM-Cross-Compiler-Framework"`, dann `.venv\Scripts\python.exe -m orchestrator.cli ...` |
| Entwicklungsumgebung (`uv sync`) | `llm-cli ...` |

`python -m orchestrator.cli` braucht den Installationsordner im Suchpfad von Python. Ruf es entweder in diesem
Ordner auf oder setze `PYTHONPATH`. Unter Linux funktioniert dieser Alias aus jedem Ordner:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-cross-compiler-framework /opt/llm-cross-compiler-framework/.venv/bin/python -m orchestrator.cli'
```

Die Beispiele unten verwenden `llm-cli`. `llmcf` ist dasselbe Programm unter seinem neuen Namen (unveröffentlicht):
Die Entwicklungsumgebung und der Framework-Container haben beide Befehle, und `llmcf mojo` tut genau dasselbe wie
`llm-cli mojo`.

### Umgebungsvariablen

| Variable | Wirkung |
|---|---|
| `LLM_FRAMEWORK_CONFIG_DIR` | Einstellungsordner (`config.yml`). Standard: `configs/` in der Installation, wenn beschreibbar, sonst `~/.llm-framework`. Das Arbeitsverzeichnis wird nie verwendet. |
| `LLM_FRAMEWORK_HOME` | Ordner für den persönlichen Zustand. Standard: `~/.llm-framework`. Darfst du im Installationsordner nicht schreiben (zum Beispiel `C:\Program Files`), liegen auch die Datenordner `output/`, `cache/` und `models/` dort. |
| `LLM_FRAMEWORK_MODELS_DIR` | Modellordner; Dittos lokale Modelle liegen im Unterordner `ditto/`. Standard: `models/` in der Installation, wenn beschreibbar, sonst im persönlichen Ordner. |
| `LLM_FRAMEWORK_<SCHLÜSSEL>` | Überschreibt eine Einstellung für diesen Aufruf, z. B. `LLM_FRAMEWORK_AI_PROVIDER=local` oder `LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`. Es zählen nur bekannte Einstellungsnamen. |
| `LLM_SECRET_STORAGE` | Wie der Secrets-Tresor für diesen Aufruf geschützt wird, z. B. `ephemeral` in der CI (siehe [secrets](#secrets)). |
| `OPENAI_CUSTOM_HEADERS`, `ANTHROPIC_CUSTOM_HEADERS` | Werden beim Start mit einer Warnung entfernt, damit sie den gespeicherten Schlüssel nicht ersetzen können; die Einstellung `ai_allow_sdk_env_headers: true` behält sie. |
| `ANTHROPIC_LOG`, `OPENAI_LOG` | `debug` wird auf `info` gesenkt, außer `--debug-http` ist angegeben. |

---

## Globale Optionen, Ausgabe und Exit-Codes

```
llm-cli [OPTIONEN] BEFEHL [ARGUMENTE]...
```

| Option | Bedeutung |
|---|---|
| `--version` | Version ausgeben und beenden (in der GUI: **Help > About**). |
| `-c, --config PATH` | Diese Einstellungsdatei verwenden (YAML oder JSON, muss existieren). Ihr Ordner wird für diesen Aufruf zum Einstellungsordner (eine `config.yml` dort wird ebenfalls gelesen), und die Werte der Datei haben Vorrang davor. Der übliche Einstellungsordner wird dann nicht gelesen. Eine Datei, die sich nicht lesen lässt, wird auf stderr genannt und übergangen; die übrigen Einstellungen (jene `config.yml`, `LLM_FRAMEWORK_*`-Variablen, Standardwerte) gelten weiter. |
| `-v, --verbose` | Log-Level `DEBUG`. |
| `-q, --quiet` | Log-Level `ERROR` und kein Start-Banner. |
| `--log-level [DEBUG\|INFO\|WARNING\|ERROR]` | Log-Level (Standard `INFO`). |
| `--debug-http` | Die Debug-Ausgabe der HTTP-Bibliotheken und der SDKs der KI-Anbieter auf stderr zeigen und `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` beibehalten. Ohne die Option bleiben diese Logger auch mit `--verbose` auf `INFO`, und die beiden Variablen werden auf `info` gesenkt. |
| `--help` | Hilfe für die CLI, eine Gruppe oder einen Befehl, z. B. `llm-cli ditto runtime start --help`. |

Globale Optionen stehen vor dem Befehl: `llm-cli -q doctor`, nicht `llm-cli doctor -q`.

Zugangsdaten (API-Schlüssel, Tokens, `Authorization`-, `x-api-key`- und Cookie-Header, Anmeldecodes und Tokens in
URLs) werden in jeder Logzeile und jedem Traceback geschwärzt, auch mit `--debug-http`.

**Ausgabe:** Das Start-Banner und Log-Meldungen gehen nach stderr, Ergebnisse nach stdout, ebenso die meisten
Meldungen, die ein Befehl selbst ausgibt, auch seine Fehlermeldungen. Bei `llm-cli ditto ask` geht nur die Antwort
nach stdout, die Befehle zur Anmeldung (`ditto login`, `logout`, `auth`) geben Fehler, Warnungen und Fragen auf
stderr aus, und bei `llm-cli mcp serve` trägt stdout das MCP-Protokoll.

**Exit-Codes:**

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Der Befehl ist gescheitert (zum Beispiel ein gescheiterter Build, eine gescheiterte Übertragung, ein Paket, das sich nicht erstellen ließ, ein Geheimnis, das sich nicht speichern ließ, eine Hardware-Probe, die keine Datei geschrieben hat, ein beschädigtes Audit-Log, ein KI-Fehler) |
| 2 | Ungültige Eingabe: unbekannte Option, ein unbekanntes Target oder eine nicht unterstützte Kombination aus Format, Board und Quantisierung (`build start`), ein unbekannter Anbieter (`--provider`), eine unbekannte Modellstufe (`ditto models download`), kein passender Evaluationsfall (`ditto eval`), eine fehlende Paket-ID, Datei oder Quelle (`zoo install`, `zoo export`), eine Schlüsseldatei ohne verwendbaren Schlüssel oder keine Textdatei (`--to`, `--from`), `--from` für ein Paket, das nicht verschlüsselt ist (`zoo install`), `--to` zusammen mit `--passphrase` (`zoo export`) |
| 3 | Anmeldung: Der Anbieter hat die Zugangsdaten abgelehnt (`ditto login`, `ditto auth status --check`) |
| 4 | Anmeldung: Anbieter oder lokaler Server nicht erreichbar |
| 5 | Der Tresor für Zugangsdaten lässt sich nicht nutzen (kein nutzbarer Schlüsselbund, keine Passphrase, ein beschädigter Tresor); die Meldung nennt die Möglichkeiten. Jeder Befehl, der den Tresor braucht, verwendet ihn |
| 6 | Anmeldung: Das Verfahren ist in dieser Version nicht verfügbar, oder der Anbieter wurde entfernt |
| 130 | `build start` wurde mit Strg+C unterbrochen (der Build wurde vorher abgebrochen) |

`llm-cli mojo` und `llm-cli doctor` verwenden 0 (OK), 1 (Warnungen) und 2 (Fehler); `llm-cli ditto eval --min-pass`
liefert 1, wenn ein Lauf unter dem angegebenen Anteil bleibt.

---

## Überblick über alle Befehle

| Befehl | Was er tut |
|---|---|
| `mojo` | Die Installation prüfen (Mojo Oracle) und reparieren, was sich sicher reparieren lässt (Mojo Miracle) (unveröffentlicht) |
| `doctor` | Der alte Name von `mojo`, mit seinen Optionen aus 3.0.0 |
| `knowledge snapshots` | Die Snapshots der Wissensbasis mit vollem Pfad, Namen, Größe und was jeder ist auflisten; die ausgewählten löschen oder exportieren (unveröffentlicht) |
| `knowledge index-docs` | Die eigene Dokumentation des Frameworks (und mit `--with-code` seinen Code) in die Wissensbasis aufnehmen (unveröffentlicht) |
| `knowledge storage` | Wo die Wissensbasis liegt (Qdrant Edge), die Umwandlung einer älteren und ihr alter Ordner: `convert`, `delete-old` (unveröffentlicht) |
| `trash list` / `restore` / `empty` | Was Löschungen in den Papierkorb verschoben haben; Einträge zurücklegen; die ausgewählten endgültig löschen (unveröffentlicht) |
| `targets list` / `targets validate` | Hardware-Targets mit ihren Boards und Formaten auflisten / prüfen, ob ein Target lädt und seine Capabilities gültig sind |
| `build start` / `build status` | Ein Modell konvertieren und quantisieren / Builds zeigen, auch solche, die die GUI, der MCP-Server oder ein anderer CLI-Aufruf gestartet hat |
| `build save` | Einen Build, der im Build-Ordner des Frameworks wartet, im Ausgabeordner oder einem anderen Ordner speichern (unveröffentlicht) |
| `deploy list-artifacts` / `deploy package` / `deploy run` | Fertige Builds auflisten / einen Build in eine ZIP-Datei packen / sie auf ein Board laden und installieren |
| `ditto login` / `ditto logout` | Ditto bei einem Anbieter anmelden (der Schlüssel verdeckt abgefragt, von stdin oder aus einer Umgebungsvariable, vor dem Speichern geprüft) / abmelden |
| `ditto auth status/methods/storage/rotate-key` | Anmeldestand aller Anbieter / die Anmeldeverfahren / wie der Tresor geschützt ist / ein neuer Schlüssel für den Tresor |
| `ditto setup` | Die Tool-Berechtigungen festlegen (dieselbe Auswahl wie im Dialog beim ersten Start der GUI) |
| `ditto ask` | Ditto eine Frage stellen |
| `ditto eval` | Ditto anhand fester Testfälle bewerten, oder die Suche der Wissensbasis |
| `ditto runtime install/start/status/stop/import/export/prune` | Der verwaltete lokale Modellserver (llama.cpp, auf diesem Rechner gebaut) für den Offline-Modus |
| `ditto runtime ensure` | Dittos lokalen Modellserver installieren, wenn er fehlt, direkt oder in Docker; `--force` wechselt die Art |
| `ditto runtime export-source/import-source` | Den festgelegten llama.cpp-Quelltext als Git-Bundle auf einen Rechner ohne Netz tragen (am Commit geprüft) |
| `ditto models list/download` | Die Modellstufen für den Offline-Modus |
| `tools list` / `tools permissions` / `tools audit` | Die Tools des Frameworks, ihre Berechtigungen und das Audit-Log |
| `mcp serve` / `mcp config` | Den MCP-Server betreiben / zeigen, wie man KI-Clients verbindet |
| `secrets set` / `secrets list` | Ein Token speichern (Hugging Face, GitHub) / die gespeicherten Namen auflisten |
| `module create` / `module generate-ai` | Ein neues Hardware-Target anlegen, per Fragen oder mit Ditto aus einer Probe-Datei |
| `scan` | Die Hardware-Probe auf diesem Rechner oder mit `--device` auf einem Board per SSH ausführen, oder mit `--import` die Probe-Datei eines Boards ablegen, und ihre Probe-ID zeigen |
| `device list` / `device settings` | Die untersuchten Boards / wie die Laufzeit auf einem Board installiert wird (Startskript oder systemd), vor einem Deployment entschieden |
| `device runtime plan/build/deploy/verify/status` | Die selbst gebaute llama.cpp-Laufzeit eines Boards: aus seinem Profil planen, cross-kompilieren, per SSH installieren und dort prüfen |
| `config show` / `config repos` | Die wirksamen Einstellungen und ihre Herkunft zeigen / Überschreibungen für Quell-Repositorys verwalten |
| `config output-folder` | Den Ausgabeordner zeigen oder festlegen, in dem fertige Modelle gespeichert werden (unveröffentlicht) |
| `zoo list` / `zoo show` | Den Community-Zoo und die installierten Pakete auflisten / einen Eintrag zeigen |
| `zoo install` / `zoo verify` | Ein Community-Paket nach Prüfungen und deiner Bestätigung installieren / installierte Pakete oder eine Paketdatei prüfen |
| `zoo export` / `zoo keys` | Ein signiertes Paket aus deinem Target, Build oder deiner Wissensbasis machen (und einen Pull-Request öffnen) / deine öffentlichen Schlüssel zeigen |

---

## mojo

```
llm-cli mojo [--oracle | --miracle] [--json] [--board NAME]
```

Der Gesundheitscheck (unveröffentlicht; er hieß `doctor`). **Mojo Oracle** (Standard) schaut nur: Es prüft Python, den
Docker-Daemon und buildx, den freien Speicherplatz für den Cache-, den Ausgabe- und den Build-Ordner, die Targets und
ihre Capabilities, ob der persönliche Ordner privat ist, den Secrets-Speicher und den Schlüsselbund, die
Tool-Berechtigungen, die MCP-Token, das Audit-Log, den Chat-Verlauf (er lässt sich schreiben, seine neuesten Einträge
stimmen), die mitgelieferten Kalibrierungsdaten, die installierten Community-Pakete (zurückgezogen, verändert oder aus
einer Datei importiert), die Snapshots der Wissensbasis (wie viele, ihre Größe im Vergleich zum freien Platz ihrer
Platte und wo; es gibt keine Grenze, ihre Zahl ist also nie eine Warnung), die lokale llama.cpp-Laufzeit (im
Docker-Modus: Dittos Image) und die Boards, denen du vertraust. Es sagt auch, wo Docker seine Images ablegt und wie viel die Build-Images des Frameworks belegen (diesen Ordner zu verlegen ist ein
Schritt für Administratoren, siehe [Container-Einrichtung](Container_Setup_DE.md#10-wo-docker-images-ablegt)).

**Boards:** Mojo nimmt keinen Netzwerkkontakt zu einem Board auf, wenn du es nicht darum bittest. Es meldet, was das
Framework aufgezeichnet hat: einen Host-Schlüssel in seinen `known_hosts`, der sich geändert hat oder fehlt, und das
letzte Deployment, Laufzeit-Deployment, die letzte Prüfung, den letzten Benchmark oder die letzte Hardware-Prüfung, die
ein Board nicht erreicht haben, mit dem Grund-Code (`device_host_down`, `device_port_closed`, `device_auth_failed`,
`device_host_key_changed`) und dem nächsten Schritt. Für ein Board, auf das so ein Fakt zeigt, fragt es im Terminal
„Check <board> now? [y/N]“; mit `y` öffnet es eine Verbindung zum SSH-Port des Boards (keine Anmeldung, kein Passwort)
und zeigt das Ergebnis. Ohne Terminal zeigt es stattdessen den Befehl. `--board NAME` prüft ein Board sofort.

Zu jeder Warnung und jedem Fehler schreibt es, was es gefunden hat, wo (den vollen Pfad oder das Objekt), warum das
wichtig ist, einen Hinweis und ob Mojo Miracle es reparieren kann, mit dem Plan einer Reparatur (was sich ändert,
vorher und nachher, ob es sich rückgängig machen lässt). Ein Satz am Ende zählt jedes Problem danach, was sich tun
lässt. Kann Mojo Miracle bei einem Problem etwas tun, fragt es im Terminal „Run Mojo Miracle now? [y/N]“; mit `y` läuft
Mojo Miracle, ohne dass der Befehl noch einmal getippt wird, und Nein (die Vorgabe) ändert nichts.

**Mojo Miracle** macht nur harmlose Reparaturen: Es schränkt den Zustandsordner auf deinen Benutzer ein, oder es lässt
Dittos Server einen intakten gespeicherten llama.cpp-Build starten, wenn der aktive fehlt. Jede Reparatur ist ein Plan,
an seinen Hash gebunden, im Audit-Log festgehalten, bevor sie läuft (sonst ändert sich nichts), nur angewendet,
solange sich seit dem Plan nichts geändert hat (ein Ordner wird geöffnet, ohne einem Link zu folgen), und danach
erneut geprüft; es läuft immer nur ein Mojo Miracle. Speicherplatz freigeben heißt löschen: Im Terminal zeigt Mojo
Miracle die Build-Images des Frameworks (Name, Image-ID, Größe, was jedes ist; nichts ausgewählt) und löscht genau die,
deren Nummern du eingibst und bestätigst (die Vorgabe ist Nein); der Docker-Build-Cache wird nie geleert. Danach
meldet es jedes Problem: repariert (erneut geprüft), schon weg, noch da oder nicht repariert mit dem Grund (Löschen
braucht deine Liste, braucht dich, braucht einen Administrator, nicht automatisch reparierbar, fehlgeschlagen). Ein
Problem, das die Prüfung danach zum ersten Mal findet, wird als neu genannt. Einzelheiten: [Mojo_DE.md](Mojo_DE.md).

| Option | Bedeutung |
|---|---|
| `--oracle` | Nur schauen und melden, was gefunden wurde (Standard); im Terminal bietet es danach Mojo Miracle an. |
| `--miracle` | Reparieren, was sich reparieren lässt, dann melden, was repariert wurde, was nicht und warum. Ohne Terminal (eine Pipe, ein Skript, eine KI-App) laufen nur die harmlosen Reparaturen, und nichts wird gelöscht. |
| `--json` | Maschinenlesbare Ausgabe: die Schlüssel von `doctor --json` plus `mode`, `summary` und je Befund `where`, `why`, `repair_id`, `cannot` und `miracle`, und `check_board` (das Board, seine Geräte-ID und der Befehl, der es prüft) für ein Board, auf das ein aufgezeichneter Fakt zeigt; mit `--miracle` auch `repaired`, `already_gone`, `not_repaired` und `new_problems`. Fragt nie, löscht nie und nimmt keinen Kontakt zu einem Board auf. |
| `--board NAME` | Dieses eine vertraute Board sofort prüfen: seine Bezeichnung, Geräte-ID oder Adresse (eine Verbindung zu seinem SSH-Port, keine Anmeldung). Nur mit `--oracle`. Ein Name, der zu keinem oder zu mehreren Boards passt, listet die vertrauten Boards auf und endet mit 2. |

Speicherplatz: Eine Regel für jede Prüfung (die Speicherplatz-Regel des Frameworks): Eine Festplatte behält ihre Reserve frei, 2 GiB oder 2 % der Festplatte, je nachdem, was größer ist, nach dem, was laufende Builds und Downloads dort reserviert haben. Weniger als die Reserve ist ein Fehler, weniger als die doppelte Reserve eine Warnung.

```bash
llmcf mojo
llmcf mojo --miracle
llmcf mojo --oracle --board "Kitchen board"
llm-cli mojo --json > mojo.json
```

In der GUI: **Tools > Mojo-Gesundheitscheck...** startet Mojo Oracle beim Öffnen (**Erneut prüfen** startet es noch
einmal) und zeigt jeden Befund so, wie der Befehl ihn ausgibt: die Schwere als Text mit Zeichen und Symbol, den
Bereich, die Meldung, wo, warum es wichtig ist, den nächsten Schritt, was Mojo Miracle tun kann, mit den Schritten
seines Plans, den zusammenfassenden Satz und eine Gesamtzeile mit dem Exit-Code. **Mojo Miracle ausführen...**
(`--miracle`) fragt „Mojo Miracle jetzt ausführen?“ (Nein ist die Vorgabe), wendet genau die gezeigten Pläne an und
öffnet die Build-Images des Frameworks im Löschdialog (die ganze Liste, nichts angehakt); danach zeigt es, was
repariert wurde, was nicht und warum. Jedes Board, auf das ein aufgezeichneter Fakt zeigt, bekommt **<Board> jetzt
prüfen**, und **Ein Board prüfen...** (`--board`) prüft ein beliebiges vertrautes Board, das du auswählst
(Bezeichnung und Adresse; zwei Boards, die gleich aussehen, etwa ein neu aufgesetztes Board mit neuem
Host-Schlüssel, zeigen auch ihre Geräte-ID): eine Verbindung zu seinem SSH-Port, keine Anmeldung. **Dieses Board prüfen** im
Deployment-Tab (Gruppe „Benchmark auf einem Board“) prüft das gewählte Board genauso.

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

Der alte Name von [mojo](#mojo); Optionen, Ausgabe und Exit-Codes bleiben wie in 3.0.0, und ein kurzer Hinweis auf
stderr nennt `llmcf mojo`.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe (Befunde, Korrekturen, Exit-Code). Mit `--fix --tier 2` listet sie auch die Build-Images des Frameworks und wo Docker sie ablegt (`framework_images`), löscht sie aber nie. |
| `--fix` | Die automatischen Korrekturen bis `--tier` anwenden, dann erneut prüfen. Das sind die harmlosen, protokollierten Reparaturen von Mojo Miracle (unveröffentlicht); ein Problem, das noch da ist, wird mit dem Grund gezeigt. |
| `--tier INTEGER RANGE` | `1` (Standard): harmlose Korrekturen (macht den persönlichen Ordner privat). `2`: listet auch jedes Build-Image des Frameworks (die ohne Namen aus früheren Builds, die benannten Images `llm-framework/<Target>:last-build` und die Builder-Images der Laufzeit; Name, Image-ID, Größe und was jedes ist), nichts ausgewählt. Du gibst die Nummern der zu löschenden ein und bestätigst genau diese im Terminal (die Vorgabe ist Nein). Ohne Terminal (eine Pipe, ein Skript, eine KI-App) listet es sie nur. **Tools > Framework Build Images...** in der GUI ist dieselbe Liste. Der Docker-Build-Cache wird nie geleert: Er lässt sich nicht auf das Framework beschränken. |

```bash
llm-cli doctor
llm-cli doctor --json > doctor.json
llm-cli doctor --fix --tier 2
```

In der GUI: **Tools > Mojo-Gesundheitscheck...** (die Prüfung; **Mojo Miracle ausführen...** macht die Reparaturen
von `--fix`) und **Tools > Framework Build Images...** (die Liste von `--tier 2`).

---

## knowledge

### knowledge snapshots

```
llm-cli knowledge snapshots [list]
llm-cli knowledge snapshots prune
llm-cli knowledge snapshots delete NAME...
llm-cli knowledge snapshots export NAME... --to ORDNER
```

Unveröffentlicht. Vor jedem Deep Ingest, jeder Aufnahme der eigenen Dokumente des Frameworks (`knowledge index-docs`),
jeder Wiederherstellung und jedem Import eines Wissens-Packs wird die Wissensbasis als Snapshot in
`knowledge_snapshots/` des persönlichen Ordners gesichert, damit sich die Änderung rückgängig machen lässt. Bevor ein
Snapshot angelegt wird, wird seine Größe geschätzt: passt er nicht auf die Platte (neben der Reserve, die immer frei
bleibt), wird kein Snapshot angelegt, und die Änderung läuft auch nicht. `list` (die Vorgabe) zeigt jeden Snapshot,
den ältesten zuerst, mit Namen, Zeitpunkt, Größe, was er ist (zum Beispiel "backup of the knowledge base taken before
a Deep Ingest") und vollem Pfad. Am Ende steht, was die Snapshots im Vergleich zum freien Platz dieser Platte belegen,
und ob der nächste Snapshot (etwa so groß wie der neueste) nicht mehr passen oder wenig Platz übrig lassen würde. Es
löscht nichts. Die GUI zeigt dieselbe Liste unter **Tools > Knowledge Snapshots...**, mit einem Kästchen für jeden
(keines angehakt).

Es gibt keine Grenze: Das Framework behält so viele Snapshots, wie die Platte fasst, löscht keinen von selbst, und
keine Anzahl bringt eine Erinnerung oder eine Warnung. Um Platz zu schaffen:

- `prune` listet jeden Snapshot mit einer Nummer (nichts ist ausgewählt). Du gibst die Nummern der zu löschenden ein
  (zum Beispiel `1,3` oder `2-4`), wählst `trash` (die Vorgabe: du kannst sie mit [`trash restore`](#trash)
  wiederherstellen) oder `permanent` und bestätigst genau diese (die Vorgabe ist Nein).
- `delete NAME...` listet genau die genannten Snapshots und fragt genauso.
- `export NAME... --to ORDNER` kopiert die Snapshots in einen vorhandenen Ordner (auf diesem Computer, einen
  USB-Stick, eine Netzwerkfreigabe) und vergleicht jede Kopie per sha256 mit dem Original. Eine gleichnamige Datei
  dort wird nie überschrieben; eine Kopie, die nicht stimmt, wird wieder entfernt und gemeldet. Ein Export löscht nie
  einen Snapshot. Exit-Code 1, wenn ein Snapshot nicht exportiert wurde.

Löschen braucht ein Terminal: Ohne eines (eine Pipe, ein Skript, eine KI-App) wird die Liste ausgegeben und nichts
gelöscht. Jedes Löschen wird im Audit-Log festgehalten, bevor etwas verschoben oder gelöscht wird.

In der GUI: **Tools > Knowledge Snapshots...**: dieselbe Liste, und die Zeile zur Festplatte sagt vorher mit denselben
Worten, ob der nächste Snapshot passt; **Angehakte löschen…** für `prune` und `delete` (die Löschliste, „Nein“ ist die
Vorgabe), **Angehakte exportieren…** für `export --to`.

```bash
llm-cli knowledge snapshots
llm-cli knowledge snapshots prune
llm-cli knowledge snapshots export pre_ingest_20261001_101500 --to /media/usb/backups
```

### knowledge index-docs

```
llm-cli knowledge index-docs [--with-code]
```

Unveröffentlicht. Nimmt die eigene Dokumentation des Frameworks (README, CONTRIBUTING und den Ordner `docs`, in beiden
Sprachen) in die Wissensbasis auf, damit Ditto Fragen zum Framework selbst beantworten kann und KI-Clients sie mit
`search_knowledge` (Art `docs`) finden. `--with-code` nimmt auch Code und Konfiguration auf (viel mehr Dateien, dauert
Minuten): die Shell-Skripte im obersten Ordner der Installation (`install.sh`, `entrypoint.sh`; nicht `install.bat`),
die Dateien `.py`, `.sh`, `.yml`, `.yaml`, `.json` und `.md` sowie Dockerfiles in `orchestrator`, `scripts` und
`Docker Setup`, die Targets, die das Framework mitliefert (`targets/_template` und die offiziellen Targets), und die
Dateien im obersten Ordner von `configs`. Dateien über 512 KB, Links, Zwischenspeicher und erzeugte Ordner bleiben
draußen, ebenso Namen, die mit einem Punkt beginnen.
Gelesen wird nur, was das Framework mitliefert: nie deine Einstellungen (`config.yml`), Targets, die du oder eine KI
angelegt habt, oder installierte Pakete. Das passiert nie von selbst: Nur dieser Befehl, **Tools > Index the
Framework's Documentation...** in der GUI (eine Frage mit „Nein“ als Vorgabe und einem Kästchen für den Code) oder das
MCP-Tool `index_framework_docs` (jedes Mal gefragt) starten es. Vorher wird ein Snapshot angelegt, damit es sich mit
einer Wiederherstellung rückgängig machen lässt; eine schon aufgenommene Datei wird ersetzt. Hat die Platte keinen
Platz für diesen Snapshot, wird nichts aufgenommen, und der Befehl sagt, wie viel Platz fehlt (kein „trotzdem
weiter“); bleibt nur wenig Platz übrig, sagt er das und macht weiter. Der erste Snapshot wird an der Größe der
Wissensbasis selbst gemessen, und ein Snapshot, der sich nicht schreiben lässt, hält die Aufnahme ebenfalls an. Eine
Datei, die gelesen, aber nicht aufgenommen wurde (eine leere Datei, ein Fehler), wird mit dem Grund genannt. Die
Wissensbasis muss eingeschaltet sein. Ein Wissenspaket, das du exportierst, enthält diese Texte nie: Jede Installation
nimmt sie selbst auf.

In der GUI: **Tools > Index the Framework's Documentation...** (das Kästchen der Frage ist `--with-code`).

```bash
llm-cli knowledge index-docs
llm-cli knowledge index-docs --with-code
```

### knowledge sources

```
llm-cli knowledge sources [--kind web|pack|docs|other|any]
```

Unveröffentlicht. Listet, was die Wissensbasis enthält: jede Quelle mit ihrer Art, der Lizenz, die sie nennt (eine
SPDX-ähnliche ID wie `CC-BY-4.0`, `other` für eine, die das Framework nicht erkennt, `-` für keine), ihrem
Urheberrechtshinweis und dem, was ein Wissenspaket von ihr enthielte, und warum: **full text** (ihre Lizenz erlaubt
die Weitergabe des Textes), **link and short facts** (ein Urheberrechtshinweis ohne eine solche Lizenz, Code ohne
Lizenz, eine unbekannte Lizenz) oder **not exported** (eine Seite eines installierten Wissenspakets, die eigenen
Dokumente des Frameworks). Adressen, Titel und Hinweise sind Text der Website und werden als reiner Text gezeigt. Die
Wissensbasis muss eingeschaltet sein.

In der GUI: **Tools > Wissensquellen...** (dieselben Spalten; der Filter **Art** ist `--kind`).

```bash
llm-cli knowledge sources
llm-cli knowledge sources --kind web
```

### knowledge storage

```
llm-cli knowledge storage [show]
llm-cli knowledge storage convert
llm-cli knowledge storage delete-old
```

Unveröffentlicht. Die Wissensbasis liegt in Qdrant Edge (`~/.llm-framework/qdrant_edge`). Eine Wissensbasis einer
früheren Version (`~/.llm-framework/qdrant`) wird bei ihrer ersten Nutzung umgewandelt; Vektoren und Texte werden
kopiert und geprüft, nichts wird neu eingebettet. `show` (die Vorgabe) liest den Eintrag dieser Umwandlung und öffnet
nichts: wann sie umgewandelt wurde, wie viele Auszüge, und den alten Ordner mit vollem Pfad und Größe; oder, wenn die
Umwandlung fehlschlug, warum und wann sie erneut versucht wird (der alte Speicher arbeitet so lange weiter). Die erste
Zeile nennt den Speicher, der wirklich genutzt wird: Ist ein Qdrant-Server eingestellt (`qdrant_url`), ist das der
Server, und der lokale Speicher wird nicht genutzt. `convert` wandelt sofort um, vor jedem Versuch oder nach einem
Fehler, der nicht von selbst erneut versucht wird; dafür muss die Wissensbasis eingeschaltet sein, und solange ein
Qdrant-Server eingestellt ist, lehnt es ab (die Wissensbasis würde den Server öffnen und nichts umwandeln).
`delete-old` zeigt den alten Ordner nach seiner Umwandlung und löscht ihn erst nach deiner Antwort in einem Terminal
(Vorgabe Papierkorb, oder endgültig; "Nein" ist die Vorgabe). Ohne
Terminal wird nichts gelöscht. Vor der Umwandlung oder solange sie fehlschlug, ist der alte Ordner die Wissensbasis
selbst und wird nicht angeboten.

In der GUI: **Tools > Knowledge Snapshots...**: die Zeile zum Speicher in jedem Zustand (`show`: Qdrant Edge, der alte
Speicher mit seinem Grund, ein Qdrant-Server, oder Qdrant Edge, das sich nicht laden lässt); **Jetzt umwandeln** (noch
nicht versucht) oder **Umwandlung erneut versuchen** (nach einem Fehler) für `convert`, unter derselben Bedingung
angeboten (nicht, solange ein Qdrant-Server eingestellt ist); **Alten Speicher löschen...** für `delete-old`.

```bash
llm-cli knowledge storage
llm-cli knowledge storage delete-old
```

---

## targets

### targets list

```
llm-cli targets list [-f table|json|yaml]
```

Listet die Targets im eingestellten Targets-Ordner auf. Die Tabelle zeigt für jedes Target die ID, den Namen (zum
Beispiel „Rockchip Family“), die Architektur sowie die Boards und Formate, die sein Block `capabilities:` angibt; ein
Target ohne diesen Block ist mit „no capabilities declared“ markiert. **Der Wert für `--target` ist die ID** (zum
Beispiel `Rockchip`); der Ordnername des Targets funktioniert ebenso. `json` und `yaml` liefern dieselben Daten mit
den Quantisierungen je Format.

| Option | Bedeutung |
|---|---|
| `-f, --format [table\|json\|yaml]` | Ausgabeformat (Standard `table`). |

In der GUI: **Build > Targets…** (dieselbe Liste, mit dem Unterstützungsgrad jedes Targets) oder **Targets…** neben
der Target-Liste des Builders.

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Prüft, ob das Target lädt (per ID oder Ordnername) und ob sein Block `capabilities:` das Schema besteht, das GUI,
CLI, Build-Service und Werkzeuge verwenden; jeder Schemafehler wird ausgegeben. Exit-Code 1, wenn das Target nicht
gefunden wird oder seine Capabilities ungültig sind. Ein Target ohne Capabilities wird gemeldet, gilt aber als
gültig.

```bash
llm-cli targets validate Rockchip
```

In der GUI: **Target prüfen** neben der Target-Liste des Builders oder **Build > Targets… > Target prüfen** (dieselbe
Prüfung; die target.yml wird frisch gelesen). Sie läuft auch, nachdem der Modul-Assistent ein Target angelegt hat, und
nach der Installation eines Targets aus dem Community Hub.

---

## build

### build start

```
llm-cli build start -m MODELL -t TARGET [Optionen]
```

Konvertiert und quantisiert ein Modell für ein Target in Docker. Die Anfrage prüft zuerst der Build-Dienst, mit
derselben Prüfung, die der Builder-Tab und KI-Clients bekommen: Eine Kombination, die die Capabilities des Targets
nicht unterstützen, ein Modell, das weder ein vorhandener lokaler Ordner bzw. eine Datei ist (die wie ein Modell
aussieht) noch eine Hugging-Face-ID, oder ein Datensatz, der keine Datei ist, stoppt mit Exit-Code 2 und dem Grund,
bevor etwas läuft. Fehlendes Board und fehlende Quantisierung werden mit den Standardwerten des Targets ergänzt. Ein
Target ohne Capabilities-Block wird ungeprüft gebaut.

| Option | Bedeutung |
|---|---|
| `-m, --model TEXT` | **Pflicht.** Hugging-Face-ID (z. B. `HuggingFaceTB/SmolLM2-135M-Instruct`) oder ein lokaler Ordner bzw. eine Datei. |
| `-t, --target TEXT` | **Pflicht.** Target-ID, wie `targets list` sie zeigt, z. B. `Rockchip`, oder der Ordnername des Targets. Groß- und Kleinschreibung spielt keine Rolle. Ein unbekanntes Target stoppt mit Exit-Code 2 und der Liste der bekannten Targets. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Ausgabeformat (Standard `gguf`). Die Liste ist länger als das, was ein Target unterstützt; Rockchip nimmt `gguf`, `rkllm` und `rknn`. |
| `-b, --board TEXT` | Board der Target-Familie, z. B. `rk3588`, `rk3576`, `rk3566`. Standard: das Standard-Board des Targets (Rockchip: `rk3588`). Groß- und Kleinschreibung spielt keine Rolle. |
| `-q, --quantization TEXT` | Quantisierung, z. B. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Standard: der Standard des Formats für das Board. Gängige Schreibweisen wie `INT8` werden übersetzt. |
| `-o, --output TEXT` | Ordner, in dem das fertige Modell (Golden-Artefakt) gespeichert wird, relativ zum aktuellen Ordner; er wird angelegt, wenn es ihn noch nicht gibt. Standard: der Ausgabeordner ([`config output-folder`](#config-output-folder), `~/llmcf/output`). An einem Terminal ohne diese Angabe wirst du gefragt, mit dem Ausgabeordner als Vorgabe; `-` lässt den Build im Build-Ordner des Frameworks ([`build save`](#build-save) speichert ihn später). Ein Systemordner oder der Programmordner des Frameworks wird abgelehnt (Exit-Code 2). `--output-dir` ist der alte Name und geht weiter. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimierungsstufe (Standard `balanced`), wird den Build-Skripten als `OPTIMIZATION_LEVEL` übergeben. Die Rockchip-Skripte werten sie nicht aus. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in der Build-Warteschlange dieses Prozesses (Standard `normal`). |
| `--parallel` | Veraltet und in `--help` ausgeblendet: Hat keine Wirkung (ein Aufruf startet einen Build) und gibt eine Warnung aus. |
| `-F, --follow` | Jede Stufe ausgeben, während der Build läuft. |
| `--gpu / --no-gpu` | Die GPU des Hosts in den Container durchreichen (Standard aus): eine NVIDIA-GPU, wenn `nvidia-smi` gefunden wird, sonst `/dev/dri`, falls vorhanden. Rockchip-Konvertierungen brauchen das nicht. |
| `--imatrix / --no-imatrix` | Importance Matrix für GGUF (Standard aus). Pflicht für `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`, `IQ2_M`, `IQ3_XXS`, `Q2_K_S`. |
| `--dataset PATH` | Deine eigene Kalibrierungsdatei (muss existieren): eine Textdatei für die GGUF-Importance-Matrix oder für RKLLM, eine Liste mit Beispieleingaben für RKNN `i8`. Ohne sie nutzt die Importance Matrix den mitgelieferten Kalibrierungstext. |

Verhalten:

- Der Befehl bleibt im Vordergrund, bis der Build fertig ist. Strg+C bricht den Build über den Build-Service ab:
  Sein laufender Container (Image-Build, Sicherheits-Scan oder Konvertierung) wird gestoppt, die CLI wartet, bis der
  Build angehalten hat (höchstens 120 s), sagt das und endet mit Exit-Code 130. In den Ausgabeordner wird nichts
  geschrieben. Ein Build, der seine Dateien schon in den Ausgabeordner verschiebt, lässt sich nicht mehr abbrechen;
  die CLI meldet dann, dass er von selbst zu Ende gelaufen ist.
- Scheitert der Build an einem bekannten Problem mit einer Korrektur, die das Framework selbst anwenden kann, zeigt
  die CLI die Ursache und fragt „Apply this fix and retry the build?“. Ohne Terminal wird der Vorschlag verworfen.
  Manuelle Schritte werden nur angezeigt.
- Ergebnisse: `<Ausgabeordner>/<Target>/<Modell>/<Quantisierung>/` mit der Modelldatei, `build_info.json` und
  `Model_Card.md`, dazu ein ZIP davon neben diesem Ordner. Sie erscheinen dort erst, wenn der Build abgeschlossen
  ist; ein abgebrochener oder gescheiterter Build hinterlässt dort keine Dateien. Ein früherer fertiger Build mit
  diesem Namen wird nie ersetzt: der neue kommt daneben (`Q4_K_M-2`). Ein Ordner, den du außerhalb des
  Ausgabeordners gewählt hast, bleibt in `deploy list-artifacts` aufgelistet.
- Zugangsbeschränkte Hugging-Face-Modelle brauchen ein Token: `llm-cli secrets set hf_token`. Gesendet wird nur das
  im Framework gespeicherte Token; ein Token in `HF_TOKEN` oder aus `hf auth login` wird nie verwendet.
- Ein Hugging-Face-Modell wird nach `<cache_dir>/models/hf/<owner>--<name>/<commit>/` heruntergeladen, ein Ordner pro
  Commit, Datei für Datei; nur dieser Ordner wird in die Build-Container eingebunden. Ein zweiter Build desselben
  Commits verwendet ihn wieder, und ein Build eines anderen Commits vermischt nie Dateien damit. Strg+C stoppt den
  Download vor seiner nächsten Datei.
- Ein volles Laufwerk wird als `insufficient_resources` mit dem freien Platz der Cache- und Ausgabelaufwerke
  gemeldet, und der Download stoppt, wenn das Cache-Laufwerk unter die Hälfte des Platzes fällt, der frei bleiben
  muss (2 GiB oder 2 % des Laufwerks). Der Self-Healing-Vorschlag für ein volles Laufwerk nennt die Ordner des
  Frameworks auf diesem Laufwerk mit ihrer Größe als manuellen Schritt; Docker aufzuräumen wird nur vorgeschlagen,
  wenn Dockers Datenverzeichnis das volle Laufwerk ist.
- Eine lokale Modelldatei wird allein in die Build-Container eingebunden, nie der Ordner, in dem sie liegt; ein
  Modell, das Dateien daneben braucht (zum Beispiel externe ONNX-Daten), wird als Ordner angegeben. Ditto und
  MCP-Clients können gar keinen Pfad nennen: Sie verwenden `<Label>:<relativer Pfad>` in den
  [Ordnern, die die KI nutzen darf](#tools-folders).
- Der erste Build eines Targets baut zuerst sein Docker-Image und dauert deutlich länger.

In der GUI: der Tab **Builder**: Modell, Target (**Targets…** zeigt jedes Target), Format (ein Target ohne Fähigkeiten
bietet jedes Format von `--format`, mit dem Hinweis, dass nichts geprüft wird), Board, Quantisierung, **Optimization**
(alle Stufen, auch `FAST`), **Priorität**, Kalibrierung und GPU, dann **Start Build Pipeline**. Die
Fortschrittsanzeige folgt dem Build (`--follow`), **Cancel Build** stoppt ihn (Strg+C), und das fertige Modell wird in
einem Speichern-Dialog am Ausgabeordner angeboten (`--output`; **Datei > Output Folder...** setzt die Vorgabe).

```bash
# GGUF für die CPU des RK3566 (Standard-Quantisierung Q4_0 auf diesem Board)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3566 --follow

# RKLLM für die NPU des RK3576 (Standard w4a16)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3576 -F

# 2-Bit-GGUF: braucht die Importance Matrix
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3588 -q IQ2_XS --imatrix -F

# RKNN int8 aus einem lokalen ONNX-Modell mit Beispielliste
llm-cli build start -m ./detector.onnx -t Rockchip -f rknn -b rk3588 -q i8 --dataset ./samples.txt -F

# Ergebnisse in einen Ordner deiner Wahl
llm-cli build start -m ./my-model -t Rockchip -o ./builds -F
```

### build save

```
llm-cli build save ARTIFACT [--to ORDNER]
```

Unveröffentlicht. Speichert einen fertigen Build, der im Build-Ordner des Frameworks wartet (ein GUI-Build, den du nicht
gespeichert hast, oder `build start -o -`), genau wie **Speichern…** im Tab „Deployment“ der GUI. `ARTIFACT` ist sein
Ordner, wie ihn `deploy list-artifacts` zeigt (`Rockchip/SmolLM2-135M-Instruct/Q4_0`), sein voller Pfad oder seine ID
(`a_...`). Ohne `--to` kommt er in den Ausgabeordner; an einem Terminal wirst du gefragt, mit dem Ausgabeordner als
Vorgabe.

Auf derselben Platte wird der Build verschoben (umbenannt, nichts kopiert). Auf eine andere Platte wird jede Datei
kopiert, jede Kopie wird per sha256 mit ihrem Original verglichen (eine Modelldatei auch mit dem Hash in ihrem
Manifest), und erst dann wird die Kopie im Build-Ordner entfernt. Einem Link im Build wird nie gefolgt, er wird nicht
kopiert; fehlt etwas in der Kopie, nennt die Ausgabe es, und die Kopie im Build-Ordner bleibt. Vor dem Kopieren wird
der freie Platz geprüft. Ein
belegter Name wird nie überschrieben (`-2`). Schlägt eine Prüfung fehl, wird nichts verschoben (Exit-Code 1). Ein
Systemordner, der Programmordner des Frameworks, sein Build-Ordner und sein persönlicher Ordner werden abgelehnt.

```bash
llm-cli build save Rockchip/SmolLM2-135M-Instruct/Q4_0 --to /mnt/models
```

So sieht eine abgelehnte Anfrage aus:

```
$ llm-cli -q build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3566
Format 'rkllm' does not run on rk3566 (supported: rk3588, rk3576).
$ echo $?
2
```

In der GUI: Tab „Deployment“ > **Speichern…**.

### build status

```
llm-cli build status [REQUEST_ID] [-a|--all]
```

Zeigt Builds aus dem gemeinsamen Build-Zustand: Jedes Programm, das baut (CLI, GUI, MCP-Server), schreibt eine
Zusammenfassung jeder Build-Anfrage in den Benutzerordner (`build_requests/`) und je Build einen Eintrag in den
Cache-Ordner (`<cache_dir>/build_records/`) mit Request, Modell, Target, Format, Quantisierung, Status, Stufe und
Fortschritt, deshalb erscheinen auch Builds, die ein anderer Prozess gestartet hat, einschließlich Anfragen, die
noch in seiner Warteschlange warten (Spalte `Where`: das Programm, das sie ausführt). Ohne ID: eine Tabelle
der 20 neuesten Builds; mit einer Request-ID (`req_...`) oder einer Build-ID: die Einzelheiten jedes Versuchs,
einschließlich des Fehlers eines gescheiterten Builds (Zugangsdaten geschwärzt). Ein Build, dessen Prozess endete,
bevor der Build fertig war, erscheint als `interrupted`. Exit-Code 1, wenn die ID nicht gefunden wird. Der Befehl
braucht weder den Tresor für Zugangsdaten noch Docker. Unter Linux und macOS sind der Ordner der Einträge und die
Einträge nur für den Benutzer lesbar, der gebaut hat (Modi 0700 und 0600), sodass in einer gemeinsamen Installation
jeder Benutzer seine eigenen Builds sieht.

| Option | Bedeutung |
|---|---|
| `-a, --all` | Jeden aufbewahrten Build statt der 20 neuesten zeigen (aufbewahrt werden die 200 neuesten fertigen oder unterbrochenen Builds). |

Ein Request, der auf eine Self-Healing-Entscheidung wartet, zeigt seinen gescheiterten ersten Versuch; entscheide in
dem Programm, das ihn ausführt. Mit einer Request-ID folgen die Korrekturvorschläge, die KI-Clients dafür gemacht
haben (`propose_healing_fix`), jeder als KI-erzeugt markiert mit dem, der ihn vorgeschlagen hat (der Aufrufer und der
eigene, selbst angegebene Name des Clients), und seinem manuellen Schritt, den das Framework nie ausführt. Ohne ID
sagt eine Zeile, für wie viele gescheiterte Builds es solche Vorschläge gibt.

In der GUI: **Build > Build-Verlauf…**: jeder Build (alle, wie `--all`); **Details anzeigen** (oder ein Doppelklick)
zeigt einen Build wie mit `REQUEST_ID`, mit seinen Fehlern, jedem Versuch und seinem Ausgabeordner und den
Korrekturvorschlägen der KI-Clients.

---

## deploy

### deploy list-artifacts

```
llm-cli deploy list-artifacts
```

Listet die fertigen Builds auf, gelesen aus ihren Manifesten: im Ausgabeordner, im Build-Ordner des Frameworks („noch
nicht gespeichert“: GUI-Builds, die du nicht gespeichert hast, und jeder frühere Build, der bleibt, wo er ist) und in
Ordnern, in die du Builds gespeichert hast. Jede Zeile zeigt den Ordner (relativ zu seinem Ordner), wo er liegt, Modell,
Format, Quantisierung, Board, Größe und den vollen Pfad.

In der GUI: die Liste **1. Select Golden Artifact** im Tab **Deployment** (dieselben Builds, „noch nicht gespeichert“
für einen im Build-Ordner und „getestet auf“ einem Board nach seinem Benchmark dort; der Tooltip zeigt den vollen
Pfad); **Refresh** liest sie neu.

### deploy package

```
llm-cli deploy package ARTIFACT_NAME --profile PROFIL [--docker|--no-docker]
```

Erstellt eine ZIP-Datei für das Board: die Modelldatei, Manifest und Model Card des Builds, ein erzeugtes
`deploy.sh` und `checksums.sha256`. Sie wird als `deploy_<Modell>_<Zeitstempel>.zip` in den Ausgabeordner
geschrieben.

| Argument / Option | Bedeutung |
|---|---|
| `ARTIFACT_NAME` | Ein Build-Ordner, wie ihn `list-artifacts` zeigt (relativ zum Ausgabeordner oder zum Build-Ordner des Frameworks), oder ein beliebiger vorhandener Pfad zu einem Build-Ordner oder einer Modelldatei. |
| `--profile TEXT` | **Pflicht.** Das Target, z. B. `Rockchip` (für Geräteoptionen im Docker-Modus). |
| `--docker / --no-docker` | Zusätzlich Container-Images einpacken (Standard aus). Experimentell und in dieser Version nicht geprüft; für eine Modelldatei aus lassen. Das Image (`image_inference_runtime`; ein Paket enthält nie die Wissensdatenbank) muss per Digest festgelegt sein (`name@sha256:...`) oder auf diesem Rechner gebaut worden sein; ein gezogenes Image, das nur über ein Tag benannt ist, wird mit Grund abgelehnt, und ebenso das voreingestellte `llm-framework/inference:local` (ein lokaler Name), bis du es hier gebaut hast. Ein fehlgeschlagener Image-Export bricht das Paket ab. |

Ohne Docker werden nur Modelldateien übertragen; Builds mit mehreren Gewichtsdateien werden abgelehnt. Ein Paket,
das nicht erstellt werden kann, gibt den Grund aus und endet mit 1; das ZIP wird unter einem vorläufigen Namen
geschrieben und erst umbenannt, wenn es vollständig ist.

In der GUI: Tab Deployment > **Nur Paket erstellen…**: der ausgewählte Build, das **Profile** des Tabs und **Use Docker
Containerization?** für `--docker`. Jeder andere Build-Ordner und jede Modelldatei (`ARTIFACT` nimmt jeden Pfad)
kommt mit **Anderer Build oder Modelldatei…** > **Build-Ordner…** oder **Modelldatei…** in die Liste, für diese
Schaltfläche und für **Deploy to Target**; ist kein Build ausgewählt, fragt die Schaltfläche nach einer Modelldatei.
Kein Board wird
kontaktiert, so geht es auch für ein Board ohne Netz. Ein Ordnerdialog öffnet sich beim Ausgabeordner: OK behält ihn,
ein anderer Ordner wird geprüft wie für jedes Golden Artifact (Abbrechen erstellt nichts). Die Meldung nennt den vollen
Pfad des Pakets oder den Grund und den nächsten Schritt; **Abbrechen** stoppt das Packen, und keine halbe Datei bleibt.

```bash
llm-cli deploy package Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
```

### deploy run

```
llm-cli deploy run PACKAGE_PATH --host HOST --user BENUTZER [--port 22] [--password [WERT] | --password-stdin]
                   [--trust-fingerprint TEXT]
                   [--label TEXT] [--wait SEKUNDEN]
```

Lädt ein Paket per SSH in einen privaten temporären Ordner auf dem Board, führt dort `deploy.sh` aus und löscht den
Ordner danach. `deploy.sh` prüft die Prüfsummen und kopiert das Modell nach `~/models` (oder `$MODEL_DIR`); für GGUF
gibt es eine passende `llama-server`-Befehlszeile aus, wenn `llama-server` auf dem Board installiert ist.

| Argument / Option | Bedeutung |
|---|---|
| `PACKAGE_PATH` | Die ZIP-Datei aus `deploy package`, das ihren vollständigen Pfad ausgibt (muss existieren). |
| `--host TEXT` | **Pflicht.** Die Adresse oder der Hostname des Boards, zum Beispiel `192.168.1.50` oder `myboard.local`. `--ip` ist der alte Name und funktioniert weiter. |
| `--user TEXT` | **Pflicht.** Benutzer auf dem Board. |
| `--port INTEGER` | SSH-Port (Standard 22). |
| `--password` | Nur für die erste Verbindung: wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu hinterlegen, und nie gespeichert. Ohne Wert wird es verdeckt abgefragt; gibst du in einem Terminal nichts an, wird ebenfalls gefragt (Enter, wenn der Schlüssel schon hinterlegt ist). `--password WERT` funktioniert für Skripte weiter, ist aber veraltet und gibt eine Warnung aus: Jeder Benutzer sieht den Wert in der Prozessliste, und er bleibt in deinem Shell-Verlauf. Ein Wert, der mit `-` beginnt, wird als weitere Option gelesen: Gib ein solches Passwort bei der Abfrage oder mit `--password-stdin` ein. |
| `--password-stdin` | Liest das Passwort von der Standardeingabe (eine Zeile), etwa aus einem Passwortmanager weitergeleitet. In einem Terminal abgelehnt, weil es dort beim Tippen sichtbar wäre; nimm dort `--password`. |
| `--trust-fingerprint TEXT` | Einem neuen Board ohne Nachfrage vertrauen, wenn sein Host-Schlüssel genau diesen SHA256-Fingerabdruck hat (für Skripte). |
| `--label TEXT` | Dein Name für das Board in den vertrauten Geräten (Standard: der Name, den es schon hat, oder seine Adresse). |
| `--wait SEKUNDEN` | Wenn das Netzwerk während des Deployments wegbricht, so lange auf das Board warten (Vorgabe 300, 0 bis 86400). |

Bricht das Netzwerk weg (zum Beispiel bei einem Board nur mit WLAN), wartet das Deployment auf das Board und zeigt,
wie lange es schon wartet und wie lange höchstens. Strg+C bricht ab, und die CLI sagt dann, ob `deploy.sh` auf dem
Board schon gestartet war (ein zweites Strg+C beendet sofort). Sobald das Board auf seinem SSH-Port wieder
antwortet, meldet sich die CLI mit derselben Prüfung des Host-Schlüssels neu an, und der Upload geht dort weiter, wo
er stehen blieb, nachdem die sha256 des Angekommenen gepasst hat. `deploy.sh` läuft auf dem Board für sich und wird
einmal gestartet, nie zweimal. Ist das Board nicht erreichbar oder lehnt es die Anmeldung ab, sagt die CLI mit einem
Code, warum, und was zu tun ist: `device_host_down` (nichts antwortet: das Board ist aus, in einem anderen Netzwerk,
oder der Hostname wird nicht aufgelöst), `device_port_closed` (das Board lehnt SSH auf diesem Port ab),
`device_auth_failed` (falscher Benutzername oder falsches Passwort, oder der Schlüssel des Frameworks fehlt),
`device_host_key_changed` (ein anderer Host-Schlüssel als der, dem du vertraut hast).

Erste Verbindung: Die CLI zeigt den Fingerabdruck des Host-Schlüssels und fragt, ob du ihm vertraust. Vergleiche ihn
mit `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` auf dem Board. Ohne Terminal und ohne `--trust-fingerprint`
wird nichts übertragen. Ein Board, dessen Host-Schlüssel sich geändert hat, wird immer abgelehnt. Nach einem
erfolgreichen Deployment kommt das Board zu den [vertrauten Geräten](#deploy-devices) (im Audit-Log vermerkt), so dass
ein KI-Client später dorthin übertragen darf, jedes Mal mit deiner Bestätigung. `ssh` und `scp` (genutzt, wenn
paramiko fehlt) bekommen nie die Standardeingabe des Frameworks.

In der GUI: Tab Deployment > **Vorhandenes Paket deployen…** (ein ZIP aus `deploy package` oder **Nur Paket
erstellen…**) oder **Deploy to Target**, das den ausgewählten Build vorher packt. Das Board sind die Felder **Adresse
oder Hostname** und **User**; **Port**, **Name** und **Auf das Board warten** sind `--port`, `--label` und `--wait`. Der
Host-Schlüssel wird im Hintergrund gelesen und sein Fingerabdruck zum Vergleichen gezeigt (**Nein** ist vorgewählt);
das Passwort wird verdeckt abgefragt, einmal benutzt und nie gespeichert. **Abbrechen** tut, was hier Strg+C tut.

```bash
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --host 192.168.1.50 --user rock

# Ein Board im WLAN über seinen Hostnamen, bis zu 10 Minuten Warten, wenn das Netzwerk wegbricht
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --host myboard.local --user rock \
  --wait 600

# In einem Skript, mit vorher geprüftem Fingerabdruck
llm-cli deploy run output/deploy_model_1791801300.zip --host 192.168.1.50 --user rock \
  --trust-fingerprint SHA256:AbCdEf...
```

### deploy devices

```
llm-cli deploy devices list [--json]
llm-cli deploy devices add HOST --user BENUTZER [--port 22] [--label TEXT]
llm-cli deploy devices edit GERÄT [--host HOST] [--port PORT] [--key "SCHLÜSSELZEILE" | --read-key]
llm-cli deploy devices remove GERÄTE_ID
```

Die vertrauten Geräte (`~/.llm-framework/devices.json`): die einzigen Geräte, die KI-Clients und Ditto für
`deploy_to_device` nennen dürfen, und jedes solche Deployment wird trotzdem erfragt. Ein Eintrag enthält deinen
Namen für das Gerät, die Adresse, den Benutzer, den Host-Schlüssel, den du bestätigt hast, und ob der eigene
SSH-Schlüssel des Frameworks sich dort anmeldet (gekoppelt); die Geräte-ID wird aus dem Host-Schlüssel abgeleitet,
dieselbe ID, die `llm-cli device list` zeigt. Einträge schreibst nur du: `deploy run`, `scan --device` und
`device runtime deploy` nach einer erfolgreichen Anmeldung, das Deployment-Fenster der GUI nach einem Deployment und
`deploy devices add` und `deploy devices edit`. Kein Tool schreibt, ändert oder entfernt einen.

| Befehl | Bedeutung |
|---|---|
| `list` | Jeder Eintrag mit seinem Host-Schlüssel und ob dieser noch der vertraute in der `known_hosts` des Frameworks ist (`trusted`, `CHANGED in known_hosts`, `not in known_hosts`), gekoppelt und dem letzten Deployment. |
| `add HOST --user BENUTZER` | Nimmt ein Board auf, dem du vor dieser Version vertraut hast (sein Host-Schlüssel steht schon in der `known_hosts` des Frameworks). Braucht ein Terminal: Es zeigt den Fingerabdruck und fragt; es vertraut nie einem neuen Schlüssel, fragt nie nach einem Passwort und markiert das Board nur dann als gekoppelt, wenn der Schlüssel des Frameworks sich anmeldet. |
| `edit GERÄT` | Ändert die Adresse eines vertrauten Boards (`--host`: Hostname oder IP-Adresse, `--port`) oder seinen SSH-Host-Schlüssel in der `known_hosts` des Frameworks, z. B. nachdem sich seine Adresse geändert hat oder es neu installiert wurde. `GERÄT` ist seine Geräte-ID, sein Name oder seine Adresse. Den neuen Schlüssel fügst du mit `--key` ein (die Zeile aus `/etc/ssh/ssh_host_ed25519_key.pub` auf dem Board, eine `known_hosts`-Zeile oder eine Zeile von `ssh-keyscan`, in Anführungszeichen; niemand tippt einen Schlüssel ab), oder `--read-key` liest ihn vom Board unter seiner (neuen) Adresse; eine Zeile mit Optionen aus `authorized_keys`, Befehlen oder einem zweiten Schlüssel und der Schlüssel eines anderen vertrauten Boards werden abgelehnt. Braucht ein Terminal: Es zeigt die Adresse und den Fingerabdruck jetzt und nach der Änderung und fragt ("Nein" ist die Vorgabe); die Änderung kommt zuerst ins Audit-Log. Ein neuer Schlüssel gibt dem Board eine neue Geräte-ID (sie leitet sich vom Schlüssel ab): Der Eintrag zieht mit Name und Benutzer dorthin, und ob sich der Schlüssel des Frameworks anmeldet, wird einmal getestet; Probe und Deploy-Einstellungen des Boards bleiben unter der alten ID, bis du es erneut prüfst. |
| `remove GERÄTE_ID` | Entfernt den Eintrag (im Audit-Log vermerkt); KI-Tools können das Gerät dann nicht mehr nennen. Sein Eintrag in `known_hosts` bleibt. |

Ein Eintrag ist nur nutzbar, solange die `known_hosts` des Frameworks genau den Schlüssel enthält, den er nennt:
Gib dem Eintrag eines neu installierten Boards, dessen Schlüssel sich geändert hat, mit `deploy devices edit` den
neuen Schlüssel (oder entferne den geänderten Schlüssel dort und vertraue dem Board erneut mit `deploy run` oder
`scan --device`).

In der GUI: **Boards > Boards und Geräte...**: Die Tabelle zeigt jedes vertraute Board (Name, Adresse, Benutzer, den Host-Schlüssel mit
seinem vollen Fingerabdruck und seinem Stand in `known_hosts`, gekoppelt, seit wann und wie ihm vertraut wird,
das letzte Deployment) neben den untersuchten Boards. **Vertrautes Board hinzufügen...** macht `add` (Adresse,
Benutzer, Port, Name; der Fingerabdruck wird gezeigt und erfragt, "Nein" ist die Vorgabe), **Adresse oder
Schlüssel ändern...** macht `edit` (Hostname oder IP-Adresse, Port und ein Feld, in das du den neuen Schlüssel
einfügst, oder stattdessen **Schlüssel vom Board lesen**; Adresse, Host-Schlüssel und Geräte-ID jetzt und nach der
Änderung nebeneinander, "Nein" ist die Vorgabe), und **Vertrauen entfernen...** macht `remove` (es nennt genau, was
wegfällt, "Nein" ist die Vorgabe, und es schreibt denselben Audit-Eintrag).

---

## ditto

Ditto ist der eingebaute Assistent. Die meisten `ditto`-Befehle nutzen Anbieter und Modell aus den KI-Einstellungen
(GUI: **Tools > Configure AI Agent (Ditto)...**, oder `ai_provider` und `ai_model` in der `config.yml`).
Anmelden kannst du dich mit [`llm-cli ditto login`](#ditto-login) oder mit **Anmelden...** in den
KI-Einstellungen.

Ditto ist optional und experimentell. Vor dem ersten `ditto`-Befehl (oder `module generate-ai`) an einem Terminal
geht einmal ein Hinweis auf stderr: „Nutzung auf eigene Gefahr und Verantwortung. Der Ersteller von LLMCF haftet
nicht.“ (der Gewährleistungsausschluss aus `LICENSE.txt`, nicht mehr). Er wird je Benutzer gespeichert und mit dem
Hinweis der GUI geteilt; ohne Terminal wird nichts ausgegeben oder gespeichert. Der Befehl läuft immer
([Details](Features_DE.md#experimentell)).

### ditto login

```
llm-cli ditto login ANBIETER [--method ID] [--with-token] [--from-env] [--base-url URL] [--workspace-id ID]
                    [--organization ID] [--project ID] [--profile NAME] [--insecure-storage] [--no-check] [--yes]
```

Meldet Ditto bei einem Anbieter an (`anthropic`, `openai`, `google`, `openrouter`, `ollama`, `custom`;
[`llm-cli ditto auth methods`](#ditto-auth-methods) listet sie mit ihren Verfahren). Ein Schlüssel kommt nie von der
Kommandozeile, wo ihn der Shell-Verlauf und die Prozessliste behalten würden: Er wird verdeckt abgefragt, von stdin
gelesen (`--with-token`) oder einmal aus der dokumentierten Umgebungsvariable des Anbieters übernommen
(`--from-env`). Bevor er im Tresor gespeichert wird, prüft ihn eine kostenlose Anfrage (die Modellliste, bei
OpenRouter der Schlüssel-Endpunkt); ein abgelehnter Schlüssel wird nicht gespeichert. Ein Schlüssel mit einem
Leerzeichen, einem Zeilenumbruch oder einem unsichtbaren Zeichen darin wird abgelehnt, bevor etwas gesendet wird. Das
Verfahren wird als `ai_auth_<anbieter>` gemerkt. Die Ausgabe zeigt höchstens die letzten vier Zeichen eines
Schlüssels, auch wenn ein Fehlertext des Servers ihn wiederholt.

| Art des Verfahrens | Was `ditto login` tut |
|---|---|
| `api_key` (Cloud-Anbieter, `custom`-Server) | Fragt den Schlüssel ab, prüft ihn und speichert ihn. Der Schlüssel eines Servers, dessen Adresse du festlegst, gehört nur zu dieser Adresse. Admin-Schlüssel (`sk-ant-admin01-`) werden abgelehnt, bevor etwas gesendet wird. |
| `bearer_optional` (lokale Engines mit optionalem Schlüssel) | Fragt ein Token ab („Enter für keins“); ohne Token wird der Server ohne Schlüssel genutzt. Ein leeres stdin mit `--with-token` ist ein Bedienfehler und löscht nie ein gespeichertes Token; das macht `ditto logout`. |
| `none` (Ollama, `custom` ohne Schlüssel) | Speichert nichts; prüft die Adresse und zählt die Modelle. |
| `cli_session` (`--method ant_profile`) | Nutzt ein Profil von Anthropics eigener `ant`-CLI; nur der Name des Profils wird gespeichert. Schlägt die Prüfung über das Anthropic-SDK fehl, sagt der Befehl, dass du `ant auth login` ausführen sollst. Erweitert: wird nie automatisch gewählt, und Anthropic hat nicht bestätigt, dass andere Apps diese Profile nutzen dürfen. |
| OAuth- und Cloud-Anmeldungen | In dieser Version nicht verfügbar (Exit-Code 6). |

Ohne Terminal (Skripte, CI) wird nichts abgefragt: Ein Anbieter mit mehr als einem Verfahren braucht `--method`
(ein Verfahren, das nur auf Wunsch gilt, wie `ant_profile`, zählt nicht mit), und der Schlüssel muss über
`--with-token` oder `--from-env` kommen.

| Option | Bedeutung |
|---|---|
| `--method ID` | Anmeldeverfahren, siehe `llm-cli ditto auth methods ANBIETER`. |
| `--with-token` | Den Schlüssel von stdin lesen. |
| `--from-env` | Den Schlüssel aus der Umgebungsvariable des Anbieters (zum Beispiel `ANTHROPIC_API_KEY`) in den Tresor übernehmen; danach wird die Variable nicht mehr gebraucht. |
| `--base-url URL` | Serveradresse bei Anbietern, deren Adresse du festlegst (Ollama, `custom`); gemerkt als `ai_base_url_<anbieter>`. Ein Schlüssel, der über reines `http://` an einen anderen Rechner geht, braucht `--yes`, weil er unverschlüsselt übertragen wird. |
| `--workspace-id ID` | Claude-API: Workspace-ID (`wrkspc_...`) für Schlüssel, die in jedem Workspace der Organisation gelten; wird mit dem Schlüssel gespeichert. |
| `--organization ID`, `--project ID` | OpenAI: Organisation (`org-...`) und Projekt (`proj_...`), die mit dem Schlüssel gesendet werden. |
| `--profile NAME` | Mit `--method ant_profile`: das Profil (Standard: das aktive Profil der `ant`-CLI). |
| `--insecure-storage` | Ohne nutzbaren Schlüsselbund: den Schlüssel des Tresors in einer Datei neben dem Tresor ablegen (**im Ruhezustand nicht verschlüsselt**). Wird ignoriert, wenn der Tresor auf eine andere Art geschützt ist, die funktioniert; einen Credential-Helper oder eine Passphrase, die du gewählt hast (`secret_storage`), ersetzt es nie (Exit-Code 5). |
| `--no-check` | Ohne Prüfung speichern (zum Beispiel offline). |
| `-y, --yes` | Nicht nachfragen, bevor ein Schlüssel über reines `http://` gesendet oder `--insecure-storage` verwendet wird. |

Exit-Codes: 0 angemeldet; 2 Bedienfehler (unbekannter Anbieter oder unbekanntes Verfahren, fehlendes `--method`,
fehlender Schlüssel oder fehlende Serveradresse, ein fehlerhafter Optionswert oder Schlüssel); 3 abgelehnt
(falscher, widerrufener oder Admin-Schlüssel, ein Problem mit dem Workspace); 4 Anbieter oder lokaler Server nicht
erreichbar; 5 der Tresor lässt sich nicht nutzen (die Meldung nennt die Möglichkeiten); 6 das Verfahren ist in
dieser Version nicht verfügbar oder der Anbieter wurde entfernt.

```bash
llm-cli ditto login anthropic                      # fragt den Schlüssel ohne Anzeige ab
printf %s "$OPENAI_API_KEY" | llm-cli ditto login openai --with-token --project proj_abc123
llm-cli ditto login anthropic --from-env --workspace-id wrkspc_01AbCd
llm-cli ditto login custom --method api_key --base-url http://127.0.0.1:8000/v1
llm-cli ditto login ollama --base-url http://192.168.1.20:11434/v1
llm-cli ditto login anthropic --method ant_profile --profile work
```

`ditto login` meldet an; welchen Anbieter Ditto nutzt (`ai_provider`, in den KI-Einstellungen), ändert es nicht.

In der GUI: **Security > Anmeldeübersicht...**, dann den Anbieter wählen: Sein Anmeldefeld hat eine Karte pro
Verfahren (`--method`) mit **Prüfen** und **Speichern** (Speichern speichert ohne Prüfung, wie `--no-check`) und den
zusätzlichen Feldern (`--workspace-id`, `--organization`, `--project`); **Dieses Profil verwenden** auf der Karte des
`ant`-Profils (`--profile`); **Den Schlüssel aus <VARIABLE> im Tresor speichern**, solange die Variable gesetzt ist
(`--from-env`; **Vorher prüfen (kostenlos)** ist angehakt); und bei Anbietern, deren Adresse du festlegst, ein
eigenes Feld **Serveradresse** (`--base-url`: nur für diesen Anbieter gemerkt, er wird dadurch nicht zu Dittos
Anbieter). Ohne nutzbaren Schlüsselbund bietet das Feld die Wahl des Schutzes an (`--insecure-storage`). Dasselbe
Feld öffnet **Tools > Configure AI Agent (Ditto)... > Sign in...** für die dort eingegebene Adresse.

### ditto logout

```
llm-cli ditto logout ANBIETER [--method ID] [--base-url URL]
```

Meldet Ditto ab: Der gespeicherte Schlüssel (bei einem Server, dessen Adresse du festlegst, der Schlüssel dieser
Adresse) wird von diesem Rechner gelöscht, und das gemerkte Verfahren wird vergessen. Der Schlüssel selbst
funktioniert weiter, bis du ihn auf der Schlüsselseite des Anbieters widerrufst; der Befehl nennt sie. Ein
Schlüssel in einer Umgebungsvariable bleibt in Gebrauch, bis du die Variable entfernst; der Befehl sagt das. Beim
`ant`-Profil wird nur der Name des Profils vergessen: `ant auth logout` würde auch die anderen Werkzeuge abmelden,
die es nutzen, deshalb überlässt der Befehl das dir.

| Option | Bedeutung |
|---|---|
| `--method ID` | Verfahren, von dem abgemeldet wird (Standard: das genutzte). |
| `--base-url URL` | Adresse, deren Schlüssel gelöscht wird (Standard: die eingestellte). |

In der GUI: **Security > Anmeldeübersicht...**, dann der Anbieter: **Abmelden** auf seiner Schlüsselkarte oder
**Nicht mehr verwenden** auf der Karte des `ant`-Profils (beide fragen vorher, „Nein“ ist vorgewählt; die Adresse ist
die **Serveradresse** des Felds). Bei einem Server ohne Anmeldung (Ollama, `custom` ohne Schlüssel) vergisst
**Vergessen** die gemerkte Serveradresse und das gemerkte Verfahren (es fragt vorher).

### ditto auth status

```
llm-cli ditto auth status [--json] [--check]
```

Eine Zeile pro Anbieter: Verfahren, woher die Zugangsdaten kommen (`vault`, `env:<VARIABLE>`, `profile:<name>`,
`runtime`), Konto oder Workspace, die letzten vier Zeichen des Schlüssels und der Zustand („Signed in as ...“,
„Not signed in“, „Using key from environment variable X“, „Expired: sign in again“). Dittos Anbieter ist mit `*`
markiert. Ein Schlüssel wird nie gezeigt.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe, ebenfalls ohne Schlüssel. |
| `--check` | Zusätzlich die kostenlose Prüfung für jeden Anbieter mit Zugangsdaten ausführen (und für Dittos Anbieter). |

Exit-Code 5, wenn sich der Tresor nicht nutzen lässt; mit `--check` der Code der ersten gescheiterten Prüfung (3
oder 4); sonst 0.

In der GUI: **Security > Anmeldeübersicht...** (alle Anbieter auf einmal, Dittos Anbieter mit `*` markiert, und wie
der Tresor geschützt ist, mit seiner Datei); **Alle prüfen** macht `--check` im Hintergrund.

### ditto auth methods

```
llm-cli ditto auth methods [ANBIETER] [--json]
```

Die Anmeldeverfahren aller Anbieter (oder eines Anbieters) in der Reihenfolge, in der sie bevorzugt werden: das
empfohlene, Verfahren, die nur auf Wunsch gelten, Verfahren, die in einer späteren Version kommen, die
Schlüsselseite, die Umgebungsvariablen, die zusätzlichen Optionen (`--workspace-id`, `--organization`, `--project`)
und Hinweise zu den Nutzungsbedingungen. Braucht weder den Tresor noch das Netz.

In der GUI: **Security > Anmeldeübersicht...**, Reiter **Alle Anmeldeverfahren** (die Verfahren jedes Anbieters mit
ihren Hinweisen, Umgebungsvariablen, Schlüsselseite, wo du den Schlüssel findest, Optionen und Anmerkungen); jede
Schlüsselkarte nennt ihre Umgebungsvariablen und ihre Anmerkung ebenfalls.

### ditto auth storage

```
llm-cli ditto auth storage [--use keyring|helper|passphrase|insecure] [--helper BEFEHL] [--yes]
```

Ohne `--use` zeigt der Befehl, wie der Tresor geschützt ist (siehe [secrets](#secrets)), und endet mit Exit-Code
5, wenn er sich nicht nutzen lässt. Mit `--use` wird der Tresor ab jetzt auf die gewählte Art geschützt: Ein
funktionierender Tresor wird mit einem neuen Schlüssel neu verschlüsselt, ein Tresor, der sich nicht öffnen ließ
(kein Schlüsselbund), wird mit dem neuen Schutz geöffnet, und die Wahl wird als `secret_storage` (und
`secret_helper`) gespeichert. Für dich wird nichts gewählt. `ephemeral` wählst du je Lauf mit
`LLM_SECRET_STORAGE=ephemeral` (hier Exit-Code 2). Eine neue Passphrase wird zweimal im Terminal abgefragt; ohne
Terminal endet der Befehl mit Exit-Code 5, und nichts ändert sich.

```bash
llm-cli ditto auth storage --use passphrase
llm-cli ditto auth storage --use helper --helper "pass show llmcc/vault-key"
```

In der GUI: **Security > Schlüsselspeicher...** zeigt den Schutz und die Tresordatei, auch während der Tresor
funktioniert. **Anders schützen** bietet den Schlüsselbund des Betriebssystems, einen Credential-Helper mit seinem
Befehl (`--helper`), eine Passphrase und die unverschlüsselte Schlüsseldatei an (nur nach der Bestätigung und der
Frage der CLI, „Nein“ ist vorgewählt); **Diesen Schutz verwenden** wechselt (`--use`).

### ditto auth rotate-key

```
llm-cli ditto auth rotate-key
```

Erzeugt einen neuen Schlüssel für den Tresor, verschlüsselt alle gespeicherten Zugangsdaten damit neu und entfernt
den alten Schlüssel. Exit-Code 5, wenn sich der Tresor nicht nutzen lässt.

In der GUI: **Security > Schlüsselspeicher... > Schlüssel erneuern...** (es fragt vorher; „Nein“ ist vorgewählt).

### ditto setup

```
llm-cli ditto setup
```

Ersteinrichtung der Tool-Berechtigungen, die Ditto und der MCP-Server verwenden. Stellt eine Frage pro Kategorie:

| Kategorie | Auswahl | Empfohlen |
|---|---|---|
| Informationen lesen (Targets, Builds, Logs, Wissensbasis) | allow, ask, deny | allow |
| Auf diesem Rechner etwas ändern (Builds starten oder abbrechen) | allow, ask, deny | ask |
| Andere Geräte erreichen oder Daten löschen (Übertragen per SSH, Uploads) | ask, deny | ask |

Die Antworten werden in `~/.llm-framework/tool_policy.json` gespeichert. Später änderst du sie mit
[`llm-cli tools permissions`](#tools-permissions).

In der GUI: Das Framework stellt dieselben Fragen bei seinem ersten Start; später **Tools > Tool Permissions (Ditto /
MCP)...**.

### ditto ask

```
llm-cli ditto ask FRAGE [--no-tools] [--provider TEXT] [--model TEXT]
```

Stellt Ditto eine Frage. Ditto darf die Tools des Frameworks im Rahmen deiner Berechtigungen nutzen; Tools, die auf
„ask“ stehen, zeigen das Tool und jedes Argument und brauchen deine Bestätigung im Terminal (ohne Terminal werden sie
abgelehnt). Die Tools sind die eigenen des Frameworks auf diesem Computer. Die Antwort geht nach stdout,
Tool-Hinweise und Fehler nach stderr. Nichts wird im Chatverlauf gespeichert.

| Option | Bedeutung |
|---|---|
| `--no-tools` | Ohne die Tools des Frameworks antworten. |
| `--provider TEXT` | Anbieter nur für diese Frage, z. B. `anthropic`, `openai`, `ollama`, `local`. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell nur für diese Frage. Standard: KI-Einstellungen. |

```bash
llm-cli ditto ask "Welche Quantisierung nehme ich für ein 1B-Modell auf dem RK3566?"
llm-cli ditto ask "Starte einen GGUF-Build von HuggingFaceTB/SmolLM2-135M-Instruct für rk3588"
llm-cli ditto ask "Erkläre IQ4_XS" --no-tools --provider local
```

Ohne Einrichtung erklärt der Befehl, was fehlt, zum Beispiel „the local model server is not running. Start it in the
GUI with Ditto > Local Runtime... > Start, or with: llm-cli ditto runtime start“.

In der GUI: der Chat-Bereich rechts (F9, **Ansicht > KI-Chat-Bereich**) mit **Senden**; **Optionen für diese
Frage** über dem Eingabefeld bietet **Ohne Werkzeuge antworten** (`--no-tools`), **Anbieter** (`--provider`) und
**Modell** (`--model`) nur für die nächste Frage, die KI-Einstellungen bleiben unverändert. Anders als dieser Befehl
behält der Chat das Gespräch.

### ditto eval

```
llm-cli ditto eval [Optionen]
```

Bewertet Ditto anhand fester Testfälle (Analyse von Build-Logs, Build-Empfehlungen, Modulvorschläge, Chat mit Tools,
Prompt-Injection), zum Beispiel um zu prüfen, ob ein lokales 2-Bit-Modell gut genug ist. Mit `--kind retrieval`
bewertet es stattdessen die Suche der Wissensbasis, ganz ohne KI-Modell ([unten](#retrieval-evaluation)). Nichts
wird gebaut oder geändert: Chat-Fälle laufen gegen einen Ersatz-Build-Dienst. Cloud-Anbieter berechnen jede Anfrage
(etwa 20 pro Lauf und Wiederholung). Wie es funktioniert: [Features_DE.md](Features_DE.md#ditto-evaluation).

| Option | Bedeutung |
|---|---|
| `--cases FILE` | Datei mit den Fällen. Standard: `evals/ditto/cases.yml` der Installation, für `--kind retrieval` `evals/retrieval/cases.yml`. |
| `--provider TEXT` | Anbieter für den Lauf. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell für den Lauf. Standard: KI-Einstellungen. |
| `--tier TEXT` | Gegen das verwaltete lokale Modell dieser Stufe laufen; wiederholbar (`--tier small --tier small-q2`). Nicht mit `--provider`/`--model` kombinierbar (Exit-Code 2). |
| `--kind [healing\|optimizer\|module\|chat\|retrieval]` | Nur Fälle dieser Art; wiederholbar. `retrieval` läuft nur, wenn es hier gewählt ist, wenn `--case` ohne `--kind` angegeben ist oder wenn die Datei aus `--cases` Retrieval-Fälle enthält. |
| `--rag-variant [fused\|rerank]` | Nur Retrieval: die zu bewertende Suchvariante; wiederholbar, ein Lauf je Variante. `fused` (Standard): die hybride Suche. `rerank`: die hybride Suche plus der Reranker aus `rag_reranker` mit seiner Relevanzschwelle. |
| `--case TEXT` | Nur diese Fall-ID; wiederholbar. |
| `--repeat INTEGER RANGE` | Läufe pro Fall, 1 bis 20 (Standard 1). |
| `--json FILE` | Den vollständigen Bericht (Einrichtung, Bestehensquoten, jede Antwort) als JSON schreiben. |
| `--min-pass FLOAT RANGE` | Exit-Code 1, wenn ein Lauf weniger Fälle besteht als dieser Anteil (0 bis 1, z. B. `0.8`). |
| `--effort [low\|medium\|high]` | Aufwand für diesen Lauf (lokale Modelle: `low` = ohne Nachdenken). Standard: KI-Einstellungen. |

Mit `--tier` muss die lokale Laufzeit installiert und das Modell der Stufe heruntergeladen sein. Die CLI startet
`llama-server` nacheinander mit jeder Stufe und stoppt die Server, die sie gestartet hat. Ein Server, den du selbst
gestartet hast, läuft weiter, wenn er die Stufe schon bedient; sonst ersetzt die Evaluation ihn für diese Stufe und
sagt das am Ende, auch wenn der Lauf früher endet (zum Beispiel, wenn eine spätere Stufe nicht startet).

```bash
# Das 2B-Modell mit 4 und 2 Bit auf diesem Rechner vergleichen, ohne Nachdenken
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json

# Nur die Chat-Fälle, je drei Läufe, gegen den eingestellten Anbieter
llm-cli ditto eval --kind chat --repeat 3

# Als Qualitätsschwelle in der CI
llm-cli ditto eval --provider anthropic --min-pass 0.8
```

Das Ergebnis ist eine Tabelle pro Lauf: bestandener Anteil, Anteil pro Art und durchschnittliche Sekunden pro Fall.
Eine Datei mit Fällen, die sich nicht lesen lässt, oder Filter, auf die kein Fall passt, stoppen mit Exit-Code 2.

In der GUI: **Ditto > Bewertung...**: die Arten (und für Retrieval die Suchvarianten), einzelne Fälle, **Auswählen...**
für eine andere Falldatei, die Läufe pro Fall, die lokalen Modellstufen sowie Anbieter, Modell und Denkaufwand nur für
diesen Lauf. **Bewertung starten...** sagt zuerst, was aufgerufen wird und ob es Geld kostet, auch wenn die
Ersatzkette der KI-Einstellungen einen Cloud-Anbieter nennt („Nein“ ist die Vorgabe); eine Stufe, die noch nicht
heruntergeladen ist, wird mit einem Hinweis auf **Ditto > Lokale Modelle...** abgelehnt. Der Lauf geht im Hintergrund
mit Fortschritt und **Abbrechen** und zeigt dieselben Tabellen (ein ersetzter Server von dir wird gemeldet, auch nach
**Abbrechen**); **Bericht speichern...** schreibt den `--json`-Bericht in eine Datei deiner Wahl. `--min-pass` setzt
nur den Exit-Code für Skripte.

#### Retrieval-Evaluation

`llm-cli ditto eval --kind retrieval` misst, wie gut die Wissensbasis den richtigen Auszug findet. Die
mitgelieferten Fälle sind 42 Suchen auf Deutsch und Englisch: Fragen über die Sprachgrenze hinweg, Bezeichner wie
`GGML_CUDA`, Antworten tief in einer großen Tabelle oder einem Skript, die letzten Zeilen gescheiterter Builds, die
Suchbegriffe, die Ditto selbst schickt, und 4 Fragen ohne Bezug. Sie laufen gegen einen Korpus aus 30 Auszügen der
Framework- und SDK-Dokumentation in `evals/retrieval/corpus/` (eine Datei mit Fällen nennt ihren Korpus-Ordner mit
`corpus:`, relativ zur Datei).

- Der Korpus wird mit dem eingestellten lokalen Embedding-Modell (`rag_embedding_model`) in eine temporäre
  Wissensbasis indiziert, die danach gelöscht wird. Deine eigene Wissensbasis bleibt unberührt, und weder ein
  KI-Modell noch ein Qdrant-Server wird genutzt. `--provider`, `--model`, `--tier` und `--effort` wirken auf diese
  Fälle nicht.
- Das Embedding-Modell und für `rerank` der Reranker werden beim ersten Mal in den Modellordner heruntergeladen
  (nicht im Offline-Modus); danach funktioniert die Evaluation offline.
- Jede Suche liefert bis zu 10 Auszüge. Ein Fall besteht, wenn der erste passende Auszug innerhalb seiner Grenze
  liegt (`within` des Falls, Standard 3); eine Frage ohne Bezug besteht nur, wenn kein Auszug zurückkommt.
- Neben der Tabelle der Läufe gibt es eine Tabelle „Retrieval“ mit einer Zeile je Variante: hit@1, hit@3, hit@k
  (k = die Grenze des jeweiligen Falls), MRR über die 10 Ergebnisse, „Unrelated: no excerpt“ (der Anteil der Fragen ohne Bezug, die
  nichts bekommen haben) und die mittlere Suchzeit („p50 ms“, Median). `--json` ergänzt für jeden Fall die
  gefundenen Dokumente und ihre Werte, die Prüfsummen von Fällen und Korpus, die Dauer der Indizierung und den
  belegten Speicher.
- `rerank` braucht einen Reranker. Mit `rag_reranker: off` oder mit `auto` auf einem Rechner mit weniger als 6 GB RAM
  gibt es keinen, und der Befehl stoppt mit Exit-Code 2 und dem Grund; setze `rag_reranker` auf eine Modell-ID, um
  trotzdem einen zu bewerten. Lässt sich der Reranker nicht laden, endet der Lauf mit Exit-Code 1, und eine Suche,
  in der der Reranker nicht lief, zählt als gescheitert. Die Schwelle ist `rag_rerank_min_score` (standardmäßig
  nicht gesetzt: die kalibrierte Schwelle des Rerankers; 0: keine).
- Ein Korpus, der sich nicht lesen lässt, oder ein Fall, der ein Dokument erwartet, das der Korpus nicht hat, stoppt
  mit Exit-Code 2. Lässt sich der Korpus nicht indizieren (zum Beispiel im Offline-Modus, bevor das Embedding-Modell
  heruntergeladen wurde), endet der Befehl mit Exit-Code 1.

Außerhalb der Evaluation nutzen nur Build-Empfehlungen den Reranker. Dittos Chat und das MCP-Tool
`search_knowledge` sortieren nur mit `rag_rerank_default: true` neu (Standard aus).

```bash
# Die hybride Suche mit dem eingestellten Embedding-Modell
llm-cli ditto eval --kind retrieval

# Hybride Suche und Reranker nebeneinander, Bericht als JSON
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json

# Nur zwei Fälle
llm-cli ditto eval --kind retrieval --case retr-en-rknn-onnx-convert --case retr-en-cuda-oom
```

### ditto runtime

Der verwaltete llama.cpp-Server für Dittos Offline-Modus. Diese Befehle funktionieren ohne Docker und ohne
Schlüsselbund (im Docker-Modus brauchen `start` und `stop` Docker; siehe `ditto runtime ensure`). Dateien:
`~/.llm-framework/runtime/llama.cpp/` (Builds, Zustand und `mode.json`) und `~/.llm-framework/logs/llama-server.log`. Die optionalen MCP-Tools `build_local_runtime`, `start_local_runtime`,
`stop_local_runtime` und `run_benchmark` tun dasselbe für einen KI-Client, sobald du sie freigibst
([Details](Features_DE.md#laufzeit-tools-für-ki-clients-optional)); `install --prebuilt` und das Installieren von
Toolchain-Teilen bleiben Befehle nur für dich.

#### ditto runtime status

```
llm-cli ditto runtime status [--json]
```

Zeigt die Art, wie der Server läuft (direkt oder in Docker, im Docker-Modus mit Dittos Image); die erkannte
Hardware; den direkten Build mit seiner Herkunft (`self-built`, `imported` oder `prebuilt, not
self-built`), Build-ID, llama.cpp-Commit, Backend, wo und wie lange er gebaut wurde, wann er geprüft wurde und welche
Geräte er gefunden hat; warum ein gespeicherter Build nicht genutzt werden kann; ob ein neuer Build nötig (die
Festlegung hat sich geändert) oder empfohlen ist (der Bauplan dieses Rechners hat sich geändert); das GPU-Backend,
das das Bau-Rezept für diesen Rechner wählt, und warum; welches offizielle vorgebaute Archiv `--prebuilt`
installieren würde und warum (oder warum keines passt, zum Beispiel bei musl oder einer älteren glibc); ob der Server
läuft (Adresse, Prozess, Modell, Kontext); das empfohlene Modell und warum; und die Log-Datei. Die Prüfung auf einen
neuen Build erkennt die Toolchain (sie kompiliert nichts).

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe (`installed`, `problems`, `rebuild`, `recommended_backend`, `prebuilt_option`, ...). |

In der GUI: **Ditto > Lokale Laufzeit...**, Bereich „Status“ (dieselben Werte als Text in deiner Sprache; „Noch einmal
prüfen“ liest sie neu). In **Tools > Configure AI Agent (Ditto)...** öffnet „Status und mehr...“ dasselbe Fenster.

#### ditto runtime install

```
llm-cli ditto runtime install [--backend auto|cpu|vulkan|cuda|rocm|metal] [--placement auto|native|container]
                              [--jobs N] [--prebuilt] [--force] [-y]
```

Baut `llama-server` (mit `llama-cli`, `llama-bench` und `llama-perplexity`) auf diesem Rechner aus dem festgelegten
llama.cpp-Commit (`configs/llama_runtime.yml`, `llama_cpp.source`). Es untersucht den Rechner, erkennt die Toolchain,
zeigt die geplanten Builds und fragt. Der CPU-Build wird zuerst gebaut, geprüft und aktiv gemacht (er bleibt der
Ersatz); danach wird der GPU-Build gebaut, den das Rezept wählt, und aktiv, sobald er geprüft ist. Ein GPU-Build, der
hier noch nicht gebaut werden kann oder scheitert, lässt den CPU-Build aktiv und wird gemeldet. Jeder Build wird auf
diesem Rechner geprüft, bevor er behalten wird: `--version` nennt die festgelegte Build-Nummer und den Commit,
`--list-devices` zeigt das geplante Gerät, und der Server lehnt `--tools` ab. Ein schon gespeicherter Build mit
derselben Build-ID wird wiederverwendet, nicht neu gebaut. Ein laufender Server läuft weiter; der nächste
`runtime start` nutzt den neuen Build.

- **Toolchain:** GCC oder Clang, CMake 3.15 oder neuer und Ninja (Debian/Ubuntu: `build-essential cmake
  ninja-build`). Ohne sie läuft der Build im Builder-Container des Frameworks (Docker, `--network none`). Ohne beides
  nennt der Befehl die Auswege und endet mit 1. Für dich wird nichts installiert.
- **Quellcode:** Der festgelegte Commit wird einmal geholt (etwa 40 MB Download, 215 MB auf der Platte), nachdem du
  zugestimmt hast, und über seinen Commit geprüft; auf einem Rechner ohne Netzwerk importiere ihn vorher
  (`ditto runtime import-source`). Konfiguration und Build laufen ohne Netzwerk.
- **Backends in dieser Version:** CPU und Vulkan unter Linux. CUDA- und ROCm-Builds sowie Windows und macOS folgen;
  auf einem Rechner mit NVIDIA-GPU läuft bis dahin der CPU-Build (`--backend vulkan` baut einen Vulkan-Server für
  die GPU).
- **Exit-Code 1** auch, wenn ein ausdrücklich verlangtes GPU-Backend nicht gebaut werden konnte (der CPU-Build ist
  aktiv).

| Option | Bedeutung |
|---|---|
| `--backend [auto\|cpu\|vulkan\|cuda\|rocm\|metal]` | GPU-Backend, das nach dem CPU-Build gebaut wird (Standard `auto`: das Bau-Rezept wählt; `cpu`: nur der CPU-Build). Mit `--prebuilt`: das zu installierende Archiv. |
| `--placement [auto\|native\|container]` | Wo der Build läuft (Standard `auto`: nativ, wenn die Toolchain vollständig ist, sonst im Builder-Container). |
| `--jobs INTEGER RANGE` | Parallele Compiler-Jobs, 1 bis 256. Standard: aus den CPUs und dem freien Speicher. |
| `--prebuilt` | Stattdessen das offizielle vorgebaute Archiv von upstream herunterladen (Größe und sha256 vor dem Entpacken geprüft). Nur auf diese Anforderung, immer als „prebuilt, not self-built“ angezeigt. Ein schon installiertes Archiv, auch eines, das eine frühere Version heruntergeladen hat (es wird ohne diese Anforderung nicht genutzt), wird übernommen, ohne es neu herunterzuladen. Linux-Archive brauchen glibc 2.35 oder 2.39; bei musl wird keines angeboten. |
| `--force` | Mit `--prebuilt`: erneut herunterladen und entpacken, auch wenn es installiert ist. |
| `-y, --yes` | Vor dem Bauen, dem Holen des Quellcodes oder dem Download nicht fragen. |

In der GUI: **Ditto > Lokale Laufzeit... > llama-server bauen...** mit den Build-Optionen des Bereichs „Dittos lokaler
Server“ (GPU-Backend, Bauen, Compile-Jobs); `--prebuilt`: **Offizielles fertiges Archiv installieren...**, `--force`:
„Noch einmal herunterladen, auch wenn es installiert ist“. Die Fragen haben „Nein“ als Vorgabe; Abbrechen hält vor dem
nächsten Build-Schritt oder zwischen zwei Download-Stücken an.

#### ditto runtime ensure

```
llm-cli ditto runtime ensure [--mode auto|bare-metal|docker] [--force] [--placement auto|native|container]
                             [--backend ...] [--jobs N] [--no-model] [-y]
```

Installiert Dittos lokalen Modellserver, wenn er fehlt, direkt auf dem Rechner (bare metal) oder in Docker. Ein
zweiter Aufruf tut nichts: Er sagt, was er gefunden hat, und hört auf. Der Ditto-Schritt der Installer führt diesen
Befehl aus.

- **Was er findet:** einen direkten Build, der seine Prüfungen auf diesem Rechner bestanden hat (oder das offizielle
  Archiv, das du mit `install --prebuilt` gewählt hast), und Dittos Image des festgelegten llama.cpp-Commits, hier
  gebaut (nie eines aus einer Registry) und in einem Container aus ihm selbst geprüft. Solange Docker nicht
  erreichbar ist, bleibt der Docker-Modus, wie du ihn gewählt hast. Der Befehl entfernt unterwegs nichts: Kein alter
  Build wird durch Aufräumen entfernt.
- **Wenn nichts installiert ist:** `--mode auto` installiert direkt (ohne den Aufwand von Docker); geht das hier
  nicht, nimmt er Docker und sagt warum. Mit `--mode bare-metal` oder `--mode docker` wählst du selbst.
- **Docker-Modus:** Dittos Image wird auf diesem Rechner aus demselben festgelegten Rezept gebaut, auf einem Docker
  Official Image als Basis, per Digest festgelegt (`configs/llama_runtime.yml`, `build_bases`). Es wird nie als
  fertiges Image geholt und nie hochgeladen. Die Programme kommen aus dem direkten Build, wenn die C-Bibliothek des
  Basis-Images passt („repackaged“, umgepackt), oder aus einem neuen Build im Builder-Container („rebuilt“, ohne
  Netzwerk beim Bauen, geprüft in einem Container der Laufzeit-Basis). Jedes Image wird in einem Container aus ihm
  selbst geprüft: Version und Commit der Festlegung, die Geräte, die es sieht, und dass der Server `--tools` ablehnt.
  Der Server-Container veröffentlicht seinen Port nur auf 127.0.0.1, bindet den Modellordner und die
  Schlüsseldatei des Starts schreibgeschützt ein, läuft als dein Benutzer ohne Capabilities und mit schreibgeschütztem
  Wurzeldateisystem, und Docker entfernt ihn, wenn er stoppt. Die GPU wird durchgereicht, wo das geht (NVIDIA über das
  NVIDIA Container Toolkit, AMD und Intel über `/dev/dri`); wo nicht, sagt der Befehl warum, und das Image bekommt
  den CPU-Build. Der Docker-Modus braucht in dieser Version Linux.
- **`--force`** wechselt zu `--mode`, obwohl Ditto in der anderen Art installiert ist. Es braucht ein Terminal (der
  Wechsel ist deine Entscheidung). Die Modelle bleiben und werden nicht noch einmal heruntergeladen; nur der Server
  wird neu erzeugt, umgepackt, wo das geht: der direkte Build ins Image, oder der Build des Images in die direkte Art,
  nachdem er seine Prüfungen auf diesem Rechner bestanden hat. Wo das nicht geht, sagt der Befehl warum und baut neu.
  Der Server der alten Art wird gestoppt.
- **Zuerst der Platz:** Direkt und Docker sind zwei Alternativen. Bevor die neue Art neben der alten gebaut wird,
  schätzt der Befehl, was sie beim Bauen braucht und was danach bleibt, und vergleicht das mit dem freien Platz jedes
  beteiligten Laufwerks (Dittos Laufzeitordner; Dockers Image-Speicher, wenn Docker einen Ordner dieses Rechners
  nennt; der temporäre Ordner, in dem der Build-Kontext des Images entsteht). Die Schätzungen werden angezeigt. Passt
  sie nicht, wird nichts gebaut. Du siehst die Größen und wählst: selbst Platz schaffen und den Befehl noch einmal
  ausführen, oder die alte Art zuerst löschen. Dafür listet der Befehl jedes Objekt der alten Art auf und fragt
  einmal („Nein“ ist vorgewählt); Ditto ist nicht verfügbar, bis die neue Art funktioniert, und die Modelle bleiben.
  Das Löschen der alten Art wird nur angeboten, wenn die neue Art danach passt: Der Befehl schätzt sie noch einmal
  ohne den Build der alten Art (ein gelöschter direkter Build lässt sich nicht umpacken) und zählt den Platz, den die
  Objekte der alten Art freigeben. Er fragt nur an einem Terminal; ohne Terminal wird nichts gelöscht und nichts
  gebaut. Scheitert die neue Art danach, sagt der Befehl das und bietet an, die alte Art wieder herzustellen.
- **Die alte Art verschwindet ganz:** Sobald die neue Art funktioniert, wird jedes Objekt der alten Art aufgelistet:
  ihre Build-Ordner (vollständiger Pfad, Ordnername, Build-ID), Dittos Images und Container (Name und ID), ihre
  Einträge in `mode.json` und `active.json` und das Log der Image-Builds, jeweils mit Größe und was es ist. Eine Frage
  löscht genau diese Liste endgültig, nicht in den Papierkorb („Nein“ ist vorgewählt, keine Auswahl je Objekt). Was
  beide teilen, bleibt: die Modelle, der festgelegte Quellcode, die Builder-Images des Frameworks und die
  Laufzeit-Builds eines Boards. Ohne Terminal wird nichts entfernt, und die Liste wird angezeigt.
- **Das Modell:** Ist das Modell der gewählten oder empfohlenen Stufe noch nicht heruntergeladen, werden zuerst
  Quelle, Größe und Lizenz angezeigt. Heruntergeladen wird es nach deinem Ja, oder mit `-y`, wenn seine Lizenz frei
  ist (Apache 2.0, MIT, BSD); jede andere Lizenz nimmst du selbst mit `llm-cli ditto models download` an. Beide Arten
  nutzen denselben Modellordner.
- Im Container-Build des Frameworks selbst (`LCF_CONTAINER_BUILD=1`) gibt es den Docker-Modus nicht.

| Option | Bedeutung |
|---|---|
| `--mode [auto\|bare-metal\|docker]` | `auto` (Standard): behalten, was installiert ist; ist nichts da, direkt, oder Docker, wenn direkt nicht geht. |
| `--force` | Nach der Platzprüfung zu `--mode` wechseln (bare-metal oder docker); sobald die neue Art funktioniert, wird jedes Objekt der alten Art nach deinem einen Ja endgültig gelöscht. Braucht ein Terminal. |
| `--placement [auto\|native\|container]` | Wo ein Build läuft, wie bei `install`. Im Docker-Modus kommt mit `native` ein nativer Build ins Image. |
| `--backend [...]` | GPU-Backend, wie bei `install`. |
| `--jobs INTEGER RANGE` | Parallele Compiler-Jobs eines Builds. |
| `--no-model` | Den Download des Modells nicht anbieten. |
| `-y, --yes` | Vor dem Holen des Quellcodes oder dem Download eines Modells unter freier Lizenz nicht fragen (Größe und Lizenz werden trotzdem angezeigt). Der Build selbst startet ohne Frage, wie die Installer es brauchen. Entfernt nie etwas. |

```bash
llm-cli ditto runtime ensure                              # installiert ihn, wenn er fehlt
llm-cli ditto runtime ensure --mode docker --force        # zu Docker wechseln
llm-cli ditto runtime ensure --mode bare-metal --force    # und zurück
```

In der GUI: Bereich „Dittos lokaler Server“ in **Ditto > Lokale Laufzeit...** und in **Tools > Configure AI Agent
(Ditto)...**: die Betriebsart (Automatisch, Direkt, In Docker), die Build-Optionen und „Danach Dittos Modell anbieten“
(ohne Haken: `--no-model`). Der Knopf heißt Installieren... (nichts installiert), Prüfen und reparieren... (die
installierte Art: geprüft und nur neu installiert, wenn sie sich nicht nutzen lässt; mit Automatisch ist es `--mode
auto`, das direkt bevorzugt, wenn nichts Nutzbares mehr da ist) oder Wechseln... (`--force`). Bevor der festgelegte
Quellcode geholt wird, stellt die GUI die Frage des Befehls („Nein“ als Vorgabe). In der ganzen GUI läuft immer nur
eine Aufgabe der Laufzeit.

#### ditto runtime import

```
llm-cli ditto runtime import DATEI [-y]
```

Importiert einen `llama-server`-Build, der mit `ditto runtime export` auf einem anderen Rechner derselben Art gemacht
wurde (gleiches Betriebssystem und gleiche Architektur, und eine mindestens so neue C-Bibliothek). Das Archiv wird
sicher entpackt (die Größe wird vorher begrenzt), jede Datei gegen sein Manifest geprüft, die Build-ID gegen seinen
Plan, der Plan gegen die Bauregeln (keine Download-Option, keine Subprozess-Unterstützung) und der Commit gegen die
Festlegung. Ein mit `-march=native` gemachter Build wird abgelehnt, wenn diese CPU von einem anderen Hersteller ist
oder ihr eine CPU-Funktion der bauenden CPU fehlt (etwa AVX-512): Er bräche bei der ersten Rechnung mit SIGILL ab.
Dann zeigt der Befehl, wo der Build gemacht wurde, und fragt, bevor eines seiner Programme läuft; danach
wird der Build hier wie ein lokaler geprüft und wird der aktive Build (ein CPU-Build auch der Ersatz). Importiere nur
Builds, die du selbst gemacht hast.

| Option | Bedeutung |
|---|---|
| `-y, --yes` | Nicht fragen, bevor die importierten Programme für die Prüfung laufen. |

In der GUI: **Ditto > Lokale Laufzeit... > Build importieren...** (Dateiauswahl; die Frage, bevor seine Programme
laufen, hat „Nein“ als Vorgabe).

#### ditto runtime export

```
llm-cli ditto runtime export DATEI
```

Schreibt den genutzten selbst gebauten Build nach `DATEI` (`.tar.gz`: seine Programme, `build.json` und
`manifest.json`, keine Logs) für `ditto runtime import` auf einem anderen Rechner derselben Art. Das vorgebaute
Archiv wird nie exportiert.

In der GUI: **Ditto > Lokale Laufzeit... > Build exportieren...** (Speichern-Dialog; `.tar.gz` wird angehängt, wenn der
Name nicht so endet).

#### ditto runtime prune

```
llm-cli ditto runtime prune
```

Listet die alten Builds von Dittos Server mit je einer Nummer (unveröffentlicht): ältere selbst gebaute Builds und
Installationen des vorgebauten Archivs einer anderen llama.cpp-Version, jeweils mit vollem Pfad, Größe und was es
ist. Nichts ist ausgewählt. Du gibst die Nummern der zu löschenden ein, wählst den Papierkorb (Standard) oder
endgültiges Löschen und bestätigst genau diese (Standard: nein). Das braucht ein Terminal; ohne eines wird nur
gelistet. Der aktive Build, der CPU-Ersatz, der Build, aus dem ein laufender Server gestartet wurde, und die Builds
deiner Boards (`llm-cli device runtime build`, derselbe Build-Speicher) stehen nie in der Liste, und ein Build, der
nach dem Anzeigen der Liste einer davon wurde, bleibt. `runtime install` löscht nichts: es sagt, wie viele ältere
Builds es behalten hat (vorher behielt es nur die zwei neuesten).

In der GUI: **Ditto > Lokale Laufzeit... > Ältere Builds entfernen...** (der Lösch-Dialog: voller Pfad, Dateiname, Größe
und was es ist; nichts angehakt, „Nein“ als Vorgabe; Papierkorb oder endgültig). **File > Trash...** stellt wieder her,
was im Papierkorb liegt.

#### ditto runtime start

```
llm-cli ditto runtime start [--model STUFE] [--ctx N] [--sleep-idle SEKUNDEN]
```

Startet `llama-server` auf 127.0.0.1 mit einem freien Port und einem neuen API-Schlüssel in einer privaten Datei, mit
einer geleerten Umgebung und ohne die Optionen, die die gemeinsame Startregel verbietet. Gestartet wird nur ein Build,
der seine Prüfung auf diesem Rechner bestanden hat, noch zu seinem Manifest passt und vom festgelegten Commit ist (der
aktive Build, sonst sein CPU-Ersatz), oder das vorgebaute Archiv nach `install --prebuilt`; ein selbst gebauter
Build, der nicht genutzt werden kann, weicht nie auf das vorgebaute Archiv aus. Der Server läuft weiter, nachdem der
Befehl zurückkehrt, bis `llm-cli ditto runtime stop`. Läuft dasselbe Modell schon aus demselben Build, wird es
weiterverwendet.

| Option | Bedeutung |
|---|---|
| `--model TEXT` | Modellstufe (siehe `ditto models list`); wird für spätere Starts gemerkt. `auto`: die Empfehlung für diesen Rechner. |
| `--ctx INTEGER RANGE` | Kontextlänge in Tokens, 256 bis 1048576. Standard: aus dem freien Speicher berechnet, höchstens 32768. |
| `--sleep-idle INTEGER RANGE` | Das Modell nach so vielen Sekunden ohne Anfrage entladen (0 = nie). Standard: 600. |

Das Modell muss vorher heruntergeladen sein (`ditto models download`). Ditto nutzt den Server als Anbieter `local`.

In der GUI: **Ditto > Lokale Laufzeit... > Starten** mit Modell (wie bisher; „Empfohlen für diesen Computer“ ist
`--model auto`; oder eine Stufe), Kontextlänge (automatisch oder ab 256) und „Modell entladen nach Leerlauf von“
(Voreinstellung oder Sekunden; 0 = nie). Das Fenster nennt das früher gewählte Modell.

#### ditto runtime stop

```
llm-cli ditto runtime stop
```

Stoppt den vom Framework gestarteten Server, auch Reste nach einem Absturz. Meldet „Not running.“, wenn keiner läuft.

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install -y
llm-cli ditto models download -y
llm-cli ditto runtime start --model small --sleep-idle 0
llm-cli ditto runtime stop
```

In der GUI: **Ditto > Lokale Laufzeit... > Stoppen**.

#### ditto runtime export-source

```
llm-cli ditto runtime export-source DATEI [--mirror URL] [-y]
```

Schreibt den festgelegten llama.cpp-Quelltext (den Commit in `configs/llama_runtime.yml`, `llama_cpp.source`) als
Git-Bundle nach `DATEI`, dazu `DATEI.json` mit dem Commit, dem Tag, Größe und SHA256 des Bundles und der
Framework-Version. Beide Dateien werden auf einen Rechner ohne Internetzugang getragen und dort importiert. Liegt der
Quelltext noch nicht im Zwischenspeicher, wird er zuerst geholt (etwa 40 MB Download, 215 MB auf der Platte): per
Commit (über seinen Tag nur, wenn der Server das Holen per Commit ablehnt), und in jedem Fall muss
`git rev-parse HEAD` den festgelegten Commit zurückgeben. Der geprüfte Baum bleibt schreibgeschützt in
`~/.llm-framework/runtime/llama.cpp/src/<commit>/`; spätere Läufe prüfen, dass darin nichts geändert oder
hinzugefügt wurde (auch keine Dateien, die Git ignoriert), und brauchen kein Netz. Eine relative `DATEI` gilt relativ
zum aktuellen Ordner. Braucht `git`.

| Option | Bedeutung |
|---|---|
| `--mirror URL` | Von diesem Spiegel holen statt aus dem festgelegten Repository. Der Commit wird genauso geprüft. |
| `-y, --yes` | Ohne Nachfrage holen, wenn der Quelltext noch nicht im Zwischenspeicher liegt. |

In der GUI: **Ditto > Lokale Laufzeit... > Quellcode exportieren...** mit dem optionalen Feld „Spiegel“ (`--mirror`);
die Frage, bevor der Quellcode geholt wird, hat „Nein“ als Vorgabe.

#### ditto runtime import-source

```
llm-cli ditto runtime import-source DATEI
```

Importiert den festgelegten llama.cpp-Quelltext aus einem Bundle von `export-source` nach
`~/.llm-framework/runtime/llama.cpp/src/<commit>/`. Git prüft jedes Objekt an seinem Hash, und der ausgecheckte
Commit muss der festgelegte sein, egal wer das Bundle mit welcher Git-Version erstellt hat. Ein Bundle mit einem
anderen Commit, ein Bundle, das nicht zu seiner `DATEI.json` passt (Größe oder SHA256), und ein beschädigtes Bundle
werden abgelehnt. Braucht `git` auch auf dem Rechner ohne Netz.

```bash
# Rechner mit Internet
llm-cli ditto runtime export-source llama.cpp-source.bundle -y
# Rechner ohne Netz (vorher llama.cpp-source.bundle und llama.cpp-source.bundle.json kopieren)
llm-cli ditto runtime import-source llama.cpp-source.bundle
```

In der GUI: **Ditto > Lokale Laufzeit... > Quellcode importieren...** (Dateiauswahl).

### ditto models

#### ditto models list

```
llm-cli ditto models list
```

Listet die festgelegten Modelle auf: Stufe, Datei, Größe, Lizenz, ob es heruntergeladen (und geprüft) ist, und
Hinweise („recommended“, „chosen“, „on request“).

In der GUI: **Ditto > Lokale Modelle...** (dieselbe Liste, mit der Empfehlung für diesen Computer und dem Grund).

#### ditto models download

```
llm-cli ditto models download [STUFE] [-y]
```

Lädt ein Modell in seiner festgelegten Hugging-Face-Revision herunter und prüft Größe und sha256. Ohne `STUFE` die
empfohlene Stufe für diesen Rechner. Unterbrochene Downloads werden fortgesetzt.

| Argument / Option | Bedeutung |
|---|---|
| `STUFE` | `small`, `small-q2`, `default`, `default-q2`, `large`, `large-iq2` oder `granite`. |
| `-y, --yes` | Vor dem Download nicht fragen. |

```bash
llm-cli ditto models list
llm-cli ditto models download small-q2
```

In der GUI: **Ditto > Lokale Modelle...** > **Empfohlenes Modell herunterladen...** (die Empfehlung, die das Fenster
zeigt; der Knopf ist an, sobald die Liste gelesen ist), oder eine Stufe auswählen und **Ausgewähltes
herunterladen...**; die Frage zeigt Repository, Datei, Lizenz, Größe und Ordner („Nein“ ist die
Vorgabe), der Download läuft im Hintergrund mit Fortschritt und **Abbrechen** (was schon heruntergeladen ist, bleibt
für die Fortsetzung).

---

## tools

Die Tools des Frameworks sind das, was Ditto und MCP-Clients verwenden: 16 nur lesende Tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_mojo_oracle`, `search_knowledge`, `list_knowledge_sources`, `read_knowledge_source`, `search_hf_models`,
`get_hf_model_info`, `list_local_files`, `get_status`, `get_job_status`, `list_devices`), 8, die auf diesem Rechner
etwas ändern (`start_build`, `cancel_build`, `resolve_healing`, `cancel_job`, `propose_healing_fix`,
`download_hf_file`, `create_deploy_package`, `run_mojo_miracle`), und `deploy_to_device`, das ein Gerät erreicht, dem
du vertraust (Stufe `external`: immer mit Frage). `run_doctor`, der Name von `run_mojo_oracle` in 3.0.0, bleibt
aufrufbar, wird aber nicht aufgelistet (unveröffentlicht). `gui` (unveröffentlicht) liest und füllt das offene
Framework-Fenster (Stufe `local_write`; seine lesenden Aktionen `read_form`, `wizard_state` und `show_window` werden
wie Lese-Tools entschieden, und deine Einstellung für `gui` gilt für alle seine Aktionen; siehe
[Funktionen](Features_DE.md)).

### tools list

```
llm-cli tools list [--caller ditto|mcp]
```

Jedes Tool mit seiner Stufe (`read`, `local_write`), der aktuellen Berechtigung (`allow`, `ask`, `deny`) und einer
Beschreibung, gefolgt von der eigenen Vorgabe des Tools, der höchsten Entscheidung, die eine Einstellung ihm geben
kann (`at most`), ob es neu ist (noch nicht gesehen: es nutzt seine eigene Vorgabe, bis du es gesehen hast) und ob
eine Bestätigung eine dauerhafte Freigabe anbieten darf. Ohne `--caller` zeigt es die gemeinsamen Berechtigungen und
nennt die Aufrufer, die eigene haben. Der Titel sagt, wenn die Berechtigungen noch nicht eingerichtet sind.

In der GUI: **Tools > Tool Permissions (Ditto / MCP)...**: die Spalte **Gilt jetzt** zeigt die Entscheidung jedes
Tools, mit dem Grund als Tooltip, und ein Hinweis nennt die Aufrufer mit eigenen Berechtigungen; **Wessen
Berechtigungen** zeigt die von Ditto oder den lokalen MCP-Clients (`--caller`).

### tools permissions

```
llm-cli tools permissions [--caller ditto|mcp | --token NAME] [--reset]
```

Stellt dieselben drei Fragen wie [`ditto setup`](#ditto-setup) und speichert die Antworten. Berechtigungen für
einzelne Tools, die in der GUI gesetzt wurden, bleiben erhalten. Vorher führt es jedes Tool mit seiner eigenen
Voreinstellung auf, der höchsten Entscheidung, die eine Einstellung ihm geben kann (`at most`), ob es seit dem
letzten Durchgang neu ist und ob eine Bestätigung eine dauerhafte Freigabe anbieten darf; das Speichern zählt jedes
aufgeführte Tool als gesehen. Der GUI-Dialog zeigt dieselben Angaben.

Es braucht eine Person an einem Terminal, mit und ohne `--reset` (wie `ditto setup`): Weitergeleitete Antworten werden
mit dem Exit-Code 2 abgelehnt. Jedes Speichern wird mit dem Akteur `cli` und den alten und neuen Entscheidungen
festgehalten, bevor es wirkt, und abgelehnt (Exit-Code 1, nichts geändert), wenn sich der Audit-Eintrag nicht
schreiben lässt oder die Berechtigungen anderswo gespeichert wurden (GUI, ein anderes Terminal), während du
geantwortet hast. Eine Berechtigungsdatei, die beschädigt, von Hand bearbeitet oder nicht signiert ist (auch eine von
3.0.0 gespeicherte), verbietet jedes Tool, bis du das hier erneut ausführst; die Fragen beginnen dann mit "deny".

| Option | Bedeutung |
|---|---|
| `--caller ditto\|mcp` | Die Berechtigungen eines Aufrufers setzen (Ditto oder lokale MCP-Clients über stdio) statt der gemeinsamen. Die ersten Berechtigungen eines Aufrufers beginnen mit den gemeinsamen, samt den Einstellungen für einzelne Tools. |
| `--token TEXT` | Stattdessen die Berechtigungen dieses [benannten MCP-Tokens](#mcp-token) setzen (nicht zusammen mit `--caller`). Braucht ein Terminal; bei einem an Ditto gebundenen Token wird zuerst gefragt, ob es eigene bekommen soll. Es führt dieselben Angaben zu den Tools für dieses Token auf. Protokolliert mit dem Akteur `cli`. |
| `--reset` | Zurück zu den empfohlenen Standardwerten (die Einrichtung fragt beim nächsten Mal erneut). Mit `--caller`: Dieser Aufrufer nutzt wieder die gemeinsamen Berechtigungen. Mit `--token`: zurück auf nur lesen. |

In der GUI: **Tools > Tool Permissions (Ditto / MCP)...** (dieselben Angaben je Tool, gespeichert mit dem Akteur
`gui`): **Wessen Berechtigungen** für `--caller`, **Auf die empfohlenen Standards zurücksetzen...** für `--reset`,
**Wieder die gemeinsamen Berechtigungen nutzen...** für `--reset --caller`, und im Abschnitt „MCP-Tokens (eines je
Client)“ **Berechtigungen...** für `--token` und **Zurück auf nur lesen...** für `--reset --token` (das Token ist
wieder ein neues Token, keins mit von Hand gesetztem Allow/Deny). Jedes Zurücksetzen nennt, was sich ändert, und fragt
zuerst („Nein“ ist vorgewählt); eine Änderung, die woanders gespeichert wurde, während du liest, wird abgelehnt. Die Spalte **Ask in
the framework** ist `--confirm-in-framework`, die Liste der dauerhaften Freigaben `--grants` und **Revoke selected
standing approval** `--revoke-grant`.

### tools audit

```
llm-cli tools audit [--limit N] [--details] [--verify] [--archive] [--summary]
```

Zeigt die letzten Tool-Aufrufe: Zeit, wer aufgerufen hat (in Worten: Ditto, alle lokalen stdio-Clients, das
MCP-Token '<Name>' oder du in der CLI oder GUI, mit dem Namen, den der Client selbst angegeben hat), das Tool mit
seinem Titel (und dem Namen, den die KI geschrieben hat, wenn das eine Schreibvariante war), die Entscheidung, ob ein
Mensch bestätigt hat, das Ergebnis, den Grundcode und wie der Mensch gefragt wurde, mit seiner Antwort (zum Beispiel
`elicitation: declined`).

| Option | Bedeutung |
|---|---|
| `--limit INTEGER` | Anzahl der Einträge (Standard 30). |
| `--details` | Zusätzlich, was die narrensichere Schicht repariert hat (Art und Feld), die Dauer, SHA-256 und Größe des Ergebnisses, Verweise auf frühere Einträge und den Sitzungs-Hash. |
| `--verify` | Stattdessen die Kette prüfen: geänderte oder entfernte Einträge und das Entfernen der neuesten Einträge (Anker-Datei). Exit-Code 0, wenn intakt, sonst 1. |
| `--summary` | Stattdessen Zahlen je Aufrufer: Aufrufe, Ablehnungen und die Meldungen, dass eine Aufgabe nicht machbar ist (`report_cannot_do`), nach dem Ergebnis der Prüfung durch das Framework: confirmed, contradicted, premature, unchecked. |
| `--archive` | Das Log und seinen Anker beiseitelegen (aufbewahrt als `audit.db.<UTC-Zeit>` und `audit.db.<UTC-Zeit>.anchor` im persönlichen Ordner) und mit dem nächsten Tool-Aufruf ein neues Log beginnen. Nutze es, wenn `--verify` oder `llm-cli mojo` ein Problem mit dem Log meldet. Lässt sich das Log nicht beiseitelegen (zum Beispiel, weil es gerade benutzt wird), endet der Befehl mit Exit-Code 1; schließe die GUI und die KI-Clients und versuche es erneut. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
llm-cli tools audit --summary
llm-cli tools audit --archive
```

In der GUI: **Security > Prüfprotokoll...**: dieselben Zeilen Seite für Seite (**Einträge pro Seite**, **Neuere**,
**Ältere**), **Details** für `--details`, **Unversehrtheit prüfen** für `--verify` (das Ergebnis in einfachen
Worten), **Archivieren...** für `--archive` (fragt zuerst; das alte Log bleibt neben dem neuen erhalten) und der
Reiter **Übersicht je Aufrufer** für `--summary`. Prüfung, Archivieren und Übersicht laufen im Hintergrund.

### tools approvals

```
llm-cli tools approvals [list]
llm-cli tools approvals show ID
llm-cli tools approvals approve ID
llm-cli tools approvals decline ID
```

Aufrufe von KI-Clients, die dich nicht selbst fragen konnten (der Client hat keine Formular-Elicitation, die Frage ist
länger als 4.000 Zeichen, oder du hast das Tool mit `tools permissions --confirm-in-framework TOOL` gesetzt). `list`
(auch ohne Unterbefehl) zeigt jeden wartenden Aufruf mit ID, Tool, Aufruferklasse, dem selbst angegebenen Namen des
Clients und der verbleibenden Zeit; `show` gibt die vollständige Frage aus; `approve` und `decline` zeigen sie erneut
und fragen „Approve this call?“ oder „Decline this call?“, bevor etwas entschieden wird. Bei der Freigabe führt das
Framework den Aufruf für den Client aus, unter dem Aufrufer des Clients; der Client liest das Ergebnis mit
`get_job_status`. Jeder Unterbefehl braucht ein Terminal (sonst Exit-Code 2); eine Entscheidung wird abgelehnt
(Exit-Code 1), wenn die Freigabe verfallen ist (15 Minuten), schon entschieden wurde, die Sitzung ihres Clients
beendet ist, sich ihre Frage inzwischen geändert hat oder das Audit-Log die Entscheidung nicht festhalten kann. Auf
einem Rechner ohne Bildschirm, etwa im Docker-Image, führst du es dort in einem Terminal aus
(`docker exec -it <container> llm-cli tools approvals`). Die GUI zeigt dieselben Aufrufe unter
**Tools > Pending Approvals...**.

In der GUI: **Tools > Pending Approvals...** (**Approve once**, **Approve and do not ask again...** für `--always` mit
Umfang und Dauer, **Decline**).

`approve ID --always [--width this|any] [--duration revoke|30d|24h]` gibt den Aufruf frei und speichert eine
dauerhafte Freigabe („nicht mehr fragen“) im Rahmen, den das Framework dafür angeboten hat, nachdem Rahmen und
Warnung gezeigt wurden; `list` kennzeichnet die Aufrufe, bei denen sie angeboten wird, und die Anfragen, die der
Dialog eines Clients geschickt hat („asks to stop asking“), die nur mit einem Rahmen freigegeben werden. `--always`
wird abgelehnt, solange eine GUI-Sitzung des Frameworks läuft: Nutze ihr Freigabefenster. Für alle lokalen
stdio-Clients zusammen gibt es nur `--width this` und `--duration 30d` oder `24h`.

| Option von `tools permissions` | Bedeutung |
|---|---|
| `--confirm-in-framework TOOL` | Fragen zu diesem Tool immer im Framework stellen (im Freigabefenster oder mit diesem Befehl), nie im eigenen Dialog des KI-Clients. Wiederholbar; mit `--caller` für einen Aufrufer. Braucht ein Terminal; wird festgehalten. |
| `--confirm-anywhere TOOL` | `--confirm-in-framework` für dieses Tool zurücknehmen. |
| `--grants` | Die dauerhaften Freigaben jedes Aufrufers auflisten: was jede abdeckt, ihren Zustand (active, expired, lapsed, outdated), wann und wie sie angelegt wurde, ihre Nutzungen und letzte Nutzung (aus dem Audit-Log) und ihren Ablauf. |
| `--revoke-grant ID` | Eine dauerhafte Freigabe sofort widerrufen (braucht ein Terminal; wird festgehalten). `--reset` löscht alle, `--reset --caller` die dieses Aufrufers; das Widerrufen eines Tokens entfernt seine eigenen. |

### tools folders

```
llm-cli tools folders list
llm-cli tools folders add PATH --label LABEL [--kind model|dataset|any]
llm-cli tools folders remove LABEL
```

Die Ordner, deren Modell- und Datensatzdateien Ditto und MCP-Clients nutzen dürfen. Sie nennen nie einen Pfad,
sondern eine Datei als `<Label>:<relativer Pfad>`, zum Beispiel `models:qwen2.5-0.5b-instruct` oder
`mymodels:llama/model.gguf`. Der Modellordner (`models`, Einstellung `models_dir`) und der Datensatzordner
(`datasets`, Einstellung `datasets_dir`) sind immer da; mit `add` dürfen sie einen weiteren Ordner unter einem Label
nutzen, das du wählst (Kleinbuchstaben, Ziffern, `-` und `_`).

- `add` und `remove` brauchen ein Terminal (weitergeleitete Antworten werden abgelehnt), und `add` zeigt den Ordner
  und fragt vorher: Dateien darin können in Builds eingebunden werden, die Community- oder KI-geschriebenen Code
  ausführen, und können über Build-Logs KI-Clients erreichen.
- `add` lehnt `/`, dein Home selbst, einen Ordner, der den persönlichen Ordner des Frameworks enthält, und einen
  Ordner in einem gesperrten Ordner oder mit einem gesperrten Ordner darin ab (Schlüssel, Anmeldedaten, das
  Hugging-Face-Token, Systemordner; die Liste steht in den [Funktionen](Features_DE.md#mcp-server)). Der Unterordner
  `hub` des Hugging-Face-Caches lässt sich hinzufügen (`~/.cache/huggingface/hub`, wo `hf download` Modelle
  ablegt); die Token-Dateien daneben bleiben gesperrt. Dieselben Grenzen gelten für `models_dir` und
  `datasets_dir`: Zeigt eine der Einstellungen auf `/`, dein Home oder einen Ordner mit dem persönlichen Ordner
  darin, markiert `list` das Label als nicht nutzbar.
- Der echte Pfad des Ordners wird festgehalten, und zwar der, den `add` dir gezeigt hat: Zeigt ein Link während der
  Frage woandershin, wird nichts hinzugefügt. Ändert er sich später, markiert `list` ihn als nicht nutzbar, und
  KI-Clients werden abgewiesen, bis du `add` mit demselben Label erneut ausführst.
- Jede Änderung landet im Audit-Log (Akteur `cli`; `gui` für dieselbe Liste in der GUI unter Tools > Folders the AI
  May Use...); lässt sich das Hinzufügen nicht festhalten, wird der Ordner nicht hinzugefügt. Die Liste liegt in
  `~/.llm-framework/ai_folders.json`, nur für dich lesbar.

```bash
llm-cli tools folders add ~/llm-models --label mymodels --kind model
llm-cli tools folders add ~/.cache/huggingface/hub --label hub
llm-cli tools folders list
llm-cli tools folders remove mymodels
```

In der GUI: **Tools > Folders the AI May Use...**: **Add folder...** fragt nach dem Ordner, seinem Label und was darin
liegt (`--kind`: Modell-Dateien, Datensatz-Dateien oder beides), dann folgt dieselbe Warnung; **Remove** für
`remove`.

### tools crawl-notice

```
llm-cli tools crawl-notice
```

Zeigt den einmaligen Deep-Ingest-Hinweis (was der Crawler liest, welche Opt-outs er beachtet, dass das Wissen auf
diesem Rechner bleibt) und fragt, ob du ihn gelesen hast. KI-Clients und Ditto können erst dann darum bitten,
Dokumentations-Websites zu crawlen (`crawl_docs_site`), wenn eine Person ihn gelesen hat, hier oder im Deep Ingest
der GUI; kein Tool kann ihn bestätigen. Der Befehl braucht ein Terminal (weitergeleitete Antworten werden
abgelehnt); die Bestätigung wird mit `via: cli` in `~/.llm-framework/crawl_notice.json` gespeichert und ins
Audit-Log geschrieben (Akteur `cli`). Ist der Hinweis schon gelesen, wird er gezeigt und nichts ändert sich; nach
einer Änderung seines Textes muss er erneut gelesen werden.

In der GUI: **Tools > Hinweis zu Deep Ingest...** zeigt den Hinweis jederzeit; solange er noch nötig ist, bittet es
dich zu bestätigen, dass du ihn gelesen hast („Nein“ ist vorgewählt), und hält das mit `via: gui` fest (Akteur `gui`).
Der Deep Ingest des Modul-Assistenten zeigt ihn vor dem ersten Crawl, auch solange die Wissensdatenbank aus ist.

---

## mcp

### mcp serve

```
llm-cli mcp serve [--transport stdio|http] [--host HOST] [--port PORT] [--container]
```

Betreibt den MCP-Server. Normalerweise startet ihn der KI-Client für dich (siehe `mcp config`). stdout trägt das
Protokoll, Logs gehen nach stderr. Sind die Tool-Berechtigungen noch nicht eingerichtet, funktionieren nur lesende
Tools ohne Nachfrage, alles andere fragt (kann der Client nicht fragen, wartet die Frage im Framework: siehe
[tools approvals](#tools-approvals)).

Solange das Framework-Fenster (die GUI) offen ist, erreicht das Tool `gui` seine Formulare über eine private Brücke,
die nur dein Konto nutzen kann (unveröffentlicht): Der Client liest und füllt sie wie Ditto, und jedes Feld, das er
ausfüllt, wird mit dem Namen seines MCP-Tokens oder mit dem Namen markiert, den der Client sich selbst gibt. Ist kein
Framework-Fenster offen, antwortet `gui` mit dem Blocker `gui_not_open` („Das Framework-Fenster öffnen.“). Dafür musst
du nichts einrichten.

| Option | Bedeutung |
|---|---|
| `--transport [stdio\|http]` | `stdio` (Standard): vom Client gestartet. `http`: lokales Streamable HTTP mit einem benannten Token je Client. |
| `--host TEXT` | Nur HTTP; nur Loopback-Adressen (Standard `127.0.0.1`; außerdem `::1`, `localhost`). |
| `--port INTEGER` | Nur HTTP (Standard 8765). |
| `--container` | Nur HTTP in einem Container (Docker, Podman): bindet die Schnittstellen des Containers statt Loopback; außerhalb eines Containers abgelehnt. Veröffentliche den Port nur auf dem Loopback des Hosts (`-p 127.0.0.1:8765:8765`); benanntes Token, `Host`- und `Origin`-Prüfung gelten wie ohne die Option. |

Mit `--transport http` lauscht der Server auf `http://<Host>:<Port>/mcp`, und jede Anfrage braucht
`Authorization: Bearer <Token>` mit einem [benannten Token](#mcp-token); der Server startet nicht, bevor es eins gibt,
und kein Token wird angenommen (und der Server startet nicht), solange andere Benutzer die Token-Datei lesen oder
ändern können oder sie einem anderen Benutzer gehört. Anfragen mit `Origin`-Header (Webseiten, CORS-Preflights)
werden abgelehnt, der `Host`-Header muss `127.0.0.1`, `localhost` oder `[::1]` mit dem Port des Servers sein, und der
Server hält höchstens 8 Sitzungen, davon höchstens 4 je Token (damit ein Client die anderen nicht aussperren kann;
darüber hinaus wird eine neue Sitzung mit Status 503 abgelehnt), schließt eine Sitzung nach 10 Minuten ohne Anfrage
und nimmt höchstens 1 MiB pro Anfrage an. Er beachtet `X-Forwarded-For` und `X-Forwarded-Proto` nicht (kein lokales
Programm kann eine andere Adresse vorgeben) und schreibt kein Zugriffsprotokoll. Eine noch offene Anfrage (ein
Warten, ein Ereignisstrom) wird geschlossen,
sobald ihr Token widerrufen oder rotiert wird, und ebenso die Sitzungen dieses Tokens (eine Sitzung gehört dem
Token-Wert, der sie geöffnet hat; nach `rotate` öffnet der Client eine neue). Die alte gemeinsame Token-Datei
`mcp_http_token` wird nicht mehr akzeptiert.

Über stdio macht die Umgebungsvariable `LCF_MCP_TOKEN` im Server-Eintrag des Clients den Client zum Aufrufer dieses
benannten Tokens (`mcp:<Name>`, mit den Berechtigungen des Tokens) statt zum gemeinsamen Aufrufer `mcp`. `llm-cli`
nimmt sie aus seiner Umgebung, bevor irgendein Befehl läuft, gleich welcher (kein Befehl und kein Kindprozess erbt
sie), und gibt sie nur an `mcp serve` weiter, das mit Exit-Code 2 endet, wenn der Wert kein gültiges Token ist; ein
laufender Server antwortet nicht mehr, sobald sein Token widerrufen oder rotiert wird.

```bash
llm-cli mcp serve --transport http --port 8765
```

In der GUI: **Security > Verbindungen zu KI-Apps (MCP)...**, Kasten „Lokaler HTTP-Server“: **Adresse**, **Port** und
**Container-Bindung** (`--container`), **Server starten** und **Server stoppen**. Die GUI führt diesen Befehl als
eigenen Kindprozess aus, lehnt vor dem Start ab, was der Befehl ablehnt, zeigt Adresse, Zustand und die Ausgabe des
Servers und fragt, bevor das Framework schließt, solange der Server noch läuft („Nein“ lässt das Framework offen; „Ja“
stoppt den Server wie Strg+C, nach 15 Sekunden sofort). Den stdio-Transport startet die KI-App selbst.

### mcp config

```
llm-cli mcp config [--write claude-desktop]
```

Zeigt, wie du Claude Code (ein Befehl `claude mcp add ...`), Claude Desktop (JSON für dessen Konfigurationsdatei) und
VS Code (JSON für `.vscode/mcp.json`) verbindest, jeweils mit dem Python und dem Ordner dieser Installation. Ruf es
mit dem Python des Frameworks auf, damit der ausgegebene Befehl dieses verwendet. Der Befehl startet Python mit `-P`,
sodass nichts aus dem Ordner importiert wird, in dem der Client den Server startet. Unter Windows ist die
Befehlszeile so gequotet, dass cmd und PowerShell sie unverändert lassen; enthält ein Pfad `%`, `!`, `$`, einen
Backtick oder ein doppeltes Anführungszeichen, wird nur das JSON gezeigt.

| Option | Bedeutung |
|---|---|
| `--write [claude-desktop]` | Den Server in die Konfiguration von Claude Desktop eintragen (der Pfad steht in der Ausgabe; unter Linux wird `XDG_CONFIG_HOME` beachtet). Andere Einträge bleiben erhalten, und eine Sicherung (`.bak`) wird geschrieben. Lässt sich die Datei nicht schreiben, nennt der Befehl den Grund und endet mit Exit-Code 1. Danach Claude Desktop neu starten. |

```bash
llm-cli mcp config
llm-cli mcp config --write claude-desktop
```

An diesen Clients meldest du dich mit deinem eigenen Konto oder Schlüssel an; das Framework sieht das nie.

In der GUI: **Security > Verbindungen zu KI-Apps (MCP)...** zeigt die drei Einträge, jeden mit einer Schaltfläche
**Kopieren**, und den Pfad der Konfigurationsdatei von Claude Desktop. **In Claude Desktop eintragen...** (`--write
claude-desktop`) zeigt die Datei, ob es sie gibt, die Änderung, die Einträge, die bleiben, und die Sicherung, fragt
zuerst („Nein“ ist vorgewählt) und schreibt genau das; eine inzwischen geänderte Datei wird nicht überschrieben.

### mcp token

```
llm-cli mcp token add NAME [--policy-from CALLER | --link ditto]
llm-cli mcp token list
llm-cli mcp token rotate NAME
llm-cli mcp token revoke NAME
```

Benannte Tokens, eins pro Client, der sich über HTTP oder über stdio mit `LCF_MCP_TOKEN` verbindet. Ein Token wird
einmal gezeigt, wenn es angelegt oder rotiert wird; das Framework behält nur seinen SHA-256 in
`~/.llm-framework/mcp_tokens.json` (Modus 0600). Sein Name (1-32 Kleinbuchstaben, Ziffern und Bindestriche) ist der
Aufrufer `mcp:<Name>` im Audit-Log, und es hat eigene Berechtigungen. `add`, `rotate` und `revoke` brauchen einen
Menschen an einem Terminal (`add` und `rotate` geben außerdem nur auf ein Terminal aus) und werden mit dem Akteur
`cli` protokolliert, ohne das Token. Kein Tool kann Tokens anlegen, auflisten, ändern oder lesen. Die GUI kann
dasselbe unter Tools > Tool Permissions (Ditto / MCP), Abschnitt „MCP-Tokens (eines je Client)“ (protokolliert mit dem
Akteur `gui`).

| Befehl / Option | Bedeutung |
|---|---|
| `add NAME` | Legt ein Token an, das nur lesen darf, bis du mit `tools permissions --token NAME` mehr erlaubst. |
| `--policy-from CALLER` | Beginnt mit einer Kopie der Berechtigungen eines anderen Aufrufers: `mcp` (lokale stdio-Clients), `ditto` oder ein Token-Name; `mcp:NAME` meint immer ein Token, auch eins namens `mcp` oder `ditto`. Spätere Änderungen der Quelle gelten nicht. |
| `--link ditto` | Nutzt Dittos Berechtigungen per Verweis, für einen Client, der genau Dittos Berechtigungen haben soll: Eine Änderung an Dittos Berechtigungen gilt sofort. |
| `list` | Namen, Audit-Akteure, Berechtigungen, Zeit des Anlegens und Rotierens; nie ein Token oder sein Hash. |
| `rotate NAME` | Ein neues Token für denselben Namen und dieselben Berechtigungen; das alte funktioniert sofort nicht mehr. |
| `revoke NAME` | Löscht das Token, seine Berechtigungen und seine dauerhaften Freigaben nach einer Rückfrage; sein Client verliert sofort den Zugang. |

```bash
llm-cli mcp token add laptop                          # nur lesen
llm-cli tools permissions --token laptop              # mehr erlauben
llm-cli mcp token add helper --link ditto             # genau Dittos Berechtigungen
claude mcp add --transport http llm-cross-compiler http://127.0.0.1:8765/mcp --header "Authorization: Bearer <token>"
```

In der GUI: **Security > Verbindungen zu KI-Apps (MCP)...** und **Tools > Tool Permissions (Ditto / MCP)...**,
Abschnitt „MCP-Tokens (eines je Client)“: **Hinzufügen...** (nur lesen, eine Kopie der Berechtigungen der lokalen
stdio-Clients, Dittos oder der eigenen eines anderen Tokens, oder Dittos per Verweis), **Rotieren**, **Widerrufen** (das
Token, seine Berechtigungen und seine dauerhaften Freigaben, nach einer Rückfrage, die sie zählt), **Zurück auf nur
lesen...** und **Berechtigungen...**.
Ein Token wird einmal gezeigt, direkt nachdem es angelegt oder rotiert wurde; die Liste zeigt nur Namen.

---

## secrets

API-Schlüssel und Tokens werden verschlüsselt im Tresor für Zugangsdaten `secrets.store` in deinem persönlichen
Statusordner gespeichert (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`). Der Tresor liegt nie in einer
Git-Arbeitskopie. Sein Schlüssel liegt standardmäßig im Schlüsselbund deines Betriebssystems. Ohne nutzbaren
Schlüsselbund brechen Befehle, die das Framework brauchen, mit Exit-Code 5 ab und nennen die Möglichkeiten; einen
anderen Schutz wählst du mit [`llm-cli ditto auth storage --use`](#ditto-auth-storage), mit der Einstellung
`secret_storage` in `config.yml` oder für einen einzelnen Lauf mit der Umgebungsvariable `LLM_SECRET_STORAGE`:

| `secret_storage` | Woher der Schlüssel des Tresors kommt |
|---|---|
| `keyring` | Aus dem Schlüsselbund des Betriebssystems (Standard). |
| `helper` | Vom Befehl in der Einstellung `secret_helper`, z. B. `pass show llmcc/vault-key`, ohne Shell ausgeführt. |
| `passphrase` | Aus einer Passphrase, die einmal je Start abgefragt wird (mindestens 8 Zeichen), nie aus einer Datei oder Variable. |
| `insecure` | Aus einer Schlüsseldatei neben dem Tresor: **im Ruhezustand nicht verschlüsselt**. |
| `ephemeral` | Nur im Arbeitsspeicher, nichts wird gespeichert (für CI). |

### secrets set

```
llm-cli secrets set KEY [--with-token] [--password TEXT]
```

Speichert ein Geheimnis unter dem Namen `KEY`. Der Wert wird zweimal ohne Anzeige abgefragt oder mit `--with-token`
von stdin gelesen; so bleibt er aus deinem Shell-Verlauf. Die Schlüssel für Dittos Anbieter speicherst du besser mit
[`llm-cli ditto login`](#ditto-login), das sie auch prüft.

| Option | Bedeutung |
|---|---|
| `--with-token` | Den Wert von stdin lesen, z. B. `printf %s "$HF_TOKEN" \| llm-cli secrets set hf_token --with-token`. |
| `--password TEXT` | Veraltet: Der Wert landet in deinem Shell-Verlauf und in der Prozessliste. |

Namen, die das Framework verwendet:

| Name | Wofür |
|---|---|
| `anthropic_api_key`, `openai_api_key`, `gemini_api_key`, `openrouter_api_key`, `custom_api_key` | Dittos Anbieter (besser: `llm-cli ditto login <anbieter>`) |
| `hf_token` | Zugangsbeschränkte Hugging-Face-Modelle in Builds und die Hugging-Face-Tools der KI-Clients (`get_hf_model_info`, `download_hf_file`): nur im Framework gelesen, nie an eine KI weitergegeben; ohne es senden sie gar kein Token (nie eins aus `HF_TOKEN` oder dem Hugging-Face-Home-Ordner) |
| `qdrant_api_key` | Ein entfernter Qdrant-Server für die Wissensbasis (Einstellung `qdrant_url`) |
| `community_signing_key`, `community_age_identity` | Deine Schlüssel für Community-Pakete, beim ersten Mal von [`llm-cli zoo`](#zoo-keys) oder vom Community-Export und -Import der GUI angelegt; nicht von Hand setzen |

```bash
llm-cli secrets set hf_token
printf %s "$GITHUB_TOKEN" | llm-cli secrets set github_token --with-token
```

In der GUI: **Security > Geheimnisse...**, **Ein Geheimnis setzen**: ein Name aus der Liste (`hf_token`,
`qdrant_api_key`, die Schlüssel der Community-Pakete) oder ein anderer Name, der Wert zweimal in verdeckten Feldern,
**Speichern**. Im Speicher `ephemeral` sagt das Fenster, dass nichts gespeichert wurde.

### secrets list

```
llm-cli secrets list
```

Listet die gespeicherten Namen auf, nie die Werte. Die Schlüssel für Dittos Anbieter erscheinen unter ihren
Namen im Tresor, z. B. `cred/anthropic/api_key` für `anthropic_api_key`; `secrets set` nimmt beide Formen an.

In der GUI: **Security > Geheimnisse...** (die bekannten und die gespeicherten Namen, jeweils mit „gespeichert“ oder
„nicht gespeichert“ und wofür; nie ein Wert).

---

## module

Neue Hardware-Targets. Die vollständige Anleitung: [Module_Developer_Guide_DE.md](Module_Developer_Guide_DE.md).

### module create

```
llm-cli module create [--name TEXT] [--arch aarch64|x86_64|armv7l|riscv64] [--sdk TEXT] [--probe DATEI]
```

Legt ein neues Target per Fragen an: Name, Architektur und SDK (wenn nicht als Option angegeben), dann
Docker-Basis-Image (Standard `debian:bookworm-slim`), Beschreibung, Systempakete (Standard
`build-essential, cmake, git`) und CPU-Flags, die das Modul nur als Hinweis vermerkt und nie anwendet. Jeder Wert wird geprüft, bevor Dateien geschrieben werden, der
fertige Ordner muss die Target-Prüfungen bestehen
([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen)), und ein vorhandenes
Target wird nie überschrieben.

| Option | Bedeutung |
|---|---|
| `--name TEXT` | Name des neuen Targets. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU-Architektur. |
| `--sdk TEXT` | Besonderes SDK, z. B. `CUDA`, `RKNN` (Standard `None`). |
| `--probe DATEI` | Hardware-Probe des Targets (das Profil, das [`scan --device`](#scan) schreibt). Der Block `runtime:` der neuen `target.yml` ist dann der Plan des Build-Rezepts für die llama.cpp-Laufzeit des Boards daraus; eine Datei, die kein Probe-Profil ist, wird abgelehnt, bevor etwas geschrieben wird. |

Das Target wird in den eingestellten Targets-Ordner geschrieben (Einstellung `targets_dir`; ein relativer Pfad
gilt relativ zur Installation, nicht zum aktuellen Ordner). Seine `target.yml` hat einen Block `capabilities:` (Unterstützungsstufe `community`) mit dem Modulnamen als einzigem
Board und GGUF als einzigem Format; trag dort deine echten Boards und Formate ein. Der Build sind die vier
Stufenmodule (`source_module.sh`, `config_module.sh`, `convert_module.sh`, `target_module.sh`), die
`modules/build.sh`, der Stage-Runner, den die Build-Engine startet, der Reihe nach ausführt; die nächsten Schritte des
Befehls nennen die Stufen convert und target, in denen ein neues Target seine Umwandlung und Quantisierung ergänzt
([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#6-die-stufen-des-builds)).

In der GUI: **Tools > Create Hardware Module...**: Name, Architektur, SDK und **Beschreibung** auf der Hardware-Seite,
Basis-Image und Pakete auf der Docker-Seite, CPU- und CMake-Flags auf der Flags-Seite; der Standard-Import auf der
ersten Seite liest eine Probe-Datei (`--probe`). Nach **Finish** wird das neue Target wie mit `targets
validate` geprüft.

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto schlägt ein Target-Modul aus einer Hardware-Probe-Datei vor (`target_hardware_config.txt`, siehe
[scan](#scan)). Der ganze Vorschlag wird als JSON ausgegeben; erst nach deiner Bestätigung wird das Modul im
eingestellten Targets-Ordner angelegt (und vom Modulgenerator erneut geprüft). Ditto nutzt die gespeicherte
Anmeldung des Anbieters ([`llm-cli ditto login`](#ditto-login)); der Befehl fragt selbst nie nach einem Schlüssel.
Weder `module create` noch `module generate-ai` hängt Seiten aus der Wissensbasis an; das ist eine Option auf der
letzten Seite des GUI-Assistenten. Ist die Probe-Datei ein Probe-Profil, wird der Block `runtime:` des Moduls daraus
geplant; die CPU- und CMake-Flags des Vorschlags stehen dort als Hinweistext und werden nie angewendet. Ditto ist
experimentell: Vor dem ersten Lauf an einem Terminal geht der Hinweis zu Ditto einmal auf stderr ([ditto](#ditto)).

| Option | Bedeutung |
|---|---|
| `--provider TEXT` | Anbieter für diesen Lauf, z. B. `anthropic`, `openai`, `ollama`. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell für diesen Lauf. Standard: KI-Einstellungen. |

```bash
llm-cli scan
llm-cli module generate-ai target_hardware_config.txt --provider local
```

In der GUI: **Tools > Create Hardware Module...**, erste Seite: **AI Auto-Discovery (Ditto)**, mit Anbieter und Modell
**nur für diese Analyse** (`--provider`, `--model`; die gespeicherten KI-Einstellungen bleiben unverändert). Das
Basis-Image wird auf seinen Digest festgelegt, bevor du den Vorschlag siehst, und das Modul wird über dieselbe
Funktion wie bei diesem Befehl gespeichert, mit denselben Ablehnungen.

---

## scan

```
llm-cli scan [--output TEXT] [--json] [--script]
llm-cli scan --device HOST --user TEXT [--port N] [--password [WERT] | --password-stdin]
             [--trust-fingerprint TEXT] [--label TEXT]
             [--output TEXT] [--json]
llm-cli scan --import DATEI [--label TEXT]
```

Führt die Hardware-Probe aus und behält ihr Ergebnis, das Hardware-Profil (Schema 2, beschrieben im
[Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#11-die-datei-der-hardware-probe)). Ein Build wird für
den Rechner geplant, auf dem das Modell läuft, deshalb läuft die Probe auf diesem Rechner.

- **Dieser Rechner** (Standard): Die Python-Probe `scripts/hardware_probe.py` läuft in der CLI. Das Profil wird in die
  mit `--output` angegebene Datei geschrieben und als Profil dieses Rechners gespeichert
  (`~/.llm-framework/hardware/local.txt`, ohne flüchtige Werte wie den freien RAM). Kann die Python-Probe nicht
  laufen, sagt die CLI, warum, und führt stattdessen das Ersatzskript aus; `--script` nimmt das Skript direkt
  (`scripts/hardware_probe.sh` mit `sh` unter Linux, also auch auf Rechnern ohne bash wie Alpine oder BusyBox;
  `scripts/hardware_probe.ps1` unter Windows).
- **Ein Board per SSH** (`--device`): Beim ersten Kontakt zeigt die CLI den Fingerabdruck des SSH-Hostschlüssels und
  bittet dich, ihn zu bestätigen, wie bei `deploy run` (`--trust-fingerprint` bestätigt ihn ohne Nachfrage); ein
  Passwort wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu installieren, und nicht gespeichert. Dann
  schickt die CLI `hardware_probe.py` an `python3 -I`, wenn das Board Python 3.8 oder neuer hat, sonst
  `hardware_probe.sh` an `sh` (jede POSIX-Shell, auch BusyBox). Das Skript kommt über den SSH-Kanal, also wird auf dem
  Board nichts installiert und nichts zurückgelassen. Das Ergebnis wird wie jede Probe-Datei von außen geprüft, nach
  `--output` geschrieben und unter der ID des Boards gespeichert (`~/.llm-framework/hardware/devices/<ID>.txt`; die ID
  kommt aus dem Hostschlüssel des Boards), mit deiner Bezeichnung und seiner Adresse. Der SSH-Benutzer wird zum
  Deploy-Benutzer des Boards für [`device runtime deploy`](#device), wenn noch keiner gesetzt ist (nie root).
- **Die Probe-Datei eines Boards** (`--import DATEI`): Eine `target_hardware_config.txt`, die du von einem Board
  kopiert hast (dort von `scripts/hardware_probe.sh` oder `llm-cli scan` geschrieben), wird wie jede Probe-Datei von
  außen geprüft, unter einer neuen zufälligen ID mit deiner Bezeichnung gespeichert (Standard: das Board-Modell aus
  der Datei), und die CLI zeigt diese **Probe-ID** und das passende Target (über den SoC als Board eines Targets,
  dann über den Hersteller von SoC oder NPU, bei gleicher Architektur). Eine ältere Datei nach Schema 1 wird
  umgewandelt; was das Schema nicht kennt, darunter der Hostname, fällt weg. KI-Clients und Ditto nennen die Probe
  eines Boards nur über diese ID (`get_hardware_probe`, der Prompt `add_board`); kein Tool liest eine Probe über
  einen Pfad.

Auf diesem Rechner läuft die Probe ohne Terminal: Das Ersatzskript bekommt seine Eingabe aus `/dev/null`,
`NON_INTERACTIVE=true` und einen eigenen Arbeitsordner (`~/.llm-framework/hardware/work`, nie den aktuellen Ordner),
und eine Probe, die länger als 120 Sekunden braucht, wird beendet. Derselbe Dienst führt die Probe für das MCP-Tool
`probe_local_hardware` aus.

Die Probes fragen nie nach, installieren nichts und verlangen keine Administratorrechte. Exit-Code 1, wenn die Probe
scheitert oder ihre Datei nicht lesbar ist, 2 bei falschen Optionen.

| Option | Bedeutung |
|---|---|
| `--output TEXT` | Datei, in die das Profil geschrieben wird, relativ zum aktuellen Ordner (Standard `target_hardware_config.txt`); ihr Ordner wird angelegt. Mit `--script` gibt die CLI sie an das Skript weiter (`hardware_probe.sh --output DATEI`, `hardware_probe.ps1 -OutputFile DATEI`). |
| `--json` | Die JSON-Ansicht schreiben (dieselben Schlüssel und Werte als ein Objekt) statt `KEY=VALUE`-Zeilen. |
| `--script` | Das Ersatzskript statt der Python-Probe nehmen (nur für diesen Rechner). |
| `--device HOST` | Dieses Board per SSH untersuchen (Hostname oder IP-Adresse) statt dieses Rechners. |
| `--user TEXT` | SSH-Benutzer auf dem Board (nötig mit `--device`). |
| `--port N` | SSH-Port des Boards (Standard 22). |
| `--password`, `--password-stdin` | Nur für die erste Verbindung: wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu installieren, und nie gespeichert. Wie bei [deploy run](#deploy-run): ohne Wert verdeckt abgefragt (auch wenn du in einem Terminal nichts angibst) oder eine Zeile aus einer Weiterleitung; `--password WERT` ist veraltet und warnt. |
| `--trust-fingerprint TEXT` | Einem neuen Board ohne Nachfrage vertrauen, wenn sein Hostschlüssel diesen SHA256-Fingerabdruck hat. |
| `--label TEXT` | Deine Bezeichnung für das Board im Profilspeicher (mit `--device` oder `--import`). |
| `--import DATEI` | Die Probe-Datei eines Boards im Probe-Speicher ablegen und ihre Probe-ID zeigen (nicht zusammen mit `--device`). |

Ein Rechner ohne Netzzugang führt eine Probe von Hand aus, und du importierst ihre Datei
([README](../README_DE.md#die-hardware-des-boards-untersuchen)).

In der GUI: Tab Builder > **Scan / Import...**: **Scan Local Host** (die Python-Probe, als Profil dieses Rechners
gespeichert, wie `llm-cli scan` es tut), **Lokalen Rechner mit dem Ersatz-Skript scannen** (`--script`), **Lokalen
Rechner scannen und Profil speichern unter...** (`--output`; eine `.json`-Datei bekommt die JSON-Ansicht von `--json`),
**Lokalen Rechner mit dem Ersatz-Skript scannen und Profil speichern unter...** (`--script --output`),
**Remote Scan via SSH...** (`--device` mit **Port** und **Name** für `--port` und `--label`; dieselbe entfernte Probe,
also auch auf Boards, die `/tmp` mit `noexec` einhängen, und das Board kommt wie über die CLI zu den vertrauten
Geräten) und **Import from File (USB/Disk)...** (`--import`; fragt nach dem Namen des Boards, `--label`). Der
Host-Schlüssel und jede Probe laufen im Hintergrund; das Protokoll rechts zeigt das Ergebnis, seine Hinweise und wo das
Profil gespeichert wurde.

```bash
llm-cli scan                                        # dieser Rechner, gespeichert als „local“
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W" --output zero3w.txt
llm-cli scan --import zero3w.txt --label "ZERO 3W"   # eine von einem Board kopierte Probe-Datei
```

---

## device

Die Boards, die du mit `scan --device` untersucht hast, und die selbst gebaute llama.cpp-Laufzeit darauf
([Funktionen](Features_DE.md#laufzeit-auf-dem-board-selbst-gebautes-llamacpp)). `GERÄT` ist die ID eines Boards
(oder ihre ersten 4 oder mehr Zeichen), seine Bezeichnung oder seine gespeicherte Adresse, wie `device list` sie
zeigt. Der Build braucht Docker auf deinem PC; das Board braucht SSH, `tar`, `gzip` und `sha256sum` (BusyBox hat
sie) und einen unprivilegierten Benutzer.

```
llm-cli device list [--json]
llm-cli device settings GERÄT [--install user|systemd] [--user NAME] [--port N] [--model PFAD] [--confirm-sudo]
                              [--kv-cache auto|f16|q8_0|q4_0]
llm-cli device runtime plan GERÄT [--json]
llm-cli device runtime build GERÄT [--jobs N] [--mirror URL] [--yes]
llm-cli device runtime deploy GERÄT [--user NAME] [--password [WERT] | --password-stdin] [--bench-model PFAD]
                              [--allow-untrusted-model]
llm-cli device runtime verify GERÄT [--user NAME] [--bench-model PFAD] [--allow-untrusted-model]
llm-cli device runtime status GERÄT [--json]
llm-cli device runtime prune GERÄT [--on-device]
```

### device list

Die gespeicherten Boards: ID, Bezeichnung, Adresse, System (OS, Version, Architektur, C-Bibliothek mit Version,
Init-System), Deploy-Einstellungen und die deployte Laufzeit. `--json` für Skripte.

In der GUI: **Boards > Boards und Geräte...** (dieselben Zeilen, zusammen mit den vertrauten Boards).

### device settings

Zeigt oder ändert, wie die Laufzeit auf dem Board installiert wird. Das wird hier entschieden, vor einem Deployment;
ein Deployment fragt nie.

| Option | Bedeutung |
|---|---|
| `--install user` | Das Startskript in deinem Home auf dem Board (die Vorgabe). Mit ihm startest du den Server. |
| `--install systemd` | Zusätzlich eine systemd-Unit, die den Server beim Booten startet. Nur für Boards, deren Probe `INIT_SYSTEM=systemd` meldet; sie braucht `--model` und deine Bestätigung, gleich hier, dass Deployments auf dem Board `sudo -n` ausführen dürfen (am Terminal abgefragt oder `--confirm-sudo`; wird ins Audit-Log geschrieben). Wählst du wieder `user`, verfällt die Bestätigung. |
| `--user NAME` | Der SSH-Benutzer auf dem Board; der Server und jede Prüfung laufen als dieser Benutzer. root wird abgelehnt. |
| `--port N` | Der Loopback-Port des Servers der systemd-Unit (1024-65535, Vorgabe 8080). Das Startskript lauscht auf 8080, solange es kein `--port` bekommt; der Startbefehl, den ein Deployment ausgibt, enthält diesen Port. |
| `--model PFAD` | Die Modelldatei auf dem Board, die die systemd-Unit bereitstellt (relative Pfade liegen im Home des Benutzers). |
| `--confirm-sudo` | Mit `--install systemd`: ohne Nachfrage bestätigen. |
| `--kv-cache auto\|f16\|q8_0\|q4_0` | Der KV-Cache des Servers auf dem Board (`-ctk`/`-ctv`): `q8_0` braucht etwa halb so viel Speicher wie `f16`, `q4_0` etwa ein Viertel, mit etwas Qualitätsverlust. `auto` (die Vorgabe) wählt `q8_0`, wenn neben dem Modell auf dem Board wenig Speicher frei bleibt (aus seiner Probe), sonst `f16`. Wähle `f16`, um den quantisierten Cache auszuschalten. Die systemd-Unit startet den Server damit; für das Startskript gibt das Deployment den Startbefehl damit aus. |

Exit-Code 1, wenn das Board kein systemd hat oder die Bestätigung fehlt (dann wird nichts geändert), 2 bei einem
falschen Wert.

Beschädigte Deploy-Einstellungen (`device list` zeigt "damaged; set them again") werden nie als die Vorgaben gelesen.
Mit einer Option nennt der Befehl die Datei mit vollem Pfad und Größe und fragt, ob sie neu festgelegt werden sollen,
ausgehend von den Vorgaben ("Nein" ist die Vorgabe; ohne Terminal wird nichts ersetzt, Exit-Code 1); die Datei wird
erst überschrieben, wenn die neuen Einstellungen gespeichert werden.

In der GUI: **Boards > Boards und Geräte...** **> Einstellungen...** (die aktuellen Einstellungen vollständig; Installation, Benutzer, Port,
Modell und KV-Cache; dieselbe sudo-Frage mit "Nein" als Vorgabe, zuerst ins Audit-Log geschrieben; bei beschädigten
Einstellungen dieselbe Frage, bevor das Formular mit den Vorgaben öffnet).

### device runtime plan

Zeigt den Cross-Build, der aus dem eigenen Profil des Boards geplant wird: Architektur, C-Bibliothek mit Version,
die Builder-Basis mit ihrem GCC, `GGML_CPU_ARM_ARCH` oder die x86-Merkmale, OpenMP und `libgomp.so.1`, die
Programme, die Build-ID und ob er gebaut ist. `--json` gibt den ganzen Bauplan aus. Exit-Code 1, wenn sich kein
Bauplan machen lässt (zum Beispiel für ein Board, dessen glibc älter ist als jede Builder-Basis, oder ein Profil ohne
`LIBC_VERSION`: untersuche es erneut).

In der GUI: **Boards > Boards und Geräte...** **> Laufzeit-Plan**.

### device runtime build

Holt den festgelegten llama.cpp-Quelltext, wenn er nicht im Cache liegt (es fragt vorher; `--yes` fragt nicht,
`--mirror` holt von einem Spiegel, `ditto runtime import-source` bringt ihn auf einen Rechner ohne Internet), baut
den Bauplan im Builder-Container ohne Netz (`--jobs` setzt die parallelen Jobs; ohne richten sie sich nach dem
Speicher), prüft das Format der Programme auf deinem PC und packt den Tarball nach
`~/.llm-framework/runtime/llama.cpp/device/<ID>/llama-runtime-<Arch>-<Build-ID>.tar.gz`. Ein schon abgelegter Build
wird nur neu gepackt. Exit-Code 1, wenn Docker nicht antwortet, der Build scheitert oder seine Prüfungen scheitern.
Ältere Pakete des Geräts bleiben (unveröffentlicht; vorher entfernte ein Build oder ein Deployment sie); die Ausgabe
sagt, wie viele, und `device runtime prune` listet sie, damit du sie löschst.

In der GUI: **Boards > Boards und Geräte...** **> Laufzeit bauen...** (parallele Jobs, ein Quell-Spiegel und dieselbe Frage nach dem
Quelltext mit "Nein" als Vorgabe; es läuft im Hintergrund mit seinem Fortschritt, und Abbrechen hält es nach
dem laufenden Schritt an).

### device runtime deploy

Installiert die gebaute Laufzeit auf dem Board, wie die Deploy-Einstellungen es sagen, und prüft sie dort. Das Board
muss das sein, mit dem das Profil erstellt wurde (derselbe Hostschlüssel; ein neues Board unter der alten Adresse
wird abgelehnt), und ihm muss schon vertraut werden; `--password` koppelt ein Board, das noch keinen
Framework-Schlüssel hat (einmal genutzt, nie gespeichert; ohne Wert verdeckt abgefragt, `--password-stdin` liest es aus
einer Weiterleitung, und `--password WERT` ist veraltet und warnt). Der SSH-Benutzer kommt aus den Deploy-Einstellungen oder
aus `--user`.

1. Das Board wird untersucht: Benutzer-ID (root wird abgelehnt), Home-Ordner, Architektur (sie muss zum Build
   passen), Init-System und die Werkzeuge, die das Deployment braucht. Mit der systemd-Einstellung muss `sudo -n
   true` funktionieren; sonst wird nichts geändert. Mit `--bench-model PFAD` wird das Modell auf dem Board an seiner
   sha256 erkannt: ein Katalogmodell läuft sofort, jedes andere nur, wenn du zustimmst (am Terminal oder
   `--allow-untrusted-model`), und das wird ins Audit-Log geschrieben. Das wird hier gefragt, bevor sich auf dem
   Board etwas ändert.
2. Der Tarball kommt über den SSH-Kanal in einen privaten Staging-Ordner in
   `~/.local/share/llm-framework/llama.cpp/`, wird gegen seine sha256 und jede Datei gegen `SHA256SUMS` geprüft, nur
   für dich lesbar gemacht und neben den aktiven Build gelegt.
3. Das Startskript erzeugt den Schlüssel `~/.config/llm-framework/llama-server.key` auf dem Board (umask 077).
4. Der neue Build wird in seinem eigenen Ordner geprüft, als dein Benutzer: `--version` gegen den Pin,
   `--list-devices`, und `llama-server` muss `--tools` ablehnen; mit `--bench-model` auch ein kurzer
   `llama-bench`-Lauf mit diesem Modell.
5. Nur ein Build, der besteht, wird zu `current`. Ein Build, der scheitert (zum Beispiel mit `SIGILL`, weil die
   CPU-Grundlage nicht zum Board passt), wird wieder entfernt, der vorige Build bleibt aktiv, und sonst ändert sich
   nichts. Nach dem Umschalten bleibt jeder ältere Build auf dem Board (unveröffentlicht; vorher wurden alle außer
   dem vorigen ohne Frage entfernt). Die Ausgabe sagt, wie viele geblieben sind und wie groß sie sind;
   `device runtime prune GERÄT --on-device` listet sie, damit du sie entfernst.
6. Mit der systemd-Einstellung wird die Unit `llm-llama-server.service` mit `sudo -n` geschrieben und aktiviert und
   gestartet, sobald das Modell da ist.

Das Ergebnis liegt in `device/<ID>/deployed.json` (ein gescheitertes Deployment ändert es nicht). Exit-Code 1, wenn
ein Schritt oder eine Prüfung scheitert.

In der GUI: **Boards > Boards und Geräte...** **> Laufzeit installieren...** (der SSH-Benutzer, das Passwort jedes Mal abgefragt und nie
gespeichert, ein Modell für den kurzen Benchmark; ein Modell, das kein Katalogmodell ist, läuft nur nach einer
ausdrücklichen Warnung mit "Nein" als Vorgabe, und die Zustimmung steht im Audit-Log, bevor es geladen wird).

Auf dem Board startet das Startskript den Server:

```bash
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh start ~/models/model.gguf -c 4096 -t 4
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh status
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh stop
curl -s http://127.0.0.1:8080/health   # braucht keinen Schlüssel; jede andere Anfrage braucht "Authorization: Bearer <Schlüssel>"
```

Es lauscht auf 127.0.0.1 (Vorgabe-Port 8080, `--port` ab 1024) mit der Schlüsseldatei und einer geleerten Umgebung.
Es reicht `-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`, `-fa`, `-cram`, `-lm`, `-a`,
`--sleep-idle-seconds` und `-ctk`/`-ctv` (nur `f16`, `q8_0` oder `q4_0`) mit geprüften Werten weiter und lehnt
jede andere Option ab (Exit-Code 2). `serve` läuft im Vordergrund, `keygen` erzeugt den Schlüssel, `version`
gibt die Version des Servers aus.

### device runtime verify

Führt die Prüfungen aus Schritt 4 erneut für den aktiven Build `current` aus (auch mit `--bench-model`) und
aktualisiert den Eintrag.

In der GUI: **Boards > Boards und Geräte...** **> Prüfen...** (auch ohne Modell; ein anderer SSH-Benutzer für diese Prüfung).

Ein kurzer Benchmark mit `--bench-model` (hier oder mit `device runtime deploy`) ist der Beweis, dass ein Modell auf
diesem Board läuft. Sein Ergebnis wird bei jedem Build dieses Modells im Ausgabeordner vermerkt: die Datei
`device_tests.json` im Build-Ordner, gefunden über die sha256 des Modells (auch wenn die Datei auf dem Board anders
heißt). Sie enthält das Board (deinen Namen dafür, Board- und SoC-Modell aus seiner Probe), den Laufzeit-Build, die
gemessenen Geschwindigkeiten, den KV-Cache der Messung (wie `llama-bench` ihn meldet) neben dem, auf den der Server
des Boards eingestellt ist, und ob die Prüfungen bestanden. Das Deployment-Fenster zeigt den Build als
„getestet auf <Board>“. Ein Modell, das kein Build dieses Frameworks gemacht hat, wird nirgends vermerkt.

### device runtime status

Was deployt ist (Build, Zeit, Startskript oder systemd, Ergebnis der Prüfungen) und ob es noch der Build ist, den
das Profil des Boards, der Pin und das Rezept verlangen; wenn nicht, erneut bauen und deployen.

In der GUI: **Boards > Boards und Geräte...** **> Status**.

### device runtime prune

Listet die älteren Laufzeit-Pakete des Geräts auf deinem PC mit je einer Nummer (jeder Tarball und seine
`.sha256`-Datei, mit vollem Pfad und Größe). Das Paket des zuletzt deployten Builds steht nie in der Liste. Nichts ist
ausgewählt: Du gibst die Nummern der zu löschenden ein, wählst den Papierkorb (Standard) oder endgültiges Löschen und
bestätigst genau diese (Standard: nein). Das braucht ein Terminal; ohne eines wird nur gelistet. Auf dem Board ändert
sich nichts.

Mit `--on-device` meldet es sich am Board an (als Benutzer der Deploy-Einstellungen, mit dem Schlüssel des
Frameworks) und listet stattdessen die älteren Laufzeit-Builds dort: jeden mit vollem Pfad auf dem Board,
Ordnername, Größe und was er ist (der Build, der vor dem letzten Deployment aktiv war, sagt das). Der aktive Build
und ein Build, den ein laufendes Programm nutzt, stehen nie in der Liste. Du gibst die Nummern ein, nichts ist
ausgewählt, und du bestätigst genau diese (Standard: nein). Ein Board hat keinen Papierkorb, sie werden also
endgültig entfernt. Das Entfernen steht zuerst im Audit-Log, nur mit den Ordnernamen. Keine KI kann das: Die
KI-Tools sagen nur, wie viele ältere Builds geblieben sind.

In der GUI: **Boards > Boards und Geräte...** **> Ältere Laufzeit-Pakete entfernen...** und **> Ältere Builds auf dem Board entfernen...**
(der Löschdialog mit der vollständigen Liste, nichts angehakt, "Nein" ist die Vorgabe; die Liste des Boards wird
im Hintergrund gelesen).

Für ein [vertrautes Gerät](#deploy-devices) führen die optionalen MCP-Tools `probe_remote_device`,
`deploy_device_runtime` und `run_device_benchmark` für einen KI-Client `scan --device`, `device runtime build` mit
`deploy` und `device runtime verify --bench-model` aus, jedes von dir bestätigt; die Deploy-Einstellungen bleiben
immer deine (`device settings`).

```bash
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W"
llm-cli device runtime plan "ZERO 3W"
llm-cli device runtime build "ZERO 3W" --yes
llm-cli device runtime deploy "ZERO 3W" --bench-model models/qwen3.5-2b-q4_0.gguf
# Die systemd-Unit statt des Startskripts, vor dem nächsten Deployment entschieden
llm-cli device settings "ZERO 3W" --install systemd --model models/qwen3.5-2b-q4_0.gguf
```

---

## config

### config show

```
llm-cli config show
```

Gibt die wirksamen Einstellungen als JSON aus, so wie das Framework sie liest: jede Einstellung mit ihrem Wert
(`value`) und ihrer Herkunft (`source`: `default`, `file <Pfad>` für die `config.yml` oder eine mit `-c` angegebene
Datei, `environment` für eine Variable `LLM_FRAMEWORK_<KEY>`). Zugangsdaten werden geschwärzt: Tokens, Schlüssel und
Passwörter in Werten (zum Beispiel in einer URL) und jeder Wert unter einem Namen, der nach Zugangsdaten aussieht,
ob eine unbekannte Einstellung (`hf_token`) oder ein Schlüssel innerhalb einer Einstellung (`password`, `api_key`).
Der Befehl braucht weder den Tresor für Zugangsdaten noch Docker.

```bash
llm-cli -q config show | jq '.models_dir'
```

KI-Clients lesen Einstellungen mit dem MCP-Tool `get_settings` (eine feste Liste; Adressen als Schema und Host,
Ordner als Labels) und ändern gewöhnliche mit `update_settings`, das dich mit altem und neuem Wert fragt und die
Änderung in der `config.yml` speichert. Einstellungen, die begrenzen, was eine KI darf, lassen sich so nur
verschärfen; Berechtigungen, Geheimnisse, den Tresor, Einstellungen des KI-Anbieters, Adressen und Ordner änderst
nur du, hier oder in der GUI.

In der GUI: **Datei > Einstellungen…**, Tab **Wirksame Einstellungen** (nur lesen; Geheimnisse geschwärzt).

### config output-folder

```
llm-cli config output-folder [ORDNER]
```

Unveröffentlicht. Ohne `ORDNER` zeigt es den Ausgabeordner (Einstellung `output_dir`, Standard `~/llmcf/output`) und
den Build-Ordner des Frameworks (Einstellung `build_dir`). Mit `ORDNER` legt es den Ausgabeordner fest und speichert
`config.yml`, zum Beispiel auf einen Ordner auf einer großen zweiten Platte; der Ordner wird angelegt, wenn es ihn
noch nicht gibt. Ein Systemordner, der Programmordner des Frameworks, sein Build-Ordner und sein persönlicher Ordner
werden abgelehnt (Exit-Code 2), ebenso ein Ordner, der einen davon enthält (wie dein Home-Ordner), weil alles
unterhalb des Ausgabeordners aufgelistet wird. Builds im alten Ordner bleiben, wo sie sind, und werden weiter
aufgelistet. Eine KI
kann diese Einstellung nicht ändern.

```bash
llm-cli config output-folder /mnt/models/llmcf
```

In der GUI: **Datei > Output Folder...** (auch in **Datei > Einstellungen…** angeboten).

### config repos

```
llm-cli config repos [--add NAME URL] [--remove NAME]
```

Ohne Optionen listet es die Überschreibungen für Quell-Repositorys auf (Einstellung `source_repositories`). `--add`
und `--remove` ändern sie und speichern die `config.yml`; lässt sie sich nicht speichern, sagt der Befehl das und
endet mit Code 1. `--add` speichert einen Eintrag `{url: ...}`; die Build-Engine reicht jeden Eintrag, auch
einfache URLs aus früheren Versionen, als `<NAME>_REPO_OVERRIDE` an die Stufen-Container weiter (`NAME`: der ganze
Name in Großbuchstaben, jedes Zeichen außer ASCII-Buchstaben und Ziffern als `_`, also ergeben `llama.cpp`,
`llama_cpp` und `llama-cpp` alle `LLAMA_CPP_REPO_OVERRIDE`). Ein Name mit Punkt wird zusätzlich unter dem Namen
früherer Versionen weitergereicht, seinem Teil nach dem letzten Punkt (`sources.llama_cpp` ergibt auch
`LLAMA_CPP_REPO_OVERRIDE`). `--add` lehnt einen Namen ohne ASCII-Buchstaben und Ziffern ab (Exit-Code 2).
`--add` lehnt auch eine URL mit Zugangsdaten ab (`user:passwort@`, ein Token als Benutzername oder in einem Parameter
wie `?private_token=`, Exit-Code 2): Die `config.yml` ist reiner Text, deshalb gehören Passwörter und Tokens in den
Secrets-Speicher (`llm-cli secrets set NAME`), und die URL wird ohne sie eingegeben. Ein früher so gespeicherter
Eintrag funktioniert weiter; die Liste, `config show` und die GUI zeigen ihn nur mit `REDACTED` statt der
Zugangsdaten.

| Option | Bedeutung |
|---|---|
| `--add TEXT...` | Eine Überschreibung hinzufügen: `NAME URL`. |
| `--remove TEXT` | Die Überschreibung `NAME` entfernen. |

Die Überschreibungen wirken sich auf Rockchip-Builds nicht aus: Ein Build nutzt das llama.cpp seines Images, und
seine Stufe source (`source_module.sh`) warnt nur, wenn `LLAMA_CPP_REPO_OVERRIDE` gesetzt ist. Nur
`source_module.sh --clone-llama-cpp`, von Hand in einem Container mit Netzwerk gestartet, klont daraus.

In der GUI: **Datei > Einstellungen…**, Tab **Quell-Repositorys**: die Liste, **Hinzufügen** (Name und URL) und
**Ausgewählte entfernen…** (fragt zuerst; „Nein“ ist die Vorgabe).

---

## zoo

Der Community-Zoo: Hardware-Targets, Rezepte und Wissenspakete, die andere Nutzer teilen. Ein Rezept ist eine
„Modellvorlage“: ein Modell in einem festgelegten Commit, gebaut für ein Target, Board, Format und eine
Quantisierung, mit den Prüfsummen dessen, was herauskam. Das Framework lädt nie von sich aus etwas hoch; Teilen ist
immer ein Schritt, den du anstößt.

**Aktueller Stand:** Das Community-Repository ist festgelegt: Die Einstellung `community_repo` nennt
[Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base), und `zoo export --pr`
übergibt Pakete dorthin. Sein Beitrags-Workflow, sein signierter Index und der Schlüssel des Maintainers sind noch nicht
veröffentlicht: `community_index_url` bleibt leer, bis in diesem Repository der erste signierte Index liegt, und
`configs/community_keys.yml` enthält keinen Herausgeber-Schlüssel. Bis dahin zeigt `zoo list` nur installierte Pakete,
Installieren per ID bricht mit einer Meldung ab, und Pakete werden auch als Dateien geteilt (`zoo install --file`).

KI-Clients und Ditto nutzen denselben Zoo über die Tools `list_packs`, `add_pack_from_index` (nur signierter Index,
immer gefragt), `start_recipe_build` (ein installiertes Rezept, genau wie festgelegt, mit der Frage von
`start_build`) und `export_pack` (in eine lokale Datei, immer gefragt); sie können kein Paket aus einer Datei
installieren, keinen Pull Request öffnen und deine Schlüssel nicht anlegen
([Details](Features_DE.md#packs-für-ki-clients)).

So sind Pakete geschützt:

- Ein Paket ist eine ZIP-Datei mit einem Manifest (`pack.json`: sha256, Größe und Modus jeder Datei) und meist einer
  Ed25519-Signatur seines Autors (`pack.sig`). Mit diesem Framework exportierte Pakete haben immer eine; bei einem
  Paket ohne Signatur sagt die Zusammenfassung „not signed by its author“. Ein Paket enthält Skripte, Rezepte und
  Text, nie Modellgewichte oder andere Binärdateien.
- Die Liste der Pakete kommt aus einem signierten Index: `index.json` unter `community_index_url` und
  `index.json.sig` daneben, geprüft mit den Schlüsseln in `configs/community_keys.yml`, die jeweils bis zu ihrem
  Ablaufdatum gelten. Ein Index, der die Prüfung nicht besteht, wird nicht verwendet. Der letzte gültige Index wird
  in `~/.llm-framework/community/` zwischengespeichert, neu geholt, wenn er älter als einen Tag ist, und nie durch
  einen älteren ersetzt.
- Installieren per ID liest zuerst den aktuellen Index, damit kein Rückzug übersehen wird, und lädt das Paket
  festgelegt über Größe und sha256 herunter. Jedes Paket wird in einen Quarantäne-Ordner entpackt und dort geprüft:
  genau die aufgeführten Dateien mit ihren Prüfsummen, keine Links, die Target-Prüfungen
  ([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen)) und die Formate
  von Rezepten und Wissen. Erst wenn du die Zusammenfassung bestätigst, wird es an seinen Platz verschoben, mit einer
  Herkunftsdatei `.lcf-provenance.json`.
- Autorensignaturen zeigen, wer ein Paket gemacht hat. Der erste Schlüssel, der für einen Autorennamen auftaucht,
  wird gemerkt; ein späteres Paket mit diesem Namen, aber einem anderen Schlüssel, wird mit einer Warnung gezeigt.

Vertrauensstufen:

| Stufe | Bedeutung |
|---|---|
| `verified` | Im signierten Index: geprüft und unabhängig nachgebaut. |
| `community` | Im signierten Index: geprüft. |
| `local` | Eine Datei, die der signierte Index nicht aufführt. Niemand hat sie geprüft: Die Installation fragt immer nach, und jeder Build eines solchen Targets zeigt eine Warnung. |
| `revoked` | In der Liste zurückgezogener Pakete des Index. Es wird nicht installiert; installierte Kopien werden von `zoo list`, `zoo verify`, `llm-cli mojo` und dem Community Hub markiert und nicht gebaut, auch nachdem der Schlüssel abgelaufen ist, der den Rückzug signiert hat. |

Targets, die mit dem Framework ausgeliefert werden, etwa Rockchip, haben keine Herkunftsdatei und erscheinen nicht im
Zoo.

Installierte Targets landen im Targets-Ordner, Rezepte in `community/recipes/<ID>/` und Wissenspakete in
`community/knowledge/<ID>/` der Installation. Ein Wissenspaket wird direkt nach der Installation in deine
Wissensbasis importiert oder, wenn die Wissensbasis ausgeschaltet ist, sobald sie eingeschaltet wird.

In der GUI: **Tools > Community Hub öffnen** (Liste, Filter nach Art und Vertrauensstufe, „Import file...“, „Build“
bei einem installierten Rezept, **Details...**, **Installierte Pakete prüfen**, **Paketdatei prüfen...**,
**Exportieren...** und **Meine öffentlichen Schlüssel...**), die letzte Seite des Modul-Assistenten und das
Kontextmenü des Builder-Logs („Export a finished build for the community...“).

### zoo list

```
llm-cli zoo list [--kind target|recipe|knowledge] [--refresh] [--json]
```

Listet die Einträge des signierten Index (jeweils die neueste Version) und die installierten Pakete auf: Art, ID,
Version, Vertrauensstufe, installierte Version, Autor und Titel. Unter der Tabelle steht, welcher Index verwendet
wurde oder warum es keinen gibt (zum Beispiel „No community index: No community index is configured (setting
community_index_url).“); installierte Pakete werden in jedem Fall aufgeführt.

| Option | Bedeutung |
|---|---|
| `--kind [target\|recipe\|knowledge]` | Nur diese Art. |
| `--refresh` | Den signierten Index erneut herunterladen, auch wenn der zwischengespeicherte jünger als einen Tag ist. Ohne Netzwerk wird der zwischengespeicherte Index verwendet. |
| `--json` | Maschinenlesbare Ausgabe: eine Liste mit `kind`, `id`, `version`, `trust`, `installed_version`, `revoked`, `author` und `title`. |

In der GUI: **Tools > Community Hub öffnen** (der Filter nach Art ist `--kind`, **Refresh** ist `--refresh`).

### zoo show

```
llm-cli zoo show PACK_ID [--kind target|recipe|knowledge]
```

Zeigt einen Eintrag: Art, ID, Version, Autor und Lizenz, Vertrauensstufe (und warum es zurückgezogen wurde), wo es
installiert ist, die Download-Adresse mit Größe und sha256, die Beschreibung und bei einem installierten Rezept
dessen `recipe.yml`. Groß- und Kleinschreibung der ID spielt keine Rolle. Der Befehl liest nur den
zwischengespeicherten Index und die installierten Pakete; führe zuerst `zoo list --refresh` aus, um neue Einträge zu
sehen. Eine unbekannte ID endet mit Exit-Code 1.

| Option | Bedeutung |
|---|---|
| `--kind [target\|recipe\|knowledge]` | Die Art, wenn es die ID für mehrere Arten gibt. |

In der GUI: **Tools > Community Hub öffnen > Details...** für die gewählte Zeile (oder ein Doppelklick auf sie).

### zoo install

```
llm-cli zoo install PACK_ID [--kind target|recipe|knowledge] [--yes]
llm-cli zoo install --file FILE [--passphrase] [--from SCHLÜSSELDATEI ...] [--yes]
```

Installiert ein Paket aus dem signierten Index (per ID) oder aus einer Datei. Gib genau eines von beiden an (sonst
Exit-Code 2). Der Befehl gibt eine Zusammenfassung auf stderr aus und fragt „Install this pack?“ (Standard nein). So
sieht eine Zusammenfassung aus:

```
target my-board 1.0.0 by <author> (licence MIT)
Trust: community
Source: https://<address of the pack>
Files: 14 (48213 bytes)
Installs to: /opt/llm-cross-compiler-framework/targets/my-board
Note: signed by author key 0123456789abcdef (first pack seen from this author)
```

| Argument / Option | Bedeutung |
|---|---|
| `PACK_ID` | Eine ID aus dem Index (`zoo list`). Installiert wird die neueste Version. |
| `--kind [target\|recipe\|knowledge]` | Art von `PACK_ID` (Standard `target`). Eine Paketdatei nennt ihre Art selbst. |
| `--file FILE` | Eine Paketdatei installieren, die dir jemand gegeben hat, oder ein verschlüsseltes Paket (`.lcfpack`). Ihre Vertrauensstufe ist `local`, außer der signierte Index führt ihre sha256 auf. Funktioniert ohne Netzwerk. |
| `--from FILE` | Verschlüsselte Pakete: nur von diesen Absendern annehmen. Die Datei enthält die öffentlichen Schlüssel der Absender, wie `llm-cli zoo keys` sie ausgibt; wiederholbar. Ohne die Option wird jeder Absender angenommen; die Zusammenfassung nennt immer die Schlüssel-ID des Absenders. Bei einem Paket, das nicht verschlüsselt ist, bricht der Befehl mit Exit-Code 2 ab (ein einfaches Paket hat keinen Absender; seine Zusammenfassung zeigt die Signatur des Autors). |
| `--passphrase` | Verschlüsselte Pakete: nach der Passphrase fragen, statt das Paket mit deinem eigenen Schlüssel zu öffnen. |
| `--yes` | Bei einem Paket, dessen Vertrauensstufe aus dem signierten Index kommt, nicht nachfragen, auch bei einer Datei, die der Index führt. Eine Datei ohne Eintrag im Index ist `local` und wird immer bestätigt; ohne Terminal wird sie nicht installiert. |

Verhalten:

- Installieren per ID braucht das Netzwerk: Zuerst wird der Index neu heruntergeladen, und scheitert das, wird nichts
  installiert. Ein zurückgezogenes Paket wird abgelehnt.
- Ein verschlüsseltes Paket (eine age-Datei und die Signatur ihres Absenders, siehe
  [Funktionen](Features_DE.md#verschlüsselte-packs-zum-privaten-teilen)) wird vor dem Entschlüsseln gegen die
  Signatur geprüft. Es öffnet sich mit deiner eigenen age-Identität (siehe [zoo keys](#zoo-keys)) oder mit
  `--passphrase` mit der Passphrase; eine Passphrase mit einem scrypt-Arbeitsfaktor über 2^20 wird abgelehnt. Ein
  Paket mit Passphrase, das ohne `--passphrase` geöffnet wird, sagt das; deine eigene Identität wird nur für ein an
  Schlüssel verschlüsseltes Paket gelesen (und beim ersten Mal angelegt). **Import file...** im Community Hub öffnet
  beide: Es hat ein Feld für die Passphrase und **Nur diese Absender annehmen** mit Schlüsseldateien (`--from`).
- Nichts wird über einen vorhandenen Ordner gleichen Namens installiert, auch wenn sich nur die Groß- und
  Kleinschreibung unterscheidet; entferne den alten Ordner zuerst.
- Exit-Code 1: Eine Prüfung ist gescheitert (die Probleme werden aufgeführt und der Quarantäne-Ordner wird gelöscht),
  du hast mit Nein geantwortet, oder es gibt kein Terminal für ein Paket, das die Frage braucht.

```bash
llm-cli zoo install my-board
llm-cli zoo install smollm2-135m-instruct-rk3588-gguf-q4_k_m --kind recipe
llm-cli zoo install --file contribution-target-my-board-1.0.0.zip
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --from kollegin.keys
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --passphrase
```

In der GUI: **Tools > Community Hub öffnen**: **Install** in einer Zeile (`PACK_ID`), oder **Import file...** (`--file`,
mit dem Feld für die Passphrase und den Schlüsseldateien der Absender); die Zusammenfassung fragt mit „Nein“ als
Vorgabe.

Ein Rezept braucht sein Target: Installiere zuerst das Target, damit das Rezept auch gegen die Capabilities des
Targets geprüft wird (ohne das Target vermerkt die Zusammenfassung nur, dass es fehlt). „Build“ bei einem
installierten Rezept im Community Hub füllt den Builder aus, einschließlich des festgelegten Commits des Modells.

### zoo verify

```
llm-cli zoo verify [PACK_FILE]
```

Ohne Argument prüft es jedes installierte Community-Paket gegen den zwischengespeicherten Index, ohne etwas
herunterzuladen: zurückgezogen, seit der Installation verändert oder Lizenzdateien, die nicht zu seinen Seiten passen.
Es gibt eine Zeile pro Paket aus (✔, oder ✘ mit dem Grund) oder „No community packs installed.“. Exit-Code 1, wenn ein
Paket ein Problem hat, sonst 0.

Mit `PACK_FILE` prüft es eine Paketdatei, ohne sie zu installieren: Format, Dateiliste und Prüfsummen, die Prüfungen
ihrer Art (bei Wissen und beim Wissens-Snapshot eines Targets auch seine Lizenztexte und die `NOTICE`), eine
enthaltene Autorensignatur und dass die Datenschutz-Bereinigung nichts darin ändern würde. Es gibt „acceptable“ oder
jedes Problem aus (Exit-Code 1). Diese Form funktioniert ohne Schlüsselbund; die CI des Community-Repositorys führt
sie für jedes eingereichte Paket aus.

In der GUI: **Tools > Community Hub öffnen > Installierte Pakete prüfen** (ohne Argument) und **Paketdatei
prüfen...** (`PACK_FILE`).

```bash
llm-cli zoo verify
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip
```

### zoo export

```
llm-cli zoo export target|recipe|knowledge [SOURCE] --author NAME [Optionen]
```

Macht ein Paket aus deiner eigenen Arbeit: ein Target (`SOURCE`: der Ordnername des Targets), ein Rezept (`SOURCE`:
der Ordner eines fertigen Builds oder seine `build_info.json`) oder deine Wissensbasis (ohne `SOURCE`). Das Paket
wird datenschutzbereinigt, so geprüft, wie eine Installation es prüfen würde, mit deinem Schlüssel signiert und in
einen Ordner geschrieben; nichts wird gesendet. Die Zusammenfassung auf stdout zeigt die Datei, den Schlüssel, mit
dem signiert wurde, einen Diff für jede Datei, die die Datenschutz-Bereinigung geändert hat (höchstens 40 Zeilen je
Datei), und Hinweise.

| Argument / Option | Bedeutung |
|---|---|
| `target\|recipe\|knowledge` | Art des Pakets. |
| `SOURCE` | Der Ordnername des Targets oder ein Build-Ordner bzw. seine `build_info.json`. Pflicht für `target` und `recipe` (ohne: Exit-Code 2). |
| `--author TEXT` | **Pflicht.** Name, der als Autor angezeigt wird; ein Pseudonym ist in Ordnung. |
| `--license TEXT` | Lizenz des Pakets als SPDX-ID. Standard `MIT`; für Wissen `NOASSERTION`, weil die Seiten unter den Bedingungen ihrer Eigentümer bleiben. |
| `--model-license TEXT` | Rezepte: Lizenz-ID des Modells (Standard `unknown`). |
| `--id TEXT` | Wissen: ID des Pakets (Standard `knowledge-` und das heutige Datum, z. B. `knowledge-20261011`). |
| `--to FILE` | Das Paket zum privaten Teilen an die age-Empfänger (`age1...`) in dieser Datei verschlüsseln: was `llm-cli zoo keys` auf dem Rechner des Empfängers ausgegeben hat, oder ein öffentlicher Schlüssel aus `age-keygen`/`rage-keygen`; wiederholbar. |
| `--passphrase` | Das Paket zum privaten Teilen mit einer Passphrase verschlüsseln (zweimal abgefragt, mindestens 12 Zeichen). Nicht zusammen mit `--to` (Exit-Code 2): age verschlüsselt an Schlüssel oder an eine Passphrase. |
| `--out DIRECTORY` | Ordner für das Paket. Standard: `community/contributions/` in der Installation. |
| `--pr` | Das Paket an einen Pull-Request im Community-Repository übergeben (siehe unten). |
| `--yes` | Mit `--pr`: ohne Nachfrage nach der Zusammenfassung einreichen. |

Was in das Paket kommt:

- **target:** jede Datei des Target-Ordners. Links, Spezialdateien und Binärdateien werden abgelehnt, und die
  Target-Prüfungen müssen bestehen, wozu ein gültiger Block `capabilities:` gehört. Die Version ist
  `metadata.version` aus der `target.yml`, wenn sie die Form `1.2.3` hat (ein Zusatz wie `-rc1` ist erlaubt), sonst
  `1.0.0`. Ein Target, das du aus dem Zoo installiert hast, wird abgelehnt: Es bleibt das Paket seines Autors.
- **recipe:** nur ein fertiger Build eines Hugging-Face-Modells, dessen `build_info.json` den Commit des Modells und
  den Fingerabdruck des Targets enthält (Builds dieser Version). Ist das Target dein eigenes, erinnert die
  Zusammenfassung daran, es ebenfalls zu exportieren, sonst können andere das Rezept nicht bauen.
- **knowledge:** deine Seiten der Wissensbasis (sie muss eingeschaltet sein), datenschutzbereinigt. Jede Seite
  behält ihre Quelladresse; Seiten ohne Quelle bleiben draußen, ebenso Seiten aus installierten
  Community-Wissenspaketen (ein Hinweis sagt, wie viele). Eine Seite kommt nur dann mit vollem Text hinein, wenn ihre
  Lizenz die Weitergabe erlaubt, mit der Namensnennung, die die Lizenz verlangt; jede andere Seite kommt als ihre
  Adresse mit kurzen Fakten hinein (Titel, Abschnittsnamen, Fachbegriffe). Die Zusammenfassung führt jede Seite auf,
  mit dem, was das Paket von ihr enthält, und warum, und ein Hinweis zählt beides
  ([knowledge sources](#knowledge-sources) zeigt dasselbe vor dem Export). Seiten mit vollem Text unter einer Lizenz,
  die ihren Text oder Erlaubnishinweis verlangt (MIT, BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA), bekommen den
  kanonischen Text in `LICENSES/<SPDX-ID>.txt` und eine `NOTICE`, die jede Seite mit Quelladresse, Copyright-Zeile und
  Lizenz aufführt; die Zusammenfassung nennt jeden Lizenztext und warum. Der Wissens-Snapshot eines Targets bekommt
  dasselbe in `knowledge/`.

Die Datei heißt `contribution-<Art>-<ID>-<Version>.zip`, verschlüsselt `<Art>-<ID>-<Version>.lcfpack`. Deine
Schlüssel werden beim ersten Mal im Secrets-Speicher angelegt (siehe [zoo keys](#zoo-keys)). Der Empfänger eines
verschlüsselten Pakets öffnet es mit `llm-cli zoo install --file`. Ein Paket mit Passphrase oder eines, das für
einen Empfänger aus `age-keygen` oder `rage-keygen` verschlüsselt ist, öffnet sich auch ohne das Framework:
entpacken, dann `age -d -i key.txt -o pack.zip pack.zip.age` (`key.txt` ist diese Identitätsdatei; bei einer
Passphrase entfällt `-i key.txt`; das Werkzeug `age` oder `rage` prüft die Signatur des Absenders nicht). Die
age-Identität von `llm-cli zoo keys` bleibt im Secrets-Speicher; Pakete, die für sie verschlüsselt sind, brauchen
also `llm-cli zoo install`.

Die Datenschutz-Bereinigung ersetzt Home-Ordner durch `~` sowie IP- und MAC-Adressen, E-Mail-Adressen, JWTs, den
Hostnamen dieses Rechners und deinen Benutzernamen durch Platzhalter wie `<ip>` und `<user>`, und sie schwärzt
API-Schlüssel. Loopback-Adressen, Image-Digests (`name@sha256:...`) und Standardkonten wie `/root` bleiben. Lies den
Diff: Ein Skript, dessen Adresse ersetzt wurde, funktioniert womöglich nicht mehr.

**Pull-Request (`--pr`).** Nach der Zusammenfassung fragt der Befehl „Submit this pack to the public community
repository?“. Antwortest du mit Nein oder gibt es kein Terminal und kein `--yes`, wird nichts eingereicht
(Exit-Code 1). Danach:

- Sind die GitHub CLI und git installiert und ist `gh` angemeldet (`gh auth login`), forkt der Befehl das Repository
  aus der Einstellung `community_repo` (`Besitzer/Name`), committet das Paket auf einem neuen Branch deines Forks nach
  `contributions/<Art>/` und öffnet den Pull-Request mit `gh pr create`.
- Sonst, oder wenn das scheitert, öffnet er die Upload-Seite des Repositorys
  (`https://github.com/<Besitzer>/<Name>/upload/main/contributions/<Art>`) in deinem Browser. Zieh die Datei dorthin;
  GitHub legt den Fork für dich an.

Das Framework fragt nie nach einem Token, liest keines und zeigt keines an: `gh` und `git` nutzen ihre eigene
gespeicherte Anmeldung. Verschlüsselte Pakete sind zum privaten Teilen gedacht und werden nie eingereicht
(Exit-Code 1). `community_repo` nennt standardmäßig das Community-Repository,
`Smilez1985/Edge-LLM-Knowledge-Base`; ist die Einstellung geleert, bricht der Befehl mit „No community repository is
configured“ und Exit-Code 1 ab, und das Paket bleibt in seinem Ordner. Sobald sein Beitrags-Workflow veröffentlicht
ist, prüft die CI des Community-Repositorys jedes eingereichte Paket mit `llm-cli zoo verify`
([CONTRIBUTING](../CONTRIBUTING_DE.md#beiträge-zum-community-zoo)).

```bash
# Ein Target, geprüft so, wie die CI des Community-Repositorys es prüfen wird
llm-cli zoo export target my-board --author "<dein Name>"
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip

# Ein fertiger Build als Rezept
llm-cli zoo export recipe output/build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 \
  --author "<dein Name>" --model-license Apache-2.0

# Deine Wissensbasis unter einer ID deiner Wahl
llm-cli zoo export knowledge --author "<dein Name>" --id rk3588-notes

# Verschlüsselt für eine Kollegin, oder mit einer Passphrase
llm-cli zoo export target my-board --author "<dein Name>" --to kollegin.keys
llm-cli zoo export target my-board --author "<dein Name>" --passphrase

# Exportieren und einen Pull-Request öffnen
llm-cli zoo export target my-board --author "<dein Name>" --pr
```

In der GUI: **Tools > Community Hub öffnen > Exportieren...**: ein Target (auch ein selbst erstelltes oder
installiertes; ein Community-Paket wird wie hier abgelehnt), die `build_info.json` eines fertigen Builds, oder die
Wissensbasis; dann Autor, Lizenz, **Paket-ID** (Wissen, `--id`), **Privat teilen** an die Schlüssel in
Schlüsseldateien (`--to`) oder mit einer zweimal eingegebenen Passphrase (`--passphrase`), **Ordner** (`--out`),
**Create pack** und **Open pull request** (`--pr`: nach der Zusammenfassung stellt es dieselbe Frage, „Nein“ ist die
Vorgabe). Außerdem die letzte Seite des Modul-Assistenten und das Kontextmenü des Builder-Logs.

### zoo keys

```
llm-cli zoo keys
```

Gibt deine öffentlichen Schlüssel aus: einen age-Empfänger (`age1...`), an den andere Pakete für dich mit
`llm-cli zoo export --to` verschlüsseln (sein privater Teil bleibt im Secrets-Speicher, eine mit den age-Werkzeugen
dafür verschlüsselte Datei lässt sich also nicht öffnen), und eine Zeile `ed25519:`, mit der deine Signaturen geprüft
werden (andere nutzen sie mit `--from`). Sie sind nicht geheim; speichere die Ausgabe in einer Datei und gib sie den
Leuten, mit denen du Pakete austauschst. Die Schlüssel werden beim ersten Mal angelegt und nur im verschlüsselten
Secrets-Speicher aufbewahrt (Namen `community_signing_key` und `community_age_identity`). Ein gespeicherter
Schlüssel, der sich nicht lesen lässt, wird nie automatisch ersetzt; bei einem beschädigten nennt die Fehlermeldung
den Befehl, der einen neuen Schlüssel speichert.

In der GUI: **Tools > Community Hub öffnen > Meine öffentlichen Schlüssel...** (mit **Kopieren** und
**Speichern...**).

```bash
llm-cli zoo keys > my.keys
```

```
# LCF public keys, key id 0123456789abcdef. Not secret: give them to people who send you
# encrypted packs (llm-cli zoo export --to) or check your signed packs.
age1<58 Zeichen>
ed25519:<base64>
```

---

## trash

```
llm-cli trash [list]
llm-cli trash restore ID...
llm-cli trash empty ID... | --all | --due
```

Unveröffentlicht. Was Löschungen in den Papierkorb verschoben haben: gebaute Modelle (im `.trash` des
Ausgabeordners) und Wissens-Snapshots (im `.trash` des persönlichen Ordners). `list` (die Vorgabe) zeigt jeden
Eintrag mit ID, was er ist, Größe, wann er verschoben wurde, wer es angefordert hat (`gui`, `cli` oder eine KI-App wie
`mcp:laptop`), Erinnerungsdatum und woher er kam. Nichts verlässt den Papierkorb von selbst: Nach
`trash_retention_days` (Vorgabe 14; eine KI kann es nicht ändern) erinnert er nur.

| Befehl | Bedeutung |
|---|---|
| `restore ID...` | Legt Einträge zurück, wo sie waren; ist der Platz belegt, daneben als `<Name>-restored-<Zeit>`. Nie über einen Link. |
| `empty ID...` | Listet genau diese Einträge und löscht sie endgültig, nachdem du im Terminal bestätigt hast (Vorgabe Nein). |
| `empty --all` / `--due` | Listet jeden Eintrag oder die, deren Erinnerungsdatum vorbei ist, mit einer Nummer; nichts ist ausgewählt. Du gibst die Nummern ein und bestätigst dann genau diese. |

Kein Tool und keine KI-App leert den Papierkorb. Die GUI zeigt dasselbe unter **File > Trash...**.

```bash
llm-cli trash
llm-cli trash restore tr_0123456789abcdef
llm-cli trash empty --due
```

---

## Bekannte Einschränkungen

- `build start --optimization` wirkt sich auf Rockchip-Builds nicht aus (die Skripte haben keine passende
  Einstellung); `--parallel` ist veraltet und hat keine Wirkung.
- Überschreibungen aus `config repos` erreichen die Builds als `<NAME>_REPO_OVERRIDE`, aber die Rockchip-Skripte
  lesen sie nicht.
- `deploy package --docker` ist experimentell.
- Einen CLI-Befehl, um Dittos Anbieter dauerhaft zu wählen, gibt es noch nicht; nutze die GUI, die `config.yml` oder
  `LLM_FRAMEWORK_AI_PROVIDER` (`ditto login` meldet an, wechselt den Anbieter aber nicht). Anmeldungen im Browser
  und bei Cloud-Plattformen (OAuth, AWS, Google Cloud, Azure) kommen später ([Roadmap](BLUEPRINT_DE.md#roadmap)).
- Das Community-Repository ist festgelegt (`community_repo`: `Smilez1985/Edge-LLM-Knowledge-Base`), sein
  Beitrags-Workflow, sein signierter Index und der Maintainer-Schlüssel sind aber noch nicht veröffentlicht
  (`community_index_url` ist leer, `configs/community_keys.yml` enthält keinen Schlüssel): `zoo list` zeigt nur
  installierte Pakete, und `zoo install <ID>` bricht mit einer Meldung ab. Pakete lassen sich als Dateien teilen.
- `build start` hat keine Option für einen Modell-Commit: Der festgelegte Commit eines Rezepts wird genutzt, wenn der
  Builder aus dem Community Hub ausgefüllt wird oder wenn ein KI-Client dem MCP-Tool `start_build` eine `revision`
  übergibt.
- Für Deep Ingest (Dokumentationsseiten zur Wissensbasis hinzufügen) gibt es keinen CLI-Befehl; es läuft im
  Modul-Assistenten der GUI (**Tools > Create Hardware Module...**).
