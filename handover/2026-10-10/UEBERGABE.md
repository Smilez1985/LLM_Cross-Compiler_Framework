# Übergabeprotokoll – Sitzung vom 2026-10-10

Arbeitsdokument für die nächste Sitzung, keine Produktdokumentation. Deshalb gibt es diese Datei nur auf Deutsch.
Nach der Übernahme kann der Ordner `handover/` gelöscht werden.

- Branch: `claude/analyze-repo-code-016nBZCF3akcreLfYwm3cZxU`. Nur dorthin pushen, nie auf andere Branches.
- Version im Code: `orchestrator/__init__.py` → `3.0.0`. CHANGELOG-Eintrag `[3.0.0] - 2026-10-10`.
- Letzter Stand vor dem Schnitt: b005e14 („Version 3.0.0 …“). Der Schnitt-Commit enthält die Review-Fixes und diesen Ordner.

---

## 1. Rahmen und Entscheidungen des Nutzers (weiterhin gültig)

- Antworten auf Deutsch. Im Repo ist **Englisch die Hauptsprache, Deutsch die zweite**: jedes Dokument doppelt (`X.md` + `X_DE.md`).
  README, Blaupause (Design + Roadmap) und CHANGELOG nach „Keep a Changelog“, jeweils mit Datum und Version.
- Arbeitsweise: erst schauen, planen, rückfragen, dann ausführen. Ultracode ist an: größere Arbeit über Workflows,
  Ergebnisse adversarial prüfen.
- Reihenfolge: (1) MCP-Server ✔ → (2) Ditto-Neubau → (3) neue Formate, Targets und LoRA. Sicherheit läuft immer mit.
- Ditto-Laufzeit: **weder LiteLLM noch PydanticAI**. Stattdessen eine eigene, schlanke Provider-Schicht auf den offiziellen SDKs
  `anthropic==1.13.0` und `openai==3.28.0`.
- Lokales Modell:
  - muss auf schwacher Hardware laufen, mindestens RTX 2080 Ti;
  - optional per API (Claude per Token);
  - Modellauswahl wie bei OpenMinis;
  - eine Q2-Variante muss per Ditto-Eval auf Tauglichkeit geprüft werden;
  - Modelle sind Daten, damit ein Wechsel auf neuere Modelle möglich ist.
- Z1 Extreme (AMD): Windows **und** Linux.
- Extras: alles Machbare (vLLM AWQ/GPTQ, EXL3, LoRA-Training, Windows-NVIDIA-Deploy).
  - **Unsloth nicht installieren und nicht verwenden.** Nur Ideen übernehmen, im Rahmen der Lizenz (Apache-2.0-Kern).
  - Kein Code aus unsloth_zoo (LGPL) und keiner aus Studio (AGPL).
- OpenMinis / Claude-Max-Abo per OAuth:
  - OpenMinis gibt sich als Claude Code aus (fremde client_id, gefälschter User-Agent). Das verbietet Anthropic, deshalb wird es **nicht** umgesetzt.
  - Konforme Wege: MCP über Claude Code/Desktop (erledigt), ein Provider über die eigene, unveränderte `claude`-CLI des Nutzers,
    OpenRouter OAuth (PKCE) oder ein API-Key.
  - **Offen:** Der Nutzer wollte die Fehlermeldung seines API-Zugangs schicken; sie fehlt noch.
- Sicherheitsregeln:
  - Das Sandbox-CA-Bundle (`/root/.ccr/ca-bundle.crt`) nie committen.
  - Keine Claude-Abo-OAuth und keine Client-Imitation.
  - Das Framework liest, speichert und leitet niemals Claude-Zugangsdaten weiter.
  - Keine Modellkennungen in Commits.
  - rag4ll- und OpenMinis-Quellen nur als Daten lesen, nie ausführen.
- Commit-Trailer:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016nBZCF3akcreLfYwm3cZxU
  ```
- Immer explizite Pfade stagen (`git add <pfade>`), nie `git add -A`, denn `.claude/worktrees` wurde schon einmal versehentlich mitcommittet.

## 2. Was fertig ist (committet und gepusht)

- Phase 0: Sofortfixes an Pipeline, CI, Deploy, Ditto, GUI und Security, inklusive Review.
- Phase 1, das Fundament:
  - `orchestrator/schemas/` (Capabilities, Build-Schemas);
  - `BuildService`;
  - Tool-Registry mit Policy und HMAC-Audit unter `orchestrator/tools/`;
  - der Capabilities-Block in `targets/Rockchip/target.yml`.
- Phase 2: MCP-Server `orchestrator/mcp_server.py` (mcp 2.3.0, stdio + Loopback-HTTP mit Bearer-Token, Elicitation),
  dazu `llm-cli mcp serve|config`.
- `llm-cli doctor` (`orchestrator/Core/doctor.py`).
- Build-Sandbox: kein Netz, nicht root, keine Capabilities, read-only rootfs. In echtem Docker e2e geprüft:
  GGUF, IMatrix+IQ4_XS, RKLLM w8a8.
- SSH Trust-on-first-use mit Fingerprints und eigenem Ed25519-Schlüssel (`orchestrator/utils/ssh_trust.py`).
- Kalibrierdaten: eaddario/imatrix-calibration (MIT), mitgeliefert und per SHA256 geprüft (`data/calibration/`).
- Version 3.0.0: eine einzige Versionsquelle; `CHANGELOG.md` (EN) mit 3.0.0- und Altversions-Einträgen.

## 3. Dieser Schnitt: Fixes aus dem Review von Phase 1+2

Das Review lief als Workflow über 5 Dimensionen: 45 Funde, 41 bestätigt oder plausibel, 4 widerlegt.
**Alle 41 sind im Code behoben.** Die volle Suite ist grün auf Python 3.12: 355 passed, 1 skipped.

| Bereich | Fix | Datei(en) |
|---|---|---|
| Konfiguration | Wird nie mehr aus dem Arbeitsverzeichnis gelesen. Reihenfolge: `LLM_FRAMEWORK_CONFIG_DIR`, dann `<install>/configs`, dann `~/.llm-framework`. `<cwd>/framework.yml` wird nicht mehr gelesen. Grund: Ein Projektordner konnte über den MCP-Server `source_repositories` umbiegen. | `Core/config_manager.py` |
| Secrets | Store pro Nutzer (`~/.llm-framework/secrets.store`), einmalige Migration aus dem alten configs-Ordner. Schreiben ist atomar (tmp+fsync+replace, 0600). Ein beschädigter Store wird **nie überschrieben**: `set_secret`/`delete_secret` verweigern dann. Vor jedem Schreiben wird neu geladen. | `Core/secrets_manager.py` |
| Audit-Schlüssel | Eigener Keyring-Eintrag `audit-hmac-key` statt Secrets-Store. Ohne Keyring entsteht eine Kette ohne Schlüssel; doctor warnt. | `tools/framework_tools.py` |
| Audit-Log | Wird erst bei Bedarf geöffnet, damit eine kaputte DB den Start nicht verhindert. `BEGIN IMMEDIATE`, damit mehrere Prozesse die Kette nicht aufspalten. Eine `meta`-Tabelle speichert die Schlüssel-ID und unterscheidet „falscher Schlüssel“ von „manipuliert“. Eine Anker-Datei `audit.db.anchor` erkennt das Löschen der neuesten Einträge. Neu: `check()` → `AuditCheck`. `verify()` bleibt kompatibel. | `tools/audit.py` |
| Framework-Start | Scheitert die Tool-Registry, laufen GUI und CLI trotzdem. Nur AI-Tools und MCP fallen aus. | `Core/framework.py` |
| Policy | Eine beschädigte oder unlesbare `tool_policy.json` führt zu „alles DENY“ statt zu den lockeren Defaults. Die Datei wird als `.bad` gesichert, `store.problem`/`damaged` gesetzt und in doctor, `tools list` und `mcp serve` angezeigt. | `tools/policy.py` |
| Registry | Pydantic-`ValidationError` gilt als interner Fehler (geloggt, generische Meldung). Alle Fehlermeldungen werden redacted. Bei „ask“ ohne Rückfrage wird im Audit `approved=None` statt `False` geschrieben. | `tools/registry.py` |
| MCP | Der Bestätigungstext zeigt **alle** Argumente vollständig, sicherheitsrelevante zuerst, Steuer- und Bidi-Zeichen escaped. Über 4000 Zeichen wird abgelehnt statt abgeschnitten. `approved` gilt nur nach echter Rückfrage (`NotAsked`-Marker). Ablehnen und Abbrechen landen im Audit (`ElicitationResult`-Annotation). Clients mit reinem URL-Modus bekommen eine saubere Ablehnung. Die Ablehnungsmeldung schlägt keine Dauerfreigabe mehr vor. | `mcp_server.py` |
| MCP-Konfig | Snippets korrekt gequotet (`shlex.join` bzw. `list2cmdline`) und ohne Rich-Panel ausgegeben. Desktop-Konfig: Backup mit 0600 und atomar, Symlinks werden am Ziel aktualisiert. Die Warnung zu `-c` geht nach stderr (stdout gehört dem MCP-Protokoll). | `mcp_server.py`, `cli.py` |
| Abbruch | Ein Abbruch wird nie rückgängig gemacht. Dafür sorgen `_enter_stage()` für alle Stufenwechsel, der Trivy-Container als registrierter Container, der Abbruch des buildx-Prozesses und eine erneute Prüfung nach `container.start()`. Abgebrochene Builds zählen bis zum Thread-Ende als laufend. | `Core/builder.py` |
| Timeout | Nur ein echter Read-Timeout gilt als Timeout. Andere Fehler aus `wait()` behalten ihre eigene Meldung. | `Core/builder.py` |
| Orchestrator | Die Build-ID wird **vor** dem Submit erfasst; mit `shield` plus Callback wird ein Build abgebrochen, wenn der Abbruch während des Submits kommt. Ein Neuversuch nach Heilung bekommt eine eigene ID (`<job>_r1`), beide Logs bleiben erhalten. `log_tail` dedupliziert mit Trennzeilen. Scheitern die Kalibrierdaten bei IQ1/IQ2-Typen, endet der Request mit Fehler statt mit einer Warnung. | `Core/orchestrator.py`, `Core/build_service.py` |
| Capabilities | Neu sind `default_board` (Rockchip: rk3588, wie build.sh), `aliases` pro Format (wie die Modulskripte), Boards ohne Rücksicht auf Groß-/Kleinschreibung und `normalize()`. Die Regel „IMatrix braucht eigenen Datensatz“ ist entfernt, weil das Framework Kalibriertext mitliefert. | `schemas/targets.py`, `targets/Rockchip/target.yml` |
| Aufrufer | CLI, BuildService und GUI normalisieren und reichen genau die geprüften Werte weiter. GUI: Ohne Capabilities ist das Board frei eingebbar. Die Benutzerwahl der Quantisierung wird explizit verfolgt. `_refresh_targets` hinterlässt keinen veralteten Zustand mehr. | `cli.py`, `Core/build_service.py`, `gui/builder_tab.py` |
| Pfade | `-o` wird relativ zum Arbeitsverzeichnis aufgelöst, der Standard relativ zur Installation. `deploy list-artifacts` und `deploy package` nutzen das aufgelöste Ausgabeverzeichnis und die Manifeste. `~` wird überall expandiert. `list_artifacts` überspringt einzelne kaputte Manifeste. | `cli.py`, `Core/orchestrator.py`, `Core/builder.py`, `Core/build_service.py` |
| Doctor | Meldet Policy-Problem, Audit-Zustand (manipuliert, entfernt, falscher Schlüssel, ohne Anker, ohne Schlüssel) und einen unlesbaren Secrets-Store. | `Core/doctor.py` |
| Tests | `conftest` setzt `LLM_FRAMEWORK_CONFIG_DIR` auf tmp. Zwei Tests auf das neue Verhalten angepasst, einer neu. | `tests/` |

**Noch offen zu diesem Schnitt (als Erstes erledigen):**
1. Regressionstests für die einzelnen Fixes schreiben. Die bestehenden Tests laufen durch, decken die neuen Pfade aber kaum ab. Fehlende Fälle:
   - Konfiguration aus einem fremden Arbeitsverzeichnis wird ignoriert;
   - der Secrets-Store wird bei Ladefehler nicht überschrieben;
   - Audit mit 3 Prozessen parallel, Anker und Löschen der neuesten Einträge;
   - lange Argumente in der MCP-Elicitation;
   - Ablehnen und Abbrechen werden auditiert, URL-only-Clients;
   - Abbruch während des Trivy-Scans;
   - Retry-IDs;
   - Aliase und Board-Normalisierung;
   - GUI: leere Quantisierung, Ziel ohne Capabilities, Refresh;
   - `-o` und Deploy-Pfade.

   Die Reproduktionsskripte der Reviewer lagen im Scratchpad und sind mit der Sitzung weg. Die Szenarien stehen aber in diesem Protokoll und in der Workflow-Ausgabe.
2. Die Tests auf Python 3.11, 3.13 und 3.14 laufen lassen, dazu ruff und bandit (wie die CI).
3. Ein adversarialer Prüf-Workflow über genau diese Fixes.
4. CHANGELOG ergänzen, in EN und DE. Vorschlag: in `[3.0.0]` einarbeiten, weil es nicht veröffentlicht ist.
   - Changed: Konfiguration nicht mehr aus dem Arbeitsverzeichnis, Secrets-Store pro Nutzer, Audit-Schlüssel im Keyring.
   - Security: alle Punkte aus der Tabelle oben.

## 4. Phase 3 (Ditto-Neubau): unfertige Stände als Patches gesichert

Der Workflow lief in eigenen Worktrees und wurde für die Pause **abgebrochen**. Lokale Branches überleben die Sitzung nicht.
Deshalb liegen die Stände unter `handover/2026-10-10/patches/`:

| Patch | Inhalt | Basis | Status |
|---|---|---|---|
| `phase3-llm.mbox` | Provider-Schicht auf den offiziellen anthropic- und openai-SDKs (`orchestrator/llm/*`, `configs/llm_providers.yml`) | 84393cf | implementiert, **Prüfagent nicht gelaufen** |
| `phase3-rag.mbox` | Wissensbasis mit FastEmbed, embedded Qdrant und Hybrid-Suche (`orchestrator/rag/*`, `rag_manager.py`), dazu zwei Fix-Commits des Prüfagenten | 84393cf | implementiert und teilweise geprüft |
| `phase3-rag-wip-on-6a94e2f.patch` | ungesicherte Änderungen des RAG-Prüfagenten, mittendrin abgebrochen (u. a. −140 Zeilen in `tests/test_rag.py`, **kritisch ansehen**) | 6a94e2f (Ende von phase3-rag.mbox) | unfertig |
| `phase3-runtime-wip-on-84393cf.patch` | verwaltetes `llama-server`, Hardware-Erkennung, Qwen3.5-Stufen (`orchestrator/runtime/*`, `configs/llama_runtime.yml`, ditto-Gruppe in `cli.py`) | 84393cf | unfertig, nicht committet, nicht geprüft |
| (keiner) | OpenRouter OAuth PKCE und Provider über die Claude-Code-CLI (phase3/auth-providers) | – | **nicht begonnen** |

Bekanntes Problem im Runtime-Paket: Das Hashen der llama.cpp-Release-Binaries (b11429) brach bei einem Download von etwa 257 MB über den Proxy ab („IncompleteRead“). Prüfen, ob in `configs/llama_runtime.yml` jedes Asset eine SHA256 hat. Assets ohne Prüfsumme dürfen nie heruntergeladen werden.

Anwenden (auf einem eigenen lokalen Hilfs-Branch, nicht pushen):
```bash
git switch -c phase3-integration b005e14            # oder den aktuellen Stand
git am handover/2026-10-10/patches/phase3-llm.mbox   # Basis 84393cf ist Vorfahr von b005e14; bei Konflikten: git am --3way
git am --3way handover/2026-10-10/patches/phase3-rag.mbox
git apply --3way handover/2026-10-10/patches/phase3-rag-wip-on-6a94e2f.patch
git apply --3way handover/2026-10-10/patches/phase3-runtime-wip-on-84393cf.patch
```
Konflikte sind zu erwarten in `orchestrator/cli.py`, `Core/config_manager.py` und `tools/framework_tools.py`, denn der Review-Schnitt hat dieselben Dateien geändert.

Danach laut Plan:
1. Abhängigkeiten zentral in pyproject eintragen, dann `poetry lock`, `poetry export` nach `requirements*.txt` (mit Hashes) und `pip-audit`.
2. Ditto selbst neu verdrahten:
   - Agent-Loop über die ToolRegistry, Schemas, Task-Profile, Privacy-Egress;
   - Modellkatalog im GUI-Dialog;
   - LiteLLM und NativeInferenceEngine entfernen.
3. auth-providers bauen.
4. Q2-Eval für das lokale Modell.
5. Ein Review-Workflow über alles.

Leitplanken aus der claude-api-Skill:
- Modell `claude-opus-5-5` als Standard, `thinking: {type: "adaptive"}`, Streaming bei langen Antworten.
- Strukturierte Ausgabe über `output_config.format`, Aufwand über `output_config.effort`.
- Kein `temperature` und kein `budget_tokens`.
- Ein erzwungenes `tool_choice` gibt auf Opus 5.5, Sonnet 5.5 und Fable 5.1 einen 400-Fehler.
- `stop_reason: "refusal"` behandeln.
- Die Modellliste live über `GET /v1/models` holen.

## 5. Dokumentation: Entwurf als Patch gesichert

Der Doku-Workflow wurde abgebrochen, bevor seine Prüfagenten liefen.
`handover/2026-10-10/patches/docs-draft-on-b005e14.patch` enthält neue Fassungen von README.md, README_DE.md und CONTRIBUTING.md
sowie die neuen Dateien CONTRIBUTING_DE.md, docs/BLUEPRINT.md und docs/BLUEPRINT_DE.md. Alles ist **ungeprüft**.
Anwenden mit `git apply handover/2026-10-10/patches/docs-draft-on-b005e14.patch`; die Prüfung auf dem Stand b005e14 lief ohne Fehler.
Danach jede Aussage gegen den Code prüfen. Die Review-Fixes oben ändern Verhalten: Konfigurationsordner, Secrets-Ort, `default_board`, Aliase, IMatrix ohne Datensatz.

Noch nicht geschrieben:
- docs/CLI_Usage(.md/_DE.md) als Ersatz für CLI_Usage_EN.md und CLI_Anleitung.md;
- docs/Module_Developer_Guide(_DE).md;
- docs/Features(_DE).md als Ersatz für features_deep_dive.md;
- CHANGELOG_DE.md;
- configs/SSOT_ARCHITECTURE_DE.md;
- data/calibration/README_DE.md.

Danach einen Test ergänzen: Jede englische Datei hat ein deutsches Gegenstück mit Versions- und Datumszeile.
Das Workflow-Skript mit allen Schreibaufträgen lag unter `~/.claude/projects/.../workflows/scripts/docs-3-0-*.js`; es ist sitzungsgebunden, die Aufträge stehen deshalb hier.

## 6. Roadmap (zur Orientierung)

- 3.1: Ditto-Neubau (Phase 3, siehe oben). Docs und CHANGELOG in EN und DE nachziehen.
- 3.2:
  - Geräte-Laufzeit für Radxa/ARM;
  - Target `NVIDIA_GPU`;
  - Target `AMD_RDNA3_APU` (Z1 Extreme) für Windows und Linux;
  - vLLM AWQ/GPTQ und EXL3.
- 3.3: LoRA und große Modelle mit einem eigenen schlanken Trainer (Ideen von Unsloth nur im Rahmen der Lizenz).
- 3.4: Lieferkette und Community:
  - signierte Packs statt XOR;
  - Trivy-Digest ohne Docker-Socket;
  - SBOM und digest-gepinnte Images;
  - Locks pro venv;
  - Keyring auch ohne Desktop.

## 7. Umgebungshinweise für die nächste Sitzung

- Die Python-venvs aus dem Scratchpad (`t3.11` bis `t3.14`) sind weg und müssen neu angelegt werden.
  Vermutlich genügt `uv venv -p 3.12 .venv && uv pip install -r requirements-dev.txt`; prüfen, ob dort `mcp` enthalten ist.
- Tests: `QT_QPA_PLATFORM=offscreen python -m pytest -q`, Laufzeit etwa 75 s.
- Docker musste in dieser Sandbox von Hand gestartet werden (`nohup dockerd &`). Das Image `llm-framework/rockchip:dev` (7,6 GB) ist nach dem Neustart weg.
- Der Workflow erlaubt hier nur etwa 2 gleichzeitige Agenten (4 CPUs). Am Ende waren etwa 5 GB Platz frei.
