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
- **Deep Ingest honours machine-readable opt-outs.** Text and data mining of public pages is allowed unless
  the rightsholder reserved it in machine-readable form (EU DSM Directive Art. 4, German UrhG § 44b), so
  besides robots.txt the crawler skips pages marked `noindex`, `none`, `noai` or `noimageai` in an
  `X-Robots-Tag` header or in `<meta name="robots">` / `<meta name="llm-conversion-framework-bot">`, does not
  follow the links of `nofollow` pages, and honours the W3C TDM Reservation Protocol (`/.well-known/tdmrep.json`
  per site, the `tdm-reservation` header and meta tag, PDF metadata). Redirect targets on other hosts are
  checked the same way. Every skipped page is named in the crawl log with its reason. None of these checks can
  be switched off.
- **Notice before the first Deep Ingest** (English and German): what is read (only publicly reachable pages),
  which opt-outs are honoured, that the knowledge stays on this computer, and that sharing it keeps source and
  licence and needs your explicit release. It is information, not a legal confirmation: one "Understood"
  button. It is shown once per user and again when its text changes.
- **Spoken announcements** of the notice and of the Deep Ingest outcome through the speech engine built into
  the operating system (Windows System.Speech, macOS `say`, Linux `spd-say` or `espeak-ng`), in the interface
  language. Best effort and never in the way: no audio files, the system volume is never touched, and it is
  silent in CI, over SSH without a display, on headless systems, with `LLM_FRAMEWORK_NO_AUDIO=1` or when
  *Tools > Spoken Announcements* (setting `audio_announcements`) is off. Every announcement is also logged.
- **Deep Ingest progress and Stop**: the wizard shows "Page N of M: <url>" instead of an endless spinner, and a
  Stop button ends the crawl after the current page. Closing the wizard stops a running crawl the same way and
  closes once the current page is stored, without freezing the window.
- **Input history**: the input fields offer what you used recently, newest first. Model, calibration dataset,
  Deep Ingest URL and device (Deployment, remote hardware scan) are editable lists; the most recent model and
  device are filled in, datasets and URLs are only offered. Targets and boards come from the target
  definitions, so the most recently built one that is still offered is preselected (a target without declared
  boards offers recent boards as suggestions). The history is per user (`input_history.json` in the per-user
  state directory), at most 20 entries per field, and never holds secrets: values that look like an API key or
  token, URLs with a user name or password and URLs with a credential or session parameter (such as `?token=`
  or `;jsessionid=`) are not remembered. *Tools > Clear Input History...* forgets it.
- **Retrieval evaluation** (`llm-cli ditto eval --kind retrieval`): 42 German and English searches
  (cross-lingual questions, identifiers such as `GGML_CUDA`, answers deep inside a large table or script, the
  last lines of failed builds and Ditto's own search strings, unrelated questions) against a corpus of 30
  framework and SDK documentation excerpts (`evals/retrieval/`). The corpus is indexed into a temporary
  knowledge base with the configured local embedding model: no AI model is needed and your own knowledge base
  is not touched. The report gives hit@1, hit@3, MRR, how often an unrelated
  question gets no excerpt, figures per tag and the search latency; it works offline once the embedding model
  is downloaded. A manual and nightly CI workflow runs it with cached models. Baseline with the default CPU
  embedding model on the 34 questions: hit@1 83 %, hit@3 97 %, MRR 0.91; all 4 unrelated questions still get
  excerpts.
- **Reranking of knowledge-base results**: a local multilingual cross-encoder (FastEmbed/ONNX, about 120 MB,
  downloaded on first use, never in offline mode) reads the question together with each candidate and drops
  the ones below a relevance floor, so Ditto gets no excerpt rather than irrelevant ones. Build recommendations
  use it. Settings: `rag_reranker` (`auto`: from 6 GB RAM, the larger bge-reranker-v2-m3 with CUDA and 16 GB;
  `off`; or a model id), `rag_rerank_min_score` and
  `rag_rerank_default` (also rerank chat and MCP searches; off). A reranker that cannot run is logged and the
  search keeps its order. `llm-cli ditto eval --kind retrieval --rag-variant rerank` grades it: on the 34
  questions hit@1 93 % and MRR 0.97 (hybrid search: 87 % and 0.93), and all 4 unrelated questions get no
  excerpt (floor 0.2: relevant excerpts scored 0.80 or more, unrelated ones 0.009 or less). It takes about
  1 s for 12 candidates and 2-2.6 s for 20 on a shared 4-core CPU and gained less than the 0.05 MRR required
  for chat, so chat stays without it by default. Build-log analysis and module proposals keep the hybrid order:
  on the last lines of failed builds the reranker found the relevant excerpt within 3 in 2 of 6 cases (hybrid
  search: 5 of 6) and dropped it below the floor in one, and for the module search string it dropped every
  excerpt.
- **Signed community packs**: a pack is a zip with a manifest (sha256, size and mode of every file) and an
  optional Ed25519 author signature. Reading a pack accepts exactly the listed files, never links or special
  files, and checks every hash. Trusted publisher keys ship in `configs/community_keys.yml`, each with an
  expiry date.
- **Encrypted packs for private sharing** (with your own machines or a team): encrypted to one or more
  recipients' X25519 public keys and/or a passphrase (scrypt), in the style of age (ChaCha20-Poly1305,
  HKDF-SHA256, authenticated header), and signed by the sender, so recipients see who sent it and any change,
  including an added or removed recipient, is detected. As in age, a file holds at most one passphrase entry.
  Each user gets an Ed25519/X25519 key pair on first use, kept only in the encrypted secrets store; the public
  keys can be exported as text.
- **Recipes**, the model zoo's "model templates": the model at a pinned commit, the target with a fingerprint
  of its files, board, format, quantization, options and verified results (artifact hashes, toolchain image,
  device tests). A recipe is generated from a finished build, checked against the target's capabilities and
  maps to a normal build request.
- `build_info.json` records what a recipe needs: the model's commit (the download is pinned to it), the target
  fingerprint and version, the toolchain image, the context length, the calibration dataset's hash and the
  framework version. Build requests accept a `revision` (commit) for Hugging Face models.
- **Community zoo** (`llm-cli zoo list|show|install|verify|export|keys`): community targets, recipes and
  knowledge packs listed from a signed community index (setting `community_index_url`: `index.json` plus
  `index.json.sig`, verified with the keys in `configs/community_keys.yml`, cached per user, fetched again when
  the cache is older than a day and never replaced by an older index). Installing always reads the current
  index first (so a revocation is never missed), downloads the pack pinned by size and sha256, checks it in a
  quarantine folder (exact file list and hashes, the target validators, the recipe and knowledge schemas), shows
  a summary with its trust level and installs it only after you confirm, atomically and with a provenance file.
  Trust levels:
  official, verified, community, local (a file without an index entry: always confirmed, and a warning on
  every build) and revoked (blocked; installed copies are flagged by `llm-cli doctor` and the hub and are not
  built, also after the key that signed the revocation has expired). `llm-cli zoo verify` finds installed packs
  that were changed or revoked. The install summary is always shown as plain text, so text from a pack cannot
  hide its trust level.
- **Community Hub** in the Tools menu: a trust column, filters for kind and trust, import of pack files (also
  packs encrypted to your key), and "Build" on an installed recipe fills in the Builder, including the model's
  pinned commit.
- **Export for community** on the module wizard's last page, in the Builder log's context menu (a finished
  build becomes a recipe) and with `llm-cli zoo export`: a pack of a target, a recipe or your knowledge base,
  privacy-scrubbed (the changes are shown), validated and signed with your key; with `--to` or `--passphrase`
  it is encrypted for private sharing instead. Knowledge packs keep the source address of every page and claim
  no licence for them (`NOASSERTION`); a community pack you installed is not re-signed as your own. "Open pull
  request" (`--pr`, which asks after the summary unless `--yes` is given) hands it to the community repository
  (setting `community_repo`): through the GitHub CLI if it is installed and logged in, otherwise the
  repository's upload page opens in the browser. The framework never asks for, reads or shows a token.
- Maintainer tools for the community repository: `scripts/zoo_sign_index.py` (signing key in the OS keyring,
  index entries from packs, signature of `index.json`) and the CI workflow template
  `scripts/community_repo/validate-contribution.yml`, which runs `llm-cli zoo verify` on every submitted pack.

- **Documentation in English and German** for every document (README, blueprint with design and roadmap, CLI
  usage, features, contributing, module developer guide, SSOT, changelog), checked by `tests/test_docs.py`:
  every document has its twin, links and anchors resolve, both changelogs list the same versions, and the
  roadmap names only what comes.

### Changed
- **Deep Ingest outcome**: it said "Deep Ingest complete" even when every URL failed. It now reports success
  (everything stored), partial (some pages stored; others failed, were reserved by the site or the crawl was
  stopped) or failure (nothing stored), each with its own message, only after the last write has returned.
  Pages are stored as soon as they are read, so a stopped or interrupted crawl keeps what it already stored,
  and pages that could not be fetched are listed in the crawl log.
- **Ditto runs on the new provider layer** (official anthropic and openai SDKs) instead of LiteLLM, and the
  built-in Transformers model for offline mode is gone (offline mode uses local providers: Ollama, the
  managed llama-server, a server on your network). Healing analyses, module proposals and build
  recommendations are validated structured answers; an invalid answer is an error and is never applied.
- **Chat history** is append-only and kept per user; a long conversation is summarized into a new one.
  Histories of earlier versions are migrated.
- Provider errors are explained, e.g. that an Anthropic API key must belong to a workspace in the Claude
  Console.
- Ditto's prompts: build failures are attributed to the build machine or the device correctly, build
  recommendations know which inputs each format accepts (an ONNX model is not sent to GGUF), questions about
  targets and builds are answered from the tools, and module proposals use compiler flags rather than the
  CPU feature list.
- Local model: effort "low" turns thinking off (Qwen3.5 on llama-server otherwise thinks at full length, which
  takes minutes on a CPU); "medium" and up keep it.
- Dependencies: `anthropic` 1.13.0, `openai` 3.28.0 (was 2.54.0) and `fastembed` 0.8.1 (local embeddings,
  ONNX on the CPU) are pinned exactly; pip-audit reports no known vulnerabilities.
- The crawler's User-Agent reports the real framework version (it said 1.6.0).
- Deep Ingest: a link that redirects to another host is skipped like any other external link; only a
  redirect of the start URL can move the crawl to another host.
- **Keyword search understands identifiers**: `-DGGML_CUDA=ON`, `rknn_toolkit2` or `rknnToolkitLite` are also
  found by their parts (`GGML_CUDA`, `rknn toolkit2`), and one- and two-word searches such as `E2041` or
  `GGML_CUDA` use the keyword index. Identifier questions of the retrieval set: hit@3 83 % to 100 %, the rest
  unchanged. Existing knowledge bases get their keyword index rebuilt from the stored text once, on first use
  (documents and vectors are kept); older snapshots are rebuilt the same way when restored.
- **Large code blocks and tables** beyond the embedding model's context (512 tokens for the default model) are
  split at line boundaries, each piece with the table header or the code fence, so their later parts can be
  found at all (answers deep inside such blocks: hit@1 50 % to 100 %). Smaller blocks stay whole. Re-import a
  document to apply it.
- **Ditto's knowledge excerpts fit the context**: they take at most a third of `chat_context_limit`; an excerpt
  that does not fit is skipped and a too long first one is shortened at a line (an open code block is closed).
- Knowledge-base models are unloaded after 10 minutes without use and loaded again on the next search
  (setting `rag_model_idle_minutes`, 0 keeps them loaded). The time counts from the end of the last search or
  import, never during one.
- Knowledge exports, and with them the knowledge snapshot of new target modules, are scrubbed: home folders,
  IP and MAC addresses, e-mail addresses, JWTs, the host name and the user name are removed, besides API keys.
  Image digests (`name@sha256:...`) and the paths of default accounts such as `/root` or `/home/runner` stay
  as they are, so a scrubbed target still builds.
- The knowledge snapshot of a new target module is opt-in (a check box on the wizard's last page) and holds only
  the pages crawled for that target in the wizard; before, the whole local knowledge base went into every module.
- Target modules created with the wizard, `llm-cli module create` or Ditto get a `capabilities:` block (support
  level community: their boards, and GGUF with the types their build script handles), so the Builder, the CLI
  and AI clients check their build settings. Generation fails if a template placeholder is left anywhere.
- The pinned download code moved to `orchestrator/utils/download.py`, shared by the local model runtime and
  the community zoo.

### Removed
- Ditto's generated filler calibration data (builds use the shipped calibration text or your own dataset).
- The unused setting `input_history` (the input history is personal and lives in the per-user state directory,
  not in the shared `configs/config.yml`).
- The *Respect robots.txt* option of the Deep Ingest dialog and the setting `crawler_respect_robots`: robots.txt
  is a machine-readable opt-out like the others, so it is always honoured.
- `litellm` and `tiktoken`, and with them 19 indirect packages (aiohttp, boto3, jinja2 and others).
- The "swarm" upload: the XOR "SwarmCipher" (obfuscation anyone could undo), `llm-cli swarm upload`, the
  Community Hub's "Contribute to Swarm" button and its GitHub token prompt. The framework never asks for or
  stores a repository token. Old swarm packets in `community/knowledge/` are skipped with a warning.
- Installing hand-copied module folders from `community/`: community packs are installed with
  `llm-cli zoo install` or the Community Hub.

### Fixed
- The knowledge base works in offline mode once its models are downloaded: the downloaded BM25 keyword model
  was never found while offline, so every import and search failed with "not in ... and offline mode is on".

### Security
- Deep Ingest reads robots.txt as RFC 9309 describes it. Rules with `*` or `$` (such as `Disallow: /*.pdf$`), an
  `Allow` listed before a longer `Disallow`, every `User-agent: *` group after the first and a file that starts
  with a byte order mark used to be ignored, so the crawler fetched pages the site had excluded.
- Ditto discards a healing fix or a build recommendation that contains a dangerous command (a download piped
  into a shell, deleting the root or home folder, world-writable permissions, overwriting a disk), whatever a
  build log or a model name told the model.
- Requests to local model servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment.
- Local model downloads are refused unless size and sha256 are pinned; the server binary archive is verified
  the same way and installed atomically.
- Community packs are installed only through a quarantine folder after their checks: an id without paths,
  exactly the listed files with their hashes, no links, special or binary files, every image a Dockerfile pulls
  (`FROM`, `COPY --from`, `RUN --mount=...,from=`, also in a lowercase `dockerfile`) pinned by digest unless it
  is an official base image, the shell deny-list on every script line that is not from the shipped template,
  and `bash -n`. A copy of the shipped Rockchip target passes these checks. A pack appears under its final name only when it is complete, never over an
  existing target (also when only the case differs). Before, a link in a community module could copy any file
  of the user into `targets/`.
- The Docker build context never follows symbolic links or special files of a target, a linked Dockerfile is
  refused, and a build's target name cannot contain a path.

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
