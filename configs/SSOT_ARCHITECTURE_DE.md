Version 3.0.0 + unveröffentlichte Änderungen · Stand 2026-10-11

[English](SSOT_ARCHITECTURE.md) · **Deutsch**

# Einzige Quellen der Wahrheit in configs/

„Single Source of Truth“ (SSOT, einzige Quelle der Wahrheit) heißt: Jede Tatsache steht an genau einer Stelle, und
alles andere liest sie von dort. Dieses Dokument beschreibt die Dateien in `configs/`: was jede enthält, ob das
Framework sie im Betrieb liest und wie man sie ändert und prüft. Außerdem nennt es, wo die übrigen einzigen Quellen
des Projekts liegen.

## Kurz gesagt

- Zwei Dateien in `configs/` steuern das laufende Framework: **`llm_providers.yml`** (Dittos KI-Anbieter) und
  **`llama_runtime.yml`** (der lokale Modellserver und seine Modelle). Beide sind neu und noch nicht veröffentlicht.
- **`project_sources.yml`** und **`detailed_project_sources.yml`** sind ein Nachschlagekatalog vorgelagerter
  Projekte und Dokumentationslinks für Entwickler. Das Framework liest sie im Betrieb **nicht**.
- Ihre eigenen Einstellungen landen in **`config.yml`** im Konfigurationsordner. Die Datei ist persönlich und wird
  nie committet.

## Die Dateien in configs/

| Datei | Enthält | Im Betrieb gelesen? | Gelesen von |
|---|---|---|---|
| `llm_providers.yml` | Vorlagen der KI-Anbieter, die Ditto nutzen kann | ja | `orchestrator/llm/catalog.py` |
| `llama_runtime.yml` | Festgelegter llama.cpp-Build, festgelegte GGUF-Modelle, Servervorgaben, Regeln für das empfohlene Modell | ja | `orchestrator/runtime/catalog.py` |
| `project_sources.yml` | Katalog vorgelagerter Projekte: Repositorys, Versionen, Dokumentationslinks, Notizen | nein | Menschen; `scripts/validate_ssot.py` |
| `detailed_project_sources.yml` | Ausführliche Entwicklerreferenz: Anleitungen, Entscheidungsbäume, weitere Herstellerabschnitte | nein | Menschen |
| `config.yml` (nicht in git) | Ihre Einstellungen | ja | `orchestrator/Core/config_manager.py` |
| `SSOT_ARCHITECTURE.md`, `SSOT_ARCHITECTURE_DE.md` | Dieses Dokument | nein | Menschen |

Der Konfigurationsordner ist nur dann das `configs/` der Installation, wenn kein anderer Ordner festgelegt ist: Das
Framework nutzt `LLM_FRAMEWORK_CONFIG_DIR`, wenn die Variable gesetzt ist, sonst das `configs/` der Installation,
wenn es beschreibbar ist, sonst den Benutzerordner (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`). Das
Arbeitsverzeichnis nutzt es nie.

## llm_providers.yml

Die Vorlagen aller KI-Anbieter, mit denen Ditto sprechen kann. Ein neues Modell oder ein OpenAI-kompatibler Server
ist eine Änderung dieser Datei, nicht des Codes. Die Datei beginnt mit einem Kommentar, der jedes Feld erklärt.

Aufbau:

```yaml
version: 1
providers:
  <id>:
    label: "Angezeigter Name"
    adapter: anthropic | openai_responses | openai_compat
    ...
aliases:            # frühere Werte der Einstellung ai_provider
  OpenAI: openai
```

Felder einer Vorlage:

| Feld | Bedeutung |
|---|---|
| `label` | Name in den KI-Einstellungen. |
| `adapter` | API-Familie: `anthropic` (Anthropic-SDK), `openai_responses` (OpenAI Responses API), `openai_compat` (jeder OpenAI-kompatible Server über das OpenAI-SDK). |
| `base_url`, `base_url_configurable` | Endpunkt. Cloud-Vorlagen behalten ihren festen Endpunkt; nur Vorlagen mit `base_url_configurable: true` (Ollama, custom) nehmen die Einstellung `ai_base_url` an. |
| `runtime_endpoint` | `true` für den verwalteten lokalen Server: Adresse, Schlüssel und Modell kommen vom laufenden Server, nicht aus den Einstellungen. |
| `location` | `cloud` (der Datenschutzfilter greift), `local` (nie gefiltert) oder `auto` (entschieden am Host der Adresse: Loopback, private Netze und `*.local` gelten als lokal). |
| `auth` | Angenommene Verfahren, das bevorzugte zuerst: `api_key`, `oauth`, `none`. |
| `secret` | Name des API-Schlüssels im verschlüsselten Geheimnisspeicher, etwa `openai_api_key`. Setzen Sie ihn mit `llm-cli secrets set <Name>` oder in den KI-Einstellungen. |
| `models` | Vorgabemodell je Aufgabe (`chat`, `tools`, `json`, `healing`), `default` für den Rest. |
| `suggested_models`, `live_models` | Angebotene Modellnamen; mit `live_models: true` (Vorgabe) wird die Liste beim Anbieter abgefragt. |
| `efforts`, `effort_map`, `reasoning` | Vorgabe-Aufwand je Aufgabe, Übersetzung der Aufwandsstufen für diesen Anbieter und wie der Aufwand übergeben wird (`none`, `reasoning_effort`, `reasoning_object`). |
| `server_fallback_models`, `no_effort_models` | Modelle mit serverseitigem Ausweichen und Modelle, die keine Aufwandseinstellung annehmen (Anthropic). |
| `no_tools_with_response_format` | `true`, wenn der Server Werkzeuge ignoriert, sobald ein JSON-Schema gesetzt ist (llama-server). |
| `headers`, `docs_url` | Zusätzliche Anfrage-Header und ein Dokumentationslink. |

Vorlagen in der Datei: `anthropic`, `openai`, `google` (Gemini über seinen OpenAI-kompatiblen Endpunkt),
`openrouter`, `ollama`, `local` (der verwaltete llama-server) und `custom` (jeder OpenAI-kompatible Server). Die
Datei enthält noch eine Vorlage `claude_code`, die Ditto über ein Claude-Abo betreibt; Anthropic erlaubt
Drittanbieter-Apps die Anmeldung mit einem Abo nicht, sie ist daher keine unterstützte Funktion und wird entfernt
(siehe [Roadmap](../docs/BLUEPRINT_DE.md#roadmap)).

**Eigene Vorlagen:** Eine Datei namens `llm_providers.yml` im Konfigurationsordner ergänzt Vorlagen oder ersetzt sie
anhand der ID und ergänzt Aliasse. Das gilt nur, wenn der Konfigurationsordner nicht das `configs/` der Installation
ist (sonst sind beide dieselbe Datei). Welche Vorlage benutzt wird, bestimmen Ihre Einstellungen: `ai_provider`,
`ai_model` (oder `ai_model_<Aufgabe>`), `ai_base_url`, `ai_effort`, `ai_fallback_chain` und `ai_security_level`.

**Prüfen:**

```bash
python -c "from orchestrator.llm.catalog import load_catalog; print(load_catalog().ids())"
```

Unbekannte Felder oder falsche Werte brechen mit einem Prüffehler ab. `tests/test_llm.py` deckt das Laden und das
Überschreiben ab.

## llama_runtime.yml

Das verwaltete lokale Modell für Dittos Offline-Modus: welcher llama.cpp-Build für welche Hardware geladen wird,
welche Modelle es gibt und wie der Server läuft. Jeder Download braucht eine festgelegte Größe **und** einen SHA256
und wird geprüft, bevor er entpackt oder benutzt wird; Einträge ohne beides werden abgelehnt (im Zweifel
geschlossen). Der Kommentar oben nennt die Quellen jedes Werts.

| Abschnitt | Inhalt |
|---|---|
| `schema_version` | Immer `1`. |
| `llama_cpp` | `build` (b-Nummer, etwa `b11429`), `version`, `base_url` (https), `features` (Serveroptionen dieses Builds) und `assets`: ein Eintrag je Betriebssystem, Architektur und Backend (`cpu`, `vulkan`, `cuda`, `rocm`, `metal`) mit seinen Dateien (`name`, `size`, `sha256`, `libs` für CUDA-Laufzeitbibliotheken) und bei GPU-Builds `cuda`, `min_driver`, `jit_min_driver`, `native_compute_caps`, `min_compute_cap` oder `gpu_targets`. `opt_in: true` kennzeichnet Builds, die nur auf Wunsch installiert werden (ROCm). |
| `model_base_url` | Woher Modelle geladen werden (https). |
| `server` | `ctx_cap` und `ctx_min` (Grenzen der Kontextlänge), `sleep_idle_seconds`, `health_timeout_seconds`, `log_max_mib`, `log_backups`. |
| `models` | Ein Eintrag je Stufe: `tier`, `repo`, `revision` (ein Commit mit 40 Zeichen, nie ein Branch), `file`, `size`, `sha256`, `license`, `native_context`, `kv_bytes_per_token`, `description`; `on_request: true` für Stufen, die nur auf Nachfrage geladen werden. |
| `tier_rules` | Automatische Wahl der empfohlenen Stufe: Die erste Regel, deren Bedingungen (`min_vram_gib`, `min_ram_gib`, `max_ram_gib`) erfüllt sind und deren Modell passt, gewinnt. Die letzte Regel darf keine Bedingungen haben, damit jeder Rechner ein Modell bekommt. |

Der Lader lehnt außerdem doppelte Stufen oder Asset-IDs ab, Stufenregeln mit unbekannter Stufe und Treiberversionen,
die als Dezimalzahl ohne Anführungszeichen geschrieben sind (YAML würde `570.60` als `570.6` lesen; schreiben Sie
`"570.60"`). Eine Überschreibung je Benutzer gibt es nicht: Es gilt immer die Datei der Installation.

**Ein Modell hinzufügen:** Ermitteln Sie Größe und SHA256 der Datei bei einem festen Commit (etwa mit
`HfApi().model_info(repo, revision, files_metadata=True)` aus `huggingface_hub`), tragen Sie den Commit als
`revision` ein und ergänzen Sie eine Stufenregel nur, wenn das Modell automatisch empfohlen werden soll.

**Prüfen:**

```bash
python -c "from orchestrator.runtime.catalog import load_catalog; c = load_catalog(); print(c.llama_cpp.build, [m.tier for m in c.models])"
```

`tests/test_runtime.py` prüft die Datei selbst und den Lader.

## project_sources.yml

Ein Katalog vorgelagerter Projekte, die das Framework nutzt, von denen es lernt oder die es vielleicht einbindet.
Abschnitte: `metadata`, `build_tools`, `inference_backends`, `optimization_frameworks`, `model_formats`,
`fine_tuning_tools`, `core`, `vector_database`, `knowledge_ingestion`, `hardware_targets`, `mobile_platforms`,
`voice_tts` und mehrere Einträge für Sprachmodelle (`glados_tts_*`, `turret_tts_*`, `thorsten_tts_*`), `models`,
`tiny_models`, `deployment_tools`, `quality_tools`, `roadmap`, `notes`.

Ein typischer Eintrag:

```yaml
inference_backends:
  llama_cpp:
    repo_url: "https://github.com/ggml-org/llama.cpp"
    commit: "b4009"
    docs_workflow: "https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md"
    docs_quantization: "..."
    priority: "CRITICAL"
    integration_status: "ACTIVE"     # ACTIVE | PLANNED | LEARN_FROM | REFERENCE
    note: "Universal CPU/GPU inference. GGUF format standard. Primary fallback backend."
```

Was die Datei **nicht** ist, egal was ihr Kopfkommentar sagt:

- Kein Teil des Frameworks liest sie im Betrieb. Sie wird nicht in Umgebungsvariablen für die Build-Container
  übersetzt, der Crawler folgt ihren Links nicht, und Ditto nutzt `docs_workflow` nicht.
- Ihre Versionen sind nicht die Versionen, mit denen gebaut wird. Builds nutzen, was das Dockerfile des Targets
  festlegt, und die Einstellung `llama_cpp_version` (Vorgabe `v0.6.0`); der lokale Modellserver nutzt
  `llama_runtime.yml`.
- Ihr Abschnitt `roadmap` (v2.2 bis v2.4) ist veraltet. Die Roadmap des Projekts steht in
  [docs/BLUEPRINT_DE.md](../docs/BLUEPRINT_DE.md#roadmap).

Nutzen Sie sie als Liste von Links und Notizen. Halten Sie Notizen kurz, verwenden Sie vollständige URLs und führen
Sie nach einer Änderung das Prüfskript aus.

## detailed_project_sources.yml

Die längere Entwicklerreferenz: Grundsätze für das Deployment, Quantisierungsformate und ein Entscheidungsbaum sowie
ausführlichere Herstellerabschnitte (etwa `rockchip_npu`, `nvidia_jetson`, `intel_ecosystem`, `hailo_ai`, `amd_rocm`,
`riscv`). Auch sie liest das Framework nicht. Sie kann hinter `project_sources.yml` zurückliegen; widersprechen sich
beide bei einem Link oder einer Version, wird zuerst `project_sources.yml` aktualisiert.

## Die Skripte

### scripts/validate_ssot.py

Prüft `project_sources.yml`:

- die YAML-Syntax (Exit-Code 1 bei einem Fehler);
- die obersten Schlüssel `metadata`, `build_tools`, `inference_backends` und `hardware_targets` (Exit-Code 1, wenn
  einer fehlt);
- eine Warnung für jeden Eintrag mit `repo_url` oder `url`, aber ohne `docs_workflow` (Warnungen lassen die Prüfung
  nicht scheitern);
- die Zahl der Inferenz-Backends und Hardware-Targets.

Links, Commits und die ausführliche Datei prüft es nicht, und es läuft nicht in der CI. Es liest
`project_sources.yml` aus dem aktuellen Ordner; führen Sie es also in `configs/` aus:

```bash
cd configs
python ../scripts/validate_ssot.py
```

Mit der aktuellen Datei meldet es die Datei als gültig, mit 11 Warnungen für Einträge ohne `docs_workflow` (etwa
unter `models` und `tiny_models`), 4 Inferenz-Backends und 8 Hardware-Targets.

### scripts/sync_ssot.py

Soll die Werte `commit`, `version`, `pypi_package` und `docker_tag` (in jeder Tiefe) und die Metadatenfelder
`ssot_version` und `last_updated` (als `last_synced`) aus `project_sources.yml` in die ausführliche Datei übernehmen
und alles andere behalten. **Nutzen Sie es in der jetzigen Form nicht:**

1. Es sucht `detailed_project.sources.yml` (mit Punkt), die Datei heißt aber `detailed_project_sources.yml`. In
   `configs/` ausgeführt, legt es deshalb eine neue Datei `detailed_project.sources.yml` als reine Kopie von
   `project_sources.yml` an, und die echte ausführliche Datei bleibt unverändert.
2. Selbst mit dem richtigen Namen würde es die ganze Datei mit `yaml.dump` neu schreiben: Alle Kommentare gingen
   verloren, und das Layout ändert sich. Außerdem würde es jeden Abschnitt aus `project_sources.yml` ergänzen, der
   in der ausführlichen Datei fehlt.
3. Wie das Prüfskript funktioniert es nur in `configs/`.

Bis es behoben ist (geplant für 3.4.0, siehe [Roadmap](../docs/BLUEPRINT_DE.md#roadmap)), pflegen Sie die
ausführliche Datei von Hand.

## Nicht verwandt: llm-cli config repos

`llm-cli config repos` (Hilfetext: „Manage SSOT Source Repositories“) hat mit `project_sources.yml` nichts zu tun.
Der Befehl bearbeitet die Einstellung `source_repositories` in `config.yml`: `--add <Name> <URL>` speichert einen
Eintrag, `--remove <Name>` entfernt ihn, und ohne Optionen listet er die Einträge auf. Die Build-Engine macht aus
Einträgen der Form `{name: {url: ...}}` Variablen `<NAME>_REPO_OVERRIDE` für die Stufen-Container. Mit `--add`
angelegte Einträge werden als reine URL gespeichert und deshalb nicht weitergegeben, und nur das alte
`source_module.sh` liest `LLAMA_CPP_REPO_OVERRIDE`, das das aktuelle Rockchip-`build.sh` nicht aufruft. Praktisch hat
die Einstellung derzeit keine Wirkung auf Builds.

## Weitere einzige Quellen des Projekts

| Tatsache | Einzige Quelle | Geprüft durch |
|---|---|---|
| Was ein Target bauen kann | `targets/<ID>/target.yml`, Block `capabilities:` | `orchestrator/schemas/targets.py`; `tests/test_foundation.py`, `tests/test_review_capabilities_paths.py` |
| Version des Frameworks | `orchestrator/__init__.py` (`__version__`) | `tests/test_versioning.py` |
| Python-Abhängigkeiten | `pyproject.toml`, fixiert in `poetry.lock`, exportiert nach `requirements*.txt` | `poetry check --lock`; pip-audit in der CI |
| Einstellungen und ihre Vorgaben | Einträge `ConfigSchema` in `orchestrator/Core/config_manager.py`; Ihre Werte in `config.yml` | Schemaprüfung beim Start |
| Werkzeuge für KI-Clients und Ditto | `orchestrator/tools/framework_tools.py` | `tests/test_foundation.py`, `tests/test_mcp_server.py` |
| Dittos Evaluationsfälle | `evals/ditto/cases.yml` | `tests/test_ditto_eval.py` |
| Kalibriertext für Importance-Matrizen | `data/calibration/`, SHA256-Werte in `orchestrator/utils/calibration.py` | `llm-cli doctor` (Code `data.calibration`) |
| Versionen in den Target-Images | das Dockerfile des Targets und die Einstellung `llama_cpp_version` | hadolint und der Image-Build in `.github/workflows/docker.yml` |
| Was war | [CHANGELOG_DE.md](../CHANGELOG_DE.md) | `tests/test_versioning.py` (neueste Version, an `CHANGELOG.md`) |
| Was kommt | [docs/BLUEPRINT_DE.md#roadmap](../docs/BLUEPRINT_DE.md#roadmap) | Durchsicht |

## Welche Datei Sie ändern

| Sie wollen | Ändern | Danach |
|---|---|---|
| einen neuen KI-Anbieter oder ein neues Modell anbieten | `llm_providers.yml` (oder Ihre eigene Kopie im Konfigurationsordner) | die Prüfung oben und `tests/test_llm.py` ausführen; Changelog-Eintrag |
| ein lokales Modell oder einen neueren llama.cpp-Build ergänzen | `llama_runtime.yml`, mit Größe, SHA256 und Commit | die Prüfung oben und `tests/test_runtime.py` ausführen; Changelog-Eintrag |
| ein Target ein neues Board, Format oder einen neuen Typ bauen lassen | `target.yml` und `build.sh` des Targets | Tests; siehe [Leitfaden für Modulentwickler](../docs/Module_Developer_Guide_DE.md) |
| ein vorgelagertes Projekt oder einen Dokumentationslink festhalten | `project_sources.yml` (und von Hand die ausführliche Datei, wenn sie den Eintrag hat) | `python ../scripts/validate_ssot.py` in `configs/` |
