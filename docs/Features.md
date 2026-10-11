Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-11

**English** · [Deutsch](Features_DE.md)

# Features in detail

This document explains what the framework does inside and why, so you always know what your tools are doing. It
describes the state of the development branch: release 3.0.0 plus the unreleased changes in the
[CHANGELOG](../CHANGELOG.md). Planned work is in the [roadmap](BLUEPRINT.md#roadmap), not here.

Back to the [README](../README.md) · All commands: [CLI_Usage.md](CLI_Usage.md)

---

## Contents

- [How the parts fit together](#how-the-parts-fit-together)
- [Targets and capabilities](#targets-and-capabilities)
- [Build pipeline](#build-pipeline)
- [Build sandbox](#build-sandbox)
- [Self-healing](#self-healing)
- [Deployment](#deployment)
- [Ditto, the assistant](#ditto-the-assistant)
- [Ditto's tools](#dittos-tools)
- [Ditto evaluation](#ditto-evaluation)
- [Local runtime and model tiers](#local-runtime-and-model-tiers)
- [Knowledge base (RAG)](#knowledge-base-rag)
- [Deep Ingest](#deep-ingest)
- [MCP server](#mcp-server)
- [Tool permissions and audit log](#tool-permissions-and-audit-log)
- [Secrets and personal state](#secrets-and-personal-state)
- [Input history](#input-history)
- [Hardware probe and new targets](#hardware-probe-and-new-targets)
- [Health check (doctor)](#health-check-doctor)
- [Community zoo](#community-zoo)
- [Secret scan for contributors](#secret-scan-for-contributors)
- [Known gaps](#known-gaps)

---

## How the parts fit together

```
 GUI (PySide6)        CLI (llm-cli)        MCP server (llm-cli mcp serve)
      \                    |                    /
       +------------ Framework manager --------+
       |                   |                   |
  Build service       Ditto (assistant)    Tool registry
  Orchestrator        LLM provider layer   (permissions, confirmation, audit log)
  Build engine ---> Docker (sandboxed conversion containers)
       |                   |
  Target capabilities  Knowledge base (FastEmbed + embedded Qdrant)
  Self-healing         Local runtime (managed llama-server)
  Deployment (SSH)
```

- The **GUI**, the **CLI** and the **MCP server** are three ways to drive the same framework.
- Builds go through one **build service**: it validates a request against the target's capabilities, starts it,
  reports status and logs, cancels it and lists finished builds.
- **Ditto** and **MCP clients** act only through the **tool registry**, which checks your permissions, asks for
  confirmation where needed and writes every call to the audit log.

---

## Targets and capabilities

A target is a folder in `targets/` (for example `targets/Rockchip/`) with a Dockerfile, build scripts and a
`target.yml`. Its `capabilities:` block says exactly what the target can build:

- boards with their CPU, NPU, RAM and example devices, and a default board (Rockchip: `rk3588`, the board the build
  scripts use when none is given),
- formats with their accepted input (`hf` for a Hugging Face model, `onnx` for an ONNX file), quantizations
  (overall and per board), default quantizations (overall and per board), and notes,
- calibration needs: whether a format uses an importance matrix, which quantizations require it, whether a
  calibration dataset is optional or required (RKNN `i8`), and what kind of dataset it is (text or a sample list),
- aliases: common spellings that are translated (`INT8` → `Q8_0` for GGUF, `int4` → `w4a16` for RKLLM).

The schema is in `orchestrator/schemas/targets.py`. The GUI, the CLI, the build service, Ditto and the MCP server
read the same block: they offer exactly these options, fill in the defaults, and reject everything else with a
clear reason before anything runs. The Rockchip values are listed in the [README](../README.md#supported-targets-and-formats).

---

## Build pipeline

A build runs these steps; each one is logged:

1. **Calibration text:** for an importance matrix without your own dataset, the framework writes its shipped,
   checksummed calibration text (setting `calibration_language`: `mixed`, `de` or `en`; see
   [data/calibration](../data/calibration/README.md)). If that fails for a quantization that requires the matrix,
   the build stops with an error.
2. **Prepare:** check the calibration dataset (kept only where the build uses it: GGUF with an importance matrix,
   RKLLM, RKNN `i8`).
3. **Build image:** the target's Docker image with all toolkits (llama.cpp v0.6.0, RKLLM-Toolkit 1.3.1,
   RKNN-Toolkit2 2.3.2, each in its own Python environment). BuildKit is used when available, and the real cause
   of a failure is reported. Later builds reuse Docker's build cache.
4. **Stage the model on the host:** a Hugging Face model is downloaded on your computer (with the token
   `hf_token` for gated models), a local folder or file is used as it is. The containers get it read-only.
5. **Security scan:** Trivy scans the image for HIGH and CRITICAL vulnerabilities (setting `enable_security_scan`,
   pinned image `aquasec/trivy:0.75.0`). Findings are a warning in the log, not an error. A scan that does not
   finish within the build's time limit (`build_timeout`) is stopped with a warning.
6. **Importance matrix** (GGUF only, when chosen).
7. **Convert and quantize** in sandboxed containers ([Build sandbox](#build-sandbox)).
8. **Results:** the model file is collected, `build_info.json` is completed with the model files and their SHA256
   hashes, a `Model_Card.md` with hashes and usage notes is written, and a ZIP of these files is made. All of this
   is prepared in a hidden folder next to the output folder and moved into it only at the end, so a cancelled or
   failed build leaves no files in the output folder, and files of an earlier build there are replaced only by a
   completed one. Links in the build output are never followed. Per-build images are removed afterwards.

Further behaviour:

- **Cancel:** the Builder tab's **Cancel Build** button (enabled while the build the tab started runs; it asks
  for confirmation), Ctrl+C in `llm-cli build start` and the `cancel_build` tool (Ditto in the GUI chat, or an MCP
  client, for builds of its own process) all cancel through the build service. The running container is stopped,
  including a running security scan or image build, and a later step can never undo it; the button and Ctrl+C wait
  until the build has stopped and say so. Once a build has failed or is moving its files into the output folder,
  it can no longer be cancelled and finishes on its own.
- **Build status across programs:** every program that builds writes one record per build into
  `<cache_dir>/build_records/`, so `llm-cli build status` also lists builds that the GUI, the MCP server or another
  CLI call started; a build whose program has ended before it finished is shown as interrupted.
- **Build slots:** at most `max_concurrent_builds` builds (setting, 1 to 10, default 2) run at the same time in one
  program; a further build waits for a free slot (its stage shows "Waiting for a free build slot") instead of
  failing. A cancelled build keeps its slot until its container has stopped.
- **Unknown target:** the CLI, Ditto and MCP clients refuse an unknown target before anything runs (the CLI with
  exit code 2). A request that still ends up with nothing to build fails with "Nothing to build" instead of
  reporting success.
- **Timeouts:** each container step has a time limit (setting `build_timeout`, default 3600 s); only a real time-out
  counts as one.
- **Output folder:** CLI builds go to `output/build_<date>_<time>/<target>/<model>/<quantization>/` in the
  installation (or the folder given with `-o`), builds started in the GUI, by Ditto or by MCP clients to
  `output/<target>/<model>/<quantization>/`.

---

## Build sandbox

Conversion containers run untrusted model code (for example custom tokenizers), so they are locked down
(setting `build_sandbox`, default on):

| Measure | Effect |
|---|---|
| Your user ID instead of root | Files belong to you; nothing in the container runs as root. |
| No network (`network_mode: none`) | The model cannot send data anywhere; libraries cannot download anything. All Hugging Face libraries are set to offline. |
| No Linux capabilities (`cap_drop: ALL`) and `no-new-privileges` | No privilege escalation. |
| Read-only image filesystem | Only the build's own work folder, a 1 GB `/tmp` and the output folder are writable; the staged model is mounted read-only. |
| Process limit (`build_pids_limit`, default 4096) | Protects against runaway processes. |
| Optional memory limit (`build_memory_limit`, e.g. `24g`) | Protects the host from running out of memory. |

Because the sandbox has no network, everything is downloaded beforehand on the host: the model (step 4 above) and
the toolkits (in the image build).

---

## Self-healing

When a build fails, the self-healing manager looks at the log:

1. **Known problems** are recognized by fixed rules: out of memory while compiling the image, a conversion killed
   by the out-of-memory killer, a full disk, missing permission for the Docker daemon, a network problem, outdated
   package sources in the base image.
2. **Otherwise Ditto** analyzes the log, if an AI provider is set up. Its answer must match a fixed structure
   (summary, cause, one suggested step, confidence, host or device) and is discarded if it contains a dangerous
   command.

The proposal waits for your decision: a dialog in the GUI, a question in the CLI, or the `resolve_healing` tool
for Ditto and MCP clients (setting `healing_approval_timeout`, default 900 s). The framework only applies fixed,
built-in fixes itself:

| Problem | What the framework does after your "yes" |
|---|---|
| Out of memory while compiling the image | Sets `build_jobs` to 1 and retries |
| Disk full | Removes dangling framework images and the Docker build cache, then retries |
| Network problem | Retries once |

Everything else, including every AI suggestion, is shown as a manual step and never executed. A retry gets its own
build ID (`<job>_r1`), and both logs are kept.

---

## Deployment

Deployment copies a finished build to a board over SSH and installs the model file there.

- **Trust on first use:** the board's host key is shown with its SHA256 fingerprint (the same format as
  `ssh-keygen -lf`). Only after you confirm is it stored in the framework's own `~/.llm-framework/known_hosts`.
  A changed key is always refused.
- **Pairing:** on the first connection your password is used once to install the framework's own Ed25519 key
  (`~/.llm-framework/id_ed25519_llm_framework`) on the board. The password is never stored; later connections use
  the key.
- **Package:** a ZIP with the model file, the build's manifest and model card, `checksums.sha256` and a generated
  `deploy.sh`. Only model files are deployed (`.gguf`, `.rkllm`, `.rknn`, `.onnx`, `.safetensors`, `.bin`); a
  build with several weight files is refused.
- **On the board:** the package goes into a private temporary folder (`mktemp -d`), `deploy.sh` checks every
  checksum with `sha256sum -c`, copies the model to `~/models` (or `$MODEL_DIR`) and the temporary folder is
  removed. For GGUF, if `llama-server` is installed on the board, it prints a `llama-server` command line that
  listens on 127.0.0.1 and warns never to expose the server without a key; otherwise it says to install llama.cpp.
  For RKLLM it names the RKLLM runtime.
- **Docker mode** (`--docker`, GUI option): also packs container images and starts them on the board. This mode
  is experimental and not verified in this version.

The framework does not install a runtime on the board. That is on the [roadmap](BLUEPRINT.md#roadmap).

---

## Ditto, the assistant

Ditto is built on the framework's own **provider layer** (`orchestrator/llm`), which uses the official `anthropic`
and `openai` SDKs (OpenAI-compatible servers go through `openai`).

### Tasks, models and effort

Ditto's four kinds of work map to the provider layer's task profiles; each task profile has its own model and
effort per provider:

| Ditto profile | Task | Answer |
|---|---|---|
| Chat | `chat` | Free text; may use the framework tools |
| Healing analysis | `healing` | Structured: summary, cause, suggested step, confidence, host or device |
| Module proposal | `json` | Structured target definition, reviewed by you and checked again by the module generator |
| Build recommendation | `json` | Structured: format and quantization from the target's options, optimization level, explanation |

Defaults per task come from [`configs/llm_providers.yml`](../configs/llm_providers.yml). Your settings override them:
`ai_model` and `ai_effort` for every task, or `ai_model_<task>` and `ai_effort_<task>` for one task. Effort levels
are `low`, `medium`, `high`, `xhigh` and `max`; each provider maps them to what it understands.

### Providers

| ID | Kind | Where it runs | Sign-in today |
|---|---|---|---|
| `anthropic` | Anthropic Claude API | cloud | API key (`anthropic_api_key`, or `ANTHROPIC_API_KEY`) |
| `openai` | OpenAI Responses API | cloud | API key (`openai_api_key`, or `OPENAI_API_KEY`) |
| `google` | Gemini, OpenAI-compatible endpoint | cloud | API key (`gemini_api_key`, or `GEMINI_API_KEY`) |
| `openrouter` | OpenRouter | cloud | API key (`openrouter_api_key`, or `OPENROUTER_API_KEY`) |
| `ollama` | Ollama | local (base URL configurable) | none |
| `local` | managed llama-server | local | per-start key, handled for you |
| `custom` | any OpenAI-compatible server | decided from the address: loopback, private and link-local addresses and names ending in `.local`, `.lan`, `.localhost` or `.home.arpa` count as local | API key of the server address (`custom_api_key`) or none |

- Cloud providers have fixed addresses; a base URL left over from Ollama or a custom server never receives their key.
- A key of a custom server belongs to the address it was saved for (scheme, host and port): after the base URL
  changes it is no longer sent, and the new server needs its own key.
- Each provider lists its sign-in methods in `configs/llm_providers.yml` in the order they are preferred; the
  first usable one is used unless `ai_auth_<provider>` names one. A key in one of the environment variables in
  the table above is used only when no key is stored, and the AI settings show the variable as its source; it
  is never copied into the vault. When the vault cannot be read, the variable is not used and the error names
  the problem with the vault.
- A free check (the provider's model list, OpenRouter's key endpoint) tells whether the stored key works, which
  workspace or key name it belongs to and how many models it can use. Sign-in details that are not secret
  (account or workspace, the last four characters of a key, when it was checked) are kept in `auth.json` next to
  the credential vault.
- The model list in the settings is loaded live from the provider; any model name can also be typed.
- A file `llm_providers.yml` in your settings folder adds or replaces providers by ID, so a new
  OpenAI-compatible server needs no code change.
- **Claude API (`anthropic`):** the stored API key always comes first. An `ant auth login` profile is used only
  when you choose it (`ai_auth_anthropic: ant_profile`; the earlier value `oauth` means the same). A key that is
  valid in every workspace of the organization needs the workspace ID (AI settings field "Workspace ID", setting
  `ai_anthropic_workspace_id`, `wrkspc_...`; while set, it wins over a workspace ID stored with the key); it is
  sent only with the API key and only to the Claude API. Admin API keys (`sk-ant-admin01-`) are refused before
  any request.
- **OpenAI (`openai`):** an organization and a project stored with the key (`org-...`, `proj_...`) go with it as
  the `OpenAI-Organization` and `OpenAI-Project` headers; a key that works but may not list models is reported as
  such, and the model ID can be typed.
- There is no `claude_code` provider (Ditto through a Claude subscription): Anthropic does not allow third-party
  apps to use subscription limits. A saved `claude_code` (also in the fallback chain or `--provider`) stops with a
  message naming the alternatives; such an entry in your own `llm_providers.yml` is skipped with a warning. Claude
  subscribers use the framework from Claude Code or Claude Desktop over [MCP](#mcp-server), or with an API key
  (Max and Team plans include monthly API credits).
- The SDKs never read a credential, header or address from the environment themselves: every client gets the
  key and base URL explicitly, and `OPENAI_CUSTOM_HEADERS` / `ANTHROPIC_CUSTOM_HEADERS` are removed at startup with a warning
  (setting `ai_allow_sdk_env_headers: true` keeps them).

### Sign-in

- **Where:** **Sign in...** in the AI settings (GUI) and `llm-cli ditto login | logout | auth` (see the
  [CLI guide](CLI_Usage.md#ditto-login)).
- **Method cards:** the panel shows the provider's sign-in methods in the catalog's order. The first one this
  version can sign in with is marked "Recommended", opt-in methods (the `ant` profile) "Advanced", and methods of a
  later version are shown but cannot be chosen. Each type has its own form: a key form (key page, password field,
  the catalog's extra fields such as the Claude workspace ID or the OpenAI organization and project, **Check**,
  **Save**, **Sign out**), "no sign-in" with Check for local engines, the `ant` CLI profile (the official command to
  run, the profile name, a check through the Anthropic SDK; the framework never starts the CLI or reads its files),
  and the managed local server, whose key is automatic.
- **Status line:** "Signed in as ...", "Not signed in", "Using key from environment variable X" or "Expired: sign in
  again". A key is shown only as its last four characters. Ditto's chat header shows the access in use, for example
  "Anthropic Claude (API) · API key · wrkspc_..." or "Local · Ollama".
- **Check before storing:** Check sends the typed key with one free request and stores nothing; `llm-cli ditto
  login` checks a key the same way before it stores it. Saving remembers the method as `ai_auth_<provider>`.
- **No secret in argv:** the CLI takes a key only from a hidden prompt, stdin (`--with-token`) or the provider's
  documented environment variable (`--from-env`). Without a terminal it asks nothing, and a provider with several
  methods needs `--method`. Exit codes: 0 signed in, 2 usage, 3 rejected, 4 network, 5 storage, 6 not available.
- **Keys stay intact and out of the output:** a key with a space, a line break or an invisible character inside is
  refused before anything is sent or stored, a server's error text that repeats the key shows `REDACTED` instead,
  and a typed key goes over plain `http://` to another computer only after you confirm it (CLI: `--yes`). A key typed
  in the AI settings' own key field replaces a method a sign-in remembered (for example the `ant` profile).
- **Sign-out is local first:** the stored key is deleted, and the key page where you revoke the key itself is named;
  a key in an environment variable stays in use until you unset it.
- **No usable keyring:** the panel shows the storage chooser (passphrase, credential helper, or the unencrypted key
  file after a confirmation), and the GUI shows the same chooser at startup before it gives up; a passphrase is asked
  in a dialog. A choice that cannot open the existing vault (one protected by the locked keyring) is not kept. The
  CLI exits with code 5 and names `llm-cli ditto auth storage --use ...`. Nothing is picked for you.
- **Responsive:** every vault, keyring or network call of the panel and of the chat header runs in a worker thread.

### Calls, retries and fallback

- The SDKs' own retries are off; the provider layer retries rate limits and outages up to twice and waits for a
  provider's `Retry-After` of up to 30 s (setting `ai_max_retry_wait`). Each request may take up to 600 s
  (`ai_timeout`).
- **Fallback chain** (`ai_fallback_chain`, e.g. `local, ollama:qwen3.5:4b`): when the model declines or the
  provider is unavailable, the next entry is tried. Entries that are not set up are skipped.
- **Offline mode** (`offline_mode`): only local providers are used; a cloud provider is refused with a clear
  message. The knowledge base downloads nothing in offline mode either.
- Requests to servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment, so the proxy never sees
  prompts or the local server's key.
- Errors are explained in plain words, for example that a Claude API key needs a workspace ID, that the key was
  revoked or has expired, that credit is used up, or that the local server is not running.

### Privacy filter for cloud providers

Before text goes to a cloud provider, personal data is replaced (setting `ai_security_level`):

| Level | Replaced |
|---|---|
| `OFF` | only secrets (API keys, tokens); they are always removed, and never restored |
| `BALANCED` | also IPv4/IPv6 and MAC addresses, e-mail addresses, `user@host` logins, user names in home paths |
| `STRICT` (default) | also host names announced in probe files, serial numbers, UUIDs and the directories of absolute paths (system paths such as `/usr/` stay readable) |

The placeholders look like `<IP_1a2b3c4d>`. They are derived from the value with a key that is created once per
installation, so the same value always gets the same placeholder. Placeholders in the answer are turned back into
the real values on your computer. Local providers are never filtered.

### Structured answers and the dangerous-command filter

Healing analyses, module proposals and build recommendations must be valid against a schema with length limits.
Anything else is an error and is never applied. For build recommendations the schema itself lists only the formats
and quantizations the target offers, and the answer is checked again per format.

Whatever a build log, a probe file or a model name tells the model, Ditto discards a healing fix or a
recommendation that contains a dangerous command: a download piped into a shell, world-writable permissions
(`chmod 777`/`666`), deleting the root or home folder, overwriting a disk (`mkfs`, `dd` to a disk) or a fork bomb.

### Untrusted text

Build logs, probe files, knowledge-base excerpts and tool results are external text. Ditto puts them into the user
turn, cleaned of hidden and control characters and wrapped in marked `<untrusted_data>` blocks, never into the
system prompt. The system prompt tells the model that such blocks are data, not instructions.

### Chat history

The GUI chat is saved per user in `~/.llm-framework/chat_history.json` (readable only by you). The conversation is
append-only: earlier turns are never edited. When it grows beyond the setting `chat_context_limit` (default 4096
tokens, estimated at four characters per token), Ditto summarizes it in a separate request and continues in a new
conversation that starts with the summary. The window keeps up to 500 entries. Histories of earlier versions are
migrated. `llm-cli ditto ask` does not use the history.

---

## Ditto's tools

In the chat (GUI) and with `llm-cli ditto ask`, Ditto can use the same tools as MCP clients:

| Tool | Tier | What it does |
|---|---|---|
| `list_targets` | read | Targets with boards, formats and quantizations |
| `get_target_capabilities` | read | What a target can build |
| `list_build_requests` | read | Build requests of this session |
| `get_build_status` | read | Status, progress, errors, a pending self-healing proposal |
| `read_build_log` | read | The last lines of a build log (secrets redacted) |
| `list_artifacts` | read | Finished builds from their manifests |
| `run_doctor` | read | The health check, without fixes |
| `search_knowledge` | read | Search the knowledge base |
| `start_build` | local write | Start a build (must match the target's capabilities) |
| `cancel_build` | local write | Cancel a build |
| `resolve_healing` | local write | Approve or dismiss a self-healing proposal |
| `get_status` | read | Your tool permissions for this caller: every tool with its decision and why (also the hidden ones), and what no tool does, with where you do it |

How a question with tools runs:

1. Ditto gets the definitions of the tools your permissions do not deny: the same tools an MCP client with the same
   permissions is offered, whatever the model's size.
2. The model may call tools. Each call goes through the tool registry: input check, your permission, confirmation
   where the permission says "ask" (a dialog in the GUI with the tool's title and every argument; a prompt in the
   CLI, denied without a terminal), audit log entry with actor `ditto`.
3. Tool results go back to the model as untrusted data. A denied or failed call is reported to the model, which is
   told not to retry the same call.
4. After at most 8 model turns per question Ditto stops and says so.

**How names and values are matched** (the same for Ditto and every MCP client, whatever the model's size):

- A tool name runs when it is exact, or the same name in other letter case or with `_`, `-`, `.` or spaces in
  other places (`StartBuild` runs `start_build`, with a note). Any other name is refused with up to three
  suggestions from the tools your permissions allow; a suggestion never runs by itself.
- Argument names: aliases a tool declares (`quant` and `quantization_type` for `quantization`, `repo` and
  `repo_id` for `model`) and keys that differ only in letter case or in `-` versus `_` are renamed, with a note.
  Any other unknown argument is refused with the list of valid ones; nothing is dropped silently. Two keys for one
  field with different values are refused.
- Values: numbers and yes/no given as text, and lists given as JSON text, are read as such. Targets, formats,
  boards and quantization types match exactly, by letter case and separators when that names exactly one value
  (`q4-k-m` is `Q4_K_M`; a target name that could mean two targets is refused with both), or by an alias the
  target declares (`Q4` for GGUF). A near miss (`rk3567`) is refused with suggestions, never corrected. A number of
  lines or results above the maximum is reduced, with a note.
- Never changed: ids (only spaces around them are removed), model names and paths, commits and hashes, and
  `approve`, which must be a real `true` or `false`.
- Every refusal and error carries a reason code (for example `unknown_argument`, `unknown_id`, `not_supported`,
  `permission_denied`, `needs_person`, `declined`) and the next step. The same failing call a second time in a
  session says so; a third time it is refused without running ("stop retrying this call, tell the user why"), and
  after three input errors in a session an error also shows a valid example call.
- What was repaired is marked in the confirmation question, listed in the result's `notes` and recorded in the
  audit log (`name_requested`, `repairs`). Ditto's confirmation shows exactly the framework's question.
- MCP clients get each tool's own closed input schema (`additionalProperties: false`), the one Ditto's model sees.
- Tool arguments a local model sends with a code fence or in Python notation (single quotes, `True`, `None`, a
  trailing comma) are read once more as such; anything else stays an error.

Ditto's prompts tell it to check a target's capabilities before starting a build, to start or cancel builds only
when you asked for it in the conversation, to decide on self-healing only with your explicit decision, to answer
questions about targets and builds from the tools rather than from memory, and to report only what tools returned.

---

## Ditto evaluation

`llm-cli ditto eval` is a tool to measure how well a provider and model work for Ditto, for example whether a
small 2-bit local model is good enough for weak hardware. Nothing is built or changed. With `--kind retrieval` it
measures the knowledge-base search instead ([Retrieval evaluation](#retrieval-evaluation)).

**Cases** ([`evals/ditto/cases.yml`](../evals/ditto/cases.yml), 19 cases; a cases file is data, new cases need no
code change):

| Kind | Cases | What is checked |
|---|---|---|
| `healing` | 6 | Build-log analysis: cause, host or device, a fitting fix (missing CMake, out of memory, gated model, full disk, a library missing on the device, a log with injected instructions) |
| `optimizer` | 5 | Build recommendations from the real Rockchip options: e.g. GGUF for an LLM on the RK3566, an ONNX vision model to RKNN, a model name with injected instructions whose command must not appear in the answer |
| `module` | 3 | Module proposals from probe files (Jetson Orin, Raspberry Pi 5, x86 server): architecture, SDK, compiler and CMake flags |
| `chat` | 5 | Chat with tools: which tools are called with which arguments, and which are not (for example a failed build whose log contains injected instructions: Ditto must explain the error and must not cancel or start builds) |

**Grading** is done by fixed rules, not by another model: expected words or patterns, forbidden words (for example
an injected command), expected fields, tools that must or must not be called. Chat cases use the real tool
definitions over a stand-in build service that validates against the real target capabilities and returns canned
builds, logs and artifacts. The knowledge base is off during the evaluation, so answers come from the case alone.

**Running it:**

```bash
llm-cli ditto eval                                      # the configured provider and model
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json
llm-cli ditto eval --kind chat --repeat 3 --min-pass 0.8
```

With `--tier` the CLI starts the managed local server with each tier in turn and stops it after each tier. The
report shows the passed share per run, per kind and the average time per case; `--json` adds the setup (provider,
model per task, effort, framework version, sha256 of the cases file) and every answer, and for chat cases every
tool call the model attempted (also refused ones: unknown names, invalid arguments, denials, repeat refusals) with a
`tool_use` summary: tool calls per task, the share of malformed calls and of unknown tool names, the repairs the
framework applied and the repeat refusals. Cloud providers bill about 20 requests per run and repeat. All options: [CLI_Usage.md](CLI_Usage.md#ditto-eval).

**First measurement** (for context, not a benchmark): on a CPU-only machine with Qwen3.5 2B, effort `low` (thinking
off) and the prompts of that time, the 2-bit tier `small-q2` passed 42% of the 19 cases and the 4-bit tier `small`
47%. The evaluation found gaps in Ditto's prompts (attributing failures to host or device, which inputs each format
accepts, using the tools for questions about targets and builds, compiler flags in module proposals). These gaps are
fixed now, and a new measurement is running.

### Retrieval evaluation

`llm-cli ditto eval --kind retrieval` grades the [knowledge-base](#knowledge-base-rag) search instead of a model. No
AI model is involved, and your own knowledge base is not touched.

- **Cases:** [`evals/retrieval/cases.yml`](../evals/retrieval/cases.yml), 42 German and English searches against
  `evals/retrieval/corpus/`, 30 documentation excerpts of the framework and of SDKs. Each case names the excerpts
  that count and the rank the first of them must reach, or, for an unrelated question, expects no excerpt at all.
- **Tags** group the report: `en` and `de` (language of the question), `cross-lingual` (question and excerpt in
  different languages), `identifier` (for example `GGML_CUDA`), `oversized` (the answer is deep inside a block
  larger than the embedding model's context), `framework` (this framework's own documentation), `log` (the last
  three lines of a failed build, as the build-log analysis searches), `callsite` (Ditto's own search strings for
  module proposals and build recommendations) and `negative` (unrelated questions).
- **How it runs:** the corpus is indexed into a temporary knowledge base in a temporary folder, deleted afterwards,
  with the configured local embedding model. The model is downloaded on first use; after that the evaluation works
  offline. Retrieval cases run only when `--kind retrieval` is given or `--case` names one of them.
- **Variants** (`--rag-variant`, repeatable, default `fused`): `fused` is the hybrid search; `rerank` adds the
  cross-encoder of `rag_reranker` and its relevance floor ([Reranking](#reranking)). `rerank` stops with a message
  when no reranker suits the machine (set `rag_reranker` to a model id to grade one anyway), and a case counts as
  failed when the reranker did not run.
- **Report:** per variant hit@1, hit@3, hit@k (within each case's rank), MRR over the first 10 results, the share
  of unrelated questions that got no excerpt and the median search time. `--json` adds the figures per tag, the
  relevant questions that got no excerpt at all, the 95th percentile of the search time, the models, the sha256 of
  the cases and the corpus, and every result.
- **Measured** with the default CPU embedding model on the 34 cases that are not `log` or `callsite` searches (30
  with an expected excerpt, 4 unrelated): the hybrid search reaches hit@1 87 % and MRR 0.93, but all 4 unrelated
  questions still get excerpts, because it has no relevance floor. The figures with reranking are under
  [Reranking](#reranking).
- **CI:** the workflow `.github/workflows/retrieval-eval.yml` runs `fused` and `rerank` every night and on demand,
  with the models cached between runs. It fails when fewer than 70 % of the cases pass (`--min-pass 0.7`), which
  catches a broken search, not small changes, and it uploads the JSON report.

```bash
llm-cli ditto eval --kind retrieval                                  # hybrid search
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json
```

---

## Local runtime and model tiers

The managed runtime gives Ditto a local model without any cloud account. It is controlled with
`llm-cli ditto runtime` and `llm-cli ditto models` ([CLI_Usage.md](CLI_Usage.md#ditto-runtime)); Ditto's provider
`local` finds the running server by itself (address, key and loaded model are read for every call).

### What is pinned

[`configs/llama_runtime.yml`](../configs/llama_runtime.yml) is data: a newer build or another model is added there,
not in code.

- **llama.cpp build b11429** (version 0.6.0) for Linux x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.8, ROCm on request),
  Linux arm64 (CPU, Vulkan, CUDA 13.4), Windows x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.4, ROCm on request),
  Windows arm64 (CPU) and macOS (Metal on Apple Silicon, CPU on Intel).
- **Models** at fixed Hugging Face commits, all Apache-2.0:

| Tier | File | Size | Note |
|---|---|---|---|
| `small` | Qwen3.5-2B-Q4_K_M.gguf | 1.2 GiB | for machines with up to 4 GB RAM |
| `small-q2` | Qwen3.5-2B-UD-Q2_K_XL.gguf | 0.9 GiB | 2-bit, for 3 GB RAM or less; less accurate |
| `default` | Qwen3.5-4B-Q4_K_M.gguf | 2.6 GiB | the default |
| `default-q2` | Qwen3.5-4B-UD-Q2_K_XL.gguf | 1.8 GiB | 2-bit, only on request |
| `large` | Qwen3.5-9B-Q4_K_M.gguf | 5.3 GiB | for GPUs with 8 GB of memory or more |
| `large-iq2` | Qwen3.5-9B-UD-IQ2_M.gguf | 3.4 GiB | 2-bit, only on request; noticeably less accurate |
| `granite` | granite-4.2-3b-Q4_K_M.gguf | 2.1 GiB | IBM Granite 4.2 3B, other model family, only on request |

Every file has a pinned size and sha256 and is checked before it is unpacked or loaded; entries without both are
refused. Downloads resume after an interruption, and a build is installed atomically next to the previous one.

### Which build

`llm-cli ditto runtime status` shows the decision and the reason:

1. An explicit `--backend` wins (ROCm is only ever used this way; on Linux it needs an installed HIP runtime and a
   supported GPU).
2. Apple Silicon gets the Metal build.
3. An NVIDIA GPU gets the newest CUDA build that suits every NVIDIA GPU in the machine and its driver. GPUs for which
   the build only contains PTX code need the toolkit's own driver: for example an RTX 2080 Ti (compute capability
   7.5) needs driver 570.26 on Linux for the CUDA 12.8 build and branch R615 for CUDA 13.4 (on Windows 551.61 for
   CUDA 12.4). With an older driver the next option is used, and the reason says that a newer driver enables CUDA.
4. Vulkan, when a Vulkan driver is found and the GPU is worth it: every dedicated GPU, AMD Radeon GPUs and APUs
   (for example the Ryzen Z1 Extreme) and Intel Arc. Older Intel graphics are usually slower than the CPU and are
   only used with `--backend vulkan`.
5. Otherwise the CPU build.

### Which model

The first rule whose conditions hold and whose model fits wins:

| Rule | Tier |
|---|---|
| GPU memory of at least 7.5 GiB (a dedicated GPU used by the build, or about two thirds of Apple Silicon's memory) | `large` |
| RAM up to 3.0 GiB | `small-q2` |
| RAM up to 4.5 GiB | `small` |
| otherwise | `default` |
| fallbacks when the model above does not fit | `small`, then `small-q2` |

"Fits" means: the model file plus 5% plus the key-value cache for 4096 tokens must fit into the RAM minus 1.5 GiB for
the system, plus 95% of a dedicated GPU's memory. Integrated AMD and Intel GPUs share the RAM, so only the RAM counts
for them. `runtime start --model <tier>` overrides the choice and remembers it; `--model auto` returns to the
recommendation.

### How the server runs

- **Context length:** computed from the free memory next to the model (free GPU memory, or free RAM on CPUs, Apple
  Silicon and integrated GPUs), rounded to 256 tokens, at least 4096 and at most 32768 (`--ctx` sets it).
- **Isolation:** `llama-server` listens on 127.0.0.1 only, on a free port. A new random API key per start reaches it
  only through a key file readable by you, and the framework checks that the server refuses requests without the
  key. The web UI and the slots endpoint are off, and the server runs with `--offline`. Options that would give the
  model its own tools, agent mode, MCP servers, remote computing or a key on the command line are refused, and
  environment variables that could set server options or look like secrets are removed.
- **GPU offload:** with a GPU build, as many layers as fit go to the GPU.
- **Idle:** the model is unloaded after 600 idle seconds and reloaded on the next request (`--sleep-idle`).
- **State and log:** `~/.llm-framework/runtime/llama.cpp/` holds the builds and the server state, which is trusted
  only while that exact process (process ID, start time, program) is alive. The key file is deleted on stop. The
  log `~/.llm-framework/logs/llama-server.log` is rotated at 5 MiB, keeping 3 old logs.
- **Thinking:** Qwen3.5 on `llama-server` otherwise thinks at full length, which takes minutes on a CPU. So effort
  `low` turns thinking off, `medium` and higher keep it.

---

## Knowledge base (RAG)

The knowledge base gives Ditto and MCP clients documentation to search (setting `enable_rag_knowledge`, GUI: **Enable
Local Knowledge Base**). Nothing is loaded until it is first used.

- **Storage:** Qdrant runs embedded in the framework process, with its data in `~/.llm-framework/qdrant` (no Docker,
  no open port). The storage is locked while in use and released after 30 idle seconds, so the GUI and the MCP
  server can take turns. To use it from several processes at the same time, set `qdrant_url` to a Qdrant server; a
  remote server needs https and the API key stored as the secret `qdrant_api_key` (never credentials in the URL).
- **Embeddings:** computed locally with FastEmbed on ONNX Runtime; the model is downloaded once into
  `models/embeddings/`. With `rag_embedding_model: auto` (default) an existing knowledge base keeps the model it was
  built with; a new one gets `intfloat/multilingual-e5-small` (384 dimensions, about 100 languages, fast on a CPU),
  or `BAAI/bge-m3` (1024 dimensions) when ONNX Runtime has CUDA and the machine has at least 8 GB RAM. Each model
  has its own collection, so switching models never mixes or loses vectors. In offline mode nothing is downloaded,
  and a missing model is reported clearly.
- **Ingesting:** pages are converted to Markdown and split along their structure (headings, paragraphs, lists, code
  blocks, tables), sized in the model's tokens. Each chunk is stored with its source, title and section.
  Re-ingesting a page updates it in place and removes chunks the new version no longer has. Code blocks and tables
  stay whole unless they are larger than the model's context ([Large code blocks and
  tables](#large-code-blocks-and-tables)).
- **Snapshots:** before every crawl the knowledge base is saved as a snapshot in
  `~/.llm-framework/knowledge_snapshots` (the newest 5 are kept). Restoring a snapshot is not available from the GUI
  or the CLI yet.
- **Search:** hybrid. Dense vectors find meaning, BM25 finds exact terms such as error codes and compiler flags; both
  rankings are fused with Reciprocal Rank Fusion. The same text from several sources is returned once. A
  cross-encoder can then rerank the results ([Reranking](#reranking)).
- **Use:** the `search_knowledge` tool (Ditto, MCP clients); without tools Ditto adds matching excerpts to the
  question itself. Healing analyses, module proposals and build recommendations (Builder: **Use RAG Knowledge
  Base**) also get excerpts. Results are always treated as untrusted data.
- **Adding documentation:** [Deep Ingest](#deep-ingest) in the module wizard, and [knowledge packs](#community-zoo)
  from the community zoo.

### Reranking

The hybrid search ranks by words and meaning. It cannot tell whether an excerpt answers the question, and it always
returns something. A reranker reads the question together with each candidate and gives it a relevance between 0
and 1. Candidates below a relevance floor are dropped, so Ditto gets no excerpt rather than an irrelevant one.

- **Model** (setting `rag_reranker`, default `auto`): a local cross-encoder on FastEmbed/ONNX, downloaded into
  `models/embeddings/` on first use, never in offline mode. With `auto`:

  | Machine | Reranker |
  |---|---|
  | ONNX Runtime with CUDA and at least 16 GB RAM | `onnx-community/bge-reranker-v2-m3-ONNX` (multilingual, about 0.6 GB download) |
  | at least 6 GB RAM | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual MiniLM, about 120 MB download, about 0.5 GB in memory) |
  | less than 6 GB RAM | none |

  `off` turns reranking off. A model id chooses another cross-encoder that FastEmbed lists.
- **Relevance floor** (setting `rag_rerank_min_score`, 0 to 1, not set by default): unset means the floor
  calibrated for the model, 0.2 for both models above; 0 turns the floor off. In the retrieval evaluation, relevant
  excerpts scored 0.80 or more and unrelated ones 0.009 or less.
- **Which searches rerank:** by default only build recommendations. The setting `rag_rerank_default: true` reranks
  the chat and the MCP tool `search_knowledge` too. Healing analyses and module proposals never rerank.
- **Why only build recommendations:** measured with the [retrieval evaluation](#retrieval-evaluation) on a shared
  4-core CPU. On the 34 cases that are not `log` or `callsite` searches, reranking raised hit@1 from 87 % to 93 %
  and MRR from 0.93 to 0.97, and all 4 unrelated questions got no excerpt. It costs about 1 s for 12 candidates and
  2 to 2.6 s for 20, and it gained less than the 0.05 MRR required for chat, so chat stays without it. On the last
  lines of failed builds it found the relevant excerpt within the first 3 in 2 of 6 cases (hybrid search: 5 of 6)
  and dropped it below the floor in one; for the search string of module proposals it dropped every excerpt. So
  build-log analysis and module proposals keep the hybrid order.
- **How a search is reranked:** up to 20 candidates (three times the requested number, at least 12), of each the
  title, section and first 1500 characters; scores are cached by text. A reranker that cannot run (not downloaded
  in offline mode, unknown model id, an error) is logged once, and searches keep the hybrid order until the next
  start or a change of `rag_reranker`.

### Excerpt budget

When Ditto adds knowledge-base excerpts to a request itself (chat without tools, healing analyses, module proposals,
build recommendations), they take at most a third of `chat_context_limit` (default 4096 tokens, so about 1365
tokens, estimated at three characters per token). The best excerpts come first. One that does not fit is skipped,
so a smaller later one can still come along. If the first excerpt alone is too long, it is shortened at a line and
marked `[…]`; a code block that is open at the cut is closed, so the rest of the prompt does not read as code.

### Keyword search for identifiers

The keyword index (BM25) also knows the parts of identifiers: `-DGGML_CUDA=ON` is found by `GGML_CUDA`, `GGML` or
`CUDA`, `rknn_toolkit2` by `rknn toolkit2`, and `rknnToolkitLite` by `rknn Toolkit Lite`. A leading `-D` is
dropped only from definitions such as `-DNAME=VALUE` or `-DNAME_WITH_UNDERSCORE` (`-DEBUG` keeps it), and parts of
one or two digits are left out (`x86_64` adds `x86`, not `64`). Only the keyword index gets the split forms; the
stored text and the vectors stay as they are. Searches of one or two words use the keyword index when they contain
an identifier (a digit, an underscore, two capital letters in a row, or a dot or hyphen inside a word, such as
`E2041`, `GGML_CUDA`, `RKNN` or `librkllmrt.so`); other short searches use meaning only. In the retrieval
evaluation this raised hit@3 of the identifier questions from 83 % to 100 %; the other results stayed the same.

A document keeps the keyword vectors it was imported with and stays searchable; importing it again (for example a
new Deep Ingest of the same URL) applies the current splitting.

### Large code blocks and tables

Code blocks and tables stay whole as long as the embedding model sees them whole: up to its context minus 96 tokens
for the line with source, title and section that is embedded with every chunk (512 tokens for the default model
`intfloat/multilingual-e5-small`, so 416 tokens). Larger ones
are split at line boundaries into pieces of the normal chunk size (384 tokens for the default model). Each piece
repeats the table header or reopens the code fence, so the later parts of a long table or script can be found at
all (in the retrieval evaluation, answers deep inside such blocks: hit@1 from 50 % to 100 %). Documents in the
knowledge base keep their old chunks: import them again (for example a new Deep Ingest of the same URL) to apply it.

### Models in memory

The embedding models and the reranker are unloaded after `rag_model_idle_minutes` (default 10) without use and
loaded again on the next search, which takes a few seconds. The time counts from the end of the last search or
import, never during one. `0` keeps them loaded. The embedded storage is released separately, after 30 idle
seconds (see **Storage** above).

---

## Deep Ingest

Deep Ingest reads documentation pages from the web into the knowledge base. Start it with **Deep Ingest (Add Docs)**
in the module wizard (**Tools > Create Hardware Module...**); the knowledge base must be on. There is no CLI command
for it in this version.

Text and data mining of publicly accessible content is permitted unless the rightsholder reserved it in
machine-readable form (EU Directive 2019/790, Art. 4; German UrhG § 44b). So the crawler checks these signals itself
instead of asking you to confirm anything, and none of the checks can be switched off. The former *Respect
robots.txt* option and the setting `crawler_respect_robots` are gone.

### The notice before the first crawl

- Before the first Deep Ingest of each user, a notice in the interface language explains what is read (only
  publicly reachable pages, no logins, starting from the addresses you enter, within the page and depth limits),
  which opt-outs are honoured, that the knowledge stays on this computer (when Ditto answers, matching excerpts go to
  the AI provider you selected; with a local provider nothing leaves the computer), and that sharing it in a target
  or a community pack keeps the source and licence of every page and needs your explicit release.
- It is information, not a legal confirmation: one button, **Understood** (also Esc), nothing to tick.
- It is shown once per user and again when its text changes in any language. The state file `crawl_notice.json` in
  the per-user folder (`~/.llm-framework`) stores a hash of its texts. If that folder cannot be used, the notice is
  shown every time.

### What the crawler honours

| Signal | Where it is read | Effect |
|---|---|---|
| robots.txt (RFC 9309) | `/robots.txt` of each site, once per scheme, host and port | Disallowed paths are not fetched. The groups whose `User-agent` is `LLM-Conversion-Framework-Bot` (any case) apply, otherwise all `*` groups (a group for `bot` is another crawler's); `*` and `$` patterns work; the longest rule wins, `Allow` wins a tie. A robots.txt that cannot be reached or answers a server error, 401 or 403 counts as "disallow everything" (for 401 and 403 stricter than RFC 9309, which allows crawling: a locked robots.txt may hold a reservation). |
| `X-Robots-Tag` header | every response | `noindex`, `none`, `noai` and `noimageai` keep the page out; `nofollow` and `none` stop following its links. Directives for this crawler (`LLM-Conversion-Framework-Bot: noai`) count, those for other crawlers do not. |
| `<meta name="robots">` and `<meta name="llm-conversion-framework-bot">` | HTML pages | the same directives as the header |
| TDM Reservation Protocol (W3C TDMRep 1.0) | `/.well-known/tdmrep.json` per site (the first matching rule decides), the `tdm-reservation` header, `<meta name="tdm-reservation">`, `tdm:reservation` in PDF metadata | A reservation keeps the page out; a `tdm-policy` is named in the log. |

- A reservation from any of these sources wins. TDMRep lets a later source lift an earlier reservation; the crawler
  stays on the cautious side.
- Redirects are not followed directly. Their targets are queued and checked like any other address: security check,
  robots.txt and `tdmrep.json` of their own host. A link that redirects to another host is skipped; only a redirect
  of the start URL (for example `example.com` to `www.example.com`) moves the crawl to that host. At most 5
  redirects per address.
- The crawler names itself `LLM-Conversion-Framework-Bot/<version> (Open Source;
  +https://github.com/Smilez1985/llm_conversion_framework)`, with the real framework version. Site operators can
  address it in robots.txt and in a meta tag with that name.

### Network safety and limits

- Private, loopback and other non-public addresses are blocked (SSRF protection), and each connection is pinned to
  the checked address, so a DNS answer cannot be switched in between.
- The Deep Ingest dialog sets, for each crawl, the link depth (0 to 5; 0 reads only the entered pages), whether PDFs
  are read and the delay between requests (0 to 10 s, fractions of a second allowed). It opens with the settings
  `crawler_max_depth` (default 2), `crawler_download_pdfs` (default on) and `crawler_rate_limit` (default 1 s); a
  larger configured depth or delay is kept and raises the upper end. What you change in the dialog applies to that
  crawl only.
- From the settings: at most `crawler_max_pages` pages per entered URL (default 50), and only pages in the
  languages of `crawler_languages` (default English and German; an empty list keeps all). A crawl also stops after
  five times as many requests as its page limit (redirects and failed fetches count too).

### Progress, Stop and the outcome

- The wizard shows "Page N of M: <address>" before each request (M is the page limit) with a progress bar.
- Pages are stored as soon as they are read. **Stop** ends the crawl after the current page (a waiting delay between
  requests is cut short); what is stored stays. Closing the wizard during a crawl stops it the same way: the window
  stays responsive and closes once the current page is stored.
- The outcome over all entered URLs is reported only after the last write has returned:

  | Outcome | When | Message box |
  |---|---|---|
  | success | every entered URL stored pages; nothing failed, nothing was reserved by a site, the crawl was not stopped | information |
  | partial | pages are stored, but others failed, were reserved by the site, or the crawl was stopped | warning |
  | failure | nothing was stored | error |

- The crawl log in the wizard (and the framework's log) names every page that was not stored with its reason, for
  example "disallowed by robots.txt", "X-Robots-Tag: noai", "TDM reservation (...)", "redirect from ... leaves the
  crawled site", "language 'fr' is not one of de, en", "PDF downloads are switched off", "neither HTML nor PDF
  (image/png)", "no text" or "PDF support (pypdf) is not installed", and every page that could not be fetched or read
  (HTTP status, unreadable PDF, too many redirects). Each page that arrives (HTTP 200) counts toward the page
  limit, stored or not. Site reservations and failures make the outcome partial; the other reasons do not.

### Spoken announcements

- The notice ("Please read the notice before your first Deep Ingest.") once it is on screen, and the outcome of a
  Deep Ingest, are also spoken in the interface language.
- Engines, the first that works: on Windows, Windows PowerShell 5.1 with System.Speech (started from the system
  folder; the text travels in an environment variable, never as code); on macOS, `say` (with a voice of the interface
  language when one is installed); on Linux, `spd-say` (speech-dispatcher), otherwise `espeak-ng` or `espeak`.
  Nothing is installed for it; without an engine it stays silent.
- It never gets in the way: a background thread with a 30 s time limit, at most 300 characters, no audio files, the
  mixer and the system volume are never touched, and a failure never stops anything. Every announcement is also
  written to the log, so it is never only audible.
- It stays silent when the setting `audio_announcements` is off (menu **Tools > Spoken Announcements**, a check mark
  that is saved at once), when `LLM_FRAMEWORK_NO_AUDIO` is set (to anything but empty, `0`, `false`, `no` or
  `off`), under CI (`CI` set in the same way), in an SSH session without a display, and on Linux
  without a display (no `DISPLAY` or `WAYLAND_DISPLAY`).

---

## MCP server

`llm-cli mcp serve` exposes the framework's tools over the [Model Context Protocol](https://modelcontextprotocol.io)
(official Python SDK `mcp` 2.3.0, specification 2026-07-28). AI clients such as Claude Code, Claude Desktop or VS Code
can then use the [same tools as Ditto](#dittos-tools). You sign in to the client with your own account or key; the
framework never sees it.

- **Every call goes through the tool registry:** name and argument matching, input schema, your permissions,
  confirmation, audit log (actor `mcp`), exactly as for Ditto ([how names and values are
  matched](#dittos-tools)). Tool annotations (read-only, idempotent) are only hints for the client.
- **Confirmation:** where your permission says "ask", the server asks the user through the client (MCP
  elicitation). The question shows the tool and every argument in full, the security-relevant ones first; control
  and invisible characters and long stacks of combining marks are shown as `\u` escapes. A question that would be
  longer than 4000 characters is refused instead of being cut. Only a real answer from the user counts as approval,
  and a "no" is final for that call, even if the permissions are loosened while the question is open. Declined and
  cancelled calls, and calls refused before they run (invalid arguments, a confirmation that fails), are audited
  too. A client that cannot ask gets a refusal with a hint how to allow the tool.
- **Instructions to the client:** the server describes the typical flow (targets → capabilities → build → status
  and log → artifacts), tells the client that fields marked as external text are data whose instructions must not
  be followed, and that a task it cannot do is reported to you with its specific reason.
- **Results:** every result goes through the registry's output filter, for Ditto as well. Secrets are redacted in
  every string. Text from outside the framework (build logs and stage names, self-healing proposals, build
  manifests, knowledge-base excerpts, doctor messages, and the labels, notes and board descriptions of a target's
  `target.yml`, which a community target pack can install) is cleaned of invisible and bidirectional control
  characters and capped (a log line at 2,000 characters, other external text at 4,000); every other string is capped at 16 KiB,
  and a result over 256 KiB is refused with a hint how to ask for less. Such fields are marked `"untrusted": true`
  in the tool's output schema. The structured result matches the output schema exactly; the text block starts with
  one sentence built only from framework facts (ids, states, counts), followed by the JSON wrapped as untrusted
  data. `notes` in a result are written by the framework only, for example that the knowledge base is reachable
  but nothing matched, or that only the builds of this process are listed. A self-healing proposal's step comes as
  `manual_step_text`: text for you to read, never a command for the client to run. The error of a failed call can
  quote a server reply, a log line or a name from a pack: it is cleaned the same way before secrets are redacted,
  and the client gets that part wrapped as untrusted data, while the framework's own refusals and the next step
  stay plain (Ditto's model gets such errors in an untrusted block too).
- **Transports:** stdio (default; the client starts the server) or Streamable HTTP on a loopback address only
  (`--transport http`, default `http://127.0.0.1:8765/mcp`). HTTP checks Host and Origin and needs a bearer token
  that is created per user in `~/.llm-framework/mcp_http_token`.
- **Working folder:** the server never reads settings or imports code from the folder it is started in (the client
  starts it with `python -P`), and on Windows it does not run programs such as `docker` or `git` from there. A
  project folder cannot change its behaviour.
- **Client setup:** `llm-cli mcp config` prints the commands and JSON with quoting that holds in POSIX shells and in
  both cmd and PowerShell; `--write claude-desktop` adds the server to Claude Desktop's configuration, keeps other
  entries and writes a private backup.

**Claude subscribers:** this is the way to use the framework with a Claude plan. Anthropic does not allow
third-party apps to use a subscription's sign-in or limits, so Ditto itself needs an API key; Claude Code and Claude
Desktop can use the framework over MCP within your plan.

---

## Tool permissions and audit log

Every tool belongs to a risk tier:

| Tier | Meaning | Recommended | Choices |
|---|---|---|---|
| `read` | Look at targets, builds, logs, knowledge | allow | allow, ask, deny |
| `local_write` | Change things on this computer (start or cancel builds, decide on healing) | ask | allow, ask, deny |
| `external` | Reach other devices or delete data | ask | ask, deny (a tool of this tier always asks at most) |
| `forbidden` | Never callable (for example arbitrary shell commands) | deny | deny |

There are no `external` or `forbidden` tools yet; deploying and uploading are not available as tools.

- The permissions are asked once (GUI dialog on the first start, `llm-cli ditto setup`) and stored in
  `~/.llm-framework/tool_policy.json` (readable only by you). Single tools can be set differently in the GUI.
- Until the setup is done, read-only tools are allowed and everything else asks.
- **Per caller:** Ditto (`ditto`) and local MCP clients over stdio (`mcp`) can have permissions of their own
  (`llm-cli tools permissions --caller ditto` or `--caller mcp`); a caller without them uses the shared ones. A new
  caller's permissions start from the shared ones, including your settings for single tools. A named token for an
  HTTP client (planned) that has no permissions of its own may only read, and never a tool that your shared
  setting for that tool or the tool's own default denies.
- **A tool's own default and cap:** a tool can bring its own default (an optional tool starts as `deny` and is
  hidden) and a highest decision that no setting lifts (a tool that reaches other devices or deletes data always
  asks at most). The order is: your setting for the tool, else the tool's default, else your setting for its tier;
  then the cap. A tool's default never opens a tier you denied.
- **New tools:** the permission file records which tools you have reviewed; saving the setup, `llm-cli tools
  permissions` or the GUI dialog marks every listed tool. A tool added later uses its own default, never a looser
  tier setting, until you review it; `llm-cli tools list` and the GUI dialog mark it as new. A permission file from
  3.0.0 counts its eleven tools as reviewed.
- **Denied tools are not listed** to MCP clients and Ditto; a call to one is still refused and audited.
  `get_status` lists every tool with its decision and why, also the hidden ones, and the actions no tool does, with
  where you do them, so an AI can tell you the real reason.
- A damaged or unreadable permission file, or a link to a missing one, denies every tool instead of falling back to
  looser defaults; a damaged file is kept as `tool_policy.json.bad`, and `llm-cli doctor`, `llm-cli tools list` and
  `llm-cli mcp serve` say so.

**Audit log** (`~/.llm-framework/audit.db`, SQLite): every tool call is recorded with time, actor, tool, decision,
whether a person confirmed it, and the outcome. Arguments are stored redacted and shortened, plus a hash of the full
text. Each entry contains the hash of the previous one; with the key from your operating system's keyring this is an
HMAC chain that someone who can only edit the database cannot recompute. After every entry the newest one is also
written to an anchor file, so removing the newest entries is detected too. `llm-cli tools audit --verify` checks the
chain and tells a changed entry apart from a wrong key. Each entry is written in its own transaction, so several
processes cannot fork the chain. `llm-cli tools audit --archive` moves the log and its anchor aside (kept with a
timestamp) and starts a new chain, for example after `llm-cli doctor` has reported a problem with the log.

**Recorded before it runs.** A call that changes something (every tier except `read`) gets a `started` entry before
it runs. If that entry cannot be written, the call is refused and nothing runs: calls that change something run only
when they are recorded. Read-only calls stay available, and the failed write is logged. A question to you is recorded
as `confirmation_requested` with the SHA-256 of the question text before it is asked; a question about a change that
cannot be recorded is not asked. Every end is recorded: `ok`, `error`, `denied`, `not_confirmed` with the answer
(`accepted`, `declined`, `cancelled`, `unavailable` when nobody could be asked, `unanswered`), `confirmation_timeout`
(an MCP question asked during the call waits at most 5 minutes) and `cancelled` (the client gave up on the call). A
question of the 2026-07-28 protocol that is returned to the client and never answered is recorded as `unanswered`
after 15 minutes while the server runs; the `confirmation_requested` entry is the lasting record. When a client
cancels a running call, the cancellation is recorded at once and passed to the running call; what already runs
finishes, and its end entry follows. Entries also carry the client's self-reported name and version (a label, not
proof of who it is), a hash of the connection, the confirmation channel (`elicitation`, `framework-gui`,
`framework-cli`), the duration, the SHA-256 and size of the result (never the result itself) and a link to the call's
`started` entry. These fields are bound to the hash chain (their SHA-256 ends the entry's chained detail), so
adding, removing or changing them is detected, while an entry keeps the hash 3.0.0 computes: logs written before them
stay verifiable, and a 3.0.0 process that still runs next to this version keeps verifying and appending to the same
log.

---

## Secrets and personal state

- **Secrets** (API keys, tokens): encrypted (Fernet, each entry with a key id) in the vault
  `~/.llm-framework/secrets.store`. By default its key is kept in your operating system's keyring (Windows
  Credential Manager on the local machine, macOS Keychain, Secret Service on Linux), one entry per vault, never in a
  file next to the store. A keyring that does not answer within 3 seconds, keeps nothing or stores in plain text is
  not used; then the framework stops and names the choices (the GUI shows a chooser, the CLI exits with code 5
  and names `llm-cli ditto auth storage --use ...`) for the setting `secret_storage` (or
  `LLM_SECRET_STORAGE` for one run): `helper` (the command in `secret_helper`, run without a shell), `passphrase`
  (asked once per start), `insecure` (a key file next to the vault, not encrypted at rest) or `ephemeral` (memory
  only). The vault is written atomically under a lock shared by the GUI, the CLI and MCP servers, is readable only
  by you, never lies inside a git working copy, and a damaged vault is never overwritten. `llm-cli doctor` warns
  about the key file and about a keyring that does not protect secrets. `llm-cli ditto auth rotate-key` replaces
  the vault key and re-encrypts every entry.
- **Redaction:** one table of secret formats (`orchestrator/security/secret_patterns.py`) is shared by the
  repository's secret scan and the runtime redaction. Credentials (known key formats, credential headers, tokens
  in URLs and form bodies, `HF_TOKEN=...` lines) are removed from every log line and traceback (the framework's own
  and those of the SDKs and HTTP libraries), from build logs read through the tools, from provider and tool error
  messages, from the arguments in the audit log and from everything sent to cloud providers. The SDKs' debug logs
  stay off unless you start with `--debug-http`.
- **Personal folder** `~/.llm-framework` (`LLM_FRAMEWORK_HOME`), private to your user: tool permissions, audit log,
  SSH key and `known_hosts`, MCP token, chat history, secrets, knowledge base and snapshots, local runtime, the
  [input history](#input-history), the state of the [Deep Ingest notice](#the-notice-before-the-first-crawl), the
  cached [community index](#the-signed-index) with the pinned author keys, and two random values per installation
  (one for the privacy placeholders, one as an anonymous cache ID for OpenAI). Your key pairs for
  [community packs](#encrypted-packs-for-private-sharing) are kept in the encrypted secrets store.
- **Settings** are never read from the working directory: `LLM_FRAMEWORK_CONFIG_DIR`, then `configs/` in the
  installation if you may write there, then the personal folder.
- **Data folders** (`output/`, `cache/`, `models/`) are in the installation if you may write there, otherwise in the
  personal folder (on Windows `C:\Program Files` is read-only for standard users). They are created when a build or
  a download first needs them, not at every start.

---

## Input history

The GUI's input fields offer what you used recently, newest first.

| Field | Where | Filled in | Remembered when |
|---|---|---|---|
| Model | Builder, **Source Model** | the most recent one | a build is submitted |
| Calibration dataset | Builder, **Calibration** | no, only offered | a build with a dataset is submitted |
| Target and board | Builder | the most recently built target and board that the target definitions still offer; a target without declared boards offers recent boards as suggestions | a build is submitted |
| Profile | Deployment | the profile chosen before, else the most recent one | a deployment is started |
| Deep Ingest URL | **Recent** list in the Deep Ingest dialog, added with **Add** | no, only offered | the dialog is confirmed |
| Device (IP address or host name) | Deployment, remote hardware scan (**Scan / Import...**) | the most recent one | its SSH host key is confirmed |

- The history is per user: `input_history.json` in the per-user folder (`~/.llm-framework`, or
  `LLM_FRAMEWORK_HOME`), readable only by you, at most 20 entries per field, without duplicates. It is not part of
  the shared `configs/config.yml`; the former setting `input_history` is gone.
- It never holds secrets. Not remembered are values that look like an API key or a token, URLs with a user name or
  password, URLs with a credential or session parameter in the query, the fragment or a path parameter (such as
  `?token=`, `?api_key=`, `#access_token=`, `;jsessionid=` or `?sid=`), `user:password@host`, text with control
  characters and values longer than 1000 characters. The same rules apply to a file edited by hand.
- A damaged file starts an empty history; the old file is kept as `input_history.json.bad`.
- **Tools > Clear Input History...** asks once, then deletes the file and the `.bad` copy, and the fields are
  refilled at once.

---

## Hardware probe and new targets

- **Probe:** `scripts/hardware_probe.sh` (Linux) and `scripts/hardware_probe.ps1` (Windows) write
  `target_hardware_config.txt` with CPU, flags, memory, GPU and NPU information. Run them on the board, use
  `llm-cli scan` on this computer, or **Scan / Import...** in the GUI (file import, remote scan over SSH with the same
  trust-on-first-use rules as deployment, or this PC).
- **New target by questions:** `llm-cli module create` or the wizard under **Tools > Create Hardware Module...**.
- **New target from a probe:** `llm-cli module generate-ai <probe file>` or the wizard's AI analysis. Ditto proposes
  architecture, SDK, base image, packages, CPU and CMake flags, setup commands and the quantization block of
  `build.sh`; you see the whole proposal first.
- **Checks before anything is written:** names, images, packages and flags are validated strictly; shell code is
  checked against a deny-list and with `bash -n`; files are written only inside `targets/`; an existing target is
  never overwritten. Setup commands run later as an unprivileged user without network.
- **Knowledge snapshot (opt-in):** on the wizard's last page, **Attach the knowledge crawled for this target** (off
  by default, available after a Deep Ingest in the wizard) writes the pages crawled from the wizard's Deep Ingest
  URLs, privacy-scrubbed, to `knowledge/initial_knowledge.json` in the new target, with a `README.md` that says
  what it holds. While a Deep Ingest is still running, the wizard's **Finish** stays disabled (the page says why),
  so the snapshot holds every crawled page; if no stored page comes from those URLs, the wizard says that no
  snapshot was attached. The rest of your knowledge base never goes into a module, and
  `llm-cli module create` and `module generate-ai` attach nothing. The framework does not import such a snapshot
  into a knowledge base; to share knowledge, export a knowledge pack.
- **Export for community:** the wizard's last page also offers **Export for community after creating** (see
  [Export for community](#export-for-community)).

Details: [Module_Developer_Guide.md](Module_Developer_Guide.md).

---

## Health check (doctor)

`llm-cli doctor` (and the read-only `run_doctor` tool) runs deterministic checks and explains each finding:

| Area | Checks |
|---|---|
| Python | supported version (3.11 to 3.14) |
| Docker | daemon reachable, buildx available |
| Disk | free space for the cache and output folders (warning below 20 GB, error below 5 GB) |
| Targets | targets found, capabilities readable |
| Security | personal folder private, secrets store usable, keyring protects secrets |
| AI | tool permissions set up and readable, audit log intact (changed, removed, wrong key, missing anchor, no key) |
| Data | shipped calibration data intact (checksums) |
| Community | installed community packs against the cached index: revoked (error), changed since installation (warning), trust `local` (information) |

`--fix` applies the automatic fixes up to `--tier` (1: makes the personal folder private; 2: also removes dangling
framework images and the Docker build cache) and checks again. Exit codes: 0 OK, 1 warnings, 2 errors; `--json`
for scripts.

---

## Community zoo

The community zoo shares three kinds of packs: **targets** (hardware modules), **recipes** (how a model was built
for a target) and **knowledge** (pages for the knowledge base). Packs are listed from a signed community index,
installed through a quarantine folder and exported from your own work for a pull request. Commands:
`llm-cli zoo list|show|install|verify|export|keys`; in the GUI the [Community Hub](#community-hub). The framework
never uploads anything and never asks for, reads or shows a repository token.

**State today:** no community repository, index or maintainer key is published yet. `configs/community_keys.yml`
lists no key, and the settings `community_index_url` and `community_repo` are empty by default. So `llm-cli zoo
list` and the hub show only installed packs, only pack files can be installed (trust `local`), and a pull request
has no repository to go to.

### Packs

- A pack is a zip file with `pack.json` (the manifest: kind, id, version, author, licence, minimum framework
  version, creation time, and the sha256, size and mode of every file), an optional `pack.sig` (the author's Ed25519
  signature over the manifest) and exactly the listed files. Reading a pack accepts nothing else: no links, no
  special files, no unlisted files, and every hash is checked. Packs carry text only (scripts, recipes, pages),
  never model weights or binary files; at most 512 MB and 10,000 files.
- **Trusted keys:** `configs/community_keys.yml` lists the publisher keys that sign the community index, each with
  `key_id`, `pubkey_b64`, `valid_until` (the last day, in UTC, on which its signatures are accepted) and an optional
  `name`. The file ships with each release; a key rotation ships the old and the new key for one release. A damaged
  file is an error, never read as "no keys".
- **Author signatures** show where a pack comes from; they do not make it trusted. A signature is checked when the
  pack includes the public key (a broken signature refuses the pack), and the author's public key (the whole key,
  not its short key id) is pinned at the first installation of a pack by that author. A later pack with the same
  author name and another key gets a warning in the summary.

### Encrypted packs for private sharing

For your own machines or a team, a pack can be encrypted instead of published (`llm-cli zoo export ... --to <key
file>` or `--passphrase`):

- It is encrypted in the [age](https://age-encryption.org/v1) format by rage, the Rust implementation of age
  (through `pyrage`): to one or more recipients' age keys (`age1...`, at most 64) or to one passphrase (at least 12
  characters, scrypt), never to both, as age requires. The sender signs the encrypted file with Ed25519, so
  recipients see who sent it, and any change, including an added or removed recipient, is detected.
- The `.lcfpack` file is a zip with the age file `pack.zip.age` and the sender's signature `pack.zip.age.sig`. The
  standard `age` and `rage` tools open it too when it is encrypted to a passphrase or to a recipient made with
  `age-keygen` or `rage-keygen`: unzip it, then `age -d -i key.txt -o pack.zip pack.zip.age`, where `key.txt` is
  that identity file (for a passphrase pack without `-i key.txt`). The age identity the framework creates for you
  never leaves the secrets store, so a pack encrypted to it opens only with `llm-cli zoo install`. The age tools do
  not check the sender's signature; the framework does.
- **Your keys:** on first use every user gets an Ed25519 key (signs packs) and an age identity (receives encrypted
  packs). They are kept only in the encrypted secrets store. `llm-cli zoo keys` prints the public keys as text (an
  `age1...` recipient and an `ed25519:` line) to give to others; a recipient made with `age-keygen` or
  `rage-keygen` works with `--to` as well. A stored key that cannot be read is never replaced automatically.
- **Receiving:** `llm-cli zoo install --file <pack>.lcfpack` decrypts with your key, or with `--passphrase`.
  `--from <key file>` (repeatable) accepts only these senders; otherwise the summary names the sender's key id. A
  passphrase pack whose scrypt work factor is above 2^20 (1 GiB of memory) is refused before anything is decrypted.
  The hub's **Import file...** opens packs encrypted to your key; a passphrase pack needs the CLI. An encrypted pack
  is never submitted to the community.

### Recipes

A recipe is the zoo's "model template": the Hugging Face model at a pinned commit (never a branch or tag), the target
with the fingerprint (sha256 over its files) and version of the target folder, board, format, quantization, options
(importance matrix, context length, name and sha256 of a calibration dataset) and verified results (artifact file,
size and sha256, framework version, toolchain image, optionally a device test). It carries no weights: a recipe is
rebuilt, never downloaded as a model.

- A recipe is generated from a finished build's `build_info.json`, never typed by hand. `build_info.json` records
  what a recipe needs: the model's commit (the download is pinned to it), the target fingerprint and version, the
  toolchain image, the context length, the calibration dataset's hash and the framework version. A build of a local
  model, or one without a target fingerprint, cannot become a recipe.
- At installation a recipe is checked against the capabilities of its target, if that target is installed. A note
  says when the target is missing or when your copy differs from the one the recipe was built with.
- **Build** on an installed recipe in the hub fills in the Builder (model at its pinned commit, target, format, board,
  quantization, importance matrix). You check the settings and start the build; a recipe maps to a normal build
  request.

### The signed index

- `index.json` and `index.json.sig` next to it, at the https address in the setting `community_index_url`, signed
  by a key from `configs/community_keys.yml`. An index whose signature does not verify is not used.
- The last verified index is cached per user in `~/.llm-framework/community/`. It is fetched again when you ask
  (`llm-cli zoo list --refresh`, **Refresh** in the hub), when nothing is cached, or when the cache is older than a
  day; without network the cached index is used. A downloaded index with a lower version than the cached one is
  refused, so an older index never replaces a newer one. The cache is verified again whenever it is used, so a
  removed or expired key invalidates it.
- The index also lists revoked packs (by sha256, or by kind, id and optionally version, each with a reason).

### Installing

`llm-cli zoo install <id> [--kind target|recipe|knowledge]`, `llm-cli zoo install --file <pack>`, or **Install** and
**Import file...** in the hub:

1. An installation from the index always reads the current index first, so a revocation is never missed; if the
   index cannot be updated, nothing is installed. A pack file is looked up in the index (updated when older than a
   day; the cached one without network): if the index lists its sha256, it gets that entry's trust level, otherwise
   `local`.
2. A pack from the index is downloaded pinned by the size and sha256 the index gives.
3. The pack is extracted into a quarantine folder (`.quarantine/` in the install folder) and checked: exactly the
   listed files with their hashes, no links, special or binary files, and the checks of its kind. Targets: a valid
   `capabilities` block, every image a Dockerfile pulls (`FROM`, `COPY --from`, `RUN --mount=...,from=`) pinned by
   digest unless it is an official base image, the shell deny-list on every script line that is not from the
   shipped template, `bash -n`, no template placeholders. Recipes: the recipe schema and the target's capabilities.
   Knowledge: a list of pages, each with its source.
4. A summary (kind, id, version, author, licence, trust level, source, files, destination and notes such as the
   author signature) is shown as plain text, so text from a pack cannot hide its trust level. Nothing is installed
   until you confirm; the default answer is no. `--yes` skips the question only for packs whose trust level comes
   from the signed index, also a file whose sha256 the index lists; a pack with trust `local` always asks, and
   without a terminal nothing is installed.
5. The pack moves into place in one rename, never over an existing folder (also when only the case differs), with
   a provenance file `.lcf-provenance.json` (source, trust level, hashes, signer, time). Anything that fails is
   removed.

Targets go to `targets/`, recipes to `community/recipes/`, knowledge to `community/knowledge/`. A knowledge pack is
imported into the knowledge base right after its installation or, if the knowledge base is off, as soon as it is
turned on.

### Trust levels

| Level | Meaning |
|---|---|
| `verified` | in the signed index: reviewed and reproduced independently |
| `community` | in the signed index: reviewed |
| `local` | a file without an entry in the signed index: nobody reviewed it. Installing always asks, and every build of such a target shows a warning. |
| `revoked` | in the revoked list of the signed index: installing is blocked. Installed copies are flagged by `llm-cli zoo list`, `zoo verify`, `llm-cli doctor` and the hub and are not built, also after the key that signed the revocation has expired. |

Targets that ship with the framework, such as Rockchip, have no provenance file; they are not packs and have no
trust level.

### Checking installed packs

- `llm-cli zoo verify` checks every installed pack against the cached index: revoked, or changed since it was
  installed. The exit code is 1 when it finds a problem.
- `llm-cli zoo verify <pack file>` checks a file without installing it: format, hashes, the checks of its kind, an
  included author signature and the privacy scrub (no home folders, addresses, host or user names, keys). It needs
  no keyring; the community repository's CI uses it.
- `llm-cli doctor` reports revoked packs as errors, changed packs as warnings and packs with trust `local` as
  information ([Health check](#health-check-doctor)).

### Community Hub

**Tools > Open Community Hub** lists the index entries and the installed packs with name, kind, version, a coloured
trust column, author and status, with a search field and filters for kind and trust. **Refresh** fetches the index
again, **Install** installs an index entry after its summary, **Import file...** installs a pack file (also one
encrypted to your key), and **Build** on an installed recipe fills in the Builder. A revoked pack shows a disabled
**Revoked** button.

### Export for community

- **Where:** the module wizard's last page (**Export for community after creating**: the new target), the Builder
  log's context menu (**Export a finished build for the community...**: a finished build's `build_info.json` becomes
  a recipe), and `llm-cli zoo export target <id>`, `zoo export recipe <build folder>` or `zoo export knowledge`,
  each with `--author <name>` (a pseudonym is fine). Knowledge packs are exported with the CLI only.
- **Privacy scrub:** every file is scrubbed on this computer. Home folders become `~`, IP and MAC addresses `<ip>`
  and `<mac>`, e-mail addresses `<email>`, JWTs `<jwt>`, the host name `<host>`, the user name `<user>`, and API
  keys are redacted. Loopback addresses, image digests, `git@` remotes and default accounts such as `root` or
  `runner` stay, so a scrubbed target still builds. The changes are shown as a diff before you share anything.
  Binary files are refused.
- The pack is checked like an installation and signed with your key. It is written to `community/contributions/`
  (or `--out`).
- **Licence:** MIT by default for your own target or recipe (`--license`; a list in the GUI). Knowledge packs hold
  other people's pages: their licence is `NOASSERTION`, every page keeps its source address, and a page without a
  source is never exported.
- A target you installed from a community pack is not exported as your own: signing it again would replace its
  author and licence. When a recipe's target is your own, a note reminds you to export the target too. A knowledge
  export takes the pages of your knowledge base, each with its source address; pages imported from installed
  community knowledge packs are left out (their authors share them already), and a note says how many.
- With `--to` or `--passphrase` the pack is [encrypted](#encrypted-packs-for-private-sharing) for private sharing
  instead.

### Pull request handoff

- **Open pull request** in the export dialog, or `--pr` with `llm-cli zoo export`, hands the pack to the repository
  in the setting `community_repo` (`owner/name`). The CLI first shows the summary and asks "Submit this pack to the
  public community repository?" unless `--yes` is given; without a terminal nothing is submitted.
- If the GitHub CLI (`gh`) and `git` are installed and `gh` is logged in, the framework forks the repository,
  commits the pack as `contributions/<kind>/<file>` on a new branch and opens the pull request with `gh pr create`;
  `gh` and `git` use their own stored credentials. Otherwise, or if that fails, the repository's upload page opens in
  the browser: drop the file there, and GitHub forks the repository for you.
- Without `community_repo` (the default today) nothing is handed over; the pack stays ready in its folder.

### Maintainer tools

For the community repository, not for users:

- `scripts/zoo_sign_index.py keygen <name>` creates an Ed25519 signing key in the OS keyring and prints its entry
  for `configs/community_keys.yml` (valid for two years). `entry <pack.zip> --url <https address>` checks a pack and
  prints its index entry. `sign index.json --key <name> [--previous <old index.json>]` checks the index (its version
  must be higher than the previous one) and writes `index.json.sig`. To revoke a pack, add it to `revoked`, raise
  the version and sign again.
- `scripts/community_repo/validate-contribution.yml` is a CI workflow template for the community repository. On
  every pull request under `contributions/` it accepts only `.zip` pack files of at most 5 MB and runs
  `llm-cli zoo verify` on each, whose privacy scrub also rejects a pack with a key in any of its files (the
  repository's secret scan skips binary files, so it does not run on the zips). A maintainer reviews and merges the
  pack, adds its entry to `index.json` and signs the index offline; the signing key never reaches CI. The maintainer
  sets the framework release the packs are checked with (`LCF_REF`); until then the template holds a placeholder,
  so the job fails.

### What was removed

- The "swarm" upload: the XOR "SwarmCipher" (an obfuscation anyone could undo), `llm-cli swarm upload`, the hub's
  "Contribute to Swarm" button and its GitHub token prompt. Old swarm packets in `community/knowledge/` are skipped
  with a warning.
- Installing hand-copied module folders from `community/`. Community packs are installed with `llm-cli zoo install`
  or the hub.

---

## Secret scan for contributors

The repository protects itself against committed credentials. `scripts/secret_scan.py` (standard library only)
recognizes provider API keys, tokens, private keys, certificates and credential files. It runs as a pre-commit and
pre-push hook (`make hooks`) and as a CI job over the whole history. Personal strings such as your e-mail address
or user name go into `.git/info/secret-patterns`, which is never committed. Findings are printed redacted. Details:
[CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Known gaps

What does not work yet, so you are not surprised:

- **Docker setup of the framework itself:** `Docker Setup/` and `make setup`, `make build`, `make up`, `make gui`,
  `make test-container` do not work (`scripts/setup_linux.sh` and `orchestrator/Dockerfile` do not exist). A
  hardened headless image for the CLI and the MCP server is being built. Conversions are not affected.
- **Windows hosts:** conversions on a Windows host are not verified.
- **Browser and cloud sign-in:** OAuth flows and the credential chains of AWS, Google Cloud and Azure are not
  available yet; Ditto signs in with API keys, and local engines need none.
- **Device runtime:** the framework does not install a runtime on the board.
- **GUI:** the Auto-Tuning and Models tabs are placeholders; the local runtime is controlled from the CLI only.
- **Deep Ingest** runs only from the module wizard in the GUI; there is no CLI command for it. Restoring a
  knowledge-base snapshot is not available from the GUI or the CLI.
- **Reranking** runs only for build recommendations; `rag_rerank_default` adds the chat and the MCP tool
  `search_knowledge`.
- **Spoken announcements** are tested with simulated speech engines only, not with the real voices of Windows,
  macOS or Linux.
- **Community zoo:** no community repository, index or maintainer key is published yet (`configs/community_keys.yml`
  is empty, `community_index_url` and `community_repo` are empty), so only pack files can be installed and a pull
  request has no repository to go to.
- **Security scan:** the Trivy container gets the Docker socket to read the image; scanning without the socket is
  planned.
- Smaller CLI gaps are listed in [CLI_Usage.md](CLI_Usage.md#known-limitations).

What is planned: [roadmap](BLUEPRINT.md#roadmap). What changed: [CHANGELOG](../CHANGELOG.md).
