# Changelog

All notable changes to this project are documented in this file.
German version: [CHANGELOG_DE.md](CHANGELOG_DE.md).

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Planned work is listed in the [roadmap](docs/BLUEPRINT.md#roadmap), not here.

## [Unreleased]

### Added
- **Ditto > Local Runtime...: every `llm-cli ditto runtime` command by mouse** (W1'): one window shows what `ditto
  runtime status` shows (hardware, mode, the build in use and its checks, problems and the rebuild advice, the
  self-built plan, the prebuilt option, the server with address, model and context, the recommended and the chosen
  model, the log file) and does `install` (**Build llama-server...** with GPU backend, where the build runs and
  compile jobs; **Install the official prebuilt archive...** with "Download it again"), `ensure` (mode Automatic,
  Bare metal or In Docker, the same build options, and the model offer afterwards with its size and licence, unticked
  like `--no-model`), **Start** (model tier or the recommendation, context length, idle unload; the model chosen
  earlier is named) and **Stop**, **Import a build...** and **Export the build...**, **Remove older builds...** (the
  deletion dialog with the full list, nothing ticked, "No" by default), **Export the source...** (with an optional
  mirror) and **Import the source...**. The same core functions as the CLI run (the status overview, the model offer
  and the llama.cpp pin now are core functions the CLI calls too; its output is unchanged); builds, downloads, Docker
  and the server start run in the background with a progress line and Cancel where the command can be interrupted;
  every question the CLI asks is asked with the same words and "No" as the default (also the one before the pinned
  source is fetched); one runtime task runs at a time in the whole GUI. The box "Ditto's local server"
  (here and in the AI settings) no longer refuses the installed mode: **Check and repair...** checks it and installs it
  again only when it cannot be used (an old llama.cpp build after an update, for example), and installs and switches
  now take the build options; the switch keeps the space check and the complete removal of the old mode. Its new
  button **Status and more...** opens the window from the AI settings. A stopped local server and a declined source
  fetch now name the GUI path next to the command.
- **`llm-cli knowledge storage` and the old knowledge base storage in the deletion list** (DECISIONS RG5): `llm-cli
  knowledge storage` shows where the knowledge base is stored, when an older one was converted (how many excerpts,
  what was checked) or why its conversion failed, and the old folder with its full path and size; it reads the record
  only, and its first line names the storage really in use. `convert`, and **Tools > Knowledge Snapshots... > Try the
  conversion again** in the GUI (in the background), try the conversion again now. `delete-old`, and **Tools >
  Knowledge Snapshots... > Delete the old storage...** in the GUI, list the old folder after its conversion and delete
  it only after your yes (trash or for good; "No" is the default; audited as `old_store_deleted`); nothing deletes it
  by itself, and an AI never can. Mojo Oracle reports a failed conversion (with the reason and both ways to try again)
  and an old folder that is still kept.
- **A deploy tells you about an old Qdrant sidecar on the board**: packages with Docker images from earlier versions
  started the container `qdrant-sidecar`, which nothing uses any more. After a successful deploy (`llm-cli deploy
  run`, the Deployment window, the AI tool `deploy_to_device`) the framework looks with `docker ps` (for at most 20
  seconds) whether it is still on the board and, if so, says it is running or stopped and how to remove it there.
  Mojo Oracle shows a line for that board from the last deploy. The framework never stops or removes the container.
- **Where finished models go: the output folder and a save dialog** (eighth round, answer 3): finished builds
  (golden artifacts) are saved to the output folder (setting `output_dir`, default `~/llmcf/output`; on Windows in
  your user folder), which you set once with **File > Output Folder...** or `llm-cli config output-folder PATH`, for
  example to a folder on a big second disk; an AI cannot change it. When a build you started in the GUI has finished,
  the system's folder dialog opens at the output folder for every golden artifact: OK saves it there or in the folder
  you pick; Cancel saves nothing elsewhere and deletes nothing, the build stays in the framework's build folder
  (setting `build_dir`) and **Save...** in the Deployment tab or `llm-cli build save` saves it later. `llm-cli build
  start --output PATH` (at a terminal without it: a prompt with the output folder as the default, `-` keeps it in the
  build folder); without a terminal, and for Ditto and MCP clients, builds go to the output folder. Saving renames on
  the same disk; onto another disk every file is copied, checked by sha256 and only then removed from the build
  folder (a link is never followed; when anything was not copied, the result names it and the build folder keeps its
  copy); the free space is checked first, taken names get `-2`, and system folders (on Windows also the program
  folders, ProgramData, the recovery and boot folders and every disk's recycle bin), the program folder, the build
  folder and the per-user folder are refused (as the output folder also a folder that holds one of them, such as your
  home folder). The Deployment tab, `deploy list-artifacts` and `list_artifacts` list
  the output folder, the build folder (earlier builds stay there), earlier output folders after you changed it and
  folders you saved to; `start_build` tells an AI where the build will be.
- **Identical builds are named before building** (eighth round, N11): `check_build_request` (`earlier_builds`, with a
  note) and the GUI's builder form report a finished build with 100 % the same chosen options: the model and its
  exact commit (a local model by every weights file's name, size and time), the target and the sha256 of its folder,
  board, format, quantization, importance matrix and calibration data, optimization, context length, GPU use and
  task. Other weights or another quantization is never identical. You use it or build again (**Use the existing
  build** / **Build again** / **Cancel**); `start_build`'s question says when it builds an identical build again.
  Nothing is skipped by itself. Every build records its options in `build_info.json` (`build_options` with their
  sha256).
- **Disk check before every knowledge snapshot, and a snapshot before every knowledge pack import** (eighth round,
  answer 2, N13): before a snapshot is made, its size is estimated. When it does not fit beside the reserve that always
  stays free, no snapshot is made and the change does not run (Deep Ingest, a restore, a pack import, the index of the
  framework's own documents); there is no "continue anyway". AI clients get the blocker `snapshot_no_room` with your
  action before the question; the GUI offers **Knowledge Snapshots...** to delete or export snapshots. When it fits but
  leaves little space, a situation warning (`disk_tight`). A knowledge pack import now saves the knowledge base first
  (`pre_pack_<time>`); without room nothing is imported and the pack is imported later. A pack that cannot be imported
  yet is tried again at the next start without another snapshot, and a name that is taken gets `-2`, so no snapshot is
  ever written over. Mojo Oracle's knowledge line (with where, why and that only your list frees the room) and
  `llm-cli knowledge snapshots` show what the snapshots use against the free space. Mojo also checks the free space
  of the framework's build folder. Nothing is deleted by itself.
- **Deleting with the full list, and the trash** (delegation design 3.10, round 5 f; round 6 10.4): every deletion of
  something you can see asks first in the framework's own window (the GUI's deletion dialog, or a table and a question
  at a terminal) and lists each item with its name, full path or Docker id, file name, size and what it is; "No" is
  the default. Where you pick from a list, nothing is ticked, the list is built again from exactly what you ticked
  (its hash is audited as `deletion_sha256`), and the button names how many and how much. Deleting moves files and
  folders to the trash by default (`.trash` in their data folder; a move to another disk is refused, never copied).
  Nothing leaves the trash by itself: after `trash_retention_days` (default 14, not changeable by an AI) it only
  reminds you. **File > Trash...** and `llm-cli trash list|restore|empty` restore entries or delete the ones you tick
  for good; no tool empties the trash, and no AI reaches the trash folders. The optional tools `list_trash` and
  `restore_from_trash` (hidden until you enable them) let an AI see the trash by name and restore only what it asked
  to delete itself. Knowledge snapshots are deleted with `llm-cli knowledge snapshots prune|delete` or **Tools >
  Knowledge Snapshots...** (tick boxes), or exported with `knowledge snapshots export NAME... --to FOLDER` (each copy
  checked by sha256; an export never deletes). **Tools > Framework Build Images...** lists the framework's Docker
  images for you to tick, as `llm-cli doctor --fix --tier 2` does; the self-healing dialog for a full Docker disk opens
  it. `llm-cli doctor` says where Docker keeps its images and how much the framework's images use; moving that folder
  is an administrator's step ([Container setup](docs/Container_Setup.md#10-where-docker-keeps-images)).
- **The facts catalogue and the per-tool classification** (delegation design 4.1-4.6, `orchestrator/tools/
  facts_catalog.py`, texts in `orchestrator/utils/l10n_facts.py`): every finding before a question has a fixed kind.
  Hard blockers (disk full, offline mode, an unpaired board or a changed host key, a gated model without access, the
  Deep Ingest notice not read, a withdrawn pack, ...) refuse before any question with their reason code, a next step
  for the AI and, apart from it, what the person can do; a blocker an AI can clear names the tool that clears it.
  Situation warnings (a tight disk, a large download, memory that is tight or too small, many pages, an untested
  module, a replaced board runtime; never the number of knowledge snapshots, which have no limit) come with a rule
  for an AI; security and legal warnings (pickle weights, a base
  image outside the Docker Official Images or not pinned, vendor downloads above the soft cap, an unreviewed or
  community target, the first build of AI-made build code, an untrusted model run natively on a board or in the
  sandbox the person has not allowed for AIs (`bench_untrusted_sandbox_ok`), signing as the person, gated models,
  licences)
  always stay with the person, a base image outside the Docker Official Images also after a confirmed build with it,
  and facts that could not be checked are the security warning `facts_unknown`, never "no problem" (the person reads
  in plain words what could not be checked, never an internal code). Builds, recipe builds, downloads, pack imports and exports, Deep Ingest,
  deploy packages and deploys, board tools, the local runtime, benchmarks, module creation and settings raise them.
  A decision is bound to the facts' decision digest, which leaves out free space and byte counts; a build or download
  under a standing approval (and later a delegation) uses only the commit its look-up resolved (blocker
  `commit_required` when Hugging Face could not be asked). The AI's view of the facts (`delegation.ai_view`) never
  carries a fact only the person sees. Warning codes are stable identifiers, ready for a later "do not ask again for
  this model version" choice (design question Q3), which is not built yet.
- **A real-client smoke test** (`scripts/mcp_client_smoke.sh`, checker `scripts/mcp_smoke_check.py`): runs Claude Code
  (`claude -p`, four scripted prompts) against the framework's stdio server in a throw-away environment (home, Hugging
  Face home, XDG folders, per-user folder, secrets in memory only, the null keyring backend, no window system; the
  server runs under `env -i`, so it gets none of the caller's variables) and checks from the isolated audit database,
  never from the model's answer: the client line, `get_status`, an unknown argument refused with a suggestion, a
  declared alias recorded on a call that ran and on a refused one and shown to the client in both (read from the tool
  results of the client's JSON output), and a call that needs a confirmation not run without an answer (a question put
  to the person, or an end for want of an answer; a refusal before any question, such as a denied permission, does not
  count). It exits with 77 (skipped) when the claude command is missing or not signed in, never reads, copies or passes
  Claude credentials or tokens, removes its temporary folder (the client's log of the server and its temporary files
  included) and needs no Docker; a manual or optional CI check (CONTRIBUTING, "The real-client smoke test").
- **`start_recipe_build` and the resource `lcf://packs/index`** (MCP design T9): an AI client builds an installed
  community recipe exactly as pinned (the model at the recipe's commit, target, format, board, quantization,
  importance matrix, context length) through the two-phase path of `start_build` (same question, disk reservation,
  quotas and standing-approval scope; `BuildService.start_recipe`). A calibration dataset is taken only when the
  recipe names one and must be exactly that file; the question shows the importance matrix and context length, and
  a recipe changed in any field after the question is not built. The resource
  shows the cached index and the installed packs as `list_packs` does, never refreshed.
- **A confirmed build unlocks a build standing approval for packs installed under one** (MCP design 6.10): a target
  or recipe pack an AI installed under a standing approval is asked about until the person confirms one build of it
  in the framework's own window (an elicitation answer, a standing approval's own use or "allow" never count); the
  framework records that build (`confirmed_builds.json` in the per-user folder, signed like the permission file: a
  hand edit, another key or a missing signature counts as no confirmation) and the build standing approval
  covers the next builds. A digest-pinned base image outside the Docker Official Images counts once a build with
  exactly that image was confirmed; a tag never does. The build standing approval's rule version is now 2, so
  standing approvals made under the narrower rule lapse. Until that first confirmed build the question carries the
  security warning "first build" (the person sees AI-made or AI-installed build code once, design question Q1), also
  for a hardware module an AI created without the person's yes (bound to its definition's sha256: one recreated under
  the same name is shown again).
- **A deploy's end in the audit log**: when a `deploy_to_device` job ends (done, failed or cancelled), its own end
  entry with the job, the device's label and the device state is written, linked to the call's `started` entry.
- **Optional tools `delete_artifact` and `restore_knowledge_snapshot`** (MCP design T10): delete one built model
  (folder and golden ZIP) by id and sha256, or replace the knowledge base with an earlier snapshot as a job. Both are
  destructive: denied by default and hidden until you enable them (allowing the whole external class does not),
  every call asked, never a standing approval. A deletion is decided only by you in the framework's own window
  (Ditto's dialog or prompt, or a pending approval for an MCP client), never in the client's own form, never under
  a standing approval or a delegation; the registry refuses any tool that deletes and could be confirmed another
  way. That window lists each item (the folder and its golden ZIP) with its speaking name, full path, file name, size
  and what it is; an AI sees labels and sizes only, and Ditto's command line without a terminal prints only the call
  and its arguments. A deletion is refused outside the output folder and the framework's build folder (a model you
  saved to another folder is yours to remove), while a build of any
  framework program writes into the folder or is queued for it, after the build changed, and when the folder is not
  the one the question named. A restore always saves the current knowledge base first (`pre_restore_<time>`), also
  outside MCP, and deletes no snapshot; it stays outside every standing approval. `delete_artifact` moves the model
  and its golden ZIP to the trash, where you can restore them (see "Deleting with the full list, and the trash").
- **The MCP server inside a container you built** (`llm-cli mcp serve --transport http --container`): binds the
  container's interfaces, and only inside a container; the named token, Host and Origin checks stay the same. Publish
  the port to the host's loopback only (`-p 127.0.0.1:8765:8765`).
- **The framework in a container, built on your own computer** (round 5 h): no framework image is published or
  pulled. `scripts/build_container_image.sh` (`make build`) checks Docker, BuildKit and the checkout, stages exactly
  the files the repository ships and builds `Docker Setup/Dockerfile` with a local tag (`lcf-framework:local`; a
  name with a registry is refused), then prints how to run it. The Dockerfile runs the same installation as on bare
  metal (`install.sh`, which leaves out only the Docker engine and the `docker` group with `LCF_CONTAINER_BUILD=1`
  and uses `runuser` where `sudo` is missing) on Debian 13 slim pinned by digest, for a non-root user with your ids,
  with the sources as a read-only build mount (no secret in any layer) and everything personal in the volume
  `/data` (settings, vault, audit log, tokens, models, builds, datasets, logs); headless CLI and MCP server by
  default (with the GLib library Qt's core needs, which the CLI imports), the GUI's desktop libraries only with
  `--gui` (everything Qt's xcb platform plugin loads); a group with your group id that Debian has already is reused.
  The build ends with a check that the command line imports and every system library Qt loads is there, and stops
  naming what is missing. A build from the repository folder itself is refused. The compose file never pulls the image (`pull_policy:
  never`), and every `docker run` the builder prints or the CI runs says `--pull never`; it publishes no port and
  drops all capabilities; AI clients reach the MCP server over stdio (`docker exec -i lcf-framework llm-cli mcp
  serve`, from another computer over SSH to the host). Targets you create and packs you install stay in the
  container's program folder: `make down` only stops, and `make up`, `make gui` and `make clean` never replace or
  remove a container that holds files of yours outside the volume; they list each one with full path, file name,
  size and what it is (`make kept-files`, `scripts/container_kept_files.sh`). The target files' `docker.registry`
  and `docker.tags` are documented as local image names only. Guide: `docs/Container_Setup.md`.
- **Shared build state** (MCP design T11): every framework program (the GUI, a CLI call, each MCP server) writes a
  summary of each build request into the per-user folder (`build_requests/`), so the GUI status bar, `llm-cli build
  status`, `list_build_requests` and `get_build_status` see the same builds whichever program started them, also a
  request that still waits in another program's queue. Each summary names the model by its repo id or label (never a
  host path), the caller class that started it (never an MCP session key) and the program that runs it (`started_in`,
  column `Where`); errors are redacted. A request whose program ended before it finished is shown as interrupted.
  Builds of another program stay read-only. The note that only this process's builds are visible is gone.
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
  vector-database server and no cloud embeddings needed, and it works offline once its models are downloaded.
  Before every ingest, every restore and every knowledge pack import it saves a snapshot in `knowledge_snapshots/`
  of the per-user folder; a snapshot that does not fit on the disk stops the change (see "Disk check before every
  knowledge snapshot"). There
  is no limit: it keeps as many as the disk holds, deletes none by itself, on any channel (round 5 f), and no number
  of them brings a reminder or a warning. `llm-cli knowledge snapshots` (in the GUI: Tools > Knowledge Snapshots...)
  lists each one with its full path, name, size and what it is, so you delete (to the trash first) or export (copy
  elsewhere, checked by sha256) the ones you tick.
- **The knowledge base Ditto uses, for AI clients** (MCP tools `list_knowledge_sources` and
  `read_knowledge_source`, resource `lcf://knowledge/sources/<source id>`): a client that does Ditto's job sees what
  the knowledge base holds and reads one source's excerpts in order. `search_knowledge` now gives every excerpt its
  provenance (source id, address or pack, title, section, kind `web`/`pack`/`docs`, trust, date added, position) and
  takes `kind` and `rerank`. Sources are named by ids, never by paths; all their text is marked untrusted. A tool call
  never downloads the embedding or reranking model: without the embedding model the search is refused with the next
  step instead of starting a download of about a gigabyte, and the refusal names no folder.
- **Deep Ingest for AI clients** (MCP tool `crawl_docs_site`, decision of 2026-10-11): an AI client or Ditto can add
  documentation sites to the knowledge base, as a job, with exactly the GUI's crawler and opt-out checks (robots.txt,
  robots directives incl. `noai`, TDMRep). You are asked every time (never "do not ask again", a per-tool "allow" is
  capped) and the question lists every start address. Refused while you have not read the one-time Deep Ingest
  notice (`needs_person`), in offline mode or with the knowledge base off; 1-5 public `http(s)` start addresses,
  checked before the question; `max_pages` up to the setting and 100, `max_depth` up to the setting and 3, at least
  one second between requests; one crawl at a time per user, 5 a day and 300 stored pages a day per client (the budget and offline mode are
  checked again when an approved crawl starts).
  `cancel_job` stops after the current page and keeps what is stored; the result has counts and the skipped or
  failed addresses with reasons, never page text or a folder of this computer. New: `llm-cli tools crawl-notice` shows the notice in a terminal
  and records that you read it.
- **The community zoo for AI clients** (MCP tools `list_packs`, `add_pack_from_index`, `export_pack`): a client lists
  the signed index and the installed packs (details, verification; `refresh` once an hour), adds a knowledge, target
  or recipe pack from the signed index (trust verified or community, never revoked, never from a file) after you
  saw the summary checked in quarantine, for a target pack with every image its Dockerfiles pull (`FROM`, `COPY --from=`,
  `RUN --mount=...,from=`) shown plainly (registry, digest or "not pinned", Docker Official Image or another registry) and that its image build runs with network, and exports a
  pack to a local file signed with your key (always asked; refused without an existing key; returns scrub counts,
  never the removed text). A quarantined copy is reused for the same pack and hash and removed on a "no", a declined
  or expired approval, or after 20 minutes. "Yes, and do not ask again" covers one pack kind, trust level and index
  key (knowledge and recipe packs, `verified` target packs); packs installed under it are marked `installed_via`; an install under it must leave the larger disk reserve
  (20 GiB or 10 %) free, or it asks.
  No tool publishes a pull request.
- **Fix proposals from AI clients** (MCP tool `propose_healing_fix`): a client that analyses a failed build hands its
  summary, cause, manual step and confidence to the framework. It passes the same checks as Ditto's own analysis,
  including the dangerous-command filter, is confirmed by you like any local change, is accepted only for a failed
  build (at most 10 per build) and is shown as AI-generated with who proposed it (the caller and the client's
  self-reported name) in `get_build_status`, `llm-cli build status <request>` and the GUI (**Tools > Fix Proposals from
  AI Clients...**). The framework never runs it, and `resolve_healing` refuses to apply it. The prompt
  `diagnose_build_failure` leads to the tool.
- **Hugging Face and local files for AI clients** (MCP tools `search_hf_models`, `get_hf_model_info`,
  `download_hf_file`, `list_local_files`): the search filters gated models with `gated: free` by default (`any`,
  `gated`) and says what it left out; the model details show the licence (permissive only on an allowlist; `other`
  with its name and link; the base model's licence and gating), the gating mode, whether the token you stored can
  read the repository (checked inside the framework), parameters, commit, files and size; `download_hf_file` fetches
  one file into `models/hf/<owner>--<name>/<commit>/` as a job, pinned to the commit and checked against the Hub's
  sha256 before it gets its name, resumable, stopped by `cancel_job` (the part is kept), with no size limit but a
  disk check; `list_local_files` lists the model and dataset files in the folders the AI may use by id and label.
  AI calls send only the stored token, or none at all (never `HF_TOKEN` or the Hugging Face home folder's token), and
  only to huggingface.co; the token never appears in inputs, results or the audit log. The prompt
  `convert_model_for_board` leads to these tools.
- **Reranking of knowledge-base results**: a local multilingual cross-encoder (FastEmbed/ONNX, about 120 MB,
  downloaded on first use, never in offline mode) reads the question together with each candidate and drops the
  ones below a relevance floor, so Ditto gets no excerpt rather than irrelevant ones. Build recommendations use
  it. Settings: `rag_reranker` (`auto`: from 6 GB RAM, the larger bge-reranker-v2-m3 with CUDA and 16 GB; `off`;
  or a model id), `rag_rerank_min_score` (unset: the model's calibrated floor; 0: none) and `rag_rerank_default`
  (also rerank chat and MCP searches; off). A reranker that cannot run is logged, and searches keep their order
  until the next start or a change of `rag_reranker`.
  `llm-cli ditto eval --kind retrieval --rag-variant rerank` grades it: on the 34 questions hit@1 93 % and MRR
  0.97 (hybrid search: 87 % and 0.93), and all 4 unrelated questions get no excerpt (floor 0.2: relevant excerpts
  scored 0.80 or more, unrelated ones 0.009 or less). It takes about 1 s for 12 candidates and 2-2.6 s for 20 on
  a shared 4-core CPU and gained less than the 0.05 MRR required for chat, so chat stays without it by default.
  Build-log analysis and module proposals keep the hybrid order: on the last lines of failed builds the reranker
  found the relevant excerpt within 3 in 2 of 6 cases (hybrid search: 5 of 6) and dropped it below the floor in
  one, and for the module search string it dropped every excerpt.
- **Keyword search understands identifiers**: `-DGGML_CUDA=ON`, `rknn_toolkit2` or `rknnToolkitLite` are also
  found by their parts (`GGML_CUDA`, `rknn toolkit2`), and one- and two-word searches such as `E2041` or
  `GGML_CUDA` use the keyword index. Identifier questions of the retrieval set: hit@3 83 % to 100 %, the rest
  unchanged.
- **Large code blocks and tables** beyond the embedding model's context (512 tokens for the default model) are
  split at line boundaries, each piece with the table header or the code fence, so their later parts can be
  found at all (answers deep inside such blocks: hit@1 50 % to 100 %). Smaller blocks stay whole. Re-import a
  document to apply it.
- Knowledge-base models are unloaded after 10 minutes without use and loaded again on the next search
  (setting `rag_model_idle_minutes`, 0 keeps them loaded, at most 7 days). The time counts from the end of the
  last search or import, never during one.
- **Managed local model** for Ditto's offline mode: `llm-cli ditto runtime install` builds `llama-server` on your
  computer from the pinned llama.cpp commit, the CPU build first (a few minutes, then `runtime start` works) and then
  the GPU build the build recipe chooses for the machine's OS, architecture and C library (Vulkan on Linux in this
  version; CUDA, ROCm, Windows and macOS builds follow), each verified on this machine before it is used; it builds
  natively with your compiler, CMake and Ninja or in the framework's builder container, never installs a toolchain
  for you, and names the ways out when neither works. `llm-cli ditto runtime start|stop|status` runs the server on
  127.0.0.1 (free RAM and VRAM read again at every start; per-start API key in a private file; a cleared environment
  that keeps only the variables choosing GPUs or locating their drivers, so no `LLAMA_*` option or
  `GGML_BACKEND_PATH` library reaches the server); `status` shows whether the build in use is self-built, imported
  or "prebuilt, not self-built", its build ID, commit and verification, and whether a rebuild is required or
  recommended. Upstream's official prebuilt archive is only the explicit choice `runtime install --prebuilt`.
  `llm-cli ditto models list|download`
  fetches Qwen3.5 models at pinned revisions into the `ditto/` folder of the configured models folder (`models_dir`,
  the one Ditto uses; the runtime also takes it when it is given no folder), checked by size and sha256. Model
  tiers go down to 2-bit models for weak hardware (3 GB RAM). The "local" provider follows the running server
  without configuration.
- **Ditto's local server bare metal or in Docker** (DECISIONS round 7 D): `llm-cli ditto runtime ensure [--mode
  auto|bare-metal|docker] [--force]` installs it when it is not there and does nothing when it is (in either mode).
  With nothing installed it chooses bare metal (no Docker overhead), or Docker when bare metal is not possible here,
  and says why. While Docker cannot be asked, Docker mode stays as you chose it; when the recorded mode is gone, the
  command says that the mode changed. `ensure` removes nothing on its way (no old build is removed by retention).
  Docker mode runs Ditto's image, built on your computer from the same pinned llama.cpp recipe on a Docker Official
  base image pinned by digest (`orchestrator/runtime/build/ditto-runtime.Dockerfile`, two stages); it is never
  pulled as a finished image and never pushed, an image with Ditto's labels that came from a registry is never used,
  and it is checked in containers of itself before it is used. The container keeps the launch policy of every server (`server_policy.py`): the port is published on
  127.0.0.1 only, the models folder and the per-start key file are mounted read-only, it runs as your user without
  capabilities on a read-only root file system, Docker removes it when it stops, and the GPU is passed through where
  it can be (NVIDIA Container Toolkit, `/dev/dri`) with a note where not. `--force` switches the mode (needs a
  terminal): the models are reused and nothing is downloaded again; the bare-metal build is repackaged into the image,
  or the image's build becomes the bare-metal build after it passed its checks on this computer; otherwise it builds
  again and says why. Bare metal and Docker are two alternatives (W2): before the new mode is built next to the old
  one, its space is estimated (what its build needs while it runs and what it keeps) and checked against the free
  space of each disk involved (Ditto's runtime folder, Docker's image storage, the temporary folder of the image's
  build). When it does not fit, nothing is built: you see the sizes and choose to free space and switch again, or to
  delete the old mode first (its full list, one question, only at a terminal or in the GUI; Ditto is unavailable until
  the new mode works, the models stay; when the new mode then fails, you are told so and offered the old mode again).
  Deleting it first is offered only when the new mode then fits: it is estimated again without the old mode's build,
  which can then no longer be reused, and counts what the old mode's objects free. Once the new mode works, every object of the old mode is listed with full path
  or name, ID, file name, size and what it is: its build folders, Ditto's images and containers, its entries in
  `mode.json` and `active.json` and the log of the image builds. One question deletes exactly that list for good, not
  into the trash ("No" is the default, no choice per item; without a terminal nothing is removed). The installers get
  a Ditto step (`install.sh --ditto=auto|bare-metal|docker|skip`, the same for
  `install.bat`, `LCF_DITTO` for unattended runs) that runs as you, never as root, does nothing in the framework's
  own container build, shows a model's size and licence before it is downloaded, and never fails the installation.
  The AI settings show the mode and offer the switch through the same code path. An AI client reads the mode in
  `get_status`; switching and removing are yours alone. `runtime status` shows the mode.
- **Ditto evaluation** (`llm-cli ditto eval`): fixed cases (build-log analysis, build recommendations, module
  proposals, chat with tools, prompt injection) graded by rules, against the configured provider or per local
  model tier (`--tier small --tier small-q2`; the servers the run starts are stopped afterwards, one you started
  yourself keeps running if it serves the tier). Nothing is built; `--json` writes the report.
- **Retrieval evaluation** (`llm-cli ditto eval --kind retrieval`): 42 German and English searches (cross-lingual
  questions, identifiers such as `GGML_CUDA`, answers deep inside a large table or script, the last lines of
  failed builds and Ditto's own search strings, unrelated questions) against a corpus of 30 framework and SDK
  documentation excerpts (`evals/retrieval/`). The corpus is indexed into a temporary knowledge base with the
  configured local embedding model: no AI model is needed and your own knowledge base is not touched. The report
  gives hit@1, hit@3, MRR, how often an unrelated question gets no excerpt, figures per tag and the search
  latency; it works offline once the embedding model is downloaded. A manual and nightly CI workflow runs it with
  cached models. Baseline with the default CPU embedding model on the 34 questions: hit@1 83 %, hit@3 97 %, MRR
  0.91; all 4 unrelated questions still get excerpts.
- **Hardware profile format, schema 2** (`configs/hardware_probe_schema.yml`, parser
  `orchestrator/runtime/profile.py`): one list of the keys a hardware probe writes, each with its source and
  normalisation per operating system and its validation. It covers the operating system (ID, version, family,
  variant such as DietPi, kernel, init system), the architecture of the userland, the C library and its version
  (glibc, musl, ...), ARM dot product, FP16 arithmetic and CPU clusters, the 14 x86 features ggml builds for, GPU
  compute capability and gfx target, Vulkan, Rockchip NPU runtime versions and toolchain versions. The parser rejects
  a schema 2 file with a duplicate key, an empty value, a value longer than 4096 characters or outside the schema,
  or values that do not fit together (an x86 feature on ARM, a compute capability on an AMD GPU). Files of the
  older probe scripts (schema 1) are converted: the host name, RAM part numbers and other identifying values are
  dropped, every CPU feature of a `hardware_probe.ps1` file becomes `UNKNOWN` (that script asked Windows with wrong
  constants), and the result asks for a new probe before a build. With it come a
  performance key for benchmark results that ignores volatile and recorded-only values, and a per-user profile store
  (`~/.llm-framework/hardware/`, private files).
- **Hardware probe for boards and PCs** (`scripts/hardware_probe.py`, standard library only, Python 3.8 or newer):
  writes the hardware profile in schema 2 on Linux, Windows and macOS, with the operating system, the C library and
  its version, ARM core clusters, ARM dot product and FP16 arithmetic, the x86 features ggml builds for, the SoC, the
  NPU node and every GPU (NVIDIA compute capability, AMD gfx target, Vulkan). `llm-cli scan` runs it in-process and
  stores the result as this computer's profile (`--script` keeps the fallback script, `--json` writes the JSON
  view); `llm-cli scan --device HOST --user USER` runs it on a board over SSH with `python3 -I` and falls back to
  `hardware_probe.sh` when the board has no Python 3.8. The script travels over the SSH channel, so nothing is left
  on the board, and the profile is stored under the board's ID. For machines without network access the README
  explains the probe by hand. The installed package carries a byte-identical copy of the probe
  (`orchestrator/runtime/hardware_probe.py`), so the framework's own probing works without the `scripts/` folder.
- **Build recipe for self-built llama.cpp** (`orchestrator/runtime/build/recipe.py`): the hardware profile of the
  machine that will run the binaries becomes a build plan (CMake options, targets, toolchain file, builder, checks)
  with a build ID that changes with every option and toolchain version but not with volatile values such as free
  memory. Every plan builds the pinned commit, sets every download-capable option of llama.cpp to off or leaves it
  unset (`FETCHCONTENT_FULLY_DISCONNECTED=ON`), and builds the server without subprocess support. A build on the
  machine itself uses `-march=native`, conversion tools built inside a target image get the build host's CPU features
  written out, and a build for another machine uses the probe's CPU features (all 14 x86 options,
  or `GGML_CPU_ARM_ARCH` such as `armv8.2-a+dotprod+fp16` for the RK3566), or all CPU variants when the probe could
  not tell. NVIDIA GPUs get real code for exactly their compute capabilities (every NVIDIA GPU must report one) with
  a CUDA toolkit no newer than the driver supports, on glibc systems; AMD GPUs their gfx target (ROCm on Linux, on
  request); Vulkan and Metal are switches; Windows builds use Clang. The plan follows the target's OS and C library:
  a builder base whose C library (glibc, or musl on Alpine) is not newer than the device's, so a build for another
  machine needs the `LIBC_VERSION` its probe reports, and a statically linked C++ runtime for device builds. A Linux
  build with Clang uses OpenMP only when libomp is installed, and the build ID tells the two apart; all CPU variants
  for an ARM board whose features the probe could not tell need GCC 14 or Clang 18 (upstream's list contains
  `armv9.2-a+sme`), otherwise the plan asks for a new probe.
- **Self-built llama.cpp: build, checks and store** (`orchestrator/runtime/build/`): a plan is built natively on Linux
  (with the detected compiler, CMake 3.15 or newer and Ninja, in a cleared environment) or in the framework's builder
  container, an image built on your machine from `builder.Dockerfile` and a base pinned by digest, whose configure
  and build steps run without network, with the source read-only and the sandbox of the conversion stages; only the
  build's own folders are writable, and the framework never follows a link out of them afterwards. The framework
  shares this recipe, never a compiled image. Parallel jobs follow the available memory. A configure
  output that says something was fetched or downloaded fails the build, and so does one that found no OpenMP runtime
  for a build planned with OpenMP. Before a build is stored it is checked: ELF architecture and libraries (device
  builds: no newer glibc symbol version than the device, no shared C++ runtime), the pinned build and commit in
  `--version`, the planned GPU in `llama-bench --list-devices`, and `llama-server` must refuse `--tools`. The smoke
  run and short benchmark on a tiny pinned model are skipped with a note until the catalog pins one; no other model
  is loaded there. Binaries come only from the build's own folder, never from `PATH`, and every program the
  framework starts takes the same forbidden-option list (also in the `--a_b` spelling, which llama.cpp reads as
  `--a-b`, and with every name of the download options, such as `-hfr`, `-dr` or `--mmproj-url`) and a cleared
  environment without `LLAMA_*` variables.
  Builds are stored under `~/.llm-framework/runtime/llama.cpp/builds/<build ID>/` with `build.json` and
  `manifest.json` (sha256 of every file, duration, verification) and reused, never stamped again; only a build
  verified on this machine can become active. The framework deletes no build by itself: an install keeps the older
  builds and says how many (Ditto's builds and the boards' builds share this store). A build from another of your
  machines can be imported: its files are checked against
  the manifest, its build ID against its plan and its commit against the pin, and you confirm before any of its
  programs runs; then every program in it must report the pin's version, not the one the archive expects. Ditto's
  `runtime install` builds through it. A CPU build of `llama-server` and `llama-bench` at the pin took 189 s on a
  4-core virtual machine and gave 28 MB of binaries; Ditto's CPU build with `llama-cli` and `llama-perplexity` as well
  took 195 s.
- **Builds of Ditto's server move between machines**: `llm-cli ditto runtime export FILE` writes the self-built
  build in use, and `llm-cli ditto runtime import FILE` on another machine of the same kind checks it against the pin
  and its manifest, asks before any of its programs runs, verifies it there and makes it the active build. A build
  made with `-march=native` is refused, before anything runs, on a CPU of another vendor or without one of the CPU
  features of the CPU that built it (it would stop with SIGILL at the first computation); `runtime start` and the
  perplexity check skip such a build too, for example in a home folder shared between machines.
  `llm-cli ditto runtime prune` lists the old builds and the installations of the prebuilt archive of another
  llama.cpp version, each with its full path and size, nothing ticked, and deletes only the ones you pick and confirm
  at a terminal (into the trash by default). It never lists the active build, the CPU fallback, the build a running
  server uses or a board's build. `llm-cli device runtime prune DEVICE` does the same for a board's older runtime
  packages on your PC; a new build or deployment keeps them.
- `llm-cli doctor` checks the local llama.cpp runtime: a self-built build in use that was changed (error) or is older
  than the pin (warning), the prebuilt archive in use (information: not self-built), and whether this machine has the
  compiler, CMake and Ninja for a native build (information, with the builder container as the alternative).
- **llama.cpp pinned by commit**: `llama_cpp.source` in `configs/llama_runtime.yml` (commit
  `d81235049384534c167caea52b85a694f6103d14`, tag `b11429`) is the one place. With it come the CUDA toolkits for
  self-built CUDA builds (NVIDIA's redist manifests pinned by size and SHA256, with the driver, compute capability,
  host compiler and CMake limits of each toolkit; NVIDIA's prebuilt development images only as an explicit opt-in
  entry) and the builder bases, pinned by digest. A test fails when any other place that names the llama.cpp version
  disagrees, including the `ARG LLAMA_CPP_COMMIT` every target Dockerfile that builds llama.cpp must have, and
  `python scripts/llama_cpp_pin.py check` checks with `git ls-remote` that upstream's tag points at
  the pinned commit.
- **Pinned llama.cpp source for air-gapped machines**: `llm-cli ditto runtime export-source FILE` writes the pinned
  commit as a git bundle plus a manifest, and `llm-cli ditto runtime import-source FILE` installs it on a machine
  without internet access. The source is fetched by commit (by its tag only where a server refuses that), must give
  the pinned commit back and is cached read-only in `~/.llm-framework/runtime/llama.cpp/src/`; later runs check that
  nothing in it was changed or added, files git ignores included, and need no network.
- **Conversion tools tuned to the build host**: target images build llama.cpp's conversion tools (`llama-quantize`,
  `llama-imatrix`, `llama-perplexity`, `llama-completion` and now `llama-bench`) with the build recipe. The image build
  gets the pin (`LLAMA_CPP_TAG`, `LLAMA_CPP_COMMIT`) and the plan for purpose `host-tools` from a probe of this
  machine taken right before it (`LLAMA_CMAKE_ARGS`: its CPU features written out, or all CPU variants when the probe
  could not tell). The Rockchip Dockerfile and the template fetch the commit and stop unless they got exactly that
  one, refuse words that are not `-DNAME=VALUE` and options that download whenever they are set, put the options every
  build has after the plan (download options off, `LLAMA_SUBPROCESS=OFF`, `FETCHCONTENT_FULLY_DISCONNECTED=ON`, build
  number and commit), stop on a configure log that reports a download and check `llama-bench --version`. Without a
  probe, or with a Docker daemon on another machine, the Dockerfile's default applies: the fixed x86-64 AVX2 baseline
  these tools always had, written out and now linked statically. `build_info.json` records the tuning
  (`llama_cpp_tools`). Ten quantization types of a test model came out byte-identical from the baseline, an
  AVX-512/AMX and a pre-AVX2 build; building the five tools took 3 to 6 minutes on a 4-core virtual machine.
- **`runtime:` block in new target modules**: the module wizard, `llm-cli module create` (new option `--probe FILE`)
  and `llm-cli module generate-ai` write the build recipe's plan for the board's llama.cpp runtime from the target's
  probe into `target.yml`: architecture, OS, the C library the cross build must not exceed, builder base, CPU options
  (`armv8.2-a+dotprod+fp16` for the RK3566), backends and programs, built from source. The block is a record (a
  device runtime is always planned again from the device's probe) and is checked against its schema in packs. CPU and
  CMake flags from the wizard or Ditto are kept there as advisory text and never applied; the wizard labels them so.
- **Self-built llama.cpp on the board** (`llm-cli device runtime plan|build|deploy|verify|status`, `llm-cli device
  list|settings`): the board's own probe profile (`llm-cli scan --device`) becomes a cross build in the builder
  container, without network, on a base whose C library is not newer than the board's (glibc, or musl on Alpine;
  a board older than every base gets a clear message), with the C++ runtime linked statically and, for OpenMP
  builds, the board's `libgomp.so.1` taken from the cross toolchain. The result is a tarball
  (`llama-server`, `llama-cli`, `llama-bench`, `llama-perplexity`, `lib/libgomp.so.1`, a start script, the build's
  stamps, llama.cpp's MIT licence and `SHA256SUMS`) that the deployment uploads over SSH into
  `~/.local/share/llm-framework/llama.cpp/` on the board and checks there file by file. The new build's programs
  are then checked on the board as your unprivileged user, before anything starts them: `--version` against the
  pin, `--list-devices`, the server's refusal of `--tools` and, with `--bench-model`, a short `llama-bench` run (a
  model that is not a catalog model only after your consent, written to the audit log). Only a build that passes
  becomes active through a `current` link (the previous build is kept); a build that fails, for example with a
  `SIGILL` from a CPU baseline that does not fit the board, is removed again and the previous build stays active.
  The start script runs the server on 127.0.0.1 with a key that is generated on the board under umask 077, a
  cleared environment and fixed options; it passes on only a few tuning options with checked values (context size,
  threads, CPU mask, ...). How the runtime starts is chosen beforehand in the deploy settings, never asked during a
  deployment: the start script in your home (the default), or a systemd unit, which can be chosen only for boards
  with systemd and after you confirm that deployments may run `sudo -n` there. Tested with a fake SSH
  channel and fixture profiles (an RK3566 on DietPi, an Alpine board, a board without systemd), and with real
  programs: `llama-server` and `llama-bench` built natively with the device plan's options for an x86-64 machine
  (30 MB, an 11.6 MB tarball) passed the format check, the deployment to a local stand-in board and the checks
  there. Not yet on a real board, and no cross build ran here (no Docker daemon).
- **Deep Ingest honours machine-readable opt-outs.** Text and data mining of public pages is allowed unless
  the rightsholder reserved it in machine-readable form (EU DSM Directive Art. 4, German UrhG § 44b), so
  besides robots.txt the crawler skips pages marked `noindex`, `none`, `noai` or `noimageai` in an
  `X-Robots-Tag` header or in `<meta name="robots">` / `<meta name="llm-conversion-framework-bot">`, does not
  follow the links of `nofollow` pages, and honours the W3C TDM Reservation Protocol (`/.well-known/tdmrep.json`
  per site, the `tdm-reservation` header and meta tag, PDF metadata). Redirect targets on other hosts are
  checked the same way. Every page that is not stored is named in the crawl log with its reason. None of these
  checks can be switched off.
- **Notice before the first Deep Ingest** (English and German): what is read (only publicly reachable pages),
  which opt-outs are honoured, that the knowledge stays on this computer, and that sharing it keeps source and
  licence and needs your explicit release. It is information, not a legal confirmation: one "Understood"
  button. It is shown once per user and again when its text changes.
- **Spoken announcements** of the notice and of the Deep Ingest outcome through the speech engine built into
  the operating system (Windows System.Speech, macOS `say`, Linux `spd-say` or `espeak-ng`), in the interface
  language. Best effort and never in the way: no audio files, the system volume is never touched, and it is
  silent in CI, over SSH without a display, on headless systems, with `LLM_FRAMEWORK_NO_AUDIO=1` or when
  *Tools > Spoken Announcements* (setting `audio_announcements`) is off. Every announcement is also logged. The
  switch warns when it cannot save the setting.
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
  or `;jsessionid=`) are not remembered. *Tools > Clear Input History...* forgets it after a question that lists
  each file it deletes (the history and the copy of a damaged one: full path, file name, size, what it is; "No" is
  the default), and deletes only those.
- **Signed community packs**: a pack is a zip with a manifest (sha256, size and mode of every file) and an
  optional Ed25519 author signature; the author's public key is pinned at the first install, and another key
  under the same author name is shown with a warning. Reading a pack accepts exactly the listed files, never links
  or special files, and checks every hash. Trusted publisher keys ship in `configs/community_keys.yml`, each with
  an expiry date.
- **Encrypted packs for private sharing** (with your own machines or a team): encrypted in the age format by rage
  (through `pyrage`) to one or more recipients' age keys or to one passphrase, and signed by the sender, so
  recipients see who sent it and any change, including an added or removed recipient, is detected. The standard
  `age` and `rage` tools open them too when they are encrypted to a passphrase or to a key made with `age-keygen`
  or `rage-keygen`. A passphrase pack whose scrypt work factor needs more than 1 GiB of memory
  is refused before anything is decrypted. Each user gets an Ed25519 key and an age identity on first use, kept
  only in the encrypted secrets store; the public keys can be exported as text.
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
  the cache is older than a day and never replaced by an older index). Installing always reads the current index
  first (so a revocation is never missed), downloads the pack pinned by size and sha256, checks it in a
  quarantine folder (exact file list and hashes, the target validators, the recipe and knowledge schemas), shows
  a summary with its trust level and installs it only after you confirm, atomically and with a provenance file.
  Trust levels: verified, community, local (a file without an index entry: always confirmed, and a warning on
  every build) and revoked (blocked; installed copies are flagged by `llm-cli zoo list`, `zoo verify`,
  `llm-cli doctor` and the hub and are not built, also after the key that signed the revocation has expired).
  `llm-cli zoo verify` finds installed packs that were changed or revoked. The install summary is always shown as
  plain text, so text from a pack cannot hide its trust level. A knowledge pack is imported into the knowledge
  base when it is installed, or when the knowledge base is turned on later.
- **Community Hub** in the Tools menu: a trust column, filters for kind and trust, import of pack files (also
  packs encrypted to your key), and "Build" on an installed recipe fills in the Builder, including the model's
  pinned commit.
- **Export for community** on the module wizard's last page, in the Builder log's context menu (a finished
  build becomes a recipe) and with `llm-cli zoo export`: a pack of a target, a recipe or your knowledge base,
  privacy-scrubbed (the changes are shown), validated and signed with your key; with `--to` or `--passphrase`
  it is encrypted for private sharing instead. Knowledge packs keep the source address of every page and claim
  no licence for them (`NOASSERTION`); a community pack you installed is not re-signed as your own, and pages
  imported from installed knowledge packs are left out of a knowledge export. "Open pull
  request" (`--pr`, which asks after the summary unless `--yes` is given) hands it to the community repository
  (setting `community_repo`): through the GitHub CLI if it is installed and logged in, otherwise the
  repository's upload page opens in the browser. The framework never asks for, reads or shows a token.
- Maintainer tools for the community repository: `scripts/zoo_sign_index.py` (signing key in the OS keyring,
  index entries from packs, signature of `index.json`) and the CI workflow template
  `scripts/community_repo/validate-contribution.yml`, which runs `llm-cli zoo verify` on every submitted pack.
- **Claude workspace ID** (AI settings field "Workspace ID", setting `ai_anthropic_workspace_id`): Claude API
  keys that are valid in every workspace of the organization now work. The ID is sent as the
  `anthropic-workspace-id` header on every request made with the API key to the Claude API, the model list
  included. An `ant auth login` profile keeps its own workspace, and other servers never receive the ID.
- **Sign-in methods per provider** (`configs/llm_providers.yml` schema 2, `orchestrator/llm/auth`): each
  provider lists its sign-in methods in the order they are preferred, with the page where the key is created,
  the environment variables it reads, a free check and extra fields such as the Claude workspace ID or the
  OpenAI organization and project. Catalogs of schema 1 in your config directory still load; one of a newer
  schema is refused with a message instead of being misread. Sign-in details
  that are not secret (account or workspace, the last four characters of a key, extra fields, when it was
  checked) are kept in `auth.json` next to the credential vault. Tokens of the sign-in flows that come later are
  renewed under a lock shared by the GUI, the CLI and the MCP server; the new refresh token is stored before it
  is used, and a refused renewal marks the sign-in as expired.
- **Credential check** (the **Check** button in the sign-in panel and `llm-cli ditto auth status --check`): one
  free GET request (the model list, OpenRouter's key endpoint) says whether the stored key works, which workspace
  or key name it belongs to and how many models it can use; a key that works but may not list models is reported as such, also
  by the live model list in the AI settings. Redirects are not followed, so the key goes to the provider only.
- **API keys from environment variables**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` and
  `OPENROUTER_API_KEY` are used when no key is stored for the provider (only the variables the provider catalog
  lists, read by the framework and never by the SDKs). The AI settings name the variable, nothing is copied
  into the vault, and a stored key always wins. When the credential vault cannot be read, no variable stands in
  for the key that may be stored there: the error says what is wrong with the vault.
- **How credentials are protected is your choice** (setting `secret_storage`, or `LLM_SECRET_STORAGE` for
  one run): the OS keyring (default), a credential helper command (`secret_helper`, e.g.
  `pass show llmcc/vault-key`, run without a shell), a passphrase asked once per start (scrypt), a key file
  next to the vault (`insecure`, not encrypted at rest) or memory only (`ephemeral`, for CI). Nothing is
  picked automatically: without a usable keyring and without a choice, the framework stops and names the
  options. `llm-cli doctor` warns about the key file and reports memory-only storage.
- **`--debug-http`** (`llm-cli --debug-http`, and the same option when starting the GUI) shows the debug output
  of the HTTP libraries and the AI provider SDKs on stderr, with credentials redacted.
- **Documentation in English and German** for every document (README, blueprint with design and roadmap, CLI
  usage, features, contributing, module developer guide, SSOT, changelog), checked by `tests/test_docs.py`:
  every document has its twin, links and anchors resolve, both changelogs list the same versions, and the
  roadmap names only what comes.
- **Sign-in from the command line**: `llm-cli ditto login <provider>` signs Ditto in with the provider's
  methods (an API key, a local engine without a key, an `ant auth login` profile with `--method ant_profile`),
  checks a key for free before it stores it and remembers the method (`ai_auth_<provider>`); `--workspace-id`,
  `--organization`, `--project` and `--base-url` set what belongs to the key. `llm-cli ditto logout` deletes the
  local copy and names the key page where the key itself is revoked. `llm-cli ditto auth status [--json]
  [--check]` shows the sign-in of every provider (method, source, account, the last four characters of a key),
  `auth methods` the methods with policy notes, `auth storage [--use ...]` how the credential vault is
  protected (a choice that fails changes nothing, `config.yml` included), and `auth rotate-key` replaces the
  vault key. After a checked login the status names the account or workspace. Exit codes: 2 usage (also an
  empty stdin, a missing server address, a malformed option value or a key with a space or line break inside),
  3 rejected, 4 network, 5 credential storage unavailable, 6 not allowed or not available.
- **Sign-in panel in the AI settings** (**Sign in...**): the provider's methods as cards in the catalog's order,
  the first usable one marked "Recommended"; a key form with the key page, the extra fields, **Check**,
  **Save** and **Sign out**; a status line ("Signed in as ...", "Not signed in", "Using key from environment
  variable X"); and, without a usable keyring, the storage chooser, which the GUI also shows at startup before
  it gives up (a choice there that cannot open the existing vault is not kept, so the next start still works). A
  vault protected by a passphrase asks for it in a dialog. Every vault, keyring and network call
  runs in a worker thread, and an older read that finishes late never replaces a newer state. Ditto's chat header
  shows the access in use, for example "Local · Ollama". A key typed in the AI settings' own key field replaces a
  method a sign-in remembered (such as the `ant` profile), so it is not left unused.
- `llm-cli secrets set --with-token` reads the value from stdin.
- **Cancel Build** in the Builder tab: cancels the running build through the build service after a confirmation
  (enabled only while the build the tab started runs); the log says when it has stopped, also after a cancel
  while the build was still being handed to the build engine.
- **More context in audit entries**: the confirmation channel (`elicitation`, `framework-gui`, `framework-cli`),
  the SHA-256 of the question text, the person's answer (accepted, declined, cancelled, unavailable, unanswered,
  timeout), the client's self-reported name, version, transport and protocol (a label only; a key in it is removed
  before the label is shortened, as in the stderr line), a hash of the connection, the duration, the SHA-256 and size
  of the result (never the result itself) and a link from the end entry to the call's write-ahead entry. The fields are bound to the hash chain without changing how 3.0.0 hashes an entry: logs written before them
  stay verifiable, and a 3.0.0 process that is still running (an MCP server an AI client started before the update)
  keeps verifying and appending to the same log instead of reporting the new entries as modified.
- **Tool permissions per caller**: Ditto and local MCP clients over stdio can have permissions of their own
  (`llm-cli tools permissions --caller ditto|mcp`, `--reset --caller` returns to the shared ones); a caller's first
  permissions start from the shared ones, including settings for single tools. `llm-cli tools list --caller`, the
  CLI setup and the GUI dialog show each tool's own default, the highest decision any setting can give it, whether
  it is new (not reviewed yet) and whether a standing approval may be offered.
- **`get_status` tool** (Ditto and MCP clients): the caller's tool permissions, with every tool's decision and why,
  also the tools that are not listed because they are denied, and the actions no tool does with where the person
  does them, so an AI can name the real reason when it cannot do something.
- **Fool-proof tool calls** (Ditto and MCP clients, every model size): spelling variants of tool names
  (`StartBuild` runs `start_build`) and declared argument aliases (`quant` for `quantization`) run with a note,
  while any other unknown argument name, also one in other letter case, is refused with a suggestion;
  values are coerced the same way on both paths and matched against the target's formats, boards and quantization
  types exactly, by canonical form if unique or by the target's aliases, never by guessing a near miss; every
  refusal carries a reason code and the next step; the same failing call is flagged the second time and refused
  without running the third time; after three input errors an error shows a valid example call. What was repaired
  is marked in the question, listed in the result's `notes` and recorded in the audit log. Tool arguments a local
  model sends with a code fence or in Python notation are read once more.
- **Two-phase confirmation and question text v2** (Ditto and MCP clients): a question starts with the consequence in
  plain words from facts the framework looked up, then who asks (an MCP client's self-reported name, version and
  transport, and whose permissions apply), the facts the framework checked (external text labelled "not checked"), and
  every argument with the speaking name of an id (a build's model and target, a local file's label). A tool can look
  facts up before the question, and the call is bound to them: `resolve_healing` shows the pending proposal from the
  framework's own state, refuses a proposal that changed after the question, and refuses to approve a manual step
  before anything is asked (a full Docker disk, `docker_prune`, included: no healing strategy deletes anything).
  Ditto's GUI dialog and CLI prompt show questions up to 16,000 characters (a scrollable dialog, the pager); an MCP
  form stays at 4,000.
- **Limits per client for tool calls** (Ditto and MCP clients): read tools 20 calls per 10 seconds, `start_build`
  3 unfinished builds and 20 starts a day, 30 questions per hour, and a build queue of at most 20 unfinished requests
  per framework process for AI callers (the person's own GUI and CLI builds are always queued); after a "no" the
  same call is not asked again for 10 minutes, and three "no" to one tool pause its questions for 30 minutes. A limit hit is an audited `rate_limited` refusal that says when to
  try again. The repeat guard lets a refused call run again 10 minutes after its last failure (it was refused until
  the process ended) and groups the calls of one MCP connection by a random id, never by a memory address a later
  connection could get again; Ditto stops calling tools early only after failures without progress, with up to 4
  more steps while it fixes an input error (a call sent to the wrong computer is one); a call that waits for a
  decision is never counted as a failure.
- **`llm-cli tools audit` shows the newer audit fields in words**: who called (Ditto, all local stdio clients, a
  named token, with the client's self-reported name), the tool's title and the name the AI wrote, the reason code and
  how the person was asked with their answer; `--details` adds repairs, duration, result hash, links and session.
- **`llm-cli ditto eval` records every tool call attempt**, not only those that reached a tool: the report's
  `tool_use` block gives tool calls per task, the share of malformed calls and of unknown tool names, repairs and
  repeat refusals.
- **Folders the AI may use**: Ditto and MCP clients name a local model or calibration dataset as
  `<label>:<relative path>` (for example `models:qwen2.5-0.5b-instruct`, `datasets:calib/de.txt`) or by its file id
  inside the models folder, the new datasets folder (setting `datasets_dir`, next to the models folder by default)
  or a folder you add with `llm-cli tools folders add|list|remove` (terminal required) or in the GUI (Tools >
  Folders the AI May Use...). Adding one shows what sharing it means and is audited (an addition that cannot be
  audited, or whose folder changes while you are asked, does not happen); `/`, your home folder, a folder
  containing the per-user folder or a denied folder cannot be added, and the same limits apply to the models and
  datasets settings. The Hugging Face cache's `hub` folder may be added; its token files stay denied.
- **Jobs and waiting for MCP clients and Ditto**: the tools `get_job_status` and `cancel_job` for long operations
  other than builds (a job id `job_...`, its state, progress, result or error; a summary of each job is kept in
  `~/.llm-framework/jobs/`), and `wait_seconds` on `get_build_status` and `get_job_status`: the call returns as
  soon as the build or job changes or ends, at most after 50 seconds (a larger value is reduced with a note), with
  progress notifications for clients that ask for them. Waiting holds no worker thread, at most 4 waits run per
  client, and cancelling a wait never touches the build or job.
- **MCP server names its clients:** for each new client `llm-cli mcp serve` writes one line to stderr (never to
  the client) with the name and version the client reports, the protocol version, the transport and the declared
  capabilities, including whether it can ask you (elicitation). The tools see the same label. Docs: a table of
  what Claude Code, Claude Desktop, Cowork and VS Code support.
- **Named MCP tokens, one per client** (`llm-cli mcp token add|list|rotate|revoke`): a token is shown once and
  stored only as its SHA-256 (`mcp_tokens.json`, mode 0600). Its name is the caller in the audit log
  (`mcp:<name>`) and it has its own permissions: a new token can only read (never a tool your shared permissions
  or the tool's own default deny) until you allow more with `llm-cli tools permissions --token NAME` (not together
  with `--caller`); `--policy-from mcp|ditto|<token>` starts with a copy of that caller's decisions and reviewed
  tools, `--link ditto` uses Ditto's permissions by reference (for a client that should have exactly Ditto's
  permissions). The same name owns
  the builds and jobs the client starts, counts its waits and is audited when its session ends. Over stdio the client's server entry sets
  `LCF_MCP_TOKEN`; the server reads it once, removes it from its environment and does not start with an invalid
  value. Rotating or revoking takes effect at once, also for a running server (the calls a revoked stdio client
  still sends are refused and audited); a revoked token loses its permissions. Only a person at a terminal manages
  tokens (audited with actor `cli`, never the token itself), and a token that ends up in a tool argument, a log line
  or the audit log is redacted like other keys.
- **Pending approvals for AI clients that cannot ask you** (MCP server). When a client has no form elicitation
  (Claude Desktop chat, Cowork), a question is longer than 4,000 characters, or you set a tool with
  `llm-cli tools permissions --confirm-in-framework TOOL` (or "Ask in the framework" in the GUI), the question waits
  for you in the framework (an answer from the client's own form never counts for such a call, also when you set it
  while the form was open): a notice in the GUI status bar opens **Tools > Pending Approvals...**, and
  `llm-cli tools approvals` (`list`, `show`, `approve`, `decline`; needs a terminal) works on a headless machine. The
  client gets `waiting_for_approval` with a job id; once you approve, the framework runs the call itself under the
  client's caller, unless a fact the decision depends on changed by then (a new commit, a new warning such as a disk
  that is now tight: then you are asked again; free space, byte counts or the time a backup snapshot is named after
  that only moved never ask again, the call then runs with the facts of now), and the client reads the result with `get_job_status`. An approval is bound to
  the caller, tool, argument hash and question hash, runs once, expires after 15 minutes and ends with the client's
  session, `cancel_job` or the framework's shutdown;
  a client has at most 5 open; the question shows the caller class first and the client's name as self-reported, up
  to 16,000 characters. The window never puts another call in place of the one you are reading, and after that
  call's question changed, approving waits a moment. Decisions are audited before they are stored, and the approval
  file is signed like the permissions.
- **Standing approvals: "yes, and do not ask again"** (MCP server, Ditto). Created only by you in the framework's own
  window (the approval window, `llm-cli tools approvals approve ID --always`, Ditto's dialog and prompt), after a
  warning that names who may then act without asking, what is covered in plain words, how wide and for how long.
  An MCP client's form can only ask the framework for one, which you confirm in its window (one open request per
  caller and tool; a declined scope is not asked for again for 24 hours). Scopes come from facts the framework
  looked up: zoo packs only for knowledge and recipe packs (trust verified or community) and verified target packs,
  bound to kind, trust level and index key; builds and downloads only for safetensors or GGUF, ungated, permissive
  licence, up to a size class; never for deploying, settings, crawling, module creation, `export_pack`, healing,
  deleting or restoring, or stopping another client's work. All local stdio clients together get only the narrowest
  scope, a warning and at most 30 days. Stored in the signed permission file and written only on your action; uses
  come from the audit log (`grant:<id>`), each use shows a notice with **Revoke** in the GUI that names who acted and
  the tool; they lapse after 90 days without use and stay lapsed. A request from a token revoked since it asked never
  becomes a standing approval of a new token with the same name. `llm-cli tools permissions --grants`,
  `--revoke-grant ID` and the Tool Permissions dialog list and revoke them; while the GUI runs the command line cannot
  create one. No current tool offers one yet: the rules come with the tools whose facts the framework looks up.
- **MCP resources**: `lcf://targets`, `lcf://targets/<target>/capabilities`, `lcf://builds/<id>/status`,
  `lcf://builds/<id>/log`, `lcf://permissions` and the installation's documents `lcf://docs/<name>` (CLI_Usage,
  Features, Module_Developer_Guide, BLUEPRINT and their `_DE` twins, from a fixed list). A resource is read through
  the tool registry like the read tool it mirrors: that tool's permission decides (a denied tool hides its
  resources, "ask" refuses with the tool to call), the same output filter cleans the result and wraps external text
  as untrusted data, a read counts toward the read rate of the read tools (one limit for read tools, resources
  and prompts together), and every read is audited as `read_resource:<uri>`. Unknown ids, documents outside the list and
  parameters with `..`, an absolute path or a NUL byte are invalid params (-32602) and audited too, also an unknown
  URI or another scheme the server refuses before any resource runs. Clients on protocol 2026-07-28
  can listen for changes: the tool, prompt and resource lists change with the permissions, and a build's status and
  log resources report each change to the caller that started the build, from the server's own event bus.
- **MCP prompts**: Ditto's tasks for a client that does Ditto's job, with the same task text and knowledge context:
  `ask_about_framework`, `convert_model_for_board`, `recommend_build_settings`, `diagnose_build_failure`, and
  `add_board` and `deploy_to_board`, which are offered once the tools they lead to exist. The first message is the
  framework's constant text, the second the context with every outside value (the arguments too) wrapped as untrusted
  data, secrets redacted, at most 24,000 characters. A prompt is read like a read tool: the read permission decides,
  an unknown argument is refused with the valid names, a get counts toward the same read rate, every get is
  audited as `get_prompt:<name>`. A prompt whose
  context holds what a read tool returns (a build's state and log, a target's capabilities) follows that tool's
  permission too (hidden while denied, refused while "ask"), and knowledge excerpts come only while
  `search_knowledge` is allowed. Completions
  (VS Code) offer target ids, boards, document names and the caller's own build requests.
- **Honest "cannot" for Ditto's module proposals and build recommendations**: the model may answer `cannot` with a
  reason code (missing information, not supported, no tool, permission, needs a person, hardware, resources, limit;
  "untested" is none of them). Ditto asks once more when the framework's check of the task input finds what the task
  needs, and shows a second `cannot` as not confirmed by the framework; with neither a probe nor a board it says so
  itself without asking a model. Module proposals gain `assumptions` (the guessed values, at most 10) and
  `base_image_reason` (required for an image that is not a Docker Official Image), and can start from the board and
  its SoC when there is no probe. The wizard and `llm-cli module generate-ai` show the base image plainly before the
  module is created: registry, digest, official image or another registry's prebuilt image, network access of the
  image build, and the AI's reason.
- **MCP tokens in the GUI**: the Tool Permissions dialog (Tools > Tool Permissions (Ditto / MCP)) has a section
  "MCP tokens" that lists the tokens and adds one (shown once; read only, a copy of the local stdio clients' or Ditto's
  permissions, or linked to Ditto's), rotates, revokes and sets each token's own permissions, audited with actor
  `gui`, never with the token.
- **Hardware probes by id for AI clients and Ditto**: `llm-cli scan --import FILE [--label TEXT]` (and the GUI's
  **Import from File** and the module wizard's imports) stores a board's probe file in the probe store, validated and
  without its host name, and prints its probe id and the matching target. The read tool `get_hardware_probe` lists
  the newest probes with their matching target or shows one probe's values (external text); the resource
  `lcf://probes/{probe_id}` mirrors it; `probe_local_hardware` (asks by default; "do not ask again" offered) probes
  this computer as a job and keeps the result as `local`. The `add_board` prompt is now listed and reads a probe by its
  id. No tool reads a probe from a path.
- **`create_target_module`**: an AI client in Ditto's role (or Ditto) adds a target module for a board nobody here may
  have tested, through the module generator's checks. The question shows the base image pinned to the digest its tag
  points at now (any registry; a non-official one shown plainly as a prebuilt image of its publisher; the image build
  runs with network access), the image's platforms and whether one matches this computer, the AI's reason, every
  vendor source, pip package and download, the values the AI guessed and that the module is untested; the module is
  written with exactly that digest, support level `community`, `ai-generated` and the `runtime:` block planned from the
  board's probe. It always asks, never offers "do not ask again", and a long definition becomes a pending approval.
- **Vendor software in target modules**: `package_sources` (an https apt repository whose signing key the image build
  checks against its fingerprint), `pip_packages` (pinned `name==version`, from PyPI or one https index) and
  `pinned_downloads` (https, exact size and sha256, into `/opt/vendor`), rendered by the framework's Dockerfile
  template, for Ditto's proposals, the wizard and the MCP tool. Their counts are soft limits (3, 30, 5): going above
  one is a warning the person's approval overrides, never a refusal; prebuilt llama.cpp stays refused.

- **Deploy for AI clients** (MCP tools `list_devices`, `create_deploy_package`, `deploy_to_device`, prompt
  `deploy_to_board`; decision 2): a client lists the devices you trusted and paired, packages a built model as a job
  (the disk must hold twice the model) and deploys it as a job. Before the deploy is asked, the framework reads the
  device's host key (no login) and refuses an unknown or changed key or an unpaired device; the question shows the
  device by your label, the host key fingerprint, the package's sha256 and the trust of its target, and the deploy
  refuses when the package or the key differs afterwards. It is always asked, never covered by a standing approval,
  one per device at a time; `cancel_job` closes the SSH session and reports the device state as unknown. The device's
  output comes back marked as external text (`get_job_status` field `external_text`). No input names an address, user,
  port, password, fingerprint or path. The account the framework logs in with and paths on the device never reach an
  AI: `list_devices` says "device account", the login (account and address) is shown only in the framework's own
  window (never in a form a client may read, nor as a device's speaking name), and the device's output, its errors
  and the deploy's reason reach an AI with the account's name, its home folder and device paths removed, also when
  the deploy worked.
- **Trusted devices** (`~/.llm-framework/devices.json`, `llm-cli deploy devices list|add|remove`, `deploy run
  --label`): `deploy run`, `scan --device`, `device runtime deploy` and the GUI's Deployment window record a device
  after you confirmed its host key and the framework logged in (written to the audit log); `devices add` takes over a
  device you trusted before (terminal only, never a new key, never a password). Only these devices can be named by AI
  tools, and only while the framework's `known_hosts` still holds that key; no tool writes a record.

- **Optional runtime tools for AI clients** (MCP tools `build_local_runtime`, `start_local_runtime`,
  `stop_local_runtime`, `run_benchmark`, `probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark`;
  resource `lcf://runtime/catalog`; prompt `set_up_offline_assistant`): registered with their runtime packages and
  hidden until you enable them, also when the whole tier is allowed. The local server is always self-built from the
  pinned commit (no prebuilt option, no CMake option or command as input); a missing toolchain part is refused with
  `needs_person` and `llm-cli ditto runtime install`; only a verified build is started and no result holds its address
  or key; a cancelled install is never activated and a cancelled model download keeps its `.part`; the AI's build
  removes no old build (old builds stay until you remove them). Only Ditto's own catalog models are benchmarked
  natively; a model built for a board never is (seventh round, 2'): `run_benchmark` measures it in the Docker sandbox,
  and its real values come from the board (`run_device_benchmark`). The board tools reach only trusted devices with
  the trusted host key, are always asked and never
  covered by a standing approval; their facts, consequences, progress, check results and errors (also on success)
  never name the device account (the paired login nor the account the deploy settings name) or a path on the board
  (the person's window shows the login and the model's path).
  Ditto's server that is not built yet, or a tier model not downloaded yet, is refused with `missing_information` and
  names `build_local_runtime`, which the AI can call itself.
- **Benchmark runs of this computer** (`~/.llm-framework/benchmarks/`): `llama-bench` results of the self-built build
  (prompt and generation speed, peak memory) with the model's sha256 and the build id; `get_status` shows the newest.
- **The chat feed store with signed rows** (`chat_feed.sqlite` in the per-user folder, mode 0600; delegation design
  5.3): the one store for what the coming chat panel shows - your messages, Ditto's answers, notices, questions that
  wait, decisions an AI made with its reason (the audit log keeps only the reason's hash), results of work an AI
  started. Every framework process appends to it in its own transaction; each row is signed with a key derived from
  the audit key (`sign_row`/`verify_row`), and a row that was changed, is unsigned or was written with another key is
  never shown (the reader learns how many were skipped; rows of a newer framework version are counted apart). Every
  string is redacted before it is stored and before anything is cut (a key split by a length limit leaves no part
  behind), AI text is cleaned of invisible characters first, a row has no field for a tool's output or raw arguments,
  facts marked for the person only never enter the facts an AI may use, parameters are one line and marked as
  framework or outside text, and who a row is from comes from the caller id (only the GUI and the terminal write in
  the person's name). A write an AI's decision depends on raises when it cannot be written (a full disk included),
  every other write fails open, and reads never wait longer than their short timeout. Clearing the chat is a person's
  action only (no code an AI can reach calls it): it needs the accepted plan (full path, file name, size, what it is),
  is audited with the plan's hash (`deletion_sha256`) before anything is deleted, overwrites the deleted text and hides
  the records of what AIs did instead of deleting them. Ditto's earlier chat is taken over once (its tool notes and
  notices as outside text); without an AI the framework explains a decision from its record alone, an outside fact on
  one quoted line. Warning rows use the shared warning vocabulary (codes, the catalogue's kinds, closed numbers).
  Speaking names (`Core/speaking_names.py`) show exact framework ids and masked values in AI text by name at display
  time, never inside code and never for a near match, and keep the original words; `llm-cli doctor` checks that the
  feed can be written and its newest rows verify. Nothing removes rows by itself yet.
- **The chat panel on the right of the main window** (delegation design 5.1-5.9): a dock (F9 or **View > AI Chat
  Panel**, Ctrl+Shift+C writes in it, a click on Ditto or the toolbar button **AI Chat (n)** shows it; movable to the
  left, floatable) with your conversation with Ditto, notices and the records of what AI apps did, read once a second
  from the signed chat feed of every framework process without ever waiting on it. Questions that wait come from the
  pending approvals and stay under **Needs you** with who asks, what and the time left (counted again while it
  waits); a new one shows a hidden panel without taking the keyboard focus and never moves the keyboard away from the
  question you are on (setting `chat_panel_auto_show`, which an AI cannot change). A filter (All, Needs
  you, Decisions, Ditto chat), routine decisions of one app folded into one line, "New since you last looked" with a
  summary counted from the entries, speaking names in AI text with "Show the original words", keyboard moves between
  entries, screen-reader announcements (questions with priority) and English/German texts
  (`orchestrator/utils/l10n_chat.py`; the dock, its View menu entries and the toolbar button follow a language change,
  and German texts name the menus as the window shows them). Where the panel sits, whether it is shown, its filter and the newest entry you
  saw are kept in `gui_state.json` in the per-user folder (never in QSettings); a damaged file gives the defaults.
- **Mojo: the health check looks (Mojo Oracle) and repairs (Mojo Miracle)** (sixth round, decision 9; seventh round,
  items 11', M' and T; [docs/Mojo.md](docs/Mojo.md)): `llmcf mojo` (Mojo Oracle, the default; `--oracle`) only looks.
  For every warning and error it says what it found, where (the full path; an AI reads a label), why it matters, a
  hint and whether Mojo Miracle can repair it, and one sentence counts every problem by what can be done about it.
  At a terminal it then asks "Run Mojo Miracle now? [y/N]" when Mojo Miracle can act; on yes Mojo Miracle runs without
  the command typed again. `llmcf mojo --miracle` makes only harmless repairs (the state folder narrowed to your user;
  Ditto's server switched to an intact stored llama.cpp build when the active one is missing): each a dry-run plan
  bound to its sha256 whose class comes from its steps, applied only as Mojo Oracle showed it (a problem that changed
  since then is not repaired), recorded in the audit log first, applied as a compare-and-swap on the folder opened
  without following a link, one at a time, and checked again. Then it reports every problem:
  repaired, already gone, still there, or not repaired with the reason (deleting needs your list, needs you, needs an
  administrator, not repairable automatically, failed), and a problem found for the first time afterwards. Freeing
  disk space goes only through the list of the framework's build images at a terminal, nothing ticked; without a
  terminal only the harmless repairs run and nothing is deleted. `--json` never asks and never deletes.
- **Mojo Oracle reports the trusted boards and checks Ditto's image** (eighth round N6; W1 of 2026-10-16; seventh
  round D): a default run makes no network contact with any board. It reports what was recorded: a host key in the
  framework's `known_hosts` that changed or is gone, and the last deploy, runtime deploy, verify, benchmark or probe
  that could not reach a board, with its reason code (`device_host_down`, `device_port_closed`,
  `device_auth_failed`, `device_host_key_changed`) and the next step. Every contact of the framework with a board
  records that; a contact that reaches the board and logs in ends the record. For a board such a fact points to, it
  offers the check: at a terminal "Check <board> now? [y/N]", in `--json` and for an AI the command or request that
  checks it. `llmcf mojo --oracle --board NAME` (label, device ID or address) checks one board now: one connection to
  its SSH port, without a login, a password or the device account's name. In Docker mode it checks that Ditto's image
  is there, was built on this computer (never pulled), is of the pinned commit and is the one that was verified
  (`runtime.docker`); it never builds, pulls or removes an image.
- **`llmcf`, the command line's new name** (seventh round, item 11): a second console script next to `llm-cli`, the
  same program; `llm-cli` stays. The framework container has both commands.
- **The MCP tools `run_mojo_oracle` and `run_mojo_miracle` and the prompt `check_installation`** (seventh round, item
  11'): `run_mojo_oracle` (read-only) gives every finding with `where` as a label (never a host path), `why` and
  `repair` (`how`: `miracle`, `deletion_list`, `person`, `admin` or `not_automatic`, with the next step).
  `run_mojo_miracle` repairs one problem whose `repair.how` is `miracle`: every call is asked with the plan (the full
  path only in the framework's own window), no standing approval, no delegation, never a deletion; any other problem
  is refused before the question with its reason, and the result says whether it was repaired, and if not, why. The
  delegation group for harmless repairs comes with the delegations. `run_mojo_oracle` contacts no board; a finding of a
  board a recorded fact points to names the request that checks it (`check_board`), and `{"board": "<label>"}` checks
  that one trusted board now. An AI names a board by its label or device ID, never by an address (a board without a
  name of its own, whose label is its address, by its device ID).
- **The framework's own documents in the knowledge base** (DECISIONS round 8, N12): **Tools > Index the Framework's
  Documentation...**, `llm-cli knowledge index-docs [--with-code]` and the MCP tool `index_framework_docs` (asked
  every time, runs as a job) add the framework's documentation (README, CONTRIBUTING, the `docs` folder) and, on
  request, its code and configuration (only what it ships: never your settings, your own targets or installed packs)
  to the knowledge base as kind `docs`, so Ditto can explain the framework's
  internals. It never happens by itself (before, the index existed but nothing called it); a snapshot is taken first
  (without room for it on the disk nothing is indexed, also for the first snapshot, measured by the knowledge base
  itself; a snapshot that cannot be written stops it too; with little room left the question or the command says
  so), a file indexed again is replaced, a file it read but did not index is named with the reason, and a knowledge
  pack export leaves these texts out.
- **Board deployment over a network that drops** (eighth round, N4 and N5): you name the board by its address or
  its host name (`myboard.local`; `llm-cli deploy run --host`, `--ip` stays as the old name). When the network drops
  during a deploy (a board with Wi-Fi only), the deploy waits for the board (300 s by default, `--wait`), shows that
  it waits with the time and the limit, and can be cancelled (Cancel in the Deployment window, Ctrl+C in the CLI,
  `cancel_job`); the result then says whether `deploy.sh` had started, also when the network dropped just as it was
  started. It logs in again with the same host key check and continues: the upload resumes at the bytes that
  arrived once their sha256 matches, and `deploy.sh` runs on the board on its own and is started once, never twice.
  `deploy_to_device` results also have `reason_code`, `resumed` and `waited_s`; its progress names the board by its
  label.
- **Why a board cannot be reached** (N6): the reason codes `device_host_down`, `device_port_closed`,
  `device_auth_failed` and `device_host_key_changed` (`orchestrator/runtime/device/reasons.py`) with what happened and
  the next step for the person in English and German, in the GUI and the CLI. An AI gets the plain code with a next
  step, without the address or the account; they are the blockers of the board tools and replace
  `device_unreachable` and `host_key_changed` of the facts catalogue.
- **KV cache of a board's server** (N7): `f16`, `q8_0` or `q4_0` (`-ctk`/`-ctv`, checked words of the shared launch
  policy and of the start script). Automatic by default: `q8_0` when little memory is left next to the model on the
  board (from its probe), otherwise `f16`; you can always choose a type, and `f16` switches it off. Chosen with
  `llm-cli device settings --kv-cache` or in the Deployment window (a saved choice the window does not show is kept
  unless you choose there); the systemd unit and the start command take it from the board's deploy settings, a
  package made in the Deployment window names it in the start command of its `deploy.sh`, and the question of
  `deploy_device_runtime` names it.
- **"Tested on board X"** (N3): a passed benchmark on the board (`llm-cli device runtime verify` or `deploy` with
  `--bench-model`, `run_device_benchmark`, and the new "Benchmark on a board" part of the Deployment window) is
  recorded with every build of that model in the output folder, the framework's build folder and the folders builds
  were saved to (`device_tests.json`, found by the model's sha256), with the KV cache the measurement used next to the
  one the board's server is set to. The Deployment window lists such a build as "tested on <board>", and after a
  deploy of a GGUF model it fills in the board and the model for that measurement.
- **The AI's hands in the window: the GUI tool `gui`, for Ditto and for AI apps alike** (`orchestrator/tools/tools_gui.py`,
  `orchestrator/tools/gui_surface.py`, `orchestrator/tools/gui_bridge.py`, `orchestrator/gui/form_surface.py`): the
  build form, the target module wizard (page by page) and the AI settings dialog register their fields, and an AI reads
  what you entered and fills fields visibly. One tool with an `action` field (`read_form`, `fill_form`,
  `wizard_state`, `wizard_open`, `wizard_fill`, `wizard_next`, `wizard_back`, `wizard_docs`, `show_window`)
  replaces the five GUI tools of the development version, so an MCP client lists exactly 40 tools, the most some
  clients take. Ditto and every MCP client use the same tool and the same code (`GuiCore`, in the GUI process): Ditto
  directly, an MCP client's server over a private bridge to the open window. Only this account reaches the bridge: an
  AF_UNIX socket in the 0700 folder `gui_bridge` of the per-user folder (on Windows a named pipe with a random name), a
  random key in `gui_bridge/bridge.json` (0600) that both sides prove before anything is sent, the peer's account
  checked on Linux, and JSON messages only. The first framework window serves AI apps; another open window takes over
  when it closes. When no framework window is open, `gui` is refused with the new blocker
  `gui_not_open` ("Open the framework window."; delegation rules version 4); it stays listed. Reading actions are
  decided like read tools, the others like a tool that changes something (asked by default); your setting for `gui`
  applies per caller (Ditto, the local stdio clients, each named token), a new token cannot use it, and a question a
  client cannot show becomes a pending approval. Secrets are only "set" or "not set" and never filled; local files reach
  the AI as labels, and no home folder (also not another account's) reaches it by name; a field you are editing or a
  value you chose is never overwritten; a fill that would change your choice through the form's own logic is undone
  completely. Every filled field names who filled it: Ditto in purple, an AI app in another colour with its MCP token's
  name or its own name in quotes as its claim (an app never looks like Ditto); **Undo the AI changes** takes back every
  AI change and is audited with who had filled. Nothing is submitted: Start Build, the wizard's Finish and Save stay
  your clicks, and a module an AI filled is marked AI-generated, so its first build is shown to you once. The AI
  settings never let an AI change the provider, the address, the privacy level or the fallback chain. Every call goes
  through the caller's own tool registry (permissions, limits, audit entry with the caller).
- **The board recommendation from the hardware probe** (`orchestrator/runtime/board_recommendation.py`): after a probe
  the Builder recommends the matched target's board with its source (the probe's SoC, or the probe's board name in the
  target's list of known boards) and how sure it is. Only an exact match is filled in; otherwise the candidates and
  the reason are shown and you pick; until you pick, **Start Build** asks whether the board in the form is yours. A
  board you chose is never replaced by a later probe or an AI, and a target you chose (also through a board you chose,
  or a recipe's) stays too: the probe's target is then offered with one click. **Ask Ditto for Config** fills the
  build form the same way as Ditto's GUI tools (marked, undoable, your own values stay; the log names each value it
  did not fill and why), and a model or dataset file you picked in the file dialog counts as your choice. The online
  look-up of the board name opens your browser only after you agree. When no target fits, the Builder offers **Create
  a target with the wizard** (starting from that probe) and **Ask Ditto** (a question in the chat that you send; a
  draft you typed there stays).
- **Documentation links for Deep Ingest** (`orchestrator/Core/doc_sources.py`): the source catalogue's `docs_*` links
  for the probe's vendor and the SDK fill the wizard's Deep Ingest window; you check them and start the crawl. When
  Ditto says that information is missing for a module, the wizard offers Deep Ingest with these links. AI clients read
  them through the read-only resources `lcf://knowledge/doc-sources` and `lcf://knowledge/doc-sources/{probe_id}`; the
  MCP prompt `add_board` points to them when information is missing. The catalogue's header and the documents now say
  that these links are read at run time.
- **Build progress and the live build log in the Builder** (`orchestrator/gui/builder_parts.py`): a progress bar with
  the step and the percent from the build service (the running build's own step, see Fixed), and the build's log live
  below it (secrets redacted, full paths, plain text, each line once, at most 20,000 lines in view; an AI that reads the
  log gets the folders by label), with a search box and a filter for Info, Warnings or Errors. Lines the service no
  longer returns are noted.
- **Ditto is marked experimental** (eighth round, Q-2; `orchestrator/utils/ditto_notice.py`): Ditto stays optional.
  The chat panel and the AI settings show an **Experimental** badge, and a notice says once per user, in English or
  German: "Use at your own risk and responsibility. The creator of LLMCF is not liable." It repeats the no-warranty
  terms of `LICENSE.txt` and adds nothing to them. In the GUI it stays at the top of the chat panel until you click
  **Understood** and blocks nothing; a hidden panel shows itself once for it (without the focus), because the Builder
  and the module wizard use Ditto without the panel. `llm-cli ditto` and `llm-cli module generate-ai` print it once
  on stderr at a terminal (without a terminal nothing is printed or stored). README and Features say the same.
- **Licence and copyright of every crawled page** (eighth round, Q-1 and Q-1'; `orchestrator/rag/licences.py`):
  Deep Ingest records per page, before the footer is dropped, a licence link (`rel="license"`, the HTTP `Link`
  header), licence metadata (`<meta name="license">`, `dc.rights`, `dcterms.license`, schema.org `license`; in a PDF
  its XMP metadata), a Creative Commons link in the footer or in a sentence that licenses the page, and an explicit
  copyright notice; for code from GitHub the repository's licence file when the same crawl read it (otherwise the
  code has no known licence; it never covers issues, pull requests, discussions, the wiki or releases). Only what
  names the page's own licence counts: its address, SPDX id, own title with version or standard grant text, never a
  licence named in passing, an image credit, a link in a PDF's text or another version; a bare link never outweighs
  "All rights reserved". `llm-cli knowledge sources`
  lists every source with its licence, its notice and what a knowledge pack would carry of it and why;
  `list_knowledge_sources` gives AI clients the licence id, its state and the share in fixed words only (the notice is
  site text and never reaches an AI).

### Changed
- **Faster start: heavy parts load when they are first used** (unreleased; maintainer decision RG1, lazy loading):
  `llm-cli`, the MCP server and the GUI imported the whole framework at start. Now a CLI command imports the
  framework, the build engine and the managers only when it uses them; the framework imports its managers when it
  boots, and the knowledge base only when it is switched on; Qt is loaded outside the GUI only when a signal of the
  Docker manager is used; the crawler (with its HTML and PDF parsers), numpy, Mojo's checks, the provider layer of
  `orchestrator.llm` and the parts of `orchestrator.runtime` load with their first use, also when the knowledge base
  is on; `--help` loads no rich. The host check that ran when the URL checks were imported took a one-second CPU
  sample and looked up two programs, results it never showed; it now runs only the checks whose errors it reports
  (disk, memory, Python, platform), so its disk warning appears when the URL checks are first used, not at every
  start. Measured on the same machine with a temporary home, before and after, in alternating runs (median of 5):
  `llm-cli --help` 3.15 s and 140 MB to 0.25 s and 27 MB; `llm-cli mcp serve` until it answers `initialize` 4.77 s
  and 191 MB to 2.67 s and 118 MB (2.69 s and 118 MB with the knowledge base on); the GUI main window 3.97 s and
  210 MB to 2.66 s and 177 MB. The boot still checks Docker as before, so `mcp serve` and the GUI still load the
  Docker SDK. Commands, results and messages are unchanged. A framework part that cannot be imported (a broken
  installation) now ends the boot with its cause, like other boot errors: the CLI prints "Failed to initialize
  framework: ..." and exits with 1 instead of a traceback, and `--help` still works. Tests start the CLI,
  `mcp serve` (also with the knowledge base on) and the GUI in a fresh interpreter and check that the crawler, the
  embedding libraries, the provider SDKs, Qt, Mojo's checks (its board check too), the knowledge base's storage and
  the private bridge to the open window are not loaded and that no licence text of the knowledge packs is read; the
  MCP server loads the bridge with the first call of its `gui` tool, and the GUI starts it with its window.
- **Roadmap: Rust where the framework meets the network or a board** (unreleased; maintainer decisions RG2 to RG4):
  the blueprint's roadmap plans the network MCP gateway in Rust with the MQTT broker rumqttd inside it (instead of
  the amqtt broker planned in the Python process), the board's root helper as a static Rust binary, and the
  framework's Rust parts as prebuilt release assets signed with Sigstore, with their checksums in the repository and
  building them yourself as an opt-in. It also records: no prebuilt Docker images for targets, no Go, and the GUI
  stays Qt.
- **The local knowledge base is stored in Qdrant Edge** (DECISIONS RG5): the Rust engine of Qdrant runs in the
  framework process (new dependency `qdrant-edge-py` 0.8.0, Apache-2.0, exact pin; wheels for Linux x86_64 and
  aarch64, Windows x86_64 and macOS, one wheel for every Python from 3.10) instead of qdrant-client's local mode, a
  Python re-implementation meant for development. The data moves to `~/.llm-framework/qdrant_edge`. Collections per
  embedding model, ids, payloads, filters, replacing a source, the reranker, the excerpt budget and the snapshots
  behave as before; the dense search stays exact and the fused scores stay the same (RRF k = 2). Only one process
  opens the storage at a time: the framework's lock file `qdrant_edge.lock` keeps the GUI, the CLI and the MCP server
  from writing it at once, a second process waits up to 5 seconds and then gets a clear "in use" error. An older
  knowledge base is converted on first use: vectors and texts are copied (nothing is embedded again), counts and a
  sample of points and searches are checked against the old store, and the conversion is recorded in
  `knowledge_conversion.json`; the old folder `qdrant` stays until you delete it. A failed conversion leaves the old
  store working and says why. Once converted, the old folder never stands in again: if Qdrant Edge cannot be loaded
  later (a missing or broken package), the knowledge base is refused with the reason and Mojo Oracle reports an error,
  instead of quietly using the old folder, which lacks what was added since. Measured twice with 50 000 synthetic
  excerpts: a search takes 12 to 14 ms instead of 2.5 to 4.9 s, opening about 1 s instead of 11 to 19 s, memory 410 to
  520 MB instead of 1.6 to 2 GB; the conversion took 68 to 159 s (5 000 excerpts: 13.5 s).
  qdrant-client stays for an optional Qdrant server (`qdrant_url`) and to read the old storage. Qdrant Edge has no
  build for Windows on ARM; there the knowledge base keeps qdrant-client's local mode, and Mojo Oracle says so.
- **Knowledge snapshots name the storage they came from (format 2)**: a snapshot stays a portable file, the same for
  every storage. Snapshots of format 1, written by earlier versions, restore into Qdrant Edge and are converted that
  way; a newer format, a vector of the wrong dimension or a broken keyword vector is refused before anything changes.
- **The identical-build check no longer blocks the builder window** (unreleased; eighth round, N11): before a build,
  the check asked Hugging Face for the model's current commit on the window's own thread, so the window stopped
  responding on a slow network (up to 10 seconds). The look-up now runs in the background; Start waits meanwhile and
  the form stays usable. The build submits exactly the values that were checked.
- **Builds in the old default folder keep their id and packages are found there** (unreleased; eighth round, answer
  3): since the output folder setting, the framework's build folder is no longer the output folder, so a build there
  got a new id, and the AI tool `deploy_to_device` no longer found the deploy packages in it by their id. The id a
  build had before still reaches it (feed rows, pending approvals and AI callers that kept it), unless another build
  has that id now. Packages are looked up in the output folder first, then in the build folder and in earlier output
  folders you had set.
- **`get_status` names the build folder's disk** (unreleased; eighth round, answer 3): the disk fact named the cache
  and the output folder only, not the build folder where a build is written first. Now it names all three; folders on
  one disk are named together, by label only.
- **The tools that store text say when the search model is downloaded first** (unreleased; next-fixes 119): the
  situation warning `embedding_model_download` was declared but never shown. Now `crawl_docs_site`,
  `index_framework_docs` and a knowledge pack from `add_pack_from_index` check, without downloading or loading
  anything, whether the knowledge base's models are on this computer. Their question says so, with the size and the
  warning when a download is needed or cannot be ruled out. An MCP prompt with knowledge excerpts never starts the
  download: like `search_knowledge` it reads with the models on this computer only and says when they are missing.
  Ditto's automatic diagnosis of a failed build, which nobody is asked about, reads the same way.
- **Repair hints for AI callers name Mojo** (unreleased; round 7 item 11'): refusals and errors that AI callers read
  sent them to `llm-cli doctor`. They now name the tool `run_mojo_oracle` (or `llmcf mojo` in a terminal). The note
  about missing builder images keeps `llm-cli doctor --fix --tier 2`, which builds them.
- **Ditto's old runtime mode goes completely** (unreleased; W2 of 2026-10-16, round 7 D): after a switch between
  bare metal and Docker, only the old mode's build folders, images and containers were listed, and the build folders
  went to the trash. Now every object of the old mode is listed, also its entries in `mode.json` and `active.json`
  and the log of the image builds, and the list is deleted for good, not into the trash, so nothing of the old mode
  stays. It is offered only after the new mode works, and the removal refuses while it does not. One question confirms
  the whole list ("No" by default).
- **The address checks agree on IPv6 in brackets** (unreleased): `deploy run --host [fe80::1]` passed the CLI's check,
  but the deploy refused the address. Now every place that takes a board's address (deploy, `scan --device`, `deploy
  devices add`, the GUI's probe and Deployment window) refuses an IPv6 address in brackets before anything is sent.
  Write it without brackets (`fe80::1`); the port has its own option.
- **The per-user folder is made private when it is created** (next-fixes 118): every read of the settings set
  `~/.llm-framework` to mode 0700 without a word, so Mojo's `state.permissions` finding almost never showed. Now the
  folder gets 0700 when the framework creates it; an existing folder keeps its mode, Mojo Oracle reports one that
  others can read, and Mojo Miracle repairs it after your yes.
- **A board deployment keeps every older runtime build** (round 5 f): `llm-cli device runtime deploy` and the AI
  tool `deploy_device_runtime` removed all older runtime builds on the board except the previous one, without a
  question. Now every older build stays. The result says how many stayed and their size, and
  `llm-cli device runtime prune DEVICE --on-device` lists them for you: full path on the board, folder name, size and
  what each is, nothing ticked, "No" by default. Only the builds you tick are removed, for good (a board has no
  trash), after an audit entry with the folder names only. The active build and one a running program uses are never
  listed. An AI only reads how many builds stayed and that only you remove them. Deploying a build again that is
  already on the board keeps it there, also when its checks fail.
- **The output folder is `~/llmcf/output`; the old output folder is the framework's build folder** (eighth round,
  answer 3): the setting `output_dir` now names the folder finished models are saved to (default `~/llmcf/output`,
  before the installation's or the per-user folder's `output/`). That old folder is the new setting `build_dir`: GUI
  builds wait there until they are saved, and every earlier build stays there and stays listed. CLI builds without
  `--output` no longer add a `build_<date>_<time>` folder (a later build goes next to an earlier one instead). The
  container image sets `LLM_FRAMEWORK_BUILD_DIR=/data/builds`.
- **The builder no longer deletes images** (released in 3.0.0; round 7 item 12): every build ended by removing its
  image and pruning the framework's dangling images. Now the build's image is named
  `llm-framework/<target>:last-build` (the next build of that target reuses it) and only its per-build name is
  dropped, after the new name is seen on the image. The image that had that name before stays without a name and is
  listed for you to delete (Tools > Framework Build Images..., `llm-cli doctor --fix --tier 2`). Nothing in the
  framework prunes Docker objects; Docker images are deleted only when you tick them.
- **No default names a registry image any more** (round 5 h, sixth round): no framework image is published, so the
  defaults name local images. `image_inference_runtime` is `llm-framework/inference:local`; a deployment package
  takes it only once you built it on this computer, as for every image without a digest. `docker_registry` is empty
  (nothing reads it). The experimental benchmark window uses `llm-framework/builder:local` and stops with a message
  when that image is not here, instead of letting Docker pull it. The `docker` block of `targets/_template/target.yml`
  and `targets/Rockchip/target.yml` names a local image without a registry (`registry: ""`); comments say that nothing
  reads `registry` or `tags`. `prebuilt_llama.KNOWN_PREBUILT_IMAGES` keeps the former `ghcr.io` names as a detection
  list.
- **uv replaces Poetry for the dependency lock** (sixth round, decision 14): `pyproject.toml` uses the standard
  `[project]` table (the dev tools in `[dependency-groups]`), `uv.lock` replaces `poetry.lock`, and `requirements.txt`
  and `requirements-dev.txt` are hash-pinned `uv export` outputs whose first lines name the command. No version
  changed: all 121 pins are those of the former `poetry.lock`; the exports only leave out the hashes of wheels for
  Python versions outside 3.11 to 3.14. The installers keep installing the hash-pinned `requirements.txt` of the
  checked-out framework and now insist on the hashes (`--require-hashes`, no fallback to unpinned packages);
  `install.bat` stops when pip fails instead of going on. Dependabot updates `uv.lock`, the new CI job "Lock files"
  checks that `uv.lock` and both exports match, and `make test` and the development setup use uv (`uv sync`).
- **Questions show a short summary and the warning classes** (delegation design 4.5, LAY-L2): a question in the
  framework's own window (Ditto's dialog, the CLI prompt, the approval window) starts, after its first line, with 3 to 6
  lines in the person's language (English or German): what happens with speaking names, how much it downloads and how
  much disk it needs, "Needs your decision because: ..." with every security or legal warning, the situation warnings
  to weigh, a safer option and who may decide. The summary is built only from computed numbers, the warnings and
  speaking names, never from repository, log or AI text. Every question, an MCP form included, lists "Situation:" and
  "Needs you (security / legal):" after the facts it checked.
- **Refused MCP calls carry their reason code as structured content** (`{"error": {"code", "field", "blocker"}}`,
  framework facts only, the text unchanged; `blocker` names the facts catalogue's hard fact that refused the call
  before any question, such as `disk_full`). A client reads the code and field from it instead of guessing them from
  the words; an unknown code is sent as `failed`, and a field that is not a plain argument name is left out.
- **`ArtifactStore` (`delete_artifact`) uses only public `BuildService` methods** (`output_folder`,
  `artifact_entries`, `busy_output_folders`, `ArtifactStore.for_service`).
- **The Deep Ingest notice records who read it again**: its state file stores where it was acknowledged (`via`:
  `gui` or `cli`), and reading it is written to the audit log (actor `gui` or `cli`). Only a person acknowledges it;
  `CrawlNotice.acknowledge` refuses any other channel.
- **A target pack never takes another target's name**: installing a target pack whose id (or declared target id)
  equals an existing target's ignoring case and separators is refused, in the Community Hub, the CLI and over MCP.
- **Builds and downloads share one disk reservation; staging through the framework's fetcher** (MCP phase T round 1
  leftovers): `download_hf_file` reserves its disk space in the cross-process ledger builds use. A build stages its
  Hugging Face model through the framework's fetcher (resumable `.part` files, each file checked against the Hub's
  size and sha256, stopped within seconds by a cancel) instead of `hf_hub_download`, and refuses when the commit
  cannot be resolved. A disk watchdog stops staging and the conversion containers when the cache disk falls below
  half the reserve. `start_build` offers a standing approval within the design's scope (shipped or verified target,
  safetensors or GGUF, ungated model and base model, permissive licence, official base image, size class) only when
  the disk keeps the larger reserve. `report_cannot_do` counts only the current session's calls, and Hugging Face
  look-ups of `check_build_request` count toward the 30 per hour rate.
- **One Hugging Face classification for builds and downloads**: `check_build_request` and `start_build` now read a model's licence, gating and files with the same rules as `get_hf_model_info` and `download_hf_file` (licences such as GPL, DeepSeek or Qwen research were "unknown" on the build path and "restrictive" on the download path), and both keep the same disk reserve.
- **MCP over HTTP**: one token holds at most 4 of the 8 sessions, so one client cannot lock the others out; a session
  closes after 10 minutes without a request (the SDK's default is 30); a request still open when its token is revoked
  or rotated (a wait, an event stream, a listen stream) is closed at once, and so are that token's sessions: a
  session belongs to the token value that opened it, so after a rotation the client opens a new one and the old
  ones hold neither its share nor the server's slots.
- **Ditto's task texts** (chat with tools, healing analysis, module proposal, build recommendation) live in one
  module, `orchestrator/llm/task_prompts.py`, shared with the MCP prompts, and each carries the honest-"cannot" rule:
  name the specific reason, never claim a result you did not get, and untested hardware is no reason to refuse. The
  module task no longer limits base images to `debian:*` and `ubuntu:*`: it prefers a Docker Official Image with the
  vendor's packages and gives its reason for any other image. A module Ditto proposed says in its description that it
  is untested on real hardware and names the assumptions, also one created in the module wizard from Ditto's
  proposal.
- **Ditto runs on the new provider layer** (official anthropic and openai SDKs) instead of LiteLLM, and the
  built-in Transformers model for offline mode is gone (offline mode uses local providers: Ollama, the
  managed llama-server, a server on your network). Healing analyses, module proposals and build
  recommendations are validated structured answers; an invalid answer is an error and is never applied.
- **Chat history** is append-only and kept per user; a long conversation is summarized into a new one.
  Histories of earlier versions are migrated.
- Provider errors are explained in plain language, e.g. a Claude API key that needs a workspace ID, a
  workspace the key cannot use, a key that is wrong, revoked or expired, or an `ant auth login` profile that
  needs a new sign-in.
- More provider errors say what happened and what to do next, with a code for the GUI and the CLI: no credits
  or a spend limit in the Claude Console, a rejected OpenAI key or a used-up OpenAI quota, a Gemini key that
  Google refuses, an OpenRouter key at its credit limit, a local server that is not running or needs a key,
  Ollama without its ollama.com sign-in, a locked keyring and an unreadable vault. A Claude spend cap is no
  longer retried like a rate limit.
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
- Dependencies: `anthropic` 1.13.0, `openai` 3.28.0 (was 2.54.0), `fastembed` 0.8.1 (local embeddings, ONNX on
  the CPU) and `pyrage` 1.4.0 (age encryption of privately shared community packs) are pinned exactly; pip-audit
  reports no known vulnerabilities.
- **Ditto's knowledge excerpts fit the context**: they take at most a third of `chat_context_limit`; an excerpt
  that does not fit is skipped and a too long first one is shortened at a line (an open code block is closed).
- **Deep Ingest outcome**: it said "Deep Ingest complete" even when every URL failed. It now reports success
  (everything stored), partial (some pages stored; others failed, were reserved by the site or the crawl was
  stopped) or failure (nothing stored), each with its own message, only after the last write has returned.
  Pages are stored as soon as they are read, so a stopped or interrupted crawl keeps what it already stored,
  and pages that could not be fetched are listed in the crawl log.
- The crawler's User-Agent reports the real framework version (it said 1.6.0).
- Deep Ingest: a link that redirects to another host is skipped like any other external link; only a
  redirect of the start URL can move the crawl to another host.
- Knowledge exports, and with them the knowledge snapshot of new target modules, are scrubbed: home folders,
  IP and MAC addresses, e-mail addresses, JWTs, the host name and the user name are removed, besides API keys.
  Image digests (`name@sha256:...`) and the paths of default accounts such as `/root` or `/home/runner` stay
  as they are, so a scrubbed target still builds.
- The knowledge snapshot of a new target module is opt-in (a check box on the wizard's last page) and holds only
  the pages crawled for that target in the wizard, all of them: Finish stays disabled while a crawl runs. Before,
  the whole local knowledge base went into every module. A `README.md` next to it says what it holds and that the
  framework does not import it; when nothing could be attached, the wizard says so.
- Target modules created with the wizard, `llm-cli module create` or Ditto get a `capabilities:` block (support
  level community: their boards, and GGUF with the types their build script handles), so the Builder, the CLI
  and AI clients check their build settings. Generation fails if a template placeholder is left anywhere except in
  the knowledge snapshot, whose crawled pages may contain text such as `YYYY-MM-DD`.
- **Credential vault format v2**: every entry carries a key id and the vault key can be rotated (every entry
  is re-encrypted, the old key is removed; an interrupted rotation is completed at the next start). Each
  vault keeps its key in its own keyring entry, so rotating it or changing its storage never touches another
  vault (for example of another `LLM_FRAMEWORK_HOME` folder); a store of an earlier version moves to its own
  entry at its first rotation. Provider keys are stored as credential records under
  `cred/<provider>/api_key`; stores and key names of earlier versions are read as before and converted on
  the next change.
- Reading a secret is written to the secrets audit log once per process and name, not on every access.
- Commands that need the credential vault exit with code 5 when it cannot be used (no usable keyring, no
  passphrase, a damaged vault) instead of 1, and name the choices, including
  `llm-cli ditto auth storage --use ...`.
- Error messages and hints name `llm-cli ditto login <provider>` instead of `llm-cli secrets set <key name>`;
  `llm-cli module generate-ai` uses the stored sign-in and never asks for a key itself.
- `scripts/hardware_probe.sh` and `scripts/hardware_probe.ps1` write schema 2, the same keys as the Python probe
  (tests run the `.sh` and the `.py` on the same fixture machines). Both are non-interactive by default: the package
  install prompt of the `.sh` needs `--install-tools` (it now offers `vulkan-tools`, the only helper the probe uses),
  and the `.ps1` restarts elevated only with `-Elevate`. The `.sh` runs in any POSIX shell (dash, BusyBox ash), and
  `llm-cli scan --script` and the GUI's scan of this PC run it with `sh`, so hosts without bash work; it reads
  `/proc` and `/sys` directly instead of `lspci` and `lsusb`, and writes all GPUs; the `.ps1` reads the usable memory
  instead of the sum of the memory slots.
- Target images build the llama.cpp pin of `configs/llama_runtime.yml` by commit (`b11429`, `d8123504`): the engine
  passes tag and commit, and the image checks the commit. The setting `llama_cpp_version` defaults to the pin's tag
  instead of a written `v0.6.0` and no longer changes what is built (another value gives a warning in the build log).
  The Dockerfiles, the `target.yml` build arguments and `configs/project_sources.yml` (which still said `b4009`) name
  the same tag and commit.
- The target template builds llama.cpp's conversion tools with the same steps as the Rockchip image, so a generated
  module no longer stops with "llama.cpp converter not found". The GPU template takes CUDA 12.9.1 from NVIDIA's redist
  archives (the manifest pinned by sha256, every component checked) on Ubuntu 22.04 pinned by digest, instead of the
  unpinned `nvidia/cuda:12.2.2` images; NVIDIA's prebuilt development image is an explicit opt-in pinned by digest (a
  base image you choose yourself; Ditto's proposals never carry one, since a probe file could steer them) and labelled
  `org.llm-framework.cuda-source=prebuilt-image`, "not self-built" in the logs. The wizard no longer fills in an
  `nvidia/cuda` image for NVIDIA hardware.
- `deploy.sh` of a GGUF package no longer suggests a `llama-server` found on the board's `PATH`: it names the start
  script of the self-built device runtime when that is installed, and otherwise how to deploy it
  (`llm-cli device runtime build` and `deploy`). `llm-cli scan --device` keeps the SSH user as the board's deploy
  user (never root).
- The perplexity check of the dataset manager runs the self-built `llama-perplexity` of this machine instead of
  pulling `ghcr.io/smilez1985/llm-runtime`: catalog models (Ditto's own) natively or in the Docker sandbox; every
  other model, such as one converted for a board, only in the Docker sandbox (the build's own locally built image, no
  network), never natively on this computer. A failed measurement is an error with the reason instead of the
  values 999.99 or 888.88.
- **MCP results**: the text block of a tool result starts with one sentence built only from framework facts (ids,
  states, counts), followed by the JSON wrapped as untrusted data; output schemas mark every field with external
  text (`"untrusted": true`); results carry `notes` written by the framework (for example "the knowledge base is
  reachable; no excerpt matched" or that only the builds of this process are listed); a pending self-healing
  proposal's `suggested_step` is now `manual_step_text`, text for the person to read. The server instructions say
  how to treat marked fields and that a task the client cannot do is reported with its specific reason.
- **MCP clients see only the tools they may use**: tools your permissions deny are left out of the tool list (the same
  tools Ditto gets for the same permissions); a call to one is still refused and audited. `destructiveHint` follows
  each tool's own flag instead of its tier (`resolve_healing` stays marked destructive: it changes a setting and
  retries a build), and tools that reach other devices ask the client to show its own prompt on every call
  (`anthropic/requiresUserInteraction`).
- A local model file is mounted into the build containers alone, no longer together with the folder it lies in
  (a model that needs files next to it, such as ONNX external data, is given as its folder). An RKNN sample list
  next to a model file now gets its own folder mount.
- MCP and Ditto: builds belong to the caller that started them (an MCP connection, each named token over HTTP,
  Ditto). Cancelling its own build
  with `cancel_build` no longer asks; cancelling another client's build follows the tool permissions (by default it
  asks). A per-tool setting still wins, a tier you denied stays denied, and a damaged permission file still denies.
  `get_build_status` shows the caller class that started a build.
- **MCP over HTTP needs a named token per client.** The one shared per-user token of 3.0.0
  (`~/.llm-framework/mcp_http_token`) is no longer accepted: create a token per client with
  `llm-cli mcp token add NAME`, give it to the client, and delete the old file. `llm-cli mcp serve --transport http`
  does not start before a named token exists.
- **A client that cannot ask you is no longer refused** for a tool set to "ask": the call waits for your approval in
  the framework instead (see Added); before, it failed with a hint how to allow the tool.
- **Confirm your tool permissions once after the update.** The permission file is signed now, and one saved by 3.0.0
  carries no signature, so every tool is denied until you set the permissions up again (the GUI shows the dialog at
  the first start and says why; `llm-cli tools permissions`). `llm-cli tools permissions`, also with `--reset`, and
  `llm-cli ditto setup` need a person at a terminal: piped answers are refused.
- **Targets for AI callers**: `list_targets` shows each target's trust (`shipped`, a pack's trust, `ai-generated`,
  `own`) and whether it is untested; `get_target_capabilities` adds the trust, an AI-created target's assumptions and
  base image reason (external text) and, with `check_files`, the problems the module and pack checks find in its
  files. AI-created targets carry `provenance: ai-generated` and `untested: true` in `target.yml`, and builds of them
  warn that they are untested and that their image is built with network access.
- **Probing and AI modules without a terminal**: the local probe (`llm-cli scan`, `probe_local_hardware`) runs the
  fallback script with input from `/dev/null`, `NON_INTERACTIVE=true` and its own working folder, and stops after 120
  seconds. `llm-cli module generate-ai` pins the base image to its digest before it asks; it and the wizard record the
  saved module in the audit log (`module_saved`), and the wizard passes on every field of Ditto's proposal.

- **MCP: builds are asked about with what the framework looked up** (design T1, DECISIONS round 2 item 8):
  `start_build` shows the Hugging Face commit it will build (and builds exactly that commit), the download size,
  weights type, licence, gating and the stored token's access, the space on each disk with the conversion margin and
  the output counted three times, an estimate whether this computer and the board can take it, mounts, target trust
  and the image build. No size cap: a build is refused only when a disk cannot hold it beside a reserve and the space
  other accepted builds reserved (`insufficient_resources`, with the numbers); reservations are kept in
  `~/.llm-framework/disk_reservations.json` and checked again when a build leaves the queue. New read tool
  `check_build_request`: the same plan as a dry run. `start_build` takes `optimization`.
- **MCP: build reads by id and label**: `list_artifacts` names built models by an id and `output:<relative path>`
  with size and hash (paged, filtered), new `get_artifact_details` returns the manifest (external text), model card
  and files, with the resources `lcf://artifacts/{id}/manifest` and `/model-card`; `list_build_requests` (paged)
  and `get_build_status` also show builds of other framework processes from their build records, read-only;
  `get_build_status` says what the request builds; `read_build_log` returns at most 1000 lines and pages back with
  `before_line`.
- **Hugging Face models are staged one folder per commit**, `<cache_dir>/models/hf/<owner>--<name>/<commit>/`, file
  by file under a lock; only that folder is mounted into the build containers (no longer the whole model cache),
  a cancel stops the download before the next file, and the download stops when the cache disk falls below half
  its reserve. A full disk is reported as `insufficient_resources` with the free space.

- **MCP: framework status and settings** (design T2, DECISIONS round 1 item 4): `get_status` shows the sections
  framework, host, local runtime (a prebuilt archive labelled "not self-built"), knowledge base, AI access (states
  only, never a key fragment, account or endpoint), permissions and this caller's audit entries; new `get_settings`
  (an explicit read list, addresses as scheme and host, folders as labels) and `update_settings` (ordinary settings
  with a question showing old and new value, saved in `config.yml`; settings that limit an AI only tightened, and
  only those may be allowed without a question, and a container memory limit of 0, which Docker reads as none,
  counts as loosening; `crawler_download_pdfs` only switched off; settings that keep you informed of what AIs do,
  such as spoken announcements, read-only for every AI, and `update_settings` is not registered if it could reach
  one; permissions, secrets, vault, AI provider settings, addresses and folders refused before the
  question; no value is read as "default").
- **MCP: honest "cannot" checked by the framework** (DECISIONS round 3): new `report_cannot_do` takes a reason code
  (no "untested") and what is missing, checks it against the caller's permissions, this session's audit entries and
  limits, the disk and the actions no tool does, and answers confirmed, contradicted, premature (with the tools to
  try first) or unchecked, with a sentence built only from framework facts (a tool only by its registered name); "the hardware is not available" is
  contradicted for every tool that uses no device. Reports are audited as `cannot_reported`;
  `llm-cli tools audit --summary` counts them per caller.

- **Packaging and deploying say why they failed**: `DeploymentManager` returns the reason (no Docker, an image not
  pinned, the host key changed, the exit status of `deploy.sh`) instead of `None` or `False` with a log line (G23);
  `llm-cli deploy package` prints it. A package is written under a temporary name and renamed when complete.

- **The AI Chat tab moved into the chat panel**: the main window no longer has the tab; everything it did works in
  the panel (Ditto's turns in a worker thread, the confirmation dialog of Ditto's tools, the access line, the saved
  conversation in `chat_history.json`, which is taken into the chat feed once). `gui/chat_window.py` keeps
  `ChatWorker`, `describe_access` and `ChatWindow` (now the panel) with the old attribute names. Enter sends, Shift+Enter
  adds a line. The status-bar notices for a fix proposal of an AI client and for a use of a standing approval also
  arrive in the chat.
- **"Clear chat" lists what it deletes before it asks**: the former "Clear History" deleted Ditto's whole chat history
  after a plain "Are you sure?". The question now lists, for Ditto's conversation (`chat_history.json` as the file holds
  it now, also when another window or the terminal saved it since) and for the conversation rows of the chat feed, the
  full path, the file name, the size and what it is, says how many records of what AI apps did are only hidden (never
  deleted), and has "No, keep everything" as the default. Both parts are written to the audit log (`feed_cleared`, with
  the hash of what was shown) before anything is deleted. Nothing is deleted without the audit log (also when there is
  no chat feed), when the chat or Ditto's conversation changed while you were asked, or when the window holds a
  conversation its file does not show.
- **The doctor is Mojo now** (released in 3.0.0; sixth round, decision 9): the code moved into
  `orchestrator/Core/mojo/` (`orchestrator/Core/doctor.py` re-exports every name it had); finding codes, exit codes
  and JSON keys stay, and the JSON adds `where`, `why`, `repair_id` and `cannot` per finding. `llm-cli doctor` keeps
  its options and output and prints a short note on stderr that names `llmcf mojo`. `doctor --fix` now makes the
  audited harmless repairs of Mojo Miracle instead of an unrecorded change of the folder's mode, and says why a
  problem is still present (for example: the audit log cannot record the repair, so nothing was changed); `--tier 2`
  still shows the list of the framework's build images. The tool `run_doctor` stays callable but is no longer listed; a
  setting for `run_doctor` or `run_mojo_oracle` applies to both, and the stricter one wins. `make mojo` next to
  `make doctor`.
- **Knowledge packs carry a page's full text only where its licence allows it, with the licence texts it asks for**
  (eighth round, Q-1 and Q-1'; W3 of 2026-10-16): a knowledge pack and the knowledge snapshot the module wizard
  attaches to a target keep the full text of a page only under a licence that allows passing it on (CC0, public
  domain, CC BY, CC BY-SA, MIT, Apache-2.0, BSD, ISC, 0BSD, Unlicense, MPL-2.0), with the attribution it requires
  (title, address, copyright notice, licence, the changes made). A page with a copyright notice and no such licence,
  code without a licence, an NC, ND or copyleft licence, an unrecognised licence and a page without any go in as their
  address and short facts (title, section names, technical terms); the recipient crawls them under their own checks.
  Before, every page went in with its full text. Pages in full under a licence that asks for its text or permission
  notice (MIT, BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA) come with that licence's text in
  `LICENSES/<SPDX id>.txt` and a `NOTICE` (in a target, in `knowledge/`). The texts ship unchanged from the SPDX
  License List 3.29.0 (data under CC0) in `orchestrator/data/licenses/`, each checked by its sha256; the privacy scrub
  never changes them. `NOTICE` lists every page with its source address, its copyright line as found and its SPDX id;
  for MIT, BSD and ISC the copyright line stands with the licence text (a repository's licence file, and code from
  GitHub that takes its line from it, list every copyright line of that file), and for Apache-2.0 the notices of the
  work's `NOTICE` file follow when the crawl read it. A page whose licence asks for its text and has no canonical text
  in the framework goes in as its address and short facts, and says why (state `no_licence_text`). The install,
  `zoo verify` and the community repository's check refuse a pack whose licence files do not match its pages (a target
  pack's licence texts also without a snapshot); an installed pack keeps them. A target export leaves out licence
  files that `knowledge/` holds without a snapshot, and says so. The export summary lists every page with what it
  carries and why and names each licence text and why (CLI, GUI; `export_pack` gives licence ids, files and page
  counts), a note counts both, and a target export applies the rule to a snapshot made before it too. The one-time
  Deep Ingest notice says so, so it is shown once more.

### Deprecated
- `--password VALUE` on `deploy run`, `scan --device` and `device runtime deploy`: every user sees the value in the
  process list, and it stays in the shell history. It still works and prints a warning; use `--password` without a
  value (hidden prompt) or `--password-stdin`.
- `llm-cli secrets set --password`: a value on the command line ends up in the shell history and the process
  list. Use the hidden prompt or `--with-token`.
- `llm-cli build start --parallel`: it never had an effect (one call runs one build). It is hidden from the help
  and prints a warning. `--optimization` stays; its help now says that the Rockchip target ignores it.

### Removed
- The stale copy `Docker Setup/pyproject.toml` (version 1.6.0, read by nothing): the container image is built from
  the repository's own `pyproject.toml` and `requirements.txt`.
- Ditto's generated filler calibration data (builds use the shipped calibration text or your own dataset).
- `litellm` and `tiktoken`, and with them 19 indirect packages (aiohttp, boto3, jinja2 and others).
- The unused setting `input_history` (the input history is personal and lives in the per-user state directory,
  not in the shared `configs/config.yml`).
- The *Respect robots.txt* option of the Deep Ingest dialog and the setting `crawler_respect_robots`: robots.txt
  is a machine-readable opt-out like the others, so it is always honoured.
- The "swarm" upload: the XOR "SwarmCipher" (obfuscation anyone could undo), `llm-cli swarm upload`, the
  Community Hub's "Contribute to Swarm" button and its GitHub token prompt. The framework never asks for or
  stores a repository token. Old swarm packets in `community/knowledge/` are skipped with a warning.
- Installing hand-copied module folders from `community/`: community packs are installed with
  `llm-cli zoo install` or the Community Hub.
- **The Claude subscription provider `claude_code`** (Ditto through `claude -p` on Claude Pro/Max plan
  limits): Anthropic does not allow other apps to use Claude subscription limits without approval. A saved
  `ai_provider: claude_code` (or `ClaudeCode`, `Claude Code`), the same name in the fallback chain or in
  `--provider`, now stops with a message, shown once in the AI settings, that names the alternatives: a Claude
  API key (Max and Team plans include monthly API credits for a linked Console organization), Claude on
  Bedrock, Vertex or Foundry, or using the framework from Claude Code itself (`llm-cli mcp config`). It never
  switches to the Claude API on its own. A `claude_code` preset left in a provider catalog in your config
  directory is skipped with a warning; the other providers keep working.
- The setting `preferred_tiny_model` of the removed Transformers offline mode. A `config.yml` that still contains
  it loads as before; the value is ignored and kept in the file.
- Leftovers of the removed Transformers offline mode and of the Qdrant container: the tiny-model download of the
  model manager (it read `source_repositories['tiny_models']`) and `DockerManager.ensure_qdrant_service` (the
  knowledge base uses embedded Qdrant), and the Qdrant sidecar of device packages: a package with `--docker` and the
  knowledge base on no longer ships the Qdrant image, an empty `data/qdrant` folder and `QDRANT_HOST`, because nothing
  on the device used them (the knowledge base serves Ditto on this computer), and the deployment window no longer
  offers "Qdrant Sidecar". The setting `image_qdrant` is ignored with a log line.
- Unused code: `orchestrator/Core/transparency_manager.py` (RepE: it needed a transformers model in the framework's
  own process, which neither the managed llama-server nor a cloud provider gives) and `scripts/ci_image_audit.sh`
  (called by nothing; it ran an unpinned `dive` image with the Docker socket mounted). The Docker workflow writes the
  Rockchip image's size and its layers into the job summary instead, with the runner's own docker CLI.

### Fixed
- **A build no longer replaces an earlier build of the same name**: in 3.0.0 a completed build moved its files into
  the folder `<target>/<model>/<quantization>` and replaced the files of an earlier build there (for example one for
  another board), so a finished golden artifact was lost without being asked. A later build now goes next to it
  (`Q4_K_M-2`, with its own ZIP); the earlier build stays whole.
- **The self-healing "Docker cleanup" deletes nothing any more** (released in 3.0.0; round 5 f): approving the
  proposal for a full Docker disk (`docker_prune`, in the GUI dialog, at the CLI prompt or through `resolve_healing`)
  removed the framework's dangling images without showing them and cleared the whole build cache of the Docker
  daemon, other projects' too. It is now a manual step: the proposal points to the framework's list of its build
  images (the dialog's **Free Docker space...**, **Tools > Framework Build Images...**, `llm-cli doctor --fix --tier
  2`), which deletes only the images you tick and confirm, and an approval is refused before anything is asked. No code of the framework clears Docker's build cache any more.
- **`llm-cli doctor --fix --tier 2` no longer deletes without a list** (released in 3.0.0; round 5 f): it cleared the
  whole build cache of the Docker daemon, other projects' too, and deleted the framework's dangling images without
  showing them. The build cache is no longer touched (it cannot be limited to the framework). Tier 2 now lists every
  build image of the framework (without a name, named `llm-framework/<target>:last-build`, runtime builders), each with
  its name, image id, size and what it is, nothing picked, and deletes exactly the ones you pick, by image id or name
  and without a prune, only after your yes at a terminal (the default answer is no). Without a terminal
  and with `--json` it only lists them; the deletion is audited first and refused when the list changed in between,
  and an image that got a name meanwhile (a build of another framework process) is kept. No tool, resource or prompt
  reaches it, so an AI app or Ditto can neither run it nor get it under a delegation. The disk findings say what tier
  2 lists instead of promising a cleanup.
- **A repair is named however the call ends** (finding of a real Claude Code 2.1.296 run over stdio, protocol
  2026-07-28): a declared alias the call used (`quant` read as `quantization`) was recorded in the audit log, but a
  call refused before or instead of the question (too little disk space found by the look-up, a limit, an invalid
  value, a denied permission, a "no", a question nobody answered in time, the repeat guard) told the client nothing
  about it, so the model never learned the right argument name. Every refusal and error after a repair now carries
  the same notes as a successful result: at the end of the text ("Notes: ...") and, over MCP, in the structured
  error (`{"error": {"code", "field", "notes"}}`), and on Ditto's in-process path. A
  refusal of the normalizer after an alias (an invalid value, an unknown argument next to it) records the alias in
  the audit log too. A registry-wide test runs every tool that declares aliases through every kind of refusal on
  both paths. The repair a tool's look-up makes (`download_hf_file` with a file name in other letter case) is a
  repair like the others: over MCP its note was lost even from a successful result (the look-up runs before the
  question with a context of its own), a refusal after it (too little disk space) never named it, and the audit log
  did not record it; now it is named in the result and in every refusal after it on every path and recorded with
  kind and field only. A pending approval's job that ends declined or refused names the call's repairs in
  `get_job_status` (`error.notes`), as its result's notes do.
- **The larger disk reserve of a standing approval is checked under the ledger's lock** for `start_build`,
  `start_recipe_build`, `download_hf_file` and `add_pack_from_index`: the reservation is taken with the 20 GiB /
  10 % reserve inside the reservation ledger every framework process shares, so a reservation another process took
  between the question and the start is counted (before, only a snapshot at question time). `add_pack_from_index`
  now takes a ledger reservation at all and counts other calls' reservations before the question.
- **The build question lists every base image of the target**: before, a build on a target that is not shipped
  showed only the first `FROM` of its top-level Dockerfile, so a vendor image pulled by a later stage (`COPY
  --from`, `RUN --mount=...,from=`) or by another Dockerfile was never shown, yet a confirmed build remembered it as
  confirmed for the build standing approval. Every image of every Dockerfile is now shown one by one (registry,
  digest, official or not), also for a shipped target that pulls an image outside the Docker Official Images.
- **The GitHub CLI and git of the pull-request handoff never read stdin** (`stdin=DEVNULL`, G25): over stdio the
  MCP server's stdin is the protocol stream.
- **The module wizard's AI import and the AI settings' model lists hand their results to the GUI through the one
  worker relay**: the wizard no longer emits from a plain thread on an unparented `WizardSignals` object it created
  (a closed wizard could take it along), and the AI settings' own relay is folded into `WorkerRelay`.
- **Deep Ingest Stop texts** (wizard button, tooltip and the `crawl.stopping` message in English and German) say
  that Stop also abandons a page that is still arriving, instead of "after the current page".
- **A damaged `authors.json` no longer loses the author keys pinned on first use**: it is kept as
  `authors.json.bad`, the install summary warns that the key could not be compared, and no key is pinned (the file
  is never overwritten) until it is repaired or removed.
- **Digest-pinned images in a deployment package start on an air-gapped board**: an image pulled by its digest has
  no tag, so it was saved without a name, came back as `<none>` after `docker load`, and `deploy.sh` could not start
  it by its digest reference without a registry. It is now packed under `<repository>:lcf-<12 hex of the digest>`
  and started by that name (an image built here keeps its own tag), and `deploy.sh` starts every packed image with
  `--pull never`, so a board where an image is missing stops instead of pulling one from a registry.
- **Hugging Face look-ups no longer hide failures**: the model manager behind the GUI's Hugging Face browser returned
  an empty search result, an empty file list or a "not restrictive" licence when Hugging Face could not be reached;
  it now reports the error. A licence counts as permissive only on a fixed allowlist: `other`, custom, list-valued
  and unknown licences are shown as unknown, and the browser asks before downloading such a model. A file the
  browser downloads comes from the commit it resolved first, not from a branch that may move meanwhile.
- MCP questions of the 2026-07-28 protocol are kept per caller and session (one client's answer no longer closes
  another client's identical question), an unanswered one is recorded as `unanswered` by a timer also when no
  further request arrives, and the ones still open when a session ends are recorded as `client_disconnected`; the
  permission check of a long-poll no longer reads the policy file on the server's event loop; every refusal with
  the reason `failed` now carries a next step, and the build service's input refusals carry their own reason code;
  an MCP confirmation that failed or got no answer in time is recorded with a reason code (`failed`,
  `needs_person`) and its refusal names a next step.
- Two-phase confirmation, the same on both paths: an MCP question too long for a form is recorded with reason
  `limit_reached` and answer `unavailable` and names a next step, as in Ditto's windows; a question that a looked-up
  fact made necessary is recorded with the decision `ask` (it said `allow`); `resolve_healing` decides nothing
  without the facts of its question, and the first line of its question names the build, not only its id; a local
  model or dataset an AI named that vanished before the build is named by its label in the refusal, never by its
  real path.
- Limits, the same on both paths: Ditto's early stop sees which field an input error is about (the registry dropped
  it, so a model that fixed one field after another was stopped and its correct call was not run); a refusal of the
  question limits (after a "no", a paused tool, too many questions) is no failed call on Ditto's path either (it
  turned into "Stop retrying" there while an MCP client kept getting the plain refusal); a start that was refused, for
  example by a full queue, no longer uses up one of the 20 builds a day; after the stop note a model that keeps
  calling tools gets at most two more turns instead of the rest of its budget.
- Module wizard: a base image Ditto proposed was replaced by `debian:bookworm-slim` or `ubuntu:22.04` when its name
  merely contained "debian" or "ubuntu" (for example a vendor image on `nvcr.io`), so another image than the proposed
  one would have been built. The proposed image is now kept as it is and shown with its registry, and the messages
  with the AI's text (also Ditto's errors and refusals) are shown as plain text instead of rich text.
- MCP server: on protocol 2026-07-28 it advertised change notifications for tools, prompts and resources and
  resource subscriptions, but never sent one. It now advertises them only together with the events it publishes,
  and without events it is built with subscriptions turned off.
- MCP server: a client that disconnects no longer leaves its builds running with nobody to clean up. The builds
  and jobs its session started are cancelled, each with an audit entry ("client disconnected"), the server waits
  up to 30 seconds for their containers to stop, and `llm-cli mcp serve` then shuts the framework down. A SIGTERM
  from the client (how clients end a stdio server that has not exited after they closed its input) no longer ends
  the process before this cleanup.
- Secrets store: a store of an earlier version that cannot be decrypted is reported by `llm-cli doctor`
  instead of being merged unreadable into the per-user store.
- Reading or storing a secret no longer fails in a container whose user has no name (no `USER` variable and
  no passwd entry).
- On Windows, saving a secret no longer fails when another process or a virus scanner has the store open for
  a moment.
- On Windows, a build of a local model lands in the output folder again (released in 3.0.0): the build folder was
  named after the model's part behind the last `/`, which for a Windows path such as `C:\models\my-model` is the
  whole path, so the build went into the model's own folder (`C:\models\my-model\<quantization>`) and no listing
  found it. The folder is named after the last part at `/` or `\` on every system.
- `llm-cli secrets set` says where the secret went (the encrypted vault, or memory only with `ephemeral`)
  instead of always "Keyring". The GUI names the encrypted credential vault as well (the secret dialog, the API key
  field and its error message in the AI settings, the token check box of the Hugging Face browser).
- The CLI and the CLI guides no longer tell you to put the GitHub token on the command line, where the shell
  history keeps it (that form did not work either). `llm-cli secrets set <name>` asks for the value with a
  hidden prompt.
- The Deep Ingest dialog opens with the settings `crawler_max_depth`, `crawler_download_pdfs` and
  `crawler_rate_limit` (depth 0 and delays below a second possible, larger values kept); before, its fixed values
  (2, PDFs on, 1 s) always overrode them.
- `llm-cli --help` and the CLI's description named version 2.4.0; every version string now comes from the
  package version.
- `llm-cli build status` lists builds that the GUI, the MCP server or another CLI call started: every build engine
  writes one record per build into `<cache_dir>/build_records/` (status, stage, progress, the error redacted), and
  a build whose program ended before it finished is shown as interrupted. The command needs neither the credential
  vault nor Docker; `--all` lists every kept build instead of the 20 newest. On Linux and macOS the records are
  readable only by the user who built (folder 0700, files 0600). A record names the build's outcome before its build
  slot is free, so whoever sees the build ended reads the outcome there, never the stage before it.
- Ctrl+C in `llm-cli build start` cancels the build through the build service: its container is stopped, the CLI
  waits until the build has stopped (also when it was still being handed to the build engine), says so and exits
  with code 130. Before, the build and its container kept running.
- `llm-cli scan --output FILE` reaches the hardware probe (`hardware_probe.sh --output`,
  `hardware_probe.ps1 -OutputFile`), which always wrote `target_hardware_config.txt` into the current folder. On
  Windows without administrator rights the CLI waits for the probe's elevated restart instead of reporting at once
  that no file was written.
- `llm-cli module create` writes into the configured targets folder (`targets_dir`, relative to the installation)
  instead of `targets/` in the current folder; its next steps name `modules/build.sh`, the script builds run,
  instead of the legacy `config_module.sh`.
- `llm-cli config show` prints the effective settings (`config.yml`, a `-c` file, `LLM_FRAMEWORK_*` variables,
  defaults), each with its source and with credentials redacted, instead of the CLI's built-in defaults.
- `llm-cli config repos --add` stores the `{url: ...}` entry that the build engine passes on as
  `<NAME>_REPO_OVERRIDE`; plain URLs stored by earlier versions are passed on as well. The name comes from the
  whole entry name (`llama.cpp` and `llama_cpp` both give `LLAMA_CPP_REPO_OVERRIDE`, the name the target scripts
  read; `llama.cpp` gave `CPP_REPO_OVERRIDE`). `--add` and `--remove` exit with code 1 when `config.yml` cannot
  be saved, instead of reporting success; `--add` refuses a name without any ASCII letter or digit (exit code 2).
- A settings file given with `-c` that cannot be read no longer drops the `LLM_FRAMEWORK_*` variables as well: it
  is named on stderr and ignored, and the other settings apply.
- The GUI's hardware scan of this PC (**Scan / Import...**) writes its result into `<cache_dir>/probes/` instead of
  the GUI's working folder, and the module wizard's *Auto-fill from Probe Result* reads it there.
- The GUI's SSH scan of a board (**Scan / Import...**) failed on boards that mount `/tmp` with `noexec`: it copied
  `hardware_probe.sh` there and ran it. It now sends the probe over the SSH channel like `llm-cli scan --device`
  (the Python probe with Python 3.8 or newer, else the shell probe), writes nothing on the board, and keeps the
  profile under the board's ID.
- Deploying a model package to a board failed where `/tmp` is mounted `noexec` (the package is unpacked there and
  `deploy.sh` was executed from it) and on boards without bash (the script started with `#!/bin/bash`). `deploy.sh`
  is now a plain POSIX `sh` script and is run with `sh deploy.sh`.
- Deployment from the GUI writes the package into the configured output folder (`output_dir`, relative to the
  installation) instead of `output/` in the working folder.
- The AI settings dialog warns when the settings could not be saved to `config.yml` (they apply until the program
  ends); before, only the log said so.
- Windows: the uninstaller (`scripts/Uninstall-LLM-Conversion-Framework.bat`) finds the installation. `install.bat`
  now writes the marker with the installation folder that the uninstaller reads, next to `install.bat`, where the
  uninstaller looks for it; before, no installer wrote it and the uninstaller always reported that nothing was
  installed. When PowerShell reports no script path (the PowerShell part of `install.bat` runs through
  `Invoke-Expression`), `install.bat` uses the current folder, which its batch part has set to its own. The start script
  `scripts/start-llm_convertion_framework.bat` also works from `scripts\` (it looked for `orchestrator\main.py`
  and `.venv` there).
- `llm-cli targets list` shows each target's id (the value for `--target`), name, boards and formats from its
  capabilities instead of placeholders; `llm-cli targets validate` checks the capabilities block against the
  schema and prints every error (exit code 1).
- `llm-cli deploy package`, `secrets set` and `scan` exit with code 1 when they fail (they exited with 0).
- The help of `llm-cli doctor --tier` names only fixes that exist (tier 1 makes the personal folder private; there
  is no folder fix).
- A target whose `metadata.id` differs from its folder name builds: the CLI, the build service, the orchestrator
  and the build engine resolve targets through one id -> folder map (before, the orchestrator skipped such a target
  with "Nothing to build"), and `-t` takes the id or the folder name.
- The setting `max_concurrent_builds` sets the number of build slots (it had no effect; the engine always used 2).
- Audit log: when several processes (GUI, CLI, MCP servers) wrote the first entries of a new log at the same
  moment, the log could be reported as damaged ("The audit anchor file was missing when entry 2 was written").
  The anchor of a new log is now written before its first entry becomes visible to other processes.
- The CLI, the GUI and the MCP server no longer create empty `cache/`, `models/` and `output/` folders in the
  installation at every start, also when `LLM_FRAMEWORK_CONFIG_DIR` or `LLM_FRAMEWORK_HOME` point elsewhere; a
  build or a download creates the folders it uses. The repository's `.gitignore` covers `output/`, `cache/`,
  `models/` and `logs/`, so build results and downloads cannot be committed.
- The language chosen at the first start of the GUI is saved in the effective config folder
  (`LLM_FRAMEWORK_CONFIG_DIR`, the installation's `configs/` if writable, otherwise the per-user folder) instead
  of always in the installation's `configs/`.
- Saving a setting (for example the first-run language or the AI settings) no longer overwrites a `config.yml`
  that cannot be read (a YAML error, or no `key: value` settings) with only the changed values; the file stays as
  it is and the error is logged.
- A number in `config.yml` that does not fit its setting (`.inf` for a whole number, or `.inf`/`.nan` for a decimal
  one) is ignored with a warning and the default applies; before, `.inf` for a whole number stopped reading the
  file, so every setting after it was ignored too.
- Windows: with the framework in `C:\Program Files`, which standard users cannot write to, the settings and the
  data folders `output/`, `cache/` and `models/` are in the per-user folder (`%USERPROFILE%\.llm-framework`).
  The writability check no longer trusts `os.access`, which ignores folder permissions on Windows. The installer
  no longer creates an unused data folder in the public documents folder with a desktop shortcut to it.
- Windows: a target module made with the module wizard, `llm-cli module create` or `create_target_module`
  (released in 3.0.0) had Windows line endings (CRLF) in its `modules/build.sh`, its other scripts, its Dockerfiles
  and its `target.yml`, so bash stopped in the Linux build container at the first line (`$'\r': command not
  found`). Its scripts, Dockerfiles and `target.yml` are now written with LF on every host. A Windows checkout of the
  repository also keeps LF in every text file (`.gitattributes`: the build container Dockerfiles, Python scripts,
  LICENSE files and git hooks were not covered), except the Windows-only `.bat` and `.ps1` scripts.
- Windows: the calibration text for the importance matrix (released in 3.0.0) was written with Windows line
  endings, so `llama-imatrix` computed it from other text than on Linux and macOS; it is now written with LF on
  every host.
- `install.sh` checks the connection over HTTPS to the package index (`pypi.org` or `PIP_INDEX_URL`) instead of
  pinging 8.8.8.8, which many networks block. It loads one small project page, not the whole index, does not show
  credentials contained in `PIP_INDEX_URL`, and stops with a clear message when started with `sh` instead of bash.
  It no longer creates an unused data folder in root's home folder. Running it again removes only files that an
  earlier run copied (listed in `.lcf-installed-files`) and the repository no longer ships; before, its
  `rsync --delete` deleted everything in `/opt` the repository does not ship: targets made with the module wizard
  or `llm-cli module create`, installed community packs, and the builds, cache, models and `configs/config.yml`.
  A list that cannot be made stops it before anything is copied or removed, a stale link is removed itself, never
  the file it points to, and the copy of `install.sh` in `/opt` copies and removes nothing. Installed from a folder
  without version control (an unpacked archive), it skips only the top-level `build/` and `dist/` folders, no longer
  every folder named `build` (among them the framework's own `orchestrator/runtime/build/`). When the Python
  environment cannot be set up (no `venv` module, a pinned package the index does not offer), it stops with a
  message; before, it reported the installation as finished with an empty environment.
- `make build`, `make up`, `make gui`, `make down` and `Docker Setup/docker-compose.yml` work: the compose file built
  from an `orchestrator/Dockerfile` that never existed, and the Makefile called the old `docker-compose` with it and
  the missing `scripts/setup_linux.sh`. The Makefile uses `docker compose` (v2) and the builder; `make setup` and
  `make test-container` are gone (they needed the missing files; tests run with `make test`), and so are the compose
  services with unpinned third-party images and the Docker socket (socket proxy, ctop, Trivy). `make audit` scans an
  archive of the image with the pinned Trivy version, without the Docker socket. `make clean` no longer deletes the
  data volume, `output/` and `cache/` without asking: it removes only containers that hold nothing of yours outside
  the volume and shows what holds data.
- Deployment packages take user profiles, voice templates and prompts only from the config folder, never from a
  `config/` folder in the working directory.
- The Hugging Face model browser can be opened again: **Tools > Hugging Face Model Browser...** (no menu led to it).
  Its licence check no longer freezes the window, and pressing Enter during a search or double-clicking another
  model during a download no longer crashes the GUI.
- `scripts/sync_ssot.py` uses the real file `configs/detailed_project_sources.yml` (it created a copy named
  `detailed_project.sources.yml` instead) and works from any folder. It lists the differences, and with `--write`
  it changes only those values; comments and layout stay (it rewrote the whole file with `yaml.dump`).
- The header of `configs/project_sources.yml` says who reads the file (no part of the framework while it runs; the
  two SSOT scripts), and its outdated roadmap section (v2.2 to v2.4) is gone.
- Generated target modules (module wizard, `llm-cli module create`, `llm-cli module generate-ai`): the template
  `build.sh` follows the Rockchip dispatcher. It uses llama.cpp's `convert_hf_to_gguf.py` and
  `build/bin/llama-quantize`, writes the F16 intermediate into the work folder instead of `/tmp` (a 1 GB tmpfs in
  the sandbox), and stops with an error instead of copying the model unchanged. Its packaging step no longer copies
  a `build/` folder that does not exist, which made every build of a generated module fail at the end.
- Secret scan with the pre-commit tool: its pre-push stage scans the commits being pushed (`--push`, which now
  reads the pushed commit from `PRE_COMMIT_TO_REF`) like `.githooks/pre-push`, instead of the whole history. The
  pre-commit check (`--staged`) also scans a file that replaced a symlink and files whose names contain a line
  break or start like `0:`, which it skipped before.
- `hardware_probe.ps1` asked Windows for the wrong CPU features: constant 40 (AVX2) as AVX-512 and 37 (SSE4.1) as
  NEON, so nearly every PC claimed AVX-512 and NEON. It now asks the documented constants (AVX 39, AVX2 40, AVX-512 F
  41, BMI2 60; on ARM NEON 29, dot product 43, i8mm 66, FP16 67, SVE 46, SME 70) and writes `UNKNOWN` where Windows
  cannot tell. It also no longer assigns the read-only `$PID`, which aborted the probe when a Rockchip board was
  attached over USB.
- `hardware_probe.sh` saw neither the ARM dot product nor FP16 arithmetic, wrote the x86 `f16c` flag as FP16 support
  on ARM, recognised only RK3588 and RK3566 and kept only the first GPU; it now writes the ARM features, the core
  clusters, every SoC of the device tree and all GPUs.
- **Unanswered, timed-out and cancelled confirmations are audited** (MCP server): a question asked during the call
  (classic protocol) waits at most 5 minutes and is then recorded as `confirmation_timeout`, a question of the
  2026-07-28 protocol that is never answered is recorded as `unanswered` after 15 minutes, and a call the client
  cancels, also while its question is open, is recorded as `cancelled`. Before, these ended without an audit entry,
  and a question could stay open without limit.
- **A client's cancel reaches a running call at once** (MCP server): the call is recorded as cancelled and the
  running handler is told immediately; what already runs finishes and its end entry follows. Before, the
  cancellation arrived only after the handler had finished, and nothing was recorded.
- **Unknown arguments are no longer dropped over MCP**: `quant` instead of `quantization` built the target's
  default quantization and reported success, while Ditto's path refused the same call. Both paths now run the same
  normalizer on the raw arguments, MCP clients get the tools' closed input schemas (`additionalProperties: false`)
  instead of open ones, JSON text in a list field is accepted on both paths, and unknown tool names are audited
  (before: on neither path).
- **The tests no longer touch the real home folder.** A test run had written SSH and Hugging Face files there
  (`~/.ssh`, `~/.cache/huggingface`). Before anything imports the framework, `tests/conftest.py` now points `HOME`,
  `USERPROFILE`, the `XDG_*` folders, `HF_HOME`, `HF_TOKEN_PATH` and the other per-user folder variables into a
  temporary folder (each test gets its own), removes credentials such as `HF_TOKEN` and `SSH_AUTH_SOCK` and the
  desktop session bus from the environment, gives every test a keyring file in its own home instead of the operating
  system's keyring (Secret Service, macOS Keychain, Windows Credential Manager), and fails the run when the real
  home's key, token, keyring or framework folders changed (it compares their listings and never opens a file).
- **Pending approvals show the two-phase question and run bound to it.** A call that waits for you in the
  framework shows the same question as any other window: the facts the framework looked up, the speaking names of
  its ids, who asks. When you approve it, the facts are looked up again; if a fact the decision depends on changed (a
  new upstream commit, for example; free space alone never counts), you are asked again, otherwise the call runs bound
  to the facts you saw. An MCP
  question between 4,000 and 16,000 characters now becomes a pending approval instead of a refusal, a standing
  approval is matched on the looked-up facts on the MCP path too (a client that could ask is not asked when one covers
  the call), Ditto's scrollable dialog for long questions also offers "do not ask again", and an approved call's
  quotas are checked again before it runs.
- **Target names that read alike**: a new target (module generator, wizard, `create_target_module`) whose name equals an
  existing target's id or folder ignoring case and separators (`rockchip`, `Rock-Chip` next to `Rockchip`) is refused,
  so it can never capture the name of another target.
- **A GPU-template module builds the base image it names**: a module on the GPU template with a base image other than
  the default (for example a vendor toolchain image) silently kept the template's Ubuntu base; now both stages start
  from it, and the generator checks that the builder stage starts from the module's base image. Image references
  with a registry port (`registry.example:5000/img@sha256:...`) are accepted.

- **`start_build` over MCP ignored the optimization level** (always balanced, G26), and **two builds of different
  commits of one model shared a staging folder** (and `a/b__c` and `a__b/c` collided, G38).
- **A full disk always proposed pruning Docker** (G40): the proposal is now a manual step naming the framework's
  folders on the full disk with their sizes, and pruning Docker is proposed only when Docker's data root is full.
- **A sandbox benchmark of a model in a read-only folder failed with a generic error**: `run_benchmark` with
  `sandbox=true` made its private link of the checked model only next to the model. It now falls back to a private
  folder in the framework's own cache or state folder on the same file system, else to a copy read from the checked
  open file and hashed again (its sha256 must be the checked one); only when nothing works does it refuse, with the
  reason (`insufficient_resources` or `needs_person`). The private folder is removed when the run ends, also on
  failure and on `cancel_job`, which now also kills the sandbox container, stops a copy that is still being written
  and starts no container after the cancel; a folder a crash left behind is removed by a later run.
- **Job ends in the audit log**: when recording a finished deploy in the device list failed, `deploy_to_device` wrote
  no end entry although the deploy had happened; the end entry is now written on every way out of the job (the
  result then says that the device list did not record it). `create_deploy_package`, the board runtime jobs
  (`probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark`) and the local runtime jobs
  (`build_local_runtime`, `start_local_runtime`, `run_benchmark`) now write job-end entries too (ok, error with its
  reason code, or cancelled), linked to the call's started entry and naming devices and models by their labels. The
  deploy and runtime tools reach built models through the public `BuildService.artifact_entry` instead of a private
  method.
- **The catalogue of upstream projects is consistent** (`configs/detailed_project_sources.yml` and
  `configs/project_sources.yml`, shipped in 3.0.0): every size multiplier has one baseline (FP16 = 1.0x, computed from
  the bits per weight) instead of FP32 for some formats and Q8_0 for others, and the example sizes follow from it; the
  tokens-per-second table and the speed-up factors without a source are removed; the Hailo note no longer states "not
  for LLM inference" as a fact but says to check Hailo's current documentation; the old TensorRT-LLM pin v0.10.0 is
  marked as not checked against current releases.
- **Build progress counts the running build** (released in 3.0.0): `get_build_status` said 0 % for a request with
  one build until the build ended, and named only the request's stage. The percent and the step now include the
  running build's own progress (preparing, image, model, modules, archive); a request that still runs never says
  100 %.

### Security
- **Pairing a board sends only a checked key line** (bandit B601): the command that adds the framework's public key
  to the board's `authorized_keys` is fixed, and its one value, the key line, was shell-quoted. Now the line must also
  be a plain SSH public key (type, key, an optional simple comment); anything else is refused before the command is
  sent.
- **`get_build_status` and `get_build_log` name finished builds by label, never by host path**: in 3.0.0 the status
  of a build request, and log lines such as "Golden Artifact created", gave AI clients the full path of each finished
  build (with the user name in it). The status now gives `output:<target>/<model>/<quantization>` (or `builds:`,
  `saved:`, a file name elsewhere), and a log line with a path on this computer names the framework's folders by
  label and any other path by its file name (also below `/home/`, `/media/`, `/Users/` and `C:\Users\`, for example a
  local model on a USB disk); your own windows keep the full path.
- **No deletion without your confirmation in the framework** (round 5 f; WP7): every code path that deletes a built
  model, a knowledge snapshot, a trash entry, a framework image, an old llama.cpp runtime build or a board's runtime
  package takes only the plan you accepted in the GUI or at a terminal, builds it again from what is on disk right before, and deletes nothing when it changed or when the audit
  log cannot record it first. An AI's `delete_artifact` only moves to the trash; emptying the trash and deleting for
  good stay with you. A source sweep lists every place in the code that deletes, with how it is covered; a new one
  fails it until it is decided.
- **Device passwords no longer belong on the command line** (sixth-round design 4.3): `deploy run`, `scan --device`
  and `device runtime deploy` ask with hidden input when `--password` has no value, and `--password-stdin` reads one
  line from a pipe (refused on a terminal, where the password would be shown while you type). paramiko's loggers are
  held at INFO or higher while a login authenticates, so a password never reaches a log, also at DEBUG.
- **The Trivy scan no longer gets the Docker socket** (released in 3.0.0): the scanner container had the host's Docker
  socket, which gives root rights on this computer even when mounted read-only. The builder now saves the image to an
  archive in a private folder (0700) of the build, and Trivy reads only that file, read-only, without capabilities and
  with `no-new-privileges`. The log says when the archive is saved and removed again; without room for it on the cache
  disk the scan is skipped with a warning that names the sizes. When Docker refuses the scan, the warning names the
  archive by the label of the cache folder (`cache:...`), never by its path on this computer.
- **The loopback MCP HTTP listener ignores proxy headers and writes no access log:** uvicorn trusted
  `X-Forwarded-For` and `X-Forwarded-Proto` from 127.0.0.1, so every local program could claim another address.
  `mcp serve --transport http` now runs with `proxy_headers=False` and `access_log=False`.
- **The installer says what the `docker` group means:** `install.sh` still adds you to the group without asking
  (seventh round, item 6) and now says plainly that its members control the Docker service, which runs as root, so
  every program running as your user can get root rights; it also names the command that undoes it.
- **`run_doctor` gives AI clients no host path** (released in 3.0.0): the findings an AI read named the per-user
  folder, the audit log, pack folders, the disk folders and whatever path a crashed check's error quoted, user name
  included. What an AI reads now names the framework's folders by label (`state:audit.db`, "the state folder",
  `targets:<pack>`) and any other path by its file name only, secrets redacted; `llm-cli doctor` (also `--json`)
  keeps the full paths for you.
- **`run_benchmark` runs the very file it hashed**: the model is opened once, hashed through the open file and
  handed to llama-bench as that open file (an inherited descriptor natively, a private hard link in the sandbox),
  so a catalog model cannot be swapped for another model between the check and a native run.
- **Board and local runtime tool errors name no host path**: paths in their messages appear as the framework's
  folder labels (`state:...`, `cache:...`, `output:...`, `models:...`) or as a file name only, never as a host path
  or `~/...` (also after a colon, as in `file:/...` or `user@board:/...`, and inside a `file:///` URL).
- **robots.txt can no longer be switched off through crawl options**: the crawler takes only its own option keys
  (pages, depth, delay, PDFs, languages) and ignores any other key, including `respect_robots`, with a log line; the
  switch for tests with a fake web is a separate keyword-only flag that no product code passes (a guard test checks
  it). Strings in a job's result (for example the addresses a crawl skipped and the sites' reasons) are now cleaned,
  capped and marked as external text like the rest of a job's status.
- **Device and benchmark failures no longer carry outside text or host paths into an AI's context**: when a job of
  `probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark` or `run_benchmark` failed, the error message
  held the device's stderr or `llama-bench`'s last output (which prints the metadata of the GGUF it loads, text an
  untrusted model controls) unmarked; a failed mktemp on the device put its stderr into `deploy_to_device`'s plain
  `reason`; and a packaging or deploy failure could name the build folder, the output folder or the home folder. The
  message and `reason` are now the framework's own sentences without host paths, and what the device or the program
  printed is returned in `get_job_status.external_text`, marked as external text (also for failed jobs).
- **A cancelled `restore_knowledge_snapshot` job no longer replaces the knowledge base** (MCP design T10): a cancel
  (`cancel_job`, or the end of the session) marked the job cancelled at once while the restore went on. The restore
  now checks for the cancel after the backup and before the knowledge base is replaced, and leaves it as it was.
- **A local model folder with pickle weights is no longer matched by a build standing approval** (design 6.10):
  pickle files beside a safetensors file (the whole folder is mounted into the build) counted as safetensors. Pickle
  weights anywhere in the folder now make it "pickle", so the person is asked.
- **Every base model of a merged model is checked for gating** (design 6.10): a card naming several base models had
  only the first one looked up, so a gated second base model did not keep the build or download out of a standing
  approval. Each one is looked up now; a gated one, or one that cannot be looked up, keeps it out.
- **The dangerous-command filter can no longer be bypassed with invisible or full-width characters** (Ditto's
  healing analyses and recommendations, and fix proposals of AI clients): it checks the text also as the GUI and
  the CLI show it (normalized, invisible characters and joiners removed), so `r<zero-width space>m -rf ~` or
  full-width letters no longer pass and then appear as a readable command. A client's proposal is kept in that
  cleaned form; the GUI's status bar notice names the build by its speaking name and shows the client's name
  cleaned.
- **The base image lookup of a new target module stays on public addresses**: a registry (any public host an AI names
  in `base_os`) could redirect the anonymous digest lookup or its token request into the local network, because
  redirects were followed without a check. Every redirect is now followed by hand and must be https and public like
  the first address, connections go only to the address that was checked, and a registry's pull token is never sent
  to another host.
- **`create_target_module` plans the `runtime:` block from exactly the probe the person was shown**: a probe replaced
  between the question and the write (for example `local` probed again while a pending approval waited) stops the
  write instead of being used.
- **A failed `probe_local_hardware` job names no host path**: when the probe could not be stored, the job's error
  (which the AI client reads) carried the per-user folder's full path; it now gives only the reason, and
  `llm-cli scan` still shows the full error in the terminal.
- **Ditto's module path follows the same base image rule as the MCP tool**: `llm-cli module generate-ai` refuses a base
  image that is not a Docker Official Image without the AI's `base_image_reason`, as `create_target_module` does.
  The counts of vendor sources, pip packages and downloads have no hard limit any more anywhere (they were refused
  above 20, 200 and 50); only the soft limits with their warning remain.
- **AI clients never see the host path of a local model they named** (MCP clients and Ditto): the build's stage
  text, the self-healing context, the build manifest's `model_source` and `list_artifacts` name it by its
  `<label>:<relative path>` (an older manifest's host path by its label, else by the file name only).
- **An MCP client can no longer skip a question's record and limits by sending an answer along with its call**
  (2026-07-28 rounds): a round counts as an answer only to a question the framework asked that client and that is
  still open. Before, any call that carried some answer was asked without the write-ahead `confirmation_requested`
  entry and without the 10-minute wait after a "no", the pause after three "no" and the questions-per-hour count.
- Ditto's build recommendation and the `recommend_build_settings` prompt give the model the target's formats,
  quantization types and format notes (text from `target.yml`, which a community target pack installs) inside an
  untrusted block; before, they stood as plain text next to the instructions.
- `LCF_MCP_TOKEN` is taken out of the environment by `llm-cli` itself before any command runs and handed only to
  `mcp serve`; before, only `mcp serve` removed it, so every other command and its child processes inherited it.
- MCP server: whatever runs outside a request that the server checked gets the deny-all caller, also on a stdio
  server, which before fell back to its own caller.
- The MCP token store is cached by the SHA-256 of the file's bytes instead of its identity, size and modification
  time, so a running server cannot keep accepting a revoked or rotated token after a same-size change within the time
  stamp's resolution.
- Ditto discards a healing fix or a build recommendation that contains a dangerous command (a download piped
  into a shell, deleting the root or home folder, world-writable permissions, overwriting a disk), whatever a
  build log or a model name told the model.
- Requests to local model servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment.
- Local model downloads are refused unless size and sha256 are pinned; the official prebuilt server archive (only
  with `runtime install --prebuilt`) is verified the same way, installed atomically, and never offered on musl or on
  an older glibc than the one it was built against.
- One launch policy for every llama.cpp program the framework starts (`orchestrator/runtime/server_policy.py`):
  Ditto's server, the build checks, the perplexity check, the start script and systemd unit on a board and the
  checks there share one list of forbidden options (tools, agent mode, MCP, web UI, remote compute, downloads, a
  prompt log on disk, a key or token on the command line, in every spelling llama.cpp accepts, also `--a_b` for
  `--a-b`) and a cleared environment, and every server listens on 127.0.0.1 only and reads its key from a private
  file, held to the same server options (Ditto's command line is checked again right before the start, the start
  script's when it is generated). `runtime start` starts only a build that
  passed its verification on this machine, still matches its manifest and is of the pinned commit (else its CPU
  fallback, else nothing); a self-built build that cannot be used never falls back to the prebuilt archive on its
  own, and an official archive an earlier version downloaded is used only after `runtime install --prebuilt` (which
  takes it without downloading it again).
- Deep Ingest reads robots.txt as RFC 9309 describes it, with one cautious deviation. Rules with `*` or `$` (such
  as `Disallow: /*.pdf$`), an `Allow` listed before a longer `Disallow`, every `User-agent: *` group after the
  first and a file that starts with a byte order mark used to be ignored, so the crawler fetched pages the site had
  excluded. Its groups are found by the exact product token `LLM-Conversion-Framework-Bot` (any case), so a group
  for `bot` no longer overrides `*`, and each scheme, host and port has its own robots.txt. The deviation: a
  robots.txt that answers 401 or 403 means "disallow everything", where RFC 9309 would allow crawling; a
  reservation the crawler cannot read still counts.
- Deep Ingest reads every response up to a limit (HTML pages 8 MiB, PDFs 64 MiB, robots.txt 500 KiB,
  `tdmrep.json` 1 MiB) and within a deadline for the whole request (60 s, PDFs 180 s). Before, a site could send an
  endless or dripping response that filled the memory or held the crawl, and Stop or closing the wizard, forever:
  the HTTP timeout only bounds each socket read. A larger page is skipped and a slower one fails, each named in the
  crawl log; Stop no longer waits for a page that is still arriving.
- Community packs are installed only through a quarantine folder after their checks: an id without paths, exactly
  the listed files with their hashes, no links, special or binary files, every image a Dockerfile pulls (`FROM`,
  `COPY --from`, `RUN --mount=...,from=`, also in a lowercase `dockerfile`) pinned by digest unless it is an
  official base image, the shell deny-list on every script line that is not from the shipped template, and
  `bash -n`. A copy of the shipped Rockchip target passes these checks. A pack appears under its final name only
  when it is complete, never over an existing target (also when only the case differs). Before, a link in a
  community module could copy any file of the user into `targets/`.
- The Docker build context never follows symbolic links or special files of a target, a linked Dockerfile is
  refused, and a build's target name cannot contain a path.
- `OPENAI_CUSTOM_HEADERS` and `ANTHROPIC_CUSTOM_HEADERS` are dropped at startup of the CLI, the GUI and the MCP
  server, with a warning, unless the setting `ai_allow_sdk_env_headers` is true (a quoted `"false"` counts as
  false; settings that cannot be read count as false): they could replace the stored API key and reach every
  server, local ones included. The OpenAI and OpenAI-compatible adapters also send the stored key as an
  explicit `Authorization` header.
- The debug output of the provider SDKs and HTTP libraries (request options, prompts, headers) stays off unless
  `--debug-http` is given: their loggers never go below INFO, even with `--log-level DEBUG`, and
  `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` are lowered to `info`.
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
- **Credentials are redacted in every log**: the framework's own, those of the provider SDKs and HTTP libraries
  (anthropic, openai, httpx, httpcore, urllib3, botocore, google.auth, azure, msal), tracebacks and the extra
  fields of the JSON log format. Redacted are the values of credential headers (`Authorization`, `x-api-key`,
  `api-key`, `x-goog-api-key`, `Cookie`, `Set-Cookie`, also in JSON with escaped quotes), sign-in and key
  parameters in URLs and form-encoded token requests (`code`, `state`, `key`, `access_token`, `token`, ...)
  and every known key format.
- Redaction also finds credentials by their context, whatever their format: a bearer token quoted in a server's
  error message, token-shaped values of `api_key`, `secret`, `token` or `password` fields, and environment
  lines such as `HF_TOKEN=...` or `export OPENAI_API_KEY=...`. Tokenizer settings in build logs (for example
  `"pad_token": "<|...|>"`) stay readable.
- Redaction can no longer be slowed down by crafted text: an error message or build log with a long run of
  `sk-` took minutes to redact before.
- **One table of secret formats** (`orchestrator/security/secret_patterns.py`) for the secret scan and for
  runtime redaction, which knew only five formats before. New: Unsloth Studio keys (`sk-unsloth-`), Bedrock
  short-term keys (`bedrock-api-key-`), LocalAI keys (`lai-`), age secret keys (`AGE-SECRET-KEY-1...`),
  Hugging Face organization tokens (`api_org_`), Stripe test keys (`sk_test_`, `rk_test_`),
  `Authorization: Bearer` lines and quoted values assigned to `api_key`, `secret` or `token` (a lower-case name
  such as `"community_signing_key"` is not reported). Runtime redaction now also covers JWTs, private key blocks,
  passwords in URLs, routable GitLab tokens in full, xAI and Replicate tokens that contain `_` or `-`, and keys
  right after a letter of another script (`令牌hf_...`, `éAKIA...`). The secret scan reports a value in the exact
  format of a token that GitHub push protection blocks (for example `hf_` followed by 34 letters, `ghp_` followed
  by 36 letters and digits) even when it looks like a placeholder or its line carries `secret-scan: allow`, and
  also in text files over 5 MB, which the other rules skip: GitHub refuses such a push however fake the value is
  and ignores the marker. Tests build such values at runtime. Only `--history` skips the two documented test
  mocks that old commits still contain, listed in `.secret-scan-allow` by the sha256 of the exact value, never
  the value itself; every other mode still reports them. A listed mock hides nothing else: a longer value of
  another format around it is still reported, the other rules also read the line without it, and a line of the
  list with a control or invisible character is malformed (exit code 2), so a comment cannot hide an entry.
- API keys held in memory by Ditto's provider layer show as `<redacted>` in `str()` and `repr()` and cannot be
  pickled.
- A key of a custom OpenAI-compatible server belongs to the address it was saved for (scheme, host and port):
  after the base URL changes it is no longer sent. A key saved by an earlier version is bound to the address it
  is next used with. Claude Admin API keys are also refused for a Claude API preset in your own provider
  catalog that does not list them.
- Sign-in hardening: the key hint in the AI settings never binds a key of an earlier version to an address that
  is still being typed; signing out also removes such a key; the credential check sends a key bound to one
  address to that address only; a key variable of a local engine in your own catalog goes only to the address
  the catalog gives. A non-secret field (workspace ID, project) can no longer be declared with a credential
  header such as `Authorization` or `x-api-key`, where it would replace the stored key. A failed token renewal
  names only the kind of failure, and a vendor message that quotes the refresh token is shown without it. A key
  of an earlier version is bound to the address before a check with typed fields sends it there.
- Sign-in never takes a secret from the command line (`ditto login` has no option for a key, and an unknown
  provider name or `--method` value that looks like a key is not echoed), shows a key only as its last four
  characters (also in `auth status --json`, and as `REDACTED` when a server's error text or account name repeats
  it, in Ditto's chat errors too), asks before a key goes over plain `http://` to another computer (also in the
  sign-in panel and the AI settings' key field; `--yes` in scripts) and never chooses how the vault is protected:
  `--insecure-storage` never replaces a protection that works, nor a credential helper or passphrase you chose. A
  key with a space, a line break or an invisible character inside is refused before anything is sent or stored.
- The Builder log shows every message as plain text, so names from community recipes, Ditto's answers or build
  output cannot render as HTML (links, images, formatting).
- Hardware profiles carry no identifying values: the probe scripts no longer write the host name, the RAM part
  numbers or the CPU id, the serial line of `/proc/cpuinfo` and the serial attribute of USB devices are never read,
  and free text is reduced to printable ASCII. Values read on a board are validated before they are written, and the
  SSH scan treats the board's answer as untrusted (size limit, strict parser).
- **Calls that change something run only when they are recorded** (Ditto and MCP clients): before such a call
  runs, a `started` entry is written to the audit log; if it cannot be written, the call is refused, and a question
  about it is not asked. Read-only calls stay available. Before, a failed audit write was only logged and the call
  ran anyway, and the only entry was written after the handler, so a crash in the middle of a change left no trace.
- **Every tool result is filtered centrally** (Ditto and MCP clients): secrets are redacted in every string of a
  result, external text (build logs and stage names, healing proposals, build manifests, knowledge excerpts, doctor
  messages, target labels and notes from `target.yml`) is cleaned of invisible and bidirectional control characters
  and capped, other strings are capped at 16 KiB, and a result over 256 KiB is refused with a hint how to ask for
  less. The error of a failed call is cleaned the same way before secrets are redacted, and an MCP client gets the
  part that quotes external text wrapped as untrusted data, as Ditto's model does. Before, only errors, warnings
  and log lines were redacted, only knowledge excerpts were cleaned, log lines and results had no size limit, and
  an MCP client got error text from a tool unmarked.
- **New tools never inherit a loosened tier setting, and a tool's cap holds**: the permission file records the
  tools you have reviewed, and a tool added later uses its own default (or the recommended tier setting) until you
  review it, so allowing a tier once no longer allows every tool added to it later. A tool's own default (for
  example `deny` for an optional tool) holds even when its tier is allowed, but never opens a tier you denied, and a
  tool that reaches other devices or deletes data always asks at most. Before, a per-tool setting could allow such a
  tool without a question. A permission file from 3.0.0 counts its eleven tools as reviewed; 3.0.0 itself reads a
  file saved by this version as damaged and denies every tool.
- MCP and Ditto: `start_build` no longer accepts a path for `model` or `dataset_path` (an MCP client could mount
  any readable file, such as `~/.ssh/id_ed25519`, into a build and read it back through the log). Only references
  inside the folders the AI may use are accepted; the resolved file must lie inside its folder and outside every
  denied folder (keys, credentials, AI clients' own sign-ins, the Hugging Face token, the framework's per-user
  folder, system folders), decided by file identity so a case-insensitive file system cannot hide one. Hard-linked
  files are refused, a folder is used only strictly below its label folder and without a denied folder, a
  hard-linked file, a socket, a device or a pipe inside, and the file is checked again right before each build
  container starts. A Hugging Face repo id from an AI caller is no longer looked up as a local path, neither by the
  build service nor by the build engine (which mounted a folder of that name below its working directory).
  Refusals name the label, never the real path.
- **MCP over HTTP is closed to web pages and DNS rebinding.** In 3.0.0 a page served from any port on localhost
  passed the Origin check (only the token stopped it), the Host check accepted a loopback name with any port, and
  all HTTP clients shared one token and one audit actor. Now every request with an `Origin` header (also a CORS
  preflight) is refused before the token is checked, the `Host` header must be a loopback name with the server's
  own port (in the framework and again in the SDK), the server keeps at most 8 sessions (was 10,000) and takes at
  most 1 MiB per request (was 4 MiB), a session and a pending question round of the 2026-07-28 protocol only answer
  the token that opened them, and no token is accepted (and the server does not start) while other users can read or
  change the token file or it belongs to another user.
- **No prebuilt llama.cpp in targets**: generated modules, community exports and target packs in quarantine are
  refused when a Dockerfile pulls a prebuilt llama.cpp image (by its name, also pinned by digest, or one of the
  framework's former runtime images) or copies llama.cpp binaries out of another image (also through a build stage
  that only names it), or when a Dockerfile, script (also one without a known suffix that starts with `#!`) or YAML
  file downloads llama.cpp release archives or binaries (also through a pipeline such as `curl ... | tar -x`), uses a
  prebuilt `llama-cpp-python` wheel index or installs a llama.cpp package: the framework shares recipes, not binaries. `nvidia/cuda` images no longer count as
  official base images and need a digest, and a llama.cpp image is refused as a module's base image.
- The llama.cpp server on a board listens on 127.0.0.1 only and needs a key that is generated on the board and
  never leaves it. A deployment reaches only the board whose host key the profile was made with, runs as an
  unprivileged user (root is refused), and uses sudo only for a systemd unit you allowed in the deploy settings,
  never with a password prompt. Every value read from the board (home folder, user ID, architecture) is checked
  before it reaches a command.
- **Only a person changes what an AI may do.** The tool permissions (`tool_policy.json`) and the MCP token store
  (`mcp_tokens.json`) are signed with a key derived from the audit key in the OS keyring. A file edited by hand,
  damaged, signed with another key or unsigned denies every tool, and the token store accepts no token, until a
  person sets them up again; `llm-cli doctor` reports it. Before, anyone who could write the per-user folder could
  allow every tool or add a token, and nothing noticed. Every change of the permissions is recorded in the audit log
  with the old and new decisions (actor `cli` or `gui`) before it takes effect, and is refused when the entry cannot
  be written. Saves take a revision number under a lock shared by the GUI, the CLI and MCP servers; a save based on
  an older revision is refused, so a window opened earlier can no longer undo a change made meanwhile. The newest
  saved revision of each file is kept in the keyring as well, so an older signed copy put back over it (a revoked
  standing approval or token would return) and a removed permission file deny everything too. Without a keyring the
  signature has no key, as for the audit chain; such a file is refused while the audit log is kept with a key.
- **Pending approvals and requests to stop asking are questions like any other.** They count toward the 30
  questions per hour; a "no" in the approval window stops the same call from being put to you again for 10 minutes
  and counts toward pausing the tool, as a "no" in the client's dialog does; a recent "no", a paused tool or a used-up
  hour refuses a new pending approval; and the limit of 5 open pending approvals is an audited refusal that never
  counts as a failed call. Before, a client that could not ask could queue questions without these limits.

- **No implicit Hugging Face token**: builds sent `token=None` to huggingface_hub when no token was stored, so a token
  from `HF_TOKEN` or the Hugging Face home folder was used without the person giving it to the framework (G37).
  Every Hugging Face call of a build now sends the stored token or `token=False`.
- **Image builds never read the MCP protocol stream**: `docker buildx build` ran with the server's stdin, which over
  stdio is the protocol stream (G25); it now gets `stdin=DEVNULL`.
- **Deployment packages take images only pinned by digest or built here**: the default inference image was
  `...:latest` and a failed pull by tag was swallowed, so a package could carry whatever the registry served (G17).
  An image now goes into a package only as `name@sha256:...` or when it was built on this computer; a tag is refused
  with the reason, a missing pinned image is pulled by its digest only, and a failed export stops the package.
- **Deploys never forward the MCP protocol stream**: without paramiko, `ssh` and `scp` ran with the framework's
  stdin, which over MCP stdio is the protocol stream (G25); they now get `stdin=DEVNULL`.
- **A recipe's calibration dataset is checked and used as one**: `start_recipe_build` checked the dataset's sha256
  when the call was committed, but the build opened the path again later, so a file swapped in between was used. The
  build engine now opens the file once (never through a link), copies its bytes into the build's own folder while it
  hashes them, and uses only that copy, and only when its sha256 is the one the recipe pins (an RKNN sample list keeps
  its folder, with the checked list laid over its own name).
- **Paths with spaces in errors toward an AI or the person**: a path with a space (a folder such as `My Models`, or a
  home folder whose user name holds one) was cut at the first space, so the label was wrong and a piece of the user
  name or of the host path could remain. Quoted paths, the framework's folders and the home folder (also by its real
  path when it is a link) are now replaced whole, a known folder further on in a path list (`/usr/bin:/home/...`) or
  in a quoted text with more than one path starts a path of its own, and a space continues a path when the next word
  is clearly part of it (or, inside a known folder, the longer path exists), so the error names a label or the file
  name only.

- **No rich text for untrusted text in the chat panel and the windows it touches** (design 5.2 R1-R7, SEC-M2): every
  text of the panel is a plain-text label (no rich-text widget or call exists in `gui/chat_panel/`, a source test
  checks it, also in the approvals window, the settings dialogs and the main window); hidden characters are shown as
  escapes; a value from outside the framework is quoted and stays on its line, so a file name cannot pose as a line of
  the framework's own text; nothing is clickable inline (an address opens only after a question showing it whole); an
  app's look comes from its caller id, never from its own name, and an app calling itself Ditto, the framework or the
  system is shown as calling itself so. Only rows from Ditto get Ditto's picture, and a notice about what an app did
  stands under that app's trusted name; a tool name Ditto's model made up is quoted, never shown as the framework's
  words; guillemets in AI text are shown as escapes, so only the
  framework writes a speaking name «like this». Message boxes, tooltips, desktop notifications and button labels of
  the new and touched windows go through `gui/plain.py` (plain-text boxes, escaped tooltips, notifications without
  markup characters, no keyboard mnemonic from an "&" in a name); no key on a chat entry approves or clicks anything.

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
