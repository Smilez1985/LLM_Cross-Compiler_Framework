Version 3.0.0 plus the [unreleased changes](CHANGELOG.md#unreleased) · Last updated 2026-10-11

**English** · [Deutsch](README_DE.md)

# LLM Cross-Compiler Framework

The framework prepares language models (LLMs) so they run on small edge devices, starting with Rockchip boards
such as the Radxa ZERO 3W or the Radxa ROCK 5B. It has a desktop app (GUI), a command line (`llm-cli`), a server
that lets AI clients such as Claude Code, Claude Desktop or VS Code operate it (MCP), and a built-in assistant
called **Ditto**.

This README describes the state of the development branch: release 3.0.0 (2026-10-10) plus the work listed under
"Unreleased" in the [CHANGELOG](CHANGELOG.md). What comes next is in the [roadmap](docs/BLUEPRINT.md#roadmap).

---

## What does the framework do?

Language models are usually published for large PCs: several gigabytes in size and in formats a small board
cannot run efficiently. The framework takes such a model and prepares it for one specific board:

1. **Fetch:** a model from [Hugging Face](https://huggingface.co/) (for example
   `HuggingFaceTB/SmolLM2-135M-Instruct`) or a folder or file on your computer.
2. **Convert:** into a format the board understands: **GGUF** for the processor (CPU, via llama.cpp), **RKLLM**
   for the AI accelerator (NPU) of Rockchip chips, **RKNN** for vision and audio models on the NPU.
3. **Quantize:** store the model's numbers with fewer bits (for example 4 instead of 16). The model becomes much
   smaller and faster and loses a little quality.
4. **Deploy:** copy the result to the board over SSH and check it with checksums.

The conversion tools run in a Docker container on your PC, so you do not install any vendor SDKs yourself. The
container has no network access while it converts. Every build writes a manifest (`build_info.json`) with the
model files and their SHA256 hashes, plus a `Model_Card.md`.

For GGUF models the framework can also build llama.cpp for the board and install it there; the RKLLM and RKNN
runtimes stay yours to install (see [The board](#the-board)).

## Supported targets and formats

| Target | Status | Boards | Formats |
|---|---|---|---|
| Rockchip | official | RK3566, RK3568, RK3576, RK3588 | GGUF, RKLLM, RKNN |
| NVIDIA GPUs, AMD (Ryzen Z1 Extreme) | planned ([roadmap](docs/BLUEPRINT.md#roadmap)) | – | – |
| Generic ARM64 CPU (Raspberry Pi 4 and 5) | planned ([roadmap](docs/BLUEPRINT.md#roadmap)) | – | GGUF |

You can add other hardware yourself: [docs/Module_Developer_Guide.md](docs/Module_Developer_Guide.md).

The values below come from the `capabilities:` block in [`targets/Rockchip/target.yml`](targets/Rockchip/target.yml).
The GUI, the CLI, Ditto and the MCP server offer exactly these options and reject anything else.

| Board (`--board`) | CPU | NPU | RAM (max.) | Example boards |
|---|---|---|---|---|
| `rk3566` | 4× Cortex-A55 | 1 TOPS | 8 GB | Radxa ZERO 3W, Radxa CM3, ROCK 3C |
| `rk3568` | 4× Cortex-A55 | 1 TOPS | 8 GB | ROCK 3A, Radxa E25 |
| `rk3576` | 4× Cortex-A72 + 4× Cortex-A53 | 6 TOPS | 16 GB | Radxa ROCK 4D |
| `rk3588` | 4× Cortex-A76 + 4× Cortex-A55 | 6 TOPS | 32 GB | Radxa ROCK 5B, Radxa ROCK 5ITX, Orange Pi 5 |

| Format (`--format`) | Runs on | Input | Boards | Quantizations | Default |
|---|---|---|---|---|---|
| `gguf` | CPU (llama.cpp v0.6.0) | Hugging Face model | all four | 33 llama.cpp types, e.g. `Q4_K_M`, `Q4_0`, `IQ4_NL`, `IQ4_XS`, `Q5_K_M`, `Q6_K`, `Q8_0`, `F16` | `Q4_K_M`; `Q4_0` on rk3566/rk3568 |
| `rkllm` | NPU, language models (RKLLM-Toolkit 1.3.1) | Hugging Face model | rk3588, rk3576 | rk3588: `w8a8`, `w8a8_g128`, `w8a8_g256`, `w8a8_g512` · rk3576: `w4a16`, `w4a16_g32`, `w4a16_g64`, `w4a16_g128`, `w8a8` | `w8a8` (rk3588), `w4a16` (rk3576) |
| `rknn` | NPU, ONNX vision/audio models (RKNN-Toolkit2 2.3.2) | local `.onnx` file | all four | `fp16`, `i8` | `fp16` |

<details>
<summary>All GGUF quantization types</summary>

`Q4_K_M`, `Q4_0`, `IQ4_NL`, `IQ4_XS`, `Q5_K_M`, `Q6_K`, `Q8_0`, `Q4_1`, `Q5_0`, `Q5_1`, `Q2_K`, `Q2_K_S`,
`Q3_K_S`, `Q3_K_M`, `Q3_K_L`, `Q4_K_S`, `Q5_K_S`, `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`, `IQ2_M`,
`IQ3_XXS`, `IQ3_XS`, `IQ3_S`, `IQ3_M`, `TQ1_0`, `TQ2_0`, `MXFP4_MOE`, `F16`, `BF16`, `F32`
</details>

- **RK3566 and RK3568** are not supported by RKLLM; use GGUF there. `Q4_0` and `IQ4_NL` are repacked for fast
  ARM kernels.
- **Importance matrix (IMatrix):** GGUF only. It improves low-bit quantization. `IQ1_S`, `IQ1_M`, `IQ2_XXS`,
  `IQ2_XS` and `IQ2_S` require it. Without your own text file the framework uses its shipped calibration text
  ([data/calibration](data/calibration/README.md)).
- **Calibration data:** optional for RKLLM and RKNN. RKNN `i8` needs a list of sample inputs (paths relative to
  the list file).
- Common spellings are accepted and translated, for example `INT8` → `Q8_0` for GGUF or `int4` → `w4a16` for
  RKLLM. Board names are not case-sensitive.

---

## Requirements

**Your PC (the "host")**

- An **x86-64 computer** (Intel or AMD). The Rockchip build image installs Rockchip's toolkits as x86-64 Linux
  packages.
- **Linux** (the installer uses `apt`, so Debian, Ubuntu and similar) or **Windows** with Docker Desktop.
- **Python 3.11 to 3.14.**
- **Docker:** on Linux, Docker Engine (the installer sets it up if it is missing); on Windows, **Docker Desktop**
  with the **WSL 2 backend**, installed and running before you install the framework.
- **Disk space:** at least 20 GB free for the build image, downloaded models and results (`llm-cli mojo` warns
  below 20 GB and reports an error below 5 GB). Large models need 2 to 3 times their size during conversion.
- **Internet access** for the installation, the first image build and model downloads.

**The board (only for deployment)**

- Linux with an SSH server, a user account you can log in to, and the tools `unzip` and `sha256sum`.

---

## Installation

### Linux

Open a terminal in the downloaded repository folder and run:

```bash
sudo ./install.sh
```

The installer:

1. checks the internet connection (HTTPS to the package index `pypi.org`, or `PIP_INDEX_URL` if you set a
   mirror; behind a proxy start it with `sudo -E` so `https_proxy` is kept) and Python (installs it with `apt` if
   it is missing, then requires 3.11 to 3.14),
2. installs Docker via `get.docker.com` if it is missing and adds your user to the `docker` group. It says what
   that means: members of the group control the Docker service, which runs as root, so every program that runs
   as your user can get root rights on this computer (`sudo gpasswd -d USER docker` undoes it; the framework
   then cannot build without sudo),
3. copies the files the repository ships to `/opt/llm-conversion-framework` (owned by your user) and lists them
   in `.lcf-installed-files`. Running it again updates those files and removes only files that an earlier run
   copied and the repository no longer ships; everything else there stays: your own targets (module wizard,
   `llm-cli module create`), installed community packs, `configs/config.yml`, builds, cache and models. Start
   updates in the repository folder: the copy of `install.sh` in `/opt` copies and removes nothing,
4. creates a virtual environment (`.venv`) there and installs the pinned packages from `requirements.txt`,
5. creates the launcher `/usr/local/bin/llm-framework`, which starts the GUI,
6. sets up Ditto's local model server (`llm-cli ditto runtime ensure`, run as you, never as root): bare metal by
   default, in Docker when you choose it or when bare metal is not possible here. If Ditto is there already, it does
   nothing. If it fails, the installer says why and finishes anyway. Before a model is downloaded you see its size
   and licence. See [Offline mode](#offline-mode-the-managed-local-model).

Choose the Ditto step with `sudo ./install.sh --ditto=auto|bare-metal|docker|skip` (default `auto`). For an unattended
run set `LCF_DITTO` instead (for example `sudo LCF_DITTO=auto ./install.sh`): then nothing is asked, and a model under
a permissive licence (such as Apache 2.0) is downloaded after its size and licence are shown.

Log out and back in once so the `docker` group takes effect. Then start the GUI with `llm-framework`.

### Windows

1. Install and start Docker Desktop (WSL 2 backend).
2. Right-click `install.bat` and choose **Run as administrator**.

The installer checks for Python (installs Python 3.13 with `winget` if none is found, then requires 3.11 to
3.14), copies the framework to `C:\Program Files\LLM-Conversion-Framework`, creates `.venv` there, installs
`requirements.txt`, and puts a **LLM-Conversion-Framework** shortcut on the desktop. The shortcut runs
`start_framework.bat`, which starts the GUI. The Ditto step is the same as on Linux (`install.bat --ditto=...` or
`LCF_DITTO`); it runs only for the person who is signed in. In this version Windows has no self-built server and
no Docker mode for Ditto yet, so the step says so and the installation goes on.

Conversions on a Windows host have not been verified yet (see [Known gaps](#known-gaps)).

### In a container (server, NAS, Proxmox)

No framework image is published or pulled: you build the image on your own computer, and it runs the same
installation inside the container as above (`install.sh`), from exactly the files the repository ships. It is
headless (CLI and MCP server); the GUI is an explicit option.

```bash
make build      # scripts/build_container_image.sh: checks Docker, builds lcf-framework:local here, prints how to run it
make up         # starts the container lcf-framework; your data lives in the volume lcf-data
docker exec -it lcf-framework llm-cli mojo
```

AI clients reach its MCP server over stdio (`docker exec -i lcf-framework llm-cli mcp serve`, from another computer
over SSH to the host). The container builds no models itself (it has no Docker inside). Setup, the credential vault,
data, the GUI and updates: [docs/Container_Setup.md](docs/Container_Setup.md).

### Calling `llm-cli`

The installers do not put `llm-cli` on your PATH. Use the framework's Python from the installation folder:

```bash
# Linux
cd /opt/llm-conversion-framework
.venv/bin/python -m orchestrator.cli --help
```

```bat
:: Windows (Command Prompt)
cd /d "C:\Program Files\LLM-Conversion-Framework"
.venv\Scripts\python.exe -m orchestrator.cli --help
```

On Linux you can make a shortcut that works from any folder:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -m orchestrator.cli'
```

In a development setup (see [CONTRIBUTING.md](CONTRIBUTING.md)) the command `llm-cli` exists after
`uv sync`. The examples in this README use `llm-cli`.

### Where your files are

| What | Where |
|---|---|
| Program and Python environment | Linux: `/opt/llm-conversion-framework` · Windows: `C:\Program Files\LLM-Conversion-Framework` |
| Finished models | The output folder `~/llmcf/output` (Windows: `%USERPROFILE%\llmcf\output`), set once with **File > Output Folder...** or `llm-cli config output-folder`; in the GUI a save dialog opens there for every finished build (unreleased) |
| Builds not saved yet, cache, downloaded models | `output/` (the framework's build folder; earlier builds stay there and stay listed), `cache/` and `models/` in the installation folder if your user may write there (Linux), otherwise in the personal folder below (Windows: `%USERPROFILE%\.llm-framework\output` and so on); created when they are first needed. The settings `build_dir`, `cache_dir` and `models_dir` set other folders |
| Settings (`config.yml`) | `configs/` in the installation folder if your user may write there, otherwise the personal folder below; `LLM_FRAMEWORK_CONFIG_DIR` sets another folder |
| Personal state: tool permissions, audit log, SSH key and known devices, MCP token, chat history, input history, knowledge base, local model server, community index cache | `~/.llm-framework` (Windows: `%USERPROFILE%\.llm-framework`); `LLM_FRAMEWORK_HOME` sets another folder |
| API keys and tokens | encrypted in `~/.llm-framework/secrets.store`; the key for it is kept in your operating system's keyring, or as you choose with the setting `secret_storage` |
| Models for Ditto's offline mode | `ditto/` in the models folder above (`LLM_FRAMEWORK_MODELS_DIR` sets another folder) |

The installers create no data folders of their own.

---

## Quick start: the GUI

1. **Start** the GUI: `llm-framework` on Linux, the desktop shortcut on Windows.
2. **Language:** on the first start a dialog asks for English or German. The choice is saved as `language`.
3. **Tool permissions ("Ditto setup"):** decide once what Ditto and connected AI clients may do on their own:

   | Category | Recommended | Choices |
   |---|---|---|
   | Read information (targets, builds, logs, knowledge base) | Allow | Allow, Ask, Deny |
   | Change things on this computer (start or cancel builds) | Ask | Allow, Ask, Deny |
   | Reach other devices or delete data | Ask | Ask, Deny (never "Allow" for the whole category) |

   Below that you can set single tools differently. Change it later under **Tools > Tool Permissions (Ditto /
   MCP)...** or with `llm-cli tools permissions`.
4. **Health check (recommended):** `llm-cli mojo` checks Python, Docker, disk space, targets, security
   settings, tool permissions and the calibration data, and explains how to fix problems.

**Builder tab:** this is where you convert a model.

1. **Source Model:** a Hugging Face ID, or a local folder or file via the folder button.
2. **Target Hardware:** the target (profile). **Scan / Import...** reads a board's hardware: import a probe
   file, scan a board over SSH, or scan this PC.
3. **AI Optimization (Ditto):** optional. Tick **Enable Ditto AI Optimization**, then **Ask Ditto for Config**
   suggests format, quantization and optimization from the options the target offers. This needs a configured AI
   provider.
4. **Build Configuration:** format, board, quantization and optimization. Only combinations the target supports
   are offered. **Calibration:** importance matrix (GGUF) and a calibration file. **Use Host GPU for Build
   Process** passes a GPU into the container; Rockchip conversions do not need it.
5. **Start Build Pipeline:** the log appears on the right. The first build takes much longer because the Docker
   image with all toolkits is built first; later builds reuse Docker's build cache. **Cancel Build** stops the
   running build after a confirmation; nothing is written to the output folder then.

If a build fails because of a known problem, a **self-healing** dialog shows the cause and a proposed fix.
Nothing changes until you agree. The framework only applies a few fixed steps itself (fewer parallel compile
jobs after an out-of-memory error, removing its own images and the build cache when the disk is full, one retry
after a network error). Suggestions from an AI are only shown as advice.

**AI chat panel on the right:** talk to Ditto (see [Ditto](#ditto-the-built-in-assistant)); questions that wait for
you and what AI apps did for you arrive there too. F9 or **View > AI Chat Panel** shows or hides it. **Deployment
tab:** copy a finished build to a board. The **Auto-Tuning** and **Models** tabs are placeholders.

Menus: **Security > Set OpenAI API Key**, **Tools > Configure AI Agent (Ditto)...**, **Tools > Create Hardware
Module...** (wizard for a new target, including "Deep Ingest" to add documentation to the knowledge base),
**Tools > Hugging Face Model Browser...** (search models, see whether they are gated and their licence, download
single files such as a GGUF into the models folder; a gated model asks for your Hugging Face token),
**Tools > Open Community Hub** (see [Community zoo](#community-zoo)), **Tools > Tool Permissions (Ditto / MCP)...**,
**Tools > Spoken Announcements** (on or off) and **Tools > Clear Input History...**

The input fields offer what you used recently (models, targets, boards, datasets, URLs, devices), per user and never
secrets: [details](docs/Features.md#input-history).

### Deploying to the board

1. In the **Deployment** tab, pick a build. Builds are listed from their manifests.
2. Choose the profile (the target, e.g. `Rockchip`), enter the board's IP address and user, and click **Deploy to
   Target**.
3. **First connection:** the framework shows the fingerprint of the board's SSH host key. Compare it with the
   output of `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the board and confirm. Then enter the password
   once: it installs the framework's own SSH key on the board and is not stored. Later deployments need no
   password.
4. The framework packs the model, manifest, model card, checksums and a `deploy.sh` into a ZIP file in
   `output/`, uploads it to a private temporary folder on the board and runs `deploy.sh` there. The script checks
   the checksums and copies the model to `~/models` (another folder can be set with `MODEL_DIR`), then removes
   the temporary folder.

Leave the Docker option off for a plain model file. With it, the package also contains container images; that
mode is experimental and not verified in this version, and it takes an image only when it is pinned by its digest
(`name@sha256:...`) or was built on your computer.

After a successful deployment the board is one of your **trusted devices** (`llm-cli deploy devices list`). An AI
client connected over MCP, or Ditto, can then package a build and deploy it to that board (`list_devices`,
`create_deploy_package`, `deploy_to_device`), each time only after you confirmed a question that shows the board,
its host key fingerprint and the package's hash. No AI can trust a new board or accept a changed host key.

### The board

To run the model on the board you need a runtime there: llama.cpp (`llama-server`) for GGUF, Rockchip's RKLLM
runtime for `.rkllm` files (RK3588/RK3576), and the RKNN runtime for `.rknn` files. The framework builds and installs
llama.cpp for the board itself (below); the RKLLM and RKNN runtimes you install yourself. For GGUF, `deploy.sh` prints
how to start the model with the framework's start script when the runtime is installed, otherwise how to deploy it.

**Deploying the llama.cpp runtime to a board** (command line; Docker on your PC for the build):

```bash
llm-cli scan --device 192.168.1.50 --user dietpi   # probe the board (once, and again after an OS upgrade)
llm-cli device settings 192.168.1.50                # how the runtime will start (default: the start script)
llm-cli device runtime build 192.168.1.50           # cross-build for this board in the builder container
llm-cli device runtime deploy 192.168.1.50          # install it over SSH and check it on the board
```

- **Built for the board:** the plan comes from the board's own probe: its architecture and CPU features (for the
  RK3566 `armv8.2-a+dotprod+fp16`) and its C library and version. The builder container uses a base whose glibc
  (or musl on Alpine) is not newer than the board's, links the C++ runtime statically and has no network while it
  builds. `llm-cli device runtime plan` shows the plan first.
- **Installed in your home:** under `~/.local/share/llm-framework/llama.cpp/` on the board, never as root and never
  with files in `/tmp`. The previous build stays next to the new one.
- **Started on loopback with a key:** `~/.local/share/llm-framework/llama.cpp/current/llama-server.sh start
  ~/models/model.gguf` runs `llama-server` on 127.0.0.1:8080 with the key in
  `~/.config/llm-framework/llama-server.key` (made on the board, readable only by you) and a cleared environment;
  `stop`, `status` and `serve` (foreground) work the same way. Options such as `-c 4096` or `-t 4` are passed on;
  options that would give the model tools, open the web UI or leave loopback are refused.
- **Start script or systemd unit, decided beforehand:** `llm-cli device settings DEVICE --install systemd --model
  models/model.gguf` chooses a systemd unit that starts the server at boot. It needs systemd on the board and your
  confirmation, given right there, that deployments may run `sudo -n` on it; a deployment itself never asks.
  Boards without systemd (OpenRC on Alpine, SysV init) use the start script.
- **Checked on the board before it is used:** the new build's programs run as your user: the version must be the
  pinned one, the devices are listed, and the server must refuse `--tools`. `--bench-model models/model.gguf` adds a
  short `llama-bench` run; a model that is not one of the framework's catalog models runs only after your consent,
  which is written to the audit log. Only a build that passes becomes the active one (and the systemd unit's); a
  build that fails is removed again and the previous build stays active.

Not yet tested on a real board; the GUI for it follows. AI clients reach the same steps through the optional MCP tools `probe_remote_device`, `deploy_device_runtime` and `run_device_benchmark`, which stay hidden until you enable them and are confirmed by you on every call.

### Probing the board's hardware

A build is planned for the hardware it will run on, so the hardware probe runs on that machine:

```bash
llm-cli scan --device 192.168.1.50 --user rock   # a board over SSH (Python probe with Python 3.8+, else shell probe)
llm-cli scan                                      # this computer
```

Each scan writes `target_hardware_config.txt` and keeps the profile in `~/.llm-framework/hardware/`. The profile
holds the operating system (ID, version, family such as Debian, variant such as DietPi, kernel, init system), the
architecture, the C library and its version (glibc or musl), the CPU with its features and core clusters, memory,
SoC, NPU and GPUs; no host name, serial number or MAC address. Details: [CLI_Usage.md](docs/CLI_Usage.md#scan).

**Machines without network access (air-gapped):** copy one probe from `scripts/` to the machine, for example on a
USB stick, run it there and carry `target_hardware_config.txt` back:

| On the machine | Command |
|---|---|
| Python 3.8 or newer (Linux, Windows, macOS) | `python3 -I hardware_probe.py` |
| Linux without Python (any POSIX shell, also BusyBox) | `sh hardware_probe.sh` |
| Windows without Python | `powershell -ExecutionPolicy Bypass -File hardware_probe.ps1` |

The probes need no network, ask nothing and install nothing. The interactive features of the old scripts are kept
as options: `hardware_probe.sh --install-tools` first offers to install `vulkan-tools` with the package manager, and
`hardware_probe.ps1 -Elevate` restarts with administrator rights. Bring the file into the framework with **Scan /
Import... > Import from File (USB/Disk)...** in the GUI, or pass it to `llm-cli module generate-ai`.

---

## Quick start: the command line

```bash
llm-cli --version
llm-cli mojo                   # health check (Mojo Oracle); exit code 0 = OK, 1 = warnings, 2 = errors; at a terminal it offers Mojo Miracle
llm-cli mojo --miracle         # repair what is harmless (makes the personal folder private), then say what was repaired, what not and why
llm-cli targets list           # available targets with boards and formats; the --target value is the id (Rockchip)
```

**Converting a model** (`--target Rockchip`; format, board and quantization as in the tables above):

```bash
# GGUF for the RK3566 CPU (Radxa ZERO 3W), default quantization Q4_0, follow the progress
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip --format gguf --board rk3566 --follow

# RKLLM for the RK3588 NPU
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3588 -q w8a8 -F

# GGUF with an importance matrix from your own calibration text
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3588 -q IQ4_XS --imatrix --dataset ./calibration.txt -F

# RKNN from a local ONNX file
llm-cli build start -m ./my_model.onnx -t Rockchip -f rknn -b rk3566 -q fp16 -F
```

- Results go to the output folder, `~/llmcf/output/Rockchip/<model>/<quantization>/` (model file, `build_info.json`,
  `Model_Card.md`, and the ZIP next to it). At a terminal you are asked first, with the output folder as the default;
  `-o/--output` sets another folder (unreleased; before, `output/build_<date>_<time>/` in the installation).
- A combination the target does not support stops before anything runs, with exit code 2 and the reason (for
  example "Format 'rkllm' does not run on rk3566").
- Gated Hugging Face models need a token: `llm-cli secrets set hf_token` (asks for the value).
- The command stays in the foreground until the build has finished; `--follow` prints each stage. If the build
  waits for a self-healing decision, the CLI asks in the terminal. Ctrl+C cancels the build (its container is
  stopped), waits until it has stopped and exits with code 130. `llm-cli build status` also lists builds that the
  GUI, the MCP server or another CLI call started.

**Deploying** (the same steps as in the GUI):

```bash
llm-cli deploy list-artifacts
llm-cli deploy package build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
llm-cli deploy run output/deploy_<model>_<timestamp>.zip --host 192.168.1.50 --user rock
```

Every command and option, with examples: [docs/CLI_Usage.md](docs/CLI_Usage.md).

---

## Quick start: AI clients over MCP (Claude Code, Claude Desktop, VS Code)

The framework runs as a [Model Context Protocol](https://modelcontextprotocol.io) server. An AI client you
already use can then operate it: list targets, check what a board supports, start and watch builds, read logs,
search the knowledge base, run the health check and decide on self-healing proposals. You sign in to the client
with your own account or key; the framework never sees those credentials.

```bash
llm-cli ditto setup                         # once: what AI clients may do on their own, what needs your confirmation
llm-cli mcp config                          # prints the setup for Claude Code, Claude Desktop and VS Code
llm-cli mcp config --write claude-desktop   # or add it to Claude Desktop directly (other entries are kept, a backup is written)
```

`llm-cli mcp config` prints a `claude mcp add ...` command for Claude Code and JSON snippets for Claude Desktop
and VS Code (`.vscode/mcp.json`), with the Python and folder of your installation. Run it with the framework's
Python (see [Calling `llm-cli`](#calling-llm-cli)). After `--write claude-desktop`, restart Claude Desktop.

- **Permissions:** read-only tools are allowed by default. Starting or cancelling builds and deciding on
  self-healing proposals ask for your confirmation, which the client shows as a question, unless you allowed
  them. When a client cannot ask you (Claude Desktop chat and Cowork, for example), the question waits for you in
  the framework instead: a notice in the GUI (**Tools > Pending Approvals...**) and `llm-cli tools approvals` in a
  terminal. Approve it there and the framework runs the call for the client. Where the framework can offer it, the
  window also has "Approve and do not ask again", a standing approval for calls within a narrow scope that you can
  revoke in Tool Permissions. Every call is written to a tamper-evident audit log (`llm-cli tools audit`).
- **Transports:** stdio (default, started by the client) or local Streamable HTTP
  (`llm-cli mcp serve --transport http`, loopback only, default address `http://127.0.0.1:8765/mcp`). Over HTTP
  every client needs its own named token: `llm-cli mcp token add NAME` shows it once, and the client sends it as
  `Authorization: Bearer <token>`. Web pages (requests with an `Origin` header) are refused.
- **One token per client:** a named token is the caller in the audit log (`mcp:<name>`) and has its own
  permissions; a new one can only read until you allow more (`llm-cli tools permissions --token NAME`, or in the
  GUI under Tools > Tool Permissions (Ditto / MCP), where tokens are also added, rotated and revoked). A stdio
  client gets its own token with `LCF_MCP_TOKEN` in its server entry; without it, all local stdio clients share your
  own permissions.
- **Resources:** the targets, a target's capabilities, a build's status and log, your tool permissions and the
  framework's documentation can also be attached as resources (`lcf://targets`, `lcf://builds/<id>/log`,
  `lcf://docs/Features`, ...). They follow the permissions of the matching read tool and are audited like a tool
  call; clients on protocol 2026-07-28 are told when a build or the permissions change.
- **Prompts:** Ditto's tasks for your client, with the same instructions and knowledge context Ditto gets:
  `ask_about_framework`, `convert_model_for_board`, `recommend_build_settings` and `diagnose_build_failure` (in
  Claude Code as slash commands). They grant nothing; every step still goes through the tools and your permissions,
  and a prompt shows nothing a read tool you denied would show.
- There is no deployment tool over MCP.

**Using a Claude subscription:** Anthropic does not allow third-party apps to use a Claude subscription's sign-in
or limits. If you have a Claude plan, use the framework **from Claude Code or Claude Desktop over MCP** as shown
above, or give Ditto an **Anthropic API key** (Max and Team plans include monthly API credits for a linked Claude
Console organization).

---

## Ditto, the built-in assistant

**Ditto is optional and experimental. Use at your own risk and responsibility. The creator of LLMCF is not liable.**
This repeats the no-warranty terms of [LICENSE.txt](LICENSE.txt) and adds nothing to them. Everything else in the
framework works without Ditto. The chat panel and the AI settings show an **Experimental** badge; the first time you
use Ditto, a notice says this once (stored per user), and `llm-cli ditto` prints it once at a terminal.

Ditto answers questions, suggests build settings, analyzes failed builds and proposes new hardware modules. It
uses the framework's tools within your tool permissions:

- **GUI:** the **chat panel on the right** of the main window (F9 shows or hides it, Ctrl+Shift+C writes in it;
  see [The chat panel](docs/Features.md#the-chat-panel)). Tools that need a confirmation open a dialog that shows the
  tool and every argument. The conversation is saved per user (`~/.llm-framework/chat_history.json`); a long
  conversation is summarized into a new one. **Clear chat…** first lists what it deletes (full path, file name, size,
  what it is), with "No" as the default.
- **CLI:** `llm-cli ditto ask "Which boards can run RKLLM?"` asks one question (no saved history).
  Confirmations are asked in the terminal and denied when there is no terminal. `--no-tools` answers without
  tools.
- **Module proposals:** `llm-cli module generate-ai target_hardware_config.txt` or the wizard under **Tools >
  Create Hardware Module...**. You see the whole proposal before anything is written.

**Set up Ditto** under **Tools > Configure AI Agent (Ditto)...**: provider, model (the list is loaded live from
the provider; you can also type a model name), API key, workspace ID (only for Claude API keys that are valid in
every workspace of the organization), base URL (Ollama and custom servers), effort, privacy level for cloud
providers, a fallback chain, the knowledge base and offline mode. **Sign in...** next to the key field shows the
provider's sign-in methods with Check, Save and Sign out. On a headless machine, sign in with
`llm-cli ditto login <provider>` (the key is asked without echo, or read from stdin with `--with-token`) and set
`ai_provider` in `config.yml` (or the environment variable `LLM_FRAMEWORK_AI_PROVIDER`).

| Provider (id) | What it is | Sign-in | From the command line |
|---|---|---|---|
| `anthropic` | Anthropic Claude API | API key (an `ant auth login` profile as an advanced option) | `llm-cli ditto login anthropic` |
| `openai` | OpenAI (Responses API) | API key | `llm-cli ditto login openai` |
| `google` | Google Gemini (OpenAI-compatible endpoint) | API key | `llm-cli ditto login google` |
| `openrouter` | OpenRouter (many models behind one key) | API key | `llm-cli ditto login openrouter` |
| `ollama` | Ollama on this computer or your network | none | `llm-cli ditto login ollama --base-url URL` |
| `local` | the managed local model server (see [Offline mode](#offline-mode-the-managed-local-model)) | none (a key per start is handled for you) | – |
| `custom` | any OpenAI-compatible server (vLLM, LM Studio, ...) | API key or none | `llm-cli ditto login custom --method api_key --base-url URL` |

- The providers are data in [`configs/llm_providers.yml`](configs/llm_providers.yml); a file with the same name
  in your settings folder adds or replaces entries. The default models per task are set there.
- **Privacy filter for cloud providers** (setting `ai_security_level`): secrets are always removed. *Balanced*
  also replaces IP, MAC and e-mail addresses, logins and user names; *Strict* (default) also host names, serial
  numbers, UUIDs and directory names. Ditto puts the real values back into the answer on your computer. Local
  providers are never filtered.
- **Fallback chain** (setting `ai_fallback_chain`, e.g. `local, ollama:qwen3.5:4b`): tried in order when the
  model declines or the provider is unavailable.
- Structured answers (healing analysis, module proposal, build recommendation) must match a fixed structure and
  are checked; an invalid answer is an error and is never applied. A proposal that contains a
  dangerous command (for example a download piped into a shell) is discarded.
- **Sign-in per provider:** **Sign in...** in the AI settings and `llm-cli ditto login` take a key from a hidden
  field, stdin or the provider's environment variable, check it for free before storing it and show it only as
  its last four characters; `llm-cli ditto auth status` lists the sign-in of every provider. Browser sign-in
  (OAuth) and cloud platforms come later. For `anthropic` the stored API key always comes first; an `ant auth login` profile is used
  only when you choose it (setting `ai_auth_anthropic: ant_profile`). Claude Admin API keys (`sk-ant-admin01-`)
  are refused. Without a stored key, a key in `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` or
  `OPENROUTER_API_KEY` is used, and the AI settings name the variable. A key of a `custom` server belongs to the
  address it was saved for and is not sent after the base URL changes.
- There is no provider for a Claude subscription: Anthropic does not allow third-party apps to use subscription
  limits. A saved `claude_code` provider stops with a message that names the alternatives; use MCP or an API key
  as described [above](#quick-start-ai-clients-over-mcp-claude-code-claude-desktop-vs-code).
- `OPENAI_CUSTOM_HEADERS` and `ANTHROPIC_CUSTOM_HEADERS` from the environment are ignored (with a warning), so they
  cannot replace your key; the setting `ai_allow_sdk_env_headers: true` keeps them.

`llm-cli ditto eval` grades Ditto on fixed test cases, for example to check whether a small local model is good
enough; `--kind retrieval` grades the knowledge-base search instead. Details and measurements:
[docs/Features.md](docs/Features.md#ditto-evaluation).

**Ditto's tools are this computer's.** Ditto uses the framework's own tools on this computer, the same ones an MCP
client gets, under Ditto's own permissions. It never connects to a framework on another computer (an earlier
development version could; that mode was removed, see
[docs/Features.md](docs/Features.md#only-this-computers-tools)).

---

## Offline mode: the managed local model

Ditto can run without any cloud account on a local model. The framework builds `llama-server` on your computer from
the pinned llama.cpp commit, downloads a pinned Qwen3.5 model and runs the server:

```bash
llm-cli ditto runtime status       # hardware, the build in use, the recommended model, server state
llm-cli ditto runtime install      # builds llama-server for this machine (CPU build first, then the GPU build)
llm-cli ditto models list          # the model tiers, their size and whether they are downloaded
llm-cli ditto models download      # the recommended model (or a tier, e.g. small-q2)
llm-cli ditto runtime start        # starts llama-server; it keeps running until "runtime stop"
llm-cli ditto runtime stop
```

**The build.** `runtime install` shows what it will build and asks first. It needs a C/C++ compiler (GCC or Clang),
CMake and Ninja (on Debian or Ubuntu: `build-essential cmake ninja-build`); without them it builds in the framework's
builder container when Docker runs. The framework never installs a toolchain for you. The pinned source (about
215 MB) is fetched once by its commit and checked; the build itself runs without network. The CPU build comes first
and takes a few minutes, so `runtime start` works soon; a GPU build (Vulkan on Linux in this version; CUDA, ROCm,
Windows and macOS builds follow) is built after it and used once it passed its checks. Every build is checked on this
machine before it is kept (version and commit of the pin, the devices it finds, and that the server refuses its own
tools), and `runtime start` starts nothing else.

- **Air-gapped machines:** `llm-cli ditto runtime export-source FILE` and `import-source FILE` carry the source;
  `llm-cli ditto runtime export FILE.tar.gz` on a machine of the same kind (same OS and architecture, and a CPU of
  the same vendor with at least the CPU features of that machine, since the build uses `-march=native`) and
  `llm-cli ditto runtime import FILE.tar.gz` here carry a finished build. An imported build is checked against the
  pin and its own manifest, you confirm it, and it is verified here before it is used.
- **Official prebuilt archive:** `llm-cli ditto runtime install --prebuilt` downloads upstream's archive instead (size
  and sha256 checked). It is only used when you ask for it, also an archive an earlier version downloaded (then
  `--prebuilt` takes it without downloading it again), and is always shown as "prebuilt, not self-built". On
  Linux it needs the glibc it was built for (2.35 or 2.39); on musl systems such as Alpine only the self-built server
  runs. Until the Windows and macOS builds land, this is the way to run the local model there.
- `llm-cli ditto runtime prune` lists old builds for you to delete (nothing ticked; the framework deletes no build by
  itself); the active build, its CPU fallback, the build a running server uses and your boards' builds are never
  listed. `runtime status` says when a rebuild is required (the pin changed) or recommended (this machine's build plan
  changed).

**Bare metal or Docker.** The server runs bare metal by default (no Docker overhead) or in Docker:

```bash
llm-cli ditto runtime ensure                              # installs it if it is not there; a second run does nothing
llm-cli ditto runtime ensure --mode docker --force        # switch to Docker (needs a terminal)
llm-cli ditto runtime ensure --mode bare-metal --force    # and back
```

- **The image** is built on your computer from the same pinned llama.cpp recipe, on a Docker Official base image
  (Debian, Ubuntu or Alpine, pinned by digest). It is never pulled as a finished image and never pushed. It is checked
  in a container of itself before it is used (version and commit of the pin, the devices it sees, and that the server
  refuses its own tools).
- **The container** keeps the same rules as the bare-metal server: its port is published on 127.0.0.1 only, the
  models folder is mounted read-only (the models are not copied), it runs as your user without extra rights, and it
  gets a new key file at every start. The GPU is passed through where it can be (NVIDIA through the NVIDIA Container
  Toolkit, AMD and Intel through `/dev/dri`); where not, `ensure` says why and the container uses the CPU.
- **Switching** keeps the models (nothing is downloaded again) and makes only the server again: the bare-metal build
  is put into the image when the base image's C library fits, and the image's build becomes the bare-metal build once
  it passed its checks on this computer. Where that is not possible, `ensure` says why and builds again.
- **The old mode's files** (build folders with their full path, Docker images and containers with their name and ID,
  each with its size) are listed after the switch, and only what you confirm is removed ("No" is the default).
  Without a terminal nothing is removed. Docker images are never deleted otherwise.
- `llm-cli ditto runtime status` shows the mode. An AI client may read it (`get_status`); switching and removing
  are yours alone.

Then choose **Local model (managed llama-server)** in the AI settings (provider `local`). Turn on **Offline Mode**
as well if Ditto should never use a cloud provider. Ditto finds the running server by itself. The AI settings show
where the server runs and can switch it; starting and stopping it is on the command line.

| Tier | Model | Download | Chosen automatically when |
|---|---|---|---|
| `large` | Qwen3.5 9B, 4-bit (Q4_K_M) | 5.3 GiB | a dedicated GPU (or Apple Silicon) with at least 7.5 GiB of memory is used |
| `default` | Qwen3.5 4B, 4-bit (Q4_K_M) | 2.6 GiB | otherwise |
| `small` | Qwen3.5 2B, 4-bit (Q4_K_M) | 1.2 GiB | up to 4.5 GiB RAM, or when `default` does not fit |
| `small-q2` | Qwen3.5 2B, 2-bit (UD-Q2_K_XL) | 0.9 GiB | up to 3 GiB RAM; less accurate |
| `default-q2`, `large-iq2`, `granite` | 2-bit variants and IBM Granite 4.2 3B | 1.8 / 3.4 / 2.1 GiB | only on request |

- `llm-cli ditto runtime start --model <tier>` picks a tier and remembers it; `--model auto` returns to the
  recommendation.
- **Hardware:** the build recipe follows the machine's OS, architecture and C library. AMD Radeon GPUs and APUs
  (e.g. Ryzen Z1 Extreme) and Intel Arc get a Vulkan build; NVIDIA GPUs will get a CUDA build matched to GPU and
  driver (until then the CPU build runs there, and `runtime install --backend vulkan` builds a Vulkan server that
  also uses NVIDIA GPUs); everything else uses the CPU build. `runtime status` explains each choice.
- **Verified downloads:** the llama.cpp commit, the prebuilt archives and every model are pinned in
  [`configs/llama_runtime.yml`](configs/llama_runtime.yml) (archives and models with size and sha256, models at a
  fixed Hugging Face commit) and checked before they are used; unpinned entries are refused.
- **Isolation:** the server listens on 127.0.0.1 only, on a free port, with a new API key per start that is
  passed in a file only you can read, and with a cleared environment. Its web UI, its own tools and its MCP client
  stay off; the self-built server is compiled without its shell tool at all.
- **Effort:** with effort "low" the local model answers without thinking first (much faster on a CPU); "medium"
  and higher let it think.

Ollama or another OpenAI-compatible server on your network also count as local providers in offline mode.

---

## The knowledge base

The optional knowledge base lets Ditto and MCP clients search documentation you added (for example SDK manuals).
Turn it on in the AI settings (**Enable Local Knowledge Base**). Everything runs on your computer:

- embeddings are computed locally with FastEmbed (ONNX Runtime); the model is downloaded once into
  `models/embeddings/` (about 0.5 GB on a machine without CUDA); in offline mode nothing is downloaded,
- the vectors are stored in an embedded Qdrant database in `~/.llm-framework/qdrant`, without Docker and
  without an open port,
- searches combine meaning (vectors) with exact words (BM25), so error codes and compiler flags are found too, also
  parts of identifiers such as `GGML_CUDA` in `-DGGML_CUDA=ON`,
- a local cross-encoder reranks the excerpts for build recommendations and drops irrelevant ones (setting
  `rag_reranker`).

Add documentation with **Deep Ingest (Add Docs)** in the module wizard (**Tools > Create Hardware Module...**), or let
an AI client or Ditto ask for a crawl with `crawl_docs_site` (you confirm each one and see every start address).
Before your first crawl, a one-time notice says what is read and kept; it is information, not a confirmation. The
crawler blocks private network addresses, limits pages and depth, and honours these machine-readable opt-outs of a
site: robots.txt, the `X-Robots-Tag` header, robots meta tags (including `noai`) and the TDM Reservation Protocol.
None of these checks can be switched off. The wizard shows "Page N of M", **Stop** keeps what is already stored, and
the result says success, partial or failure; the outcome is also spoken by the speech engine of the operating system,
if there is one, unless you turn that off. Search results are treated as external text, never as instructions. Each
page also records its licence and copyright notice: a knowledge pack carries a page's full text only where its licence
allows passing it on, every other page as its address and short facts (`llm-cli knowledge sources` shows each page).
More: [knowledge base](docs/Features.md#knowledge-base-rag), [Deep Ingest](docs/Features.md#deep-ingest) and
[licences and copyright](docs/Features.md#licences-and-copyright).

---

## Community zoo

Targets, recipes (how a model was built for a board) and knowledge can be shared as signed packs:
`llm-cli zoo list|show|install|verify|export|keys` or **Tools > Open Community Hub**. A pack is installed only after
checks in a quarantine folder and your confirmation, and it shows a trust level. Your own work is exported
privacy-scrubbed and signed with your key (knowledge with a page's full text only where its licence allows it, and
the export lists every page with what it carries and why); a pull request goes through the GitHub CLI or the
browser, and the framework never asks for a token. Packs can also be encrypted for your own machines or a team, in
the age format, so the standard age and rage tools open them too. No community repository or index is published yet,
so today only pack files can be installed. Details: [docs/Features.md](docs/Features.md#community-zoo).

---

## Security in short

- **Sandboxed builds:** conversion containers run as your user instead of root, without network access, without
  Linux capabilities, with `no-new-privileges`, a read-only image filesystem and a process limit; a memory limit
  can be set. Models are downloaded on the host beforehand.
- **Tool permissions and audit log:** Ditto and MCP clients act only through registered tools with risk
  categories. You decide what runs on its own, what asks and what is denied. Every call lands in an HMAC-chained
  audit log; `llm-cli tools audit --verify` detects changed or removed entries.
- **SSH trust on first use:** an unknown board's host key is shown with its SHA256 fingerprint and stored only
  after you confirm. A changed key is always refused. The SSH password is used once to install the framework's
  own key and is never stored.
- **Secrets:** API keys and tokens are stored encrypted in a vault in your personal folder; its key is in your
  operating system's keyring. Without a usable keyring nothing is chosen for you: the framework stops and names
  the choices (setting `secret_storage`: a credential helper command, a passphrase, an unencrypted key file or
  memory only). Credentials are redacted in every log, also with `--debug-http`. `llm-cli mojo` warns when the
  vault key is not protected.
- **Untrusted text:** web pages, knowledge-base text, build logs, tool results and probe files are cleaned of
  hidden characters and passed to the AI as marked data, never as instructions. Chat output is escaped, and links
  open only after you confirm.
- **Privacy:** for cloud providers, personal data is replaced by placeholders before it leaves your computer
  (see [Ditto](#ditto-the-built-in-assistant)).

Details: [docs/Features.md](docs/Features.md).

---

## Settings

The GUI saves its settings in `config.yml` in the settings folder (see [Where your files are](#where-your-files-are)).
You can also edit the file, or override single values with environment variables of the form
`LLM_FRAMEWORK_<KEY>`, for example `LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`.

Some settings with their default values:

```yaml
language: en                  # en or de
ai_provider: OpenAI           # a provider ID from the table above, e.g. local; older names such as OpenAI still work
ai_security_level: STRICT     # STRICT, BALANCED or OFF (cloud providers only)
offline_mode: false           # true: only local providers
secret_storage: keyring       # keyring, helper, passphrase, insecure or ephemeral (protection of the secrets vault)
enable_rag_knowledge: false   # the local knowledge base
calibration_language: mixed   # shipped calibration text: mixed, de or en
build_sandbox: true           # sandbox for conversion containers
build_memory_limit: ""        # e.g. 24g; empty = no limit
build_pids_limit: 4096        # process limit in the container
build_jobs: 0                 # parallel compile jobs for image builds; 0 = all cores
crawler_max_depth: 2          # link depth the Deep Ingest dialog opens with
rag_reranker: auto            # auto (chosen by hardware), off, or a cross-encoder model id
rag_rerank_default: false     # true: also rerank chat and MCP searches (default: build recommendations only)
# rag_rerank_min_score: 0.3   # relevance floor 0..1; unset = the model's calibrated floor (0.2), 0 = no floor
rag_model_idle_minutes: 10    # unload the knowledge-base models after this idle time; 0 = keep them loaded
audio_announcements: true     # spoken Deep Ingest notice and outcome (Tools > Spoken Announcements)
community_index_url: ""       # https address of the signed community index; empty until one is published
community_repo: ""            # GitHub owner/name that receives community packs as pull requests; empty for now
```

The knowledge-base, Deep Ingest and community settings are explained in [docs/Features.md](docs/Features.md).

---

## Known gaps

- **The framework in a container builds no models:** the image you build with `make build` has no Docker inside,
  so conversions run on a computer where the framework is installed directly
  ([docs/Container_Setup.md](docs/Container_Setup.md#8-limits)).
- **Windows hosts:** the test suite runs on Windows in CI, but conversions on a Windows host have not been
  verified.
- **Browser and cloud sign-in** (OAuth, AWS, Google Cloud, Azure) are not available yet; Ditto signs in with API
  keys, local engines need none, and an `ant auth login` profile is an advanced option.
- **Deep Ingest** has no CLI command: you start it in the module wizard in the GUI, or confirm an AI client's
  `crawl_docs_site`.
- **Community zoo:** no community repository, index or maintainer key is published yet (`configs/community_keys.yml`
  is empty, `community_index_url` and `community_repo` are empty), so only pack files can be installed.

---

## What comes next

The roadmap lists what is planned; finished items move to the [CHANGELOG](CHANGELOG.md) at release. In short:

- **In progress:** the remaining sign-in methods per access type, browser sign-in and cloud platforms (API keys,
  local engines and the `ant` profile work in the GUI and with `llm-cli ditto login`; the Claude-subscription
  provider is removed, and Claude subscribers use MCP or an API key); a new
  Ditto evaluation run with the improved prompts; the self-built llama.cpp runtime on the board (the command line
  works; the GUI, the MCP tools and a run on a real board follow).
- **Planned:** recipes as target templates, then right after them a rotation stage before quantization (SpinQuant,
  QuaRot; only where the format supports it) and a generic ARM64 target for the Raspberry Pi; targets for NVIDIA GPUs
  and AMD (Ryzen Z1 Extreme); later Llama-family models in every format, ONNX, Hailo, Intel and Qualcomm; LoRA and
  large models, and supply-chain hardening. New targets start from the hardware probe, and a benchmark on the board
  is the proof ("tested on <board>"). Builds in the cloud are not planned.

Full list: [docs/BLUEPRINT.md](docs/BLUEPRINT.md#roadmap).

---

## Documentation

| Document | Contents |
|---|---|
| [docs/CLI_Usage.md](docs/CLI_Usage.md) | Every CLI command and option, with examples |
| [docs/Features.md](docs/Features.md) | Features in detail: build pipeline, sandbox, Ditto, evaluation, local runtime, knowledge base, Deep Ingest, MCP, community zoo |
| [docs/BLUEPRINT.md](docs/BLUEPRINT.md) | Architecture and roadmap |
| [docs/Module_Developer_Guide.md](docs/Module_Developer_Guide.md) | Adding a new hardware target |
| [docs/Container_Setup.md](docs/Container_Setup.md) | The framework in a container you build yourself (server, NAS, Proxmox) |
| [docs/Mojo.md](docs/Mojo.md) | The health check: Mojo Oracle looks, Mojo Miracle repairs (formerly the doctor) |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, tests, conventions |
| [configs/SSOT_ARCHITECTURE.md](configs/SSOT_ARCHITECTURE.md) | Project sources (single source of truth) |
| [data/calibration/README.md](data/calibration/README.md) | Shipped calibration texts: sources, checksums, license |

Every document is also available in German (`<name>_DE.md`).

## Contributing

Contributions are welcome, especially new hardware targets. How to set up a development environment, run the
tests and submit changes: [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)**: GGUF conversion, quantization and the local model server
- **[Hugging Face](https://huggingface.co/)**: the model ecosystem
- **[Rockchip RKNN-LLM](https://github.com/airockchip/rknn-llm)** and
  **[RKNN-Toolkit2](https://github.com/airockchip/rknn-toolkit2)**: the NPU toolkits
- **[Qwen](https://huggingface.co/Qwen)** and **[IBM Granite](https://huggingface.co/ibm-granite)**: the models
  of Ditto's offline mode
- **[Ditto](https://github.com/yoheinakajima/ditto)** by [@yoheinakajima](https://github.com/yoheinakajima):
  AI agent framework and namesake of the assistant
- **[Model Context Protocol](https://modelcontextprotocol.io)**: the Python SDK behind the MCP server
- **[eaddario/imatrix-calibration](https://huggingface.co/datasets/eaddario/imatrix-calibration)**: the shipped
  calibration texts (MIT)
- **[Qdrant](https://qdrant.tech/)** and **[FastEmbed](https://github.com/qdrant/fastembed)**: the local
  knowledge base
- **[rag4ll](https://github.com/Smilez1985/rag4ll)**: the reranker, the excerpt budget, the pack manifest, the
  privacy scrub and the shape of the speech call are adapted from it (MIT, see
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md))
- **[Radxa Community](https://forum.radxa.com/)**: support with the RK3566 integration
- **[Docker](https://www.docker.com/)**, **[PySide6](https://doc.qt.io/qtforpython-6/)**,
  **[uv](https://docs.astral.sh/uv/)**

## License

MIT License, see [LICENSE.txt](LICENSE.txt). The file also contains additional terms on attribution, third-party
software, vendor SDKs and model licenses. Models you convert keep their own licenses.
