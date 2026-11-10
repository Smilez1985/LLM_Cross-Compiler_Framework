Version 3.4.0 · Last updated 2026-11-10

**English** · [Deutsch](ROADMAP_DE.md)

# Roadmap

What comes next. Version numbers and contents can still change; nothing in this document is part of a released
version. It is one of the three files of the [blueprint](BLUEPRINT.md): how the framework is built and why is in the
[design](DESIGN.md), what each version contains is in the [changelog](../CHANGELOG.md). The latest version is 3.3.0
(see the changelog).

Status per item:

- **in progress**: being built.
- **planned**: decided, not started.
- **open question**: asked, not answered yet; nothing of it is built or decided before the maintainer answers.

When an item is finished, its row leaves the roadmap in the same commit that adds its entry to the changelog, under the
version it lands in; an item finished in part keeps only what is missing. Each row names the round of the maintainer's
decisions it comes from.

| Version | Theme | Status |
|---|---|---|
| 3.4.0 | Ditto's context, the board bundle, the device runtime and more hardware; LoRA and large models | in progress |
| next | Build service, queue and modes | planned |
| 3.5.0 | Supply chain and community | planned |
| later | Rust parts for the network and the board | planned |
| later | More hardware and MCP extensions | planned |

## 3.4.0 - Ditto's context, the board bundle, the device runtime and more hardware; LoRA and large models (in progress)

3.3.0 is released (see the changelog); what it left open is carried on here, with LoRA and large models after it.
This version comes in this order: Ditto's context first, the board bundle next to it, then the gateway, voice and MCP
on the board. The first hardware sets the priorities for the device runtime: RK3566 boards, NVIDIA RTX GPUs and the
AMD Ryzen Z1 Extreme, on Windows and Linux. The recipes as target templates and the generic ARM64 target are built;
the rotation stage comes next.

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
  plans (beta until it is tested with a real account), and a device code where a vendor offers one to
  third-party apps. The framework never reuses another program's login.
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
| **2-bit tiers only after a passed evaluation**: every 2-bit tier (the 2B, 4B and 9B models at 2-bit) is offered for Ditto only after `llm-cli ditto eval` passed with it on this computer. Until then the 4B and 9B tiers at 2-bit are never recommended and only downloaded by name, and the recommendation for 3 GiB of RAM or less, which picks the 2B model at 2-bit today, names it only as a candidate to evaluate; a failed evaluation is said plainly ([design](DESIGN.md#ditto-the-local-runtime-and-the-llm-layer)). | planned |
| **Ditto's archive, phase 1: facts from tool results.** A searchable archive of what Ditto learned about llmcf and building (never the person's private talk), readable by everyone who uses the harness, in its own collection of the existing knowledge-base storage; a newer fact replaces the older one. An entry the person verified is replaced too, but its text is kept as a copy, with a notice and Undo. | planned |
| **LLMLingua-2 on demand**, off by default: about 150 lines of the MIT method ported (a numpy runner in the app), the model fetched on demand from its original source and only when it is missing, never shipped in the repository. | planned |
| **Ditto's archive, phase 2**: facts from Ditto's own answers, export and import, snapshots. | planned |
| **Saved conversation caches on disk**, only if the measurement shows that people often come back after the server slept. | planned |
| **Ditto in editors (ACP): the MCP servers an editor passes.** A minimal MCP client for the stdio servers an editor hands to Ditto, used only for an entry the person enabled in the framework: their tools offered to the model under their own names, every call asked in the framework (no standing approval), its results marked as untrusted data, through the privacy filter and recorded in the audit log. Until then the entries are recorded and shown, and none is used. | planned |
| **ACP agents in the MCP listing (read-only).** An MCP tool that lists the ACP agents the person added (name, kind, version, whether it needs the internet, labels only, never a path or a key), so an AI can name them; adding, signing in and starting stay the person's. | planned |
| **ACP sessions that can be continued (`session/load`).** Ditto as an ACP agent offers `loadSession` and the ACP client resumes an agent's session when the agent offers it; today a session lives in memory for one connection. | planned |
| **A real end-to-end run with a third-party ACP agent.** One registry agent (npx or uvx) added, signed in with its own sign-in and run through a turn that calls the framework's tools, on a computer with network access; the tests so far use a fake agent. | planned |
| **Add-on, later: Android Studio as a partner for Android deployment.** Ditto runs as a custom ACP agent in Android Studio (`~/.jetbrains/acp.json`, see [Editors and agents](Editors_and_Agents.md#android-studio-1)) and uses the tools Android Studio hands its agents (build, emulator, Logcat) for an Android target: build the app that carries the model, install it on an emulator or a phone, read the result back. Needs the minimal MCP client of the row "Ditto in editors (ACP): the MCP servers an editor passes" first; every Android Studio tool call asked in the framework, recorded in the audit log. | later (add-on) |

### Board bundle, gateway, voice, MCP on the board

| Item | Status |
|---|---|
| **Prebuilt, signed Rust binaries for boards** (the board side of "The framework's Rust parts shipped prebuilt and signed" under Rust parts). | planned |
| **Shared golden vectors for the context rules**, so the board's Rust code and the host's Python code decide the same. | planned |
| **The bundle, its further components cross-compiled in their own steps** (the rest of deploy way 3): speech output (sherpa-onnx with onnxruntime and eSpeak NG) built by the framework for the board through the same self-build path as llama.cpp and whisper.cpp, instead of a program the person picks. | partly done (blocks, manifest, generator, store, the unprivileged installer, the Board bundle window, `llm-cli bundle`, the MCP tools, whisper.cpp through the one self-build path and vision with `--mmproj` are there) |
| **`llmcf-gateway` as the one door between every harness and the board's models**: besides the OpenAI interface, the Ollama interface (`/api/chat`, `/api/generate`, `/api/tags`, `/api/show`, `/api/version`, `/api/embed`, NDJSON streaming) and the Anthropic Messages interface (`/v1/messages` with streaming, tools and images; API versions `2023-06-01` and `2026-10-21` each answered like the real API, so `temperature`, `top_p` and `top_k` are refused under `2026-10-21` and passed on under `2023-06-01`; the key read from `x-api-key` or `Authorization: Bearer`), all translated to the board's llama-server; speech to text (`/v1/audio/transcriptions`) to the board's whisper.cpp server, text to speech (`/v1/audio/speech`) once the speech output block is there, images through the llm's `--mmproj`. On the board's loopback only: the gateway asks the kernel which account a connection belongs to and lets in only `llmcf` and the harness accounts the person entered (Ollama clients send no key), a key per harness on top where the client sends one; also on port 11434 (loopback, when free) so Ollama clients work unchanged; the local network still only with TLS and a token, never open on every address. Order: the scheduler first and the speech and image routes after it (both built), next the vision engine on the NPU (step 1 built, steps 2 to 4 below), then Ollama, then Anthropic. Done: the OpenAI-compatible speech and image routes (`POST /v1/audio/transcriptions` to the board's whisper-server with its own audio cap and the audio's type checked by its bytes; images as data URLs only in chat requests to the bundle's vision llama-server, never fetched from an address; the routing table in `/health`, counts per route in `/metrics`). Rest: the Ollama and Anthropic interfaces, the kernel-checked accounts, port 11434, text to speech once a speech output block has an HTTP server. | in progress |
| **`llmcf-gateway` as the board orchestrator of the models**: it does not only pass requests on and check them, it keeps the board out of the out-of-memory killer. A queue instead of a refusal at the in-flight limit (a fair order per harness by the kernel-checked account, a time limit, a capped length); the engines (LLM, whisper, vision) loaded and unloaded in turn when the measured memory cannot hold them together, the person's choice per case, while llmcf always checks and shows whether all together fits; the start and stop right through llmcf-install for the whisper and vision units too; the kernel's killer never reached: systemd memory limits, `oom_score_adj` (the model before the gateway), no new admission when free memory falls. Optional (opt-in, with a size cap from the measured KV cache per token): a waiting session's KV state saved to disk (llama-server's slot save and restore), 0600 in the service account and encrypted with a key held in memory only; it is only a cache that can be computed again, the session's memory is its text in the session store. RKLLM: the queue only. The gateway's unit ceiling comes from a measurement on the board (64 MB until then, plus room for the audio uploads and images in flight; measured on x86_64: 2.0 MB program, 3.3 MB resident at rest, 7.6 MB at the peak with 64 parallel clients all waiting in the queue, 53 MB with two 24 MB audio uploads in flight); no split of the core, separate programs only for big, seldom used parts (MQTT broker, MCP bridge) started on demand, where a measurement shows a gain. Done: the fair queue with its time limit and capped length (the client's own key, else the account the kernel names for its connection, else its address), the memory guard (free memory and memory pressure; new requests wait, never refused for it), the units' `OOMScoreAdjust` (the model's 500, the gateway's -500) and the llm unit's `MemoryHigh`/`MemoryMax` from its measured need, the gateway's limit derived from its own measurement once there is one. Done since: the engines in turn with the person's choice and the plan's check of all together and in turn (the gateway's `--engine` list, `--engines together|in-turn`, `bundle plan|build --engines`, the Board bundle window's checkbox), the start and stop right for the llm, whisper and vision units (the root action `allow_unit_control`: a polkit rule, or a sudoers file without polkit's rules). Done since: the speech and image requests through the same queue, memory guard and scheduler (row above), and in turn the engine units installed but not started at boot (the root action `install_unit` with `on_demand`): the gateway starts the language model at its own start and every other engine for its requests. Done since: the universal adapter (decisions of 2026-11-08): every model and engine type, future ones included, works with only a target module that builds it and a bundle block whose `reach` says how it runs and how it is reached, never a change of the gateway; the bundle generator makes the gateway's route table (`routes.json`, signed with the manifest, one `--routes` argument) from the chosen blocks and the board's measurement; the OpenAI adapters (chat with images, completions, models, embeddings, speech to text, speech output) and a generic checked passthrough with the declared limits (`http-json`, `http-multipart`, `http-sse`, `http-binary`), every route through the same queue, memory guard and scheduler; block kinds are open, every engine scheduled by its component id. Done since: a block proposed from a model (`runtime/bundle/propose.py`): the Hugging Face pipeline task (the library where there is none; a local folder's `config.json` or model card) gives the kind and the `reach` draft with conservative limits and the reason of every field, a detection-type model the notes on what its engine needs to know; the person reviews and writes it (`llm-cli bundle block propose ... --write`, Propose from model... in the Board bundle window), an AI only proposes (the optional read-only tool `propose_bundle_block` and a note the person sees), never writes or signs a block. Done since: the first new block reached only through its `reach`, `vision-rknn-detect` with the vision engine `llmcf-detect` (step 1 of the vision engine, posted images and image files; its cameras are step 2 below). Rest: the measurement step that measures the gateway on the board, extended to "measure the engines" (all engines together under load at the same time: memory, pressure, CPU, temperature and each engine's slowdown; a recommendation of together or in turn, the llm's slots and the number of whisper processes, kept with the board's limits, the person decides), with the CPU cores, NPU cores and GPU share split per engine and lazy loading as the default on small boards (an engine started on its first request and stopped when idle, `mmap` and never `mlock`, the vision projector only for images, the MCP server and MQTT on first access); polkit (0.106 or later) installed by the board installer as a dependency through the three ways, the sudoers rule only where the distribution has no polkit package; the KV state on disk; RKLLM's queue. | in progress |
| **Vision on the NPU, step 2: cameras and video**: USB cameras (MJPEG through MPP, libjpeg-turbo or the CPU) and CSI cameras through the ISP (`rkisp`, NV12, the node found by its name), RTSP and video files through MPP, RGA for scale, colour and letterbox with a CPU path; only the latest frame; the camera open only to the engine's account; video with the results drawn in as MJPEG over HTTP through a new checked gateway protocol `http-mjpeg`, and long streams in a pool of their own beside the requests in flight; the board installer's setup (the NPU runtime's pinned download with Rockchip's licence shown where the board has no runtime of its own new enough for the card, and the plan's choice between the two; NPU and camera overlays for the person's camera model as the board maker describes it, Rockchip's camera service `rkaiq_3A` and the tuning file, MPP and RGA, the engine's account, a restart only when needed) as typed root actions of llmcf-install, after the person's yes, with the camera table of the Rockchip target. | planned |
| **Vision on the NPU, step 3: the remaining single-model kinds**: instance segmentation with prototypes, pose, oriented boxes, classification, semantic segmentation, depth, CTC text and DB text detection, each with golden results from recorded tensors and in both layouts, and the blocks of these kinds for the same program. | planned |
| **Vision on the NPU, step 4: embeddings and two-stage pipelines**: embeddings (ArcFace, CLIP, re-ID) and a `pipeline` card that cuts the first model's results out of the frame, aligns them and hands them to a second model (OCR detection and recognition, faces and their embeddings, a detector and a classifier); open-vocabulary detection with the person's words embedded at conversion. | planned |
| **Vision on the NPU on a real board: the first NPU run** (waits for hardware): the engine on an RK3566 and an RK3588 with Rockchip's runtime; the FFI structures, the runtime and driver versions (whether a driver older than 0.9.2, as on some RK356x images, needs an exception), the core mask, and the results compared with the simulated NPU's on the same input and with the model zoo's own post-processing on the host, kept as new fixtures; the first bytes of an RKNN file confirmed. | planned |
| **Vision on the NPU on a real board: camera bring-up** (waits for hardware): each camera of the camera table on its boards through the board installer's setup (overlay, camera service, tuning file, restart), the first frames through rkisp and UVC, the MPP and RGA paths checked, the table's rows marked tested. | planned |
| **Vision on the NPU on a real board: performance tuning** (waits for hardware): zero-copy input from RGA into the NPU's memory and the native NHWC output through the runtime's zero-copy interface, frame rates and latency per kind and board, the engine's memory measured by "measure the engines" and the block's defaults (memory, frame rate, video size) set from it, together or in turn with the language model and whisper. | planned |
| **Vision on the NPU on the RV1103 and RV1106**: the mini runtime `librknnmrt.so` on uClibc systems (a tier-3 Rust target), INT8 and NHWC only; after the four steps. | planned |
| **The gateway's status for an AI and the token for network clients**: an MCP read tool with the gateway's labels, and the host's way to hand the bearer token of `bind lan` to a client the person names. | planned |
| **A wake word on the board, and the opt-in voice parts runnable**: whisper.cpp's server has no wake word (only an example that needs SDL2 and a microphone has one), so a wake word needs its own engine; NeMo and the GigaSpeech wake word are documented opt-in templates whose licence terms the person accepts first, never on by default, and become buildable with the speech output's engine. | planned |
| **An agent harness on the board as an opt-in bundle component**: the person's own harness (its own project and repository, never code in llmcf, never built by llmcf). Partly done: the take-in at a pin in every common format, recognised by its content (a folder, on Windows too with the executable bits derived from the content; zip, tar and its gzip, bzip2, xz and zstd kinds; 7z, rar, iso and cpio through 7-Zip, which both installers set up; disk images read in place, an ext rootfs without extracting it, only the harness's folder; encrypted archives with the person's password; a .bin installer as a harness of one file), the block `harness-agent` with the tree's checksum in the signed manifest, its own installer run as root through llmcf-install after the person's yes (its dry run first, its questions answered live, detached and re-attachable, watched with `strace` where the board has it and always before and after, flagged and reported before and after), by command, by mouse and read-only over MCP. The report says whether the installer brought its own account(s) and service unit(s) (a harness like Chimera makes its own system accounts and systemd units), and llmcf never nests an account or a service of its own around them; the records come back in full, in checked parts. Rest: an own Linux account and service for the harness made by llmcf-install, only as the person's opt-in and only when the installer brings neither (the run's report says so), never on top of one it brings; the gateway letting in the harness's account; a Mojo finding when 7-Zip is missing (Mojo checks no host tools yet; the take-in's refusal names the repair today). This is the board only: llmcf itself keeps Ditto, with a local model or a cloud API. | in progress |
| **Chimera's installer run through on a real aarch64 board**: Chimera is only an example harness, itself in development, but its installer must run through end to end under llmcf (take-in, upload, dry run, the run with its questions answered live, the report); so far it ran only as a dry run on an x86_64 computer. | planned |
| **A harness taken out of a container image**: an OCI or Docker image (OpenClaw is one example) from a registry or a saved image file; the manifest for the board's architecture (refused when there is none), every layer checked against its digest and applied in order into the quarantine folder with the whiteouts honoured, the image configuration's entrypoint, command, environment, working folder and user telling how the harness runs; then installed bare metal on the board, without Docker, as a harness of the bundle with the take-in's checks (quarantine, the list and its check, the refusals) and the same root-readiness and approval flow. Disk images are not extended: a harness is set up cleanly from its repository ([design](DESIGN.md#deployment-to-boards)). | planned |
| **Three ways for a harness installer to reach what it fetches**, the person's choice per board: (1) the board is online itself; (2) the board is linked only to the PC (cable, the PC's hotspot) and llmcf on the PC relays and caches what the installer fetches (packages, Python packages, downloads), every request logged; (3) fully offline: llmcf fetches beforehand on the PC what the installer needs for the board (packages, Python wheels for the board's architecture, cross-built by llmcf where no wheel exists, the harness's own release files), from a list the harness gives or from a recorded first run, puts it in the bundle and runs the installer against these local sources. | planned |
| **A portable model archive for a computer without a network (`llmcf-model/1`)**, beside modular tool packs: the archive carries one Hugging Face model at its exact commit with every file a conversion needs (weights, `config.json`, `generation_config.json`, the tokenizer files, the chat template, the model card), the licence and gating of the model and of its base model, a sha256 per file and one over the whole, and a signature; it reuses the pack format's manifest, signature, quarantine and archive checks with its own kind and limits, and is checked on import before the network-less conversion sandbox starts. The tool packs carry what the conversion runs with (container images, Python wheels, the llama.cpp commit) as their own modules, one tool pack for many models; they build on the existing checked image export and import and the dependency pre-fetch cache. A model archive is stored and carried by the person only, never uploaded: only recipes and target modules are shared, every person converts their own model. | planned |
| **Every long board action detached and re-attachable**, like tmux: deploy, bundle install, measurement and every other long step run on the board in their own session, a dropped connection never ends them, and the host attaches again and reads on from where it was (the harness run and the bench do this already). | planned |
| **Languages on the board like in llmcf**: German and English for the board's voice and texts, and language packs the person makes and adds (upload), checked before they are used. | planned |
| **A contract test for harnesses** that plug into the gateway, without any harness code in llmcf. | planned |
| **An optional session store on the board**, any time after the gateway is deployed: a harness's conversation is its memory and stays available; it is appended, never edited, and compacted by Ditto's rule (a summary that copies ids, numbers, versions and error messages exactly); "never logged" holds for the logs only. | planned |
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
| **Deep Ingest under a delegation**: a delegation that names start addresses lets an AI crawl exactly those without a question of its own; the opt-out checks, limits and the one-time notice still apply, every other crawl asks. | planned |
| **"Do not ask again for this model version"** as a tick box in the build question, on the stable warning codes. | planned |
| **A message box for every MCP client**: a note on every result plus read and reply tools. The wake-up exists: the build doorbell on the person's own MQTT broker carries only fixed keywords ("Build ready", "Error while building", "Build stopped"), the details come over MCP; later the MQTT broker of the Rust gateway (Rust parts) carries it. | planned |
| **An own account `llmcf` on the host and on boards**: the installer creates it and gives it the folders it needs (on Windows under the program folder with explicit permissions), so a path that reaches an AI is always the same; on a board the person enters the admin password once, the framework creates the account and its key and keeps the key, never the password; that account's key runs only llmcf's command gate and its exact sudo rules (as the framework key does since 3.3.0), so the account itself holds no other right. | planned |
| **Presence proof and access control as opt-ins**: a click is enough to confirm; PIN, password or fingerprint only when the person turns them on; user accounts in the framework only as an opt-in. | planned |
| **An own target template for the RK3566**: the LLM on the CPU (Cortex-A55, NEON, GGUF), RKNN only as an optional step for voice and vision models that never blocks the LLM build; the other Rockchip SoCs keep the standard template, updated to the newest toolkits. | planned |
| **Board updates through the board installer**: the bundle's idempotent installer (the board installer with llmcf-install) is the board's updater: run again, it skips what is done and installs and checks newer pinned versions of what it installs (runtimes, the vendors' SDKs, CUDA and the like where a target uses them); OS updates offered with a plain sentence and run as root steps of llmcf-install, waiting without a backup until backups are on (the backup's maximum age a setting, 14 days by default) or the person ticks the override for that board; Windows follows the same steps without OS updates. No separate update orchestrator: the one decided on 2026-10-22 was never imported and is dropped ([design](DESIGN.md#updates-backups-and-board-images-planned)). | planned |
| **Board backups pulled by the PC, two image kinds**: the PC pulls every backup over the deploy's SSH session, nothing is written to the board's own card; the image is built and shrunk on the PC (an unprivileged assembler; PiShrink, pinned, only for raw card images brought in from outside), the unshrunk image deleted once the small one is verified; a device backup restores exactly this board, a fleet image is generalised (new host keys and machine id at first boot, per-board values as variables from a list in the framework or a file on the boot partition). Also decided: the full backup right after a deploy; the device backup preselected in private mode, the fleet image in enterprise mode; per-board values from the framework's list by default, the boot-partition file only for a fixed address at first boot; a fleet image clears the tick for OS updates without backup. | planned |
| **The board side on Windows, after the Linux points**: once the remaining Linux points are done, the board side is ported to Windows for all three device kinds (x64 with an NVIDIA RTX GPU, AMD Ryzen Z1, Windows on ARM): Windows' own OpenSSH server installed like a dependency, the key line in `administrators_authorized_keys`, the command gate as a small Rust program, `llmcf-install` for Windows x64 and ARM64, a local account and a Windows service, Job Objects for the limits; later the Rust gate replaces the POSIX-sh gate on Linux too. Jetson stays on Linux. | planned |
| **Periodic board backups next to the fleet image**: the PC pulls each board's backup on a schedule, only when something changed, through the same container; the board only reads and streams (no `dd`, the Rockchip bootloader area copied raw, one exact sudo line for a fixed read script); a NAS login from the vault, never a config file; the catalog versions kept by the update orchestrator's modules in CI. | planned |
| **Starting without a terminal**: a Linux application-menu entry, a Windows shortcut without a console window (today it starts a batch file with one), and a crash dialog with "Open log folder" and "Copy details". | planned |
| **The Ditto console beside the chat**: one window with the chat and a separate console that shows what Ditto and external AI apps type and do, each area shown or hidden and resized by dragging, the sizes remembered; the existing feed and activity rows wired into it. | planned |
| **The Models tab as a real list** of downloaded models and builds (today a placeholder in `gui/main_window.py`). | planned |
| **Auto-Tuning**: quantization settings measured and tuned automatically (the tab in `gui/main_window.py` says "coming soon" today and is to be marked planned). | planned |
| **Start thresholds for Ditto's local model**: at least 8 tokens per second and a KL divergence of at most 0.05 (0.10 as the outer bound) on one fixed evaluation text, tuned by `ditto eval`. | planned |
| **Guided install of vendor toolchains**: the vendor's licence text shown, the person's own yes is the licence acceptance, never an AI's. | planned |
| **One settings window**: File > Settings... becomes the one place for every setting, so the person never needs the command line for it: the language (German or English) beside the form of address, applied at once; a page Ditto with the AI settings and Ditto's permissions and folders; a page Language model with the local runtime, the cloud providers with their keys and the sign-in; a page Update with the update check and "Update Framework". The existing widgets are moved there, never copied; the old entries (Ditto > AI Settings..., Security > Secrets..., Sign-in Overview..., Key Storage..., Tools > Maintenance > Update Framework...) open the matching page; the command line stays an equal path with the same settings (`llm-cli update`, `llmcf config set`, `llm-cli ditto login`), since everything in llmcf is possible through both the GUI and the CLI ([design](DESIGN.md#gui-and-the-persons-path)). | planned |

### Device runtime and more hardware

| Item | Status |
|---|---|
| **Rotation stage before quantization** (SpinQuant, QuaRot), right after the recipes: rotations are baked into the weights before conversion and quantization, only where the target format supports them (a capability of the format, not a fixed step). Why: 4-bit models of the Llama family keep their quality on small boards. The slot is there: the stage runner runs a target's `modules/rotate_module.sh` between the convert and target stages; the algorithm is still to come. | planned |
| **The generic ARM64 target on a real Raspberry Pi 4 and 5** (waits for hardware): a build for each board run on the board with its package (`test_model.sh`, `llama-server`), its tokens per second measured by the board test (`llama-bench`, shown as measured rows of the target's boards), and the recommended order of quantizations checked against those numbers. The target itself is built ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **The new board profiles on real boards** (waits for hardware): a build for the Raspberry Pi Zero 2 W (`bcm2837`), an Allwinner H616 or H618 board (Orange Pi Zero 2, Zero 3, Zero 2W, Banana Pi BPI-M4 Berry, BPI-M4 Zero), an Amlogic S905X3 board (Banana Pi BPI-M5, BPI-M2 Pro) and an Orange Pi or Banana Pi board with a Rockchip SoC run on the board, its probe checked against the board it maps to, its tokens per second and, on the Zero 2 W, its memory measured by the board test, and the recommended order of quantizations checked against those numbers. The profiles are built ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **The memory estimate before a build starts**: the Builder, `llm-cli build start` and the AI's start question show the estimated size of the model at the chosen quantization against the board's memory before the build (today the warning comes after the build, from the model file's real size), still a warning and never a refusal; the person decides ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **A board profile for the Raspberry Pi Zero 3 W, once released**: its device tree in Raspberry Pi's kernel fork names the BCM2711 (four Cortex-A72, as in the Raspberry Pi 4), not its RAM; the profile is written from Raspberry Pi's published specifications at release, not from reports ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **The Raspberry Pi AI Camera (IMX500)**: on Raspberry Pi targets, with the Raspberry Pi target modules, through Raspberry Pi's own stack (the kernel driver over I2C, the sensor's network firmware downloaded at every start, package `imx500-all`, and the RP2040 on the module through Raspberry Pi's SPI driver); on Rockchip boards a port of the imx500 driver, the firmware download and the RP2040 SPI driver to the Rockchip vendor kernel, whose feasibility is checked first; until then it is not usable there ([design](DESIGN.md#on-the-board-gateway-voice-and-mcp-planned)). | planned |
| **Pairing a Windows board during the probe.** The probe kit's pairing covers Linux boards (the `.py` and the `.sh`); `hardware_probe.ps1` says that pairing was skipped. Rest: the framework's key line and host-key fingerprints for Windows' own OpenSSH server (`administrators_authorized_keys` and its ACL), once the Windows target port is there. Why: a Windows board deserves the same first login without a fingerprint question. | planned |
| **Targets from the hardware probe, proven on the board.** A new target starts from the probe you run on the board (also without network, the file carried back) or, if you want, from Ditto probing it over SSH; a passed benchmark on the board is the proof, shown as "tested on <board>" in the Community Hub and carried in packs. Why: the maintainer does not own most boards, and only a run on the real board shows that a target works. | planned |
| **Runtime on the device, the rest.** The first run of the self-built board runtime on a real RK3566 board; the device runtime inside deployment packages; the runtime image for Docker deployments built locally from a device build (instead of the pulled runtime image); device benchmarks and a measured quantization recommendation per board; a build on the board when no builder base is old enough for its C library; Vulkan on Mali only after llama-bench shows that it beats the CPU build; MCP tools for probing, deploying and benchmarking devices. | planned |
| **Self-built llama.cpp, stage 2.** CUDA builds (NVIDIA's redist archives, the toolkit chosen by driver, compute capability, host compiler and CMake), Windows builds (Clang for the CPU backend, MSVC only as CUDA host compiler; until then Ditto's local model on Windows and macOS only through the `--prebuilt` opt-in), macOS (untested), host benchmarks that choose Ditto's model tier, and MCP tools for the runtime. | planned |
| **Pairing during the probe, the rest:** pairing by code on by default in a probe run with sudo and without a probe kit next to it (with the person's opt-out); today the person starts it with `--pair-code`, while the probe kit pairs by default. | planned |
| **A pairing is kept only when the board's root step went through**: today llmcf keeps a pairing whose root step on the board failed after the exchange; it is to be dropped then, with the reason, so the person pairs again. | planned |
| **The maintainer's test on real hardware and with a real agent**, after the merge to main: pairing a board both ways (the probe kit and the short code), a real deploy of a build to a real board, and one real ACP agent (see "A real end-to-end run with a third-party ACP agent" under Ditto's context); so far these ran only against simulated boards and agents. | planned |
| **Both rights paths of the engine switch on a real board**: the gateway's right to start and stop exactly its bundle's engines, once through polkit and once through the sudo rule, tested on a real board. | planned |
| **More Rockchip SoCs for RKLLM** (RK3562, RV1126B) with a per-SoC table of supported types. | planned |
| **The module wizard checked from start to end**: the wizard, `llm-cli module create` and the tool `create_target_module` each run through with a hardware probe, the generated target validated and built once in the build image, what fails fixed ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **Target templates for NPUs and GPUs made by the module wizard**, after the check and the "edit all files" step: from the hardware probe and the crawled vendor documentation, the conversion and packaging stages, the format's capabilities and `sdk:` needs and the Dockerfile's toolchain steps (Hailo HEF, TensorRT, QNN and the like), Ditto's proposal or the person's code through the edit step, marked untested ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **Chip variants and model-size guidance in the board lists**: the RK3582, RK3588S and RK3588J shown as entries pointing to the RK3588 profile with the reason (tooltip, note line, field for an AI); a board profile's guidance on model sizes per task, for example 135M to 360M models on the Raspberry Pi Zero 2 W for narrow tasks such as MQTT switching, never a refusal ([design](DESIGN.md#targets-hardware-and-templates)). | planned |
| **NVIDIA GPU target**: GGUF with CUDA for RTX GPUs of the 20 and 30 series (CUDA version chosen by driver), vLLM formats AWQ and GPTQ, EXL3, and native deployment to Windows machines with NVIDIA GPUs. | planned |
| **GPU acceleration** for the importance matrix and the RKLLM export. | planned |
| **AMD RDNA3 APU target** for the Ryzen Z1 Extreme: llama.cpp with Vulkan and HIP, Linux container and native Windows deployment; ROCm also where AMD does not support the GPU officially, through the known workarounds (such as `HSA_OVERRIDE_GFX_VERSION`), the person deciding whether it is useful. | planned |
| **The pre-flight check on real hardware** (waits for hardware; the check itself is built, [design](DESIGN.md#build-pipeline-and-target-modules)): its verdicts confirmed on a PC whose NVIDIA driver supports an older CUDA major version than the GPU image's and on an RK3588 whose NPU driver is below 0.9.2, and RKLLM's NPU driver minimum added to the Rockchip target's `sdk:` entry once a Rockchip source names one. | planned |
| **Benchmark window** (`orchestrator/gui/benchmark_window.py`, in development): run llama-bench and show the results as a table; still to be finished on top of the runtime on the device and made reachable from the GUI. | planned |
| **One SSH connection shared by a board step's commands** (OpenSSH `ControlMaster`, as an option): the OpenSSH fallback logs in once per step instead of once per command; connection sharing stays off until then, and the framework's own SSH settings refuse it. | planned |
| **NPU converters for voice models on Rockchip** (piper, vosk, whisper), the next numbered step after the RK3566 template's slot for NPU voice models. | planned |
| **The Hailo line in the GUI's scan**: the same "Hailo:" line `llm-cli scan` prints, with the Hailo wave. | planned |

### LoRA and large models

| Item | Status |
|---|---|
| **LoRA adapters**: merge them into the model or keep them as an adapter (or both), with an own streaming merger, GGUF adapters for `llama-server --lora`, RKLLM LoRA, and a `lora` block in the target capabilities. | planned |
| **Optional LoRA training** with a lean trainer of our own: ideas and solutions from Unsloth only within its licence (from its Apache-2.0 core, nothing from unsloth_zoo (LGPL) or Unsloth Studio (AGPL)), and no Unsloth installation. | planned |
| **Large models**: a check of disk space and RAM before a build, reuse of F16 intermediates, GGUF as input (passthrough and requantization), per-tensor quantization recipes checked by KL divergence, an importance matrix computed, taken from Hugging Face or loaded from a file, and tokenizer and chat-template checks after each build. | planned |
| **airllm, evaluated as an option**: an evaluation first decides where airllm gives llmcf real added value, for example converting large weights on a computer with little memory or checking a large converted model layer by layer; each use that passes becomes an optional part in its own pinned environment, each use that fails is recorded with its numbers and stays out ([design](DESIGN.md#build-pipeline-and-target-modules)). | planned |
| **Bring the pip cache back in CI once the quota allows**: `cache: pip` in the setup-python steps of `ci.yml`, `security.yml` and `retrieval-eval.yml` is switched off for now, because the account's cache and egress quota ran out and a job hung at the cache restore ([design](DESIGN.md#tests-ci-and-the-windows-hazards)). | planned |

### Open questions for the maintainer

Asked and not answered yet. Nothing of them is built or decided before the answer; the design names each where it
belongs.

| Item | Status |
|---|---|


## Next - build service, queue and modes (planned; starts right after the architecture consolidation)

The steps come in this order. Decisions that are still open are written into their row; they are asked when that
step starts.

| Item | Status |
|---|---|
| **Step 1 - Build service**: one background program per account owns every build; the GUI, the command line and the MCP server become its clients. A crash of the GUI no longer ends a build. The service can be stopped and restarted without losing anything (the state is already kept in files: build records, build requests, the reservation ledger, job summaries), and a build resumes at its last finished stage of the four-module build (source, config, convert, target). Why: today a build runs inside the program that started it, and ends when that program ends. Also decided: when the service cannot start, a build is refused with the reason and a next step (no fallback to the old way); Ctrl+C at the terminal only ends watching; closing the GUI keeps builds running with a short note; Ditto's builds run on like the person's; an AI never unlocks the vault; the service becomes the home of the local API server. | planned |
| **Step 2 - One queue across all programs**: builds wait in one queue in the build service and are admitted by RAM, CPU and disk. The disk part exists (the reservation ledger in `orchestrator/Core/disk_plan.py`); the queue extends what is there (the queue in `orchestrator/Core/orchestrator.py`, the build engine's slots, the request store) instead of a second structure. No Celery. Why: several programs (GUI, CLI, AI apps) start builds on one computer. | planned |
| **Step 3 - Private and enterprise mode**: private mode is the default, enterprise mode is optional. The mode is chosen in the installer (`install.sh` and the Windows installer) and changed in File > Settings...; an AI can read it but never change it. It reuses the advanced/enterprise scaffolding that already exists in `orchestrator/Core/config_manager.py` (decision U7). Why: steps 4 and 5 bring company features that a private installation does not need by default. Partly done: the setting `framework_mode` (private by default, the person's alone) with its one reader and writer in `Core/config_manager.py`, the choice in both installers, in File > Settings... and with `llmcf config set framework_mode`, and the board key tiers follow it (enterprise mode refuses the weaker tiers at pairing and at every use). Rest: the company features of steps 4 and 5. Still to do: the installer asks for its language first, and a desktop install gets a small graphical page for the mode. | in progress |
| **Step 4 - Secret backends**: HashiCorp Vault and CyberArk Conjur (enterprise mode; opt-in in private mode). Values are fetched when they are used, kept in memory only and never written to the local vault (`secrets.store`); every fetch is audited, and leases are renewed and revoked. Later: dynamic secrets and signed SSH certificates for boards. Why: companies keep secrets in a central store and must see every use. Open decisions, asked when this step starts: audit rows in CI mode; one fetch per operation; references only in enterprise mode; internet-hosted backends; where the backend's own sign-in credential is kept. | planned |
| **Step 5 - Kubernetes and Nomad**: opt-in places to run builds, next to the local Docker builds that stay the default. They use images the company builds itself from the framework's Dockerfiles into its own registry, pinned by digest, never images from the community hub. Why: a company may want builds on its own cluster. Open decisions: both modes or enterprise mode only; through `kubectl`/`nomad` or their HTTP APIs. | planned |
| **Step 6 - Own keyring service on Linux**: the framework ships its own keyring service on Linux (decided on 2026-10-15, not built; Windows keeps the Windows Credential Manager); after step 4. Why: many Linux installations have no usable keyring, and the vault refuses one that hangs, keeps nothing or stores in plain text. Open decision: build it or keep the OS keyring. | planned |
| **Step 7 - Newest fitting versions kept automatically**: every dependency (Python, CUDA, Docker base images, toolkits, GitHub Actions) stays on the highest version that fits, and each update is tested before it lands. Done for the Python dependencies: ranges up to the next major, a 5-day cooldown, a fresh resolve with audit, self-check and rollback on every installer run, and a weekly update run that lands the lock only when green ([design](DESIGN.md#installer-platforms-and-dependencies)); a plan for the rest is being prepared. Why: old versions collect security advisories, and an update must not break anything. | planned |

## 3.5.0 - Supply chain and community (planned)

| Item | Status |
|---|---|
| **Trivy** pinned by digest (it already scans a saved archive, without the Docker socket). | planned |
| **Pinning inside images**: base images by digest, hashed lock files per venv. | planned |
| **CycloneDX SBOM** and artifact provenance. | planned |
| **Dependabot cooldown** for LLM SDKs. | planned |
| **A second secret scanner in CI** (the gitleaks binary, pinned and checksum-verified) and GitHub push protection. | planned |
| **Signed updates** of the framework itself (`orchestrator/utils/updater.py` today: update check, then a git fast-forward and a fresh run of the installer with the person's flags, which resolves the dependencies anew; `llm-cli update` and Tools > Maintenance > Update Framework...), finished as verified updates of a signed release instead of a plain `git pull`. Toolchain images are not part of it: the framework shares recipes, never compiled images, so they stay built on your machine from the target's Dockerfile; the settings `docker_registry` (empty by default) and `docker_namespace`, which nothing reads, are kept. No default names a registry image: the image settings name local images (`llm-framework/...`) or third-party images such as Trivy and Qdrant, and the `docker` block of a target's `target.yml` names a local image without a registry. | planned |
| **Shared healing knowledge**: build errors and their fixes, cleaned of personal data, shared with the community as knowledge packs (opt-in, scrubbed and signed, through the pull-request hand-off), so self-healing grows with use; a shared fix stays advice and is applied only through the fixed strategies with the person's approval. | planned |
| **Community knowledge as an opt-in download**: the signed community index gets the field `knowledge_offer` (`packs`, `download` or `both`, starting at `both`), set by the maintainer in the Community Hub's Maintainer page or with `llm-cli zoo maintainer offer`, so the maintainer can change the offer at any time without a framework release; the knowledge archive as a signed index entry; the person's setting `community_knowledge_download` (off by default), and the download when the knowledge base is used, at most once a day, pinned, through the quarantine, with a snapshot and its own holder. The signed packs stay ([design](DESIGN.md#community-recipes-and-packs)). | planned |

## Rust parts (planned)

The core stays Python. Parts that stand alone at a trust boundary and are not built yet are written in Rust; other
parts move only when a measurement shows a gain (maintainer decision of 2026-10-16).

| Item | Status |
|---|---|
| **Network MCP gateway in Rust.** One gateway process in front of the Python MCP server for clients on other devices: TLS, and sign-in by OAuth, a named token or a pre-shared key (an HMAC signature on every request). Why: this part faces the network; Rust gives it memory safety and a small, fast process. | planned |
| **MQTT broker inside the gateway (rumqttd).** It wakes AI clients with fixed keywords only (the details come over MCP), and other applications can subscribe and add their own feeds. It takes over the build doorbell's topics and payload (`<prefix>/v1/build/<request_id>/end`, `<prefix>/v1/events`, `<prefix>/v1/status`), so clients keep their subscriptions, and the person's own broker becomes optional. It replaces the amqtt broker that was planned inside the Python process. Why: no second server to run, and the broker sits where the network connections arrive. | planned |
| **Root helper on the board in Rust for the runtime deploy**: the deploy's systemd unit still goes through llmcf's sh helper llmcf-root; it moves into a static binary with a fixed set of subcommands as well (the bundle's llmcf-install and its sudoers rule for this helper only are done). Why: no general sudo on the board, and a static binary runs on every board, whatever its C library. | planned |
| **The framework's Rust parts shipped prebuilt and signed.** CI makes them release assets of this repository and signs them keyless with Sigstore; their checksums are kept in the repository, and the installer checks signature and checksum before it uses a binary. Building them yourself stays possible as an opt-in. Why: no Rust toolchain on your computer, and every binary can still be checked. | planned |
| **Build egress with allowed destinations.** Build containers run in an internal container network: a small DNS filter in Rust (on hickory-dns) resolves only the names on an allow list (sources and model hubs the build needs) and logs every lookup per build; offline mode lets it resolve local names only; a network rule closes the way around it by raw IP addresses. It reuses the framework's one locality rule, runs only as a companion container of the build network and never changes the host's own DNS. Why: a build script from a third party can no longer call home or reach an address the person did not allow, and every build shows which names it asked for. | planned |

- **No prebuilt Docker images for targets.** Target images are still built on your computer from the target's
  Dockerfile and never pushed to a registry. Why: the framework shares recipes, not images, and an image would need
  a rebuild for every update.
- **No bundled Caddy and no system-wide Unbound.** Why: TLS for the network parts already comes from the Rust gateway (rustls, its own certificate, token), so a second proxy would duplicate it with a larger attack surface; a system resolver would take port 53 from systemd-resolved or dnsmasq and change the person's computer. Whoever already runs Caddy in front of llmcf can keep it.
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
  - **Newly researched formats and runtimes**, as candidates: NVFP4 and FP8 (llm-compressor, ModelOpt),
    ExecuTorch, MNN-LLM, MLC LLM, ONNX Runtime GenAI and OpenVINO with NNCF. Why: new quantization methods
    and runtimes came after the framework was first made, and it must know them.
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
  - **A bot that proposes new models as a tested pull request**, after a compatibility test; it never takes anything
    over by itself. Why: new models appear faster than releases.
  - **From the agent-papers recon:** a typed build-failure summary, fault injection, a retrieval red team,
    OpenTelemetry spans, measuring tool confusion, a note on steering across MCP servers, retrieval ablations and a
    constrained-repair experiment. Why: they make Ditto's behaviour measurable beyond the first wave.
  - **A k3s agent in the fleet image**, for people who spread containers over a fleet. Why: Kubernetes spreads
    containers, not OS images, so it is an addition to the image, not its way out.
  - **A QEMU smoke test of cross builds in CI**, never on your computer. Why: a broken cross build is found before it
    reaches a board.
  - **An "edit all files" step in the module wizard**, with a diff and the checks run again: the stage modules,
    `target.yml` (with its `stages:` slots) and the Dockerfile. The stage runner is never among them: it is the
    framework's runtime layer, the same for every target. Why: vendor paths and flags can be finished before the
    module is saved.
  - **The first release of llmcf-setpriv for the toolbox way `fetch`**: the maintainer pushes a tag `tools-v<version>`
    (the release workflow builds, checks, attests and uploads it), then its URL and sha256 go into
    `orchestrator/runtime_layer/toolbox/prebuilt.json`. Why: until then `fetch` refuses plainly ("no release yet")
    and the tools are built on this computer (`build` or `build-rust`, needs the network once).
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
