# Changelog

All notable changes to this project are documented in this file: only finished work, wired, tested and
committed together with its code. German version: [CHANGELOG_DE.md](CHANGELOG_DE.md).

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). One point differs on
purpose: there is no `[Unreleased]` section. Every entry starts with its time stamp and version
(`` `YYYY-MM-DD HH:MM UTC` · `vX.Y.Z` ``) and is written under the version it lands in. Planned work is listed in the
[roadmap](docs/ROADMAP.md), not here; how the framework is built and why is in the [design](docs/DESIGN.md).

## [3.2.0] - 2026-10-30

### Added
- `2026-10-30 01:02 UTC` · `v3.2.0` **Your own crawled pages belong to a topic from the start**: the Deep Ingest window asks for the topic of the
  crawl's pages (empty: the site's name, such as `docs.example.com`), and every excerpt of the crawl carries it, so
  `search_knowledge(topic=...)`, the Topic column and the knowledge exports find them under it. A crawl an AI started
  (`crawl_docs_site`) takes the site's name; it never names a topic itself.
- `2026-10-30 01:16 UTC` · `v3.2.0` **Uninstall an installed community pack**: `llm-cli zoo uninstall PACK_ID --kind target|recipe|knowledge`, the
  Community Hub's **Uninstall...** and, for an AI only as a request the person decides, the optional tool
  `remove_pack` (hidden until enabled, always asked, never granted or delegated, trash only). Everything goes
  through the deletion rules (`Core/deletion.py`): the exact list first (the pack's folder with its provenance, its
  import notes, and how many excerpts only this pack holds), "No" by default, then the trash (`Core/trash.py`) or for
  good, audited before anything goes (`pack_uninstalled`); excerpts another pack or your own crawl still holds stay
  (`CommunityManager.uninstall_plan` / `uninstall`, `RAGManager.holding`). A pack whose pages are in the knowledge base
  is refused while the knowledge base is off.

### Changed
- `2026-10-30 01:26 UTC` · `v3.2.0` **A linked knowledge folder is ignored, and said so**: a symbolic link as `community/knowledge` or in its first two
  levels was silently left out, so the packs behind it seemed missing. They are still ignored (the framework never
  follows a link out of its own folders: what is installed and checked must stay where it was checked), and now the
  pack listing (`llm-cli zoo list`, the Community Hub) and Mojo Oracle (`community.knowledge.links`) name each link,
  say why, and say to move the folder there instead (`CommunityManager.ignored_links` / `links_note`).

### Fixed
- `2026-10-30 00:48 UTC` · `v3.2.0` **A recipe bound to a target pack made on Linux verifies on Windows**: Windows keeps no execute bit, so the
  installed copy of a target pack with `0o755` scripts gave another folder digest there, and the recipe install check
  said "your copy of target ... differs", the recipe tools called the target different, and a build recorded a digest
  no Linux recipe names. The builder, the build service's identical-build check, the recipe tools and the recipe
  install check now use one helper (`community_manager.installed_digest` / `same_content`, over
  `Provenance.installed_unchanged`): an installed pack unchanged since its install counts as the pack's own content;
  an edited target is still another target.
- `2026-10-30 00:57 UTC` · `v3.2.0` **Two packs that hold the same page keep it for each other**: an excerpt's id is its page and chunk, never its
  pack, so two installed packs (or a pack and your own crawl) carrying the same page shared the stored excerpts, and
  the last import owned them. A newer version of that pack that dropped the page removed it while the other pack still
  held it, and that pack's marker kept it from being imported again; a re-crawl of a page likewise removed a pack's
  excerpts of it. Every excerpt now names its holders (`holders`, with each holder's digest, topic or target in
  `holder_info`; the excerpt id is unchanged); a newer pack version, a re-crawl or an uninstall lets go of only its
  own hold, and an excerpt goes when nobody holds it any more (`RAGManager.release_holder`). A shared excerpt is found
  under the topic or target of every holder. Excerpts stored before this name one holder, the one their fields name.
- `2026-10-30 01:03 UTC` · `v3.2.0` **A re-crawl of a site removes the pages that are gone**: pages that vanished from a crawled site kept their
  excerpts for ever. A crawl of the same start address again replaces its pages, and when it read the whole site
  (it succeeded, no page limit or request budget ended it, nothing failed, was reserved or stopped) the excerpts of the
  earlier pages it no longer reached are removed too, only where this crawl was their last holder; the result names
  them (`removed`). A partial or failed crawl removes nothing.
- `2026-10-30 01:26 UTC` · `v3.2.0` **A topic folder taken by an older pack names that pack and the way out**: when a knowledge pack's topic or target
  is the folder of a pack installed before knowledge was sorted into topic folders, the refusal said only "choose
  another topic name", which the person cannot do for a pack from the index. It now names the older pack (kind, id,
  version) and the way out: uninstall that pack, or install it again (it then moves into its own topic folder), and
  then install this one; the next step points to **Uninstall...** (`llm-cli zoo uninstall`). Sorting a loose file,
  where the person chooses the topic, also offers another topic name.
- `2026-10-30 01:26 UTC` · `v3.2.0` **An edited target or knowledge pack says why it is not imported and how to go on**: a pack whose files changed
  since its install is still not imported (the check against the signed digest is the security gate), but the
  waiting reason of the knowledge import and Mojo Oracle's finding now say why and what to do: to undo the edits,
  uninstall it and install it again; to keep them, copy the target folder under a name of your own and export that
  as a new pack (a knowledge pack: export your version as a new knowledge pack). Mojo's findings for a revoked or
  changed pack point to **Uninstall...** instead of telling the person to delete a folder or type a command.
- `2026-10-30 01:30 UTC` · `v3.2.0` **Tools > Knowledge Sources... shows Topic or target third, as the CLI does**: the window listed the topic or target
  as its last column, while `llm-cli knowledge sources` shows it third. Both now take their order from one list
  (`knowledge_sources.COLUMNS`): Source, Kind, Topic or target, Hidden text left out, Licence, Copyright notice, In a
  pack, Why.

## [3.1.0] - 2026-10-29

### Added
- `2026-10-24 00:06 UTC` · `v3.1.0` **Mojo closes the circle: the crystal ball after Mojo Miracle** (DECISIONS round 27 and its addendum): when Mojo
  Miracle ends after Mojo Oracle at a terminal (the answer `y` to "Run Mojo Miracle now?", or `llmcf mojo --miracle`
  while an earlier Oracle run at the terminal is still open), `llmcf mojo` shows a large ASCII crystal ball, in colour
  where the console can show colour (rich's console decides: no colour without a terminal, with `NO_COLOR` or on a
  console that cannot show it), and under it "[Mojo] Miracle managed. The oracle is pleased." when at least one problem
  was repaired, none is left and the check afterwards found nothing new, or "[Mojo] 404 - miracle not found" when there
  was nothing to repair (no problem, or every one already gone). Any other outcome (a problem left or declined, a new
  one) shows no logo. The open Oracle run is one small private file next to Mojo Miracle's lock in the per-user state
  folder of the `LLM_FRAMEWORK_HOME` in use (no path, no account name); every Miracle run at a terminal removes it when
  it ends, whatever its outcome. Never with `--json`, without a terminal, through `run_mojo_oracle` and
  `run_mojo_miracle` or in the GUI: nothing AI-facing changes. The drawing (pure ASCII, 60 columns) and its colour mask
  live in `orchestrator/Core/mojo/banner.py`, one place to swap them; a test checks that they have the same shape.
- `2026-10-23 17:39 UTC` · `v3.1.0` **Hardware probe 3.1.0: L2 cache, Vulkan instance version, OpenCL platforms, the Hailo keys and the vendor
  runtimes** (consolidation chain probe-keys; decisions round 23 item 11, O-PREP and O-HAILO, HAILO_PLAN.md section 2):
  `scripts/hardware_probe.py` (and its package copy), `hardware_probe.sh` and `hardware_probe.ps1` write, validated by
  `configs/hardware_probe_schema.yml`, `CPU_L2_CACHE_KB` (the largest level-2 cache), `VULKAN_INSTANCE_VERSION`
  (vulkaninfo, else the loader's file name or `vulkan-1.dll`) and `OPENCL_PLATFORMS` (`<name>:<OpenCL version>` of
  clinfo); the nine Hailo keys `HAILO_FAMILY`, `HAILO_ARCH`, `HAILO_DEVICES`, `HAILO_INTERFACE`, `HAILO_DRIVER`,
  `HAILO_DRIVER_VERSION`, `HAILO_FW_VERSION`, `HAILORT_VERSION` and `HAILO_PYHAILORT_VERSION` (files first:
  `/sys/module`, the sysfs classes, nodes, PCI and USB ids; then `hailortcli -v`, `scan` and `fw-control identify`,
  never `--extended`, only its architecture and firmware lines read, device ids never written) with the new warning
  code `version_mismatch`; and for the targets prepared for others (O-PREP) `JETSON_L4T_VERSION`,
  `JETSON_JETPACK_VERSION`, `CUDA_TOOLKIT_VERSION`, `TENSORRT_VERSION`, `OPENVINO_VERSION`, `INTEL_GPU_DEVICE`,
  `INTEL_GPU_DRIVER`, `INTEL_NPU_DEVICE` and `ROCM_GFX_TARGETS`, each read only when it is there; nothing is installed
  or run with sudo. The quick probe of free RAM and the GPUs at every start of the local runtime and for a build's
  estimate runs no new tool (clinfo and the vendor runtime files are read by the full probe only), and NUL bytes and
  CRLF line ends give the same values in all three probes. `HardwareProfile.hailo()` returns the Hailo facts as one typed value (`HailoFacts`); `llm-cli
  scan` (also `--device` and `--import`) prints them in a "Hailo:" line, also next to a Rockchip NPU; target matching,
  the board recommendation, the documentation links and deploy read the same value once the Hailo targets of
  HAILO_PLAN section 3 exist. The `.py` and the `.sh` run on the fixture
  machines of HAILO_PLAN 6.1 (identify and scan outputs of Hailo's documentation and of the current format strings,
  the version mismatch on stderr, sysroot trees) with a fake `hailortcli` and agree; the Module Developer Guide
  (section 11.6) says how a stage module and the framework use the keys.
- `2026-10-23 08:49 UTC` · `v3.1.0` **`llm-cli models take-over`: the Take over of old Hugging Face downloads at a terminal** (consolidation chain
  hf-online): headless installations had no way to take over what earlier versions of the GUI's Hugging Face browser
  saved in huggingface_hub's cache inside the models folder. The command runs the same core calls as **Take over old
  downloads...** in **Tools > Hugging Face Model Browser...** (`ModelManager.old_downloads`, `take_over`,
  `old_copies_plan`, `delete_old_copies`): the full list of the files still to take over, the yes question with "No"
  as the default, the copy (each file checked, under the one disk rule; Ctrl+C stops between two chunks), each file
  that stays with its reason, then the full list of old copies that can go, trash or for good, and the deletion only
  after your yes ("No" is the default), audited first. Without a terminal nothing is copied or deleted. A copy that
  fails (a disk that cannot hold the copies, a file in use by another `llm-cli` or the GUI) ends with one plain line,
  "Nothing was taken over: ..." with the reason, as the window shows it, never with a traceback. The GUI parity test
  names its mouse path (`orchestrator/gui/menus/existing.py`: `btn_hf_take_over` in the browser). Tests:
  `tests/test_hf_takeover.py` `TestTheCommandLinesTakeOver`.
- `2026-10-22 14:37 UTC` · `v3.1.0` **A trusted board's address and host key are editable by you** (DECISIONS round 10 Q5): `llm-cli deploy devices
  edit DEVICE [--host] [--port] [--key "KEY LINE" | --read-key]` and **Edit address or key...** in **Boards > Boards
  and Devices...** change the board's host name or IP address, its SSH port or its host key in the framework's
  `known_hosts`, for example after its address changed or it was reinstalled. Nobody types a key: it is pasted (the
  line of `/etc/ssh/ssh_host_ed25519_key.pub` on the board, a `known_hosts` line or a line of `ssh-keyscan`) or read
  from the board at its new address; a line with `authorized_keys` options, commands or a second key is refused.
  You see the address, the host key and the device ID now and after the change
  side by side (in the window) and answer with "No" as the default; the change is written to the audit log before
  anything changes and refused when `known_hosts` changed meanwhile. A new key gives the board a new device ID: its
  record moves there with its name and user, and whether the framework's key logs in is tested once; the key of
  another trusted board is refused, naming that board, so its record is never overwritten. The board's data moves
  with its new key (DECISIONS round 22, P6; `orchestrator/runtime/device/board_move.py`): its stored probe, deploy
  settings, deployed runtime (the record of the last deployment with its checks and benchmark, and its runtime
  packages), deploy record and its benchmark results with the builds ("tested on"). Every part is copied to the new
  device ID and checked (read back, the runtime packages as hard links or as copies with the same sha256) before
  `known_hosts` changes, and the old ID's copies go only after the change; when a copy fails or the change is refused
  (no audit row, `known_hosts` changed meanwhile) or ends any other way before `known_hosts` changes (its lock held by
  another process, a Ctrl+C), the copies go again and nothing has changed. Once `known_hosts` holds the new key, the
  old copies go also when a Ctrl+C ends the login test after it, so the board's data is never left twice. What the new
  ID already holds with other content is never overwritten (it stays under the old ID; the probe and its label and
  address move or stay together), an old copy that cannot be removed stays too, and the CLI and the window name both,
  also when the board's record could not be written; both also say to probe a board that was set up anew again (its
  moved probe may be old). The work runs
  in the background; the texts are English and German. No tool, the MCP server, Ditto or the device runtime steps can
  reach it (`orchestrator/remote/pairing.py` `replace_key`, an import-graph test).
- `2026-10-17 12:38 UTC` · `v3.1.0` **Next steps instead of commands, allow-to-run proposals and translated, regrouped menus** (wave 6, chain a0;
  DECISIONS round 10 V1, V2, Q2, Q7): the Qt-free catalogue `orchestrator/utils/next_steps/` holds every next step
  the wave-6 audit names (104 steps in nine area modules found by themselves, English and German texts), and one
  renderer turns a step into the command for the command line (`Next: ... llmcf ...`), the menu path in the person's
  language for the window (`StepsRow` with Open, Copy for a board's, an administrator's or another program's command,
  Open page) and the tool or the place for an AI (`ToolContext.channel`: an AI working for the window is never told a
  command). `proposal()` builds an allow-to-run command from a catalogue step and checked values (the exact command,
  what it does and why, localized), `run_proposal()` runs exactly that command with the framework's own Python and
  streams its lines (deletions open the deletion list, secrets go through stdin, other places are only shown, each
  decision can be recorded; every value of every proposal is checked by its kind, a proposal that differs from the
  catalogue's in any word the person read is refused, only commands that finish without a terminal and do no more
  than their question says are runnable, and an AI never reads a board's login or a path from a step); `error_report()` gives the error box a localized summary, the next step and the English
  technical detail. Carriers: `ToolError(steps=...)` rendered for the caller's channel at the registry boundary,
  `BlockerSpec.step`, `WithSteps` on the core exceptions, `Line`, `Finding.steps`, `_say_error` in the CLI. The menus:
  the AI administration in the Ditto menu ("Set up Ditto..." first), runtime, models and evaluation under Ditto >
  Advanced, Tools with Knowledge and Maintenance; translated titles with mnemonics, every entry named and labelled
  where it is created, one layout table, `retranslate_menus()`. The hand-written menu paths of the texts (30 keys in
  English and German) became `{path:<objectName>}`, filled from the live labels; `tests/test_no_command_texts.py`
  guards the texts, their "Sie" forms included, and counts the rest against a baseline that only goes down. The
  catalogue's German texts follow the chosen form of address: a module's `FORMAL` table holds the "Sie" form of each
  text that differs.
- `2026-10-17 06:42 UTC` · `v3.1.0` **What Ditto and AI apps do becomes entries of the chat feed, in every process** (wave 6, DECISIONS round 9 U1,
  consequence b): `orchestrator/Core/activity.py` writes one signed entry per event, from the process that did the work
  (the GUI, `llm-cli`, every stdio MCP server and the HTTP child): a call that changed something (the new registry event
  `call_done`, entry kind `ai_action`), the end of every job kind (a finish listener of the job service), the end of a
  build (the orchestrator; who cancelled it, the saved folder as a label), uses of standing approvals, pending approvals
  and their end, and refused work (`blocked`). The person's own calls make no entries; the sender is the trusted caller
  id, an app's own name only its claim; entries hold closed values only and name the window that shows the result
  (`orchestrator/tools/tool_windows.py`: the closed window list, a window for every tool that changes something, checked
  by a registry-wide test, for every job kind, and what opens each window) plus the step ids of the next-step catalogue
  (never a text key that looks like one); `refs.asked_in` marks Ditto's turns in the GUI's own chat. It fails open, and
  the same refusal is written once in 30 seconds. `ActivityProducer.add_listener` hands the GUI its own process's
  entries at once, and `ConfigManager.reload_user_config` takes settings another process saved, except values this
  process holds itself (reported as conflicts).
- `2026-10-17 11:20 UTC` · `v3.1.0` **Technical terms explained on mouse-over, a plain error box, and "du" or "Sie" in German** (wave 6, DECISIONS
  round 10, V6, Q1, Q7): a glossary of 74 technical terms (`orchestrator/utils/l10n_glossary.py`, for example GGUF,
  quantization and its types, importance matrix, KV cache, RKLLM, RKNN, Qdrant, MCP, SSH host key, fingerprint,
  digest, keyring), each with a short explanation in English and German and a link to the page that explains it best
  (Wikipedia, the term's own documentation such as qdrant.tech or the llama.cpp docs, or this manual). Resting the
  mouse on a marked label shows a hover card with the explanation; it stays while the mouse is on it, and its link
  opens in the web browser only when you click it. F1 on the field opens the same card for the keyboard. The Builder's
  **Format** and **Quantization** labels are the first; the other windows follow (a menu path in a text is not
  searched for terms). A new error box says what happened in your language, why, and one next step (a sentence, or
  the place with its menu path and an **Open** button), with the English **Technical details** folded away (secrets
  and your home folder removed) to copy into a bug report or ask Ditto about. German texts say "du";
  **File > Settings...** now has a **General** tab where you choose "Sie" instead (setting `form_of_address`), used
  at once, also for the next steps. Sizes in the deletion
  dialog follow your language ("1,5 GB" in German).
- `2026-10-22 19:07 UTC` · `v3.1.0` **Maintainer actions for the community repository** (decision 2026-10-22: the maintainer's work as a few clicks
  instead of a manual): **Tools > Open Community Hub > Maintainer** and `llm-cli zoo maintainer key|review|revoke`,
  one implementation in `orchestrator/Core/community_maintainer.py` (`scripts/zoo_sign_index.py` is now a thin wrapper
  over it). **Create signing key** (once) puts the index signing key into the OS keyring (a name that exists is
  refused) and shows its public entry for `configs/community_keys.yml`, saved as a text file you choose. **Review and
  publish** lists the open pull requests of `community_repo` that add packs (and packs merged under `contributions/`
  but not published yet), fetches a pack from the pull request's head, checks it with `zoo verify` and shows every
  file (GUI: a file list with a read-only text view; CLI: printed, or written to a folder you name with
  `--review-dir`; every member of the pack file), then asks "Publish?" ("No" is the default); a pull request that
  changes anything besides new packs under `contributions/<kind>/`, or goes into another branch than `main`, is not
  published, and the review names that change. Yes merges the contribution (only when its required
  check `validate` passed and its head is the reviewed commit), updates a local clone in the per-user folder, moves
  the pack to `packs/<kind>/`, adds its index entry with the raw address, raises the index version and time, signs
  the index with the keyring key, commits on a new branch, pushes it, opens a pull request, waits for the required
  check and merges it; nothing is ever pushed to `main`. **Revoke a published pack** adds a revocation (one file, one
  version or every version, with the reason) the same way. Every step is reported and written to the audit log,
  Cancel (Ctrl+C at a terminal) stops before the next step up to the last merge (after the push it closes the
  publication's own pull request and deletes its branch, so nothing half-published stays), and a refusal says what
  happened so far and the next step (no `gh`, not logged in, offline, a check that has not passed, a rejected pack, a
  clone with changes of its own). The index on `main` must verify with your key or a trusted key before anything is signed on
  top of it. gh and git use your own login; the framework never reads, stores or shows a token. The person only: no
  MCP tool, Ditto or other AI path reaches it (guard test). The check workflow template
  `scripts/community_repo/validate-contribution.yml` now runs on every pull request (no paths filter; a pull request
  without a pack passes), as the community repository's copy does, because its protected `main` requires it. Its
  messages name a framework step as its command at a terminal and as its place in the window (the next-step
  catalogue's `hub.refresh`, `maint.review` and `audit.verify`, and the settings by their menu path), never a command
  in the window; the Community Hub's **Refresh** button is now translated.
- `2026-10-17 02:35 UTC` · `v3.1.0` **`tests/test_cli_gui_parity.py`: every person-facing CLI command and option has a mouse path** (W1', round 9
  U2): the test walks the whole command tree of `llm-cli` (294 paths: commands, subcommands and options) and reads
  `CLI_PATHS`, `MACHINE_ONLY` and the window and tab tables of every module in `orchestrator/gui/menus/`. It fails
  when a command or option has neither a mouse path nor a machine-only reason, when a table names a command that does
  not exist or that two modules claim, and, in an offscreen main window, when a named object is missing or a menu
  action cannot be reached from the menu bar (every window is opened from its menu entry; window and tab objects are
  checked inside the window or tab their entry opens). Today 238 paths have a mouse path, 37 are machine-only with
  their reason, none is missing. The paths that existed before wave 5 without an area module (File > Trash...,
  `ditto setup`, `--version` in Help > About) are named in `orchestrator/gui/menus/existing.py`; the menus of every
  area are found by their object names through one helper (`orchestrator/gui/menus/place.py`), so a translated menu
  title never adds a second menu. CONTRIBUTING names the rule.
- `2026-10-16 20:14 UTC` · `v3.1.0` **Tools > Mojo Health Check... in the GUI** (DECISIONS W1': every action of the command line also by mouse): the
  Mojo window runs Mojo Oracle when it opens, off the GUI thread, and shows every finding as `llmcf mojo` prints it
  (the severity as text with its mark and icon, never colour alone; where with the full path, why it matters, the
  next step, what Mojo Miracle can do with the steps of its plan; the summary sentence and an overall line with the
  exit code). **Run Mojo Miracle...** asks "Run Mojo Miracle now?" ("No" is the default) and runs Mojo Miracle with
  the GUI as your channel: the plans shown bind the repairs, the framework's build images open in the deletion dialog
  with the full list and nothing ticked, and the window shows what was repaired, what was not and why. No board is
  contacted by itself: a board a recorded fact points to gets **Check <board> now**, and **Check a board...** checks
  any trusted board you pick (by label and address, and also by device ID where two boards read the same; one
  connection to its SSH port, no login). The command and the window share the same
  core functions (`Core/mojo/boards.py`, the person's view in `Core/mojo/views.py`, `docker_objects.offer_image_list`),
  so the output of `llmcf mojo` stays as it was.
- `2026-10-16 20:14 UTC` · `v3.1.0` **Check this board in the Deployment tab** (group "Benchmark on a board", DECISIONS W1'): checks the chosen board
  as `llmcf mojo --oracle --board` does, in the background (one connection to its SSH port, no login), and shows its
  finding with the next step under the row; a problem is also said in a message.
- `2026-10-16 20:23 UTC` · `v3.1.0` **Boards > Boards and Devices...: every `deploy devices` and `device` command by mouse** (W1'): a new Boards
  menu opens one window for every board you probed or trusted, with the board's account, the full host-key
  fingerprint and its state in `known_hosts`, since when it is trusted, the last deploy, its system, deploy settings
  and deployed runtime. Its buttons call the same core functions as the CLI (new `runtime/device/person_steps.py`
  and `steps.py`; the CLI calls them too, its output unchanged), with the same checks, refusals, questions ("No" is
  the default) and audit records: Add a trusted board..., Remove trust... (until now only the CLI could withdraw a
  board's permission for AI deploys), Settings... (with the systemd sudo question), Runtime plan, Status, Build
  runtime... (jobs, mirror, the source question, Cancel), Deploy runtime... (the password asked each time, never
  stored; an untrusted benchmark model only after an explicit warning), Verify... (also without a model),
  Remove older runtime packages... and Remove older builds on the board... (the deletion dialog with the full list)
  and Check this board (Mojo). Every board contact and build runs in the background. The AI's runtime tools build
  and install with the same steps. Damaged deploy settings ("damaged; set them again") can now be set again, in the
  window and with `device settings`, after a question that names the file with its full path and size.
- `2026-10-16 21:52 UTC` · `v3.1.0` **Tools > Tool Permissions (Ditto / MCP)...: every `tools list` and `tools permissions` option by mouse** (W1'):
  a column "Effective now" shows each tool's decision with its reason (as `tools list` prints them) and a note
  names the callers that have permissions of their own; "Whose permissions" shows and edits the shared permissions
  or the own section of Ditto or the local MCP clients (`--caller`); "Reset to recommended defaults..." (`--reset`),
  "Use the shared permissions again..." (`--reset --caller`) and "Back to read only..." for a token (`--reset
  --token`: a new token again, not one with hand-set Allow/Deny) list what changes and ask first ("No" is the
  default; a change saved elsewhere meanwhile is refused). A new token can start as a copy of another token's own
  permissions (`mcp token add --policy-from TOKEN`). The CLI and the GUI share the core functions
  (`policy.with_section`, `reset_policy`, `reset_changes`, `mcp_tokens.add_token`, `copied_policy`,
  `set_token_policy`, `reset_token_policy`, `revoke_token`); the CLI's output is unchanged.
- `2026-10-16 21:52 UTC` · `v3.1.0` **Security > Audit Log...: every `tools audit` option by mouse** (W1'): the audit log's rows as the CLI shows
  them (shared `tools/audit_view.py`), page by page with "Entries per page", "Newer" and "Older"; "Details"
  (`--details`); "Check integrity" (`--verify`) with the result in plain words; "Archive..." (`--archive`) after a
  question, nothing deleted; and the tab "Summary per caller" (`--summary`). The check, the archive and the summary
  run in the background. `AuditLog.entries` takes an offset and `AuditLog.count` gives the size, for paging.
- `2026-10-16 21:52 UTC` · `v3.1.0` **Security > AI App Connections (MCP)...: every `mcp config`, `mcp serve --transport http` and `mcp token` action
  by mouse** (W1'): the entries for Claude Code, Claude Desktop (with the path of its configuration file) and VS
  Code, each with a Copy button; "Write to Claude Desktop..." shows the file, whether it exists, the change, the
  entries that are kept and the backup, asks first and writes exactly that (`mcp_server.plan_claude_desktop_config`,
  a file changed meanwhile is refused); the local HTTP server (address, port, container binding) as a child process
  of the GUI with the CLI's own entry and refusals, its address, state and output shown, stopped on request or when
  the framework closes after a question ("No" keeps it open) by closing its input, so it ends as on Ctrl+C on every
  system, also on Windows without a console (`mcp_server.stop_when_input_ends`; Ctrl+Break or SIGTERM follows only
  after 5 seconds, an immediate end after 15); and the token list (Add, also as a copy of another
  token's permissions, Rotate, Revoke, Back to read only, Permissions). A token is shown once, right after it is
  made or rotated.
- `2026-10-16 21:52 UTC` · `v3.1.0` **Tools > Deep Ingest Notice... and the kind of an AI folder** (W1'): the Deep Ingest notice has its own entry and
  can be read at any time (`tools crawl-notice`; acknowledged on the person's yes, "No" is the default, with the
  CLI's function `crawl_notice.acknowledge_and_audit`); the module wizard shows it before the knowledge base check.
  Tools > Folders the AI May Use... > Add folder... asks what the folder holds (model files, dataset files or any:
  `tools folders add --kind`).
- `2026-10-16 21:18 UTC` · `v3.1.0` **Security > Sign-in Overview...: every `ditto login`, `ditto logout` and `ditto auth status|methods` path by
  mouse** (W1'): one window shows every provider's sign-in at once (method, source, account or the last four
  characters of a key, status, Ditto's provider marked) and how the credential vault is protected with its file;
  **Check all** runs the free check of every provider with a credential (`--check`) in the background; the tab
  **All sign-in methods** lists every provider's methods with their environment variables and notes. Selecting a
  provider shows its sign-in panel with its own **Server address** field: an address typed there (or given by the AI
  settings) is remembered for that provider only and no longer only through the AI settings, which also made the
  provider Ditto's (`login --base-url`). The key card names the method's environment variables and note and offers
  **Store the key from <VARIABLE> in the vault** while one is set (`login --from-env`, checked first unless you
  untick it; new `SignIn.env_variable()` / `SignIn.env_key()`, which the CLI calls too); a server without sign-in
  (Ollama, `custom` without a key) has **Forget** (`logout`: the remembered address and method). It asks before
  signing out or forgetting ("No" is the default). The framework never reads, stores or forwards Claude subscription
  credentials and never starts the claude command.
- `2026-10-16 21:18 UTC` · `v3.1.0` **Security > Key Storage...: `ditto auth storage` and `ditto auth rotate-key` by mouse** (W1'): how the vault is
  protected and its file, also while it works (until now the GUI showed it only when it failed); **Protect it another
  way** switches between the OS keyring (new as a GUI choice, so the way back to the keyring no longer needs the
  CLI), a credential helper with its command, a passphrase and the unencrypted key file (after the acknowledgement
  and the CLI's question, "No" is the default); **Rotate key...** asks first ("No" is the default). The vault is
  re-encrypted in the background with the same core functions as the CLI.
- `2026-10-16 21:18 UTC` · `v3.1.0` **Security > Secrets...: `secrets list` and `secrets set` by mouse** (W1'): the names the framework reads
  (`hf_token`, `qdrant_api_key`, the community pack keys) and every stored name with "stored" or "not stored" and
  what it is for, never a value; **Set a secret** takes a name from the list or any other name and the value twice
  in masked fields. The CLI and the window call the same new `Core/secret_entries.py` (`store_secret`,
  `secret_entries`; the CLI's output is unchanged): in the ephemeral storage both say that nothing was saved, and so
  does Security > Set OpenAI API Key, which said "stored" there.
- `2026-10-16 23:00 UTC` · `v3.1.0` **Deployment tab: every `deploy package` and `deploy run` option by mouse** (W1', GUI parity): **Create package
  only…** makes the package of the selected build for the chosen profile without contacting any board, with Docker
  images for a board without network if you tick Docker, and **Other build or model file…** adds any build folder or
  model file to the build list (the CLI takes any path, too); the folder dialog opens at
  the output folder as for every golden artifact, and the message names the package's full path or the reason and
  the next step (`llm-cli deploy package`, the same core step). **Deploy existing package…** uploads a ZIP made
  earlier (`deploy run PACKAGE_PATH`). The new **Port**, **Name** and **Wait for the board** fields are `--port`,
  `--label` and `--wait` for both deploy buttons. The board's host key is now read in the background and its
  fingerprint shown with "No" as the default; a failed package in **Deploy to Target** says why; the success message
  names the full package path, how often the deploy waited for the board, and the trusted board's name.
- `2026-10-16 23:00 UTC` · `v3.1.0` **Builder tab > Scan / Import...: every `llm-cli scan` option by mouse** (W1', GUI parity): **Scan Local Host** now
  runs the CLI's own step, the Python probe, and stores the result as this computer's probe (probe id `local`, the one
  AI clients and Ditto name), with its notes and the "run the probe again" warning in the log; **Scan Local Host with
  the Fallback Script** (`--script`), **Scan Local Host and Save the Profile As...** (`--output`; a `.json` file
  gets the JSON view, `--json`) and both together are new. **Remote Scan via SSH...** gets **Port** and **Name** fields and, like `scan
  --device`, writes the board's trusted, paired record (audited), so the board appears in the benchmark list and AI
  deploys can name it; the CLI and the GUI share one core step (`scan_steps.scan_device`). **Import from File** asks
  for the board's name (`--label`).
- `2026-10-16 20:29 UTC` · `v3.1.0` **Build > Targets...** (GUI parity, every CLI action by mouse): every target with its ID (the value for `--target`),
  name, architecture, boards, formats and support level, or "no capabilities declared" (or that its capabilities block
  is invalid), from the query of `llm-cli targets list`. **Check target** (there and next to the Builder's target list,
  beside the new **Targets...** button) checks the selected target as `llm-cli targets validate` does, through one core
  function (`TargetManager.validate`, which the CLI now calls too) with its `target.yml` read again, and names each
  schema error by field. The same check runs after the module wizard created a target and after a Community Hub target
  install, and its result is shown.
- `2026-10-16 20:29 UTC` · `v3.1.0` **Build > Build History...**: every build of every program (GUI, CLI, MCP server), all of them as `llm-cli build
  status --all`, failed, cancelled, interrupted and queued ones included; **Show details** (or a double click) shows
  one build as `build status REQUEST_ID`: its request with errors and notes, every attempt with its output folder
  (full path) and error, and the AI clients' fix proposals, marked AI-generated. The CLI and the window read one query
  (`orchestrator/Core/build_history.py`). The status bar now names this window instead of a command for builds of
  other programs.
- `2026-10-16 20:29 UTC` · `v3.1.0` **File > Settings...**: the effective settings with where each value comes from, secrets redacted (as `llm-cli config
  show`), and the source repository overrides with the variable each becomes, to add and remove (as `llm-cli config
  repos`; removing asks first, "No" is the default). Both use one core module (`orchestrator/Core/settings_view.py`)
  that the CLI now calls too; a save that fails leaves the setting unchanged in the running program as well.
- `2026-10-16 20:29 UTC` · `v3.1.0` **The Builder offers what the CLI offers**: a target without capabilities gets every format of `--format` (not only
  GGUF, RKLLM and RKNN), with the note that its boards, formats and quantizations are not checked (for a target whose
  capabilities block is invalid the note says so, and **Check target** names each error); the optimization level
  `FAST`; a **Priority** list (`--priority`, default normal; a failed critical build stops the pipeline).
- `2026-10-16 20:29 UTC` · `v3.1.0` **Module wizard: description, and provider and model for one analysis**: the hardware page has a **Description** field
  (`llm-cli module create` asks for one; empty gives "Target for <name>"). Only you fill it: for a module an AI helped
  with, your description leads and the framework's note that the module is AI-generated and untested follows. The first
  page offers provider and model **for this analysis only** (`module generate-ai --provider --model`): Ditto runs on
  them through `DittoManager.for_run`, which the CLI's `generate-ai` now uses too, and the saved AI settings stay
  unchanged. `for_run` uses the same provider check and settings overlay as the chat panel's choice for one
  question and Ditto > Evaluation... (`orchestrator/Core/ai_override.py`): one code path for every such choice.
- `2026-10-16 20:19 UTC` · `v3.1.0` **Ditto > Local Runtime...: every `llm-cli ditto runtime` command by mouse** (W1'): one window shows what `ditto
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
- `2026-10-16 21:23 UTC` · `v3.1.0` **Tools > Knowledge Snapshots... says beforehand whether the next snapshot fits, and shows the storage in every
  state** (W1'): the disk line now says, in the words of `llm-cli knowledge snapshots`, when the next snapshot (about
  the size of the newest) would not fit, so Deep Ingest, a restore and a pack import would be refused, or would leave
  little space. The storage line is shown in every state, as `llm-cli knowledge storage` shows it (Qdrant Edge with its
  folder, the old storage with its reason, a Qdrant server, Qdrant Edge that cannot be loaded), and **Convert now** is
  offered also before the first attempt, on the condition of `llm-cli knowledge storage convert`. The CLI and the
  window call the same new core functions (`knowledge_snapshots.snapshot_disk`, `migration.storage_status`,
  `storage_lines` and `convert_refusal`); the CLI's output is unchanged apart from a set Qdrant server (see Fixed).
- `2026-10-16 21:23 UTC` · `v3.1.0` **Tools > Knowledge Sources...: every source of the knowledge base by mouse** (W1'): a new window lists every
  source with its kind, the licence and copyright notice it shows, and what a knowledge pack would carry of it (full
  text, link and short facts, not exported) and why, with a kind filter (web pages, knowledge pack pages, framework
  documents, other). It reads the list in the background through the new `knowledge_sources` core module, which
  `llm-cli knowledge sources` uses too, so both show the same words. The menu wiring lives in
  `orchestrator/gui/menus/knowledge.py`, whose `CLI_PATHS` names the mouse path of every `knowledge` and `zoo`
  command.
- `2026-10-16 21:23 UTC` · `v3.1.0` **Community Hub: every `llm-cli zoo` action by mouse** (W1'): **Details...** (or a double click) shows what `zoo
  show` shows (licence, trust, installed folder, download address, size, sha256, an installed recipe's `recipe.yml`);
  **Import file...** takes a passphrase (never stored or shown) and the key files of the only senders to accept, as
  `zoo install --file --passphrase / --from` does; **Check installed packs** and **Check a pack file...** run `zoo
  verify` with and without a file; **Export...** exports any target folder (also an existing or installed one), a
  finished build or the knowledge base with its pack id, plain or encrypted to the keys in key files or with a
  passphrase, into a folder you choose; **My public keys...** shows `zoo keys` with Copy and Save.... Each calls the
  core function of the CLI (new: `CommunityManager.details`, `exportable_targets`, `read_public_key_files`,
  `needs_passphrase`) in the background; questions keep "No" as the default, and an encrypted pack is never submitted.
- `2026-10-16 21:39 UTC` · `v3.1.0` **Ditto > Local Models...: `llm-cli ditto models list` and `download` by mouse** (W1'): every tier of Ditto's
  pinned catalogue with file, size, licence, whether it is downloaded (and verified) and its note (recommended for
  this computer, chosen, on request, not pinned), the reason for the recommendation and the full models folder;
  **Download the recommended model...** or **Download selected...** after the CLI's question (repo, file, licence,
  size, folder; "No" by default), in the background with progress and **Cancel** (the part is kept for a resume). The
  recommended download takes the recommendation the window shows (read with the list in the background), so no
  hardware probe runs on the GUI thread. The catalogue rows and the model a download is for are core functions the CLI
  calls too (its output is unchanged). The runtime install question of the AI settings names this window instead of
  the CLI command. A download is a task of Ditto's local runtime: it waits while a build, a server start or another
  download runs in Ditto > Local Runtime... or the AI settings, and they wait for it.
- `2026-10-16 21:39 UTC` · `v3.1.0` **Ditto > Evaluation...: `llm-cli ditto eval` by mouse** (W1'): kinds and retrieval variants (fused, rerank),
  single cases, another cases file, runs per case, several local tiers, or provider, model and reasoning effort for
  this run only (the saved AI settings stay unchanged); a question before the run says what is called and whether it
  costs money, also when the fallback chain of the AI settings names a cloud provider, and that retrieval downloads
  its models on first use ("No" by default); a tier that is not downloaded yet is refused with a pointer to Ditto >
  Local Models... (starting it would stop your server first); a run of local tiers is a task of Ditto's local
  runtime and waits while another one runs; the run goes in the background with progress and
  **Cancel** (before the next attempt; Cancel while indexing loads no reranker), the window shows the CLI's tables,
  and **Save report...** writes its JSON report. The run itself (case selection, the tier loop with the server
  restore, the retrieval runs, the tables and the report) moved into `orchestrator/evals/eval_run.py`, which the CLI
  calls too; its output and exit codes are unchanged, except that a server of yours replaced by a tier is now also
  reported when the run stops early (a later tier that does not start in the CLI, Cancel in the window).
- `2026-10-16 21:39 UTC` · `v3.1.0` **Chat panel: options for one question** (W1', `llm-cli ditto ask --no-tools/--provider/--model` by mouse):
  **Options for this question** above the message field give **Answer without tools**, a provider and a model for the
  next question only. The saved AI settings stay unchanged (the question goes to a Ditto with these values over the
  settings, `orchestrator/Core/ai_override.py`, which also checks the provider like the CLI), the panel says what the
  question used, and the options are reset after it is sent; feed, questions and approvals work as before.
- `2026-10-16 15:23 UTC` · `v3.1.0` **`llm-cli knowledge storage` and the old knowledge base storage in the deletion list** (DECISIONS RG5): `llm-cli
  knowledge storage` shows where the knowledge base is stored, when an older one was converted (how many excerpts,
  what was checked) or why its conversion failed, and the old folder with its full path and size; it reads the record
  only, and its first line names the storage really in use. `convert`, and **Tools > Knowledge Snapshots... > Try the
  conversion again** in the GUI (in the background), try the conversion again now. `delete-old`, and **Tools >
  Knowledge Snapshots... > Delete the old storage...** in the GUI, list the old folder after its conversion and delete
  it only after your yes (trash or for good; "No" is the default; audited as `old_store_deleted`); nothing deletes it
  by itself, and an AI never can. Mojo Oracle reports a failed conversion (with the reason and both ways to try again)
  and an old folder that is still kept.
- `2026-10-16 08:50 UTC` · `v3.1.0` **A deploy tells you about an old Qdrant sidecar on the board**: packages with Docker images from earlier versions
  started the container `qdrant-sidecar`, which nothing uses any more. After a successful deploy (`llm-cli deploy
  run`, the Deployment window, the AI tool `deploy_to_device`) the framework looks with `docker ps` (for at most 20
  seconds) whether it is still on the board and, if so, says it is running or stopped and how to remove it there.
  Mojo Oracle shows a line for that board from the last deploy. The framework never stops or removes the container.
- `2026-10-16 01:03 UTC` · `v3.1.0` **Where finished models go: the output folder and a save dialog** (eighth round, answer 3): finished builds
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
- `2026-10-16 01:03 UTC` · `v3.1.0` **Identical builds are named before building** (eighth round, N11): `check_build_request` (`earlier_builds`, with a
  note) and the GUI's builder form report a finished build with 100 % the same chosen options: the model and its
  exact commit (a local model by every weights file's name, size and time), the target and the sha256 of its folder,
  board, format, quantization, importance matrix and calibration data, optimization, context length, GPU use and
  task. Other weights or another quantization is never identical. You use it or build again (**Use the existing
  build** / **Build again** / **Cancel**); `start_build`'s question says when it builds an identical build again.
  Nothing is skipped by itself. Every build records its options in `build_info.json` (`build_options` with their
  sha256).
- `2026-10-16 01:03 UTC` · `v3.1.0` **Disk check before every knowledge snapshot, and a snapshot before every knowledge pack import** (eighth round,
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
- `2026-10-15 21:27 UTC` · `v3.1.0` **Deleting with the full list, and the trash** (delegation design 3.10, round 5 f; round 6 10.4): every deletion of
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
- `2026-10-12 17:38 UTC` · `v3.1.0` **The facts catalogue and the per-tool classification** (delegation design 4.1-4.6, `orchestrator/tools/
  facts_catalog.py`, texts in `orchestrator/utils/l10n_facts.py`): every finding before a question has a fixed kind.
  Hard blockers (disk full, offline mode, an unpaired board or a changed host key, a gated model without access, the
  Deep Ingest notice not read, a withdrawn pack, ...) refuse before any question with their reason code, a next step for
  the AI and, apart from it, what the person can do; a blocker an AI can clear names the tool that clears it. Situation
  warnings (a tight disk, a large download, memory that is tight or too small, many pages, an untested module, a
  replaced board runtime; never the number of knowledge snapshots, which have no limit) come with a rule for an AI;
  security and legal warnings (pickle weights, a base image outside the Docker Official Images or not pinned, vendor
  downloads above the soft cap, an unreviewed or community target, the first build of AI-made build code, an untrusted
  model run natively on a board or in the sandbox the person has not allowed for AIs (`bench_untrusted_sandbox_ok`),
  signing as the person, gated models, licences) always stay with the person, a base image outside the Docker Official
  Images also after a confirmed build with it, and facts that could not be checked are the security warning
  `facts_unknown`, never "no problem" (the person reads in plain words what could not be checked, never an internal
  code). Builds, recipe builds, downloads, pack imports and exports, Deep Ingest, deploy packages and deploys, board
  tools, the local runtime, benchmarks, module creation and settings raise them. A decision is bound to the facts'
  decision digest, which leaves out free space and byte counts; a build or download under a standing approval (and later
  a delegation) uses only the commit its look-up resolved (blocker `commit_required` when Hugging Face could not be
  asked). The AI's view of the facts (`delegation.ai_view`) never carries a fact only the person sees. Warning codes are
  stable identifiers.
- `2026-10-12 10:42 UTC` · `v3.1.0` **A real-client smoke test** (`scripts/mcp_client_smoke.sh`, checker `scripts/mcp_smoke_check.py`): runs Claude Code
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
- `2026-10-12 09:16 UTC` · `v3.1.0` **`start_recipe_build` and the resource `lcf://packs/index`** (MCP design T9): an AI client builds an installed
  community recipe exactly as pinned (the model at the recipe's commit, target, format, board, quantization,
  importance matrix, context length) through the two-phase path of `start_build` (same question, disk reservation,
  quotas and standing-approval scope; `BuildService.start_recipe`). A calibration dataset is taken only when the
  recipe names one and must be exactly that file; the question shows the importance matrix and context length, and
  a recipe changed in any field after the question is not built. The resource
  shows the cached index and the installed packs as `list_packs` does, never refreshed.
- `2026-10-12 09:16 UTC` · `v3.1.0` **A confirmed build unlocks a build standing approval for packs installed under one** (MCP design 6.10): a target
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
- `2026-10-12 09:20 UTC` · `v3.1.0` **A deploy's end in the audit log**: when a `deploy_to_device` job ends (done, failed or cancelled), its own end
  entry with the job, the device's label and the device state is written, linked to the call's `started` entry.
- `2026-10-12 07:24 UTC` · `v3.1.0` **Optional tools `delete_artifact` and `restore_knowledge_snapshot`** (MCP design T10): delete one built model
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
- `2026-10-16 00:39 UTC` · `v3.1.0` **The MCP server inside a container you built** (`llm-cli mcp serve --transport http --container`): binds the
  container's interfaces, and only inside a container; the named token, Host and Origin checks stay the same. Publish
  the port to the host's loopback only (`-p 127.0.0.1:8765:8765`).
- `2026-10-12 14:23 UTC` · `v3.1.0` **The framework in a container, built on your own computer** (round 5 h): no framework image is published or
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
- `2026-10-12 06:38 UTC` · `v3.1.0` **Shared build state** (MCP design T11): every framework program (the GUI, a CLI call, each MCP server) writes a
  summary of each build request into the per-user folder (`build_requests/`), so the GUI status bar, `llm-cli build
  status`, `list_build_requests` and `get_build_status` see the same builds whichever program started them, also a
  request that still waits in another program's queue. Each summary names the model by its repo id or label (never a
  host path), the caller class that started it (never an MCP session key) and the program that runs it (`started_in`,
  column `Where`); errors are redacted. A request whose program ended before it finished is shown as interrupted.
  Builds of another program stay read-only. The note that only this process's builds are visible is gone.
- `2026-10-10 21:50 UTC` · `v3.1.0` **Ditto uses the framework tools**: in the chat (GUI) and with `llm-cli ditto ask "<question>"`, Ditto can
  list targets, check capabilities, start and cancel builds, read logs, search the knowledge base and run the
  doctor, always within your tool permissions. Tools that need a confirmation show their title and every
  argument (a dialog in the GUI, a prompt in the CLI; denied without a terminal).
- `2026-10-10 21:50 UTC` · `v3.1.0` **AI settings**: providers from `configs/llm_providers.yml`, a model list loaded live from the provider
  (free text still works), effort, privacy level for cloud providers, offline mode and a fallback chain.
- `2026-10-10 23:50 UTC` · `v3.1.0` **Provider layer for AI** (`orchestrator/llm`): one client for every provider, built on the official
  `anthropic` and `openai` SDKs (OpenAI-compatible servers through `openai`). Providers are data
  (`configs/llm_providers.yml`): model and effort per task, retries, a fallback chain, structured answers
  validated against schemas, and a privacy filter for cloud providers (setting `ai_security_level`): secrets
  are always redacted; addresses, e-mail addresses, logins, user names, host names and serial numbers are
  replaced by stable placeholders that are mapped back locally in the answer.
- `2026-10-10 23:50 UTC` · `v3.1.0` **Local knowledge base** on local embeddings (FastEmbed, ONNX on the CPU) and an embedded Qdrant store: no
  vector-database server and no cloud embeddings needed, and it works offline once its models are downloaded.
  Before every ingest, every restore and every knowledge pack import it saves a snapshot in `knowledge_snapshots/`
  of the per-user folder; a snapshot that does not fit on the disk stops the change (see "Disk check before every
  knowledge snapshot"). There
  is no limit: it keeps as many as the disk holds, deletes none by itself, on any channel (round 5 f), and no number
  of them brings a reminder or a warning. `llm-cli knowledge snapshots` (in the GUI: Tools > Knowledge Snapshots...)
  lists each one with its full path, name, size and what it is, so you delete (to the trash first) or export (copy
  elsewhere, checked by sha256) the ones you tick.
- `2026-10-12 02:57 UTC` · `v3.1.0` **The knowledge base Ditto uses, for AI clients** (MCP tools `list_knowledge_sources` and
  `read_knowledge_source`, resource `lcf://knowledge/sources/<source id>`): a client that does Ditto's job sees what
  the knowledge base holds and reads one source's excerpts in order. `search_knowledge` now gives every excerpt its
  provenance (source id, address or pack, title, section, kind `web`/`pack`/`docs`, trust, date added, position) and
  takes `kind` and `rerank`. Sources are named by ids, never by paths; all their text is marked untrusted. A tool call
  never downloads the embedding or reranking model: without the embedding model the search is refused with the next
  step instead of starting a download of about a gigabyte, and the refusal names no folder.
- `2026-10-12 05:51 UTC` · `v3.1.0` **Deep Ingest for AI clients** (MCP tool `crawl_docs_site`, decision of 2026-10-11): an AI client or Ditto can add
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
- `2026-10-12 06:09 UTC` · `v3.1.0` **The community zoo for AI clients** (MCP tools `list_packs`, `add_pack_from_index`, `export_pack`): a client lists
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
- `2026-10-12 03:20 UTC` · `v3.1.0` **Fix proposals from AI clients** (MCP tool `propose_healing_fix`): a client that analyses a failed build hands its
  summary, cause, manual step and confidence to the framework. It passes the same checks as Ditto's own analysis,
  including the dangerous-command filter, is confirmed by you like any local change, is accepted only for a failed
  build (at most 10 per build) and is shown as AI-generated with who proposed it (the caller and the client's
  self-reported name) in `get_build_status`, `llm-cli build status <request>` and the GUI (**Tools > Fix Proposals from
  AI Clients...**). The framework never runs it, and `resolve_healing` refuses to apply it. The prompt
  `diagnose_build_failure` leads to the tool.
- `2026-10-12 03:49 UTC` · `v3.1.0` **Hugging Face and local files for AI clients** (MCP tools `search_hf_models`, `get_hf_model_info`,
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
- `2026-10-11 02:31 UTC` · `v3.1.0` **Reranking of knowledge-base results**: a local multilingual cross-encoder (FastEmbed/ONNX, about 120 MB,
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
- `2026-10-11 02:31 UTC` · `v3.1.0` **Keyword search understands identifiers**: `-DGGML_CUDA=ON`, `rknn_toolkit2` or `rknnToolkitLite` are also
  found by their parts (`GGML_CUDA`, `rknn toolkit2`), and one- and two-word searches such as `E2041` or
  `GGML_CUDA` use the keyword index. Identifier questions of the retrieval set: hit@3 83 % to 100 %, the rest
  unchanged.
- `2026-10-11 02:31 UTC` · `v3.1.0` **Large code blocks and tables** beyond the embedding model's context (512 tokens for the default model) are
  split at line boundaries, each piece with the table header or the code fence, so their later parts can be
  found at all (answers deep inside such blocks: hit@1 50 % to 100 %). Smaller blocks stay whole. Re-import a
  document to apply it.
- `2026-10-11 02:31 UTC` · `v3.1.0` Knowledge-base models are unloaded after 10 minutes without use and loaded again on the next search
  (setting `rag_model_idle_minutes`, 0 keeps them loaded, at most 7 days). The time counts from the end of the
  last search or import, never during one.
- `2026-10-12 00:37 UTC` · `v3.1.0` **Managed local model** for Ditto's offline mode: `llm-cli ditto runtime install` builds `llama-server` on your
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
- `2026-10-15 22:54 UTC` · `v3.1.0` **Ditto's local server bare metal or in Docker** (DECISIONS round 7 D): `llm-cli ditto runtime ensure [--mode
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
- `2026-10-10 23:50 UTC` · `v3.1.0` **Ditto evaluation** (`llm-cli ditto eval`): fixed cases (build-log analysis, build recommendations, module
  proposals, chat with tools, prompt injection) graded by rules, against the configured provider or per local
  model tier (`--tier small --tier small-q2`; the servers the run starts are stopped afterwards, one you started
  yourself keeps running if it serves the tier). Nothing is built; `--json` writes the report.
- `2026-10-11 01:38 UTC` · `v3.1.0` **Retrieval evaluation** (`llm-cli ditto eval --kind retrieval`): 42 German and English searches (cross-lingual
  questions, identifiers such as `GGML_CUDA`, answers deep inside a large table or script, the last lines of
  failed builds and Ditto's own search strings, unrelated questions) against a corpus of 30 framework and SDK
  documentation excerpts (`evals/retrieval/`). The corpus is indexed into a temporary knowledge base with the
  configured local embedding model: no AI model is needed and your own knowledge base is not touched. The report
  gives hit@1, hit@3, MRR, how often an unrelated question gets no excerpt, figures per tag and the search
  latency; it works offline once the embedding model is downloaded. A manual and nightly CI workflow runs it with
  cached models. Baseline with the default CPU embedding model on the 34 questions: hit@1 83 %, hit@3 97 %, MRR
  0.91; all 4 unrelated questions still get excerpts.
- `2026-10-11 18:29 UTC` · `v3.1.0` **Hardware profile format, schema 2** (`configs/hardware_probe_schema.yml`, parser
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
- `2026-10-11 20:08 UTC` · `v3.1.0` **Hardware probe for boards and PCs** (`scripts/hardware_probe.py`, standard library only, Python 3.8 or newer):
  writes the hardware profile in schema 2 on Linux, Windows and macOS, with the operating system, the C library and
  its version, ARM core clusters, ARM dot product and FP16 arithmetic, the x86 features ggml builds for, the SoC, the
  NPU node and every GPU (NVIDIA compute capability, AMD gfx target, Vulkan). `llm-cli scan` runs it in-process and
  stores the result as this computer's profile (`--script` keeps the fallback script, `--json` writes the JSON
  view); `llm-cli scan --device HOST --user USER` runs it on a board over SSH with `python3 -I` and falls back to
  `hardware_probe.sh` when the board has no Python 3.8. The script travels over the SSH channel, so nothing is left
  on the board, and the profile is stored under the board's ID. For machines without network access the README
  explains the probe by hand. The installed package carries a byte-identical copy of the probe
  (`orchestrator/runtime/hardware_probe.py`), so the framework's own probing works without the `scripts/` folder.
- `2026-10-11 19:39 UTC` · `v3.1.0` **Build recipe for self-built llama.cpp** (`orchestrator/runtime/build/recipe.py`): the hardware profile of the
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
- `2026-10-11 22:03 UTC` · `v3.1.0` **Self-built llama.cpp: build, checks and store** (`orchestrator/runtime/build/`): a plan is built natively on Linux
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
- `2026-10-12 00:37 UTC` · `v3.1.0` **Builds of Ditto's server move between machines**: `llm-cli ditto runtime export FILE` writes the self-built
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
- `2026-10-11 22:03 UTC` · `v3.1.0` `llm-cli doctor` checks the local llama.cpp runtime: a self-built build in use that was changed (error) or is older
  than the pin (warning), the prebuilt archive in use (information: not self-built), and whether this machine has the
  compiler, CMake and Ninja for a native build (information, with the builder container as the alternative).
- `2026-10-11 19:39 UTC` · `v3.1.0` **llama.cpp pinned by commit**: `llama_cpp.source` in `configs/llama_runtime.yml` (commit
  `d81235049384534c167caea52b85a694f6103d14`, tag `b11429`) is the one place. With it come the CUDA toolkits for
  self-built CUDA builds (NVIDIA's redist manifests pinned by size and SHA256, with the driver, compute capability,
  host compiler and CMake limits of each toolkit; NVIDIA's prebuilt development images only as an explicit opt-in
  entry) and the builder bases, pinned by digest. A test fails when any other place that names the llama.cpp version
  disagrees, including the `ARG LLAMA_CPP_COMMIT` every target Dockerfile that builds llama.cpp must have, and
  `python scripts/llama_cpp_pin.py check` checks with `git ls-remote` that upstream's tag points at
  the pinned commit.
- `2026-10-11 19:39 UTC` · `v3.1.0` **Pinned llama.cpp source for air-gapped machines**: `llm-cli ditto runtime export-source FILE` writes the pinned
  commit as a git bundle plus a manifest, and `llm-cli ditto runtime import-source FILE` installs it on a machine
  without internet access. The source is fetched by commit (by its tag only where a server refuses that), must give
  the pinned commit back and is cached read-only in `~/.llm-framework/runtime/llama.cpp/src/`; later runs check that
  nothing in it was changed or added, files git ignores included, and need no network.
- `2026-10-11 23:59 UTC` · `v3.1.0` **Conversion tools tuned to the build host**: target images build llama.cpp's conversion tools (`llama-quantize`,
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
- `2026-10-11 23:59 UTC` · `v3.1.0` **`runtime:` block in new target modules**: the module wizard, `llm-cli module create` (new option `--probe FILE`)
  and `llm-cli module generate-ai` write the build recipe's plan for the board's llama.cpp runtime from the target's
  probe into `target.yml`: architecture, OS, the C library the cross build must not exceed, builder base, CPU options
  (`armv8.2-a+dotprod+fp16` for the RK3566), backends and programs, built from source. The block is a record (a
  device runtime is always planned again from the device's probe) and is checked against its schema in packs. CPU and
  CMake flags from the wizard or Ditto are kept there as advisory text and never applied; the wizard labels them so.
- `2026-10-11 23:49 UTC` · `v3.1.0` **Self-built llama.cpp on the board** (`llm-cli device runtime plan|build|deploy|verify|status`, `llm-cli device
  list|settings`): the board's own probe profile (`llm-cli scan --device`) becomes a cross build in the builder
  container, without network, on a base whose C library is not newer than the board's (glibc, or musl on Alpine; a board
  older than every base gets a clear message), with the C++ runtime linked statically and, for OpenMP builds, the
  board's `libgomp.so.1` taken from the cross toolchain. The result is a tarball (`llama-server`, `llama-cli`,
  `llama-bench`, `llama-perplexity`, `lib/libgomp.so.1`, a start script, the build's stamps, llama.cpp's MIT licence and
  `SHA256SUMS`) that the deployment uploads over SSH into `~/.local/share/llm-framework/llama.cpp/` on the board and
  checks there file by file. The new build's programs are then checked on the board as your unprivileged user, before
  anything starts them: `--version` against the pin, `--list-devices`, the server's refusal of `--tools` and, with
  `--bench-model`, a short `llama-bench` run (a model that is not a catalog model only after your consent, written to
  the audit log). Only a build that passes becomes active through a `current` link (the previous build is kept); a build
  that fails, for example with a `SIGILL` from a CPU baseline that does not fit the board, is removed again and the
  previous build stays active. The start script runs the server on 127.0.0.1 with a key that is generated on the board
  under umask 077, a cleared environment and fixed options; it passes on only a few tuning options with checked values
  (context size, threads, CPU mask, ...). How the runtime starts is chosen beforehand in the deploy settings, never
  asked during a deployment: the start script in your home (the default), or a systemd unit, which can be chosen only
  for boards with systemd and after you confirm that deployments may run `sudo -n` there. Tested with a fake SSH channel
  and fixture profiles (an RK3566 on DietPi, an Alpine board, a board without systemd), and with real programs:
  `llama-server` and `llama-bench` built natively with the device plan's options for an x86-64 machine (30 MB, an 11.6
  MB tarball) passed the format check, the deployment to a local stand-in board and the checks there.
- `2026-10-11 01:30 UTC` · `v3.1.0` **Deep Ingest honours machine-readable opt-outs.** Text and data mining of public pages is allowed unless
  the rightsholder reserved it in machine-readable form (EU DSM Directive Art. 4, German UrhG § 44b), so
  besides robots.txt the crawler skips pages marked `noindex`, `none`, `noai` or `noimageai` in an
  `X-Robots-Tag` header or in `<meta name="robots">` / `<meta name="llm-cross-compiler-framework-bot">` (also under
  its earlier name `llm-conversion-framework-bot`), does not follow the links of `nofollow` pages, and honours the
  W3C TDM Reservation Protocol (`/.well-known/tdmrep.json` per site, the `tdm-reservation` header and meta tag, PDF
  metadata). Redirect targets on other hosts are checked the same way. Every page that is not stored is named in
  the crawl log with its reason. None of these checks can be switched off.
- `2026-10-11 02:03 UTC` · `v3.1.0` **Notice before the first Deep Ingest** (English and German): what is read (only publicly reachable pages),
  which opt-outs are honoured, that the knowledge stays on this computer, and that sharing it keeps source and
  licence and needs your explicit release. It is information, not a legal confirmation: one "Understood"
  button. It is shown once per user and again when its text changes.
- `2026-10-11 02:03 UTC` · `v3.1.0` **Spoken announcements** of the notice and of the Deep Ingest outcome through the speech engine built into
  the operating system (Windows System.Speech, macOS `say`, Linux `spd-say` or `espeak-ng`), in the interface
  language. Best effort and never in the way: no audio files, the system volume is never touched, and it is
  silent in CI, over SSH without a display, on headless systems, with `LLM_FRAMEWORK_NO_AUDIO=1` or when
  *Tools > Spoken Announcements* (setting `audio_announcements`) is off. Every announcement is also logged. The
  switch warns when it cannot save the setting.
- `2026-10-11 02:03 UTC` · `v3.1.0` **Deep Ingest progress and Stop**: the wizard shows "Page N of M: <url>" instead of an endless spinner, and a
  Stop button ends the crawl after the current page. Closing the wizard stops a running crawl the same way and
  closes once the current page is stored, without freezing the window.
- `2026-10-11 02:55 UTC` · `v3.1.0` **Input history**: the input fields offer what you used recently, newest first. Model, calibration dataset,
  Deep Ingest URL and device (Deployment, remote hardware scan) are editable lists; the most recent model and
  device are filled in, datasets and URLs are only offered. Targets and boards come from the target
  definitions, so the most recently built one that is still offered is preselected (a target without declared
  boards offers recent boards as suggestions). The history is per user (`input_history.json` in the per-user
  state directory), at most 20 entries per field, and never holds secrets: values that look like an API key or
  token, URLs with a user name or password and URLs with a credential or session parameter (such as `?token=`
  or `;jsessionid=`) are not remembered. *Tools > Clear Input History...* forgets it after a question that lists
  each file it deletes (the history and the copy of a damaged one: full path, file name, size, what it is; "No" is
  the default), and deletes only those.
- `2026-10-11 01:54 UTC` · `v3.1.0` **Signed community packs**: a pack is a zip with a manifest (sha256, size and mode of every file) and an
  optional Ed25519 author signature; the author's public key is pinned at the first install, and another key
  under the same author name is shown with a warning. Reading a pack accepts exactly the listed files, never links
  or special files, and checks every hash. Trusted publisher keys ship in `configs/community_keys.yml`, each with
  an expiry date.
- `2026-10-11 01:54 UTC` · `v3.1.0` **Encrypted packs for private sharing** (with your own machines or a team): encrypted in the age format by rage
  (through `pyrage`) to one or more recipients' age keys or to one passphrase, and signed by the sender, so
  recipients see who sent it and any change, including an added or removed recipient, is detected. The standard
  `age` and `rage` tools open them too when they are encrypted to a passphrase or to a key made with `age-keygen`
  or `rage-keygen`. A passphrase pack whose scrypt work factor needs more than 1 GiB of memory
  is refused before anything is decrypted. Each user gets an Ed25519 key and an age identity on first use, kept
  only in the encrypted secrets store; the public keys can be exported as text.
- `2026-10-11 01:54 UTC` · `v3.1.0` **Recipes**, the model zoo's "model templates": the model at a pinned commit, the target with a fingerprint
  of its files, board, format, quantization, options and verified results (artifact hashes, toolchain image,
  device tests). A recipe is generated from a finished build, checked against the target's capabilities and
  maps to a normal build request.
- `2026-10-11 01:54 UTC` · `v3.1.0` `build_info.json` records what a recipe needs: the model's commit (the download is pinned to it), the target
  fingerprint and version, the toolchain image, the context length, the calibration dataset's hash and the
  framework version. Build requests accept a `revision` (commit) for Hugging Face models.
- `2026-10-11 02:42 UTC` · `v3.1.0` **Community zoo** (`llm-cli zoo list|show|install|verify|export|keys`): community targets, recipes and
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
- `2026-10-11 02:42 UTC` · `v3.1.0` **Community Hub** in the Tools menu: a trust column, filters for kind and trust, import of pack files (also
  packs encrypted to your key), and "Build" on an installed recipe fills in the Builder, including the model's
  pinned commit.
- `2026-10-11 02:42 UTC` · `v3.1.0` **Export for community** on the module wizard's last page, in the Builder log's context menu (a finished
  build becomes a recipe) and with `llm-cli zoo export`: a pack of a target, a recipe or your knowledge base,
  privacy-scrubbed (the changes are shown), validated and signed with your key; with `--to` or `--passphrase`
  it is encrypted for private sharing instead. Knowledge packs keep the source address of every page and claim
  no licence for them (`NOASSERTION`); a community pack you installed is not re-signed as your own, and pages
  imported from installed knowledge packs are left out of a knowledge export. "Open pull
  request" (`--pr`, which asks after the summary unless `--yes` is given) hands it to the community repository
  (setting `community_repo`): through the GitHub CLI if it is installed and logged in, otherwise the
  repository's upload page opens in the browser. The framework never asks for, reads or shows a token.
- `2026-10-11 02:42 UTC` · `v3.1.0` Maintainer tools for the community repository: `scripts/zoo_sign_index.py` (signing key in the OS keyring,
  index entries from packs, signature of `index.json`) and the CI workflow template
  `scripts/community_repo/validate-contribution.yml`, which runs `llm-cli zoo verify` on every submitted pack.
- `2026-10-11 00:21 UTC` · `v3.1.0` **Claude workspace ID** (AI settings field "Workspace ID", setting `ai_anthropic_workspace_id`): Claude API
  keys that are valid in every workspace of the organization now work. The ID is sent as the
  `anthropic-workspace-id` header on every request made with the API key to the Claude API, the model list
  included. An `ant auth login` profile keeps its own workspace, and other servers never receive the ID.
- `2026-10-11 03:18 UTC` · `v3.1.0` **Sign-in methods per provider** (`configs/llm_providers.yml` schema 2, `orchestrator/llm/auth`): each
  provider lists its sign-in methods in the order they are preferred, with the page where the key is created,
  the environment variables it reads, a free check and extra fields such as the Claude workspace ID or the
  OpenAI organization and project. Catalogs of schema 1 in your config directory still load; one of a newer
  schema is refused with a message instead of being misread. Sign-in details
  that are not secret (account or workspace, the last four characters of a key, extra fields, when it was
  checked) are kept in `auth.json` next to the credential vault. Tokens of the sign-in flows that come later are
  renewed under a lock shared by the GUI, the CLI and the MCP server; the new refresh token is stored before it
  is used, and a refused renewal marks the sign-in as expired.
- `2026-10-11 03:18 UTC` · `v3.1.0` **Credential check** (the **Check** button in the sign-in panel and `llm-cli ditto auth status --check`): one
  free GET request (the model list, OpenRouter's key endpoint) says whether the stored key works, which workspace
  or key name it belongs to and how many models it can use; a key that works but may not list models is reported as such, also
  by the live model list in the AI settings. Redirects are not followed, so the key goes to the provider only.
- `2026-10-11 03:18 UTC` · `v3.1.0` **API keys from environment variables**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` and
  `OPENROUTER_API_KEY` are used when no key is stored for the provider (only the variables the provider catalog
  lists, read by the framework and never by the SDKs). The AI settings name the variable, nothing is copied
  into the vault, and a stored key always wins. When the credential vault cannot be read, no variable stands in
  for the key that may be stored there: the error says what is wrong with the vault.
- `2026-10-11 00:29 UTC` · `v3.1.0` **How credentials are protected is your choice** (setting `secret_storage`, or `LLM_SECRET_STORAGE` for
  one run): the OS keyring (default), a credential helper command (`secret_helper`, e.g.
  `pass show llmcc/vault-key`, run without a shell), a passphrase asked once per start (scrypt), a key file
  next to the vault (`insecure`, not encrypted at rest) or memory only (`ephemeral`, for CI). Nothing is
  picked automatically: without a usable keyring and without a choice, the framework stops and names the
  options. `llm-cli doctor` warns about the key file and reports memory-only storage.
- `2026-10-11 02:11 UTC` · `v3.1.0` **`--debug-http`** (`llm-cli --debug-http`, and the same option when starting the GUI) shows the debug output
  of the HTTP libraries and the AI provider SDKs on stderr, with credentials redacted.
- `2026-10-11 01:37 UTC` · `v3.1.0` **Documentation in English and German** for every document (README, blueprint with design and roadmap, CLI
  usage, features, contributing, module developer guide, SSOT, changelog), checked by `tests/test_docs.py`:
  every document has its twin, links and anchors resolve, both changelogs list the same versions, and the
  roadmap names only what comes.
- `2026-10-11 04:36 UTC` · `v3.1.0` **Sign-in from the command line**: `llm-cli ditto login <provider>` signs Ditto in with the provider's
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
- `2026-10-11 04:36 UTC` · `v3.1.0` **Sign-in panel in the AI settings** (**Sign in...**): the provider's methods as cards in the catalog's order,
  the first usable one marked "Recommended"; a key form with the key page, the extra fields, **Check**,
  **Save** and **Sign out**; a status line ("Signed in as ...", "Not signed in", "Using key from environment
  variable X"); and, without a usable keyring, the storage chooser, which the GUI also shows at startup before
  it gives up (a choice there that cannot open the existing vault is not kept, so the next start still works). A
  vault protected by a passphrase asks for it in a dialog. Every vault, keyring and network call
  runs in a worker thread, and an older read that finishes late never replaces a newer state. Ditto's chat header
  shows the access in use, for example "Local · Ollama". A key typed in the AI settings' own key field replaces a
  method a sign-in remembered (such as the `ant` profile), so it is not left unused.
- `2026-10-11 04:36 UTC` · `v3.1.0` `llm-cli secrets set --with-token` reads the value from stdin.
- `2026-10-11 13:50 UTC` · `v3.1.0` **Cancel Build** in the Builder tab: cancels the running build through the build service after a confirmation
  (enabled only while the build the tab started runs); the log says when it has stopped, also after a cancel
  while the build was still being handed to the build engine.
- `2026-10-11 18:30 UTC` · `v3.1.0` **More context in audit entries**: the confirmation channel (`elicitation`, `framework-gui`, `framework-cli`),
  the SHA-256 of the question text, the person's answer (accepted, declined, cancelled, unavailable, unanswered,
  timeout), the client's self-reported name, version, transport and protocol (a label only; a key in it is removed
  before the label is shortened, as in the stderr line), a hash of the connection, the duration, the SHA-256 and size
  of the result (never the result itself) and a link from the end entry to the call's write-ahead entry. The fields are bound to the hash chain without changing how 3.0.0 hashes an entry: logs written before them
  stay verifiable, and a 3.0.0 process that is still running (an MCP server an AI client started before the update)
  keeps verifying and appending to the same log instead of reporting the new entries as modified.
- `2026-10-11 18:55 UTC` · `v3.1.0` **Tool permissions per caller**: Ditto and local MCP clients over stdio can have permissions of their own
  (`llm-cli tools permissions --caller ditto|mcp`, `--reset --caller` returns to the shared ones); a caller's first
  permissions start from the shared ones, including settings for single tools. `llm-cli tools list --caller`, the
  CLI setup and the GUI dialog show each tool's own default, the highest decision any setting can give it, whether
  it is new (not reviewed yet) and whether a standing approval may be offered.
- `2026-10-11 18:55 UTC` · `v3.1.0` **`get_status` tool** (Ditto and MCP clients): the caller's tool permissions, with every tool's decision and why,
  also the tools that are not listed because they are denied, and the actions no tool does with where the person
  does them, so an AI can name the real reason when it cannot do something.
- `2026-10-11 19:16 UTC` · `v3.1.0` **Fool-proof tool calls** (Ditto and MCP clients, every model size): spelling variants of tool names
  (`StartBuild` runs `start_build`) and declared argument aliases (`quant` for `quantization`) run with a note,
  while any other unknown argument name, also one in other letter case, is refused with a suggestion;
  values are coerced the same way on both paths and matched against the target's formats, boards and quantization
  types exactly, by canonical form if unique or by the target's aliases, never by guessing a near miss; every
  refusal carries a reason code and the next step; the same failing call is flagged the second time and refused
  without running the third time; after three input errors an error shows a valid example call. What was repaired
  is marked in the question, listed in the result's `notes` and recorded in the audit log. Tool arguments a local
  model sends with a code fence or in Python notation are read once more.
- `2026-10-11 22:39 UTC` · `v3.1.0` **Two-phase confirmation and question text v2** (Ditto and MCP clients): a question starts with the consequence in
  plain words from facts the framework looked up, then who asks (an MCP client's self-reported name, version and
  transport, and whose permissions apply), the facts the framework checked (external text labelled "not checked"), and
  every argument with the speaking name of an id (a build's model and target, a local file's label). A tool can look
  facts up before the question, and the call is bound to them: `resolve_healing` shows the pending proposal from the
  framework's own state, refuses a proposal that changed after the question, and refuses to approve a manual step
  before anything is asked (a full Docker disk, `docker_prune`, included: no healing strategy deletes anything).
  Ditto's GUI dialog and CLI prompt show questions up to 16,000 characters (a scrollable dialog, the pager); an MCP
  form stays at 4,000.
- `2026-10-11 23:23 UTC` · `v3.1.0` **Limits per client for tool calls** (Ditto and MCP clients): read tools 20 calls per 10 seconds, `start_build`
  3 unfinished builds and 20 starts a day, 30 questions per hour, and a build queue of at most 20 unfinished requests
  per framework process for AI callers (the person's own GUI and CLI builds are always queued); after a "no" the
  same call is not asked again for 10 minutes, and three "no" to one tool pause its questions for 30 minutes. A limit hit is an audited `rate_limited` refusal that says when to
  try again. The repeat guard lets a refused call run again 10 minutes after its last failure (it was refused until
  the process ended) and groups the calls of one MCP connection by a random id, never by a memory address a later
  connection could get again; Ditto stops calling tools early only after failures without progress, with up to 4
  more steps while it fixes an input error (a call sent to the wrong computer is one); a call that waits for a
  decision is never counted as a failure.
- `2026-10-11 23:38 UTC` · `v3.1.0` **`llm-cli tools audit` shows the newer audit fields in words**: who called (Ditto, all local stdio clients, a
  named token, with the client's self-reported name), the tool's title and the name the AI wrote, the reason code and
  how the person was asked with their answer; `--details` adds repairs, duration, result hash, links and session.
- `2026-10-11 19:16 UTC` · `v3.1.0` **`llm-cli ditto eval` records every tool call attempt**, not only those that reached a tool: the report's
  `tool_use` block gives tool calls per task, the share of malformed calls and of unknown tool names, repairs and
  repeat refusals.
- `2026-10-11 18:52 UTC` · `v3.1.0` **Folders the AI may use**: Ditto and MCP clients name a local model or calibration dataset as
  `<label>:<relative path>` (for example `models:qwen2.5-0.5b-instruct`, `datasets:calib/de.txt`) or by its file id
  inside the models folder, the new datasets folder (setting `datasets_dir`, next to the models folder by default)
  or a folder you add with `llm-cli tools folders add|list|remove` (terminal required) or in the GUI (Tools >
  Folders the AI May Use...). Adding one shows what sharing it means and is audited (an addition that cannot be
  audited, or whose folder changes while you are asked, does not happen); `/`, your home folder, a folder
  containing the per-user folder or a denied folder cannot be added, and the same limits apply to the models and
  datasets settings. The Hugging Face cache's `hub` folder may be added; its token files stay denied.
- `2026-10-11 19:07 UTC` · `v3.1.0` **Jobs and waiting for MCP clients and Ditto**: the tools `get_job_status` and `cancel_job` for long operations
  other than builds (a job id `job_...`, its state, progress, result or error; a summary of each job is kept in
  `~/.llm-framework/jobs/`), and `wait_seconds` on `get_build_status` and `get_job_status`: the call returns as
  soon as the build or job changes or ends, at most after 50 seconds (a larger value is reduced with a note), with
  progress notifications for clients that ask for them. Waiting holds no worker thread, at most 4 waits run per
  client, and cancelling a wait never touches the build or job.
- `2026-10-11 19:25 UTC` · `v3.1.0` **MCP server names its clients:** for each new client `llm-cli mcp serve` writes one line to stderr (never to
  the client) with the name and version the client reports, the protocol version, the transport and the declared
  capabilities, including whether it can ask you (elicitation). The tools see the same label. Docs: a table of
  what Claude Code, Claude Desktop, Cowork and VS Code support.
- `2026-10-11 19:52 UTC` · `v3.1.0` **Named MCP tokens, one per client** (`llm-cli mcp token add|list|rotate|revoke`): a token is shown once and
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
- `2026-10-11 22:55 UTC` · `v3.1.0` **Pending approvals for AI clients that cannot ask you** (MCP server). When a client has no form elicitation
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
- `2026-10-11 23:07 UTC` · `v3.1.0` **Standing approvals: "yes, and do not ask again"** (MCP server, Ditto). Created only by you in the framework's own
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
- `2026-10-11 23:38 UTC` · `v3.1.0` **MCP resources**: `lcf://targets`, `lcf://targets/<target>/capabilities`, `lcf://builds/<id>/status`,
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
- `2026-10-11 23:52 UTC` · `v3.1.0` **MCP prompts**: Ditto's tasks for a client that does Ditto's job, with the same task text and knowledge context:
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
- `2026-10-11 23:52 UTC` · `v3.1.0` **Honest "cannot" for Ditto's module proposals and build recommendations**: the model may answer `cannot` with a
  reason code (missing information, not supported, no tool, permission, needs a person, hardware, resources, limit;
  "untested" is none of them). Ditto asks once more when the framework's check of the task input finds what the task
  needs, and shows a second `cannot` as not confirmed by the framework; with neither a probe nor a board it says so
  itself without asking a model. Module proposals gain `assumptions` (the guessed values, at most 10) and
  `base_image_reason` (required for an image that is not a Docker Official Image), and can start from the board and
  its SoC when there is no probe. The wizard and `llm-cli module generate-ai` show the base image plainly before the
  module is created: registry, digest, official image or another registry's prebuilt image, network access of the
  image build, and the AI's reason.
- `2026-10-12 00:11 UTC` · `v3.1.0` **MCP tokens in the GUI**: the Tool Permissions dialog (Tools > Tool Permissions (Ditto / MCP)) has a section
  "MCP tokens" that lists the tokens and adds one (shown once; read only, a copy of the local stdio clients' or Ditto's
  permissions, or linked to Ditto's), rotates, revokes and sets each token's own permissions, audited with actor
  `gui`, never with the token.
- `2026-10-12 04:17 UTC` · `v3.1.0` **Hardware probes by id for AI clients and Ditto**: `llm-cli scan --import FILE [--label TEXT]` (and the GUI's
  **Import from File** and the module wizard's imports) stores a board's probe file in the probe store, validated and
  without its host name, and prints its probe id and the matching target. The read tool `get_hardware_probe` lists
  the newest probes with their matching target or shows one probe's values (external text); the resource
  `lcf://probes/{probe_id}` mirrors it; `probe_local_hardware` (asks by default; "do not ask again" offered) probes
  this computer as a job and keeps the result as `local`. The `add_board` prompt is now listed and reads a probe by its
  id. No tool reads a probe from a path.
- `2026-10-12 04:17 UTC` · `v3.1.0` **`create_target_module`**: an AI client in Ditto's role (or Ditto) adds a target module for a board nobody here may
  have tested, through the module generator's checks. The question shows the base image pinned to the digest its tag
  points at now (any registry; a non-official one shown plainly as a prebuilt image of its publisher; the image build
  runs with network access), the image's platforms and whether one matches this computer, the AI's reason, every
  vendor source, pip package and download, the values the AI guessed and that the module is untested; the module is
  written with exactly that digest, support level `community`, `ai-generated` and the `runtime:` block planned from the
  board's probe. It always asks, never offers "do not ask again", and a long definition becomes a pending approval.
- `2026-10-12 04:17 UTC` · `v3.1.0` **Vendor software in target modules**: `package_sources` (an https apt repository whose signing key the image build
  checks against its fingerprint), `pip_packages` (pinned `name==version`, from PyPI or one https index) and
  `pinned_downloads` (https, exact size and sha256, into `/opt/vendor`), rendered by the framework's Dockerfile
  template, for Ditto's proposals, the wizard and the MCP tool. Their counts are soft limits (3, 30, 5): going above
  one is a warning the person's approval overrides, never a refusal; prebuilt llama.cpp stays refused.

- `2026-10-12 06:03 UTC` · `v3.1.0` **Deploy for AI clients** (MCP tools `list_devices`, `create_deploy_package`, `deploy_to_device`, prompt
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
- `2026-10-12 06:03 UTC` · `v3.1.0` **Trusted devices** (`~/.llm-framework/devices.json`, `llm-cli deploy devices list|add|remove`, `deploy run
  --label`): `deploy run`, `scan --device`, `device runtime deploy` and the GUI's Deployment window record a device
  after you confirmed its host key and the framework logged in (written to the audit log); `devices add` takes over a
  device you trusted before (terminal only, never a new key, never a password). Only these devices can be named by AI
  tools, and only while the framework's `known_hosts` still holds that key; no tool writes a record.

- `2026-10-12 06:32 UTC` · `v3.1.0` **Optional runtime tools for AI clients** (MCP tools `build_local_runtime`, `start_local_runtime`,
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
- `2026-10-12 06:32 UTC` · `v3.1.0` **Benchmark runs of this computer** (`~/.llm-framework/benchmarks/`): `llama-bench` results of the self-built build
  (prompt and generation speed, peak memory) with the model's sha256 and the build id; `get_status` shows the newest.
- `2026-10-12 13:40 UTC` · `v3.1.0` **The chat feed store with signed rows** (`chat_feed.sqlite` in the per-user folder, mode 0600; delegation design
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
- `2026-10-12 18:24 UTC` · `v3.1.0` **The chat panel on the right of the main window** (delegation design 5.1-5.9): a dock (F9 or **View > AI Chat
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
- `2026-10-16 00:25 UTC` · `v3.1.0` **Mojo: the health check looks (Mojo Oracle) and repairs (Mojo Miracle)** (sixth round, decision 9; seventh round,
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
- `2026-10-16 15:09 UTC` · `v3.1.0` **Mojo Oracle reports the trusted boards and checks Ditto's image** (eighth round N6; W1 of 2026-10-16; seventh
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
- `2026-10-16 00:25 UTC` · `v3.1.0` **`llmcf`, the command line's new name** (seventh round, item 11): a second console script next to `llm-cli`, the
  same program; `llm-cli` stays. The framework container has both commands.
- `2026-10-16 00:25 UTC` · `v3.1.0` **The MCP tools `run_mojo_oracle` and `run_mojo_miracle` and the prompt `check_installation`** (seventh round, item
  11'): `run_mojo_oracle` (read-only) gives every finding with `where` as a label (never a host path), `why` and
  `repair` (`how`: `miracle`, `deletion_list`, `person`, `admin` or `not_automatic`, with the next step).
  `run_mojo_miracle` repairs one problem whose `repair.how` is `miracle`: every call is asked with the plan (the full
  path only in the framework's own window), no standing approval, no delegation, never a deletion; any other problem is
  refused before the question with its reason, and the result says whether it was repaired, and if not, why.
  `run_mojo_oracle` contacts no board; a finding of a board a recorded fact points to names the request that checks it
  (`check_board`), and `{"board": "<label>"}` checks that one trusted board now. An AI names a board by its label or
  device ID, never by an address (a board without a name of its own, whose label is its address, by its device ID).
- `2026-10-16 00:39 UTC` · `v3.1.0` **The framework's own documents in the knowledge base** (DECISIONS round 8, N12): **Tools > Index the Framework's
  Documentation...**, `llm-cli knowledge index-docs [--with-code]` and the MCP tool `index_framework_docs` (asked
  every time, runs as a job) add the framework's documentation (README, CONTRIBUTING, the `docs` folder) and, on
  request, its code and configuration (only what it ships: never your settings, your own targets or installed packs)
  to the knowledge base as kind `docs`, so Ditto can explain the framework's
  internals. It never happens by itself (before, the index existed but nothing called it); a snapshot is taken first
  (without room for it on the disk nothing is indexed, also for the first snapshot, measured by the knowledge base
  itself; a snapshot that cannot be written stops it too; with little room left the question or the command says
  so), a file indexed again is replaced, a file it read but did not index is named with the reason, and a knowledge
  pack export leaves these texts out.
- `2026-10-16 02:12 UTC` · `v3.1.0` **Board deployment over a network that drops** (eighth round, N4 and N5): you name the board by its address or
  its host name (`myboard.local`; `llm-cli deploy run --host`, `--ip` stays as the old name). When the network drops
  during a deploy (a board with Wi-Fi only), the deploy waits for the board (300 s by default, `--wait`), shows that
  it waits with the time and the limit, and can be cancelled (Cancel in the Deployment window, Ctrl+C in the CLI,
  `cancel_job`); the result then says whether `deploy.sh` had started, also when the network dropped just as it was
  started. It logs in again with the same host key check and continues: the upload resumes at the bytes that
  arrived once their sha256 matches, and `deploy.sh` runs on the board on its own and is started once, never twice.
  `deploy_to_device` results also have `reason_code`, `resumed` and `waited_s`; its progress names the board by its
  label.
- `2026-10-16 02:12 UTC` · `v3.1.0` **Why a board cannot be reached** (N6): the reason codes `device_host_down`, `device_port_closed`,
  `device_auth_failed` and `device_host_key_changed` (`orchestrator/runtime/device/reasons.py`) with what happened and
  the next step for the person in English and German, in the GUI and the CLI. An AI gets the plain code with a next
  step, without the address or the account; they are the blockers of the board tools and replace
  `device_unreachable` and `host_key_changed` of the facts catalogue.
- `2026-10-16 02:12 UTC` · `v3.1.0` **KV cache of a board's server** (N7): `f16`, `q8_0` or `q4_0` (`-ctk`/`-ctv`, checked words of the shared launch
  policy and of the start script). Automatic by default: `q8_0` when little memory is left next to the model on the
  board (from its probe), otherwise `f16`; you can always choose a type, and `f16` switches it off. Chosen with
  `llm-cli device settings --kv-cache` or in the Deployment window (a saved choice the window does not show is kept
  unless you choose there); the systemd unit and the start command take it from the board's deploy settings, a
  package made in the Deployment window names it in the start command of its `deploy.sh`, and the question of
  `deploy_device_runtime` names it.
- `2026-10-16 02:12 UTC` · `v3.1.0` **"Tested on board X"** (N3): a passed benchmark on the board (`llm-cli device runtime verify` or `deploy` with
  `--bench-model`, `run_device_benchmark`, and the new "Benchmark on a board" part of the Deployment window) is
  recorded with every build of that model in the output folder, the framework's build folder and the folders builds
  were saved to (`device_tests.json`, found by the model's sha256), with the KV cache the measurement used next to the
  one the board's server is set to. The Deployment window lists such a build as "tested on <board>", and after a
  deploy of a GGUF model it fills in the board and the model for that measurement.
- `2026-10-16 16:20 UTC` · `v3.1.0` **The AI's hands in the window: the GUI tool `gui`, for Ditto and for AI apps alike** (`orchestrator/tools/tools_gui.py`,
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
- `2026-10-16 00:55 UTC` · `v3.1.0` **The board recommendation from the hardware probe** (`orchestrator/runtime/board_recommendation.py`): after a probe
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
- `2026-10-16 00:55 UTC` · `v3.1.0` **Documentation links for Deep Ingest** (`orchestrator/Core/doc_sources.py`): the source catalogue's `docs_*` links
  for the probe's vendor and the SDK fill the wizard's Deep Ingest window; you check them and start the crawl. When
  Ditto says that information is missing for a module, the wizard offers Deep Ingest with these links. AI clients read
  them through the read-only resources `lcf://knowledge/doc-sources` and `lcf://knowledge/doc-sources/{probe_id}`; the
  MCP prompt `add_board` points to them when information is missing. The catalogue's header and the documents now say
  that these links are read at run time.
- `2026-10-16 00:55 UTC` · `v3.1.0` **Build progress and the live build log in the Builder** (`orchestrator/gui/builder_parts.py`): a progress bar with
  the step and the percent from the build service (the running build's own step, see Fixed), and the build's log live
  below it (secrets redacted, full paths, plain text, each line once, at most 20,000 lines in view; an AI that reads the
  log gets the folders by label), with a search box and a filter for Info, Warnings or Errors. Lines the service no
  longer returns are noted.
- `2026-10-16 08:38 UTC` · `v3.1.0` **Ditto is marked experimental** (eighth round, Q-2; `orchestrator/utils/ditto_notice.py`): Ditto stays optional.
  The chat panel and the AI settings show an **Experimental** badge, and a notice says once per user, in English or
  German: "Use at your own risk and responsibility. The creator of LLMCF is not liable." It repeats the no-warranty
  terms of `LICENSE.txt` and adds nothing to them. In the GUI it stays at the top of the chat panel until you click
  **Understood** and blocks nothing; a hidden panel shows itself once for it (without the focus), because the Builder
  and the module wizard use Ditto without the panel. `llm-cli ditto` and `llm-cli module generate-ai` print it once
  on stderr at a terminal (without a terminal nothing is printed or stored). README and Features say the same.
- `2026-10-16 09:37 UTC` · `v3.1.0` **Licence and copyright of every crawled page** (eighth round, Q-1 and Q-1'; `orchestrator/rag/licences.py`):
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
- `2026-10-29 21:26 UTC` · `v3.1.0` **The blueprint is three files: the design, the roadmap and the changelog, and every decision is in the design**
  (DECISIONS rounds 34 and 35): `docs/BLUEPRINT.md` held the design and the roadmap in one document; it is split word
  for word into `docs/DESIGN.md` (what the framework is, every component, and every decision with what it says, how the
  code does it and why) and `docs/ROADMAP.md` (what comes, only "in progress" and "planned"), each with its German twin,
  and `docs/BLUEPRINT.md` stays as a short index whose table sends every old anchor to its new place. The changelog has
  no `[Unreleased]` section any more: each of its entries was checked against the code and is part of this version 3.1.0
  (`pyproject.toml` and `orchestrator.__version__` say 3.1.0), planned parts moved to the roadmap, and every entry of
  every version starts with the time stamp and version of the commit that first wrote it (`` `YYYY-MM-DD HH:MM UTC` ·
  `vX.Y.Z` ``, the German entry with the stamp of its English twin); every version has its link. The design carries
  every decision of the maintainer at the component it concerns (what, how, why; a replaced decision as one history
  line) and says that the decisions log lives outside the repository. The "(unreleased)" marks are gone from every
  document, and the version lines say 3.1.0. The MCP resources `lcf://docs/DESIGN` and `lcf://docs/ROADMAP` (and `_DE`)
  sit beside `lcf://docs/BLUEPRINT`, which returns the index. `tests/test_docs.py` checks the three files and the index,
  that no section is undated, the stamp of every entry (its version is its section's, its date not after the section's),
  the same sections, types, counts and stamps in English and German, only the six types with the newest version first
  and a link per version, no "done" row in the roadmap and no roadmap row that is also a changelog entry, every relative
  link and anchor, a scrub of the three files (no address outside the documentation ranges, no e-mail address, no home
  folder, no token-like string) and a "Decisions" block in every component of the design.
- `2026-10-23 22:37 UTC` · `v3.1.0` **Offline mode: your opt-outs count only for what it may reach, a name with local and public addresses counts by
  its local ones, and your own crawl reads your own network** (DECISIONS round 26 Q3, Q4, Q5): the locality rule now
  answers two questions with one API, `check_reach` (may offline mode reach this: your opt-outs count) and
  `check_location` (where does this server stand: they never count). The privacy filter and a provider's local/cloud
  label ask the second, so a public host you count as local keeps the privacy filter on, online and offline, and
  keeps its label (`ProviderPreset.is_local` and `is_local_url` no longer take the settings); the explanations of
  **File > Settings... > Local network** and `llm-cli config local-network` say so. A name that resolves to
  addresses in your network and on the internet (a home router that also registers a global IPv6 address) now
  counts as local, and every connection goes only to its checked local addresses (the provider client, the
  local-only download adapter, git's `http.curloptResolve` pin, the crawler); it was refused before. A Deep Ingest
  you start in offline mode reads sites on this computer and in your own network (it refused before); an AI's
  `crawl_docs_site` stays refused in offline mode. With your own Headscale, an address of the VPN counts as local only
  for this computer or a device of the tailnet (`tailscale status --json`), and a private address behind a device's
  subnet route counts like a VPN address. A lookup and the VPN check are kept 5 minutes and dropped after a failed
  connection, instead of for the whole run.
- `2026-10-23 23:28 UTC` · `v3.1.0` **Offline mode: a build uses a model already on this computer, and Docker images come only from this computer or
  your own network** (consolidation chain hf-local; decisions round 25 Q1 and round 24 L-NET). A build you start in
  offline mode no longer downloads its model from Hugging Face (`BuildEngine._stage_model` did not read offline mode;
  only AI builds and the GUI's commit check refused): a model named by its Hugging Face id is used from its complete
  copy on this computer without any connection (`orchestrator/Core/local_models.py` `local_copy`: the Builder's own
  copy, which its staging now records as complete; a model folder the Hugging Face browser downloaded; the old
  huggingface_hub cache). Without one nothing starts and the models already here are listed with their labels (the
  models folder and the folders shared with the AI by their local-file references, the Builder's copies by their
  repository id, the inputs of earlier builds by their path): `llm-cli build start` prints them and takes a listed label
  or a local path with `--model` (asked at a terminal, exit code 2 without one), the Builder tab opens a choice with an
  open dialog for a model file or folder of your own, and an AI's build plan names the labels only (no host path; a
  file pick is yours only). `llm-cli ditto models download` (and Ditto > Local Models...) in offline mode refuses in
  plain words and names the Ditto models already here; a local model mirror still serves it. A package with Docker
  images (`deploy package --docker`, the Deployment tab, the AI's `create_deploy_package` with
  `include_docker_images`) no longer pulls a digest-pinned image from outside your own network in offline mode: it
  uses images already here or a registry the one locality rule counts as local, and otherwise stops naming the image
  (`utils/image_ref.offline_pull_refusal`). The target image is neither pulled nor built in offline mode: the image of
  the target built earlier from the same settings is used (new label `org.llm-framework.inputs`), and without one the
  build stops naming the base images and the step to take while online. The build's security scan in offline mode
  runs Trivy without any network and with the vulnerability database it already has (`--skip-db-update`,
  `--offline-scan`); its image is pulled only from a registry in your own network, otherwise the scan is skipped
  with a warning (before, the scan pulled `aquasec/trivy` from Docker Hub and Trivy downloaded its database).
  Tests: `tests/test_offline_models.py`, `tests/test_offline_images.py`, `tests/test_review_build_engine.py`.
- `2026-10-23 23:48 UTC` · `v3.1.0` **The framework's own OpenSSH settings file; OpenSSH reads neither `~/.ssh/config` nor `/etc/ssh/ssh_config`**
  (DECISIONS round 25 Q4 and Q5): every call of this computer's OpenSSH client (the fallback without paramiko) gets
  `-F` with `ssh_config` in the framework's state folder from the one command-line builder
  (`orchestrator/remote/transport.py` `openssh_options`), created owner-only on first use and holding only what you
  add: `llm-cli deploy ssh-config show|edit HOST --line ...|import HOST` and **Boards > Boards and Devices... > SSH
  settings...** show it, set a board's `Host` block, or copy one block from your own `~/.ssh/config` after showing it
  (that file is only read). The trust options stay first on the command line, where the first value wins, and lines
  that would change them, run a command, include a file or share connections are refused with the reason
  (`StrictHostKeyChecking`, `UserKnownHostsFile`, `GlobalKnownHostsFile`, `KnownHostsCommand`, `HostKeyAlias`,
  `HostName`, `LocalCommand`, `RemoteCommand`, `Include`, `Match`, `ControlMaster`, forwardings, ...); a `ProxyJump` or
  `ProxyCommand` is added only after you read what it does and say yes ("No" is the default), audited. A hand-edited
  file with a refused line stops every OpenSSH call with the reason (`local_problem`). No AI tool writes the file
  (`orchestrator/remote/ssh_config.py`; written only by `person_steps.save_ssh_block` for the CLI and the GUI);
  paramiko reads no settings file. A `%` in the framework's own key and `known_hosts` paths is written `%%` where
  OpenSSH expands tokens, so a state folder with `%` in its name works (a real `sshd` test); on Windows the `-F` path
  is given with forward slashes. Connection sharing (`ControlMaster`) stays off; it is on the roadmap as an option.
  Tests: `tests/test_ssh_config.py`, `tests/test_remote_system_ssh.py` (the pinned command lines now start with `-F`).
- `2026-10-23 23:48 UTC` · `v3.1.0` **The board's temperature during the checks on it, with a warning when it is hot** (DECISIONS round 23 item 16): a
  runtime deploy's checks, `llm-cli device runtime verify` and the benchmark (CLI, Boards window, Deployment tab, the
  AI's tools) read the board's thermal zones (temperature, type, trip points) and, on a Raspberry Pi, `vcgencmd` with
  one read-only command over the session they already have, before the short `llama-bench` run and after it; nothing
  is installed. The limit is the zone's own passive (else hot) trip point, else a careful default per SoC family (80 °C
  for a Raspberry Pi and other boards, 85 °C for Rockchip), never above the critical trip point less 10 °C; the
  firmware's soft temperature limit counts as hot. The check lines name the temperature; a hot board gets a warning in
  your language with what helps (a heatsink or a fan, fewer threads, a smaller quantization, a pause between runs) and
  a Mojo line: Mojo Oracle warns about a board that was hot at its last checks (recorded with the board's facts in
  `device_deploys.json`) until a cooler reading replaces it. A hot board fails no check. An AI gets the same facts in
  English (`temperature`, `temperature_note`) without a path or an account. No key of the hardware probe
  (`orchestrator/runtime/device/thermal.py`; `VerifyReport.heat`). Tests: `tests/test_board_heat.py`.
- `2026-10-23 23:48 UTC` · `v3.1.0` **A cancelled benchmark stops on the board too** (the wave-3 trust-followups open item): `llama-bench` runs on the
  board on its own; a Cancel in the Boards window or of an AI's job, Ctrl+C at a terminal and the
  follower's own time limit now stop exactly the process (its process group where the board has `setsid`) the
  framework started there, by the PID it recorded in the run's folder, never a program found by its name; when the
  Cancel closed the connection, one new login with the same host-key check stops it (`RemoteSession.for_cleanup`).
  Where the board has a working `timeout`, the board's own limit (900 s) wraps the run too, and the check names it
  when it ended the run. The clean-up of the run's folder stays as before. Tests: `tests/test_bench_stop.py`.
- `2026-10-23 23:48 UTC` · `v3.1.0` **One audit sink for the person's board rows** (DECISIONS round 25 Q3): the CLI, the GUI (the Deployment tab, its
  hardware scan, the Boards window) and the person's consents behind the service's steps write their device, trust
  and consent rows through `person_steps.device_audit` (the tool registry's log, else the per-user `audit.db`); the
  Boards window's `audit` and the CLI's consent row no longer open their own. A test pins that each surface writes
  each row exactly once (`tests/test_audit_sink.py`).
- `2026-10-23 23:48 UTC` · `v3.1.0` **A busy `deploy devices edit` in the Boards window says why, as the CLI does**: a change of a board's address or
  host key that another part of the framework holds (`LockTimeout`), or that a file on this computer stops, used to
  show "The step failed: <message>"; it now shows the CLI's plain reason (`local_problem`, "something on this
  computer") through the shared step error text, with nothing changed. The texts of the other steps stay as they were
  (pinned).
- `2026-10-23 23:48 UTC` · `v3.1.0` **The Docker branch of a package's `deploy.sh` hands the model to the inference container**: it installs the model
  as the native branch does (into `$MODEL_DIR`, by default `~/models`) and starts the container with that folder
  mounted read-only at `/models` and `MODEL_FILE` naming the model there; before, the container started without the
  model. The generated script is run in a test with a stand-in `docker` (`tests/test_deploy_docker_model.py`).
- `2026-10-29 15:08 UTC` · `v3.1.0` **The board's hardware probe drives every build; NPU formats only with its NPU** (DECISIONS round 23 items 5 and
  15): a build request names the board's probe by its id (`llm-cli build start --probe ID`, `BuildRequestIn.probe`;
  the Builder tab the probe it imported or scanned last, also for a recipe it builds as pinned,
  `BuildService.start_recipe(probe=...)`); without one the newest stored probe of a board of the
  request's SoC is used, and a probe of another board is refused: another board of the target, a board of another
  SoC (an RK3588S counts as an RK3588) or a system of another architecture than the target's (this PC's probe for a
  Rockchip board); the Builder tab uses its probe only for the target the probe matched. The build engine writes it into the build's folder
  and mounts it read-only into every build container, the importance-matrix job too, as `HARDWARE_CONFIG_FILE`: the
  config stage plans the build for that board, and the probe's sha256 is part of an identical build. Formats follow
  the probe through one verdict (`build_formats.format_refusal`): RKLLM and RKNN only when the probe shows the
  Rockchip NPU, else GGUF; RKLLM only for the RK3588, RK3576, RK3562 and RV1126B (not the RK3566 or RK3568) and only
  on an x86_64 build computer (RKLLM-Toolkit 1.3.1 ships Linux x86_64 wheels only); GGUF and RKNN (rknn-toolkit2
  2.3.2 has aarch64 wheels) also natively on an ARM build computer. The build service refuses with the reason after
  the target's own refusal; the Builder tab offers only the formats left.
- `2026-10-29 15:08 UTC` · `v3.1.0` **`GGML_AVX512` only with the whole AVX-512 set; the template plans x86 like the build recipe** (round 25 Q7):
  `GGML_AVX512` compiles for AVX-512 F, CD, VL, DQ and BW, but the probe's `SUPPORTS_AVX512` is AVX-512 F alone, so a
  Xeon Phi (Knights Landing) got `-mavx512bw` and illegal instructions. The build recipe and both config stages now
  use it only when the probe's CPU flags name every part, else the AVX2 plan (`recipe.AVX512_SET`,
  `avx512_missing`; recipe version 4). The template's config stage has Rockchip's x86 block byte for byte: explicit
  features for another PC, `-march=native` only for this build machine.
- `2026-10-29 15:08 UTC` · `v3.1.0` **Every `cmake` call of a module is checked, and a target's Dockerfile too**: `build.sh` checks the words of each
  `cmake` call of the modules (also in their setup, conversion and packaging commands) as the shell expanded them
  with `module_flag_problem --alone` of `target_module.sh` before cmake runs, and a refused call does not start
  cmake; before, only the module's `CMAKE_FLAGS` of the package's cross build were checked. `validate_target_folder`
  also reads a target's Dockerfile lines that the template's Dockerfiles do not have for a llama.cpp option that
  downloads. The verdicts stay in that one function. A project include (`CMAKE_PROJECT_INCLUDE`,
  `CMAKE_PROJECT_INCLUDE_BEFORE`, `CMAKE_PROJECT_<name>_INCLUDE`, `CMAKE_PROJECT_TOP_LEVEL_INCLUDES`) runs a script
  that can switch any option back on, so it is refused like `-C` and `-P` (`recipe.refused_option`, both verdicts).
- `2026-10-29 15:08 UTC` · `v3.1.0` **Rockchip image in two stages; the template's format helpers are real** (round 23 item 14): the `builder` stage
  builds llama.cpp's tools and the three venvs with every pin; the image gets the venvs and the source tree with its
  tools at the same paths and only what the build stages run (no `build-essential`, `python3-dev`, `python3-venv` or
  `curl`), keeping the `llmbuilder` user and the entrypoint; hadolint has one warning fewer. On an arm64 build computer
  the RKLLM venv is left out with the reason in `/opt/venv-rkllm/NOT_INSTALLED`, which `rkllm_module.sh` and the
  entrypoint's test report. The template's `rknn_module.sh` and `rkllm_module.sh` cloned toolkits at their newest
  commit and installed wheels during the build (which has no network) and converted nothing: they are now Rockchip's
  helpers, using the image's venv and refusing plainly without it.
- `2026-10-23 17:16 UTC` · `v3.1.0` **`install.sh` on every Linux family, through the family's own package manager** (DECISIONS round 24 PKG): the
  installer finds the family from `ID` and `ID_LIKE` in `/etc/os-release` with the hardware probe's table and installs
  what it needs (Python 3.11 to 3.14, `rsync`, Docker) with `apt`, `dnf`, `zypper`, `pacman` or `apk`, under the
  distribution's real package names; before, it knew only `apt` (Python and `rsync` too: `rsync` was installed with
  `apt-get` and a failure went unnoticed). Python: the distribution's `python3`, and where that is older than 3.11 (RHEL
  8/9 and their rebuilds, openSUSE Leap 15) a numbered one (`python3.13`, `python3.12`, ...; `python313`, `python312`,
  ...), with which the virtual environment is then made. Docker: Docker's apt repository as before; on Fedora, RHEL and
  CentOS Stream Docker's rpm repository, its key trusted only with the fingerprint Docker's install guide publishes
  (`060A 61C5 1B55 8A7F 742B 77AA C52F EB6B 621E 9F35`, pinned in the script with its source, checked like the apt key),
  the repository file naming only that checked key and dnf checking every package; where Docker publishes no repository,
  the distribution's own package (`moby-engine` on other Fedora-based systems, `docker` on Amazon Linux, openSUSE and
  SLES, where the Containers Module is switched on with `SUSEConnect`, and on Arch, Manjaro and EndeavourOS with
  `docker-buildx`; `docker` from Alpine's community repository). Rocky Linux, AlmaLinux, Oracle Linux and the other
  RHEL rebuilds get Docker CE from Docker's RHEL repository (DECISIONS round 26 Q1: the framework needs BuildKit, for
  example for Hailo), with the same key check and the RHEL version from `PLATFORM_ID` or `VERSION_ID`; Podman
  (`podman-docker` from the distribution, the person's `podman.socket` switched on) is only the fallback, when Docker
  publishes nothing for the architecture, the repository cannot be reached, the key does not match, dnf finds no
  package or the install fails. Before the key is trusted, `dnf install --assumeno --allowerasing` works out what dnf
  would remove or replace for Docker CE (`podman-docker`; on RHEL 8 rebuilds also `runc`, `podman`, `buildah`, ...),
  without changing anything: the installer names exactly these packages and asks the person on the terminal, and
  only a yes uses `--allowerasing`; a no (or no terminal) removes nothing, takes back the repository file and key it
  wrote, and gives the Podman fallback. Fedora, RHEL and CentOS Stream keep their way exactly (no trial run, no
  question, `dnf install -y` without `--allowerasing`; a conflict stops with the reason): round 26 decided the
  question and the fallback for the rebuilds only (consolidation review install-followups-v). The
  dry run names both ways and why; a second run where `docker` is Podman says how to switch. Checked for real in
  Rocky Linux 9.8 containers (Docker CE fresh; with `podman-docker` and a no; with `podman-docker` and a yes). SLES:
  when no Python 3.11 to 3.14 is there, the installer offers to switch on SUSE's Python 3 Module
  (`SUSEConnect -p sle-module-python3/<version>/<arch>`, the identifier checked in SUSE Customer Center's package
  search) and asks first; an unregistered system stops naming the registration step, and the Python hint names the
  module (dry-run line too). No package
  manager is told to skip a signature or to take a key on its own, and `pacman -Sy` (a partial upgrade) never runs. On
  Alpine the framework itself cannot run (onnxruntime, part of its pinned packages, has no musl build): the installer
  stops after Docker and points to the framework image, whose build script also needs `git` and GNU `tar` there
  (busybox's `tar` has no `--null`): the installer adds them with `apk add git tar` (only what is missing; the dry run
  names it). Any other family (Gentoo, Void, NixOS, ...) stops naming the
  family and the manual pages when something must be installed. A `docker-ce.repo` of the person's own in which
  Docker's repository is switched off is never replaced or removed (the installer stops before it changes anything and
  says how to switch it on), and where the `docker` command is Podman (a second run, or `podman-docker` installed
  earlier) the installer takes Podman's way instead of the `docker` group. New: `./install.sh --dry-run` shows the plan
  (family, package manager, packages, repositories, key fingerprints) without changing, downloading or calling
  anything and without root, exit code 0 or 1 as the installation would end. Tests run the installer against the
  `/etc/os-release` files of real distributions with fake package managers (`tests/test_install_distros.py`).
- `2026-10-23 22:32 UTC` · `v3.1.0` **Podman instead of Docker: the framework finds it and refuses what it cannot do** (DECISIONS round 26 Q1): when
  `DOCKER_HOST` is not set and no Docker daemon answers, the framework sets `DOCKER_HOST` to the person's Podman socket
  for itself and the programs it starts (`orchestrator/utils/docker_engine.py`, one helper for the build engine, the
  builder image, Ditto's container, the one Docker check and `scripts/build_container_image.sh`; a `DOCKER_HOST` the
  person set always wins). Under Podman (checked with Podman 5.8.2 and buildah 1.43.1): images are built through
  Podman's API rather than `docker buildx`; a GPU build with an NVIDIA GPU is refused before it is queued, because
  Podman's Docker-compatible interface drops Docker's GPU request without a word, and Ditto's container then says why
  it runs on the CPU; features that need BuildKit are refused with the reason and the way to Docker CE;
  `scripts/build_container_image.sh` builds with `DOCKER_BUILDKIT=0`. `llm-cli mojo` names the engine
  (`docker.daemon`) and lists Podman's limits (`docker.engine`). Every container of the framework is created through
  `create_container` (the build stages, the llama.cpp builder, Ditto's containers): under rootless Podman, which the
  fallback always is, one that runs as the person gets Podman's `keep-id`; without it such a container sees the
  person's folders as root's and every build stopped with "Permission denied" (found and checked against Podman
  5.8.9 rootless in the consolidation review install-followups-v).
- `2026-10-23 17:39 UTC` · `v3.1.0` **Hardware probe: version 3.1.0, wider Hailo detection** (consolidation chain probe-keys): `PROBE_TOOL_VERSION` is
  3.1.0 in all three probes (new keys are a schema change; HAILO_PLAN question 5, default recorded in round 23). The
  new keys are never required or `ON`/`OFF` keys, so a profile of probe 3.0.0 is read and stored as before (tested on
  every schema 2 fixture). `NPU_DEVICE` also accepts the Hailo master driver's nodes `/dev/h1x-<N>` and `/dev/h1x`,
  `NPU_MODEL` is filled for a Hailo (`hailo-8l`, `hailo-10h`), and `SUPPORTS_HAILO` is also `ON` for the USB
  Hailo-10H, an entry of a Hailo sysfs class and a device `hailortcli scan` lists; `HAILO_FAMILY` and `HAILO_ARCH`
  enter the performance key. A board whose probe sees a Hailo without `hailortcli` now gets `HAILO_FAMILY`,
  `HAILO_INTERFACE`, `HAILO_DEVICES` and the warning `not_determined:HAILO_ARCH`. The 60-second cap of the Hailo part
  in `hardware_probe.sh` no longer depends on `date +%s` (not POSIX; some busybox builds lack it): without it the
  whole seconds since boot from `/proc/uptime` are the clock. `hardware_probe.ps1` no longer names its Hailo loop
  variable `$arch`: PowerShell names ignore case, so it overwrote the script's `ARCH` value. `hardware_probe.sh` no longer uses
  `ls | grep` for the DRM nodes (ShellCheck clean at warning level).
- `2026-10-23 17:59 UTC` · `v3.1.0` **Offline mode reaches only this computer and your own network, and you decide what that includes** (DECISIONS
  round 24, L-NET and "L-NET final"): one network-locality rule (`orchestrator/utils/locality.py`) decides every
  reach-out of offline mode. A server name counts by every address it resolves to: loopback, private (RFC 1918,
  `fc00::/7`) and link-local addresses are local, a `.local`, `.lan`, `.fritz.box` or single-label name only when it
  resolves to such an address (it counted by its ending before, also with a public address; the privacy filter's
  local label follows the same rule), and a name with local and public addresses counts by its local ones (round 26,
  entry below). A Tailscale address
  (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) counts as local only when the VPN is Headscale in your own network with
  only own DERP relays, read with the `tailscale` command (`debug prefs`, `debug derp-map`); a public name such as a
  reverse proxy's is not local. You can count a VPN, a host or an address range as local in **File > Settings... >
  Local network** or with `llm-cli config local-network` (new), each with a plain explanation of what can then leave
  your network (settings `offline_local_vpns`, `offline_local_hosts`, `offline_local_ranges`); only you can: the AI
  settings change and MCP `update_settings` refuse them, and an AI can neither read nor set them. The lookup uses
  the system resolver only, with a 2 s timeout, once per run and never on the GUI thread (the AI settings dialog
  checks a server name or a Tailscale address in a worker before it saves, and a dialog closed meanwhile saves
  nothing), and the connection that follows goes only to the checked addresses, tried in turn as the system's own
  connection would (a link-local one with its zone; a `localhost` name to the loopback addresses, never where a DNS
  server points it), and through no proxy (the provider client of offline mode uses none from the environment;
  runtime downloads through a local-only adapter; git with `http.curloptResolve`, an empty `http.proxy` and no
  redirects). Ollama at an address outside your
  network is now refused in offline mode (its preset counted as local whatever its address), and the runtime's
  downloads and the pinned source come in offline mode only from your own network (a local mirror): `device runtime
  build`, `ditto runtime install` and `export-source`, the Boards window and Ditto > Local Runtime fetched from
  GitHub or Hugging Face before. The person's Deep Ingest reads only the own network in offline mode (round 26,
  entry below), and an AI's crawl reads nothing then.
  `build_local_runtime`, `deploy_device_runtime` and `add_pack_from_index` look at offline mode again after the
  person's answer, as `download_hf_file` does. Refusals name the host, why it is not local and the opt-out. The
  local/cloud labels of the bundled providers do not change. Every reader of the setting now asks the one offline
  check, `locality.offline_mode` (`hf_hub.offline` and the other getters delegate; behaviour and texts unchanged).
- `2026-10-23 17:18 UTC` · `v3.1.0` **`llm-cli build start --format` offers what the installed targets build** (DECISIONS round 23 item 7): instead of
  every format of the engine (`tensorrt`, `coreml`, ... that no target builds), `--format` takes a format the target
  builds, as its `target.yml` capabilities declare it, the list the Builder tab's format box offers
  (`build_formats.offered_formats`, one list for both; a target without capabilities takes every format but `hf`,
  unchecked, as before). `--help` and the shell completion list what each installed target builds, read from the
  targets when they are shown; a format the target does not build stops with exit code 2 in plain words naming what
  each installed target builds (`build_formats.format_problem`). The format is no longer case-sensitive, and the
  start panel names it as the target spells it, the format the build runs with (`-f RKLLM` shows `rkllm`), never
  read as markup.
- `2026-10-23 17:18 UTC` · `v3.1.0` **"Use the existing build" copies it into the chosen folder, and `llm-cli build start` asks too** (DECISIONS round
  23 item 9): `build start` checks for a finished build with 100 % the same options before anything runs, as the
  Builder tab does (`BuildService.earlier_builds`, the commit looked up once when it could be identical), and asks the
  same question at a terminal (use it, build it again, cancel; without a terminal it builds as given). "Use" now copies
  the earlier build with its golden ZIP into the folder the build would have gone to (`--output`, the prompt's answer
  or the output folder; in the GUI the folder dialog that opens at the output folder), where it was only named before:
  `artifact_output.plan_copy` and `copy_artifact`, the one disk rule before the copy, every copy checked by sha256,
  the earlier build left where it is. The identical build there already (the same manifest, and each of its model
  files there with its size) is not copied again; anything else under that name, also a folder whose model file was
  deleted or changed, is replaced only after you accepted the full list of what is replaced ("No" is the default; to
  the trash or for good; written to the audit log first) and only by a complete copy (one that left a link out keeps
  a name of its own).
- `2026-10-23 17:05 UTC` · `v3.1.0` **The Deployment tab's Docker table decides what the package carries** (DECISIONS round 23 item 6): the table in
  **Use Docker Containerization?** no longer shows mock-up choices that did nothing (Llama.cpp (CPU), RKLLM (NPU),
  TensorRT (GPU)); it lists the containers a package with Docker images carries for the chosen profile, from the one
  list `llm-cli deploy package --docker` packs (`deployment_manager.package_containers`): the inference engine with its
  image (setting `image_inference_runtime`) and the hardware access the profile's target gives it on the board. Every
  container is ticked, which packs exactly what `--docker` packs; an unticked container stays out of the package
  (`package_docker_config(docker, containers)`, through `make_package` and the one deploy step
  `person_steps.deploy_build`), and with none ticked **Deploy to Target** and **Create package only...** say why and
  do nothing. An empty `hardware:` block in a `target.yml` (or an empty `npu:` or `gpu:` block, e.g. with its lines
  commented out) gives no device access, as no block does, instead of keeping the tab from opening; the Builder tab's
  probe import reads such a block (and an empty `cpu:` block) the same way when it suggests a target, instead of
  stopping there.
- `2026-10-23 09:33 UTC` · `v3.1.0` **Knowledge always belongs to a topic or a target** (DECISIONS round 23 items 1 and 8): an installed knowledge pack
  lives in `community/knowledge/<topic or target>/<pack>/` (packs installed earlier stay where they are), and every
  excerpt it brings carries its topic or target. A knowledge pack names its topic or target in its manifest:
  `llm-cli zoo export knowledge` needs `--topic` or `--target` (a target of this computer), as do the export window
  (**Belongs to:**) and the tool `export_pack`; a pack made before that still verifies and installs, with its id as its
  topic, which the summary says and only your confirmation accepts (no standing approval covers it).
  `search_knowledge` and `list_knowledge_sources` take `topic`, `llm-cli knowledge sources --topic` and the new field
  and column **Topic or target** in **Tools > Knowledge Sources...** narrow the list. A loose knowledge file is listed
  (`llm-cli knowledge loose`, Mojo, **Sort loose knowledge...** in the Community Hub) and sorted into a topic or
  target folder (`llm-cli knowledge sort NAME --topic|--target`) through the checks of an installed pack, then
  imported. The knowledge snapshot of a target pack (`knowledge/initial_knowledge.json`) is checked like a knowledge
  pack and imported into the recipient's knowledge base under that target, with a snapshot first; the README the
  module wizard writes next to it says so. The module wizard's temporary knowledge export goes to a private scratch
  folder instead of `community/knowledge/`.
- `2026-10-23 08:53 UTC` · `v3.1.0` **One deploy step for a build and one flow for a board's runtime, for the CLI, the GUI and the AI** (consolidation
  chain c3-deploy-flow). `llmcf deploy run` and the Deployment tab's Deploy and Deploy existing package... now go
  through one core step, `person_steps.deploy_build`: the package, the deploy, then the board's trusted record. The
  tab now makes its package the way `llmcf deploy package` and Create package only... do (`make_package`): a Docker
  package carries the inference image's name, and Cancel also stops the packaging between its steps (no package
  stays). The reason a package cannot be made comes back with the result instead of the shared
  `DeploymentManager.last_package_error`, which no framework code reads any more (`create_deployment_package` keeps
  it for older callers). The tab's trusted record is audited as the CLI audits it: without the registry's audit log
  it now writes its row to the per-user audit log, where it wrote none before. The GUI's question about a new host
  key (before the tab's deploy and in the Hardware scan) writes the trust's row the same way, as `llmcf deploy run` and
  `llmcf scan --device` do. `llmcf device runtime deploy`, the
  Boards window's Deploy runtime... and the AI's `deploy_device_runtime` run one flow, `steps.deploy_runtime_flow`.
  It logs in, writes the board's trusted record (person only), reads the board and refuses root before any question,
  asks about the benchmark model with its consent (person only), installs, closes the session and records "tested
  on". Each caller hands over its login and its words as hooks. The AI's hooks hold no trusted record, no question
  and no early root refusal, so its results are unchanged; a test checks this. What the CLI prints and the windows
  show is otherwise unchanged.
- `2026-10-23 00:01 UTC` · `v3.1.0` **The GUI's Hugging Face browser downloads into the models folder's `hf/` folder, checked, like an AI's download**
  (maintainer decision A6; consolidation chain hf). New location: `<models folder>/hf/<owner>--<name>/<first 12
  characters of the commit>/<file>`, the same folder as an AI's `download_hf_file` (the Builder keeps staging whole
  models in `cache/models/hf/`); before,
  the browser saved through huggingface_hub into that library's cache (`<models folder>/models--<owner>--<name>/`,
  links to blobs named by their checksum). One code path: `ModelManager.download_file` now calls
  `orchestrator/Core/hf_hub.py` `download_for_person`, which looks up the commit first, fetches exactly that commit's
  file with the framework's fetcher (`HubFetcher`: resumable `.part` file, the Hub's size and sha256 checked before the
  file gets its name, the token only to huggingface.co, never an implicit `HF_TOKEN`) and applies the one disk rule
  first (`Core/disk_plan` reserve plus what running builds and downloads reserved; a refusal in the person's language)
  and holds its own reservation in the per-user ledger while it runs, so a build started meanwhile counts its space.
  The token the person types in the browser is used, else the one stored in the framework (`hf_token`), else none.
  The window shows the progress and has a Stop button (the part downloaded so far is kept; downloading again resumes
  it). Models in the old cache stay listed for AI callers and usable for builds. The new button **Take over old
  downloads...** (`orchestrator/Core/hf_takeover.py`, optional and one-time) copies them into the new folder, each
  file checked against the checksum it was downloaded with (the blob's name: the LFS sha256 or the git blob id; for
  a cache without links what the Hub lists for that commit), under the disk rule with its own reservation; a file
  that does not match, or another file of the same name in the new folder, stays where it is with its reason. Then it
  offers to remove the old copies only in the deletion dialog with the exact list (each model's cache folder whose
  every file was taken over, and the files of the cache's shared store only they use, with full path and size; a
  folder that holds a file the Take over cannot list is never offered; trash or for good; "No" is the default),
  audited first; nothing is deleted without that yes. Every step runs in a worker thread. The texts are in
  `orchestrator/utils/l10n_hf.py` (English and German). In offline mode the browser refuses before any request
  (see Fixed).
- `2026-10-22 16:50 UTC` · `v3.1.0` **A build is the four stage modules again, and `build.sh` only runs them** (maintainer decision U9; consolidation
  chain build4). `targets/Rockchip/modules/build.sh` is a stage runner over `source_module.sh` (the toolchain
  sources the image was built from), `config_module.sh` (normalizes the request, refuses what the target cannot
  build and writes the build plan), `convert_module.sh` (the model in full precision, the importance matrix, RKLLM
  and RKNN through their format helpers `rkllm_module.sh` and `rknn_module.sh`) and `target_module.sh`
  (quantization, `build_info.json`); the dispatcher's work moved into the stage it belongs to, and a GGUF, RKLLM or
  RKNN build produces the same files with the same names as before (`tests/test_build_stages.py` pins commands,
  outputs, `SHA256SUMS`, the environment variables read and the exit codes, before and after). New: after every
  stage that really finished, a resume marker in the build's work folder (`stages/<job>/`) with a digest of the
  stage's inputs (request, model revision and files, quantization, target, format, calibration data, the image's
  llama.cpp pin and the SHA-256 of every module and script) and the files it wrote; run again on the same work
  folder with the same inputs, finished stages are skipped, and a changed input or a missing file runs that stage
  and every later one again (`build.sh --fresh` ignores the markers, `--to STAGE` stops after a stage). Each stage
  prints a progress line (`=== Stage 3/4: convert ===`, `... done in 41 s`, `... resumed`, `... failed (exit 1)`),
  and the build status, the Builder and `llm-cli build status` show the stage ("Running modules: convert (3/4)")
  with its share of the progress bar. The engine passes the Hugging Face commit of the model as `MODEL_REVISION`.
  An optional `modules/rotate_module.sh` runs between convert and target, the slot for a rotation such as
  SpinQuant (no algorithm is shipped). Benchmarks stay on the board: no stage runs `benchmark_module.sh`, which
  says so in its header and points to `llm-cli device runtime verify --bench-model`. The 1.x functions of the
  modules are reachable again (tested with stub toolchains): a board's hardware profile (`build.sh --hardware-config FILE`) gives the
  CMake toolchain and the board package with cross-compiled `llama-cli` and `llama-server`, and
  `source_module.sh --clone-llama-cpp` (also the entrypoint mode `source --clone-llama-cpp`), started by hand,
  clones llama.cpp at a given commit; four latent faults of
  those 1.x paths were fixed on the way (an undefined cross-compile function, CMake flags passed as one quoted
  word, a profile check that ended the script under `set -e`, writes into the read-only cache folder). What they
  did and that a build no longer needs (building llama.cpp tools inside the build: the image has them; the old
  `convert.py` fallback: the pinned llama.cpp has no such file) is not run again. Each module also runs on its own
  with its 1.x options, as in the maintainer's design: `convert_module.sh --input DIR --output FILE --model-name NAME`
  writes the F16 GGUF to FILE, `target_module.sh --input FILE.gguf --quantization TYPE --model-name NAME` quantizes
  that GGUF without converting it again and keeps it (1.x defaults Q4_K_M and the file's name), and
  `config_module.sh --hardware-config FILE` writes the toolchain and the plan without a model; a path given as an
  option is made absolute. The config stage reads the file the hardware probe writes on the board (its `ARCH` and
  `CPU_MODEL`; the 1.x names `ARCHITECTURE_FULL` and `CPU_MODEL_NAME` still come first): a real probe file got the
  generic ARMv8-A flags and an x86_64 one the aarch64 cross compilers, because the stage read only the 1.x names.
  Now a Cortex-A55, A72 (RK3576) or A76 gets its CPU flags, an x86_64 profile its AVX2 and AVX512 GGML flags, and a
  system the image has no compiler for (a 32-bit OS on a board writes `ARCH=armv7`) is refused before anything is
  planned, instead of getting programs that cannot run there. The resume digest also covers the output folder and
  the hardware profile that the config stage reads when none is named, and a late stage line of a finished container no longer takes back the
  build's next step.
- `2026-10-23 00:37 UTC` · `v3.1.0` **The convert stage detects, checks, counts and tests what it converts, and leaves nothing half-written**
  (the maintainer's design of the 1.x convert module, decision U9 addendum; consolidation chain how2-convert).
  `scripts/model_check.py` (the Rockchip target and the template ship the same file; it needs only the image's
  Python and its standard library) detects the input: a Hugging Face folder with SafeTensors or PyTorch weights, a
  GGUF file (nothing is converted, the target stage quantizes it), an ONNX model, a single weights file or a folder
  of GGUF files. What the build's format cannot use is refused with the reason before anything slow runs: an ONNX
  model for GGUF ("llama.cpp has no converter from ONNX to GGUF") or RKLLM names the format rknn for the NPU (on the
  RK3566 the NPU runs voice and vision models, the LLM runs on the CPU as a GGUF), and a single weights file asks
  for the model's folder. Before, such an input reached the converter and failed there with a traceback. The input's
  integrity is checked (its JSON files, every SafeTensors header against the file size as the safetensors library
  checks it, the shards an index lists, PyTorch archives, a GGUF header; only the weights the converter reads, so
  a staged repository's leftover `pytorch_model.bin.index.json` beside its SafeTensors is no reason to refuse it),
  and so is every GGUF the converter wrote
  (every tensor's data inside the file, as llama.cpp's loader checks it); the 1.x warnings about a missing config,
  tokenizer or weights stay. A short functional test runs the written GGUF with `llama-completion`, the one-shot
  generator that was `llama-cli` in 1.x (`llama-cli` itself when an image has only that): 8 tokens, at most 120 s,
  offline. A run that ends with an error or crashes (llama.cpp aborts on a tensor it cannot use) fails the stage
  with the tool's last lines; a timeout or a run stopped from outside (the out-of-memory killer) is a note; it is
  skipped with a note when the image has neither tool, for an embedding model, for the NPU formats and
  in the `imatrix` job. The statistics (input and output size, ratio, duration of the conversion, the test's result;
  the input counts the weights the converter reads, not a second copy such as Mistral's `consolidated.safetensors`)
  go into the log and into `build_info.json` as `conversion` (numbers and words, no paths), which the build engine
  keeps when it completes the manifest. A file a converter replaces (the `--output` file of 1.x, an earlier result)
  is kept as `FILE.convert-backup` until the new one passed its checks; a failed or stopped stage removes what it
  wrote and puts the backups back, and after a killed run the next start of the stage does (an earlier file the
  person put back by hand stays). A second build into the same output folder finds the RKLLM or RKNN model the
  format helper wrote over the first one's file of the same name. The modules stay
  callable on their own with their 1.x options. The characterization tests compare `build_info.json` without the new
  key, and their stub toolchains write GGUF and SafeTensors files with valid headers (`tests/model_files.py`).
- `2026-10-22 16:50 UTC` · `v3.1.0` **New targets get working stage modules, and the entrypoint modes run the stage runner.** The template's
  `build.sh` is the same runner as Rockchip's (a test keeps them equal), and its four stage modules take the
  wizard's values: the setup commands run once in the source stage (what they export reaches the later stages, also
  on a resume), the quantization logic is the convert stage's case, the packaging commands run in the target stage,
  and both may still use `$QUANT` next to `$QUANT_TYPE`, as with the dispatcher.
  The module generator writes the template's convert and target modules instead of "Placeholder" scripts that ended
  with exit code 0; a stage without a template stops the build with the reason. `entrypoint.sh pipeline INPUT NAME
  [QUANTIZATION] [PROFILE]` and the modes `source`, `config`, `convert` and `target` run `build.sh` (the latter up
  to that stage, with the 1.x module options). The `modules:` blocks of both `target.yml` files, the wizard, the
  `create_target_module` tool, Ditto's module prompt, `llm-cli module create` (whose next steps no longer call the
  modules legacy files) and the guides name the stage each part belongs to; the roadmap item to remove the
  four-module scripts is done differently (they are the build).
- `2026-10-23 02:26 UTC` · `v3.1.0` **The config stage checks its plan, and the target stage makes the deploy-ready package of version 1.x again**
  (U9 addendum, DECISIONS round 16; Rockchip and the template). With a board's hardware profile the config stage
  finds the planned compilers in the image (nothing is planned when one is missing), builds and links a small C and
  C++ program with the planned flags (and runs them when they are for this machine), checks with the ELF header that
  they are for the board's system, lets `cmake -P` read the generated toolchain file and checks the plan
  `build_config.sh` in a clean shell before it keeps it. The build jobs are capped at the board's cores (`CPU_CORES`,
  or `CPU_CORES_LOGICAL` of the probe file), and a 32-bit ARM system is refused with the plain reason (the boards
  have 64-bit CPUs, the image only the aarch64 cross compilers; install the 64-bit system and probe again), also
  `ARCH=armv8l` of a 1.x probe file (a 32-bit system on a 64-bit CPU); the template refuses every profile that is not
  of the module's system, also one of a system it does not know. With a
  profile, the target stage of a GGUF build makes the package in `output/packages/`: the model, `llama-cli` and `llama-server`
  for the board, the toolchain file and the profile, a model card, `test_model.sh` (checks the files and that the
  machine runs the package's programs, a 32-bit Arm package also on a 64-bit Arm kernel, then a short answer from the
  model), `deploy.sh` (`USER@HOST [DEST]` over `ssh` and `scp` with `StrictHostKeyChecking=yes`, or
  on the board itself to `/opt/llm`), `MANIFEST.json` with every build detail and `SHA256SUMS`; the model is run once
  on this machine, the programs must be for the board, and every file must match its checksum before the package is
  kept. That run is the convert stage's functional test (`scripts/model_check.py test`, one implementation since the
  merge of both chains): a run that ends with an error or crashes on the model stops the package; a timeout, a stop
  from outside (the out-of-memory killer), a tool this machine's CPU cannot run or that does not start in the image,
  and an embedding model are notes the manifest names, as in the convert stage (before, the package had a test of its
  own that refused these, so a build could pass its convert stage and then fail its package for the same reason, and
  an embedding model got no package). Their cross build turns off every llama.cpp option that can download something, as
  every llama.cpp build of
  the framework does (`DOWNLOAD_OPTIONS_OFF` of `orchestrator/runtime/build/recipe.py`): at the pin
  `LLAMA_USE_PREBUILT_UI` is on by default and fetched `llama-server`'s web UI from a Hugging Face bucket in a
  container with network. `build.sh --package` (also `target_module.sh` started by hand and the entrypoint modes `target` and
  `pipeline`) makes it without a profile too, with the target's plain defaults, which the manifest names; the
  framework's own deployment stays `llm-cli deploy` over its SSH session. Every quantization method of the pinned
  `llama-quantize` is listed and accepted (new: `Q1_0`, `Q2_0` and the short names `Q3_K`, `Q4_K`, `Q5_K`), and
  `IQ2_M`, `IQ3_XXS`, `IQ3_XS` and `Q2_K_S`, which `llama-quantize` refuses without an importance matrix, are refused
  before converting unless IMatrix is on. `source_module.sh --clone-llama-cpp` clones the framework's pin when no
  `LLAMA_CPP_COMMIT` is given and checks the checkout; its `LLAMA_CPP_PIN_TAG` and `LLAMA_CPP_PIN_COMMIT` are pin
  places that `tests/test_build_recipe.py` checks. The `config repos` override of llama.cpp
  (`LLAMA_CPP_REPO_OVERRIDE`) now takes effect: the image fetches the pinned commit from it (build argument
  `LLAMA_CPP_REPO`) when it is an https address without a credential, and still stops unless it got exactly that
  commit; the docs said it had no effect.
- `2026-10-23 10:13 UTC` · `v3.1.0` **The board package keeps the framework's build rules: the disk rule, no downloads, the CPU of the PC it is for**
  (maintainer decisions of consolidation stage C, wave 3; chain build-safety; Rockchip and the template). The package
  copies the model next to the build's own, and by-hand and pipeline runs always package; now the target stage
  checks the one disk rule first (`scripts/board_package.py room`: the disk of `output/packages/` must hold what the
  package copies and keep 2 GiB or 2 % of it free, the reserve of `orchestrator/Core/disk_plan.py`), for the model
  before the functional test and the cross build and for every copied file just before the copy, and stops with the
  rule's words ("Not enough disk space for the package for the board: output disk: 1.2 GB needed, 2.5 GB free, 2.1
  GB must stay free. Free disk space ...") and no package. The image has no framework, so the script carries the
  rule's numbers and words (a test compares them); the per-user reservations are not visible in a build container. A
  module's CMake flags can no longer switch a llama.cpp download back on: the module generator refuses such
  `cmake_flags` (wizard, AI proposals, MCP, CLI; `-DLLAMA_USE_PREBUILT_UI=ON`, `-DLLAMA_CURL=ON`,
  `-DGGML_CUDA_CCCL_VERSION=...`, `-DFETCHCONTENT_FULLY_DISCONNECTED=OFF`, `-DLLAMA_SUBPROCESS=ON` and the like) with
  the build recipe's reason (`recipe.refused_option`, the rule `check_download_options` applies to every plan), the
  folder check of generated modules, exports and community packs refuses them in every script line that is not the
  template's (in every spelling the shell hands cmake: `-D NAME=VALUE`, `-DNAME:TYPE=VALUE`, quoted), and the
  package's cross build passes the framework's options after the module's flags, so they always win. What no later
  option turns off stops the package before anything slow, with the reason and no package: a module flag that sets
  an option of the recipe's `NEVER_SET` (it downloads whenever it is set) or makes CMake run a script (`-C`, an
  initial cache, or `-P`, which can set any option; the generator refuses these in `cmake_flags` as well). A quoted
  value that keeps a download off (`-DLLAMA_BUILD_TESTS="OFF"`) is no longer refused. Its `llama-server` is built
  without subprocesses now (`LLAMA_SUBPROCESS=OFF`, decision D22), as every server
  of the framework; before, the package's server had them. An x86_64 hardware profile is planned with
  `-march=native` only when it is of this build machine itself (the same CPU model and CPU flags as `/proc/cpuinfo`
  here); a profile of another PC gets the CPU features of its probe, as the build recipe plans a build for another
  machine (all 14 GGML x86 options explicit, their compiler flags on `-march=x86-64`, a feature the probe did not
  confirm left out, and for a 1.x probe file the features its CPU flags name). Before, every x86_64 profile got
  `-march=native`, so an AVX-512 build machine gave an AVX2-only PC programs it cannot run.
- `2026-10-17 17:14 UTC` · `v3.1.0` **The code is arranged in layers, and a test keeps them** (consolidation; maintainer decision of 2026-10-17: one
  repository, layers enforced by a test). `tests/test_layering.py` maps every module of `orchestrator/` to a layer
  (bottom, remote, kernel, domain, tool definitions, adapters), reads every import (also inside functions, under
  `TYPE_CHECKING` and lazy ones) and fails on an import that points upward, on one adapter importing another, on
  PySide6, click/rich, mcp/uvicorn or paramiko outside their home, and on a larger import cycle; today's exceptions
  stand with their reason in `tests/data/layering_allowlist.json`, which may only shrink. Shared helpers moved out
  of the tool modules to the layer that needs them, and the old names stay: path labels for AIs to
  `orchestrator/security/paths.py` (with `scrub_for_ai`, the one status scrub of AI-facing texts, and the
  home-folder patterns the GUI tool, the pack export and the privacy masks share), the private atomic writers to
  `orchestrator/utils/atomic.py`, the one-time notices to `orchestrator/utils/notice_store.py`, Docker image
  references to `orchestrator/utils/image_ref.py`, the SSRF-safe HTTP adapter to `orchestrator/security/http.py`, the
  audit key to `Core/vault_key.audit_key`, a pack's base images to `Core/community_pack.py`, the snapshot check before
  an index to `Core/knowledge_snapshots.py` and the audit of a saved module to `Core/module_generator.py`. Nothing
  changes for the person; the Core no longer imports tool definitions for these, and the largest import cycle shrank
  from 81 to 9 modules.
- `2026-10-22 14:37 UTC` · `v3.1.0` **One read-only host-key store and the person's trust writers** (consolidation chain c2-remote-trust):
  `orchestrator/remote/trust.py` (`TrustStore`: `status`, `status_of`, `check` with a `KeyCheck`,
  `read_presented_key`; the host-key errors moved there) is what the nine copies of "read the key and check it"
  use, each keeping only its policy (the person may trust, an AI is refused); `orchestrator/remote/pairing.py` holds
  the person's writers, and the three trust-on-first-use flows (`llm-cli deploy run`, `llm-cli scan --device`, the
  GUI's host-key question) are one decision with a prompt per channel, "No" by default as before. A key you trust
  now is written to the audit log in the same step (`host_key_trusted`), also when the login after it fails; before,
  only the board's record after a successful login was audited. The Deployment tab binds the deploy to the key you
  confirmed in its question and records the board with it, so one GUI deploy opens four connections to the board's
  SSH port instead of five. The old names in `orchestrator/utils/ssh_trust.py` keep working.
- `2026-10-17 16:08 UTC` · `v3.1.0` **The code that talks to a board starts its own package, `orchestrator/remote/`** (consolidation chain
  c2-remote-base; nothing changes for the person or an AI). The existing implementations moved there instead of
  getting a second copy: the host check of the remote probe, the store and known_hosts forms and the reading of a
  stored address (`address.py`), the reason codes of `orchestrator/runtime/device/reasons.py` with the code every
  caller records (`reasons.py`, `reach_code`; the old path is the same module), the one-line tail of a board's answer,
  the deploy's kept output lines and the three copies of a running deploy's text for the Deployment tab, `llm-cli
  deploy run` and the deploy tool (`text.py`). The copies of the user check (five), the host check (three), the
  reason-code fallback (six) and the reach record for Mojo (six, now `record_reach`) call the one implementation;
  the board steps' errors share `RemoteFailure`. paramiko is imported on first use only, so `orchestrator/utils/
  ssh_trust.py` and everything that imports it load no SSH library, and the deploy and the Builder tab ask with a
  real import whether SSH works (a broken installation counted as present there and as missing at the login).
- `2026-10-22 14:18 UTC` · `v3.1.0` **One board session for every contact with a board, `orchestrator/remote/session.py`** (consolidation chain
  c2-remote-session, finding 2 of the architecture audit). What only the deploy's board session could do (log in again
  with the host key checked as at the first login, wait for a board whose network dropped, resume an upload at what
  arrived, follow an installation that runs on the board on its own) is now `RemoteSession`, over one `Transport`
  interface (`orchestrator/remote/transport.py` `ParamikoTransport`), so the runtime deploy, the probe and the on-board
  benchmark can use the same steps next (chain c2-remote-callers). The deploy keeps its package and deploy.sh's job and
  is a thin adapter: its results, reasons and device states are the same, pinned by `tests/test_remote_session.py`
  against the session before the move. Its credentials are typed: `KeyOnly()` (every AI path) can never reach the key
  installation, only the person's `OneTimePassword` can pair. Every login reads and checks the board's host key
  through the one read-only trust store (`orchestrator/remote/trust.py` `TrustStore.check`, with the fingerprint the
  person was shown), like every other host-key check. `RemoteShell` and the probe read a board's answer with
  the transport's one bounded reader, `ssh_trust.connect` and `paramiko_quiet` delegate to the transport, and the
  reason codes read paramiko's error types through it (paramiko now has the transport as its only home in the remote
  layer). Every command of the deploy now has the time limit and the output cap the device runtime's commands had
  (120 s of silence, 1 MiB; a sha256 check on the board waits at least 900 s and longer for a large package), and the
  session keeps the connection alive every 15 s: a board that stops answering in the middle of a command no longer
  holds the deploy. Such a command counts as a network drop (paramiko's connection still stands then until TCP gives
  up): the deploy waits for the board as after any drop, logs in again with the host key checked again and continues
  the upload or follows the installation again, at most 20 times, instead of ending with "could not be followed".
- `2026-10-22 22:51 UTC` · `v3.1.0` **The OpenSSH fallback of the deploy is a second transport, not a second deploy** (decision A5, consolidation
  chain c2-remote-system, finding 3 of the architecture audit). Where paramiko cannot be imported, `deploy_package`
  now runs the same board session as with paramiko, over this computer's OpenSSH client
  (`orchestrator/remote/transport.py` `SystemSshTransport`): the deploy waits for a board whose network dropped (its
  wait time, the waiting events, Cancel), resumes the upload at the bytes whose sha256 on the board matches, starts
  `deploy.sh` once on the board on its own (detached, behind a marker) and follows it again after a drop, instead of
  an upload with `scp` and an installation tied to the SSH session that a drop killed. `deploy run` and the Deployment
  tab still read the board's host key with paramiko before they deploy, so without paramiko they stop there, as
  before. Every OpenSSH call comes from one builder (`openssh_argv`, `openssh_options`; `ssh_trust.system_ssh_options`
  is its old name): the framework key and known_hosts only, never a question and never a new key, as before, now also
  a login time limit (`ConnectTimeout`) and keepalive (`ServerAliveInterval`, `ServerAliveCountMax`), each command as
  one `sh -c` with a time limit (which also holds while the command's input is written) and an output cap, and
  `UserKnownHostsFile` in quotes: a state folder with a space in its path (a Windows account name such as "Jane Doe")
  split the framework's known_hosts into two files that do not exist, so every board's key counted as unknown.
  `GlobalKnownHostsFile` is the null device (`/dev/null`, `NUL` on Windows): OpenSSH no longer also reads this
  computer's system-wide known_hosts, so a key another program or an administrator put there never lets a board in
  that you did not confirm in the framework (`StrictHostKeyChecking=yes` stays). A password is no longer dropped
  silently: OpenSSH never asks for one, so the deploy logs in with the framework key and
  its result says the password was not used. A deploy bound to a fingerprint you were shown is still refused without
  paramiko, and so is a board session that must also check which board answers (its device ID). The framework's SSH key is made private to your account when it is created and before OpenSSH uses it
  (`orchestrator/utils/atomic.py` `owner_only`: group and other bits off on Linux and macOS, on Windows a protected ACL
  for your account alone, which OpenSSH for Windows requires of a private key). A key file OpenSSH still refuses
  (others may read it, or it is damaged) is reported as a problem on this computer (`local_problem`, never kept with
  the board), not as the board refusing the login, although OpenSSH then goes on without the key and ends with the
  board's "Permission denied". Tested against a real `sshd` as well (`tests/test_remote_system_ssh.py`).
- `2026-10-22 23:26 UTC` · `v3.1.0` **The runtime deploy, the checks on the board, the on-board benchmark and the probe go through the one board
  session** (consolidation chain c2-remote-callers). A Wi-Fi drop no longer ends them with "the SSH connection
  failed": `runtime/device/remote.py` `RemoteShell` is a facade over `RemoteSession`, so the session waits for the
  board (N5), logs in again with the host key checked again and goes on. The runtime tarball goes over SFTP into its
  private staging folder and continues at what arrived (a board without SFTP gets it on a command's stdin as before);
  the checks only read and run again; a step that must not run twice ends with a clear sentence once the board answers
  again, and the staging folder is still removed; a board that does not answer again ends every later step at once
  with its reason code. The short llama-bench run starts on the board on its own (`run_detached`) and is followed again
  after a drop, never started twice (round 16 G4: work keeps running while nobody watches; a board without nohup runs
  it attached as before); `RemoteSession.follow` follows a job started before. Its private folder
  (`<install folder>/.bench.XXXXXXXX`) goes afterwards, also after a failure or a Ctrl+C at the terminal; one that a
  Cancel in the Boards window or an AI's `cancel_job` left (the Cancel closes the connection, so the board cannot be
  reached then) goes with the board's next benchmark once no run can follow its job any more: nothing in it changed
  for longer than the benchmark's time limit (`sweep_bench_folders`, as the local benchmark removes the sandbox folders
  a crash left). The probe sends its read-only commands again after a drop. The AI path (`runtime/device/service.py`) opens a `KeyOnly` session (decision 2), with the same
  refusals and texts as before; the person's board steps (`person_steps.connect_stored`, the CLI's
  `device runtime deploy|verify|prune --on-device`, the Boards window) open one with the person's one-time password
  when one is typed, and `scan --device` hands its login to a session. The CLI and the Boards window say when the
  board's network dropped and when it answers again (EN/DE), each wait anew (also after an upload that started
  again from the beginning), an AI's job shows it by the board's label (`probe_remote_device`'s job too, which showed
  no wait at all: the tool now passes the session's events as the deploy and benchmark tools do). So does the Builder
  tab's **Remote Scan via SSH...** in its log, with a **Cancel scan** button while the scan runs, as the Boards window
  has one: it ends a wait for the board at once (not after 300 s) and nothing is stored (`scan_steps.scan_device` takes the caller's
  `CancelToken` and `on_event`). The CLI's wait line of `device runtime deploy|verify|prune --on-device` and of
  `scan --device` (which waited silently under its spinner) names the key that ends it, " (Ctrl+C)", as
  `deploy run`'s does; a Ctrl+C during the runtime deploy's wait now ends the
  command as a Ctrl+C (the staging folder's clean-up, which cannot reach the board then, no longer replaces it with
  "the connection to the device is closed"), and a board that does not answer again ends these commands (also
  `scan --device`) with the plain reason and its next step (`_print_reason`, as `deploy run` prints it), not with a
  sentence that only names
  the reason code. One
  `CancelToken` replaces the four "hold the client so Cancel can close it" copies, and closing a session ends its
  waits. The person's board steps, `scan --device` and Mojo ask `remote.transport.available()` whether SSH works, and
  the GUI's messages for a problem on this computer (local_problem) now also name what failed there (the system's
  words and the file's name, never its folder or an account). Kept as before (review of the chain): a command that
  stays silent while its connection still answers (`exit 0` on it) is busy on the board, not cut off, so its time
  limit ends it at once with "no answer from the device within N s", without a second login, a second run on the
  board or a line that the network dropped; the tarball is made under umask 077 before the SFTP upload writes into it,
  also the copy sent again after a damaged arrival, so it is the user's only as with `umask 077 && cat >`; a drop
  just as the deploy asks whether the board has SFTP waits for the board and uploads over SFTP instead of ending the
  deploy; and the board session's sentences an AI gets as external text name the board by its label, never its
  address (D0).
- `2026-10-17 15:50 UTC` · `v3.1.0` **One file hash and one disk watchdog** (architecture consolidation c1-hashing and c1-disk, maintainer rule R0: the
  existing implementation first). The 24 copies of the file sha256 (15 named helpers, 9 loops; the loop of
  `utils/network.py` left with that module, see below) run through the oldest one's module,
  `orchestrator/utils/helpers.py` (where `calculate_file_checksum` was), which gained one hashing loop with optional
  cancel and progress callbacks, hashing of an open handle or its first bytes (the deploy's resume check),
  copy-while-hashing (the recipe dataset, saved artifacts, the benchmark sandbox's model copy), a case-insensitive
  constant-time compare, a hash cache with a lock (the benchmark tool's had none), the `SHA256SUMS` writer and the
  `.sha256` sidecar. Every old name stays as a delegate with its error mode (an error, None or an empty text) and link
  rule; `tests/test_hashing.py` pins each of them and refuses a new hash loop beside the one implementation. The disk
  watchdog's texts and checks moved from the build engine into `orchestrator/Core/disk_plan.py` (`low_disk_text`,
  `disk_full_text`, `is_disk_full`, `low_disk_floor`, `free_space`, `nearest_existing` and `DiskWatchdog`, which the
  container watchdog and the staging watcher now are); the build engine keeps its old method names as delegates, the
  texts are unchanged and keep "No space left on device", which the self-healing pattern (now built from the same
  phrases) matches. Margins are unchanged.
- `2026-10-22 15:43 UTC` · `v3.1.0` **One disk rule for every free-space check** (architecture consolidation c3-disk-callers, maintainer decision A4).
  Every check measures through `orchestrator/Core/disk_plan.py` (`room`, the one check of a disk, which `check_disk`
  and `require` build on) and keeps the reserve free that builds keep, 2 GiB or 2 % of the disk (20 GiB or 10 % under
  a standing approval), beside what accepted calls of any process reserved in the ledger of the per-user folder: the
  local runtime's archive and model downloads and the community zoo's pack downloads (256 MiB before), the benchmark
  sandbox's model copy and the knowledge-base conversion (512 MiB before), the knowledge snapshot export (no margin
  before), Mojo's disk check (fixed 5 and 20 GB before; now an error below the reserve and a warning below twice the
  reserve, in decimal GB like every other size), the AI's disk fact and the checks of the deployment package and the
  runtime build tools. A download that a running build leaves no room for is refused with the numbers and the next
  step (try again once that build or download has finished, or free disk space); the Local Runtime, Local Models and
  Community Hub windows say so in English or German, and a reservation of a program that has ended never blocks (the
  ledger drops it). A deployment package holds its space in the ledger while it is made, as an AI's Hugging Face
  download does; when another call took the space while the person was asked, both are refused the same way, naming
  the download or the package (the text said "this build"). The self-healing diagnosis after a full disk still names the disks below the reserve without
  counting reservations, because it looks for the disk that ran full. The downloader in `orchestrator/utils/
  download.py` takes its caller's disk rule (`space`), so the bottom layer imports nothing above it. A running AI
  build records what of its reservation has landed on disk (`ReservationLedger.landed`, through `LandedMeter`: the
  growth of its staged download, its build folder and its staging folder, after each stage and in every round of its
  disk watchdogs), so other checks no longer count those bytes twice, once in the free space and once in the
  reservation.
- `2026-10-17 15:58 UTC` · `v3.1.0` **The GUI is an optional extra; the command line, the MCP server and the core run without Qt.** PySide6 is the
  extra `gui` in `pyproject.toml` (the hash-pinned `requirements-gui.txt`, an export of `uv.lock` like
  `requirements.txt`). The Linux and Windows installers install it as before; `sudo ./install.sh --headless`
  (`LCF_HEADLESS=1`) and the framework image without `--gui` leave out Qt and its system libraries (the headless image
  no longer installs libglib, and its build refuses an installation with Qt). The texts every process reads
  (`ui_text`, `tr`, `current_language`, the one table `TRANSLATIONS` in `orchestrator/utils/localization.py`) no longer
  import Qt; the window's language object (`LocalizationManager`, `get_instance`) lives in
  `orchestrator/gui/localization.py`, and the old import path still finds it. The GUI starts in
  `orchestrator/gui/app.py`; `orchestrator/main.py` stays the launchers' entry point and, without the extra, says how
  to install it instead of a traceback. The next-step catalogue reads its labels from the one text table instead of a
  second reader of the `l10n_*` modules. A CI job runs the Qt-free checks and the whole suite without PySide6.
- `2026-10-17 15:58 UTC` · `v3.1.0` **Qt's own buttons speak the window's language**: German dialogs showed Qt's standard buttons in English ("Close",
  "Cancel", "Yes", "No"). The window now installs Qt's own translation (`qtbase_de`, shipped with PySide6) with its
  language and swaps it when the language changes; an open dialog follows.
- `2026-10-17 15:58 UTC` · `v3.1.0` **The Builder shows the build container's CPU and memory**, under the progress bar while a stage container runs
  ("Build container: CPU 251 % · memory 2.0 GB of 8.0 GB"), and the build log warns once when the container uses 90 %
  of its memory limit (Docker's, or the WSL2 / Docker Desktop VM's), with what to do. The figures came from the Docker
  manager's older build path, which nothing called any more; they are now sampled by the one build path (the build
  engine, `BuildService.live_resources`, read from the orchestrator the build service works on, like the build's
  status), and the rest of that older path (start, monitor, stop, a healing trigger
  and six Qt signals nothing listened to) was removed because the one build path does all of it (decision round 12,
  item 6). The AI's build status is unchanged.
- `2026-10-17 16:17 UTC` · `v3.1.0` **One AI-settings change service for every channel** (`orchestrator/llm/settings_change.py`,
  `AISettingsChange`): the rules the AI settings dialog kept to itself (known providers in the fallback chain, no
  cloud provider in offline mode, a key required and not an admin key, the workspace ID's form, the server address,
  a model, the plain-http question for a key, the key stored for its address, the settings written, the knowledge
  base started and Ditto reloaded) now live in one Qt-free place. The dialog keeps only its message boxes and its
  question; sign-in and `llmcf ditto login` use the same address rules, and MCP `update_settings` the same
  tighten-only rule, which moved there from the tool module (`direction_problem`, still importable from
  `orchestrator.tools.tools_status`). `AISettingsChange.current()` and `with_setting()` are ready for a change of
  one setting at a time (the coming `llmcf config set`), and `problems_of_settings()` runs every rule for such a
  change. MCP `update_settings` uses it for the two AI settings an AI may change: offline mode with a cloud provider
  is now refused with the dialog's text (before, only the direction was checked, so an AI could switch offline mode
  on while a cloud provider was set), checked again when the person has answered; a problem the saved settings
  already have (such as a missing key) refuses nothing. The refusal names folders by their label and files by their
  name only: an unreadable provider list (`llm_providers.yml`) no longer shows the AI the config folder's path.
- `2026-10-17 15:44 UTC` · `v3.1.0` **One build-submission path for the CLI, the GUI and AI callers**: `llmcf build start` and the Builder tab's
  **Start Build** queue their build through `BuildService.start_build`, the path the MCP server and Ditto already
  used, instead of building their own orchestrator request each. The CLI and the GUI therefore check a request the
  same way before anything starts: a request without a model, a local model that does not exist (or does not look
  like a model), a calibration dataset that is not a file and a revision for a local model are refused with the
  reason (the CLI exits with code 2, the GUI shows it in a message box) instead of failing later in the build. What
  each window sets stays as it was: the CLI's priority, optimization and destination prompt, the GUI's priority,
  parallel builds and build folder, a target without capabilities built unchecked, and the person's own builds are
  never refused by the queue limit for AI callers. The CLI's follow loop reads the build through the same service.
  The Builder's check for an earlier build with the same options now also covers a target without capabilities
  (before, the service refused to compare such a request and the Builder only logged that).
- `2026-10-17 18:03 UTC` · `v3.1.0` **One implementation per job, the older one where it is adequate (round 12 of the decisions).** Every item of the
  unwired audit was checked against its history (`git log -S`) and is wired or removed with proof: the chat's link
  question is `bubbles.confirm_open_link` (the panel's unused copy is gone); the zero-cost credential check is
  `SignIn.check` (`LLMClient.check_credentials` is gone, its test runs through `SignIn.check`). Mojo's Python and Docker
  checks run on the framework's original checks in `orchestrator/utils/validation.py`: `validate_python_version` and
  `check_docker_environment`, which the build engine's start uses as well (findings and log lines unchanged; its buildx
  question runs through `helpers.execute_command`, so it closes stdin, opens no console window on Windows and, as the
  engine's original did, never stops the start, also not for output it cannot decode). Importing `validation.py` no
  longer checks the host. The module keeps the SSRF guard and the checks no newer code does, and loses what newer code
  does better: the system and Docker reports (Mojo), the disk check (the one disk rule), the config validator
  (`ConfigSchema`), the image and target name rules (the module generator's), the writability test
  (`ConfigManager.writable_dir`) and `validate_url`. Older helpers are used by their newer copies again:
  `helpers.execute_command` runs the hardware and toolchain probes, `helpers.safe_json_load` reads the local runtime's
  and the knowledge store's state files, `llama_server.managed_endpoint` finds the local server for the chat client,
  `LLMOrchestrator.list_workflows` (now a plain method) replaces reads of its private table in the main window,
  `TargetIn` and `TargetCapabilitiesOut` are the bases of the target tool's models (`TargetIn` lives in
  `schemas/targets.py` and the tool result `TargetCapabilitiesOut` in `tools/tools_targets.py`, so importing the target
  tools loads no tool hub and `schemas/` defines no tool result; `framework_tools` keeps the names), `grant_facts`
  decides a build plan's standing approval, `ChatHistory.in_state_dir` loads the chat panel's history, `PROFILE_FILE_NAME` names the
  script probe's file and `TUNINGS` guards what `build_info.json` records. Removed: the texts of the v2 GUI and seven
  newer duplicate texts (EN and DE; the older key stays), the menu name aliases `BUILD_MENU_NAMES` and
  `TOOLS_MENU_NAMES`, `utils/network.py` (NetworkGuard pinged 8.8.8.8 and waited without limit; `PinnedDownloader`
  resumes, retries within bounds and checks the pin), `scripts/quick_smoke_test.py` (it required paths that do not
  exist; CONTRIBUTING now names `llmcf mojo`, `make test` and the real-client smoke test), and the leftovers
  `check_command_exists`, `is_admin`, `canonical_key`, `_with_message`, `BoardOut`, `is_probe_id`, `SOURCE_KINDS`,
  `snapshot_listing`, `_write_profile_file`, `TokenStore.policy_of`, `deploy_artifact` (callers use `deploy_package`),
  `WIDTH_LABELS` and `REQUEST_TTL`. CONTRIBUTING (EN/DE) states the rule: use what exists before building it again.
- `2026-10-22 18:00 UTC` · `v3.1.0` **Community packs go to the community repository
  [Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base)**: the maintainer chose
  it (2026-10-22), and it is now the default of the setting `community_repo`, so "Open pull request" in the export
  window and `llm-cli zoo export --pr` hand a pack to it instead of stopping with "No community repository is
  configured". Its contribution workflow, its signed index and the maintainer's key are not published yet:
  `community_index_url` stays empty until the first signed index exists in that repository, so the zoo still installs
  pack files only. If you empty the setting yourself, nothing is handed over, as before. A test keeps the default and
  checks it against the setting's rule and the handoff's.
- `2026-10-17 10:54 UTC` · `v3.1.0` **The repository and the product identifiers carry the name LLM Cross-Compiler Framework.** The repository is now
  `Smilez1985/LLM_Cross-Compiler_Framework` (GitHub forwards the old address `Smilez1985/llm_conversion_framework`),
  and what was still named after the LLM Conversion Framework follows: the links in the crawler's user agent, in the
  error report and in the community repository's workflow; the install folders (Linux
  `/opt/llm-cross-compiler-framework`, Windows `C:\Program Files\LLM-Cross-Compiler-Framework` with the desktop
  shortcut **LLM-Cross-Compiler-Framework**, and the program folder of the framework image); the Python distribution
  `llm-cross-compiler-framework`; the Windows scripts `scripts/Uninstall-LLM-Cross-Compiler-Framework.bat` and
  `scripts/start-llm_cross_compiler_framework.bat`; the crawler's product token `LLM-Cross-Compiler-Framework-Bot`
  (robots.txt groups, `X-Robots-Tag` scopes and meta tags written for `LLM-Conversion-Framework-Bot` still apply, so
  an opt-out a site made earlier holds); and the keyring service `llm-cross-compiler-framework` (vault keys, the audit
  key and the revision anchors still under `llm-conversion-framework` are read and copied to the new name on first
  use, the old entries are never removed, and the vault's stored check value keeps its text, so every existing vault
  opens). How an existing installation moves: an installation in the old folder is never changed or deleted.
  `install.sh` copies the files of yours from its `configs/` that the new installation does not have (`config.yml`, a
  hand-written `framework.yml`, and `secrets.store`, the credential vault of a version before 3.0.0, which the
  framework then takes over; never over an existing file, never through a link), says what it did not take over
  (builds, cache, models, your own targets and packs), that AI apps connected over MCP start the old installation until
  they are set up from the new one, and how to remove the old folder after looking inside. `install.bat` names the old
  folder, the AI apps and the uninstaller, which now finds both installations, lists for each one what it removes and
  asks first (no answer: nothing is removed). `make` keeps a container built before
  the rename while it holds files of yours in the old program folder. See
  [README: An installation from before the rename](README.md#an-installation-from-before-the-rename).
- `2026-10-16 15:21 UTC` · `v3.1.0` **Faster start: heavy parts load when they are first used** (maintainer decision RG1, lazy loading): `llm-cli`, the
  MCP server and the GUI imported the whole framework at start. Now a CLI command imports the framework, the build
  engine and the managers only when it uses them; the framework imports its managers when it boots, and the knowledge
  base only when it is switched on; Qt is loaded outside the GUI only when a signal of the Docker manager is used; the
  crawler (with its HTML and PDF parsers), numpy, Mojo's checks, the provider layer of `orchestrator.llm` and the parts
  of `orchestrator.runtime` load with their first use, also when the knowledge base is on; `--help` loads no rich. The
  host check that ran when the URL checks were imported took a one-second CPU sample and looked up two programs, results
  it never showed; it now runs only the checks whose errors it reports (disk, memory, Python, platform), so its disk
  warning appears when the URL checks are first used, not at every start. Measured on the same machine with a temporary
  home, before and after, in alternating runs (median of 5): `llm-cli --help` 3.15 s and 140 MB to 0.25 s and 27 MB;
  `llm-cli mcp serve` until it answers `initialize` 4.77 s and 191 MB to 2.67 s and 118 MB (2.69 s and 118 MB with the
  knowledge base on); the GUI main window 3.97 s and 210 MB to 2.66 s and 177 MB. The boot still checks Docker as
  before, so `mcp serve` and the GUI still load the Docker SDK. Commands, results and messages are unchanged. A
  framework part that cannot be imported (a broken installation) now ends the boot with its cause, like other boot
  errors: the CLI prints "Failed to initialize framework: ..." and exits with 1 instead of a traceback, and `--help`
  still works. Tests start the CLI, `mcp serve` (also with the knowledge base on) and the GUI in a fresh interpreter and
  check that the crawler, the embedding libraries, the provider SDKs, Qt, Mojo's checks (its board check too), the
  knowledge base's storage and the private bridge to the open window are not loaded and that no licence text of the
  knowledge packs is read; the MCP server loads the bridge with the first call of its `gui` tool, and the GUI starts it
  with its window.
- `2026-10-16 15:23 UTC` · `v3.1.0` **The local knowledge base is stored in Qdrant Edge** (DECISIONS RG5): the Rust engine of Qdrant runs in the
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
- `2026-10-16 15:23 UTC` · `v3.1.0` **Knowledge snapshots name the storage they came from (format 2)**: a snapshot stays a portable file, the same for
  every storage. Snapshots of format 1, written by earlier versions, restore into Qdrant Edge and are converted that
  way; a newer format, a vector of the wrong dimension or a broken keyword vector is refused before anything changes.
- `2026-10-16 09:39 UTC` · `v3.1.0` **The identical-build check no longer blocks the builder window** (eighth round, N11): before a build, the check asked
  Hugging Face for the model's current commit on the window's own thread, so the window stopped responding on a slow
  network (up to 10 seconds). The look-up now runs in the background; Start waits meanwhile and the form stays usable.
  The build submits exactly the values that were checked.
- `2026-10-16 09:39 UTC` · `v3.1.0` **Builds in the old default folder keep their id and packages are found there** (eighth round, answer 3): since the
  output folder setting, the framework's build folder is no longer the output folder, so a build there got a new id, and
  the AI tool `deploy_to_device` no longer found the deploy packages in it by their id. The id a build had before still
  reaches it (feed rows, pending approvals and AI callers that kept it), unless another build has that id now. Packages
  are looked up in the output folder first, then in the build folder and in earlier output folders you had set.
- `2026-10-16 09:39 UTC` · `v3.1.0` **`get_status` names the build folder's disk** (eighth round, answer 3): the disk fact named the cache and the output
  folder only, not the build folder where a build is written first. Now it names all three; folders on one disk are
  named together, by label only.
- `2026-10-16 09:39 UTC` · `v3.1.0` **The tools that store text say when the search model is downloaded first** (next-fixes 119): the situation warning
  `embedding_model_download` was declared but never shown. Now `crawl_docs_site`, `index_framework_docs` and a knowledge
  pack from `add_pack_from_index` check, without downloading or loading anything, whether the knowledge base's models
  are on this computer. Their question says so, with the size and the warning when a download is needed or cannot be
  ruled out. An MCP prompt with knowledge excerpts never starts the download: like `search_knowledge` it reads with the
  models on this computer only and says when they are missing. Ditto's automatic diagnosis of a failed build, which
  nobody is asked about, reads the same way.
- `2026-10-16 09:39 UTC` · `v3.1.0` **Repair hints for AI callers name Mojo** (round 7 item 11'): refusals and errors that AI callers read sent them to
  `llm-cli doctor`. They now name the tool `run_mojo_oracle` (or `llmcf mojo` in a terminal). The note about missing
  builder images keeps `llm-cli doctor --fix --tier 2`, which builds them.
- `2026-10-16 15:09 UTC` · `v3.1.0` **Ditto's old runtime mode goes completely** (W2 of 2026-10-16, round 7 D): after a switch between bare metal and
  Docker, only the old mode's build folders, images and containers were listed, and the build folders went to the trash.
  Now every object of the old mode is listed, also its entries in `mode.json` and `active.json` and the log of the image
  builds, and the list is deleted for good, not into the trash, so nothing of the old mode stays. It is offered only
  after the new mode works, and the removal refuses while it does not. One question confirms the whole list ("No" by
  default).
- `2026-10-16 09:39 UTC` · `v3.1.0` **The address checks agree on IPv6 in brackets**: `deploy run --host [fe80::1]` passed the CLI's check, but the deploy
  refused the address. Now every place that takes a board's address (deploy, `scan --device`, `deploy devices add`, the
  GUI's probe and Deployment window) refuses an IPv6 address in brackets before anything is sent. Write it without
  brackets (`fe80::1`); the port has its own option.
- `2026-10-16 09:39 UTC` · `v3.1.0` **The per-user folder is made private when it is created** (next-fixes 118): every read of the settings set
  `~/.llm-framework` to mode 0700 without a word, so Mojo's `state.permissions` finding almost never showed. Now the
  folder gets 0700 when the framework creates it; an existing folder keeps its mode, Mojo Oracle reports one that
  others can read, and Mojo Miracle repairs it after your yes.
- `2026-10-16 08:50 UTC` · `v3.1.0` **A board deployment keeps every older runtime build** (round 5 f): `llm-cli device runtime deploy` and the AI
  tool `deploy_device_runtime` removed all older runtime builds on the board except the previous one, without a
  question. Now every older build stays. The result says how many stayed and their size, and
  `llm-cli device runtime prune DEVICE --on-device` lists them for you: full path on the board, folder name, size and
  what each is, nothing ticked, "No" by default. Only the builds you tick are removed, for good (a board has no
  trash), after an audit entry with the folder names only. The active build and one a running program uses are never
  listed. An AI only reads how many builds stayed and that only you remove them. Deploying a build again that is
  already on the board keeps it there, also when its checks fail.
- `2026-10-16 01:03 UTC` · `v3.1.0` **The output folder is `~/llmcf/output`; the old output folder is the framework's build folder** (eighth round,
  answer 3): the setting `output_dir` now names the folder finished models are saved to (default `~/llmcf/output`,
  before the installation's or the per-user folder's `output/`). That old folder is the new setting `build_dir`: GUI
  builds wait there until they are saved, and every earlier build stays there and stays listed. CLI builds without
  `--output` no longer add a `build_<date>_<time>` folder (a later build goes next to an earlier one instead). The
  container image sets `LLM_FRAMEWORK_BUILD_DIR=/data/builds`.
- `2026-10-15 21:27 UTC` · `v3.1.0` **The builder no longer deletes images** (released in 3.0.0; round 7 item 12): every build ended by removing its
  image and pruning the framework's dangling images. Now the build's image is named
  `llm-framework/<target>:last-build` (the next build of that target reuses it) and only its per-build name is
  dropped, after the new name is seen on the image. The image that had that name before stays without a name and is
  listed for you to delete (Tools > Framework Build Images..., `llm-cli doctor --fix --tier 2`). Nothing in the
  framework prunes Docker objects; Docker images are deleted only when you tick them.
- `2026-10-15 21:06 UTC` · `v3.1.0` **No default names a registry image any more** (round 5 h, sixth round): no framework image is published, so the
  defaults name local images. `image_inference_runtime` is `llm-framework/inference:local`; a deployment package
  takes it only once you built it on this computer, as for every image without a digest. `docker_registry` is empty
  (nothing reads it). The experimental benchmark window uses `llm-framework/builder:local` and stops with a message
  when that image is not here, instead of letting Docker pull it. The `docker` block of `targets/_template/target.yml`
  and `targets/Rockchip/target.yml` names a local image without a registry (`registry: ""`); comments say that nothing
  reads `registry` or `tags`. `prebuilt_llama.KNOWN_PREBUILT_IMAGES` keeps the former `ghcr.io` names as a detection
  list.
- `2026-10-15 20:23 UTC` · `v3.1.0` **uv replaces Poetry for the dependency lock** (sixth round, decision 14): `pyproject.toml` uses the standard
  `[project]` table (the dev tools in `[dependency-groups]`), `uv.lock` replaces `poetry.lock`, and `requirements.txt`
  and `requirements-dev.txt` are hash-pinned `uv export` outputs whose first lines name the command. No version
  changed: all 121 pins are those of the former `poetry.lock`; the exports only leave out the hashes of wheels for
  Python versions outside 3.11 to 3.14. The installers keep installing the hash-pinned `requirements.txt` of the
  checked-out framework and now insist on the hashes (`--require-hashes`, no fallback to unpinned packages);
  `install.bat` stops when pip fails instead of going on. Dependabot updates `uv.lock`, the new CI job "Lock files"
  checks that `uv.lock` and both exports match, and `make test` and the development setup use uv (`uv sync`).
- `2026-10-12 17:38 UTC` · `v3.1.0` **Questions show a short summary and the warning classes** (delegation design 4.5, LAY-L2): a question in the
  framework's own window (Ditto's dialog, the CLI prompt, the approval window) starts, after its first line, with 3 to 6
  lines in the person's language (English or German): what happens with speaking names, how much it downloads and how
  much disk it needs, "Needs your decision because: ..." with every security or legal warning, the situation warnings
  to weigh, a safer option and who may decide. The summary is built only from computed numbers, the warnings and
  speaking names, never from repository, log or AI text. Every question, an MCP form included, lists "Situation:" and
  "Needs you (security / legal):" after the facts it checked.
- `2026-10-12 09:20 UTC` · `v3.1.0` **Refused MCP calls carry their reason code as structured content** (`{"error": {"code", "field", "blocker"}}`,
  framework facts only, the text unchanged; `blocker` names the facts catalogue's hard fact that refused the call
  before any question, such as `disk_full`). A client reads the code and field from it instead of guessing them from
  the words; an unknown code is sent as `failed`, and a field that is not a plain argument name is left out.
- `2026-10-12 09:20 UTC` · `v3.1.0` **`ArtifactStore` (`delete_artifact`) uses only public `BuildService` methods** (`output_folder`,
  `artifact_entries`, `busy_output_folders`, `ArtifactStore.for_service`).
- `2026-10-12 05:51 UTC` · `v3.1.0` **The Deep Ingest notice records who read it again**: its state file stores where it was acknowledged (`via`:
  `gui` or `cli`), and reading it is written to the audit log (actor `gui` or `cli`). Only a person acknowledges it;
  `CrawlNotice.acknowledge` refuses any other channel.
- `2026-10-12 06:09 UTC` · `v3.1.0` **A target pack never takes another target's name**: installing a target pack whose id (or declared target id)
  equals an existing target's ignoring case and separators is refused, in the Community Hub, the CLI and over MCP.
- `2026-10-12 07:38 UTC` · `v3.1.0` **Builds and downloads share one disk reservation; staging through the framework's fetcher** (MCP phase T round 1
  leftovers): `download_hf_file` reserves its disk space in the cross-process ledger builds use. A build stages its
  Hugging Face model through the framework's fetcher (resumable `.part` files, each file checked against the Hub's
  size and sha256, stopped within seconds by a cancel) instead of `hf_hub_download`, and refuses when the commit
  cannot be resolved. A disk watchdog stops staging and the conversion containers when the cache disk falls below
  half the reserve. `start_build` offers a standing approval within the design's scope (shipped or verified target,
  safetensors or GGUF, ungated model and base model, permissive licence, official base image, size class) only when
  the disk keeps the larger reserve. `report_cannot_do` counts only the current session's calls, and Hugging Face
  look-ups of `check_build_request` count toward the 30 per hour rate.
- `2026-10-12 05:23 UTC` · `v3.1.0` **One Hugging Face classification for builds and downloads**: `check_build_request` and `start_build` now read a model's licence, gating and files with the same rules as `get_hf_model_info` and `download_hf_file` (licences such as GPL, DeepSeek or Qwen research were "unknown" on the build path and "restrictive" on the download path), and both keep the same disk reserve.
- `2026-10-12 00:11 UTC` · `v3.1.0` **MCP over HTTP**: one token holds at most 4 of the 8 sessions, so one client cannot lock the others out; a session
  closes after 10 minutes without a request (the SDK's default is 30); a request still open when its token is revoked
  or rotated (a wait, an event stream, a listen stream) is closed at once, and so are that token's sessions: a
  session belongs to the token value that opened it, so after a rotation the client opens a new one and the old
  ones hold neither its share nor the server's slots.
- `2026-10-11 23:52 UTC` · `v3.1.0` **Ditto's task texts** (chat with tools, healing analysis, module proposal, build recommendation) live in one
  module, `orchestrator/llm/task_prompts.py`, shared with the MCP prompts, and each carries the honest-"cannot" rule:
  name the specific reason, never claim a result you did not get, and untested hardware is no reason to refuse. The
  module task no longer limits base images to `debian:*` and `ubuntu:*`: it prefers a Docker Official Image with the
  vendor's packages and gives its reason for any other image. A module Ditto proposed says in its description that it
  is untested on real hardware and names the assumptions, also one created in the module wizard from Ditto's
  proposal.
- `2026-10-10 21:50 UTC` · `v3.1.0` **Ditto runs on the new provider layer** (official anthropic and openai SDKs) instead of LiteLLM, and the
  built-in Transformers model for offline mode is gone (offline mode uses local providers: Ollama, the
  managed llama-server, a server on your network). Healing analyses, module proposals and build
  recommendations are validated structured answers; an invalid answer is an error and is never applied.
- `2026-10-10 21:50 UTC` · `v3.1.0` **Chat history** is append-only and kept per user; a long conversation is summarized into a new one.
  Histories of earlier versions are migrated.
- `2026-10-11 00:21 UTC` · `v3.1.0` Provider errors are explained in plain language, e.g. a Claude API key that needs a workspace ID, a
  workspace the key cannot use, a key that is wrong, revoked or expired, or an `ant auth login` profile that
  needs a new sign-in.
- `2026-10-11 03:18 UTC` · `v3.1.0` More provider errors say what happened and what to do next, with a code for the GUI and the CLI: no credits
  or a spend limit in the Claude Console, a rejected OpenAI key or a used-up OpenAI quota, a Gemini key that
  Google refuses, an OpenRouter key at its credit limit, a local server that is not running or needs a key,
  Ollama without its ollama.com sign-in, a locked keyring and an unreadable vault. A Claude spend cap is no
  longer retried like a rate limit.
- `2026-10-11 00:21 UTC` · `v3.1.0` **Claude API sign-in**: a stored API key is always used first. An `ant auth login` profile is used only when
  you choose it (`ai_auth_anthropic: ant_profile`); before, a profile on disk silently took the place of the
  stored key, and with it the account that is billed. The earlier name `oauth` still means the profile, in the
  setting and in a provider catalog in your config directory.
- `2026-10-10 23:50 UTC` · `v3.1.0` Ditto's prompts: build failures are attributed to the build machine or the device correctly, build
  recommendations know which inputs each format accepts (an ONNX model is not sent to GGUF), questions about
  targets and builds are answered from the tools, and module proposals use compiler flags rather than the
  CPU feature list.
- `2026-10-10 23:50 UTC` · `v3.1.0` Local model: effort "low" turns thinking off (Qwen3.5 on llama-server otherwise thinks at full length, which
  takes minutes on a CPU); "medium" and up keep it.
- `2026-10-10 22:57 UTC` · `v3.1.0` Dependencies: `anthropic` 1.13.0, `openai` 3.28.0 (was 2.54.0), `fastembed` 0.8.1 (local embeddings, ONNX on
  the CPU) and `pyrage` 1.4.0 (age encryption of privately shared community packs) are pinned exactly; pip-audit
  reports no known vulnerabilities.
- `2026-10-11 02:31 UTC` · `v3.1.0` **Ditto's knowledge excerpts fit the context**: they take at most a third of `chat_context_limit`; an excerpt
  that does not fit is skipped and a too long first one is shortened at a line (an open code block is closed).
- `2026-10-11 02:03 UTC` · `v3.1.0` **Deep Ingest outcome**: it said "Deep Ingest complete" even when every URL failed. It now reports success
  (everything stored), partial (some pages stored; others failed, were reserved by the site or the crawl was
  stopped) or failure (nothing stored), each with its own message, only after the last write has returned.
  Pages are stored as soon as they are read, so a stopped or interrupted crawl keeps what it already stored,
  and pages that could not be fetched are listed in the crawl log.
- `2026-10-11 01:30 UTC` · `v3.1.0` The crawler's User-Agent reports the real framework version (it said 1.6.0).
- `2026-10-11 01:30 UTC` · `v3.1.0` Deep Ingest: a link that redirects to another host is skipped like any other external link; only a
  redirect of the start URL can move the crawl to another host.
- `2026-10-11 01:54 UTC` · `v3.1.0` Knowledge exports, and with them the knowledge snapshot of new target modules, are scrubbed: home folders,
  IP and MAC addresses, e-mail addresses, JWTs, the host name and the user name are removed, besides API keys.
  Image digests (`name@sha256:...`) and the paths of default accounts such as `/root` or `/home/runner` stay
  as they are, so a scrubbed target still builds.
- `2026-10-11 02:42 UTC` · `v3.1.0` The knowledge snapshot of a new target module is opt-in (a check box on the wizard's last page) and holds only
  the pages crawled for that target in the wizard, all of them: Finish stays disabled while a crawl runs. Before,
  the whole local knowledge base went into every module. A `README.md` next to it says what it holds and that the
  framework does not import it; when nothing could be attached, the wizard says so.
- `2026-10-11 02:42 UTC` · `v3.1.0` Target modules created with the wizard, `llm-cli module create` or Ditto get a `capabilities:` block (support
  level community: their boards, and GGUF with the types their build script handles), so the Builder, the CLI
  and AI clients check their build settings. Generation fails if a template placeholder is left anywhere except in
  the knowledge snapshot, whose crawled pages may contain text such as `YYYY-MM-DD`.
- `2026-10-11 00:29 UTC` · `v3.1.0` **Credential vault format v2**: every entry carries a key id and the vault key can be rotated (every entry
  is re-encrypted, the old key is removed; an interrupted rotation is completed at the next start). Each
  vault keeps its key in its own keyring entry, so rotating it or changing its storage never touches another
  vault (for example of another `LLM_FRAMEWORK_HOME` folder); a store of an earlier version moves to its own
  entry at its first rotation. Provider keys are stored as credential records under
  `cred/<provider>/api_key`; stores and key names of earlier versions are read as before and converted on
  the next change.
- `2026-10-11 00:29 UTC` · `v3.1.0` Reading a secret is written to the secrets audit log once per process and name, not on every access.
- `2026-10-11 04:36 UTC` · `v3.1.0` Commands that need the credential vault exit with code 5 when it cannot be used (no usable keyring, no
  passphrase, a damaged vault) instead of 1, and name the choices, including
  `llm-cli ditto auth storage --use ...`.
- `2026-10-11 04:36 UTC` · `v3.1.0` Error messages and hints name `llm-cli ditto login <provider>` instead of `llm-cli secrets set <key name>`;
  `llm-cli module generate-ai` uses the stored sign-in and never asks for a key itself.
- `2026-10-11 20:08 UTC` · `v3.1.0` `scripts/hardware_probe.sh` and `scripts/hardware_probe.ps1` write schema 2, the same keys as the Python probe
  (tests run the `.sh` and the `.py` on the same fixture machines). Both are non-interactive by default: the package
  install prompt of the `.sh` needs `--install-tools` (it now offers `vulkan-tools`, the only helper the probe uses),
  and the `.ps1` restarts elevated only with `-Elevate`. The `.sh` runs in any POSIX shell (dash, BusyBox ash), and
  `llm-cli scan --script` and the GUI's scan of this PC run it with `sh`, so hosts without bash work; it reads
  `/proc` and `/sys` directly instead of `lspci` and `lsusb`, and writes all GPUs; the `.ps1` reads the usable memory
  instead of the sum of the memory slots.
- `2026-10-11 23:59 UTC` · `v3.1.0` Target images build the llama.cpp pin of `configs/llama_runtime.yml` by commit (`b11429`, `d8123504`): the engine
  passes tag and commit, and the image checks the commit. The setting `llama_cpp_version` defaults to the pin's tag
  instead of a written `v0.6.0` and no longer changes what is built (another value gives a warning in the build log).
  The Dockerfiles, the `target.yml` build arguments and `configs/project_sources.yml` (which still said `b4009`) name
  the same tag and commit.
- `2026-10-11 23:59 UTC` · `v3.1.0` The target template builds llama.cpp's conversion tools with the same steps as the Rockchip image, so a generated
  module no longer stops with "llama.cpp converter not found". The GPU template takes CUDA 12.9.1 from NVIDIA's redist
  archives (the manifest pinned by sha256, every component checked) on Ubuntu 22.04 pinned by digest, instead of the
  unpinned `nvidia/cuda:12.2.2` images; NVIDIA's prebuilt development image is an explicit opt-in pinned by digest (a
  base image you choose yourself; Ditto's proposals never carry one, since a probe file could steer them) and labelled
  `org.llm-framework.cuda-source=prebuilt-image`, "not self-built" in the logs. The wizard no longer fills in an
  `nvidia/cuda` image for NVIDIA hardware.
- `2026-10-11 23:49 UTC` · `v3.1.0` `deploy.sh` of a GGUF package no longer suggests a `llama-server` found on the board's `PATH`: it names the start
  script of the self-built device runtime when that is installed, and otherwise how to deploy it
  (`llm-cli device runtime build` and `deploy`). `llm-cli scan --device` keeps the SSH user as the board's deploy
  user (never root).
- `2026-10-12 00:37 UTC` · `v3.1.0` The perplexity check of the dataset manager runs the self-built `llama-perplexity` of this machine instead of
  pulling `ghcr.io/smilez1985/llm-runtime`: catalog models (Ditto's own) natively or in the Docker sandbox; every
  other model, such as one converted for a board, only in the Docker sandbox (the build's own locally built image, no
  network), never natively on this computer. A failed measurement is an error with the reason instead of the
  values 999.99 or 888.88.
- `2026-10-11 18:40 UTC` · `v3.1.0` **MCP results**: the text block of a tool result starts with one sentence built only from framework facts (ids,
  states, counts), followed by the JSON wrapped as untrusted data; output schemas mark every field with external
  text (`"untrusted": true`); results carry `notes` written by the framework (for example "the knowledge base is
  reachable; no excerpt matched" or that only the builds of this process are listed); a pending self-healing
  proposal's `suggested_step` is now `manual_step_text`, text for the person to read. The server instructions say
  how to treat marked fields and that a task the client cannot do is reported with its specific reason.
- `2026-10-11 18:55 UTC` · `v3.1.0` **MCP clients see only the tools they may use**: tools your permissions deny are left out of the tool list (the same
  tools Ditto gets for the same permissions); a call to one is still refused and audited. `destructiveHint` follows
  each tool's own flag instead of its tier (`resolve_healing` stays marked destructive: it changes a setting and
  retries a build), and tools that reach other devices ask the client to show its own prompt on every call
  (`anthropic/requiresUserInteraction`).
- `2026-10-11 18:52 UTC` · `v3.1.0` A local model file is mounted into the build containers alone, no longer together with the folder it lies in
  (a model that needs files next to it, such as ONNX external data, is given as its folder). An RKNN sample list
  next to a model file now gets its own folder mount.
- `2026-10-11 19:07 UTC` · `v3.1.0` MCP and Ditto: builds belong to the caller that started them (an MCP connection, each named token over HTTP,
  Ditto). Cancelling its own build
  with `cancel_build` no longer asks; cancelling another client's build follows the tool permissions (by default it
  asks). A per-tool setting still wins, a tier you denied stays denied, and a damaged permission file still denies.
  `get_build_status` shows the caller class that started a build.
- `2026-10-11 19:52 UTC` · `v3.1.0` **MCP over HTTP needs a named token per client.** The one shared per-user token of 3.0.0
  (`~/.llm-framework/mcp_http_token`) is no longer accepted: create a token per client with
  `llm-cli mcp token add NAME`, give it to the client, and delete the old file. `llm-cli mcp serve --transport http`
  does not start before a named token exists.
- `2026-10-11 22:55 UTC` · `v3.1.0` **A client that cannot ask you is no longer refused** for a tool set to "ask": the call waits for your approval in
  the framework instead (see Added); before, it failed with a hint how to allow the tool.
- `2026-10-11 22:32 UTC` · `v3.1.0` **Confirm your tool permissions once after the update.** The permission file is signed now, and one saved by 3.0.0
  carries no signature, so every tool is denied until you set the permissions up again (the GUI shows the dialog at
  the first start and says why; `llm-cli tools permissions`). `llm-cli tools permissions`, also with `--reset`, and
  `llm-cli ditto setup` need a person at a terminal: piped answers are refused.
- `2026-10-12 04:17 UTC` · `v3.1.0` **Targets for AI callers**: `list_targets` shows each target's trust (`shipped`, a pack's trust, `ai-generated`,
  `own`) and whether it is untested; `get_target_capabilities` adds the trust, an AI-created target's assumptions and
  base image reason (external text) and, with `check_files`, the problems the module and pack checks find in its
  files. AI-created targets carry `provenance: ai-generated` and `untested: true` in `target.yml`, and builds of them
  warn that they are untested and that their image is built with network access.
- `2026-10-12 04:17 UTC` · `v3.1.0` **Probing and AI modules without a terminal**: the local probe (`llm-cli scan`, `probe_local_hardware`) runs the
  fallback script with input from `/dev/null`, `NON_INTERACTIVE=true` and its own working folder, and stops after 120
  seconds. `llm-cli module generate-ai` pins the base image to its digest before it asks; it and the wizard record the
  saved module in the audit log (`module_saved`), and the wizard passes on every field of Ditto's proposal.

- `2026-10-12 03:20 UTC` · `v3.1.0` **MCP: builds are asked about with what the framework looked up** (design T1, DECISIONS round 2 item 8):
  `start_build` shows the Hugging Face commit it will build (and builds exactly that commit), the download size,
  weights type, licence, gating and the stored token's access, the space on each disk with the conversion margin and
  the output counted three times, an estimate whether this computer and the board can take it, mounts, target trust
  and the image build. No size cap: a build is refused only when a disk cannot hold it beside a reserve and the space
  other accepted builds reserved (`insufficient_resources`, with the numbers); reservations are kept in
  `~/.llm-framework/disk_reservations.json` and checked again when a build leaves the queue. New read tool
  `check_build_request`: the same plan as a dry run. `start_build` takes `optimization`.
- `2026-10-12 03:20 UTC` · `v3.1.0` **MCP: build reads by id and label**: `list_artifacts` names built models by an id and `output:<relative path>`
  with size and hash (paged, filtered), new `get_artifact_details` returns the manifest (external text), model card
  and files, with the resources `lcf://artifacts/{id}/manifest` and `/model-card`; `list_build_requests` (paged)
  and `get_build_status` also show builds of other framework processes from their build records, read-only;
  `get_build_status` says what the request builds; `read_build_log` returns at most 1000 lines and pages back with
  `before_line`.
- `2026-10-12 03:20 UTC` · `v3.1.0` **Hugging Face models are staged one folder per commit**, `<cache_dir>/models/hf/<owner>--<name>/<commit>/`, file
  by file under a lock; only that folder is mounted into the build containers (no longer the whole model cache),
  a cancel stops the download before the next file, and the download stops when the cache disk falls below half
  its reserve. A full disk is reported as `insufficient_resources` with the free space.

- `2026-10-12 03:34 UTC` · `v3.1.0` **MCP: framework status and settings** (design T2, DECISIONS round 1 item 4): `get_status` shows the sections
  framework, host, local runtime (a prebuilt archive labelled "not self-built"), knowledge base, AI access (states
  only, never a key fragment, account or endpoint), permissions and this caller's audit entries; new `get_settings`
  (an explicit read list, addresses as scheme and host, folders as labels) and `update_settings` (ordinary settings
  with a question showing old and new value, saved in `config.yml`; settings that limit an AI only tightened, and
  only those may be allowed without a question, and a container memory limit of 0, which Docker reads as none,
  counts as loosening; `crawler_download_pdfs` only switched off; settings that keep you informed of what AIs do,
  such as spoken announcements, read-only for every AI, and `update_settings` is not registered if it could reach
  one; permissions, secrets, vault, AI provider settings, addresses and folders refused before the
  question; no value is read as "default").
- `2026-10-12 03:34 UTC` · `v3.1.0` **MCP: honest "cannot" checked by the framework** (DECISIONS round 3): new `report_cannot_do` takes a reason code
  (no "untested") and what is missing, checks it against the caller's permissions, this session's audit entries and
  limits, the disk and the actions no tool does, and answers confirmed, contradicted, premature (with the tools to
  try first) or unchecked, with a sentence built only from framework facts (a tool only by its registered name); "the hardware is not available" is
  contradicted for every tool that uses no device. Reports are audited as `cannot_reported`;
  `llm-cli tools audit --summary` counts them per caller.

- `2026-10-12 06:03 UTC` · `v3.1.0` **Packaging and deploying say why they failed**: `DeploymentManager` returns the reason (no Docker, an image not
  pinned, the host key changed, the exit status of `deploy.sh`) instead of `None` or `False` with a log line (G23);
  `llm-cli deploy package` prints it. A package is written under a temporary name and renamed when complete.

- `2026-10-12 18:24 UTC` · `v3.1.0` **The AI Chat tab moved into the chat panel**: the main window no longer has the tab; everything it did works in
  the panel (Ditto's turns in a worker thread, the confirmation dialog of Ditto's tools, the access line, the saved
  conversation in `chat_history.json`, which is taken into the chat feed once). `gui/chat_window.py` keeps
  `ChatWorker`, `describe_access` and `ChatWindow` (now the panel) with the old attribute names. Enter sends, Shift+Enter
  adds a line. The status-bar notices for a fix proposal of an AI client and for a use of a standing approval also
  arrive in the chat.
- `2026-10-12 18:24 UTC` · `v3.1.0` **"Clear chat" lists what it deletes before it asks**: the former "Clear History" deleted Ditto's whole chat history
  after a plain "Are you sure?". The question now lists, for Ditto's conversation (`chat_history.json` as the file holds
  it now, also when another window or the terminal saved it since) and for the conversation rows of the chat feed, the
  full path, the file name, the size and what it is, says how many records of what AI apps did are only hidden (never
  deleted), and has "No, keep everything" as the default. Both parts are written to the audit log (`feed_cleared`, with
  the hash of what was shown) before anything is deleted. Nothing is deleted without the audit log (also when there is
  no chat feed), when the chat or Ditto's conversation changed while you were asked, or when the window holds a
  conversation its file does not show.
- `2026-10-16 00:25 UTC` · `v3.1.0` **The doctor is Mojo now** (released in 3.0.0; sixth round, decision 9): the code moved into
  `orchestrator/Core/mojo/` (`orchestrator/Core/doctor.py` re-exports every name it had); finding codes, exit codes
  and JSON keys stay, and the JSON adds `where`, `why`, `repair_id` and `cannot` per finding. `llm-cli doctor` keeps
  its options and output and prints a short note on stderr that names `llmcf mojo`. `doctor --fix` now makes the
  audited harmless repairs of Mojo Miracle instead of an unrecorded change of the folder's mode, and says why a
  problem is still present (for example: the audit log cannot record the repair, so nothing was changed); `--tier 2`
  still shows the list of the framework's build images. The tool `run_doctor` stays callable but is no longer listed; a
  setting for `run_doctor` or `run_mojo_oracle` applies to both, and the stricter one wins. `make mojo` next to
  `make doctor`.
- `2026-10-16 09:37 UTC` · `v3.1.0` **Knowledge packs carry a page's full text only where its licence allows it, with the licence texts it asks for**
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
- `2026-10-15 20:45 UTC` · `v3.1.0` `--password VALUE` on `deploy run`, `scan --device` and `device runtime deploy`: every user sees the value in the
  process list, and it stays in the shell history. It still works and prints a warning; use `--password` without a
  value (hidden prompt) or `--password-stdin`.
- `2026-10-11 04:36 UTC` · `v3.1.0` `llm-cli secrets set --password`: a value on the command line ends up in the shell history and the process
  list. Use the hidden prompt or `--with-token`.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli build start --parallel`: it never had an effect (one call runs one build). It is hidden from the help
  and prints a warning. `--optimization` stays; its help now says that the Rockchip target ignores it.
- `2026-10-23 09:38 UTC` · `v3.1.0` `DeploymentManager.trust_host` (DECISIONS round 22, P5): the old writer of a confirmed host key, without an audit
  row, that nothing in the framework calls. It is now a thin delegate to `orchestrator/remote/pairing.py`
  `trust_presented_key`, the one writer, which stores the key under its lock and writes the trust's audit row in the
  same step. Only the person can use it: it takes the channel's `pairing.PersonChannel` (`cli` or `gui`, with the
  audit log), which only the person's channels can make, because no module an AI can reach imports `pairing`. Without
  one it trusts nothing and raises `PermissionError`; every call warns with `DeprecationWarning`. The guard in
  `tests/test_remote_trust.py` is tighter now: the deploy module names no trust writer at all.

### Removed
- `2026-10-17 15:24 UTC` · `v3.1.0` The Builder tab's unused `LocalProbeWorker`, a second runner of the fallback probe script (its own subprocess call
  without a time limit, with the inherited stdin, in the GUI's working folder). Every local scan runs the script
  through `HardwareProbes.run_local` (the one runner `llmcf scan --script` and the scan menu use); its tests moved
  there.
- `2026-10-15 20:23 UTC` · `v3.1.0` The stale copy `Docker Setup/pyproject.toml` (version 1.6.0, read by nothing): the container image is built from
  the repository's own `pyproject.toml` and `requirements.txt`.
- `2026-10-10 21:50 UTC` · `v3.1.0` Ditto's generated filler calibration data (builds use the shipped calibration text or your own dataset).
- `2026-10-10 22:57 UTC` · `v3.1.0` `litellm` and `tiktoken`, and with them 19 indirect packages (aiohttp, boto3, jinja2 and others).
- `2026-10-11 02:55 UTC` · `v3.1.0` The unused setting `input_history` (the input history is personal and lives in the per-user state directory,
  not in the shared `configs/config.yml`).
- `2026-10-11 03:30 UTC` · `v3.1.0` The *Respect robots.txt* option of the Deep Ingest dialog and the setting `crawler_respect_robots`: robots.txt
  is a machine-readable opt-out like the others, so it is always honoured.
- `2026-10-11 01:54 UTC` · `v3.1.0` The "swarm" upload: the XOR "SwarmCipher" (obfuscation anyone could undo), `llm-cli swarm upload`, the
  Community Hub's "Contribute to Swarm" button and its GitHub token prompt. The framework never asks for or
  stores a repository token. Old swarm packets in `community/knowledge/` are skipped with a warning.
- `2026-10-11 02:42 UTC` · `v3.1.0` Installing hand-copied module folders from `community/`: community packs are installed with
  `llm-cli zoo install` or the Community Hub.
- `2026-10-11 00:21 UTC` · `v3.1.0` **The Claude subscription provider `claude_code`** (Ditto through `claude -p` on Claude Pro/Max plan
  limits): Anthropic does not allow other apps to use Claude subscription limits without approval. A saved
  `ai_provider: claude_code` (or `ClaudeCode`, `Claude Code`), the same name in the fallback chain or in
  `--provider`, now stops with a message, shown once in the AI settings, that names the alternatives: a Claude
  API key (Max and Team plans include monthly API credits for a linked Console organization), Claude on
  Bedrock, Vertex or Foundry, or using the framework from Claude Code itself (`llm-cli mcp config`). It never
  switches to the Claude API on its own. A `claude_code` preset left in a provider catalog in your config
  directory is skipped with a warning; the other providers keep working.
- `2026-10-11 15:48 UTC` · `v3.1.0` The setting `preferred_tiny_model` of the removed Transformers offline mode. A `config.yml` that still contains
  it loads as before; the value is ignored and kept in the file.
- `2026-10-11 13:54 UTC` · `v3.1.0` Leftovers of the removed Transformers offline mode and of the Qdrant container: the tiny-model download of the
  model manager (it read `source_repositories['tiny_models']`) and `DockerManager.ensure_qdrant_service` (the
  knowledge base uses embedded Qdrant), and the Qdrant sidecar of device packages: a package with `--docker` and the
  knowledge base on no longer ships the Qdrant image, an empty `data/qdrant` folder and `QDRANT_HOST`, because nothing
  on the device used them (the knowledge base serves Ditto on this computer), and the deployment window no longer
  offers "Qdrant Sidecar". The setting `image_qdrant` is ignored with a log line.
- `2026-10-16 00:39 UTC` · `v3.1.0` Unused code: `orchestrator/Core/transparency_manager.py` (RepE: it needed a transformers model in the framework's
  own process, which neither the managed llama-server nor a cloud provider gives) and `scripts/ci_image_audit.sh`
  (called by nothing; it ran an unpinned `dive` image with the Docker socket mounted). The Docker workflow writes the
  Rockchip image's size and its layers into the job summary instead, with the runner's own docker CLI.

### Fixed
- `2026-10-29 15:08 UTC` · `v3.1.0` **An unquoted vendor id of a `target.yml` matches the probe again**: YAML reads `vendor_id: 0x1e60` without quotes
  as the integer 7776, and the target suggestion of a probe import compared it with the probe's `0x1e60` and matched
  nothing. `target_manager.hardware_id` gives one spelling for an integer and for hex text in any letter case, for
  the target's vendor and device ids and the probe's.
- `2026-10-24 00:54 UTC` · `v3.1.0` **The pinned llama.cpp source's git opens no console window and reads any output** (port follow-up):
  `runtime/build/source.Git` started git without `CREATE_NO_WINDOW`, so on Windows a console window flashed up when
  the Boards window built a board's runtime or Ditto's runtime export ran it from the GUI, and git output that was
  not UTF-8 ended in a `UnicodeDecodeError` traceback instead of the plain refusal. Git now starts as
  `utils/helpers.execute_command` starts a console program: the same creation flags (`helpers.windowless_flags`),
  output decoded with replacement characters, stdin closed; the options of offline mode are unchanged. Regression
  tests: `tests/test_build_source.py` `test_git_starts_like_the_one_helper_starts_a_console_program`,
  `test_a_message_that_is_not_utf8_is_reported_never_a_traceback`.
- `2026-10-23 17:21 UTC` · `v3.1.0` **`llm-cli deploy devices edit` gives the plain reason instead of a traceback**: it caught only the board steps'
  own errors, so the framework's `known_hosts` held by another process (a `LockTimeout` of `pairing.replace_key`),
  settings that could not be read after you said yes, or a file that could not be read before the question ended in
  a traceback. Each now prints the `local_problem` reason with its next step (`_print_reason`) and what failed, exits
  with code 1 and changes nothing.
- `2026-10-23 17:18 UTC` · `v3.1.0` **Removing old Hugging Face downloads works with the real audit log**: the audit log refuses outcomes it does not
  know, and the deletion of old downloads (Take over, `hf_takeover.delete_old_copies`) recorded
  `old_downloads_deleted`, which it did not know, so the deletion stopped with "The audit log cannot record the
  deletion" (the tests used a stand-in log). It is a known outcome now, as is `earlier_build_replaced` of "Use".
- `2026-10-23 09:15 UTC` · `v3.1.0` **hadolint findings at build time are shown, not thrown away** (DECISIONS round 23, item 4): the build reported
  every outcome of its Dockerfile check as "Hadolint check skipped (tool not found or failed)" and dropped what
  hadolint found. `BuildEngine._validate_dockerfile_hadolint` now runs hadolint through the new
  `orchestrator/utils/hadolint.py` (in the Dockerfile's folder on its file name, without colours, at most 60 s) and
  tells the cases apart: not installed (a note in the build log with where to get it, no warning any more),
  findings (a summary line, then each finding in plain words, for example "hadolint, Dockerfile: line 30, DL3008
  (warning): ...", in the build log and as a progress warning, at most 10 warnings), no findings (said in the log),
  and a run without a result (a crash, a time-out: one warning with what hadolint wrote). The check stays advisory:
  the build always goes on. CONTRIBUTING's local hadolint command pins the image by digest
  (`hadolint/hadolint:v2.15.1@sha256:32dac941...`, FRESHNESS_PLAN F-5) and lists all five Dockerfiles CI lints.
- `2026-10-23 09:33 UTC` · `v3.1.0` **A newer version of a knowledge pack left the old version's excerpts behind** (DECISIONS round 23 item 2): pack
  pages are stored piece by piece without replacing, so a changed or dropped page kept its old excerpts. Every excerpt
  now carries the installed pack's content digest; once every page of the new version is in, the excerpts of the
  older one are removed (`RAGManager.remove_pack_leftovers`), per pack and page source, never those of other packs or
  your own pages; a sorted loose file also replaces the excerpts an earlier version imported from it unsorted. The
  snapshot before the import stays. A pack page that failed to import used to be written only to
  the log; why a pack waits is now said after the install (`last_sync_problem`): only why the installed pack waits
  (another pack that waits is named when the knowledge base starts), and the Community Hub offers the list of
  snapshots to delete only when the snapshot taken first did not fit. A sorted loose file gets a name that is free in
  its folder before you are asked (also when the file lay in a folder of that topic), never a Windows device name,
  and its sort never removes the excerpts of an installed pack of the same name or of a target pack. A knowledge pack
  whose folder exists already is refused before the question, as other packs are. Windows keeps no execute bit, so
  an installed pack with scripts made on Linux or macOS (every target the module wizard makes) never matched its
  digest there: its knowledge snapshot would never be imported and **Check installed packs** called it changed. The
  provenance now also holds the pack's digest with the modes as the file system keeps them (only where it differs;
  the contents stay the manifest's, so a file changed later is still caught), and both checks accept it. Crawled
  pages already replaced their earlier excerpts and are stored right after their download (unchanged).
- `2026-10-23 09:22 UTC` · `v3.1.0` **`ditto models download` without a tier fetches the model a plain `runtime start` loads** (chain port-runtime,
  from the review of the managed llama-server): `runtime start` loads the tier chosen earlier with `--model`, but
  the download without a tier fetched the recommendation for this computer, so after choosing another tier the next
  start still failed with "not downloaded". The download now takes the tier chosen earlier ("Chosen earlier with
  'runtime start --model': ..."), else the recommendation as before (`runtime/ditto_models.model_to_download`). The
  GUI's **Ditto > Local Models...** does the same: with a tier chosen earlier its button reads **Download the chosen
  model...** and asks about that tier. Docs: README, `docs/CLI_Usage.md`, `docs/Features.md` (EN/DE). Regression
  tests: `tests/test_runtime.py` `test_model_download_defaults_to_the_model_runtime_start_loads`,
  `tests/test_ditto_models_core.py` `test_without_a_tier_the_download_is_the_model_runtime_start_loads`,
  `tests/test_gui_ditto_models.py` `test_without_a_selection_the_download_is_the_model_the_server_loads`.
- `2026-10-23 09:18 UTC` · `v3.1.0` **A machine whose GPU has no Vulkan driver gets the CPU build** (chain port-runtime, from the review of the managed
  llama-server): when `vulkaninfo` ran but listed only a software renderer (llvmpipe, lavapipe), the hardware probe
  still set `VULKAN=ON` because a Vulkan loader, a display adapter (Windows) or `vulkan-1.dll` was there, so the
  official prebuilt choice and the self-built plan picked the Vulkan build, which would only have found llvmpipe.
  Such a `vulkaninfo` now means `VULKAN=OFF`; the loader, the adapters and the DLL count only when `vulkaninfo` is
  missing or fails. The same in the three probes: `orchestrator/runtime/hardware_probe.py` (with its byte-identical
  copy `scripts/hardware_probe.py`), `scripts/hardware_probe.sh` and `scripts/hardware_probe.ps1`. Regression tests:
  `tests/test_runtime.py` `test_vulkaninfo_with_only_a_software_renderer_means_no_gpu_driver`,
  `test_windows_vulkaninfo_with_only_a_software_renderer`, `tests/test_hardware_probe.py`
  `test_a_software_renderer_only_means_no_vulkan` (the .sh and the .py on the same machine).
- `2026-10-23 09:06 UTC` · `v3.1.0` **`runtime start --model` and `--sleep-idle` count also while a server runs** (chain port-runtime, from the review
  of the managed llama-server): a start that found the running server already serving the chosen model returned
  before the choice was recorded, so `runtime start --model X` (or `--model auto`, and the GUI's model choice) on a
  running server was forgotten and the next plain start loaded the recommendation again. The choice is now recorded
  under the runtime lock in both cases (an evaluation run, `remember=False`, still keeps the earlier one). An
  explicit idle time (`--sleep-idle`, the GUI's field) that differs from the running server's was ignored; the
  server now records its effective idle time and is started again when another one is asked for (a build without
  the option always runs with 0 and is not restarted for it). Both on bare metal and in Docker mode. Regression
  tests: `tests/test_runtime.py` `test_choosing_a_tier_is_remembered_when_the_running_server_is_reused`,
  `test_an_explicit_idle_time_restarts_a_server_started_with_another`,
  `test_an_idle_time_the_build_cannot_use_restarts_nothing`, `tests/test_runtime_mode.py`
  `test_a_reused_container_remembers_the_tier_and_another_idle_time_restarts_it`.
- `2026-10-23 09:02 UTC` · `v3.1.0` **The local runtime's lock is not held for minutes any more** (chain port-runtime, from the review of the managed
  llama-server): `ditto runtime install --prebuilt` (and the GUI's **Install the official prebuilt archive…**)
  downloaded the archives while it held the runtime lock, so a `runtime start` or `runtime stop` elsewhere waited for
  the whole download (a CUDA build is hundreds of MB), up to the lock's 15 minutes. The archives are now downloaded
  outside the lock (each file has its own); under the lock the install checks again whether another process
  installed the same build meanwhile (then it is used as it is), whether a server runs from it, and fetches the
  archives again if a concurrent install removed them; a running server is still refused before anything is
  downloaded, and the folders an interrupted install left behind are still removed (under the lock) before the space
  for unpacking is checked (review of port-runtime: the check had moved in front of that removal). The exit handler of a server tied to its process (`start(tie_to_process=True)`) waits at most 5 s for
  the lock and then still ends the exact process it started (pid, start time and program checked), instead of
  keeping the exiting process alive behind another start. Regression tests: `tests/test_runtime.py`
  `test_downloads_do_not_hold_the_runtime_lock`, `test_an_install_finished_meanwhile_by_another_process`,
  `test_the_leftovers_of_an_interrupted_install_are_removed_before_the_space_check`,
  `test_exit_handler_does_not_wait_for_a_busy_lock`.
- `2026-10-23 08:56 UTC` · `v3.1.0` **A pinned download that cannot work says why at once** (chain port-runtime, from the review of the managed
  llama-server; `orchestrator/utils/download.py` `PinnedDownloader`, shared by the local runtime's builds and models and
  the community zoo): a TLS certificate that could not be verified was retried five times with backoff like a dropped
  connection and ended in "did not complete after 5 attempts"; it now ends at once with "the TLS certificate of <host>
  could not be verified" (only the host, never the possibly signed download URL; after a redirect the host that failed,
  such as a release CDN, not the first one, review of port-runtime; behind a proxy that inspects TLS its CA certificate
  belongs in `REQUESTS_CA_BUNDLE`). A connection that ends during its TLS handshake (a flapping link, a proxy) or whose
  TLS record breaks on the way is no certificate problem: it is retried and resumed like any dropped connection (review
  of stage C wave 3). Any other request error (too many redirects, an invalid URL) and a `.part` file that cannot be
  written (a full disk, no permission) used to escape as a raw exception; they now end with a clear message, and the
  part is kept for a resume. The Hugging Face fetcher (`hf_hub.HubFetcher`: the browser's, the Builder's and an AI's
  downloads) ran its own copy of the range download without these fixes; it now runs the same one and adds only what is
  the Hub's own (the token only for huggingface.co, the refusal of another host and of HTTP 401 and 403), so a size on
  the server other than the one the Hub lists is refused there before anything is written too (stage C wave 3).
  Regression tests: `tests/test_runtime.py` `TestDownloads`, `tests/test_hf_tools.py` `TestTheFetcherSaysWhyAtOnce`.
- `2026-10-23 08:52 UTC` · `v3.1.0` **The local runtime no longer fails on Windows while another window reads its files** (chain port-runtime, from
  the review of the managed llama-server): Windows refuses to replace or delete a file that another process has open
  at that moment. The GUI's LLMClient reads `server.json` and the server's key file, and a process waiting for the
  runtime lock reads the lock file, so a start, stop or install in another window could end with "The process cannot
  access the file". The runtime's state files (also those of the build store and the runtime mode) are now replaced
  with `utils/atomic.replace_file`, and `server.json`, the key files and the lock files of `utils/download.FileLock`
  (the runtime lock, the build store's lock and the per-download locks) are deleted with the new
  `utils/atomic.remove_file`; both try again for a moment on Windows, never elsewhere. The downloaded archives of
  `ditto runtime install --prebuilt` go through `remove_file` too, and one that still cannot be deleted (a second
  process installing the same build hashes it, a virus scanner reads it) no longer fails a finished install: it stays
  in the downloads folder, with a warning in the log. Regression tests: `tests/test_atomic.py`,
  `tests/test_runtime.py` `test_windows_sharing_violations_are_retried`,
  `test_an_archive_that_cannot_be_deleted_fails_no_finished_install`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **Ditto's tool calls and their results always pair up** (port of the kept worktree ditto-rewire-verified): every
  provider rejects a conversation that goes on after an unanswered tool call, so every later question on such a
  history failed. An answer cut off at the length limit while calling a tool left the call unanswered; the tool
  loop now answers it with an error result (the call never runs), and a history an earlier version saved with an
  open call at its end is closed by appended results before the next question (earlier turns stay unchanged). A
  saved conversation whose calls and results do not pair up (an OpenAI-compatible server may have accepted one)
  starts anew with the notice that it could not be read, and a `transcript` or `messages` value that is no list no
  longer breaks loading the chat. One implementation in `orchestrator/llm/tool_loop.py` (`CALL_NOT_RUN`,
  `open_tool_calls`, `close_open_calls`, `conversation_is_consistent`). Regression tests: `tests/test_ditto.py`
  `TestToolCallsAndResultsPairUp`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **A failed Ditto turn after tools ran says so**: when the provider fails or the model declines after tools already
  ran in the turn, the message names them and says that Ditto will not remember the turn, so you check what they did
  (a build may have started) before asking again. A refusal in the tool loop takes the same path. Regression test:
  `tests/test_ditto.py` `test_a_failed_turn_after_tools_ran_names_them_and_says_it_is_not_remembered`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **A conversation with tool turns asked without tools continues as a new one**: the chat panel's **Answer without
  tools** sent the earlier tool calls and results without tool definitions, which Claude rejects. Such a conversation
  is now summarized into a new one, like a long one, and the notice says why (`ChatReply.new_conversation_reason`,
  `chat.notice.new_conversation_no_tools`). Regression tests: `tests/test_ditto.py`
  `test_a_conversation_with_tool_turns_asked_without_tools_continues_as_a_new_one`, `tests/test_chat_panel.py`
  `test_the_new_conversation_notice_says_why`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **Ctrl+C at Ditto's confirmation in the terminal is a no**: `llm-cli ditto ask` crashed without stdin, and Ctrl+C or
  the end of input at the confirmation or at the y/a/n prompt of a standing approval reached the model as a failed
  tool call ("Abort") with no `not_confirmed` row in the audit log. Both are now an audited no; `module generate-ai`
  reports a probe that cannot be read (a folder, no permission) or a module that cannot be saved instead of a
  traceback. Regression tests: `tests/test_ditto.py` `TestConfirmations`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **Offline mode refuses a custom server outside this computer and the local network at save**: the AI settings
  (dialog, sign-in, MCP `update_settings`) refused only cloud presets in offline mode, so a custom server with a
  public address was saved and refused only when Ditto was asked. `AISettingsChange.problems()` now uses the LLM
  layer's own rule (`preset.is_local(address)`). Regression tests: `tests/test_ai_settings_change.py` and
  `tests/test_dialogs.py` `test_offline_mode_accepts_a_custom_server_only_on_the_local_network`.
- `2026-10-23 08:44 UTC` · `v3.1.0` **In offline mode the Hugging Face browser no longer contacts Hugging Face** (consolidation chain hf-online): its
  search, licence check, file list and download went online with offline mode on, while an AI caller's calls were
  refused. **Tools > Hugging Face Model Browser...** now refuses before any request, before a worker starts or a token
  is asked for, in plain words in your language: offline mode is on, nothing is searched, checked or downloaded,
  then the words every other offline refusal shows you: switch offline mode off (Ditto > AI Settings...), or use a
  model already on this computer.
  `ModelManager` refuses the same way for every caller (`hf_hub.offline_refusal`, the refusal `HubClient` gives an
  AI; `hf_hub.person_text` gives a window or the command line the person's words). **Take over old downloads...**
  works offline: a file whose checksum only Hugging Face could give (a cache without links) stays in the old cache
  with the reason "offline mode is on", and Hugging Face is not asked; `llm-cli models take-over` behaves the same.
  An AI's `download_hf_file` looks again after your answer (review of stage C wave 3): offline mode switched on while
  its question was open refuses the download with the same refusal, before anything is reserved or requested.
  Regression tests: `tests/test_hf_online.py` `TestOfflineMode`, `TestTheBrowserWindowOffline`, `tests/test_hf_tools.py`
  `test_offline_mode_switched_on_while_the_person_is_asked_refuses_before_any_request`.
- `2026-10-22 23:41 UTC` · `v3.1.0` **A model from the old Hugging Face download cache works in a build again** (A6, "stay usable"): the GUI's Hugging
  Face browser used to save into huggingface_hub's own cache inside the models folder, where a file is a link to a
  blob named by its checksum. A build of such a GGUF file mounted it under that checksum name, so the build no longer
  saw a `.gguf` file (`config_module.sh` tells a GGUF input by its suffix) and tried to convert it; a cached model
  folder chosen by its path in the Builder was mounted whole, and its links to the blobs outside it did not resolve
  inside the container. A link to a cache blob (a file named by its checksum) now keeps its link's name in the build
  (`tools/local_files.py` `mount_plan` and the Builder's local path), and a person's folder whose links lead to cache
  blobs is bound file by file, as an AI caller's reference already was (`file_binds`, `linked_folder_binds`). Every
  other link, and a folder with any other link (or one the binder refuses: broken, to a folder, a second name for
  one file), is mounted exactly as before: a link's own name would change the build's model name, and a link out of
  a person's folder is never followed into the container. Regression tests: `tests/test_hf_gui_download.py`
  `TestTheOldCacheIsUsableForABuild`.
- `2026-10-22 13:58 UTC` · `v3.1.0` **A problem on this computer is no longer "the board does not answer"**: every contact with a board counted an
  error that was none of the four reason codes as `device_host_down`. A full or read-only disk, or no permission on
  the framework's own files (its SSH key, known_hosts), therefore told the person to switch the board on, the deploy
  waited for the board in vain, and the board was recorded as down for Mojo. Such an error now has its own reason,
  `local_problem` ("Something on this computer stopped the contact, not the board"), with its next step in English
  and German (free disk space, check the state folder and the security software of this computer), the AI's next step
  and Mojo's check of the installation as the catalogue step (`orchestrator/remote/reasons.py` `is_local`,
  `LOCAL_PROBLEM`, `CODES`). It is never recorded with the board. The deploy's reason names the file without its
  folder, and a home folder or a folder with this computer's account name only by its kind, since that name is an
  account's name and the reason reaches an AI (the account's names are USER, USERNAME and the name of every home
  folder `orchestrator/security/paths.py` `home_folders` knows, also USERPROFILE); the CLI also prints the error
  itself. It is no blocker of the
  delegation rules, so no delegation is outdated.
- `2026-10-22 13:44 UTC` · `v3.1.0` **A deploy works on a board whose account uses another login shell**: sshd hands every command to the board
  account's login shell. The deploy's commands (the private folder, the hash checks, the start of the installation
  that runs on its own and the polls of its end) and the installation of the framework key went there as they were,
  so on an account whose login shell is fish, csh or another shell that is not POSIX, their `&&`, `||`, `2>/dev/null`
  and `$?` meant something else or nothing. Every one of them now reaches the board as one `sh -c '...'`
  (`orchestrator/remote/transport.py` `sh_command`, the form the device runtime and the board probe already used),
  so the board's sh reads the script whatever the login shell is.
- `2026-10-22 17:32 UTC` · `v3.1.0` **The framework's `known_hosts` is written like every other private file**: trusting a board's host key (and the
  change of a board's address or key) wrote the file through a temporary file that stayed behind when writing failed
  (for example on a full disk), and on Windows the trust failed when another program had the file open at that
  moment. The temporary file now goes again, and the replace is the one of `orchestrator/utils/atomic.py`, tried again
  for a moment on Windows (`orchestrator/utils/ssh_trust.py` `save_host_keys`).
- `2026-10-17 15:50 UTC` · `v3.1.0` **A deploy keeps waiting while the board's SSH server starts again**: after the network dropped, the deploy waits
  for the board (N5). When the board answered again but its SSH server was still starting, the connection opened and
  dropped (a reset, an abort, a broken pipe or EOF in the key exchange); none of these had a reason code, so the deploy
  gave up at once instead of waiting on. While it waits, such a drop now means "not back yet" and the wait goes on
  (`orchestrator/remote/reasons.py`, `reach_code(..., while_waiting=True)`). The deploy's first contact also uses the
  rule every other path uses: a connection error that is none of the four codes means the board does not answer
  (`device_host_down`, recorded for Mojo) instead of an empty code.
- `2026-10-17 15:47 UTC` · `v3.1.0` **The language chosen at the first start applies at once**: the first-start dialog ran with the window's texts in
  English, and choosing German left them English (also Qt's own buttons) until the next start. The window now
  switches to the chosen language before it opens (`orchestrator/gui/app.py`).
- `2026-10-22 13:47 UTC` · `v3.1.0` **Dependabot no longer proposes another Ubuntu release for the GPU template**: `targets/_template/Dockerfile.gpu`
  builds on the Ubuntu release its CUDA redist archives and the build-base catalogue are pinned to (decision D6),
  so a bump to 25.10 broke the tests. `.github/dependabot.yml` ignores Ubuntu release changes (major and minor);
  a test keeps the rule.
- `2026-10-22 13:45 UTC` · `v3.1.0` **The nightly retrieval evaluation runs again**: the workflow `.github/workflows/retrieval-eval.yml` started the
  framework on a GitHub runner, which has no system keyring, without saying how the credential vault is kept, so the
  framework refused to start (exit code 5) every night. The job now sets `LLM_SECRET_STORAGE=ephemeral` (memory
  only; the evaluation needs no credentials), and a test fails when a workflow step starts the framework without it.
- `2026-10-17 01:49 UTC` · `v3.1.0` **A board whose home folder has a long path can be deployed to**: the device runtime refused a board user's home
  folder longer than 200 characters (and its staging folder and models below 400), so a home on a deep mount point
  stopped the deployment with "not a plain absolute path". The characters are checked as before; the length is now
  only a sanity bound far below the system limit (1024 characters for a folder, 1536 for a file in it, one place in
  `runtime/device/launcher.py`). The device tests also pass under a long temporary folder (as on the Windows
  runner).
- `2026-10-17 01:49 UTC` · `v3.1.0` **A damaged store of pinned author keys is copied once, not twice**: an install reads the store twice (for its
  summary, then to pin the key), and the second copy of the same damage was made again when that read fell into the
  next second, because the copy was named by the time alone. An identical copy is now kept once, and a different
  damage in the same second gets its own name instead of replacing a kept copy.
- `2026-10-16 21:52 UTC` · `v3.1.0` **Revoking an MCP token in the GUI also removes its standing approvals, and a caller's new own section keeps the
  tools asked in the framework only**: the GUI's Revoke deleted the token but left its standing approvals (grants of
  `mcp:NAME`) in the permissions file, listed until revoked one by one (they no longer applied); the token, its
  permissions and its standing approvals now go in one core function shared with `llm-cli mcp token revoke`.
  `llm-cli tools permissions --caller ditto|mcp` built a caller's new section without the shared list of tools whose
  questions are put to you in the framework only, so those questions could reach the AI client's own dialog again for
  that caller; the new section now starts from that list too, as `--confirm-in-framework` did.
- `2026-10-16 23:31 UTC` · `v3.1.0` **The list of tools asked in the framework only is no longer dropped by a re-run, a copy or a token's first own
  permissions**: running `llm-cli tools permissions --caller ditto|mcp` again for a caller that already had permissions
  of its own rebuilt its section without that list; a token made as a copy of Ditto's or the local MCP clients'
  permissions (`llm-cli mcp token add --policy-from`, or Add... in the GUI) got none; and a new token, which follows
  the shared list, lost it at its first `tools permissions --token NAME` (or Permissions... in the GUI), which started
  from an empty list. Each time, the questions about those tools could reach the AI client's own dialog again. The
  permissions shown and copied for a caller now carry the list that applies to it (`ToolRegistry.policy`).
- `2026-10-16 23:47 UTC` · `v3.1.0` **The token list speaks the window's language, and "Start server" says why it cannot start**: the section "MCP
  tokens (one per client)" in Security > AI App Connections (MCP)... and in Tools > Tool Permissions (Ditto / MCP)...
  was English only (title, note, columns, buttons, the questions when adding and rotating); it is English and German
  now, and Rotate and the question about a linked token are plain text with "No" as the default. An installation path
  that PYTHONPATH cannot hold made "Start server" fail without any message; the window now says why, and nothing is
  started.
- `2026-10-16 22:46 UTC` · `v3.1.0` **Community Hub and knowledge storage: installs in the background, the pull request question, a Qdrant server**
  (W1' review): installing a pack (Install, Import file...) moved it into place on the GUI thread after your "Yes",
  which for a knowledge pack meant a snapshot of the knowledge base and the import of every page with the window
  frozen; it now runs in the background, and the status line names the snapshot taken first, as `llm-cli zoo install`
  does. **Open pull request** in the export window handed the pack to a public pull request at once; it now asks the
  question of `llm-cli zoo export --pr` first ("Submit this pack to the public community repository?", "No" is the
  default). With a Qdrant server set (`qdrant_url`), `llm-cli knowledge storage` and **Tools > Knowledge
  Snapshots...** named the local storage and offered to convert it, which opened the server and converted nothing;
  both now name the server, and the conversion is refused while it is set. A passphrase typed into **Import file...**
  or the export window no longer stays in the hidden window after Cancel or Close.
- `2026-10-16 01:03 UTC` · `v3.1.0` **A build no longer replaces an earlier build of the same name**: in 3.0.0 a completed build moved its files into
  the folder `<target>/<model>/<quantization>` and replaced the files of an earlier build there (for example one for
  another board), so a finished golden artifact was lost without being asked. A later build now goes next to it
  (`Q4_K_M-2`, with its own ZIP); the earlier build stays whole.
- `2026-10-15 17:14 UTC` · `v3.1.0` **The self-healing "Docker cleanup" deletes nothing any more** (released in 3.0.0; round 5 f): approving the
  proposal for a full Docker disk (`docker_prune`, in the GUI dialog, at the CLI prompt or through `resolve_healing`)
  removed the framework's dangling images without showing them and cleared the whole build cache of the Docker
  daemon, other projects' too. It is now a manual step: the proposal points to the framework's list of its build
  images (the dialog's **Free Docker space...**, **Tools > Framework Build Images...**, `llm-cli doctor --fix --tier
  2`), which deletes only the images you tick and confirm, and an approval is refused before anything is asked. No code of the framework clears Docker's build cache any more.
- `2026-10-12 18:37 UTC` · `v3.1.0` **`llm-cli doctor --fix --tier 2` no longer deletes without a list** (released in 3.0.0; round 5 f): it cleared the
  whole build cache of the Docker daemon, other projects' too, and deleted the framework's dangling images without
  showing them. The build cache is no longer touched (it cannot be limited to the framework). Tier 2 now lists every
  build image of the framework (without a name, named `llm-framework/<target>:last-build`, runtime builders), each with
  its name, image id, size and what it is, nothing picked, and deletes exactly the ones you pick, by image id or name
  and without a prune, only after your yes at a terminal (the default answer is no). Without a terminal
  and with `--json` it only lists them; the deletion is audited first and refused when the list changed in between,
  and an image that got a name meanwhile (a build of another framework process) is kept. No tool, resource or prompt
  reaches it, so an AI app or Ditto can neither run it nor get it under a delegation. The disk findings say what tier
  2 lists instead of promising a cleanup.
- `2026-10-12 10:42 UTC` · `v3.1.0` **A repair is named however the call ends** (finding of a real Claude Code 2.1.296 run over stdio, protocol
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
- `2026-10-12 09:16 UTC` · `v3.1.0` **The larger disk reserve of a standing approval is checked under the ledger's lock** for `start_build`,
  `start_recipe_build`, `download_hf_file` and `add_pack_from_index`: the reservation is taken with the 20 GiB /
  10 % reserve inside the reservation ledger every framework process shares, so a reservation another process took
  between the question and the start is counted (before, only a snapshot at question time). `add_pack_from_index`
  now takes a ledger reservation at all and counts other calls' reservations before the question.
- `2026-10-12 09:39 UTC` · `v3.1.0` **The build question lists every base image of the target**: before, a build on a target that is not shipped
  showed only the first `FROM` of its top-level Dockerfile, so a vendor image pulled by a later stage (`COPY
  --from`, `RUN --mount=...,from=`) or by another Dockerfile was never shown, yet a confirmed build remembered it as
  confirmed for the build standing approval. Every image of every Dockerfile is now shown one by one (registry,
  digest, official or not), also for a shipped target that pulls an image outside the Docker Official Images.
- `2026-10-12 09:16 UTC` · `v3.1.0` **The GitHub CLI and git of the pull-request handoff never read stdin** (`stdin=DEVNULL`, G25): over stdio the
  MCP server's stdin is the protocol stream.
- `2026-10-12 09:20 UTC` · `v3.1.0` **The module wizard's AI import and the AI settings' model lists hand their results to the GUI through the one
  worker relay**: the wizard no longer emits from a plain thread on an unparented `WizardSignals` object it created
  (a closed wizard could take it along), and the AI settings' own relay is folded into `WorkerRelay`.
- `2026-10-12 09:20 UTC` · `v3.1.0` **Deep Ingest Stop texts** (wizard button, tooltip and the `crawl.stopping` message in English and German) say
  that Stop also abandons a page that is still arriving, instead of "after the current page".
- `2026-10-12 09:20 UTC` · `v3.1.0` **A damaged `authors.json` no longer loses the author keys pinned on first use**: it is kept as
  `authors.json.bad`, the install summary warns that the key could not be compared, and no key is pinned (the file
  is never overwritten) until it is repaired or removed.
- `2026-10-12 07:15 UTC` · `v3.1.0` **Digest-pinned images in a deployment package start on an air-gapped board**: an image pulled by its digest has
  no tag, so it was saved without a name, came back as `<none>` after `docker load`, and `deploy.sh` could not start
  it by its digest reference without a registry. It is now packed under `<repository>:lcf-<12 hex of the digest>`
  and started by that name (an image built here keeps its own tag), and `deploy.sh` starts every packed image with
  `--pull never`, so a board where an image is missing stops instead of pulling one from a registry.
- `2026-10-12 03:49 UTC` · `v3.1.0` **Hugging Face look-ups no longer hide failures**: the model manager behind the GUI's Hugging Face browser returned
  an empty search result, an empty file list or a "not restrictive" licence when Hugging Face could not be reached;
  it now reports the error. A licence counts as permissive only on a fixed allowlist: `other`, custom, list-valued
  and unknown licences are shown as unknown, and the browser asks before downloading such a model. A file the
  browser downloads comes from the commit it resolved first, not from a branch that may move meanwhile.
- `2026-10-11 23:38 UTC` · `v3.1.0` MCP questions of the 2026-07-28 protocol are kept per caller and session (one client's answer no longer closes
  another client's identical question), an unanswered one is recorded as `unanswered` by a timer also when no
  further request arrives, and the ones still open when a session ends are recorded as `client_disconnected`; the
  permission check of a long-poll no longer reads the policy file on the server's event loop; every refusal with
  the reason `failed` now carries a next step, and the build service's input refusals carry their own reason code;
  an MCP confirmation that failed or got no answer in time is recorded with a reason code (`failed`,
  `needs_person`) and its refusal names a next step.
- `2026-10-12 00:42 UTC` · `v3.1.0` Two-phase confirmation, the same on both paths: an MCP question too long for a form is recorded with reason
  `limit_reached` and answer `unavailable` and names a next step, as in Ditto's windows; a question that a looked-up
  fact made necessary is recorded with the decision `ask` (it said `allow`); `resolve_healing` decides nothing
  without the facts of its question, and the first line of its question names the build, not only its id; a local
  model or dataset an AI named that vanished before the build is named by its label in the refusal, never by its
  real path.
- `2026-10-12 00:54 UTC` · `v3.1.0` Limits, the same on both paths: Ditto's early stop sees which field an input error is about (the registry dropped
  it, so a model that fixed one field after another was stopped and its correct call was not run); a refusal of the
  question limits (after a "no", a paused tool, too many questions) is no failed call on Ditto's path either (it
  turned into "Stop retrying" there while an MCP client kept getting the plain refusal); a start that was refused, for
  example by a full queue, no longer uses up one of the 20 builds a day; after the stop note a model that keeps
  calling tools gets at most two more turns instead of the rest of its budget.
- `2026-10-11 23:52 UTC` · `v3.1.0` Module wizard: a base image Ditto proposed was replaced by `debian:bookworm-slim` or `ubuntu:22.04` when its name
  merely contained "debian" or "ubuntu" (for example a vendor image on `nvcr.io`), so another image than the proposed
  one would have been built. The proposed image is now kept as it is and shown with its registry, and the messages
  with the AI's text (also Ditto's errors and refusals) are shown as plain text instead of rich text.
- `2026-10-11 23:38 UTC` · `v3.1.0` MCP server: on protocol 2026-07-28 it advertised change notifications for tools, prompts and resources and
  resource subscriptions, but never sent one. It now advertises them only together with the events it publishes,
  and without events it is built with subscriptions turned off.
- `2026-10-11 19:07 UTC` · `v3.1.0` MCP server: a client that disconnects no longer leaves its builds running with nobody to clean up. The builds
  and jobs its session started are cancelled, each with an audit entry ("client disconnected"), the server waits
  up to 30 seconds for their containers to stop, and `llm-cli mcp serve` then shuts the framework down. A SIGTERM
  from the client (how clients end a stdio server that has not exited after they closed its input) no longer ends
  the process before this cleanup.
- `2026-10-11 02:01 UTC` · `v3.1.0` Secrets store: a store of an earlier version that cannot be decrypted is reported by `llm-cli doctor`
  instead of being merged unreadable into the per-user store.
- `2026-10-11 01:34 UTC` · `v3.1.0` Reading or storing a secret no longer fails in a container whose user has no name (no `USER` variable and
  no passwd entry).
- `2026-10-11 01:34 UTC` · `v3.1.0` On Windows, saving a secret no longer fails when another process or a virus scanner has the store open for
  a moment.
- `2026-10-16 05:55 UTC` · `v3.1.0` On Windows, a build of a local model lands in the output folder again (released in 3.0.0): the build folder was
  named after the model's part behind the last `/`, which for a Windows path such as `C:\models\my-model` is the
  whole path, so the build went into the model's own folder (`C:\models\my-model\<quantization>`) and no listing
  found it. The folder is named after the last part at `/` or `\` on every system.
- `2026-10-11 01:44 UTC` · `v3.1.0` `llm-cli secrets set` says where the secret went (the encrypted vault, or memory only with `ephemeral`)
  instead of always "Keyring". The GUI names the encrypted credential vault as well (the secret dialog, the API key
  field and its error message in the AI settings, the token check box of the Hugging Face browser).
- `2026-10-11 01:30 UTC` · `v3.1.0` The CLI and the CLI guides no longer tell you to put the GitHub token on the command line, where the shell
  history keeps it (that form did not work either). `llm-cli secrets set <name>` asks for the value with a
  hidden prompt.
- `2026-10-11 14:39 UTC` · `v3.1.0` The Deep Ingest dialog opens with the settings `crawler_max_depth`, `crawler_download_pdfs` and
  `crawler_rate_limit` (depth 0 and delays below a second possible, larger values kept); before, its fixed values
  (2, PDFs on, 1 s) always overrode them.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli --help` and the CLI's description named version 2.4.0; every version string now comes from the
  package version.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli build status` lists builds that the GUI, the MCP server or another CLI call started: every build engine
  writes one record per build into `<cache_dir>/build_records/` (status, stage, progress, the error redacted), and
  a build whose program ended before it finished is shown as interrupted. The command needs neither the credential
  vault nor Docker; `--all` lists every kept build instead of the 20 newest. On Linux and macOS the records are
  readable only by the user who built (folder 0700, files 0600). A record names the build's outcome before its build
  slot is free, so whoever sees the build ended reads the outcome there, never the stage before it.
- `2026-10-11 13:50 UTC` · `v3.1.0` Ctrl+C in `llm-cli build start` cancels the build through the build service: its container is stopped, the CLI
  waits until the build has stopped (also when it was still being handed to the build engine), says so and exits
  with code 130. Before, the build and its container kept running.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli scan --output FILE` reaches the hardware probe (`hardware_probe.sh --output`,
  `hardware_probe.ps1 -OutputFile`), which always wrote `target_hardware_config.txt` into the current folder. On
  Windows without administrator rights the CLI waits for the probe's elevated restart instead of reporting at once
  that no file was written.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli module create` writes into the configured targets folder (`targets_dir`, relative to the installation)
  instead of `targets/` in the current folder; its next steps name `modules/build.sh`, the script builds run,
  instead of the legacy `config_module.sh`.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli config show` prints the effective settings (`config.yml`, a `-c` file, `LLM_FRAMEWORK_*` variables,
  defaults), each with its source and with credentials redacted, instead of the CLI's built-in defaults.
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli config repos --add` stores the `{url: ...}` entry that the build engine passes on as
  `<NAME>_REPO_OVERRIDE`; plain URLs stored by earlier versions are passed on as well. The name comes from the
  whole entry name (`llama.cpp` and `llama_cpp` both give `LLAMA_CPP_REPO_OVERRIDE`, the name the target scripts
  read; `llama.cpp` gave `CPP_REPO_OVERRIDE`). `--add` and `--remove` exit with code 1 when `config.yml` cannot
  be saved, instead of reporting success; `--add` refuses a name without any ASCII letter or digit (exit code 2).
- `2026-10-11 16:38 UTC` · `v3.1.0` A settings file given with `-c` that cannot be read no longer drops the `LLM_FRAMEWORK_*` variables as well: it
  is named on stderr and ignored, and the other settings apply.
- `2026-10-11 16:42 UTC` · `v3.1.0` The GUI's hardware scan of this PC (**Scan / Import...**) writes its result into `<cache_dir>/probes/` instead of
  the GUI's working folder, and the module wizard's *Auto-fill from Probe Result* reads it there.
- `2026-10-11 23:49 UTC` · `v3.1.0` The GUI's SSH scan of a board (**Scan / Import...**) failed on boards that mount `/tmp` with `noexec`: it copied
  `hardware_probe.sh` there and ran it. It now sends the probe over the SSH channel like `llm-cli scan --device`
  (the Python probe with Python 3.8 or newer, else the shell probe), writes nothing on the board, and keeps the
  profile under the board's ID.
- `2026-10-12 00:25 UTC` · `v3.1.0` Deploying a model package to a board failed where `/tmp` is mounted `noexec` (the package is unpacked there and
  `deploy.sh` was executed from it) and on boards without bash (the script started with `#!/bin/bash`). `deploy.sh`
  is now a plain POSIX `sh` script and is run with `sh deploy.sh`.
- `2026-10-11 16:55 UTC` · `v3.1.0` Deployment from the GUI writes the package into the configured output folder (`output_dir`, relative to the
  installation) instead of `output/` in the working folder.
- `2026-10-11 16:50 UTC` · `v3.1.0` The AI settings dialog warns when the settings could not be saved to `config.yml` (they apply until the program
  ends); before, only the log said so.
- `2026-10-11 16:55 UTC` · `v3.1.0` Windows: the uninstaller (`scripts/Uninstall-LLM-Cross-Compiler-Framework.bat`) finds the installation. `install.bat`
  now writes the marker with the installation folder that the uninstaller reads, next to `install.bat`, where the
  uninstaller looks for it; before, no installer wrote it and the uninstaller always reported that nothing was
  installed. When PowerShell reports no script path (the PowerShell part of `install.bat` runs through
  `Invoke-Expression`), `install.bat` uses the current folder, which its batch part has set to its own. The start script
  `scripts/start-llm_cross_compiler_framework.bat` also works from `scripts\` (it looked for `orchestrator\main.py`
  and `.venv` there).
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli targets list` shows each target's id (the value for `--target`), name, boards and formats from its
  capabilities instead of placeholders; `llm-cli targets validate` checks the capabilities block against the
  schema and prints every error (exit code 1).
- `2026-10-11 13:50 UTC` · `v3.1.0` `llm-cli deploy package`, `secrets set` and `scan` exit with code 1 when they fail (they exited with 0).
- `2026-10-11 13:50 UTC` · `v3.1.0` The help of `llm-cli doctor --tier` names only fixes that exist (tier 1 makes the personal folder private; there
  is no folder fix).
- `2026-10-11 13:50 UTC` · `v3.1.0` A target whose `metadata.id` differs from its folder name builds: the CLI, the build service, the orchestrator
  and the build engine resolve targets through one id -> folder map (before, the orchestrator skipped such a target
  with "Nothing to build"), and `-t` takes the id or the folder name.
- `2026-10-11 13:50 UTC` · `v3.1.0` The setting `max_concurrent_builds` sets the number of build slots (it had no effect; the engine always used 2).
- `2026-10-11 13:31 UTC` · `v3.1.0` Audit log: when several processes (GUI, CLI, MCP servers) wrote the first entries of a new log at the same
  moment, the log could be reported as damaged ("The audit anchor file was missing when entry 2 was written").
  The anchor of a new log is now written before its first entry becomes visible to other processes.
- `2026-10-11 13:46 UTC` · `v3.1.0` The CLI, the GUI and the MCP server no longer create empty `cache/`, `models/` and `output/` folders in the
  installation at every start, also when `LLM_FRAMEWORK_CONFIG_DIR` or `LLM_FRAMEWORK_HOME` point elsewhere; a
  build or a download creates the folders it uses. The repository's `.gitignore` covers `output/`, `cache/`,
  `models/` and `logs/`, so build results and downloads cannot be committed.
- `2026-10-11 13:46 UTC` · `v3.1.0` The language chosen at the first start of the GUI is saved in the effective config folder
  (`LLM_FRAMEWORK_CONFIG_DIR`, the installation's `configs/` if writable, otherwise the per-user folder) instead
  of always in the installation's `configs/`.
- `2026-10-11 15:00 UTC` · `v3.1.0` Saving a setting (for example the first-run language or the AI settings) no longer overwrites a `config.yml`
  that cannot be read (a YAML error, or no `key: value` settings) with only the changed values; the file stays as
  it is and the error is logged.
- `2026-10-11 15:58 UTC` · `v3.1.0` A number in `config.yml` that does not fit its setting (`.inf` for a whole number, or `.inf`/`.nan` for a decimal
  one) is ignored with a warning and the default applies; before, `.inf` for a whole number stopped reading the
  file, so every setting after it was ignored too.
- `2026-10-11 13:46 UTC` · `v3.1.0` Windows: with the framework in `C:\Program Files`, which standard users cannot write to, the settings and the
  data folders `output/`, `cache/` and `models/` are in the per-user folder (`%USERPROFILE%\.llm-framework`).
  The writability check no longer trusts `os.access`, which ignores folder permissions on Windows. The installer
  no longer creates an unused data folder in the public documents folder with a desktop shortcut to it.
- `2026-10-16 05:47 UTC` · `v3.1.0` Windows: a target module made with the module wizard, `llm-cli module create` or `create_target_module`
  (released in 3.0.0) had Windows line endings (CRLF) in its `modules/build.sh`, its other scripts, its Dockerfiles
  and its `target.yml`, so bash stopped in the Linux build container at the first line (`$'\r': command not
  found`). Its scripts, Dockerfiles and `target.yml` are now written with LF on every host. A Windows checkout of the
  repository also keeps LF in every text file (`.gitattributes`: the build container Dockerfiles, Python scripts,
  LICENSE files and git hooks were not covered), except the Windows-only `.bat` and `.ps1` scripts.
- `2026-10-16 05:47 UTC` · `v3.1.0` Windows: the calibration text for the importance matrix (released in 3.0.0) was written with Windows line
  endings, so `llama-imatrix` computed it from other text than on Linux and macOS; it is now written with LF on
  every host.
- `2026-10-11 13:46 UTC` · `v3.1.0` `install.sh` checks the connection over HTTPS to the package index (`pypi.org` or `PIP_INDEX_URL`) instead of
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
- `2026-10-12 14:23 UTC` · `v3.1.0` `make build`, `make up`, `make gui`, `make down` and `Docker Setup/docker-compose.yml` work: the compose file built
  from an `orchestrator/Dockerfile` that never existed, and the Makefile called the old `docker-compose` with it and
  the missing `scripts/setup_linux.sh`. The Makefile uses `docker compose` (v2) and the builder; `make setup` and
  `make test-container` are gone (they needed the missing files; tests run with `make test`), and so are the compose
  services with unpinned third-party images and the Docker socket (socket proxy, ctop, Trivy). `make audit` scans an
  archive of the image with the pinned Trivy version, without the Docker socket. `make clean` no longer deletes the
  data volume, `output/` and `cache/` without asking: it removes only containers that hold nothing of yours outside
  the volume and shows what holds data.
- `2026-10-11 13:46 UTC` · `v3.1.0` Deployment packages take user profiles, voice templates and prompts only from the config folder, never from a
  `config/` folder in the working directory.
- `2026-10-11 14:01 UTC` · `v3.1.0` The Hugging Face model browser can be opened again: **Tools > Hugging Face Model Browser...** (no menu led to it).
  Its licence check no longer freezes the window, and pressing Enter during a search or double-clicking another
  model during a download no longer crashes the GUI.
- `2026-10-11 14:09 UTC` · `v3.1.0` `scripts/sync_ssot.py` uses the real file `configs/detailed_project_sources.yml` (it created a copy named
  `detailed_project.sources.yml` instead) and works from any folder. It lists the differences, and with `--write`
  it changes only those values; comments and layout stay (it rewrote the whole file with `yaml.dump`).
- `2026-10-11 14:09 UTC` · `v3.1.0` The header of `configs/project_sources.yml` says who reads the file (no part of the framework while it runs; the
  two SSOT scripts), and its outdated roadmap section (v2.2 to v2.4) is gone.
- `2026-10-11 14:17 UTC` · `v3.1.0` Generated target modules (module wizard, `llm-cli module create`, `llm-cli module generate-ai`): the template
  `build.sh` follows the Rockchip dispatcher. It uses llama.cpp's `convert_hf_to_gguf.py` and
  `build/bin/llama-quantize`, writes the F16 intermediate into the work folder instead of `/tmp` (a 1 GB tmpfs in
  the sandbox), and stops with an error instead of copying the model unchanged. Its packaging step no longer copies
  a `build/` folder that does not exist, which made every build of a generated module fail at the end.
- `2026-10-11 14:23 UTC` · `v3.1.0` Secret scan with the pre-commit tool: its pre-push stage scans the commits being pushed (`--push`, which now
  reads the pushed commit from `PRE_COMMIT_TO_REF`) like `.githooks/pre-push`, instead of the whole history. The
  pre-commit check (`--staged`) also scans a file that replaced a symlink and files whose names contain a line
  break or start like `0:`, which it skipped before.
- `2026-10-11 20:08 UTC` · `v3.1.0` `hardware_probe.ps1` asked Windows for the wrong CPU features: constant 40 (AVX2) as AVX-512 and 37 (SSE4.1) as
  NEON, so nearly every PC claimed AVX-512 and NEON. It now asks the documented constants (AVX 39, AVX2 40, AVX-512 F
  41, BMI2 60; on ARM NEON 29, dot product 43, i8mm 66, FP16 67, SVE 46, SME 70) and writes `UNKNOWN` where Windows
  cannot tell. It also no longer assigns the read-only `$PID`, which aborted the probe when a Rockchip board was
  attached over USB.
- `2026-10-11 20:08 UTC` · `v3.1.0` `hardware_probe.sh` saw neither the ARM dot product nor FP16 arithmetic, wrote the x86 `f16c` flag as FP16 support
  on ARM, recognised only RK3588 and RK3566 and kept only the first GPU; it now writes the ARM features, the core
  clusters, every SoC of the device tree and all GPUs.
- `2026-10-11 18:30 UTC` · `v3.1.0` **Unanswered, timed-out and cancelled confirmations are audited** (MCP server): a question asked during the call
  (classic protocol) waits at most 5 minutes and is then recorded as `confirmation_timeout`, a question of the
  2026-07-28 protocol that is never answered is recorded as `unanswered` after 15 minutes, and a call the client
  cancels, also while its question is open, is recorded as `cancelled`. Before, these ended without an audit entry,
  and a question could stay open without limit.
- `2026-10-11 18:30 UTC` · `v3.1.0` **A client's cancel reaches a running call at once** (MCP server): the call is recorded as cancelled and the
  running handler is told immediately; what already runs finishes and its end entry follows. Before, the
  cancellation arrived only after the handler had finished, and nothing was recorded.
- `2026-10-11 19:16 UTC` · `v3.1.0` **Unknown arguments are no longer dropped over MCP**: `quant` instead of `quantization` built the target's
  default quantization and reported success, while Ditto's path refused the same call. Both paths now run the same
  normalizer on the raw arguments, MCP clients get the tools' closed input schemas (`additionalProperties: false`)
  instead of open ones, JSON text in a list field is accepted on both paths, and unknown tool names are audited
  (before: on neither path).
- `2026-10-11 22:15 UTC` · `v3.1.0` **The tests no longer touch the real home folder.** A test run had written SSH and Hugging Face files there
  (`~/.ssh`, `~/.cache/huggingface`). Before anything imports the framework, `tests/conftest.py` now points `HOME`,
  `USERPROFILE`, the `XDG_*` folders, `HF_HOME`, `HF_TOKEN_PATH` and the other per-user folder variables into a
  temporary folder (each test gets its own), removes credentials such as `HF_TOKEN` and `SSH_AUTH_SOCK` and the
  desktop session bus from the environment, gives every test a keyring file in its own home instead of the operating
  system's keyring (Secret Service, macOS Keychain, Windows Credential Manager), and fails the run when the real
  home's key, token, keyring or framework folders changed (it compares their listings and never opens a file).
- `2026-10-12 01:44 UTC` · `v3.1.0` **Pending approvals show the two-phase question and run bound to it.** A call that waits for you in the
  framework shows the same question as any other window: the facts the framework looked up, the speaking names of
  its ids, who asks. When you approve it, the facts are looked up again; if a fact the decision depends on changed (a
  new upstream commit, for example; free space alone never counts), you are asked again, otherwise the call runs bound
  to the facts you saw. An MCP
  question between 4,000 and 16,000 characters now becomes a pending approval instead of a refusal, a standing
  approval is matched on the looked-up facts on the MCP path too (a client that could ask is not asked when one covers
  the call), Ditto's scrollable dialog for long questions also offers "do not ask again", and an approved call's
  quotas are checked again before it runs.
- `2026-10-12 04:17 UTC` · `v3.1.0` **Target names that read alike**: a new target (module generator, wizard, `create_target_module`) whose name equals an
  existing target's id or folder ignoring case and separators (`rockchip`, `Rock-Chip` next to `Rockchip`) is refused,
  so it can never capture the name of another target.
- `2026-10-12 04:17 UTC` · `v3.1.0` **A GPU-template module builds the base image it names**: a module on the GPU template with a base image other than
  the default (for example a vendor toolchain image) silently kept the template's Ubuntu base; now both stages start
  from it, and the generator checks that the builder stage starts from the module's base image. Image references
  with a registry port (`registry.example:5000/img@sha256:...`) are accepted.

- `2026-10-12 03:20 UTC` · `v3.1.0` **`start_build` over MCP ignored the optimization level** (always balanced, G26), and **two builds of different
  commits of one model shared a staging folder** (and `a/b__c` and `a__b/c` collided, G38).
- `2026-10-12 03:20 UTC` · `v3.1.0` **A full disk always proposed pruning Docker** (G40): the proposal is now a manual step naming the framework's
  folders on the full disk with their sizes, and pruning Docker is proposed only when Docker's data root is full.
- `2026-10-12 10:35 UTC` · `v3.1.0` **A sandbox benchmark of a model in a read-only folder failed with a generic error**: `run_benchmark` with
  `sandbox=true` made its private link of the checked model only next to the model. It now falls back to a private
  folder in the framework's own cache or state folder on the same file system, else to a copy read from the checked
  open file and hashed again (its sha256 must be the checked one); only when nothing works does it refuse, with the
  reason (`insufficient_resources` or `needs_person`). The private folder is removed when the run ends, also on
  failure and on `cancel_job`, which now also kills the sandbox container, stops a copy that is still being written
  and starts no container after the cancel; a folder a crash left behind is removed by a later run.
- `2026-10-12 10:35 UTC` · `v3.1.0` **Job ends in the audit log**: when recording a finished deploy in the device list failed, `deploy_to_device` wrote
  no end entry although the deploy had happened; the end entry is now written on every way out of the job (the
  result then says that the device list did not record it). `create_deploy_package`, the board runtime jobs
  (`probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark`) and the local runtime jobs
  (`build_local_runtime`, `start_local_runtime`, `run_benchmark`) now write job-end entries too (ok, error with its
  reason code, or cancelled), linked to the call's started entry and naming devices and models by their labels. The
  deploy and runtime tools reach built models through the public `BuildService.artifact_entry` instead of a private
  method.
- `2026-10-16 00:39 UTC` · `v3.1.0` **The catalogue of upstream projects is consistent** (`configs/detailed_project_sources.yml` and
  `configs/project_sources.yml`, shipped in 3.0.0): every size multiplier has one baseline (FP16 = 1.0x, computed from
  the bits per weight) instead of FP32 for some formats and Q8_0 for others, and the example sizes follow from it; the
  tokens-per-second table and the speed-up factors without a source are removed; the Hailo note no longer states "not
  for LLM inference" as a fact but says to check Hailo's current documentation; the old TensorRT-LLM pin v0.10.0 is
  marked as not checked against current releases.
- `2026-10-16 02:19 UTC` · `v3.1.0` **Build progress counts the running build** (released in 3.0.0): `get_build_status` said 0 % for a request with
  one build until the build ended, and named only the request's stage. The percent and the step now include the
  running build's own progress (preparing, image, model, modules, archive); a request that still runs never says
  100 %.

### Security
- `2026-10-23 22:37 UTC` · `v3.1.0` **Every reach-out of offline mode now goes through the locality rule** (DECISIONS round 24 L-NET, round 26 d-g):
  a Qdrant server (`qdrant_url`) is judged by it, a cloud Qdrant is refused in plain words, and qdrant-client
  connects only to the checked addresses without a proxy and without its own version request; the sign-in
  **Check**, `ditto login --check` and the AI settings' sign-in panel refuse a provider offline mode may not reach
  before any socket opens; the llama.cpp repository override of an image build (`LLAMA_CPP_REPO_OVERRIDE`) and the
  pin-bump check (`scripts/llama_cpp_pin.py`) reach only a mirror in your own network, pinned like the source fetch
  (offline mode builds no target image, so the image built earlier with the same override is used); an international mirror host is refused
  until it is written in its `xn--` form (Python and git's curl convert a few characters differently). A crawl never
  reads an IPv4 link-local address or a cloud metadata service (AWS, Google, Azure, Oracle, OpenStack, Akamai/Linode,
  Alibaba, Scaleway, Tencent, from their documentation; also IPv4-mapped, NAT64 and 6to4 spellings, and a name with
  one such address among others). Online, a custom server labelled local by its address (its prompts go without the
  privacy filter) is reached only at its checked local addresses, so a DNS answer that changes cannot send them to a
  public host; a turn without the privacy filter always gets that pinned client, also when the name's answer changed
  between the turn's check and its connection. The pinned clients leave out only the proxy: an own CA named in
  `SSL_CERT_FILE` or `SSL_CERT_DIR` stays trusted, as with the SDK's own client. A Qdrant server opened online is
  opened again under the rule once offline mode is switched on (a cloud Qdrant is refused then), and a crawl that
  started online reads nothing more once offline mode is switched on.
- `2026-10-23 23:28 UTC` · `v3.1.0` **Hugging Face tokens leave this computer only where Hugging Face needs one, and no library sends one by itself**
  (consolidation chain hf-local; decision round 25 Q2). Every look-up, file list and file of a public repository used
  to carry the token stored in the framework (or typed in the browser) to huggingface.co; now none of them does, also
  with a token stored. The token goes only to a gated or private repository, only to huggingface.co: from its
  repository information (`hf_hub.needs_token`: gated or private) or after the Hub answered 401/403 to a request
  without it, then one request with it (`hf_hub.tokenless_first`, `HubFetcher(token_needed=...)`), for the Builder's
  staging, the build plan's look-up, the GUI's commit check, the Hugging Face browser, the Take over and every AI
  caller; searches and listings never carry it. The process runs with huggingface_hub's
  `HF_HUB_DISABLE_IMPLICIT_TOKEN` and `HF_HUB_DISABLE_TELEMETRY` on, set when the package `orchestrator` is imported
  and also after a library imported huggingface_hub first (`orchestrator/utils/hf_env.py`; huggingface_hub 1.33 reads
  both once, in `huggingface_hub.constants`): no library sends `HF_TOKEN` or the Hugging Face home folder's token file
  by itself, and huggingface_hub's daily agent-harnesses registry request to the host in `HF_ENDPOINT` stops (its user
  agent asks `detect_agent()` only while telemetry is on). The knowledge base's fastembed models no longer come
  through fastembed's own download with huggingface_hub's defaults: the framework fetches them through its verified
  fetcher into fastembed's cache, without any token and only from huggingface.co (`hf_hub.fetch_snapshot`, handed to
  the embedders by the Core), and fastembed loads that folder without contacting anybody; in offline mode only a copy
  that is there. Regression tests: `tests/test_hf_tokens.py` (`HF_ENDPOINT` on another host with `HF_TOKEN` set: no
  request carries a token anywhere but huggingface.co; a public file without an Authorization header; the fastembed
  path without a token; offline mode without a socket; the agent-harnesses request), `tests/test_hf_online.py`.
- `2026-10-23 09:15 UTC` · `v3.1.0` **`install.sh` installs Docker only with a checksum** (DECISIONS round 23, item 3): when Docker was missing, the
  installer downloaded `get.docker.com` and ran it as root without any check. Now Docker comes only from Docker's
  official apt repository on Debian, Ubuntu and Raspberry Pi OS (32-bit gets Debian's armhf packages, as Docker's guide
  says from Engine 29 on). Systems built on Ubuntu or Debian (`ID_LIKE`: Linux Mint, Pop!_OS, LMDE and the like), which
  `get.docker.com` served as well, get the packages of the release they are built on, as before (`UBUNTU_CODENAME`;
  `DEBIAN_CODENAME` or `/etc/debian_version`, which also gives Debian testing its codename). The signing key is read
  into a throwaway keyring and trusted only when the download holds exactly one key whose primary fingerprint is the one
  Docker's install guide publishes (`9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`, pinned in the script with its
  source); apt then checks every package against the signed package lists. Nothing is written before the check, and the
  check starts no gpg-agent that would outlive the installer. An apt source for Docker that is already there is kept as
  it is; a commented-out or `Enabled: no` one (as a release upgrade of Ubuntu leaves it) does not count. A system
  without a package manager the installer uses (the other families: see Changed), ARMv6 (Raspberry Pi 1, Zero), a key
  that does not match or cannot be downloaded, and a failing apt stop in plain words with Docker's manual install link;
  a new apt source that breaks `apt-get update` is taken back. No downloaded script runs any more. Tests run the
  installer in a sandbox with fake curl, apt-get, dpkg and uname and a real gpg key made for the test; `install.sh` is
  ShellCheck-clean (the two findings of the base are fixed too).
- `2026-10-23 09:15 UTC` · `v3.1.0` **The installers no longer download pip without a checksum**: `install.sh` and `install.bat` ran
  `pip install --upgrade pip` before the hash-checked install, which fetched the newest pip from the package index
  unchecked (on Windows as Administrator). The virtual environment now keeps the pip that comes with Python, which
  is enough for `--require-hashes --no-deps`; the installer test holds every pip call to the hash-pinned export.
  `install.bat` downloads nothing else unchecked (winget checks the Python installer against its manifest's SHA256).
- `2026-10-23 09:33 UTC` · `v3.1.0` **Loose knowledge files are no longer imported without the pack checks** (DECISIONS round 23 item 1): every `.json`
  file in `community/knowledge/` and every `knowledge.json` in a folder without the pack's provenance was imported
  into the knowledge base at each start, with no manifest, signature, quarantine or validator. Only installed packs
  are imported now, and only while their files match what was installed; a loose file waits until the person sorts it
  through the checks of an installed pack. A pack's page can no longer set the fields the import sets itself
  (`origin_pack`, `origin_sha256`, `topic`, `target`), and a topic or target reaches an AI client only as a plain name.
- `2026-10-23 09:15 UTC` · `v3.1.0` **A restarted local model server's new key never goes to its old port** (chain port-runtime, from the review of the
  managed llama-server): every start gives the server a new port and a new key. `LLMClient` resolved the target
  (with the URL) and then built the adapter with a credential that read the server's state again, so a restart in
  between paired the old URL with the new key and sent it to whatever listened on the old port by then. The adapter
  now reads URL and key of the local server once, together, and refuses when the URL changed since the target was
  resolved ("the local model server was stopped or restarted; try again", an outage for the fallback chain), so
  nothing is built or sent for the old port (`orchestrator/llm/client.py` `adapter`; the credential resolver takes
  that key, `CredentialResolver.resolve(..., runtime_key=...)`). Regression test: `tests/test_runtime.py`
  `test_a_new_key_never_goes_to_the_port_of_the_previous_server`.
- `2026-10-23 09:12 UTC` · `v3.1.0` **Another program on the local model server's port never receives its key or a prompt** (chain port-runtime,
  from the review of the managed llama-server): `runtime start` chose a free port, started llama-server on it and
  took any answer of `/health` on that port for the server. A program that took the port between the choice and
  llama-server's bind (another process of any user on this computer) was then sent the new key by the key check,
  and the endpoint handed to Ditto pointed at it, so it would also have received the prompts. An answer now counts
  only while llama-server itself listens on the port (its connection table through psutil); otherwise llama-server
  exits at once (it found the port taken), the start stops it and tries another port, and nothing is sent to the
  other program. Where the connection table cannot be read, a server that keeps running for 10 s has bound its
  port and is used as before. Bare metal only: in Docker mode Docker binds the published port itself, and a taken
  port fails the start. Review of port-runtime: `server.json` is written right after the launch and LLMClient in
  every other process (the GUI, the MCP server) reads it, so while the check still ran a chat sent there got the
  other program's port with the new key. The record is now marked (`"port_unconfirmed": true`) until llama-server is
  listed as the port's listener (it binds before it loads the model, so the endpoint is still handed out while the
  model loads), and `read_endpoint` hands a marked record to no one; a start that ended before the check leaves a
  server the next start stops. Regression tests: `tests/test_runtime.py`
  `test_a_program_answering_on_the_chosen_port_never_gets_the_key`,
  `test_an_unreliable_connection_table_does_not_block_the_start`,
  `test_other_processes_get_no_endpoint_before_the_port_is_confirmed`,
  `test_a_server_that_holds_its_port_is_handed_out_while_it_loads`,
  `test_a_start_that_died_before_the_port_was_confirmed_leaves_no_server_behind`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **A provider or model chosen for one command never becomes a saved setting** (port of the kept worktree
  ditto-rewire-verified): `llm-cli ditto ask --provider/--model` and the effort of `ditto eval` were set as ordinary
  runtime values, which `save_user_config` writes, so a settings save during the command (the `update_settings` tool
  you confirm) saved the one-command choice as the AI settings. They are now set with the settings source `cli`,
  which is never saved and which a reload from `config.yml` does not replace (reason `command_line`). A provider other
  than the saved one also drops the saved provider's model, per-task models, server address and sign-in method for
  the question or run (`Core/ai_override.override_values(..., ditto=...)`, shared by the CLI, the module wizard, the
  chat panel's **For this question** and **Ditto > Evaluation...**), so a saved Ollama or custom address never
  receives another provider's requests; a model applies to every task. Regression tests: `tests/test_ditto.py`
  `test_one_command_provider_and_model_are_never_saved`, `test_the_eval_effort_is_never_saved`,
  `tests/test_ditto_models_core.py` `TestOneRunProviderSwitch`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **Model output cannot steer the terminal**: `llm-cli ditto ask` printed the answer, error texts quoting a provider
  or a tool, and tool lines (a made-up tool name, a handler's error) as they came, so escape sequences steered by a
  build log or a web page could rewrite the screen, the window title or the clipboard. Control characters (C0 with
  carriage return, DEL, C1) and format characters (bidi controls, zero-width characters) are removed there and from
  the line of `module generate-ai` that says the AI's base image cannot be pinned; the zero-width joiner inside an
  emoji sequence stays, as in the cleaning of outside text (`utils/untrusted`), and umlauts, CJK and other scripts are
  shown as they are. The `module generate-ai` proposal and its facts show hidden characters as escapes
  (`tools.confirmation.visible`). The self-healing proposal a build shows at the terminal (summary, cause, fix) and
  the AI clients' fix proposals in `llm-cli build status` go through the same cleaning (`cli._printable`); before,
  they only had their markup escaped, and the client proposals lost format characters but kept ESC, C1 and BEL.
  Regression tests: `tests/test_ditto.py`
  `test_ditto_ask_keeps_control_characters_of_the_model_out_of_the_terminal`,
  `test_the_terminal_text_keeps_every_script_and_emoji_as_they_are`,
  `test_generate_ai_keeps_control_characters_of_the_base_image_out_of_the_pin_error` and the tests next to them,
  `tests/test_ai_safety.py` `test_the_proposal_loses_its_control_characters_and_keeps_its_text`,
  `tests/test_client_proposals.py` `test_cli_prints_the_proposal_without_control_characters`.
- `2026-10-23 10:01 UTC` · `v3.1.0` **The AI settings dialog's model list never sends a key to the saved provider's address**: choosing another
  provider before typing its address (a saved Ollama on the local network, then **custom**) listed models at the
  saved provider's server with the other provider's key. The address field now always counts for the list, an empty
  one meaning the provider's own endpoint, also over the provider's own address from `llm-cli ditto login
  --base-url` (which the field shows and the save replaces), so a key typed for a new address never goes to the old
  one. Regression tests: `tests/test_dialogs.py` `test_model_list_never_sends_a_key_to_the_saved_providers_url`,
  `test_model_list_sends_a_typed_key_only_to_the_typed_address`.
- `2026-10-23 03:09 UTC` · `v3.1.0` **The Hugging Face token goes only to huggingface.co, also when `HF_ENDPOINT` names another host** (review of
  chain hf): huggingface_hub sends its API calls to the host in the `HF_ENDPOINT` environment variable (a mirror).
  The commit lookup of the GUI browser's download and the Hub list of **Take over old downloads...** sent the token
  the person typed or stored there, and so did every search and lookup of an AI caller, while the fetcher sends it
  only to huggingface.co. Every Hub API call of `orchestrator/Core/hf_hub.py` (`HubClient`) and of the browser's
  download and Take over (`ModelManager`) now goes through `hf_hub.hub_api`, pinned to huggingface.co. Regression
  tests: `tests/test_hf_gui_download.py` `TestTheTokenGoesOnlyToHuggingFace`.
- `2026-10-23 11:57 UTC` · `v3.1.0` **No Hugging Face call of the framework's own code sends a token to the host in `HF_ENDPOINT` or an implicit
  `HF_TOKEN` any more** (consolidation chain hf-online). Outside `hf_hub` four callers still made their own
  `HfApi()`: the Builder's staging of a Hugging Face model, the build plan's look-up (`hf_lookup.look_up`) and the
  GUI's commit check of an identical build (`BuildService.model_commit`) sent the token stored in the framework to
  the host in `HF_ENDPOINT`, and the Hugging Face browser's search, licence check and file list
  (`ModelManager.search_huggingface_models`, `check_license`, `list_repo_files`) sent the implicit token (`HF_TOKEN`
  or the Hugging Face home folder's token file) there. Every one of them now goes through `hf_hub.hub_api`
  (huggingface.co only; the token the person typed in the browser, else the one stored in the framework, else none),
  and every file list is read by `hf_hub.hub_file` (the Builder's `_hub_files` delegates to it). The browser's search
  and licence check use the stored token where they used the implicit one. A mirror named in `HF_ENDPOINT` is no
  longer used by any of these calls either, as for the browser's downloads since A6; fastembed, the knowledge
  base's embedding library, fetched its models with huggingface_hub's own defaults until its models came through
  the framework's fetcher (the entry "Hugging Face tokens leave this computer only where Hugging Face needs one"). A redirect of an API answer to another host is followed without the token. Regression tests:
  `tests/test_hf_online.py` drives every caller through huggingface_hub's own HTTP client with `HF_ENDPOINT` naming
  another host and `HF_TOKEN` set, and fails on a token sent to that host, on the implicit token, on a token that
  follows a redirect to another host and on an `HfApi` made outside `hf_hub`.
- `2026-10-22 15:49 UTC` · `v3.1.0` **No other account's home folder or name reaches an AI through a file URL or a space** (D0; default of
  2026-10-17). The path labels for AIs (`security.paths.label_paths`, also inside `device_text`) left the path of a
  `file:///` URL as it was when it named another account's home folder (`file:///home/<name>/...`,
  `file:///C:/Users/<name>/...`, `file://localhost/...`, `file://server/Users/<name>`); its path is now labelled like
  any other (the file name, a known folder's label, "a home folder"), and the URL keeps its `file://`; so does a
  version-control file URL as pip and uv write it (`git+file:///home/<name>/pkg`, also `svn+`, `hg+`, `bzr+`), which
  keeps its scheme. An account name
  with a space was cut at the space ("a home folder Doe"): this computer's own account name is now taken whole after
  any root, and after another account's Windows home folder the following words that start with a capital letter or
  a digit belong to the name (a POSIX account name has no space, so `/home/pi Failed` keeps its next word).
- `2026-10-17 16:42 UTC` · `v3.1.0` **The GUI tool hides this computer's home folder in every spelling**: a form value an AI reads through the `gui`
  tool (Ditto or an AI app) showed the home folder as `~` only when it was written exactly as `Path.home()` gives it.
  Named by HOME or USERPROFILE (the MSYS form `/c/Users/<name>`), with the other separator, in another case
  (`c:/users/<name>`) or with doubled backslashes as Python quotes a path, it reached the AI with the account name.
  `tools_gui._no_home` now uses the shared `security.paths.shorten_home` (the one the runtime and deploy tools use)
  before the pattern for other accounts' home folders. The problem report (`utils/telemetry.py`, the GitHub issue
  link) replaced only `os.path.expanduser("~")` letter by letter; it now replaces every spelling through the same
  `security.paths` functions and keeps its `~USER` marker.
- `2026-10-17 15:44 UTC` · `v3.1.0` **Every place takes the same SSH user names**: the deploy (`llm-cli deploy run`, **Deploy to Target** in the
  Deployment tab) and `llm-cli deploy devices add` checked the user name without `re.ASCII`, so Python's case folding
  let a long s (`ſ`) or the Kelvin sign pass as `s` and `k`, and the deploy's `match()` also let a trailing line break
  through. Such a deploy ran, and the board's trusted record then refused the same name without a word, so AI tools
  could not name the board. One check now serves every place (`orchestrator/remote/address.py`, `valid_user`: ASCII
  only, the whole name, root only where it is allowed): the deploy, adding a board, the Deployment tab, the
  trusted-board records, the deploy settings, the systemd unit and the account names an AI never reads
  (`device_text`). Adding a board no longer loads the build engine and the Docker SDK for this check. The board scan
  (`llm-cli scan --device`, **Remote Scan via SSH...** in the Builder tab) checks the name the same way before the
  board is contacted, with the refusal in the person's language; before, such a name reached the board's login.
- `2026-10-17 18:19 UTC` · `v3.1.0` **The AI settings dialog refuses a server address with a user name or password in it**, as sign-in and
  `llmcf ditto login` already did (the same address rule from `AISettingsChange`): before, such an address (or one
  with a space or a backslash, which parsers can read as two different hosts) was saved to `config.yml` as typed.
- `2026-10-17 15:51 UTC` · `v3.1.0` **A community recipe built from the GUI is checked when it is built, as over MCP**: the Community Hub's **Build**
  button and the Builder tab's **Start Build** go through `BuildService.installed_recipe` and
  `BuildService.start_recipe`, the path `start_recipe_build` uses. A recipe the community index revoked after the
  Hub's list was shown, or one that can no longer be read, is refused at the click and again at Start Build; before,
  only the Build button the list had disabled stood in the way, and the GUI parsed `recipe.yml` itself. A build that
  keeps every value of the recipe now pins its calibration dataset: a file other than the one the recipe's sha256
  names is refused, and the build engine checks the copy it uses. A recipe that changed since it filled the form is
  refused too. Changing a value of the recipe in the form still builds your own values at the recipe's commit,
  after the same revocation check; so does a calibration dataset for a recipe that names none, or none for a recipe
  that names one, as before.
- `2026-10-17 16:54 UTC` · `v3.1.0` **Text hidden from a reader never reaches the knowledge base** (recon P1): text that a web page hides from the person
  reading it (inline `display:none` or `visibility:hidden`, a font size or opacity near zero, off-screen or clipped
  boxes, text in its own background colour, the `hidden` attribute, `aria-hidden="true"`, `<template>`, `<noscript>`,
  and a CDATA section, which the converter even turned into page text) reached the knowledge base unchanged and with
  it any instruction for an AI it carried. Deep Ingest now drops those
  elements before it reads a page's text and links (one rule, `hidden_kind` in `rag/html_text.py`, which the
  crawler's boilerplate step uses, so a link a reader cannot see is not followed either); the inactive panel of a tab
  widget stays. A page is read the way a browser reads it: the inline style with `!important`, CSS comments and
  escapes, transparent text (not gradient text), `scale(0)`, `max-height: 0`, large negative margins and
  translations, `filter: opacity(0)` and `content-visibility: hidden`; the fallback text of `<iframe>`, `<canvas>`,
  `<video>` and `<audio>`, `<noembed>`, `<noframes>`, `<datalist>`, `<rp>`, a closed `<dialog>` and a second
  `<title>`; a hiding element written as a self-closing tag (`<div hidden/>`) holds what follows it, and of an
  attribute written twice the first counts. Every outside text is then stored already cleaned the way a model's view
  of it is (NFKC, invisible and tag characters and HTML comments removed; `utils/untrusted.clean_untrusted`, the
  existing cleaning, now also reporting what it removed), not only cleaned when it is shown: crawled pages and PDFs
  (`RAGManager.ingest_page`), and every knowledge pack page, whose HTML is read like a crawled page or, inside
  Markdown, loses its hidden elements (code spans and code blocks stay; where a CommonMark rendering of the result
  still hides text, the visible text of that rendering is stored instead). A `<!--` that nothing closes in text
  already extracted loses only those characters, not the rest of the page. How much was left out, by kind, is the
  page's new provenance field `hidden_removed`, which only the framework writes: **Tools > Knowledge Sources...** and
  `llm-cli knowledge sources` show it in the new column "Hidden text left out"; it never reaches an AI and never goes
  into an exported pack. The
  framework's own documents are stored as written. Fixture pages that each hide one payload
  (`evals/retrieval/carriers/`) run through the crawler and the knowledge base in the tests and as their own group in
  the nightly retrieval evaluation (`llm-cli ditto eval --kind retrieval --cases evals/retrieval/carriers.yml`), which
  fails when a payload reaches the stored excerpts; the evaluation now indexes its corpus as outside text too.
  Pages stored before keep their excerpts until they are ingested again.
- `2026-10-22 21:55 UTC` · `v3.1.0` **A pull request can no longer rewrite the community repository's required check**: the check workflow template
  `scripts/community_repo/validate-contribution.yml` ran on `pull_request`, so a pull request from a fork ran its own
  edited copy: it could make the check pass and add a bad pack, or change `.github/`, `index.json`, `packs/` or a
  README, which the check never looked at (it listed only `contributions/`). The maintainer actions refused such a
  pull request, but a merge on the GitHub web page was not stopped. The check now runs on `pull_request_target`
  (opened, synchronize, reopened; pull requests into `main`), so the copy on `main` always checks, with
  `contents: read` only, no secret, no cache and `persist-credentials: false` on its one checkout, the framework's.
  The pull request's head commit is data only and never checked out (a checkout of it under `pull_request_target` is
  the known dangerous pattern, and actions/checkout prints its subject, where a carriage return would start a
  workflow command): after the framework is installed, `main` and the head commit are fetched by its id, quietly and
  without credentials, into a repository without a working tree, nothing of it is run, sourced, installed or
  imported, git alone reads it, and no value of the pull request (title, branch, body, commit messages, file names) is
  written into a `run:` script; it comes through `env`. git computes the changes between `main` and the head
  (`--raw -z --no-renames`, from their one merge base, as GitHub merges them; a pull request with no or more than one
  merge base fails, since GitHub's merge would then combine bases the check does not see, so a history that merges
  two lines of `main` again could undo a publication unseen). A file name with a control character, a symbolic link
  (a link named `x.zip` can no longer make the check read another file), an executable file or a submodule fails for
  everyone. Anyone but the owner, a member or a collaborator (`author_association`) may only add
  `contributions/<kind>/<name>.zip`, the paths the maintainer actions review; any other added, changed or deleted
  file fails with its name, the README of a kind folder included (it passed as a notice before). The owner's, a
  member's or a collaborator's other changes are notices, so the maintainer actions' own publication and revocation
  pull requests keep passing. Every added pack is still checked as before (at most 5 MB, `llm-cli zoo verify`), now
  from the blob of the head commit, and what zoo verify prints is never read as a workflow command. The CI's lint job
  also runs actionlint over the template. Tests run the real steps in bash against local git repositories with a stub
  `llm-cli` (including the maintainer actions' publication through the fake GitHub, a commit message with a carriage
  return and a history with two merge bases) and guard the workflow's structure. The community repository's copy is
  changed by its maintainer (the template says how to switch a copy that still runs on `pull_request`, and names the
  setting the repository needs as well: fork pull request workflows only with approval, because a pull request can
  still bring a workflow of its own with a job named `validate`).
- `2026-10-22 17:46 UTC` · `v3.1.0` **Dependencies on the newest versions that fit, with the advisories of 2026-10 fixed**: `uv lock --upgrade` moved
  every dependency to the newest version its range allows (among them pypdf 6.21.2, certifi 2026.10.19, paramiko
  5.1.0, qdrant-client 1.20.0, filelock 4.2.4), and Pillow goes to 13.0.0, which fixes 35 advisories of 12.3.0.
  fastembed (up to 0.9.0) still declares Pillow < 13; the framework uses only its text models and never decodes an
  image with Pillow, so `pyproject.toml` overrides that cap (`[tool.uv] override-dependencies`), and the installers
  and CI install the complete hash-pinned export with `--no-deps` so pip does not resolve it again: `requirements-gui.txt`
  (with the optional extra `gui`), `requirements.txt` (headless) and `requirements-dev.txt`, in the installers, the
  test, headless, lint and security jobs, the nightly retrieval evaluation and the community repository's workflow
  template. A test removes the
  override's reason as soon as fastembed allows Pillow 13 itself. The CI's `astral-sh/setup-uv` goes to v10.3.0
  (Dependabot PR #8). Checked with the full test suite and a real retrieval evaluation (88 % of the cases pass).
  Later the same day: anthropic 1.15.0, openai 3.35.1, fastembed 0.9.0 (it still caps Pillow, so the override
  stays), the build backend uv_build up to 0.13, and the Ubuntu 22.04 base of the GPU template and of the build-base
  catalogue (`configs/llama_runtime.yml`) to its current digest, both together (Dependabot PRs #10 to #12, applied by
  hand: Dependabot moved the template only and leaves the hash-pinned exports to us). Dependabot now opens its pull
  requests against `development`, where the work lands, instead of `main`.
- `2026-10-17 03:07 UTC` · `v3.1.0` **A base image an AI fills into the module wizard is pinned to its digest too**: `llm-cli module generate-ai` and
  the `create_target_module` tool write an AI's base image with the digest its tag points at, and so did the
  wizard for Ditto's own proposal; a base image an AI filled in through the GUI tools (Ditto in the chat or an AI
  app) was saved as a bare tag such as `ubuntu:24.04`. **Finish** now looks the digest up off the GUI thread (the same
  lookup, `ditto_manager.pin_reference`), shows the pinned image and asks first ("No" is the default); the module is
  saved through `DittoManager.save_module` with exactly that image. When the digest cannot be looked up (offline
  mode, no registry), nothing is saved and the window says why and what to do. The field keeps the AI's value and
  its mark.
- `2026-10-17 01:41 UTC` · `v3.1.0` **A source repository URL no longer takes a password or token**: `llm-cli config repos --add` and **File >
  Settings... > Add** stored a URL exactly as typed, so `https://user:password@...`, a token as the user name or a
  parameter such as `?private_token=` ended up in plain text in `config.yml`. Both now refuse such a URL through one
  core check (`settings_view.url_credential` in `add_repo_override`; exit code 2 in the CLI) with a plain reason that
  points to the secrets store (`llm-cli secrets set`, **Security > Secrets...**); a plain account name such as the
  one of an SSH address is no credential. An entry stored with one before still reaches the builds and is shown
  only with the credential replaced by `REDACTED` (the list, `config show`, File > Settings...).
- `2026-10-16 20:29 UTC` · `v3.1.0` **The module wizard saves Ditto's proposal the way `llm-cli module generate-ai` does**: it saved an AI-made module
  through the module generator alone, so DittoManager.save_module's checks were skipped (the refusal of any prebuilt
  `nvidia/cuda` image in an AI proposal, and the AI's reason required for an image that is not a Docker Official
  Image), and it did not pin the proposed base image to its digest. Now the proposal's base image is pinned in the
  analysis worker before you see it (`pin_base_image`, the CLI's function), and the module is saved through
  `DittoManager.save_module` and audited as on the command line; a base image an AI filled into the wizard through the
  GUI tools takes the same path. A prebuilt image you choose yourself stays your explicit opt-in. A rule-based probe
  import after Ditto's proposal now also replaces Ditto's packages, setup commands and quantization logic: they stayed
  in their fields and went into a module saved as yours, unmarked and not audited.
- `2026-10-16 09:39 UTC` · `v3.1.0` **Pairing a board sends only a checked key line** (bandit B601): the command that adds the framework's public key
  to the board's `authorized_keys` is fixed, and its one value, the key line, was shell-quoted. Now the line must also
  be a plain SSH public key (type, key, an optional simple comment); anything else is refused before the command is
  sent.
- `2026-10-16 01:03 UTC` · `v3.1.0` **`get_build_status` and `get_build_log` name finished builds by label, never by host path**: in 3.0.0 the status
  of a build request, and log lines such as "Golden Artifact created", gave AI clients the full path of each finished
  build (with the user name in it). The status now gives `output:<target>/<model>/<quantization>` (or `builds:`,
  `saved:`, a file name elsewhere), and a log line with a path on this computer names the framework's folders by
  label and any other path by its file name (also below `/home/`, `/media/`, `/Users/` and `C:\Users\`, for example a
  local model on a USB disk); your own windows keep the full path.
- `2026-10-15 21:27 UTC` · `v3.1.0` **No deletion without your confirmation in the framework** (round 5 f; WP7): every code path that deletes a built
  model, a knowledge snapshot, a trash entry, a framework image, an old llama.cpp runtime build or a board's runtime
  package takes only the plan you accepted in the GUI or at a terminal, builds it again from what is on disk right before, and deletes nothing when it changed or when the audit
  log cannot record it first. An AI's `delete_artifact` only moves to the trash; emptying the trash and deleting for
  good stay with you. A source sweep lists every place in the code that deletes, with how it is covered; a new one
  fails it until it is decided.
- `2026-10-15 20:45 UTC` · `v3.1.0` **Device passwords no longer belong on the command line** (sixth-round design 4.3): `deploy run`, `scan --device`
  and `device runtime deploy` ask with hidden input when `--password` has no value, and `--password-stdin` reads one
  line from a pipe (refused on a terminal, where the password would be shown while you type). paramiko's loggers are
  held at INFO or higher while a login authenticates, so a password never reaches a log, also at DEBUG.
- `2026-10-15 20:45 UTC` · `v3.1.0` **The Trivy scan no longer gets the Docker socket** (released in 3.0.0): the scanner container had the host's Docker
  socket, which gives root rights on this computer even when mounted read-only. The builder now saves the image to an
  archive in a private folder (0700) of the build, and Trivy reads only that file, read-only, without capabilities and
  with `no-new-privileges`. The log says when the archive is saved and removed again; without room for it on the cache
  disk the scan is skipped with a warning that names the sizes. When Docker refuses the scan, the warning names the
  archive by the label of the cache folder (`cache:...`), never by its path on this computer.
- `2026-10-15 20:45 UTC` · `v3.1.0` **The loopback MCP HTTP listener ignores proxy headers and writes no access log:** uvicorn trusted
  `X-Forwarded-For` and `X-Forwarded-Proto` from 127.0.0.1, so every local program could claim another address.
  `mcp serve --transport http` now runs with `proxy_headers=False` and `access_log=False`.
- `2026-10-15 20:45 UTC` · `v3.1.0` **The installer says what the `docker` group means:** `install.sh` still adds you to the group without asking
  (seventh round, item 6) and now says plainly that its members control the Docker service, which runs as root, so
  every program running as your user can get root rights; it also names the command that undoes it.
- `2026-10-12 18:37 UTC` · `v3.1.0` **`run_doctor` gives AI clients no host path** (released in 3.0.0): the findings an AI read named the per-user
  folder, the audit log, pack folders, the disk folders and whatever path a crashed check's error quoted, user name
  included. What an AI reads now names the framework's folders by label (`state:audit.db`, "the state folder",
  `targets:<pack>`) and any other path by its file name only, secrets redacted; `llm-cli doctor` (also `--json`)
  keeps the full paths for you.
- `2026-10-12 09:16 UTC` · `v3.1.0` **`run_benchmark` runs the very file it hashed**: the model is opened once, hashed through the open file and
  handed to llama-bench as that open file (an inherited descriptor natively, a private hard link in the sandbox),
  so a catalog model cannot be swapped for another model between the check and a native run.
- `2026-10-12 09:20 UTC` · `v3.1.0` **Board and local runtime tool errors name no host path**: paths in their messages appear as the framework's
  folder labels (`state:...`, `cache:...`, `output:...`, `models:...`) or as a file name only, never as a host path
  or `~/...` (also after a colon, as in `file:/...` or `user@board:/...`, and inside a `file:///` URL).
- `2026-10-12 05:51 UTC` · `v3.1.0` **robots.txt can no longer be switched off through crawl options**: the crawler takes only its own option keys
  (pages, depth, delay, PDFs, languages) and ignores any other key, including `respect_robots`, with a log line; the
  switch for tests with a fake web is a separate keyword-only flag that no product code passes (a guard test checks
  it). Strings in a job's result (for example the addresses a crawl skipped and the sites' reasons) are now cleaned,
  capped and marked as external text like the rest of a job's status.
- `2026-10-12 07:15 UTC` · `v3.1.0` **Device and benchmark failures no longer carry outside text or host paths into an AI's context**: when a job of
  `probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark` or `run_benchmark` failed, the error message
  held the device's stderr or `llama-bench`'s last output (which prints the metadata of the GGUF it loads, text an
  untrusted model controls) unmarked; a failed mktemp on the device put its stderr into `deploy_to_device`'s plain
  `reason`; and a packaging or deploy failure could name the build folder, the output folder or the home folder. The
  message and `reason` are now the framework's own sentences without host paths, and what the device or the program
  printed is returned in `get_job_status.external_text`, marked as external text (also for failed jobs).
- `2026-10-12 08:09 UTC` · `v3.1.0` **A cancelled `restore_knowledge_snapshot` job no longer replaces the knowledge base** (MCP design T10): a cancel
  (`cancel_job`, or the end of the session) marked the job cancelled at once while the restore went on. The restore
  now checks for the cancel after the backup and before the knowledge base is replaced, and leaves it as it was.
- `2026-10-12 08:09 UTC` · `v3.1.0` **A local model folder with pickle weights is no longer matched by a build standing approval** (design 6.10):
  pickle files beside a safetensors file (the whole folder is mounted into the build) counted as safetensors. Pickle
  weights anywhere in the folder now make it "pickle", so the person is asked.
- `2026-10-12 08:09 UTC` · `v3.1.0` **Every base model of a merged model is checked for gating** (design 6.10): a card naming several base models had
  only the first one looked up, so a gated second base model did not keep the build or download out of a standing
  approval. Each one is looked up now; a gated one, or one that cannot be looked up, keeps it out.
- `2026-10-12 04:22 UTC` · `v3.1.0` **The dangerous-command filter can no longer be bypassed with invisible or full-width characters** (Ditto's
  healing analyses and recommendations, and fix proposals of AI clients): it checks the text also as the GUI and
  the CLI show it (normalized, invisible characters and joiners removed), so `r<zero-width space>m -rf ~` or
  full-width letters no longer pass and then appear as a readable command. A client's proposal is kept in that
  cleaned form; the GUI's status bar notice names the build by its speaking name and shows the client's name
  cleaned.
- `2026-10-12 05:11 UTC` · `v3.1.0` **The base image lookup of a new target module stays on public addresses**: a registry (any public host an AI names
  in `base_os`) could redirect the anonymous digest lookup or its token request into the local network, because
  redirects were followed without a check. Every redirect is now followed by hand and must be https and public like
  the first address, connections go only to the address that was checked, and a registry's pull token is never sent
  to another host.
- `2026-10-12 05:11 UTC` · `v3.1.0` **`create_target_module` plans the `runtime:` block from exactly the probe the person was shown**: a probe replaced
  between the question and the write (for example `local` probed again while a pending approval waited) stops the
  write instead of being used.
- `2026-10-12 05:11 UTC` · `v3.1.0` **A failed `probe_local_hardware` job names no host path**: when the probe could not be stored, the job's error
  (which the AI client reads) carried the per-user folder's full path; it now gives only the reason, and
  `llm-cli scan` still shows the full error in the terminal.
- `2026-10-12 05:11 UTC` · `v3.1.0` **Ditto's module path follows the same base image rule as the MCP tool**: `llm-cli module generate-ai` refuses a base
  image that is not a Docker Official Image without the AI's `base_image_reason`, as `create_target_module` does.
  The counts of vendor sources, pip packages and downloads have no hard limit any more anywhere (they were refused
  above 20, 200 and 50); only the soft limits with their warning remain.
- `2026-10-11 22:39 UTC` · `v3.1.0` **AI clients never see the host path of a local model they named** (MCP clients and Ditto): the build's stage
  text, the self-healing context, the build manifest's `model_source` and `list_artifacts` name it by its
  `<label>:<relative path>` (an older manifest's host path by its label, else by the file name only).
- `2026-10-12 00:42 UTC` · `v3.1.0` **An MCP client can no longer skip a question's record and limits by sending an answer along with its call**
  (2026-07-28 rounds): a round counts as an answer only to a question the framework asked that client and that is
  still open. Before, any call that carried some answer was asked without the write-ahead `confirmation_requested`
  entry and without the 10-minute wait after a "no", the pause after three "no" and the questions-per-hour count.
- `2026-10-12 01:05 UTC` · `v3.1.0` Ditto's build recommendation and the `recommend_build_settings` prompt give the model the target's formats,
  quantization types and format notes (text from `target.yml`, which a community target pack installs) inside an
  untrusted block; before, they stood as plain text next to the instructions.
- `2026-10-12 00:11 UTC` · `v3.1.0` `LCF_MCP_TOKEN` is taken out of the environment by `llm-cli` itself before any command runs and handed only to
  `mcp serve`; before, only `mcp serve` removed it, so every other command and its child processes inherited it.
- `2026-10-12 00:11 UTC` · `v3.1.0` MCP server: whatever runs outside a request that the server checked gets the deny-all caller, also on a stdio
  server, which before fell back to its own caller.
- `2026-10-12 00:11 UTC` · `v3.1.0` The MCP token store is cached by the SHA-256 of the file's bytes instead of its identity, size and modification
  time, so a running server cannot keep accepting a revoked or rotated token after a same-size change within the time
  stamp's resolution.
- `2026-10-10 23:50 UTC` · `v3.1.0` Ditto discards a healing fix or a build recommendation that contains a dangerous command (a download piped
  into a shell, deleting the root or home folder, world-writable permissions, overwriting a disk), whatever a
  build log or a model name told the model.
- `2026-10-10 23:50 UTC` · `v3.1.0` Requests to local model servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment.
- `2026-10-12 00:37 UTC` · `v3.1.0` Local model downloads are refused unless size and sha256 are pinned; the official prebuilt server archive (only
  with `runtime install --prebuilt`) is verified the same way, installed atomically, and never offered on musl or on
  an older glibc than the one it was built against.
- `2026-10-12 00:37 UTC` · `v3.1.0` One launch policy for every llama.cpp program the framework starts (`orchestrator/runtime/server_policy.py`):
  Ditto's server, the build checks, the perplexity check, the start script and systemd unit on a board and the
  checks there share one list of forbidden options (tools, agent mode, MCP, web UI, remote compute, downloads, a
  prompt log on disk, a port another process may bind as well (`--reuse-port`), a key or token on the command line,
  in every spelling llama.cpp accepts, also `--a_b` for `--a-b`) and a cleared environment, and every server listens on 127.0.0.1 only and reads its key from a private
  file, held to the same server options (Ditto's command line is checked again right before the start, the start
  script's when it is generated). `runtime start` starts only a build that
  passed its verification on this machine, still matches its manifest and is of the pinned commit (else its CPU
  fallback, else nothing); a self-built build that cannot be used never falls back to the prebuilt archive on its
  own, and an official archive an earlier version downloaded is used only after `runtime install --prebuilt` (which
  takes it without downloading it again).
- `2026-10-11 03:30 UTC` · `v3.1.0` Deep Ingest reads robots.txt as RFC 9309 describes it, with one cautious deviation. Rules with `*` or `$` (such
  as `Disallow: /*.pdf$`), an `Allow` listed before a longer `Disallow`, every `User-agent: *` group after the
  first and a file that starts with a byte order mark used to be ignored, so the crawler fetched pages the site had
  excluded. Its groups are found by the exact product token `LLM-Cross-Compiler-Framework-Bot` or its earlier name
  `LLM-Conversion-Framework-Bot` (any case), so a group for `bot` no longer overrides `*`, and each scheme, host and
  port has its own robots.txt. The deviation: a robots.txt that answers 401 or 403 means "disallow everything", where
  RFC 9309 would allow crawling; a reservation the crawler cannot read still counts.
- `2026-10-11 16:13 UTC` · `v3.1.0` Deep Ingest reads every response up to a limit (HTML pages 8 MiB, PDFs 64 MiB, robots.txt 500 KiB,
  `tdmrep.json` 1 MiB) and within a deadline for the whole request (60 s, PDFs 180 s). Before, a site could send an
  endless or dripping response that filled the memory or held the crawl, and Stop or closing the wizard, forever:
  the HTTP timeout only bounds each socket read. A larger page is skipped and a slower one fails, each named in the
  crawl log; Stop no longer waits for a page that is still arriving.
- `2026-10-11 02:42 UTC` · `v3.1.0` Community packs are installed only through a quarantine folder after their checks: an id without paths, exactly
  the listed files with their hashes, no links, special or binary files, every image a Dockerfile pulls (`FROM`,
  `COPY --from`, `RUN --mount=...,from=`, also in a lowercase `dockerfile`) pinned by digest unless it is an
  official base image, the shell deny-list on every script line that is not from the shipped template, and
  `bash -n`. A copy of the shipped Rockchip target passes these checks. A pack appears under its final name only
  when it is complete, never over an existing target (also when only the case differs). Before, a link in a
  community module could copy any file of the user into `targets/`.
- `2026-10-11 01:54 UTC` · `v3.1.0` The Docker build context never follows symbolic links or special files of a target, a linked Dockerfile is
  refused, and a build's target name cannot contain a path.
- `2026-10-11 00:21 UTC` · `v3.1.0` `OPENAI_CUSTOM_HEADERS` and `ANTHROPIC_CUSTOM_HEADERS` are dropped at startup of the CLI, the GUI and the MCP
  server, with a warning, unless the setting `ai_allow_sdk_env_headers` is true (a quoted `"false"` counts as
  false; settings that cannot be read count as false): they could replace the stored API key and reach every
  server, local ones included. The OpenAI and OpenAI-compatible adapters also send the stored key as an
  explicit `Authorization` header.
- `2026-10-11 02:06 UTC` · `v3.1.0` The debug output of the provider SDKs and HTTP libraries (request options, prompts, headers) stays off unless
  `--debug-http` is given: their loggers never go below INFO, even with `--log-level DEBUG`, and
  `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` are lowered to `info`.
- `2026-10-11 00:21 UTC` · `v3.1.0` Claude Admin API keys (`sk-ant-admin01-`) are refused before any request is sent, also when they were stored
  with surrounding spaces.
- `2026-10-11 00:29 UTC` · `v3.1.0` The credential vault is changed under a cross-process lock: the GUI, the CLI and an MCP server writing at
  the same time no longer lose each other's changes.
- `2026-10-11 00:29 UTC` · `v3.1.0` A keyring that keeps nothing (such as `keyring.backends.null`) or does not answer within 3 seconds is no
  longer used; before, secrets stored with it were unreadable after the next start. A keyring backend that
  stores without encryption (`keyrings.alt`) is not used either: that is `secret_storage: insecure`, and
  only you can choose it.
- `2026-10-11 00:29 UTC` · `v3.1.0` On Windows the vault key stays on the local machine instead of roaming with the user profile.
- `2026-10-11 00:29 UTC` · `v3.1.0` The secrets store refuses a location inside a git work tree (set `LLM_FRAMEWORK_HOME` elsewhere).
- `2026-10-11 01:34 UTC` · `v3.1.0` Secret names with line breaks or control characters are refused, and the secrets audit log escapes them,
  so a name (or a user name) cannot forge audit entries.
- `2026-10-11 00:59 UTC` · `v3.1.0` **Credentials are redacted in every log**: the framework's own, those of the provider SDKs and HTTP libraries
  (anthropic, openai, httpx, httpcore, urllib3, botocore, google.auth, azure, msal), tracebacks and the extra
  fields of the JSON log format. Redacted are the values of credential headers (`Authorization`, `x-api-key`,
  `api-key`, `x-goog-api-key`, `Cookie`, `Set-Cookie`, also in JSON with escaped quotes), sign-in and key
  parameters in URLs and form-encoded token requests (`code`, `state`, `key`, `access_token`, `token`, ...)
  and every known key format.
- `2026-10-11 01:30 UTC` · `v3.1.0` Redaction also finds credentials by their context, whatever their format: a bearer token quoted in a server's
  error message, token-shaped values of `api_key`, `secret`, `token` or `password` fields, and environment
  lines such as `HF_TOKEN=...` or `export OPENAI_API_KEY=...`. Tokenizer settings in build logs (for example
  `"pad_token": "<|...|>"`) stay readable.
- `2026-10-11 01:30 UTC` · `v3.1.0` Redaction can no longer be slowed down by crafted text: an error message or build log with a long run of
  `sk-` took minutes to redact before.
- `2026-10-11 00:59 UTC` · `v3.1.0` **One table of secret formats** (`orchestrator/security/secret_patterns.py`) for the secret scan and for
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
- `2026-10-11 00:59 UTC` · `v3.1.0` API keys held in memory by Ditto's provider layer show as `<redacted>` in `str()` and `repr()` and cannot be
  pickled.
- `2026-10-11 03:18 UTC` · `v3.1.0` A key of a custom OpenAI-compatible server belongs to the address it was saved for (scheme, host and port):
  after the base URL changes it is no longer sent. A key saved by an earlier version is bound to the address it
  is next used with. Claude Admin API keys are also refused for a Claude API preset in your own provider
  catalog that does not list them.
- `2026-10-11 03:45 UTC` · `v3.1.0` Sign-in hardening: the key hint in the AI settings never binds a key of an earlier version to an address that
  is still being typed; signing out also removes such a key; the credential check sends a key bound to one
  address to that address only; a key variable of a local engine in your own catalog goes only to the address
  the catalog gives. A non-secret field (workspace ID, project) can no longer be declared with a credential
  header such as `Authorization` or `x-api-key`, where it would replace the stored key. A failed token renewal
  names only the kind of failure, and a vendor message that quotes the refresh token is shown without it. A key
  of an earlier version is bound to the address before a check with typed fields sends it there.
- `2026-10-11 04:36 UTC` · `v3.1.0` Sign-in never takes a secret from the command line (`ditto login` has no option for a key, and an unknown
  provider name or `--method` value that looks like a key is not echoed), shows a key only as its last four
  characters (also in `auth status --json`, and as `REDACTED` when a server's error text or account name repeats
  it, in Ditto's chat errors too), asks before a key goes over plain `http://` to another computer (also in the
  sign-in panel and the AI settings' key field; `--yes` in scripts) and never chooses how the vault is protected:
  `--insecure-storage` never replaces a protection that works, nor a credential helper or passphrase you chose. A
  key with a space, a line break or an invisible character inside is refused before anything is sent or stored.
- `2026-10-11 13:50 UTC` · `v3.1.0` The Builder log shows every message as plain text, so names from community recipes, Ditto's answers or build
  output cannot render as HTML (links, images, formatting).
- `2026-10-11 20:08 UTC` · `v3.1.0` Hardware profiles carry no identifying values: the probe scripts no longer write the host name, the RAM part
  numbers or the CPU id, the serial line of `/proc/cpuinfo` and the serial attribute of USB devices are never read,
  and free text is reduced to printable ASCII. Values read on a board are validated before they are written, and the
  SSH scan treats the board's answer as untrusted (size limit, strict parser).
- `2026-10-11 18:30 UTC` · `v3.1.0` **Calls that change something run only when they are recorded** (Ditto and MCP clients): before such a call
  runs, a `started` entry is written to the audit log; if it cannot be written, the call is refused, and a question
  about it is not asked. Read-only calls stay available. Before, a failed audit write was only logged and the call
  ran anyway, and the only entry was written after the handler, so a crash in the middle of a change left no trace.
- `2026-10-11 18:40 UTC` · `v3.1.0` **Every tool result is filtered centrally** (Ditto and MCP clients): secrets are redacted in every string of a
  result, external text (build logs and stage names, healing proposals, build manifests, knowledge excerpts, doctor
  messages, target labels and notes from `target.yml`) is cleaned of invisible and bidirectional control characters
  and capped, other strings are capped at 16 KiB, and a result over 256 KiB is refused with a hint how to ask for
  less. The error of a failed call is cleaned the same way before secrets are redacted, and an MCP client gets the
  part that quotes external text wrapped as untrusted data, as Ditto's model does. Before, only errors, warnings
  and log lines were redacted, only knowledge excerpts were cleaned, log lines and results had no size limit, and
  an MCP client got error text from a tool unmarked.
- `2026-10-11 18:55 UTC` · `v3.1.0` **New tools never inherit a loosened tier setting, and a tool's cap holds**: the permission file records the
  tools you have reviewed, and a tool added later uses its own default (or the recommended tier setting) until you
  review it, so allowing a tier once no longer allows every tool added to it later. A tool's own default (for
  example `deny` for an optional tool) holds even when its tier is allowed, but never opens a tier you denied, and a
  tool that reaches other devices or deletes data always asks at most. Before, a per-tool setting could allow such a
  tool without a question. A permission file from 3.0.0 counts its eleven tools as reviewed; 3.0.0 itself reads a
  file saved by this version as damaged and denies every tool.
- `2026-10-11 18:52 UTC` · `v3.1.0` MCP and Ditto: `start_build` no longer accepts a path for `model` or `dataset_path` (an MCP client could mount
  any readable file, such as `~/.ssh/id_ed25519`, into a build and read it back through the log). Only references
  inside the folders the AI may use are accepted; the resolved file must lie inside its folder and outside every
  denied folder (keys, credentials, AI clients' own sign-ins, the Hugging Face token, the framework's per-user
  folder, system folders), decided by file identity so a case-insensitive file system cannot hide one. Hard-linked
  files are refused, a folder is used only strictly below its label folder and without a denied folder, a
  hard-linked file, a socket, a device or a pipe inside, and the file is checked again right before each build
  container starts. A Hugging Face repo id from an AI caller is no longer looked up as a local path, neither by the
  build service nor by the build engine (which mounted a folder of that name below its working directory).
  Refusals name the label, never the real path.
- `2026-10-11 19:52 UTC` · `v3.1.0` **MCP over HTTP is closed to web pages and DNS rebinding.** In 3.0.0 a page served from any port on localhost
  passed the Origin check (only the token stopped it), the Host check accepted a loopback name with any port, and
  all HTTP clients shared one token and one audit actor. Now every request with an `Origin` header (also a CORS
  preflight) is refused before the token is checked, the `Host` header must be a loopback name with the server's
  own port (in the framework and again in the SDK), the server keeps at most 8 sessions (was 10,000) and takes at
  most 1 MiB per request (was 4 MiB), a session and a pending question round of the 2026-07-28 protocol only answer
  the token that opened them, and no token is accepted (and the server does not start) while other users can read or
  change the token file or it belongs to another user.
- `2026-10-11 23:59 UTC` · `v3.1.0` **No prebuilt llama.cpp in targets**: generated modules, community exports and target packs in quarantine are
  refused when a Dockerfile pulls a prebuilt llama.cpp image (by its name, also pinned by digest, or one of the
  framework's former runtime images) or copies llama.cpp binaries out of another image (also through a build stage
  that only names it), or when a Dockerfile, script (also one without a known suffix that starts with `#!`) or YAML
  file downloads llama.cpp release archives or binaries (also through a pipeline such as `curl ... | tar -x`), uses a
  prebuilt `llama-cpp-python` wheel index or installs a llama.cpp package: the framework shares recipes, not binaries. `nvidia/cuda` images no longer count as
  official base images and need a digest, and a llama.cpp image is refused as a module's base image.
- `2026-10-11 23:49 UTC` · `v3.1.0` The llama.cpp server on a board listens on 127.0.0.1 only and needs a key that is generated on the board and
  never leaves it. A deployment reaches only the board whose host key the profile was made with, runs as an
  unprivileged user (root is refused), and uses sudo only for a systemd unit you allowed in the deploy settings,
  never with a password prompt. Every value read from the board (home folder, user ID, architecture) is checked
  before it reaches a command.
- `2026-10-11 22:32 UTC` · `v3.1.0` **Only a person changes what an AI may do.** The tool permissions (`tool_policy.json`) and the MCP token store
  (`mcp_tokens.json`) are signed with a key derived from the audit key in the OS keyring. A file edited by hand,
  damaged, signed with another key or unsigned denies every tool, and the token store accepts no token, until a
  person sets them up again; `llm-cli doctor` reports it. Before, anyone who could write the per-user folder could
  allow every tool or add a token, and nothing noticed. Every change of the permissions is recorded in the audit log
  with the old and new decisions (actor `cli` or `gui`) before it takes effect, and is refused when the entry cannot
  be written. Saves take a revision number under a lock shared by the GUI, the CLI and MCP servers; a save based on
  an older revision is refused, so a window opened earlier can no longer undo a change made meanwhile. The newest
  saved revision of each file is kept in the keyring as well, so an older signed copy put back over it (a revoked
  standing approval or token would return) and a removed permission file deny everything too. An older copy of the
  token store is refused also when its bytes are the very ones a running MCP server or the GUI read before: the
  parsed tokens are cached by the SHA-256 of the file's bytes, and before, such a copy brought the revoked token
  back in that process. The revision is read before the file, so a token change saved meanwhile in another window
  never looks like an older copy. On Linux and macOS an older copy of either file is refused also when it is the
  very file a running process read before, put back through a hard link kept to it (the same inode, size and
  modification time): the revision in the keyring is read again also when the file's change time (ctime) moved. On
  Windows, where st_ctime is the creation time (which a hard link keeps), NTFS's own change time is used for this
  (GetFileInformationByHandleEx, no new dependency); before, a running process there still accepted the file put
  back. Where Windows gives no change time for the file (a file system that keeps none), the revision is read on
  every look instead of that check being skipped. Each framework home keeps its own revisions: two LLM_FRAMEWORK_HOME folders of one account shared one
  keyring entry, so a save in one made the other home's file an "older copy" (every tool denied, no token accepted).
  The default home (~/.llm-framework) keeps the entry of the versions before; another folder gets an entry named by
  a digest of the folder (no path in it). A home whose entry is still empty anchors the revision of the genuinely
  signed file it accepts at its first look, so an older copy put back afterwards is refused from then on, not only
  after the next save; it never writes when another process saved a newer file meanwhile (that would lower the kept
  revision below the other save). The builds the person confirmed (`confirmed_builds.json`) keep a revision in the keyring the
  same way: an older signed copy put back brought back a confirmation a later one had replaced (a module an AI
  recreated with its earlier content, a pack reinstalled in an earlier version), so a build standing approval covered
  it without the person; such a copy now holds no confirmation and the person is asked again. The pending approvals
  and the trash's list need none: a yes put back runs nothing unless it answers the question the holding process asks
  now, and an entry leaves the trash's list only once its item has left the trash folder. Regression tests:
  `tests/test_policy_signing.py` `TestOlderCopiesAndRemovedFiles`
  (`test_on_windows_the_ntfs_change_time_tells_the_very_file_put_back`,
  `test_the_ntfs_change_time_moves_when_the_file_is_put_back` on Windows,
  `test_two_framework_homes_keep_their_own_revision`,
  `test_a_home_without_a_kept_revision_anchors_the_file_it_accepts`), `tests/test_confirmed_builds.py`
  `test_an_older_copy_put_back_brings_no_replaced_confirmation_back`,
  `tests/test_approvals_two_phase.py` `test_an_older_copy_with_an_earlier_yes_runs_nothing_the_person_did_not_approve`,
  `tests/test_trash.py` `test_an_older_index_put_back_brings_nothing_back_to_restore_or_empty`.
  Without a keyring the signature has no key, as for the audit chain; such a file is refused while the audit log is
  kept with a key.
- `2026-10-12 01:44 UTC` · `v3.1.0` **Pending approvals and requests to stop asking are questions like any other.** They count toward the 30
  questions per hour; a "no" in the approval window stops the same call from being put to you again for 10 minutes
  and counts toward pausing the tool, as a "no" in the client's dialog does; a recent "no", a paused tool or a used-up
  hour refuses a new pending approval; and the limit of 5 open pending approvals is an audited refusal that never
  counts as a failed call. Before, a client that could not ask could queue questions without these limits.

- `2026-10-12 03:20 UTC` · `v3.1.0` **No implicit Hugging Face token**: builds sent `token=None` to huggingface_hub when no token was stored, so a token
  from `HF_TOKEN` or the Hugging Face home folder was used without the person giving it to the framework (G37).
  Every Hugging Face call of a build now sends the stored token or `token=False`.
- `2026-10-12 03:20 UTC` · `v3.1.0` **Image builds never read the MCP protocol stream**: `docker buildx build` ran with the server's stdin, which over
  stdio is the protocol stream (G25); it now gets `stdin=DEVNULL`.
- `2026-10-12 06:03 UTC` · `v3.1.0` **Deployment packages take images only pinned by digest or built here**: the default inference image was
  `...:latest` and a failed pull by tag was swallowed, so a package could carry whatever the registry served (G17).
  An image now goes into a package only as `name@sha256:...` or when it was built on this computer; a tag is refused
  with the reason, a missing pinned image is pulled by its digest only, and a failed export stops the package.
- `2026-10-12 06:03 UTC` · `v3.1.0` **Deploys never forward the MCP protocol stream**: without paramiko, `ssh` and `scp` ran with the framework's
  stdin, which over MCP stdio is the protocol stream (G25); they now get `stdin=DEVNULL`.
- `2026-10-12 10:35 UTC` · `v3.1.0` **A recipe's calibration dataset is checked and used as one**: `start_recipe_build` checked the dataset's sha256
  when the call was committed, but the build opened the path again later, so a file swapped in between was used. The
  build engine now opens the file once (never through a link), copies its bytes into the build's own folder while it
  hashes them, and uses only that copy, and only when its sha256 is the one the recipe pins (an RKNN sample list keeps
  its folder, with the checked list laid over its own name).
- `2026-10-12 10:35 UTC` · `v3.1.0` **Paths with spaces in errors toward an AI or the person**: a path with a space (a folder such as `My Models`, or a
  home folder whose user name holds one) was cut at the first space, so the label was wrong and a piece of the user
  name or of the host path could remain. Quoted paths, the framework's folders and the home folder (also by its real
  path when it is a link) are now replaced whole, a known folder further on in a path list (`/usr/bin:/home/...`) or
  in a quoted text with more than one path starts a path of its own, and a space continues a path when the next word
  is clearly part of it (or, inside a known folder, the longer path exists), so the error names a label or the file
  name only.

- `2026-10-12 18:24 UTC` · `v3.1.0` **No rich text for untrusted text in the chat panel and the windows it touches** (design 5.2 R1-R7, SEC-M2): every
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
- `2026-10-10 16:48 UTC` · `v3.0.0` **MCP server** (`llm-cli mcp serve`, official SDK `mcp` 2.3.0, spec 2026-07-28): Claude Code, Claude
  Desktop or VS Code can list targets, check capabilities, start/cancel builds, read logs and artifacts,
  search the knowledge base, run the doctor and decide on self-healing proposals. stdio by default;
  optional Streamable HTTP on loopback with a per-user bearer token. `llm-cli mcp config` prints the client
  setup, `--write claude-desktop` adds it to Claude Desktop.
- `2026-10-10 16:48 UTC` · `v3.0.0` **Tool registry with permissions and audit log** shared by Ditto and the MCP server: typed tools with risk
  tiers (read, local write, external, forbidden), permissions asked once at Ditto's first setup (GUI dialog,
  `llm-cli ditto setup`, `llm-cli tools permissions`), confirmations via MCP elicitation, and an HMAC-chained
  audit log (`llm-cli tools audit --verify`).
- `2026-10-10 16:48 UTC` · `v3.0.0` **Target capabilities** (`capabilities:` block in `target.yml`, pydantic schema): boards, formats,
  quantizations per board, defaults and calibration needs. GUI, CLI, build service and MCP tools offer and
  validate exactly what a target declares.
- `2026-10-10 16:48 UTC` · `v3.0.0` **Build service**: one typed entry point for builds (validate, start, status, cancel, logs, artifacts).
- `2026-10-10 16:48 UTC` · `v3.0.0` **`llm-cli doctor`**: deterministic health checks (Docker, disk, targets, security, tool permissions,
  calibration data) with fix hints, tiered automatic fixes and JSON output (inspired by "Mojo" in rag4ll).
- `2026-10-10 16:48 UTC` · `v3.0.0` **Curated calibration text** (`data/calibration`, MIT, checksummed German/English/tool-use sets) for
  importance matrices.
- `2026-10-10 16:48 UTC` · `v3.0.0` **Build manifests**: every build completes `build_info.json` with its model files and SHA256 hashes; the
  deployment window lists builds from these manifests.
- `2026-10-10 16:48 UTC` · `v3.0.0` **Real build cancellation and per-stage timeouts**.
- `2026-10-10 16:48 UTC` · `v3.0.0` **SSH trust on first use**: host key fingerprints are confirmed by the user and stored in the framework's
  own `known_hosts`; a framework Ed25519 key is installed on the device once ("pairing").
- `2026-10-10 16:48 UTC` · `v3.0.0` **Real CI workflows** (lint, tests on Ubuntu 3.11-3.14 and Windows, bandit, pip-audit, Docker build and
  smoke tests including the sandbox) and Dependabot.
- `2026-10-10 16:48 UTC` · `v3.0.0` Python 3.14 support; per-user state directory `~/.llm-framework` (`LLM_FRAMEWORK_HOME`).

### Changed
- `2026-10-10 16:48 UTC` · `v3.0.0` **Rockchip pipeline rebuilt** and verified end to end: llama.cpp v0.6.0 (GGUF incl. IQ types and
  importance matrix), RKLLM-Toolkit 1.3.1 and RKNN-Toolkit2 2.3.2, each in its own venv; models are staged on
  the host (Hugging Face repo or local path), format dispatch `gguf`/`rkllm`/`rknn`, board-aware defaults.
- `2026-10-10 16:48 UTC` · `v3.0.0` Image builds use BuildKit when available and report the real cause of failures; per-build images are
  removed after the build.
- `2026-10-10 16:48 UTC` · `v3.0.0` The calibration dataset is separate from the importance matrix (imatrix for GGUF only, dataset passed to
  RKLLM/RKNN directly).
- `2026-10-10 16:48 UTC` · `v3.0.0` The stored Hugging Face token reaches builds (one shared build engine).
- `2026-10-10 16:48 UTC` · `v3.0.0` Relative data paths resolve against the installation directory, not the working directory.
- `2026-10-10 16:48 UTC` · `v3.0.0` Self-healing: proposals wait for an explicit decision (GUI dialog, CLI prompt or MCP tool); only fixed,
  framework-defined fixes are applied (fewer compile jobs after an out-of-memory image build, cleanup of
  framework images); AI suggestions are advice only.
- `2026-10-10 16:48 UTC` · `v3.0.0` Ditto: errors and refusals raise instead of being shown as answers; API keys are passed per call;
  knowledge-base text, logs and probe files go into the user turn as sanitized untrusted blocks.
- `2026-10-10 16:48 UTC` · `v3.0.0` Dependencies updated with hashed pins (Poetry 2 lock, `requirements*.txt`); `pyside6-essentials` instead
  of the full PySide6; `pydantic` and `mcp` added.
- `2026-10-10 16:48 UTC` · `v3.0.0` Installers and the smoke test require Python 3.11-3.14.
- `2026-10-10 16:48 UTC` · `v3.0.0` Single version source (`orchestrator.__version__`).
- `2026-10-10 20:56 UTC` · `v3.0.0` **Configuration is never read from the working directory** any more: `LLM_FRAMEWORK_CONFIG_DIR`, then the
  installation's `configs/`, then `~/.llm-framework` (`framework.yml` in the current directory is ignored).
- `2026-10-10 20:56 UTC` · `v3.0.0` The secrets store lives in the per-user folder (`~/.llm-framework/secrets.store`, copied over once from the
  old location); the audit log key has its own keyring entry.
- `2026-10-10 20:56 UTC` · `v3.0.0` Capabilities: `default_board` (the board the build scripts use when none is given), per-format quantization
  aliases (e.g. `INT8` -> `Q8_0`), case-insensitive board names; CLI, build service and GUI submit exactly the
  values they validated. IMatrix without an own dataset is valid again (shipped calibration text).
- `2026-10-10 20:56 UTC` · `v3.0.0` `build start -o` resolves against the working directory, the configured default against the installation;
  `deploy list-artifacts` and `deploy package` read the build manifests in the configured output folder.

### Removed
- `2026-10-10 16:48 UTC` · `v3.0.0` LangChain, python-dotenv, pywin32 and winshell dependencies.
- `2026-10-10 16:48 UTC` · `v3.0.0` The stored SSH password (`target_password`) and its menu item: devices use the framework key.
- `2026-10-10 16:48 UTC` · `v3.0.0` Ditto's generated calibration filler (it downloaded a URL that returns 404 and fell back to repeated
  sentences).

### Fixed
- `2026-10-10 16:48 UTC` · `v3.0.0` Many crashes and dead paths: build status enum, GUI builds stopping when a slot returned, healing dialog,
  chat window (sent each question twice, crashed on every message with PySide6 6.12), module wizard import,
  CLI hard exits and log levels, deployment of folders and archives.
- `2026-10-10 16:48 UTC` · `v3.0.0` RKNN: dataset mounts no longer replace the model mount; JSON datasets keep their suffix; stale artifacts of
  other boards are never deployed.
- `2026-10-10 16:48 UTC` · `v3.0.0` Entrypoint restart loop when the host user is root; checksums with Windows line endings.
- `2026-10-10 20:56 UTC` · `v3.0.0` Cancelling a build can no longer be undone by a later stage (security scan, image build and stage
  containers are stopped); only real read timeouts count as timeouts; a retry after a healing fix keeps its own
  build id and log; a damaged audit database or per-user folder no longer stops the framework from starting.
- `2026-10-10 00:00 UTC` · `v3.0.0` Audit log: removed newest entries stay detectable after later calls; no false tamper alarms after a failed
  commit, a failed anchor write or a write with another key; auditing goes on after the database was moved
  aside; `llm-cli tools audit --archive` starts a new chain and keeps the old one checkable.
- `2026-10-10 00:00 UTC` · `v3.0.0` Secrets: the store of an earlier version is taken over exactly once (also into an existing per-user store),
  and a damaged one is reported; a running MCP server sees secrets that another process added or deleted;
  nothing is written into the working directory any more (secrets audit log, model folder).
- `2026-10-10 00:00 UTC` · `v3.0.0` Tool permissions: a deeply nested or dangling permission file denies every tool like other damage.
- `2026-10-10 00:00 UTC` · `v3.0.0` MCP: an explicit "no" stays final even if the permissions change meanwhile; every refusal is audited; errors
  while writing the Claude Desktop config are reported without a traceback; `XDG_CONFIG_HOME` is honoured on
  Linux; `-v` no longer puts log lines on the protocol channel.
- `2026-10-10 00:00 UTC` · `v3.0.0` Builds: a cancelled build leaves no manifest or archive behind; a finished build frees its slot before its end
  state is visible; links in the output folder are never followed; the GUI sends no board for a target without
  capabilities unless one was typed.

### Security
- `2026-10-10 16:48 UTC` · `v3.0.0` **Sandboxed conversion containers**: host user instead of root, no network, no capabilities,
  no-new-privileges, read-only image filesystem, process/memory limits.
- `2026-10-10 16:48 UTC` · `v3.0.0` Crawler: SSRF protection with DNS-rebinding-safe connection pinning, robots.txt per RFC 9309, redirect and
  request limits.
- `2026-10-10 16:48 UTC` · `v3.0.0` Module generation: strict validation of names, images, packages, flags and shell code (deny-list,
  `bash -n`), confinement to `targets/`, no overwriting of existing targets.
- `2026-10-10 16:48 UTC` · `v3.0.0` Chat output is escaped; links open only after confirmation.
- `2026-10-10 16:48 UTC` · `v3.0.0` Archive extraction blocks path traversal, links and devices; secrets are redacted in logs.
- `2026-10-10 16:48 UTC` · `v3.0.0` pip-audit reports no known vulnerabilities (279 before); GitHub Actions pinned by commit SHA.
- `2026-10-10 20:56 UTC` · `v3.0.0` A project folder can no longer change the settings of an MCP server started inside it (e.g. source
  repositories of the build toolchain).
- `2026-10-10 20:56 UTC` · `v3.0.0` A damaged secrets store is never overwritten; secrets are written atomically with mode 0600.
- `2026-10-10 20:56 UTC` · `v3.0.0` Audit log: one write transaction per entry (no forked chains with several processes), key id per log,
  an anchor file that reveals removed newest entries.
- `2026-10-10 20:56 UTC` · `v3.0.0` A damaged tool permission file denies every tool instead of falling back to looser defaults.
- `2026-10-10 20:56 UTC` · `v3.0.0` MCP: confirmations show every argument in full (too long: refused), only a real answer of the user counts as
  approval, declined and cancelled calls are audited, client snippets are quoted, and the Claude Desktop config
  backup is private.
- `2026-10-10 00:00 UTC` · `v3.0.0` The MCP launch command written into client configs never imports code from the folder the client starts it
  in (`python -P`); Windows snippets are quoted for cmd and PowerShell; invisible and combining characters in
  confirmation texts are shown escaped; tool error messages are redacted before they reach a client.
- `2026-10-10 22:50 UTC` · `v3.0.0` **Secret scan** (`scripts/secret_scan.py`, standard library only): provider API keys, tokens, private keys,
  certificates and credential files are refused by a pre-commit and a pre-push hook (`make hooks`) and by a CI
  job over the whole history. Personal strings (e-mail address, user name) go into the never-committed
  `.git/info/secret-patterns`; findings are printed redacted.

## [2.4.0] - 2026-01-14
**Reconstructed entry.** Versions 2.0 to 2.4 were developed without changelog entries; this summary is
taken from source headers and the git history (2025-12-13 to 2026-01-14).

### Added
- `2026-01-14 00:00 UTC` · `v2.4.0` Self-healing manager (regex heuristics plus AI analysis of build logs).
- `2026-01-14 00:00 UTC` · `v2.4.0` Offline mode for Ditto with small local models, telemetry (opt-in), updater, community hub.
- `2026-01-14 00:00 UTC` · `v2.4.0` IMatrix "Smart Calibration" for GGUF quantization.
- `2026-01-14 00:00 UTC` · `v2.4.0` Single source of truth for project sources (`configs/project_sources.yml`, `SSOT_ARCHITECTURE.md`,
  `validate_ssot.py`, `sync_ssot.py`).
- `2026-01-14 00:00 UTC` · `v2.4.0` German voice models (GLaDOS, Thorsten) in the source catalog; transparency manager (experimental).

### Changed
- `2026-01-14 00:00 UTC` · `v2.4.0` Container images configurable centrally; security hardening of the GUI windows.

## [1.7.0] - 2025-12-10
**Hardware & Deployment Update.** Bridges the gap between build and run by introducing a zero-dependency deployment manager and full Intel hardware support.

### Added
- `2025-12-10 00:00 UTC` · `v1.7.0` **Deployment Manager:** Integrated SSH/SCP client to deploy artifacts directly to edge devices. Features a "Zero-Dependency" mode (using system binaries or Paramiko) and a "Network Guard" ping-loop for connection stability.
- `2025-12-10 00:00 UTC` · `v1.7.0` **Intel Ecosystem:** Full support for Intel IPEX-LLM and OpenVINO. `hardware_probe` now detects Intel Arc GPUs, iGPUs, and NPU accelerators (Meteor Lake).
- `2025-12-10 00:00 UTC` · `v1.7.0` **Live Monitoring:** Real-time CPU and RAM usage visualization of the build container directly in the GUI (replacing external tools like `ctop`).
- `2025-12-10 00:00 UTC` · `v1.7.0` **Visual Identity:** "Ditto" comes to life! Added dynamic avatar states in the Wizard (Thinking, Reading, Success, Error) to improve user experience.
- `2025-12-10 00:00 UTC` · `v1.7.0` **Auto-Documentation:** The builder now automatically generates a `Model_Card.md` containing SHA256 hashes and usage instructions, bundled into the Golden Artifact ZIP.

### Changed
- `2025-12-10 00:00 UTC` · `v1.7.0` **Hardware Probe:** Massively expanded `hardware_probe.sh` and `.ps1` to detect AVX512-VNNI and AMX flags essential for Intel optimization.
- `2025-12-10 00:00 UTC` · `v1.7.0` **GUI:** Added "Deploy" button to Main Window (active after successful build) and a secure `DeploymentDialog` that keeps credentials in RAM only.
- `2025-12-10 00:00 UTC` · `v1.7.0` **Builder:** Updated artifact handling to deterministically identify model files and create a standardized ZIP package ("Golden Artifact").

## [1.6.0] - 2025-12-06
**Deep Ingest Release.** Empowers the AI Agent "Ditto" to recursively crawl, filter, and learn from external documentation websites and PDFs.

### Added
- `2025-12-06 00:00 UTC` · `v1.6.0` **Deep Crawler Engine:** Integrated `LangChain` based Recursive URL Loader to parse entire documentation sites (not just single pages).
- `2025-12-06 00:00 UTC` · `v1.6.0` **PDF Intelligence:** Native PDF parsing support using `pypdf`, enabling Ditto to read data sheets and technical manuals.
- `2025-12-06 00:00 UTC` · `v1.6.0` **Smart Filtering:** Implemented language detection (ignoring non-English content) and navigation/footer removal for cleaner knowledge.
- `2025-12-06 00:00 UTC` · `v1.6.0` **Knowledge Snapshots:** The Module Generator now automatically extracts learned knowledge from Qdrant and bundles it as `knowledge.json` within the target module for sharing.
- `2025-12-06 00:00 UTC` · `v1.6.0` **GUI Dialogs:** New `URLInputDialog` for batch-adding documentation links with configuration for crawl depth and page limits.
- `2025-12-06 00:00 UTC` · `v1.6.0` **Legal Compliance:** Mandatory "Robots.txt" respect and user disclaimer checkbox before crawling.

### Changed
- `2025-12-06 00:00 UTC` · `v1.6.0` **Wizard Workflow:** Integrated "Deep Ingest" step into the module creation wizard with live progress logging.
- `2025-12-06 00:00 UTC` · `v1.6.0` **RAG Manager:** Expanded `ingest_url` to handle recursive crawling results and PDF streams.
- `2025-12-06 00:00 UTC` · `v1.6.0` **Config Manager:** Added settings for `crawler_max_depth`, `crawler_max_pages`, and input history.

## [1.5.0] - 2025-12-01
**Expert Knowledge Release.** Transforms the AI Agent "Ditto" from a passive reader into an active expert system using local RAG technology.

### Added
- `2025-12-01 00:00 UTC` · `v1.5.0` **Local RAG (Experimental):** Integration of Qdrant Vector Database to enable semantic search over hardware documentation instead of naive scraping.
- `2025-12-01 00:00 UTC` · `v1.5.0` **Dynamic Sidecar Architecture:** Qdrant runs as an on-demand container. It is only downloaded and started if the user explicitly enables "Knowledge Base" to save resources.
- `2025-12-01 00:00 UTC` · `v1.5.0` **Community Brain:** New workflow to share indexed knowledge snapshots via Git. Users can import community-curated knowledge packs (`community/knowledge/*.json`) for offline intelligence.
- `2025-12-01 00:00 UTC` · `v1.5.0` **RAGManager:** New core component (`orchestrator/Core/rag_manager.py`) handling document ingestion, chunking, and vector retrieval.

### Changed
- `2025-12-01 00:00 UTC` · `v1.5.0` **Ditto Agent:** Updated `_fetch_documentation` logic to prioritize vector search results over raw URL downloads for higher precision in module generation.
- `2025-12-01 00:00 UTC` · `v1.5.0` **DockerManager:** Added logic to dynamically spawn/stop the `llm-qdrant` container based on user configuration.
- `2025-12-01 00:00 UTC` · `v1.5.0` **GUI:** Added "Enable Local Knowledge Base" toggle in AI Settings dialog.

### Security
- `2025-12-01 00:00 UTC` · `v1.5.0` **Data Privacy:** Vector data remains strictly local (127.0.0.1). No external cloud vector store is used.
- `2025-12-01 00:00 UTC` · `v1.5.0` **Sanitization:** Export tools for community knowledge automatically strip potential sensitive paths or API keys before creating snapshots.

## [1.4.0] - 2025-11-30
**Smart Calibration Release.** Introduces a context-aware quantization pipeline with Human-in-the-Loop verification to ensure enterprise-grade model quality on NPU targets.

### Added
- `2025-11-30 00:00 UTC` · `v1.4.0` **Smart Calibration:** Automated workflow to detect if a model requires calibration data (e.g., INT8) and generate/select it.
- `2025-11-30 00:00 UTC` · `v1.4.0` **DatasetManager:** New core component (`orchestrator/Core/dataset_manager.py`) for deterministic domain detection and dataset validation.
- `2025-11-30 00:00 UTC` · `v1.4.0` **Human-in-the-Loop:** Added `DatasetReviewDialog` to allow users to verify and edit AI-generated calibration data before build execution.
- `2025-11-30 00:00 UTC` · `v1.4.0` **Ditto AI Upgrade:** Expanded Ditto's capabilities to generate synthetic domain-specific training data (Code, Chat, Medical) based on SSOT knowledge.

### Changed
- `2025-11-30 00:00 UTC` · `v1.4.0` **Build Pipeline:** Updated `builder.py` and `docker_manager.py` to inject calibration datasets (`dataset.json`) into the build container.
- `2025-11-30 00:00 UTC` · `v1.4.0` **Rockchip Modules:** Updated `rkllm_module.sh` and `rknn_module.sh` (and their Python wrappers) to accept and utilize external calibration datasets for higher quantization accuracy.
- `2025-11-30 00:00 UTC` · `v1.4.0` **GUI:** `MainWindow` now intelligently prompts for datasets when selecting sensitive quantization modes (INT8/W8A8).
- `2025-11-30 00:00 UTC` · `v1.4.0` **Policy:** Removed heuristic guessing for model domains; introduced strict metadata checks or user prompts.

## [1.3.0] - 2025-11-29
**Production Ready Release.** Focuses on full internationalization, security hardening, and closing the loop between GUI and Build Engine.

### Added
- `2025-11-29 00:00 UTC` · `v1.3.0` **I18n Support:** Full English/German localization for GUI and Wizard.
- `2025-11-29 00:00 UTC` · `v1.3.0` **LocalizationManager:** Centralized service for dynamic language switching (`orchestrator/utils/localization.py`).
- `2025-11-29 00:00 UTC` · `v1.3.0` **GUI Enhancements:**
    - New Dropdowns for **Task** (LLM, Voice, VLM) and **Quantization** (INT8, INT4, FP16).
    - Added **"Use GPU"** checkbox for NVIDIA Passthrough.
    - Integrated Language Selection Dialog on first start.
    - **SecretsManager:** AES-256 encryption for sensitive API keys.

### Changed
- `2025-11-29 00:00 UTC` · `v1.3.0` **Refactoring:** Centralized text resources in `LocalizationManager`.
- `2025-11-29 00:00 UTC` · `v1.3.0` **Dependencies:** Added `litellm`, `rich`, `psutil` and `cryptography` to core requirements.
- `2025-11-29 00:00 UTC` · `v1.3.0` **Docker:** Removed static builder services in favor of dynamic spawning.

## [1.2.0] - 2025-11-29
**AI Integration Release.** Introduces the Ditto Agent for automated hardware discovery and target generation.

### Added
- `2025-11-29 00:00 UTC` · `v1.2.0` **Ditto AI Agent:** Automated analysis of hardware probes and documentation fetching from SSOT.
- `2025-11-29 00:00 UTC` · `v1.2.0` **Smart Dispatcher:** Implemented dynamic `build.sh` template handling Task and Hardware routing.
- `2025-11-29 00:00 UTC` · `v1.2.0` **Templates:** Added `rkllm_module` and `rknn_module` templates for Rockchip NPU support.
- `2025-11-29 00:00 UTC` · `v1.2.0` **Security:** Added `trivy-infra-scanner` for automated container vulnerability auditing.
- `2025-11-29 00:00 UTC` · `v1.2.0` **NetworkGuard:** Robust connectivity checks and secure downloads.

### Changed
- `2025-11-29 00:00 UTC` · `v1.2.0` **Hardware Probe:** Hardened Windows detection using Native API (C# Injection) for reliable CPU flags.
- `2025-11-29 00:00 UTC` · `v1.2.0` **Pipeline:** Decoupled Python generation logic from Bash scripts.

## [1.1.0] - 2025-11-28
**Architecture Refactoring.** Decouples the core builder from specific target logic to enable universal cross-compilation.

### Changed
- `2025-11-28 00:00 UTC` · `v1.1.0` **Architecture:** Removed hardcoded `TargetArch` enums; Builder is now target-agnostic.
- `2025-11-28 00:00 UTC` · `v1.1.0` **Clean Code:** Extracted inline Python code from Bash scripts into standalone files.
- `2025-11-28 00:00 UTC` · `v1.1.0` **Builder:** Switched to dynamic volume mounting for module execution.

### Fixed
- `2025-11-28 00:00 UTC` · `v1.1.0` **Stability:** Fixed list/set type errors in Windows installer.
- `2025-11-28 00:00 UTC` · `v1.1.0` **Cross-Compilation:** Added missing cross-compilers (`gcc-aarch64`) to Docker templates.

## [1.0.0] - 2025-11-20
**Initial Release.** Establishes the core framework structure and basic Rockchip support.

### Added
- `2025-11-20 00:00 UTC` · `v1.0.0` Core Framework structure (Orchestrator, Builder, Docker Manager).
- `2025-11-20 00:00 UTC` · `v1.0.0` Basic support for Rockchip RK3588/RK3566 cross-compilation.
- `2025-11-20 00:00 UTC` · `v1.0.0` CLI (`llm-cli`) and GUI (`llm-gui`) entry points.

[3.2.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v2.4.0...v3.0.0
[2.4.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.7.0...v2.4.0
[1.7.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/releases/tag/v1.0.0
