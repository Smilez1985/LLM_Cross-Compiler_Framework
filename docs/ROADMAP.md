Version 3.3.0 · Last updated 2026-10-31

**English** · [Deutsch](ROADMAP_DE.md)

# Roadmap

What comes next. Version numbers and contents can still change; nothing in this document is part of a released
version. It is one of the three files of the [blueprint](BLUEPRINT.md): how the framework is built and why is in the
[design](DESIGN.md), what each version contains is in the [changelog](../CHANGELOG.md). The latest version is 3.1.0
(see the changelog).

Status per item:

- **in progress**: being built.
- **planned**: decided, not started.

When an item is finished, its row leaves the roadmap in the same commit that adds its entry to the changelog, under the
version it lands in; an item finished in part keeps only what is missing. Each row names the round of the maintainer's
decisions it comes from.

| Version | Theme | Status |
|---|---|---|
| 3.3.0 | Ditto's context, the board bundle, the device runtime and more hardware | in progress |
| next | Build service, queue and modes | planned |
| 3.4.0 | LoRA and large models | planned |
| 3.5.0 | Supply chain and community | planned |
| later | Rust parts for the network and the board | planned |
| later | More hardware and MCP extensions | planned |

## 3.3.0 - Ditto's context, the board bundle, the device runtime and more hardware (in progress)

This version comes in this order: Ditto's context first, the board bundle next to it, then the gateway, voice and MCP
on the board. The first hardware sets the priorities for the device runtime: RK3566 boards, NVIDIA RTX GPUs and the
AMD Ryzen Z1 Extreme, on Windows and Linux. The recipes come first there; the rotation stage and the generic ARM64
target follow right after them.

### Carried over from 3.1.0

| Item | Status |
|---|---|
| **Sign-in per access type** for all common cloud and API providers and for local engines (details below). | in progress |
| **Documentation in English and German, the rest**: every document has its German twin (checked by `tests/test_docs.py`); still to do is the pass over every window's texts (see "Every window translated" under Later). | in progress |
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

### Ditto's context

| Item | Status |
|---|---|
| **Real token numbers: the person's paid run.** The measurement is built (the evaluation's `tokens` cases and the paid test `openai_paid`); what is left is the run itself: the person starts the paid OpenAI test once in their own llmcf with their own key (never from a sandbox or CI; the key never leaves the computer), and its `openai_cache_check.json` settles the 12-hour cache lifetime and the token count of OpenAI. | planned |
| **The real context window per model**, with counting, a budget and compaction: Ditto's local model from the measured hardware limits, API models per model, asked from the provider's server where it tells. | planned |
| **Cloud caching the provider's way**: the provider's own caching and its guides for placing cache points (Anthropic, OpenRouter), the cache lifetime a setting with a short default and a cost limit as soft cap. | planned |
| **Ditto's archive, phase 1: facts from tool results.** A searchable archive of what Ditto learned about llmcf and building (never the person's private talk), readable by everyone who uses the harness, in its own collection of the existing knowledge-base storage; a newer fact replaces the older one. | planned |
| **LLMLingua-2 on demand**, off by default: about 150 lines of the MIT method ported (a numpy runner in the app), the model fetched on demand from its original source and only when it is missing, never shipped in the repository. | planned |
| **Ditto's archive, phase 2**: facts from Ditto's own answers, export and import, snapshots. | planned |
| **Saved conversation caches on disk**, only if the measurement shows that people often come back after the server slept. | planned |

### Board bundle, gateway, voice, MCP on the board

| Item | Status |
|---|---|
| **Measure the board before any board default is set**: the RAM reserve of a board comes from a measurement on that board, never from a guess. | planned |
| **The start script passes context, cache budget and a per-board reserve.** | planned |
| **Prebuilt, signed Rust binaries for boards** (the board side of "The framework's Rust parts shipped prebuilt and signed" under Rust parts). | planned |
| **Shared golden vectors for the context rules**, so the board's Rust code and the host's Python code decide the same. | planned |
| **The bundle, deploy way 3**: a complete, hash-listed bundle the person prepares on the host (several components, each cross-compiled in its own step), with a signed manifest and its own store; it sits beside ways 1 (golden artifact and `deploy.sh`) and 2 (runtime package) and calls their code instead of copying it. | planned |
| **An idempotent installer on the board**: users, units, cgroups, files, hashes, rollback and uninstall, through the Rust root helper `llmcf-install`; root only through `sudo -n` with the person's yes for this deploy, or users the person created beforehand; offline the bundle is complete, online the installer fetches only pinned, hash-checked items of the manifest. | planned |
| **`llmcf-gateway`, stage 1 in Rust**: a token-saving middleware in front of the local model on the board; it starts and stops `llama-server`, which runs in its own unit outside the gateway's memory limit. | planned |
| **The gateway as a bundle component**, with verify and bench; loopback first, the local network only with TLS (own certificate, pinned fingerprint), never plain HTTP. | planned |
| **Voice with whisper.cpp** for speech recognition and the wake word; other voice parts (NeMo, the GigaSpeech wake word, eSpeak NG, a prebuilt onnxruntime) only as opt-ins with their licence shown before download, never on by default for non-free terms. | planned |
| **Vision with `--mmproj`** as a bundle component. | planned |
| **An agent harness as a bundle component**, at a pinned commit, under its own Linux user, installed by its own installer after the person's yes; llmcf does not block what that installer fetches, watches what it does (network targets, packages, files, processes), flags suspicious activity and reports before and after. | planned |
| **A "Board bundle..." window, CLI and MCP**: the person picks the components, with presets and a proposal from Ditto; whether the LLM runs on the board is the person's choice first, then the measured limits decide; the MCP tool `bundle_deploy` sits beside `deploy_device_runtime`. | planned |
| **A contract test for harnesses** that plug into the gateway, without any harness code in llmcf. | planned |
| **An optional session store on the board**, any time after the gateway is deployed. | planned |
| **An MCP server in the gateway**: loopback only, read tools first (model status, health, bench, logs). | planned |
| **The host reaches the board's MCP over SSH stdio** (`device_board_*` tools beside `probe_remote_device` and `deploy_device_runtime`); it talks to the board's own gateway, never to another installation of llmcf. | planned |
| **Approvals for the board's tools**, through the existing pending approvals: a harness may ask its own person, except a harness that listens to the room; standing approvals only offered as a recommendation with a soft cap. | planned |
| **Switch the model, voice and vision on the board, restart, bench** through MCP. | planned |
| **Bridge mode with autodiscovery**: the gateway connects to MCP servers that already run and offers their tools in one place; discovered tools are listed but not offered until the person ticks them, bridged tools are untrusted, and the gateway never starts a server. | planned |
| **The gateway's own tool loop** for the board's local model, with tool profiles. | planned |
| **MCP on the board's network address**, only with TLS and the pinned fingerprint. | planned |

### Decided, waiting for its turn

| Item | Status |
|---|---|
| **Ditto: security and measurability**: eval verdicts with a fingerprint, seeded repeats, intervals, pass^k and a pass-to-fail gate, a rule for turns tainted by outside text, the source on every knowledge excerpt, new groups of eval cases, replay cases and grading of the first tool call, fuller tool-use numbers, packing excerpts for small contexts and summaries that keep the facts. | planned |
| **Delegations**: a release the person creates in the framework (never over MCP) lets an AI answer its own routine questions, situation warnings included; security and legal warnings, licences, first trust of a host key, the AI's own permissions and every deletion stay with the person; lifetimes 8 hours, 24 hours or 30 days, "until revoked" lapses after 90 days without use. | planned |
| **"Do not ask again for this model version"** as a tick box in the build question, on the stable warning codes. | planned |
| **A message box for every MCP client**: a note on every result plus read and reply tools. The wake-up exists: the build doorbell on the person's own MQTT broker carries only fixed keywords ("Build ready", "Error while building", "Build stopped"), the details come over MCP; later the MQTT broker of the Rust gateway (Rust parts) carries it. | planned |
| **An own account `llmcf` on the host and on boards**: the installer creates it and gives it the folders it needs (on Windows under the program folder with explicit permissions), so a path that reaches an AI is always the same; on a board the person enters the admin password once, the framework creates the account and its key and keeps the key, never the password; that account's key runs only llmcf's command gate and its exact sudo rules (as the framework key does since 3.3.0), so the account itself holds no other right. | planned |
| **Presence proof and access control as opt-ins**: a click is enough to confirm; PIN, password or fingerprint only when the person turns them on; user accounts in the framework only as an opt-in. | planned |
| **An own target template for the RK3566**: the LLM on the CPU (Cortex-A55, NEON, GGUF), RKNN only as an optional step for voice and vision models that never blocks the LLM build; the other Rockchip SoCs keep the standard template, updated to the newest toolkits. | planned |
| **The update orchestrator imported natively**: an update orchestrator by the same author is imported into llmcf with its origin recorded and reworked there (its own repository stays as it is); on boards root only through cron and exact sudo lines; OS updates without a backup wait until backups are on (the backup's maximum age a setting, 14 days by default) or the person ticks the override for that board; Windows follows the same steps without OS updates; llmcf's own releases with SHA256SUMS and a signature, the git update path kept for developers. | planned |
| **Board backups pulled by the PC, two image kinds**: the PC pulls every backup over the deploy's SSH session, nothing is written to the board's own card; the image is built and shrunk on the PC (an unprivileged assembler; PiShrink, pinned, only for raw card images brought in from outside), the unshrunk image deleted once the small one is verified; a device backup restores exactly this board, a fleet image is generalised (new host keys and machine id at first boot, per-board values as variables from a list in the framework or a file on the boot partition). | planned |

### Device runtime and more hardware

| Item | Status |
|---|---|
| **Recipes as target templates.** A target template holds the build code; model, quantization, format and deployment are variables you pick in the build menu, and the builder uses everything a recipe holds. Why: you are never bound to the choices of the person who made the template. | planned |
| **Rotation stage before quantization** (SpinQuant, QuaRot), right after the recipes: rotations are baked into the weights before conversion and quantization, only where the target format supports them (a capability of the format, not a fixed step). Why: 4-bit models of the Llama family keep their quality on small boards. The slot is there: the stage runner runs a target's `modules/rotate_module.sh` between the convert and target stages; the algorithm is still to come. | planned |
| **Generic ARM64 CPU target (GGUF)** with board defaults for the Raspberry Pi 4 and 5, right after the recipes. Why: Rockchip was only the start, and these boards need no vendor SDK. | planned |
| **Targets from the hardware probe, proven on the board.** A new target starts from the probe you run on the board (also without network, the file carried back) or, if you want, from Ditto probing it over SSH; a passed benchmark on the board is the proof, shown as "tested on <board>" in the Community Hub and carried in packs. Why: the maintainer does not own most boards, and only a run on the real board shows that a target works. | planned |
| **Runtime on the device, the rest.** The first run of the self-built board runtime on a real RK3566 board; the device runtime inside deployment packages; the runtime image for Docker deployments built locally from a device build (instead of the pulled runtime image); device benchmarks and a measured quantization recommendation per board; a build on the board when no builder base is old enough for its C library; Vulkan on Mali only after llama-bench shows that it beats the CPU build; MCP tools for probing, deploying and benchmarking devices. | planned |
| **Self-built llama.cpp, stage 2.** CUDA builds (NVIDIA's redist archives, the toolkit chosen by driver, compute capability, host compiler and CMake), Windows builds (Clang for the CPU backend, MSVC only as CUDA host compiler; until then Ditto's local model on Windows and macOS only through the `--prebuilt` opt-in), macOS (untested), host benchmarks that choose Ditto's model tier, and MCP tools for the runtime. | planned |
| **More Rockchip SoCs for RKLLM** (RK3562, RV1126B) with a per-SoC table of supported types. | planned |
| **NVIDIA GPU target**: GGUF with CUDA for RTX GPUs of the 20 and 30 series (CUDA version chosen by driver), vLLM formats AWQ and GPTQ, EXL3, and native deployment to Windows machines with NVIDIA GPUs. | planned |
| **GPU acceleration** for the importance matrix and the RKLLM export. | planned |
| **AMD RDNA3 APU target** for the Ryzen Z1 Extreme: llama.cpp with Vulkan and HIP, Linux container and native Windows deployment. | planned |
| **Pre-flight check before a build** (`orchestrator/Core/consistency_manager.py`, in development): compares the drivers the hardware probe found with what the target's SDK needs (for example the CUDA and driver matrix) and stops a build that cannot succeed before it starts; still to be wired into the build start. | planned |
| **Benchmark window** (`orchestrator/gui/benchmark_window.py`, in development): run llama-bench and show the results as a table; still to be finished on top of the runtime on the device and made reachable from the GUI. | planned |
| **One SSH connection shared by a board step's commands** (OpenSSH `ControlMaster`, as an option): the OpenSSH fallback logs in once per step instead of once per command; connection sharing stays off until then, and the framework's own SSH settings refuse it. | planned |

## Next - build service, queue and modes (planned; starts right after the architecture consolidation)

The steps come in this order. Decisions that are still open are written into their row; they are asked when that
step starts.

| Item | Status |
|---|---|
| **Step 1 - Build service**: one background program per account owns every build; the GUI, the command line and the MCP server become its clients. A crash of the GUI no longer ends a build. The service can be stopped and restarted without losing anything (the state is already kept in files: build records, build requests, the reservation ledger, job summaries), and a build resumes at its last finished stage of the four-module build (source, config, convert, target). Why: today a build runs inside the program that started it, and ends when that program ends. | planned |
| **Step 2 - One queue across all programs**: builds wait in one queue in the build service and are admitted by RAM, CPU and disk. The disk part exists (the reservation ledger in `orchestrator/Core/disk_plan.py`); the queue extends what is there (the queue in `orchestrator/Core/orchestrator.py`, the build engine's slots, the request store) instead of a second structure. No Celery. Why: several programs (GUI, CLI, AI apps) start builds on one computer. | planned |
| **Step 3 - Private and enterprise mode**: private mode is the default, enterprise mode is optional. The mode is chosen in the installer (`install.sh` and the Windows installer) and changed in File > Settings...; an AI can read it but never change it. It reuses the advanced/enterprise scaffolding that already exists in `orchestrator/Core/config_manager.py` (decision U7). Why: steps 4 and 5 bring company features that a private installation does not need by default. Partly done: the setting `framework_mode` (private by default, the person's alone) and its one reader `Core/config_manager.py` `framework_mode` exist, and the board key tiers follow it (enterprise mode refuses the weaker tiers). Rest: the choice in the installers and in File > Settings..., and the company features of steps 4 and 5. | in progress |
| **Step 4 - Secret backends**: HashiCorp Vault and CyberArk Conjur (enterprise mode; opt-in in private mode). Values are fetched when they are used, kept in memory only and never written to the local vault (`secrets.store`); every fetch is audited, and leases are renewed and revoked. Later: dynamic secrets and signed SSH certificates for boards. Why: companies keep secrets in a central store and must see every use. Open decisions, asked when this step starts: audit rows in CI mode; one fetch per operation; references only in enterprise mode; internet-hosted backends; where the backend's own sign-in credential is kept. | planned |
| **Step 5 - Kubernetes and Nomad**: opt-in places to run builds, next to the local Docker builds that stay the default. They use images the company builds itself from the framework's Dockerfiles into its own registry, pinned by digest, never images from the community hub. Why: a company may want builds on its own cluster. Open decisions: both modes or enterprise mode only; through `kubectl`/`nomad` or their HTTP APIs. | planned |
| **Step 6 - Own keyring service on Linux**: the framework ships its own keyring service on Linux (decided on 2026-10-15, not built; Windows keeps the Windows Credential Manager); after step 4. Why: many Linux installations have no usable keyring, and the vault refuses one that hangs, keeps nothing or stores in plain text. Open decision: build it or keep the OS keyring. | planned |
| **Step 7 - Newest fitting versions kept automatically**: every dependency (Python, CUDA, Docker base images, toolkits, GitHub Actions) stays on the highest version that fits, and each update is tested before it lands. A plan is being prepared. Why: old versions collect security advisories, and an update must not break anything. | planned |

## 3.4.0 - LoRA and large models (planned)

| Item | Status |
|---|---|
| **LoRA adapters**: merge them into the model or keep them as an adapter (or both), with an own streaming merger, GGUF adapters for `llama-server --lora`, RKLLM LoRA, and a `lora` block in the target capabilities. | planned |
| **Optional LoRA training** with a lean trainer of our own: ideas from Unsloth, but no Unsloth code and no Unsloth installation. | planned |
| **Large models**: a check of disk space and RAM before a build, reuse of F16 intermediates, GGUF as input (passthrough and requantization), per-tensor quantization recipes checked by KL divergence, an importance matrix computed, taken from Hugging Face or loaded from a file, and tokenizer and chat-template checks after each build. | planned |
| **Defaults for the importance matrix and calibration** (settings `default_enable_imatrix` and `default_calibration_dataset`, in development): used as the defaults of the Builder, the CLI and the AI tools. | planned |
| **Bring the pip cache back in CI once the quota allows**: `cache: pip` in the setup-python steps of `ci.yml`, `security.yml` and `retrieval-eval.yml` is switched off for now, because the account's cache and egress quota ran out and a job hung at the cache restore ([design](DESIGN.md#tests-ci-and-the-windows-hazards)). | planned |

Not planned: airllm. It is a runtime, not a converter, and does not belong in the pipeline.

## 3.5.0 - Supply chain and community (planned)

| Item | Status |
|---|---|
| **Trivy** pinned by digest (it already scans a saved archive, without the Docker socket). | planned |
| **Pinning inside images**: base images by digest, hashed lock files per venv. | planned |
| **CycloneDX SBOM** and artifact provenance. | planned |
| **Dependabot cooldown** for LLM SDKs. | planned |
| **A second secret scanner in CI** (the gitleaks binary, pinned and checksum-verified) and GitHub push protection. | planned |
| **Signed updates** of the framework itself (`orchestrator/utils/updater.py`, in development: update check, update and restart; not wired yet), finished as verified updates of a signed release instead of a plain `git pull`. Toolchain images are not part of it: the framework shares recipes, never compiled images, so they stay built on your machine from the target's Dockerfile; the settings `docker_registry` (empty by default) and `docker_namespace`, which nothing reads, are kept. No default names a registry image: the image settings name local images (`llm-framework/...`) or third-party images such as Trivy and Qdrant, and the `docker` block of a target's `target.yml` names a local image without a registry. | planned |

## Rust parts (planned)

The core stays Python. Parts that stand alone at a trust boundary and are not built yet are written in Rust; other
parts move only when a measurement shows a gain (maintainer decision of 2026-10-16).

| Item | Status |
|---|---|
| **Network MCP gateway in Rust.** One gateway process in front of the Python MCP server for clients on other devices: TLS, and sign-in by OAuth, a named token or a pre-shared key (an HMAC signature on every request). Why: this part faces the network; Rust gives it memory safety and a small, fast process. | planned |
| **MQTT broker inside the gateway (rumqttd).** It wakes AI clients with fixed keywords only (the details come over MCP), and other applications can subscribe and add their own feeds. It takes over the build doorbell's topics and payload (`<prefix>/v1/build/<request_id>/end`, `<prefix>/v1/events`, `<prefix>/v1/status`), so clients keep their subscriptions, and the person's own broker becomes optional. It replaces the amqtt broker that was planned inside the Python process. Why: no second server to run, and the broker sits where the network connections arrive. | planned |
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
  - **Hailo.** Why: a common NPU add-on, for example for the Raspberry Pi 5. First Hailo-8 on a Raspberry Pi 5-class
    board (Hailo-10 experimental until a run passes); the gated Dataflow Compiler is never shipped, only found and used
    where the person has it.
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

Not planned: builds in the cloud (AWS, Azure) or on rented machines. Remote compiling was never planned; if builds on
another computer ever come, the framework builds them itself.
