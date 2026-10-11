Version 3.0.0 + unveröffentlichte Änderungen · Stand 2026-10-11

[English](SSOT_ARCHITECTURE.md) · **Deutsch**

# Einzige Quellen der Wahrheit in configs/

„Single Source of Truth“ (SSOT, einzige Quelle der Wahrheit) heißt: Jede Tatsache steht an genau einer Stelle, und
alles andere liest sie von dort. Dieses Dokument beschreibt die Dateien in `configs/`: was jede enthält, ob das
Framework sie im Betrieb liest und wie man sie ändert und prüft. Außerdem nennt es, wo die übrigen einzigen Quellen
des Projekts liegen.

## Kurz gesagt

- Zwei Dateien in `configs/` steuern das laufende Framework: **`llm_providers.yml`** (Dittos KI-Anbieter) und
  **`llama_runtime.yml`** (der lokale Modellserver und seine Modelle sowie der eine llama.cpp-Commit, aus dem jedes
  selbst gebaute llama.cpp stammt). Beide sind neu und noch nicht veröffentlicht.
- **`hardware_probe_schema.yml`** legt die Datei der Hardware-Probe fest (Schema 2): jeden Schlüssel mit Quelle,
  Normalisierung und Prüfung. Der Profil-Parser lädt sie und setzt sie durch. Sie ist neu und noch nicht
  veröffentlicht.
- **`project_sources.yml`** und **`detailed_project_sources.yml`** sind ein Nachschlagekatalog vorgelagerter
  Projekte und Dokumentationslinks für Entwickler. Das Framework liest sie im Betrieb **nicht**.
- Deine eigenen Einstellungen landen in **`config.yml`** im Einstellungsordner. Die Datei ist persönlich und wird
  nie committet.

## Die Dateien in configs/

| Datei | Enthält | Im Betrieb gelesen? | Gelesen von |
|---|---|---|---|
| `llm_providers.yml` | Vorlagen der KI-Anbieter, die Ditto nutzen kann | ja | `orchestrator/llm/catalog.py` |
| `llama_runtime.yml` | Der llama.cpp-Pin (Commit), die vorgebauten llama.cpp-Archive, CUDA-Toolkits und Builder-Basen für selbst gebautes llama.cpp, festgelegte GGUF-Modelle, Servervorgaben, Regeln für das empfohlene Modell | ja | `orchestrator/runtime/catalog.py`; den Pin auch `orchestrator/utils/llama_pin.py` |
| `hardware_probe_schema.yml` | Schlüssel der Datei der Hardware-Probe (Schema 2): Typ, Prüfung, Quelle und Normalisierung je Betriebssystem, ob ein Schlüssel in den Leistungsschlüssel eingeht | ja, wenn ein Hardware-Profil gelesen wird | `orchestrator/runtime/profile.py` |
| `project_sources.yml` | Katalog vorgelagerter Projekte: Repositorys, Versionen, Dokumentationslinks, Notizen | nein | Menschen; `scripts/validate_ssot.py`, `scripts/sync_ssot.py` |
| `detailed_project_sources.yml` | Ausführliche Entwicklerreferenz: Anleitungen, Entscheidungsbäume, weitere Herstellerabschnitte | nein | Menschen; `scripts/sync_ssot.py` |
| `config.yml` (nicht in git) | Deine Einstellungen | ja | `orchestrator/Core/config_manager.py` |
| `SSOT_ARCHITECTURE.md`, `SSOT_ARCHITECTURE_DE.md` | Dieses Dokument | nein | Menschen |

Der Einstellungsordner ist nur dann das `configs/` der Installation, wenn kein anderer Ordner festgelegt ist: Das
Framework nutzt `LLM_FRAMEWORK_CONFIG_DIR`, wenn die Variable gesetzt ist, sonst das `configs/` der Installation,
wenn es beschreibbar ist, sonst den Benutzerordner (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`). Das
Arbeitsverzeichnis nutzt es nie.

## llm_providers.yml

Die Vorlagen aller KI-Anbieter, mit denen Ditto sprechen kann. Ein neues Modell oder ein OpenAI-kompatibler Server
ist eine Änderung dieser Datei, nicht des Codes. Die Datei beginnt mit einem Kommentar, der jedes Feld erklärt.

Aufbau (Schema 2; Dateien mit Schema 1, in denen `auth` Verfahrensnamen wie `[api_key, none]` aufzählt, werden
weiterhin geladen):

```yaml
version: 2
providers:
  <id>:
    label: "Angezeigter Name"
    adapter: anthropic | openai_responses | openai_compat
    auth:                       # Anmeldeverfahren, das bevorzugte zuerst
      - id: api_key
        type: api_key
        key_page: https://...
        env: [VENDOR_API_KEY]
        validate: {path: /models}
    ...
aliases:            # frühere Werte der Einstellung ai_provider
  OpenAI: openai
```

Felder einer Vorlage:

| Feld | Bedeutung |
|---|---|
| `label`, `group`, `category` | Name in den KI-Einstellungen, der Anbieter, unter dem sie gruppiert wird, und ihre Art (`cloud_api`, `cloud_platform`, `aggregator`, `local_engine`, `mcp_reverse`). |
| `adapter` | API-Familie: `anthropic` (Anthropic-SDK), `openai_responses` (OpenAI Responses API), `openai_compat` (jeder OpenAI-kompatible Server über das OpenAI-SDK). |
| `base_url`, `base_url_configurable` | Endpunkt. Cloud-Vorlagen behalten ihren festen Endpunkt; nur Vorlagen mit `base_url_configurable: true` (Ollama, custom) nehmen die Einstellung `ai_base_url` an. Ein Schlüssel einer solchen Vorlage gehört zu der Adresse, für die er gespeichert wurde, und geht nie an eine andere. |
| `runtime_endpoint` | `true` für den verwalteten lokalen Server: Adresse, Schlüssel und Modell kommen vom laufenden Server, nicht aus den Einstellungen. |
| `location` | `cloud` (der Datenschutzfilter greift), `local` (nie gefiltert) oder `auto` (entschieden am Host der Adresse: Loopback, private Netze und `*.local` gelten als lokal). |
| `auth` | Anmeldeverfahren, das bevorzugte zuerst; das erste nutzbare wird genutzt, außer die Einstellung `ai_auth_<Anbieter>` nennt eines. Felder eines Verfahrens unten. |
| `secret` | Der Name, unter dem frühere Versionen den API-Schlüssel gespeichert haben, etwa `openai_api_key`; `llm-cli secrets set <Name>` und die KI-Einstellungen nehmen ihn weiterhin an. Der Tresor führt den Schlüssel als `cred/<Anbieter>/<Verfahren>`. |
| `models` | Vorgabemodell je Aufgabe (`chat`, `tools`, `json`, `healing`), `default` für den Rest. |
| `suggested_models`, `live_models` | Angebotene Modellnamen; mit `live_models: true` (Vorgabe) wird die Liste beim Anbieter abgefragt. |
| `efforts`, `effort_map`, `reasoning` | Vorgabe-Aufwand je Aufgabe, Übersetzung der Aufwandsstufen für diesen Anbieter und wie der Aufwand übergeben wird (`none`, `reasoning_effort`, `reasoning_object`). |
| `server_fallback_models`, `no_effort_models` | Modelle mit serverseitigem Fallback und Modelle, die keine Aufwandseinstellung annehmen (Anthropic). |
| `no_tools_with_response_format` | `true`, wenn der Server Tools ignoriert, sobald ein JSON-Schema gesetzt ist (llama-server). |
| `headers`, `docs_url` | Zusätzliche Anfrage-Header und ein Dokumentationslink. |

Felder eines Anmeldeverfahrens (Eintrag in `auth`):

| Feld | Bedeutung |
|---|---|
| `id`, `type`, `label` | ID (Kleinbuchstaben; die Einstellung `ai_auth_<Anbieter>` nennt sie), Typ und der Name auf seiner Karte. Typen: `none`, `bearer_optional` (eine lokale Engine, deren Schlüssel optional ist), `api_key`, `cli_session` (eine offizielle Kommandozeile des Anbieters hält die Sitzung, etwa `ant auth login`) sowie `oauth_pkce_loopback`, `oauth_other`, `oauth_device_code`, `cloud_credential_chain` und `short_lived_token`, die abgelehnt werden, bis es ihre Anmeldeabläufe gibt. |
| `key_page`, `where` | Wo der Schlüssel erstellt und widerrufen wird (https), oder bei einer lokalen Engine, wo ihre Einstellungen ihn zeigen. |
| `env` | Umgebungsvariablen, die als Schlüssel dienen, wenn keiner gespeichert ist und der Tresor lesbar ist. Die KI-Einstellungen nennen die Variable; sie wird nie in den Tresor kopiert. Für einen an eine Adresse gebundenen Schlüssel (änderbare Basis-URL, `bearer_optional`) nur an der eigenen `base_url` der Vorlage. |
| `reject_prefixes` | Schlüsselarten, die vor jeder Anfrage abgelehnt werden, etwa `sk-ant-admin01-` (Admin-API-Schlüssel). |
| `fields` | Werte, die nicht geheim sind: `id`, `label`, `pattern` (muss vollständig passen), `header` (wird mit dem Schlüssel gesendet; nie ein Header für Zugangsdaten wie `Authorization`, `x-api-key`, `api-key` oder `Cookie`), `setting` (eine Einstellung, die den Wert ebenfalls hält und Vorrang hat, solange sie gesetzt ist) und `invalid_code` (der Fehler für einen unpassenden Wert). |
| `validate` | Die kostenlose Prüfung hinter „Prüfen“: ein GET mit `path` (unterhalb von `base_url`) oder `url`, `account_field` oder `account_header` (nennt das Konto), `models_field` (als Modelle gezählt), `public: true` für einen Endpunkt, der ohne Schlüssel antwortet. Ein an eine Adresse gebundener Schlüssel wird nur an dieser Adresse geprüft. |
| `opt_in` | Wird nie automatisch gewählt, nur wenn `ai_auth_<Anbieter>` es nennt. Ein `ant auth login`-Profil ist immer opt-in. |
| `note` | Ein Hinweis zu Nutzungsbedingungen oder Gebrauch, der mit dem Verfahren gezeigt wird (`llm-cli ditto auth methods`, die Karte im Anmeldebereich). |
| `cli`, `oauth`, `sources` | Installationsseite und Befehle zum An- und Abmelden der Kommandozeile des Anbieters, die Endpunkte eines späteren Anmeldeablaufs und die Primärquellen zu den Angaben des Verfahrens. |

Vorlagen in der Datei: `anthropic`, `openai`, `google` (Gemini über seinen OpenAI-kompatiblen Endpunkt),
`openrouter`, `ollama`, `local` (der verwaltete llama-server) und `custom` (jeder OpenAI-kompatible Server).

Angaben zu einer Anmeldung, die nicht geheim sind (Konto oder Workspace, die letzten vier Zeichen eines
Schlüssels, die Felder, Zeitpunkt der Prüfung), liegen in `auth.json` neben dem Tresor (`~/.llm-framework` oder
`LLM_FRAMEWORK_HOME`), nie in dieser Datei.

**Eigene Vorlagen:** Eine Datei namens `llm_providers.yml` im Einstellungsordner ergänzt Vorlagen oder ersetzt sie
anhand der ID und ergänzt Aliasse. Das gilt nur, wenn der Einstellungsordner nicht das `configs/` der Installation
ist (sonst sind beide dieselbe Datei). Welche Vorlage benutzt wird, bestimmen deine Einstellungen: `ai_provider`,
`ai_model` (oder `ai_model_<Aufgabe>`), `ai_base_url`, `ai_effort`, `ai_fallback_chain` und `ai_security_level`.

**Prüfen:**

```bash
python -c "from orchestrator.llm.catalog import load_catalog; print(load_catalog().ids())"
```

Unbekannte Felder oder falsche Werte brechen mit einem Prüffehler ab. `tests/test_llm.py` deckt das Laden und das
Überschreiben ab, `tests/test_auth_core.py` beide Schemaversionen und die Anmeldeverfahren.

## llama_runtime.yml

Das verwaltete lokale Modell für Dittos Offline-Modus und selbst gebautes llama.cpp: der eine llama.cpp-Commit, aus
dem jeder Build stammt, welcher vorgebaute Build für welche Hardware geladen wird, die CUDA-Toolkits und
Builder-Basen, die das Build-Rezept nutzt, welche Modelle es gibt und wie der Server läuft. Jeder Download braucht
eine festgelegte Größe **und** einen SHA256 und wird geprüft, bevor er entpackt oder benutzt wird; Einträge ohne
beides werden abgelehnt (im Zweifel geschlossen). Der Kommentar oben nennt die Quellen jedes Werts.

| Abschnitt | Inhalt |
|---|---|
| `schema_version` | Immer `1`. |
| `llama_cpp` | `build` (b-Nummer, etwa `b11429`), `version`, `source` (der Pin, siehe unten), `base_url` (https), `features` (Serveroptionen dieses Builds) und `assets`: ein Eintrag je Betriebssystem, Architektur und Backend (`cpu`, `vulkan`, `cuda`, `rocm`, `metal`) mit seinen Dateien (`name`, `size`, `sha256`, `libs` für CUDA-Laufzeitbibliotheken) und bei GPU-Builds `cuda`, `min_driver`, `jit_min_driver`, `native_compute_caps`, `min_compute_cap` oder `gpu_targets`. `opt_in: true` kennzeichnet Builds, die nur auf Wunsch installiert werden (ROCm). |
| `cuda` | CUDA-Toolkits für selbst gebaute CUDA-Builds (Entscheidung D6): `redist_base_url` (die Redist-Archive von NVIDIA), `cmake_min` und je Toolkit `version`, `manifest` (`redistrib_<version>.json` mit `size` und `sha256`; das Manifest nennt den SHA256 jeder Komponente), `components`, `platforms`, `compute_caps` (`min`, `max`) und `host_compilers` (die Bereiche für `gcc`, `clang` und `msvc`, die nvcc annimmt). `prebuilt_images`: Entwicklungs-Images von NVIDIA, per Digest festgelegt, nur auf ausdrücklichen Wunsch genutzt (`opt_in: true` ist Pflicht). |
| `build_bases` | Builder-Container-Basen für Container- und Cross-Builds: `id`, `libc` (`glibc` oder `musl`), `libc_version`, `gcc`, `image` (per Digest festgelegt). Ein Build für einen Rechner mit glibc nutzt die neueste Basis, deren glibc nicht neuer ist als die des Rechners, ein Rechner mit musl die Alpine-Basis. |
| `model_base_url` | Woher Modelle geladen werden (https). |
| `server` | `ctx_cap` und `ctx_min` (Grenzen der Kontextlänge), `sleep_idle_seconds`, `health_timeout_seconds`, `log_max_mib`, `log_backups`. |
| `models` | Ein Eintrag je Stufe: `tier`, `repo`, `revision` (ein Commit mit 40 Zeichen, nie ein Branch), `file`, `size`, `sha256`, `license`, `native_context`, `kv_bytes_per_token`, `description`; `on_request: true` kennzeichnet Stufen, die nur über ihren Namen geladen werden sollen (in `llm-cli ditto models list` als „on request“ angezeigt; keine Stufenregel nennt sie). |
| `tier_rules` | Automatische Wahl der empfohlenen Stufe: Die erste Regel, deren Bedingungen (`min_vram_gib`, `min_ram_gib`, `max_ram_gib`) erfüllt sind und deren Modell passt, gewinnt. Die letzte Regel darf keine Bedingungen haben, damit jeder Rechner ein Modell bekommt. |

Der Lader lehnt außerdem doppelte Stufen oder Asset-IDs ab, Stufenregeln mit unbekannter Stufe und Treiberversionen,
die als Dezimalzahl ohne Anführungszeichen geschrieben sind (YAML würde `570.60` als `570.6` lesen; schreib
`"570.60"`). Eine Überschreibung je Benutzer gibt es nicht: Es gilt immer die Datei der Installation.

**Wo der llama.cpp-Pin steht.** `llama_cpp.source` ist die eine Stelle:

```yaml
llama_cpp:
  build: b11429
  source:
    repo: https://github.com/ggml-org/llama.cpp.git
    commit: d81235049384534c167caea52b85a694f6103d14   # = refs/tags/b11429
    tag: b11429                                        # immer dasselbe wie build
    build_number: 11429
```

Gebaut wird der Commit: `orchestrator/runtime/build/source.py` holt genau diesen Commit und lehnt jeden anderen ab.
Der Lader verlangt 40 kleingeschriebene Hex-Zeichen, ein https-Repository ohne Zugangsdaten und einen `tag`, der
sowohl `b<build_number>` als auch `build` entspricht. Alles andere folgt aus diesem Eintrag:

| Stelle | Wie sie folgt |
|---|---|
| Das Build-Rezept (`orchestrator/runtime/build/recipe.py`) | liest ihn: Jeder Plan baut diesen Commit und setzt `LLAMA_BUILD_NUMBER` und `LLAMA_BUILD_COMMIT` daraus |
| Die Einstellung `llama_cpp_version` (der Tag, den Target-Images klonen) | ihre Vorgabe wird aus `tag` abgeleitet (`orchestrator/utils/llama_pin.py`) |
| `ARG LLAMA_CPP_TAG` in `targets/Rockchip/dockerfile` (geprüft wird jedes `Dockerfile*` eines Targets, das es setzt oder llama.cpp klont), `docker.build_args.LLAMA_CPP_TAG` in `targets/*/target.yml` | Rückfallwerte für Image-Builds außerhalb des Frameworks; müssen `tag` entsprechen |
| `inference_backends.llama_cpp.commit` in `project_sources.yml` und `detailed_project_sources.yml` | Nachschlagekatalog; muss `tag` entsprechen |
| Vorgaben für `LLAMA_CPP_COMMIT` in `targets/*/modules/source_module.sh` | alte Vier-Modul-Kette, unverändert gelassen (Entscheidung D20); gemeldet, nicht geprüft |

`tests/test_build_recipe.py` schlägt fehl, wenn eine Stelle abweicht. `python scripts/llama_cpp_pin.py check` gibt
dieselbe Tabelle aus und prüft mit `git ls-remote`, dass der Tag bei Upstream auf den festgelegten Commit zeigt. Wie
der Pin angehoben wird: [CONTRIBUTING_DE.md](../CONTRIBUTING_DE.md#den-llamacpp-pin-anheben).

**Ein Modell hinzufügen:** Ermittle Größe und SHA256 der Datei bei einem festen Commit (etwa mit
`HfApi().model_info(repo, revision, files_metadata=True)` aus `huggingface_hub`), trag den Commit als `revision` ein
und ergänze eine Stufenregel nur, wenn das Modell automatisch empfohlen werden soll.

**Prüfen:**

```bash
python -c "from orchestrator.runtime.catalog import load_catalog; c = load_catalog(); print(c.llama_cpp.build, [m.tier for m in c.models])"
```

`tests/test_runtime.py` prüft die Datei selbst und den Lader, `tests/test_build_recipe.py` den Pin, die
CUDA-Toolkits und die Builder-Basen.

## hardware_probe_schema.yml

Das Format von `target_hardware_config.txt`, der Datei, die die Hardware-Probe auf dem Rechner schreibt, der die
Programme ausführen wird. Die Datei beginnt mit einem Kommentar, der jedes Feld erklärt; der
[Leitfaden für Modulentwickler](../docs/Module_Developer_Guide_DE.md#11-die-datei-der-hardware-probe) beschreibt das
Format für alle, die Probe-Dateien schreiben oder lesen.

| Abschnitt | Inhalt |
|---|---|
| `schema` | Immer `2`. |
| `file` | Dateiregeln: Dateiname, die Größengrenze, höchstens 4096 Zeichen je Wert, das Schlüsselmuster, `ON`/`OFF`/`UNKNOWN`, höchstens 16 GPUs. |
| `patterns` | Benannte Muster, die Schlüssel gemeinsam nutzen (`version`, `identifier`). |
| `tables` | Normalisierungstabellen: Architekturnamen, Betriebssystem-Familien, CPU-Hersteller, ARM-Kernnamen, PCI-Hersteller, die SoC-Modelle, nach denen das Framework entscheidet, SMBIOS-Speichertypen, Rockchip-USB-Produkt-IDs, Warncodes. |
| `keys` | Ein Eintrag je Schlüssel: `group`, `type` (`bool`, `enum`, `int`, `pattern`, `version`, `text`, `list`, `timestamp`) mit seiner Prüfung, `required`, `stability` (`stable` oder `volatile`), `perf` (`key`, `recorded` oder `none`), `platforms`, `sources` je Betriebssystem, `normalise`, `consumers` und, wo ein Wert nur auf manchen Architekturen, Betriebssystemen oder bei manchen GPU-Herstellern existiert, `only_arch`, `only_os`, `only_gpu_vendor`, `os_values`. CPU-Merkmale nennen außerdem ihre `/proc/cpuinfo`-Tokens, die Windows-Konstante für `IsProcessorFeaturePresent` mit dem niedrigsten Windows-Build, der sie kennt (`windows_feature`, `windows_min_build`), und den macOS-sysctl. |
| `gpu_keys` | Dasselbe für die Schlüssel je GPU, `GPU<n>_<NAME>`. |

Der Lader lehnt einen Schlüssel ohne Quellen, Normalisierung oder Prüfung ab, Enum-Werte ohne Anführungszeichen
(YAML liest `ON` als Wahrheitswert) und flüchtige Schlüssel, die Pflicht sind oder in den Leistungsschlüssel eingehen.
Eine Überschreibung je Benutzer gibt es nicht: Es gilt immer die Datei der Installation.

**Einen Schlüssel hinzufügen:** Trag den Eintrag mit allen Feldern ein, schreib ihn in den drei Probes
(`scripts/hardware_probe.py`, `.sh`, `.ps1`; jede trägt eine Kopie der Prüfungen und Tabellen, die sie nutzt) und
ergänze einen Testwert in den Fixtures unter `tests/fixtures/hardware_probe/`. `tests/test_hardware_probe.py` schlägt
fehl, bis die Kopien zum Schema passen. Ein neuer Eintrag mit `perf: key` ändert jeden gespeicherten
Leistungsschlüssel; vermerke das im Changelog.

**Prüfen:**

```bash
python -c "from orchestrator.runtime.profile import load_schema; s = load_schema(); print(len(s.keys), len(s.gpu_keys))"
```

`tests/test_hardware_profile.py` prüft die Datei selbst, den Parser und den Profilspeicher.

## project_sources.yml

Ein Katalog vorgelagerter Projekte, die das Framework nutzt, von denen es lernt oder die es vielleicht einbindet.
Abschnitte: `metadata`, `build_tools`, `inference_backends`, `optimization_frameworks`, `model_formats`,
`fine_tuning_tools`, `core`, `vector_database`, `knowledge_ingestion`, `hardware_targets`, `mobile_platforms`,
`voice_tts` und mehrere Einträge für Sprachmodelle (`glados_tts_*`, `turret_tts_*`, `thorsten_tts_*`), `models`,
`tiny_models`, `deployment_tools`, `quality_tools`, `notes`.

Ein typischer Eintrag:

```yaml
inference_backends:
  llama_cpp:
    repo_url: "https://github.com/ggml-org/llama.cpp"
    commit: "b11429"
    docs_workflow: "https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md"
    docs_quantization: "..."
    priority: "CRITICAL"
    integration_status: "ACTIVE"     # ACTIVE | PLANNED | LEARN_FROM | REFERENCE
    note: "Universal CPU/GPU inference. GGUF format standard. Primary fallback backend."
```

Was die Datei **nicht** ist (ihr Kopfkommentar sagt dasselbe):

- Kein Teil des Frameworks liest sie im Betrieb. Sie wird nicht in Umgebungsvariablen für die Build-Container
  übersetzt, der Crawler folgt ihren Links nicht, und Ditto nutzt `docs_workflow` nicht.
- Ihre Versionen sind nicht die Versionen, mit denen gebaut wird. Builds nutzen, was das Dockerfile des Targets
  festlegt, und die Einstellung `llama_cpp_version`, deren Vorgabe aus dem llama.cpp-Pin in `llama_runtime.yml`
  stammt; der lokale Modellserver nutzt `llama_runtime.yml`. Ihr llama.cpp-Eintrag wird trotzdem gegen diesen Pin
  geprüft.
- Sie hat keine Roadmap. Die Roadmap des Projekts steht in [docs/BLUEPRINT_DE.md](../docs/BLUEPRINT_DE.md#roadmap).

Nutze sie als Liste von Links und Notizen. Halte Notizen kurz, verwende vollständige URLs und führe nach einer
Änderung das Prüfskript aus.

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
`project_sources.yml` aus dem aktuellen Ordner; führe es also in `configs/` aus:

```bash
cd configs
python ../scripts/validate_ssot.py
```

Mit der aktuellen Datei meldet es die Datei als gültig, mit 11 Warnungen für Einträge ohne `docs_workflow` (etwa
unter `models` und `tiny_models`), 4 Inferenz-Backends und 8 Hardware-Targets.

### scripts/sync_ssot.py

Vergleicht die Werte `commit`, `version`, `pypi_package` und `docker_tag` (in jeder Tiefe) und die Metadatenfelder
`ssot_version` und `last_updated` (als `last_synced`) von `project_sources.yml` mit
`detailed_project_sources.yml`. Es findet beide Dateien im `configs/` des Repositorys aus jedem Ordner (`--configs`
wählt einen anderen Ordner).

```bash
python scripts/sync_ssot.py            # die Unterschiede auflisten; Exit-Code 1, wenn es welche gibt
python scripts/sync_ssot.py --write    # die Werte in die ausführliche Datei übernehmen
```

Mit `--write` ändert es nur den Text der Werte, die sich unterscheiden; Kommentare, Reihenfolge, Anführungszeichen
und Layout bleiben, wie sie sind. Einen Wert, den die ausführliche Datei nicht hat, nennt es als Hinweis und legt
ihn nie an (die ausführliche Datei ist anders gegliedert; ein solcher Eintrag gehört nur hinein, wenn du ihn von Hand
ergänzt). Eine fehlende Datei ist ein Fehler (Exit-Code 2); das Skript legt nie eine an. Mit den aktuellen Dateien
stimmen die gemeinsamen Werte überein, und es nennt elf Einträge, die nur `project_sources.yml` hat.

## Nicht verwandt: llm-cli config repos

`llm-cli config repos` (Hilfetext: „Manage SSOT Source Repositories“) hat mit `project_sources.yml` nichts zu tun.
Der Befehl bearbeitet die Einstellung `source_repositories` in `config.yml`: `--add <Name> <URL>` speichert einen
Eintrag, `--remove <Name>` entfernt ihn, und ohne Optionen listet er die Einträge auf. Die Build-Engine macht aus
Einträgen der Form `{name: {url: ...}}`, wie `--add` sie speichert, und aus reinen URLs früherer Versionen
Variablen `<NAME>_REPO_OVERRIDE` für die Stufen-Container. Nur das alte `source_module.sh` liest
`LLAMA_CPP_REPO_OVERRIDE`, und das aktuelle Rockchip-`build.sh` ruft es nicht auf. Praktisch hat die Einstellung
derzeit keine Wirkung auf Builds.

## Weitere einzige Quellen des Projekts

| Tatsache | Einzige Quelle | Geprüft durch |
|---|---|---|
| Was ein Target bauen kann | `targets/<ID>/target.yml`, Block `capabilities:` | `orchestrator/schemas/targets.py`; `tests/test_foundation.py`, `tests/test_review_capabilities_paths.py` |
| Version des Frameworks | `orchestrator/__init__.py` (`__version__`) | `tests/test_versioning.py` |
| Python-Abhängigkeiten | `pyproject.toml`, fixiert in `poetry.lock`, exportiert nach `requirements*.txt` | `poetry check --lock`; pip-audit in der CI |
| Einstellungen und ihre Vorgaben | Einträge `ConfigSchema` in `orchestrator/Core/config_manager.py`; deine Werte in `config.yml` | Schemaprüfung beim Start |
| Tools für KI-Clients und Ditto | `orchestrator/tools/framework_tools.py` | `tests/test_foundation.py`, `tests/test_mcp_server.py` |
| Dittos Evaluationsfälle | `evals/ditto/cases.yml` | `tests/test_ditto_eval.py` |
| Kalibriertext für Importance-Matrizen | `data/calibration/`, SHA256-Werte in `orchestrator/utils/calibration.py` | `llm-cli doctor` (Code `data.calibration`) |
| llama.cpp-Version | `llama_cpp.source` in `configs/llama_runtime.yml` (der Commit) | `tests/test_build_recipe.py`, `python scripts/llama_cpp_pin.py check` |
| Weitere Versionen in den Target-Images | das Dockerfile des Targets | hadolint und der Image-Build in `.github/workflows/docker.yml` |
| Was war | [CHANGELOG_DE.md](../CHANGELOG_DE.md) | `tests/test_versioning.py` (neueste Version, an `CHANGELOG.md`) |
| Was kommt | [docs/BLUEPRINT_DE.md#roadmap](../docs/BLUEPRINT_DE.md#roadmap) | Durchsicht |

## Welche Datei du änderst

| Du willst | Ändern | Danach |
|---|---|---|
| einen neuen KI-Anbieter oder ein neues Modell anbieten | `llm_providers.yml` (oder deine eigene Kopie im Einstellungsordner) | die Prüfung oben und `tests/test_llm.py` ausführen; Changelog-Eintrag |
| ein lokales Modell oder einen neueren llama.cpp-Build ergänzen | `llama_runtime.yml`, mit Größe, SHA256 und Commit | die Prüfung oben und `tests/test_runtime.py` ausführen; Changelog-Eintrag |
| auf einen anderen llama.cpp-Commit wechseln | `llama_cpp.source` in `llama_runtime.yml` und die Stellen, die dazu passen müssen | das Vorgehen in [CONTRIBUTING_DE.md](../CONTRIBUTING_DE.md#den-llamacpp-pin-anheben) |
| einen Schlüssel der Datei der Hardware-Probe ergänzen oder ändern | `hardware_probe_schema.yml` und die Probes, die ihn schreiben | die Prüfung oben, `tests/test_hardware_profile.py` und `tests/test_hardware_probe.py` ausführen; Changelog-Eintrag |
| ein Target ein neues Board, Format oder einen neuen Typ bauen lassen | `target.yml` und `build.sh` des Targets | Tests; siehe [Leitfaden für Modulentwickler](../docs/Module_Developer_Guide_DE.md) |
| ein vorgelagertes Projekt oder einen Dokumentationslink festhalten | `project_sources.yml` (neue Einträge in der ausführlichen Datei von Hand) | `python ../scripts/validate_ssot.py` in `configs/`, danach `python ../scripts/sync_ssot.py --write` für geänderte Versionen |
