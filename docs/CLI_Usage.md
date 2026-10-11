Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-11

**English** · [Deutsch](CLI_Usage_DE.md)

# Command line (`llm-cli`)

The command line can do everything you need on a server without a screen, in scripts and in CI: check the
installation, convert models, deploy them, set up Ditto and its local model, manage tool permissions, run the
MCP server and share or install community packs. Every command and option below was taken from the real `--help`
output of this version.

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
- [zoo](#zoo): `list`, `show`, `install`, `verify`, `export`, `keys`
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
command prints itself, including its error messages. For `llm-cli ditto ask` only the answer goes to stdout, and for
`llm-cli mcp serve` stdout carries the MCP protocol.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The command failed (for example a failed build, a failed deployment, a damaged audit log, an AI error) |
| 2 | Invalid input: unknown option, an unknown target or an unsupported format/board/quantization combination (`build start`), an unknown provider (`--provider`), an unknown model tier (`ditto models download`), no matching evaluation case (`ditto eval`), a missing pack id, file or source (`zoo install`, `zoo export`), a key file without a usable key (`--to`, `--from`) |
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
| `ditto setup` | Set the tool permissions (the same choices as the GUI's first-start dialog) |
| `ditto ask` | Ask Ditto one question |
| `ditto eval` | Grade Ditto on fixed test cases, or the knowledge-base search |
| `ditto runtime install/start/status/stop` | The managed local model server (llama.cpp) for offline mode |
| `ditto models list/download` | The model tiers for offline mode |
| `tools list` / `tools permissions` / `tools audit` | The framework tools, their permissions and the audit log |
| `mcp serve` / `mcp config` | Run the MCP server / show how to connect AI clients |
| `secrets set` / `secrets list` | Store an API key or token / list the stored names |
| `module create` / `module generate-ai` | Create a new hardware target, by questions or from a probe file with Ditto |
| `scan` | Run the hardware probe on this computer |
| `config show` / `config repos` | Show the CLI's base settings / manage source repository overrides |
| `zoo list` / `zoo show` | List the community zoo and the installed packs / show one entry |
| `zoo install` / `zoo verify` | Install a community pack after checks and your confirmation / check installed packs or a pack file |
| `zoo export` / `zoo keys` | Make a signed pack of your own target, build or knowledge base (and open a pull request) / show your public keys |

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

Checks Python, the Docker daemon and buildx, free disk space for the cache and output folders, the targets and
their capabilities, the privacy of the personal folder, the secrets store and the keyring, the tool
permissions, the audit log, the shipped calibration data and the installed community packs (revoked, changed or
imported from a file). Each finding comes with a hint how to fix it.

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
prompt injection), for example to check whether a 2-bit local model is good enough. With `--kind retrieval` it
grades the knowledge-base search instead, without any AI model ([below](#retrieval-evaluation)). Nothing is built
or changed: chat cases run against a stand-in build service. Cloud providers bill every request (about 20 per run
and repeat). How it works: [Features.md](Features.md#ditto-evaluation).

| Option | Meaning |
|---|---|
| `--cases FILE` | Cases file. Default: `evals/ditto/cases.yml` of the installation, and for `--kind retrieval` `evals/retrieval/cases.yml`. |
| `--provider TEXT` | Provider for the run. Default: AI settings. |
| `--model TEXT` | Model for the run. Default: AI settings. |
| `--tier TEXT` | Run against the managed local model of this tier; repeatable (`--tier small --tier small-q2`). Cannot be combined with `--provider`/`--model` (exit code 2). |
| `--kind [healing\|optimizer\|module\|chat\|retrieval]` | Only cases of this kind; repeatable. `retrieval` runs only when it is chosen here, when `--case` is given without `--kind`, or when the `--cases` file holds retrieval cases. |
| `--rag-variant [fused\|rerank]` | Retrieval only: the search variant to grade; repeatable, one run per variant. `fused` (default): the hybrid search. `rerank`: the hybrid search plus the reranker of `rag_reranker` and its relevance floor. |
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

The result is a table per run: passed share, share per kind and average seconds per case. A cases file that
cannot be read, or filters that match no case, stop with exit code 2.

#### Retrieval evaluation

`llm-cli ditto eval --kind retrieval` measures how well the knowledge base finds the right excerpt. The shipped
cases are 42 searches in German and English: questions across the two languages, identifiers such as
`GGML_CUDA`, answers deep inside a large table or script, the last lines of failed builds, the search strings Ditto
itself sends, and 4 unrelated questions. They run against a corpus of 30 framework and SDK documentation excerpts
in `evals/retrieval/corpus/` (a cases file names its corpus folder with `corpus:`, relative to the file).

- The corpus is indexed into a temporary knowledge base with the configured local embedding model
  (`rag_embedding_model`) and deleted afterwards. Your own knowledge base is not touched, and no AI model and no
  Qdrant server is used. `--provider`, `--model`, `--tier` and `--effort` have no effect on these cases.
- The embedding model, and for `rerank` the reranker, is downloaded into the models folder on first use (not in
  offline mode); after that the evaluation works offline.
- Each search returns up to 10 excerpts. A case passes when the first relevant excerpt is within its limit (the
  case's `within`, default 3); an unrelated question passes only when no excerpt comes back.
- Besides the run table there is a "Retrieval" table with one row per variant: hit@1, hit@3, hit@k (k = each
  case's limit), MRR over the 10 results, "Unrelated: no excerpt" (the share of unrelated questions that got nothing) and the
  median search time ("p50 ms"). `--json` adds the retrieved documents and scores of every case, the digests of
  cases and corpus, the indexing time and the memory in use.
- `rerank` needs a reranker. With `rag_reranker: off`, or `auto` on a computer with less than 6 GB RAM, there is
  none, and the command stops with exit code 2 and the reason; set `rag_reranker` to a model id to grade one
  anyway. A reranker that cannot be loaded stops the run with exit code 1, and a search in which the reranker did
  not run counts as failed. The floor is `rag_rerank_min_score` (default 0: the reranker's calibrated floor).
- A corpus that cannot be read, or a case that expects a document the corpus does not have, stops with exit
  code 2. If the corpus cannot be indexed (for example in offline mode before the embedding model was downloaded), the
  command exits with code 1.

Outside the evaluation, only build recommendations use the reranker. Ditto's chat and the MCP tool
`search_knowledge` rerank only with `rag_rerank_default: true` (default off).

```bash
# The hybrid search with the configured embedding model
llm-cli ditto eval --kind retrieval

# Hybrid search and reranker side by side, report as JSON
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json

# Only two cases
llm-cli ditto eval --kind retrieval --case retr-en-rknn-onnx-convert --case retr-en-cuda-oom
```

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
exit code 1 and list the choices; choose another protection with the setting `secret_storage` in `config.yml`, or
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
| `qdrant_api_key` | A remote Qdrant server for the knowledge base (setting `qdrant_url`) |
| `community_signing_key`, `community_encryption_key` | Your key pairs for community packs, created on first use by [`llm-cli zoo`](#zoo-keys) or the GUI's community export and import; do not set them by hand |

```bash
llm-cli secrets set anthropic_api_key
llm-cli secrets set hf_token
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
image (default `debian:bookworm-slim`), description, system packages (default `build-essential, cmake, git`) and
default CPU flags. Every value is checked before files are written, the finished folder must pass the target
validators ([Module Developer Guide](Module_Developer_Guide.md#91-what-the-validators-check)), and an existing
target is never overwritten.

| Option | Meaning |
|---|---|
| `--name TEXT` | Name of the new target. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU architecture. |
| `--sdk TEXT` | Special SDK, e.g. `CUDA`, `RKNN` (default `None`). |

The target is written to `targets/` **of the current folder**; run the command from the installation folder. Its
`target.yml` has a `capabilities:` block (support level `community`) with the module name as the only board and
GGUF as the only format; add your real boards and formats there. The command's hint to customize
`modules/config_module.sh` refers to a legacy script that the build pipeline does not call
([Module Developer Guide](Module_Developer_Guide.md#10-legacy-files)).

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto proposes a target module from a hardware probe file (`target_hardware_config.txt`, see [scan](#scan)). The
whole proposal is printed as JSON; only after you confirm is the module created (checked again by the module
generator) in the configured targets folder. Neither `module create` nor `module generate-ai` attaches pages from
the knowledge base; that is an option on the last page of the GUI wizard.

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

## zoo

The community zoo: hardware targets, recipes and knowledge packs that other users share. A recipe is a "model
template": a model at a pinned commit, built for a target, board, format and quantization, with the hashes of what
came out. The framework never uploads anything by itself; sharing is always a step you start.

**Current state:** the community repository and its signed index are not published yet. The settings
`community_index_url` and `community_repo` are empty, and `configs/community_keys.yml` lists no publisher key. Until
then `zoo list` shows only installed packs, installing by ID and `zoo export --pr` stop with a message, and packs
are shared as files (`zoo install --file`).

How packs are protected:

- A pack is a zip file with a manifest (`pack.json`: sha256, size and mode of every file) and usually an Ed25519
  signature of its author (`pack.sig`). Packs exported with this framework always have one; for a pack without
  one, the summary says "not signed by its author". A pack carries scripts, recipes and text, never model weights
  or other binary files.
- The list of packs comes from a signed index: `index.json` at `community_index_url` and `index.json.sig` next to
  it, checked with the keys in `configs/community_keys.yml`, each accepted until its expiry date. An index that does
  not verify is not used. The last good index is cached in `~/.llm-framework/community/`, fetched again when it is
  older than a day and never replaced by an older one.
- Installing by ID reads the current index first, so a revocation is never missed, and downloads the pack pinned
  by its size and sha256. Every pack is extracted into a quarantine folder and checked there: exactly the listed
  files with their hashes, no links, the target validators
  ([Module Developer Guide](Module_Developer_Guide.md#91-what-the-validators-check)), and the recipe and knowledge
  formats. Only after you confirm the summary is it moved into place, with a provenance file
  `.lcf-provenance.json`.
- Author signatures show who made a pack. The first key seen for an author name is remembered; a later pack of
  that name signed with another key is shown with a warning.

Trust levels:

| Trust | Meaning |
|---|---|
| `verified` | In the signed index: reviewed and reproduced independently. |
| `community` | In the signed index: reviewed. |
| `local` | A file the signed index does not list. Nobody reviewed it: installing it always asks, and every build of such a target shows a warning. |
| `revoked` | In the index's list of revoked packs. It is not installed; installed copies are flagged by `zoo verify`, `llm-cli doctor` and the Community Hub and are not built. `zoo verify`, `llm-cli doctor` and builds keep applying the cached revocation after the key that signed it has expired; `zoo list` and the Community Hub then no longer use that index. |

Targets that ship with the framework, such as Rockchip, have no provenance file and do not appear in the zoo.

Installed targets go into the targets folder, recipes into `community/recipes/<id>/` and knowledge packs into
`community/knowledge/<id>/` of the installation. A knowledge pack is imported into your knowledge base right after
it is installed; the knowledge base must be enabled at that moment.

In the GUI: **Tools > Open Community Hub** (list, filters by kind and trust, "Import file...", "Build" on an
installed recipe), the module wizard's last page and the Builder log's context menu ("Export a finished build for
the community...").

### zoo list

```
llm-cli zoo list [--kind target|recipe|knowledge] [--refresh] [--json]
```

Lists the entries of the signed index (the newest version of each) and the installed packs: kind, ID, version,
trust, installed version, author and title. Below the table it says which index was used, or why there is none
(for example "No community index: No community index is configured (setting community_index_url)."); installed
packs are listed in either case.

| Option | Meaning |
|---|---|
| `--kind [target\|recipe\|knowledge]` | Only this kind. |
| `--refresh` | Download the signed index again, even if the cached one is less than a day old. Without network the cached index is used. |
| `--json` | Machine-readable output: a list with `kind`, `id`, `version`, `trust`, `installed_version`, `revoked`, `author` and `title`. |

### zoo show

```
llm-cli zoo show PACK_ID [--kind target|recipe|knowledge]
```

Shows one entry: kind, ID, version, author and licence, trust (and why it was revoked), where it is installed, the
download address with size and sha256, the description, and for an installed recipe its `recipe.yml`. The ID is not
case-sensitive. The command reads only the cached index and the installed packs; run `zoo list --refresh` first to
see new entries. An unknown ID exits with code 1.

| Option | Meaning |
|---|---|
| `--kind [target\|recipe\|knowledge]` | The kind, if the ID exists for several kinds. |

### zoo install

```
llm-cli zoo install PACK_ID [--kind target|recipe|knowledge] [--yes]
llm-cli zoo install --file FILE [--passphrase] [--from KEY_FILE ...] [--yes]
```

Installs a pack from the signed index (by ID) or from a file. Give exactly one of the two (exit code 2 otherwise).
The command prints a summary to stderr and asks "Install this pack?" (default no). A summary looks like this:

```
target my-board 1.0.0 by <author> (licence MIT)
Trust: community
Source: https://<address of the pack>
Files: 14 (48213 bytes)
Installs to: /opt/llm-conversion-framework/targets/my-board
Note: signed by author key 0123456789abcdef (first pack seen from this author)
```

| Argument / option | Meaning |
|---|---|
| `PACK_ID` | An ID from the index (`zoo list`). The newest version is installed. |
| `--kind [target\|recipe\|knowledge]` | Kind of `PACK_ID` (default `target`). A pack file names its own kind. |
| `--file FILE` | Install a pack file someone gave you, or an encrypted pack (`.lcfpack`). Its trust is `local` unless the signed index lists its sha256. Works without network. |
| `--from FILE` | Encrypted packs: accept them only from these senders. The file holds the senders' public keys as `llm-cli zoo keys` prints them; repeatable. Without it, any sender is accepted; the summary always names the sender's key ID. |
| `--passphrase` | Encrypted packs: ask for the passphrase instead of opening the pack with your own key. |
| `--yes` | Do not ask for a pack whose trust comes from the signed index. A `local` pack always asks. |

Behaviour:

- Installing by ID needs the network: the index is downloaded again first, and if that fails nothing is installed.
  A revoked pack is refused.
- An encrypted pack is checked against its sender's signature before it is decrypted. It opens with your own key
  (see [zoo keys](#zoo-keys)) or, with `--passphrase`, with the passphrase. The Community Hub opens packs encrypted
  to your key; packs with a passphrase need this command.
- Nothing is installed over an existing folder of the same name, also when only the case differs; remove the old
  folder first.
- Exit code 1: a check failed (the problems are listed and the quarantine folder is removed), you answered no, or
  there is no terminal for a pack that needs the question.

```bash
llm-cli zoo install my-board
llm-cli zoo install smollm2-135m-instruct-rk3588-gguf-q4_k_m --kind recipe
llm-cli zoo install --file contribution-target-my-board-1.0.0.zip
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --from colleague.keys
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --passphrase
```

A recipe needs its target: install the target first, so the recipe is also checked against the target's
capabilities (without the target, the summary only notes that it is missing). "Build" on an installed recipe in
the Community Hub fills in the Builder, including the model's pinned commit.

### zoo verify

```
llm-cli zoo verify [PACK_FILE]
```

Without an argument it checks every installed community pack against the cached index, without downloading
anything: revoked, or changed since it was installed. It prints one line per pack (✔, or ✘ with the reason), or
"No community packs installed.". Exit code 1 if a pack has a problem, otherwise 0.

With `PACK_FILE` it checks a pack file without installing it: format, file list and hashes, the checks of its
kind, an included author signature, and that the privacy scrub would not change anything in it. It prints
"acceptable" or every problem (exit code 1). This form works without a keyring; the community repository's CI runs
it on every submitted pack.

```bash
llm-cli zoo verify
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip
```

### zoo export

```
llm-cli zoo export target|recipe|knowledge [SOURCE] --author NAME [options]
```

Makes a pack of your own work: a target (`SOURCE`: the target's folder name), a recipe (`SOURCE`: the folder of a
finished build or its `build_info.json`) or your knowledge base (no `SOURCE`). The pack is privacy-scrubbed, checked
the way an installation would check it, signed with your key and written to a folder; nothing is sent. The summary
on stdout shows the file, the key it was signed with, a diff for every file the privacy scrub changed (at most 40
lines per file) and notes.

| Argument / option | Meaning |
|---|---|
| `target\|recipe\|knowledge` | Kind of pack. |
| `SOURCE` | The target's folder name, or a build folder or its `build_info.json`. Required for `target` and `recipe` (exit code 2 without it). |
| `--author TEXT` | **Required.** Name shown as the author; a pseudonym is fine. |
| `--license TEXT` | Licence of the pack as an SPDX ID. Default `MIT`; for knowledge `NOASSERTION`, because the pages keep their owners' terms. |
| `--model-license TEXT` | Recipes: licence ID of the model (default `unknown`). |
| `--id TEXT` | Knowledge: ID of the pack (default `knowledge-` and today's date, e.g. `knowledge-20261011`). |
| `--to FILE` | Encrypt the pack for private sharing to the public keys in this file (what `llm-cli zoo keys` printed on the recipient's computer); repeatable. |
| `--passphrase` | Encrypt the pack for private sharing with a passphrase (asked twice, at least 12 characters). Can be combined with `--to`. |
| `--out DIRECTORY` | Folder for the pack. Default: `community/contributions/` in the installation. |
| `--pr` | Hand the pack to a pull request on the community repository (see below). |
| `--yes` | With `--pr`: submit without asking after the summary. |

What goes into the pack:

- **target:** every file of the target folder. Links, special files and binary files are refused, and the target
  validators must pass, which includes a valid `capabilities:` block. The version is `metadata.version` from
  `target.yml` if it has the form `1.2.3` (a suffix such as `-rc1` is allowed), otherwise `1.0.0`. A target you
  installed from the zoo is refused: it remains its author's pack.
- **recipe:** only a finished build of a Hugging Face model whose `build_info.json` records the model's commit and
  the target's fingerprint (builds of this version). If the target is your own, the summary reminds you to export
  it too, or others cannot build the recipe.
- **knowledge:** the whole knowledge base (it must be enabled), privacy-scrubbed. Every page keeps its source
  address; pages without one are left out.

The file is called `contribution-<kind>-<id>-<version>.zip`, or `<kind>-<id>-<version>.lcfpack` when it is
encrypted. Your key pair is created in the secrets store on first use (see [zoo keys](#zoo-keys)).

The privacy scrub replaces home folders with `~`, and IP and MAC addresses, e-mail addresses, JWTs, this
computer's host name and your user name with placeholders such as `<ip>` and `<user>`, and it redacts API keys.
Loopback addresses, image digests (`name@sha256:...`) and default accounts such as `/root` stay. Read the diff: a
script whose address was replaced may no longer work.

**Pull request (`--pr`).** After the summary the command asks "Submit this pack to the public community
repository?". If you answer no, or there is no terminal and no `--yes`, nothing is submitted (exit code 1). Then:

- With the GitHub CLI and git installed and `gh` logged in (`gh auth login`), it forks the repository named in the
  setting `community_repo` (`owner/name`), commits the pack to `contributions/<kind>/` on a new branch of your fork
  and opens the pull request with `gh pr create`.
- Otherwise, or if that fails, it opens the repository's upload page
  (`https://github.com/<owner>/<name>/upload/main/contributions/<kind>`) in your browser. Drop the file there;
  GitHub creates the fork for you.

The framework never asks for, reads or shows a token: `gh` and `git` use their own stored sign-in. Encrypted packs
are for private sharing and are never submitted (exit code 1). While `community_repo` is empty, as it is for now,
the command stops with "No community repository is configured" and exit code 1; the pack stays in its folder. The
community repository's CI checks every submitted pack with `llm-cli zoo verify`
([CONTRIBUTING](../CONTRIBUTING.md#contributing-to-the-community-zoo)).

```bash
# A target, checked the way the community repository's CI will check it
llm-cli zoo export target my-board --author "<your name>"
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip

# A finished build as a recipe
llm-cli zoo export recipe output/build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 \
  --author "<your name>" --model-license Apache-2.0

# Your knowledge base under an ID of your choice
llm-cli zoo export knowledge --author "<your name>" --id rk3588-notes

# Encrypted for a colleague, or with a passphrase
llm-cli zoo export target my-board --author "<your name>" --to colleague.keys
llm-cli zoo export target my-board --author "<your name>" --passphrase

# Export and open a pull request
llm-cli zoo export target my-board --author "<your name>" --pr
```

### zoo keys

```
llm-cli zoo keys
```

Prints your public keys: an `ed25519:` line, which checks your signatures (others use it with `--from`), and an
`x25519:` line, to which others encrypt packs for you (`--to`). They are not secret; save the output to a file and
give it to the people you exchange packs with. The keys are created on first use and kept only in the encrypted
secrets store (names `community_signing_key` and `community_encryption_key`). A stored key that cannot be read is
never replaced automatically.

```bash
llm-cli zoo keys > my.keys
```

```
# LCF public keys, key id 0123456789abcdef. Not secret: give them to people who send you
# encrypted packs or verify your signed packs.
ed25519:<base64>
x25519:<base64>
```

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
  `LLM_FRAMEWORK_AI_PROVIDER`. Signing in per provider is being built ([roadmap](BLUEPRINT.md#roadmap)).
- The community repository and its signed index are not published yet (`community_index_url` and
  `community_repo` are empty, `configs/community_keys.yml` lists no key): `zoo list` shows only installed packs,
  and `zoo install <id>` and `zoo export --pr` stop with a message. Packs can be shared as files.
- `build start` has no option for a model commit: the pinned commit of a recipe is used when the Builder is filled
  from the Community Hub, or when an AI client passes `revision` to the MCP tool `start_build`.
- There is no CLI command for Deep Ingest (adding documentation pages to the knowledge base); it runs in the GUI's
  module wizard (**Tools > Create Hardware Module...**).
