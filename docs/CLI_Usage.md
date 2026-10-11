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
- [ditto](#ditto): `setup`, `ask`, `eval`, `runtime`, `models`
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

---

## Global options, output and exit codes

```
llm-cli [OPTIONS] COMMAND [ARGS]...
```

| Option | Meaning |
|---|---|
| `--version` | Print the version and exit. |
| `-c, --config PATH` | Use this settings file (YAML or JSON, must exist). Its values override the saved settings, and its folder is used as the settings folder for this run. |
| `-v, --verbose` | Log level `DEBUG`. |
| `-q, --quiet` | Log level `ERROR` and no start banner. |
| `--log-level [DEBUG\|INFO\|WARNING\|ERROR]` | Log level (default `INFO`). |
| `--help` | Help for the CLI, a group or a command, e.g. `llm-cli ditto runtime start --help`. |

Global options go before the command: `llm-cli -q doctor`, not `llm-cli doctor -q`.

**Output:** the start banner and log messages go to stderr; results go to stdout. For `llm-cli ditto ask` only the
answer goes to stdout, and for `llm-cli mcp serve` stdout carries the MCP protocol.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The command failed (for example a failed build, a failed deployment, a damaged audit log, an AI error) |
| 2 | Invalid input: unknown option, an unsupported format/board/quantization combination, an unknown provider or model tier |
| 130 | `build start` was interrupted with Ctrl+C |

`llm-cli doctor` uses 0 (OK), 1 (warnings) and 2 (errors); `llm-cli ditto eval --min-pass` returns 1 when a run
stays below the given share.

---

## Overview of all commands

| Command | What it does |
|---|---|
| `doctor` | Check the installation and explain how to fix problems |
| `targets list` / `targets validate` | List hardware targets / check that a target can be loaded |
| `build start` / `build status` | Convert and quantize a model / show the builds of this process |
| `deploy list-artifacts` / `deploy package` / `deploy run` | List finished builds / pack a build into a ZIP / upload and install it on a board |
| `ditto setup` | Set the tool permissions (the same dialog as on the GUI's first start) |
| `ditto ask` | Ask Ditto one question |
| `ditto eval` | Grade Ditto on fixed test cases |
| `ditto runtime install/start/status/stop` | The managed local model server (llama.cpp) for offline mode |
| `ditto models list/download` | The model tiers for offline mode |
| `tools list` / `tools permissions` / `tools audit` | The framework tools, their permissions and the audit log |
| `mcp serve` / `mcp config` | Run the MCP server / show how to connect AI clients |
| `secrets set` / `secrets list` | Store an API key or token / list the stored names |
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
| `--tier INTEGER RANGE` | `1` (default): harmless fixes (permissions, folders). `2`: also removes caches (dangling framework images and the Docker build cache). |

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
| `-t, --target TEXT` | **Required.** Target folder name in `targets/`, e.g. `Rockchip`. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Output format (default `gguf`). The list is longer than what any target supports; Rockchip accepts `gguf`, `rkllm` and `rknn`. |
| `-b, --board TEXT` | Board of the target family, e.g. `rk3588`, `rk3576`, `rk3566`. Default: the target's default board (Rockchip: `rk3588`). Not case-sensitive. |
| `-q, --quantization TEXT` | Quantization, e.g. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Default: the format's default for the board. Common spellings such as `INT8` are translated. |
| `-o, --output-dir TEXT` | Output folder, relative to the current folder. Default: `output/build_<date>_<time>` in the installation. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimization level (default `balanced`), passed to the build scripts as `OPTIMIZATION_LEVEL`. The Rockchip scripts do not use it. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in this process's build queue (default `normal`). |
| `--parallel` | Has no effect in this version (it is always on, and one call runs one build). |
| `-F, --follow` | Print each stage while the build runs. |
| `--gpu / --no-gpu` | Pass the host's NVIDIA GPU into the container (default off; needs `nvidia-smi` on the host). Rockchip conversions do not need it. |
| `--imatrix / --no-imatrix` | Importance matrix for GGUF (default off). Required for `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`. |
| `--dataset PATH` | Your own calibration file (must exist): a text file for the GGUF importance matrix or for RKLLM, a list of sample inputs for RKNN `i8`. Without it, the importance matrix uses the shipped calibration text. |

Behaviour:

- The command stays in the foreground until the build has finished. Ctrl+C cancels the build (exit code 130).
- If the build fails with a known problem and a fix the framework can apply itself, the CLI shows the cause and
  asks "Apply this fix and retry the build?". Without a terminal the proposal is dismissed. Manual steps are only
  shown.
- Results: `<output folder>/<target>/<model>/<quantization>/` with the model file, `build_info.json` and
  `Model_Card.md`.
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
a matching `llama-server` command.

| Argument / option | Meaning |
|---|---|
| `PACKAGE_PATH` | The ZIP file from `deploy package` (must exist). |
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
**Tools > Configure AI Agent (Ditto)...**, or `ai_provider` and `ai_model` in `config.yml`). API keys are stored
with [`llm-cli secrets set`](#secrets).

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
with each tier in turn and **stops the local server at the end**, also one that was running before.

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
llm-cli tools audit [--limit N] [--verify]
```

Shows the most recent tool calls: time, actor (`ditto` or `mcp`), tool, decision, whether a person confirmed it,
and the outcome.

| Option | Meaning |
|---|---|
| `--limit INTEGER` | Number of entries (default 30). |
| `--verify` | Check the chain instead: changed or removed entries, and removal of the newest entries (anchor file). Exit code 0 if intact, otherwise 1. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
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
framework's own Python so the printed command uses it.

| Option | Meaning |
|---|---|
| `--write [claude-desktop]` | Add the server to Claude Desktop's configuration. Other entries are kept and a backup (`.bak`) is written. Restart Claude Desktop afterwards. |

```bash
llm-cli mcp config
llm-cli mcp config --write claude-desktop
```

You sign in to these clients with your own account or key; the framework never sees it.

---

## secrets

API keys and tokens are stored encrypted in `~/.llm-framework/secrets.store`; the key for that file is in your
operating system's keyring.

### secrets set

```
llm-cli secrets set KEY [--password TEXT]
```

Stores a secret under the name `KEY`. Without `--password` the value is asked twice without echo, which keeps it
out of your shell history.

| Option | Meaning |
|---|---|
| `--password TEXT` | The value (avoid this: it ends up in your shell history). |

Names the framework uses:

| Name | Used for |
|---|---|
| `anthropic_api_key`, `openai_api_key`, `gemini_api_key`, `openrouter_api_key`, `custom_api_key` | Ditto's providers |
| `hf_token` | Gated Hugging Face models in builds |
| `github_token` | `swarm upload` |
| `qdrant_api_key` | A remote Qdrant server for the knowledge base (setting `qdrant_url`) |

```bash
llm-cli secrets set anthropic_api_key
llm-cli secrets set hf_token
```

### secrets list

```
llm-cli secrets list
```

Lists the stored names, never the values.

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
generator) in the configured targets folder.

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
- `build start --parallel` and `--optimization` have no effect on Rockchip builds.
- `scan --output` does not change where the probe writes.
- `module create` writes to `targets/` of the current folder.
- `config show` shows only the CLI's base settings; `config repos --add` does not reach builds.
- `deploy package --docker` is experimental.
- There is no CLI command yet to choose Ditto's provider permanently; use the GUI, `config.yml` or
  `LLM_FRAMEWORK_AI_PROVIDER`. Signing in per provider is being built ([roadmap](BLUEPRINT.md#roadmap)).
