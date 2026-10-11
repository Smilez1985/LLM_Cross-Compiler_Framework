# Changelog

All notable changes to this project are documented in this file.
German version: [CHANGELOG_DE.md](CHANGELOG_DE.md).

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Planned work is listed in the [roadmap](docs/BLUEPRINT.md#roadmap), not here.

## [Unreleased]

### Added
- **Ditto uses the framework tools**: in the chat (GUI) and with `llm-cli ditto ask "<question>"`, Ditto can
  list targets, check capabilities, start and cancel builds, read logs, search the knowledge base and run the
  doctor, always within your tool permissions. Tools that need a confirmation show their title and every
  argument (a dialog in the GUI, a prompt in the CLI; denied without a terminal).
- **AI settings**: providers from `configs/llm_providers.yml`, a model list loaded live from the provider
  (free text still works), effort, privacy level for cloud providers, offline mode and a fallback chain.
- **Provider layer for AI** (`orchestrator/llm`): one client for every provider, built on the official
  `anthropic` and `openai` SDKs (OpenAI-compatible servers through `openai`). Providers are data
  (`configs/llm_providers.yml`): model and effort per task, retries, a fallback chain, structured answers
  validated against schemas, and a privacy filter for cloud providers (setting `ai_security_level`): secrets
  are always redacted; addresses, e-mail addresses, logins, user names, host names and serial numbers are
  replaced by stable placeholders that are mapped back locally in the answer.
- **Local knowledge base** on local embeddings (FastEmbed, ONNX on the CPU) and an embedded Qdrant store: no
  vector-database server and no cloud embeddings needed.
- **Managed local model** for Ditto's offline mode: `llm-cli ditto runtime install|start|stop|status` runs a
  pinned llama.cpp `llama-server` on 127.0.0.1 (build chosen for the hardware: CUDA, Vulkan, Metal or CPU;
  ROCm on request; per-start API key in a private file), and `llm-cli ditto models list|download` fetches Qwen3.5 models
  at pinned revisions, checked by size and sha256. Model tiers go down to 2-bit models for weak hardware
  (3 GB RAM). The "local" provider follows the running server without configuration.
- **Ditto evaluation** (`llm-cli ditto eval`): fixed cases (build-log analysis, build recommendations, module
  proposals, chat with tools, prompt injection) graded by rules, against the configured provider or per local
  model tier (`--tier small --tier small-q2`). Nothing is built; `--json` writes the report.
- **Claude workspace ID** (AI settings field "Workspace ID", setting `ai_anthropic_workspace_id`): Claude API
  keys that are valid in every workspace of the organization now work. The ID is sent as the
  `anthropic-workspace-id` header on every request made with the API key to the Claude API, the model list
  included. An `ant auth login` profile keeps its own workspace, and other servers never receive the ID.
- **How credentials are protected is your choice** (setting `secret_storage`, or `LLM_SECRET_STORAGE` for
  one run): the OS keyring (default), a credential helper command (`secret_helper`, e.g.
  `pass show llmcc/vault-key`, run without a shell), a passphrase asked once per start (scrypt), a key file
  next to the vault (`insecure`, not encrypted at rest) or memory only (`ephemeral`, for CI). Nothing is
  picked automatically: without a usable keyring and without a choice, the framework stops and names the
  options. `llm-cli doctor` warns about the key file and reports memory-only storage.
- **Documentation in English and German** for every document (README, blueprint with design and roadmap, CLI
  usage, features, contributing, module developer guide, SSOT, changelog), checked by `tests/test_docs.py`:
  every document has its twin, links and anchors resolve, both changelogs list the same versions, and the
  roadmap names only what comes.

### Changed
- **Ditto runs on the new provider layer** (official anthropic and openai SDKs) instead of LiteLLM, and the
  built-in Transformers model for offline mode is gone (offline mode uses local providers: Ollama, the
  managed llama-server, a server on your network). Healing analyses, module proposals and build
  recommendations are validated structured answers; an invalid answer is an error and is never applied.
- **Chat history** is append-only and kept per user; a long conversation is summarized into a new one.
  Histories of earlier versions are migrated.
- Provider errors are explained in plain language, e.g. a Claude API key that needs a workspace ID, a
  workspace the key cannot use, a key that is wrong, revoked or expired, or an `ant auth login` profile that
  needs a new sign-in.
- **Claude API sign-in**: a stored API key is always used first. An `ant auth login` profile is used only when
  you choose it (`ai_auth_anthropic: ant_profile`); before, a profile on disk silently took the place of the
  stored key, and with it the account that is billed. The earlier name `oauth` still means the profile, in the
  setting and in a provider catalog in your config directory.
- Ditto's prompts: build failures are attributed to the build machine or the device correctly, build
  recommendations know which inputs each format accepts (an ONNX model is not sent to GGUF), questions about
  targets and builds are answered from the tools, and module proposals use compiler flags rather than the
  CPU feature list.
- Local model: effort "low" turns thinking off (Qwen3.5 on llama-server otherwise thinks at full length, which
  takes minutes on a CPU); "medium" and up keep it.
- Dependencies: `anthropic` 1.13.0, `openai` 3.28.0 (was 2.54.0) and `fastembed` 0.8.1 (local embeddings,
  ONNX on the CPU) are pinned exactly; pip-audit reports no known vulnerabilities.
- **Credential vault format v2**: every entry carries a key id and the vault key can be rotated (every entry
  is re-encrypted, the old key is removed; an interrupted rotation is completed at the next start). Each
  vault keeps its key in its own keyring entry, so rotating it or changing its storage never touches another
  vault (for example of another `LLM_FRAMEWORK_HOME` folder); a store of an earlier version moves to its own
  entry at its first rotation. Provider keys are stored as credential records under
  `cred/<provider>/api_key`; stores and key names of earlier versions are read as before and converted on
  the next change.
- Reading a secret is written to the secrets audit log once per process and name, not on every access.

### Removed
- Ditto's generated filler calibration data (builds use the shipped calibration text or your own dataset).
- `litellm` and `tiktoken`, and with them 19 indirect packages (aiohttp, boto3, jinja2 and others).
- **The Claude subscription provider `claude_code`** (Ditto through `claude -p` on Claude Pro/Max plan
  limits): Anthropic does not allow other apps to use Claude subscription limits without approval. A saved
  `ai_provider: claude_code` (or `ClaudeCode`, `Claude Code`), the same name in the fallback chain or in
  `--provider`, now stops with a message, shown once in the AI settings, that names the alternatives: a Claude
  API key (Max and Team plans include monthly API credits for a linked Console organization), Claude on
  Bedrock, Vertex or Foundry, or using the framework from Claude Code itself (`llm-cli mcp config`). It never
  switches to the Claude API on its own. A `claude_code` preset left in a provider catalog in your config
  directory is skipped with a warning; the other providers keep working.

### Fixed
- Secrets store: a store of an earlier version that cannot be decrypted is reported by `llm-cli doctor`
  instead of being merged unreadable into the per-user store.
- Reading or storing a secret no longer fails in a container whose user has no name (no `USER` variable and
  no passwd entry).
- On Windows, saving a secret no longer fails when another process or a virus scanner has the store open for
  a moment.
- `llm-cli secrets set` says where the secret went (the encrypted vault, or memory only with `ephemeral`)
  instead of always "Keyring".

### Security
- Ditto discards a healing fix or a build recommendation that contains a dangerous command (a download piped
  into a shell, deleting the root or home folder, world-writable permissions, overwriting a disk), whatever a
  build log or a model name told the model.
- Requests to local model servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment.
- Local model downloads are refused unless size and sha256 are pinned; the server binary archive is verified
  the same way and installed atomically.
- `OPENAI_CUSTOM_HEADERS` and `ANTHROPIC_CUSTOM_HEADERS` are dropped at startup of the CLI, the GUI and the MCP
  server, with a warning, unless the setting `ai_allow_sdk_env_headers` is true (a quoted `"false"` counts as
  false; settings that cannot be read count as false): they could replace the stored API key and reach every
  server, local ones included. The OpenAI and OpenAI-compatible adapters also send the stored key as an
  explicit `Authorization` header.
- `ANTHROPIC_LOG=debug` and `OPENAI_LOG=debug` are lowered to `info` unless `--debug-http` is given (the SDKs'
  debug logs show request details).
- Claude Admin API keys (`sk-ant-admin01-`) are refused before any request is sent, also when they were stored
  with surrounding spaces.
- The credential vault is changed under a cross-process lock: the GUI, the CLI and an MCP server writing at
  the same time no longer lose each other's changes.
- A keyring that keeps nothing (such as `keyring.backends.null`) or does not answer within 3 seconds is no
  longer used; before, secrets stored with it were unreadable after the next start. A keyring backend that
  stores without encryption (`keyrings.alt`) is not used either: that is `secret_storage: insecure`, and
  only you can choose it.
- On Windows the vault key stays on the local machine instead of roaming with the user profile.
- The secrets store refuses a location inside a git work tree (set `LLM_FRAMEWORK_HOME` elsewhere).
- Secret names with line breaks or control characters are refused, and the secrets audit log escapes them,
  so a name (or a user name) cannot forge audit entries.

## [3.0.0] - 2026-10-10
**Repair, security and MCP release.** The build pipeline works end to end again (verified with real
conversions), every known dependency vulnerability is gone, AI clients can drive the framework over the
Model Context Protocol, and conversion containers run in a sandbox.

### Added
- **MCP server** (`llm-cli mcp serve`, official SDK `mcp` 2.3.0, spec 2026-07-28): Claude Code, Claude
  Desktop or VS Code can list targets, check capabilities, start/cancel builds, read logs and artifacts,
  search the knowledge base, run the doctor and decide on self-healing proposals. stdio by default;
  optional Streamable HTTP on loopback with a per-user bearer token. `llm-cli mcp config` prints the client
  setup, `--write claude-desktop` adds it to Claude Desktop.
- **Tool registry with permissions and audit log** shared by Ditto and the MCP server: typed tools with risk
  tiers (read, local write, external, forbidden), permissions asked once at Ditto's first setup (GUI dialog,
  `llm-cli ditto setup`, `llm-cli tools permissions`), confirmations via MCP elicitation, and an HMAC-chained
  audit log (`llm-cli tools audit --verify`).
- **Target capabilities** (`capabilities:` block in `target.yml`, pydantic schema): boards, formats,
  quantizations per board, defaults and calibration needs. GUI, CLI, build service and MCP tools offer and
  validate exactly what a target declares.
- **Build service**: one typed entry point for builds (validate, start, status, cancel, logs, artifacts).
- **`llm-cli doctor`**: deterministic health checks (Docker, disk, targets, security, tool permissions,
  calibration data) with fix hints, tiered automatic fixes and JSON output (inspired by "Mojo" in rag4ll).
- **Curated calibration text** (`data/calibration`, MIT, checksummed German/English/tool-use sets) for
  importance matrices.
- **Build manifests**: every build completes `build_info.json` with its model files and SHA256 hashes; the
  deployment window lists builds from these manifests.
- **Real build cancellation and per-stage timeouts**.
- **SSH trust on first use**: host key fingerprints are confirmed by the user and stored in the framework's
  own `known_hosts`; a framework Ed25519 key is installed on the device once ("pairing").
- **Real CI workflows** (lint, tests on Ubuntu 3.11-3.14 and Windows, bandit, pip-audit, Docker build and
  smoke tests including the sandbox) and Dependabot.
- Python 3.14 support; per-user state directory `~/.llm-framework` (`LLM_FRAMEWORK_HOME`).

### Changed
- **Rockchip pipeline rebuilt** and verified end to end: llama.cpp v0.6.0 (GGUF incl. IQ types and
  importance matrix), RKLLM-Toolkit 1.3.1 and RKNN-Toolkit2 2.3.2, each in its own venv; models are staged on
  the host (Hugging Face repo or local path), format dispatch `gguf`/`rkllm`/`rknn`, board-aware defaults.
- Image builds use BuildKit when available and report the real cause of failures; per-build images are
  removed after the build.
- The calibration dataset is separate from the importance matrix (imatrix for GGUF only, dataset passed to
  RKLLM/RKNN directly).
- The stored Hugging Face token reaches builds (one shared build engine).
- Relative data paths resolve against the installation directory, not the working directory.
- Self-healing: proposals wait for an explicit decision (GUI dialog, CLI prompt or MCP tool); only fixed,
  framework-defined fixes are applied (fewer compile jobs after an out-of-memory image build, cleanup of
  framework images); AI suggestions are advice only.
- Ditto: errors and refusals raise instead of being shown as answers; API keys are passed per call;
  knowledge-base text, logs and probe files go into the user turn as sanitized untrusted blocks.
- Dependencies updated with hashed pins (Poetry 2 lock, `requirements*.txt`); `pyside6-essentials` instead
  of the full PySide6; `pydantic` and `mcp` added.
- Installers and the smoke test require Python 3.11-3.14.
- Single version source (`orchestrator.__version__`).
- **Configuration is never read from the working directory** any more: `LLM_FRAMEWORK_CONFIG_DIR`, then the
  installation's `configs/`, then `~/.llm-framework` (`framework.yml` in the current directory is ignored).
- The secrets store lives in the per-user folder (`~/.llm-framework/secrets.store`, copied over once from the
  old location); the audit log key has its own keyring entry.
- Capabilities: `default_board` (the board the build scripts use when none is given), per-format quantization
  aliases (e.g. `INT8` -> `Q8_0`), case-insensitive board names; CLI, build service and GUI submit exactly the
  values they validated. IMatrix without an own dataset is valid again (shipped calibration text).
- `build start -o` resolves against the working directory, the configured default against the installation;
  `deploy list-artifacts` and `deploy package` read the build manifests in the configured output folder.

### Removed
- LangChain, python-dotenv, pywin32 and winshell dependencies.
- The stored SSH password (`target_password`) and its menu item: devices use the framework key.
- Ditto's generated calibration filler (it downloaded a URL that returns 404 and fell back to repeated
  sentences).

### Fixed
- Many crashes and dead paths: build status enum, GUI builds stopping when a slot returned, healing dialog,
  chat window (sent each question twice, crashed on every message with PySide6 6.12), module wizard import,
  CLI hard exits and log levels, deployment of folders and archives.
- RKNN: dataset mounts no longer replace the model mount; JSON datasets keep their suffix; stale artifacts of
  other boards are never deployed.
- Entrypoint restart loop when the host user is root; checksums with Windows line endings.
- Cancelling a build can no longer be undone by a later stage (security scan, image build and stage
  containers are stopped); only real read timeouts count as timeouts; a retry after a healing fix keeps its own
  build id and log; a damaged audit database or per-user folder no longer stops the framework from starting.
- Audit log: removed newest entries stay detectable after later calls; no false tamper alarms after a failed
  commit, a failed anchor write or a write with another key; auditing goes on after the database was moved
  aside; `llm-cli tools audit --archive` starts a new chain and keeps the old one checkable.
- Secrets: the store of an earlier version is taken over exactly once (also into an existing per-user store),
  and a damaged one is reported; a running MCP server sees secrets that another process added or deleted;
  nothing is written into the working directory any more (secrets audit log, model folder).
- Tool permissions: a deeply nested or dangling permission file denies every tool like other damage.
- MCP: an explicit "no" stays final even if the permissions change meanwhile; every refusal is audited; errors
  while writing the Claude Desktop config are reported without a traceback; `XDG_CONFIG_HOME` is honoured on
  Linux; `-v` no longer puts log lines on the protocol channel.
- Builds: a cancelled build leaves no manifest or archive behind; a finished build frees its slot before its end
  state is visible; links in the output folder are never followed; the GUI sends no board for a target without
  capabilities unless one was typed.

### Security
- **Sandboxed conversion containers**: host user instead of root, no network, no capabilities,
  no-new-privileges, read-only image filesystem, process/memory limits.
- Crawler: SSRF protection with DNS-rebinding-safe connection pinning, robots.txt per RFC 9309, redirect and
  request limits.
- Module generation: strict validation of names, images, packages, flags and shell code (deny-list,
  `bash -n`), confinement to `targets/`, no overwriting of existing targets.
- Chat output is escaped; links open only after confirmation.
- Archive extraction blocks path traversal, links and devices; secrets are redacted in logs.
- pip-audit reports no known vulnerabilities (279 before); GitHub Actions pinned by commit SHA.
- A project folder can no longer change the settings of an MCP server started inside it (e.g. source
  repositories of the build toolchain).
- A damaged secrets store is never overwritten; secrets are written atomically with mode 0600.
- Audit log: one write transaction per entry (no forked chains with several processes), key id per log,
  an anchor file that reveals removed newest entries.
- A damaged tool permission file denies every tool instead of falling back to looser defaults.
- MCP: confirmations show every argument in full (too long: refused), only a real answer of the user counts as
  approval, declined and cancelled calls are audited, client snippets are quoted, and the Claude Desktop config
  backup is private.
- The MCP launch command written into client configs never imports code from the folder the client starts it
  in (`python -P`); Windows snippets are quoted for cmd and PowerShell; invisible and combining characters in
  confirmation texts are shown escaped; tool error messages are redacted before they reach a client.
- **Secret scan** (`scripts/secret_scan.py`, standard library only): provider API keys, tokens, private keys,
  certificates and credential files are refused by a pre-commit and a pre-push hook (`make hooks`) and by a CI
  job over the whole history. Personal strings (e-mail address, user name) go into the never-committed
  `.git/info/secret-patterns`; findings are printed redacted.

## [2.4.0] - 2026-01-14
**Reconstructed entry.** Versions 2.0 to 2.4 were developed without changelog entries; this summary is
taken from source headers and the git history (2025-12-13 to 2026-01-14).

### Added
- Self-healing manager (regex heuristics plus AI analysis of build logs).
- Offline mode for Ditto with small local models, telemetry (opt-in), updater, community hub.
- IMatrix "Smart Calibration" for GGUF quantization.
- Single source of truth for project sources (`configs/project_sources.yml`, `SSOT_ARCHITECTURE.md`,
  `validate_ssot.py`, `sync_ssot.py`).
- German voice models (GLaDOS, Thorsten) in the source catalog; transparency manager (experimental).

### Changed
- Container images configurable centrally; security hardening of the GUI windows.

## [1.7.0] - 2025-12-10
**Hardware & Deployment Update.** Bridges the gap between build and run by introducing a zero-dependency deployment manager and full Intel hardware support.

### Added
- **Deployment Manager:** Integrated SSH/SCP client to deploy artifacts directly to edge devices. Features a "Zero-Dependency" mode (using system binaries or Paramiko) and a "Network Guard" ping-loop for connection stability.
- **Intel Ecosystem:** Full support for Intel IPEX-LLM and OpenVINO. `hardware_probe` now detects Intel Arc GPUs, iGPUs, and NPU accelerators (Meteor Lake).
- **Live Monitoring:** Real-time CPU and RAM usage visualization of the build container directly in the GUI (replacing external tools like `ctop`).
- **Visual Identity:** "Ditto" comes to life! Added dynamic avatar states in the Wizard (Thinking, Reading, Success, Error) to improve user experience.
- **Auto-Documentation:** The builder now automatically generates a `Model_Card.md` containing SHA256 hashes and usage instructions, bundled into the Golden Artifact ZIP.

### Changed
- **Hardware Probe:** Massively expanded `hardware_probe.sh` and `.ps1` to detect AVX512-VNNI and AMX flags essential for Intel optimization.
- **GUI:** Added "Deploy" button to Main Window (active after successful build) and a secure `DeploymentDialog` that keeps credentials in RAM only.
- **Builder:** Updated artifact handling to deterministically identify model files and create a standardized ZIP package ("Golden Artifact").

## [1.6.0] - 2025-12-06
**Deep Ingest Release.** Empowers the AI Agent "Ditto" to recursively crawl, filter, and learn from external documentation websites and PDFs.

### Added
- **Deep Crawler Engine:** Integrated `LangChain` based Recursive URL Loader to parse entire documentation sites (not just single pages).
- **PDF Intelligence:** Native PDF parsing support using `pypdf`, enabling Ditto to read data sheets and technical manuals.
- **Smart Filtering:** Implemented language detection (ignoring non-English content) and navigation/footer removal for cleaner knowledge.
- **Knowledge Snapshots:** The Module Generator now automatically extracts learned knowledge from Qdrant and bundles it as `knowledge.json` within the target module for sharing.
- **GUI Dialogs:** New `URLInputDialog` for batch-adding documentation links with configuration for crawl depth and page limits.
- **Legal Compliance:** Mandatory "Robots.txt" respect and user disclaimer checkbox before crawling.

### Changed
- **Wizard Workflow:** Integrated "Deep Ingest" step into the module creation wizard with live progress logging.
- **RAG Manager:** Expanded `ingest_url` to handle recursive crawling results and PDF streams.
- **Config Manager:** Added settings for `crawler_max_depth`, `crawler_max_pages`, and input history.

## [1.5.0] - 2025-12-01
**Expert Knowledge Release.** Transforms the AI Agent "Ditto" from a passive reader into an active expert system using local RAG technology.

### Added
- **Local RAG (Experimental):** Integration of Qdrant Vector Database to enable semantic search over hardware documentation instead of naive scraping.
- **Dynamic Sidecar Architecture:** Qdrant runs as an on-demand container. It is only downloaded and started if the user explicitly enables "Knowledge Base" to save resources.
- **Community Brain:** New workflow to share indexed knowledge snapshots via Git. Users can import community-curated knowledge packs (`community/knowledge/*.json`) for offline intelligence.
- **RAGManager:** New core component (`orchestrator/Core/rag_manager.py`) handling document ingestion, chunking, and vector retrieval.

### Changed
- **Ditto Agent:** Updated `_fetch_documentation` logic to prioritize vector search results over raw URL downloads for higher precision in module generation.
- **DockerManager:** Added logic to dynamically spawn/stop the `llm-qdrant` container based on user configuration.
- **GUI:** Added "Enable Local Knowledge Base" toggle in AI Settings dialog.

### Security
- **Data Privacy:** Vector data remains strictly local (127.0.0.1). No external cloud vector store is used.
- **Sanitization:** Export tools for community knowledge automatically strip potential sensitive paths or API keys before creating snapshots.

## [1.4.0] - 2025-11-30
**Smart Calibration Release.** Introduces a context-aware quantization pipeline with Human-in-the-Loop verification to ensure enterprise-grade model quality on NPU targets.

### Added
- **Smart Calibration:** Automated workflow to detect if a model requires calibration data (e.g., INT8) and generate/select it.
- **DatasetManager:** New core component (`orchestrator/Core/dataset_manager.py`) for deterministic domain detection and dataset validation.
- **Human-in-the-Loop:** Added `DatasetReviewDialog` to allow users to verify and edit AI-generated calibration data before build execution.
- **Ditto AI Upgrade:** Expanded Ditto's capabilities to generate synthetic domain-specific training data (Code, Chat, Medical) based on SSOT knowledge.

### Changed
- **Build Pipeline:** Updated `builder.py` and `docker_manager.py` to inject calibration datasets (`dataset.json`) into the build container.
- **Rockchip Modules:** Updated `rkllm_module.sh` and `rknn_module.sh` (and their Python wrappers) to accept and utilize external calibration datasets for higher quantization accuracy.
- **GUI:** `MainWindow` now intelligently prompts for datasets when selecting sensitive quantization modes (INT8/W8A8).
- **Policy:** Removed heuristic guessing for model domains; introduced strict metadata checks or user prompts.

## [1.3.0] - 2025-11-29
**Production Ready Release.** Focuses on full internationalization, security hardening, and closing the loop between GUI and Build Engine.

### Added
- **I18n Support:** Full English/German localization for GUI and Wizard.
- **LocalizationManager:** Centralized service for dynamic language switching (`orchestrator/utils/localization.py`).
- **GUI Enhancements:**
    - New Dropdowns for **Task** (LLM, Voice, VLM) and **Quantization** (INT8, INT4, FP16).
    - Added **"Use GPU"** checkbox for NVIDIA Passthrough.
    - Integrated Language Selection Dialog on first start.
    - **SecretsManager:** AES-256 encryption for sensitive API keys.

### Changed
- **Refactoring:** Centralized text resources in `LocalizationManager`.
- **Dependencies:** Added `litellm`, `rich`, `psutil` and `cryptography` to core requirements.
- **Docker:** Removed static builder services in favor of dynamic spawning.

## [1.2.0] - 2025-11-29
**AI Integration Release.** Introduces the Ditto Agent for automated hardware discovery and target generation.

### Added
- **Ditto AI Agent:** Automated analysis of hardware probes and documentation fetching from SSOT.
- **Smart Dispatcher:** Implemented dynamic `build.sh` template handling Task and Hardware routing.
- **Templates:** Added `rkllm_module` and `rknn_module` templates for Rockchip NPU support.
- **Security:** Added `trivy-infra-scanner` for automated container vulnerability auditing.
- **NetworkGuard:** Robust connectivity checks and secure downloads.

### Changed
- **Hardware Probe:** Hardened Windows detection using Native API (C# Injection) for reliable CPU flags.
- **Pipeline:** Decoupled Python generation logic from Bash scripts.

## [1.1.0] - 2025-11-28
**Architecture Refactoring.** Decouples the core builder from specific target logic to enable universal cross-compilation.

### Changed
- **Architecture:** Removed hardcoded `TargetArch` enums; Builder is now target-agnostic.
- **Clean Code:** Extracted inline Python code from Bash scripts into standalone files.
- **Builder:** Switched to dynamic volume mounting for module execution.

### Fixed
- **Stability:** Fixed list/set type errors in Windows installer.
- **Cross-Compilation:** Added missing cross-compilers (`gcc-aarch64`) to Docker templates.

## [1.0.0] - 2025-11-20
**Initial Release.** Establishes the core framework structure and basic Rockchip support.

### Added
- Core Framework structure (Orchestrator, Builder, Docker Manager).
- Basic support for Rockchip RK3588/RK3566 cross-compilation.
- CLI (`llm-cli`) and GUI (`llm-gui`) entry points.
