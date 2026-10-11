Version 3.0.0 + unreleased changes · Last updated 2026-10-11

**English** · [Deutsch](SSOT_ARCHITECTURE_DE.md)

# Single sources of truth in configs/

"Single source of truth" (SSOT) means that each fact is written down in exactly one place, and everything else
reads it from there. This document describes the files in `configs/`: what each one holds, whether the framework
reads it while it runs, and how to change and check it. It also lists where the other single sources of the
project live.

## In short

- Two files in `configs/` steer the running framework: **`llm_providers.yml`** (Ditto's AI providers) and
  **`llama_runtime.yml`** (the local model server and its models). Both are new and not released yet.
- **`project_sources.yml`** and **`detailed_project_sources.yml`** are a reference catalogue of upstream projects
  and documentation links for developers. The framework does **not** read them while it runs.
- Your own settings are saved in **`config.yml`** in the config folder. It is personal and never committed.

## The files in configs/

| File | Holds | Read while the framework runs? | Read by |
|---|---|---|---|
| `llm_providers.yml` | Presets of the AI providers Ditto can use | yes | `orchestrator/llm/catalog.py` |
| `llama_runtime.yml` | Pinned llama.cpp build, pinned GGUF models, server defaults, rules for the recommended model | yes | `orchestrator/runtime/catalog.py` |
| `project_sources.yml` | Catalogue of upstream projects: repositories, versions, documentation links, notes | no | people; `scripts/validate_ssot.py`, `scripts/sync_ssot.py` |
| `detailed_project_sources.yml` | Extended developer reference: guides, decision trees, more vendor sections | no | people; `scripts/sync_ssot.py` |
| `config.yml` (not in git) | Your settings | yes | `orchestrator/Core/config_manager.py` |
| `SSOT_ARCHITECTURE.md`, `SSOT_ARCHITECTURE_DE.md` | This document | no | people |

The config folder is `configs/` of the installation only if no other folder is set: the framework uses
`LLM_FRAMEWORK_CONFIG_DIR` if it is set, otherwise the installation's `configs/` if it is writable, otherwise the
per-user folder (`~/.llm-framework` or `LLM_FRAMEWORK_HOME`). It never uses the working directory.

## llm_providers.yml

The presets of every AI provider Ditto can talk to. A new model or an OpenAI-compatible server is an edit of this
file, not of the code. The file starts with a comment that explains every field.

Structure (schema 2; files of schema 1, where `auth` lists method names such as `[api_key, none]`, still load):

```yaml
version: 2
providers:
  <id>:
    label: "Shown name"
    adapter: anthropic | openai_responses | openai_compat
    auth:                       # sign-in methods, preferred first
      - id: api_key
        type: api_key
        key_page: https://...
        env: [VENDOR_API_KEY]
        validate: {path: /models}
    ...
aliases:            # earlier values of the setting ai_provider
  OpenAI: openai
```

Fields of a preset:

| Field | Meaning |
|---|---|
| `label`, `group`, `category` | Name shown in the AI settings, the vendor it is grouped under, and its kind (`cloud_api`, `cloud_platform`, `aggregator`, `local_engine`, `mcp_reverse`). |
| `adapter` | API family: `anthropic` (Anthropic SDK), `openai_responses` (OpenAI Responses API), `openai_compat` (any OpenAI-compatible server through the OpenAI SDK). |
| `base_url`, `base_url_configurable` | Endpoint. Cloud presets keep their fixed endpoint; only presets with `base_url_configurable: true` (Ollama, custom) accept the setting `ai_base_url`. A key of such a preset belongs to the address it was saved for and is never sent to another one. |
| `runtime_endpoint` | `true` for the managed local server: address, key and model come from the running server, not from the settings. |
| `location` | `cloud` (the privacy filter applies), `local` (never filtered) or `auto` (decided from the host of the address: loopback, private networks and `*.local` count as local). |
| `auth` | Sign-in methods, preferred first; the first usable one is used unless the setting `ai_auth_<provider>` names one. Fields of a method below. |
| `secret` | The name earlier versions stored the API key under, for example `openai_api_key`; `llm-cli secrets set <name>` and the AI settings still accept it. The vault keeps the key as `cred/<provider>/<method>`. |
| `models` | Default model per task (`chat`, `tools`, `json`, `healing`), `default` for the rest. |
| `suggested_models`, `live_models` | Offered model names; with `live_models: true` (the default) the list is loaded from the provider. |
| `efforts`, `effort_map`, `reasoning` | Default effort per task, translation of the effort levels for this provider, and how effort is sent (`none`, `reasoning_effort`, `reasoning_object`). |
| `server_fallback_models`, `no_effort_models` | Models with a server-side fallback, and models that do not accept an effort setting (Anthropic). |
| `no_tools_with_response_format` | `true` if the server ignores tools when a JSON schema is set (llama-server). |
| `headers`, `docs_url` | Extra request headers and a documentation link. |

Fields of a sign-in method (`auth` entry):

| Field | Meaning |
|---|---|
| `id`, `type`, `label` | Id (lower case; the setting `ai_auth_<provider>` names it), type and the name on its card. Types: `none`, `bearer_optional` (a local engine whose key is optional), `api_key`, `cli_session` (an official vendor CLI owns the session, such as `ant auth login`), and `oauth_pkce_loopback`, `oauth_other`, `oauth_device_code`, `cloud_credential_chain`, `short_lived_token`, which are refused until their sign-in flows exist. |
| `key_page`, `where` | Where the user creates and revokes the key (https), or for a local engine where its settings show it. |
| `env` | Environment variables used as the key when none is stored and the vault can be read. The AI settings name the variable; it is never copied into the vault. For a key bound to an address (a changeable base URL, `bearer_optional`) only at the preset's own `base_url`. |
| `reject_prefixes` | Key kinds refused before any request, for example `sk-ant-admin01-` (Admin API keys). |
| `fields` | Values that are not secret: `id`, `label`, `pattern` (must match in full), `header` (sent with the key; never a credential header such as `Authorization`, `x-api-key`, `api-key` or `Cookie`), `setting` (a setting that holds the value too and wins while it is set) and `invalid_code` (the error for a value that does not match). |
| `validate` | The free check behind "Check": a GET with `path` (below `base_url`) or `url`, `account_field` or `account_header` (names the account), `models_field` (counted as models), `public: true` for an endpoint that answers without a key. A key bound to an address is checked at that address only. |
| `opt_in` | Never picked automatically, only when `ai_auth_<provider>` names it. An `ant auth login` profile is always opt-in. |
| `note` | A policy or usage note shown with the method (`llm-cli ditto auth methods`, the card in the sign-in panel). |
| `cli`, `oauth`, `sources` | The vendor CLI's install page and login/logout commands, the endpoints of a later sign-in flow, and the primary sources of the method's facts. |

Presets in the file: `anthropic`, `openai`, `google` (Gemini through its OpenAI-compatible endpoint),
`openrouter`, `ollama`, `local` (the managed llama-server) and `custom` (any OpenAI-compatible server).

Sign-in details that are not secret (the account or workspace, the last four characters of a key, the fields,
when it was checked) are kept in `auth.json` next to the credential vault (`~/.llm-framework` or
`LLM_FRAMEWORK_HOME`), never in this file.

**Your own presets:** a file named `llm_providers.yml` in the config folder adds presets or replaces them by id, and
adds aliases. This applies only if the config folder is not the installation's `configs/` (otherwise both are the
same file). Which preset is used comes from your settings: `ai_provider`, `ai_model` (or `ai_model_<task>`),
`ai_base_url`, `ai_effort`, `ai_fallback_chain` and `ai_security_level`.

**Check it:**

```bash
python -c "from orchestrator.llm.catalog import load_catalog; print(load_catalog().ids())"
```

Unknown fields or wrong values stop with a validation error. `tests/test_llm.py` covers the loader and the
override, `tests/test_auth_core.py` both schema versions and the sign-in methods.

## llama_runtime.yml

The managed local model for Ditto's offline mode: which llama.cpp build is downloaded for which hardware, which
models exist, and how the server runs. Every download needs a pinned size **and** SHA256 and is verified before it
is unpacked or used; entries without both are refused (fail closed). The comment at the top names the sources of
every value.

| Section | Content |
|---|---|
| `schema_version` | Always `1`. |
| `llama_cpp` | `build` (b-number, for example `b11429`), `version`, `base_url` (https), `features` (server options of this build), and `assets`: one entry per operating system, architecture and backend (`cpu`, `vulkan`, `cuda`, `rocm`, `metal`) with its files (`name`, `size`, `sha256`, `libs` for CUDA runtime libraries) and, for GPU builds, `cuda`, `min_driver`, `jit_min_driver`, `native_compute_caps`, `min_compute_cap` or `gpu_targets`. `opt_in: true` marks builds that are installed only when chosen (ROCm). |
| `model_base_url` | Where models are downloaded from (https). |
| `server` | `ctx_cap` and `ctx_min` (context length limits), `sleep_idle_seconds`, `health_timeout_seconds`, `log_max_mib`, `log_backups`. |
| `models` | One entry per tier: `tier`, `repo`, `revision` (a 40-character commit, never a branch), `file`, `size`, `sha256`, `license`, `native_context`, `kv_bytes_per_token`, `description`; `on_request: true` marks tiers that are meant to be downloaded only by name (shown as "on request" in `llm-cli ditto models list`; no tier rule names them). |
| `tier_rules` | Automatic choice of the recommended tier: the first rule whose conditions (`min_vram_gib`, `min_ram_gib`, `max_ram_gib`) hold and whose model fits wins. The last rule must have no conditions, so every machine gets a model. |

The loader also refuses duplicate tiers or asset ids, tier rules that name an unknown tier, and driver versions
written as unquoted decimal numbers (YAML would read `570.60` as `570.6`; write `"570.60"`). There is no per-user
override: the file of the installation is always used.

**Add a model:** take size and SHA256 of the file at a fixed commit (for example with
`HfApi().model_info(repo, revision, files_metadata=True)` from `huggingface_hub`), enter the commit as `revision`,
and add a tier rule only if the model should be recommended automatically.

**Check it:**

```bash
python -c "from orchestrator.runtime.catalog import load_catalog; c = load_catalog(); print(c.llama_cpp.build, [m.tier for m in c.models])"
```

`tests/test_runtime.py` checks the file itself and the loader.

## project_sources.yml

A catalogue of upstream projects that the framework uses, learns from or might integrate. Sections: `metadata`,
`build_tools`, `inference_backends`, `optimization_frameworks`, `model_formats`, `fine_tuning_tools`, `core`,
`vector_database`, `knowledge_ingestion`, `hardware_targets`, `mobile_platforms`, `voice_tts` and several voice
model entries (`glados_tts_*`, `turret_tts_*`, `thorsten_tts_*`), `models`, `tiny_models`, `deployment_tools`,
`quality_tools`, `notes`.

A typical entry:

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

What the file is **not** (its header comment says the same):

- No part of the framework reads it while it runs. It is not turned into environment variables for the build
  containers, the crawler does not follow its links, and Ditto does not use `docs_workflow`.
- Its versions are not the versions builds use. Builds use what the target's Dockerfile pins and the setting
  `llama_cpp_version` (default `v0.6.0`); the local model server uses `llama_runtime.yml`.
- It has no roadmap. The project's roadmap is in [docs/BLUEPRINT.md](../docs/BLUEPRINT.md#roadmap).

Use it as a list of links and notes. Keep notes short, use full URLs, and run the validation script after a change.

## detailed_project_sources.yml

The longer developer reference: deployment philosophy, quantization formats and a decision tree, and more detailed
vendor sections (for example `rockchip_npu`, `nvidia_jetson`, `intel_ecosystem`, `hailo_ai`, `amd_rocm`,
`riscv`). The framework does not read it either. It may lag behind `project_sources.yml`; if the two disagree on a
link or a version, `project_sources.yml` is the one to update first.

## The scripts

### scripts/validate_ssot.py

Checks `project_sources.yml`:

- the YAML syntax (exit code 1 on an error);
- the top-level keys `metadata`, `build_tools`, `inference_backends` and `hardware_targets` (exit code 1 if one is
  missing);
- a warning for every entry with `repo_url` or `url` but no `docs_workflow` (warnings do not fail);
- the number of inference backends and hardware targets.

It does not check links, commits or the detailed file, and it is not part of the CI. It reads
`project_sources.yml` from the current folder, so run it inside `configs/`:

```bash
cd configs
python ../scripts/validate_ssot.py
```

With the current file it reports the file as valid, with 11 warnings for entries without `docs_workflow` (for
example below `models` and `tiny_models`), 4 inference backends and 8 hardware targets.

### scripts/sync_ssot.py

Compares the values `commit`, `version`, `pypi_package` and `docker_tag` (at every depth) and the metadata fields
`ssot_version` and `last_updated` (as `last_synced`) of `project_sources.yml` with
`detailed_project_sources.yml`. It finds both files in the repository's `configs/` from any folder (`--configs`
selects another folder).

```bash
python scripts/sync_ssot.py            # list the differences; exit code 1 if there are any
python scripts/sync_ssot.py --write    # copy the values into the detailed file
```

With `--write` it changes only the text of the values that differ; comments, order, quoting style and layout stay
as they are. A value that the detailed file does not have is listed as a note and never added (the detailed file
is organised differently, so such an entry belongs there only if you add it by hand). A missing file is an error
(exit code 2); the script never creates one. With the current files the shared values are in sync, and it lists
eleven entries that only `project_sources.yml` has.

## Not related: llm-cli config repos

`llm-cli config repos` (help text: "Manage SSOT Source Repositories") has nothing to do with
`project_sources.yml`. It edits the setting `source_repositories` in `config.yml`: `--add <name> <url>` stores an
entry, `--remove <name>` removes it, and without options it lists the entries. The build engine turns entries of
the form `{name: {url: ...}}`, which `--add` stores, and plain URLs stored by earlier versions into variables
`<NAME>_REPO_OVERRIDE` for the stage containers. Only the legacy `source_module.sh` reads `LLAMA_CPP_REPO_OVERRIDE`,
and the current Rockchip `build.sh` does not call it. In practice the setting has no effect on builds at the
moment.

## Other single sources of the project

| Fact | Single source | Checked by |
|---|---|---|
| What a target can build | `targets/<id>/target.yml`, block `capabilities:` | `orchestrator/schemas/targets.py`; `tests/test_foundation.py`, `tests/test_review_capabilities_paths.py` |
| Framework version | `orchestrator/__init__.py` (`__version__`) | `tests/test_versioning.py` |
| Python dependencies | `pyproject.toml`, pinned in `poetry.lock`, exported to `requirements*.txt` | `poetry check --lock`; pip-audit in the CI |
| Settings and their defaults | `ConfigSchema` entries in `orchestrator/Core/config_manager.py`; your values in `config.yml` | schema validation at start |
| Tools for AI clients and Ditto | `orchestrator/tools/framework_tools.py` | `tests/test_foundation.py`, `tests/test_mcp_server.py` |
| Ditto's evaluation cases | `evals/ditto/cases.yml` | `tests/test_ditto_eval.py` |
| Calibration text for importance matrices | `data/calibration/`, SHA256 values in `orchestrator/utils/calibration.py` | `llm-cli doctor` (code `data.calibration`) |
| Versions inside target images | the target's Dockerfile and the setting `llama_cpp_version` | hadolint and the image build in `.github/workflows/docker.yml` |
| What was done | [CHANGELOG.md](../CHANGELOG.md) | `tests/test_versioning.py` (newest version) |
| What comes next | [docs/BLUEPRINT.md#roadmap](../docs/BLUEPRINT.md#roadmap) | review |

## Which file to change

| You want to | Change | Then |
|---|---|---|
| offer a new AI provider or model | `llm_providers.yml` (or your own copy in the config folder) | run the check above and `tests/test_llm.py`; changelog entry |
| add a local model or a newer llama.cpp build | `llama_runtime.yml`, with size, SHA256 and commit | run the check above and `tests/test_runtime.py`; changelog entry |
| let a target build a new board, format or type | the target's `target.yml` and `build.sh` | tests; see the [Module Developer Guide](../docs/Module_Developer_Guide.md) |
| record an upstream project or a documentation link | `project_sources.yml` (new entries in the detailed file by hand) | `python ../scripts/validate_ssot.py` inside `configs/`, then `python ../scripts/sync_ssot.py --write` for changed versions |
