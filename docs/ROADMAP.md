Version 3.0.0 + unreleased changes · Last updated 2026-10-29

**English** · [Deutsch](ROADMAP_DE.md)

# Roadmap

What comes next. Version numbers and contents can still change; nothing in this document is part of a released
version. It is one of the three files of the [blueprint](BLUEPRINT.md): how the framework is built is in the
[design](DESIGN.md). The latest release is 3.0.0 (2026-10-10, see the [changelog](../CHANGELOG.md)).

Status per item:

- **done**: built and listed under "Unreleased" in the changelog; part of the next release.
- **in progress**: being built.
- **planned**: decided, not started.

When a version is released, its done items leave the roadmap and appear in the changelog under that version.

| Version | Theme | Status |
|---|---|---|
| 3.1.0 | Ditto rebuild, sign-in and local AI | in progress |
| next | Build service, queue and modes | planned |
| 3.2.0 | Device runtime and the maintainer's hardware | planned |
| 3.3.0 | LoRA and large models | planned |
| 3.4.0 | Supply chain and community | planned |
| later | Rust parts for the network and the board | planned |
| later | More hardware and MCP extensions | planned |

## 3.1.0 - Ditto rebuild, sign-in and local AI (in progress)

| Item | Status |
|---|---|
| **Ditto rebuilt on its own provider layer.** Official `anthropic` and `openai` SDKs instead of LiteLLM; providers as data (`configs/llm_providers.yml`) with model and effort per task and a live model list; retries, fallback chain, validated structured answers, prompt caching, privacy filter for cloud providers; Ditto uses the framework tools within your permissions (GUI chat and `llm-cli ditto ask`). | done |
| **Local knowledge base.** FastEmbed embeddings on the CPU, Qdrant embedded in the process, hybrid search (BM25 plus dense vectors, Reciprocal Rank Fusion); no vector-database server and no cloud embeddings. | done |
| **Knowledge base in Qdrant Edge.** The Rust engine of Qdrant in the process instead of qdrant-client's local mode; an older knowledge base is converted on first use and checked, its old folder stays until you delete it (see [The knowledge base's storage](DESIGN.md#the-knowledge-bases-storage-qdrant-edge-unreleased)). | done |
| **Managed local model with tiers down to 2-bit.** llama.cpp `llama-server` self-built on your machine from a pinned commit (Linux: CPU, then Vulkan; see the self-built runtime below), the official prebuilt archive (CUDA, Vulkan, Metal or CPU; ROCm only when chosen) only as the explicit `--prebuilt` opt-in, labelled "not self-built", and pinned Qwen3.5 models from 9B down to 2-bit models for weak hardware (3 GB RAM); `llm-cli ditto runtime` and `llm-cli ditto models`. | done |
| **Self-built llama.cpp, stage 1: one hardware probe.** Probe format schema 2 with the Python probe as the main path and the `.sh` and `.ps1` scripts as fallbacks that write the same keys: OS, version, init system, C library and its version, architecture and CPU features of the machine that will run the binaries, read on a board over SSH without writing anything there; no host names, serial or part numbers in profiles. | done |
| **Self-built llama.cpp, stage 1: one recipe, pinned by commit, built without network.** One build recipe turns a profile into a build plan with a build ID; llama.cpp is pinned by commit in one place, which target images, templates and the catalog fetch and check (moved up from 3.4.0); the source is fetched once and checked, and carried to air-gapped machines as a bundle; configure and build run natively or in the builder container without network; every build is verified (format, glibc symbol versions, version and commit, devices, the server refuses `--tools`) and kept in one build store; a build from another of your machines can be imported. | done |
| **Self-built llama.cpp, stage 1: conversion tools tuned to the build host.** Target images build `llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` and `llama-bench` with the recipe for the build host's CPU; new target modules get a `runtime:` block; target modules build llama.cpp from source and never pull prebuilt llama.cpp images or binaries. | done |
| **Self-built llama.cpp, stage 1: Ditto on the self-built server (Linux).** `llm-cli ditto runtime install` builds `llama-server` (CPU first, then the GPU build) instead of downloading it; one launch policy (forbidden options in every spelling, a cleared environment, loopback and a key file) for every program the framework starts, on the PC and on a board; the perplexity check runs the self-built `llama-perplexity` without a pulled image. | done |
| **Self-built llama.cpp, stage 1: the runtime on the board.** `llama-server`, `llama-cli`, `llama-bench` and `llama-perplexity` cross-compiled from the board's own probe in the builder container (base matched to the board's glibc or musl, C++ runtime linked statically, `libgomp.so.1` included), packed as a tarball with a start script, deployed over SSH as an unprivileged user and checked on the board before it becomes active; start script or systemd unit chosen beforehand in the deploy settings. Tested with fakes and a local stand-in board, not yet on a real board. | done |
| **Board deployment over a network that drops.** Address or host name; the deploy waits for a board whose Wi-Fi drops (visible waiting state, limit, Cancel) and resumes the upload after a sha256 check, `deploy.sh` runs on the board on its own and is started once; four SSH reason codes with next steps in English and German and plain codes for AIs; the KV cache (`f16`, `q8_0`, `q4_0`) in the deploy settings, automatic `q8_0` on boards with little memory; models built for a board are benchmarked on the board (or in the Docker sandbox), never natively on this computer, and a passed run on the board is recorded with the build as "tested on <board>". | done |
| **Ditto evaluation.** `llm-cli ditto eval` with fixed cases graded by rules, per provider or local model tier. The first measurement found gaps in Ditto's prompts; they are fixed, and a new measurement is running. | done |
| **Sign-in per access type** for all common cloud and API providers and for local engines (details below). | in progress |
| **Removal of the `claude_code` provider.** Anthropic does not allow third-party apps to use Claude subscription logins. Claude subscribers use the framework from Claude Code or Claude Desktop through the MCP server (`llm-cli mcp config` prints the setup), or give Ditto an Anthropic API key; Max and Team plans include monthly API credits. | done |
| **The framework in a container, built locally.** No framework image is published or pulled: `scripts/build_container_image.sh` (`make build`) builds the image on your computer by running the same installation as on bare metal (`install.sh`) from the shipped files; headless CLI and MCP server by default, the GUI only as an option; a working compose file and Makefile targets, and a build guide ([Container setup](Container_Setup.md)). | done |
| **Documentation in English and German** for every document. | in progress |
| **Crawler that checks the site's own signals.** A user cannot know whether a site may be evaluated, so the crawler checks the machine-readable opt-outs itself (robots.txt per RFC 9309, the `X-Robots-Tag` header, robots meta tags including `noai` and its own bot name, the TDM Reservation Protocol) and skips reserved pages, naming them in the log; any reservation wins, and no check can be switched off. Instead of a legal confirmation, a one-time notice (per user, again when its text changes) explains what is read and kept, with a spoken announcement through the operating system's speech engine (the technique of the rag4ll USB watcher); a Stop button keeps what was stored, the progress shows "Page N of M", the result says success, partial or failure honestly, and the crawler reports its real version. A person starts Deep Ingest in the GUI or confirms an AI client's `crawl_docs_site` (unreleased). | done |
| **Input history**: recent models, targets, boards, URLs, devices and datasets offered in the GUI fields, per user, without secrets; Tools > Clear Input History... forgets them. | done |
| **Faster start through lazy loading.** The CLI, the MCP server and the GUI load a heavy part only when a command first uses it: the framework's managers (not for commands that need no framework), the knowledge base and the crawler, Qt outside the GUI, Mojo's checks (its board check too), the MCP server's bridge to the open window (with the first call of its `gui` tool), the knowledge packs' licence texts (with the first pack), rich for `--help`. Importing the URL checks checks nothing about the host any more (Mojo reports the Python version and the free disk space). Why: `llm-cli mcp serve` must answer an AI client quickly, and `llm-cli --help` should not load everything. | done |
| **The GUI is an optional extra.** PySide6 is the extra `gui` (`requirements-gui.txt`): the desktop installers install it, `install.sh --headless` and the framework image without `--gui` leave out Qt and its system libraries, and the command line and the MCP server never load Qt (the core's texts are Qt-free). `llm-gui` without the extra says how to add it. Qt's own buttons (Close, Cancel) follow the window's language. Why: a server or a container needs no desktop libraries. | done |
| **The build container's CPU and memory in the Builder**, and one warning in the build log before the container runs out of memory (Docker's limit, or the WSL2 / Docker Desktop VM's). Why: a build can use up the memory of Docker or WSL2. | done |
| **Better answers from the knowledge base**, taken from rag4ll and kept small: reranking with a cross-encoder on the CPU and a relevance floor that keeps unrelated excerpts out of Ditto's prompt (on for build recommendations; for the chat and MCP searches with `rag_rerank_default`), a size budget for the excerpts, splitting of oversized code and table blocks, keyword search that understands identifiers such as `-DGGML_CUDA=ON`, and unloading of idle models; measured with the retrieval evaluation (`llm-cli ditto eval --kind retrieval`). | done |
| **Community packs with real cryptography**: packs with a manifest of every file, public packs signed with Ed25519, private packs encrypted in the age format (rage, through `pyrage`) to the recipients' keys or a passphrase, signed by the sender and readable with the standard age tools. The XOR "SwarmCipher" upload and its GitHub token prompt are removed; the framework never asks for a token. | done |
| **Model zoo from the community**: recipes (model at a pinned commit, target fingerprint, format, quantization, toolchain image and verified hashes) generated from finished builds; a signed index with revocations, installation through a quarantine with trust levels, `llm-cli zoo`, the Community Hub, export for the community with a hand-over to a pull request, and the maintainer tools for the community repository. | done |
| **Community repository and index published**: the repository is chosen ([Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base), the default of `community_repo` since 2026-10-22, so pull requests already go there). Still to do: its contribution workflow, the first signed index, the maintainer's key in `configs/community_keys.yml` and the setting `community_index_url` filled in, shipped with a release. Until then the zoo installs pack files only. | planned |

Sign-in per access type, as designed. Built so far (listed in the changelog): the API key first for Anthropic,
the credential store's second version, the one pattern table, the sign-in core (methods per provider, check,
errors with a next step) and the sign-in panel in the GUI with the matching CLI commands (`llm-cli ditto login`,
`logout`, `auth`) for API keys, local engines and the `ant` profile.

- One sign-in panel per access type in the GUI and matching CLI commands.
- **API keys first.** For Anthropic the API key is used unless you choose an `ant auth login` profile yourself;
  nothing switches the credential or the billed account silently. OpenAI, Google Gemini, OpenRouter and further
  OpenAI-compatible API services get presets with key pages and checks.
- **Browser sign-in only where the vendor allows it for third-party apps**: OpenRouter (OAuth with PKCE, which
  returns an API key), Hugging Face with the framework's own registered app, and "Sign in with ChatGPT" for OpenAI
  plans (beta until it is tested with a real account). The framework never reuses another program's login.
- **Cloud platforms** (AWS, Google Cloud, Azure) through the vendors' own credential chains, as optional extras;
  only selectors such as profile and region are stored.
- **Local engines as a client only**: Ollama, LM Studio, llama.cpp, vLLM, Jan, Lemonade, Foundry Local and
  Unsloth Studio. Engines are recognized by their responses, not by their port; keys are bound to the engine's
  address; a warning appears when an engine is reachable from the network without a key; Ditto never turns on
  server-side tools.
- **Credential store, second version**: encrypted vault with a lock across processes and key rotation; when no
  keyring is usable, you choose a fallback explicitly.
- **One pattern table** for the repository's secret scan and the redaction in logs.

## Next - build service, queue and modes (planned; starts right after the architecture consolidation)

The steps come in this order. Decisions that are still open are written into their row; they are asked when that
step starts.

| Item | Status |
|---|---|
| **Step 1 - Build service**: one background program per account owns every build; the GUI, the command line and the MCP server become its clients. A crash of the GUI no longer ends a build. The service can be stopped and restarted without losing anything (the state is already kept in files: build records, build requests, the reservation ledger, job summaries), and a build resumes at its last finished stage of the four-module build (source, config, convert, target). Why: today a build runs inside the program that started it, and ends when that program ends. | planned |
| **Step 2 - One queue across all programs**: builds wait in one queue in the build service and are admitted by RAM, CPU and disk. The disk part exists (the reservation ledger in `orchestrator/Core/disk_plan.py`); the queue extends what is there (the queue in `orchestrator/Core/orchestrator.py`, the build engine's slots, the request store) instead of a second structure. No Celery. Why: several programs (GUI, CLI, AI apps) start builds on one computer. | planned |
| **Step 3 - Private and enterprise mode**: private mode is the default, enterprise mode is optional. The mode is chosen in the installer (`install.sh` and the Windows installer) and changed in File > Settings...; an AI can read it but never change it. It reuses the advanced/enterprise scaffolding that already exists in `orchestrator/Core/config_manager.py` (decision U7). Why: steps 4 and 5 bring company features that a private installation does not need by default. | planned |
| **Step 4 - Secret backends**: HashiCorp Vault and CyberArk Conjur (enterprise mode; opt-in in private mode). Values are fetched when they are used, kept in memory only and never written to the local vault (`secrets.store`); every fetch is audited, and leases are renewed and revoked. Later: dynamic secrets and signed SSH certificates for boards. Why: companies keep secrets in a central store and must see every use. Open decisions, asked when this step starts: audit rows in CI mode; one fetch per operation; references only in enterprise mode; internet-hosted backends; where the backend's own sign-in credential is kept. | planned |
| **Step 5 - Kubernetes and Nomad**: opt-in places to run builds, next to the local Docker builds that stay the default. They use images the company builds itself from the framework's Dockerfiles into its own registry, pinned by digest, never images from the community hub. Why: a company may want builds on its own cluster. Open decisions: both modes or enterprise mode only; through `kubectl`/`nomad` or their HTTP APIs. | planned |
| **Step 6 - Own keyring service on Linux**: the framework ships its own keyring service on Linux (decided in round 7 on 2026-10-15, not built; Windows keeps the Windows Credential Manager); after step 4. Why: many Linux installations have no usable keyring, and the vault refuses one that hangs, keeps nothing or stores in plain text. Open decision: build it or keep the OS keyring. | planned |
| **Step 7 - Newest fitting versions kept automatically**: every dependency (Python, CUDA, Docker base images, toolkits, GitHub Actions) stays on the highest version that fits, and each update is tested before it lands. A plan is being prepared. Why: old versions collect security advisories, and an update must not break anything. | planned |

## 3.2.0 - Device runtime and the maintainer's hardware (planned)

The maintainer's own hardware sets the priorities: Radxa ZERO 3W (RK3566), NVIDIA RTX 2080 Ti and RTX 3090, AMD
Ryzen Z1 Extreme, on Windows and Linux. The recipes come first; the rotation stage and the generic ARM64 target follow
right after them.

| Item | Status |
|---|---|
| **Recipes as target templates.** A target template holds the build code; model, quantization, format and deployment are variables you pick in the build menu, and the builder uses everything a recipe holds. Why: you are never bound to the choices of the person who made the template. | planned |
| **Rotation stage before quantization** (SpinQuant, QuaRot), right after the recipes: rotations are baked into the weights before conversion and quantization, only where the target format supports them (a capability of the format, not a fixed step). Why: 4-bit models of the Llama family keep their quality on small boards. The slot is there: the stage runner runs a target's `modules/rotate_module.sh` between the convert and target stages; the algorithm is still to come. | planned |
| **Generic ARM64 CPU target (GGUF)** with board defaults for the Raspberry Pi 4 and 5, right after the recipes. Why: Rockchip was only the start, and these boards need no vendor SDK. | planned |
| **Targets from the hardware probe, proven on the board.** A new target starts from the probe you run on the board (also without network, the file carried back) or, if you want, from Ditto probing it over SSH; a passed benchmark on the board is the proof, shown as "tested on <board>" in the Community Hub and carried in packs. Why: the maintainer does not own most boards, and only a run on the real board shows that a target works. | planned |
| **Runtime on the device, the rest.** The first run of the self-built board runtime on a real Radxa ZERO 3W; the device runtime inside deployment packages; the runtime image for Docker deployments built locally from a device build (instead of the pulled runtime image); device benchmarks and a measured quantization recommendation per board; a build on the board when no builder base is old enough for its C library; Vulkan on Mali only after llama-bench shows that it beats the CPU build; MCP tools for probing, deploying and benchmarking devices. | planned |
| **Self-built llama.cpp, stage 2.** CUDA builds (NVIDIA's redist archives, the toolkit chosen by driver, compute capability, host compiler and CMake), Windows builds (Clang for the CPU backend, MSVC only as CUDA host compiler; until then Ditto's local model on Windows and macOS only through the `--prebuilt` opt-in), macOS (untested), host benchmarks that choose Ditto's model tier, and MCP tools for the runtime. | planned |
| **More Rockchip SoCs for RKLLM** (RK3562, RV1126B) with a per-SoC table of supported types. | planned |
| **NVIDIA GPU target**: GGUF with CUDA for the RTX 2080 Ti and RTX 3090 (CUDA version chosen by driver), vLLM formats AWQ and GPTQ, EXL3, and native deployment to Windows machines with NVIDIA GPUs. | planned |
| **GPU acceleration** for the importance matrix and the RKLLM export. | planned |
| **AMD RDNA3 APU target** for the Ryzen Z1 Extreme: llama.cpp with Vulkan and HIP, Linux container and native Windows deployment. | planned |
| **Pre-flight check before a build** (`orchestrator/Core/consistency_manager.py`, in development): compares the drivers the hardware probe found with what the target's SDK needs (for example the CUDA and driver matrix) and stops a build that cannot succeed before it starts; still to be wired into the build start. | planned |
| **Benchmark window** (`orchestrator/gui/benchmark_window.py`, in development): run llama-bench and show the results as a table; still to be finished on top of the runtime on the device and made reachable from the GUI. | planned |
| **One SSH connection shared by a board step's commands** (OpenSSH `ControlMaster`, as an option): the OpenSSH fallback logs in once per step instead of once per command; connection sharing stays off until then, and the framework's own SSH settings refuse it. | planned |

## 3.3.0 - LoRA and large models (planned)

| Item | Status |
|---|---|
| **LoRA adapters**: merge them into the model or keep them as an adapter (or both), with an own streaming merger, GGUF adapters for `llama-server --lora`, RKLLM LoRA, and a `lora` block in the target capabilities. | planned |
| **Optional LoRA training** with a lean trainer of our own: ideas from Unsloth, but no Unsloth code and no Unsloth installation. | planned |
| **Large models**: a check of disk space and RAM before a build, reuse of F16 intermediates, GGUF as input (passthrough and requantization), per-tensor quantization recipes checked by KL divergence, an importance matrix computed, taken from Hugging Face or loaded from a file, and tokenizer and chat-template checks after each build. | planned |
| **Defaults for the importance matrix and calibration** (settings `default_enable_imatrix` and `default_calibration_dataset`, in development): used as the defaults of the Builder, the CLI and the AI tools. | planned |

Not planned: airllm. It is a runtime, not a converter, and does not belong in the pipeline.

## 3.4.0 - Supply chain and community (planned)

| Item | Status |
|---|---|
| **Trivy** pinned by digest (it already scans a saved archive, without the Docker socket). | planned |
| **Pinning inside images**: base images by digest, hashed lock files per venv. | planned |
| **CycloneDX SBOM** and artifact provenance. | planned |
| **Dependabot cooldown** for LLM SDKs. | planned |
| **A second secret scanner in CI** (the gitleaks binary, pinned and checksum-verified) and GitHub push protection. | planned |
| **Signed updates** of the framework itself (`orchestrator/utils/updater.py`, in development: update check, update and restart; not wired yet), finished as verified updates of a signed release instead of a plain `git pull`. Toolchain images are not part of it: the framework shares recipes, never compiled images, so they stay built on your machine from the target's Dockerfile; the settings `docker_registry` (empty by default) and `docker_namespace`, which nothing reads, are kept. No default names a registry image: the image settings name local images (`llm-framework/...`) or third-party images such as Trivy and Qdrant, and the `docker` block of a target's `target.yml` names a local image without a registry. | planned |
| **The four modules are the build structure** (the planned clean-up of the four-module scripts, done differently than planned): instead of removing them, the maintainer's decision U9 made them the build. `build.sh` of Rockchip and of the template is a stage runner over `source_module.sh`, `config_module.sh`, `convert_module.sh` and `target_module.sh`, with resume markers per stage, progress per stage and the rotation slot; the dispatcher's work moved into the stage it belongs to, and the module generator writes working stage modules instead of placeholders. | done |
| **SSOT corrections** for the project-sources catalogue and its scripts. | done |

## Rust parts (planned)

The core stays Python. Parts that stand alone at a trust boundary and are not built yet are written in Rust; other
parts move only when a measurement shows a gain (maintainer decision of 2026-10-16).

| Item | Status |
|---|---|
| **Network MCP gateway in Rust.** One gateway process in front of the Python MCP server for clients on other devices: TLS, and sign-in by OAuth, a named token or a pre-shared key (an HMAC signature on every request). Why: this part faces the network; Rust gives it memory safety and a small, fast process. | planned |
| **MQTT broker inside the gateway (rumqttd).** It wakes AI clients with fixed keywords only (the details come over MCP), and other applications can subscribe and add their own feeds. It replaces the amqtt broker that was planned inside the Python process. Why: no second server to run, and the broker sits where the network connections arrive. | planned |
| **Root helper on the board in Rust**: a static binary with a fixed set of subcommands for what build and deploy need, and a sudoers rule for this helper only. Why: no general sudo on the board, and a static binary runs on every board, whatever its C library. | planned |
| **The framework's Rust parts shipped prebuilt and signed.** CI makes them release assets of this repository and signs them keyless with Sigstore; their checksums are kept in the repository, and the installer checks signature and checksum before it uses a binary. Building them yourself stays possible as an opt-in. Why: no Rust toolchain on your computer, and every binary can still be checked. | planned |

- **No prebuilt Docker images for targets.** Target images are still built on your computer from the target's
  Dockerfile and never pushed to a registry. Why: the framework shares recipes, not images, and an image would need
  a rebuild for every update.
- **No Go.** Why: one compiled language next to Python is enough; a second one adds a second toolchain.
- **The GUI stays Qt (PySide6).** Why: it runs on Windows and Linux, and Qt itself is native code.

## Later (planned)

- **More targets and formats**, as target templates and through a contribution kit:
  - **Llama-family models in every format.** Why: the framework started with RKNN, and Llama models must run on
    every target.
  - **ONNX, with a front end from Hugging Face to ONNX** for NPU toolchains (RKNN, Hailo, Intel NPU), in its own
    pinned environment. Why: today RKNN needs a local `.onnx` file that a layperson cannot make.
  - **NVIDIA Jetson** (the RTX GPU target is in 3.2.0). Why: a common edge platform with its own toolchain.
  - **Hailo.** Why: a common NPU add-on, for example for the Raspberry Pi 5.
  - **Intel** NPU and GPU. Why: many small PCs have one.
  - **Qualcomm.** Why: many ARM boards and laptops have its NPU.
  - **Vendor SDK files you provide:** a file you downloaded behind a vendor login, checked by sha256 and size; you
    accept its licence, and packs carry only a reference, never the file. Why: Hailo and Qualcomm ship parts of their
    SDKs only after registration.
- **Useful later**, from the maintainer's notes and the gap check:
  - **Version-aware knowledge:** excerpts tagged with target, toolkit and driver version, searches filtered by them,
    and documentation sources with their SDK version in `target.yml`. Why: answers for different SDK versions must
    not mix.
  - **Code signing** of the Windows installer and scripts, free routes for open source first. Why: no "unknown
    publisher" warning and no `ExecutionPolicy Bypass`.
  - **"Report this problem"** after an unknown build failure: a prefilled issue with the privacy-scrubbed probe,
    shown to you first and opened in the browser, never sent by itself. Why: the maintainer cannot reproduce boards
    he does not own.
  - **A notice of a newer llama.cpp release** (a Mojo line or a scheduled CI job); a person still moves the pin.
    Why: llama.cpp changes fast, and the pin stays a deliberate choice.
  - **Lint of generated modules** (ShellCheck, hadolint), with the findings returned to the AI that made them. Why:
    mistakes are fixed before the first build.
  - **Trivy findings as JSON** (package, CVE, severity, fixed version) in the result and the build record. Why: you
    and the AI see the findings, not only log lines.
  - **Community CI builds** of contributed targets (image build without push, smoke test, Trivy on the archive).
    Why: a contribution must work, not only pass the checks.
  - **Every window translated, with a language menu** (menus, tabs, dialogs, the hub, the deployment window). Why:
    many windows are still English only.
  - **A QEMU smoke test of cross builds in CI**, never on your computer. Why: a broken cross build is found before it
    reaches a board.
  - **An "edit all files" step in the module wizard**, with a diff and the checks run again. Why: vendor paths and
    flags can be finished before the module is saved.
  - **A history of healing fixes** with their success rate in Mojo; a fix is never applied by itself. Why: it shows
    which fixes really work.
  - **A purpose field** (chat, code, summaries, embeddings) for the quantization recommendation. Why: the best type
    depends on the use.
  - **An optional Ollama Modelfile** next to a GGUF. Why: Ollama can then load the result directly.
  - **A guide for edge use cases:** your deployed model in other programs (its OpenAI-compatible API, local RAG,
    voice). Why: the step after the deploy is not documented yet.
  - **Finding boards on the network** (zeroconf), with the host-key rules unchanged. Why: you do not need to know
    the board's address.
- **Crawls of large documentation sites with resume**: continue a stopped crawl later instead of only a page limit.
- **A reranker floor for keyword searches**: calibrate the relevance floor for short keyword queries, such as the
  module proposal's search string, with the retrieval evaluation, so those searches can be reranked too.
- **Measurement of the GPU tier**: grade `BAAI/bge-m3` and `bge-reranker-v2-m3`, which are chosen automatically
  with CUDA, with the retrieval evaluation on a CUDA machine.
- **Sign-in methods that need registrations**: AWS IAM Identity Center with an own registered client, GitHub
  Copilot through an own GitHub App, Gemini with the user's own OAuth client.
- **Local API server** (settings `api_enabled`, `api_host`, `api_port`, in development).
- **GUI theme and automatic refresh** (settings `gui_theme`, `gui_auto_refresh`, `gui_refresh_interval`, in
  development).
- **Housekeeping** (settings `auto_cleanup` and `logs_dir`, in development): clean up work folders after builds and
  a configurable log folder.
- **MCP Apps UI.**
- **An MCP server on edge devices.**

Not planned: builds in the cloud (AWS, Azure) or on rented machines. Remote compiling was never planned; if builds on
another computer ever come, the framework builds them itself.

