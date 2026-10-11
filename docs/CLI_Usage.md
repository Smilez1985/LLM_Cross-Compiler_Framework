Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-11

**English** · [Deutsch](CLI_Usage_DE.md)

# Command line (`llm-cli`)

The command line can do everything you need on a server without a screen, in scripts and in CI: check the
installation, convert models, deploy them, set up Ditto and its local model, manage tool permissions and run the
MCP server. Every command and option below was taken from the real `--help` output of this version.

Back to the [README](../README.md) · Features in detail: [Features.md](Features.md)

---

## Contents

- [Calling the CLI](#calling-the-cli)
- [Global options, output and exit codes](#global-options-output-and-exit-codes)
- [Overview of all commands](#overview-of-all-commands)
- [doctor](#doctor): health check
- [targets](#targets): `list`, `validate`
- [build](#build): `start`, `status`
- [deploy](#deploy): `list-artifacts`, `package`, `run`
- [ditto](#ditto): `login`, `logout`, `auth`, `setup`, `ask`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [mcp](#mcp): `serve`, `config`
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): hardware probe
- [config](#config): `show`, `repos`
- [swarm](#swarm): `upload`
- [Known limitations](#known-limitations)

---

## Calling the CLI

| Installation | Command |
|---|---|
| Linux installer (`install.sh`) | `cd /opt/llm-conversion-framework` and then `.venv/bin/python -m orchestrator.cli ...` |
| Windows installer (`install.bat`) | `cd /d "C:\Program Files\LLM-Conversion-Framework"` and then `.venv\Scripts\python.exe -m orchestrator.cli ...` |
| Development setup (`poetry install`) | `llm-cli ...` |

`python -m orchestrator.cli` needs the installation folder on Python's search path. Either run it from that
folder, or set `PYTHONPATH`. On Linux this alias works from any folder:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -m orchestrator.cli'
```

The examples below use `llm-cli`.

### Environment variables

| Variable | Effect |
|---|---|
| `LLM_FRAMEWORK_CONFIG_DIR` | Settings folder (`config.yml`). Default: `configs/` in the installation if writable, otherwise `~/.llm-framework`. The working directory is never used. |
| `LLM_FRAMEWORK_HOME` | Personal state folder. Default: `~/.llm-framework`. |
| `LLM_FRAMEWORK_MODELS_DIR` | Models folder; Ditto's local models are in its `ditto/` subfolder. Default: `models/` in the installation. |
| `LLM_FRAMEWORK_<KEY>` | Overrides one setting for this run, e.g. `LLM_FRAMEWORK_AI_PROVIDER=local` or `LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`. Only known setting names count. |
| `LLM_SECRET_STORAGE` | How the secrets vault is protected for this run, e.g. `ephemeral` in CI (see [secrets](#secrets)). |
| `OPENAI_CUSTOM_HEADERS`, `ANTHROPIC_CUSTOM_HEADERS` | Removed at startup with a warning, so they cannot replace the stored key; the setting `ai_allow_sdk_env_headers: true` keeps them. |
| `ANTHROPIC_LOG`, `OPENAI_LOG` | `debug` is lowered to `info` unless `--debug-http` is given. |

---

## Global options, output and exit codes

```
llm-cli [OPTIONS] COMMAND [ARGS]...
```

| Option | Meaning |
|---|---|
| `--version` | Print the version and exit. |
| `-c, --config PATH` | Use this settings file (YAML or JSON, must exist). Its folder becomes the settings folder for this run (a `config.yml` there is read as well), and the file's own values win over it. The usual settings folder is not read then. |
| `-v, --verbose` | Log level `DEBUG`. |
| `-q, --quiet` | Log level `ERROR` and no start banner. |
| `--log-level [DEBUG\|INFO\|WARNING\|ERROR]` | Log level (default `INFO`). |
| `--debug-http` | Show the debug output of the HTTP libraries and the AI provider SDKs on stderr, and keep `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug`. Without it these loggers stay at `INFO` even with `--verbose`, and the two variables are lowered to `info`. |
| `--help` | Help for the CLI, a group or a command, e.g. `llm-cli ditto runtime start --help`. |

Global options go before the command: `llm-cli -q doctor`, not `llm-cli doctor -q`.

Credentials (API keys, tokens, `Authorization`, `x-api-key` and cookie headers, sign-in codes and tokens in
URLs) are redacted in every log line and traceback, also with `--debug-http`.

**Output:** the start banner and log messages go to stderr; results go to stdout, and so do most messages a
command prints itself, including its error messages. For `llm-cli ditto ask` only the answer goes to stdout, the
sign-in commands (`ditto login`, `logout`, `auth`) print errors, warnings and questions to stderr, and for
`llm-cli mcp serve` stdout carries the MCP protocol.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The command failed (for example a failed build, a failed deployment, a damaged audit log, an AI error) |
| 2 | Invalid input: unknown option, an unknown target or an unsupported format/board/quantization combination (`build start`), an unknown provider (`--provider`), an unknown model tier (`ditto models download`) |
| 3 | Sign-in: the provider rejected the credential (`ditto login`, `ditto auth status --check`) |
| 4 | Sign-in: the provider or the local server cannot be reached |
| 5 | The credential vault cannot be used (no usable keyring, no passphrase, a damaged vault); the message names the choices. Every command that needs the vault uses it |
| 6 | Sign-in: the method is not available in this version, or the provider was removed |
| 130 | `build start` was interrupted with Ctrl+C (the CLI exits only after the build has ended) |

`llm-cli doctor` uses 0 (OK), 1 (warnings) and 2 (errors); `llm-cli ditto eval --min-pass` returns 1 when a run
stays below the given share. A few commands report a failure but still exit with 0 (see
[Known limitations](#known-limitations)).

---

## Overview of all commands

| Command | What it does |
|---|---|
| `doctor` | Check the installation and explain how to fix problems |
| `targets list` / `targets validate` | List hardware targets / check that a target can be loaded |
| `build start` / `build status` | Convert and quantize a model / show the builds of this process |
| `deploy list-artifacts` / `deploy package` / `deploy run` | List finished builds / pack a build into a ZIP / upload and install it on a board |
| `ditto login` / `ditto logout` | Sign Ditto in to a provider (the key from a hidden prompt, stdin or an environment variable, checked before it is stored) / sign out |
| `ditto auth status/methods/storage/rotate-key` | Sign-in state of every provider / the sign-in methods / how the credential vault is protected / a new vault key |
| `ditto setup` | Set the tool permissions (the same choices as the GUI's first-start dialog) |
| `ditto ask` | Ask Ditto one question |
| `ditto eval` | Grade Ditto on fixed test cases |
| `ditto runtime install/start/status/stop` | The managed local model server (llama.cpp) for offline mode |
| `ditto models list/download` | The model tiers for offline mode |
| `tools list` / `tools permissions` / `tools audit` | The framework tools, their permissions and the audit log |
| `mcp serve` / `mcp config` | Run the MCP server / show how to connect AI clients |
| `secrets set` / `secrets list` | Store a token (Hugging Face, GitHub) / list the stored names |
| `module create` / `module generate-ai` | Create a new hardware target, by questions or from a probe file with Ditto |
| `scan` | Run the hardware probe on this computer |
| `config show` / `config repos` | Show the CLI's base settings / manage source repository overrides |
| `swarm upload` | Upload a knowledge-base export to the community repository |

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

Checks Python, the Docker daemon and buildx, free disk space for the cache and output folders, the targets and
their capabilities, the privacy of the personal folder, the secrets store and the keyring, the tool
permissions, the audit log and the shipped calibration data. Each finding comes with a hint how to fix it.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output (findings, fixes, exit code). |
| `--fix` | Apply the automatic fixes up to `--tier`, then check again. |
| `--tier INTEGER RANGE` | `1` (default): harmless fixes (the help says "permissions, folders"; in this version that is making the personal folder private). `2`: also removes caches (dangling framework images and the Docker build cache). |

Disk space: below 20 GB free is a warning, below 5 GB an error.

```bash
llm-cli doctor
llm-cli doctor --json > doctor.json
llm-cli doctor --fix --tier 2
```

---

## targets

### targets list

```
llm-cli targets list [-f table|json|yaml]
```

Lists the targets in `targets/`. The table shows the target's display name (for example "Rockchip Family"), its
architecture, whether it is ready, and its folder. **The value for `--target` is the folder name**, for example
`Rockchip`.

| Option | Meaning |
|---|---|
| `-f, --format [table\|json\|yaml]` | Output format (default `table`). |

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Checks that the target can be loaded. Exit code 1 if it is not found.

```bash
llm-cli targets validate Rockchip
```

---

## build

### build start

```
llm-cli build start -m MODEL -t TARGET [options]
```

Converts and quantizes a model for a target in Docker. The request is checked against the target's capabilities
first: an unsupported combination stops with exit code 2 and the reason, before anything runs. Missing board and
quantization are filled with the target's defaults.

| Option | Meaning |
|---|---|
| `-m, --model TEXT` | **Required.** Hugging Face ID (e.g. `HuggingFaceTB/SmolLM2-135M-Instruct`) or a local folder or file. |
| `-t, --target TEXT` | **Required.** Target folder name in `targets/`, e.g. `Rockchip`. Not case-sensitive. An unknown target stops with exit code 2 and the list of known targets. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Output format (default `gguf`). The list is longer than what any target supports; Rockchip accepts `gguf`, `rkllm` and `rknn`. |
| `-b, --board TEXT` | Board of the target family, e.g. `rk3588`, `rk3576`, `rk3566`. Default: the target's default board (Rockchip: `rk3588`). Not case-sensitive. |
| `-q, --quantization TEXT` | Quantization, e.g. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Default: the format's default for the board. Common spellings such as `INT8` are translated. |
| `-o, --output-dir TEXT` | Output folder, relative to the current folder. Default: `output/build_<date>_<time>` in the installation. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimization level (default `balanced`), passed to the build scripts as `OPTIMIZATION_LEVEL`. The Rockchip scripts do not use it. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in this process's build queue (default `normal`). |
| `--parallel` | Has no effect in this version (it is always on, and one call runs one build). |
| `-F, --follow` | Print each stage while the build runs. |
| `--gpu / --no-gpu` | Pass the host's GPU into the container (default off): an NVIDIA GPU when `nvidia-smi` is found, otherwise `/dev/dri` if it exists. Rockchip conversions do not need it. |
| `--imatrix / --no-imatrix` | Importance matrix for GGUF (default off). Required for `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`. |
| `--dataset PATH` | Your own calibration file (must exist): a text file for the GGUF importance matrix or for RKLLM, a list of sample inputs for RKNN `i8`. Without it, the importance matrix uses the shipped calibration text. |

Behaviour:

- The command stays in the foreground until the build has finished. Ctrl+C does not cancel the build in this
  version: the CLI stops showing progress but waits until the build has ended, then exits with code 130.
- If the build fails with a known problem and a fix the framework can apply itself, the CLI shows the cause and
  asks "Apply this fix and retry the build?". Without a terminal the proposal is dismissed. Manual steps are only
  shown.
- Results: `<output folder>/<target>/<model>/<quantization>/` with the model file, `build_info.json` and
  `Model_Card.md`, and a ZIP of them next to that folder. They appear there only when the build has completed; a
  cancelled or failed build leaves no files there.
- Gated Hugging Face models need a token: `llm-cli secrets set hf_token`.
- The first build of a target builds its Docker image first and takes much longer.

```bash
# GGUF for the RK3566 CPU (default quantization Q4_0 on this board)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3566 --follow

# RKLLM for the RK3576 NPU (default w4a16)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3576 -F

# 2-bit GGUF: needs the importance matrix
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3588 -q IQ2_XS --imatrix -F

# RKNN int8 from a local ONNX model with a sample list
llm-cli build start -m ./detector.onnx -t Rockchip -f rknn -b rk3588 -q i8 --dataset ./samples.txt -F

# Results into a folder of your choice
llm-cli build start -m ./my-model -t Rockchip -o ./builds -F
```

What a rejected request looks like:

```
$ llm-cli -q build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3566
Format 'rkllm' does not run on rk3566 (supported: rk3588, rk3576).
$ echo $?
2
```

### build status

```
llm-cli build status [REQUEST_ID] [-a|--all]
```

Shows the builds this process knows about: without a request ID (or with `--all`) a table with request ID, status
and progress; with a request ID its status and progress.

| Option | Meaning |
|---|---|
| `-a, --all` | Show all builds. |

Builds live in the process that started them. A separate `llm-cli build status` call does not see builds started
by the GUI, the MCP server or another CLI call and prints "No builds found". Use `build start --follow`, the GUI log
or `llm-cli deploy list-artifacts` for finished builds.

---

## deploy

### deploy list-artifacts

```
llm-cli deploy list-artifacts
```

Lists the finished builds in the configured output folder, read from their manifests: folder (relative to the
output folder), model, format, quantization, board and size.

### deploy package

```
llm-cli deploy package ARTIFACT_NAME --profile PROFILE [--docker|--no-docker]
```

Creates a ZIP file for the board: the model file, the build's manifest and model card, a generated `deploy.sh` and
`checksums.sha256`. It is written to the output folder as `deploy_<model>_<timestamp>.zip`.

| Argument / option | Meaning |
|---|---|
| `ARTIFACT_NAME` | A build folder as shown by `list-artifacts` (relative to the output folder), or any existing path to a build folder or model file. |
| `--profile TEXT` | **Required.** The target, e.g. `Rockchip` (used for device options in Docker mode). |
| `--docker / --no-docker` | Also pack container images (default off). Experimental and not verified in this version; leave it off for a model file. |

Only model files are deployed without Docker; builds that produced several weight files are refused.

```bash
llm-cli deploy package build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
```

### deploy run

```
llm-cli deploy run PACKAGE_PATH --ip IP --user USER [--port 22] [--password TEXT] [--trust-fingerprint TEXT]
```

Uploads a package over SSH to a private temporary folder on the board, runs `deploy.sh` there and removes the
folder. `deploy.sh` checks the checksums and copies the model to `~/models` (or `$MODEL_DIR`); for GGUF it prints
a matching `llama-server` command if `llama-server` is installed on the board.

| Argument / option | Meaning |
|---|---|
| `PACKAGE_PATH` | The ZIP file from `deploy package`, which prints its full path (must exist). |
| `--ip TEXT` | **Required.** The board's IP address. |
| `--user TEXT` | **Required.** User on the board. |
| `--port INTEGER` | SSH port (default 22). |
| `--password TEXT` | Only for the first connection: used once to install the framework's SSH key, never stored. Asked interactively when omitted. A password on the command line ends up in your shell history. |
| `--trust-fingerprint TEXT` | Trust a new board without asking if its host key has exactly this SHA256 fingerprint (for scripts). |

First connection: the CLI shows the board's host key fingerprint and asks whether to trust it. Compare it with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the board. Without a terminal and without
`--trust-fingerprint` nothing is deployed. A board whose host key has changed is always refused.

```bash
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --ip 192.168.1.50 --user rock

# In a script, with the fingerprint checked beforehand
llm-cli deploy run output/deploy_model_1791801300.zip --ip 192.168.1.50 --user rock \
  --trust-fingerprint SHA256:AbCdEf...
```

---

## ditto

Ditto is the built-in assistant. Most `ditto` commands use the provider and model from the AI settings (GUI:
**Tools > Configure AI Agent (Ditto)...**, or `ai_provider` and `ai_model` in `config.yml`). Sign in with
[`llm-cli ditto login`](#ditto-login), or with **Sign in...** in the AI settings.

### ditto login

```
llm-cli ditto login PROVIDER [--method ID] [--with-token] [--from-env] [--base-url URL] [--workspace-id ID]
                    [--organization ID] [--project ID] [--profile NAME] [--insecure-storage] [--no-check] [--yes]
```

Signs Ditto in to a provider (`anthropic`, `openai`, `google`, `openrouter`, `ollama`, `custom`;
[`llm-cli ditto auth methods`](#ditto-auth-methods) lists them with their methods). A key never comes from the
command line, where the shell history and the process list would keep it: it is asked with a hidden prompt, read
from stdin (`--with-token`) or copied once from the provider's documented environment variable (`--from-env`).
Before it is stored in the credential vault, a free request checks it (the model list; OpenRouter's key endpoint),
and a rejected key is not stored. The method is remembered as `ai_auth_<provider>`. The output shows at most the
last four characters of a key.

| Method type | What `ditto login` does |
|---|---|
| `api_key` (cloud providers, `custom` servers) | Asks for the key, checks it and stores it. The key of a server whose address you set belongs to that address only. Admin keys (`sk-ant-admin01-`) are refused before anything is sent. |
| `bearer_optional` (local engines whose key is optional) | Asks for a token ("Enter for none"); without one the server is used without a key. |
| `none` (Ollama, `custom` without a key) | Stores nothing; checks the address and counts the models. |
| `cli_session` (`--method ant_profile`) | Uses a profile of Anthropic's own `ant` CLI; only the profile name is kept. When the check through the Anthropic SDK fails, it tells you to run `ant auth login`. Advanced: never chosen automatically, and Anthropic has not confirmed that other apps may use these profiles. |
| OAuth and cloud sign-ins | Not available in this version (exit code 6). |

Without a terminal (scripts, CI) nothing is asked: a provider with more than one method needs `--method` (an
opt-in method such as `ant_profile` does not count), and the key must come from `--with-token` or `--from-env`.

| Option | Meaning |
|---|---|
| `--method ID` | Sign-in method, see `llm-cli ditto auth methods PROVIDER`. |
| `--with-token` | Read the key from stdin. |
| `--from-env` | Copy the key from the provider's environment variable (for example `ANTHROPIC_API_KEY`) into the vault; afterwards the variable is no longer needed. |
| `--base-url URL` | Server address for providers whose address you set (Ollama, `custom`); remembered as `ai_base_url_<provider>`. A key sent over plain `http://` to another computer needs `--yes`, because it travels unencrypted. |
| `--workspace-id ID` | Claude API: workspace ID (`wrkspc_...`) for keys that are valid in every workspace of the organization; stored with the key. |
| `--organization ID`, `--project ID` | OpenAI: organization (`org-...`) and project (`proj_...`), sent with the key. |
| `--profile NAME` | With `--method ant_profile`: the profile (default: the `ant` CLI's active profile). |
| `--insecure-storage` | Without a usable keyring: keep the vault key in a file next to the vault (**not encrypted at rest**). Ignored when the vault is protected another way that works. |
| `--no-check` | Store without the check (for example offline). |
| `-y, --yes` | Do not ask before sending a key over plain `http://` or before `--insecure-storage`. |

Exit codes: 0 signed in; 2 usage (an unknown provider or method, a missing `--method` or key without a terminal);
3 rejected (a wrong, revoked or admin key, a workspace problem); 4 the provider or the local server cannot be
reached; 5 the credential vault cannot be used (the message names the choices); 6 the method is not available in
this version or the provider was removed.

```bash
llm-cli ditto login anthropic                      # asks for the key without echo
printf %s "$OPENAI_API_KEY" | llm-cli ditto login openai --with-token --project proj_abc123
llm-cli ditto login anthropic --from-env --workspace-id wrkspc_01AbCd
llm-cli ditto login custom --method api_key --base-url http://127.0.0.1:8000/v1
llm-cli ditto login ollama --base-url http://192.168.1.20:11434/v1
llm-cli ditto login anthropic --method ant_profile --profile work
```

`ditto login` signs in; it does not change which provider Ditto uses (`ai_provider`, set in the AI settings).

### ditto logout

```
llm-cli ditto logout PROVIDER [--method ID] [--base-url URL]
```

Signs Ditto out: the stored key (for a server whose address you set, the key of that address) is deleted from this
computer, and the remembered method is forgotten. The key itself keeps working until you revoke it on the
provider's key page, which the command names. A key in an environment variable stays in use until you unset the
variable; the command says so. For the `ant` profile only the profile name is forgotten: `ant auth logout` would
also sign out the other tools that use it, so the command leaves that to you.

| Option | Meaning |
|---|---|
| `--method ID` | Method to sign out of (default: the one in use). |
| `--base-url URL` | Address whose key is deleted (default: the configured one). |

### ditto auth status

```
llm-cli ditto auth status [--json] [--check]
```

One line per provider: method, where the credential comes from (`vault`, `env:<VARIABLE>`, `profile:<name>`,
`runtime`), account or workspace, the last four characters of the key, and the state ("Signed in as ...", "Not
signed in", "Using key from environment variable X", "Expired: sign in again"). Ditto's provider is marked with `*`.
A key is never shown.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output, also without any key. |
| `--check` | Also run the free check for every provider with a credential (and for Ditto's provider). |

Exit code 5 when the credential vault cannot be used; with `--check` the code of the first failed check (3 or 4);
otherwise 0.

### ditto auth methods

```
llm-cli ditto auth methods [PROVIDER] [--json]
```

The sign-in methods of every provider (or of one) in the order they are preferred: the recommended one, opt-in
methods, methods that come in a later version, the key page, the environment variables, the extra options
(`--workspace-id`, `--organization`, `--project`) and policy notes. Needs neither the vault nor the network.

### ditto auth storage

```
llm-cli ditto auth storage [--use keyring|helper|passphrase|insecure] [--helper COMMAND] [--yes]
```

Without `--use`, it shows how the credential vault is protected (see [secrets](#secrets)) and ends with exit code
5 when the vault cannot be used. With `--use`, the vault is protected the chosen way from now on: a working vault
is re-encrypted with a new key, a vault that could not be opened (no keyring) is opened with the new protection,
and the choice is saved as `secret_storage` (and `secret_helper`). Nothing is chosen for you. `ephemeral` is chosen
per run with `LLM_SECRET_STORAGE=ephemeral` (exit code 2 here). A new passphrase is asked twice in the terminal;
without a terminal the command ends with exit code 5 and nothing changes.

```bash
llm-cli ditto auth storage --use passphrase
llm-cli ditto auth storage --use helper --helper "pass show llmcc/vault-key"
```

### ditto auth rotate-key

```
llm-cli ditto auth rotate-key
```

Creates a new vault key, re-encrypts every stored credential with it and removes the old key. Exit code 5 when
the vault cannot be used.

### ditto setup

```
llm-cli ditto setup
```

First-time setup of the tool permissions, used by Ditto and the MCP server. Asks one question per category:

| Category | Choices | Recommended |
|---|---|---|
| Read information (targets, builds, logs, knowledge base) | allow, ask, deny | allow |
| Change things on this computer (start or cancel builds) | allow, ask, deny | ask |
| Reach other devices or delete data (deploy over SSH, uploads) | ask, deny | ask |

The answers are saved in `~/.llm-framework/tool_policy.json`. Change them later with
[`llm-cli tools permissions`](#tools-permissions).

### ditto ask

```
llm-cli ditto ask QUESTION [--no-tools] [--provider TEXT] [--model TEXT]
```

Asks Ditto one question. Ditto may use the framework tools within your permissions; tools set to "ask" show the
tool and every argument and need your confirmation in the terminal (without a terminal they are denied). The
answer goes to stdout, tool notes and errors to stderr. Nothing is saved to the chat history.

| Option | Meaning |
|---|---|
| `--no-tools` | Answer without the framework tools. |
| `--provider TEXT` | Provider for this question only, e.g. `anthropic`, `openai`, `ollama`, `local`. Default: AI settings. |
| `--model TEXT` | Model for this question only. Default: AI settings. |

```bash
llm-cli ditto ask "Which quantization should I use for a 1B model on the RK3566?"
llm-cli ditto ask "Start a GGUF build of HuggingFaceTB/SmolLM2-135M-Instruct for rk3588"
llm-cli ditto ask "Explain IQ4_XS" --no-tools --provider local
```

Without setup the command explains what is missing, for example "the local model server is not running. Start it
with: llm-cli ditto runtime start".

### ditto eval

```
llm-cli ditto eval [options]
```

Grades Ditto on fixed test cases (build-log analysis, build recommendations, module proposals, chat with tools,
prompt injection), for example to check whether a 2-bit local model is good enough. Nothing is built or changed:
chat cases run against a stand-in build service. Cloud providers bill every request (about 20 per run and repeat).
How it works: [Features.md](Features.md#ditto-evaluation).

| Option | Meaning |
|---|---|
| `--cases FILE` | Cases file (default: `evals/ditto/cases.yml` of the installation). |
| `--provider TEXT` | Provider for the run. Default: AI settings. |
| `--model TEXT` | Model for the run. Default: AI settings. |
| `--tier TEXT` | Run against the managed local model of this tier; repeatable (`--tier small --tier small-q2`). Cannot be combined with `--provider`/`--model`. |
| `--kind [healing\|optimizer\|module\|chat]` | Only cases of this kind; repeatable. |
| `--case TEXT` | Only this case ID; repeatable. |
| `--repeat INTEGER RANGE` | Runs per case, 1 to 20 (default 1). |
| `--json FILE` | Write the full report (setup, pass rates, every answer) as JSON. |
| `--min-pass FLOAT RANGE` | Exit code 1 if a run passes fewer cases than this share (0 to 1, e.g. `0.8`). |
| `--effort [low\|medium\|high]` | Effort for this run (local models: `low` = no thinking). Default: AI settings. |

With `--tier`, the local runtime must be installed and the tier's model downloaded. The CLI starts `llama-server`
with each tier in turn and **stops the local server after each tier**, so a server that was running before is
stopped as well.

```bash
# Compare the 2B model at 4 and 2 bits on this machine, without thinking
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json

# Only the chat cases, three runs each, against the configured provider
llm-cli ditto eval --kind chat --repeat 3

# As a quality gate in CI
llm-cli ditto eval --provider anthropic --min-pass 0.8
```

The result is a table per run: passed share, share per kind and average seconds per case.

### ditto runtime

The managed llama.cpp server for Ditto's offline mode. These commands work without Docker and without the keyring.
Files: `~/.llm-framework/runtime/llama.cpp/` (builds and state) and `~/.llm-framework/logs/llama-server.log`.

#### ditto runtime status

```
llm-cli ditto runtime status [--json]
```

Shows the detected hardware, the recommended llama.cpp build and why, whether a build is installed, whether the
server runs (address, process, model, context), the recommended model and why, and the log file.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output. |

#### ditto runtime install

```
llm-cli ditto runtime install [--backend auto|cpu|vulkan|cuda|rocm|metal] [--force] [-y]
```

Downloads the pinned llama.cpp build for this machine, checks size and sha256 before unpacking, and installs it.

| Option | Meaning |
|---|---|
| `--backend [auto\|cpu\|vulkan\|cuda\|rocm\|metal]` | Build to install (default `auto`). ROCm is only used when chosen here. |
| `--force` | Download and unpack again even if it is installed. |
| `-y, --yes` | Do not ask before downloading. |

#### ditto runtime start

```
llm-cli ditto runtime start [--model TIER] [--ctx N] [--sleep-idle SECONDS]
```

Starts `llama-server` on 127.0.0.1 with a free port and a new API key in a private file. The server keeps running
after the command returns, until `llm-cli ditto runtime stop`. If the same model already runs, it is reused.

| Option | Meaning |
|---|---|
| `--model TEXT` | Model tier (see `ditto models list`); remembered for later starts. `auto`: the recommendation for this machine. |
| `--ctx INTEGER RANGE` | Context length in tokens, 256 to 1048576. Default: computed from free memory, at most 32768. |
| `--sleep-idle INTEGER RANGE` | Unload the model after this many idle seconds (0 = never). Default: 600. |

The model must be downloaded first (`ditto models download`). Ditto uses the server as the provider `local`.

#### ditto runtime stop

```
llm-cli ditto runtime stop
```

Stops the server started by the framework, including leftovers of a crash. Prints "Not running." if there is none.

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install -y
llm-cli ditto models download -y
llm-cli ditto runtime start --model small --sleep-idle 0
llm-cli ditto runtime stop
```

### ditto models

#### ditto models list

```
llm-cli ditto models list
```

Lists the pinned models: tier, file, size, license, whether it is downloaded (and verified), and notes
("recommended", "chosen", "on request").

#### ditto models download

```
llm-cli ditto models download [TIER] [-y]
```

Downloads a model at its pinned Hugging Face revision and checks size and sha256. Without `TIER` the recommended
tier for this machine. Interrupted downloads resume.

| Argument / option | Meaning |
|---|---|
| `TIER` | `small`, `small-q2`, `default`, `default-q2`, `large`, `large-iq2` or `granite`. |
| `-y, --yes` | Do not ask before downloading. |

```bash
llm-cli ditto models list
llm-cli ditto models download small-q2
```

---

## tools

The framework tools are what Ditto and MCP clients use: 8 read-only tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_doctor`, `search_knowledge`) and 3 that change things on this computer (`start_build`, `cancel_build`,
`resolve_healing`).

### tools list

```
llm-cli tools list
```

Every tool with its tier (`read`, `local_write`), the current permission (`allow`, `ask`, `deny`) and a
description. The title says when the permissions are not set up yet.

### tools permissions

```
llm-cli tools permissions [--reset]
```

Asks the same three questions as [`ditto setup`](#ditto-setup) and saves the answers. Permissions for single tools
set in the GUI are kept.

| Option | Meaning |
|---|---|
| `--reset` | Back to the recommended defaults (the setup asks again next time). |

### tools audit

```
llm-cli tools audit [--limit N] [--verify] [--archive]
```

Shows the most recent tool calls: time, actor (`ditto` or `mcp`), tool, decision, whether a person confirmed it,
and the outcome.

| Option | Meaning |
|---|---|
| `--limit INTEGER` | Number of entries (default 30). |
| `--verify` | Check the chain instead: changed or removed entries, and removal of the newest entries (anchor file). Exit code 0 if intact, otherwise 1. |
| `--archive` | Move the log and its anchor aside (kept as `audit.db.<UTC time>` and `audit.db.<UTC time>.anchor` in the personal folder) and start a new log with the next tool call. Use it when `--verify` or `llm-cli doctor` reports a problem with the log. If the log cannot be moved (for example because it is busy), the command exits with code 1; close the GUI and AI clients and try again. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
llm-cli tools audit --archive
```

---

## mcp

### mcp serve

```
llm-cli mcp serve [--transport stdio|http] [--host HOST] [--port PORT]
```

Runs the MCP server. Normally the AI client starts it for you (see `mcp config`). stdout carries the protocol;
logs go to stderr. If the tool permissions are not set up yet, read-only tools work and everything else asks (or is
denied when the client cannot ask).

| Option | Meaning |
|---|---|
| `--transport [stdio\|http]` | `stdio` (default): started by the client. `http`: local Streamable HTTP with a bearer token. |
| `--host TEXT` | HTTP only; loopback addresses only (default `127.0.0.1`; also `::1`, `localhost`). |
| `--port INTEGER` | HTTP only (default 8765). |

With `--transport http` the server listens on `http://<host>:<port>/mcp` and every request needs
`Authorization: Bearer <token>`; the token is in `~/.llm-framework/mcp_http_token` (created on first use).

```bash
llm-cli mcp serve --transport http --port 8765
```

### mcp config

```
llm-cli mcp config [--write claude-desktop]
```

Prints how to connect Claude Code (a `claude mcp add ...` command), Claude Desktop (JSON for its configuration
file) and VS Code (JSON for `.vscode/mcp.json`), with the Python and folder of this installation. Run it with the
framework's own Python so the printed command uses it. The command starts Python with `-P`, so nothing is imported
from the folder the client starts the server in. On Windows the command line is quoted so that cmd and PowerShell
both keep it as it is; if a path contains `%`, `!`, `$`, a backtick or a double quote, only the JSON is shown.

| Option | Meaning |
|---|---|
| `--write [claude-desktop]` | Add the server to Claude Desktop's configuration (the path is shown in the output; on Linux `XDG_CONFIG_HOME` is honoured). Other entries are kept and a backup (`.bak`) is written. If the file cannot be written, the command prints the reason and exits with code 1. Restart Claude Desktop afterwards. |

```bash
llm-cli mcp config
llm-cli mcp config --write claude-desktop
```

You sign in to these clients with your own account or key; the framework never sees it.

---

## secrets

API keys and tokens are stored encrypted in the credential vault `secrets.store` in your personal state folder
(`~/.llm-framework`, or `LLM_FRAMEWORK_HOME`). The vault is never placed inside a git working copy. Its key is in
your operating system's keyring by default. Without a usable keyring, commands that need the framework stop with
exit code 5 and list the choices; choose another protection with
[`llm-cli ditto auth storage --use`](#ditto-auth-storage), with the setting `secret_storage` in `config.yml`, or
with the environment variable `LLM_SECRET_STORAGE` for one run:

| `secret_storage` | Where the vault key comes from |
|---|---|
| `keyring` | The operating system's keyring (default). |
| `helper` | The command in the setting `secret_helper`, e.g. `pass show llmcc/vault-key`, run without a shell. |
| `passphrase` | A passphrase asked once per start (at least 8 characters), never read from a file or a variable. |
| `insecure` | A key file next to the vault: **not encrypted at rest**. |
| `ephemeral` | Memory only, nothing is saved (for CI). |

### secrets set

```
llm-cli secrets set KEY [--with-token] [--password TEXT]
```

Stores a secret under the name `KEY`. The value is asked twice without echo, or read from stdin with `--with-token`;
both keep it out of your shell history. Ditto's provider keys are better stored with [`llm-cli ditto login`](#ditto-login),
which also checks them.

| Option | Meaning |
|---|---|
| `--with-token` | Read the value from stdin, e.g. `printf %s "$HF_TOKEN" \| llm-cli secrets set hf_token --with-token`. |
| `--password TEXT` | Deprecated: the value ends up in your shell history and the process list. |

Names the framework uses:

| Name | Used for |
|---|---|
| `anthropic_api_key`, `openai_api_key`, `gemini_api_key`, `openrouter_api_key`, `custom_api_key` | Ditto's providers (better: `llm-cli ditto login <provider>`) |
| `hf_token` | Gated Hugging Face models in builds |
| `github_token` | `swarm upload` |
| `qdrant_api_key` | A remote Qdrant server for the knowledge base (setting `qdrant_url`) |

```bash
llm-cli secrets set hf_token
printf %s "$GITHUB_TOKEN" | llm-cli secrets set github_token --with-token
```

### secrets list

```
llm-cli secrets list
```

Lists the stored names, never the values. Ditto's provider keys are listed under their vault names, for example
`cred/anthropic/api_key` for `anthropic_api_key`; `secrets set` accepts both forms.

---

## module

New hardware targets. The full guide: [Module_Developer_Guide.md](Module_Developer_Guide.md).

### module create

```
llm-cli module create [--name TEXT] [--arch aarch64|x86_64|armv7l|riscv64] [--sdk TEXT]
```

Creates a new target by asking questions: name, architecture and SDK (if not given as options), then base Docker
image (default `debian:bookworm-slim`), description, system packages and default CPU flags. Every value is checked
before files are written; an existing target is never overwritten.

| Option | Meaning |
|---|---|
| `--name TEXT` | Name of the new target. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU architecture. |
| `--sdk TEXT` | Special SDK, e.g. `CUDA`, `RKNN` (default `None`). |

The target is written to `targets/` **of the current folder**; run the command from the installation folder.
Afterwards add boards to `target.yml` and adjust `modules/config_module.sh`.

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto proposes a target module from a hardware probe file (`target_hardware_config.txt`, see [scan](#scan)). The
whole proposal is printed as JSON; only after you confirm is the module created (checked again by the module
generator) in the configured targets folder. Ditto uses the provider's stored sign-in
([`llm-cli ditto login`](#ditto-login)); the command never asks for a key itself.

| Option | Meaning |
|---|---|
| `--provider TEXT` | Provider for this run, e.g. `anthropic`, `openai`, `ollama`. Default: AI settings. |
| `--model TEXT` | Model for this run. Default: AI settings. |

```bash
llm-cli scan
llm-cli module generate-ai target_hardware_config.txt --provider local
```

---

## scan

```
llm-cli scan [--output TEXT]
```

Runs the hardware probe on this computer (`scripts/hardware_probe.sh` on Linux, `scripts/hardware_probe.ps1` on
Windows). The probe writes `target_hardware_config.txt` into the current folder. It may ask whether to install
missing helper tools (`pciutils`, `usbutils`, `dmidecode`) with your package manager; answer `n` to skip them.

| Option | Meaning |
|---|---|
| `--output TEXT` | File name the command checks for afterwards (default `target_hardware_config.txt`). The probe itself always writes `target_hardware_config.txt`, so keep the default. |

To probe a board instead of this computer, run `scripts/hardware_probe.sh` on the board, or use **Scan / Import...
> Remote Scan via SSH** in the GUI.

---

## config

### config show

```
llm-cli config show
```

Prints the CLI's base settings as JSON: the folders (`targets_dir`, `models_dir`, `output_dir`, `configs_dir`,
`cache_dir`, `logs_dir`), `log_level` and `max_concurrent_builds`, merged with a file given with `-c`. It does not
show the settings in `config.yml` (AI provider, sandbox, crawler and so on); look at that file directly.

### config repos

```
llm-cli config repos [--add NAME URL] [--remove NAME]
```

Without options it lists the source repository overrides (setting `source_repositories`). `--add` and `--remove`
change them and save `config.yml`.

| Option | Meaning |
|---|---|
| `--add TEXT...` | Add an override: `NAME URL`. |
| `--remove TEXT` | Remove the override `NAME`. |

In this version, overrides added here do not reach builds: the build engine only passes entries of the form
`{url: ...}`, and `--add` stores a plain URL.

---

## swarm

### swarm upload

```
llm-cli swarm upload FILE_PATH [--user TEXT]
```

Uploads a knowledge-base export (a JSON file) to the community repository on GitHub. The file is obfuscated
(XOR), **not encrypted**: anyone can read it. Upload only what you would publish.

| Argument / option | Meaning |
|---|---|
| `FILE_PATH` | The export file (must exist). |
| `--user TEXT` | Contributor name (default `CLI_User`). |

Needs a GitHub token: `llm-cli secrets set github_token` (the value is asked).

---

## Known limitations

- `build status` only sees builds of its own process (see [build status](#build-status)).
- Ctrl+C in `build start` does not cancel the build; the CLI waits until it has ended.
- `build start --parallel` and `--optimization` have no effect on Rockchip builds.
- `deploy package`, `secrets set` and `scan` print a failure ("Package generation failed", "Failed to store
  secret", "output file was not created") but exit with code 0; check the output in scripts.
- `scan --output` does not change where the probe writes.
- `module create` writes to `targets/` of the current folder.
- `config show` shows only the CLI's base settings; `config repos --add` does not reach builds.
- `deploy package --docker` is experimental.
- There is no CLI command yet to choose Ditto's provider permanently; use the GUI, `config.yml` or
  `LLM_FRAMEWORK_AI_PROVIDER` (`ditto login` signs in but does not switch the provider). Browser and cloud
  sign-ins (OAuth, AWS, Google Cloud, Azure) come later ([roadmap](BLUEPRINT.md#roadmap)).
