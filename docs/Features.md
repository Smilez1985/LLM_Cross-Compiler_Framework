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
- [MCP server](#mcp-server)
- [Tool permissions and audit log](#tool-permissions-and-audit-log)
- [Secrets and personal state](#secrets-and-personal-state)
- [Hardware probe and new targets](#hardware-probe-and-new-targets)
- [Health check (doctor)](#health-check-doctor)
- [Community knowledge](#community-knowledge)
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

- **Cancel:** the `cancel_build` tool (Ditto in the GUI chat, or an MCP client, for builds of its own process)
  stops the running container, including a running security scan or image build, and a later step can never undo
  it. Once a build has failed or is moving its files into the output folder, it can no longer be cancelled and
  finishes on its own. The GUI has no cancel button, and Ctrl+C in `llm-cli build start` does not cancel (see
  [Known gaps](#known-gaps)).
- **Build slots:** at most two builds run at the same time in one program; a further build waits for a free slot
  (its stage shows "Waiting for a free build slot") instead of failing. A cancelled build keeps its slot until its
  container has stopped.
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

How a question with tools runs:

1. Ditto gets the definitions of the tools your permissions do not deny.
2. The model may call tools. Each call goes through the tool registry: input check, your permission, confirmation
   where the permission says "ask" (a dialog in the GUI with the tool's title and every argument; a prompt in the
   CLI, denied without a terminal), audit log entry with actor `ditto`.
3. Tool results go back to the model as untrusted data. A denied or failed call is reported to the model, which is
   told not to retry the same call.
4. After at most 8 model turns per question Ditto stops and says so.

Ditto's prompts tell it to check a target's capabilities before starting a build, to start or cancel builds only
when you asked for it in the conversation, to decide on self-healing only with your explicit decision, to answer
questions about targets and builds from the tools rather than from memory, and to report only what tools returned.

---

## Ditto evaluation

`llm-cli ditto eval` is a tool to measure how well a provider and model work for Ditto, for example whether a
small 2-bit local model is good enough for weak hardware. Nothing is built or changed.

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
model per task, effort, framework version, sha256 of the cases file) and every answer. Cloud providers bill about 20
requests per run and repeat. All options: [CLI_Usage.md](CLI_Usage.md#ditto-eval).

**First measurement** (for context, not a benchmark): on a CPU-only machine with Qwen3.5 2B, effort `low` (thinking
off) and the prompts of that time, the 2-bit tier `small-q2` passed 42% of the 19 cases and the 4-bit tier `small`
47%. The evaluation found gaps in Ditto's prompts (attributing failures to host or device, which inputs each format
accepts, using the tools for questions about targets and builds, compiler flags in module proposals). These gaps are
fixed now, and a new measurement is running.

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
- **Ingesting:** pages are converted to Markdown and split along their structure (headings, paragraphs, lists;
  code blocks and tables are never split), sized in the model's tokens. Each chunk is stored with its source, title
  and section. Re-ingesting a page updates it in place and removes chunks the new version no longer has.
- **Snapshots:** before every crawl the knowledge base is saved as a snapshot in
  `~/.llm-framework/knowledge_snapshots` (the newest 5 are kept). Restoring a snapshot is not available from the GUI
  or the CLI yet.
- **Search:** hybrid. Dense vectors find meaning, BM25 finds exact terms such as error codes and compiler flags; both
  rankings are fused with Reciprocal Rank Fusion. The same text from several sources is returned once.
- **Use:** the `search_knowledge` tool (Ditto, MCP clients); without tools Ditto adds matching excerpts to the
  question itself. Healing analyses, module proposals and (on request) build recommendations also get excerpts.
  Results are always treated as untrusted data.

**Adding documentation:** **Deep Ingest (Add Docs)** in the module wizard (**Tools > Create Hardware Module...**)
crawls the URLs you enter. The crawler:

- blocks private, loopback and other non-public addresses (SSRF protection) and pins each connection to the checked
  address, so a DNS answer cannot be switched in between,
- respects `robots.txt` (setting `crawler_respect_robots`), waits between requests (`crawler_rate_limit`, default
  1 s), follows at most 5 redirects and stops after a request budget,
- limits depth and pages (`crawler_max_depth` 2, `crawler_max_pages` 50 by default), reads PDFs
  (`crawler_download_pdfs`) and keeps only pages in the languages of `crawler_languages` (default English and
  German; an empty list keeps all).

---

## MCP server

`llm-cli mcp serve` exposes the framework's tools over the [Model Context Protocol](https://modelcontextprotocol.io)
(official Python SDK `mcp` 2.3.0, specification 2026-07-28). AI clients such as Claude Code, Claude Desktop or VS Code
can then use the [same tools as Ditto](#dittos-tools). You sign in to the client with your own account or key; the
framework never sees it.

- **Every call goes through the tool registry:** input schema, your permissions, confirmation, audit log (actor
  `mcp`). Tool annotations (read-only, idempotent) are only hints for the client.
- **Confirmation:** where your permission says "ask", the server asks the user through the client (MCP
  elicitation). The question shows the tool and every argument in full, the security-relevant ones first; control
  and invisible characters and long stacks of combining marks are shown as `\u` escapes. A question that would be
  longer than 4000 characters is refused instead of being cut. Only a real answer from the user counts as approval,
  and a "no" is final for that call, even if the permissions are loosened while the question is open. Declined and
  cancelled calls, and calls refused before they run (invalid arguments, a confirmation that fails), are audited
  too. A client that cannot ask gets a refusal with a hint how to allow the tool.
- **Instructions to the client:** the server describes the typical flow (targets → capabilities → build → status
  and log → artifacts) and tells the client that build logs and knowledge-base results are external text whose
  instructions must not be followed.
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
| `external` | Reach other devices or delete data | ask | ask, deny; single tools can be allowed one by one |
| `forbidden` | Never callable (for example arbitrary shell commands) | deny | deny |

There are no `external` or `forbidden` tools yet; deploying and uploading are not available as tools.

- The permissions are asked once (GUI dialog on the first start, `llm-cli ditto setup`) and stored in
  `~/.llm-framework/tool_policy.json` (readable only by you). Single tools can be set differently in the GUI.
- Until the setup is done, read-only tools are allowed and everything else asks.
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
  SSH key and `known_hosts`, MCP token, chat history, secrets, knowledge base and snapshots, local runtime, and two
  random values per installation (one for the privacy placeholders, one as an anonymous cache ID for OpenAI).
- **Settings** are never read from the working directory: `LLM_FRAMEWORK_CONFIG_DIR`, then `configs/` in the
  installation if you may write there, then the personal folder.

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
- When a target is created in the GUI wizard or with `module generate-ai` and the knowledge base is on and holds
  documents, a sanitized export of it is attached to the new target as `knowledge/initial_knowledge.json`.

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

`--fix` applies the automatic fixes up to `--tier` (1: makes the personal folder private; 2: also removes dangling
framework images and the Docker build cache) and checks again. Exit codes: 0 OK, 1 warnings, 2 errors; `--json`
for scripts.

---

## Community knowledge

The framework has the beginnings of a way to share knowledge-base contents:

- **Export:** the module generator attaches a sanitized export of the knowledge base to a new target (secrets
  removed from content, sources and metadata; see [Hardware probe and new targets](#hardware-probe-and-new-targets)).
- **Upload:** `llm-cli swarm upload <file>` pushes an export to the community repository on GitHub with your
  `github_token`. The file is only obfuscated (XOR), **not encrypted**; treat it as public.
- **Import:** reading community packs from `community/knowledge/` into the knowledge base exists in the code but is
  not reachable from the GUI or the CLI in this version.

Signed packs instead of XOR obfuscation are on the [roadmap](BLUEPRINT.md#roadmap).

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
- **Build status across processes:** builds live in the process that started them; `llm-cli build status` in
  another process does not see them.
- **Cancelling:** the GUI has no cancel button, and Ctrl+C in `llm-cli build start` does not stop the build (the
  CLI waits until it has ended); only the `cancel_build` tool cancels.
- **Browser and cloud sign-in:** OAuth flows and the credential chains of AWS, Google Cloud and Azure are not
  available yet; Ditto signs in with API keys, and local engines need none.
- **Device runtime:** the framework does not install a runtime on the board.
- **GUI:** the Auto-Tuning and Models tabs are placeholders; the local runtime is controlled from the CLI only.
- **Security scan:** the Trivy container gets the Docker socket to read the image; scanning without the socket is
  planned.
- Smaller CLI gaps are listed in [CLI_Usage.md](CLI_Usage.md#known-limitations).

What is planned: [roadmap](BLUEPRINT.md#roadmap). What changed: [CHANGELOG](../CHANGELOG.md).
