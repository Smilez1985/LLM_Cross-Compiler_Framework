Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-16

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
- [mojo](#mojo): health check (Mojo Oracle) and repairs (Mojo Miracle)
- [doctor](#doctor): the old name of `mojo`
- [knowledge](#knowledge): `snapshots` (`list`, `prune`, `delete`, `export`)
- [targets](#targets): `list`, `validate`
- [build](#build): `start`, `save`, `status`
- [deploy](#deploy): `list-artifacts`, `package`, `run`
- [ditto](#ditto): `login`, `logout`, `auth`, `setup`, `ask`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [mcp](#mcp): `serve`, `config`, `token add|list|rotate|revoke`
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): hardware probe
- [device](#device): `list`, `settings`, `runtime`
- [config](#config): `show`, `output-folder`, `repos`
- [zoo](#zoo): `list`, `show`, `install`, `verify`, `export`, `keys`
- [trash](#trash): `list`, `restore`, `empty`
- [Known limitations](#known-limitations)

---

## Calling the CLI

| Installation | Command |
|---|---|
| Linux installer (`install.sh`) | `cd /opt/llm-conversion-framework` and then `.venv/bin/python -m orchestrator.cli ...` |
| Windows installer (`install.bat`) | `cd /d "C:\Program Files\LLM-Conversion-Framework"` and then `.venv\Scripts\python.exe -m orchestrator.cli ...` |
| Development setup (`uv sync`) | `llm-cli ...` |

`python -m orchestrator.cli` needs the installation folder on Python's search path. Either run it from that
folder, or set `PYTHONPATH`. On Linux this alias works from any folder:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -m orchestrator.cli'
```

The examples below use `llm-cli`. `llmcf` is the same program under its new name (unreleased): the development
setup and the framework container have both commands, and `llmcf mojo` does exactly what `llm-cli mojo` does.

### Environment variables

| Variable | Effect |
|---|---|
| `LLM_FRAMEWORK_CONFIG_DIR` | Settings folder (`config.yml`). Default: `configs/` in the installation if writable, otherwise `~/.llm-framework`. The working directory is never used. |
| `LLM_FRAMEWORK_HOME` | Personal state folder. Default: `~/.llm-framework`. If you may not write to the installation folder (for example `C:\Program Files`), the data folders `output/`, `cache/` and `models/` are there too. |
| `LLM_FRAMEWORK_MODELS_DIR` | Models folder; Ditto's local models are in its `ditto/` subfolder. Default: `models/` in the installation if writable, otherwise in the personal folder. |
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
| `-c, --config PATH` | Use this settings file (YAML or JSON, must exist). Its folder becomes the settings folder for this run (a `config.yml` there is read as well), and the file's own values win over it. The usual settings folder is not read then. A file that cannot be read is named on stderr and ignored; the other settings (that `config.yml`, `LLM_FRAMEWORK_*` variables, defaults) still apply. |
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
| 1 | The command failed (for example a failed build, a failed deployment, a package that could not be created, a secret that could not be stored, a hardware probe that wrote no file, a damaged audit log, an AI error) |
| 2 | Invalid input: unknown option, an unknown target or an unsupported format/board/quantization combination (`build start`), an unknown provider (`--provider`), an unknown model tier (`ditto models download`), no matching evaluation case (`ditto eval`), a missing pack id, file or source (`zoo install`, `zoo export`), a key file without a usable key or not a text file (`--to`, `--from`), `--from` for a pack that is not encrypted (`zoo install`), `--to` together with `--passphrase` (`zoo export`) |
| 3 | Sign-in: the provider rejected the credential (`ditto login`, `ditto auth status --check`) |
| 4 | Sign-in: the provider or the local server cannot be reached |
| 5 | The credential vault cannot be used (no usable keyring, no passphrase, a damaged vault); the message names the choices. Every command that needs the vault uses it |
| 6 | Sign-in: the method is not available in this version, or the provider was removed |
| 130 | `build start` was interrupted with Ctrl+C (the build was cancelled first) |

`llm-cli mojo` and `llm-cli doctor` use 0 (OK), 1 (warnings) and 2 (errors); `llm-cli ditto eval --min-pass` returns
1 when a run stays below the given share.

---

## Overview of all commands

| Command | What it does |
|---|---|
| `mojo` | Check the installation (Mojo Oracle) and repair what can be repaired safely (Mojo Miracle) (unreleased) |
| `doctor` | The old name of `mojo`, with its options of 3.0.0 |
| `knowledge snapshots` | List the knowledge base's snapshots with full path, name, size and what each is; delete the ones you pick, or export them (unreleased) |
| `knowledge index-docs` | Add the framework's own documentation (and with `--with-code` its code) to the knowledge base (unreleased) |
| `knowledge storage` | Where the knowledge base is stored (Qdrant Edge), the conversion of an older one, and its old folder: `convert`, `delete-old` (unreleased) |
| `trash list` / `restore` / `empty` | What deletions moved to the trash; put entries back; delete the ones you pick for good (unreleased) |
| `targets list` / `targets validate` | List hardware targets with their boards and formats / check that a target loads and its capabilities are valid |
| `build start` / `build status` | Convert and quantize a model / show builds, also those the GUI, the MCP server or another CLI call started |
| `build save` | Save a build that waits in the framework's build folder to the output folder or another folder (unreleased) |
| `deploy list-artifacts` / `deploy package` / `deploy run` | List finished builds / pack a build into a ZIP / upload and install it on a board |
| `ditto login` / `ditto logout` | Sign Ditto in to a provider (the key from a hidden prompt, stdin or an environment variable, checked before it is stored) / sign out |
| `ditto auth status/methods/storage/rotate-key` | Sign-in state of every provider / the sign-in methods / how the credential vault is protected / a new vault key |
| `ditto setup` | Set the tool permissions (the same choices as the GUI's first-start dialog) |
| `ditto ask` | Ask Ditto one question |
| `ditto eval` | Grade Ditto on fixed test cases, or the knowledge-base search |
| `ditto runtime install/start/status/stop/import/export/prune` | The managed local model server (llama.cpp, built on this machine) for offline mode |
| `ditto runtime ensure` | Install Ditto's local model server when it is not there, bare metal or in Docker; `--force` switches the mode |
| `ditto runtime export-source/import-source` | Carry the pinned llama.cpp source to an air-gapped machine as a git bundle (checked by its commit) |
| `ditto models list/download` | The model tiers for offline mode |
| `tools list` / `tools permissions` / `tools audit` | The framework tools, their permissions and the audit log |
| `mcp serve` / `mcp config` | Run the MCP server / show how to connect AI clients |
| `secrets set` / `secrets list` | Store a token (Hugging Face, GitHub) / list the stored names |
| `module create` / `module generate-ai` | Create a new hardware target, by questions or from a probe file with Ditto |
| `scan` | Run the hardware probe on this computer or, with `--device`, on a board over SSH, or with `--import` store a board's probe file, and print its probe id |
| `device list` / `device settings` | The probed boards / how the runtime is installed on a board (start script or systemd), decided before a deployment |
| `device runtime plan/build/deploy/verify/status` | The self-built llama.cpp runtime of a board: plan it from the board's profile, cross-build it, install it over SSH and check it there |
| `config show` / `config repos` | Show the effective settings and where each comes from / manage source repository overrides |
| `config output-folder` | Show or set the output folder finished models are saved to (unreleased) |
| `zoo list` / `zoo show` | List the community zoo and the installed packs / show one entry |
| `zoo install` / `zoo verify` | Install a community pack after checks and your confirmation / check installed packs or a pack file |
| `zoo export` / `zoo keys` | Make a signed pack of your own target, build or knowledge base (and open a pull request) / show your public keys |

---

## mojo

```
llm-cli mojo [--oracle | --miracle] [--json] [--board NAME]
```

The health check (unreleased; it was called `doctor`). **Mojo Oracle** (the default) only looks: it checks Python, the
Docker daemon and buildx, free disk space for the cache, output and build folders, the targets and their capabilities,
the privacy of the personal folder, the secrets store and the keyring, the tool permissions, the MCP tokens, the audit
log, the chat feed (it can be written, its newest entries verify), the shipped calibration data, the installed community
packs (revoked, changed or imported from a file), the knowledge snapshots (how many, their size against the free space
of their disk, and where; there is no limit, so their number is never a warning), the local llama.cpp runtime (in Docker
mode: Ditto's image) and the boards you trusted. It also says where Docker keeps its images and how much
the framework's build images use (moving that folder is an administrator's step, see [Container
setup](Container_Setup.md#10-where-docker-keeps-images)).

**Boards:** Mojo makes no network contact with a board unless you ask it to. It reports what the framework recorded:
a host key in its `known_hosts` that changed or is gone, and the last deploy, runtime deploy, verify, benchmark or
probe that could not reach a board, with its reason code (`device_host_down`, `device_port_closed`,
`device_auth_failed`, `device_host_key_changed`) and the next step. For a board such a fact points to it asks at a
terminal "Check <board> now? [y/N]"; with `y` it opens one connection to the board's SSH port (no login, no password)
and prints the result. Without a terminal it prints the command instead. `--board NAME` checks one board right away.

For every warning and error it prints what it found, where (the full path or the object), why it matters, a hint and
whether Mojo Miracle can repair it, with the plan of a repair (what changes, before and after, whether it can be
undone). One sentence at the end counts every problem by what can be done about it. When Mojo Miracle can act on a
problem, it asks at a terminal "Run Mojo Miracle now? [y/N]"; with `y` Mojo Miracle runs without the command typed
again, and No (the default) changes nothing.

**Mojo Miracle** makes only harmless repairs: it narrows the state folder to your user, or switches Ditto's server to
an intact stored llama.cpp build when the active one is missing. Each repair is a plan bound to its hash, recorded in
the audit log before it runs (or nothing changes), applied only while nothing changed since the plan (a folder is
opened without following a link), and checked again afterwards; only one Mojo Miracle runs at a time. Freeing disk
space means deleting: at a terminal Mojo Miracle shows the framework's build images (name, image id, size, what each
is; nothing ticked) and deletes exactly those you type and confirm (the default answer is no); the Docker build cache
is never cleared. Then it reports every problem: repaired (checked again), already gone, still there, or not
repaired with the reason (deleting needs your list, needs you, needs an administrator, not repairable automatically,
failed). A problem the check afterwards finds for the first time is listed as new. Details: [Mojo.md](Mojo.md).

| Option | Meaning |
|---|---|
| `--oracle` | Only look and report what was found (the default); at a terminal it then offers Mojo Miracle. |
| `--miracle` | Repair what can be repaired, then report what was repaired, what was not and why. Without a terminal (a pipe, a script, an AI app) only the harmless repairs run and nothing is deleted. |
| `--json` | Machine-readable output: the keys of `doctor --json` plus `mode`, `summary` and, per finding, `where`, `why`, `repair_id`, `cannot` and `miracle`, and `check_board` (the board, its device ID and the command that checks it) for a board a recorded fact points to; with `--miracle` also `repaired`, `already_gone`, `not_repaired` and `new_problems`. Never asks, never deletes and contacts no board. |
| `--board NAME` | Check this one trusted board now: its label, device ID or address (one connection to its SSH port, no login). Only with `--oracle`. A name that fits no board, or several, lists the trusted boards and exits with 2. |

Disk space: below 20 GB free is a warning, below 5 GB an error.

```bash
llmcf mojo
llmcf mojo --miracle
llmcf mojo --oracle --board "Kitchen board"
llm-cli mojo --json > mojo.json
```

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

The old name of [mojo](#mojo); options, output and exit codes stay as in 3.0.0, and a short note on stderr names
`llmcf mojo`.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output (findings, fixes, exit code). With `--fix --tier 2` it also lists the framework's build images and where Docker keeps them (`framework_images`), but never deletes them. |
| `--fix` | Apply the automatic fixes up to `--tier`, then check again. These are the harmless, audited repairs of Mojo Miracle (unreleased); a problem still present is shown with the reason. |
| `--tier INTEGER RANGE` | `1` (default): harmless fixes (makes the personal folder private). `2`: also lists every build image of the framework (left over from earlier builds without a name, the named `llm-framework/<target>:last-build` images and the runtime builder images; name, image id, size and what each is), nothing picked. You type the numbers of the ones to delete and confirm exactly those at a terminal (the default answer is no). Without a terminal (a pipe, a script, an AI app) it only lists them. The GUI's **Tools > Framework Build Images...** is the same list. The Docker build cache is never cleared: it cannot be limited to the framework. |

```bash
llm-cli doctor
llm-cli doctor --json > doctor.json
llm-cli doctor --fix --tier 2
```

---

## knowledge

### knowledge snapshots

```
llm-cli knowledge snapshots [list]
llm-cli knowledge snapshots prune
llm-cli knowledge snapshots delete NAME...
llm-cli knowledge snapshots export NAME... --to FOLDER
```

Unreleased. Before every Deep Ingest, every index of the framework's own documents (`knowledge index-docs`), every
restore and every knowledge pack import, the knowledge base is saved as a snapshot in `knowledge_snapshots/` of the
personal folder, so the change can be undone. Before a snapshot is made, its size is estimated: when it does not fit
on the disk (beside the reserve that always stays free), no snapshot is made and the change does not run either.
`list` (the default) shows every snapshot, oldest first, with its name, when it was taken, its size, what it is (for
example "backup of the knowledge base taken before a Deep Ingest") and its full path. It ends with what the snapshots
use against the free space of that disk, and says when the next snapshot (about the size of the newest) would not fit
or would leave little space. It deletes nothing. The GUI shows the same list under **Tools > Knowledge Snapshots...**,
with a check box for each (none ticked).

There is no limit: the framework keeps as many snapshots as the disk holds, deletes none by itself, and no number of
them brings a reminder or a warning. To free space:

- `prune` lists every snapshot with a number (nothing is picked). You type the numbers to delete (for example `1,3`
  or `2-4`), choose `trash` (the default: you can restore them with [`trash restore`](#trash)) or `permanent`, and
  confirm exactly those (the default answer is no).
- `delete NAME...` lists exactly the named snapshots and asks the same way.
- `export NAME... --to FOLDER` copies the snapshots to an existing folder (on this computer, a USB stick, a network
  share) and checks each copy against the original by sha256. A file of the same name there is never overwritten; a
  copy that does not match is removed again and reported. Exporting never deletes a snapshot. Exit code 1 when a
  snapshot was not exported.

Deleting needs a terminal: without one (a pipe, a script, an AI app) the list is printed and nothing is deleted.
Every deletion is recorded in the audit log before anything is moved or deleted.

```bash
llm-cli knowledge snapshots
llm-cli knowledge snapshots prune
llm-cli knowledge snapshots export pre_ingest_20261001_101500 --to /media/usb/backups
```

### knowledge index-docs

```
llm-cli knowledge index-docs [--with-code]
```

Unreleased. Adds the framework's own documentation (README, CONTRIBUTING and the `docs` folder, in both languages) to
the knowledge base, so Ditto can answer questions about the framework itself, and AI clients find it with
`search_knowledge` (kind `docs`). `--with-code` also adds its code and configuration (many more files, takes
minutes): the shell scripts in the top folder of the installation (`install.sh`, `entrypoint.sh`; not `install.bat`),
the `.py`, `.sh`, `.yml`, `.yaml`, `.json` and `.md` files and Dockerfiles in `orchestrator`, `scripts` and
`Docker Setup`, the targets the framework ships (`targets/_template` and the official targets) and the files in the
top folder of `configs`. Files above 512 KB, links, caches and generated folders are left out, and so are names that
start with a dot. Only what the framework ships is read: never your settings (`config.yml`), targets you or an AI
created, or installed packs. It
never happens by itself: only this command, **Tools > Index the Framework's Documentation...** in the GUI (a question
with "No" as the default and a box for the code) or the MCP tool `index_framework_docs` (asked every time) start it. A
snapshot is taken first, so it can be undone with a restore; a file indexed before is replaced. When the disk has no
room for that snapshot, nothing is indexed and the command says how much space is missing (no "continue anyway"); when
little room is left, it says so and goes on. The first snapshot is checked against the size of the knowledge base
itself, and a snapshot that cannot be written stops the index too. A file it read but did not index (an empty file, an
error) is named with the reason. The knowledge base must be switched on. A knowledge pack you export never carries
these texts: every installation indexes them itself.

```bash
llm-cli knowledge index-docs
llm-cli knowledge index-docs --with-code
```

### knowledge sources

```
llm-cli knowledge sources [--kind web|pack|docs|other|any]
```

Unreleased. Lists what the knowledge base holds: every source with its kind, the licence it names (an SPDX-like id
such as `CC-BY-4.0`, `other` for one the framework does not recognise, `-` for none), its copyright notice, and what a
knowledge pack would carry of it and why: **full text** (its licence allows passing the text on), **link and short
facts** (a copyright notice and no such licence, code without a licence, an unknown licence) or **not exported** (a
page of an installed knowledge pack, the framework's own documents). Addresses, titles and notices are site text and
are shown as plain text. The knowledge base must be switched on.

```bash
llm-cli knowledge sources
llm-cli knowledge sources --kind web
```

### knowledge storage

```
llm-cli knowledge storage [show]
llm-cli knowledge storage convert
llm-cli knowledge storage delete-old
```

Unreleased. The knowledge base is stored in Qdrant Edge (`~/.llm-framework/qdrant_edge`). A knowledge base of an earlier
version (`~/.llm-framework/qdrant`) is converted on its first use; vectors and texts are copied and checked, nothing is
embedded again. `show` (the default) reads the record of that conversion and opens nothing: when it was converted, how
many excerpts, and the old folder with its full path and size; or, when the conversion failed, why and when it is tried
again (the old store keeps working meanwhile). The first line names the storage really in use. `convert` tries the
conversion now, also after a failure that is not tried again by itself; it needs the knowledge base switched on (in the
GUI: **Tools > Knowledge Snapshots... > Try the conversion again**). `delete-old` lists the old folder after its
conversion and deletes it only after your answer at a terminal (trash by default, or for good; "No" is the default).
Without a terminal nothing is deleted. Before the conversion, or while it failed, the old folder is the knowledge base
itself and is not offered.

```bash
llm-cli knowledge storage
llm-cli knowledge storage delete-old
```

---

## targets

### targets list

```
llm-cli targets list [-f table|json|yaml]
```

Lists the targets in the configured targets folder. The table shows each target's id, name (for example
"Rockchip Family"), architecture, and the boards and formats its `capabilities:` block declares; a target without
one is marked "no capabilities declared". **The value for `--target` is the id** (for example `Rockchip`); the
target's folder name works as well. `json` and `yaml` give the same data with the quantizations per format.

| Option | Meaning |
|---|---|
| `-f, --format [table\|json\|yaml]` | Output format (default `table`). |

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Checks that the target loads (by id or folder name) and that its `capabilities:` block passes the schema that
the GUI, the CLI, the build service and the tools use; every schema error is printed. Exit code 1 if the target is
not found or its capabilities are invalid. A target without capabilities is reported but counts as valid.

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
| `-t, --target TEXT` | **Required.** Target id as listed by `targets list`, e.g. `Rockchip`, or the target's folder name. Not case-sensitive. An unknown target stops with exit code 2 and the list of known targets. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Output format (default `gguf`). The list is longer than what any target supports; Rockchip accepts `gguf`, `rkllm` and `rknn`. |
| `-b, --board TEXT` | Board of the target family, e.g. `rk3588`, `rk3576`, `rk3566`. Default: the target's default board (Rockchip: `rk3588`). Not case-sensitive. |
| `-q, --quantization TEXT` | Quantization, e.g. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Default: the format's default for the board. Common spellings such as `INT8` are translated. |
| `-o, --output TEXT` | Folder the finished model (golden artifact) is saved to, relative to the current folder; made when it does not exist. Default: the output folder ([`config output-folder`](#config-output-folder), `~/llmcf/output`). At a terminal without it you are asked, with the output folder as the default; `-` keeps the build in the framework's build folder ([`build save`](#build-save) saves it later). A system folder or the framework's program folder is refused (exit code 2). `--output-dir` is the old name and still works. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimization level (default `balanced`), passed to the build scripts as `OPTIMIZATION_LEVEL`. The Rockchip scripts do not use it. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in this process's build queue (default `normal`). |
| `--parallel` | Deprecated and hidden from `--help`: it has no effect (one call runs one build) and prints a warning. |
| `-F, --follow` | Print each stage while the build runs. |
| `--gpu / --no-gpu` | Pass the host's GPU into the container (default off): an NVIDIA GPU when `nvidia-smi` is found, otherwise `/dev/dri` if it exists. Rockchip conversions do not need it. |
| `--imatrix / --no-imatrix` | Importance matrix for GGUF (default off). Required for `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`. |
| `--dataset PATH` | Your own calibration file (must exist): a text file for the GGUF importance matrix or for RKLLM, a list of sample inputs for RKNN `i8`. Without it, the importance matrix uses the shipped calibration text. |

Behaviour:

- The command stays in the foreground until the build has finished. Ctrl+C cancels the build through the build
  service: its running container (image build, security scan or conversion) is stopped, the CLI waits until the
  build has stopped (at most 120 s), says so and exits with code 130. Nothing is written to the output folder. A
  build that is already moving its files into the output folder can no longer be cancelled; the CLI then says that
  it has ended on its own.
- If the build fails with a known problem and a fix the framework can apply itself, the CLI shows the cause and
  asks "Apply this fix and retry the build?". Without a terminal the proposal is dismissed. Manual steps are only
  shown.
- Results: `<output folder>/<target>/<model>/<quantization>/` with the model file, `build_info.json` and
  `Model_Card.md`, and a ZIP of them next to that folder. They appear there only when the build has completed; a
  cancelled or failed build leaves no files there. An earlier finished build of that name is never replaced: the
  new one goes next to it (`Q4_K_M-2`). A folder you chose outside the output folder stays listed by
  `deploy list-artifacts`.
- Gated Hugging Face models need a token: `llm-cli secrets set hf_token`. Only the token stored in the framework is
  sent; a token in `HF_TOKEN` or from `hf auth login` is never used.
- A Hugging Face model is downloaded into `<cache_dir>/models/hf/<owner>--<name>/<commit>/`, one folder per commit,
  file by file; only that folder is mounted into the build containers. A second build of the same commit reuses
  it, and a build of another commit never mixes files with it. Ctrl+C stops the download before its next file.
- A full disk is reported as `insufficient_resources` with the free space of the cache and output disks, and
  the download stops when the cache disk falls below half of the space that must stay free (2 GiB or 2 % of the
  disk). The self-healing proposal for a full disk names the framework's folders on that disk with their sizes
  as a manual step; pruning Docker is proposed only when Docker's data root is the full disk.
- A local model file is mounted into the build containers alone, never the folder it lies in; a model that needs
  files next to it (for example ONNX external data) is given as its folder. Ditto and MCP clients cannot name a path
  at all: they use `<label>:<relative path>` inside the [folders the AI may use](#tools-folders).
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

### build save

```
llm-cli build save ARTIFACT [--to FOLDER]
```

Unreleased. Saves a finished build that waits in the framework's build folder (a GUI build you did not save, or
`build start -o -`), the same as **Save...** in the GUI's Deployment tab. `ARTIFACT` is its folder as
`deploy list-artifacts` shows it (`Rockchip/SmolLM2-135M-Instruct/Q4_0`), its full path, or its id (`a_...`).
Without `--to` it goes to the output folder; at a terminal you are asked, with the output folder as the default.

On the same disk the build is moved (renamed, nothing copied). Onto another disk every file is copied, each copy is
checked against its original by sha256 (a model file also against the hash in its manifest), and only then is the
copy in the build folder removed. A link in the build is never followed and not copied; when anything was left out,
the output names it and the copy in the build folder stays. The free space is checked before copying. A name that
is taken is never overwritten (`-2`). If a check fails, nothing is moved (exit code 1). A system folder, the
framework's program folder, its build folder and its per-user folder are refused.

```bash
llm-cli build save Rockchip/SmolLM2-135M-Instruct/Q4_0 --to /mnt/models
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

Shows builds from the shared build state: every program that builds (CLI, GUI, MCP server) writes a summary of each
build request into the per-user folder (`build_requests/`) and one record per build into the cache folder
(`<cache_dir>/build_records/`) with its request, model, target, format, quantization, status, stage and progress,
so builds that another process started are listed too, including requests still waiting in its queue (column
`Where`: the program that runs it). Without an ID: a table of the 20 newest
builds; with a request ID (`req_...`) or a build ID: the details of each attempt, including the error of a failed
build (credentials redacted). A build whose process has ended before the build finished is shown as `interrupted`.
Exit code 1 if the ID is not found. The command needs neither the credential vault nor Docker. On Linux and macOS
the records folder and its records are readable only by the user who built (modes 0700 and 0600), so in a shared
installation each user sees their own builds.

| Option | Meaning |
|---|---|
| `-a, --all` | Show every kept build instead of the 20 newest (the 200 newest finished or interrupted builds are kept). |

A request that waits for a self-healing decision shows its failed first attempt; decide in the program that runs it.
With a request ID, the fix proposals AI clients made for it (`propose_healing_fix`) follow, each marked as
AI-generated with who proposed it (the caller and the client's own, self-reported name) and its manual step, which
the framework never runs. Without an ID, a line says for how many failed builds there are such proposals.

---

## deploy

### deploy list-artifacts

```
llm-cli deploy list-artifacts
```

Lists the finished builds, read from their manifests: in the output folder, in the framework's build folder ("not
saved yet": GUI builds you did not save, and every earlier build, which stay where they are) and in folders you saved
builds to. Each row shows the folder (relative to its folder), where it is, model, format, quantization, board, size
and the full path.

In the GUI: the **Deployment** tab's list **1. Select Golden Artifact** (the same builds, "not saved yet" for one in
the build folder and "tested on" a board after its benchmark there; the tooltip shows the full path); **Refresh**
reads them again.

### deploy package

```
llm-cli deploy package ARTIFACT_NAME --profile PROFILE [--docker|--no-docker]
```

Creates a ZIP file for the board: the model file, the build's manifest and model card, a generated `deploy.sh` and
`checksums.sha256`. It is written to the output folder as `deploy_<model>_<timestamp>.zip`.

| Argument / option | Meaning |
|---|---|
| `ARTIFACT_NAME` | A build folder as shown by `list-artifacts` (relative to the output folder or the framework's build folder), or any existing path to a build folder or model file. |
| `--profile TEXT` | **Required.** The target, e.g. `Rockchip` (used for device options in Docker mode). |
| `--docker / --no-docker` | Also pack container images (default off). Experimental and not verified in this version; leave it off for a model file. The image (`image_inference_runtime`; a package never carries the knowledge base) must be pinned by its digest (`name@sha256:...`) or built on this computer; an image named only by a tag that was pulled is refused with the reason, and so is the default `llm-framework/inference:local` (a local name) until you have built it here. A failed image export stops the package. |

Only model files are deployed without Docker; builds that produced several weight files are refused. A package that
cannot be made prints the reason and exits with 1; the ZIP is written under a temporary name and renamed when it is
complete.

In the GUI: Deployment tab > **Create package only…**: the selected build (with none selected, a model file you
choose), the tab's **Profile** and **Use Docker Containerization?** for `--docker`. No board is contacted, so this is
the way for a board without network. A folder dialog opens at the output folder: OK keeps it, another folder is
checked as for every golden artifact (Cancel makes nothing). The message names the package's full path, or the reason
and the next step; **Cancel** stops the packaging and no partial file stays.

```bash
llm-cli deploy package Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
```

### deploy run

```
llm-cli deploy run PACKAGE_PATH --host HOST --user USER [--port 22] [--password [VALUE] | --password-stdin]
                   [--trust-fingerprint TEXT]
                   [--label TEXT] [--wait SECONDS]
```

Uploads a package over SSH to a private temporary folder on the board, runs `deploy.sh` there and removes the
folder. `deploy.sh` checks the checksums and copies the model to `~/models` (or `$MODEL_DIR`); for GGUF it prints
a matching `llama-server` command if `llama-server` is installed on the board.

| Argument / option | Meaning |
|---|---|
| `PACKAGE_PATH` | The ZIP file from `deploy package`, which prints its full path (must exist). |
| `--host TEXT` | **Required.** The board's address or host name, for example `192.168.1.50` or `myboard.local`. `--ip` is the old name and still works. |
| `--user TEXT` | **Required.** User on the board. |
| `--port INTEGER` | SSH port (default 22). |
| `--password` | Only for the first connection: used once to install the framework's SSH key, never stored. Without a value it is asked with hidden input; when you give nothing on a terminal it is asked too (Enter if the key is already installed). `--password VALUE` still works for scripts but is deprecated and prints a warning: every user sees the value in the process list, and it stays in your shell history. A value that starts with `-` is read as another option: give such a password at the prompt or with `--password-stdin`. |
| `--password-stdin` | Reads the password from standard input (one line), for example piped from a password manager. Refused on a terminal, where it would be shown while you type; use `--password` there. |
| `--trust-fingerprint TEXT` | Trust a new board without asking if its host key has exactly this SHA256 fingerprint (for scripts). |
| `--label TEXT` | Your name for the board in the trusted devices (default: the label it already has, or its address). |
| `--wait SECONDS` | When the network drops during the deploy, wait this long for the board (default 300, 0 to 86400). |

When the network drops (a board with Wi-Fi only, for example), the deploy waits for the board and shows how long
it has waited and the limit. Ctrl+C cancels, and the CLI then says whether `deploy.sh` had already started on the
board (a second Ctrl+C stops at once). Once the board answers on its SSH port again, the CLI logs in again
with the same host key check and the upload continues where it stopped, after the sha256 of what arrived matched.
`deploy.sh` runs on the board on its own and is started once, never twice. When the board cannot be reached or
refuses the login, the CLI says why with a code and what to do: `device_host_down` (nothing answers: the board is
off, on another network, or the host name does not resolve), `device_port_closed` (the board refuses SSH on that
port), `device_auth_failed` (wrong user name or password, or the framework's key is gone),
`device_host_key_changed` (another host key than the one you trusted).

First connection: the CLI shows the board's host key fingerprint and asks whether to trust it. Compare it with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the board. Without a terminal and without
`--trust-fingerprint` nothing is deployed. A board whose host key has changed is always refused. After a successful
deployment the board is added to the [trusted devices](#deploy-devices) (written to the audit log), so an AI client
may deploy to it later, each time with your confirmation. `ssh` and `scp` (used when paramiko is missing) never
get the framework's standard input.

In the GUI: Deployment tab > **Deploy existing package…** (a ZIP from `deploy package` or **Create package only…**),
or **Deploy to Target**, which packages the selected build first. The board is the **Address or host name** and
**User** fields; **Port**, **Name** and **Wait for the board** are `--port`, `--label` and `--wait`. The host key is
read in the background and its fingerprint shown for you to compare (**No** is the default); the password is asked
with hidden input, used once and never stored. **Cancel** does what Ctrl+C does here.

```bash
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --host 192.168.1.50 --user rock

# A board on Wi-Fi by its host name, waiting up to 10 minutes when the network drops
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --host myboard.local --user rock \
  --wait 600

# In a script, with the fingerprint checked beforehand
llm-cli deploy run output/deploy_model_1791801300.zip --host 192.168.1.50 --user rock \
  --trust-fingerprint SHA256:AbCdEf...
```

### deploy devices

```
llm-cli deploy devices list [--json]
llm-cli deploy devices add HOST --user USER [--port 22] [--label TEXT]
llm-cli deploy devices remove DEVICE_ID
```

The trusted devices (`~/.llm-framework/devices.json`): the only devices AI clients and Ditto may name for
`deploy_to_device`, and every such deploy is still asked. A record holds your label, the address, the user, the host
key you confirmed and whether the framework's own SSH key logs in there (paired); the device id is derived from the
host key, the same id `llm-cli device list` shows. Records are written only by you: by `deploy run`,
`scan --device` and `device runtime deploy` after a successful login, by the GUI's Deployment window after a
deployment, and by `deploy devices add`. No tool writes, changes or removes one.

| Command | Meaning |
|---|---|
| `list` | Every record with its host key and whether the key is still the trusted one in the framework's `known_hosts` (`trusted`, `CHANGED in known_hosts`, `not in known_hosts`), paired, and the last deploy. |
| `add HOST --user USER` | Adds a board you trusted before this version (its host key is already in the framework's `known_hosts`). Needs a terminal: it shows the fingerprint and asks; it never trusts a new key and never asks for a password, and it marks the board as paired only when the framework's key logs in. |
| `remove DEVICE_ID` | Removes the record (written to the audit log); AI tools can no longer name the device. Its `known_hosts` entry is kept. |

A record is usable only while the framework's `known_hosts` holds exactly the key it names: after you removed a
changed key there (to trust a reinstalled board again), trust the board again with `deploy run` or `scan --device`.

---

## ditto

Ditto is the built-in assistant. Most `ditto` commands use the provider and model from the AI settings (GUI:
**Tools > Configure AI Agent (Ditto)...**, or `ai_provider` and `ai_model` in `config.yml`). Sign in with
[`llm-cli ditto login`](#ditto-login), or with **Sign in...** in the AI settings.

Ditto is optional and experimental. Before the first `ditto` command (or `module generate-ai`) at a terminal, a
notice goes to stderr once: "Use at your own risk and responsibility. The creator of LLMCF is not liable." (the
no-warranty terms of `LICENSE.txt`, nothing more). It is stored per user and shared with the GUI's notice; without a
terminal nothing is printed or stored. The command always runs ([details](Features.md#experimental)).

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
and a rejected key is not stored. A key with a space, a line break or an invisible character inside is refused
before anything is sent. The method is remembered as `ai_auth_<provider>`. The output shows at most the last four
characters of a key, also when a server's error text repeats it.

| Method type | What `ditto login` does |
|---|---|
| `api_key` (cloud providers, `custom` servers) | Asks for the key, checks it and stores it. The key of a server whose address you set belongs to that address only. Admin keys (`sk-ant-admin01-`) are refused before anything is sent. |
| `bearer_optional` (local engines whose key is optional) | Asks for a token ("Enter for none"); without one the server is used without a key. An empty stdin with `--with-token` is a usage error and never deletes a stored token; `ditto logout` does that. |
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
| `--insecure-storage` | Without a usable keyring: keep the vault key in a file next to the vault (**not encrypted at rest**). Ignored when the vault is protected another way that works; a credential helper or passphrase you chose (`secret_storage`) is never replaced by it (exit code 5). |
| `--no-check` | Store without the check (for example offline). |
| `-y, --yes` | Do not ask before sending a key over plain `http://` or before `--insecure-storage`. |

Exit codes: 0 signed in; 2 usage (an unknown provider or method, a missing `--method`, key or server address, a
malformed option value or key); 3 rejected (a wrong, revoked or admin key, a workspace problem); 4 the provider or
the local server cannot be reached; 5 the credential vault cannot be used (the message names the choices); 6 the
method is not available in this version or the provider was removed.

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
tools are the framework's own on this computer. The answer goes to stdout, tool notes and errors to stderr. Nothing is
saved to the chat history.

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
with each tier in turn and stops the servers it started. A server you started yourself keeps running if it already
serves the tier; otherwise the evaluation replaces it for that tier and says so at the end.

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
  not run counts as failed. The floor is `rag_rerank_min_score` (not set by default: the reranker's calibrated
  floor; 0: none).
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

The managed llama.cpp server for Ditto's offline mode. These commands work without Docker and without the keyring
(in Docker mode, `start` and `stop` need Docker; see `ditto runtime ensure`). Files:
`~/.llm-framework/runtime/llama.cpp/` (builds, state and `mode.json`) and `~/.llm-framework/logs/llama-server.log`.
The optional MCP tools `build_local_runtime`, `start_local_runtime`, `stop_local_runtime` and `run_benchmark` do the
same for an AI client once you enable them ([details](Features.md#local-runtime-tools-for-ai-clients-optional));
`install --prebuilt` and installing toolchain parts stay commands for you only.

#### ditto runtime status

```
llm-cli ditto runtime status [--json]
```

Shows the runtime mode (bare metal or Docker, with Ditto's image in Docker mode); the detected hardware; the
bare-metal build with its origin (`self-built`, `imported` or `prebuilt, not
self-built`), build ID, llama.cpp commit, backend, where and how long it was built, when it was verified and which
devices it found; why a stored build cannot be used; whether a rebuild is required (the pin changed) or recommended
(this machine's build plan changed); the GPU backend the build recipe chooses for this machine and why; which
official prebuilt archive `--prebuilt` would install and why (or why none fits, for example on musl or an older
glibc); whether the server runs (address, process, model, context); the recommended model and why; and the log
file. Checking the rebuild detects the toolchain (it compiles nothing).

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output (`installed`, `problems`, `rebuild`, `recommended_backend`, `prebuilt_option`, ...). |

#### ditto runtime install

```
llm-cli ditto runtime install [--backend auto|cpu|vulkan|cuda|rocm|metal] [--placement auto|native|container]
                              [--jobs N] [--prebuilt] [--force] [-y]
```

Builds `llama-server` (with `llama-cli`, `llama-bench` and `llama-perplexity`) on this machine from the pinned
llama.cpp commit (`configs/llama_runtime.yml`, `llama_cpp.source`). It probes the machine, detects the toolchain,
shows the builds it plans and asks. The CPU build is built first, verified and made active (it stays the fallback);
then the GPU build the recipe chooses is built and becomes active once it is verified. A GPU build that cannot be
built here yet or fails leaves the CPU build active and is reported. Every build is verified on this machine before
it is kept: `--version` gives the pinned build number and commit, `--list-devices` shows the planned device, and the
server refuses `--tools`. A build that is already stored with the same build ID is reused, not built again. A
running server keeps running; the next `runtime start` uses the new build.

- **Toolchain:** GCC or Clang, CMake 3.15 or newer and Ninja (Debian/Ubuntu: `build-essential cmake ninja-build`).
  Without them the build runs in the framework's builder container (Docker, `--network none`). Without either, the
  command names the ways out and exits with 1. Nothing is installed for you.
- **Source:** the pinned commit is fetched once (about 40 MB to download, 215 MB on disk) after you confirm, and
  checked by its commit; on a machine without network import it first (`ditto runtime import-source`). Configure and
  build run without network.
- **Backends in this version:** CPU and Vulkan on Linux. CUDA and ROCm builds, and Windows and macOS, follow; on an
  NVIDIA machine the CPU build runs until then (`--backend vulkan` builds a Vulkan server for the GPU).
- **Exit code 1** also when an explicitly requested GPU backend could not be built (the CPU build is active).

| Option | Meaning |
|---|---|
| `--backend [auto\|cpu\|vulkan\|cuda\|rocm\|metal]` | GPU backend built after the CPU build (default `auto`: the build recipe chooses; `cpu`: only the CPU build). With `--prebuilt`: the archive to install. |
| `--placement [auto\|native\|container]` | Where the build runs (default `auto`: natively when the toolchain is complete, else in the builder container). |
| `--jobs INTEGER RANGE` | Parallel compile jobs, 1 to 256. Default: from the CPUs and the free memory. |
| `--prebuilt` | Download upstream's official prebuilt archive instead (size and sha256 checked before unpacking). Only on this request, always shown as "prebuilt, not self-built". An archive that is already installed, also one an earlier version downloaded (which is not used without this request), is taken without downloading it again. Linux archives need glibc 2.35 or 2.39; none is offered on musl. |
| `--force` | With `--prebuilt`: download and unpack again even if it is installed. |
| `-y, --yes` | Do not ask before building, fetching the source or downloading. |

#### ditto runtime ensure

```
llm-cli ditto runtime ensure [--mode auto|bare-metal|docker] [--force] [--placement auto|native|container]
                             [--backend ...] [--jobs N] [--no-model] [-y]
```

Installs Ditto's local model server when it is not there, bare metal or in Docker. Running it again does nothing:
it says what it found and stops. The installers' Ditto step runs this command.

- **What it finds:** a bare-metal build that passed its checks on this computer (or the official archive you chose
  with `install --prebuilt`), and Ditto's image of the pinned llama.cpp commit, built here (never one from a
  registry) and checked in a container of itself. While Docker cannot be asked, Docker mode stays as you chose it.
  The command removes nothing on its way: no old build is removed by retention.
- **When nothing is installed:** `--mode auto` installs bare metal (no Docker overhead); when bare metal is not
  possible here, it uses Docker and says why. `--mode bare-metal` or `--mode docker` choose the mode yourself.
- **Docker mode:** Ditto's image is built on this computer from the same pinned recipe, on a Docker Official base
  image pinned by digest (`configs/llama_runtime.yml`, `build_bases`). It is never pulled as a finished image and
  never pushed. The programs come from the bare-metal build when the base image's C library fits ("repackaged"), or
  from a new build in the builder container ("rebuilt", no network during the build, checked in a container of the
  runtime base). Every image is checked in a container of itself: version and commit of the pin, the devices it
  sees, and that the server refuses `--tools`. The server container publishes its port on 127.0.0.1 only, mounts
  the models folder and the per-start key file read-only, runs as your user without capabilities and with a
  read-only root file system, and is removed by Docker when it stops. The GPU is passed through where it can be
  (NVIDIA through the NVIDIA Container Toolkit, AMD and Intel through `/dev/dri`); where not, the command says why
  and the image gets the CPU build. Docker mode needs Linux in this version.
- **`--force`** switches to `--mode` although Ditto is installed in the other mode. It needs a terminal (switching is
  your decision). The models stay and are not downloaded again; only the server is made again, repackaged where
  possible: the bare-metal build into the image, or the image's build into bare metal after it passed its checks on
  this computer. Where that is not possible, the command says why and builds again. The server of the old mode is
  stopped.
- **Space first:** bare metal and Docker are two alternatives. Before the new mode is built next to the old one, the
  command estimates what it needs while it is built and what it keeps, and checks that against the free space of
  each disk involved (Ditto's runtime folder; Docker's image storage when Docker names a folder of this computer; the
  temporary folder where the image's build context is made). The estimates are shown. When it does not fit, nothing
  is built. You see the sizes and choose: free space yourself and run the command again, or delete the old mode
  first. For that the command lists every object of the old mode and asks once ("No" is the default); Ditto is
  unavailable until the new mode works, and the models stay. Deleting the old mode first is offered only when the new
  mode then fits: the command estimates it again without the old mode's build (a bare-metal build that is deleted
  cannot be repackaged) and counts the space the old mode's objects free. It asks only at a terminal; without one
  nothing is deleted or built. When the new mode then fails, the command says so and offers to make the old mode
  again.
- **The old mode goes completely:** once the new mode works, every object of the old mode is listed: its build
  folders (full path, folder name, build ID), Ditto's images and containers (name and ID), its entries in `mode.json`
  and `active.json`, and the log of the image builds, each with its size and what it is. One question deletes exactly
  that list for good, not into the trash ("No" is the default, no choice per item). Shared things stay: the models,
  the pinned source, the framework's builder images and a board's runtime builds. Without a terminal nothing is
  removed and the list is shown.
- **The model:** when the model of the chosen or recommended tier is not downloaded yet, its source, size and licence
  are shown first. It is downloaded after your yes, or with `-y` when its licence is permissive (Apache 2.0, MIT,
  BSD); any other licence is yours to accept with `llm-cli ditto models download`. Both modes use the same models
  folder.
- In the framework's own container build (`LCF_CONTAINER_BUILD=1`) Docker mode is not available.

| Option | Meaning |
|---|---|
| `--mode [auto\|bare-metal\|docker]` | `auto` (default): keep what is installed; when nothing is, bare metal, or Docker when bare metal is not possible. |
| `--force` | Switch to `--mode` (bare-metal or docker) after the space check; once the new mode works, every object of the old mode is deleted for good after your one yes. Needs a terminal. |
| `--placement [auto\|native\|container]` | Where a build runs, as for `install`. With Docker mode, `native` puts a native build into the image. |
| `--backend [...]` | GPU backend, as for `install`. |
| `--jobs INTEGER RANGE` | Parallel compile jobs of a build. |
| `--no-model` | Do not offer the model download. |
| `-y, --yes` | Do not ask before fetching the source or downloading a model under a permissive licence (size and licence are still shown). The build itself starts without a question, as the installers need it. Never removes anything. |

```bash
llm-cli ditto runtime ensure                              # installs it if it is not there
llm-cli ditto runtime ensure --mode docker --force        # switch to Docker
llm-cli ditto runtime ensure --mode bare-metal --force    # and back
```

#### ditto runtime import

```
llm-cli ditto runtime import FILE [-y]
```

Imports a `llama-server` build made with `ditto runtime export` on another machine of the same kind (same OS and
architecture, and a C library at least as new). The archive is unpacked safely (size limited first), every file is
checked against its manifest, the build ID against its plan, the plan against the build rules (no download option,
no subprocess support) and the commit against the pin. A build made with `-march=native` is refused when this CPU is
of another vendor or lacks a CPU feature of the CPU that built it (AVX-512, for example): it would stop with SIGILL at
the first computation. Then it shows where the build was made and asks before any of its programs runs; after that
the build is verified here like a local one and becomes the active build (a CPU build also the fallback). Import only
builds you made yourself.

| Option | Meaning |
|---|---|
| `-y, --yes` | Do not ask before running the imported programs for the check. |

#### ditto runtime export

```
llm-cli ditto runtime export FILE
```

Writes the self-built build in use to `FILE` (`.tar.gz`: its programs, `build.json` and `manifest.json`, no logs)
for `ditto runtime import` on another machine of the same kind. The prebuilt archive is never exported.

#### ditto runtime prune

```
llm-cli ditto runtime prune
```

Lists the old builds of Ditto's server with a number each (unreleased): older self-built builds and installations of
the prebuilt archive of another llama.cpp version, each with its full path, size and what it is. Nothing is picked.
You type the numbers to delete, choose the trash (the default) or deleting for good, and confirm exactly those
(default no). It needs a terminal; without one it only lists them. The active build, the CPU fallback, the build a
running server was started from and the builds of your boards (`llm-cli device runtime build`, same build store) are
never listed, and a build that became one of them after the list was shown is kept. `runtime install` deletes
nothing: it says how many older builds it kept (before, it kept only the two newest).

#### ditto runtime start

```
llm-cli ditto runtime start [--model TIER] [--ctx N] [--sleep-idle SECONDS]
```

Starts `llama-server` on 127.0.0.1 with a free port and a new API key in a private file, with a cleared environment
and none of the options the shared launch policy forbids. Only a build that passed its verification on this machine,
still matches its manifest and is of the pinned commit is started (the active build, else its CPU fallback), or the
prebuilt archive after `install --prebuilt`; a self-built build that cannot be used never falls back to the prebuilt
archive. The server keeps running after the command returns, until `llm-cli ditto runtime stop`. If the same model
already runs from the same build, it is reused.

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

#### ditto runtime export-source

```
llm-cli ditto runtime export-source FILE [--mirror URL] [-y]
```

Writes the pinned llama.cpp source (the commit in `configs/llama_runtime.yml`, `llama_cpp.source`) as a git bundle
to `FILE`, plus `FILE.json` with the commit, the tag, the size and SHA256 of the bundle and the framework version.
Carry both files to a machine without internet access and import them there. If the source is not cached yet, it
is fetched first (about 40 MB to download, 215 MB on disk): by commit (by its tag only when the server refuses a
fetch by commit), and either way `git rev-parse HEAD` must give the pinned commit back. The verified tree is kept
read-only in `~/.llm-framework/runtime/llama.cpp/src/<commit>/`; later runs check that nothing in it was changed or
added (files git ignores included) and need no network. A relative `FILE` is relative to the current folder. Needs
`git`.

| Option | Meaning |
|---|---|
| `--mirror URL` | Fetch from this mirror instead of the pinned repository. The commit is checked the same way. |
| `-y, --yes` | Fetch without asking when the source is not cached yet. |

#### ditto runtime import-source

```
llm-cli ditto runtime import-source FILE
```

Imports the pinned llama.cpp source from a bundle of `export-source` into
`~/.llm-framework/runtime/llama.cpp/src/<commit>/`. Git checks every object by its hash, and the checked-out commit
must be the pinned one, whoever made the bundle and with whichever git version. A bundle of another commit, a
bundle that does not match its `FILE.json` (size or SHA256) and a damaged bundle are refused. Needs `git` on the
air-gapped machine too.

```bash
# Online machine
llm-cli ditto runtime export-source llama.cpp-source.bundle -y
# Air-gapped machine (copy llama.cpp-source.bundle and llama.cpp-source.bundle.json first)
llm-cli ditto runtime import-source llama.cpp-source.bundle
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

The framework tools are what Ditto and MCP clients use: 16 read-only tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_mojo_oracle`, `search_knowledge`, `list_knowledge_sources`, `read_knowledge_source`, `search_hf_models`,
`get_hf_model_info`, `list_local_files`, `get_status`, `get_job_status`, `list_devices`), 8 that change things on
this computer (`start_build`, `cancel_build`, `resolve_healing`, `cancel_job`, `propose_healing_fix`,
`download_hf_file`, `create_deploy_package`, `run_mojo_miracle`) and `deploy_to_device`, which reaches a device you
trusted (tier `external`: always asked). `run_doctor`, the 3.0.0 name of `run_mojo_oracle`, stays callable but is
not listed (unreleased). `gui` (unreleased) reads and fills the open framework window (tier `local_write`; its reading
actions `read_form`, `wizard_state` and `show_window` are decided like read tools, and your setting for `gui` covers
all its actions; see [Features](Features.md)).

### tools list

```
llm-cli tools list [--caller ditto|mcp]
```

Every tool with its tier (`read`, `local_write`), the current permission (`allow`, `ask`, `deny`) and a
description, followed by the tool's own default, the highest decision any setting can give it (`at most`), whether
it is new (not reviewed yet: it uses its own default until you review it) and whether a confirmation may offer a
standing approval. Without `--caller` it shows the shared permissions and names the callers that have their own.
The title says when the permissions are not set up yet.

### tools permissions

```
llm-cli tools permissions [--caller ditto|mcp | --token NAME] [--reset]
```

Asks the same three questions as [`ditto setup`](#ditto-setup) and saves the answers. Permissions for single tools
set in the GUI are kept. It first lists every tool with its own default, the highest decision any setting can give
it (`at most`), whether it is new since the last review and whether a confirmation may offer a standing approval;
saving counts every listed tool as reviewed. The GUI dialog shows the same facts.

It needs a person at a terminal, with and without `--reset` (as does `ditto setup`): piped answers are refused with
exit code 2. Every save is audited with actor `cli` and the old and new decisions before it takes effect, and is
refused (exit code 1, nothing changed) when the audit entry cannot be written or when the permissions were saved
elsewhere (the GUI, another terminal) while you answered. A permission file that is damaged, edited by hand or not
signed (one saved by 3.0.0, too) denies every tool until you run this again; the questions then start from "deny".

| Option | Meaning |
|---|---|
| `--caller ditto\|mcp` | Set the permissions of one caller (Ditto, or local MCP clients over stdio) instead of the shared ones. A caller's first permissions start from the shared ones, including the settings for single tools. |
| `--token TEXT` | Set the permissions of this [named MCP token](#mcp-token) instead (not together with `--caller`). Needs a terminal; a token linked to Ditto is asked first whether it should get its own. It lists the same tool facts for that token. Audited with actor `cli`. |
| `--reset` | Back to the recommended defaults (the setup asks again next time). With `--caller`: that caller uses the shared permissions again. With `--token`: back to read only. |

### tools audit

```
llm-cli tools audit [--limit N] [--details] [--verify] [--archive] [--summary]
```

Shows the most recent tool calls: time, who called (in words: Ditto, all local stdio clients, the MCP token
'<name>', or you in the CLI or GUI, with the client's name as it reported itself), the tool by its title (and the
name the AI wrote when that was a spelling variant), the decision, whether a person confirmed it, the outcome, the
reason code and how the person was asked with their answer (for example `elicitation: declined`).

| Option | Meaning |
|---|---|
| `--limit INTEGER` | Number of entries (default 30). |
| `--details` | Also show what the fool-proof layer repaired (kind and field), the duration, the SHA-256 and size of the result, links to earlier entries and the session hash. |
| `--verify` | Check the chain instead: changed or removed entries, and removal of the newest entries (anchor file). Exit code 0 if intact, otherwise 1. |
| `--summary` | Counts per caller instead: calls, refusals, and the reports that a task cannot be done (`report_cannot_do`) by how the framework checked them: confirmed, contradicted, premature, unchecked. |
| `--archive` | Move the log and its anchor aside (kept as `audit.db.<UTC time>` and `audit.db.<UTC time>.anchor` in the personal folder) and start a new log with the next tool call. Use it when `--verify` or `llm-cli mojo` reports a problem with the log. If the log cannot be moved (for example because it is busy), the command exits with code 1; close the GUI and AI clients and try again. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
llm-cli tools audit --summary
llm-cli tools audit --archive
```

### tools approvals

```
llm-cli tools approvals [list]
llm-cli tools approvals show ID
llm-cli tools approvals approve ID
llm-cli tools approvals decline ID
```

Calls of AI clients that could not ask you themselves (the client has no form elicitation, the question is longer
than 4,000 characters, or you set the tool with `tools permissions --confirm-in-framework TOOL`). `list` (also
without a subcommand) shows each waiting call with its id, tool, caller class, the client's self-reported name and
the time left; `show` prints the full question; `approve` and `decline` show it again and ask "Approve this call?"
or "Decline this call?" before anything is decided. On approval the framework runs the call for the client, under
the client's caller; the client reads the result with `get_job_status`. Every subcommand needs a terminal (exit code
2 otherwise); a decision is refused (exit code 1) when the approval has expired (15 minutes), was already decided,
its client's session has ended, its question changed meanwhile, or the audit log cannot record the decision. On a
headless machine, such as the Docker image, run it in a terminal there (`docker exec -it <container> llm-cli tools
approvals`). The GUI shows the same calls under **Tools > Pending Approvals...**.

`approve ID --always [--width this|any] [--duration revoke|30d|24h]` approves the call and saves a standing
approval ("do not ask again") within the scope the framework offered for it, after showing the scope and the
warning; `list` marks the calls where it is offered and the requests a client's dialog sent ("asks to stop
asking"), which are approved only with a scope. `--always` is refused while a GUI session of the framework runs:
use its approval window. For all local stdio clients together only `--width this` and `--duration 30d` or `24h`
exist.

| Option of `tools permissions` | Meaning |
|---|---|
| `--confirm-in-framework TOOL` | Always put this tool's questions to you in the framework (the approval window or this command), never in the AI client's own dialog. Repeatable; with `--caller` for one caller. Needs a terminal; audited. |
| `--confirm-anywhere TOOL` | Undo `--confirm-in-framework` for this tool. |
| `--grants` | List the standing approvals of every caller: what each covers, its state (active, expired, lapsed, outdated), when and how it was created, its uses and last use (from the audit log) and its expiry. |
| `--revoke-grant ID` | Revoke one standing approval at once (needs a terminal; audited). `--reset` clears them all, `--reset --caller` those of that caller; revoking a token removes its own. |

### tools folders

```
llm-cli tools folders list
llm-cli tools folders add PATH --label LABEL [--kind model|dataset|any]
llm-cli tools folders remove LABEL
```

The folders whose model and dataset files Ditto and MCP clients may use. They never name a path; they name a file
as `<label>:<relative path>`, for example `models:qwen2.5-0.5b-instruct` or `mymodels:llama/model.gguf`. The models
folder (`models`, setting `models_dir`) and the datasets folder (`datasets`, setting `datasets_dir`) are always
there; `add` lets them use another folder under a label you choose (lowercase letters, digits, `-` and `_`).

- `add` and `remove` need a terminal (piped answers are refused), and `add` shows the folder and asks first:
  files in it can be mounted into builds that run community or AI-written code, and can reach AI clients through
  build logs.
- `add` refuses `/`, your home folder itself, a folder that contains the framework's per-user folder, and a folder
  inside or containing a denied folder (keys, credentials, the Hugging Face token, system folders; the list is in
  [Features](Features.md#mcp-server)). The Hugging Face cache's `hub` subfolder may be added
  (`~/.cache/huggingface/hub`, where `hf download` stores models); the token files next to it stay denied. The same
  limits hold for `models_dir` and `datasets_dir`: if either setting points at `/`, your home folder or a folder
  holding the per-user folder, `list` marks the label as not usable.
- The folder's real path is recorded, and it is the one `add` showed you: if a link points elsewhere while you are
  asked, nothing is added. If it changes later, `list` marks it as not usable and AI clients are refused until you
  run `add` with the same label again.
- Each change is written to the audit log (actor `cli`; `gui` for the same list in the GUI under Tools > Folders
  the AI May Use...); an addition that cannot be recorded does not happen. The list is stored in
  `~/.llm-framework/ai_folders.json`, readable only by you.

```bash
llm-cli tools folders add ~/llm-models --label mymodels --kind model
llm-cli tools folders add ~/.cache/huggingface/hub --label hub
llm-cli tools folders list
llm-cli tools folders remove mymodels
```

### tools crawl-notice

```
llm-cli tools crawl-notice
```

Shows the one-time Deep Ingest notice (what the crawler reads, which opt-outs it honours, that the knowledge stays on
this computer) and asks whether you have read it. AI clients and Ditto can ask to crawl documentation sites
(`crawl_docs_site`) only after a person read it, here or in the GUI's Deep Ingest; no tool can acknowledge it. It
needs a terminal (piped answers are refused); the acknowledgement is stored with `via: cli` in
`~/.llm-framework/crawl_notice.json` and written to the audit log (actor `cli`). When the notice is already read, it
is shown and nothing changes; after its text changes it must be read again.

---

## mcp

### mcp serve

```
llm-cli mcp serve [--transport stdio|http] [--host HOST] [--port PORT] [--container]
```

Runs the MCP server. Normally the AI client starts it for you (see `mcp config`). stdout carries the protocol;
logs go to stderr. If the tool permissions are not set up yet, read-only tools work and everything else asks (when
the client cannot ask, the question waits in the framework: see [tools approvals](#tools-approvals)).

While the framework window (the GUI) is open, the tool `gui` reaches its forms through a private bridge that only
your account can use (unreleased): the client reads and fills them like Ditto, and each field it fills is marked with
its MCP token's name or with the name the client gives itself. When no framework window is open, `gui` answers with
the blocker `gui_not_open` ("Open the framework window."). Nothing needs to be set up for this.

| Option | Meaning |
|---|---|
| `--transport [stdio\|http]` | `stdio` (default): started by the client. `http`: local Streamable HTTP with a named token per client. |
| `--host TEXT` | HTTP only; loopback addresses only (default `127.0.0.1`; also `::1`, `localhost`). |
| `--port INTEGER` | HTTP only (default 8765). |
| `--container` | HTTP inside a container only (Docker, Podman): binds the container's interfaces instead of loopback; refused outside a container. Publish the port to the host's loopback only (`-p 127.0.0.1:8765:8765`); the named token, the `Host` and the `Origin` checks apply as without it. |

With `--transport http` the server listens on `http://<host>:<port>/mcp`, and every request needs
`Authorization: Bearer <token>` with a [named token](#mcp-token); the server does not start before one exists, and
no token is accepted (nor does the server start) while other users can read or change the token file or it belongs
to another user. Requests with an `Origin` header (web pages, CORS preflights) are refused,
the `Host` header must be `127.0.0.1`, `localhost` or `[::1]` with the server's port, and the server keeps at most
8 sessions, at most 4 of them per token (so one client cannot lock the others out; beyond that a new session is
refused with status 503), closes a session after 10 minutes without a request, and takes at most 1 MiB per request.
It ignores `X-Forwarded-For` and `X-Forwarded-Proto` (no local program can claim another address) and writes no
access log.
A request that is still open (a wait, an event stream) is closed as soon as its token is revoked or rotated, and so
are that token's sessions (a session belongs to the token value that opened it; after `rotate` the client opens a
new one). The old shared token file `mcp_http_token` is no longer accepted.

Over stdio, the environment variable `LCF_MCP_TOKEN` in the client's server entry makes the client the caller of
that named token (`mcp:<name>`, with the token's permissions) instead of the shared caller `mcp`. `llm-cli` takes it
out of its environment before any command runs, whatever the command (no command and no child process inherits it),
and hands it only to `mcp serve`, which exits with code 2 if the value is not a valid token; a running server stops
answering when its token is revoked or rotated.

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

### mcp token

```
llm-cli mcp token add NAME [--policy-from CALLER | --link ditto]
llm-cli mcp token list
llm-cli mcp token rotate NAME
llm-cli mcp token revoke NAME
```

Named tokens, one per client that connects over HTTP or over stdio with `LCF_MCP_TOKEN`. A token is shown once,
when it is created or rotated; the framework keeps only its SHA-256 in `~/.llm-framework/mcp_tokens.json` (mode
0600). Its name (1-32 lower-case letters, digits and hyphens) is the caller `mcp:<name>` in the audit log, and it has
its own permissions. `add`, `rotate` and `revoke` need a person at a terminal (`add` and `rotate` also print only to
a terminal) and are audited with actor `cli`, without the token. No tool can create, list, change or read tokens. The
GUI does the same in Tools > Tool Permissions (Ditto / MCP), section "MCP tokens" (audited with actor `gui`).

| Command / option | Meaning |
|---|---|
| `add NAME` | Creates a token that can only read, until you allow more with `tools permissions --token NAME`. |
| `--policy-from CALLER` | Starts with a copy of another caller's permissions: `mcp` (local stdio clients), `ditto`, or a token name; `mcp:NAME` always names a token, also one called `mcp` or `ditto`. Later changes of the source do not apply. |
| `--link ditto` | Uses Ditto's permissions by reference, for a client that should have exactly Ditto's permissions: a change to Ditto's permissions applies at once. |
| `list` | Names, audit actors, permissions, creation and rotation time; never a token or its hash. |
| `rotate NAME` | A new token for the same name and permissions; the old one stops working at once. |
| `revoke NAME` | Deletes the token and its permissions after a question; its client loses access at once. |

```bash
llm-cli mcp token add laptop                          # read only
llm-cli tools permissions --token laptop              # allow more
llm-cli mcp token add helper --link ditto             # exactly Ditto's permissions
claude mcp add --transport http llm-cross-compiler http://127.0.0.1:8765/mcp --header "Authorization: Bearer <token>"
```

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
| `hf_token` | Gated Hugging Face models in builds, and the Hugging Face tools of AI clients (`get_hf_model_info`, `download_hf_file`): read inside the framework only, never passed to an AI; without it they send no token at all (never one from `HF_TOKEN` or the Hugging Face home folder) |
| `qdrant_api_key` | A remote Qdrant server for the knowledge base (setting `qdrant_url`) |
| `community_signing_key`, `community_age_identity` | Your keys for community packs, created on first use by [`llm-cli zoo`](#zoo-keys) or the GUI's community export and import; do not set them by hand |

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
llm-cli module create [--name TEXT] [--arch aarch64|x86_64|armv7l|riscv64] [--sdk TEXT] [--probe FILE]
```

Creates a new target by asking questions: name, architecture and SDK (if not given as options), then base Docker
image (default `debian:bookworm-slim`), description, system packages (default `build-essential, cmake, git`) and
CPU flags, which the module keeps as advisory text and never applies. Every value is checked before files are written, the finished folder must pass the target
validators ([Module Developer Guide](Module_Developer_Guide.md#91-what-the-validators-check)), and an existing
target is never overwritten.

| Option | Meaning |
|---|---|
| `--name TEXT` | Name of the new target. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU architecture. |
| `--sdk TEXT` | Special SDK, e.g. `CUDA`, `RKNN` (default `None`). |
| `--probe FILE` | Hardware probe of the target (the profile [`scan --device`](#scan) writes). The `runtime:` block of the new `target.yml` is then the build recipe's plan for the board's llama.cpp runtime from it; a file that is not a probe profile is refused before anything is written. |

The target is written to the configured targets folder (setting `targets_dir`; a relative path is relative to the
installation, not the current folder). Its `target.yml` has a `capabilities:` block (support level `community`) with the module name as the only board and
GGUF as the only format; add your real boards and formats there. The command's next steps name
`modules/build.sh`, the dispatcher the build engine runs; the generated `config_module.sh` and the other
`*_module.sh` scripts are legacy files the build pipeline does not call
([Module Developer Guide](Module_Developer_Guide.md#10-legacy-files)).

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto proposes a target module from a hardware probe file (`target_hardware_config.txt`, see [scan](#scan)). The
whole proposal is printed as JSON; only after you confirm is the module created (checked again by the module
generator) in the configured targets folder. Ditto uses the provider's stored sign-in
([`llm-cli ditto login`](#ditto-login)); the command never asks for a key itself. Neither `module create` nor
`module generate-ai` attaches pages from the knowledge base; that is an option on the last page of the GUI wizard.
When the probe file is a probe profile, the module's `runtime:` block is planned from it; the proposal's CPU and
CMake flags are kept there as advisory text and never applied. Ditto is experimental: before the first run at a
terminal the notice on Ditto goes to stderr once ([ditto](#ditto)).

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
llm-cli scan [--output TEXT] [--json] [--script]
llm-cli scan --device HOST --user TEXT [--port N] [--password [VALUE] | --password-stdin]
             [--trust-fingerprint TEXT] [--label TEXT]
             [--output TEXT] [--json]
llm-cli scan --import FILE [--label TEXT]
```

Runs the hardware probe and keeps its result, the hardware profile (schema 2, described in the
[Module Developer Guide](Module_Developer_Guide.md#11-the-hardware-probe-file)). A build is planned for the machine
that runs the model, so the probe runs on that machine.

- **This computer** (default): the Python probe `scripts/hardware_probe.py` runs inside the CLI. The profile is
  written to the file given with `--output` and stored as this computer's profile
  (`~/.llm-framework/hardware/local.txt`, without volatile values such as free RAM). If the Python probe cannot run,
  the CLI says why and runs the fallback script instead; `--script` uses the script directly
  (`scripts/hardware_probe.sh` with `sh` on Linux, so hosts without bash such as Alpine or BusyBox work too;
  `scripts/hardware_probe.ps1` on Windows).
- **A board over SSH** (`--device`): the first connection shows the board's SSH host key fingerprint and asks you to
  confirm it, as `deploy run` does (`--trust-fingerprint` confirms it without asking); a password is used once to
  install the framework's SSH key and is not stored. The CLI then sends `hardware_probe.py` to `python3 -I` when the
  board has Python 3.8 or newer, otherwise `hardware_probe.sh` to `sh` (any POSIX shell, also BusyBox). The script
  travels over the SSH channel, so nothing is installed on the board and nothing is left there. The result is checked
  like any probe file from outside, written to `--output` and stored under the board's ID
  (`~/.llm-framework/hardware/devices/<id>.txt`; the ID comes from the board's host key) with your label and its
  address. The SSH user becomes the board's deploy user for [`device runtime deploy`](#device) when none is set
  (never root).
- **A board's probe file** (`--import FILE`): a `target_hardware_config.txt` you copied from a board (written there
  by `scripts/hardware_probe.sh` or `llm-cli scan`) is checked like any probe file from outside, stored under a new
  random ID with your label (default: the board model in the file) and the CLI prints that **probe id** and the
  target that matches it (by the SoC as a board of a target, then by the SoC's or NPU's vendor, within the same
  architecture). An older schema-1 file is converted; what the schema does not know, the host name among it, is
  dropped. AI clients and Ditto name a board's probe only by this id (`get_hardware_probe`, the `add_board`
  prompt); no tool reads a probe from a path.

On this computer the probe runs without a terminal: the fallback script gets its input from `/dev/null`,
`NON_INTERACTIVE=true` and its own working folder (`~/.llm-framework/hardware/work`, never the current folder), and a
probe that takes longer than 120 seconds is stopped. The same service runs the probe for the MCP tool
`probe_local_hardware`.

The probes never ask, install or ask for administrator rights. Exit code 1 if the probe fails or its file cannot be
read, 2 for wrong options.

| Option | Meaning |
|---|---|
| `--output TEXT` | File the profile is written to, relative to the current folder (default `target_hardware_config.txt`); its folder is created. With `--script` the CLI passes it to the script (`hardware_probe.sh --output FILE`, `hardware_probe.ps1 -OutputFile FILE`). |
| `--json` | Write the JSON view (the same keys and values as one object) instead of `KEY=VALUE` lines. |
| `--script` | Use the fallback script instead of the Python probe (this computer only). |
| `--device HOST` | Probe this board over SSH (host name or IP address) instead of this computer. |
| `--user TEXT` | SSH user on the board (needed with `--device`). |
| `--port N` | SSH port of the board (default 22). |
| `--password`, `--password-stdin` | Only for the first connection: used once to install the framework's SSH key, never stored. As for [deploy run](#deploy-run): without a value asked with hidden input (also when you give nothing on a terminal), or one line from a pipe; `--password VALUE` is deprecated and warns. |
| `--trust-fingerprint TEXT` | Trust a new board without asking if its host key has this SHA256 fingerprint. |
| `--label TEXT` | Your name for the board in the profile store (with `--device` or `--import`). |
| `--import FILE` | Store a board's probe file in the probe store and print its probe id (not together with `--device`). |

A machine without network access runs a probe by hand, and you import its file
([README](../README.md#probing-the-boards-hardware)).

In the GUI: Builder tab > **Scan / Import...**: **Scan Local Host** (the Python probe, stored as this computer's
profile, as `llm-cli scan` does), **Scan Local Host with the Fallback Script** (`--script`), **Scan Local Host and
Save the Profile As...** (`--output`; a `.json` file gets the JSON view of `--json`), **Remote Scan via SSH...**
(`--device` with **Port** and **Name** for `--port` and `--label`; the same remote probe, so boards with `/tmp`
mounted `noexec` work, and the board is added to the trusted devices as by the CLI) and **Import from File
(USB/Disk)...** (`--import`; asks for the board's name, `--label`). The host key and every probe run in the
background; the log on the right shows the result, its notes and where the profile was stored.

```bash
llm-cli scan                                        # this computer, stored as "local"
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W" --output zero3w.txt
llm-cli scan --import zero3w.txt --label "ZERO 3W"   # a probe file copied from a board
```

---

## device

The boards you probed with `scan --device`, and the self-built llama.cpp runtime on them
([Features](Features.md#runtime-on-the-board-self-built-llamacpp)). `DEVICE` is a board's ID (or its first 4 or
more characters), its label or its stored address, as `device list` shows them. The build needs Docker on your PC;
the board needs SSH, `tar`, `gzip` and `sha256sum` (BusyBox has them), and an unprivileged user.

```
llm-cli device list [--json]
llm-cli device settings DEVICE [--install user|systemd] [--user NAME] [--port N] [--model PATH] [--confirm-sudo]
                               [--kv-cache auto|f16|q8_0|q4_0]
llm-cli device runtime plan DEVICE [--json]
llm-cli device runtime build DEVICE [--jobs N] [--mirror URL] [--yes]
llm-cli device runtime deploy DEVICE [--user NAME] [--password [VALUE] | --password-stdin] [--bench-model PATH]
                              [--allow-untrusted-model]
llm-cli device runtime verify DEVICE [--user NAME] [--bench-model PATH] [--allow-untrusted-model]
llm-cli device runtime status DEVICE [--json]
llm-cli device runtime prune DEVICE [--on-device]
```

### device list

The stored boards: ID, label, address, system (OS, version, architecture, C library and version, init system),
deploy settings and the deployed runtime. `--json` for scripts.

### device settings

Shows or changes how the runtime is installed on the board. This is decided here, before a deployment; a deployment
never asks.

| Option | Meaning |
|---|---|
| `--install user` | The start script in your home on the board (the default). You start the server with it. |
| `--install systemd` | Also a systemd unit that starts the server at boot. Only for boards whose probe reports `INIT_SYSTEM=systemd`; it needs `--model` and your confirmation, right here, that deployments may run `sudo -n` on the board (asked at a terminal, or `--confirm-sudo`; written to the audit log). Choosing `user` again drops the confirmation. |
| `--user NAME` | The SSH user on the board; the server and every check run as this user. root is refused. |
| `--port N` | The loopback port of the systemd unit's server (1024-65535, default 8080). The start script listens on 8080 unless it gets `--port`; the start command a deployment prints includes this port. |
| `--model PATH` | The model file on the board that the systemd unit serves (relative paths are in the user's home). |
| `--confirm-sudo` | With `--install systemd`: confirm without a question. |
| `--kv-cache auto\|f16\|q8_0\|q4_0` | The KV cache of the board's server (`-ctk`/`-ctv`): `q8_0` needs about half the memory of `f16`, `q4_0` about a quarter with some loss of quality. `auto` (the default) chooses `q8_0` when little memory is left next to the model on the board (from its probe), otherwise `f16`. Choose `f16` to switch the quantized cache off. The systemd unit starts the server with it; for the start script, the deployment prints the start command with it. |

Exit code 1 when the board does not run systemd or the confirmation is missing (nothing is changed), 2 for a bad
value.

### device runtime plan

Shows the cross build planned from the board's own profile: architecture, C library and version, the builder base
and its GCC, `GGML_CPU_ARM_ARCH` or the x86 features, OpenMP and `libgomp.so.1`, the programs, the build ID, and
whether it is built. `--json` prints the whole plan. Exit code 1 when no plan can be made (for example a board whose
glibc is older than every builder base, or a profile without `LIBC_VERSION`: probe it again).

### device runtime build

Fetches the pinned llama.cpp source if it is not cached (it asks first; `--yes` does not ask, `--mirror` fetches
from a mirror, `ditto runtime import-source` brings it to a machine without internet), builds the plan in the
builder container without network (`--jobs` sets the parallel jobs; by default they follow the memory), checks the
format of the programs on your PC and packs the tarball into
`~/.llm-framework/runtime/llama.cpp/device/<ID>/llama-runtime-<arch>-<build ID>.tar.gz`. A build that is already
stored is only packed again. Exit code 1 when Docker does not answer, the build fails or its checks fail. Older
packages of the device are kept (unreleased; before, a build or a deployment removed them); the output says how many,
and `device runtime prune` lists them for you to delete.

### device runtime deploy

Installs the built runtime on the board as the deploy settings say, then checks it there. The board must be the one
the profile was made with (same host key; a new board at the old address is refused) and already trusted;
`--password` pairs a board that has no framework key yet (used once, never stored; without a value it is asked with
hidden input, `--password-stdin` reads it from a pipe, and `--password VALUE` is deprecated and warns). The SSH user
comes from the deploy settings or `--user`.

1. The board is inspected: user ID (root is refused), home folder, architecture (it must match the build), init
   system, and the tools the deployment needs. With the systemd setting, `sudo -n true` must work; otherwise nothing
   is changed. With `--bench-model PATH` the model on the board is recognised by its sha256: a catalog model runs at
   once, any other only after you agree (at a terminal, or `--allow-untrusted-model`), which is written to the audit
   log. This is asked here, before anything changes on the board.
2. The tarball goes over the SSH channel into a private staging folder in
   `~/.local/share/llm-framework/llama.cpp/`, is checked against its sha256 and every file against `SHA256SUMS`, made
   readable only for you and moved next to the active build.
3. The start script creates the key `~/.config/llm-framework/llama-server.key` on the board (umask 077).
4. The new build is checked in its own folder, as your user: `--version` against the pin, `--list-devices`, and
   `llama-server` must refuse `--tools`; with `--bench-model` also a short `llama-bench` run on that model.
5. Only a build that passes becomes `current`. A build that fails (for example a `SIGILL` because the CPU baseline
   does not fit the board) is removed again, the previous build stays active, and nothing else changes. After the
   switch every older build stays on the board (unreleased; before, all but the previous one were removed without
   a question). The output says how many stayed and their size; `device runtime prune DEVICE --on-device` lists
   them for you to remove.
6. With the systemd setting the unit `llm-llama-server.service` is written and enabled with `sudo -n` and started
   when the model is there.

The result is kept in `device/<ID>/deployed.json` (a failed deployment does not change it). Exit code 1 when a step
or a check fails.

On the board, the start script runs the server:

```bash
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh start ~/models/model.gguf -c 4096 -t 4
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh status
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh stop
curl -s http://127.0.0.1:8080/health   # needs no key; every other request needs "Authorization: Bearer <key>"
```

It listens on 127.0.0.1 (default port 8080, `--port` from 1024 up) with the key file and a cleared environment.
It passes on `-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`, `-fa`, `-cram`, `-lm`, `-a`,
`--sleep-idle-seconds` and `-ctk`/`-ctv` (only `f16`, `q8_0` or `q4_0`) with checked values and refuses every
other option (exit code 2). `serve` runs in the foreground, `keygen` creates the key, `version` prints the
server's version.

### device runtime verify

Runs the checks of step 4 again on the active build, `current` (also with `--bench-model`), and updates the record.

A short benchmark with `--bench-model` (here or with `device runtime deploy`) is the proof that a model works on
that board. Its result is recorded with every build of that model in the output folder: the file
`device_tests.json` in the build folder, found by the model's sha256 (also when the file on the board has another
name). It holds the board (your label, the board and SoC model from its probe), the runtime build, the measured
speeds, the KV cache the measurement used (as `llama-bench` reports it) next to the one the board's server is set
to, and whether the checks passed. The Deployment window lists the build as "tested on <board>".
A model that no build of this framework made is recorded nowhere.

### device runtime status

What is deployed (build, time, start script or systemd, result of the checks) and whether it is still the build the
board's profile, the pin and the recipe call for; if not, build and deploy again.

### device runtime prune

Lists the device's older runtime packages on your PC with a number each (each tarball and its `.sha256` file, with
full path and size). The package of the build deployed last is never listed. Nothing is picked: you type the numbers
to delete, choose the trash (the default) or deleting for good, and confirm exactly those (default no). It needs a
terminal; without one it only lists them. It changes nothing on the board.

With `--on-device` it logs in to the board (as the deploy settings' user, with the framework's key) and lists the
older runtime builds there instead: each with its full path on the board, folder name, size and what it is (the
build active before the last deployment says so). The active build and a build a running program uses are never
listed. You type the numbers, nothing is picked, and you confirm exactly those (default no). A board has no trash,
so they are removed for good. The removal is written to the audit log first, with the folder names only. No AI
can do this: the AI tools only say how many older builds stayed.

For a [trusted device](#deploy-devices), the optional MCP tools `probe_remote_device`, `deploy_device_runtime` and
`run_device_benchmark` run `scan --device`, `device runtime build` with `deploy`, and `device runtime verify
--bench-model` for an AI client, each confirmed by you; the deploy settings are always yours (`device settings`).

```bash
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W"
llm-cli device runtime plan "ZERO 3W"
llm-cli device runtime build "ZERO 3W" --yes
llm-cli device runtime deploy "ZERO 3W" --bench-model models/qwen3.5-2b-q4_0.gguf
# The systemd unit instead of the start script, decided before the next deployment
llm-cli device settings "ZERO 3W" --install systemd --model models/qwen3.5-2b-q4_0.gguf
```

---

## config

### config show

```
llm-cli config show
```

Prints the effective settings as JSON, as the framework reads them: every setting with its `value` and its
`source` (`default`, `file <path>` for `config.yml` or a file given with `-c`, `environment` for an
`LLM_FRAMEWORK_<KEY>` variable). Credentials are redacted: tokens, keys and passwords inside values (for example in
a URL), and every value under a name that looks like a credential, whether an unknown setting (`hf_token`) or a key
inside a setting (`password`, `api_key`). The command needs neither the credential vault nor Docker.

```bash
llm-cli -q config show | jq '.models_dir'
```

AI clients read settings with the MCP tool `get_settings` (an explicit list; addresses as scheme and host, folders
as labels) and change ordinary ones with `update_settings`, which asks you with the old and new value and saves
the change in `config.yml`. Settings that limit what an AI may do can only be tightened that way; permissions,
secrets, the vault, AI provider settings, addresses and folders are changed only by you, here or in the GUI.

### config output-folder

```
llm-cli config output-folder [FOLDER]
```

Unreleased. Without `FOLDER` it shows the output folder (setting `output_dir`, default `~/llmcf/output`) and the
framework's build folder (setting `build_dir`). With `FOLDER` it sets the output folder and saves `config.yml`, for
example to a folder on a big second disk; the folder is made when it does not exist. A system folder, the
framework's program folder, its build folder and its per-user folder are refused (exit code 2), and so is a folder
that holds one of them (such as your home folder), because everything below the output folder is listed. Builds in
the old folder stay where they are and stay listed. An AI cannot change this setting.

```bash
llm-cli config output-folder /mnt/models/llmcf
```

### config repos

```
llm-cli config repos [--add NAME URL] [--remove NAME]
```

Without options it lists the source repository overrides (setting `source_repositories`). `--add` and `--remove`
change them and save `config.yml`; if it cannot be saved, the command says so and exits with code 1. `--add`
stores an entry `{url: ...}`; the build engine passes every entry, and plain URLs stored by earlier versions, to
the stage containers as `<NAME>_REPO_OVERRIDE` (`NAME`: the whole name in upper case, every character other than
ASCII letters and digits as `_`, so `llama.cpp`, `llama_cpp` and `llama-cpp` all give `LLAMA_CPP_REPO_OVERRIDE`). A
dotted name is passed under the name earlier versions used as well, its part after the last dot (`sources.llama_cpp`
also gives `LLAMA_CPP_REPO_OVERRIDE`). `--add` refuses a name without any ASCII letter or digit (exit code 2).

| Option | Meaning |
|---|---|
| `--add TEXT...` | Add an override: `NAME URL`. |
| `--remove TEXT` | Remove the override `NAME`. |

The current Rockchip scripts read none of these variables (only the legacy `source_module.sh` reads
`LLAMA_CPP_REPO_OVERRIDE`), so the overrides have no effect on Rockchip builds.

---

## zoo

The community zoo: hardware targets, recipes and knowledge packs that other users share. A recipe is a "model
template": a model at a pinned commit, built for a target, board, format and quantization, with the hashes of what
came out. The framework never uploads anything by itself; sharing is always a step you start.

**Current state:** the community repository and its signed index are not published yet. The settings
`community_index_url` and `community_repo` are empty, and `configs/community_keys.yml` lists no publisher key. Until
then `zoo list` shows only installed packs, installing by ID and `zoo export --pr` stop with a message, and packs
are shared as files (`zoo install --file`).

AI clients and Ditto use the same zoo through the tools `list_packs`, `add_pack_from_index` (signed index only,
always asked), `start_recipe_build` (an installed recipe, exactly as pinned, with the question of `start_build`)
and `export_pack` (to a local file, always asked); they cannot install a pack from a file, open a pull
request or create your keys ([details](Features.md#packs-for-ai-clients)).

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
| `revoked` | In the index's list of revoked packs. It is not installed; installed copies are flagged by `zoo list`, `zoo verify`, `llm-cli mojo` and the Community Hub and are not built, also after the key that signed the revocation has expired. |

Targets that ship with the framework, such as Rockchip, have no provenance file and do not appear in the zoo.

Installed targets go into the targets folder, recipes into `community/recipes/<id>/` and knowledge packs into
`community/knowledge/<id>/` of the installation. A knowledge pack is imported into your knowledge base right after
it is installed or, if the knowledge base is off, as soon as it is turned on.

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
| `--from FILE` | Encrypted packs: accept them only from these senders. The file holds the senders' public keys as `llm-cli zoo keys` prints them; repeatable. Without it, any sender is accepted; the summary always names the sender's key ID. For a pack that is not encrypted the command stops with exit code 2 (a plain pack has no sender; its summary shows the author signature). |
| `--passphrase` | Encrypted packs: ask for the passphrase instead of opening the pack with your own key. |
| `--yes` | Do not ask for a pack whose trust comes from the signed index, also a file the index lists. A file without an index entry is `local` and is always confirmed; without a terminal it is not installed. |

Behaviour:

- Installing by ID needs the network: the index is downloaded again first, and if that fails nothing is installed.
  A revoked pack is refused.
- An encrypted pack (an age file and its sender's signature, see
  [Features](Features.md#encrypted-packs-for-private-sharing)) is checked against the signature before it is
  decrypted. It opens with your own age identity (see [zoo keys](#zoo-keys)) or, with `--passphrase`, with the
  passphrase; a passphrase whose scrypt work factor is above 2^20 is refused. A pack with a passphrase opened
  without `--passphrase` says so; your own identity is read (and created on first use) only for a pack encrypted
  to keys. The Community Hub opens packs encrypted to your key; packs with a passphrase need this command.
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

Without an argument it checks every installed community pack against the cached index, without downloading anything:
revoked, changed since it was installed, or licence files that do not match its pages. It prints one line per pack (✔,
or ✘ with the reason), or "No community packs installed.". Exit code 1 if a pack has a problem, otherwise 0.

With `PACK_FILE` it checks a pack file without installing it: format, file list and hashes, the checks of its kind
(for knowledge, and a target's knowledge snapshot, also its licence texts and `NOTICE`), an included author signature,
and that the privacy scrub would not change anything in it. It prints "acceptable" or every problem (exit code 1).
This form works without a keyring; the community repository's CI runs it on every submitted pack.

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
| `--to FILE` | Encrypt the pack for private sharing to the age recipients (`age1...`) in this file: what `llm-cli zoo keys` printed on the recipient's computer, or a public key from `age-keygen`/`rage-keygen`; repeatable. |
| `--passphrase` | Encrypt the pack for private sharing with a passphrase (asked twice, at least 12 characters). Not together with `--to` (exit code 2): age encrypts to keys or to one passphrase. |
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
- **knowledge:** your pages of the knowledge base (it must be enabled), privacy-scrubbed. Every page keeps its
  source address; pages without one are left out, and so are pages imported from installed community knowledge
  packs (a note says how many). A page goes in with its full text only where its licence allows passing the text on,
  with the attribution the licence requires; every other page goes in as its address and short facts (title, section
  names, technical terms). The summary lists every page with what the pack carries of it and why, and a note counts
  both ([knowledge sources](#knowledge-sources) shows the same before you export). Pages in full under a licence that
  asks for its text or permission notice (MIT, BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA) get the canonical text
  in `LICENSES/<SPDX id>.txt` and a `NOTICE` that lists every page with its source address, copyright line and
  licence; the summary names each licence text and why. A target's knowledge snapshot gets the same in
  `knowledge/`.

The file is called `contribution-<kind>-<id>-<version>.zip`, or `<kind>-<id>-<version>.lcfpack` when it is
encrypted. Your keys are created in the secrets store on first use (see [zoo keys](#zoo-keys)). The recipient of
an encrypted pack opens it with `llm-cli zoo install --file`. A passphrase pack, or one encrypted to a recipient
made with `age-keygen` or `rage-keygen`, also opens without the framework: unzip it, then
`age -d -i key.txt -o pack.zip pack.zip.age` (`key.txt` is that identity file; omit `-i key.txt` for a passphrase;
the `age` or `rage` tool does not check the sender's signature). The age identity of `llm-cli zoo keys` stays in
the secrets store, so packs encrypted to it need `llm-cli zoo install`.

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

Prints your public keys: an age recipient (`age1...`), to which others encrypt packs for you with
`llm-cli zoo export --to` (its private part stays in the secrets store, so a file encrypted to it with the age tools
cannot be opened), and an `ed25519:` line, which checks your signatures (others use it with
`--from`). They are not secret; save the output to a file and give it to the people you exchange packs with. The
keys are created on first use and kept only in the encrypted secrets store (names `community_signing_key` and
`community_age_identity`). A stored key that cannot be read is never replaced automatically; for a damaged one the
error message names the command that stores a new key.

```bash
llm-cli zoo keys > my.keys
```

```
# LCF public keys, key id 0123456789abcdef. Not secret: give them to people who send you
# encrypted packs (llm-cli zoo export --to) or check your signed packs.
age1<58 characters>
ed25519:<base64>
```

---

## trash

```
llm-cli trash [list]
llm-cli trash restore ID...
llm-cli trash empty ID... | --all | --due
```

Unreleased. What deletions moved to the trash: built models (in the output folder's `.trash`) and knowledge snapshots
(in the personal folder's `.trash`). `list` (the default) shows each entry with its id, what it is, its size, when it
was moved there, who asked for it (`gui`, `cli`, or an AI app such as `mcp:laptop`), its reminder date and where it
came from. Nothing leaves the trash by itself: after `trash_retention_days` (default 14; an AI cannot change it) it
only reminds you.

| Command | Meaning |
|---|---|
| `restore ID...` | Puts entries back where they were; when that place is taken, next to it as `<name>-restored-<time>`. Never through a link. |
| `empty ID...` | Lists exactly these entries and deletes them for good after you confirm at a terminal (default no). |
| `empty --all` / `--due` | Lists every entry, or those past their reminder date, with a number; nothing is picked. You type the numbers, then confirm exactly those. |

No tool and no AI app empties the trash. The GUI shows the same under **File > Trash...**.

```bash
llm-cli trash
llm-cli trash restore tr_0123456789abcdef
llm-cli trash empty --due
```

---

## Known limitations

- `build start --optimization` has no effect on Rockchip builds (the scripts have no matching setting);
  `--parallel` is deprecated and has no effect.
- `config repos` overrides reach builds as `<NAME>_REPO_OVERRIDE`, but the Rockchip scripts do not read them.
- `deploy package --docker` is experimental.
- There is no CLI command yet to choose Ditto's provider permanently; use the GUI, `config.yml` or
  `LLM_FRAMEWORK_AI_PROVIDER` (`ditto login` signs in but does not switch the provider). Browser and cloud
  sign-ins (OAuth, AWS, Google Cloud, Azure) come later ([roadmap](BLUEPRINT.md#roadmap)).
- The community repository and its signed index are not published yet (`community_index_url` and
  `community_repo` are empty, `configs/community_keys.yml` lists no key): `zoo list` shows only installed packs,
  and `zoo install <id>` and `zoo export --pr` stop with a message. Packs can be shared as files.
- `build start` has no option for a model commit: the pinned commit of a recipe is used when the Builder is filled
  from the Community Hub, or when an AI client passes `revision` to the MCP tool `start_build`.
- There is no CLI command for Deep Ingest (adding documentation pages to the knowledge base); it runs in the GUI's
  module wizard (**Tools > Create Hardware Module...**).
