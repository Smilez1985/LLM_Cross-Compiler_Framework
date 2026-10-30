Version 3.3.0 · Last updated 2026-10-30

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
6. [The build stages](#6-the-build-stages)
7. [Sandbox rules](#7-sandbox-rules)
8. [Testing a target](#8-testing-a-target)
9. [Sharing a target](#9-sharing-a-target)
10. [What the stage modules kept from version 1.x](#10-what-the-stage-modules-kept-from-version-1x)
11. [The hardware probe file](#11-the-hardware-probe-file)

## 1. How the framework uses a target

1. **Loading.** At start, the `TargetManager` reads `target.yml` in every folder below `targets/`. Folders whose
   name starts with `_` or `.` (such as `_template`) are skipped, and so is a `target.yml` without a `metadata:`
   block. The target id is `metadata.id` if set, otherwise the folder name. The CLI, the build service, the
   orchestrator and the build engine resolve a target through one id -> folder map, so `llm-cli build start -t`
   takes the id or the folder name (in any letter case) and the build uses the right folder either way.
2. **Capabilities.** The `capabilities:` block is validated against the schema in `orchestrator/schemas/targets.py`.
   The GUI offers exactly the formats, boards and quantizations listed there, the CLI refuses anything else, and the
   tools of the MCP server and Ditto start builds only for targets that have a valid block. An invalid block is
   logged as an error, and the target is then treated as if it had none.
3. **Image build.** For each build the `BuildEngine` stages a build context (the target's `modules/` and
   `scripts/`, the shared `entrypoint.sh` as `docker/entrypoint.sh`, and the Dockerfile) and builds the image
   `llm-framework/<target>:<build id>`. This step has network access.
4. **Stages.** The model is staged on the host first. Then `modules/build.sh`, the stage runner, runs in sandboxed
   containers without network: once with `JOB_TYPE=imatrix` when an importance matrix is requested for GGUF, then
   with `JOB_TYPE=build`. It runs the target's four stage modules in this order: `source`, `config`, `convert`
   and `target` ([section 6](#6-the-build-stages)); the imatrix job stops after `convert`.
5. **Results.** Files that the stages write to the output folder are copied to `output/`. The engine completes
   `build_info.json` with every model file's size and SHA256 hash, writes `Model_Card.md` and a ZIP. Deployment
   finds builds through this manifest.

## 2. Folder layout

```text
targets/Rockchip/
├── target.yml           metadata and capabilities
├── dockerfile           build environment ("Dockerfile" works as well)
├── modules/
│   ├── build.sh           the stage runner: the entry point of every stage container
│   ├── source_module.sh   1. source: the toolchain sources of the image
│   ├── config_module.sh   2. config: request checks and the build plan
│   ├── convert_module.sh  3. convert: the model conversion
│   ├── target_module.sh   4. target: quantization and the results
│   ├── rkllm_module.sh    RKLLM export (RK3588, RK3576), a format helper of the convert stage
│   └── rknn_module.sh     RKNN conversion, a format helper of the convert stage
└── scripts/
    ├── export_rkllm.py
    └── rknn_converter.py
```

| File | Needed | What it is for |
|---|---|---|
| `target.yml` with a `metadata:` block | yes | Without it the folder is not a target. |
| `capabilities:` block in `target.yml` | strongly recommended; required to share the target | Without it the GUI and CLI cannot check settings, the MCP tools and Ditto cannot start builds for the target, `llm-cli mojo` warns, and the target cannot be exported or installed as a community pack. |
| `Dockerfile` or `dockerfile` | yes | The image all stages run in. |
| `Dockerfile.gpu` | optional | Used for builds with GPU passthrough (`llm-cli build start --gpu`, or "Use Host GPU for Build Process" in the GUI, which is checked by default on Linux); without it the normal Dockerfile is used. |
| `modules/build.sh` | yes | The stage runner; runs in every stage container as `/app/modules/build.sh`. Copy it from the template unchanged. |
| `modules/source_module.sh`, `config_module.sh`, `convert_module.sh`, `target_module.sh` | yes | The four stages of the build ([section 6](#6-the-build-stages)). |
| `modules/rotate_module.sh` | optional | A rotation step between convert and target (roadmap); runs only when the file exists. |
| `modules/benchmark_module.sh` | optional | Never run by a build: it measures the build host, and a model for a board is measured on the board. |
| `scripts/` | optional | Helper scripts; copied into the image next to `modules/`. |

## 3. Creating a target

### 3.1 Probe the board

The probe runs on the device, because the build is planned from its result. Over SSH:

```bash
llm-cli scan --device 192.0.2.10 --user dietpi
```

The CLI checks the device's host key the first time (as deployment does), sends `hardware_probe.py` to `python3 -I`
when the device has Python 3.8 or newer and `hardware_probe.sh` otherwise, writes `target_hardware_config.txt` and
keeps the profile under the device's ID ([section 11.5](#115-where-profiles-are-kept)). Nothing is installed on the
device. Without SSH, copy a probe to the device, run it there and bring the file back:

```bash
python3 -I hardware_probe.py                                 # Python 3.8 or newer
sh hardware_probe.sh                                         # Linux without Python, any POSIX shell
powershell -ExecutionPolicy Bypass -File hardware_probe.ps1  # Windows without Python
```

The file holds the operating system (ID, version, family, kernel, init system), the architecture, the C library and
its version, CPU model, cores, clusters and features, memory, SoC, NPU and GPUs; no host name, serial number or MAC
address. The probes ask nothing and install nothing; `hardware_probe.sh --install-tools` offers to install
`vulkan-tools` first and `hardware_probe.ps1 -Elevate` restarts with administrator rights. The format is described
in [section 11](#11-the-hardware-probe-file).

In the GUI build tab, **Scan / Import... > Remote Scan via SSH...** runs the shell probe on a device over SSH (with
the same trust-on-first-use check) and fetches the result. `llm-cli scan` without `--device` probes the computer you
are working on.

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
  `riscv64`), SDK, base image, description, packages and CPU flags (advisory, see
  [section 3.4](#34-what-the-generator-writes-and-what-you-add)). With `--probe <probe file>` (the profile
  `llm-cli scan --device` writes) the module's `runtime:` block is planned from the board's probe; a file that is not
  a probe profile is refused. Run it from the installation folder: unlike the other commands, it resolves a relative
  `targets_dir` against the current folder.
- **CLI with Ditto:** `llm-cli module generate-ai target_hardware_config.txt` (optionally `--provider` and
  `--model` for this run). Ditto returns a structured proposal (architecture, SDK, base image, packages, CPU and
  CMake flags, setup commands, quantization block). If the knowledge base is enabled, it adds matching
  documentation excerpts first. The proposal is printed in full; nothing is written until you confirm. Spaces in
  the module name become underscores. When the probe file is a probe profile, the `runtime:` block is planned from
  it, as with the wizard's imported probe file.

### 3.3 What the generator checks

Every value ends up in a Dockerfile, YAML or a shell script, so it is checked before any template is filled in:

- **Name:** 1 to 64 letters, digits, spaces, `_`, `.` or `-`, starting with a letter or digit. The folder name is
  the lower-case name with underscores (`My Board` becomes `targets/my_board/`).
- **Architecture:** one of `aarch64`, `x86_64`, `armv7l`, `riscv64`.
- **Base image:** must be pinned by digest (`image@sha256:...`) unless it starts with `debian:`, `ubuntu:`,
  `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` or `python:`. NVIDIA's prebuilt CUDA images are not exempt: for the GPU
  template an `nvidia/cuda:<version>-devel-ubuntu22.04` image pinned by digest is the explicit opt-in described in
  [section 5.2](#52-cuda-in-the-gpu-template); any other `nvidia/cuda` image is refused. A prebuilt llama.cpp image
  is refused as well.
- **Packages:** valid Debian package names. **Flags:** plain compiler or CMake options such as
  `-mcpu=cortex-a76` or `-DGGML_CUDA=ON`. They are advisory: the module keeps them in its `runtime:` block and no
  build ever applies them. The CMake flags may not switch on a llama.cpp option that downloads while it is built
  (`-DLLAMA_USE_PREBUILT_UI=ON`, `-DLLAMA_CURL=ON`, `-DGGML_CUDA_CCCL_VERSION=...`,
  `-DFETCHCONTENT_FULLY_DISCONNECTED=OFF`) or gives `llama-server` subprocesses (`-DLLAMA_SUBPROCESS=ON`): builds
  have no network, and the board package of a hardware profile given by hand takes these flags. They are refused
  with the build recipe's reason (`refused_option` of `orchestrator/runtime/build/recipe.py`, the rule every build
  plan follows). Nor may they make CMake run a script (`-C`, an initial cache, or `-P`): a script can set any
  option.
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
`requirements`, a `capabilities:` block and a `runtime:` block), a `Dockerfile` (from `Dockerfile.gpu` of the
template if the SDK contains "cuda" or the base image contains "nvidia"), the stage runner `modules/build.sh`, the four
stage modules `modules/source_module.sh`, `config_module.sh`, `convert_module.sh` and `target_module.sh` (with the
module's name, its architecture and its base image in their headers), `modules/rkllm_module.sh`,
`modules/rknn_module.sh`, `modules/benchmark_module.sh`, `generate_profile.sh` and the template's `scripts/`. The
fields of the wizard go into the stage they belong to: the setup commands into the source stage, the quantization
logic into the convert stage and the packaging commands into the target stage.

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

**The runtime block.** The hardware probe runs on the target, and the build recipe
(`orchestrator/runtime/build/recipe.py`) derives from it how the board's llama.cpp runtime (`llama-server`,
`llama-cli`, `llama-bench`, `llama-perplexity`) is built from the pinned commit. The generator writes a summary of
that plan into `target.yml`. With the probe of a Radxa ZERO 3W (RK3566, DietPi) and the flags a wizard suggested it
looks like this:

```yaml
runtime:
  schema_version: 1
  built_from: source
  recipe: orchestrator/runtime/build/recipe.py
  purpose: device-runtime
  arch: arm64
  os: linux
  profile_perf_key: 2beb8057d73cf1dcc0a5db4f830650d61814ef9b953159f42733c224929fd833
  libc: glibc
  libc_min: glibc-2.36
  placement: cross
  builder_base: debian-12
  cpu: explicit
  cpu_arm_arch: armv8.2-a+dotprod+fp16
  backends: [cpu]
  targets: [llama-server, llama-cli, llama-bench, llama-perplexity]
  advisory:
    cpu_flags: -mcpu=cortex-a55
    cmake_flags: -DGGML_NEON=ON
    note: "proposed in the module wizard or by an AI; never applied: ..."
```

- **Where the probe comes from:** the probe file imported in the wizard, the probe file of `module generate-ai`, or
  `llm-cli module create --probe <file>`. Without one, the block has the architecture, `backends: [cpu]` and a note
  that the runtime is planned from the board's probe when it is built (`llm-cli scan --device`).
- **What the fields mean:** `libc_min` is the C library the device has, so the cross build uses a builder base that
  is not newer (`builder_base`, from `build_bases` in `configs/llama_runtime.yml`); `cpu_arm_arch` (ARM) or
  `cpu_features` (x86) are the CPU options from the probe, `cpu: all-variants` means the probe could not tell and
  every CPU variant is built, `cpu: native` a build on the device itself; `backends` the builds worth making (the CPU
  build first); `profile_perf_key` identifies the probe for benchmark results; `notes` say what the recipe left out
  and why.
- **A record, not an input:** the device runtime is always planned again from the device's own probe when it is
  built, so editing the block, or a block in a community pack, changes no build. A pack's block must still match
  the schema (`TargetRuntime` in `orchestrator/schemas/targets.py`).
- **Flags are advisory:** CPU and CMake flags from the wizard or Ditto's proposal stay readable under `advisory` and
  are never applied by a build; `config_module.sh` uses them only for a hardware profile given by hand (section
  [10](#10-what-the-stage-modules-kept-from-version-1x)).

**Knowledge snapshot (opt-in).** On the wizard's last page, the check box "Attach the knowledge crawled for this
target (Deep Ingest, privacy scrub applied)" is off by default and can only be switched on after Deep Ingest ran in
this wizard. Then the generator exports only the pages crawled from the URLs you entered in this wizard,
privacy-scrubbed (home folders, addresses, host and user names, keys), to `knowledge/initial_knowledge.json`, with a
`README.md` next to it that says what the file holds. While a Deep Ingest is still running, Finish stays disabled,
so the snapshot holds every crawled page; if no stored page comes from those URLs, the wizard says that no snapshot
was attached. Nothing else from your knowledge base goes into the module, and without
the check box there is no `knowledge/` folder. `llm-cli module create` and `generate-ai` never attach knowledge.
The framework does not import that file into a knowledge base, and the generator's placeholder check leaves it out
(crawled pages may well contain text like `YYYY-MM-DD`). The pages remain under their owners' terms; the licence of
a pack does not cover them. Pages kept in full under a licence that asks for its text or permission notice (MIT, BSD,
ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA) get the canonical licence text in `knowledge/LICENSES/<SPDX id>.txt` and a
`knowledge/NOTICE` that lists every page with its source address, copyright line and licence; the placeholder check
leaves these out too, and an export makes them anew from the snapshot.

On your computer the snapshot is not imported: its pages are in your knowledge base already. When you export the
target as a pack, the snapshot travels with it, and the recipient's framework checks it at the install like a
knowledge pack (a list of pages, each with its source, and its licence files) and imports it into their knowledge base
under this target, with a snapshot of their knowledge base first; a newer version of your target pack replaces the
excerpts of the older one there.

The result is a **skeleton**, not a working target yet:

- **The capabilities block is a starting point.** Add your real boards, other formats, per-board quantizations and
  calibration needs ([section 4](#4-targetyml-and-the-capabilities-block)), and keep the block in sync with
  `config_module.sh` (format, board and type names).
- **The template's stages follow the Rockchip stages** (`targets/Rockchip/modules/`), and its `build.sh` is the same
  stage runner: llama.cpp's `convert_hf_to_gguf.py` and `build/bin/llama-quantize` / `llama-imatrix` below
  `LLAMA_CPP_PATH` (`/usr/src/llama.cpp`), the F16 intermediate in the work folder (`BUILD_SCRATCH_DIR`, never
  `/tmp`), the same GGUF type lists and importance-matrix rules, and the `imatrix` job. The branches of your
  quantization block run first, in the convert stage; a branch can call `build_gguf <TYPE>` or
  `convert_to_gguf <outtype> <file>`, and every other type goes through `build_gguf`, which refuses types
  llama-quantize does not know. `build_gguf` leaves the quantization to the target stage. Nothing is copied through
  unchanged, and a build that writes no artifact fails.
- **Setup commands run once,** in the source stage. Each stage is its own process: what the commands export reaches
  the convert and target stages (through `sdk_environment.sh` in the stage folder, also when a build resumes), a
  variable they only set does not.
- **The template Dockerfile builds llama.cpp's conversion tools** with the same steps as
  `targets/Rockchip/dockerfile` ([section 5.1](#51-llamacpp-in-the-image-recipes-not-binaries)): the pinned commit
  in `/usr/src/llama.cpp`, `llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` and
  `llama-bench` tuned to the build host, and the converter's requirements. Keep these steps when you change the
  Dockerfile.

### 3.5 CMake flags of a module

A module's CMake flags (`cmake_flags` of the wizard or of an AI's proposal, written as `CMAKE_OPTIONS` into
`modules/config_module.sh`, and a Dockerfile's `LLAMA_CMAKE_ARGS`) follow one verdict, `cmake_flag_verdicts` of
`orchestrator/runtime/build/recipe.py`. The module generator, the folder check, the build plan and the package's last
check in the build container (`module_flag_verdicts` of `target_module.sh`) give the same verdicts
(`tests/test_module_flags.py` runs both over `tests/data/cmake_flag_vectors.json`).

- **Allowed:** `-DNAME=VALUE`, also `-D NAME=VALUE` and `-DNAME:TYPE=VALUE`, and CMake's own warning words (`-Wdev`,
  `-Wno-dev`, `-Werror=dev`, `-Wdeprecated`, `-Wno-deprecated` and their `error=` forms).
- **Never:** what makes CMake load code or another toolchain (`CMAKE_PROJECT_INCLUDE`, `CMAKE_PROJECT_INCLUDE_BEFORE`,
  `CMAKE_PROJECT_<name>_INCLUDE`, `CMAKE_PROJECT_TOP_LEVEL_INCLUDES`, `CMAKE_TOOLCHAIN_FILE`, `CMAKE_MODULE_PATH`,
  `CMAKE_USER_MAKE_RULES_OVERRIDE*`, `-C`, `-P`, `--preset`, `--toolchain`), what makes CMake or the build run
  another program (`CMAKE_<LANG>_COMPILER_LAUNCHER`, `CMAKE_<LANG>_LINKER_LAUNCHER`, `CMAKE_<LANG>_CLANG_TIDY`,
  `_CPPCHECK`, `_CPPLINT`, `_INCLUDE_WHAT_YOU_USE`, `_ICSTAT`, `CMAKE_LINK_WHAT_YOU_USE_CHECK`,
  `CMAKE_CROSSCOMPILING_EMULATOR`, `CMAKE_MAKE_PROGRAM`; for ccache use `GGML_CCACHE`), the source and build folders (`-S`,
  `-B`: the build plan sets them), the `FETCHCONTENT_*` variables (only the framework's dependency cache sets them),
  a `CMAKE_PREFIX_PATH` that names anything but absolute folders inside the build container, an option that would let
  llama.cpp download, and every word that is not a CMake option.
- **The person decides:** a valid CMake switch outside the allowlist (`-G`, `-T`, `-A`, `-U`, `--fresh`,
  `--log-level`, `--log-context`, `--debug-output`, `--debug-find`, `--trace`, `--trace-expand`,
  `--warn-uninitialized`, `--no-warn-unused-cli`, `--check-system-vars`, `--compile-no-warning-as-error`,
  `--install-prefix`) is no refusal. When the module is built, the Builder (or `llm-cli build start` at a terminal)
  shows what each option does and a recommendation for the model and quantization being built, from the target's
  recipe plan for that hardware; the person allows the options or goes on with the standard flags (the options are
  left out of the package's cross build). The decision can be remembered for this module version and flag set
  (`--remember-flags`, or the check box of the question): a new version or another flag set asks again. Only the
  person decides: an AI-started build stops with `needs_person` (blocker `module_flags_undecided`) unless the person
  remembered a decision. Without a terminal, `--module-flags allow` or `--module-flags standard` gives the decision.
- **Fresh cache, no toolchain from the environment:** every cmake call of a module's own scripts (build.sh's `cmake`)
  starts a configure run with `--fresh` (when the image's CMake knows it), so a `CMakeCache.txt` left in a build
  folder sets nothing, and runs without a `CMAKE_TOOLCHAIN_FILE` environment variable unless the build plan set
  exactly that file. The folder check reads the flags as written: a word the shell expands (`$X`) is a limit of that
  check; build.sh's cmake and the package's last check see the expanded words.
- **Download options with a fetched dependency:** `-DLLAMA_BUILD_BORINGSSL=ON`, `-DLLAMA_BUILD_LIBRESSL=ON` and
  `-DGGML_CPU_KLEIDIAI=ON` would make llama.cpp download BoringSSL, LibreSSL or KleidiAI while it is built. In a
  module's flags they are allowed, because the framework fetches that dependency before the build
  (`orchestrator/runtime/build/deps.py`): outside the sandbox, never in offline mode, pinned exactly as the pinned
  llama.cpp declares it (a git tag, or an archive checked against the MD5 llama.cpp names), with the sha256 of its tree
  recorded on the first fetch and checked at every later use. It is kept read-only in a dependency cache below the
  framework's cache folder and mounted read-only into the build (`LLMCF_DEPS`); the cross build points
  `FETCHCONTENT_SOURCE_DIR_<NAME>` at it and keeps `FETCHCONTENT_FULLY_DISCONNECTED=ON`, so the build itself still
  downloads nothing. In offline mode a dependency that is not in the cache stops the build with what is missing. The
  build plan lists each dependency with its source, pin and size, so an AI-started build shows the download in the
  question the person answers. `llm-cli build deps list|fetch|delete` (Build > Build Dependencies... in the GUI)
  lists, fetches and deletes the cache's entries. Other download options (CUDA CCCL, ZenDNN, llguidance, the virtual
  GPU header, OpenMP on Windows) have no pinned source and stay refused.

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
  # id: optional; without it the folder name is the target id (see section 1)
```

### 4.2 capabilities

The block is the single source of truth for what the target can build. GUI, CLI, the build service, Ditto and the
MCP server derive their choices and their checks from it.

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board        # the board the config stage uses when none is given
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
| `default_board` | The board the build uses when a request names none. Must be one of `boards` and must match the fallback in your `config_module.sh`. |
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

**Formats follow the board's probe.** After the capabilities, one verdict (`format_refusal` in
`orchestrator/Core/build_formats.py`) checks the format against the board's hardware probe (the one the request names,
else the newest stored probe of a board of its SoC) and the build computer; the build service refuses with that
sentence, and the Builder tab offers only the formats left (`probe_formats`):

- An NPU format (RKLLM, RKNN) only when the probe shows the Rockchip NPU (`SUPPORTS_RKNN=ON`); without it GGUF, which
  runs on the board's CPU.
- RKLLM only for the RK3588, RK3576, RK3562 and RV1126B (RKLLM-Toolkit 1.3.1); an RK3566 or RK3568 is refused with the
  reason.
- RKLLM only on an x86_64 build computer (RKLLM-Toolkit 1.3.1 ships Linux x86_64 wheels only); on an ARM build
  computer it is refused with the reason and GGUF offered. GGUF and RKNN (rknn-toolkit2 2.3.2 has aarch64 wheels)
  convert natively there.

### 4.4 Other blocks

The build pipeline reads `metadata`, `capabilities` and, for the choices of a build, `optimization_profiles`. An optional `hardware:` block (`cpu.architecture`,
`gpu.vendor`, `gpu.vendor_id`, `npu.vendor`, `npu.vendor_id`) is used in two places: to suggest a matching target
when a probe file is imported in the build tab, and for device options in the run commands of a deployment package
that includes Docker images. The `runtime:` block of generated modules
([section 3.4](#34-what-the-generator-writes-and-what-you-add)) is a record of the recipe's plan and is checked
against its schema, but no build reads it. The other blocks in the Rockchip `target.yml` (`supported_boards`,
`docker`, `modules`, `requirements` and so on) are descriptive: `modules` lists the stage runner, the four stage
modules and the format helpers, and `docker.build_args` only repeats the pin for builds outside the framework.

**`optimization_profiles`** (schema `OptimizationProfile` in `orchestrator/schemas/targets.py`, read by
`orchestrator/Core/board_profiles.py`): one entry per board id of the capabilities block.

| Field | Side | Meaning |
|---|---|---|
| `arch` | source | `arm64` or `x86_64` |
| `cpu` | source | the cores as GCC's `-mcpu` names them (`cortex-a76.cortex-a55` for big.LITTLE), for the reader |
| `cpu_features` | source | what the board's probe reports as `SUPPORTS_<NAME>`: `neon`, `dotprod`, `fp16`, `i8mm`, `sve` (ARM) or the x86 names (`avx2`, `fma`, ...) |
| `cmake_options` | source | llama.cpp's CMake options the build recipe plans for a cross build of this SoC, e.g. `GGML_NATIVE: "OFF"`, `GGML_CPU_ARM_ARCH: "armv8.2-a+dotprod+fp16"` |
| `cpu_flags` | source | the compiler flags of that plan, e.g. `-march=armv8.2-a+dotprod+fp16` |
| `memory_profile` | target | `low_memory` (1-4 GB, models up to about 1.5B at 4 bit), `medium_memory` (4-8 GB, up to about 4B), `high_memory` (8 GB and more, up to about 8B) |
| `optimization` | target | the optimization level a build starts from when none is given (`fast`, `balanced`, `size`, `speed`, `aggressive`) |
| `recommended_quantization` | target | the quantizations the Builder, `llm-cli build start` and AI clients offer first; each must be one a format of the board accepts |
| `note` | - | optional text for the reader |

The build recipe (`orchestrator/runtime/build/recipe.py`) stays the one place that decides llama.cpp's options and
the compiler flags: the source side must be exactly what it plans for a probe with these CPU features.
`llm-cli targets validate` plans that build and names every value that differs; it also refuses `-mfpu` and
`-mfloat-abi` on arm64 (AArch32 options), `GGML_NATIVE` other than OFF (a build for a board is a cross build), a
`native` flag and any option that downloads. The module generator writes a profile per board from the board's probe
(without one: generic ARMv8-A with NEON, or x86-64).

**`performance_benchmarks`** (schema `BenchmarkEntry`): a list of rows with `model`, `board`, `quantization`, the
values `gen_ts` and `prompt_ts` (tokens per second while generating and for the prompt, llama-bench `-n 128` and
`-p 512`), `first_token_ms`, `memory_usage_mb`, `cpu_usage_percent`, and a `source`: `kind: measured` (with
`board_label` and `date`: measured by llmcf on that board), `kind: published` (with `url` and `date`) or
`kind: unmeasured` (an estimate; shown as "unmeasured", never as a measurement). A board test fills `gen_ts` and
`prompt_ts` (`device_tests.json` of the build). `llm-cli targets boards`, Build > Targets... > Boards..., the
Builder's board line and the MCP tools `list_targets` and `get_target_capabilities` show the rows with that mark.
A `published` source is a plain https address (no user name or password in it).

A `target.yml` of an earlier version (a community pack, a target an earlier module wizard made) keeps passing
`llm-cli targets validate`: a profile in the old format (a `cmake_flags` line instead of `arch` and
`cmake_options`) and benchmarks as a mapping by model and board are never read, so none of their values is used or
shown. Rewrite them in the format above to give the boards a profile.

`docker.image_name`, `docker.registry` and `docker.tags` are **local image names only**. No framework image is
published or pulled: the build engine builds each target's image on your computer and tags it locally as
`llm-framework/<target>:<build id>`, and nothing reads `registry` or `tags` (the `ghcr.io` and `latest`/`v1.0.0`
values in the Rockchip file and the template are leftovers that only describe a name). Do not push target images to
a registry from the framework, and do not rely on these keys for anything.

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
| `LLAMA_CPP_TAG`, `LLAMA_CPP_COMMIT` | the llama.cpp pin in `configs/llama_runtime.yml` (`b11429`, `d81235049384534c167caea52b85a694f6103d14`). The setting `llama_cpp_version` cannot move them: a different value only gives a warning in the build log |
| `LLAMA_CMAKE_ARGS` | only when the Dockerfile declares `ARG LLAMA_CMAKE_ARGS`: the build recipe's plan for the conversion tools on this machine, as `-DNAME=VALUE` words ([section 5.1](#51-llamacpp-in-the-image-recipes-not-binaries)) |
| `BUILD_JOBS` | the setting `build_jobs`, only if it is greater than 0; self-healing lowers it to 1 after a compiler ran out of memory |

The three llama.cpp arguments come only from the pin and the recipe: build arguments a build request carries
cannot replace them. The host user's ids are not build arguments (since 3.3.0): the image keeps the Dockerfile's
default build user, and the stage containers run as the host user (the sandbox's `--user`, or the entrypoint's
`PUID`/`PGID` mapping), so an exported image works on another computer.

What the image must provide (see `targets/Rockchip/dockerfile`):

- `/app/modules/build.sh` and the stage modules next to it, executable (`COPY modules/ ./modules/` and `chmod +x`).
- The shared entrypoint as `ENTRYPOINT ["/entrypoint.sh"]` and a user `llmbuilder`; when a container starts as
  root (legacy mode without sandbox), the entrypoint maps it to the host user and drops to `llmbuilder`.
- `ENV BUILD_CACHE_DIR=/build-cache` and every tool the conversion needs. Everything must be installed while the
  image is built, because the stages have no network.
- **Pinned versions:** a base image pinned by digest (official base images such as `debian:bookworm-slim` may use a
  tag, see [section 9.1](#91-what-the-validators-check)), git tags or commits, exact pip versions, and SHA256 checks
  for downloaded files (like `RKLLM_WHEEL_SHA256` for the RKLLM wheel).
- **One venv per toolchain** if their dependencies conflict (Rockchip: `/opt/venv`, `/opt/venv-rkllm`,
  `/opt/venv-rknn`).
- **Build tools in a builder stage.** Rockchip's image is a multi-stage build: the `builder` stage fetches and
  builds llama.cpp's tools and installs the venvs with every pin; the final stage copies the venvs and the source tree
  with `build/bin` to the same paths and installs only what the build stages run (cmake, make, the compilers, git,
  gosu and the libraries of the toolkits), not `build-essential`, `python3-dev` or `curl`. On an arm64 build computer
  the RKLLM venv is left out (its wheels are x86_64 only): `/opt/venv-rkllm/NOT_INSTALLED` holds the reason, which
  `rkllm_module.sh` and the entrypoint's test report.
- No binaries in the repository: download wheels and libraries in the Dockerfile and check their hashes.

### 5.1 llama.cpp in the image: recipes, not binaries

The framework shares recipes, never compiled images or binaries. A target builds llama.cpp from source with the
shared steps of `targets/Rockchip/dockerfile`, which the template copies unchanged:

1. **Fetch by commit.** `LLAMA_CPP_COMMIT` (40 hex characters) and `LLAMA_CPP_TAG` (`b` and a number) are checked,
   the commit is fetched with `git fetch --depth 1 origin <commit>` (the tag only where a server refuses that), and
   the build stops unless `git rev-parse HEAD` gives back the commit. A moved tag therefore stops the build. `.git`
   is removed afterwards; `.llama_cpp_tag` and `.llama_cpp_commit` record the pin in the image.
2. **Configure with the plan.** Every word of `LLAMA_CMAKE_ARGS` must look like `-DNAME=VALUE` (letters, digits and
   `. _ : + / $ ; -` only), `GGML_NATIVE` must be set, and options that download whenever they are set
   (`LLAMA_BUILD_BORINGSSL`, `GGML_CUDA_CCCL_VERSION` and the others of the recipe) are refused. After these words
   the command line sets what every build has: tests, examples, the app, the server, the web UI, llguidance,
   KleidiAI and the OpenMP download off, `LLAMA_OPENSSL=OFF`, `LLAMA_SUBPROCESS=OFF`,
   `FETCHCONTENT_FULLY_DISCONNECTED=ON`, and the build number and commit for `--version`. CMake takes the last
   value, so they always win. A configure log that reports a download stops the build.
3. **Build and check.** `llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` and `llama-bench`
   are built, and `llama-bench --version` must report the pinned build and commit.

**Tuned to the build host.** The image runs on the machine that builds it, so the engine passes the recipe's plan
for purpose `host-tools` from a probe of that machine, taken right before the image build (or, if that fails, the
profile `llm-cli scan` stored): the CPU features written out (for example `-DGGML_AVX512=ON` only where the probe's
CPU flags name the whole set `GGML_AVX512` compiles for, AVX-512 F, BW, VL, DQ and CD; a CPU with AVX-512 F alone, such
as a Xeon Phi, gets the AVX2 plan), or all CPU variants with the best one chosen at run time when the probe could not tell every feature.
Without a probe, or when the Docker daemon runs on another machine, the argument is left out and the Dockerfile's
default applies: the fixed x86-64 baseline these tools always had (AVX2, FMA, F16C, no AVX-512), static, written
out. The build log says which, and `build_info.json` records it under `llama_cpp_tools` (tag, commit, tuning, the
plan's ID and words). CPU and CMake flags from the module wizard or an AI never reach this build.

**What the validators refuse** ([section 9.1](#91-what-the-validators-check)): an image named after llama.cpp or
`llama-server` (also pinned by digest) or one of the framework's former runtime images, llama.cpp binaries copied
out of another image, downloads of llama.cpp release archives or binaries, prebuilt `llama-cpp-python` wheel
indexes, and distribution packages of llama.cpp (`apt-get install llama.cpp` and the like).

### 5.2 CUDA in the GPU template

`targets/_template/Dockerfile.gpu` builds on `ubuntu:22.04`, pinned by digest, in both stages and takes CUDA
12.9.1 from NVIDIA's redist archives: `redistrib_12.9.1.json` is pinned by its sha256 (the values of `cuda.toolkits`
in `configs/llama_runtime.yml`), and each component (`cuda_nvcc`, `cuda_cudart`, `cuda_cccl`, `libcublas`) is checked
against the sha256 the manifest lists before it is unpacked into `/usr/local/cuda`. The runtime stage gets the CUDA
runtime libraries (cudart, cuBLAS). The components come under NVIDIA's CUDA Toolkit licence (LICENSE in each
archive); building the image downloads them for your own use. The llama.cpp conversion tools stay CPU builds; GPU
tools are a later step of the recipe.

NVIDIA's prebuilt development image is an explicit opt-in: choose
`nvidia/cuda:12.9.1-devel-ubuntu22.04@sha256:bd4e2680a261c212f1e2fea241606f71497dc67a417f73175d794ec8212b5ba8` (or
another `-devel-ubuntu22.04` image pinned by digest) as the base image yourself, in the wizard or `module create`.
Ditto's proposals never carry an `nvidia/cuda` image: one that does is sent back to Ditto once and otherwise refused,
because a probe file could steer the proposal. The generator then uses the image for the builder stage and sets
`CUDA_SOURCE=prebuilt-image`, which the image carries as the label `org.llm-framework.cuda-source`; the toolkit is
then not provisioned from the pinned manifest, and the generator's log and the image build's log say "not
self-built". The runtime stage stays on the pinned Ubuntu 22.04, because the Python environment is copied from one
stage to the other.

### 5.3 Base images and vendor software of AI-created modules

A module an AI proposes (Ditto in `module generate-ai` and the wizard, or an MCP client through
`create_target_module`) may start from **any base image**, because some vendors ship their toolchain only as a
container image (for example NVIDIA's Jetson/L4T images on `nvcr.io`). The rules:

- **Pinned by digest.** A tag is resolved to the digest it points at when you are asked (anonymously through the
  registry's public API; refused in offline mode; a registry that needs a login is not used, give the image as
  `image@sha256:...`), and the Dockerfile's builder stage starts from exactly that digest; the generator checks that
  the builder stage starts from the module's base image, also for the GPU template. Registries with a port
  (`registry.example:5000/img@sha256:...`) are accepted.
- **Shown plainly.** A Docker Official Image (`debian`, `ubuntu`, `python`, without a namespace) gets the normal
  question. Any other image is shown as "other registry: <registry>; prebuilt image made by its publisher, not checked
  by the framework", with "the image build runs with network access on this computer, outside the build sandbox",
  the platforms in its manifest and whether one matches this computer (an arm64-only image on an x86_64 computer is
  shown, not refused). Such an image needs `base_image_reason`; prefer an official image plus the vendor's sources.
- **Vendor software**, rendered by the framework's Dockerfile template during the image build (the AI's shell fields
  still may not download):
  - `package_sources`: an https apt repository (`url`, `suite`, `components`) with its public signing key
    (`signed_by_url`) and the key's full fingerprint (`signed_by_fingerprint`). The image build fetches the key,
    stops when its fingerprint differs, and adds the source with `signed-by`; `packages` can then name its packages.
  - `pip_packages`: Python packages pinned exactly as `name==version`, from PyPI or from one https index
    (`pip_index_url`), installed into the image's Python environment.
  - `pinned_downloads`: files with an https address, a file `name`, the exact `size_bytes` and the `sha256`; the image
    build checks size and hash before the file gets its name in `/opt/vendor` (copied into the runtime stage), where
    `setup_commands` can unpack it.
  Security checks are hard (https, exact pins, size and sha256, 40-hex fingerprints, never a prebuilt llama.cpp
  package, wheel index or binary). The counts are soft limits (3 sources, 30 pip packages, 5 downloads): going above
  one is a warning in the question that the person's approval overrides, never a refusal.
- **Untested is stated.** The new `target.yml` gets `metadata.provenance: ai-generated`, `metadata.untested: true`,
  the AI's `assumptions` and `base_image_reason`, and support level `community`; builds of it warn. AI-created
  modules never get a standing approval: every one is confirmed by the person.

### 5.4 Sources in the own network for offline builds

An offline build of the build container image (offline mode: no internet, maybe a local network) passes these build
arguments only when the person set the source and the locality rule judged it local; online none of them is passed,
so a Dockerfile that takes them builds as before:

| Argument | What the Dockerfile does with it |
|---|---|
| `APT_MIRROR`, `APT_PROXY` | Declared (`ARG`, no default) in every stage that runs apt: the sources are rewritten to the mirror (`sed` over `/etc/apt/sources.list*`), the proxy goes into `/etc/apt/apt.conf.d/01llmcf-proxy`, before the first `apt-get update`. |
| `PIP_INDEX_URL`, `PIP_TRUSTED_HOST` | Declared in every stage that runs pip; pip reads both from the environment. With an index, drop the requirements' own `--extra-index-url` lines. |
| `LLAMA_CPP_SOURCE` | `ARG LLAMA_CPP_SOURCE=git` and `COPY sources/ /tmp/llmcf-sources/` before the llama.cpp fetch; with `context` the fetch step copies `/tmp/llmcf-sources/llama.cpp` (with its `.git`) and checks its commit and that nothing changed, as for a fetch. The framework creates `sources/` in every build context. |

Every other address the Dockerfile fetches from (a URL of any scheme, such as `https://`, `git://` or `ssh://`, or the
scp-like `user@host:path`, in a `RUN` or `ADD` line or an `ARG` default) is judged by its host; an offline build stops
when it is not in the own network. Packages come only through apt and pip: a Dockerfile that runs another package
manager (`apk`, `dnf`, `yum`, `zypper`, `pacman`, `conda`, `npm`, `cargo`, `go install` and the like) cannot be built
offline; its image is built online once or imported. An `ARG` whose name ends in `_PIP_URL` is a pip
location that the pip index replaces (Rockchip's `TORCH_PIP_URL` and `RKLLM_PIP_URL`): use it as
`${PIP_INDEX_URL:-${TORCH_PIP_URL}}` or, for a wheel with a pinned sha256, download it from the index and check the
same sha256. These arguments are no inputs of the image, so an image built online is found offline. The shipped
Dockerfiles and the templates do all of this.

## 6. The build stages

A build is four stage modules in `modules/`. `modules/build.sh`, the **stage runner**, is the entry point of every
stage container (`/app/modules/build.sh`, in the sandbox of [section 7](#7-sandbox-rules)); it runs the stages in
this order and does nothing else (maintainer decision U9). Each module's header says which image it runs in, which
system its result is for and what its stage does; keep these headers true when you change a stage.

| Stage | Module | What it does (Rockchip) |
|---|---|---|
| 1. source | `source_module.sh` | Names the toolchain sources the image was built from (the llama.cpp tag and commit its Dockerfile checked, the SDK venvs); in the template it also runs the setup commands of the module definition once. Nothing is downloaded. |
| 2. config | `config_module.sh` | Reads the request from the environment, normalizes format, board and type names, refuses what the target cannot build, and writes the build plan `build_config.sh` into the stage folder. With a hardware profile it also plans the board (toolchain and flags) and checks that plan before it keeps it ([section 10](#10-what-the-stage-modules-kept-from-version-1x)). |
| 3. convert | `convert_module.sh` | Converts the model: GGUF types the converter writes itself go straight into the output folder; every other type becomes an F16 intermediate in the work folder for the target stage. RKLLM and RKNN run their format helpers `rkllm_module.sh` and `rknn_module.sh`. In the `imatrix` job it computes the importance matrix. Around every conversion it detects the input's format, checks the input and what the converter wrote, runs a short functional test and records statistics ([section 6.5](#65-the-checks-of-the-convert-stage)). |
| (rotate) | `rotate_module.sh` | Optional hook between convert and target for a rotation such as SpinQuant or QuaRot (roadmap): it runs only when the target has this file; none is shipped. It may change the full-precision intermediate before it is quantized. |
| 4. target | `target_module.sh` | Quantizes what the convert stage left for it (`quantize_jobs.sh`) and removes the intermediate, writes `build_info.json` and lists the results. For a GGUF build with a hardware profile, and with `build.sh --package`, it also makes the package for the board ([section 10](#10-what-the-stage-modules-kept-from-version-1x)). |

The `imatrix` job runs source, config and convert; the `build` job runs every stage. A stage module started by hand
(`bash modules/convert_module.sh`) runs the build up to its stage through the runner; `build.sh --to STAGE` does the
same (`target_module.sh` started by hand also asks for the package: `build.sh --to target --package`). A later
stage may use the functions of an earlier one by sourcing its module (`config_module.sh` and `source_module.sh` run
their `main` only in their own stage). `benchmark_module.sh` is no stage: it would measure the
build host's CPU, and a model for a board is measured on the board (`llm-cli device runtime verify --bench-model`,
or **Boards > Boards and Devices... > Verify...**).

**Resume.** When a stage has finished (exit code 0), the runner writes a marker into `$BUILD_SCRATCH_DIR/stages/<job>/`
with a digest of the stage's inputs and the size of every file the stage named with `stage_output FILE`. The inputs
are the request (format, board, quantization, model folder with its file sizes and times, model revision and name,
context length, importance matrix, calibration dataset, hardware profile, also the one the config stage reads when
none is named, output folder), the toolchain (the llama.cpp pin of the
image, the SDK paths) and the SHA-256 of every file in `modules/` and `scripts/`. Started again on the same work
folder, the runner skips every stage whose marker has the same digest and whose files are still there; a stage that
runs again makes every later stage run again. A stage that fails writes no marker. `build.sh --fresh` ignores the
markers.

**Loop protection.** A stage that was started with the same inputs and was stopped from outside before it finished
(its container stopped, the build resumed, again and again) is a loop, not a long build. The runner counts these
starts in `<stage>.starts` and does not start the stage a fourth time (`STAGE_RESTART_LIMIT`, default 3): it fails
with exit code 75 and says so. A stage that ends with its own error (exit code below 128) and a finished stage reset
the count; `--fresh` forgets it.

**Progress.** The runner prints one line when a stage starts, ends, is resumed or fails:
`=== Stage 3/4: convert ===`, `=== Stage 3/4: convert done in 41 s ===`, `=== Stage 1/4: source resumed ===`,
`=== Stage 3/4: convert failed (exit 1) ===`. The build engine reads these lines from the container log: the build
status, the Builder and `llm-cli build status` show the stage ("Running modules: convert (3/4)") and its percent. A
failed stage ends the build with the stage's exit code.

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
| `MODEL_TASK`, `OPTIMIZATION_LEVEL`, `BUILD_ID` | task (default `LLM`), optimization level (`fast`, `balanced`, `size`, `speed`, `aggressive`; the Rockchip scripts ignore it), build id |
| `MAX_CONTEXT_LENGTH` | only if set for the build |
| `MODEL_REVISION` | the Hugging Face commit of the staged model (not for a local model); part of the resume digest |
| `USE_IMATRIX`, `IMATRIX_PATH` | build stage: `1` and the path of `imatrix.dat` if an importance matrix was computed |
| `DATASET_PATH` | calibration dataset, if the build uses one |
| `BUILD_SCRATCH_DIR`, `TMPDIR`, `HOME`, `XDG_CACHE_HOME` | writable work folder `/build-cache/work` and its sub-folders |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE` | `1` in the sandbox: libraries never try the network |
| `HARDWARE_CONFIG_FILE` | when the build has the board's hardware probe: `/build-cache/hardware/target_hardware_config.txt`; the config stage plans the build for that board ([section 10](#10-what-the-stage-modules-kept-from-version-1x)) |
| `PUID`, `PGID` | the host user's ids (not on Windows) |

What the stage runner gives each stage:

| Name | Meaning |
|---|---|
| `STAGE_DIR` | the stage folder `$BUILD_SCRATCH_DIR/stages/<job>/`: the markers, the build plan `build_config.sh` and what a stage hands to the next one (`quantize_jobs.sh`, `sdk_environment.sh`) |
| `MODULES_DIR` | the folder of the stage modules (`/app/modules` in the image) |
| `STAGE_RUNNER`, `STAGE_NAME` | set while the runner runs a stage; a module started without `STAGE_RUNNER` hands over to the runner |
| `PACKAGE_REQUESTED` | `1` when `build.sh --package` asked the target stage for the package for the board (also without a hardware profile), else `0`; an input of the target stage only |
| `stage_output FILE` | a shell function: names a file the stage wrote, which a resume checks |

Mounts:

| Path in the container | Mode | Content |
|---|---|---|
| `/build-cache/output` | read-write (build stage) | where the stages write their results |
| `/build-cache/imatrix` | read-write in the imatrix stage, read-only in the build stage | `imatrix.dat` |
| `/build-cache/work` | read-write | scratch space for large intermediates |
| `/build-cache/input-model` | read-only | a local model folder or the folder of a local model file |
| `/build-cache/models` | read-only in the sandbox | Hugging Face downloads under `hf/<owner>__<name>` |
| `/build-cache/hardware` | read-only | the board's hardware probe (`target_hardware_config.txt`), in the build and the imatrix job, when the build has one |
| `/build-cache/dataset.txt`, `/build-cache/dataset.json` or `/build-cache/dataset/` | read-only | calibration dataset (an RKNN sample list gets its folder); a dataset inside the model folder is reached through the model mount |

### 6.2 Outputs and exit code

- Write model files to `$BUILD_CACHE_DIR/output` (`/build-cache/output`). Files ending in `.gguf`, `.rkllm`,
  `.rknn`, `.onnx`, `.safetensors` or `.bin` count as model files; the largest file with the format's extension
  becomes the primary file that deployment installs.
- You may write `build_info.json` there with your own fields (Rockchip writes model, format, board, quantization
  and the llama.cpp tag, in its target stage, and the convert stage's statistics as `conversion`); the engine adds
  the file list, hashes and build data.
- In the imatrix job, the convert stage writes `$BUILD_CACHE_DIR/imatrix/imatrix.dat`.
- Name every file a later stage needs, and every result, with `stage_output FILE`: a resume runs a stage again when
  one of them is gone.
- Exit with a non-zero code on any error and print the reason to stderr. The runner ends the build with that code,
  and the last log lines go to the user and to self-healing, so make them clear
  (`die "RKLLM needs an RK3588 or RK3576 NPU; ..."`).

### 6.3 Rules

- Start every module with `set -euo pipefail`, and run its work in `main`, which the module calls only when it was
  started itself (`if [[ "${BASH_SOURCE[0]}" == "${0}" ]]`): then a later stage can source it for its functions.
- Keep `build.sh` as the template has it; put the work into the stage it belongs to, and keep each module's header
  (image, target system, what the stage does) true.
- In the config stage: lower-case format and board, normalize the quantization and accept exactly the aliases you
  declared in the capabilities. Refuse unsupported values with a message that lists what is supported, before
  anything slow runs. Use the same default board as `default_board`.
- Hand values to the later stages through the build plan (`printf '%s=%q\n'`, as `config_module.sh` does), never
  as unquoted text.
- Write large intermediates to `$BUILD_SCRATCH_DIR`, never into the image or a fixed `/tmp` path.
- Never download anything; the stages have no network.
- Never run a benchmark in a stage: a model for a board is measured on the board.
- Keep the capabilities and the scripts in sync with tests, like `tests/test_foundation.py`,
  `tests/test_review_capabilities_paths.py` and `tests/test_build_stages.py` do for Rockchip.

### 6.4 Example

The convert and target stages of a minimal GGUF target for the capabilities in [section 4.2](#42-capabilities);
`build.sh` and the source and config stages come unchanged from the template (the config stage writes `QUANT_TYPE`
and the paths into the build plan). It assumes that the image has llama.cpp in `/usr/src/llama.cpp` with the tools
built in `build/bin`, as the Rockchip image does. Both pass `bash -n` and ShellCheck; for F16/BF16 output, the
importance matrix, aliases and more checks, see `targets/Rockchip/modules/`.

```bash
#!/bin/bash
# convert_module.sh - stage 3 of the target "my_board": the model as an F16 GGUF for the target stage
# Image: the Dockerfile of my_board (llama.cpp in /usr/src/llama.cpp). Target: my_board (aarch64 Linux).
set -euo pipefail

main() {
    # The plan of the config stage: MODEL_SOURCE, MODEL_NAME, QUANT_TYPE, OUTPUT_DIR, SCRATCH_DIR, LLAMA_BASE, ...
    # shellcheck source=/dev/null
    source "$STAGE_DIR/build_config.sh"
    local f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
    python3 "$LLAMA_BASE/convert_hf_to_gguf.py" "$MODEL_SOURCE" --outfile "$f16" --outtype f16
    stage_output "$f16"   # a resume runs this stage again when the file is gone
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to convert "$@"
    main "$@"
fi
```

```bash
#!/bin/bash
# target_module.sh - stage 4 of the target "my_board": quantization and build_info.json
# Image: the Dockerfile of my_board (llama-quantize in /usr/src/llama.cpp/build/bin). Target: my_board.
set -euo pipefail

main() {
    # shellcheck source=/dev/null
    source "$STAGE_DIR/build_config.sh"
    local f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf" out="$OUTPUT_DIR/${MODEL_NAME}-${QUANT_TYPE}.gguf"
    "$LLAMA_BASE/build/bin/llama-quantize" "$f16" "$out" "$QUANT_TYPE"
    rm -f "$f16"
    printf '{"model": "%s", "quantization": "%s"}\n' "$MODEL_NAME" "$QUANT_TYPE" > "$OUTPUT_DIR/build_info.json"
    stage_output "$out"
    stage_output "$OUTPUT_DIR/build_info.json"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to target "$@"
    main "$@"
fi
```

### 6.5 The checks of the convert stage

The convert modules of Rockchip and of the template run `scripts/model_check.py` around every conversion (with the
image's `python3` and the standard library only; the module generator copies it into a new target's `scripts/`, keep
it there). What it does is what the convert module of version 1.x promised in the maintainer's design:

| Command | When | What it does |
|---|---|---|
| `input PATH --format FORMAT --stats FILE` | before converting | Detects the input: a Hugging Face folder with SafeTensors or PyTorch weights, a GGUF file (nothing to convert), an ONNX model, a single weights file, a folder of GGUF files. Refuses what the format cannot use with the reason (`--onnx-next TEXT` adds where an ONNX model goes on this target), checks the input's integrity (its JSON files, SafeTensors headers against the file sizes, the shards an index lists, PyTorch archives, a GGUF header) and starts the statistics. |
| `begin FILE --state FILE [--scratch DIR]` | before a converter writes FILE | Keeps an existing FILE as `FILE.convert-backup` until the new one passed its checks (an intermediate `tmp-*` of the work folder is simply replaced). |
| `watch DIR --state FILE` | before a format helper runs | Notes the files of DIR with their sizes and times, so that `output --watched` finds the model the helper wrote (a new file, or one it wrote over) and a failure removes it. |
| `output FILE --format gguf --type TYPE --seconds N --stats FILE --tools DIR` | after the converter | Checks the GGUF (every tensor's data inside the file), runs the functional test with `llama-completion` (or `llama-cli`) from DIR: 8 tokens, offline; it is stopped when it prints nothing for `--test-seconds` (120, `FUNCTIONAL_TEST_SECONDS`) or the model repeats itself, never by the clock alone. `--test-skip REASON` skips the test with a note. Completes the statistics. |
| `test FILE --tools DIR --result FILE` | in the target stage, before the package is kept | The same functional test of another GGUF (the quantized model; an embedding model is not run): `--what TEXT` names it in a failure, `--stage NAME` its log lines. Writes the result, the tool and the manifest's line to `--result`. |
| `keep --state FILE` | when the stage succeeded | Removes the backups. |
| `restore --state FILE --failed` or `--stopped` | when the stage fails; at its next start | Removes what the run wrote and puts the backups back (`--stopped`: what a killed run left). A file whose backup is no longer there stays as it is: the earlier file was put back by hand. |

The statistics (`convert_stats.json` in the stage folder) go into `build_info.json` as `conversion`: `input_format`,
`weights`, `input_bytes` (what the converter reads: a weights file it leaves aside, such as Mistral's
`consolidated.safetensors` beside the `model*.safetensors` shards, is not counted), `converted`, `output_format`,
`output_type`, `output_bytes`, `ratio` (input / output),
`seconds`, `functional_test` (`passed`, `skipped`, `timed out` or `stopped`) and `functional_test_tool`; numbers and
words only, no paths. A functional test that ends with an error or crashes (a signal such as llama.cpp's abort on a
tensor it cannot use) fails the stage with the tool's last lines: the GGUF does not work. A run that prints nothing
for `FUNCTIONAL_TEST_SECONDS` (`timed out`), a model that repeats itself (`stopped`) and a run stopped from outside
(SIGKILL of the out-of-memory killer, SIGTERM) are a note. It is skipped with a note when the image has neither tool or the tool does not start there, for an embedding model, for the NPU formats
(the model runs on the board) and in the `imatrix` job (`llama-imatrix` runs the model there).

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

1. **Is the target loaded and are its capabilities valid?** `llm-cli mojo` reports the code
   `targets.<id>.capabilities` if the block is missing or invalid; the error itself is in the log. To see the schema
   error directly:

   ```bash
   python -c "import yaml; from orchestrator.schemas.targets import TargetCapabilities as T; \
   T.model_validate(yaml.safe_load(open('targets/my_board/target.yml', encoding='utf-8'))['capabilities']); print('ok')"
   ```

   `llm-cli targets validate <id>` does the same check through the framework and prints every schema error
   (exit code 1 if the block is invalid).
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
| Runtime block | If `target.yml` has a `runtime:` block, it matches its schema: known architectures, backends and binaries, `built_from: source`, plain advisory flags, no other keys ([section 3.4](#34-what-the-generator-writes-and-what-you-add)). |
| Pinned images | In every file whose name starts with `dockerfile` in any case (`Dockerfile`, `dockerfile`, `Dockerfile.gpu`), every image that `FROM`, `COPY --from=` or `RUN --mount=...,from=` uses must be pinned by digest (`name@sha256:` and 64 hex digits). Exempt are official base images with a tag (names starting with `debian:`, `ubuntu:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` or `python:`), an earlier build stage (`FROM ... AS builder`, then `COPY --from=builder`) and a stage number (`--from=0`). NVIDIA's `nvidia/cuda` images need a digest. Comment lines are skipped. A variable such as `FROM ${BASE_IMAGE}` is refused, because it is not an image reference. |
| No prebuilt llama.cpp | The framework shares recipes, not binaries ([section 5.1](#51-llamacpp-in-the-image-recipes-not-binaries)). In Dockerfiles: no image whose name contains `llama.cpp`, `llama-cpp`, `llamacpp` or `llama-server`, and none of the framework's former runtime images (`ghcr.io/smilez1985/llm-runtime`, `ghcr.io/llm-framework/builder`, `ghcr.io/llm-framework/inference-node`), also when pinned by digest; no `COPY --from=` (or `RUN --mount=...,from=`) of llama.cpp binaries out of another image, also not through a build stage that only names that image (`FROM acme/tools@sha256:... AS tools`, then `COPY --from=tools /usr/bin/llama-server ...`). A stage whose `RUN` step compiles with CMake, or that copied the binaries from such a stage, builds them from source. In Dockerfiles, scripts (`.sh`, `.py`, `.ps1`, `.bat`, `.cmd`, and any file whose first line starts with `#!`) and YAML files: no download of a llama.cpp release (`.../llama.cpp/releases/download/...`, release archive names such as `llama-b11429-bin-...`), no download of a llama.cpp binary (`curl`, `wget`, `ADD` and similar with `llama-server`, `llama-quantize`, `libllama` and the other tool names in the same command; a pipeline such as `curl ... \| tar -x ... llama-server` is one command), no prebuilt `llama-cpp-python` wheel index and no installation of a llama.cpp package (`apt-get install llama.cpp`, `apk add`, `conda install` and others). Comment lines are skipped; the knowledge snapshot is not checked. |
| Shell deny-list | Every line of every `*.sh` file, comments included, unless the same line is in the template's file with the same path (for `modules/build.sh`: `targets/_template/modules/build.sh`). Refused: downloads (`curl`, `wget`, `aria2c`, and `fetch` as a word, which includes `git fetch`), deleting `/` (`rm -rf /`), `chmod 777`, `sudo`, `mkfs` and `dd if=`, the Docker socket or `docker run`, `eval`, `source` or `.` of a `$(...)` result, `sh -c "$(...)"`, and process substitution `<(...)` except as plain input (`done < <(grep ...)`). Downloads belong in the Dockerfile, pinned. |
| No llama.cpp download option | The same script lines (every line that is not the template's): no CMake option `-DNAME=VALUE` that would let llama.cpp download while it is built or give `llama-server` subprocesses, by the build recipe's rule (`refused_option`): an option of `NEVER_SET` with any value, one of `DOWNLOAD_OPTIONS_OFF` or `LLAMA_CURL` with another value than `OFF`, `FETCHCONTENT_FULLY_DISCONNECTED` other than `ON`, `LLAMA_SUBPROCESS` other than `OFF`. Every spelling the shell hands cmake as such an option counts: `-DNAME:TYPE=VALUE`, `-D NAME=VALUE` (cmake reads `-D` and the next word as one option) and quotes around the word, the name or the value. This covers a module's CMake flags (`CMAKE_OPTIONS` of `modules/config_module.sh`), any `cmake` call of its scripts and the target's Dockerfile lines that the template's Dockerfiles do not have. At build time `build.sh` checks every `cmake` call of the modules again, with the words as the shell expanded them (`module_flag_problem --alone` of `target_module.sh`), and a refused call does not start cmake. |
| Shell syntax | Every `*.sh` file passes `bash -n` (skipped where bash is not installed). |
| Text only | No file contains a NUL byte: binary files are refused. On export every file must also be UTF-8 text. |
| Placeholders | No file except the knowledge snapshot `knowledge/initial_knowledge.json` (crawled pages) and its licence files `knowledge/NOTICE` and `knowledge/LICENSES/` contains a template placeholder (`[MODULE_NAME]`, `[Hardware-Familie]`, `[IHRE_ARCHITEKTUR]`, `[Hersteller]`, `[QUANTIZATION_LOGIC]`, `[CPU_FLAGS]`, `[CMAKE_FLAGS]`, `[SDK_SETUP_COMMANDS]`, `[PACKAGING_COMMANDS]`, `[ihr-target-name]`, `{packages_str}` or `YYYY-MM-DD`), and no value in `target.yml` holds text in square brackets, such as `[Board Name 1]`. |

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

## 10. What the stage modules kept from version 1.x

Version 1.x already built a target with these four scripts, driven by the hardware probe; for a while the build
called only a single `build.sh` dispatcher, and the scripts were left aside. Since the maintainer's decision U9 they
are the build again, with the dispatcher's work moved into the stage it belongs to. Their 1.x functions stayed:

- **Hardware profile.** Given a profile of the board (`build.sh --hardware-config FILE`, or the fourth argument of
  the entrypoint mode `pipeline`), the config stage generates the CMake toolchain and compiler flags for that board
  from it (the template's with the wizard's CPU and CMake flags), and the target stage of a GGUF build also
  cross-compiles `llama-cli` and `llama-server` from the image's llama.cpp source and packages them with the model, a
  model card and helper scripts in `output/packages/`. The profile is the file the hardware probe writes on the board
  ([section 11](#11-the-hardware-probe-file)): Rockchip's config stage takes the system from `ARCH` and the cores from
  `CPU_MODEL` (Cortex-A55, A72 and A76 get their CPU flags; `SUPPORTS_NEON`, `SUPPORTS_AVX2` and `SUPPORTS_AVX512`
  their GGML flags) and refuses a system its image has no compiler for (only aarch64 and x86_64). An x86_64 profile
  gets `-march=native` only when it is of this build machine itself (the same CPU model and CPU flags as
  `/proc/cpuinfo` here); a profile of another PC gets the CPU features of its probe as the build recipe plans a build
  for another machine (all 14 GGML x86 options explicit, their compiler flags such as `-mavx2` on `-march=x86-64`, a
  feature the probe did not confirm left out, and for a 1.x probe file the features its `CPU_FLAGS_ALL` names), so
  the programs run on that PC and not only on a CPU like the build machine's. A 32-bit ARM
  system (`ARCH=armv7` and the like) is refused with the plain reason: the Rockchip boards have 64-bit CPUs and the
  image has only the aarch64 cross compilers, so install the board's 64-bit system and probe it again. The build jobs
  are capped at the board's cores (`CPU_CORES`, or `CPU_CORES_LOGICAL` of the probe file). A build of the framework
  gets the board's probe (the one the request names with `llm-cli build start --probe ID` or the Builder tab imported
  or scanned last, else the newest stored probe of a board of the request's SoC) read-only as
  `HARDWARE_CONFIG_FILE`, so the config stage plans it for that board; its sha256 is part of an identical build. The
  board's runtime is built from the board's own probe (`llm-cli device runtime build`), and the board package comes
  from `llm-cli deploy package` or `llm-cli device runtime deploy`. The template's config stage plans the same way
  (the x86 block is Rockchip's, byte for byte), and its `rknn_module.sh` and `rkllm_module.sh` are Rockchip's helpers:
  they use the image's venv and refuse plainly without it.
- **The plan is checked before it is kept.** The config stage finds the planned compilers in the image (it plans
  nothing when one is missing), builds and links a small C and C++ program with the planned flags (and runs them when
  they are for this machine), checks with the ELF header that they are for the board's system, lets `cmake -P` read
  the generated toolchain file, and checks the plan `build_config.sh` in a clean shell: it must be a valid script that
  sets every name the later stages read. A plan that fails a check is not written.
- **The package for the board.** `output/packages/<model>_<type>_<arch>_<time>/` holds the GGUF, `llama-cli` and
  `llama-server`, the toolchain file (`cmake_toolchain.cmake`) and the hardware profile, a model card (`README.md`),
  `test_model.sh` (checks the files against `SHA256SUMS`, then asks the model for a short answer with `llama-cli`),
  `deploy.sh` (`./deploy.sh USER@HOST [DEST]` copies the package over `ssh` and `scp` with
  `StrictHostKeyChecking=yes`, so the board's host key must already be known, to DEST, default `llm/<package>` in the
  user's home on the board, and checks the copy there; `./deploy.sh [DEST]` on the board itself copies it to DEST, default `/opt/llm`), `MANIFEST.json` with every build
  detail (model, quantization, llama.cpp, board, profile, flags, tests, each file with its size and SHA-256; the
  build's statistics in `build`: `convert_seconds`, `quantize_seconds`, `cross_compile_seconds`, `input_bytes` and
  `converted_bytes` of the conversion, and `tools` with the first line of `python3`, `cmake` and the cross compiler's
  `--version`, never a path) and `SHA256SUMS`. `build_info.json` records `quantize_seconds` next to the convert
  stage's `conversion`. The helpers come from the target's `scripts/package_test_model.sh`, `scripts/package_deploy.sh` and
  `scripts/board_package.py`. Before the package is kept, the model is run once on this machine (`llama-completion`
  of the image, when it has one) with the convert stage's functional test (`scripts/model_check.py test`,
  [section 6.5](#65-the-checks-of-the-convert-stage)): a run that ends with an error or crashes stops the package,
  and what says nothing about the model (a timeout, a stop from outside, a tool this machine cannot run or start, an
  embedding model) is a note in the manifest. The programs must be ELF programs for the board, and every file must
  match `SHA256SUMS`. Their cross build turns off every llama.cpp option that can download something and builds
  `llama-server` without subprocesses, after the module's CMake flags, so those cannot switch them on again. What no
  later option turns off stops the package before anything slow, as the image's Dockerfile refuses such
  `LLAMA_CMAKE_ARGS`: a module flag that sets an option of the recipe's `NEVER_SET` (also as `-D NAME=VALUE` or
  `-DNAME:TYPE=VALUE`) or makes CMake run a script (`-C`, `-P`). The
  package copies the model next to the build's own, so the framework's disk rule comes first
  (`scripts/board_package.py room`, the reserve of `orchestrator/Core/disk_plan.py`: the disk of `output/packages/`
  must hold what the package copies and keep 2 GiB or 2 % of it free): for the model before the functional test and
  the cross build, and for every file just before the copy; a disk that cannot hold it stops the stage with the rule's
  words and no package. The build container does not see the reservations of the per-user folder.
  `build.sh --package` (also `target_module.sh` started by hand and the entrypoint modes `target` and
  `pipeline`) makes the package without a profile too, with the plain defaults of the target (aarch64 for Rockchip),
  and the manifest says so (`hardware.source: defaults`). The framework's own deployment stays `llm-cli deploy` and
  the **Deployment** tab, over its SSH session with the framework's key and known hosts.
- **Cloning llama.cpp.** `source_module.sh --clone-llama-cpp` (or the entrypoint mode `source --clone-llama-cpp`),
  started by hand in a container with network, clones llama.cpp at `LLAMA_CPP_COMMIT` (a full commit id; without it
  the framework's pin, `LLAMA_CPP_PIN_COMMIT` in the module) from `LLAMA_CPP_REPO_OVERRIDE` (an https address without
  a user name, password, token or query; without it upstream), checks that the checkout is that commit and links it
  as `LLAMA_CPP_PATH`, for an image without llama.cpp. A build never does this: its image carries the pinned
  llama.cpp, fetched from the repository override when the framework built the image.
- **The module options.** `--input`, `--output`, `--model-name`, `--quantization` and `--hardware-config` work on
  `build.sh`, on every stage module started by hand and on every entrypoint stage mode, as in the maintainer's 1.x
  design: `convert_module.sh --input DIR --output FILE --model-name NAME` writes the F16 GGUF to FILE (kept);
  `target_module.sh --input FILE.gguf --quantization TYPE --model-name NAME` quantizes a GGUF without converting it
  again and keeps it (without `--quantization` Q4_K_M, without `--model-name` the file name without `.gguf` and
  `.fp16`); `config_module.sh --hardware-config FILE` needs no model and writes the toolchain and the plan into
  the stage folder. A path given as an option is made absolute from the current folder.

A target generated before this change keeps its own `build.sh`, which still runs as before.

## 11. The hardware probe file

The probe writes `target_hardware_config.txt` on the machine that will run the binaries (a board or a PC). One
parser reads it, `orchestrator/runtime/profile.py`, and the complete list of keys with the source, normalisation and
validation of each key is [`configs/hardware_probe_schema.yml`](../configs/hardware_probe_schema.yml). This section
is for anyone who writes a probe, reads a profile, or carries one in from an air-gapped machine.

Three probes write it with the same keys and rules: `scripts/hardware_probe.py` (the main path; standard library
only, Python 3.8 or newer) and the fallbacks `scripts/hardware_probe.sh` (any POSIX shell on Linux) and
`scripts/hardware_probe.ps1` (Windows). Each carries a copy of the schema's validation and tables, and
`tests/test_hardware_probe.py` and `tests/test_hardware_probe_keys.py` compare the copies with the schema and run the
`.py` and the `.sh` on the same fixture machines. Files of the older scripts (schema 1) are converted
([section 11.3](#113-how-the-framework-checks-a-profile)).

### 11.1 Format (schema 2)

- UTF-8 (a byte order mark is accepted), LF line ends (CRLF is accepted); lines starting with `#` are comments.
- The first line that is not a comment is `PROBE_SCHEMA=2`.
- One `KEY=VALUE` per line. Keys are upper case (`A-Z`, digits, `_`) and appear at most once.
- Values are one line, unquoted, at most 4096 characters; lists are comma-separated without spaces.
- Every `ON`/`OFF` key is always written: `ON`, `OFF` or `UNKNOWN`. `UNKNOWN` means "this method cannot tell"
  (for example AVX-VNNI on Windows); it is never guessed from a model name.
- Any other fact the probe cannot determine is left out, never written empty, and a code goes into `PROBE_WARNINGS`
  (for example `not_determined:LIBC_VERSION` or `needs_root:RKNPU_DRIVER_VERSION`).
- Per-GPU keys are numbered: `GPU0_VENDOR`, `GPU1_VENDOR`, ... below `GPU_COUNT`.
- The JSON view has the same keys with the same string values, `PROBE_SCHEMA` included.

Example: an RK3566 board running DietPi, shortened (the full file is
`tests/fixtures/hardware_probe/schema2_rk3566_dietpi.txt`):

```text
PROBE_SCHEMA=2
PROBE_TOOL=sh
PROBE_TOOL_VERSION=3.0.0
GENERATED_AT=2026-10-11T08:15:00Z
OS=linux
OS_ID=debian
OS_FAMILY=debian
OS_VARIANT_ID=dietpi
OS_VERSION_ID=12
KERNEL=6.1.99-vendor-rk35xx
ARCH=arm64
LIBC=glibc
LIBC_VERSION=2.36
INIT_SYSTEM=systemd
CPU_MODEL=Cortex-A55
CPU_CLUSTERS=0-3:0x41:0xd05:1800
SUPPORTS_AVX2=OFF
SUPPORTS_NEON=ON
SUPPORTS_DOTPROD=ON
SUPPORTS_FP16_ARITH=ON
SUPPORTS_I8MM=OFF
RAM_TOTAL_MB=3912
SOC_VENDOR=rockchip
SOC_MODEL=rk3566
NPU_DEVICE=/dev/rknpu
SUPPORTS_RKNN=ON
GPU_COUNT=1
GPU0_VENDOR=arm
GPU0_DISCRETE=OFF
GPU0_UNIFIED=ON
PROBE_WARNINGS=needs_root:RKNPU_DRIVER_VERSION
```

### 11.2 Keys

| Group | Keys |
|---|---|
| Meta | `PROBE_SCHEMA`, `PROBE_TOOL` (`python`, `sh`, `ps1`), `PROBE_TOOL_VERSION`, `GENERATED_AT` (UTC), `PROBE_WARNINGS` |
| Operating system | `OS` (`linux`, `windows`, `macos`, `freebsd`, `android`), `OS_ID`, `OS_ID_LIKE`, `OS_FAMILY` (`debian`, `rhel`, `alpine`, ...), `OS_VARIANT_ID` (`dietpi`, `armbian`, ...), `OS_VERSION_ID`, `OS_VERSION_CODENAME`, `OS_PRETTY_NAME`, `KERNEL`, `KERNEL_ARCH`, `INIT_SYSTEM` (`systemd`, `openrc`, `sysvinit`, ...) |
| Binary compatibility | `ARCH` (`x86_64`, `arm64`, `armv7`, ...; the architecture of the userland, so a 32-bit system on a 64-bit kernel is `armv7`), `LIBC` (`glibc`, `musl`, `ucrt`, ...), `LIBC_VERSION` |
| CPU | `CPU_MODEL`, `CPU_VENDOR`, `CPU_CORES_LOGICAL`, `CPU_CORES_PHYSICAL`, `CPU_CLUSTERS` (ARM only: CPUs, implementer, part and maximum MHz per cluster), `CPU_FLAGS_ALL`, `CPU_FLAGS_SOURCE`, `CPU_L2_CACHE_KB` |
| x86 features | the 14 x86 options of ggml: `SUPPORTS_SSE42`, `SUPPORTS_AVX`, `SUPPORTS_AVX2`, `SUPPORTS_BMI2`, `SUPPORTS_F16C`, `SUPPORTS_FMA`, `SUPPORTS_AVX512` (AVX-512 F), `SUPPORTS_AVX512_VBMI`, `SUPPORTS_AVX512_VNNI`, `SUPPORTS_AVX512_BF16`, `SUPPORTS_AVX_VNNI`, `SUPPORTS_AMX_TILE`, `SUPPORTS_AMX_INT8`, `SUPPORTS_AMX_BF16` |
| ARM features | `SUPPORTS_NEON`, `SUPPORTS_DOTPROD`, `SUPPORTS_FP16_ARITH`, `SUPPORTS_I8MM`, `SUPPORTS_SVE`, `SUPPORTS_SVE2`, `SUPPORTS_SME`, `SUPPORTS_BF16_ARM` |
| RISC-V, LoongArch, POWER, s390x features | probe 3.2.0 ([section 11.7](#117-the-cpu-features-of-probe-320-and-the-one-planner)): `SUPPORTS_RVV`, `SUPPORTS_RV_ZFH`, `SUPPORTS_RV_ZVFH`, `SUPPORTS_RV_ZICBOP`, `SUPPORTS_RV_ZIHINTPAUSE`, `SUPPORTS_RV_ZVFBFWMA`, `SUPPORTS_RV_ZBA`, `SUPPORTS_XTHEADVECTOR`; `SUPPORTS_LSX`, `SUPPORTS_LASX`; `SUPPORTS_VSX`, `POWER_CPU_TYPE` (`power7` to `power11`); `SUPPORTS_VXE`, `SUPPORTS_VXE2`, `SUPPORTS_NNPA` |
| Memory, disk | `RAM_TOTAL_MB` (usable memory, never the sum of the memory slots), `SWAP_TOTAL_MB`, `RAM_AVAILABLE_MB`, `DISK_FREE_MB`, `RAM_TYPE`, `RAM_SPEED_MTS` |
| SoC, NPU | `SOC_VENDOR`, `SOC_MODEL` (`rk3566`, ...), `BOARD_MODEL`, `JETSON_L4T_VERSION`, `JETSON_JETPACK_VERSION`, `NPU_VENDOR`, `NPU_MODEL`, `NPU_DEVICE`, `RKNPU_DRIVER_VERSION`, `RKLLM_RUNTIME_VERSION`, `RKNN_RUNTIME_VERSION`, `USB_ROCKCHIP_PID`, `USB_ROCKCHIP_MODE` (a board attached over USB), `SUPPORTS_RKNN`, `SUPPORTS_HAILO`, `SUPPORTS_METIS`, `SUPPORTS_MX3`, `SUPPORTS_CORAL`, `SUPPORTS_INTEL_NPU`, `INTEL_NPU_DEVICE` |
| Hailo | `HAILO_FAMILY` (`hailo8`, `hailo1x`), `HAILO_ARCH` (`hailo8`, `hailo8l`, `hailo10h`, ...), `HAILO_DEVICES`, `HAILO_INTERFACE`, `HAILO_DRIVER`, `HAILO_DRIVER_VERSION`, `HAILO_FW_VERSION`, `HAILORT_VERSION`, `HAILO_PYHAILORT_VERSION` ([section 11.6](#116-the-keys-of-probe-310-and-how-a-module-uses-them)) |
| GPU | `GPU_COUNT`; per GPU `_VENDOR`, `_NAME`, `_PCI_ID`, `_DISCRETE`, `_UNIFIED`, `_VRAM_MB`, `_VRAM_FREE_MB`, `_DRIVER_VERSION`, `_COMPUTE_CAP` (NVIDIA), `_GFX_TARGET` (AMD), `_SOURCE`; `NVIDIA_DRIVER_CUDA_MAX`, `SUPPORTS_CUDA`, `SUPPORTS_INTEL_XPU`, `VULKAN`, `VULKAN_DEVICES`, `VULKAN_INSTANCE_VERSION`, `OPENCL_PLATFORMS`, `HIP_RUNTIME`, `ROCM_VERSION`, `ROCM_GFX_TARGETS`, `CUDA_TOOLKIT_VERSION`, `TENSORRT_VERSION`, `OPENVINO_VERSION`, `INTEL_GPU_DEVICE`, `INTEL_GPU_DRIVER`, `METAL` |
| Toolchain | `TOOL_CC`, `TOOL_CXX` (`gcc-12.2.0`, `clang-18.1.3`), `TOOL_CLANG`, `TOOL_MSVC`, `TOOL_CMAKE`, `TOOL_NINJA`, `TOOL_MAKE`, `TOOL_GIT`, `TOOL_CCACHE`, `TOOL_NVCC`, `TOOL_GLSLC`, `TOOL_HIPCONFIG`, `TOOL_DOCKER`, `TOOL_PYTHON`: versions only, never paths |

Not in a profile: the host name, serial or part numbers (also not the per-slot RAM part numbers the old Windows probe
wrote), network or cloud data. Derived values such as CMake options are not in the probe either: the framework
computes them from the profile, so a profile carried in from an air-gapped machine stays valid.

### 11.3 How the framework checks a profile

Profiles come from devices or are carried in by hand, so the parser treats them as untrusted:

- A schema 2 file is rejected as a whole when a key appears twice, a value is empty, longer than 4096 characters,
  contains a control character or does not match the key's validation (a fixed list, a number range or a pattern),
  or when a required key or an `ON`/`OFF` key is missing.
- Values must fit together: an x86 feature cannot be `ON` on `arm64` (nor an ARM feature on `x86_64`),
  `CPU_CLUSTERS` exists only on ARM, `GPU<n>_COMPUTE_CAP` only for NVIDIA and `GPU<n>_GFX_TARGET` only for AMD GPUs,
  `LIBC=ucrt` only on Windows, `METAL=ON` only on macOS.
- `NPU_DEVICE` names only an accelerator node (`/dev/rknpu`, `/dev/dri/renderD<N>` or `/dev/dri/card<N>`,
  `/dev/accel/accel<N>`, `/dev/hailo<N>`, `/dev/h1x-<N>`, `/dev/h1x`, `/dev/apex_<N>`), never a disk, memory or other
  device node, because a deploy step may pass it to `docker run --device`; `INTEL_GPU_DEVICE` names only
  `/dev/dri/renderD<N>` and `INTEL_NPU_DEVICE` only `/dev/accel/accel<N>`, for the same reason.
- The Hailo driver keys exist only on Linux, the Jetson keys only on `arm64` Linux.
- A key the schema does not list is dropped; its value is never kept.
- Free text (`CPU_MODEL`, GPU names, `BOARD_MODEL`) is for display only and never reaches a command line or a file
  name.

**Schema 1** files (the output of `hardware_probe.sh` 2.4 and `.ps1` 2.5, which the framework shipped up to and
including release 3.0.0, without a `PROBE_SCHEMA` line) are still read:

- The known keys are converted: `CPU_ARCH_TYPE` to `ARCH`, `TRUE`/`FALSE` to `ON`/`OFF`, `RAM_SWAP_MB` to
  `SWAP_TOTAL_MB`, the first GPU to `GPU0_*`, `DOCKER_VERSION` to `TOOL_DOCKER`. The CPU features of a `.sh` file
  are derived from its flag list, not taken from its own `SUPPORTS_*` lines; without a flag list they are
  `UNKNOWN`.
- Every CPU feature of a `.ps1` file becomes `UNKNOWN`, because that script asked Windows with wrong constants. Its
  `RAM_TOTAL_MB` (the sum of the memory slots) is dropped.
- The host name, the RAM part numbers and every other key without a schema 2 meaning are dropped without keeping
  the value. A key written twice with different values is dropped, and so is a value that fails validation.
- `ON`/`OFF` keys the file does not have become `UNKNOWN` (`OFF` where they cannot exist, such as NEON on x86).

The result carries `PROBE_CONVERTED_FROM=1` and the warnings `converted_from_schema_1` and `reprobe_before_build`:
run the probe again before a build.

### 11.4 The performance key

`HardwareProfile.perf_key()` is a sha256 over the values that change speed or the binary: `OS`, `ARCH`, `LIBC`,
`CPU_VENDOR`, `CPU_MODEL`, `CPU_CLUSTERS`, every `SUPPORTS_*` key, `SOC_MODEL`, `NPU_MODEL`, `HAILO_FAMILY`,
`HAILO_ARCH`, and per GPU its vendor, PCI id, compute capability and gfx target. It is the same whichever probe wrote the file and in whichever order the
GPUs are listed. It does not change with volatile values (free RAM, free VRAM, free disk space), the time of the
probe, the kernel, tool versions, or the values that are only recorded with a benchmark (OS version, C library
version, total RAM, VRAM size, driver versions, and the versions of probe 3.1.0: the L2 cache size, Vulkan instance
version, OpenCL platforms, the Hailo driver, firmware and runtime versions and the vendor runtimes); `recorded()`
returns those.

### 11.5 Where profiles are kept

`ProfileStore` keeps profiles in the per-user folder (`~/.llm-framework`, or `LLM_FRAMEWORK_HOME`):

- `hardware/local.txt` for this machine;
- `hardware/devices/<device id>.txt` for other machines, with `<device id>.json` next to it for the label and the
  SSH address.

A device ID is the first 16 hex characters of the sha256 of the device's SSH host key fingerprint: it stays the
same when the IP address changes and contains no host name. A profile imported without SSH gets a random ID. Files
are private to the user (mode 0600 in folders with mode 0700), volatile values are not stored, and stored files are
checked like probe files when they are read back. `llm-cli scan` stores this computer's profile as `local`, and
`llm-cli scan --device` stores a device's profile under its ID, and `llm-cli scan --import FILE` (or the GUI's
**Import from File** and the wizard's imports) stores a board's probe file under a random ID. AI clients and Ditto
name a stored profile by this probe id (`local` or the device ID; tools `get_hardware_probe` and
`probe_local_hardware`, resource `lcf://probes/{probe_id}`), never by a path. The GUI build tab's local and SSH scans
still write the probe file as described in [section 3.1](#31-probe-the-board).

### 11.6 The keys of probe 3.1.0 and how a module uses them

Probe 3.1.0 (`PROBE_TOOL_VERSION=3.1.0`) added the keys
below. Each is read the way the older keys are read: from a file, a sysfs entry or a vendor tool that is already on
the machine, with a fixed argument list, an empty stdin and a time limit; nothing is installed, nothing runs with
sudo. A key whose hardware or software is not there is left out without a warning; a value that is there but cannot
be read is left out with a code in `PROBE_WARNINGS` ([section 11.1](#111-format-schema-2)). None of them is required
or an `ON`/`OFF` key, so a file of probe 3.0.0, which lacks them, is read as it is.

| Keys | What they say | Read from |
|---|---|---|
| `CPU_L2_CACHE_KB` | the largest level-2 cache in KiB (a cache several cores share counts once) | `/sys/devices/system/cpu/cpu*/cache/index*`, Windows `GetLogicalProcessorInformation`, macOS `sysctl hw.*l2cachesize` |
| `VULKAN_INSTANCE_VERSION` | the highest Vulkan version an instance can be created with (the loader's) | `vulkaninfo --summary`, else the loader's file name `libvulkan.so.1.Y.Z` or the file version of `vulkan-1.dll` |
| `OPENCL_PLATFORMS` | `<name>:<OpenCL version>` per platform (`nvidia-cuda:3.0,intel-opencl-graphics:3.0`) | `clinfo` |
| `HAILO_FAMILY`, `HAILO_ARCH`, `HAILO_DEVICES`, `HAILO_INTERFACE`, `HAILO_DRIVER`, `HAILO_DRIVER_VERSION`, `HAILO_FW_VERSION`, `HAILORT_VERSION`, `HAILO_PYHAILORT_VERSION` | a Hailo accelerator: the family (`hailo8` = HailoRT 4.x, `hailo1x` = HailoRT 5.x), the architecture in the Dataflow Compiler's `--hw-arch` spelling (`hailo8`, `hailo8l`, `hailo10h`, ...), the number of devices, the interface (`pcie`, `usb`, `integrated`), the kernel module and its version (Linux), the firmware, HailoRT (= libhailort) and pyhailort versions | `/sys/module/<driver>` (never `lsmod`), the sysfs classes `hailo_chardev`, `hailo1x`, `hailo1x_integrated`, the PCI and USB ids, then `hailortcli -v`, `scan` and `fw-control identify -s <id>` (never `--extended`; only its architecture and firmware lines are read) |
| `JETSON_L4T_VERSION`, `JETSON_JETPACK_VERSION` | the Jetson Linux release (`36.4.0`) and the JetPack version (`6.1`) | the first line of `/etc/nv_tegra_release`, the installed package `nvidia-jetpack` |
| `CUDA_TOOLKIT_VERSION`, `TENSORRT_VERSION` | the CUDA toolkit release (`12.6.3`; also on a Jetson, where `nvcc` is not on PATH) and the TensorRT runtime | `/usr/local/cuda/version.json` (else `version.txt`), the file name `libnvinfer.so.X.Y.Z` |
| `OPENVINO_VERSION`, `INTEL_GPU_DEVICE`, `INTEL_GPU_DRIVER`, `INTEL_NPU_DEVICE` | the OpenVINO runtime, the Intel GPU's render node and its driver (`i915`, `xe`), the Intel NPU's node | the file name `libopenvino.so.YYYY.M.P`, `/sys/class/drm/renderD*`, `/sys/class/accel/accel*` (driver `intel_vpu`) |
| `ROCM_GFX_TARGETS` | the gfx targets of every KFD node, also of compute devices that are no display controller (an AMD Instinct card has no `GPU<n>` entry) | `/sys/class/kfd/kfd/topology/nodes/*/properties` |

With them came the warning code `version_mismatch` (hailortcli refuses to run because libhailort has another
version: `HAILORT_VERSION` is left out and `scan` and `identify` are not run), the master driver's Hailo nodes
`/dev/h1x-<N>` and `/dev/h1x` in `NPU_DEVICE`, and `NPU_MODEL` for a Hailo (`hailo-8l`, `hailo-10h`).
`SUPPORTS_HAILO` is now also `ON` for the USB Hailo-10H, an entry of a Hailo sysfs class and a device `hailortcli scan`
lists. The Hailo keys are written whatever NPU `NPU_VENDOR` describes, so a Rockchip board with a Hailo M.2 card keeps
both. `tests/test_hardware_probe_keys.py` runs the `.py` and the `.sh` on the fixture machines of HAILO_PLAN 6.1
(identify and scan outputs in `tests/fixtures/hardware_probe/hailo/`) and compares them.

**In a stage module.** The config stage reads the board's profile into `HW_CONFIG` (only keys of `A-Z`, `0-9` and
`_`; [section 6](#6-the-build-stages)). Use a key only when it is there, and when the module needs it, refuse with the
reason before anything slow runs:

```bash
# config_module.sh of a Hailo target (a sketch): the HailoRT line and the architecture come from the board's probe
case "${HW_CONFIG[HAILO_FAMILY]:-}" in
    hailo8)  HAILORT_LINE=4 ;;   # HailoRT 4.x (branch hailo8): Hailo-8, -8L, -8R
    hailo1x) HAILORT_LINE=5 ;;   # HailoRT 5.x (branch master): Hailo-10H, Hailo-15
    *) die "The board's probe names no Hailo family (SUPPORTS_HAILO=${HW_CONFIG[SUPPORTS_HAILO]:-UNKNOWN}): probe the board with its Hailo driver loaded and hailortcli installed." ;;
esac
HW_ARCH="${HW_CONFIG[HAILO_ARCH]:-}"   # the Dataflow Compiler's --hw-arch (hailo8, hailo8l, hailo10h, ...)
```

- A Hailo target builds HailoRT at exactly the version of `HAILO_DRIVER_VERSION`; driver, firmware and HailoRT of one
  board must be the same release. The probe only reports the versions; comparing them is the preflight's and
  deploy's job.
- A target for an NVIDIA Jetson compares the L4T release of its base image with `JETSON_L4T_VERSION` (refuse a board
  on another major release) and its CUDA with `CUDA_TOOLKIT_VERSION`; a desktop CUDA target also reads
  `NVIDIA_DRIVER_CUDA_MAX` (what the driver can run) and `GPU<n>_COMPUTE_CAP`. A TensorRT target reads
  `TENSORRT_VERSION`.
- An OpenVINO target reads `OPENVINO_VERSION`; its GPU plugin needs the node `INTEL_GPU_DEVICE`, its NPU plugin the
  node `INTEL_NPU_DEVICE` (both are checked to be accelerator nodes only, as `NPU_DEVICE` is).
- A ROCm target takes the gfx targets of a HIP build from `ROCM_GFX_TARGETS` (and `ROCM_VERSION`).
- An OpenCL or Vulkan backend reads `OPENCL_PLATFORMS` and `VULKAN_INSTANCE_VERSION`.

**In the framework.** Python code reads the keys from a `HardwareProfile` (`profile.get("CUDA_TOOLKIT_VERSION")`).
The Hailo facts come as one typed value: `profile.hailo()` returns a `HailoFacts` with `present`, `family`, `arch`,
`devices`, `interface`, `driver`, `driver_version`, `firmware_version`, `hailort_version`, `pyhailort_version`,
`version_mismatch` and the derived `model` (`hailo-8l`) and `hailort_line` (`4` or `5`); a profile without Hailo keys
gives `present=False` and `None` everywhere else. `llm-cli scan` prints these facts in a "Hailo:" line.

### 11.7 The CPU features of probe 3.2.0 and the one planner

Probe 3.2.0 (`PROBE_TOOL_VERSION=3.2.0`) writes a key for every CPU feature the pinned llama.cpp can use, also on
the systems the build recipe does not plan yet: RISC-V (the `isa` line of `/proc/cpuinfo`: `SUPPORTS_RVV`,
`SUPPORTS_RV_ZFH`, `SUPPORTS_RV_ZVFH`, `SUPPORTS_RV_ZICBOP`, `SUPPORTS_RV_ZIHINTPAUSE`, `SUPPORTS_RV_ZVFBFWMA`,
`SUPPORTS_RV_ZBA`, `SUPPORTS_XTHEADVECTOR`), LoongArch (`SUPPORTS_LSX`, `SUPPORTS_LASX`), POWER (`SUPPORTS_VSX`,
`POWER_CPU_TYPE` from the `cpu` line) and s390x (`SUPPORTS_VXE`, `SUPPORTS_VXE2`, `SUPPORTS_NNPA`). On Linux the
kernel's HWCAP bits (`/proc/self/auxv`) fill in what `/proc/cpuinfo` does not name; the bits are in the schema's
`tables.hwcap_bits`. Each key is `ON`/`OFF`/`UNKNOWN` like the x86 and ARM keys, `OFF` on another system. A file of an
older probe lacks them: it is read with them `OFF` outside their system and `UNKNOWN` on it, and the profile's notes
say `probe_again:<KEY>`; a file that says 3.2.0 and lacks one is refused.

**The one planner.** Only the build recipe (`orchestrator/runtime/build/recipe.py`) turns these keys into GGML options
and compiler flags: `arm_plan` for aarch64 (`armv8.2-a` plus `+dotprod +fp16 +sve +i8mm +sve2 +sme` in ggml's order
for the features that are `ON`, `armv8-a` without any; a tag only with the one it builds on: `+fp16` for `+sve`,
`+dotprod` for `+i8mm`, `+sve` for `+sve2`, `+sve2` for `+sme`; `+sme` only with GCC 14 or Clang 18), `_x86_options` for
x86_64. `orchestrator/runtime/build/ggml_cpu_options.py` lists every CPU option of the pinned llama.cpp with the probe
key it needs and whether the recipe plans it or why it cannot (for example `+bf16` on ARM, for which the pin has no
kernel). The plan records `cpu_features` (`used`, and `unusable` with each reason), and `llm-cli runtime device plan`,
the Boards window and the board package's `MANIFEST.json` (`hardware.cpu_features`) show them.

**In a target pack.** A config stage never plans CPU flags of its own and never derives `-mcpu` from the core's name;
it mirrors the recipe. The Rockchip target and the template carry the same functions (`plan_arm_cpu`, `plan_x86_cpu`
in `modules/config_module.sh`), which set `ARM_CFLAGS`, `ARM_CMAKE_FLAGS` (`-DGGML_CPU_ARM_ARCH=...`),
`CPU_FEATURES_USED` and `CPU_FEATURES_UNUSABLE`; keep them as they are, and `tests/test_target_stage.py` runs them
against the recipe on the same profiles. A board of a system the recipe does not plan yet (riscv64, loongarch64,
ppc64le, s390x) is refused, and the refusal names the features its probe found. When the pin of llama.cpp moves,
`tests/test_cpu_feature_coverage.py` compares the inventory with excerpts of its CMake files
(`tests/fixtures/llama_cpp/<tag>/`): replace the excerpts and add each new option to the inventory, planned or with
the reason it is not usable.
