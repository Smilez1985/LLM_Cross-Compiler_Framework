Version 3.0.0 + unreleased changes · Last updated 2026-10-11

**English** · [Deutsch](Module_Developer_Guide_DE.md)

# Module Developer Guide

This guide explains how to add support for new hardware to the LLM Cross-Compiler Framework. A **target** is a
hardware family with its own toolbox image (for example Rockchip); a **board** is one chip or device within it
(for example RK3566). Each target is a folder below `targets/`. The Rockchip target is the reference: when this
guide says "see Rockchip", look at `targets/Rockchip/`.

You need the [development setup](../CONTRIBUTING.md#1-development-setup), Docker, and a device of the new
hardware (or its specifications).

## Contents

1. [How the framework uses a target](#1-how-the-framework-uses-a-target)
2. [Folder layout](#2-folder-layout)
3. [Creating a target](#3-creating-a-target)
4. [target.yml and the capabilities block](#4-targetyml-and-the-capabilities-block)
5. [The Dockerfile](#5-the-dockerfile)
6. [The build script](#6-the-build-script)
7. [Sandbox rules](#7-sandbox-rules)
8. [Testing a target](#8-testing-a-target)
9. [Sharing a target](#9-sharing-a-target)
10. [Legacy files](#10-legacy-files)

## 1. How the framework uses a target

1. **Loading.** At start, the `TargetManager` reads `target.yml` in every folder below `targets/`. Folders whose
   name starts with `_` or `.` (such as `_template`) are skipped, and so is a `target.yml` without a `metadata:`
   block. The target id is `metadata.id` if set, otherwise the folder name. Capabilities are looked up by the id,
   but the build engine finds the target by its folder name (`llm-cli build start -t <folder>`). Leave
   `metadata.id` unset (or equal to the folder name); otherwise either the capabilities are not found or the build
   does not find the target folder.
2. **Capabilities.** The `capabilities:` block is validated against the schema in `orchestrator/schemas/targets.py`.
   The GUI offers exactly the formats, boards and quantizations listed there, the CLI refuses anything else, and the
   tools of the MCP server and Ditto start builds only for targets that have a valid block. An invalid block is
   logged as an error, and the target is then treated as if it had none.
3. **Image build.** For each build the `BuildEngine` stages a build context (the target's `modules/` and
   `scripts/`, the shared `entrypoint.sh` as `docker/entrypoint.sh`, and the Dockerfile) and builds the image
   `llm-framework/<target>:<build id>`. This step has network access.
4. **Stages.** The model is staged on the host first. Then `modules/build.sh` runs in sandboxed containers without
   network: once with `JOB_TYPE=imatrix` when an importance matrix is requested for GGUF, then with
   `JOB_TYPE=build`.
5. **Results.** Files that `build.sh` writes to the output folder are copied to `output/`. The engine completes
   `build_info.json` with every model file's size and SHA256 hash, writes `Model_Card.md` and a ZIP. Deployment
   finds builds through this manifest.

## 2. Folder layout

```text
targets/Rockchip/
├── target.yml           metadata and capabilities
├── dockerfile           build environment ("Dockerfile" works as well)
├── modules/
│   ├── build.sh         dispatcher: the only entry point of every stage
│   ├── rkllm_module.sh  RKLLM export (RK3588, RK3576)
│   ├── rknn_module.sh   RKNN conversion
│   └── source_module.sh, config_module.sh, convert_module.sh, target_module.sh   (legacy, not called)
└── scripts/
    ├── export_rkllm.py
    └── rknn_converter.py
```

| File | Needed | What it is for |
|---|---|---|
| `target.yml` with a `metadata:` block | yes | Without it the folder is not a target. |
| `capabilities:` block in `target.yml` | strongly recommended; required to share the target | Without it the GUI and CLI cannot check settings, the MCP tools and Ditto cannot start builds for the target, `llm-cli doctor` warns, and the target cannot be exported or installed as a community pack. |
| `Dockerfile` or `dockerfile` | yes | The image all stages run in. |
| `Dockerfile.gpu` | optional | Used for builds with GPU passthrough (`llm-cli build start --gpu`, or "Use Host GPU for Build Process" in the GUI, which is checked by default on Linux); without it the normal Dockerfile is used. |
| `modules/build.sh` | yes | Runs in every stage as `/app/modules/build.sh`. |
| `scripts/` | optional | Helper scripts; copied into the image next to `modules/`. |

## 3. Creating a target

### 3.1 Probe the board

Copy `scripts/hardware_probe.sh` to the device and run it there (on Windows devices: `scripts/hardware_probe.ps1`):

```bash
bash hardware_probe.sh
```

It writes `target_hardware_config.txt` into the current folder: CPU model, cores and flags, memory, GPU and NPU
vendor ids, and more. It also contains the device's host name; remove it before you share the file. If a helper
tool is missing, the script offers to install it; set `NON_INTERACTIVE=true` to skip that.

In the GUI build tab, **Scan / Import... > Remote Scan via SSH...** runs the probe on a device over SSH (with the
same trust-on-first-use check as deployment) and fetches the result. `llm-cli scan` runs the probe on the computer
you are working on, which helps only if that computer is the target hardware.

### 3.2 Generate the skeleton

There are three ways; all of them end in the same module generator (`orchestrator/Core/module_generator.py`).

- **GUI wizard:** **Tools > Create Hardware Module...** Import the probe file with the standard import (fixed
  parsing) or let Ditto analyse it, optionally add documentation pages to the knowledge base with "Deep Ingest",
  then check the pages for hardware, Docker and flags and confirm the summary. For `aarch64`, `armv7l` and
  `riscv64`, the wizard adds the matching cross-compiler packages. The last page ("Summary & Generation") has two
  check boxes, both off by default: the knowledge snapshot
  ([section 3.4](#34-what-the-generator-writes-and-what-you-add)) and "Export for community after creating"
  ([section 9.2](#92-exporting-a-target-for-the-community)).
- **CLI, interactive:** `llm-cli module create` asks for name, architecture (`aarch64`, `x86_64`, `armv7l`,
  `riscv64`), SDK, base image, description, packages and CPU flags. Run it from the installation folder: unlike the
  other commands, it resolves a relative `targets_dir` against the current folder.
- **CLI with Ditto:** `llm-cli module generate-ai target_hardware_config.txt` (optionally `--provider` and
  `--model` for this run). Ditto returns a structured proposal (architecture, SDK, base image, packages, CPU and
  CMake flags, setup commands, quantization block). If the knowledge base is enabled, it adds matching
  documentation excerpts first. The proposal is printed in full; nothing is written until you confirm. Spaces in
  the module name become underscores.

### 3.3 What the generator checks

Every value ends up in a Dockerfile, YAML or a shell script, so it is checked before any template is filled in:

- **Name:** 1 to 64 letters, digits, spaces, `_`, `.` or `-`, starting with a letter or digit. The folder name is
  the lower-case name with underscores (`My Board` becomes `targets/my_board/`).
- **Architecture:** one of `aarch64`, `x86_64`, `armv7l`, `riscv64`.
- **Base image:** must be pinned by digest (`image@sha256:...`) unless it starts with `debian:`, `ubuntu:`,
  `nvidia/cuda:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` or `python:`.
- **Packages:** valid Debian package names. **Flags:** plain compiler or CMake options such as
  `-mcpu=cortex-a76` or `-DGGML_CUDA=ON`.
- **Shell code** (setup commands, quantization block): no downloads (`curl`, `wget`, ...), no `sudo`, no deleting
  `/`, no `chmod 777`, no raw device writes, no Docker socket or `docker run`, no evaluation of generated code; no
  control characters. Every generated script must pass `bash -n`.
- **Place:** the target is created only directly below `targets/`, never over an existing target (also not with
  different case), and it is removed again if generation fails.
- **The finished folder:** before the generator reports success, the new target must pass the same validators as a
  community pack ([section 9.1](#91-what-the-validators-check)), including that no template placeholder is left
  anywhere. If anything fails, the folder is removed and the error lists the problems.

### 3.4 What the generator writes, and what you add

The generator writes `target.yml` (metadata, `supported_boards`, `docker`, `modules`, `optimization_profiles`,
`requirements` and a `capabilities:` block), a `Dockerfile` (from `Dockerfile.gpu` of the template if the SDK
contains "cuda" or the base image contains "nvidia"), `modules/build.sh`, `modules/config_module.sh`,
`modules/source_module.sh`, `modules/rkllm_module.sh`, `modules/rknn_module.sh`, `modules/benchmark_module.sh`,
placeholders for `convert_module.sh` and `target_module.sh`, `generate_profile.sh` and the template's `scripts/`.

**The capabilities block.** Every generated target gets one, with support level `community`, so the Builder, the
CLI and AI clients check its build settings from the start. For a module named "My Board" it looks like this:

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board
  boards:
    my_board:
      label: My Board
  formats:
    gguf:
      label: GGUF (llama.cpp)
      input: [hf]
      quantizations: [Q4_K_M, Q4_0, Q5_K_M, Q6_K, Q8_0, F16]
      default_quantization: Q4_K_M
      imatrix: true
      dataset: optional
```

- **Boards:** the boards given to the generator; the id is the name in lower case with underscores, the label is
  the name. The GUI wizard and `llm-cli module create` give none and `generate-ai` gives the module name, so in
  practice the module name is the only board.
- **Quantizations:** the labels of the `case` branches in the quantization logic of the wizard or of Ditto's
  proposal (for example `Q4_K_M)` or `Q4_K_M|Q5_K_M)`; only names of letters, digits and `_`). Without such
  branches: `Q4_K_M`, `Q4_0`, `Q5_K_M`, `Q6_K`, `Q8_0` and `F16`. The first one is the default. Labels are taken as
  written, so `Q4_K_M|q4_k_m)` lists both spellings. Keep one spelling in the block: requests are matched without
  regard to case, and other names go into `aliases` (for example `INT8: Q8_0`).

**Knowledge snapshot (opt-in).** On the wizard's last page, the check box "Attach the knowledge crawled for this
target (Deep Ingest, privacy scrub applied)" is off by default and can only be switched on after Deep Ingest ran in
this wizard. Then the generator exports only the pages crawled from the URLs you entered in this wizard,
privacy-scrubbed (home folders, addresses, host and user names, keys), to `knowledge/initial_knowledge.json`, with a
`README.md` next to it that says what the file holds. While a Deep Ingest is still running, Finish stays disabled,
so the snapshot holds every crawled page. Nothing else from your knowledge base goes into the module, and without
the check box there is no `knowledge/` folder. `llm-cli module create` and `generate-ai` never attach knowledge.
The framework does not import that file into a knowledge base, and the generator's placeholder check leaves it out
(crawled pages may well contain text like `YYYY-MM-DD`). The pages remain under their owners' terms; the licence of
a pack does not cover them.

The result is a **skeleton**, not a working target yet:

- **The capabilities block is a starting point.** Add your real boards, other formats, per-board quantizations and
  calibration needs ([section 4](#4-targetyml-and-the-capabilities-block)), and keep the block in sync with
  `build.sh`.
- **The template `build.sh` does not match the current pipeline.** It looks for `convert-hf-to-gguf.py` and the
  llama.cpp tools in places where current llama.cpp builds do not put them, writes the F16 intermediate to `/tmp`
  (a 1 GB tmpfs in the sandbox) and falls back to copying the model unchanged. The template Dockerfile does not build
  llama.cpp. For GGUF, take `targets/Rockchip/dockerfile` and `targets/Rockchip/modules/build.sh` as the starting
  point, or the example in [section 6](#6-the-build-script). The generated block declares GGUF all the same, so a
  build of the unchanged skeleton is accepted and then fails or copies the model.

An updated template is planned for 3.4.0 (see the [roadmap](BLUEPRINT.md#roadmap)).

## 4. target.yml and the capabilities block

### 4.1 metadata

```yaml
metadata:
  name: "My Board"
  description: "CPU-only GGUF builds for My Board"
  maintainer: "Your name"
  version: "1.0.0"
  architecture_family: "aarch64"
  vendor: "community"
  # id: leave it out; the target id must match the folder name (see section 1)
```

### 4.2 capabilities

The block is the single source of truth for what the target can build. GUI, CLI, the build service, Ditto and the
MCP server derive their choices and their checks from it.

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board        # the board build.sh uses when none is given
  boards:
    my_board:
      label: "My Board (4x Cortex-A76)"
      cpu: "4x Cortex-A76"
      ram_max_gb: 8
      examples: ["My Board 8 GB"]
  formats:
    gguf:
      label: "GGUF (llama.cpp, CPU)"
      input: [hf]
      quantizations: [Q4_K_M, Q4_0, Q5_K_M, Q8_0, IQ2_XS]
      default_quantization: Q4_K_M
      imatrix: true
      needs_imatrix: [IQ2_XS]
      dataset: optional
      dataset_kind: text
      aliases: {INT8: Q8_0, Q4: Q4_K_M}
      notes: "Runs on the CPU."
```

Top level:

| Field | Meaning |
|---|---|
| `schema_version` | Always `1`. |
| `support_level` | `official` (maintained and tested by the project), `community` (contributed; works for its maintainers) or `experimental` (incomplete or unverified). Default: `official`. |
| `default_board` | The board `build.sh` uses when a request names none. Must be one of `boards` and must match the fallback in your `build.sh`. |
| `boards` | At least one board: id → `label` (required), `cpu`, `ram_max_gb` (greater than 0), `npu`, `gpu`, `examples`. |
| `formats` | At least one output format: id → the fields below. |

Per format:

| Field | Meaning |
|---|---|
| `label` | Name shown to users (required). |
| `input` | Accepted model inputs: any of `hf` (Hugging Face model), `onnx`, `gguf`. Default: `[hf]`. Ditto's build recommendations use it. |
| `quantizations` | All types this format accepts (at least one). |
| `default_quantization` | Used when a request names none; must be in `quantizations`. |
| `boards` | Boards that can run this format; omitted means every board of the target. |
| `board_quantizations` | Per-board subsets of `quantizations`, where a chip supports fewer types. |
| `board_default_quantization` | Per-board default; must be allowed on that board. |
| `imatrix` | `true` if the format can use an importance matrix (GGUF). |
| `needs_imatrix` | Types that are refused without an importance matrix. Requires `imatrix: true`. |
| `dataset` | Calibration data: `none`, `optional` or `required`. |
| `dataset_kind` | `text` (a text file) or `sample_list` (a list of sample inputs, as RKNN uses). |
| `dataset_required_for` | Types that need a dataset even if `dataset` is `optional`. |
| `aliases` | Other spellings your scripts accept, for example `{INT8: Q8_0}`; matched case-insensitively. Every alias must point to a listed type. |
| `notes` | Short hint for users and for Ditto. |

The schema refuses inconsistent blocks with a message, for example a default that is not in the list, a board
default that is not allowed on that board, unknown boards in `boards`, `board_quantizations` or
`board_default_quantization`, `needs_imatrix` without `imatrix: true`, or an alias that points to an unknown type.
The Rockchip block shows every field in use, including `board_quantizations` for RKLLM and `sample_list` for RKNN.

### 4.3 How a request is normalized and checked

Before a build starts, the values are normalized and then checked; the build runs with exactly the normalized
values:

1. Format, board and quantization are matched case-insensitively; a quantization may also be one of the aliases.
2. A missing board becomes the only board of the format, or else `default_board`.
3. A missing quantization becomes the board default, then the format default (or the first type allowed on the
   board).
4. Problems come back as sentences that list the allowed values.

With the Rockchip block:

| Request | Result |
|---|---|
| `-f gguf -b RK3566` | board `rk3566`, quantization `Q4_0` (board default) |
| `-f rkllm -q int4` | board `rk3588` (default board), quantization `w4a16` (alias), then refused: "Quantization 'w4a16' is not supported for rkllm on rk3588 (allowed: w8a8, w8a8_g128, w8a8_g256, w8a8_g512)." |
| `-f gguf -b rk3566 -q IQ2_XS` without IMatrix | refused: "IQ2_XS needs an importance matrix: enable IMatrix." |

### 4.4 Other blocks

The build pipeline reads only `metadata` and `capabilities`. An optional `hardware:` block (`cpu.architecture`,
`gpu.vendor`, `gpu.vendor_id`, `npu.vendor`, `npu.vendor_id`) is used in two places: to suggest a matching target
when a probe file is imported in the build tab, and for device options in the run commands of a deployment package
that includes Docker images. The other blocks in the Rockchip `target.yml` (`supported_boards`, `docker`,
`modules`, `optimization_profiles`, `requirements` and so on) are descriptive.

## 5. The Dockerfile

The build context the engine stages looks like this, so the Dockerfile copies from these paths:

```text
<context>/
├── Dockerfile           your Dockerfile (or Dockerfile.gpu)
├── modules/             your modules/
├── scripts/             your scripts/ (empty if you have none)
└── docker/entrypoint.sh the shared entrypoint.sh
```

Build arguments the engine passes:

| Argument | Value |
|---|---|
| `LLAMA_CPP_TAG` | the setting `llama_cpp_version` (default `v0.6.0`) |
| `BUILD_JOBS` | the setting `build_jobs`, only if it is greater than 0; self-healing lowers it to 1 after a compiler ran out of memory |
| `USER_ID`, `GROUP_ID` | the host user's ids (Linux and macOS, when the host user is not root) |

What the image must provide (see `targets/Rockchip/dockerfile`):

- `/app/modules/build.sh`, executable (`COPY modules/ ./modules/` and `chmod +x`).
- The shared entrypoint as `ENTRYPOINT ["/entrypoint.sh"]` and a user `llmbuilder`; when a container starts as
  root (legacy mode without sandbox), the entrypoint maps it to the host user and drops to `llmbuilder`.
- `ENV BUILD_CACHE_DIR=/build-cache` and every tool the conversion needs. Everything must be installed while the
  image is built, because the stages have no network.
- **Pinned versions:** a base image pinned by digest (official base images such as `debian:bookworm-slim` may use a
  tag, see [section 9.1](#91-what-the-validators-check)), git tags or commits, exact pip versions, and SHA256 checks
  for downloaded files (like `RKLLM_WHEEL_SHA256` for the RKLLM wheel).
- **One venv per toolchain** if their dependencies conflict (Rockchip: `/opt/venv`, `/opt/venv-rkllm`,
  `/opt/venv-rknn`).
- No binaries in the repository: download wheels and libraries in the Dockerfile and check their hashes.

## 6. The build script

`modules/build.sh` is the only entry point. It runs once per stage, as `/app/modules/build.sh`, in the sandbox
described in [section 7](#7-sandbox-rules).

### 6.1 Inputs

Environment variables set by the engine:

| Variable | Meaning |
|---|---|
| `JOB_TYPE` | `imatrix` (importance-matrix stage) or `build` |
| `MODEL_SOURCE` | path of the model inside the container (folder or file) |
| `MODEL_NAME` | file-name-safe model name, for output file names |
| `TARGET_FORMAT` | format id from the capabilities, for example `gguf` |
| `TARGET_BOARD` | board id, or empty (then use the same fallback as `default_board`) |
| `QUANTIZATION` | quantization type, already normalized; empty only for targets without capabilities |
| `TARGET_ARCH` | the target id |
| `MODEL_TASK`, `OPTIMIZATION_LEVEL`, `BUILD_ID` | task (default `LLM`), optimization level, build id |
| `MAX_CONTEXT_LENGTH` | only if set for the build |
| `USE_IMATRIX`, `IMATRIX_PATH` | build stage: `1` and the path of `imatrix.dat` if an importance matrix was computed |
| `DATASET_PATH` | calibration dataset, if the build uses one |
| `BUILD_SCRATCH_DIR`, `TMPDIR`, `HOME`, `XDG_CACHE_HOME` | writable work folder `/build-cache/work` and its sub-folders |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE` | `1` in the sandbox: libraries never try the network |
| `PUID`, `PGID` | the host user's ids (not on Windows) |

Mounts:

| Path in the container | Mode | Content |
|---|---|---|
| `/build-cache/output` | read-write (build stage) | where `build.sh` writes its results |
| `/build-cache/imatrix` | read-write in the imatrix stage, read-only in the build stage | `imatrix.dat` |
| `/build-cache/work` | read-write | scratch space for large intermediates |
| `/build-cache/input-model` | read-only | a local model folder or the folder of a local model file |
| `/build-cache/models` | read-only in the sandbox | Hugging Face downloads under `hf/<owner>__<name>` |
| `/build-cache/dataset.txt`, `/build-cache/dataset.json` or `/build-cache/dataset/` | read-only | calibration dataset (an RKNN sample list gets its folder); a dataset inside the model folder is reached through the model mount |

### 6.2 Outputs and exit code

- Write model files to `$BUILD_CACHE_DIR/output` (`/build-cache/output`). Files ending in `.gguf`, `.rkllm`,
  `.rknn`, `.onnx`, `.safetensors` or `.bin` count as model files; the largest file with the format's extension
  becomes the primary file that deployment installs.
- You may write `build_info.json` there with your own fields (Rockchip writes model, format, board, quantization
  and the llama.cpp tag); the engine adds the file list, hashes and build data.
- In the imatrix stage, write `$BUILD_CACHE_DIR/imatrix/imatrix.dat`.
- Exit with a non-zero code on any error and print the reason to stderr. The last log lines go to the user and to
  self-healing, so make them clear (`die "RKLLM needs an RK3588 or RK3576 NPU; ..."`).

### 6.3 Rules

- Start with `set -euo pipefail`.
- Lower-case format and board, normalize the quantization and accept exactly the aliases you declared in the
  capabilities. Refuse unsupported values with a message that lists what is supported.
- Use the same default board as `default_board`.
- Write large intermediates to `$BUILD_SCRATCH_DIR`, never into the image or a fixed `/tmp` path.
- Never download anything; the stage has no network.
- Keep the capabilities and the script in sync with tests, like `tests/test_foundation.py` and
  `tests/test_review_capabilities_paths.py` do for Rockchip.

### 6.4 Example

A minimal GGUF dispatcher for the capabilities in [section 4.2](#42-capabilities). It assumes that the image has
llama.cpp in `/usr/src/llama.cpp` with the tools built in `build/bin`, as the Rockchip image does. It passes
`bash -n` and ShellCheck; for F16/BF16 output, alias handling and more checks, see `targets/Rockchip/modules/build.sh`.

```bash
#!/bin/bash
# build.sh - dispatcher of the target "my_board" (run by the BuildEngine inside the target image)
set -euo pipefail

WORK_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="$WORK_DIR/output"
IMATRIX_DIR="$WORK_DIR/imatrix"
SCRATCH_DIR="${BUILD_SCRATCH_DIR:-$WORK_DIR}"   # large intermediates; the image filesystem is read-only
FORMAT="$(echo "${TARGET_FORMAT:-gguf}" | tr '[:upper:]' '[:lower:]')"
BOARD="$(echo "${TARGET_BOARD:-my_board}" | tr '[:upper:]' '[:lower:]')"   # same as default_board
QUANT="$(echo "${QUANTIZATION:-Q4_K_M}" | tr '[:lower:]' '[:upper:]')"
MODEL_NAME="${MODEL_NAME:-model}"
LLAMA_BASE="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -e "${MODEL_SOURCE:-}" ] || die "MODEL_SOURCE '${MODEL_SOURCE:-}' does not exist in the container."
case "$FORMAT" in
    gguf) ;;
    *) die "Format '$FORMAT' is not supported by this target (gguf)." ;;
esac
echo "Board: $BOARD, format: $FORMAT, quantization: $QUANT, job: ${JOB_TYPE:-build}"

f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
python3 "$LLAMA_BASE/convert_hf_to_gguf.py" "$MODEL_SOURCE" --outfile "$f16" --outtype f16

if [ "${JOB_TYPE:-build}" = "imatrix" ]; then
    [ -f "${DATASET_PATH:-}" ] || die "Calibration dataset not found at '${DATASET_PATH:-}'."
    mkdir -p "$IMATRIX_DIR"
    "$LLAMA_BASE/build/bin/llama-imatrix" -m "$f16" -f "$DATASET_PATH" -o "$IMATRIX_DIR/imatrix.dat" --chunks 100
    rm -f "$f16"
    exit 0
fi

imatrix_args=()
if [ "${USE_IMATRIX:-0}" = "1" ]; then
    [ -f "${IMATRIX_PATH:-}" ] || die "USE_IMATRIX=1 but '${IMATRIX_PATH:-}' is missing."
    imatrix_args=(--imatrix "$IMATRIX_PATH")
fi
mkdir -p "$OUTPUT_DIR"
"$LLAMA_BASE/build/bin/llama-quantize" "${imatrix_args[@]}" "$f16" "$OUTPUT_DIR/${MODEL_NAME}-${QUANT}.gguf" "$QUANT"
rm -f "$f16"
```

## 7. Sandbox rules

Conversion stages run model tooling and the target's build code, which may be generated by an AI. With the
default `build_sandbox: true`, every stage container runs:

- as the host user, not as root (1000:1000 if the host user is root or on Windows);
- **without network** (`network_mode: none`); the model and the dataset were staged on the host;
- with all Linux capabilities dropped and `no-new-privileges`;
- with a **read-only image filesystem**; `/tmp` is a 1 GB tmpfs;
- with a process limit (`build_pids_limit`, default 4096) and, if set, a memory limit (`build_memory_limit`, for
  example `24g`);
- with only the build's own folders writable (work folder, output, and the importance matrix in its stage); model,
  cache and dataset mounts are read-only.

With `--gpu`, NVIDIA GPUs are passed through if `nvidia-smi` exists on the host; otherwise `/dev/dri` is passed and
the container user joins the groups that own the render devices.

What this means for your target: install everything in the Dockerfile; do not write into the image; do not rely on
root, `sudo` or a fixed user id; keep large files in `$BUILD_SCRATCH_DIR`; read the model and the dataset only
through the given paths. `build_sandbox: false` switches back to the old mode (root plus entrypoint); use it only to
find out whether a failure is caused by the sandbox.

## 8. Testing a target

1. **Is the target loaded and are its capabilities valid?** `llm-cli doctor` reports the code
   `targets.<id>.capabilities` if the block is missing or invalid; the error itself is in the log. To see the schema
   error directly:

   ```bash
   python -c "import yaml; from orchestrator.schemas.targets import TargetCapabilities as T; \
   T.model_validate(yaml.safe_load(open('targets/my_board/target.yml', encoding='utf-8'))['capabilities']); print('ok')"
   ```

   `llm-cli targets validate <id>` only confirms that `target.yml` was loaded; it does not check the capabilities.
2. **Does the image build?** Stage the context the same way the engine does and build it:

   ```bash
   python3 -m orchestrator.utils.build_context targets/my_board /tmp/my_board-context
   docker build -t llm-framework/my_board:dev /tmp/my_board-context
   ```

3. **A real build with a small model:**

   ```bash
   llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t my_board -f gguf -b my_board -F
   ```

   Check the new folder in `output/`: the model file and `build_info.json` with `artifacts` and `primary`.
4. **Lint:** ShellCheck runs on every tracked `.sh` file outside `targets/_template`
   ([CONTRIBUTING, section 5](../CONTRIBUTING.md#5-lint-and-security-checks)). Add your Dockerfile to the hadolint
   matrix in `.github/workflows/docker.yml` and to `.github/dependabot.yml`.
5. **Tests:** add tests that keep your capabilities and your scripts in sync (see the Rockchip tests named in
   [section 6.3](#63-rules)).
6. **On the device:** deploy the build (deployment window, or `llm-cli deploy package` and `llm-cli deploy run`)
   and start the model there.
7. **Ready to share?** Run the validators of [section 9.1](#91-what-the-validators-check) on the folder; `ok` means
   no problem:

   ```bash
   python -c "from pathlib import Path; from orchestrator.Core.module_generator import validate_target_folder as v; \
   print('\n'.join(v(Path('targets/my_board'))) or 'ok')"
   ```

## 9. Sharing a target

There are two ways:

- **As a community pack**, for a target you maintain yourself: export it as a signed pack and submit it to the
  community repository; users install it with `llm-cli zoo install` or the Community Hub. Keep
  `support_level: community`.
- **In this repository**, if the maintainers take the target over: open a pull request with the target folder and
  describe which boards and models you tested. Follow the [contribution rules](../CONTRIBUTING.md): no binaries, no
  secrets, pinned versions, and documentation in English and German.

Either way the target has to pass the validators below. Copying target folders by hand into `community/` is no
longer a way to install them.

### 9.1 What the validators check

`validate_target_folder` in `orchestrator/Core/module_generator.py` checks a target folder that others will build.
It runs when the module generator finishes a target, when you export a target (`llm-cli zoo export target` or the
export dialog), when a target pack is installed (in the quarantine folder) and in
`llm-cli zoo verify <pack file>`, which the community repository's CI runs. Each problem is reported with its file
and, for scripts, its line.

| Check | Rule |
|---|---|
| Capabilities | `target.yml` exists and has a `capabilities:` block the schema accepts ([section 4.2](#42-capabilities)). |
| Pinned images | In every file whose name starts with `dockerfile` in any case (`Dockerfile`, `dockerfile`, `Dockerfile.gpu`), every image that `FROM`, `COPY --from=` or `RUN --mount=...,from=` uses must be pinned by digest (`name@sha256:` and 64 hex digits). Exempt are official base images with a tag (names starting with `debian:`, `ubuntu:`, `nvidia/cuda:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` or `python:`), an earlier build stage (`FROM ... AS builder`, then `COPY --from=builder`) and a stage number (`--from=0`). Comment lines are skipped. A variable such as `FROM ${BASE_IMAGE}` is refused, because it is not an image reference. |
| Shell deny-list | Every line of every `*.sh` file, comments included, unless the same line is in the template's file with the same path (for `modules/build.sh`: `targets/_template/modules/build.sh`). Refused: downloads (`curl`, `wget`, `aria2c`, and `fetch` as a word, which includes `git fetch`), deleting `/` (`rm -rf /`), `chmod 777`, `sudo`, `mkfs` and `dd if=`, the Docker socket or `docker run`, `eval`, `source` or `.` of a `$(...)` result, `sh -c "$(...)"`, and process substitution `<(...)` except as plain input (`done < <(grep ...)`). Downloads belong in the Dockerfile, pinned. |
| Shell syntax | Every `*.sh` file passes `bash -n` (skipped where bash is not installed). |
| Text only | No file contains a NUL byte: binary files are refused. On export every file must also be UTF-8 text. |
| Placeholders | No file except the knowledge snapshot `knowledge/initial_knowledge.json` (crawled pages) contains a template placeholder (`[MODULE_NAME]`, `[Hardware-Familie]`, `[IHRE_ARCHITEKTUR]`, `[Hersteller]`, `[QUANTIZATION_LOGIC]`, `[CPU_FLAGS]`, `[CMAKE_FLAGS]`, `[SDK_SETUP_COMMANDS]`, `[PACKAGING_COMMANDS]`, `[ihr-target-name]`, `{packages_str}` or `YYYY-MM-DD`), and no value in `target.yml` holds text in square brackets, such as `[Board Name 1]`. |

The pack format adds its own rules when a target is exported or installed:

- No symbolic links, junctions or special files anywhere in the folder.
- Paths are relative and `/`-separated, without `..`, backslashes, drive letters, the characters `: * ? " < > |`,
  control characters or a trailing dot or space, and they are unique regardless of case.
- At most 10,000 files and 512 MB. Files keep only the modes 0644 or 0755 (executable or not).
- `pack.json`, `pack.sig` and `.lcf-provenance.json` are reserved names at the top of the folder.
- The community repository's CI accepts packs of at most 5 MB, and `llm-cli zoo verify` refuses a pack in which the
  privacy scrub would still change something.

The shipped Rockchip target passes all of these checks.

### 9.2 Exporting a target for the community

1. **Finish the target:** test it with a real build ([section 8](#8-testing-a-target)), keep
   `support_level: community` and set `metadata.version` in the form `1.2.3` (otherwise the pack gets `1.0.0`).
2. **Export it.**
   - In the GUI, for a new target: the check box "Export for community after creating (a scrubbed, signed pack for
     a pull request)" on the wizard's last page opens the dialog "Export target for the community" after the
     target was created. Enter an author name (a pseudonym is fine) and a licence (MIT unless you choose another),
     then click "Create pack". "Show folder" opens the folder with the pack.
   - On the command line, for any target of your own: `llm-cli zoo export target my_board --author "<your name>"`.

   The pack is written to `community/contributions/contribution-target-my_board-1.0.0.zip` and signed with your
   key, which is created in the secrets store on first use.
3. **Read the summary.** It shows a diff for every file the privacy scrub changed (home folders, IP and MAC
   addresses, e-mail addresses, host and user names, keys; at most 40 lines per file) and notes, for example that
   `knowledge/` holds pages from the web whose terms the pack licence does not cover. If a change breaks a script,
   fix the file in your target and export again.
4. **Check it the way the community repository will:**
   `llm-cli zoo verify community/contributions/contribution-target-my_board-1.0.0.zip`.
5. **Submit it:** "Open pull request" in the dialog, or the export command with `--pr` added. What happens then,
   and the current state of the community repository, is described in
   [CONTRIBUTING](../CONTRIBUTING.md#contributing-to-the-community-zoo).

To share a target only with your own machines or a team, encrypt it instead: `--to <key file>` or `--passphrase`
([CLI guide](CLI_Usage.md#zoo-export)). The recipient installs it with `llm-cli zoo install --file`. A target you
installed from the zoo cannot be exported as your own: it remains its author's pack.

## 10. Legacy files

Version 1.x used a chain of four scripts per target (`source_module.sh`, `config_module.sh`, `convert_module.sh`,
`target_module.sh`) driven by the hardware probe. The current pipeline calls only `build.sh`; the Rockchip
`build.sh` does not use these scripts, and the module generator still writes them. Their removal and an updated
template are planned for 3.4.0.
