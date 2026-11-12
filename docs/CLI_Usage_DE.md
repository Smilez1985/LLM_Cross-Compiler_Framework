Version 3.4.0 · Stand 2026-11-10

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
- [update](#update): das Framework aktualisieren (Git-Vorspulen, dann erneut der Installer)
- [launcher](#launcher): der Starteintrag ohne Terminal (`status`, `install`, `remove`)
- [knowledge](#knowledge): `snapshots` (`list`, `prune`, `delete`, `export`)
- [targets](#targets): `list`, `validate`
- [build](#build): `start`, `preflight`, `tune`, `save`, `deps`, `status`
- [deploy](#deploy): `list-artifacts`, `package`, `run`, `devices`, `ssh-config`
- [ditto](#ditto): `login`, `logout`, `auth`, `setup`, `ask`, `console`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [agents](#agents): `list`, `search`, `add`, `remove`, `login`, `run`
- [mcp](#mcp): `serve`, `config`, `token add|list|rotate|revoke`
- [acp](#acp): Ditto als Agent von Editoren (`config`, `servers`)
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): Hardware-Probe
- [device](#device): `list`, `settings`, `runtime`
- [bundle](#bundle): `plan`, `build`, `list`, `deploy`, `status`, `uninstall`, `rollback`, `delete`, `block`, `harness`
- [config](#config): `show`, `output-folder`, `repos`
- [zoo](#zoo): `list`, `show`, `install`, `verify`, `export`, `keys`, `maintainer`
- [trash](#trash): `list`, `restore`, `empty`
- [models](#models): `take-over`, `list`, `delete`, `bench`, `archive`
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

Die Beispiele unten verwenden `llm-cli`. `llmcf` ist dasselbe Programm unter seinem neuen Namen:
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
| `mojo` | Die Installation prüfen (Mojo Oracle) und reparieren, was sich sicher reparieren lässt (Mojo Miracle) |
| `doctor` | Der alte Name von `mojo`, mit seinen Optionen aus 3.0.0 |
| `update` | Das Framework aktualisieren: Der Checkout, aus dem du installiert hast, wird vorgespult, dann läuft sein Installer erneut mit den neuesten passenden Abhängigkeiten |
| `launcher status` / `install` / `remove` | Der Starteintrag, der die GUI ohne Terminal öffnet (der Eintrag im Linux-Anwendungsmenü, die Windows-Verknüpfung im Startmenü): zeigen, anlegen oder reparieren, den eigenen entfernen |
| `knowledge snapshots` | Die Snapshots der Wissensbasis mit vollem Pfad, Namen, Größe und was jeder ist auflisten; die ausgewählten löschen oder exportieren |
| `knowledge index-docs` | Die eigene Dokumentation des Frameworks (und mit `--with-code` seinen Code) in die Wissensbasis aufnehmen |
| `knowledge storage` | Wo die Wissensbasis liegt (Qdrant Edge), die Umwandlung einer älteren und ihr alter Ordner: `convert`, `delete-old` |
| `knowledge loose` / `knowledge sort` | Die losen Wissensdateien auflisten / eine nach den Paketprüfungen und deiner Bestätigung in einen Themen- oder Target-Ordner einsortieren und importieren |
| `trash list` / `restore` / `empty` | Was Löschungen in den Papierkorb verschoben haben; Einträge zurücklegen; die ausgewählten endgültig löschen |
| `models suggestions` | Die Modellvorschläge des Quellenkatalogs: genaue Hugging-Face-IDs, die kein Token brauchen, kleine Modelle zuerst |
| `models take-over` | Was frühere Versionen des Hugging-Face-Browsers im Hugging-Face-Cache gespeichert haben, in den Modellordner kopieren, jede Datei geprüft, und danach das Entfernen der alten Kopien anbieten |
| `models list` / `models delete` | Jedes heruntergeladene Modell und jeden Build in einer Liste (der Reiter Modelle) / Zeilen daraus löschen, jede über ihr eigenes Löschen |
| `models bench` / `models bench-results` | Ein GGUF-Modell mit llama-bench auf diesem Rechner messen (das Benchmark-Fenster) / die aufbewahrten Läufe |
| `models archive write` / `take` / `list` / `delete` | Ein portables Modell-Archiv für einen Computer ohne Netz: eines aus einem Hugging-Face-Modell in seinem genauen Commit schreiben, eines nach seinen Prüfungen und deinem Ja zu seiner Lizenz einlesen, die eingelesenen auflisten und löschen (Modell-Archive… im Reiter Modelle) |
| `targets list` / `targets validate` | Hardware-Targets mit ihren Boards und Formaten auflisten / prüfen, ob ein Target lädt und seine Capabilities gültig sind |
| `targets boards` | Jedes Board eines Targets mit seinem Optimierungsprofil und seinen Benchmark-Zeilen (gemessen, veröffentlicht oder ungemessen) |
| `build start` / `build status` | Ein Modell konvertieren und quantisieren / Builds zeigen, auch solche, die die GUI, der MCP-Server oder ein anderer CLI-Aufruf gestartet hat |
| `build preflight` | Die Prüfung vor dem Build, ohne ihn zu starten: die Hardware-Probes gegen das, was der Build und das SDK des Targets brauchen; mit `--model` die Speicherschätzung des Modells auf dem Board |
| `build tune` | Auto-Tuning: die Quantisierungstypen eines Modells für ein Board messen (KL-Divergenz und Top-1-Übereinstimmung gegen das Original), die Berichte zeigen und den empfohlenen Typ bauen |
| `build save` | Einen Build, der im Build-Ordner des Frameworks wartet, im Ausgabeordner oder einem anderen Ordner speichern |
| `build image plan/sources/export/import` | Das Build-Container-Image eines Targets: Kann ein Offline-Build es bauen, die Build-Quellen in deinem Netz, auf einen anderen Offline-Rechner als geprüfte Datei bringen |
| `build preload write/take` | Das Preload des Builds für einen Rechner ganz ohne Netz: Skripte für genau das, was einem Offline-Build fehlt, und das Archiv, das sie schreiben, aufgenommen |
| `build source-image list/take` | Die Quell-Images eines Targets, und die Dateien aus einem Image, das sein Hersteller nur als Datei veröffentlicht |
| `build deps` | Die Abhängigkeiten von CMake-Optionen der Module, die vor einem Build geholt werden, listen, holen und löschen |
| `build interrupted list/continue/delete` | Ein Build, der mit aufbewahrten fertigen Stufen angehalten hat: fortsetzen oder löschen, was er hinterlassen hat |
| `deploy list-artifacts` / `deploy package` / `deploy run` | Fertige Builds auflisten / einen Build in eine ZIP-Datei packen / sie auf ein Board laden und installieren |
| `deploy ssh-config show/edit/import` | Die eigene OpenSSH-Einstellungsdatei des Frameworks (gelesen statt `~/.ssh/config`): zeigen, den Block eines Boards setzen, einen Block aus `~/.ssh/config` übernehmen |
| `ditto login` / `ditto logout` | Ditto bei einem Anbieter anmelden (der Schlüssel verdeckt abgefragt, von stdin oder aus einer Umgebungsvariable, vor dem Speichern geprüft) / abmelden |
| `ditto auth status/methods/storage/rotate-key` | Anmeldestand aller Anbieter / die Anmeldeverfahren / wie der Tresor geschützt ist / ein neuer Schlüssel für den Tresor |
| `ditto setup` | Die Tool-Berechtigungen festlegen (dieselbe Auswahl wie im Dialog beim ersten Start der GUI) |
| `ditto ask` | Ditto eine Frage stellen |
| `ditto console` | Was Ditto und KI-Apps tippen und tun, wie die Ditto-Konsole neben dem Chat es zeigt; `--follow` druckt weiter |
| `ditto eval` | Ditto anhand fester Testfälle bewerten, oder die Suche der Wissensbasis |
| `ditto runtime install/start/status/stop/import/export/prune` | Der verwaltete lokale Modellserver (llama.cpp, auf diesem Rechner gebaut) für den Offline-Modus |
| `ditto runtime ensure` | Dittos lokalen Modellserver installieren, wenn er fehlt, direkt oder in Docker; `--force` wechselt die Art |
| `ditto runtime export-source/import-source` | Den festgelegten llama.cpp-Quelltext als Git-Bundle auf einen Rechner ohne Netz tragen (am Commit geprüft) |
| `ditto models list/download` | Die Modellstufen für den Offline-Modus |
| `ditto cache` / `ditto cache set` | Das Speicherbudget von Dittos lokalem Server: Prompt-Cache, Haltezeit, KV-Cache |
| `tools list` / `tools permissions` / `tools audit` | Die Tools des Frameworks, ihre Berechtigungen und das Audit-Log |
| `agents list/search/add/remove/login/run` | Externe ACP-Agenten, die du auswählst (Claude Agent, Codex, Gemini CLI, dein eigenes Programm), mit den Werkzeugen des Frameworks |
| `mcp serve` / `mcp config` | Den MCP-Server betreiben / zeigen, wie man KI-Clients verbindet |
| `acp` / `acp config` / `acp servers` | Ditto als Agent von Editoren, die ACP sprechen (der Editor startet es) / den Eintrag für den Editor zeigen / die MCP-Server, die Editoren übergeben haben, mit deiner Zustimmung je Eintrag |
| `secrets set` / `secrets list` | Ein Token speichern (Hugging Face, GitHub) / die gespeicherten Namen auflisten |
| `module create` / `module generate-ai` / `module edit` / `module repair` | Ein neues Hardware-Target anlegen, per Fragen oder mit Ditto aus einer Probe-Datei / seine Dateien mit Diff und Prüfungen bearbeiten / es nach einem gescheiterten Build reparieren, von Hand oder aus seinem Fehlerlog |
| `scan` | Die Hardware-Probe auf diesem Rechner oder mit `--device` auf einem Board per SSH ausführen, oder mit `--import` die Probe-Datei eines Boards ablegen, und ihre Probe-ID zeigen |
| `device list` / `device settings` | Die untersuchten Boards / wie die Laufzeit auf einem Board installiert wird (Startskript oder systemd), vor einem Deployment entschieden |
| `device runtime plan/build/deploy/verify/status` | Die selbst gebaute llama.cpp-Laufzeit eines Boards: aus seinem Profil planen, cross-kompilieren, per SSH installieren und dort prüfen |
| `config show` / `config repos` | Die wirksamen Einstellungen und ihre Herkunft zeigen / Überschreibungen für Quell-Repositorys verwalten |
| `config local-network` | Was der Offline-Modus als lokal zählt, und was du selbst als lokal zählst (ein VPN, ein Host, ein Adressbereich) |
| `config doorbell` | Dein MQTT-Broker für die Build-Türklingel, und eine Test-Klingel |
| `config set framework_mode` | Dein Framework-Modus: privat (Standard) oder enterprise (auf einem Board nur OpenSSH mit sudo) |
| `config set default_enable_imatrix` | Build-Vorgaben: die Importance Matrix, die Kalibrier-Textdatei und die Sprachen des mitgelieferten Texts |
| `config set language` | Die Sprache des Programms (en oder de) und die deutsche Anrede (`form_of_address`: du oder sie) |
| `config set ai_provider` | Dittos KI-Einstellungen (Anbieter, Modell, Adresse, Aufwand, Datenschutz, Ausweichkette, Wissensdatenbank, Offline-Modus), geprüft wie im Fenster |
| `config output-folder` | Den Ausgabeordner zeigen oder festlegen, in dem fertige Modelle gespeichert werden |
| `config take-over` | Was eine frühere Version im Installationsordner abgelegt hat, in deine eigenen Ordner verschieben |
| `zoo list` / `zoo show` | Den Community-Zoo und die installierten Pakete auflisten / einen Eintrag zeigen |
| `zoo install` / `zoo verify` | Ein Community-Paket nach Prüfungen und deiner Bestätigung installieren / installierte Pakete oder eine Paketdatei prüfen |
| `zoo uninstall` | Ein installiertes Paket nach der genauen Liste und deiner Bestätigung deinstallieren („Nein“ als Vorgabe) |
| `zoo export` / `zoo keys` | Ein signiertes Paket aus deinem Target, Build oder deiner Wissensbasis machen (und einen Pull-Request öffnen) / deine öffentlichen Schlüssel zeigen |
| `zoo maintainer key` / `review` / `revoke` | Für den Maintainer des Community-Repositorys: den Signaturschlüssel des Index anlegen / Beiträge prüfen und veröffentlichen / ein veröffentlichtes Paket zurückziehen |

---

## mojo

```
llm-cli mojo [--oracle | --miracle] [--json] [--board NAME]
```

Der Gesundheitscheck (er hieß `doctor`). **Mojo Oracle** (Standard) schaut nur: Es prüft Python, den
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

Endet Mojo Miracle nach Mojo Oracle im Terminal (du hast „Run Mojo Miracle now?“ mit `y` beantwortet oder
`llmcf mojo --miracle` aufgerufen, während ein früheres `llmcf mojo` im Terminal noch offen ist), zeigt die Konsole
Mojos Kristallkugel, farbig, wo die Konsole Farben zeigen kann, und darunter „[Mojo] Miracle managed. The oracle is
pleased.“ (das Wunder ist gelungen: mindestens ein Problem repariert, keines übrig, nichts Neues dazugekommen) oder
„[Mojo] 404 - miracle not found“ (es gab nichts zu reparieren, also auch kein Wunder). Sie erscheint nie mit
`--json`, ohne Terminal, über die KI-Tools oder in der GUI; der offene Oracle-Lauf ist eine kleine private Notiz in
deinem persönlichen Ordner; sie verfällt nach einer Stunde, und jeder deiner Läufe von Mojo Miracle entfernt sie an
seinem Ende (auch `--miracle --json` und `llm-cli doctor --fix`, die kein Logo zeigen).

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
| `--fix` | Die automatischen Korrekturen bis `--tier` anwenden, dann erneut prüfen. Das sind die harmlosen, protokollierten Reparaturen von Mojo Miracle; ein Problem, das noch da ist, wird mit dem Grund gezeigt. |
| `--tier INTEGER RANGE` | `1` (Standard): harmlose Korrekturen (macht den persönlichen Ordner privat). `2`: listet auch jedes Build-Image des Frameworks (die ohne Namen aus früheren Builds, die benannten Images `llm-framework/<Target>:last-build` und die Builder-Images der Laufzeit; Name, Image-ID, Größe und was jedes ist), nichts ausgewählt. Du gibst die Nummern der zu löschenden ein und bestätigst genau diese im Terminal (die Vorgabe ist Nein). Ohne Terminal (eine Pipe, ein Skript, eine KI-App) listet es sie nur. **Tools > Framework Build Images...** in der GUI ist dieselbe Liste. Der Docker-Build-Cache wird nie geleert: Er lässt sich nicht auf das Framework beschränken. |

```bash
llm-cli doctor
llm-cli doctor --json > doctor.json
llm-cli doctor --fix --tier 2
```

In der GUI: **Tools > Mojo-Gesundheitscheck...** (die Prüfung; **Mojo Miracle ausführen...** macht die Reparaturen
von `--fix`) und **Tools > Framework Build Images...** (die Liste von `--tier 2`).

---

## update

```
llm-cli update [--check] [--offline] [--yes]
```

Aktualisiert das Framework: Der Git-Checkout, aus dem du installiert hast, wird vorgespult (nur Fast-Forward; ein
Checkout mit eigenen Commits bleibt, wie er ist, und der Befehl sagt warum), dann läuft sein Installer erneut
(`install.sh` mit sudo, unter Windows `install.bat`). Der Installer löst bei jedem Lauf die neuesten passenden
Abhängigkeiten auf, prüft sie und behält einen Weg zurück; ein Update ist also ein weiterer Lauf von ihm. Die
installierte Kopie hat kein `.git`; der Installer schreibt den Checkout, aus dem er lief, dort in `.install_source`,
und `update` startet von diesem Checkout. Unter Windows startet der Installer in einer eigenen Konsole und der Befehl
endet sofort, weil der Installer die Umgebung ersetzt, aus der er läuft.

| Option | Bedeutung |
|---|---|
| `--check` | Sagt nur, wie viele neue Commits der Upstream des Checkouts hat; nichts wird verändert. |
| `--offline` | Geht an den Installer: installiert den getesteten Stand (`uv.lock`), ohne neu aufzulösen und ohne das Netz zu versuchen: aus deinem eigenen Spiegel (`PIP_INDEX_URL`, `PIP_FIND_LINKS`) oder aus einem Preload, das der Installer schreibt und auf das er dann wartet ([README](../README_DE.md#ein-rechner-ganz-ohne-netz-das-preload)). |
| `--yes` | Fragt nicht, bevor das Update startet (die Vorgabe ist Nein). Der Installer stellt seine eigenen Fragen weiter. |

```bash
llm-cli update --check
llm-cli update
llm-cli update --offline
```

In der GUI: **Datei > Einstellungen…**, Seite **Update** (auch **Extras > Wartung > Framework aktualisieren...**):
**Nach Updates suchen** ist `--check`, **Nur den getesteten Abhängigkeitsstand** ist `--offline`, **Framework
aktualisieren...** prüft, fragt mit „Nein“ als Vorgabe und startet das Update dann in einem Terminal; unter Windows in
der Konsole des Installers, und das Framework schließt sich. Eine KI startet nie ein Update.

---

## launcher

```
llm-cli launcher status
llm-cli launcher install
llm-cli launcher remove
```

Der Starteintrag, der die GUI ohne Terminal öffnet. Der Installer schreibt ihn; diese Befehle zeigen ihn, legen ihn
an oder reparieren ihn und entfernen ihn, ohne neu zu installieren.

- **Linux**: dein eigener Desktop-Eintrag `llm-cross-compiler-framework.desktop` in `~/.local/share/applications`
  (oder `$XDG_DATA_HOME/applications`), so steht das Framework in deinem Anwendungsmenü. Er startet das Python der
  Installation in `.venv` mit `orchestrator/main.py` über absolute Pfade, ohne Shell und ohne aktivierte Umgebung,
  und ohne Terminalfenster. Jede Person auf einem Rechner hat ihren eigenen Eintrag (der Installer schreibt ihn für
  die Person, die ihn startet). Ist der Installationsordner weg, blenden Desktops den Eintrag von selbst aus;
  `remove` löscht die Datei.
- **Windows**: Der Installer legt die Verknüpfung für alle Konten ins Startmenü (und, wenn du ja gesagt hast, auf den
  Desktop). Sie startet `pythonw.exe`, also öffnet sich kein Konsolenfenster. `install` schreibt eine eigene
  Verknüpfung in dein Startmenü (ohne Administratorrechte) nur, wenn jene fehlt oder woandershin zeigt; `remove`
  entfernt nur deine eigene (die für alle Konten geht mit dem Deinstaller).

| Befehl | Was er tut |
|---|---|
| `status` | Wo der Eintrag ist und ob er da ist, aktuell, für eine andere Installation gemacht, oder eine gleichnamige Datei, die nicht vom Framework stammt (die wird nie geändert). |
| `install` | Legt den Eintrag an oder repariert ihn, wenn er nicht mehr zu dieser Installation passt. |
| `remove` | Entfernt deinen eigenen Eintrag, nur einen, den das Framework geschrieben hat. |

Jeder Start der GUI schreibt deinen Linux-Eintrag außerdem neu, wenn er nicht mehr zu dieser Installation passt, legt
aber nie einen an, den du entfernt hast. Trifft die GUI auf einen Fehler, den niemand abgefangen hat, sagt ein Dialog
das in einfachen Worten, mit **Log-Ordner öffnen** und **Details kopieren** (der Traceback, die Version, Python und das
System, ohne Geheimnisse und ohne deinen Home-Ordner); das Log ist `gui.log` in deinem Log-Ordner
(`~/.llm-framework/logs`). Ein Absturz, bevor es das Fenster gibt, landet dort in einer `crash-<Zeit>.txt`.

In der GUI: **Datei > Einstellungen…**, Seite **Update**, Teil **Starteintrag**: der Zustand ist `status`, **Anlegen
oder reparieren** ist `install`, **Entfernen** ist `remove`.

---

## knowledge

### knowledge snapshots

```
llm-cli knowledge snapshots [list]
llm-cli knowledge snapshots prune
llm-cli knowledge snapshots delete NAME...
llm-cli knowledge snapshots export NAME... --to ORDNER
```

Vor jedem Deep Ingest, jeder Aufnahme der eigenen Dokumente des Frameworks (`knowledge index-docs`),
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

Nimmt die eigene Dokumentation des Frameworks (README, CONTRIBUTING und den Ordner `docs`, in beiden
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
llm-cli knowledge sources [--kind web|pack|docs|other|any] [--topic NAME]
```

Listet, was die Wissensbasis enthält: jede Quelle mit ihrer Art, der Lizenz, die sie nennt (eine
SPDX-ähnliche ID wie `CC-BY-4.0`, `other` für eine, die das Framework nicht erkennt, `-` für keine), ihrem
Urheberrechtshinweis und dem, was ein Wissenspaket von ihr enthielte, und warum: **full text** (ihre Lizenz erlaubt
die Weitergabe des Textes), **link and short facts** (ein Urheberrechtshinweis ohne eine solche Lizenz, Code ohne
Lizenz, eine unbekannte Lizenz) oder **not exported** (eine Seite eines installierten Wissenspakets, die eigenen
Dokumente des Frameworks). Adressen, Titel und Hinweise sind Text der Website und werden als reiner Text gezeigt. Die
Wissensbasis muss eingeschaltet sein. Die dritte Spalte, **Thema oder Target**, nennt das Thema oder Target, zu dem die
Auszüge eines Wissenspakets oder eines Target-Pakets gehören, und das Thema deines eigenen Crawls (`-` für andere
eigene Seiten, die beim Export eins bekommen);
`--topic NAME` listet nur die Quellen dieses Themas oder Targets (Groß- und Kleinschreibung egal).

In der GUI: **Tools > Wissensquellen...** (dieselben Spalten in derselben Reihenfolge; der Filter **Art** ist `--kind`, das Feld **Thema oder
Target** ist `--topic`).

```bash
llm-cli knowledge sources
llm-cli knowledge sources --kind web
llm-cli knowledge sources --topic Rockchip
```

### knowledge loose und knowledge sort

```
llm-cli knowledge loose
llm-cli knowledge sort NAME --topic NAME | --target NAME
```

Wissen gehört immer zu einem Thema oder einem Target. Ein installiertes Wissenspaket liegt im Ordner
seines Themas oder Targets, `community/knowledge/<Thema oder Target>/<Paket>/`, und eine lose Wissensdatei wird nie
importiert: eine `.json`-Datei direkt in `community/knowledge/` oder in einem Themenordner, oder eine `knowledge.json`
in einem Ordner ohne die `.lcf-provenance.json` des Pakets. `knowledge loose` listet sie (Mojo nennt sie auch).
`knowledge sort` sortiert eine davon (`NAME`, wie `knowledge loose` ihn ausgibt) in ein Thema (einen vorhandenen
Themenordner oder einen neuen Namen) oder in ein Target dieses Computers: Die Datei wird in der Quarantäne wie ein
installiertes Wissenspaket geprüft (eine Liste von Seiten, jede mit ihrer Quelle; die Lizenztexte und die `NOTICE`,
die ihre Volltext-Seiten brauchen, werden daneben geschrieben), die Zusammenfassung wird gezeigt, und erst nach
deinem **y** an einem Terminal („Nein“ ist die Vorgabe) wird sie in den Ordner verschoben (Vertrauensstufe `local`)
und in die Wissensbasis importiert, nach einem Snapshot von ihr. Ohne Terminal wird nichts einsortiert. Das Paket
heißt wie die Datei (eine `knowledge.json` wie ihr Ordner), mit einer angehängten Zahl, wenn ein installiertes Paket
oder etwas im gewählten Ordner schon so heißt; die Zusammenfassung nennt den Ordner, bevor du antwortest. Eine Datei
im alten „Swarm“-Format, oder eine, die die Prüfung nicht besteht, bleibt mit dem Grund liegen.

In der GUI: **Lose Wissensdateien einsortieren...** im Community Hub (die Liste der losen Dateien, **Gehört zu:** ein
Thema oder ein Target, dann die Zusammenfassung mit „Nein“ als Vorgabe).

```bash
llm-cli knowledge loose
llm-cli knowledge sort notes.json --topic llama.cpp
llm-cli knowledge sort board/knowledge.json --target Rockchip
```

### knowledge storage

```
llm-cli knowledge storage [show]
llm-cli knowledge storage convert
llm-cli knowledge storage delete-old
```

Die Wissensbasis liegt in Qdrant Edge (`~/.llm-framework/qdrant_edge`). Eine Wissensbasis einer
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
Die Spalte **Build test** ist der Zustand eines erzeugten Moduls (vom Assistenten, von `module create`, von einer KI
oder aus einem installierten Paket): *untested* (noch nicht gebaut oder seit seinem letzten Build geändert), *tested*
(ein Build dieser Dateien hat seine Target-Stufe bestanden: die ID des Builds, das Datum und die Framework-Version) oder
*needs rework* (sein neuester Build ist im Teil des Moduls gescheitert; repariere es mit
[`module repair`](#module-repair)); ein mitgeliefertes Target zeigt „- (shipped)“. In `json` und `yaml` ist es das
Feld `build_test`.

| Option | Bedeutung |
|---|---|
| `-f, --format [table\|json\|yaml]` | Ausgabeformat (Standard `table`). |

In der GUI: **Build > Targets…** (dieselbe Liste, mit dem Unterstützungsgrad jedes Targets) oder **Targets…** neben
der Target-Liste des Builders.

### targets boards

```
llm-cli targets boards TARGET_NAME
```

Gibt jedes Board des Targets mit seinem Optimierungsprofil aus: die empfohlenen Quantisierungen (der Builder bietet
sie zuerst an), das Speicherprofil, die Optimierungsstufe, mit der ein Build ohne `--optimization` beginnt, die
llama.cpp-Optionen und Compiler-Flags, die das Build-Rezept für den SoC des Boards plant, die CPU des Boards und, wo
das Profil sie hat, `model sizes:` (welche Modellgrößen zu welchen Aufgaben passen; ein Hinweis, nie eine Grenze).
Darunter stehen die Benchmark-Zeilen des Boards (Modell, Quantisierung, Werte) mit ihrer Quelle am Ende:
`measured by llmcf (board <Name>, <Datum>)`, `published: <Adresse>` oder `unmeasured` (eine Schätzung; nie als Messung gezeigt).
Die Board-Tests der Builds dieses Computers stehen vorn: der neueste bestandene Test je Board, Modell und
Quantisierung, gemessen auf dem Board, das du benannt hast. Eine letzte Zeile nennt Einträge im Format vor 3.2.0, die
übergangen wurden (`N entries in the old format were ignored: <Namen>`). Nach einem Board stehen die Chips, die es
ohne eigenes Profil bedient (`capabilities.variants`), jeder als `<Chip>: <Bezeichnung>` mit einer `note:`-Zeile: das
Board, das er nutzt, der Grund und seine Quelle (`rk3582`, `rk3588s` und `rk3588j` nach `rk3588`). `build start
--board rk3582` baut `rk3588` und gibt eine `Note:`-Zeile aus, die das sagt. Exit-Code 2 für ein unbekanntes Target,
1, wenn seine Profile oder Benchmarks ungültig sind (`targets validate` nennt den Grund).

```bash
llm-cli targets boards Rockchip
```

In der GUI: **Build > Targets… > Boards…** (derselbe Text) und die Zeile unter der Board-Liste des Builders.

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Prüft, ob das Target lädt (per ID oder Ordnername) und ob sein Block `capabilities:` das Schema besteht, das GUI,
CLI, Build-Service und Werkzeuge verwenden; jeder Schemafehler wird ausgegeben. Auch `optimization_profiles:` und
`performance_benchmarks:` werden geprüft: ihr Schema, ihre Boards und Quantisierungen, keine Option, die etwas
herunterlädt, und jedes Profil gegen den Plan des Build-Rezepts für seinen SoC (ein abweichender Wert wird genannt). Exit-Code 1, wenn das Target nicht
gefunden wird oder seine Capabilities ungültig sind. Ein Target ohne Capabilities wird gemeldet, gilt aber als
gültig. Einträge im Format vor 3.2.0 werden nicht abgelehnt; eine letzte Zeile nennt sie: `N entries in the old format
were ignored: <Namen>` (die Target-Prüfung der GUI sagt dasselbe).

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
llm-cli build start --recipe ID [Optionen]
```

Konvertiert und quantisiert ein Modell für ein Target in Docker. Die Anfrage prüft zuerst der Build-Dienst, mit
derselben Prüfung, die der Builder-Tab und KI-Clients bekommen: Eine Kombination, die die Capabilities des Targets
nicht unterstützen, ein Modell, das weder ein vorhandener lokaler Ordner bzw. eine Datei ist (die wie ein Modell
aussieht) noch eine Hugging-Face-ID, oder ein Datensatz, der keine Datei ist, stoppt mit Exit-Code 2 und dem Grund,
bevor etwas läuft. Fehlendes Board und fehlende Quantisierung werden mit den Standardwerten des Targets ergänzt. Ein
Target ohne Capabilities-Block wird ungeprüft gebaut.

| Option | Bedeutung |
|---|---|
| `--recipe ID` | Mit einem installierten Community-Rezept (`zoo list --kind recipe`) als Target-Vorlage bauen: Sein Target enthält den Build-Code, und jede Option, die du weglässt, nimmt den Wert des Rezepts (das Modell beim Commit des Rezepts, Format, Board, Quantisierung, Importance Matrix, Optimierungsstufe; die Kontextlänge ist immer die des Rezepts). `--model`, `--format`, `--board`, `--quantization`, `--imatrix/--no-imatrix`, `--dataset/--no-dataset` und `--optimization` ändern einen Wert; `--target` darf nur das Target des Rezepts nennen. Ein widerrufenes oder unlesbares Rezept stoppt mit Exit-Code 2. |
| `-m, --model TEXT` | **Pflicht** ohne `--recipe`. Hugging-Face-ID (z. B. `HuggingFaceTB/SmolLM2-135M-Instruct`), auch mit ihrem Commit als `OWNER/NAME@COMMIT` (ein eingelesenes [Modell-Archiv](#models)), oder ein lokaler Ordner bzw. eine Datei. |
| `--revision COMMIT` | Der Commit des Hugging-Face-Modells mit 40 Zeichen. Ein eingelesenes Modell-Archiv dieses Commits wird ohne jede Anfrage an Hugging Face genutzt; sonst wird genau dieser Commit geholt. Vorgabe: der Commit, auf den der Standard-Branch jetzt zeigt. |
| `-t, --target TEXT` | **Pflicht** ohne `--recipe`. Target-ID, wie `targets list` sie zeigt, z. B. `Rockchip`, oder der Ordnername des Targets. Groß- und Kleinschreibung spielt keine Rolle. Ein unbekanntes Target stoppt mit Exit-Code 2 und der Liste der bekannten Targets. |
| `-f, --format FORMAT` | Ausgabeformat (Standard `gguf`): eines, das das Target baut, wie seine Capabilities in `target.yml` es angeben (die Formate, die der Builder-Tab dafür anbietet). `--help` und die Shell-Vervollständigung nennen, was jedes installierte Target baut (Rockchip: `gguf`, `rkllm`, `rknn`). Ein Target ohne Capabilities nimmt jedes Format außer `hf`, ungeprüft. Ein Format, das das Target nicht baut, stoppt mit Exit-Code 2 und nennt, was jedes installierte Target baut. Groß- und Kleinschreibung spielt keine Rolle. |
| `-b, --board TEXT` | Board der Target-Familie, z. B. `rk3588`, `rk3576`, `rk3566`. Standard: das Standard-Board des Targets (Rockchip: `rk3588`). Groß- und Kleinschreibung spielt keine Rolle. |
| `-q, --quantization TEXT` | Quantisierung, z. B. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Standard: der Standard des Formats für das Board. Gängige Schreibweisen wie `INT8` werden übersetzt. |
| `-o, --output TEXT` | Ordner, in dem das fertige Modell (Golden-Artefakt) gespeichert wird, relativ zum aktuellen Ordner; er wird angelegt, wenn es ihn noch nicht gibt. Standard: der Ausgabeordner ([`config output-folder`](#config-output-folder), `~/llmcf/output`). An einem Terminal ohne diese Angabe wirst du gefragt, mit dem Ausgabeordner als Vorgabe; `-` lässt den Build im Build-Ordner des Frameworks ([`build save`](#build-save) speichert ihn später). Ein Systemordner oder der Programmordner des Frameworks wird abgelehnt (Exit-Code 2). `--output-dir` ist der alte Name und geht weiter. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimierungsstufe, wird den Build-Skripten als `OPTIMIZATION_LEVEL` übergeben. Standard: die Stufe des Optimierungsprofils des Boards (`targets boards`; etwa `size` bei einem RK3566), sonst `balanced`; mit `--recipe` die Stufe des Rezepts, wenn es eine nennt, sonst die des Board-Profils. Die Rockchip-Skripte werten sie nicht aus. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in der Build-Warteschlange dieses Prozesses (Standard `normal`). |
| `--parallel` | Veraltet und in `--help` ausgeblendet: Hat keine Wirkung (ein Aufruf startet einen Build) und gibt eine Warnung aus. |
| `-F, --follow` | Jede Stufe mit ihrem Prozentwert ausgeben, während der Build läuft (der Modell-Download: `Fetching model 37 % (1.2 GB of 3.4 GB)`). |
| `--gpu / --no-gpu` | Die GPU des Hosts in den Container durchreichen (Standard aus): eine NVIDIA-GPU, wenn `nvidia-smi` gefunden wird, sonst `/dev/dri`, falls vorhanden. Rockchip-Konvertierungen brauchen das nicht. |
| `--imatrix / --no-imatrix` | Importance Matrix für GGUF. Weggelassen: an für die Typen unter 5 Bit je Gewicht (`Q2_K` bis `Q4_K_M`, `Q4_0`, `Q4_1` und jeder `IQ`-Typ), aus für `Q5_*`, `Q6_K`, `Q8_0` und die Gleitkommatypen (Einstellung `default_enable_imatrix`, siehe [config set](#config-set)). Pflicht für `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`, `IQ2_M`, `IQ3_XXS`, `IQ3_XS`, `Q2_K_S`: Weggelassen bekommen sie immer eine; `--no-imatrix` wird für sie abgelehnt. |
| `--dataset PATH` | Deine eigene Kalibrierungsdatei (muss existieren): eine Textdatei für die GGUF-Importance-Matrix oder für RKLLM, eine Liste mit Beispieleingaben für RKNN `i8`. Ohne sie die Datei der Einstellung `default_calibration_dataset` (GGUF-Importance-Matrix und RKLLM), sonst für die Importance Matrix der mitgelieferte Kalibrierungstext. |
| `--no-dataset` | Kein eigener Datensatz, auch wenn `default_calibration_dataset` einen nennt: Die Importance Matrix nutzt den mitgelieferten Kalibrierungstext. |
| `--module-flags [allow\|standard]` | Deine Entscheidung zu den CMake-Optionen des Moduls, die gültig sind, aber außerhalb der erlaubten Liste liegen (`-G`, `--trace`, ...; [Modul-Entwicklerhandbuch, Abschnitt 3.5](Module_Developer_Guide_DE.md#35-cmake-flags-eines-moduls)): erlauben oder mit den Standard-Flags weitermachen (die Optionen bleiben draußen). Im Terminal ohne die Option wirst du gefragt, nach dem, was jede Option tut, und einer Empfehlung für dieses Modell und diese Quantisierung; ohne Terminal und ohne die Option startet nichts (Exit 2). Eine für diese Modulversion und diese Flags gemerkte Entscheidung gilt ohne Frage. |
| `--remember-flags` | Diese Entscheidung für diese Modulversion und diese Flags merken (ein von einer KI gestarteter Build des Moduls nutzt sie; eine neue Version oder andere Flags fragen wieder). |
| `--ignore-preflight` | Auch dann bauen, wenn die [Prüfung vor dem Build](#build-preflight) sagt, dass dieser Build nicht gelingen kann. Deine Entscheidung für diesen einen Build, nie gemerkt und in den Warnungen des Builds vermerkt. Im Terminal ohne die Option wirst du gefragt „Build anyway?“ (Nein ist die Vorgabe); ohne Terminal und ohne die Option startet nichts (Exit 2). |
| `--preload DATEI` | Offline: das Archiv des [Preloads des Builds](#build-preload), vor dem Build geprüft und aufgenommen. Ohne die Option schreibt ein Offline-Build, dessen Image nicht hier ist und dem Quellen fehlen, die Preload-Skripte und fragt im Terminal nach dem Pfad des Archivs; ohne Terminal hält er mit den Schritten an (Exit 2). |
| `--imatrix-file PFAD\|hf:BESITZER/NAME/DATEI` | Eine vorgegebene Importance Matrix (GGUF), statt eine zu berechnen: eine Datei im GGUF- oder `.dat`-Format von llama-imatrix (höchstens 2 GiB) oder eine Hugging-Face-Datei als `hf:besitzer/name/pfad/im/repository`, vom geprüften Downloader des Frameworks zu dem Commit geladen, den er auflöst, mit geprüfter sha256 (nur online). Sie wird vor dem Build geprüft und in den Ordner des Builds kopiert; die Convert-Stufe vergleicht ihre Tensornamen mit denen des Modells und hält mit dem Grund an, wenn sie weniger als die Hälfte der Gewichtsmatrizen des Modells abdeckt (die Matrix eines anderen Modells). Sie schaltet die Importance Matrix ein. |
| `--tensor-type MUSTER=TYP` | Ein Typ je Tensor für llama-quantize (wiederholbar oder durch Kommas getrennt): ein regulärer Ausdruck auf dem Namen des Tensors (der erste Treffer gilt) und ein ggml-Typ, z. B. `ffn_down=q6_k`; `output=TYP` und `token_embd=TYP` sind die eigenen Optionen `--output-tensor-type` und `--token-embedding-type` des Quantisierers. Nur für einen Typ, den llama-quantize macht (nicht F16, BF16, F32 oder Q8_0); höchstens 32 Einträge. Die Speicherschätzung zählt sie; `build_info.json`, die Prüfung auf gleiche Builds und ein Rezept aus dem Build (Format 3) tragen sie. Miss ein solches Rezept zuerst mit [`build tune start --candidate`](#build-tune). |
| `--accept-resource-risk` | Dein Ja zu einem Build, für den Platte oder Speicher dieses Computers nach der Prüfung vor dem Build nicht reichen (er kann spät scheitern oder auslagern). Am Terminal ohne die Option wirst du gefragt „Trotzdem bauen?“ (Nein ist die Vorgabe); ohne Terminal und ohne die Option startet nichts (Exit 2). |

Verhalten:

- Bevor etwas läuft, werden die fertigen Builds mit der Anfrage verglichen (die Prüfung des Builder-Tabs): Hat einer
  zu 100 % dieselben Optionen (Modell und sein Commit, Target, Board, Format, Quantisierung, Kalibrierung,
  Optimierung, ...), wirst du mit seinem vollen Pfad gefragt: **u** ihn nehmen, **a** noch einmal bauen, **c**
  abbrechen (`u` ist die Vorgabe). „Nehmen“ startet nichts und kopiert den früheren Build samt Golden-ZIP in den
  Ordner, in den dieser Build gegangen wäre (`--output`, die Antwort auf die Frage oder der Ausgabeordner; `-` kopiert
  nichts und nennt ihn): Zuerst wird der freie Platz dort geprüft (mit den Reservierungen jedes laufenden Builds und
  Downloads), jede Kopie wird per sha256 mit ihrem Original verglichen, und der frühere Build bleibt, wo er ist. Liegt
  der gleiche Build dort schon, wird nichts kopiert; liegt unter diesem Namen ein anderer Build, wird er erst ersetzt,
  wenn du die vollständige Liste dessen angenommen hast, was ersetzt wird („Nein“ ist die Vorgabe; in den Papierkorb
  oder endgültig). Für ein Hugging-Face-Modell wird der aktuelle Commit einmal nachgeschlagen (nie im Offline-Modus),
  wenn ein früherer Build gleich sein könnte. Ohne Terminal wird nichts gefragt: Der frühere Build wird genannt und der
  Build startet wie angegeben (nichts wird von selbst übersprungen).
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
  im Framework gespeicherte Token, und nur dort, wo Hugging Face es braucht (ein gated oder privates Repository;
  Dateien und Abfragen eines öffentlichen gehen ohne Token); ein Token in `HF_TOKEN` oder aus `hf auth login` wird
  nie verwendet.
- Im Offline-Modus wird nichts geladen: Ein Hugging-Face-Modell kommt aus seiner Kopie auf diesem Computer (die
  eines früheren Builds, des Hugging-Face-Browsers oder des alten Download-Caches). Ohne Kopie startet nichts, und
  die Modelle, die schon hier sind, werden mit ihren Labels aufgeführt (`models:...` für den Modellordner, die
  Repository-ID für die Kopie eines früheren Builds, der Pfad der Eingabe eines früheren Builds); am Terminal wirst
  du nach einem der Labels oder einem lokalen Pfad gefragt, ohne Terminal endet der Befehl mit Exit-Code 2 und du
  startest ihn mit `--model` noch einmal. Das Image des Targets wird offline weder gezogen noch gebaut: Es kommt
  das Image, das früher mit denselben Einstellungen gebaut wurde.
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
- Direkt nach den Prüfungen der Anfrage läuft die [Prüfung vor dem Build](#build-preflight) und gibt ihr Urteil aus:
  eine Zeile, wenn sie bestanden ist, sonst jeden Befund, was sie verglichen hat und was sie nicht vergleichen
  konnte. Ein Build, der so nicht gelingen kann (bei einem GPU-Build ist der NVIDIA-Treiber dieses Computers zu alt
  für das CUDA des GPU-Images des Targets, der NPU-Treiber des Boards liegt unter dem, was das Format braucht),
  startet nichts, wenn du ihn nicht übergehst; Warnungen (eine neuere CUDA-Nebenversion, keine NVIDIA-GPU, eine
  Runtime auf dem Board, die älter als das Toolkit ist) werden ausgegeben und in den Warnungen des Builds behalten,
  und der Build startet.
- Mit einem Board folgt die Speicherschätzung: wie viel Speicher das Modell im gewählten Typ auf dem Board braucht
  (die Gewichte Tensor für Tensor aus der `config.json` des Modells mit den Regeln von llama.cpp für den Typ
  geschätzt, etwa ein Zehntel für die Puffer des Servers und der KV-Cache eines Kontexts von 2048 Token) gegen den
  Speicher des Boards (seine Probe, sonst der größte Speicher, mit dem das Board kommt), mit dem Urteil passt, knapp
  (über drei Vierteln) oder passt nicht (über dem Speicher: nur mit Swap, wenn überhaupt). Sie ist eine Schätzung,
  sagt das und hält den Build nie an: Du entscheidest. Die `config.json` des Modells wird von huggingface.co zu dem
  Commit gelesen, den der Build holt (offline: die Kopie auf diesem Computer); ohne sie ist die Schätzung die gröbere
  aus der Parameterzahl. Der Build hält die Schätzung in `build_info.json` fest (`memory_estimate`, neben der echten
  Dateigröße), und die Warnung nach dem Build zählt denselben KV-Cache.
- Dann die Prüfung dieses Computers: Platte und Speicher gegen das, was der Build braucht, eine Zeile je Dateisystem
  (`Disk: needs about ... on the cache disk (estimate); ... free, ... kept free: enough`) und eine für den Speicher der
  Umwandlung (`host_fit`: die Importance Matrix lässt das Modell laufen, ein NPU-Export lädt es in PyTorch), mit passt,
  knapp oder passt nicht. Was auf der Platte landet, folgt dem Build: Eine GGUF-Eingabe braucht keinen
  Umwandlungsaufschlag, eine aufbewahrte F16-Zwischenstufe des Modells auch nicht. Passt ein Teil nicht, entscheidest
  du (`--accept-resource-risk` oder dein Ja im Terminal); ist die Platte knapp, nennt die Zeile die Größe der
  aufbewahrten F16-Zwischenstufen.
- Ein quantisierter GGUF-Build wandelt das Modell einmal nach F16 um: Der Importance-Matrix-Job und der Build-Job
  teilen diese Datei, und nach einem fertigen Build eines Hugging-Face-Modells mit bekanntem Commit wird sie im
  Build-Cache aufbewahrt (Einstellung `keep_f16_intermediates`, Vorgabe an; nur solange die Platte mehr als das Doppelte
  ihrer Reserve frei behält), sodass der nächste Build desselben Modells, ein anderer Typ, ein Auto-Tuning-Lauf oder
  der empfohlene Typ, von ihr aus quantisiert, ohne erneut umzuwandeln. [`build f16`](#build-f16) listet und löscht sie.
  Die F16 eines lokalen Modellordners wird nie aufbewahrt.
- Eine GGUF-Datei als `--model`: Ihr eigener Typ wird aus ihrem Kopf gelesen. Derselbe Typ, oder kein
  `--quantization`, reicht sie durch (nichts wird quantisiert; sie wird geprüft, verpackt und getestet); ein anderer Typ
  quantisiert sie neu, und eine schon quantisierte Datei bekommt `--allow-requantize` von llama-quantize mit der
  Warnung, dass das mehr verliert als das Quantisieren ihres 16-Bit-Originals (und dass ein Typ mit mehr Bits nichts
  gewinnt). Ein Hugging-Face-Repository mit nur GGUF-Dateien wird aus seiner heruntergeladenen Datei gebaut (der
  Hugging-Face-Browser oder `download_hf_file` für einen KI-Client).
- Am Ende jedes GGUF-Builds werden Tokenizer und Chat-Template gegen die Quelle geprüft: das Vokabular gegen die
  Embeddings, die EOS-ID gegen `config.json` und `generation_config.json`, die Token-IDs, die llama.cpp einem festen
  Text gibt (Englisch, Deutsch, Code, Ziffern, Emoji und der gerenderte Chat), gegen die eigene `tokenizer.json` der
  Quelle, das Chat-Template gegen das der Quelle und gerendert mit einem festen Gespräch, und jedes Spezial-Token des
  Templates als ein Token gelesen. Das Ergebnis steht am Ende (`Tokenizer and chat template: passed` oder die Warnungen
  mit dem ersten Unterschied) und in `build_info.json` (`tokenizer_check`); es lässt den Build nie scheitern. Eine
  GGUF-Eingabe wird mit ihrem eigenen Kopf verglichen.
- Mit `--recipe` gibt der Befehl zuerst das Rezept aus (Autor, Modell und Commit, Lizenz des Modells, Kontextlänge),
  die Optimierungsstufe und ob sie die des Rezepts, deine oder die des Board-Profils ist, was du daran geändert hast,
  seine Referenzergebnisse (Artefakt, sha256, Framework-Version, Toolchain-Image, ein Gerätetest) und eine Warnung,
  wenn die Dateien des installierten Targets von denen abweichen, mit denen das Rezept entstand. Eine
  `--dataset`-Datei mit dem sha256, den das Rezept festlegt, ist die des Rezepts; jede andere Datei ist deine (die
  Variable `dataset`). Ein Rezept, das eine Kalibrierdatei nennt, braucht `--dataset` oder `--no-dataset`. Ein Build,
  der auf dem eigenen Target des Rezepts nichts ändert, wird an seinem Ende mit der Referenz verglichen:
  `build_info.json` vermerkt das Rezept, die geänderten Variablen und `reproduced` (true oder false; null, wenn es
  nichts zu vergleichen gab), und ein abweichendes Ergebnis ist eine Warnung, die beide Framework-Versionen und
  Toolchain-Images nennt.

In der GUI: der Tab **Builder**: Modell, Target (**Targets…** zeigt jedes Target), Format (genau die Formate, die das
Target angibt, die Liste, die `--format` dafür nimmt; ein Target ohne Fähigkeiten bietet jedes Format, mit dem Hinweis,
dass nichts geprüft wird), Board, Quantisierung, **Optimization**
(alle Stufen, auch `FAST`), **Priorität**, Kalibrierung und GPU, dann **Start Build Pipeline**. Die
Fortschrittsanzeige folgt dem Build (`--follow`), **Cancel Build** stoppt ihn (Strg+C), und das fertige Modell wird in
einem Speichern-Dialog am Ausgabeordner angeboten (`--output`; **Datei > Output Folder...** setzt die Vorgabe). Die
Frage nach einem gleichen früheren Build ist dieselbe; **Den vorhandenen Build nehmen** öffnet den Ordnerdialog am
Ausgabeordner und kopiert ihn dorthin, wie oben.

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

# Ein Community-Rezept als Vorlage: sein Target und seine Werte, eine andere Quantisierung
llm-cli build start --recipe smollm2-135m-instruct-rk3588-gguf-q4_k_m -q Q8_0 -F
```

### build preflight

```
llm-cli build preflight -t TARGET [-f FORMAT] [-b BOARD] [--probe ID] [--gpu] [-m MODEL [-q QUANT] [--tensor-type MUSTER=TYP ...]] [--json]
```

Die Prüfung vor einem Build, ohne etwas zu starten; `build start`, der Builder, der Build eines Rezepts, ein
fortgesetzter Build und `start_build` der KI-Clients führen dieselbe Prüfung aus. Sie vergleicht zwei Probes, jede für
das, was sie sagen kann:

- Bei einem GPU-Build (`--gpu`) den NVIDIA-Treiber dieses Computers mit der CUDA-Version des GPU-Images des Targets
  (`Dockerfile.gpu`: sein `CUDA_VERSION`, oder ein `CUDA_VERSION` in den `docker.build_args` der target.yml, oder ein
  Basis-Image `nvidia/cuda`). Der Treiber meldet selbst, welches CUDA er kann (`nvidia-smi`, `NVIDIA_DRIVER_CUDA_MAX`
  der Probe). Eine neuere CUDA-Hauptversion, als der Treiber kann, hält den Build an; eine neuere Nebenversion
  derselben Hauptversion, keine NVIDIA-GPU mit funktionierendem Treiber und ein Target ohne GPU-Image sind Warnungen.
- Bei einem NPU-Format den NPU-Treiber und die Runtime des Boards (seine gespeicherte Probe: `--probe`, sonst die
  neueste Probe eines Boards dieses SoC) mit dem, was das Toolkit des Formats braucht (der `sdk:`-Eintrag des Formats
  in den Fähigkeiten des Targets). Rockchip RKNN: ein Treiber unter 0.9.2 hält den Build an, unter 0.9.8 ist er eine
  Warnung, ein `librknnrt`, der älter als RKNN-Toolkit2 2.3.2 ist, ist eine Warnung; RKLLM: ein `librkllmrt`, der
  älter als RKLLM-Toolkit 1.3.1 ist, ist eine Warnung.

Ein Wert, den eine Probe nicht hat (die Treiberversion braucht root auf dem Board: führe die Probe dort mit sudo aus;
keine Probe des Boards; dieser Computer nicht geprüft), steht unter „Not checked“, nie geraten. Exit-Code 0, wenn sie
bestanden ist oder nur Warnungen fand, 1, wenn der Build anhalten würde, 2 bei einer Anfrage, die der Build-Dienst
ablehnt (ein unbekanntes Target, ein Format, das das Target nicht baut, die Probe eines anderen Boards). `--json` gibt
das Urteil, die Befunde, was verglichen wurde und was nicht aus.

Mit `--model` (eine Hugging-Face-ID oder ein lokaler Pfad) und einem Board wird auch die
[Speicherschätzung](#build-start) des Modells in `--quantization` (Vorgabe: der Standardtyp des Boards) gegen den
Speicher des Boards ausgegeben, und `--json` trägt sie als `memory_estimate` (ihre Zahlen, ihr Urteil und ihr Text).
Danach folgt die Prüfung von Platte und Speicher dieses Computers (wie bei [`build start`](#build-start)), und `--json`
trägt sie als `resources`; `--tensor-type` lässt die Schätzung Typen je Tensor zählen. Beides sind nur Warnungen und
ändern den Exit-Code nie.

```bash
llm-cli build preflight -t Rockchip -f rknn -b rk3588
llm-cli build preflight -t Rockchip -f gguf --gpu --json
llm-cli build preflight -t ARM64 -b bcm2837 -m HuggingFaceTB/SmolLM2-360M-Instruct -q IQ4_XS
```

Nur du kannst trotzdem bauen: `build start --ignore-preflight` oder dein Ja im Terminal. Ein KI-Client sieht einen Halt
als Sicherheitswarnung seiner Start-Frage, die nur du beantwortest; keine dauerhafte Freigabe und keine Delegation
deckt ihn.

In der GUI: der Tab **Builder**, **Prüfung vor dem Build** neben dem Start-Knopf zeigt dieselben Zeilen und schreibt sie
ins Builder-Protokoll; der Start-Knopf fragt vor einem Build, den die Prüfung anhält (**Trotzdem bauen**, Abbrechen ist
die Vorgabe). Die Speicherschätzung ist die Zeile unter **Quantization**, aktualisiert, wenn Modell, Board oder Typ
wechseln.

### build tune

```
llm-cli build tune start -m MODEL -t TARGET [-b BOARD] [--probe ID] [--type TYPE ...] [--candidate TYP+MUSTER=TYP,... ...] [--imatrix auto|both] [--chunks N] [--ctx 256|512|1024] [-y] [-F]
llm-cli build tune list [--json]
llm-cli build tune show [RUN] [--json]
llm-cli build tune build [RUN] [--type LABEL] [-F]
```

Auto-Tuning: welche GGUF-Quantisierung eines Modells, und ob mit Importance Matrix, die beste Qualität ergibt, die noch
auf ein Board passt, gemessen statt geraten.

- **`start`** plant den Lauf und gibt ihn aus (die Kandidaten mit dem Urteil ihrer Speicherschätzung, die Referenz,
  die Bewertung und den Platz, den er braucht), fragt am Terminal (`--yes` startet ohne Frage) und führt ihn als einen
  Build aus: Das Modell wird geholt und konvertiert wie bei `build start`, dann wird jeder Kandidat quantisiert, wie
  ein Build ihn macht, und mit dem Original über die KL-Divergenz und die Top-1-Übereinstimmung auf einem festen
  englischen und deutschen Bewertungstext verglichen, im Build-Image, ohne Netz. **Kandidaten**: die Typen, die das
  Profil des Boards empfiehlt und die laut Speicherschätzung passen (oder knapp sind), dazu jeder Typ, den du mit
  `--type` nennst, gleich was seine Schätzung sagt (nichts wird verweigert). Typen unter 5 Bit pro Gewicht werden mit
  Importance Matrix gemessen; `--imatrix both` misst sie auch ohne. Die Referenz ist das F16-Modell, oder ein daraus
  gemachtes Q8_0, wenn das F16 nicht in drei Viertel des Speichers dieses Computers passt. `--candidate` fügt ein
  Rezept je Tensor hinzu, `TYP+MUSTER=TYP[,MUSTER=TYP]` (zum Beispiel `Q4_K_M+ffn_down=q6_k,attn_v=q8_0`): der Typ mit
  diesen Typen je Tensor quantisiert (siehe `build start --tensor-type`), gegen dieselbe Referenz auf demselben Text
  gemessen und als `Q4_K_M+<id>` aufgeführt (mit `-imatrix`, wenn der Typ die Importance Matrix liest). Ein Lauf,
  dessen Modell eine aufbewahrte F16-Zwischenstufe hat, quantisiert von ihr aus, ohne erneut umzuwandeln, und ein Lauf
  bewahrt die F16 auf, die er umgewandelt hat, wie ein Build. `--chunks` (4 bis 48,
  Vorgabe 16) und `--ctx` (Vorgabe 512) legen fest, wie viel Text gemessen wird. Strg+C bricht ab; der Lauf wird wie
  ein unterbrochener Build fortgesetzt und misst nur, was fehlt. Exit-Code 2 für eine Anfrage, die sich nicht planen
  lässt (zum Beispiel ein anderes Format als gguf oder ein `--type`, den das Target nicht baut).
- **`list`** zeigt die fertigen Läufe, die neuesten zuerst, mit ihrer Empfehlung; **`show`** den Bericht eines Laufs
  (Vorgabe der neueste): je Typ die Größe, das Urteil der Speicherschätzung, die KL-Divergenz mit ihrer Unsicherheit
  (bis 0,05 gut, bis 0,10 brauchbar), die Top-1-Übereinstimmung und die auf dem Board gemessenen Tokens pro Sekunde,
  und die Empfehlung mit ihrem Grund. Eine Geschwindigkeit steht nur da, wenn ein Board-Test sie gemessen hat
  (`llm-cli device runtime verify --bench-model` nach einem Build dieses Typs); ein Typ, der unter 8 Tokens pro Sekunde
  gemessen wurde, wird nicht empfohlen.
- **`build`** startet den normalen Build des empfohlenen Typs (oder `--type`, jeden gemessenen Kandidaten, wie der
  Bericht ihn nennt, zum Beispiel `Q4_K_M-imatrix`) mit Modell, Target und Board des Laufs; seine `build_info.json`
  nennt den Tuning-Lauf, und `llm-cli zoo export recipe` macht ein Rezept daraus. Ein Kandidat je Tensor wird mit
  seinen Typen je Tensor gebaut, und der Build hält sie als gemessen fest (`tensor_types_measured: true`).

```bash
llm-cli build tune start -m HuggingFaceTB/SmolLM2-360M-Instruct -t ARM64 -b bcm2712
llm-cli build tune start -m HuggingFaceTB/SmolLM2-135M-Instruct -t ARM64 -b bcm2837 --type Q8_0 --imatrix both -y
llm-cli build tune show
llm-cli build tune build --type Q5_K_M
llm-cli build tune start -m HuggingFaceTB/SmolLM2-135M-Instruct -t ARM64 -b bcm2712 --candidate Q4_K_M+ffn_down=q6_k
```

In der GUI: der Reiter **Auto-Tuning** (**Planen**, **Tuning starten**, die fertigen Läufe mit ihrem Bericht und **Das
Empfohlene bauen**). Ein KI-Client nutzt die optionalen Werkzeuge `start_tuning` und `get_tuning_report`; jeden
Tuning-Lauf, den er startet, beantwortest du, keine stehende Freigabe deckt einen.

### build save

```
llm-cli build save ARTIFACT [--to ORDNER]
```

Speichert einen fertigen Build, der im Build-Ordner des Frameworks wartet (ein GUI-Build, den du nicht
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

### build interrupted

```
llm-cli build interrupted list
llm-cli build interrupted continue BUILD_ID
llm-cli build interrupted delete BUILD_ID
```

Ein Build, der vor seinem Ende angehalten hat (er ist gescheitert, du hast ihn abgebrochen, ein Zeitlimit hat ihn
gestoppt oder der Prozess des Frameworks wurde beendet), behält seinen Ordner, wenn dort Stufen fertig waren: Erneut auf
demselben Arbeitsordner gestartet, führt der Stage-Runner eine fertige Stufe nicht noch einmal aus. Ein Build, von dem
sich nichts noch einmal nutzen lässt, wird sofort gelöscht. Direkt nach dem Ende eines solchen Builds fragt
`build start` im Terminal: fortsetzen, löschen oder später.

| Befehl | Was er tut |
|--------|------------|
| `list` | Jeden aufbewahrten Build: sein Target, seine fertigen Stufen, seine Größe, seinen Ordner und ob eine KI danach gefragt hat. |
| `continue BUILD_ID` | Führt die [Prüfung vor dem Build](#build-preflight) erneut aus (ein Treiber kann sich geändert haben; `--ignore-preflight` oder dein Ja im Terminal setzt trotzdem fort), dann startet er denselben Build erneut auf seinem aufbewahrten Arbeitsordner, als neuen Build-Auftrag, und verfolgt ihn wie `build start --follow`: eine Statuszeile mit seiner Stufe und dem Prozentwert, sobald sie sich ändert, Strg+C bricht ihn ab (sein Container wird gestoppt, Exit-Code 130), und am Ende, wie er geendet hat, mit dem Exit-Code dieses Endes (0 fertig, 1 gescheitert, 2 abgebrochen). Sein Auftrag erscheint in `build status` und in `get_build_status` wie jeder gestartete Build. |
| `delete BUILD_ID` | Zeigt den einen Eintrag (voller Pfad, Größe, was er ist) und löscht ihn nach deinem Ja im Terminal. Wenn ein Stufenmodul Dateien hinterlassen hat, die nur das Stufen-Konto ändern konnte, läuft vorher ein kurzer Aufräum-Container als dieses Konto (kein Netzwerk, keine Capability). Das Ergebnis sagt, was geblieben ist, mit den Pfaden. |

Nur du entscheidest. Ein KI-Aufrufer (MCP, Ditto) liest den aufbewahrten Build in `get_build_status` mit seiner
Bezeichnung und seinen fertigen Stufen, und seine Frage wartet in `list` auf dich. In der GUI: Build >
**Build-Verlauf…** > **Fortsetzen oder löschen…**.

### build deps

```
llm-cli build deps list
llm-cli build deps fetch NAME
llm-cli build deps delete
```

Der Abhängigkeits-Cache der CMake-Optionen von Modulen ([Modul-Entwicklerhandbuch, Abschnitt
3.5](Module_Developer_Guide_DE.md#35-cmake-flags-eines-moduls)): Was `-DLLAMA_BUILD_BORINGSSL=ON`,
`-DLLAMA_BUILD_LIBRESSL=ON` oder `-DGGML_CPU_KLEIDIAI=ON` beim Bauen von llama.cpp herunterladen würde, wird stattdessen
vor dem Build geholt, festgelegt, wie das festgelegte llama.cpp es angibt, gegen die sha256 vom ersten Holen geprüft und
nur lesend in den Build eingehängt. Ein Build holt selbst, was sein Modul braucht; diese Befehle sind zum Nachsehen und
zum Holen, solange du online bist.

| Befehl | Was er tut |
|--------|------------|
| `list` | Jede Abhängigkeit, die das Framework holen kann (BORINGSSL, LIBRESSL, KLEIDIAI): die Option, die sie braucht, ihre Quelle, ihr Pin, ob sie im Cache ist (mit Größe) und ihre festgehaltene sha256. |
| `fetch NAME` | Holt eine jetzt (Groß- und Kleinschreibung egal), festgelegt und geprüft. Im Offline-Modus wird nichts heruntergeladen (Exit-Code 1, mit dem, was zu tun ist); ein unbekannter Name ist Exit-Code 2. |
| `delete` | Listet die Einträge des Caches und löscht die, die du auswählst, nachdem du die ganze Liste im Terminal bestätigt hast (Papierkorb oder endgültig). Die festgehaltene sha256 bleibt: Wird derselbe Pin wieder geholt, muss derselbe Inhalt zurückkommen. |

`build start` nennt vor dem Start jede Abhängigkeit, die sein Modul braucht (`Build dependency: ...`); im Offline-Modus
stoppt eine, die nicht im Cache ist, ihn dort (Exit-Code 2).

In der GUI: Build > **Build-Abhängigkeiten…** (Holen, Löschen…).

### build f16

```
llm-cli build f16 list
llm-cli build f16 delete
```

Die aufbewahrten F16-Zwischenstufen: die F16-GGUF, die ein quantisierter GGUF-Build aus einem Hugging-Face-Modell
umgewandelt hat, im Build-Cache (`<cache_dir>/f16/<schlüssel>/`) je Modell, Commit und llama.cpp-Festlegung aufbewahrt,
sodass der nächste Build desselben Modells von ihr aus quantisiert, ohne erneut umzuwandeln (siehe
[`build start`](#build-start)). Ein Build bewahrt eine nur auf, solange die Einstellung `keep_f16_intermediates` an ist
(die Vorgabe) und die Platte mit ihr mehr als das Doppelte ihrer Reserve frei behält.

| Befehl | Was er tut |
|--------|------------|
| `list` | Jede aufbewahrte Datei: Modell, Commit, llama.cpp-Festlegung, Größe, wann sie aufbewahrt und wann sie zuletzt von einem Build genutzt wurde. |
| `delete` | Listet sie und löscht die, die du wählst, nachdem du die vollständige Liste im Terminal bestätigt hast (Papierkorb oder endgültig). |

Ein KI-Client liest ihre Zahl und Größe in `get_status` und löscht sie nie. In der GUI: Build >
**F16-Zwischenstufen…** (Löschen…).

### build status

```
llm-cli build status [REQUEST_ID] [-a|--all] [--wait SEKUNDEN]
```

Zeigt Builds aus dem gemeinsamen Build-Zustand: Jedes Programm, das baut (CLI, GUI, MCP-Server), schreibt eine
Zusammenfassung jeder Build-Anfrage in den Benutzerordner (`build_requests/`) und je Build einen Eintrag in den
Cache-Ordner (`<cache_dir>/build_records/`) mit Request, Modell, Target, Format, Quantisierung, Status, Stufe und
Fortschritt, deshalb erscheinen auch Builds, die ein anderer Prozess gestartet hat, einschließlich Anfragen, die
noch in seiner Warteschlange warten (Spalte `Where`: das Programm, das sie ausführt). Ohne ID: eine Tabelle
der 20 neuesten Builds; mit einer Request-ID (`req_...`) oder einer Build-ID: die Einzelheiten jedes Versuchs,
einschließlich des Fehlers eines gescheiterten Builds (Zugangsdaten geschwärzt) und bei einem fertigen Build seiner
Statistik (wie lange Konvertierung und Quantisierung liefen, die Größe davor und danach, aus seiner
`build_info.json`). Ein Build, dessen Prozess endete,
bevor der Build fertig war, erscheint als `interrupted`. Exit-Code 1, wenn die ID nicht gefunden wird. Der Befehl
braucht weder den Tresor für Zugangsdaten noch Docker. Unter Linux und macOS sind der Ordner der Einträge und die
Einträge nur für den Benutzer lesbar, der gebaut hat (Modi 0700 und 0600), sodass in einer gemeinsamen Installation
jeder Benutzer seine eigenen Builds sieht.

| Option | Bedeutung |
|---|---|
| `-a, --all` | Jeden aufbewahrten Build statt der 20 neuesten zeigen (aufbewahrt werden die 200 neuesten fertigen oder unterbrochenen Builds). |
| `--wait SEKUNDEN` | Mit `REQUEST_ID`: höchstens SEKUNDEN warten, bis der Build zu Ende ist (auch einer, den die GUI oder ein MCP-Client gestartet hat), dann ihn zeigen und sagen, wie er endete. |

Wie ein Build endete, ist sein Endgrund (Feld `End`): `done` (sein Abschluss-Eintrag sagt fertig; der einzige Erfolg),
`failed`, `cancelled`, `soft_cap` (ein von einer KI gestarteter Build lief über seine weiche Grenze) oder `idle` (ein
Schritt hat so lange nichts ausgegeben wie die Leerlauf-Grenze). Ein Build endet, weil sein Stufen-Runner seinen
Abschluss-Eintrag geschrieben hat, nie weil danach eine Uhr abläuft. `build status REQUEST_ID --wait SEKUNDEN` und
`build start --follow` enden daran und sagen es in klaren Worten („Build ready“, „Error while building: …“, „Build
stopped: …“), mit diesen Exit-Codes:

| Exit-Code | Endgrund |
|---|---|
| 0 | done |
| 1 | failed, soft_cap, idle |
| 2 | cancelled (Strg+C in `build start` bleibt 130) |
| 3 | nur `--wait`: läuft noch, als das Warten endete |

Ein Request, der auf eine Self-Healing-Entscheidung wartet, zeigt seinen gescheiterten ersten Versuch; entscheide in
dem Programm, das ihn ausführt. Mit einer Request-ID folgen die Korrekturvorschläge, die KI-Clients dafür gemacht
haben (`propose_healing_fix`), jeder als KI-erzeugt markiert mit dem, der ihn vorgeschlagen hat (der Aufrufer und der
eigene, selbst angegebene Name des Clients), und seinem manuellen Schritt, den das Framework nie ausführt. Ohne ID
sagt eine Zeile, für wie viele gescheiterte Builds es solche Vorschläge gibt.

In der GUI: **Build > Build-Verlauf…**: jeder Build (alle, wie `--all`); **Details anzeigen** (oder ein Doppelklick)
zeigt einen Build wie mit `REQUEST_ID`, mit seinen Fehlern, jedem Versuch und seinem Ausgabeordner und den
Korrekturvorschlägen der KI-Clients.

### build image

Das Build-Container-Image ist das Docker-Image mit der Werkzeugkette eines Targets, in dem seine Build-Stufen laufen
(nicht das Target-Artefakt, Paket oder Bündel, das aufs Board geht). Der Offline-Modus baut es nur, wenn jede Quelle,
die sein Dockerfile braucht, auf diesem Computer oder in deinem eigenen Netz liegt; diese Befehle zeigen das, setzen
die Quellen und bringen ein fertiges Image auf einen anderen Offline-Rechner.

```
llm-cli build image plan TARGET [--gpu] [--json]
llm-cli build image sources [--apt-mirror URL] [--apt-proxy URL] [--pip-index URL]
llm-cli build image layer [--toolbox build|build-rust|fetch]
llm-cli build image export TARGET --to DATEI [--image TAG_ODER_ID]
llm-cli build image import DATEI [--sha256 HEX] [--target TARGET] [--yes]
```

- `plan` listet die Build-Container-Images von TARGET auf diesem Computer und jede Quelle, die ein Offline-Build
  eines neuen braucht: die Basis-Images (auf diesem Computer, aus einer Registry in deinem Netz oder über einen
  Registry-Spiegel des Docker-Daemons dort; jeder Spiegel des Daemons wird geprüft), llama.cpp (die
  Repository-Überschreibung in deinem Netz oder die geprüfte lokale Kopie des festgelegten Commits, wie
  `ditto runtime import-source` sie ablegt), apt- und pip-Pakete (nur über die Quellen unten) und jede andere Adresse,
  von der das Dockerfile holt. Jede Zeile sagt lokal oder nicht, wie sie bereitsteht oder bereitgestellt werden kann
  und für dich den Host und warum. Nichts wird gezogen, geholt oder gebaut. `--json` gibt nur die Labels aus, wie
  eine KI sie liest (keine Adresse, kein Pfad).
- `sources` zeigt oder setzt den apt-Spiegel (er bedient die Pfade der Distribution, etwa `/debian`), den
  apt-Cache-Proxy (etwa apt-cacher-ng) und den pip-Index in deinem Netz und prüft jeden nach der Lokalitätsregel
  (deine Ausnahmen aus `config local-network` zählen). Eine Adresse ist http oder https ohne Benutzernamen,
  Passwort, Token oder Query; `""` löscht eine. Nur du setzt sie: Eine KI und MCP `update_settings` werden
  abgelehnt.
- `layer` zeigt die Laufzeit-Schicht des Frameworks, die jeder Build-Container ausführt (ihren Stufen-Runner, den
  Helfer des Stufen-Kontos und eine statische busybox und setpriv): ob sie der mitgelieferten Kopie entspricht, wie
  ihre Werkzeuge entstanden und welches setpriv sie enthalten (`util-linux` oder `llmcf-setpriv`) und ob dieser
  Rechner den Abschluss-Ordner eines Builds schützt (Docker Desktop unter Windows und macOS tut es nicht: Ein solcher
  Build wird als „Abschluss-Ordner ungeschützt“ markiert). `--toolbox` wählt, wie die Werkzeuge einmal entstehen:
  `build` (die Vorgabe: beide aus festgelegten Quellen im Builder-Image des Frameworks, mit util-linux setpriv),
  `build-rust` (busybox genauso, und der eigene Rust-uid-Wechsel des Frameworks, llmcf-setpriv, aus diesem Repository
  in einem festgelegten Rust-Builder-Image gebaut) oder `fetch` (festgelegte fertige Dateien aus der Tabelle im
  Repository: busybox von Debian, setpriv nur als llmcf-setpriv aus einem Release dieses Projekts; solange kein
  Release festgelegt ist, verweigert `fetch` und nennt `build` und `build-rust`). Ein schon gemachter Werkzeugsatz
  bleibt, bis er neu gemacht wird. Nur du setzt es: Eine KI und MCP `update_settings` werden abgelehnt. Dasselbe
  steht in **Build > Build-Container-Images...**.
- `export` schreibt das neueste Image von TARGET (oder `--image`) mit `docker save` in eine neue DATEI und seine
  sha256 in `DATEI.sha256` daneben; nichts wird ersetzt, und der Ziel-Datenträger muss seine Reserve frei behalten.
- `import` prüft DATEI gegen `--sha256` oder `DATEI.sha256`, liest dann die Labels des Images: Sein Target und die
  Eingaben, die ein Build dieses Targets hier berechnet, müssen passen (ein Image für eine andere CPU, einen anderen
  llama.cpp-Pin oder andere Einstellungen wird abgelehnt, ohne dass etwas geladen wird), und lädt es dann
  (`docker load`, auch unter Podman). Exit-Code 1 bei einer Ablehnung. Ohne `--yes` fragt es vor dem Laden.

Ein Build im Offline-Modus nutzt zuerst ein früher mit denselben Einstellungen gebautes Image; ohne eines baut er eines
aus den Quellen oben oder hält vor allem Langsamen an und nennt jede Quelle, die nicht lokal ist. Bekannte Grenze: Eine
festgelegte IPv6-Adresse eines Spiegels erreicht BuildKit als `NAME=[ADRESSE]`, was ein neueres docker buildx braucht.

In der GUI: **Build > Build-Container-Images...** (Target, die Images, **Offline-Quellen prüfen**, die Quellen in
deinem Netz mit **Quellen speichern**, **In Datei exportieren...** und **Aus Datei importieren...** mit dem
sha256-Feld).

### build preload

Das Preload des Builds für einen Rechner ganz ohne Netz: Fehlen einem Offline-Build Quellen (das Build-Container-Image
des Targets ist nicht hier, und `build image plan` nennt Quellen, die nicht lokal sind), schreibt llmcf Preload-Skripte
für genau diese, du führst eins auf einem Rechner mit Netz aus, und das Archiv, das es schreibt, wird als lokale
Quellen aufgenommen.

```
llm-cli build preload write TARGET [--gpu]
llm-cli build preload take DATEI --target TARGET [--sha256 HEX]
llm-cli build start ... --preload DATEI
```

- `write` schreibt `preload.sh` und `preload.ps1` nach `llmcf-preload-scripts/build-TARGET/` in deinem Zustandsordner
  (`~/.llm-framework`) und sagt, was sie holen: die Basis-Images, die der Plan nennt (über die HTTP-API der Registry
  gelesen, jeder Blob gegen seinen Digest geprüft, als `docker save`-Archiv geschrieben; auf dem Rechner mit Netz ist
  keine Container-Engine nötig), llama.cpp beim gepinnten Commit (ein Git-Bundle; dort braucht es git), die
  Python-Pakete des Build-Images, wie das Target sie in `target.yml` `build_preload` erklärt (Wheels für das Python
  des Images und die Plattform dieses Rechners, mit ihren Abhängigkeiten), die gepinnten Downloads des Dockerfiles
  (Größe und sha256 geprüft), die Dateien, die die Vorlage des Targets aus Quell-Images nimmt, und die
  Distributionspakete des Build-Images: die `apt-get install`-Pakete jeder Stufe des Dockerfiles des Targets (für die
  Architektur dieses Rechners) mit ihren Abhängigkeiten, aufgelöst gegen das, was das Basis-Image der Stufe schon hat
  (sein dpkg-Status, aus dem Image auf diesem Rechner oder aus dem, das das Preload holt, gelesen, nie ausgeführt),
  aus dem Archiv der Distribution geholt und gegen ihre signierten Paketlisten mit den Archivschlüsseln geprüft, die
  llmcf festlegt, im Ordner `packages/` des Board-Preloads. Was ein Preload nicht tragen kann, wird mit seinem eigenen
  Weg genannt: apt-Pakete eines Dockerfiles, dessen Installationen nicht sicher gelesen werden können oder das kein
  `ARG APT_PRELOAD` nimmt (ein apt-Spiegel oder -Cache in deinem Netz, `build image sources`), andere
  Paketverwaltungen, ein über seinen Digest gepinntes Basis-Image, ein Download ohne gepinntes sha256. Die Skripte brauchen nur Python 3 mit pip (und git für llama.cpp); sie schreiben
  `llmcf-preload-build-TARGET-ARCH-REZEPT.tar.gz` und geben sein sha256 aus. Mach es auf einem Rechner mit derselben
  Architektur wie dieser: pip wählt die Abhängigkeiten der Pakete nach den Markern des Rechners, der lädt.
- `take` prüft das Archiv, bevor etwas verändert wird: nur Dateien und Ordner unter `llmcf-preload/`, keine Links,
  jede Datei in `SHA256SUMS` mit ihrem sha256 (wie beim Preload des Installers), dann die Art `build`, das Target, die
  Plattform dieses Rechners, ein hier geschriebenes Rezept (ein Archiv aus dem Rezept eines anderen Rechners oder
  eines anderen Builds wird abgelehnt), jedes Image-Archiv, das genau sein Basis-Image nennt, llama.cpp über seinen
  Commit, jeder Download und jede genommene Datei über ihr gepinntes sha256 und die Distributionspakete, wie ein
  Board-Preload geprüft wird (die Archivschlüssel nur mit festgelegtem Fingerabdruck, jede `Release` von einem davon
  signiert, vom Release und der Architektur der Basis, jede Liste gegen ihre `Release`, jede `.deb` gegen ihre Liste,
  keine andere Datei; noch einmal mit `gpgv`, wenn dieser Rechner es hat); `--sha256` vergleicht auch das sha256 des
  Archivs selbst. Dann werden die Basis-Images in die Container-Engine geladen, llama.cpp kommt in den Quellspeicher,
  und die Wheels, Downloads, genommenen Dateien und Distributionspakete bleiben in `build-preload/` deines
  Zustandsordners für jeden späteren Offline-Build des Targets; der Build installiert die Pakete aus einem
  Datei-Repository in seinem Build-Kontext, das die festgelegten Schlüssel signieren, ohne andere Paketquelle und ohne
  Netz. Exit-Code 1 bei einer Ablehnung.
- `build start` macht das im Offline-Modus von selbst: Ist das Image nicht hier und fehlen Quellen, schreibt er die
  Skripte, sagt, wo sie liegen und was sie holen, und fragt im Terminal nach dem Pfad des Archivs (Enter ohne Pfad
  startet nichts); `--preload DATEI` gibt es vorab; ohne Terminal hält der Lauf mit den Schritten an (Exit-Code 2).

Ein Archiv, das eine Datei enthält, deren Lizenz die Weitergabe nicht erlaubt (`redistributable: false` in den
`source_images` der Vorlage), ist mit `personal=yes` markiert: Es ist für deine eigenen Rechner; gib es nicht weiter.
In der GUI: **Start Build** im Builder fragt im Offline-Modus dasselbe in einem Dialog (**Aufnehmen und bauen**), und
**Build > Build-Container-Images...** hat **Preload-Skripte schreiben...** und **Ein Preload aufnehmen...**. Ein
KI-Client nennt ein Archiv nur über das Feld `preload` von `start_build`, als Verweis auf eine lokale Datei, und nur
eins aus einem hier geschriebenen Rezept.

Ein Quell-Image, das nur als Datei veröffentlicht ist (in den `source_images` der Vorlage über den sha256 der Datei
genannt, siehe unten), steht im Rezept mit seinem sha256 und Namen; das Skript nimmt es nur aus der Datei, die du ihm
gibst: `sh preload.sh --image-file DATEI` (`-ImageFile DATEI` in PowerShell; für mehrere Dateien wiederholen). Ohne sie
hält das Skript an und nennt die Datei, die es braucht.

### build source-image

Die Images, aus denen die Vorlage eines Targets Dateien nimmt (`template_variables.source_images`): ein Image in einer
Registry, über seinen Digest gepinnt, das ein Build mit Netz selbst liest, oder ein Image, das sein Hersteller nur als
Datei veröffentlicht (ein Archiv aus `docker save` oder ein Archiv im OCI-Image-Layout, auch gzip-komprimiert), über
den sha256 der Datei gepinnt, das kein Build holen kann, also gibst du es an.

```
llm-cli build source-image list --target TARGET
llm-cli build source-image take DATEI --target TARGET
```

- `list` zeigt jedes Quell-Image, wie es genannt ist (Registry-Digest oder sha256 und Name der Datei), seine Plattform
  und wie viele seiner Dateien in deinem Cache liegen (`build-preload/image-files/` in deinem Zustandsordner).
- `take` prüft zuerst den sha256 von DATEI: Er muss einer sein, den das Target nennt, sonst wird nichts darin gelesen
  (Exit-Code 1). Dann wird das Image der Plattform gewählt (einem OCI-Layout wird von `index.json` aus gefolgt, ein
  Archiv aus `docker save` über seine `manifest.json`; eine Datei ohne Image für die Plattform wird abgelehnt, mit den
  Plattformen, die sie hat), jedes Dokument und jede Schicht gegen ihren Digest und jede Schicht gegen die `diff_ids`
  der Konfiguration geprüft (gzip-komprimierte und unkomprimierte Schichten), die Schichten von oben nach unten mit
  beachteten Whiteouts gelesen, und nur die genannten regulären Dateien bleiben, jede gegen ihren sha256 geprüft. Ein
  Pfad mit `..`, ein Link, eine Gerätedatei oder eine gelöschte Datei wird abgelehnt und genannt. Nichts wird
  entpackt, in die Container-Engine geladen oder ausgeführt, und DATEI selbst wird nicht aufbewahrt.

Ein Build mit Netz, dessen Cache die Dateien eines solchen Images fehlen, hält vor allem Langsamen mit diesem Schritt
an; offline nennt der Plan sie als fehlende Quelle, und das Preload des Builds kann sie bringen (oben). In der GUI:
**Build > Build-Container-Images...** hat **Quell-Images** und **Aus einer Image-Datei nehmen...** (ein Dateidialog).
Ein KI-Client sieht nur die Labels des Plans und nennt die Datei nie.

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
llm-cli deploy devices pair-code [--wait SEKUNDEN]
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
| `edit GERÄT` | Ändert die Adresse eines vertrauten Boards (`--host`: Hostname oder IP-Adresse, `--port`) oder seinen SSH-Host-Schlüssel in der `known_hosts` des Frameworks, z. B. nachdem sich seine Adresse geändert hat oder es neu installiert wurde. `GERÄT` ist seine Geräte-ID, sein Name oder seine Adresse. Den neuen Schlüssel fügst du mit `--key` ein (die Zeile aus `/etc/ssh/ssh_host_ed25519_key.pub` auf dem Board, eine `known_hosts`-Zeile oder eine Zeile von `ssh-keyscan`, in Anführungszeichen; niemand tippt einen Schlüssel ab), oder `--read-key` liest ihn vom Board unter seiner (neuen) Adresse; eine Zeile mit Optionen aus `authorized_keys`, Befehlen oder einem zweiten Schlüssel und der Schlüssel eines anderen vertrauten Boards werden abgelehnt. Braucht ein Terminal: Es zeigt die Adresse und den Fingerabdruck jetzt und nach der Änderung und fragt ("Nein" ist die Vorgabe); die Änderung kommt zuerst ins Audit-Log. Ein neuer Schlüssel gibt dem Board eine neue Geräte-ID (sie leitet sich vom Schlüssel ab): Der Eintrag zieht mit Name und Benutzer dorthin, und ob sich der Schlüssel des Frameworks anmeldet, wird einmal getestet. Die Daten des Boards ziehen mit: seine gespeicherte Probe, die Deploy-Einstellungen, die deployte Laufzeit (der Eintrag des letzten Deployments mit seinen Prüfungen und seinem Benchmark und seine Laufzeit-Pakete), der Deploy-Eintrag und seine Benchmark-Ergebnisse bei den Builds ("getestet auf"). Jeder Teil wird zur neuen ID kopiert und geprüft, bevor sich die `known_hosts` ändert, und die Kopien der alten ID gehen erst nach der Änderung; scheitert eine Kopie, wird nichts geändert. Ein Teil, den die neue ID schon mit anderem Inhalt hat, bleibt unter der alten ID, ebenso eine alte Kopie, die sich nicht entfernen lässt; der Befehl nennt beides. Prüfe das Board erneut, wenn es neu eingerichtet wurde. Hält etwas auf diesem Computer die Änderung auf (die `known_hosts` des Frameworks ist von einem anderen Teil des Frameworks belegt, etwa der GUI oder einem MCP-Server, Einstellungen oder eine Datei lassen sich nicht lesen), sagt der Befehl das mit dem Grund (`local_problem`) und Exit-Code 1, nie mit einem Traceback; es wird nichts geändert. |
| `pair-code` | Koppelt ein Board, dessen Hardware-Probe einen Kopplungscode zeigt (`hardware_probe.sh --pair-code` oder `hardware_probe.py --pair-code`, mit sudo auf dem Board ausgeführt; die Probe startet `llmcf-pair`, das höchstens 10 Minuten lang 8 Ziffern auf der Konsole des Boards zeigt). Es sucht solche Boards nur auf den lokalen Verbindungen dieses Computers (`--wait`, Standard 3 Sekunden): ein Kabel direkt zwischen Computer und Board (Link-Local-Adressen, kein Router nötig), der eigene Hotspot des Computers, das WLAN des Boards als Access Point oder das eigene Netz; nie über das Internet. Du wählst das Board und gibst den Code ein (verdeckt; er geht nie über das Netz: SPAKE2). Ein falscher Code verbraucht einen der drei Versuche des Boards. Nachdem beide Seiten den Code bewiesen haben, werden die SSH-Host-Keys des Boards über denselben Kopplungskern festgehalten wie das Ergebnis eines Probe-Kits (ein einmaliges Kopplungsangebot, im Audit-Log vermerkt; die Fingerabdrücke werden aus den Schlüsselzeilen berechnet, die das Board bewiesen hat; keine Host-Key-Frage folgt), mit der Adresse des Boards: seiner IPv4-Adresse auf der Verbindung, sonst seiner link-lokalen IPv6-Adresse mit der Schnittstelle (`fe80::...%eth0`, eine Adresse, die `deploy` und `scan --device` nehmen). Die Schlüsselzeile und die Signer-Dateien des Frameworks gehen an das Board, wo die Probe sie mit dem eigenen Root-Schritt des Probe-Kits installiert und die Kopplung in ihrer Ergebnisdatei wie ein Kit meldet (ein späterer Import dieser Datei bringt die Hardware-Daten; die Kopplung gilt als erledigt). Braucht ein Terminal; nie über eine KI. |
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

### deploy ssh-config

```
llm-cli deploy ssh-config [show]
llm-cli deploy ssh-config edit HOST [--line "OPTION WERT" ...]
llm-cli deploy ssh-config import HOST
```

Die eigenen OpenSSH-Einstellungen des Frameworks: die Datei `ssh_config` im Zustandsordner des Frameworks, die jeder
Aufruf des OpenSSH-Clients dieses Computers (der Rückfallweg ohne paramiko) mit `-F` bekommt, darum werden weder
`~/.ssh/config` noch `/etc/ssh/ssh_config` gelesen. Sie wird bei der ersten Nutzung angelegt, ist nur für dich lesbar
und enthält nur, was du hier hinzufügst. paramiko liest keine Einstellungsdatei.

| Befehl | Bedeutung |
|---|---|
| `show` (auch ohne Unterbefehl) | Gibt den Pfad der Datei und ihren Inhalt aus. |
| `edit HOST --line ...` | Setzt den Block `Host HOST` (die Adresse oder der Hostname des Boards, wie das Framework es nennt) auf die angegebenen Zeilen, ein `--line` je Optionszeile, zum Beispiel `--line "ProxyJump jump.example"` oder `--line "User radxa"`; ohne `--line` fällt der Block weg. Es zeigt den Block jetzt und nach der Änderung. Eine Zeile, die ändern würde, wie einem Host-Schlüssel vertraut wird, einen Befehl ausführen, eine andere Datei einbinden oder Verbindungen teilen würde, wird mit Grund abgelehnt, und nichts wird geschrieben (`StrictHostKeyChecking`, `UserKnownHostsFile`, `GlobalKnownHostsFile`, `KnownHostsCommand`, `HostKeyAlias`, `HostName`, `LocalCommand`, `RemoteCommand`, `Include`, `Match`, `ControlMaster`, Weiterleitungen, ...; Exit-Code 1). Ein `ProxyJump` oder `ProxyCommand` wird vor der Frage zum Schreiben erklärt und eigens erfragt; "Nein" ist bei beiden die Vorgabe. Dein Ja zu genau diesen Zeilen wird festgehalten, und ein danach von Hand geänderter `ProxyJump` oder `ProxyCommand` wird nicht benutzt, bis du dieselben Zeilen hier noch einmal eingibst und Ja sagst. `IdentityFile`, `CertificateFile`, `IdentityAgent`, `AddKeysToAgent`, `SetEnv` und `SendEnv` werden ebenfalls abgelehnt; `LogLevel` ist erlaubt. Braucht ein Terminal; die Änderung wird ins Audit-Log geschrieben. |
| `import HOST` | Liest den eigenen Block von `HOST` aus deiner `~/.ssh/config` (nur gelesen, nie geändert: der erste `Host`-Block, der `HOST` selbst nennt; Muster wie `Host *` passen nicht, `Include` und `Match` werden nicht verfolgt), zeigt ihn und macht dann mit seinen Zeilen wie `edit` weiter: dieselben Prüfungen und Fragen. |

Die Vertrauenseinstellungen bleiben auf der Befehlszeile von OpenSSH, wo der erste Wert gilt:
`StrictHostKeyChecking=yes`, die `known_hosts` des Frameworks, das Null-Gerät als `GlobalKnownHostsFile`, nur sein
eigener Schlüssel (`IdentitiesOnly`). Ein `%` in diesen Pfaden wird als `%%` geschrieben. Eine von Hand bearbeitete
Datei mit einer abgelehnten Zeile stoppt jeden OpenSSH-Aufruf mit Grund (`local_problem`, Exit-Code 1), bis du sie
änderst. Kein KI-Tool kann diese Datei schreiben.

In der GUI: **Boards > Boards und Geräte... > SSH-Einstellungen...** zeigt die Datei und den Block eines Boards (die
Adresse des ausgewählten Boards ist eingetragen), mit **Aus ~/.ssh/config übernehmen** und denselben Ablehnungen und
Fragen.

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
Tool-Hinweise und Fehler nach stderr, ohne Steuerzeichen (Escape-Sequenzen, Wagenrückläufe, Bidi- und
Nullbreitenzeichen; der Verbinder in einem Emoji bleibt). Strg+C oder das Ende der Eingabe an einer Bestätigung ist
ein Nein. Nichts wird im Chatverlauf gespeichert, und `--provider`/`--model` gelten nur für diesen Befehl: Sie werden
nie gespeichert, auch nicht, wenn ein Tool während der Frage die Einstellungen speichert.

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

### ditto console

```
llm-cli ditto console [--follow] [--source QUELLE] [--search TEXT] [--limit N] [--json]
```

Zeigt, was Ditto und KI-Apps tippen und tun, dieselben Zeilen wie die Ditto-Konsole neben dem Chat in der GUI: Dittos
Antworten, die Antworten einer App, den Grund, den eine KI für eine Entscheidung festgehalten hat, Dittos
Werkzeug-Notizen, die Aufzeichnungen dessen, was eine KI geändert, gestartet, beendet, gefragt hat oder nicht tun
konnte, und jeden Tool-Aufruf von Ditto oder einer App mit seinen Argumenten und wie er endete (gestartet, erledigt,
fehlgeschlagen, bittet um dein Ja, nicht bestätigt, abgelehnt mit seinem Grund). Jede Zeile beginnt mit ihrer Zeit und
ihrer Quelle: Ditto oder die App unter der Verbindung, unter der das Framework sie kennt, ihr eigener Name nur in
Anführungszeichen als ihre Angabe. Deine eigenen Aufrufe bleiben weg. Die Zeilen werden aus dem Chat-Feed und dem
Audit-Log gelesen, die der Befehl nie verändert; Secrets, die Werte von Argumenten, die Schlüsselmaterial halten
(gezeigt als `[verborgen]`), und dein Home-Ordner (`~`) erscheinen nie, und die Ausgabe eines Tools wird nie gezeigt.

| Option | Bedeutung |
|---|---|
| `--follow`, `-f` | Neue Zeilen weiter drucken, sobald sie kommen, bis Strg+C. |
| `--source QUELLE` | `all` (Standard), `ditto`, `apps` oder eine App über ihre Aufrufer-ID: `mcp` (die gemeinsame Verbindung) oder `mcp:<Tokenname>`. |
| `--search TEXT` | Nur die Zeilen, die diesen Text enthalten (Groß- und Kleinschreibung egal). |
| `--limit N` | Wie viele der neuesten Zeilen zuerst gedruckt werden (Standard 50, höchstens 2000). |
| `--json` | Ein JSON-Objekt pro Zeile: `key`, `ts`, `source`, `caller`, `who`, `kind` (`says`, `call`, `did`), `text`, `level`. |

```bash
llm-cli ditto console --follow
llm-cli ditto console --source apps --search start_build
llm-cli ditto console --json --limit 200
```

In der GUI: **Ansicht > Ditto-Konsole** (Umschalt+F9) oder der Knopf **Konsole** der Werkzeugleiste zeigt die Konsole
neben dem Chat, mit **Zeigen:** (`--source`), **Suchen:** (`--search`), **Mitlaufen anhalten**, **Ansicht leeren**
(nur die Ansicht) und **Kopieren**.

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
| `--kind [healing\|optimizer\|module\|chat\|retrieval\|tokens\|openai_paid]` | Nur Fälle dieser Art; wiederholbar. `retrieval` läuft nur, wenn es hier gewählt ist, wenn `--case` ohne `--kind` angegeben ist oder wenn die Datei aus `--cases` Retrieval-Fälle enthält. `tokens` (die [Token- und Cache-Messung](#token--und-cache-messung)) läuft nur, wenn es hier oder mit `--case` gewählt ist; `openai_paid` (der bezahlte OpenAI-Test) nur, wenn es hier gewählt ist, allein und mit `--provider openai --i-pay`. |
| `--pause-minutes FLOAT RANGE` | Nur `tokens` und `openai_paid`: so viele Minuten zwischen den Runden warten (0,1 bis 180), um Cache-Lebensdauern zu vergleichen. Standard: keine Pause. In CI abgelehnt (Exit-Code 2). |
| `--i-pay` | Nur `openai_paid`: Du startest den bezahlten Test selbst, abgerechnet über deinen eigenen OpenAI-Key. Die CLI fragt am Terminal noch einmal mit dem, was er höchstens kostet („Nein“ ist die Vorgabe); ohne Terminal, ohne `--i-pay`, in CI oder im Offline-Modus läuft er nicht (Exit-Code 2). |
| `--rag-variant [fused\|rerank]` | Nur Retrieval: die zu bewertende Suchvariante; wiederholbar, ein Lauf je Variante. `fused` (Standard): die hybride Suche. `rerank`: die hybride Suche plus der Reranker aus `rag_reranker` mit seiner Relevanzschwelle. |
| `--case TEXT` | Nur diese Fall-ID; wiederholbar. |
| `--repeat INTEGER RANGE` | Läufe pro Fall, 1 bis 20 (Standard 1). |
| `--json FILE` | Den vollständigen Bericht (Einrichtung, Bestehensquoten, jede Antwort) als JSON schreiben. |
| `--min-pass FLOAT RANGE` | Exit-Code 1, wenn ein Lauf weniger Fälle besteht als dieser Anteil (0 bis 1, z. B. `0.8`). |
| `--effort [low\|medium\|high]` | Aufwand für diesen Lauf (lokale Modelle: `low` = ohne Nachdenken). Standard: KI-Einstellungen. |
| `--measure` | Mit `--tier`: jede Stufe zuerst [auf diesem Rechner messen](#messung-auf-diesem-rechner) (Tokens pro Sekunde, KL-Divergenz von ihrer Referenz). Ohne `--tier`: Exit-Code 2. |
| `--measure-only` | `--measure` ohne die Fälle; Exit-Code 1, wenn eine Stufe nicht bestand. |
| `--chunks INTEGER RANGE` | `--measure`: Abschnitte des Bewertungstextes, 4 bis 48 (Standard 16). |
| `--ctx [256\|512\|1024]` | `--measure`: Tokens je Abschnitt (Standard 512). |
| `--again` | `--measure`: eine Stufe erneut messen, obwohl ihre Messung auf diesem Rechner noch gilt. |
| `--download-reference` | `--measure`: eine fehlende Referenz ohne Frage laden (sonst fragt die CLI am Terminal; ohne eines stoppt sie mit Exit-Code 2). |

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

#### Messung auf diesem Rechner

`llm-cli ditto eval --measure --tier STUFE` misst eine Stufe von Dittos lokalem Modell vor den Fällen auf diesem
Rechner, mit dem selbst gebauten llama.cpp dieses Rechners: die erzeugten Tokens pro Sekunde (llama-bench) und die
KL-Divergenz ihrer Antworten von ihrer Referenz (llama-perplexity, derselbe Aufruf wie beim Auto-Tuning) auf einem
festen englischen und deutschen Text. Die Referenz ist das 16-Bit-Original der Stufe, wenn es in drei Viertel des
RAM passt, sonst ihr Q8_0; beide sind im Katalog festgelegt. Eine fehlende Referenz wird erst nach deinem Ja (oder
`--download-reference`) geladen und bleibt in Dittos Modellordner. Das Urteil: **bestanden** mit mindestens 8 Tokens
pro Sekunde und einer KLD von höchstens 0,05, **brauchbar** bis 0,10, sonst **nicht bestanden**, gesagt mit den
Zahlen. Es wird je Stufe mit diesem Rechner, der Datei und dem llama.cpp-Build aufbewahrt; eine Stufe, deren Messung
noch gilt, wird nicht erneut gemessen (`--again` misst trotzdem). Die vorübergehende Logits-Datei (etwa 2 GB für
Qwen3.5 bei 16 Abschnitten; der Speicherplatz wird vorher geprüft) wird gelöscht, wenn die Messung endet, auch nach
Strg+C.

Eine 2-Bit-Stufe (`small-q2`, `default-q2`, `large-iq2`) wird Ditto erst angeboten, wenn sie hier bestanden hat: Bis
dahin nennt `ditto models list` sie „candidate to evaluate“ (oder „failed here“), und ein einfaches `ditto runtime
start` lädt sie nicht. Jede Stufe kannst du weiter per Namen starten (`--model small-q2`); dann bekommst du einen
Hinweis.

```bash
# Das 2B-Modell mit 2 Bit hier messen, ohne die Fälle
llm-cli ditto eval --measure-only --tier small-q2

# Beide 2B-Stufen messen (ein Referenzdurchgang für beide), dann die Fälle mit jeder
llm-cli ditto eval --measure --tier small --tier small-q2 --effort low
```

In der GUI: **Ditto > Bewertung...** > **Auf diesem Computer messen** (die Optionen oben als Kästchen, Zahl und Liste;
**Bewertung starten...** fragt, bevor eine Referenz geladen wird), oder **Ditto > Lokale Modelle...** > **Erst
prüfen...**, das die Bewertung mit der Stufe und der Messung gewählt öffnet.

#### Token- und Cache-Messung

`llm-cli ditto eval --kind tokens` lässt Gespräche über mehrere Runden mit Beispieldaten laufen und hält je Runde die
eigenen Eingabe-, Ausgabe-, Cache-Lese- und Cache-Schreib-Tokens des Anbieters fest (aus seiner Usage-Antwort, nie
geschätzt) und je Gespräch den Anteil aus dem Cache; mit Claude hält sie auch fest, warum der Prompt-Cache gegenüber
der vorigen Antwort nicht getroffen hat (Anthropics Cache-Diagnose). Zwei Fälle, die heute scheitern, sind mit
`expected_fail` und dem Grund markiert, aus dem sie scheitern (`expected_fail_reason`): Dieses Scheitern wird nicht
gezählt, ein Scheitern aus einem anderen Grund zählt wie jedes andere, und ein Bestehen wird als `UNEXPECTED PASS`
ausgegeben.
Die zusätzliche Tabelle heißt **Tokens**.

```bash
# Die Token-Fälle gegen den eingestellten Anbieter, mit dem Bericht
llm-cli ditto eval --kind tokens --json tokens.json

# Dasselbe auf dem lokalen 2B-Modell, 6 Minuten zwischen den Runden (ein 5-Minuten-Cache ist dann abgelaufen)
llm-cli ditto eval --kind tokens --tier small --pause-minutes 6

# Der bezahlte OpenAI-Test (12-Stunden-Cache-Lebensdauer, OpenAIs eigene Token-Zählung), mit deinem eigenen Key
llm-cli ditto eval --kind openai_paid --provider openai --i-pay
```

Der bezahlte Test fragt vor dem Start noch einmal („Nein“ ist die Vorgabe) und nennt, was er höchstens kostet. Er
schreibt `openai_cache_check.json` nach `evals/` in deinem Zustandsordner (nur Wahrheitswerte und Token-Zahlen;
eine Prüfung, die dieser Lauf nicht gemessen hat, behält ihr früheres Ergebnis) und
gibt aus, wohin. In der GUI: die Art in **Ditto > Bewertung...** anhaken, die Pause ist **Pause zwischen den
Runden**, und **Bewertung starten...** stellt die Frage des bezahlten Tests mit seinen Kosten. Einzelheiten:
[Features_DE.md](Features_DE.md#token--und-cache-messung).

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

Listet die alten Builds von Dittos Server mit je einer Nummer: ältere selbst gebaute Builds und
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
Hinweise („recommended“, „chosen“, „on request“; für eine 2-Bit-Stufe „candidate to evaluate“, „passed here“,
„usable here“, „failed here“ oder „not evaluated here“, aus der [Messung auf diesem
Rechner](#messung-auf-diesem-rechner)). Die Zeile unter der Tabelle ist die Empfehlung oder „No local model is
recommended yet: ...“ mit dem Befehl, der den Kandidaten prüft.

In der GUI: **Ditto > Lokale Modelle...** (dieselbe Liste, mit der Empfehlung für diesen Computer und dem Grund).

#### ditto models download

```
llm-cli ditto models download [STUFE] [-y]
```

Lädt ein Modell in seiner festgelegten Hugging-Face-Revision herunter und prüft Größe und sha256. Ohne `STUFE` das
Modell, das ein einfaches `ditto runtime start` lädt: die früher mit `runtime start --model` gewählte Stufe („Chosen
earlier with 'runtime start --model': ...“), sonst die empfohlene Stufe für diesen Rechner („Recommended: ...“; ein 2-Bit-Kandidat wird zum
Prüfen geladen und sagt das). Unterbrochene Downloads werden fortgesetzt. Im Offline-Modus lehnt er in klaren Worten ab und nennt die
Ditto-Modelle, die schon auf diesem Computer sind, außer die Download-Adresse liegt in deinem eigenen Netz (ein
lokaler Modell-Spiegel).

| Argument / Option | Bedeutung |
|---|---|
| `STUFE` | `small`, `small-q2`, `default`, `default-q2`, `large`, `large-iq2` oder `granite`. |
| `-y, --yes` | Vor dem Download nicht fragen. |

```bash
llm-cli ditto models list
llm-cli ditto models download small-q2
```

In der GUI: **Ditto > Lokale Modelle...** > **Empfohlenes Modell herunterladen...** (die Empfehlung, die das Fenster
zeigt; der Knopf ist an, sobald die Liste gelesen ist; **Gewähltes Modell herunterladen...**, wenn früher eine Stufe für
den lokalen Server gewählt wurde), oder eine Stufe auswählen und **Ausgewähltes
herunterladen...**; die Frage zeigt Repository, Datei, Lizenz, Größe und Ordner („Nein“ ist die
Vorgabe), der Download läuft im Hintergrund mit Fortschritt und **Abbrechen** (was schon heruntergeladen ist, bleibt
für die Fortsetzung).

### ditto cache

#### ditto cache show

```
llm-cli ditto cache [--json]
llm-cli ditto cache show [--json]
```

Das Speicherbudget von Dittos lokalem Server, aus der gemessenen Hardware dieses Computers: für den Prompt-Cache im
RAM (`local_cache_ram_mib`, `--cache-ram`), die Zeit, die das Modell ohne Anfragen geladen bleibt
(`local_cache_keep_minutes`), und den KV-Cache-Typ (`local_kv_cache`) zeigt es deinen gespeicherten Wert (auto oder
einen Wert), wofür auto hier steht, was der nächste Start nimmt, die weiche und die harte Grenze und warum; danach, was
der laufende Server bekommen hat. `--json` gibt dasselbe als JSON aus. Hat der llama.cpp-Build keine Flash Attention,
die ein quantisierter KV-Cache braucht, wird ein automatisches q8_0 oder q4_0 beim Start zu f16, und `ditto runtime
start` und `ditto cache` sagen es in einer gelben Zeile; ein q8_0 oder q4_0, das du selbst gesetzt hast, bleibt, und
der Start scheitert mit einem Text, der `local_kv_cache` und f16 nennt.

#### ditto cache set

```
llm-cli ditto cache set NAME WERT [--yes-over-cap]
```

| Argument / Option | Bedeutung |
|---|---|
| `NAME` | `local_cache_ram_mib` (oder `ram`), `local_cache_keep_minutes` (`keep`), `local_kv_cache` (`kv`), `local_cache_over_cap_ok` (`over-cap-ok`). |
| `WERT` | `auto` oder eine ganze Zahl (MiB, Minuten; `0` Minuten hält das Modell geladen); für den KV-Cache `auto`, `f16`, `q8_0` oder `q4_0`; für `over-cap-ok` `true` oder `false`. |
| `--yes-over-cap` | Einen Wert über der weichen Grenze dieses Computers ohne die Frage setzen. |

Ein Wert über der harten Grenze (mehr Speicher, als dieser Computer neben dem Modell hat) wird mit Exit-Code 2
abgelehnt, ebenso ein Wert, der keine Zahl ist (es gibt kein `-1` für „ohne Grenze“). Über der weichen Grenze bekommst
du eine Warnung und die Frage „Set it anyway?“ („Nein“ ist die Vorgabe), außer du hast `--yes-over-cap` angegeben oder
`local_cache_over_cap_ok` gesetzt. Gespeichert wird nur die genannte Einstellung; ein laufender Server übernimmt sie bei
seinem nächsten Start (er startet einmal neu). Ein KI-Client darf über MCP nur `local_cache_keep_minutes` kürzen und
nie über die weiche Grenze; `local_cache_ram_mib`, `local_kv_cache`, `local_cache_over_cap_ok` und 0 Minuten setzt nur
du (eine Zahl von einer KI würde einen Wert einfrieren, der zu diesem Computer bald nicht mehr passt).

```bash
llm-cli ditto cache
llm-cli ditto cache set kv q8_0
llm-cli ditto cache set ram auto
```

In der GUI: die Gruppe **Prompt-Cache** in **Ditto > Lokale Modelle...** und in den KI-Einstellungen (Felder, die Wahl
des KV-Caches und **Übernehmen**; über der weichen Grenze dieselbe Frage).

---

## tools

Die Tools des Frameworks sind das, was Ditto und MCP-Clients verwenden: 16 nur lesende Tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_mojo_oracle`, `search_knowledge`, `list_knowledge_sources`, `read_knowledge_source`, `search_hf_models`,
`get_hf_model_info`, `list_local_files`, `get_status`, `get_job_status`, `list_devices`), 8, die auf diesem Rechner
etwas ändern (`start_build`, `cancel_build`, `resolve_healing`, `cancel_job`, `propose_healing_fix`,
`download_hf_file`, `create_deploy_package`, `run_mojo_miracle`), und `deploy_to_device`, das ein Gerät erreicht, dem
du vertraust (Stufe `external`: immer mit Frage). `run_doctor`, der Name von `run_mojo_oracle` in 3.0.0, bleibt
aufrufbar, wird aber nicht aufgelistet. `gui` liest und füllt das offene
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

## agents

Externe ACP-Agenten, die du auswählst, mit den Werkzeugen dieses Frameworks ([Features](Features_DE.md#acp-agenten-die-du-auswählst)).
Jeder Befehl, der etwas ändert, ist allein deiner: Eine KI kann nie einen Agenten hinzufügen, anmelden oder starten.

| Befehl | Was er tut |
|---|---|
| `agents list [--json]` | Deine Agenten: Name, Verteilung, Version, Herkunft, ob er das Internet braucht, die Variablen seiner Schlüssel |
| `agents search [TEXT] [--refresh] [--json]` | Die ACP-Registry durchsuchen, wie zuletzt abgerufen; `--refresh` ruft sie jetzt ab (HTTPS, im Offline-Modus abgelehnt) |
| `agents add ID [--kind npx\|uvx\|binary]` | Einen Registry-Agenten hinzufügen, nachdem Name, Beschreibung, Verteilung, Lizenz, der genaue Befehl, der Download und seine Prüfsumme gezeigt sind und du Ja sagst („Nein“ ist vorgewählt); npx und uvx nur mit genauer Version, ein Binary nur mit der Prüfsumme der Registry |
| `agents add --command PFAD --name NAME [--arg A]... [--local] [--key VAR]...` | Dein eigenes Programm hinzufügen (`--local`: es geht ohne Internet und darf im Offline-Modus starten; `--key`: eine Variable für seinen Schlüssel) |
| `agents login NAME [--method ID]` | Mit der eigenen Anmeldung des Agenten anmelden: seine Browser-Anmeldung, sein Anmeldebefehl in diesem Terminal oder verdeckt abgefragte Schlüssel im Tresor für Zugangsdaten |
| `agents login NAME --key VAR` | Einen Schlüssel für den Agenten speichern (verdeckt, zweimal); er geht nur an diesen Agenten, als `VAR` |
| `agents remove NAME...` | Agenten entfernen nach der vollständigen Liste ihrer Ordner und Einträge („Nein“ ist vorgewählt); ihre Schlüssel verlassen den Tresor |
| `agents run NAME` | Mit dem Agenten in diesem Terminal chatten: sein Text und seine Gedanken, wie sie kommen, Werkzeugaufrufe mit Stand, der Plan; seine Berechtigungsfragen werden hier gestellt (einmal erlauben oder ablehnen, „Nein“ vorgewählt); Strg+C beendet seinen Zug, eine leere Zeile beendet den Chat |

Der Agent läuft in seinem eigenen Ordner im Framework-Home mit einer sauberen Umgebung (das Nötigste des Systems und
seine eigenen Schlüssel) und bekommt den MCP-Server des Frameworks (`llm-cli mcp serve`) als seine einzigen Werkzeuge.
Schritt für Schritt, mit den Meldungen, die dir begegnen können: [Editoren und Agenten:
Einrichtungsanleitung](Editors_and_Agents_DE.md#c-agenten-im-eigenen-fenster-des-frameworks).

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
die nur dein Konto nutzen kann: Der Client liest und füllt sie wie Ditto, und jedes Feld, das er
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

Zeigt, wie du Claude Code (ein Befehl `claude mcp add ...`), Claude Desktop (JSON für dessen Konfigurationsdatei),
VS Code (JSON für `.vscode/mcp.json`) und Zed (JSON für `context_servers` in seiner `settings.json`; Zed kann den
Server an die ACP-Agenten weitergeben, die es startet, siehe die [Einrichtungsanleitung](Editors_and_Agents_DE.md))
verbindest, jeweils mit dem Python und dem Ordner dieser Installation. Ruf es
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

In der GUI: **Security > Verbindungen zu KI-Apps (MCP)...** zeigt die vier Einträge, jeden mit einer Schaltfläche
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

## acp

Ditto als Agent von Editoren, die das Agent Client Protocol (ACP) sprechen, etwa Zed, JetBrains-IDEs, Android Studio,
Neovim oder Emacs. Der Editor startet `llm-cli acp` selbst und spricht über stdin und stdout mit Ditto (Version 1). Das
ist ein zusätzlicher Weg: Dittos Chat in der GUI und mit `ditto ask` bleibt, wie er ist. Experimentell wie Ditto; der
Hinweis ist die erste Nachricht jeder Sitzung im Editor.

```
llm-cli acp [--login]
llm-cli acp config [--json]
llm-cli acp servers list [--json]
llm-cli acp servers enable|disable SERVER
```

| Befehl / Option | Bedeutung |
|---|---|
| `acp` | Den Agent betreiben (das tut der Editor; stdout trägt nur das Protokoll, Logs gehen nach stderr). Ditto antwortet mit dem KI-Anbieter deiner KI-Einstellungen; jeder Tool-Aufruf läuft innerhalb deiner Tool-Berechtigungen und steht mit dem Kanal `acp` im Audit-Log. |
| `acp --login` | Ditto anmelden: fragt nach dem Anbieter und führt dafür `ditto login` aus. Der Editor führt das aus, wenn er „Ditto anmelden“ anbietet (gezeigt, solange Ditto keinen Anbieter eingerichtet hat). Braucht ein Terminal. |
| `acp config` | Die Befehlszeile und die `agent_servers`-Einträge für den Editor ausgeben (`settings.json` von Zed, mit `"type": "custom"`; `acp.json` einer JetBrains-IDE), mit dem Python und dem Ordner dieser Installation und `-P`, wie `mcp config`. `--json`: nur der JetBrains-Eintrag. |
| `acp servers list` | Die MCP-Server, die Editoren an Ditto übergeben haben: Name, Transport, was laufen würde (Geheimnisse geschwärzt, nur die Namen der Umgebungsvariablen), der Editor (wie er sich selbst nennt), zuletzt gesehen, zugestimmt oder nicht. `--json` für Skripte. |
| `acp servers enable` / `disable` | Deine Zustimmung für einen Eintrag (per ID oder einem Namen, den nur ein Eintrag hat). Diese Version verbindet sich so oder so mit keinem MCP-Server eines Editors: Ihre Tools würden an den Berechtigungen und am Audit-Log des Frameworks vorbeilaufen. |

Was der Editor bekommt:

- **Berechtigungsanfragen** bieten nur „einmal erlauben“ und „ablehnen“, mit der eigenen Frage des Frameworks; „nicht
  mehr fragen“ gibt es nur im Freigabefenster des Frameworks. Eine Löschung oder ein Tool, das auf „im Framework
  bestätigen“ steht, wird nie im Editor gefragt, und eine Frage, die der Editor nicht beantwortet (ein Fehler, keine
  Antwort innerhalb von 10 Minuten), wartet als ausstehende Freigabe im Framework (`tools approvals`).
- **Ordner**: Die Projektordner des Editors erreichen das Modell nur als Labels (`project`, `project-2`, ...), jeder
  Pfad in einem Prompt als `label:relativer/pfad` oder als sein Dateiname.
- **Fortschritt**: Jeder Tool-Aufruf zeigt sich im Editor, während er läuft; ein Build, den Ditto gestartet hat, zeigt
  seinen Fortschritt und die vier Build-Stufen. Abbrechen im Editor beendet die Runde; nichts weiter wird gestartet.
- **Anbieter und Modell** lassen sich im Editor je Sitzung wählen; die gespeicherten KI-Einstellungen bleiben, wie sie
  sind.
- **Slash-Befehle**: `/status`, `/targets`, `/builds`, `/mojo`.
- Die Datei- und Terminal-Dienste des Editors werden nie benutzt.

```bash
llm-cli acp config
llm-cli acp servers list
llm-cli acp servers enable 0123456789abcdef
```

In der GUI: **Datei > Einstellungen...**, Tab **Ditto in Editoren**: der Befehl und die zwei Einträge, jeweils mit einem
Knopf **Kopieren**, und die vermerkten MCP-Server der Editoren mit je einem Zustimmungs-Häkchen. Die Anmeldung ist
**Security > Anmeldeübersicht...**. Schritt für Schritt für Zed, JetBrains-IDEs und Android Studio: [Editoren und
Agenten: Einrichtungsanleitung](Editors_and_Agents_DE.md#b-ditto-als-agent-des-editors).

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
                      [--description TEXT] [--base-image IMAGE] [--packages TEXT] [--cpu-flags TEXT]
                      [--cmake-flags TEXT] [--setup-commands DATEI] [--quantization-logic DATEI]
                      [--knowledge-root URL]... [--formats DATEI] [--edit]
```

Legt ein neues Target mit den Werten des Modul-Assistenten an, als Optionen; was fehlt, wird gefragt: Name,
Architektur und SDK, dann Docker-Basis-Image (Standard `debian:bookworm-slim`), Beschreibung, Systempakete (Standard
`build-essential, cmake, git`) und CPU-Flags. Mit `--probe` werden Name, Architektur und SDK wie im Assistenten aus
der Probe vorgeschlagen und nicht gefragt. Jeder Wert wird geprüft, bevor Dateien geschrieben werden, der fertige
Ordner muss die Target-Prüfungen bestehen
([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen)), und ein vorhandenes
Target wird nie überschrieben. Das Target ist als ungetestet markiert, bis ein Lauf auf seinem Board es belegt.

| Option | Bedeutung |
|---|---|
| `--name TEXT` | Name des neuen Targets. Mit `--probe`: Standard ist der Board-Name der Probe, sonst ihr SoC oder ihre CPU, eindeutig unter den Targets. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU-Architektur. Mit `--probe`: Standard ist die der Probe. |
| `--sdk TEXT` | Besonderes SDK, z. B. `CUDA`, `RKNN` (Standard `None`; mit `--probe`: aus den Beschleunigern der Probe). |
| `--probe DATEI` | Hardware-Probe des Targets (das Profil, das [`scan --device`](#scan) schreibt). Sie wird im Probe-Speicher abgelegt wie mit [`scan --import`](#scan) und im Assistenten (der Befehl nennt ihre Probe-ID), ihr SoC wird das Board des Targets (so findet die Probe das Target), und der Block `runtime:` der neuen `target.yml` ist der Plan des Build-Rezepts für die llama.cpp-Laufzeit des Boards daraus; eine Datei, die kein Probe-Profil ist, wird abgelehnt, bevor etwas geschrieben wird. |
| `--description TEXT` | Beschreibung des Targets. |
| `--base-image IMAGE` | Basis-Image des Build-Images. |
| `--packages TEXT` | Systempakete, durch Kommas oder Leerzeichen getrennt. |
| `--cpu-flags TEXT` | GCC-Flags für den Cross-Build des Board-Pakets (die Config-Stufe); der Plan aus der Probe des Boards kommt nach ihnen und gewinnt, wo er entscheidet. |
| `--cmake-flags TEXT` | CMake-Optionen von llama.cpp für denselben Cross-Build, nach derselben Regel. |
| `--setup-commands DATEI` | Befehle, die die Source-Stufe einmal vor dem Konvertieren ausführt (als Build-Benutzer ohne Rechte, ohne Netz). |
| `--quantization-logic DATEI` | Die bash-`case`-Zweige der Convert-Stufe (`modules/convert_module.sh`). |
| `--knowledge-root URL` | Die Seiten der Wissensbasis anhängen, die von dieser Adresse gecrawlt wurden (Datenschutz-Bereinigung angewendet); wiederholbar. Nur diese Seiten werden angehängt. |
| `--formats DATEI` | Die Ausgabeformate des Moduls, die Variablen der allgemeinen Vorlage ([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#38-die-allgemeine-vorlage-formate-als-variablen)): eine YAML- oder JSON-Liste, deren Einträge der Name einer Vorgabe (`gguf`, `rknn`, `rkllm`) oder die Werte eines Formats sind (sein Konverter des Herstellers mit seinen Argumenten, die Eingabeformate, die Endung der Ausgabe, die Quantisierungstypen, die Runtime-Bibliotheken mit Version und der Angabe, ob sie weitergegeben werden dürfen, die Pfade auf dem Board, seine `sdk:`-Anforderungen, die Herstellerdateien, die nicht weitergegeben werden dürfen). Jeder Wert wird wie die übrigen Felder geprüft. Ohne die Option baut das Modul GGUF. |
| `--edit` | Der Schritt „alle Dateien bearbeiten“ des Assistenten: Das Modul wird in einem privaten Arbeitsordner erzeugt, seine Stage-Module, die `target.yml` und das Dockerfile öffnen nacheinander in `$EDITOR`, der Diff wird gezeigt und die Prüfungen laufen erneut ([`module edit`](#module-edit)); bei einer gescheiterten Prüfung lassen sich die Dateien noch einmal öffnen, und das Modul kommt erst in den Targets-Ordner, wenn jede Prüfung besteht und du Ja sagst. Sonst wird nichts geschrieben. |

Das Target wird in den eingestellten Targets-Ordner geschrieben (Einstellung `targets_dir`; ein relativer Pfad
gilt relativ zur Installation, nicht zum aktuellen Ordner). Seine `target.yml` hat einen Block `capabilities:`
(Unterstützungsstufe `community`) mit dem Board der Probe (ohne Probe dem Modulnamen) als einzigem Board und GGUF als
einzigem Format; trag dort deine echten Boards und Formate ein. Der Build sind die vier Stufenmodule
(`source_module.sh`, `config_module.sh`, `convert_module.sh`, `target_module.sh`), die der Stage-Runner des Frameworks
(`orchestrator/runtime_layer/runner.sh`) der Reihe nach ausführt; ein `modules/build.sh` eines Targets wird nicht
genutzt. Die nächsten Schritte des Befehls nennen die Stufen convert und target, in denen ein neues Target seine
Umwandlung und Quantisierung ergänzt ([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#6-die-stufen-des-builds)),
und wie man es teilt ([`zoo export`](#zoo-export)).

In der GUI: **Tools > Create Hardware Module...**: Name, Architektur, SDK und **Beschreibung** auf der Hardware-Seite,
Basis-Image und Pakete auf der Docker-Seite, CPU- und CMake-Flags, Quantisierungslogik und Einrichtungsbefehle auf der
Flags-Seite, das Anhängen des Wissens und der Community-Export auf der letzten Seite; der Standard-Import auf der
ersten Seite liest eine Probe-Datei (`--probe`). Die letzte Seite ist der Schritt „alle Dateien bearbeiten“
(`--edit`): Dort wird das Modul erzeugt, noch nicht gespeichert, und seine Dateien mit ihrem Diff gelistet; **Finish**
führt die Prüfungen erneut aus, lehnt mit den Gründen ab, solange eine scheitert, und speichert dann das Modul. Nach
**Finish** wird das neue Target wie mit `targets validate` geprüft.

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto schlägt ein Target-Modul aus einer Hardware-Probe-Datei vor (`target_hardware_config.txt`, siehe
[scan](#scan)). Der ganze Vorschlag wird als JSON ausgegeben, versteckte Zeichen und Steuerzeichen als
`\u`-Escapes; erst nach deiner Bestätigung wird das Modul im
eingestellten Targets-Ordner angelegt (und vom Modulgenerator erneut geprüft). Ditto nutzt die gespeicherte
Anmeldung des Anbieters ([`llm-cli ditto login`](#ditto-login)); der Befehl fragt selbst nie nach einem Schlüssel.
`module generate-ai` hängt keine Seiten aus der Wissensbasis an; `module create --knowledge-root` und die letzte
Seite des GUI-Assistenten tun es. Ist die Probe-Datei ein Probe-Profil, wird der Block `runtime:` des Moduls daraus
geplant; die CPU- und CMake-Flags des Vorschlags gehen in die Config-Stufe für den Cross-Build des Board-Pakets, wo der
Plan aus der Probe des Boards gewinnt. Ditto ist
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

### module edit

```
llm-cli module edit TARGET [--file DATEI]... [--apply DIFF_DATEI] [--variables DATEI]
```

Der Schritt „alle Dateien bearbeiten“ für ein Target des Targets-Ordners (nach ID oder Ordnername), der Ort, an dem
ein Modul von Hand repariert wird. Er listet die vier Stage-Module, die Slot-Module, die die `target.yml` in ihrem
Block `stages:` nennt, die `target.yml` und jedes Dockerfile; nie den Stage-Runner des Frameworks, keine Runner-Datei,
die ein Target mitbringt (`modules/build.sh`, `stage_user.sh`), keine Hilfsskripte und nicht das gecrawlte Wissen.
Jede Datei öffnet in `$EDITOR`; dann wird der Diff gegenüber den gespeicherten Dateien gezeigt, und die Prüfungen
eines Target-Ordners laufen erneut auf einer Kopie mit deinen Änderungen
([`validate_target_folder`](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen): die Sperrmuster für
Shell-Code, die Platzhalter der Vorlage, die gepinnten Images, die Slots, `bash -n`). Solange eine Prüfung scheitert,
wird nichts gespeichert, mit den Gründen; eine Änderung darf die Markierungen `untested` und `provenance` der
`target.yml` nicht entfernen. Nur dein Ja schreibt die Dateien (keine Antwort, kein Terminal: nichts wird geschrieben).

| Option | Bedeutung |
|---|---|
| `--file DATEI` | Nur diese Datei des Schritts öffnen (z. B. `modules/config_module.sh`, `target.yml`, `Dockerfile`); wiederholbar. Standard: jede Datei, außer mit `--apply`. |
| `--apply DIFF_DATEI` | Zuerst einen Unified Diff anwenden (den Vorschlag einer KI oder einen von Hand geschriebenen), ganz oder gar nicht; er wird gezeigt und geprüft wie jede Änderung und nur auf dein Ja gespeichert. Ein Diff, der eine Datei anlegt, löscht oder umbenennt oder den Runner berührt, wird abgelehnt. |
| `--variables DATEI` | Für ein Modul aus der allgemeinen Vorlage: zuerst seine Variablen ändern (seine Ausgabeformate, YAML oder JSON); die Dateien, die sie enthalten (`template_variables` und Capabilities in `target.yml`, der Block in `config_module.sh`), werden aus der Vorlage neu erzeugt, als ihr Diff gezeigt und wie jede Änderung geprüft. Ein Format, das eine Datei bräuchte, die das Modul nicht hat, wird abgelehnt (dann das Modul neu erzeugen). Ohne `--file`, `--apply` oder `--variables` öffnen sich zuerst die Variablen in `$EDITOR`, dann die Dateien. |

```bash
llm-cli module edit my_board --file modules/config_module.sh
llm-cli module edit my_board --apply proposal.diff
```

In der GUI: **Build > Targets...**, das Target wählen, **Dateien bearbeiten...**: dieselben Dateien mit ihrem Diff,
**Prüfungen ausführen**, **Diff anwenden...** (`--apply`) und **Speichern**, das mit den Gründen abgelehnt wird,
solange eine Prüfung scheitert. Eine KI-App oder Ditto kann dort über die GUI-Werkzeuge eine Änderung vorschlagen, als
Diff im Feld „Vorschlag“: Er wird im Fenster angewendet, markiert und mit einem Rückgängig gelistet, und nur dein
**Speichern** schreibt ihn.

### module repair

```
llm-cli module repair TARGET [--propose] [--apply DIFF_DATEI] [--variables DATEI] [--file DATEI]... [--retry]
```

Repariert ein erzeugtes Modul nach einem gescheiterten Build. Ein Build eines Moduls, das nicht mitgeliefert ist,
markiert es: **getestet**, wenn seine Target-Stufe bestanden hat (das Board-Paket erstellt wurde), **Nacharbeit
nötig**, wenn er im Teil des Moduls gescheitert ist (beim Bau seines Images aus seinem Dockerfile oder in seinen
Stage-Modulen) und die Musterbibliothek das Log nicht auf diesen Computer zurückführt (volle Platte, Berechtigung der
Container-Engine, Netz, Speicher). Der Zustand liegt in `.lcf-module-test.json` neben der `target.yml`, nie in der
`target.yml` selbst, sodass ein festgehaltenes Ergebnis nichts ändert, was ein Build, ein Rezept oder die Prüfung eines
Pakets vergleicht; er hält das Log des neuesten gescheiterten Builds (seine letzten 400 Zeilen, Geheimnisse
geschwärzt) und den Verlauf der Ergebnisse und Reparaturen. `metadata.untested` in der `target.yml` bleibt bis zu einem
Lauf auf dem Board.

Der Befehl zeigt den Zustand, den gescheiterten Build mit seinem Fehler und dem Ende seines Logs und einen
Reparaturvorschlag, der wartet (von Ditto, der Musterbibliothek oder einer KI-App). Die Reparatur läuft durch denselben
Schritt wie [`module edit`](#module-edit): Der wartende Vorschlag (oder dein Diff) wird auf die Dateien angewendet, die
Dateien öffnen in `$EDITOR`, der Diff wird gezeigt und die Prüfungen laufen erneut, und nur dein Ja schreibt die
Dateien. Das Modul bleibt danach ungetestet, bis ein Build besteht, und der Befehl gibt die Zeile `llm-cli build start`
aus, die die Einstellungen des gescheiterten Builds erneut baut.

| Option | Bedeutung |
|---|---|
| `--propose` | Zuerst die Analyse der Selbstheilung um eine Reparatur aus dem Fehlerlog bitten: die Musterbibliothek, dann Ditto (das Log bereinigt, geschwärzt und als nicht vertrauenswürdiger Text eingepackt; die Dateien des Moduls als nicht vertrauenswürdiger Text). Eine bekannte Ursache gibt einen Schritt für dich als Text (nichts wird ausgeführt); Dittos Antwort ist ein Unified Diff der Dateien des Moduls, abgelehnt, wenn er nicht auf sie passt oder einen gefährlichen Befehl nennt. Braucht das Framework (und für Ditto einen KI-Anbieter). |
| `--apply DIFF_DATEI` | Zuerst einen Unified Diff statt des wartenden Vorschlags anwenden, wie bei `module edit --apply`. |
| `--variables DATEI` | Stattdessen durch geänderte Variablen der Vorlage reparieren, wie bei `module edit --variables`. Eine KI-App schlägt dasselbe mit `propose_healing_fix` und seinem Feld `module_variables` vor (die geänderten Formate als YAML), das zum wartenden Diff der Dateien wird. |
| `--file DATEI` | Diese Datei des Schritts in `$EDITOR` öffnen; wiederholbar. Ohne `--propose`, `--apply` oder einen wartenden Vorschlag öffnet jede Datei. |
| `--retry` | Nachdem die Reparatur gespeichert ist, den gescheiterten Build mit seinen Einstellungen (Modell, Format, Quantisierung, Board) erneut starten, nach einem Ja. |

```bash
llm-cli module repair my_board --propose
llm-cli module repair my_board --apply fix.diff --retry
```

In der GUI: **Build > Targets...** zeigt die Spalte **Build-Test**; **Dateien bearbeiten...** an einem Modul mit
Nacharbeit zeigt Fehler und Log des gescheiterten Builds über den Dateien, wendet einen wartenden Vorschlag an
(markiert im Diff, **Vorschlag zurücknehmen**), bietet **Reparatur aus dem Log vorschlagen** (`--propose`) und fragt
nach dem **Speichern**, ob der gescheiterte Build im Builder erneut starten soll (`--retry`). Der Builder zeigt den
Zustand als Tooltip jedes Targets, schreibt ihn am Ende eines Builds ins Build-Log und bietet nach einem gescheiterten
Build eines Moduls an, seine Dateien mit dem Fehler zu öffnen. Eine KI-App liest den Zustand in `list_targets` und
`get_target_capabilities` (`build_test`; mit `module_files` auch die Dateien des Moduls und das Log des Fehlschlags)
und schlägt eine Reparatur mit `propose_healing_fix` und seinem Feld `module_diff` vor; der Vorschlag wartet hier auf
dich, und keine KI speichert ihn.

---

## scan

```
llm-cli scan [--output TEXT] [--json] [--script]
llm-cli scan --device HOST --user TEXT [--port N] [--password [WERT] | --password-stdin]
             [--trust-fingerprint TEXT] [--label TEXT]
             [--output TEXT] [--json]
llm-cli scan --import DATEI [--label TEXT]
llm-cli scan --kit ORDNER [--no-pair]
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
- **Was sie ausgibt**: eine kurze Zusammenfassung des Profils (System, CPU, GPU, NPU, Hailo) und seit Probe 3.3.0
  eine Zeile `Access:`: der SSH-Server (OpenSSH oder Dropbear) und seine Version, sudo (ohne Passwort, fragt nach
  einem, fehlt, oder die Probe lief als root), die Version von ssh-keygen und ob es Signaturen prüfen kann, das
  Init-System, die Paketverwaltung und die installierten Pakete, die Dropbear bereitstellen (ohne root bei der
  Paketverwaltung erfragt, etwa `dropbear, dropbear-bin, dropbear-run`). Der Scan im Builder-Tab zeigt dieselbe
  Zeile; eine ältere Probe-Datei hat keine.
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
| `--import DATEI` | Die Probe-Datei eines Boards im Probe-Speicher ablegen und ihre Probe-ID zeigen (nicht zusammen mit `--device`). Die Kopplung eines Probe-Kits in der Datei wird abgeschlossen: Der Host-Key des Boards wird gepinnt und die Probe unter der ID des Boards gespeichert; eine Datei eines anderen Frameworks, eine abgelaufene oder eine benutzte wird mit Grund abgelehnt, die Hardware-Daten werden trotzdem gespeichert. |
| `--kit ORDNER` | Das Probe-Kit für ein Board in ORDNER schreiben: die drei Probes, eine README und, außer mit `--no-pair`, den Ordner `llmcf-pairing` (der öffentliche Schlüssel des Frameworks, seine Signer-Datei und eine Einmal-Zahl, 24 Stunden für ein Board gültig). Starte die Probe dort einmal mit sudo, um das Board zu koppeln; ohne sudo liest sie nur die Hardware. Ein Ordner, in dem schon eine README.txt oder Probe von dir liegt (nicht von einem früheren Kit), wird mit ihren Namen abgelehnt; nichts wird überschrieben. |
| `--no-pair` | Mit `--kit`: keine Kopplung (das Kit liest nur die Hardware). |

Bricht das Netz des Boards während `--device` ab (zum Beispiel bei einem Board nur mit WLAN), wartet der Scan bis zu
300 s auf das Board und sagt das einmal (`Warte auf das Board unter ... (Ctrl+C)`); Strg+C beendet den Befehl
sofort, und kein Profil wird gespeichert. Ein Board, das nicht wieder antwortet, beendet den Scan mit dem, was das
bedeutet, und dem nächsten Schritt, wie `deploy run` es sagt.

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
llm-cli scan --kit /media/stick/probe-kit             # das Kit für ein Board, Kopplung an
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
                              [--kv-cache auto|f16|q8_0|q4_0] [--clock-from-pc yes|no]
llm-cli device clock GERÄT [--set [--yes]]
llm-cli device package show GERÄT [--bundle BÜNDEL]... [--read]
llm-cli device package install GERÄT [PAKET]... [--bundle BÜNDEL] [--yes]
llm-cli device package preload write GERÄT [PAKET]... [--bundle BÜNDEL] --out ORDNER
                                     [--distribution debian|ubuntu|raspbian] [--release CODENAME] [--arch ARCH]
                                     [--archive raspberrypi|armbian]...
llm-cli device package preload take GERÄT DATEI [--bundle BÜNDEL] [--on-board]
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
| `--clock-from-pc yes\|no` | Deine dauerhafte Antwort für die Uhr des Boards: `yes` lässt llmcf sie von der Zeit dieses Rechners stellen, wann immer sie bei einem Kontakt (ein Deploy, eine Bundle-Installation, eine Prüfung) um mehr als eine Minute abweicht, als root über die Uhr-Aktion von llmcf-install (sie braucht die Root-Bereitschaft des Boards, `bundle prepare-root`); `no` belässt es bei einer Warnung, die die Abweichung nennt. Wird mit der Zeit der Antwort ins Audit-Log geschrieben. Einmal gefragt, nachdem du ein Board gepairt hast (`deploy devices pair-code`, ein Pairing mit dem Board-Passwort); siehe [device clock](#device-clock). |

Exit-Code 1, wenn das Board kein systemd hat oder die Bestätigung fehlt (dann wird nichts geändert), 2 bei einem
falschen Wert.

Beschädigte Deploy-Einstellungen (`device list` zeigt "damaged; set them again") werden nie als die Vorgaben gelesen.
Mit einer Option nennt der Befehl die Datei mit vollem Pfad und Größe und fragt, ob sie neu festgelegt werden sollen,
ausgehend von den Vorgaben ("Nein" ist die Vorgabe; ohne Terminal wird nichts ersetzt, Exit-Code 1); die Datei wird
erst überschrieben, wenn die neuen Einstellungen gespeichert werden.

In der GUI: **Boards > Boards und Geräte...** **> Einstellungen...** (die aktuellen Einstellungen vollständig; Installation, Benutzer, Port,
Modell und KV-Cache; dieselbe sudo-Frage mit "Nein" als Vorgabe, zuerst ins Audit-Log geschrieben; bei beschädigten
Einstellungen dieselbe Frage, bevor das Formular mit den Vorgaben öffnet). Das Feld "Uhr von diesem Rechner" nimmt
die Antwort für die Uhr des Boards.

### device clock

Ein Board ohne batteriegepufferte Uhr, das offline ist, kann die Zeit nicht kennen; seine Logs und Dateien trügen ein
falsches Datum (llmcf selbst prüft nur Hashes und Signaturen ohne Gültigkeitsdatum, die keine Zeit brauchen). Bei
jedem Kontakt mit einem Board (ein Deploy, eine Bundle-Installation, eine Prüfung, eine Probe) liest llmcf die Zeit
des Boards über den Login, den es schon hat, und vergleicht sie mit der dieses Rechners. Weicht sie um mehr als eine
Minute ab, stellt es sie mit deinem dauerhaften Ja für dieses Board (`device settings --clock-from-pc yes`), sonst
gibt es eine Warnung aus, die die Abweichung nennt.

`device clock GERÄT` zeigt den Stand des letzten Kontakts: die Abweichung, ob das Board eine eigene funktionierende
Uhr hat (eine RTC, die eine Zeit ab 2025 hält), was seine Zeit über einen Neustart behält (fake-hwclock oder die
Uhrdatei von systemd-timesyncd), wann sie zuletzt gelesen und gestellt wurde, und deine Antwort. `--set` liest die Uhr
jetzt und stellt sie nach deinem Ja einmal auf die Zeit dieses Rechners (`--yes`: ohne Frage); ohne Terminal und ohne
`--yes` wird nichts gestellt (Exit-Code 1).

Die Zeit wird nur als root über die Uhr-Aktion von llmcf-install gestellt (`llmcf-install clock
YYYY-MM-DDTHH:MM:SSZ`, eine UTC-Zeit von 2025 bis 2099, auf dem Board erneut geprüft, nie eine Shell-Zeile), aus
seinem root-eigenen Ort über seine sudo-Regel mit `sudo -n`, nie mit einem Passwort: Die Root-Bereitschaft des Boards
muss eingerichtet sein (`bundle prepare-root`; eine sudo-Regel von vor der Uhr-Aktion wird dort ersetzt).
llmcf-install schreibt die Zeit in eine funktionierende RTC (`hwclock -w -u`) und sichert sie für den nächsten Start
(`fake-hwclock save force` oder die Uhrdatei von systemd-timesyncd). Jedes Stellen, auch ein gescheitertes, kommt ins
Audit-Log. Nach dem Root-Apply eines Bundles auf einem Board ohne funktionierende Uhr richtet dasselbe Ja auch ein,
dass es seine Zeit über einen Neustart behält (`llmcf-install clock keep`: fake-hwclock, wo es installiert ist, sonst
systemd-timesyncd). Ein Board ohne beides bekommt den Vorschlag, fake-hwclock zu installieren
([device package](#device-package)).

In der GUI: **Boards > Boards und Geräte...** **> Uhr des Boards...** (derselbe Stand, dann die Frage, ob sie jetzt
gestellt werden soll; "Nein" ist die Vorgabe). Nach dem Pairing eines Boards per Code fragt das Fenster einmal nach
der dauerhaften Antwort.

### device package

Die Systempakete der Distribution eines Boards, die seine Bündel brauchen (das Feld `system_packages` eines
Bausteins, das zur Root-Aktion `install_package` des Bündels wird), und der Uhrhalter `fake-hwclock` eines Boards
ohne funktionierende Uhr und ohne Zeithalter. Sie werden nur als root über die typisierte Aktion `install_package` von
llmcf-install installiert, nur wenn eine signierte Liste sie nennt (das signierte Manifest des Bündels, oder, ohne
`--bundle`, nur `fake-hwclock`), über apt und dpkg (Debian, Ubuntu, Raspberry Pi OS, Armbian, DietPi; eine andere
Paketverwaltung wird genannt, und nichts läuft), ohne die empfohlenen Pakete und ohne je eines zu entfernen. Die
Root-Bereitschaft des Boards muss eingerichtet sein (`bundle prepare-root`; eine ältere sudo-Regel wird dort ersetzt).
Beim Root-Apply eines Bündels läuft dieselbe Aktion zuerst, vor den Konten und Units; die Pakete bleiben, wenn das
Bündel zurückgerollt oder deinstalliert wird.

`device package show GERÄT` zeigt Distribution, Release und Architektur des Boards, seine Paketquellen, was seine
Bündel (`--bundle`) und seine Uhr brauchen, was bei der letzten Lesung fehlte, die übernommenen Board-Preloads und die
letzte Installation; `--read` liest sie jetzt vom Board (nichts ändert sich).

`device package install GERÄT` zeigt zuerst den Plan (welche Pakete und Abhängigkeiten kämen und woher) und fragt dann
("Nein" ist die Vorgabe; `--yes`: ohne Frage; ohne Terminal wird nichts installiert, Exit-Code 1). Die Quelle ist das
dafür übernommene Board-Preload, wenn es eines gibt, sonst die eigenen Paketquellen des Boards (`apt-get update` und
`apt-get install`). Im Enterprise-Modus müssen die eigenen Quellen des Boards der interne Spiegel der Firma sein: Eine
Quelle außerhalb des eigenen Netzes verweigert den Weg übers Netz und verweist auf das Board-Preload. Jede
Installation, auch eine gescheiterte, kommt ins Audit-Log und in den Paket-Eintrag des Boards.

**Ohne Netz** (ein Board in einem abgeschotteten Netz, in beiden Modi): `device package preload write GERÄT --out
ORDNER` schreibt `preload.sh` und `preload.ps1` für genau diese Pakete für Distribution, Release und Architektur des
Boards (aus seiner letzten Paketlesung; für ein nie gelesenes Board nenne sie mit `--distribution`, `--release`,
`--arch` und `--archive`). Führe eines auf einem beliebigen Rechner mit Netz und Python 3 aus (Linux, macOS oder
Windows): Es holt die signierten `Release`-Listen der Distribution (die Archivschlüssel nur mit den Fingerabdrücken,
die llmcf festlegt), löst die Abhängigkeiten auf, prüft jedes Paket gegen die Listen und schreibt
`llmcf-preload-packages-...tar.gz`. `device package preload take GERÄT DATEI` prüft es hier (seine Dateien, die
Signaturen, jedes Paket) und kopiert es per SSH auf das Board; hast du das Archiv per USB zum Board gebracht, kopiere es
in das Home des Board-Kontos und nenne dort seinen Pfad mit `--on-board`. Dann installiert `device package install`
daraus ohne jedes Netz; llmcf-install prüft die Signaturen erneut gegen den eigenen Archiv-Schlüsselbund des Boards und
jedes Paket gegen die signierten Listen und verweigert das ganze Preload, wenn eine Datei nicht passt.

In der GUI: **Boards > Boards und Geräte...** **> Pakete des Boards...** (derselbe Stand; installieren mit Plan und
Frage, ein Board-Preload schreiben, eines von diesem Rechner oder vom Board übernehmen). Eine KI liest nur die
Paketfakten des Boards und einen Vorschlag (`list_devices`); sie installiert nie.

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
Ältere Pakete des Geräts bleiben (vorher entfernte ein Build oder ein Deployment sie); die Ausgabe
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
   nichts. Nach dem Umschalten bleibt jeder ältere Build auf dem Board (vorher wurden alle außer
   dem vorigen ohne Frage entfernt). Die Ausgabe sagt, wie viele geblieben sind und wie groß sie sind;
   `device runtime prune GERÄT --on-device` listet sie, damit du sie entfernst.
6. Mit der systemd-Einstellung wird die Unit `llm-llama-server.service` mit `sudo -n` geschrieben und aktiviert und
   gestartet, sobald das Modell da ist.

Das Ergebnis liegt in `device/<ID>/deployed.json` (ein gescheitertes Deployment ändert es nicht). Exit-Code 1, wenn
ein Schritt oder eine Prüfung scheitert.

Bricht das Netz des Boards während eines Schritts ab (zum Beispiel bei einem Board nur mit WLAN), warten dieser
Befehl, `device runtime verify` und `device runtime prune --on-device` bis zu 300 s auf das Board und sagen das einmal
(`Warte auf das Board unter ... (Ctrl+C)`); Strg+C beendet den Befehl sofort. Ein Board, das nicht wieder antwortet,
beendet den Befehl mit dem, was das bedeutet, und dem nächsten Schritt, wie `deploy run` es sagt.

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

Derselbe kurze Benchmark misst das Board (seinen Speicher mit dem Modell); der Server des Boards nimmt Kontext,
KV-Cache und Prompt-Cache aus dieser Messung. Ein Deployment ohne ihn sagt klar, dass das Board zuerst seine
Messung braucht: Bis dahin startet das Startskript den Server nicht (Exit-Code 78), und die systemd-Unit bleibt
gestoppt, statt es alle 10 Sekunden neu zu versuchen.

Die Prüfzeilen enden mit der Temperatur des Boards während der Prüfungen (vor und nach dem kurzen Benchmark gelesen;
siehe [Funktionen](Features_DE.md#laufzeit-auf-dem-board-selbst-gebautes-llamacpp)). Ein heißes Board bekommt eine
Warnung mit dem, was hilft, und eine Zeile zu Mojo Oracle; es lässt keine Prüfung scheitern, der Exit-Code bleibt also
der der Prüfungen. Strg+C während des kurzen Benchmarks stoppt `llama-bench` auch auf dem Board (die Prozessgruppe,
die das Framework dort gestartet hat).

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

## bundle

Das Board-Bündel, Deploy-Weg 3 ([Funktionen](Features_DE.md#das-board-bündel)): Bausteine mit ihren Variablen und
Dateien, ein gegen die Untersuchung des Boards geprüfter Plan, ein mit dem Schlüssel des Frameworks signiertes Bündel
im Speicher dieses Frameworks und sein Installer auf dem Board. `GERÄT` ist ein gespeichertes Board wie bei
[`device`](#device); `BUNDLE` ist die ID eines Bündels, wie `bundle list` sie zeigt.

In der GUI: **Boards > Board-Bündel...**: das Board, eine Vorlage und die Bausteine, eine Tabelle der Variablen (jede
beim Tippen gegen ihren Typ geprüft) und eine der Dateien (**Datei wählen...** oder eine https-Adresse mit sha256 und
Größe), **Planen**, **Bauen...**, die gespeicherten Bündel mit **Probelauf auf dem Board**, **Ausrollen...**, **Status
auf dem Board**, **Zurückrollen...**, **Deinstallieren...** und **Gespeicherte Bündel löschen...**, und **Neuer
Baustein...**; dieselben Schritte und Fragen wie hier, jeder Lauf auf dem Board im Hintergrund mit **Abbrechen**.

```
llm-cli bundle plan GERÄT [--preset llm|voice|stt] [--block ID]... [--set KOMPONENTE.NAME=WERT]...
                    [--file KOMPONENTE.ROLLE=PFAD]... [--online KOMPONENTE.ROLLE=URL --sha256 KOMPONENTE.ROLLE=HEX
                    --size KOMPONENTE.ROLLE=BYTES]... [--licence KOMPONENTE.ROLLE=ID]... [--unprivileged-only]
                    [--accept-terms BAUSTEIN]... [--without-measurement] [--accept-memory-risk]
                    [--engines together|in-turn] [--json]
llm-cli bundle build GERÄT (dieselbe Auswahl) [--allow-own]
llm-cli bundle list [--json]
llm-cli bundle deploy BUNDLE [--dry-run] [--skip-root]
llm-cli bundle status BUNDLE
llm-cli bundle uninstall BUNDLE
llm-cli bundle rollback BUNDLE [--root]
llm-cli bundle prepare-root BUNDLE [--remove]
llm-cli bundle delete [BUNDLE]...
llm-cli bundle block list [--json]
llm-cli bundle block new ID --kind ART --title TEXT [--summary TEXT] [--from BAUSTEIN]
llm-cli bundle block propose (--hf BESITZER/NAME [--revision COMMIT] | --from-folder PFAD | --task AUFGABE) [--id ID]
                    [--write [ORDNER]] [--json]
llm-cli bundle harness take-in ID LOCATION --kind git|archive|folder --installer PATH [--ref REF] [--pin PIN]
                    [--title TEXT] [--dry-run-arg=OPTION]... [--assume-yes NAME=VALUE] [--exit-code CODE=done|findings]...
                    [--image-partition N] [--image-path FOLDER]
llm-cli bundle harness dry-run BUNDLE [--component ID]
llm-cli bundle harness run BUNDLE [--component ID] [--assume-yes] [--skip-dry-run]
llm-cli bundle harness attach|stop BUNDLE RUN [--component ID]
llm-cli bundle harness answer BUNDLE RUN [--component ID] [--hidden]
llm-cli bundle harness report BUNDLE [RUN] [--component ID] [--json]
```

### bundle plan

Zeigt den ganzen Plan, bevor etwas gebaut oder gesendet wird: jede Komponente mit ihrem Baustein, ihrer
Vertrauensstufe und ihren Variablen (jede gegen ihren Typ geprüft), jede Datei mit Rolle, Name, Größe, Quelle (die
lokale Datei, aus der sie kopiert wird, das Laufzeitpaket des Boards oder die festgelegte Online-Adresse) und Lizenz,
die Root-Aktionen, die über llmcf-install laufen, den Speicherbedarf gegenüber dem, was das Board hat, was gesendet wird,
und die Prüfungen gegen die Untersuchung des Boards (Architektur, Init-System, sudo, Offline-Modus, Speicher:
bestanden, nicht bestanden, Warnung oder unbekannt). Es wird kein Board kontaktiert. Eine Komponente heißt nach ihrer
Art (`llm`, `stt`, eine zweite derselben Art `stt-2`). `--json` gibt den Plan nur als Labels aus: kein lokaler Pfad,
keine Adresse, kein Konto. Exit 1, wenn der Plan Probleme hat.

Ein Baustein, dessen Teile eigenen Lizenzbedingungen unterliegen (zum Beispiel eSpeak NG in `tts-sherpa-onnx` oder
die Opt-in-Vorlagen `stt-nemo-sherpa` und `stt-kws-gigaspeech` mit Bedingungen, die keine freie Lizenz sind), wird
mit dem Problem geplant, dass seine Bedingungen noch nicht angenommen sind, und der Plan druckt die Bedingungen danach
vollständig. `--accept-terms BAUSTEIN` nimmt genau den gezeigten Text an (die Annahme ist an seinen sha256 gebunden;
ein geänderter Text ist nicht angenommen). Ein Baustein mit einer nicht freien Bedingung ist nie Teil einer Vorlage.

Ein Baustein, dessen Programm das Framework für das Board baut (`llm-llama-server` und `vision-mmproj`: llama.cpp;
`stt-whisper-cpp`: das festgelegte whisper.cpp), braucht dieses Board-Paket. Der Plan nennt es, wenn es noch nicht
gebaut ist, und prüft jede Komponente gegen die Untersuchung des Boards (`component stt`: die Architektur des Pakets
gegen die des Boards, die CPU-Merkmale, für die es gebaut ist, der Speicher). Für `vision-mmproj` prüft der Plan auch,
dass Modell und Projektor ein Paar sind (`vision pair vision`), und zählt beide im Speicher.

`--engines together|in-turn` ist deine Wahl, wie sich die Programme des Bündels (Sprachmodell, Spracherkennung und
Bild) den Speicher des Boards teilen; `together` ist die Vorgabe. Der Plan zeigt immer beide Zahlen, in seiner
Prüfung `engines` und in einer Zeile in deiner Sprache: alle zusammen (die Programme, der Rest des Bündels und die
Reserve gegen den gemessenen freien Speicher des Boards) und nacheinander (das größte Programm mit dem Rest und der
Reserve). Mit `in-turn` lädt das Gateway des Boards die Programme nacheinander: Bevor eine Anfrage an ein Programm
geht, das nicht läuft, beenden die anderen ihre Anfragen und werden gestoppt, dann wird dieses Programm gestartet. Das
braucht das Gateway und jedes Programm mit Autostart (ihre Units); der Plan lehnt `in-turn` ab, wenn schon das größte
Programm allein nicht passt, und nennt `in-turn`, wenn alle zusammen nicht passen, eines nach dem anderen aber schon.
Bisher gehen nur die Anfragen an das Sprachmodell durch das Gateway, nacheinander bleibt ein Sprach- oder Bildprogramm
also gestoppt, während das Modell läuft. Der Plan einer KI wählt das nie.

Ein Bundle, das der Speicher des Boards nicht fasst (nach seiner Messung oder mit `--without-measurement` nach dem
angegebenen Speicher der Probe), wird mit den Zahlen abgelehnt, nie nur wegen seiner Größe: `--accept-memory-risk`
ist dein Ja dazu. Der Plan trägt die Lücke dann als Warnung, und das Bundle wird gebaut; das Board lagert aus (wenn
es Swap hat) und läuft viel langsamer, und ein Sprachmodell, das laut Messung nicht passt, startet mit dem Kontext
seines Bausteins (`--set llm.ctx_size=N` für einen anderen). Das Profil eines Boards empfiehlt nur; was du deinem
Board zumutest, entscheidest du. Der Plan einer KI nimmt das Speicherrisiko nie an.

### bundle build

Plant wie `bundle plan`, zeigt den Plan und schreibt das Bündel in den Speicher dieses Frameworks, signiert mit dem
Board-Schlüssel des Frameworks (nichts wird ans Board gesendet). Ein eigener, nicht signierter Baustein wird nur nach
deinem Ja im Terminal verwendet ("Nein" ist die Vorgabe; `--allow-own` beantwortet es vorab); der Enterprise-Modus
nimmt nur die Bausteine des Frameworks und aus dem signierten Index installierte. Ein Bündel mit Root-Aktionen
(Autostart: ein Konto, eine Unit, ihre Speicher- und CPU-Grenzen) enthält auch llmcf-install, den Root-Helfer des
Frameworks, gebaut für die Architektur des Boards (x86_64, aarch64 oder armv7, statisch) und mit dem Bündel signiert:
Die auf diesem Rechner aufbewahrte Kopie wird genommen, sonst wird er hier mit cargo oder im Builder-Image über die
Container-Engine gebaut. Im Offline-Modus wird nur eine aufbewahrte Kopie genommen; ohne sie hält der Build mit dem
klaren Grund an (einmal online bauen oder die Variante ohne Sonderrechte wählen).

Bevor etwas gebaut wird, zeigt `bundle build` im Terminal die noch nicht angenommenen Lizenzbedingungen jedes
Bausteins vollständig und fragt je Baustein ("Nein" ist die Vorgabe; `--accept-terms` beantwortet es vorab). Wartet
der Plan auf ein noch nicht gebautes Board-Paket (whisper.cpp, llama.cpp), fragt es, ob es jetzt gebaut werden soll
("Nein" ist die Vorgabe), baut es über denselben Weg des eigenen Builds wie `device runtime build` (der festgelegte
Commit, Architektur, C-Bibliothek und CPU-Merkmale des Boards; offline nur aus einer aufbewahrten Quelle) und plant
dann neu. Ohne Terminal hält es mit den Problemen des Plans an.

### bundle deploy

Meldet sich nur mit dem Schlüssel des Frameworks am Board des Bündels an (als Benutzer aus den Deploy-Einstellungen
des Boards, nie mit Passwort), sendet, was das Board noch nicht hat, und führt zuerst den Probelauf des Installers
aus; hat er bestanden, fragt es "Send bundle ... to board ...?" ("Nein" ist die Vorgabe; ohne Terminal wird nichts
installiert) und installiert dann die Schritte ohne Sonderrechte. Vor den Root-Aktionen hält der Installer an;
dann zeigt llmcf-install seinen eigenen Plan davon und fragt ein zweites Mal ("Run the ... root action(s) ... as root
now, through llmcf-install?", "Nein" ist die Vorgabe). Nach deinem Ja führt das Board ihn über `sudo -n` als root
aus; fragt sudo auf dem Board nach einem Passwort, lässt dich der private Modus das Passwort des Board-Kontos einmal
für diesen Lauf eingeben (es wird einmal gesendet und nie aufbewahrt), während der Enterprise-Modus Root-Aktionen nur
mit `sudo -n` ausführt und mit dem Hinweis anhält, was zu erlauben ist. llmcf-install prüft die Signatur des Bündels
selbst gegen den root-eigenen Signaturschlüssel des Frameworks, führt nur die typisierten Root-Aktionen des Manifests
aus, ändert nichts, was schon so ist, wie es sein soll, und führt ein Journal für Zurückrollen und Deinstallieren.
`--skip-root` hält vor den Root-Aktionen an (ein späteres Deployment führt sie aus). Ein abgebrochenes Deployment
macht im selben Upload-Ordner weiter. `--dry-run` sendet das Bündel und führt nur den Probelauf aus.

### bundle status, uninstall, rollback

`status` fragt den Installer, den das Board behalten hat, welche Schritte erledigt sind, und llmcf-install,
welche Root-Aktionen bestehen (ändert nichts). `uninstall` listet zuerst, was llmcf-install als root angelegt hat
(aus seinem Journal), und fragt für diesen Teil, dann listet es die eigenen Schritte des Installers (die neuesten
zuerst) und fragt erneut ("Nein" ist jedes Mal die Vorgabe); behältst du den Root-Teil, bleibt auch der Rest.
`rollback` zeigt den Status und fragt, bevor es das letzte Apply rückgängig macht; `rollback --root` nimmt
stattdessen den letzten Lauf von llmcf-install zurück (genau das, was dieser Lauf laut Journal angelegt oder geändert
hat). Scheitert eine Root-Aktion, nachdem andere schon erledigt sind, sagt es das; `rollback --root` nimmt diesen
Lauf zurück.

### bundle prepare-root

```
llm-cli bundle prepare-root BUNDLE [--remove]
```

Richtet die Root-Bereitschaft des Boards für das llmcf-install des Bündels ein, wie eine Abhängigkeit: Es liest, was da ist (sudo, der Signierschlüssel in Root-Besitz, llmcf-install unter `/usr/local/libexec/llmcf/llmcf-install`, seine sudo-Regel `/etc/sudoers.d/llmcf-install`), zeigt den Plan von nur dem, was fehlt, fragt (Nein ist die Vorgabe) und richtet es einmal als root ein: sudo über die Paketverwaltung des Boards (ein Board ohne sudo: das root-Passwort für `su`, nur im Privatmodus), das Programm in Root-Besitz nach Prüfung von Signatur und sha256, die Regel geprüft mit `visudo -cf`. Ein Passwort wird einmal getippt und nur über stdin gesendet. Der Signierschlüssel in Root-Besitz kommt vom erneuten Koppeln des Boards mit seinem Passwort. `llm-cli bundle deploy` fragt dasselbe, wenn die Root-Aktionen es brauchen; ein Nein beendet es mit Exit 1. `--remove` listet genau die zwei Dateien und entfernt sie nach einem getrennten Ja.

### bundle delete

Listet die gespeicherten Bündel (oder die genannten) mit vollem Pfad, Größe und was sie sind (ein Bündel, das ein
Board hat, sagt das: es hier zu löschen lässt das Board, wie es ist). Nichts ist angehakt: du tippst die Nummern,
wählst den Papierkorb (die Vorgabe) oder endgültiges Löschen und bestätigst genau diese (Vorgabe Nein). Es braucht ein
Terminal; ohne eines listet es nur. Das Löschen wird zuerst ins Audit-Log geschrieben; was nicht gelöscht werden
konnte, wird mit jedem gebliebenen Pfad gemeldet.

Für ein [vertrautes Gerät](#deploy-devices) tun die optionalen MCP-Werkzeuge `bundle_plan` (der Plan nur als Labels)
und `bundle_deploy` (`bundle deploy` ohne den Probelauf-Schritt, von dir im eigenen Fenster des Frameworks bestätigt)
das für einen KI-Client; die Dateien wählst immer du.

### bundle harness

Dein eigener Agent-Harness auf dem Board ([Funktionen](Features_DE.md#dein-agent-harness-auf-dem-board)). `take-in`
übernimmt ihn an einem Pin und bewahrt seinen Baum mit einer Prüfsumme im Benutzerordner dieses Computers auf
(`--kind git`: ein https-Link, `--ref` ein Branch oder Tag, der zu seinem Commit aufgelöst wird, oder `--pin` der Commit;
`archive`: ein Archiv oder Disk-Image, `--pin` seine sha256: zip, tar, tar.gz, tar.bz2, tar.xz, tar.zst, eine einzelne
.gz-/.bz2-/.xz-/.zst-Datei und mit 7-Zip 7z, rar, iso, cpio und .img-/.bin-Disk-Images, erkannt an ihrem Inhalt, nicht
am Namen; die ext-Partition eines Disk-Images wird an Ort und Stelle gelesen (nur der Ordner des Harness;
`--image-path ORDNER` nennt ihn in der Partition, sonst wird der Installer allein über die Ordner gesucht), eine FAT-
oder NTFS-Partition über 7-Zip; `--image-partition N` nennt die Partition (0 die erste), wenn mehrere den Installer
enthalten, und die Übernahme sagt, wie viel sie vom Image gelesen hat; ein verschlüsseltes Archiv fragt ohne Anzeige
nach seinem Passwort (drei Versuche; ohne Terminal wird es abgelehnt); ein harter Link innerhalb des Baums wird eine
eigene Datei; eine .bin, die ein Skript oder ein Programm ist, ist ein Harness aus einer Datei, `--installer` ihr eigener
Name; `folder`: ein Ordner; unter Windows, das kein Ausführbar-Bit kennt, nimmt ein git-Checkout die Bits aus seinem
Index, jeder andere Ordner aus dem Inhalt: Eine Datei, die mit `#!` beginnt oder ein ELF- oder Mach-O-Programm ist, und
der Installer sind ausführbar, und die Übernahme sagt, wie viele sie markiert hat). Ein Format, das 7-Zip braucht, wird
ohne 7-Zip mit seinem Namen und der Abhilfe abgelehnt (den Installer noch einmal ausführen oder 7-Zip installieren). Eine Option des
Probelaufs schreibst du mit `=`, wie in `--dry-run-arg=--dry-run`. In ein Bündel kommt er mit
`bundle build GERÄT --block harness-agent --set harness.harness=ID` (`--set harness.assume_yes=yes` nimmt seinen Assume-Yes-Schalter mit,
dein Opt-in); `bundle deploy` legt ihn mit den Root-Aktionen des Bündels auf das Board.

`run` beginnt mit `dry-run` (dem eigenen Probelauf des Installers nach seiner Frage, seiner Ausgabe und seinem Bericht:
der Bericht vorher; `--skip-dry-run` lässt ihn weg), fragt dann „Den Installer des Harness ... als root ausführen?“
(„Nein“ ist die Vorgabe; ohne Terminal startet nichts) und führt ihn über llmcf-install aus, das vorher die
Baum-Prüfsumme neu rechnet. Die Ausgabe kommt live als reiner Text; jede Zeile, die du tippst, geht als Antwort an den
Installer (`answer --hidden` tippt eine verborgen, für ein Passwort, nach dem der Installer fragt). Strg+C trennt: Der
Installer läuft auf dem Board weiter, und `attach BUNDLE RUN` liest dort weiter, wo dieser Computer zuletzt gelesen
hat, wie nach einer abgebrochenen Verbindung. `stop` beendet den Installer nach seiner Frage. Ist er fertig, sagt der
Bericht fertig, fertig mit Befunden oder fehlgeschlagen (nach den Rückgabewerten des Harness), was sich geändert hat
und was markiert ist; `report BUNDLE` listet die Läufe, `report BUNDLE RUN` zeigt einen (`--json` für Skripte).
`--assume-yes` nutzt den eigenen Schalter des Harness, nur wenn das Bündel ihn trägt. Die Läufe brauchen die
Root-Bereitschaft des Boards (`bundle prepare-root`): Ein Harness-Lauf nimmt kein Passwort. Findet ein Harness-Schritt
sie fehlend (oder ist die sudo-Regel des Boards eine ältere ohne die Harness- oder Uhr-Aktionen), listet der Befehl, was ihre
Einrichtung tun wird, fragt „Die Root-Bereitschaft von Board ... jetzt einrichten?“ („Nein“ ist die Vorgabe; ohne
Terminal wird nichts eingerichtet), fragt nach dem Passwort des Board-Kontos, wenn sudo eines braucht (einmal, nie
gespeichert), führt die eine Einrichtung der Root-Bereitschaft von `bundle prepare-root` aus (nur was fehlt: eine
ältere sudo-Regel wird ersetzt, eine aktuelle bleibt) und führt den Schritt erneut aus; ist die Bereitschaft schon da,
sagt er das und ändert nichts. Deine Antwort und das Ergebnis sind Zeilen im Audit-Protokoll.

Die Aufzeichnungen kommen vollständig zurück, nie abgeschnitten: llmcf-install listet sie mit Größe und sha256 und
schickt sie in Teilen von höchstens 4 MiB; dieser Computer prüft zuerst seinen freien Platz (die eine Speicherregel
des Frameworks), liest Teil für Teil in den Ordner des Laufs, liest nach einer abgebrochenen Verbindung ab dem letzten
guten Teil weiter und macht den Bericht erst, wenn die sha256 jeder Datei stimmt. Der Bericht sagt auch, ob der
Installer eigene Konten und Service-Units mitgebracht hat; das Framework legt nie ein eigenes Konto oder einen eigenen
Dienst obendrauf an. Hat er keines von beiden mitgebracht, sagt der Bericht das: Ein eigenes Konto und ein eigener
Dienst für einen Harness sind ein späterer Schritt der Roadmap, nur als dein Opt-in.

Das MCP-Werkzeug `harness_report` (optional) lässt eine KI die Läufe nur als Ergebnis, Zahlen und Markierungs-Codes
lesen (auch, wie viele Konten und Service-Units der Installer mitgebracht hat, nie ihre Namen); es startet,
beantwortet oder stoppt nie einen und richtet nie die Root-Bereitschaft eines Boards ein.

```bash
llm-cli bundle harness take-in chimera https://git.example.invalid/agents/chimera --kind git --ref entwicklung \
    --installer installer/chimera-install --dry-run-arg=--dry-run --assume-yes CHIMERA_ASSUME_YES=1 \
    --exit-code 0=done --exit-code 1=findings
llm-cli bundle build "ZERO 3W" --block harness-agent --set harness.harness=chimera
llm-cli bundle harness run harness-01234567-20261107120000
```

### bundle block list, block new, block propose

`block list` zeigt jeden Baustein mit Art, Status (bereit oder dokumentierte Vorlage) und Vertrauensstufe
(framework, indexed, signed, own). `block new` legt deinen eigenen Baustein an (`block.yml` und seine Anleitung
`README.md`, auf Wunsch ausgehend von einem Baustein des Frameworks) in `community/blocks/`, nicht signiert: der
private Modus fragt, bevor er verwendet wird, der Enterprise-Modus nimmt ihn erst, wenn er veröffentlicht und aus dem
signierten Index installiert ist. Wie du ihn ausfüllst und veröffentlichst:
[Modul-Entwicklerhandbuch](Module_Developer_Guide_DE.md#12-bausteine-für-das-bündel).

`block propose` schlägt einen Baustein aus einem Modell vor: `--hf BESITZER/NAME` liest seine Hugging-Face-Fakten in
einer Abfrage (Pipeline-Aufgabe, Bibliothek, Lizenz, Dateien; `--revision` legt einen Commit fest; der Offline-Modus
lehnt es ab, ein gesperrtes Modell braucht dein gespeichertes Token), `--from-folder PFAD` liest einen lokalen
Modellordner ohne Netz (seine `config.json` und den Vorspann seiner `README.md`), `--task AUFGABE` nimmt nur eine
Pipeline-Aufgabe. Es zeigt die vorgeschlagene `block.yml` (Art, Dateien nach Rolle, `reach` mit vorsichtigen
Grenzen), den Grund jedes Felds (die Aufgabe, die Bibliothek, ein Standardwert, die Modelldateien oder "needs the
person"), was eine Engine vom Typ Erkennung wissen muss und woher, was du entscheidest, und die Texte, die KI-Clients
für dieses Modell vorgeschlagen haben (als KI-erzeugt markiert, nie übernommen). Ohne `--write` wird nichts
geschrieben: dann fragt es an einem Terminal ("Nein" als Vorgabe) und schreibt den Baustein als deinen eigenen,
nicht signierten Baustein nach `ORDNER` oder, ohne `ORDNER`, nach `community/blocks/`. Er bleibt eine Vorlage, bis du
sein Programm und seine Startargumente aus dem Zielmodul ausfüllst. `--json` gibt dasselbe als Kennzeichen aus. Die
Regeln: [Modul-Entwicklerhandbuch](Module_Developer_Guide_DE.md#128-einen-baustein-aus-einem-modell-vorschlagen).

```bash
llm-cli bundle block propose --hf example/detector-tiny
llm-cli bundle block propose --from-folder models/whisper-small --write
llm-cli bundle block propose --task object-detection --id detect-cam --write
```

```bash
llm-cli bundle plan "ZERO 3W" --preset stt --file stt.model=models/ggml-base.bin \
    --file stt.program=build/whisper-server --licence stt.model=MIT
llm-cli bundle build "ZERO 3W" --preset stt --file stt.model=models/ggml-base.bin \
    --file stt.program=build/whisper-server --licence stt.model=MIT --unprivileged-only
llm-cli bundle deploy stt-01234567-20261101120000
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

In der GUI: **Datei > Einstellungen…**, Seite **Wirksame Einstellungen** (nur lesen; Geheimnisse geschwärzt).

### config output-folder

```
llm-cli config output-folder [ORDNER]
```

Ohne `ORDNER` zeigt es den Ausgabeordner (Einstellung `output_dir`, Standard `~/llmcf/output`) und
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

### config take-over

```
llm-cli config take-over [--yes] [--others]
```

Den Installationsordner teilen sich alle, die llmcf auf dem Rechner nutzen, und das Framework schreibt im Betrieb nicht
mehr hinein (nur ein Git-Checkout, in den dein Benutzer schreiben darf, behält seine Daten in sich). Eine frühere
Version hat dort deine Einstellungen und den alten Tresor (`configs/`), `output/`, `cache/`, `models/`, `datasets/`,
`logs/`, Targets, die du angelegt oder installiert hast, und `community/` abgelegt. Der Befehl listet jedes davon mit
dem Ort, an den es in deinen eigenen Ordnern kommt (`~/.llm-framework` mit seinen `targets/` und `community/`, oder
der Ordner, den eine Einstellung nennt), und verschiebt sie nach deinem Ja; `--yes` verschiebt sie ohne Frage. Dinge,
die einem anderen Konto des Rechners gehören oder deren Besitzer das System nicht nennt (Windows), werden getrennt
aufgelistet und nur mit `--others` verschoben (an einem Terminal noch einmal gefragt). Nichts wird überschrieben: Hast
du einen Namen schon, kommt die Kopie aus der Installation nach `from-installation/` in deinem persönlichen Ordner.
Nichts wird gelöscht: Jedes Ding wird zuerst kopiert, und das Original bleibt, wenn dein Konto die Installation nicht
ändern darf (ein Administrator kann es entfernen). Der erste Befehl nach dem Update nennt diesen Befehl einmal; die
GUI stellt dieselbe Frage bei ihrem ersten Start, und ein Nein dort wird nicht noch einmal gefragt.

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

Die Überschreibung für llama.cpp (`LLAMA_CPP_REPO_OVERRIDE`, zum Beispiel ein Spiegel in einem Firmennetz) ist der
Ort, von dem ein Target-Image den gepinnten llama.cpp-Commit holt: Die Engine übergibt sie als Build-Argument
`LLAMA_CPP_REPO`, wenn sie eine https-Adresse ohne Benutzernamen, Passwort, Token oder Query ist (sonst warnt das
Build-Log, und das Image holt von Upstream), und das Image bricht weiterhin ab, wenn es nicht genau den gepinnten
Commit bekommt. Das Build-Log sagt, wann die Überschreibung genutzt wurde oder warum nicht.
`source_module.sh --clone-llama-cpp`, von Hand in einem Container mit Netzwerk gestartet, klont nach derselben Regel
daraus. Die übrigen Überschreibungen ändern in einem Build nichts: Seine Stufen-Container haben kein Netzwerk.

In der GUI: **Datei > Einstellungen…**, Seite **Quell-Repositorys**: die Liste, **Hinzufügen** (Name und URL) und
**Ausgewählte entfernen…** (fragt zuerst; „Nein“ ist die Vorgabe).

### config set

```bash
llmcf config set framework_mode enterprise
llmcf config set framework_mode private
llmcf config set default_enable_imatrix false
llmcf config set default_calibration_dataset calib_de.txt
llmcf config set calibration_language de
llmcf config set language de
llmcf config set form_of_address sie
llmcf config set ai_provider ollama
llmcf config set ai_effort high
llmcf config set offline_mode true
```

Setzt eine deiner eigenen Einstellungen in config.yml. Jede Einstellung hier stellst du auch in **Datei >
Einstellungen…** ein, dem einen Einstellungsfenster. `framework_mode` ist private (Standard) oder enterprise: Der Enterprise-Modus nimmt auf einem Board nur OpenSSH mit sudo (die Schlüsselzeile in Root-Besitz) und lehnt die schwächere Dropbear-Stufe und das Home-Tor ab, beim Koppeln und bei jeder Benutzung eines früher gekoppelten Boards. Exit-Code 2 für einen Wert, der keins von beiden ist. Nur du setzt ihn; eine KI darf ihn lesen (`get_settings`), nie ändern. Die Installer fragen dasselbe (`install.sh --mode=private|enterprise`, `install.bat --mode=...` oder `LCF_MODE` für einen unbeaufsichtigten Lauf).

In der GUI: **Datei > Einstellungen…**, Seite **Allgemein**, **Framework-Modus**.

Die Build-Vorgaben, die gelten, wenn ein Build keine nennt (`build start`, der Builder-Tab und die KI-Tools
gleichermaßen): `default_enable_imatrix` ist true (Standard) oder false: Mit true bekommt ein GGUF-Build eines Typs
unter 5 Bit je Gewicht eine Importance Matrix, mit false nur ein Typ, der eine braucht. `default_calibration_dataset`
ist leer (Standard: der mitgelieferte Kalibrierungstext, [data/calibration](../data/calibration/README_DE.md)) oder
deine eigene Textdatei, ein Name im Datensatz-Ordner (Einstellung `datasets_dir`) oder ein voller Pfad; sie gilt für
die GGUF-Importance-Matrix und für RKLLM, nie für RKNN. Eine Datei, die es noch nicht gibt, wird mit einer Warnung
gespeichert, und ein Build, der sie lesen würde, wird abgelehnt, bis es sie gibt; `''` setzt wieder den
mitgelieferten Text. `calibration_language` ist `mixed`, `de` oder `en`, die Sprachen des mitgelieferten Texts.
Exit-Code 2 für einen Wert, der keins davon ist. `--imatrix`, `--no-imatrix`, `--dataset` und `--no-dataset` von
`build start` gewinnen über die Vorgaben. Eine KI darf `default_enable_imatrix` und `calibration_language` ändern
und liest die Datensatz-Einstellung als `datasets:<Name>`, setzt sie aber nie. In der GUI: **Datei >
Einstellungen…**, Seite **Allgemein**, der Kasten „Build-Vorgaben“.

Die Sprache des Programms: `language` ist `en` oder `de`, `form_of_address` ist `du` (Standard) oder `sie`, die
deutsche Anrede. Exit-Code 2 für einen anderen Wert. Eine laufende GUI übernimmt sie beim nächsten Start; in der GUI
schaltet **Datei > Einstellungen…**, Seite **Allgemein**, die Menüs, die Reitertitel, das Chat-Panel und jedes danach
geöffnete Fenster sofort um (die Inhalte der Reiter Builder, Deployment und Auto-Tuning beim nächsten Start).

Dittos KI-Einstellungen, geprüft nach denselben Regeln wie im Fenster (**Datei > Einstellungen…**, Seite **Ditto**,
Abschnitt **KI-Einstellungen**; auch **Ditto > KI-Einstellungen...**): `ai_provider` (eine Anbieter-ID wie
`anthropic`, `openai` oder `ollama`), `ai_model`, `ai_base_url` (die Serveradresse eines Anbieters, dessen Adresse du
setzt), `ai_anthropic_workspace_id`, `ai_effort` (`low`, `medium`, `high`, `max` oder `''`), `ai_security_level`,
`ai_fallback_chain` (`anbieter[:modell], ...` oder `''`), `offline_mode` und `enable_rag_knowledge` (true oder
false). Was das Fenster ablehnt, wird hier mit Exit-Code 2 abgelehnt (ein Anbieter ohne Schlüssel, eine Adresse, die
keine http(s)-URL ist, ein Cloud-Anbieter im Offline-Modus, ...); ein Problem, das die gespeicherten Einstellungen
schon haben, blockiert keine andere Änderung. Ein Schlüssel kommt nie auf die Kommandozeile: Melde dich zuerst mit
[ditto login](#ditto-login) an und setze dann den Anbieter. Die Tool-Berechtigungen, die Ordner, die eine KI nutzen
darf, die lokale Laufzeit, der Schlüsselspeicher und das Update behalten ihre eigenen Befehle (`tools permissions`,
`tools folders`, `ditto runtime ensure`, `ditto cache set`, `ditto auth storage`, `update`); das Fenster hat für jeden
eine Seite.

### config local-network

```
llm-cli config local-network [--add ART WERT | --remove ART WERT | --check ADRESSE] [--yes]
```

Was der Offline-Modus als lokal zählt: diesen Computer, dein eigenes Netz und was du selbst als
lokal zählst. Ein Name zählt nach den Adressen, zu denen er auflöst (Loopback, private und Link-Local-Adressen sind
lokal; ein `.local`- oder `.lan`-Name nur, wenn er zu einer solchen Adresse auflöst; ein Name mit nur öffentlichen
Adressen ist nie lokal; ein Name mit lokalen und öffentlichen Adressen zählt als lokal, und das Framework verbindet
sich nur mit seinen lokalen, was `--check` sagt), und eine Adresse eines Tailscale- oder Headscale-VPN nur, wenn sein
Steuerserver und jedes Relay in deinem eigenen Netz sind und sie zu einem Gerät deines Tailnets gehört (gelesen mit
dem Befehl `tailscale`, wenn er installiert ist; eine private Adresse hinter der Subnet-Route eines Geräts zählt wie
eine VPN-Adresse). Ohne Optionen listet der Befehl, was du als lokal zählst, mit dem, was dann dein Netz verlassen
kann, und die Prüfung des VPN dieses Computers. Was du hier als lokal zählst, zählt nur dafür, was der
Offline-Modus erreichen darf: Der Datenschutzfilter bleibt dafür an.

`--add` zählt einen weiteren Eintrag als lokal: `vpn tailscale` (jede Tailscale- oder Headscale-Adresse, auch mit
Tailscales Servern oder Relays außerhalb), `host NAME` (zum Beispiel den öffentlichen Namen deines Reverse Proxys,
egal wohin er auflöst) oder `range CIDR` (einen Adressbereich wie `203.0.113.0/24` oder eine einzelne Adresse). Es
zeigt die einfache Erklärung, was dann dein Netz verlassen kann, und fragt zuerst („Nein“ ist die Vorgabe; `--yes`
überspringt die Frage, die Erklärung steht trotzdem da), dann speichert es `config.yml`. `--remove` zählt einen
Eintrag nicht mehr als lokal. `--check` zeigt, ob eine Adresse, ein Hostname oder eine URL als lokal zählt und
warum, mit den Adressen, zu denen er auflöst. Nur du legst das fest: die Änderung der KI-Einstellungen und MCP
`update_settings` lehnen es ab, und eine KI kann es weder lesen noch setzen. Exit-Code 2 für einen Wert, der kein
VPN, Hostname oder Bereich ist (eine Adresse gehört unter `range`), 1, wenn `config.yml` nicht gespeichert werden
konnte.

| Option | Bedeutung |
|---|---|
| `--add KIND VALUE` | Ein VPN, einen Host oder einen Adressbereich als lokal zählen (fragt zuerst). |
| `--remove KIND VALUE` | Nicht mehr als lokal zählen. |
| `--check ADDRESS` | Ob eine Adresse, ein Hostname oder eine URL als lokal zählt, und warum. |
| `-y`, `--yes` | Vor `--add` nicht fragen; für Skripte. |

In der GUI: **Datei > Einstellungen…**, Seite **Lokales Netz**: die Liste mit dem, was dann dein Netz verlassen kann,
**Als lokal zählen…** (Art und Wert; fragt mit der Erklärung, „Nein“ ist die Vorgabe), **Entfernen** (fragt zuerst),
die Prüfung des VPN dieses Computers und **Prüfen** für eine Adresse (beides im Hintergrund).

### config doorbell

```
llm-cli config doorbell [--broker URL] [--username NAME] [--prefix PRÄFIX] [--ca-file DATEI] [--password] [--test]
```

Die Build-Türklingel: mit einem eingestellten Broker wird jedes Build-Ende einmal an deinen MQTT-Broker gesendet, an
`<prefix>/v1/build/<request_id>/end` und `<prefix>/v1/events` (QoS 1, nicht gehalten); `<prefix>/v1/status` sagt
`online`, während das Framework sendet, und danach `offline` (gehalten, auch als letzter Wille). Die Nachricht ist
JSON nur mit festen Wörtern:

```json
{"v": 1, "event": "build_ready", "reason": "done", "name": "Build ready", "request_id": "req_1a2b3c4d", "ts": "2026-10-30T12:00:00Z"}
```

`event` ist `build_ready`, `build_error` oder `build_stopped`, `reason` eines von `done`, `failed`, `cancelled`,
`soft_cap`, `idle`; nie steht ein Pfad, Log-Text oder der Name eines Modells darin, die Einzelheiten kommen mit
`build status REQUEST_ID` oder über MCP. Das Framework sendet nur, es abonniert nie. Ein Broker, der nicht läuft,
hält nie einen Build auf; das letzte gescheiterte Senden zeigt Mojo. Lass die ACLs des Brokers nur dem Benutzer des
Frameworks erlauben, unter dem Präfix zu senden.

Ohne Optionen zeigt der Befehl die Einstellungen der Türklingel. `--broker` stellt den Broker ein: `mqtt://host:port`
nur auf diesem Computer, jeder andere Broker `mqtts://host:port` mit geprüftem Zertifikat (gegen die CAs des Systems
oder `--ca-file`); im Offline-Modus nur ein Broker, der als lokal zählt ([config local-network](#config-local-network));
`''` schaltet die Türklingel aus. `--password` fragt nach dem Passwort des Brokers und legt es in den Tresor für
Zugangsdaten, nie in `config.yml`. `--test` sendet eine Test-Klingel (Anfrage-ID `req_00000000`) und sagt, ob der
Broker sie angenommen hat. Nur du stellst das ein: die Änderung der KI-Einstellungen und MCP `update_settings` lehnen
sie ab. Exit-Code 2 für eine Einstellung, die nicht passt (die Einstellung bleibt, wie sie war), 1, wenn die
Test-Klingel nicht angenommen wurde oder `config.yml` nicht gespeichert werden konnte.

| Option | Bedeutung |
|---|---|
| `--broker URL` | Der Broker: `mqtt://host:port` (dieser Computer) oder `mqtts://host:port`; `''` = aus. |
| `--username NAME` | Der Benutzername beim Broker. |
| `--prefix PRÄFIX` | Das Topic-Präfix (Standard `llmcf`; 1 bis 32 aus a-z, 0-9, `_` und `-`). |
| `--ca-file DATEI` | Die CA-Datei, gegen die das Zertifikat des Brokers geprüft wird (`''` = die CAs des Systems). |
| `--password` | Nach dem Passwort fragen und es in den Tresor für Zugangsdaten legen. |
| `--test` | Jetzt eine Test-Klingel senden. |

In der GUI: **Datei > Einstellungen…**, Seite **Build-Türklingel**: dieselben Felder, **Speichern** und
**Test-Klingel senden** (im Hintergrund).

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
immer gefragt), `start_recipe_build` (ein installiertes Rezept als Vorlage: seine Werte oder die Änderungen des Clients, mit der
Frage von `start_build`) und `export_pack` (in eine lokale Datei, immer gefragt); sie können kein Paket aus einer Datei
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

Installierte Targets landen im Targets-Ordner, Rezepte in `community/recipes/<ID>/` und Wissenspakete im Ordner
ihres Themas oder Targets, `community/knowledge/<Thema oder Target>/<ID>/` der Installation (ein Paket, das eine
frühere Version installiert hat, bleibt in `community/knowledge/<ID>/`). Ein Wissenspaket wird direkt nach der
Installation in deine Wissensbasis importiert oder, wenn die Wissensbasis ausgeschaltet ist, sobald sie eingeschaltet
wird; jeder Auszug trägt sein Thema oder Target. Ein Paket, das erstellt wurde, bevor Pakete ihr Thema nannten,
bekommt seine ID als Thema, was die Zusammenfassung sagt und die Installation bestätigt. Eine neuere Version eines
Pakets (installiert, nachdem du den Ordner der älteren entfernt hast) ersetzt die Auszüge der älteren Version. Ein
Target-Paket mit einem Wissens-Snapshot (`knowledge/initial_knowledge.json`) lässt ihn wie ein Wissenspaket prüfen und
unter seinem Target importieren, ebenfalls mit einem Snapshot der Wissensbasis vorher.

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
installiert ist, die Download-Adresse mit Größe und sha256, bei einem Wissenspaket, wohin es gehört (**Belongs to:**
das Thema oder Target, das sein Indexeintrag nennt), die Beschreibung und bei einem installierten Rezept
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
Installs to: ~/.llm-framework/targets/my-board
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
installierten Rezept im Community Hub füllt den Builder aus, einschließlich des festgelegten Commits des Modells; jedes
Feld bleibt deins (das Rezept ist eine Target-Vorlage und bleibt, solange sein Target gewählt ist), und
`build start --recipe ID` tut dasselbe auf der Kommandozeile.

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

### zoo uninstall

```
llm-cli zoo uninstall PACK_ID --kind target|recipe|knowledge
```

Deinstalliert ein installiertes Community-Paket. Zuerst listet es genau auf, was geht: den Ordner des Pakets mit
seiner Herkunftsangabe, seine Import-Notizen im Wissensordner und (bei einem Wissenspaket oder dem Wissens-Snapshot
eines Target-Pakets) wie viele Auszüge nur dieses Paket hält; Auszüge, die ein anderes Paket oder dein eigener Crawl
auch hält, bleiben. Dann fragt es, ob alles in den Papierkorb soll (wiederherstellbar mit `llm-cli trash restore`) oder
endgültig gelöscht wird, und „Genau N Einträge (Größe) deinstallieren?“ mit „Nein“ als Vorgabe. Nur an einem Terminal;
ohne eines wird nichts gelöscht. Ein Paket, dessen Seiten in der Wissensbasis sind, wird abgelehnt, solange die
Wissensbasis aus ist. Um eine neuere Version eines Pakets zu installieren, deinstalliere zuerst die alte. Exit-Code 1,
wenn nichts deinstalliert wurde oder ein Eintrag nicht entfernt werden konnte.

In der GUI: **Tools > Community Hub öffnen**, das installierte Paket wählen, **Deinstallieren...**. Eine KI darf nur
darum bitten (das optionale Tool `remove_pack`, verborgen, bis du es freigibst): Du siehst dieselbe Liste im
eigenen Fenster des Frameworks und entscheidest, und ihre Bitte verschiebt nur in den Papierkorb.

```bash
llm-cli zoo uninstall rk3588-docs --kind knowledge
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
| `--topic TEXT` / `--target TEXT` | Wissen: **eins davon ist Pflicht**: das Thema, zu dem seine Seiten gehören (z. B. `llama.cpp`), oder ein Target dieses Computers. Der Empfänger findet die Seiten darunter. Nicht für `target` und `recipe` (Exit-Code 2). |
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
llm-cli zoo export knowledge --author "<dein Name>" --id rk3588-notes --target Rockchip

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

### zoo maintainer

```
llm-cli zoo maintainer key NAME [--save DATEI]
llm-cli zoo maintainer review [ITEM] [--review-dir ORDNER] [--key NAME] [--trust community|verified] [--title T]
llm-cli zoo maintainer revoke KIND ID --reason TEXT [--version V | --sha256 S] [--key NAME]
```

Für den Maintainer des Community-Repositorys (`community_repo`), nicht für Benutzer. GitHub wird mit
deinem eigenen Login der GitHub CLI (`gh`) und git erreicht, beide installiert und angemeldet (`gh auth login`); das
Framework liest, speichert und zeigt nie einen Token. Kein KI-Client und kein Weg von Ditto erreicht diese Befehle. Jeder
Schritt wird ausgegeben und ins Audit-Log geschrieben; Strg+C bricht vor dem nächsten Schritt ab, bis zum letzten
Merge (nach dem Push schließt es auch den eigenen Pull Request der Veröffentlichung und löscht seinen Branch). Eine
Ablehnung sagt, was auf GitHub bis dahin geschehen ist, und nennt den nächsten Schritt.

- **`key NAME`** (einmalig): ein neuer Ed25519-Signaturschlüssel für den Index im Schlüsselbund des Betriebssystems
  (Dienst `lcf-community-index`). Ein Name, den es schon gibt, wird abgelehnt; ein Schlüssel wird nie ersetzt. Der
  Befehl gibt den Eintrag für `configs/community_keys.yml` aus (zwei Jahre gültig); `--save DATEI` schreibt ihn
  zusätzlich in eine Textdatei (eine vorhandene Datei wird abgelehnt, bevor ein Schlüssel entsteht). Wähle einen
  Namen, der nicht dein persönlicher Name ist, zum Beispiel `lcf-community-2026`: er steht im öffentlichen Eintrag.
- **`review`** ohne ITEM listet die offenen Pull Requests, die Pakete unter `contributions/<kind>/` hinzufügen, mit dem
  Stand der Pflichtprüfung `validate`, und die dort gemergten, noch nicht veröffentlichten Pakete
  (`main:contributions/...`).
- **`review ITEM`** (eine Pull-Request-Nummer wie `7` oder `#7`, oder `main:contributions/KIND/DATEI.zip`): holt die
  Pakete dieses Commits in den lokalen Klon im benutzereigenen Ordner, prüft jedes mit `zoo verify` und gibt jede Datei
  des Pakets aus (`pack.json`, `pack.sig`, jede Datei und jedes andere Element der Paket-Datei; versteckte Zeichen
  erscheinen als Escape). Ein Pull Request, der außer neuen Paketen unter `contributions/<kind>/` noch etwas ändert
  oder in einen anderen Branch als `main` geht, wird nicht veröffentlicht; die Prüfung nennt diese Änderung. Mit
  `--review-dir ORDNER` werden die Dateien stattdessen in diesen neuen oder leeren Ordner geschrieben, damit du sie
  dort liest. Dann fragt er „Publish?“ („Nein“ ist die Vorgabe; ohne Terminal wird nichts veröffentlicht). Ja mergt
  den Beitrag (nur wenn seine Prüfung bestanden ist und sein Stand der geprüfte Commit ist), verschiebt das Paket nach
  `packs/<kind>/`, trägt es mit der Raw-Adresse, dem Vertrauen (`--trust`, Vorgabe `community`; `verified` nur, wenn
  jemand anderes als der Autor das Paket nachgebaut hat) und dem Titel (`--title`, bei einem Beitrag mit einem Paket;
  Vorgabe die Paket-ID) in den Index ein, erhöht Version und Zeit des Index, signiert `index.json` mit dem Schlüssel
  (`--key`, Vorgabe der zuletzt benutzte), committet auf einem neuen Branch, pusht ihn, öffnet einen Pull Request,
  wartet auf die Pflichtprüfung und mergt ihn. Nichts wird nach `main` gepusht.
- **`revoke KIND ID --reason TEXT`**: zieht jede Version der ID, eine Version (`--version`) oder genau eine Datei
  (`--sha256`, aus dem Index) zurück, mit dem Grund, den alle sehen, nach der Frage „Revoke ...?“ („Nein“ ist die
  Vorgabe); der Rückzug kommt auf demselben Weg in den signierten Index. Der Eintrag bleibt, damit der Hub ihn als
  zurückgezogen zeigt.

In der GUI: **Tools > Community Hub öffnen > Maintainer**: **1. Signaturschlüssel anlegen...** (mit **Kopieren** und
**Speichern...**), **2. Prüfen und veröffentlichen: Beiträge laden**, **Auswahl prüfen** (Dateiliste und
schreibgeschützte Textansicht), **Vertrauen**, **Titel** und **Veröffentlichen...**, **3. Veröffentlichtes Pack
zurückziehen...**, der **Name des Signaturschlüssels** und **Abbrechen**.

```bash
llm-cli zoo maintainer key lcf-community-2026 --save lcf-community-2026.public-key.txt
llm-cli zoo maintainer review
llm-cli zoo maintainer review 7 --review-dir ../review-7
llm-cli zoo maintainer revoke target Rockchip --version 1.0.0 --reason "breaks the RK3588 build"
```

---

## trash

```
llm-cli trash [list]
llm-cli trash restore ID...
llm-cli trash empty ID... | --all | --due
```

Was Löschungen in den Papierkorb verschoben haben: gebaute Modelle (im `.trash` des
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

## models

```
llm-cli models suggestions [--json]
```

Listet die Modellvorschläge des Quellenkatalogs (`configs/project_sources.yml`, Abschnitte `tiny_models` und
`models`): genaue Hugging-Face-IDs, die kein Token brauchen, kleine Modelle zuerst, jeweils mit der Größe der Gewichte,
der empfohlenen Quantisierung, der Lizenz und dem Tag, an dem sie am Hub geprüft wurden. Ein gated Modell ist nie ein
Vorschlag. Die Modell-Liste des Builders bietet dieselben nach deinen zuletzt verwendeten Modellen an; `--model` von
`build start` nimmt auch jede andere Hugging-Face-ID oder ein lokales Modell. `--json` gibt die Liste für Skripte aus.
KI-Clients lesen sie als Ressource `lcf://models/suggestions`.

```
llm-cli models take-over
```

Frühere Versionen des Hugging-Face-Browsers der GUI speicherten ihre Downloads im Cache von
huggingface_hub im Modellordner (`models/models--<owner>--<name>/`); diese Modelle bleiben aufgeführt und nutzbar.
`take-over` macht, was **Alte Downloads übernehmen…** in **Tools > Hugging Face Model Browser...** macht, über dieselben
Aufrufe im Kern: Es listet jede Datei, die noch zu übernehmen ist (Modell, Datei, Größe), fragt „Copy exactly N files
(size)? [y/N]“ („Nein“ ist die Vorgabe) und kopiert jede Datei nach `models/hf/<owner>--<name>/<die ersten 12 Zeichen
des Commits>/`, geprüft gegen die Prüfsumme, mit der sie geladen wurde (bei einem Cache ohne Links gegen das, was
Hugging Face für diesen Commit führt; im Offline-Modus bleibt eine solche Datei, mit diesem Grund). Die eine
Speicherplatz-Regel kommt zuerst, und die Kopie hält während sie läuft eine eigene Reservierung. Strg+C hält zwischen
zwei Blöcken an (die bis dahin kopierten Dateien bleiben; die gerade kopierte wird nicht behalten). Jede Datei, die im
alten Cache bleibt, wird mit ihrem Grund ausgegeben. Danach zeigt es die vollständige Liste der alten Kopien, die
gehen können (der Cache-Ordner jedes Modells, dessen Dateien alle übernommen sind, und die Dateien des gemeinsamen
Speichers des Caches, die nur sie nutzen, mit vollem Pfad und Größe), fragt nach Papierkorb oder endgültig und löscht
erst nach „Move/Delete exactly N items (size) ...? [y/N]“ („Nein“ ist die Vorgabe), vorher protokolliert. Ohne Terminal
wird nichts kopiert und nichts gelöscht. Kein KI-Tool macht das.

```bash
llm-cli models take-over
```

```
llm-cli models list [--kind KIND] [--search TEXT] [--json]
```

Jedes Modell auf diesem Rechner und jeder Build daraus in einer Tabelle, wie sie der Reiter **Modelle** der GUI zeigt:
die Downloads im Modellordner und in den Ordnern, die die KI nutzen darf, Dittos Modelle, die Kopien des Builders von
Hugging-Face-Modellen, die Eingaben früherer Builds, die aufbewahrten F16-Zwischenstufen und die fertigen Builds. Jede
Zeile hat Name, Art, Format, Quantisierungstyp (bei einer GGUF-Datei ihr eigener Typ aus ihrem Kopf), Größe, Ort,
Datum, Target und Board, die Boards, auf denen ein Build getestet wurde, seine Tokenizer-Prüfung und den neuesten
Benchmark hier sowie den vollen Pfad. `--kind` ist eines von `download`, `ditto`, `copy`, `archive`, `input`, `f16`, `build`;
`--search` behält die Zeilen, deren Name, Typ, Target, Board oder Pfad den Text enthält; `--json` gibt die Zeilen für
Skripte aus.

```
llm-cli models delete NAME...
```

Löscht Zeilen der Liste, jede über ihr eigenes Löschen, wie **Löschen…** im Reiter Modelle: einen Build (sein Ordner
und sein goldenes ZIP), einen Download im Modellordner, eine aufbewahrte F16-Zwischenstufe. NAME ist der volle Pfad,
das Label oder der Name, den `models list` zeigt. Zuerst kommt die volle Liste (voller Pfad, Dateiname, Größe, was es
ist), dann Papierkorb oder endgültig, dann „Delete exactly N items? [y/N]“ („No“ ist die Vorgabe), vorher
protokolliert; ohne Terminal wird nichts gelöscht. Dittos Modelle (`ditto models list`), Dateien in deinen eigenen
Ordnern, die Kopien des Builders und die Eingaben früherer Builds werden hier nicht gelöscht. Kein KI-Werkzeug tut das.

```
llm-cli models bench NAME [--threads N] [--json]
llm-cli models bench-results [--json]
```

`bench` misst ein GGUF-Modell der Liste mit llama-bench auf diesem Rechner, wie **Build > Benchmark…**:
Prompt-Verarbeitung (pp512) und Erzeugung (tg128) in Tokens pro Sekunde, je drei Wiederholungen, mit Threads und
Backend. Dittos Katalogmodelle laufen nativ; jedes andere Modell nur in der Docker-Sandbox mit dem im Builder-Container
gebauten llama-bench (`ditto runtime install --placement container`), weil ein für ein Board gemachtes Modell, hier
gemessen, sagt, wie schnell dieser Rechner ist, nicht das Board. `--threads` setzt die Threads von llama-bench (0:
seine eigene Wahl). Strg+C stoppt ihn. Der Lauf wird aufbewahrt; `bench-results` listet die aufbewahrten Läufe, die
neuesten zuerst. Ein Board wird mit `device runtime verify DEVICE --bench-model PATH` gemessen.

```bash
llm-cli models list --kind build
llm-cli models bench ~/llmcf/models/hf/owner--model-GGUF/0123456789ab/model-q8_0.gguf --threads 4
llm-cli models bench-results
```

```
llm-cli models archive write OWNER/NAME [--revision REV] --to DATEI
llm-cli models archive take DATEI [--sha256 HEX] [--from SCHLÜSSELDATEI]...
llm-cli models archive list [--json]
llm-cli models archive delete OWNER/NAME@COMMIT...
```

Ein Modell-Archiv (`llmcf-model/1`, eine `.llmcfmodel`-Datei) bringt ein Hugging-Face-Modell in seinem genauen Commit
auf einen Computer ohne Netz, wie **Build > Modell-Archive…** (auch im Reiter Modelle). `write` schlägt auf einem Computer mit Netz den
Commit nach (`--revision`: ein Commit oder ein Branch; weggelassen der Standard-Branch von jetzt), holt genau die
Dateien, die ein Build davon bereitstellt (keine Kopien als GGUF, ONNX oder TensorFlow, keine Pickle-Gewichte neben
safetensors, keine versteckten Dateien: die Gewichte, `config.json`, `generation_config.json`, die Tokenizer-Dateien,
das Chat-Template und die Modellkarte), in die Kopie des Builders für diesen Commit und schreibt das Archiv: die
Dateien unverändert gespeichert, ein Manifest mit Lizenz und Gating des Modells und seiner Basismodelle und einem
sha256 je Datei und einem über das Ganze, signiert mit deinem eigenen Schlüssel (dem, mit dem auch Pakete signiert
werden; `zoo keys` gibt seine öffentliche Hälfte aus). Eine Lizenz mit Gating, eine einschränkende oder eine unbekannte
wird zuerst gezeigt und im Terminal gefragt („No“ ist die Vorgabe; ohne Terminal wird nichts geschrieben); ein Modell
mit Gating braucht dein gespeichertes Hugging-Face-Token, das sein Gating passiert hat. Es gibt den sha256 der Datei und
die Schlüssel-ID aus. Der Offline-Modus lehnt es ab.

`take` prüft das Archiv auf dem Computer ohne Netz in einem Quarantäne-Ordner, bevor irgendetwas es benutzt: den
sha256 der Datei, wenn du `--sha256` angibst, den Aufbau des Zips (kein Link, kein Ordner, kein verschlüsselter oder
nicht aufgeführter Eintrag, jede Größe die des Manifests), das Manifest, die Signatur (Pflicht; mit `--from` und den
öffentlichen Schlüsseln, die `zoo keys` auf dem schreibenden Computer ausgab, muss es dieser Schlüssel sein), dann den
sha256 jeder Datei. Bei jedem Problem bleibt nichts. Dann zeigt es Repository, Commit, Dateien, Größe, Signierenden und
die Lizenzen und fragt „Accept this licence and take the archive in? [y/N]“, nur im Terminal; es gibt keine Option,
die das überspringt. Die Dateien kommen nach `models/archives/<owner>--<name>/<commit>/`; derselbe Commit ein zweites Mal
wird abgelehnt. Ein Build nennt das Modell dann als `OWNER/NAME@COMMIT` (oder `--model OWNER/NAME --revision COMMIT`)
und nutzt diese Dateien ohne jede Anfrage an Hugging Face, auch online. `list` zeigt die eingelesenen Archive (`--json`
für Skripte), `delete` entfernt sie über die Löschfrage (Papierkorb oder endgültig, „No“ als Vorgabe). Kein Befehl lädt
ein Archiv je hoch, und kein KI-Werkzeug schreibt, liest ein oder löscht eines.

```bash
llm-cli models archive write HuggingFaceTB/SmolLM2-135M-Instruct --revision 12fd25f77366fa6b3b4b768ec3050bf629380bac --to ./smollm2
llm-cli models archive take ./smollm2.llmcfmodel --from ./online-keys.txt
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct@12fd25f77366fa6b3b4b768ec3050bf629380bac -t ARM64 -f gguf
```

---

## Bekannte Einschränkungen

- `build start --optimization` wirkt sich auf Rockchip-Builds nicht aus (die Skripte haben keine passende
  Einstellung); `--parallel` ist veraltet und hat keine Wirkung.
- Von den Überschreibungen aus `config repos` wirkt nur die für llama.cpp (woher das Image den gepinnten Commit
  holt); die übrigen erreichen die Builds als `<NAME>_REPO_OVERRIDE`, aber die Rockchip-Skripte lesen sie nicht.
- `deploy package --docker` ist experimentell.
- Einen CLI-Befehl, um Dittos Anbieter dauerhaft zu wählen, gibt es noch nicht; nutze die GUI, die `config.yml` oder
  `LLM_FRAMEWORK_AI_PROVIDER` (`ditto login` meldet an, wechselt den Anbieter aber nicht). Anmeldungen im Browser
  und bei Cloud-Plattformen (OAuth, AWS, Google Cloud, Azure) kommen später ([Roadmap](ROADMAP_DE.md)).
- Das Community-Repository ist festgelegt (`community_repo`: `Smilez1985/Edge-LLM-Knowledge-Base`), sein
  Beitrags-Workflow, sein signierter Index und der Maintainer-Schlüssel sind aber noch nicht veröffentlicht
  (`community_index_url` ist leer, `configs/community_keys.yml` enthält keinen Schlüssel): `zoo list` zeigt nur
  installierte Pakete, und `zoo install <ID>` bricht mit einer Meldung ab. Pakete lassen sich als Dateien teilen.
- Für Deep Ingest (Dokumentationsseiten zur Wissensbasis hinzufügen) gibt es keinen CLI-Befehl; es läuft im
  Modul-Assistenten der GUI (**Tools > Create Hardware Module...**).
