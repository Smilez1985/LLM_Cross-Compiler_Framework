Version 3.0.0 + unveröffentlichte Änderungen · Stand 2026-10-17

[English](CONTRIBUTING.md) · **Deutsch**

# Mitarbeit am LLM Cross-Compiler Framework

Danke, dass du hilfst. Dieser Leitfaden erklärt, wie du eine Entwicklungsumgebung einrichtest, dieselben Prüfungen
wie die CI ausführst, Zugangsdaten aus dem Repository heraushältst und Änderungen einreichst. Wenn du das Framework
nur benutzen willst, beginne mit der [README](README_DE.md). Wie das Framework aufgebaut ist, steht im
[Bauplan](docs/BLUEPRINT_DE.md).

Möglichkeiten mitzuhelfen:

- **Probleme melden:** Beschreibe, was du getan hast und was passiert ist, und hänge die Ausgabe von
  `llm-cli mojo --json` und das Build-Log an. Entferne vorher persönliche Daten (Hostnamen, Benutzernamen,
  Schlüssel).
- **Ein Hardware-Target hinzufügen oder verbessern** (siehe [Abschnitt 7](#7-ein-hardware-target-hinzufügen)).
- **Ein Target, ein Rezept oder Wissen teilen**, als signiertes Community-Paket (siehe
  [Beiträge zum Community-Zoo](#beiträge-zum-community-zoo)).
- **Fehler beheben und Tests schreiben.**
- **Die Dokumentation verbessern**, auf Englisch und Deutsch.

## Checkliste für einen Pull-Request

- [ ] Die Tests laufen durch: `QT_QPA_PLATFORM=offscreen python -m pytest`
- [ ] Lint- und Sicherheitsprüfungen laufen durch ([Abschnitt 5](#5-lint--und-sicherheitsprüfungen))
- [ ] Die Hooks des Secret-Scans sind eingeschaltet (`make hooks`), und der Scan ist sauber ([Abschnitt 6](#6-secret-scan-und-git-hooks))
- [ ] Neues Verhalten ist durch Tests in `tests/` abgedeckt
- [ ] Ein neuer CLI-Befehl oder eine neue Option hat ihren Weg mit der Maus in der GUI und einen Eintrag in
  `CLI_PATHS`, oder einen Grund in `MACHINE_ONLY` ([Abschnitt 8](#8-regeln-für-den-code))
- [ ] Eintrag unter `[Unreleased]` in `CHANGELOG.md` und `CHANGELOG_DE.md`
- [ ] Dokumentation auf Englisch und Deutsch aktualisiert
- [ ] Keine Secrets, kein persönlicher Zustand, keine Binärdateien und keine Modelldateien im Diff

---

## 1. Entwicklungsumgebung

Den Code holen:

```bash
git clone https://github.com/Smilez1985/LLM_Cross-Compiler_Framework.git
cd LLM_Cross-Compiler_Framework
```

Bis Release 3.0.0 hieß das Repository `llm_conversion_framework`. GitHub leitet die alte Adresse weiter, ein
vorhandener Klon funktioniert also weiter; auf die neue Adresse umstellen:
`git remote set-url origin https://github.com/Smilez1985/LLM_Cross-Compiler_Framework.git`.

Du brauchst **Python 3.11 bis 3.14**, Git und für echte Builds Docker. PySide6 für die GUI ist das optionale Extra
`gui` (`requirements-gui.txt`); Kommandozeile, MCP-Server und Kern laufen ohne. Auf einem Linux-Rechner ohne Desktop
brauchen die Qt-Tests der GUI ein paar Systembibliotheken; die CI installiert
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0`.

### Variante A: uv

Installiert genau die festgelegten Versionen mit Hashes aus den Requirements-Dateien:

```bash
uv venv --python 3.13
source .venv/bin/activate              # Windows: .venv\Scripts\activate
uv pip install -r requirements-gui.txt -r requirements-dev.txt   # ohne GUI: requirements.txt

python -m orchestrator.cli --help      # Kommandozeile
python -m orchestrator.main            # GUI (orchestrator/gui/app.py; ohne PySide6 sagt sie, wie du es installierst)
```

### Variante B: Projektumgebung von uv

Installiert genau die Versionen, die `uv.lock` festlegt:

```bash
uv sync --extra gui                    # Laufzeit (mit GUI) und Entwicklungsabhängigkeiten, die Befehle llm-cli, llmcf und llm-gui
uv run llm-cli --help
uv run llm-gui
```

### Eine neue Umgebung prüfen

`uv run llmcf mojo` prüft, was ein Build auf diesem Rechner braucht (Python 3.11 bis 3.14, Docker und buildx, freien
Speicherplatz, Targets, den State-Ordner, Secrets und Schlüsselbund, Tool-Berechtigungen), und sagt zu jedem
Problem, was zu tun ist. `make test` führt danach die Tests aus ([Abschnitt 3](#3-tests-ausführen)), und
`scripts/mcp_client_smoke.sh` ist der Smoke-Test mit einem echten KI-Client
([weiter unten](#der-smoke-test-mit-einem-echten-client)). Die CI testet die Build-Images mit Smoke-Tests. Ein
eigenes Schnellprüf-Skript gibt es nicht.

### Entwicklungsumgebung von der echten trennen

- `LLM_FRAMEWORK_CONFIG_DIR=/ein/ordner` lässt das Framework diesen Ordner für seine Einstellungen nutzen. Ohne die
  Variable nutzt ein Checkout seinen eigenen Ordner `configs/` und schreibt hinein (`config.yml` und die Ordner
  `environments/`, `templates/`, `backups/`).
- `LLM_FRAMEWORK_HOME=~/.llm-framework-dev` trennt Secrets, Tool-Berechtigungen, Audit-Log, SSH-Schlüssel, bekannte
  Hosts, Chatverlauf und den lokalen Modellserver deiner Entwicklungsumgebung von deinem echten `~/.llm-framework`.
- `LLM_SECRET_STORAGE=ephemeral` lässt die CLI auch auf Rechnern ohne Schlüsselbund des Systems starten (etwa in
  Containern oder in der CI): Secrets bleiben nur für diesen Lauf im Arbeitsspeicher. Ein Schlüsselbund-Backend, das
  nichts behält, etwa `keyring.backends.null`, wird abgelehnt. `LLM_FRAMEWORK_HOME` darf nicht in eine
  Git-Arbeitskopie zeigen, weil der Secrets-Tresor einen Ort darin ablehnt.

---

## 2. Abhängigkeiten

- `pyproject.toml` legt die erlaubten Versionen fest (`[project]`, die Entwicklungswerkzeuge in
  `[dependency-groups]`), `uv.lock` fixiert sie (die Lock-Datei von [uv](https://docs.astral.sh/uv/)).
- `requirements.txt` (Laufzeit), `requirements-gui.txt` (Laufzeit plus das optionale Extra `gui`, PySide6) und
  `requirements-dev.txt` (Entwicklungswerkzeuge) sind Exporte von `uv.lock` mit Hashes. Ihre ersten Zeilen nennen den
  Befehl, der sie erzeugt hat. Die Installer und die CI installieren daraus: die Desktop-Installer
  `requirements-gui.txt`, `install.sh --headless` und das Framework-Image ohne `--gui` `requirements.txt`.
  **Bearbeite sie nie von Hand.**
- `anthropic`, `openai`, `fastembed` und `mcp` sind absichtlich auf exakte Versionen festgelegt: KI-Werkzeuge sind
  ein Ziel von Angriffen auf die Lieferkette (siehe die Kommentare in `pyproject.toml`). Wenn du eine solche
  Festlegung änderst, begründe es im Pull-Request.

Nach dem Hinzufügen, Entfernen oder Aktualisieren einer Abhängigkeit:

```bash
uv lock                                         # uv.lock aktualisieren (andere Festlegungen bleiben; ein Paket: --upgrade-package NAME)
uv lock --check                                 # pyproject.toml und uv.lock passen zusammen
uv export --frozen --format requirements-txt --no-dev --no-emit-project -o requirements.txt
uv export --frozen --format requirements-txt --no-dev --extra gui --no-emit-project -o requirements-gui.txt
uv export --frozen --format requirements-txt --only-group dev --no-emit-project -o requirements-dev.txt
```

Danach pip-audit ([Abschnitt 5](#5-lint--und-sicherheitsprüfungen)) und die Tests ausführen und `pyproject.toml`,
`uv.lock` und die drei Requirements-Dateien zusammen committen. Der CI-Job "Lock files" führt dieselben Befehle aus und
schlägt fehl, wenn eine Datei abweicht.

Dependabot öffnet wöchentlich Pull-Requests für Python-Pakete (`pyproject.toml` und `uv.lock`), GitHub Actions und
die Dockerfiles der Targets. Es ändert nur `uv.lock`: Führe die drei Export-Befehle auf seinem Branch aus und pushe
die Requirements-Dateien mit.

---

## 3. Tests ausführen

```bash
QT_QPA_PLATFORM=offscreen python -m pytest      # in der Projektumgebung von uv: QT_QPA_PLATFORM=offscreen uv run pytest
```

Windows (PowerShell):

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
```

- `tests/conftest.py` schottet jeden Test ab: ein temporäres Home- und Arbeitsverzeichnis,
  `LLM_FRAMEWORK_CONFIG_DIR` zeigt auf einen temporären Ordner, alle Variablen `LLM_*` und `FRAMEWORK_*` sowie die
  Anbieterschlüssel werden entfernt (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` und jede weitere
  Variable, die ein Verfahren in `configs/llm_providers.yml` unter `env` nennt). Tests dürfen nicht
  von deinem echten `~/.llm-framework`, deinem Schlüsselbund, echten API-Schlüsseln oder dem Netzwerk abhängen;
  nutze die Fixture `memory_keyring` und nachgebaute Server.
- `tests/test_versioning.py` prüft, dass `orchestrator/__init__.py`, `pyproject.toml` und der neueste
  veröffentlichte Changelog-Eintrag dieselbe Version tragen.
- `tests/test_foundation.py` und `tests/test_review_capabilities_paths.py` prüfen, dass die Rockchip-Capabilities
  in `target.yml` zu `modules/build.sh`, `modules/rkllm_module.sh`, `modules/rknn_module.sh` und
  `scripts/export_rkllm.py` passen.

### Die Matrix Python 3.11 bis 3.14

Die CI führt die Tests unter Ubuntu mit Python 3.11, 3.12, 3.13 und 3.14 aus und unter Windows mit Python 3.13
(`.github/workflows/ci.yml`). Dieselbe Matrix lokal mit uv (es lädt fehlende Python-Versionen herunter):

```bash
for v in 3.11 3.12 3.13 3.14; do
  uv venv --python "$v" ".venv-$v"
  uv pip install --python ".venv-$v" -r requirements.txt -r requirements-dev.txt
  QT_QPA_PLATFORM=offscreen ".venv-$v/bin/python" -m pytest -q || echo "FEHLER unter Python $v"
done
```

Eine Änderung, die nur unter einer Python-Version funktioniert, ist nicht fertig. Typische Unterschiede sind das
Verhalten der Standardbibliothek (etwa Filter beim Entpacken von tar-Archiven) und Wheels, die für die neueste
Version noch fehlen.

### Der Smoke-Test mit einem echten Client

Die Unit-Tests steuern den MCP-Server mit dem eigenen Client des SDK. `scripts/mcp_client_smoke.sh` (POSIX sh) prüft
den Server mit einem echten, Claude Code: als manuelle Prüfung vor einem Release oder als optionaler CI-Job auf
einem Runner, auf dem der claude-Befehl installiert und angemeldet ist:

```bash
LCF_SMOKE_PYTHON=.venv/bin/python scripts/mcp_client_smoke.sh
```

- Er baut eine Wegwerf-Umgebung (Home, Hugging-Face-Home, XDG-Ordner, `LLM_FRAMEWORK_HOME`,
  `LLM_SECRET_STORAGE=ephemeral`, das Null-Keyring-Backend, `QT_QPA_PLATFORM=offscreen`, der Hugging Face Hub
  offline) und schreibt eine MCP-Konfiguration, die `python -P -m orchestrator.cli -q mcp serve` im Repository unter
  `env -i` startet, sodass der Server nur diese Variablen bekommt und nichts aus deiner Umgebung.
- Er startet `claude -p` mit `--mcp-config <Datei> --strict-mcp-config --allowedTools mcp__lcf --tools ""
  --output-format json --verbose --no-session-persistence` und vier vorgegebenen Prompts, jeden in einer eigenen
  Sitzung.
- `scripts/mcp_smoke_check.py` prüft danach die Ergebnisse aus der isolierten Audit-Datenbank, nie aus der Antwort
  des Modells: die Client-Zeile, `get_status` lief, ein unbekanntes Argument wurde mit Vorschlag abgelehnt, der
  erklärte Alias `quant` wurde bei einem Aufruf, der lief, und bei einem abgelehnten festgehalten, und ein Aufruf,
  der eine Bestätigung braucht (`update_settings`), lief ohne Antwort nicht: Er wurde gefragt, oder er endete mangels
  Antwort; eine Ablehnung vor jeder Frage (etwa eine verweigerte Berechtigung) lässt diese Prüfung scheitern, weil sie
  nichts prüft. Ob der Client den Reparaturhinweis im Ergebnis und in der Ablehnung gezeigt bekam, liest er aus den
  Tool-Ergebnissen in der JSON-Ausgabe des Clients.
- Exit-Codes: 0 alle Prüfungen bestanden, 1 eine Prüfung fehlgeschlagen, 2 ein Problem der Umgebung (etwa ein Python
  ohne die Abhängigkeiten des Frameworks), 77 übersprungen, weil der claude-Befehl fehlt oder nicht angemeldet ist.
- Der claude-Befehl meldet sich selbst an: Das Skript liest, kopiert oder übergibt nie Claude-Zugangsdaten oder
  Tokens. Der Cache des Clients, in dem er das stderr des Servers protokolliert, und seine temporären Dateien
  (`TMPDIR`) liegen ebenfalls im temporären Ordner, der am Ende gelöscht wird (`LCF_SMOKE_KEEP=1` behält ihn); der
  claude-Befehl aktualisiert seine eigene Einstellungsdatei wie bei jedem Lauf. Docker wird nicht gebraucht.
  `LCF_SMOKE_CLAUDE` nennt einen anderen claude-Befehl, `LCF_SMOKE_TIMEOUT` die Sekunden je Prompt (300).
- `tests/test_mcp_smoke_check.py` testet die Prüfung mit Fixture-Datenbanken, die Isolation der Server-Umgebung, die
  Regeln fürs Überspringen und das Skript von Anfang bis Ende mit einem Ersatz-Client.

---

## 4. Das lokale Modell und die Evaluation

Tests laden nie Modelle herunter, und sie holen oder bauen nie llama.cpp: Eine Fixture in `tests/conftest.py` lehnt
ein Holen des llama.cpp-Quellcodes über das Netzwerk ab, also nutzt ein Test der Installation Attrappen für
Toolchain, Executor und Prüfung (siehe `tests/test_runtime_selfbuilt.py`). Um Dittos Offline-Modus von Hand
auszuprobieren, nutze ein eigenes `LLM_FRAMEWORK_HOME` (Modelle landen im Ordner `models/ditto/` der Installation,
den git ignoriert):

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install      # baut llama-server aus dem festgelegten Commit (einige Minuten für den CPU-Build)
llm-cli ditto models download small
llm-cli ditto runtime start --model small
llm-cli ditto ask "Welche Formate kann das Rockchip-Target bauen?"
llm-cli ditto runtime stop
```

Wenn du Dittos Prompts, ein Modell für strukturierte Antworten oder eine Tool-Beschreibung änderst, führe die
Evaluation vor und nach der Änderung aus und vergleiche die Berichte:

```bash
llm-cli ditto eval --tier small --tier small-q2 --json vorher.json
llm-cli ditto eval --provider ollama --model qwen3.5:4b --kind chat
```

Die Fälle stehen in `evals/ditto/cases.yml` und werden nach Regeln bewertet; gebaut wird nichts. Cloud-Anbieter
berechnen jede Anfrage (etwa 20 je Lauf und Wiederholung). Neue Fälle sind Daten: Ergänze sie in der Falldatei,
nicht im Code.

Wenn du änderst, wie die Wissensbasis Text liest oder speichert (das Zerlegen in Abschnitte, das Auslesen von HTML,
die Reinigung von Text von außen), führe die Retrieval-Evaluation vor und nach der Änderung aus (ohne KI-Modell; die
lokalen Embedding- und Reranking-Modelle werden einmal heruntergeladen) und ihre Gruppe mit versteckten Trägern, die
scheitert, wenn eine versteckte Nutzlast in die gespeicherten Auszüge gelangt:

```bash
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json vorher.json
llm-cli ditto eval --kind retrieval --cases evals/retrieval/carriers.yml
```

### Den llama.cpp-Pin anheben

Jedes selbst gebaute llama.cpp stammt aus einem Commit: `llama_cpp.source` in `configs/llama_runtime.yml` (wo er
steht und was aus ihm folgt: [SSOT_ARCHITECTURE_DE.md](configs/SSOT_ARCHITECTURE_DE.md#llama_runtimeyml)). Ein
Commit legt den Inhalt fest; ein Tag lässt sich verschieben. Eine Anhebung läuft so:

1. Den Commit des neuen Upstream-Tags nachschlagen: `python scripts/llama_cpp_pin.py show b<N>`.
2. In `configs/llama_runtime.yml` `llama_cpp.source.commit`, `.tag` und `.build_number` ändern, dazu
   `llama_cpp.build` (dieselbe Bezeichnung). Die offiziellen vorgebauten Archive dieses Releases tragen die
   Bezeichnung aus `build`: Ihre Namen, Größen und SHA256-Werte neu festlegen, wie es der Kommentar am Anfang der
   Datei beschreibt.
3. Die Stellen ändern, die den Katalog nicht lesen können: `ARG LLAMA_CPP_TAG` und `ARG LLAMA_CPP_COMMIT` in
   `targets/Rockchip/dockerfile`, `targets/_template/Dockerfile` und `targets/_template/Dockerfile.gpu` (die Vorgaben
   für Image-Builds außerhalb des Frameworks; das Framework übergibt den Pin selbst), `docker.build_args.LLAMA_CPP_TAG`
   und `LLAMA_CPP_COMMIT` in `targets/*/target.yml` und `inference_backends.llama_cpp.commit` in
   `configs/project_sources.yml` (danach `python ../scripts/sync_ssot.py --write` in `configs/`). Die Einstellung
   `llama_cpp_version` folgt dem Katalog von selbst.
4. Prüfen, dass der Commit von Upstream stammt: `python scripts/llama_cpp_pin.py check`. Es ruft
   `git ls-remote https://github.com/ggml-org/llama.cpp.git refs/tags/b<N>` auf, das genau den neuen Commit ausgeben
   muss (GitHub liefert Commits, die in irgendeinen Fork gepusht wurden, auch über die Upstream-URL aus, der Commit
   allein beweist also nicht seine Herkunft), und listet jede Stelle auf, die die Version nennt: abgeleitete und
   Referenz-Stellen müssen übereinstimmen, die Legacy-Vorgaben in `modules/source_module.sh` werden nur gemeldet.
5. Die Optionen, die etwas herunterladen können, gegen den neuen Baum prüfen. `DOWNLOAD_OPTIONS_OFF` und `NEVER_SET`
   in `orchestrator/runtime/build/recipe.py` nennen jede Option mit Datei und Zeilen; den neuen Baum nach
   `FetchContent_Declare`, `ExternalProject_Add` und `file(DOWNLOAD` durchsuchen und jede neue ergänzen. Ebenso die
   Regeln für CUDA-Architekturen (`ggml/src/ggml-cuda/CMakeLists.txt`) und welche Ziele `tools/CMakeLists.txt` baut.
   Die Target-Dockerfiles wiederholen beide Listen in ihrem llama.cpp-Bauschritt (die zuletzt gesetzten Optionen und
   die, die sie ablehnen); `tests/test_target_dockerfiles.py` schlägt fehl, bis sie wieder zum Rezept passen.
6. Die Golden-Pläne neu erzeugen und den Unterschied durchsehen:
   `LLM_FRAMEWORK_UPDATE_GOLDEN=1 QT_QPA_PLATFORM=offscreen python -m pytest tests/test_build_recipe.py`. Ohne
   Änderung am Rezept ändern sich nur der Commit, die Build-Nummer und die Build-IDs.
7. Die ganze Testsuite ausführen, einen Changelog-Eintrag in beiden Sprachen ergänzen und erst mergen, wenn alle
   Prüfungen grün sind.

---

## 5. Lint- und Sicherheitsprüfungen

Das sind die Prüfungen aus `.github/workflows/`. Außer hadolint kommen alle Werkzeuge mit `requirements-dev.txt`.

**Ruff** (`ci.yml`): nur Syntaxfehler und undefinierte Namen. Regeln: `E9` (Syntax- und Ein-/Ausgabefehler), `F63`
(ungültige Vergleiche und Asserts), `F7` (Anweisungen an falscher Stelle, etwa `return` außerhalb einer Funktion),
`F82` (undefinierte Namen).

```bash
ruff check --select E9,F63,F7,F82 orchestrator scripts tests targets
```

**ShellCheck** (`ci.yml`): Fehler in allen versionierten Shell-Skripten. `targets/_template` wird übersprungen, weil
es Platzhalter enthält, die der Modulgenerator ausfüllt.

```bash
git ls-files -z '*.sh' ':!:targets/_template/*' | xargs -0 shellcheck --severity=error
```

**actionlint** (`ci.yml`): prüft die Workflow-Dateien.

```bash
actionlint
```

**Bandit** (`security.yml`): statische Sicherheitsanalyse. Die CI scheitert an Funden mit hoher Schwere und hoher
Sicherheit und gibt zusätzlich einen Bericht ab mittlerer Schwere zur Information aus.

```bash
bandit -r orchestrator scripts targets --severity-level high --confidence-level high
bandit -r orchestrator scripts targets --severity-level medium --exit-zero
```

**pip-audit** (`security.yml`): bekannte Schwachstellen. Lokal prüfst du deine Umgebung und die
Entwicklungswerkzeuge:

```bash
pip-audit
pip-audit --requirement requirements-dev.txt
```

Die CI prüft außerdem jede Festlegung in `requirements-gui.txt` (alles aus `requirements.txt` plus PySide6), auch
die nur für Windows oder bestimmte Versionen; das genaue Skript steht in `security.yml`.

**Tests ohne das GUI-Extra** (`ci.yml`, Job `test-headless`): installiert `requirements.txt` ohne PySide6 und führt
die Qt-freien Prüfungen aus (`tests/test_qt_free_core.py`: die Texte des Kerns, `llmcf --help`, `llmcf mcp serve` und
die schlichte Meldung von `llm-gui`) und die ganze Testsuite, deren GUI-Tests sich selbst überspringen
(`pytest.importorskip("PySide6")` oder die Fixture `qapp`). Ein Testmodul, das `orchestrator.gui` oben importiert,
überspringt sich ohne Qt zuerst. Lokal:
`python -m venv .venv-headless && .venv-headless/bin/pip install -r requirements.txt -r requirements-dev.txt`.

**Secret-Scan** (`security.yml`): jeder Commit der ganzen Historie, siehe [Abschnitt 6](#6-secret-scan-und-git-hooks).

**hadolint** (`docker.yml`): prüft `targets/Rockchip/dockerfile`, `targets/_template/Dockerfile` und
`targets/_template/Dockerfile.gpu` und scheitert an Fehlern. Lokal mit installiertem hadolint oder mit Docker:

```bash
hadolint --failure-threshold error targets/Rockchip/dockerfile
docker run --rm -i hadolint/hadolint hadolint --failure-threshold error - < targets/Rockchip/dockerfile
```

**Image-Build** (`docker.yml`): Wenn sich `targets/**`, `entrypoint.sh`, `orchestrator/utils/build_context.py` oder
`orchestrator/Core/builder.py` ändern, baut die CI das Rockchip-Image und führt Rauchtests aus (Werkzeugketten,
Entrypoint, abgeschotteter Container). So baust du das Image lokal aus demselben Kontext, den das Framework nutzt:

```bash
python3 -m orchestrator.utils.build_context targets/Rockchip /tmp/rockchip-context
docker build -t llm-framework/rockchip:dev /tmp/rockchip-context
```

GitHub Actions sind per Commit-SHA festgelegt, mit der Version als Kommentar. Halte das bei neuen Actions so.

**Framework-Container** (`framework-container.yml`): Wenn sich `Docker Setup/`, `install.sh`, ein
Requirements-Export, der Builder oder das Makefile ändern, baut die CI das Image des Frameworks mit `scripts/build_container_image.sh`
(ein lokales Tag, nie hochgeladen), prüft sein Dockerfile mit einem Linter und führt Rauchtests aus. `make build`,
`make up`, `make down`, `make mojo` und `make audit` funktionieren damit lokal
([docs/Container_Setup_DE.md](docs/Container_Setup_DE.md)). Kein Workflow, kein Makefile-Ziel, kein Skript und kein
Dokument lädt ein Framework-Image hoch oder herunter (`tests/test_container_setup.py`). `make test` (uv),
`make hooks` und `make secret-scan` sind unverändert.

---

## 6. Secret-Scan und Git-Hooks

API-Schlüssel, Tokens, private Schlüssel und persönlicher Zustand dürfen nie committet werden.
`scripts/secret_scan.py` (nur Standardbibliothek) findet sie; Git-Hooks und die CI führen ihn für dich aus.

**Schalte die Hooks einmal je Klon ein:**

```bash
make hooks          # dasselbe wie: git config core.hooksPath .githooks
```

- Der Hook **pre-commit** durchsucht, was der nächste Commit enthalten würde (`--staged`), und lehnt den Commit ab,
  wenn er etwas findet.
- Der Hook **pre-push** durchsucht die Commits, die ein Push veröffentlichen würde (`--push`); er fängt also auch
  Commits ab, die mit `git commit --no-verify` entstanden sind.
- Wer stattdessen das Werkzeug pre-commit nutzt (https://pre-commit.com):
  `pre-commit install -t pre-commit -t pre-push` (`.pre-commit-config.yaml`). Seine pre-push-Stufe führt dieselbe
  Prüfung `--push` aus; pre-commit übergibt bei einem Push mehrerer Branches nur den ersten, pushe sie also einzeln
  oder nutze die Git-Hooks oben.
- Der CI-Job `secret-scan` in `security.yml` durchsucht jeden Commit der ganzen Historie.

**Von Hand ausführen:**

```bash
python3 scripts/secret_scan.py               # versionierte Dateien des Arbeitsverzeichnisses
python3 scripts/secret_scan.py --staged      # was der nächste Commit enthalten würde
python3 scripts/secret_scan.py --history     # jeder Commit (dasselbe wie: make secret-scan)
python3 scripts/secret_scan.py pfad/zur/datei  # nur diese Dateien
```

Exit-Code 0 heißt sauber, 1 Funde, 2 ein Bedienungs- oder Git-Fehler. Funde werden geschwärzt ausgegeben.

**Wonach er sucht:** Schlüssel und Tokens verbreiteter Anbieter (Anthropic, OpenAI, OpenRouter, Hugging Face,
GitHub, GitLab, AWS, Google, Groq, xAI und andere), JSON Web Tokens, private Schlüssel, Zertifikate und URLs mit
Passwort. Manche Dateien werden schon am Namen abgelehnt, egal was drinsteht, etwa `.env`, `credentials.json`,
`secrets.store`, `audit.db`, `known_hosts`, `id_ed25519`, `chat_history.json`, `tool_policy.json` und `*.pem`.
Offensichtliche Platzhalter (`EXAMPLE`, `REDACTED`, `xxxxxxxx`, `<...>`, Beispiel-Domains) werden ignoriert. Eine
Zeile mit `secret-scan: allow` wird übersprungen; nutze das nur für dokumentierte Fantasiewerte. Beides gilt nicht
für die exakten Token-Formate unten.

**Unechte Tokens in Tests und Dokumentation:** Der Push-Schutz von GitHub lehnt einen Push ab, der einen Wert im
exakten Token-Format eines Anbieters enthält, so unecht der Wert auch aussieht: `hf_` gefolgt von 34 Buchstaben
(auch das Alphabet), `ghp_` gefolgt von 36 Buchstaben und Ziffern, ein vollständiger Schlüssel
`sk-ant-api03-...AA` und so weiter. Die Markierung `secret-scan: allow` kennt GitHub nicht. Deshalb meldet der Scan
solche Werte auch dann, wenn sie wie Platzhalter aussehen, und die Markierung unterdrückt diese Funde nicht; die
Meldung sagt, dass GitHub den Push blockieren wird. Das gilt in jedem Modus: Auch `--push` meldet ältere Commits,
die einen solchen Wert noch enthalten, denn ein Push dieser Commits würde ebenfalls abgelehnt, und ebenso
`--history`, ausgenommen die unten gelisteten unechten Testwerte. Es gilt auch für Textdateien über 5 MB, die die
übrigen Regeln überspringen. Die Formate stehen in `EXACT_FORMATS` in `orchestrator/security/secret_patterns.py`
(Hugging Face, GitHub, AWS, Slack, GitLab, Stripe, PyPI, OpenAI, Anthropic, OpenRouter, Groq, xAI, Perplexity,
Replicate). Baue einen solchen Testwert stattdessen zur Laufzeit zusammen, damit die Datei ihn nie am Stück enthält:

```python
TOKEN = "hf_" "abcdefghijklmnopqrstuvwxyzABCDEFGH"  # benachbarte String-Literale: zur Laufzeit ein String
TOKEN = "hf_" + "x" * 34                            # oder Verkettung
```

Oder nimm einen Wert, der nicht zum exakten Format passt: eine andere Länge oder ein Zeichen, das das Format nicht
erlaubt (eine Ziffer in einem Hugging-Face-Token). In der Dokumentation kürze den Wert (`hf_...`).

**Unechte Testwerte in alten Commits:** Ein bereits veröffentlichter Commit lässt sich nicht ändern, ohne die
Historie umzuschreiben. `.secret-scan-allow` im Wurzelverzeichnis des Repositorys listet die dokumentierten unechten
Testwerte, die alte Commits noch enthalten, damit `--history` (der CI-Job, `make secret-scan`) durchläuft. Derzeit
sind es zwei, aus alten Fassungen von `tests/test_security.py` und `tests/test_cli_builds.py`. Jede Zeile nennt
genau einen Wert über den sha256-Hexwert dieses exakten Werts, nie über den Wert selbst (das wäre ein Literal im
exakten Format, das GitHub blockiert), danach zwei Leerzeichen und eine Notiz: die ersten und letzten 4 Zeichen, die
Länge, warum es ein unechter Testwert ist und wo er steht. Berechne den Hexwert, ohne den Wert in deiner
Shell-Historie zu hinterlassen:

```bash
python3 -c "import getpass, hashlib; print(hashlib.sha256(getpass.getpass('value: ').encode()).hexdigest())"
```

Die Liste gilt nur für `--history` und nur für den gelisteten Wert selbst: Jeder andere Modus meldet ihn weiterhin,
die Git-Hooks halten einen neuen Commit damit also auf. Ein gelisteter Wert verdeckt nichts anderes: Ein längerer
Wert eines anderen Formats, der ihn enthält, wird weiterhin gemeldet, und die übrigen Regeln (Anbieterschlüssel,
lokale Muster, Dateinamen) lassen nie einen Fund wegen der Liste weg, ein echt aussehender Schlüssel wird also auch
dann gemeldet, wenn sein Hexwert gelistet ist. Sie lesen die Zeile außerdem ohne den gelisteten Wert, damit ein Wert
neben einem unechten Testwert nicht als Platzhalter durchgeht. Eine fehlerhafte Zeile stoppt den Scan in jedem Modus
(Exit-Code 2), damit ein Tippfehler nicht mehr erlaubt; ein Steuer-, Format- oder Zeilentrennzeichen (ein einzelnes
Carriage Return, eine Byte Order Mark, ein Leerzeichen der Breite null) macht eine Zeile ebenfalls fehlerhaft, damit
ein Kommentar keinen Eintrag verstecken kann. Trage nie echte Zugangsdaten ein, so alt sie auch sind: Tausche sie aus
und schreibe die Historie um ([Abschnitt 12](#12-wenn-ein-secret-offengelegt-wurde)).

**Persönliche Zeichenfolgen** wie deine E-Mail-Adresse, dein Benutzername oder Hostnamen gehören ebenfalls nicht ins
Repository, können aber nicht in einer öffentlichen Musterliste stehen. Trage sie in `.git/info/secret-patterns`
ein; git committet diese Datei nie:

```text
# eine Zeichenfolge je Zeile (ohne Beachtung der Groß- und Kleinschreibung) oder ein regulärer Ausdruck nach "re:"
mein-benutzername
mein-laptop-hostname
re:\bmein\.name@[a-z.]+\b
```

In einem Git-Worktree gilt die Datei im `.git/info/` des Haupt-Repositorys. Alternativ zeigt
`SECRET_SCAN_PATTERNS_FILE` auf eine Datei an einem anderen Ort. Lokale Muster erscheinen nie in der Ausgabe.

**Findet der Scan etwas** in einem Commit, den du noch nicht gepusht hast: Entferne den Wert, lege ihn stattdessen
im Secrets-Speicher oder in einer Umgebungsvariablen ab und ändere den Commit (`--amend`). War der Wert ein echter
Schlüssel, behandle ihn trotzdem als offengelegt und folge [Abschnitt 12](#12-wenn-ein-secret-offengelegt-wurde).

---

## 7. Ein Hardware-Target hinzufügen

Der vollständige Leitfaden ist der [Leitfaden für Modulentwickler](docs/Module_Developer_Guide_DE.md). Kurz:

1. **Das Board untersuchen:** `llm-cli scan --device <Adresse> --user <Benutzer>` führt die Probe per SSH auf dem
   Gerät aus (oder du führst `scripts/hardware_probe.py` oder `scripts/hardware_probe.sh` von Hand auf dem Gerät aus);
   sie schreibt `target_hardware_config.txt`.
2. **Das Target anlegen:** in der GUI unter **Tools > Create Hardware Module...**, mit `llm-cli module create` oder
   mit Ditto: `llm-cli module generate-ai target_hardware_config.txt`. Der Generator prüft Namen, Images, Pakete,
   Flags und Shell-Code, schreibt nur unterhalb von `targets/` und überschreibt nie ein bestehendes Target, und der
   fertige Ordner muss dieselben Prüfungen bestehen wie ein Community-Paket.
3. **Die Capabilities vervollständigen:** Der Generator schreibt einen Block `capabilities:` mit der
   Unterstützungsstufe `community`, dem Modulnamen als einzigem Board und GGUF mit den Quantisierungstypen aus den
   `case`-Zweigen der Quantisierungslogik (ohne solche Zweige sechs gängige GGUF-Typen). Ergänze deine echten Boards,
   Formate, Quantisierungen je Board, Aliasse, Vorgaben und den Bedarf an Kalibrierdaten in
   `targets/<Name>/target.yml` (Schema in `orchestrator/schemas/targets.py`; der Rockchip-Block ist das Vorbild). GUI, CLI, Ditto und der MCP-Server bieten genau das an und prüfen genau das, was
   dort steht.
4. **Dockerfile und Build-Skript:** Lege jede Version fest (Tags, Commits, Hashes, wie `RKLLM_WHEEL_SHA256` im
   Rockchip-Dockerfile). Die Umwandlung muss in der Sandbox funktionieren: als Benutzer ohne root-Rechte, ohne
   Netzwerk, mit schreibgeschütztem Image-Dateisystem und nur den eigenen Ordnern des Builds beschreibbar.
5. **Mit einem echten Build testen**, zum Beispiel
   `llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t <Name> -f gguf -b <Board> -F`, und das Dockerfile
   in die hadolint-Matrix in `.github/workflows/docker.yml` und in `.github/dependabot.yml` aufnehmen.
6. **Teilen**, als Community-Paket (unten) oder, wenn die Maintainer das Target übernehmen, mit einem Pull-Request an
   dieses Repository ([Abschnitt 9](#9-commits-und-pull-requests)).

### Beiträge zum Community-Zoo

Targets, die du selbst pflegst, Rezepte (fertige Builds, die andere nachbauen können) und Wissen werden als
signierte Community-Pakete über das Community-Repository geteilt, nicht als Ordner, die von Hand nach `community/`
kopiert werden. Befehle und Optionen stehen in der [CLI-Anleitung](docs/CLI_Usage_DE.md#zoo-export); für Targets
siehe auch den [Leitfaden für Modulentwickler](docs/Module_Developer_Guide_DE.md#9-ein-target-teilen).

1. **Ein Paket exportieren:** `llm-cli zoo export target <ID> --author "<Name>"` für ein Target,
   `llm-cli zoo export recipe <Build-Ordner> --author "<Name>"` für einen fertigen Build oder
   `llm-cli zoo export knowledge --author "<Name>"` für deine Wissensbasis. In der GUI: „Export for community after
   creating“ auf der letzten Seite des Modul-Assistenten oder „Export a finished build for the community...“ im
   Kontextmenü des Builder-Logs. Das Paket wird datenschutzbereinigt, geprüft, mit deinem Schlüssel signiert und
   nach `community/contributions/` geschrieben. Gesendet wird noch nichts.
2. **Prüfen:** Die Zusammenfassung zeigt einen Diff für jede Datei, die die Datenschutz-Bereinigung geändert hat
   (höchstens 40 Zeilen je Datei). Prüfe das Paket dann so, wie das Community-Repository es prüfen wird:
   `llm-cli zoo verify community/contributions/<Datei>.zip`.
3. **Den Pull-Request öffnen:** Ergänze den Export-Befehl um `--pr` (er fragt nach der Zusammenfassung, außer
   `--yes` ist angegeben) oder klicke im Export-Dialog auf „Open pull request“. Sind die GitHub CLI und git
   installiert und ist `gh` angemeldet, forkt das Framework das Repository aus der Einstellung `community_repo`,
   committet das Paket auf einem neuen Branch nach `contributions/<Art>/` und öffnet den Pull-Request. Sonst, oder
   wenn das scheitert, öffnet sich die Upload-Seite des Repositorys in deinem Browser, und du ziehst die Datei
   dorthin.
4. **Die CI-Prüfung:** Bei jedem Pull-Request, der `contributions/` berührt, nimmt der Workflow des
   Community-Repositorys nur Paketdateien an (`contributions/target/*.zip`, `contributions/recipe/*.zip`,
   `contributions/knowledge/*.zip`), jeweils höchstens 5 MB, führt für jede `llm-cli zoo verify` aus (Manifest und
   Prüfsummen, keine Links, zusätzlichen oder binären Dateien, die Target-Prüfungen, die Formate von Rezepten und
   Wissen, eine enthaltene Autorensignatur, die Datenschutz-Bereinigung). Die Datenschutz-Bereinigung umfasst auch
   die Tabelle der Geheimnis-Formate des Frameworks; ein Paket mit einem Schlüssel in einer seiner Dateien wird also
   abgelehnt. Der Secret-Scan des Repositorys läuft nicht über die ZIP-Dateien, weil er Binärdateien überspringt und
   in einem Paket nichts prüfen würde.
5. **Durchsicht und Index:** Ein Maintainer sieht das Paket durch, führt es zusammen, trägt es in den signierten
   Index ein und signiert den Index offline. Danach erscheint es in `llm-cli zoo list` und im Community Hub.

Das Framework fragt nie nach einem Token, liest keines und zeigt keines an: `gh` und `git` nutzen ihre eigene
gespeicherte Anmeldung, und die Upload-Seite nutzt die GitHub-Sitzung deines Browsers.

**Aktueller Stand:** Das Community-Repository und sein signierter Index sind noch nicht veröffentlicht.
`community_repo` und `community_index_url` sind leer, und `configs/community_keys.yml` enthält keinen Schlüssel;
`--pr` bricht deshalb mit „No community repository is configured“ ab und lässt das Paket in
`community/contributions/`. Bis es das Repository gibt, lässt sich ein Paket als Datei teilen
(`llm-cli zoo install --file <Paket>` installiert es mit der Vertrauensstufe `local`).

Wissenspakete behalten die Quelladresse jeder Seite und beanspruchen keine Lizenz für die Seiten (`NOASSERTION`); die
Seiten bleiben unter den Bedingungen ihrer Eigentümer. Seiten mit vollem Text unter einer Lizenz, die ihren Text
verlangt (MIT, BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA), kommen mit dem kanonischen Lizenztext in `LICENSES/`
und einer `NOTICE`; die Prüfung (`llm-cli zoo verify <Datei>`) lehnt ein Paket ab, dessen Lizenzdateien nicht zu
seinen Seiten passen. Ein Target, das mit dem Framework ausgeliefert werden soll (Unterstützungsstufe `official`),
kommt weiterhin mit einem normalen Pull-Request in dieses Repository.

### Das Community-Repository betreuen

Zwei Dateien in diesem Repository sind Werkzeuge für die Maintainer des Community-Repositorys; das Framework selbst
nutzt sie nicht.

- `scripts/community_repo/validate-contribution.yml` ist die Vorlage für den CI-Workflow des Community-Repositorys.
  Kopiere sie dort nach `.github/workflows/` (dieses Repository führt sie nicht aus) und setze `LCF_REF` auf die
  Framework-Version, mit der die Pakete geprüft werden: In der Vorlage steht ein Platzhalter, der Job schlägt also
  fehl, bis das Tag eines veröffentlichten Releases eingetragen ist. Sie führt die Prüfungen aus Schritt 4 oben mit
  dem Framework in diesem Tag aus. Der Signaturschlüssel erreicht die CI nie.
- `scripts/zoo_sign_index.py` signiert den Index. Sein Signaturschlüssel liegt nur im Schlüsselbund des
  Betriebssystems (Dienst `lcf-community-index`):

  ```bash
  python scripts/zoo_sign_index.py keygen <Schlüsselname>
  python scripts/zoo_sign_index.py entry <Paket>.zip --url https://<Adresse des Pakets> [--trust community|verified] [--title "<Titel>"]
  python scripts/zoo_sign_index.py sign index.json --key <Schlüsselname> [--previous <alte index.json>]
  ```

  `keygen` legt einen Schlüssel an (einen vorhandenen Namen lehnt es ab) und gibt seinen Eintrag für
  `configs/community_keys.yml` aus, zwei Jahre gültig; liefere diesen Eintrag mit einer Framework-Version aus, bevor
  der Schlüssel den ersten Index signiert. `entry` prüft ein durchgesehenes Paket wie `llm-cli zoo verify` und gibt
  seinen Index-Eintrag aus (Art, ID, Version, Titel, Autor, Lizenz, Vertrauensstufe, URL, Größe und sha256; `--trust`
  ist standardmäßig `community`). `sign` prüft `index.json` gegen das Schema (mit `--previous` muss ihre `version`
  höher sein) und schreibt `index.json.sig` daneben.

`index.json` enthält `schema_version` (1), `version` (eine Zahl, die mit jedem signierten Index wächst), `generated`
(UTC-Zeit in der Form `2026-10-11T12:00:00Z`), `entries` (die Ausgabe von `entry`) und `revoked`. Die Signatur deckt
die genauen Bytes ab; veröffentliche `index.json` also unverändert. Clients lehnen einen Index ab, dessen Version
niedriger ist als die zwischengespeicherte. Um ein Paket zurückzuziehen, trag einen Eintrag in `revoked` ein, erhöhe
`version` und signiere erneut. Jeder Eintrag braucht `kind`, `id` und einen `reason`; mit `sha256` zieht er genau
diese Paketdatei zurück, ohne `sha256` die angegebene `version` oder, ohne `version`, jede Version dieser ID.

---

## 8. Regeln für den Code

- **Erst nutzen, was es gibt.** Bevor du einen Helfer, eine Prüfung, ein Modell oder eine Suche schreibst, such
  im Repository nach einem, der die Aufgabe schon erledigt (`git log -S` zeigt, was zuerst da war). Nutze ihn,
  erweitere ihn und verdrahte ihn; etwas Neues daneben baust du nur, wenn es nichts Passendes gibt, und sagst im
  Pull Request, warum. Zwei Umsetzungen einer Aufgabe werden zur besseren zusammengeführt (zur älteren, wenn sie
  genügt), und jeder Aufrufer wird an sie verdrahtet. Fertig heißt verdrahtet und getestet, nicht nur geschrieben.
- Nutze Typangaben für neue Funktionen.
- Protokolliere mit `orchestrator.utils.logging.get_logger`, nicht mit `print()`. CLI-Ausgaben laufen über `rich`;
  maskiere externen Text vor der Ausgabe mit `rich.markup.escape`.
- Starte Unterprozesse mit Argumentlisten, nie mit Shell-Zeichenketten, die Benutzereingaben enthalten. Werte für
  Shell-Befehle auf entfernten Rechnern setzt du mit `shlex.quote` in Anführungszeichen.
- Externer Text (Webseiten, Logs, Probe-Dateien, Wissensbasis, Tool-Ergebnisse) erreicht ein Sprachmodell nur über
  `orchestrator/utils/untrusted.py` und nie in einem System-Prompt.
- **Imports zeigen die Schichten hinunter.** Jedes Modul von `orchestrator/` hat eine Schicht und importiert nur seine
  eigene oder eine tiefere: (1) unten: `utils/`, `security/`, `schemas/`; (2) remote: `remote/` und
  `utils/ssh_trust.py`; (3) Kern: die Module von `tools/`, die keine Tools festlegen (Registry, Richtlinie, Audit,
  Freigaben, Signaturen, spec, ...); (4) Fachlogik: `Core/`, `runtime/`, `rag/`, `llm/`, `evals/`, wobei `llm` und `rag`
  nie `Core` importieren; (5) Tool-Definitionen: `tools/tools_*`, `framework_*`, `gui_bridge`, `gui_surface`; (6)
  Adapter: `cli.py`, `mcp_server.py`, `gui/` mit `main.py`, und kein Adapter importiert einen anderen. PySide6 gibt es
  nur in der GUI, click und rich nur in der CLI, mcp und uvicorn nur im MCP-Server, paramiko nur im SSH-Transport
  (`utils/ssh_trust.py`, später `remote/transport.py`). `tests/test_layering.py` liest jeden Import (auch in Funktionen,
  unter `TYPE_CHECKING` und Modulnamen, die `import_module`, `lazy_attributes` oder `LazyModule` bekommen). Was heute
  eine Regel bricht, steht in `tests/data/layering_allowlist.json`, jeder Eintrag mit seinem Grund und der Kette, die
  ihn entfernt; der Test schlägt bei einem neuen Verstoß fehl und bei einem Eintrag, den es nicht mehr gibt (entferne
  ihn), und der größte Import-Zyklus darf nur schrumpfen (senke `largest_cycle`, wenn er es tut).
  `python tests/test_layering.py` gibt die heutigen Listen aus. Gemeinsamer Code, den eine tiefere Schicht braucht,
  zieht nach unten, statt nach oben importiert zu werden; der alte Ort behält den Namen als Re-Export.
- **Gemeinsame Helfer haben ein Zuhause unterhalb der Tools.** Pfade in Text, den eine KI liest:
  `orchestrator/security/paths.py` (`label_paths`, `shorten_home`, `device_text`, `scrub_for_ai` und die
  Home-Ordner-Muster `HOME_ROOT` und `ACCOUNT_NAME`, auf denen andere Bereiniger aufbauen). Private atomare Dateien:
  `orchestrator/utils/atomic.py` (`replace_file` mit dem Windows-Wiederholen, `write_private`,
  `write_private_exclusive`). Einmalige Hinweise: `orchestrator/utils/notice_store.py` (`OneTimeNotice`).
  Docker-Image-Referenzen: `orchestrator/utils/image_ref.py`. HTTP, das nur öffentliche Adressen erreicht:
  `orchestrator/security/http.py` (`PublicOnlyAdapter`). Der Audit-Schlüssel: `Core/vault_key.audit_key`; die
  Basis-Images eines Pakets: `Core/community_pack.py`; die Snapshot-Prüfung vor einem Index:
  `Core/knowledge_snapshots.snapshot_space`; das Audit eines gespeicherten Moduls:
  `Core/module_generator.record_module_saved`.
- **Text von außen kommt gereinigt und auf einem Weg in die Wissensbasis.** HTML wird mit
  `rag/html_text.strip_boilerplate` gelesen (Seitenelemente und jedes Element, das `hidden_kind` als versteckt erkennt,
  fallen heraus, bevor Text und Links gelesen werden); eine gecrawlte Seite wird mit `RAGManager.ingest_page`
  gespeichert, anderer Text von außen mit `ingest_document(..., outside=True)` (die Seite eines Pakets zählt immer als
  Text von außen). Beide reinigen den Text mit `utils/untrusted.clean_untrusted` und halten fest, was herausgefallen
  ist (`hidden_removed`). Baue nie einen zweiten Bereiniger und keinen Speicheraufruf daneben; ein neuer Trick zum
  Verstecken kommt in `hidden_kind`, mit einer Seite in `evals/retrieval/carriers/`.
- **KI-Aufrufe laufen über die Anbieterschicht.** Nutze `LLMClient` aus `orchestrator/llm`; baue nie einen eigenen
  SDK-Client. Antworten, nach denen das Framework handelt, sind pydantic-Modelle (strukturierte Antworten), nie
  ausgewerteter freier Text. Neue Anbieter oder Modelle sind Vorlagen in `configs/llm_providers.yml`.
- **Downloads der lokalen Laufzeit sind festgelegt.** Ein neuer llama.cpp-Build oder ein neues Modell in
  `configs/llama_runtime.yml` braucht Größe und SHA256 aus einem vollständigen Download, Modelle zusätzlich einen
  Hugging-Face-Commit als Revision. Übergib `llama-server` nie Optionen, die ihm Tools, einen Agentenmodus,
  MCP-Clients oder eine Weboberfläche geben: Jeder Startweg nimmt die verbotenen Optionen und die Regeln für die
  Umgebung aus `orchestrator/runtime/server_policy.py`, und ein llama.cpp-Programm wird nur aus einem geprüften
  Build-Ordner gestartet, nie über `PATH` gefunden.
- **Die Hardware-Probe gibt es zweimal.** `scripts/hardware_probe.py` ist die eigenständige Datei, die an Geräte
  geschickt und von Hand ausgeführt wird; `orchestrator/runtime/hardware_probe.py` ist ihre byte-gleiche Kopie im
  Paket. Ändere das Skript und kopiere es dann (`cp scripts/hardware_probe.py orchestrator/runtime/hardware_probe.py`);
  ein Test schlägt fehl, solange die beiden sich unterscheiden.
- **Ein Datei-Hash, ein Disk-Watchdog.** Jeder sha256 einer Datei läuft über `orchestrator/utils/helpers.py`, wo der
  erste (`calculate_file_checksum`) schon war: `sha256_file` (optionale Callbacks für Abbruch und Fortschritt, Links
  auf Wunsch abgelehnt), `try_sha256_file` (None für einen Link, einen Ordner oder eine unlesbare Datei),
  `sha256_fileobj` (ein offenes Handle oder seine ersten N Bytes für das Fortsetzen), `sha256_fd`, `copy_hashed`
  (kopiert genau die Bytes, die es hasht, nie durch einen Link), `hash_stream`, `digest_matches` (konstante Zeit, ohne
  Groß- und Kleinschreibung), `HashCache`, `write_sha256sums` und die `.sha256`-Begleitdatei. Alte Namen anderswo
  sind Delegates; `tests/test_hashing.py` schlägt bei einer neuen Hash-Schleife daneben fehl. Ein Disk-Watchdog, ein
  Text bei wenig Platz oder bei voller Platte kommt aus `orchestrator/Core/disk_plan.py` (`low_disk_text`,
  `disk_full_text`, `is_disk_full`, `DiskWatchdog`, `free_space`, `nearest_existing`); so ein Text nennt Ordner beim
  Label und behält `DISK_FULL_PHRASE`, auf die die Self-Healing-Heuristik passt. Jede Prüfung des freien Platzes
  folgt dort der einen Speicherplatz-Regel (Entscheidung A4): `room(path, needed, ledger=...)` (eine Festplatte)
  oder `check_disk` (mehrere) lassen die Reserve frei (`reserve_for`), neben dem, was andere Aufrufe im
  `ReservationLedger` des Benutzerordners reserviert haben, und `refusal`/`require` sagen, was nicht passt und was zu
  tun ist; ein Fenster zeigt die Worte für die Person mit `person_reason`. Vergleiche `shutil.disk_usage` nie mit
  einem eigenen Spielraum. Ein Download bekommt die Regel als `PinnedDownloader(space=download_rule(ledger=...))`;
  ein langer Aufruf, der später schreibt, hält seinen Platz mit `hf_hub.reserve_space`. Tests lesen eine geräumige
  Festplatte (die Fixture `roomy_disk_usage`); ein Test einer Prüfung gibt seine eigene vor.
- **Jede CLI-Aktion hat einen Weg mit der Maus.** Viele Menschen nutzen nie ein Terminal, deshalb kann eine Person
  jeden Befehl und jede Option von `llm-cli` auch in der GUI starten, ohne einen Befehl zu tippen (einen Wert in ein
  Feld zu tippen ist in Ordnung). Ein neuer Befehl oder eine neue Option braucht ihren Weg mit der Maus, einen
  Menüeintrag, eine Schaltfläche oder ein Feld, das dieselbe Kernfunktion wie die CLI aufruft, und einen Eintrag in
  `CLI_PATHS` des Bereichsmoduls in `orchestrator/gui/menus/` (der Befehlspfad, z. B. `"deploy devices remove"` oder
  `"mojo --board"`, auf den objectName der QAction oder des Widgets; `WINDOW_OBJECTS` oder `TAB_OBJECTS` nennt das
  Fenster oder den Reiter, in dem es liegt). Ein Schalter nur für Skripte (Maschinenausgabe wie `--json`, ein
  `--yes`, das eine Frage überspringt, die die GUI immer stellt, ein Kompatibilitäts-Alias) kommt stattdessen mit
  seinem Grund nach `MACHINE_ONLY`. Sonst schlägt `tests/test_cli_gui_parity.py` fehl: Er geht den ganzen
  Befehlsbaum durch, öffnet jedes Fenster offscreen über seinen Menüeintrag und prüft, dass jedes genannte Objekt
  existiert und jede Aktion von der Menüleiste aus erreichbar ist. Menüs werden über
  `orchestrator/gui/menus/place.py` gefunden und platziert.
- Neue Aktionen für KI-Clients und Ditto sind Tools in `orchestrator/tools/framework_tools.py` mit der richtigen
  Stufe (`read`, `local_write`, `external`). Umgehe nie die Tool-Registry, die Berechtigungen oder das Audit-Log.
- **Ein Anwendungsfall, ein Weg; erst schauen, dann erweitern.** Bevor du etwas schreibst, such den Code, der es
  schon tut, und erweitere und verdrahte ihn, statt eine zweite Kopie daneben zu bauen. Ein Build wird nur über
  `BuildService.start_build` eingereiht (`start_recipe` für ein installiertes Community-Rezept): CLI, Builder-Tab und
  Tools geben ihre eigenen Optionen mit (`local_paths`, `priority`, `output_dir`, ...) und behalten nur ihre Fragen
  und ihre Darstellung. Die `recipe.yml` liest nur `community_manager.read_recipe`. Eine Änderung der
  KI-Einstellungen geht über `AISettingsChange` (`orchestrator/llm/settings_change.py`): `problems()`, die eigene
  Rückfrage des Kanals zu `plain_http_url()`, dann `apply()`. `tests/test_build_parity_core.py` und
  `tests/test_ai_settings_change.py` prüfen, dass kein Fenster und kein Befehl an ihnen vorbeigeht.
- Das Framework fragt nie nach einem Repository-Token, liest keines und speichert keines. Übergib Arbeit an GitHub
  über eine installierte GitHub CLI (sie behält ihre eigene Anmeldung) oder den Browser, so wie
  `llm-cli zoo export --pr` es tut.
- Code, der aus einem anderen Projekt übernommen ist, behält einen Kommentar, der seine Quelle nennt („Adapted from
  ...“), und bekommt einen Eintrag mit der Lizenz dieses Projekts in `THIRD_PARTY_NOTICES.md`.
- Schwäche nicht die Build-Sandbox, den Datenschutzfilter oder die Regeln für SSH-Vertrauen.
- **Code, der mit einem Board spricht, nutzt `orchestrator/remote/`.** Prüfe Adresse, Port oder SSH-Benutzer eines
  Boards mit `remote.address` (`valid_host`, `valid_port`, `valid_user`; die Schreibweisen kommen von `Endpoint`),
  mach aus einem fehlgeschlagenen Kontakt seinen Grundcode mit `remote.reasons.reach_code` (`while_waiting=True` in
  einem Warten auf das Board), halte fest, was ein Kontakt ergab, mit `record_reach`
  (`orchestrator/runtime/device/trusted.py`), leite den Fehler eines Board-Schritts von
  `remote.errors.RemoteFailure` ab, zeig den Fortschritt eines Deployments mit `remote.text.event_text` und frag
  `remote.transport.available()`, ob SSH geht; importiere paramiko nie auf Modulebene. Schreib nie einen zweiten
  regulären Ausdruck, eine zweite Grundtabelle oder einen zweiten Recorder daneben. Das Paket importiert nur
  `orchestrator/utils` und `orchestrator/security` (`tests/test_remote_layer.py`).
- **Qt gibt es nur in `orchestrator/gui/`.** PySide6 ist das optionale Extra `gui`; Kommandozeile, MCP-Server und
  alles unterhalb der GUI importieren kein Qt, auch nicht in einer Funktion (`tests/test_qt_free_core.py` prüft jeden
  Import und führt sie in einem Prozess ohne PySide6 aus). Texte für jeden Prozess kommen aus
  `orchestrator/utils/localization.py` (`ui_text`, `tr`, `current_language`, die eine Tabelle `TRANSLATIONS`); das
  Sprachobjekt des Fensters mit seinem Signal `language_changed` und Qts eigener Übersetzung seiner
  Standard-Schaltflächen ist `orchestrator/gui/localization.py` (`LocalizationManager`, `get_instance`; der alte
  Importpfad führt beim ersten Gebrauch dorthin). Kern-Code, dem das Fenster folgen soll, meldet schlichte Werte, die
  die GUI abfragt (zum Beispiel die Werte des Build-Containers in `BuildProgress.resources`, gelesen über
  `BuildService.live_resources`), nie ein Qt-Signal. Die GUI startet in `orchestrator/gui/app.py`;
  `orchestrator/main.py` ist der Einstieg der Starter.
- **Texte, die die Person liest.** Jeder sichtbare Text ist Englisch und Deutsch über `ui_text` (eine Tabelle `TEXTS`
  in einem Modul `orchestrator/utils/l10n_*.py`, das in `TEXT_MODULES` steht). Deutsch sagt „du“; ein Modul ergänzt
  `FORMAL = {"de": {key: text}}` mit der „Sie“-Form genau der Texte, die die Person ansprechen (gleiche
  `{Platzhalter}`, beim Zusammenführen geprüft). Fachbegriffe bleiben Englisch; markiere sie mit dem Glossar aus
  `orchestrator/utils/l10n_glossary.py` (ein neuer Begriff: ein Eintrag in `TERMS` mit einem https-Link und
  `term.<id>` auf Englisch und Deutsch): `explain(widget, "kv_cache")`, `term_label("Quantization:", "quantization",
  buddy=field)`, `help_button(term)` oder `mark_terms_in(window)` aus `orchestrator/gui/explain.py`. Eine markierte
  Beschriftung braucht einen Buddy (F1 in ihrem Feld öffnet die Karte) oder einen `help_button()`;
  `accessibility_problems(window)` nennt eine, die keine Taste erreicht. `find_terms` liest „KV-Cache“ wie „KV cache“,
  findet aber kein deutsches Wort für einen Begriff („Wissensbasis“): markiere es über die ID. `mark_terms`
  überspringt die Wörter eines ausgefüllten `{path:...}` (die Menütitel auf dem Weg zu einem Ort sind keine Begriffe
  des Textes). Fehler für die Person laufen über `show_error()` / `show_exception()`
  (`orchestrator/gui/error_dialog.py`, auch `plain.plain_error()`, nur im GUI-Thread): eine übersetzte
  Zusammenfassung, ein nächster Schritt (ein Satz oder Schritte aus dem Katalog von Chain a0), die englischen Details
  eingeklappt, jeder Text des Fensters ohne Geheimnisse. `error_report(e, lang).dialog_kwargs()` von a0 füllt es;
  Sätze bleiben einfache Zeilen „Nächster Schritt:“, Schritte aus dem Katalog bekommen eine `StepsRow` mit [Öffnen];
  eine leere Liste zeigt `EmptyState` (`orchestrator/gui/empty_state.py`); Größen kommen aus
  `orchestrator/utils/sizes.py`; eine Beschriftung mit Tastenkürzel und eingesetzten Werten nutzt
  `plain.mnemonic_text()`; `orchestrator/gui/accessible.py` verbindet Beschriftungen mit Feldern und benennt Knöpfe
  ohne Text. Überall reiner Text: Keine Beschriftung, kein Fenster und kein Tooltip zeigt je HTML aus Daten.

### Nächste Schritte statt Befehlen

Das Fenster sagt einer Person nie, dass sie einen Befehl tippen soll (DECISIONS Runde 9 U2). Eine Meldung sagt, was
nicht stimmt; was zu tun ist, ist ein **nächster Schritt** des Katalogs in `orchestrator/utils/next_steps/`, nie ein
Befehl oder ein von Hand geschriebener Menüpfad in einem Text:

- **Ein neuer Hinweis** ist ein `Step` im Bereichsmodul seines Teils (`runtime.py`, `boards.py`, `knowledge.py`, ...;
  ein neues Modul wird von selbst gefunden): seine ID (`runtime.install`), der CLI-Befehl (`cli`, ein Schlüssel von
  `CLI_PATHS`, und die Argumentvorlage `cli_args` mit geprüften `params`), das Tool, das eine KI aufruft, oder warum
  nur die Person es tun kann (`person_only`), das Objekt, zu dem [Öffnen] im Fenster führt (`target`, das
  `CLI_PATHS[cli]` sein muss), und seine Texte auf Englisch und Deutsch (`step.<id>`; `step.<id>.what` und `.why` für
  einen Befehl, den die Person zulassen kann). Ein Objekt, das der Schritt nennt, braucht seinen Platz in
  `MOUSE_LABELS` (sein Elternobjekt und den ui_text-Schlüssel seiner Beschriftung, aus einem `l10n_*`-Modul); ein
  Menüeintrag bekommt ihn aus `MENU_LAYOUT` in `common.py`. Die Texte der Schritte duzen wie jeder deutsche Text;
  ein deutscher Text, der in der Sie-Form anders lautet, bekommt sie im `FORMAL = {"de": {...}}` des Moduls (dieselben
  Platzhalter, ein eigener Du-Text; der Katalog liest sie, solange die Anrede des Fensters „Sie“ ist). Der Katalog
  liest seine Beschriftungen über `orchestrator/utils/localization.py`, das ohne Qt auskommt: Ein Tool-Aufruf über MCP
  stellt seine Schritte in einem Worker-Thread dar, und ein erster Qt-Import dort machte ein Fenster kaputt, das
  derselbe Prozess später öffnet.
- **Den Schritt mitgeben, nicht die Worte:** Eine Kern-Ausnahme mit `.with_steps("runtime.install")` auslösen (das
  Mixin `WithSteps`), einem `ToolError` `steps=[...]` geben, einer Ergebniszeile `Line(text, steps)`, einem
  Mojo-Befund `steps=(...)`. Der Renderer schreibt den Befehl für die CLI (`_say_error` in `cli.py`), den Menüpfad für
  das Fenster (`StepsRow`) und das Tool oder den Ort für eine KI (`ToolContext.channel`). Eine blockierte Zeile der
  KI-Aktivität trägt nur Schritt-IDs, die der Katalog kennt (`refs.steps`); ein `person_action`-Schlüssel aus
  `facts_catalog` ist kein Schritt.
- **Ein Text, der einen Ort nennt,** schreibt `{path:<objectName>}`; `ui_text` füllt ihn aus den Beschriftungen auf
  dem Bildschirm in der Sprache des Texts.
- **Ein Befehl, den die Person zulassen kann** (V1), wird nur mit `next_steps.proposal(step, values)` gebaut und mit
  `run_proposal()` ausgeführt: nie aus freiem KI-Text; eine Löschung öffnet die Löschliste, ein Geheimnis geht über
  die Standardeingabe. Jeder Wert jedes Vorschlags ist ein geschlossener (eine Auswahl, ein Name, eine ID, eine Zahl;
  ein Name nennt am besten seine vorhandenen `choices`), ein Geheimnis oder ein Pfad ist nie ein Wert, und der Lauf
  lehnt einen Vorschlag ab, der in einem Wort, das die Person gelesen hat, von dem des Katalogs abweicht. Markiere
  einen Schritt nur dann als `runnable`, wenn sein Befehl ohne Terminal und ohne Rückfrage durchläuft, keine Datei in
  den Ordner schreibt, in dem er startet, und nicht mehr tut, als sein `.what`-Text sagt; nie eine Löschung, eine
  Lizenz oder einen Befehl für das Board, mit Administratorrechten oder eines anderen Programms (die Tests prüfen das
  gegen die echte CLI). Die Adresse oder das Login-Konto eines Boards ist ein `Param(..., private=True)`: Eine KI
  liest es nie, ebenso wenig einen Pfad.
- `tests/test_no_command_texts.py` schlägt bei einem Befehl oder einem von Hand geschriebenen Pfad in einem Text der
  Person fehl (auch in seiner Sie-Form im `FORMAL` eines Moduls) und bei mehr Befehlen oder Pfaden in einem anderen
  Literal, als `tests/data/command_text_baseline.json` erlaubt (nur nach unten; die Integration zieht es an, eine
  Änderung hebt es nie an). `tests/test_cli_gui_parity.py`
  prüft jeden Schritt gegen die CLI, die Tools und die Fenster und jede Mausbeschriftung gegen die echten Menüs und
  Schaltflächen.

---

## 9. Commits und Pull-Requests

- Branches: die laufende Arbeit geschieht auf `development`; `main` hält den stabilen Stand und übernimmt
  `development` per Pull-Request, sobald es stabil ist (CI grün unter Linux und Windows, geprüft). Zweige von
  `development` ab und richte deinen Pull-Request darauf; ein Thema je Pull-Request. Die CI läuft bei jedem Push
  auf `main` und `development` und bei jedem Pull-Request.
- Commit-Nachrichten: eine Zusammenfassungszeile auf Englisch, im Imperativ, nur der erste Buchstabe groß, ohne
  Typ-Präfix, möglichst nicht länger als etwa 72 Zeichen. Beispiele aus der Historie: `Add a secret scan:
  pre-commit and pre-push hooks and a CI job` und `Pin the provider SDKs and local embeddings, drop LiteLLM and
  tiktoken`. Der Text darunter erklärt das Warum.
- Nimm Dateien mit ausdrücklichen Pfaden auf (`git add pfad/zur/datei`), nicht mit `git add -A` oder `git add .`,
  damit kein lokaler Zustand hineinrutscht.
- Code, Tests, Dokumentation (Englisch und Deutsch) und der Changelog-Eintrag gehören in denselben Pull-Request.
- Beschreibe im Pull-Request, wie du getestet hast, etwa mit welchem Board und welchem Modell.
- Committe nie Secrets oder persönlichen Zustand: `configs/config.yml`, `secrets.store`, API-Schlüssel, Tokens oder
  Dateien aus `~/.llm-framework`.
- Keine Binärdateien: keine `.whl`-, `.so`- oder Modelldateien. Lade sie im Dockerfile herunter und prüfe ihre
  Hashes.

---

## 10. Changelog

- Format: [Keep a Changelog 1.1.0](https://keepachangelog.com/de/1.1.0/); Versionen folgen
  [Semantic Versioning 2.0.0](https://semver.org/lang/de/spec/v2.0.0.html).
- **Der Changelog sagt, was war.** Jede Änderung, die Benutzer bemerken, bekommt einen Eintrag unter
  `## [Unreleased]` in `CHANGELOG.md` und denselben Eintrag auf Deutsch in `CHANGELOG_DE.md`. Nutze die Abschnitte
  Added, Changed, Deprecated, Removed, Fixed und Security und im deutschen Changelog die entsprechenden Abschnitte.
- Schreib für Benutzer: was sich geändert hat und was das für sie bedeutet.
- **Die Roadmap sagt, was kommt.** Geplante oder unfertige Arbeit gehört in die Roadmap
  ([docs/BLUEPRINT_DE.md](docs/BLUEPRINT_DE.md#roadmap)), nicht in den Changelog. Wenn du einen Roadmap-Punkt
  fertigstellst, setze seinen Status in beiden Sprachfassungen des Bauplans auf „fertig“ und ergänze den
  Changelog-Eintrag.
- Für ein Release machen die Maintainer aus `[Unreleased]` ein `## [x.y.z] - JJJJ-MM-TT`, setzen dieselbe Version in
  `orchestrator/__init__.py` (`__version__`) und `pyproject.toml` und nehmen die veröffentlichten Punkte aus der
  Roadmap.

---

## 11. Dokumentation

- Englisch ist die Hauptsprache, Deutsch die zweite. **Jedes Dokument gibt es zweimal:** `<Name>.md` (Englisch) und
  `<Name>_DE.md` (Deutsch), mit gleicher Gliederung und gleichem Inhalt.
- Schreib natürliches Deutsch mit korrekten Umlauten (ä, ö, ü, ß) und typografischen Anführungszeichen („…“), keine
  Wort-für-Wort-Übersetzung.
- Beide Dateien verlinken oben aufeinander. Jedes Dokument beginnt mit einer Zeile wie
  `Version 3.0.0 · Stand 2026-10-10` (Englisch: `Version 3.0.0 · Last updated 2026-10-10`); aktualisiere sie, wenn
  du das Dokument änderst. Beschreibt es auch unveröffentlichte Arbeit, sag das:
  `Version 3.0.0 + unveröffentlichte Änderungen`.
- Beschreibe nur, was es auf dem aktuellen Zweig gibt; geplante Funktionen kommen in die Roadmap. Prüfe jeden
  Befehl, jede Option, jeden Pfad und jeden Konfigurationsschlüssel am Code oder an der Ausgabe von `--help`, bevor
  du ihn aufschreibst.
- Schreib zuerst für Einsteiger, dann ergänze die Einzelheiten. Keine Werbesprache.
- Schreib nie echte Schlüssel, E-Mail-Adressen oder andere persönliche Daten in Beispiele; nutze Platzhalter wie
  `<dein Schlüssel>`.
- Ändere beide Sprachfassungen im selben Pull-Request.

---

## 12. Wenn ein Secret offengelegt wurde

Ein Schlüssel, der in einen Commit, einen Pull-Request, ein Issue, ein geteiltes Log oder einen öffentlichen Fork
gelangt ist, gilt als offengelegt, auch wenn du ihn eine Minute später entfernst. Geh in dieser Reihenfolge vor:

1. **Widerrufe oder erneuere ihn zuerst beim Anbieter.** Nur dieser Schritt macht den Schlüssel nutzlos.
   - Anthropic: https://platform.claude.com/settings/keys
   - OpenAI: https://platform.openai.com/settings/organization/api-keys
   - OpenRouter: https://openrouter.ai/keys
   - Google (Gemini-API-Schlüssel): https://aistudio.google.com/apikey
   - Hugging Face: https://huggingface.co/settings/tokens (ein offengelegtes Hugging-Face-Token kann außerdem jeder
     ungültig machen, siehe https://huggingface.co/docs/hub/security-tokens)
   - GitHub-Tokens: https://github.com/settings/tokens
2. **Lege den neuen Schlüssel nur im Secrets-Speicher ab** (`llm-cli ditto login <anbieter>` für Schlüssel von
   KI-Anbietern, `llm-cli secrets set <Name>` für andere Tokens oder die KI-Einstellungen der GUI), nie in einer
   Datei im Repository.
3. **Prüfe, wofür der Schlüssel benutzt wurde**, in der Nutzungs- oder Abrechnungsansicht des Anbieters, und melde
   Missbrauch dem Anbieter.
4. **Entferne den Wert aus dem Repository** mit einem normalen Commit. Die Historie umzuschreiben ist optional und
   kommt erst danach: Ein gepushtes Secret bleibt in Klonen, Forks und Caches erhalten; eine umgeschriebene Historie
   macht einen Schlüssel also nie wieder sicher.
5. **Informiere die Maintainer vertraulich** über ein Security Advisory (siehe
   [Abschnitt 13](#13-sicherheitsprobleme-melden)), wenn der Schlüssel ins öffentliche Repository gelangt ist, und
   ergänze ein lokales Muster ([Abschnitt 6](#6-secret-scan-und-git-hooks)), wenn der Scan sein Format nicht
   erkannt hat.

Für Maintainer: Das Secret Scanning von GitHub läuft auf öffentlichen Repositorys; schaltet außerdem den Push-Schutz
für das Repository ein (Settings > Advanced Security,
https://docs.github.com/en/code-security/secret-scanning/enabling-secret-scanning-features/enabling-push-protection-for-your-repository).

---

## 13. Sicherheitsprobleme melden

Bitte melde Schwachstellen nicht in einem öffentlichen Issue. Öffne stattdessen ein vertrauliches Security Advisory
auf GitHub.

Bleib respektvoll. Uns ist stabil und sicher lieber als schnell und kaputt.
