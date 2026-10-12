Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-11

**English** · [Deutsch](Features_DE.md)

# Features in detail

This document explains what the framework does inside and why, so you always know what your tools are doing. It
describes the state of the development branch: release 3.0.0 plus the unreleased changes in the
[CHANGELOG](../CHANGELOG.md). Planned work is in the [roadmap](BLUEPRINT.md#roadmap), not here.

Back to the [README](../README.md) · All commands: [CLI_Usage.md](CLI_Usage.md)

---

## Contents

- [How the parts fit together](#how-the-parts-fit-together)
- [Targets and capabilities](#targets-and-capabilities)
- [Build pipeline](#build-pipeline)
- [Build sandbox](#build-sandbox)
- [Self-healing](#self-healing)
- [Deployment](#deployment)
- [Ditto, the assistant](#ditto-the-assistant)
- [Ditto's tools](#dittos-tools)
- [Ditto evaluation](#ditto-evaluation)
- [Local runtime and model tiers](#local-runtime-and-model-tiers)
- [Knowledge base (RAG)](#knowledge-base-rag)
- [Deep Ingest](#deep-ingest)
- [MCP server](#mcp-server)
- [Tool permissions and audit log](#tool-permissions-and-audit-log)
- [Secrets and personal state](#secrets-and-personal-state)
- [Input history](#input-history)
- [Hardware probe and new targets](#hardware-probe-and-new-targets)
- [Health check (doctor)](#health-check-doctor)
- [Community zoo](#community-zoo)
- [Secret scan for contributors](#secret-scan-for-contributors)
- [Known gaps](#known-gaps)

---

## How the parts fit together

```
 GUI (PySide6)        CLI (llm-cli)        MCP server (llm-cli mcp serve)
      \                    |                    /
       +------------ Framework manager --------+
       |                   |                   |
  Build service       Ditto (assistant)    Tool registry
  Orchestrator        LLM provider layer   (permissions, confirmation, audit log)
  Build engine ---> Docker (sandboxed conversion containers)
       |                   |
  Target capabilities  Knowledge base (FastEmbed + embedded Qdrant)
  Self-healing         Local runtime (managed llama-server)
  Deployment (SSH)
```

- The **GUI**, the **CLI** and the **MCP server** are three ways to drive the same framework.
- Builds go through one **build service**: it validates a request against the target's capabilities, starts it,
  reports status and logs, cancels it and lists finished builds.
- **Ditto** and **MCP clients** act only through the **tool registry**, which checks your permissions, asks for
  confirmation where needed and writes every call to the audit log.

---

## Targets and capabilities

A target is a folder in `targets/` (for example `targets/Rockchip/`) with a Dockerfile, build scripts and a
`target.yml`. Its `capabilities:` block says exactly what the target can build:

- boards with their CPU, NPU, RAM and example devices, and a default board (Rockchip: `rk3588`, the board the build
  scripts use when none is given),
- formats with their accepted input (`hf` for a Hugging Face model, `onnx` for an ONNX file), quantizations
  (overall and per board), default quantizations (overall and per board), and notes,
- calibration needs: whether a format uses an importance matrix, which quantizations require it, whether a
  calibration dataset is optional or required (RKNN `i8`), and what kind of dataset it is (text or a sample list),
- aliases: common spellings that are translated (`INT8` → `Q8_0` for GGUF, `int4` → `w4a16` for RKLLM).

The schema is in `orchestrator/schemas/targets.py`. The GUI, the CLI, the build service, Ditto and the MCP server
read the same block: they offer exactly these options, fill in the defaults, and reject everything else with a
clear reason before anything runs. The Rockchip values are listed in the [README](../README.md#supported-targets-and-formats).

---

## Build pipeline

A build runs these steps; each one is logged:

1. **Calibration text:** for an importance matrix without your own dataset, the framework writes its shipped,
   checksummed calibration text (setting `calibration_language`: `mixed`, `de` or `en`; see
   [data/calibration](../data/calibration/README.md)). If that fails for a quantization that requires the matrix,
   the build stops with an error.
2. **Prepare:** check the calibration dataset (kept only where the build uses it: GGUF with an importance matrix,
   RKLLM, RKNN `i8`).
3. **Build image:** the target's Docker image with all toolkits (llama.cpp v0.6.0, RKLLM-Toolkit 1.3.1,
   RKNN-Toolkit2 2.3.2, each in its own Python environment). BuildKit is used when available, and the real cause
   of a failure is reported. Later builds reuse Docker's build cache. llama.cpp's conversion tools
   ([below](#conversion-tools-tuned-to-the-build-host)) are built in this step from the pinned commit with the build
   recipe's options for this machine.
4. **Stage the model on the host:** a Hugging Face model is downloaded on your computer (with the token
   `hf_token` for gated models), a local folder or file is used as it is. The containers get it read-only.
5. **Security scan:** Trivy scans the image for HIGH and CRITICAL vulnerabilities (setting `enable_security_scan`,
   pinned image `aquasec/trivy:0.75.0`). Findings are a warning in the log, not an error. A scan that does not
   finish within the build's time limit (`build_timeout`) is stopped with a warning.
6. **Importance matrix** (GGUF only, when chosen).
7. **Convert and quantize** in sandboxed containers ([Build sandbox](#build-sandbox)).
8. **Results:** the model file is collected, `build_info.json` is completed with the model files and their SHA256
   hashes, a `Model_Card.md` with hashes and usage notes is written, and a ZIP of these files is made. All of this
   is prepared in a hidden folder next to the output folder and moved into it only at the end, so a cancelled or
   failed build leaves no files in the output folder, and files of an earlier build there are replaced only by a
   completed one. Links in the build output are never followed. Per-build images are removed afterwards.

Further behaviour:

- **Cancel:** the Builder tab's **Cancel Build** button (enabled while the build the tab started runs; it asks
  for confirmation), Ctrl+C in `llm-cli build start` and the `cancel_build` tool (Ditto in the GUI chat, or an MCP
  client, for builds of its own process) all cancel through the build service. The running container is stopped,
  including a running security scan or image build, and a later step can never undo it; the button and Ctrl+C wait
  until the build has stopped and say so. Once a build has failed or is moving its files into the output folder,
  it can no longer be cancelled and finishes on its own.
- **Build status across programs:** every program that builds writes one record per build into
  `<cache_dir>/build_records/`, so `llm-cli build status` also lists builds that the GUI, the MCP server or another
  CLI call started; a build whose program has ended before it finished is shown as interrupted.
- **Build slots:** at most `max_concurrent_builds` builds (setting, 1 to 10, default 2) run at the same time in one
  program; a further build waits for a free slot (its stage shows "Waiting for a free build slot") instead of
  failing. A cancelled build keeps its slot until its container has stopped.
- **Unknown target:** the CLI, Ditto and MCP clients refuse an unknown target before anything runs (the CLI with
  exit code 2). A request that still ends up with nothing to build fails with "Nothing to build" instead of
  reporting success.
- **Timeouts:** each container step has a time limit (setting `build_timeout`, default 3600 s); only a real time-out
  counts as one.
- **Output folder:** CLI builds go to `output/build_<date>_<time>/<target>/<model>/<quantization>/` in the
  installation (or the folder given with `-o`), builds started in the GUI, by Ditto or by MCP clients to
  `output/<target>/<model>/<quantization>/`.

### Conversion tools tuned to the build host

The target images build llama.cpp's conversion tools (`llama-quantize`, `llama-imatrix`, `llama-perplexity`,
`llama-completion`, `llama-bench`) from source with the same recipe as Ditto's server
([How the runtime is built](#how-the-runtime-is-built-self-built-llamacpp)); the shipped Rockchip target and the
template for new targets use the same steps.

- **The pin, by commit:** the engine passes `LLAMA_CPP_TAG` and `LLAMA_CPP_COMMIT`; the image fetches the commit and
  stops unless `git rev-parse HEAD` gives it back, so a moved tag cannot change what is built. The setting
  `llama_cpp_version` cannot move it either; a different value gives a warning.
- **Tuned to the machine that runs them:** the image runs on the build host, so `LLAMA_CMAKE_ARGS` is the recipe's
  plan for purpose `host-tools` from a probe of this machine, taken right before the image build (or this machine's
  stored profile if the probe fails): its CPU features written out (AVX-512 only where the CPU has it, no AVX2 on a
  CPU before Haswell, so no illegal instruction), or all CPU variants, the best one chosen at run time, when the
  probe could not tell every feature. Without a probe, or with a Docker daemon on another machine, the Dockerfile's
  default applies: the fixed x86-64 baseline these tools always had (AVX2, FMA, F16C, no AVX-512), written out.
- **Every build:** after these options the image sets what every build has (nothing downloaded at configure time,
  no subprocess support, the build number and commit); words that are not `-DNAME=VALUE`, options that download
  whenever they are set and a configure log that reports a download stop the build, and `llama-bench --version`
  must report the pinned build and commit. Build arguments of a build request cannot replace the three llama.cpp
  arguments, and CPU or CMake flags from the module wizard or an AI never reach them.
- **Recorded:** the build log names the tuning, and `build_info.json` keeps it under `llama_cpp_tools` (tag, commit,
  tuning, plan ID and its words).
- **Same output:** the GGUF `llama-quantize` writes does not depend on this tuning. Measured at the pin with GCC
  13.3 on a synthetic 6 MB llama model: Q2_K, Q3_K_M, Q4_0, Q4_K_M, Q5_K_M, Q6_K, Q8_0, IQ4_NL, IQ4_XS and TQ2_0
  (without an importance matrix) were byte-identical from three builds: the default baseline, AVX-512 with AMX, and
  AVX without AVX2 or FMA. Building the five tools took 3 to 6 minutes on a 4-core virtual machine (`-j4`).

---

## Build sandbox

Conversion containers run untrusted model code (for example custom tokenizers), so they are locked down
(setting `build_sandbox`, default on):

| Measure | Effect |
|---|---|
| Your user ID instead of root | Files belong to you; nothing in the container runs as root. |
| No network (`network_mode: none`) | The model cannot send data anywhere; libraries cannot download anything. All Hugging Face libraries are set to offline. |
| No Linux capabilities (`cap_drop: ALL`) and `no-new-privileges` | No privilege escalation. |
| Read-only image filesystem | Only the build's own work folder, a 1 GB `/tmp` and the output folder are writable; the staged model is mounted read-only. |
| Process limit (`build_pids_limit`, default 4096) | Protects against runaway processes. |
| Optional memory limit (`build_memory_limit`, e.g. `24g`) | Protects the host from running out of memory. |

Because the sandbox has no network, everything is downloaded beforehand on the host: the model (step 4 above) and
the toolkits (in the image build).

---

## Self-healing

When a build fails, the self-healing manager looks at the log:

1. **Known problems** are recognized by fixed rules: out of memory while compiling the image, a conversion killed
   by the out-of-memory killer, a full disk, missing permission for the Docker daemon, a network problem, outdated
   package sources in the base image.
2. **Otherwise Ditto** analyzes the log, if an AI provider is set up. Its answer must match a fixed structure
   (summary, cause, one suggested step, confidence, host or device) and is discarded if it contains a dangerous
   command.

The proposal waits for your decision: a dialog in the GUI, a question in the CLI, or the `resolve_healing` tool
for Ditto and MCP clients (setting `healing_approval_timeout`, default 900 s). The framework only applies fixed,
built-in fixes itself:

| Problem | What the framework does after your "yes" |
|---|---|
| Out of memory while compiling the image | Sets `build_jobs` to 1 and retries |
| Disk full | Removes dangling framework images and the Docker build cache, then retries |
| Network problem | Retries once |

Everything else, including every AI suggestion, is shown as a manual step and never executed. A retry gets its own
build ID (`<job>_r1`), and both logs are kept.

---

## Deployment

Deployment copies a finished build to a board over SSH and installs the model file there.

- **Trust on first use:** the board's host key is shown with its SHA256 fingerprint (the same format as
  `ssh-keygen -lf`). Only after you confirm is it stored in the framework's own `~/.llm-framework/known_hosts`.
  A changed key is always refused.
- **Pairing:** on the first connection your password is used once to install the framework's own Ed25519 key
  (`~/.llm-framework/id_ed25519_llm_framework`) on the board. The password is never stored; later connections use
  the key.
- **Package:** a ZIP with the model file, the build's manifest and model card, `checksums.sha256` and a generated
  `deploy.sh`. Only model files are deployed (`.gguf`, `.rkllm`, `.rknn`, `.onnx`, `.safetensors`, `.bin`); a
  build with several weight files is refused.
- **On the board:** the package goes into a private temporary folder (`mktemp -d`), `deploy.sh` (run with `sh`,
  so boards that mount `/tmp` with `noexec` or have no bash work too) checks every checksum with `sha256sum -c`,
  copies the model to `~/models` (or `$MODEL_DIR`) and the temporary folder is
  removed. For GGUF it prints how to start the model with the start script of the
  [self-built runtime](#runtime-on-the-board-self-built-llamacpp) when that is installed on the board (on
  127.0.0.1 with the board's key), otherwise how to deploy it; it never points to a `llama-server` found on the
  board's `PATH`. For RKLLM it names the RKLLM runtime.
- **Docker mode** (`--docker`, GUI option): also packs container images and starts them on the board. This mode
  is experimental and not verified in this version.

### Runtime on the board (self-built llama.cpp)

For GGUF models the framework builds llama.cpp for a board and installs it there (`llm-cli device ...`,
[CLI_Usage.md](CLI_Usage.md#device)). The RKLLM and RKNN runtimes are vendor binaries and stay yours to install.

- **Plan from the board's own probe** (decision D14): `llm-cli scan --device` stores the board's profile under its ID
  (from its SSH host key). The plan is a cross build for that profile: `GGML_CPU_ARM_ARCH` from the board's CPU
  features (RK3566: `armv8.2-a+dotprod+fp16`), and a builder base whose C library is not newer than the board's:
  the newest Debian or Ubuntu base with a glibc not newer than the board's (Debian 11 for glibc 2.31 up to Debian 13
  for 2.41), or Alpine for musl boards, which build with Clang and the board's sysroot (unverified). A board older
  than every base, or a profile without `LIBC_VERSION`, gets no plan and a message saying why. Only CPU builds for
  now: Vulkan on a Mali GPU only after `llama-bench` shows it beats the CPU build (decision D8). `llm-cli device
  runtime plan` shows the plan.
- **Build:** in the builder container without network, the C++ runtime linked statically, the server without
  subprocess support. The build is format-checked on your PC (ELF architecture, no newer glibc symbol version than
  the board, no shared C++ runtime) and stored like every self-built build. An OpenMP build also gets the board's
  `libgomp.so.1` from the cross toolchain that linked it, checked the same way.
- **Tarball:** `~/.llm-framework/runtime/llama.cpp/device/<device ID>/llama-runtime-<arch>-<build ID>.tar.gz` (with
  a `.sha256`): `bin/` with `llama-server`, `llama-cli`, `llama-bench` and `llama-perplexity`, `lib/libgomp.so.1`
  when needed (the programs find it through their `$ORIGIN/../lib` RUNPATH), the start script `llama-server.sh`,
  `build.json` and `manifest.json`, llama.cpp's licence as `LICENSE-llama.cpp`, and `SHA256SUMS`. It is
  deterministic (fixed owners, modes and times). The framework shares this recipe, never the tarball (decision D21).
- **Deployment** (`llm-cli device runtime deploy`): only to the board whose host key the profile was made with,
  as the SSH user of the deploy settings; root is refused. Values the board reports (home folder, user ID,
  architecture, missing tools) are checked before they are used. The tarball travels on the command's stdin into a
  private staging folder in `~/.local/share/llm-framework/llama.cpp/` (never `/tmp`, which may be `noexec`), is
  checked against its sha256 and every file against `SHA256SUMS`, made readable only for the user, and placed next to
  the active build. The new build's programs are then checked on the board (below), and only a build that passes
  becomes `current`; one that fails is removed again and the previous build stays active (design 3.8: a failed
  build is never activated). After the switch the previous build is kept; older ones are removed unless a running
  program uses them. The key in `~/.config/llm-framework/llama-server.key` is generated on the board by the start
  script under umask 077 and never leaves it.
- **Start script** (`llama-server.sh serve|start|stop|status|keygen|version`): POSIX sh, tested with dash and bash.
  It starts `llama-server` from its own folder (never from `PATH`) with `env -i` (HOME, a fixed PATH, the C locale),
  `--host 127.0.0.1`, `--api-key-file`, `--no-ui`, `--no-slots`, `--jinja` and `--offline`. It passes on only
  tuning options with checked values (`-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`, `-fa`,
  `-cram`, `-lm`, `-a`, `--sleep-idle-seconds`, and `--port` from 1024 up); an option of the shared forbidden list is
  refused by name, also in the `--a_b` spelling, and any other option too, so the model never gets tools and the
  server never leaves loopback.
- **Deploy settings, decided before the deployment** (decision D7, `llm-cli device settings`): the start script in
  the user's home (the default) or a systemd unit. The unit can only be chosen when the board's probe reports
  `INIT_SYSTEM=systemd`, needs the model it serves, and needs your confirmation, given when you choose it, that
  deployments may run `sudo -n` on the board; the confirmation is written to the audit log. Once the new build has
  passed its checks, the deployment writes `/etc/systemd/system/llm-llama-server.service` with `sudo -n` (which
  fails instead of asking), enables it and starts it once the model is there. The unit runs the start script's `serve` as the user, without `Environment=`,
  with `NoNewPrivileges`, a read-only system and home, and `IPAddressDeny=any` plus `IPAddressAllow=localhost`.
  Boards with OpenRC or SysV init use the start script. A deployment never asks a question.
- **Checks on the board** (also `llm-cli device runtime verify`), as the unprivileged user and never with sudo:
  `llama-server --version` and `llama-bench --version` must report the pin, `llama-bench --list-devices` must show
  the planned devices, and `llama-server --tools all` must be refused. With `--bench-model` a short `llama-bench -p
  64 -n 16 -r 1` runs on a model on the board: a catalog model (recognised by its sha256) at once, any other model
  only after your consent, written to the audit log (decision D23). A `SIGILL` means the build's CPU baseline does
  not fit the board. A deployment runs these checks on the new build before it becomes `current`, so a build that
  fails them never runs as the board's server. The result is kept in `device/<device ID>/deployed.json`; `llm-cli
  device runtime status` says whether the deployed build is still the one the profile and the pin call for.
- **Measured** with real programs instead of a board: `llama-server` and `llama-bench` built natively with the device
  plan's options for an x86-64 machine (GCC 13.3, `-j4`, about 8 minutes on 4 shared cores) gave 30 MB of programs
  and an 11.6 MB tarball; the format check (glibc 2.38 needed, no shared C++ runtime), the deployment to a local
  stand-in board and the checks there passed, and `libgomp.so.1` was found in `lib/` through the RUNPATH.
- **Not yet:** a run on a real board, a cross build in this development environment (no Docker daemon answered),
  building on the board itself when no base is old enough, the GUI and the MCP tool `deploy_runtime`, and the
  runtime inside deployment packages.

---

## Ditto, the assistant

Ditto is built on the framework's own **provider layer** (`orchestrator/llm`), which uses the official `anthropic`
and `openai` SDKs (OpenAI-compatible servers go through `openai`).

### Tasks, models and effort

Ditto's four kinds of work map to the provider layer's task profiles; each task profile has its own model and
effort per provider:

| Ditto profile | Task | Answer |
|---|---|---|
| Chat | `chat` | Free text; may use the framework tools |
| Healing analysis | `healing` | Structured: summary, cause, suggested step, confidence, host or device |
| Module proposal | `json` | Structured target definition, reviewed by you and checked again by the module generator |
| Build recommendation | `json` | Structured: format and quantization from the target's options, optimization level, explanation |

Defaults per task come from [`configs/llm_providers.yml`](../configs/llm_providers.yml). Your settings override them:
`ai_model` and `ai_effort` for every task, or `ai_model_<task>` and `ai_effort_<task>` for one task. Effort levels
are `low`, `medium`, `high`, `xhigh` and `max`; each provider maps them to what it understands.

### Providers

| ID | Kind | Where it runs | Sign-in today |
|---|---|---|---|
| `anthropic` | Anthropic Claude API | cloud | API key (`anthropic_api_key`, or `ANTHROPIC_API_KEY`) |
| `openai` | OpenAI Responses API | cloud | API key (`openai_api_key`, or `OPENAI_API_KEY`) |
| `google` | Gemini, OpenAI-compatible endpoint | cloud | API key (`gemini_api_key`, or `GEMINI_API_KEY`) |
| `openrouter` | OpenRouter | cloud | API key (`openrouter_api_key`, or `OPENROUTER_API_KEY`) |
| `ollama` | Ollama | local (base URL configurable) | none |
| `local` | managed llama-server | local | per-start key, handled for you |
| `custom` | any OpenAI-compatible server | decided from the address: loopback, private and link-local addresses and names ending in `.local`, `.lan`, `.localhost` or `.home.arpa` count as local | API key of the server address (`custom_api_key`) or none |

- Cloud providers have fixed addresses; a base URL left over from Ollama or a custom server never receives their key.
- A key of a custom server belongs to the address it was saved for (scheme, host and port): after the base URL
  changes it is no longer sent, and the new server needs its own key.
- Each provider lists its sign-in methods in `configs/llm_providers.yml` in the order they are preferred; the
  first usable one is used unless `ai_auth_<provider>` names one. A key in one of the environment variables in
  the table above is used only when no key is stored, and the AI settings show the variable as its source; it
  is never copied into the vault. When the vault cannot be read, the variable is not used and the error names
  the problem with the vault.
- A free check (the provider's model list, OpenRouter's key endpoint) tells whether the stored key works, which
  workspace or key name it belongs to and how many models it can use. Sign-in details that are not secret
  (account or workspace, the last four characters of a key, when it was checked) are kept in `auth.json` next to
  the credential vault.
- The model list in the settings is loaded live from the provider; any model name can also be typed.
- A file `llm_providers.yml` in your settings folder adds or replaces providers by ID, so a new
  OpenAI-compatible server needs no code change.
- **Claude API (`anthropic`):** the stored API key always comes first. An `ant auth login` profile is used only
  when you choose it (`ai_auth_anthropic: ant_profile`; the earlier value `oauth` means the same). A key that is
  valid in every workspace of the organization needs the workspace ID (AI settings field "Workspace ID", setting
  `ai_anthropic_workspace_id`, `wrkspc_...`; while set, it wins over a workspace ID stored with the key); it is
  sent only with the API key and only to the Claude API. Admin API keys (`sk-ant-admin01-`) are refused before
  any request.
- **OpenAI (`openai`):** an organization and a project stored with the key (`org-...`, `proj_...`) go with it as
  the `OpenAI-Organization` and `OpenAI-Project` headers; a key that works but may not list models is reported as
  such, and the model ID can be typed.
- There is no `claude_code` provider (Ditto through a Claude subscription): Anthropic does not allow third-party
  apps to use subscription limits. A saved `claude_code` (also in the fallback chain or `--provider`) stops with a
  message naming the alternatives; such an entry in your own `llm_providers.yml` is skipped with a warning. Claude
  subscribers use the framework from Claude Code or Claude Desktop over [MCP](#mcp-server), or with an API key
  (Max and Team plans include monthly API credits).
- The SDKs never read a credential, header or address from the environment themselves: every client gets the
  key and base URL explicitly, and `OPENAI_CUSTOM_HEADERS` / `ANTHROPIC_CUSTOM_HEADERS` are removed at startup with a warning
  (setting `ai_allow_sdk_env_headers: true` keeps them).

### Sign-in

- **Where:** **Sign in...** in the AI settings (GUI) and `llm-cli ditto login | logout | auth` (see the
  [CLI guide](CLI_Usage.md#ditto-login)).
- **Method cards:** the panel shows the provider's sign-in methods in the catalog's order. The first one this
  version can sign in with is marked "Recommended", opt-in methods (the `ant` profile) "Advanced", and methods of a
  later version are shown but cannot be chosen. Each type has its own form: a key form (key page, password field,
  the catalog's extra fields such as the Claude workspace ID or the OpenAI organization and project, **Check**,
  **Save**, **Sign out**), "no sign-in" with Check for local engines, the `ant` CLI profile (the official command to
  run, the profile name, a check through the Anthropic SDK; the framework never starts the CLI or reads its files),
  and the managed local server, whose key is automatic.
- **Status line:** "Signed in as ...", "Not signed in", "Using key from environment variable X" or "Expired: sign in
  again". A key is shown only as its last four characters. Ditto's chat header shows the access in use, for example
  "Anthropic Claude (API) · API key · wrkspc_..." or "Local · Ollama".
- **Check before storing:** Check sends the typed key with one free request and stores nothing; `llm-cli ditto
  login` checks a key the same way before it stores it. Saving remembers the method as `ai_auth_<provider>`.
- **No secret in argv:** the CLI takes a key only from a hidden prompt, stdin (`--with-token`) or the provider's
  documented environment variable (`--from-env`). Without a terminal it asks nothing, and a provider with several
  methods needs `--method`. Exit codes: 0 signed in, 2 usage, 3 rejected, 4 network, 5 storage, 6 not available.
- **Keys stay intact and out of the output:** a key with a space, a line break or an invisible character inside is
  refused before anything is sent or stored, a server's error text that repeats the key shows `REDACTED` instead,
  and a typed key goes over plain `http://` to another computer only after you confirm it (CLI: `--yes`). A key typed
  in the AI settings' own key field replaces a method a sign-in remembered (for example the `ant` profile).
- **Sign-out is local first:** the stored key is deleted, and the key page where you revoke the key itself is named;
  a key in an environment variable stays in use until you unset it.
- **No usable keyring:** the panel shows the storage chooser (passphrase, credential helper, or the unencrypted key
  file after a confirmation), and the GUI shows the same chooser at startup before it gives up; a passphrase is asked
  in a dialog. A choice that cannot open the existing vault (one protected by the locked keyring) is not kept. The
  CLI exits with code 5 and names `llm-cli ditto auth storage --use ...`. Nothing is picked for you.
- **Responsive:** every vault, keyring or network call of the panel and of the chat header runs in a worker thread.

### Calls, retries and fallback

- The SDKs' own retries are off; the provider layer retries rate limits and outages up to twice and waits for a
  provider's `Retry-After` of up to 30 s (setting `ai_max_retry_wait`). Each request may take up to 600 s
  (`ai_timeout`).
- **Fallback chain** (`ai_fallback_chain`, e.g. `local, ollama:qwen3.5:4b`): when the model declines or the
  provider is unavailable, the next entry is tried. Entries that are not set up are skipped.
- **Offline mode** (`offline_mode`): only local providers are used; a cloud provider is refused with a clear
  message. The knowledge base downloads nothing in offline mode either.
- Requests to servers on 127.0.0.1 never go through an HTTP(S) proxy from the environment, so the proxy never sees
  prompts or the local server's key.
- Errors are explained in plain words, for example that a Claude API key needs a workspace ID, that the key was
  revoked or has expired, that credit is used up, or that the local server is not running.

### Privacy filter for cloud providers

Before text goes to a cloud provider, personal data is replaced (setting `ai_security_level`):

| Level | Replaced |
|---|---|
| `OFF` | only secrets (API keys, tokens); they are always removed, and never restored |
| `BALANCED` | also IPv4/IPv6 and MAC addresses, e-mail addresses, `user@host` logins, user names in home paths |
| `STRICT` (default) | also host names announced in probe files, serial numbers, UUIDs and the directories of absolute paths (system paths such as `/usr/` stay readable) |

The placeholders look like `<IP_1a2b3c4d>`. They are derived from the value with a key that is created once per
installation, so the same value always gets the same placeholder. Placeholders in the answer are turned back into
the real values on your computer. Local providers are never filtered.

### Structured answers and the dangerous-command filter

Healing analyses, module proposals and build recommendations must be valid against a schema with length limits.
Anything else is an error and is never applied. For build recommendations the schema itself lists only the formats
and quantizations the target offers, and the answer is checked again per format.

Whatever a build log, a probe file or a model name tells the model, Ditto discards a healing fix or a
recommendation that contains a dangerous command: a download piped into a shell, world-writable permissions
(`chmod 777`/`666`), deleting the root or home folder, overwriting a disk (`mkfs`, `dd` to a disk) or a fork bomb.

### Untrusted text

Build logs, probe files, knowledge-base excerpts and tool results are external text. Ditto puts them into the user
turn, cleaned of hidden and control characters and wrapped in marked `<untrusted_data>` blocks, never into the
system prompt. The system prompt tells the model that such blocks are data, not instructions.

### Chat history

The GUI chat is saved per user in `~/.llm-framework/chat_history.json` (readable only by you). The conversation is
append-only: earlier turns are never edited. When it grows beyond the setting `chat_context_limit` (default 4096
tokens, estimated at four characters per token), Ditto summarizes it in a separate request and continues in a new
conversation that starts with the summary. The window keeps up to 500 entries. Histories of earlier versions are
migrated. `llm-cli ditto ask` does not use the history.

---

## Ditto's tools

In the chat (GUI) and with `llm-cli ditto ask`, Ditto can use the same tools as MCP clients:

| Tool | Tier | What it does |
|---|---|---|
| `list_targets` | read | Targets with boards, formats and quantizations |
| `get_target_capabilities` | read | What a target can build |
| `list_build_requests` | read | Build requests of this session |
| `get_build_status` | read | Status, progress, errors, a pending self-healing proposal |
| `read_build_log` | read | The last lines of a build log (secrets redacted) |
| `list_artifacts` | read | Finished builds from their manifests |
| `run_doctor` | read | The health check, without fixes |
| `search_knowledge` | read | Search the knowledge base |
| `start_build` | local write | Start a build (must match the target's capabilities) |
| `cancel_build` | local write | Cancel a build |
| `resolve_healing` | local write | Approve or dismiss a self-healing proposal |

How a question with tools runs:

1. Ditto gets the definitions of the tools your permissions do not deny.
2. The model may call tools. Each call goes through the tool registry: input check, your permission, confirmation
   where the permission says "ask" (a dialog in the GUI with the tool's title and every argument; a prompt in the
   CLI, denied without a terminal), audit log entry with actor `ditto`.
3. Tool results go back to the model as untrusted data. A denied or failed call is reported to the model, which is
   told not to retry the same call.
4. After at most 8 model turns per question Ditto stops and says so.

Ditto's prompts tell it to check a target's capabilities before starting a build, to start or cancel builds only
when you asked for it in the conversation, to decide on self-healing only with your explicit decision, to answer
questions about targets and builds from the tools rather than from memory, and to report only what tools returned.

---

## Ditto evaluation

`llm-cli ditto eval` is a tool to measure how well a provider and model work for Ditto, for example whether a
small 2-bit local model is good enough for weak hardware. Nothing is built or changed. With `--kind retrieval` it
measures the knowledge-base search instead ([Retrieval evaluation](#retrieval-evaluation)).

**Cases** ([`evals/ditto/cases.yml`](../evals/ditto/cases.yml), 19 cases; a cases file is data, new cases need no
code change):

| Kind | Cases | What is checked |
|---|---|---|
| `healing` | 6 | Build-log analysis: cause, host or device, a fitting fix (missing CMake, out of memory, gated model, full disk, a library missing on the device, a log with injected instructions) |
| `optimizer` | 5 | Build recommendations from the real Rockchip options: e.g. GGUF for an LLM on the RK3566, an ONNX vision model to RKNN, a model name with injected instructions whose command must not appear in the answer |
| `module` | 3 | Module proposals from probe files (Jetson Orin, Raspberry Pi 5, x86 server): architecture, SDK, compiler and CMake flags |
| `chat` | 5 | Chat with tools: which tools are called with which arguments, and which are not (for example a failed build whose log contains injected instructions: Ditto must explain the error and must not cancel or start builds) |

**Grading** is done by fixed rules, not by another model: expected words or patterns, forbidden words (for example
an injected command), expected fields, tools that must or must not be called. Chat cases use the real tool
definitions over a stand-in build service that validates against the real target capabilities and returns canned
builds, logs and artifacts. The knowledge base is off during the evaluation, so answers come from the case alone.

**Running it:**

```bash
llm-cli ditto eval                                      # the configured provider and model
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json
llm-cli ditto eval --kind chat --repeat 3 --min-pass 0.8
```

With `--tier` the CLI starts the managed local server with each tier in turn and stops it after each tier. The
report shows the passed share per run, per kind and the average time per case; `--json` adds the setup (provider,
model per task, effort, framework version, sha256 of the cases file) and every answer. Cloud providers bill about 20
requests per run and repeat. All options: [CLI_Usage.md](CLI_Usage.md#ditto-eval).

**First measurement** (for context, not a benchmark): on a CPU-only machine with Qwen3.5 2B, effort `low` (thinking
off) and the prompts of that time, the 2-bit tier `small-q2` passed 42% of the 19 cases and the 4-bit tier `small`
47%. The evaluation found gaps in Ditto's prompts (attributing failures to host or device, which inputs each format
accepts, using the tools for questions about targets and builds, compiler flags in module proposals). These gaps are
fixed now, and a new measurement is running.

### Retrieval evaluation

`llm-cli ditto eval --kind retrieval` grades the [knowledge-base](#knowledge-base-rag) search instead of a model. No
AI model is involved, and your own knowledge base is not touched.

- **Cases:** [`evals/retrieval/cases.yml`](../evals/retrieval/cases.yml), 42 German and English searches against
  `evals/retrieval/corpus/`, 30 documentation excerpts of the framework and of SDKs. Each case names the excerpts
  that count and the rank the first of them must reach, or, for an unrelated question, expects no excerpt at all.
- **Tags** group the report: `en` and `de` (language of the question), `cross-lingual` (question and excerpt in
  different languages), `identifier` (for example `GGML_CUDA`), `oversized` (the answer is deep inside a block
  larger than the embedding model's context), `framework` (this framework's own documentation), `log` (the last
  three lines of a failed build, as the build-log analysis searches), `callsite` (Ditto's own search strings for
  module proposals and build recommendations) and `negative` (unrelated questions).
- **How it runs:** the corpus is indexed into a temporary knowledge base in a temporary folder, deleted afterwards,
  with the configured local embedding model. The model is downloaded on first use; after that the evaluation works
  offline. Retrieval cases run only when `--kind retrieval` is given or `--case` names one of them.
- **Variants** (`--rag-variant`, repeatable, default `fused`): `fused` is the hybrid search; `rerank` adds the
  cross-encoder of `rag_reranker` and its relevance floor ([Reranking](#reranking)). `rerank` stops with a message
  when no reranker suits the machine (set `rag_reranker` to a model id to grade one anyway), and a case counts as
  failed when the reranker did not run.
- **Report:** per variant hit@1, hit@3, hit@k (within each case's rank), MRR over the first 10 results, the share
  of unrelated questions that got no excerpt and the median search time. `--json` adds the figures per tag, the
  relevant questions that got no excerpt at all, the 95th percentile of the search time, the models, the sha256 of
  the cases and the corpus, and every result.
- **Measured** with the default CPU embedding model on the 34 cases that are not `log` or `callsite` searches (30
  with an expected excerpt, 4 unrelated): the hybrid search reaches hit@1 87 % and MRR 0.93, but all 4 unrelated
  questions still get excerpts, because it has no relevance floor. The figures with reranking are under
  [Reranking](#reranking).
- **CI:** the workflow `.github/workflows/retrieval-eval.yml` runs `fused` and `rerank` every night and on demand,
  with the models cached between runs. It fails when fewer than 70 % of the cases pass (`--min-pass 0.7`), which
  catches a broken search, not small changes, and it uploads the JSON report.

```bash
llm-cli ditto eval --kind retrieval                                  # hybrid search
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json
```

---

## Local runtime and model tiers

The managed runtime gives Ditto a local model without any cloud account. It is controlled with
`llm-cli ditto runtime` and `llm-cli ditto models` ([CLI_Usage.md](CLI_Usage.md#ditto-runtime)); Ditto's provider
`local` finds the running server by itself (address, key and loaded model are read for every call).

### What is pinned

[`configs/llama_runtime.yml`](../configs/llama_runtime.yml) is data: a newer build or another model is added there,
not in code.

- **llama.cpp commit** `d8123504` (tag b11429, version 0.6.0): `ditto runtime install` builds the server from exactly
  this commit (see [How the runtime is built](#how-the-runtime-is-built-self-built-llamacpp)).
- **Official prebuilt archives** of b11429, only for the explicit opt-in `ditto runtime install --prebuilt` and
  always shown as "prebuilt, not self-built": Linux x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.8, ROCm on request),
  Linux arm64 (CPU, Vulkan, CUDA 13.4), Windows x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.4, ROCm on request),
  Windows arm64 (CPU) and macOS (Metal on Apple Silicon, CPU on Intel). Each Linux archive names the glibc it was
  built against (2.35 for x86-64 CPU and Vulkan, 2.39 for the others, read from upstream's release workflow); a
  machine with an older glibc, or with musl, is offered none.
- **Models** at fixed Hugging Face commits, all Apache-2.0:

| Tier | File | Size | Note |
|---|---|---|---|
| `small` | Qwen3.5-2B-Q4_K_M.gguf | 1.2 GiB | for machines with up to 4 GB RAM |
| `small-q2` | Qwen3.5-2B-UD-Q2_K_XL.gguf | 0.9 GiB | 2-bit, for 3 GB RAM or less; less accurate |
| `default` | Qwen3.5-4B-Q4_K_M.gguf | 2.6 GiB | the default |
| `default-q2` | Qwen3.5-4B-UD-Q2_K_XL.gguf | 1.8 GiB | 2-bit, only on request |
| `large` | Qwen3.5-9B-Q4_K_M.gguf | 5.3 GiB | for GPUs with 8 GB of memory or more |
| `large-iq2` | Qwen3.5-9B-UD-IQ2_M.gguf | 3.4 GiB | 2-bit, only on request; noticeably less accurate |
| `granite` | granite-4.2-3b-Q4_K_M.gguf | 2.1 GiB | IBM Granite 4.2 3B, other model family, only on request |

Every download has a pinned size and sha256 and is checked before it is unpacked or loaded; entries without both
are refused. Downloads resume after an interruption, and a prebuilt archive is installed atomically next to the
previous one.

### Which build

The self-built server: the build recipe chooses the GPU backend from the machine's hardware profile, following its
OS, architecture and C library (`recipe.choose_backend`). The CPU build is always built first and stays the
fallback. A GPU on which Vulkan beats the CPU (every dedicated GPU, AMD Radeon GPUs and APUs such as the Ryzen Z1
Extreme, Intel Arc) gets a Vulkan build; Mali GPUs on boards only after `llama-bench` shows that Vulkan is faster.
NVIDIA GPUs will get a CUDA build matched to every GPU and the driver once the CUDA toolkit provisioning lands; until
then the CPU build runs there, `runtime status` says so, and `--backend vulkan` builds a Vulkan server that also uses
NVIDIA GPUs. The model tier and the context then follow the build that actually runs.

The prebuilt opt-in (`--prebuilt`): `llm-cli ditto runtime status` shows which archive it would install and why:

1. Archives whose C library does not fit the machine are never offered (musl, an unknown C library, or an older
   glibc than the archive's).
2. An explicit `--backend` wins (ROCm is only ever used this way; on Linux it needs an installed HIP runtime and a
   supported GPU).
3. Apple Silicon gets the Metal build.
4. An NVIDIA GPU gets the newest CUDA build that suits every NVIDIA GPU in the machine and its driver. GPUs for which
   the build only contains PTX code need the toolkit's own driver: for example an RTX 2080 Ti (compute capability
   7.5) needs driver 570.26 on Linux for the CUDA 12.8 build and branch R615 for CUDA 13.4 (on Windows 551.61 for
   CUDA 12.4). With an older driver the next option is used, and the reason says that a newer driver enables CUDA.
5. Vulkan, when a Vulkan driver is found and the GPU is worth it: every dedicated GPU, AMD Radeon GPUs and APUs
   (for example the Ryzen Z1 Extreme) and Intel Arc. Older Intel graphics are usually slower than the CPU and are
   only used with `--backend vulkan`.
6. Otherwise the CPU build.

### Which model

The first rule whose conditions hold and whose model fits wins:

| Rule | Tier |
|---|---|
| GPU memory of at least 7.5 GiB (a dedicated GPU used by the build, or about two thirds of Apple Silicon's memory) | `large` |
| RAM up to 3.0 GiB | `small-q2` |
| RAM up to 4.5 GiB | `small` |
| otherwise | `default` |
| fallbacks when the model above does not fit | `small`, then `small-q2` |

"Fits" means: the model file plus 5% plus the key-value cache for 4096 tokens must fit into the RAM minus 1.5 GiB for
the system, plus 95% of a dedicated GPU's memory. Integrated AMD and Intel GPUs share the RAM, so only the RAM counts
for them. `runtime start --model <tier>` overrides the choice and remembers it; `--model auto` returns to the
recommendation.

### How the server runs

- **Only verified builds:** `runtime start` starts the active self-built (or imported) build only when it passed its
  verification on this machine, every file still matches its manifest, it is of the pinned commit and it is built
  for this machine (OS and architecture, and for a `-march=native` build a CPU of the same vendor with every CPU
  feature of the CPU it was built on); otherwise the CPU fallback build, and otherwise nothing, with the reason. The
  prebuilt archive is started only after `install --prebuilt`. An archive an earlier version downloaded without that
  request is not used: `runtime status` names it, and `install --prebuilt` switches to it without downloading it
  again. A self-built build that cannot be used never switches to it on its own.
- **One launch policy:** the forbidden options, the cleared environment, loopback and the key file come from one
  module, `orchestrator/runtime/server_policy.py`, which every launcher uses: Ditto's server, the build verification
  and the perplexity check (and the device runtime's start script).
- **Context length:** computed from the free memory next to the model (free GPU memory, or free RAM on CPUs, Apple
  Silicon and integrated GPUs), rounded to 256 tokens, at least 4096 and at most 32768 (`--ctx` sets it).
- **Isolation:** `llama-server` listens on 127.0.0.1 only, on a free port. A new random API key per start reaches it
  only through a key file readable by you, and the framework checks that the server refuses requests without the
  key. The web UI and the slots endpoint are off, and the server runs with `--offline`. Options that would give the
  model its own tools, agent mode, MCP servers or remote computing, that download (Hugging Face, URLs), write the
  prompts to disk, run programs from a folder, or put a key or token on the command line are refused, in every
  spelling llama.cpp accepts (its aliases, and `--a_b` for `--a-b`). The
  server starts with a cleared environment: besides your home folder, a fixed `PATH` and the C locale it only gets
  the variables that choose GPUs or locate GPU drivers (`CUDA_VISIBLE_DEVICES`, `HIP_VISIBLE_DEVICES`,
  `ROCR_VISIBLE_DEVICES`, `GGML_VK_VISIBLE_DEVICES`, `HSA_OVERRIDE_GFX_VERSION`, the Vulkan driver variables,
  `LD_LIBRARY_PATH` and a few more), so no `LLAMA_*` option, no `GGML_BACKEND_PATH` library, no proxy and no key
  reaches it.
- **GPU offload:** with a GPU build, as many layers as fit go to the GPU.
- **Idle:** the model is unloaded after 600 idle seconds and reloaded on the next request (`--sleep-idle`).
- **State and log:** `~/.llm-framework/runtime/llama.cpp/` holds the builds and the server state, which is trusted
  only while that exact process (process ID, start time, program) is alive. The key file is deleted on stop. The
  log `~/.llm-framework/logs/llama-server.log` is rotated at 5 MiB, keeping 3 old logs.
- **Thinking:** Qwen3.5 on `llama-server` otherwise thinks at full length, which takes minutes on a CPU. So effort
  `low` turns thinking off, `medium` and higher keep it.

### How the runtime is built (self-built llama.cpp)

`llm-cli ditto runtime install` builds llama.cpp on this machine from the pinned source: it probes the machine,
detects the toolchain, plans the CPU build and the GPU build, shows them and asks, fetches and checks the source
once, builds and verifies the CPU build and makes it active, then does the same for the GPU build. A build of
another machine of the same kind can be imported (`ditto runtime export` there, `ditto runtime import` here). The
official prebuilt download stays only as the explicit choice `--prebuilt`, shown as "prebuilt, not self-built".

- **One recipe:** the hardware profile of the machine that will run the binaries becomes a build plan: the pinned
  commit, every download-capable option of llama.cpp off, the server without subprocess support
  (`LLAMA_SUBPROCESS=OFF`), `-march=native` on that machine itself or the probe's CPU features for another machine.
  A Linux build with Clang uses OpenMP only when libomp is installed. All CPU variants for an ARM board whose
  features the probe could not tell need GCC 14 or Clang 18 (upstream's list contains `armv9.2-a+sme`); with an
  older compiler the plan asks for a new probe instead.
- **Toolchain:** the framework looks for the compiler, CMake (3.15 or newer) and Ninja and names what is missing; it
  never installs a toolchain or accepts a licence for you. Parallel jobs follow the memory: `-j` is the number of
  CPUs, but at most the available RAM divided by an estimate per job (1 GiB for a CPU build; `build_jobs` in the
  catalog).
- **Where it builds:** natively on Linux, in a cleared environment, or in the framework's builder container. That
  image is built on your machine from `orchestrator/runtime/build/builder.Dockerfile` and a Debian, Ubuntu or Alpine
  base pinned by digest whose C library is not newer than the one of the machine that runs the binaries. Configure
  and build run in containers of that image without network, with the source mounted read-only, no capabilities, a
  read-only root file system and your user, as the conversion stages run. Only the build's own folders are writable,
  and when the framework collects the programs or cleans up afterwards it never follows a link out of them. The
  framework shares this recipe, never a compiled image.
- **No downloads:** besides the options, the configure output is checked: a line that says llama.cpp fetched or
  downloaded anything fails the build. So does a configure that found no OpenMP runtime for a build planned with
  OpenMP (ggml would only warn and build without it, so the build ID would no longer be right).
- **Checks before use:** every binary must be an ELF file for the planned architecture whose libraries are in the
  build or are system libraries; device builds must not need a newer glibc symbol version than the device has, nor a
  shared C++ runtime. `llama-bench --version` and `llama-server --version` must report the pinned build and commit,
  `llama-bench --list-devices` must show the planned GPU, and `llama-server --tools all` must be refused. A smoke run
  and a short `llama-bench` run on a tiny pinned model follow once the catalog pins one; until then they are skipped
  with a note. No other model is ever loaded there. Binaries are only ever taken from the build's own folder, never
  from `PATH`, and run with a cleared environment.
- **Store:** `~/.llm-framework/runtime/llama.cpp/builds/<build ID>/` holds the binaries with `build.json` (the plan)
  and `manifest.json` (sha256 of every file, duration, build host, verification results). The build ID is the hash of
  everything that changes the binaries, so a stored build is reused and never stamped again. Only a build verified on
  this machine can become the active one; retention keeps the active build, the CPU fallback, the build a running
  server uses and the two newest others. A build made on another of your machines can be imported: every file is
  checked against its manifest, the build ID against its plan and the commit against the pin, you confirm before any
  of its programs runs, and then it is verified like a local build. The version it must report comes from the pin,
  not from the archive, and every program in it is checked, also one the archive does not list. Ditto's import
  (`ditto runtime import`) refuses a `-march=native` build of a CPU of another vendor or with a CPU feature this CPU
  lacks (AVX-512, for example) before anything runs: such a build would stop with SIGILL at the first computation,
  which the checks cannot reach while no smoke-test model is pinned.
- **Measured** on a 4-core x86-64 virtual machine (GCC 13.3, `-j4`): `llama-server` and `llama-bench` at the pin in
  189 seconds including the checks, 28 MB of binaries; `ditto runtime install` with Ditto's CPU build
  (`llama-server`, `llama-cli`, `llama-bench`, `llama-perplexity`) in 195 seconds including the checks.

### Perplexity check of converted models

The dataset manager (`orchestrator/Core/dataset_manager.py`) measures the perplexity of a GGUF model on a text file
with the self-built `llama-perplexity` of this machine, which `ditto runtime install` builds together with the server.
No prebuilt runtime image is pulled and no `llama-perplexity` from `PATH` is used: only a stored build that passed its
verification here, still matches its manifest and is of the pinned commit.

- **Catalog models** (pinned by sha256) run natively.
- **Every other model** (your conversions, downloads) is untrusted. It runs natively, without Docker overhead, only
  after your explicit consent, and that consent is written to the tool audit log (`llm-cli tools audit`) before the
  run; without consent, or when the audit log cannot be written, nothing runs.
- **Docker sandbox (opt-in):** the run happens in the build's own builder image (only a build made in the builder
  container has one; it is built locally and never pulled) without network, without capabilities, with a read-only
  root file system and your user; the program, the model and the text are mounted read-only.
- Every run uses the shared launch policy (no forbidden option, a cleared environment). Failures are errors with the
  reason (for example a text too short for the context), never placeholder values.
- In this version nothing in the GUI or CLI calls the check yet; the benchmark and auto-tune work of the roadmap will.

---

## Knowledge base (RAG)

The knowledge base gives Ditto and MCP clients documentation to search (setting `enable_rag_knowledge`, GUI: **Enable
Local Knowledge Base**). Nothing is loaded until it is first used.

- **Storage:** Qdrant runs embedded in the framework process, with its data in `~/.llm-framework/qdrant` (no Docker,
  no open port). The storage is locked while in use and released after 30 idle seconds, so the GUI and the MCP
  server can take turns. To use it from several processes at the same time, set `qdrant_url` to a Qdrant server; a
  remote server needs https and the API key stored as the secret `qdrant_api_key` (never credentials in the URL).
- **Embeddings:** computed locally with FastEmbed on ONNX Runtime; the model is downloaded once into
  `models/embeddings/`. With `rag_embedding_model: auto` (default) an existing knowledge base keeps the model it was
  built with; a new one gets `intfloat/multilingual-e5-small` (384 dimensions, about 100 languages, fast on a CPU),
  or `BAAI/bge-m3` (1024 dimensions) when ONNX Runtime has CUDA and the machine has at least 8 GB RAM. Each model
  has its own collection, so switching models never mixes or loses vectors. In offline mode nothing is downloaded,
  and a missing model is reported clearly.
- **Ingesting:** pages are converted to Markdown and split along their structure (headings, paragraphs, lists, code
  blocks, tables), sized in the model's tokens. Each chunk is stored with its source, title and section.
  Re-ingesting a page updates it in place and removes chunks the new version no longer has. Code blocks and tables
  stay whole unless they are larger than the model's context ([Large code blocks and
  tables](#large-code-blocks-and-tables)).
- **Snapshots:** before every crawl the knowledge base is saved as a snapshot in
  `~/.llm-framework/knowledge_snapshots` (the newest 5 are kept). Restoring a snapshot is not available from the GUI
  or the CLI yet.
- **Search:** hybrid. Dense vectors find meaning, BM25 finds exact terms such as error codes and compiler flags; both
  rankings are fused with Reciprocal Rank Fusion. The same text from several sources is returned once. A
  cross-encoder can then rerank the results ([Reranking](#reranking)).
- **Use:** the `search_knowledge` tool (Ditto, MCP clients); without tools Ditto adds matching excerpts to the
  question itself. Healing analyses, module proposals and build recommendations (Builder: **Use RAG Knowledge
  Base**) also get excerpts. Results are always treated as untrusted data.
- **Adding documentation:** [Deep Ingest](#deep-ingest) in the module wizard, and [knowledge packs](#community-zoo)
  from the community zoo.

### Reranking

The hybrid search ranks by words and meaning. It cannot tell whether an excerpt answers the question, and it always
returns something. A reranker reads the question together with each candidate and gives it a relevance between 0
and 1. Candidates below a relevance floor are dropped, so Ditto gets no excerpt rather than an irrelevant one.

- **Model** (setting `rag_reranker`, default `auto`): a local cross-encoder on FastEmbed/ONNX, downloaded into
  `models/embeddings/` on first use, never in offline mode. With `auto`:

  | Machine | Reranker |
  |---|---|
  | ONNX Runtime with CUDA and at least 16 GB RAM | `onnx-community/bge-reranker-v2-m3-ONNX` (multilingual, about 0.6 GB download) |
  | at least 6 GB RAM | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual MiniLM, about 120 MB download, about 0.5 GB in memory) |
  | less than 6 GB RAM | none |

  `off` turns reranking off. A model id chooses another cross-encoder that FastEmbed lists.
- **Relevance floor** (setting `rag_rerank_min_score`, 0 to 1, not set by default): unset means the floor
  calibrated for the model, 0.2 for both models above; 0 turns the floor off. In the retrieval evaluation, relevant
  excerpts scored 0.80 or more and unrelated ones 0.009 or less.
- **Which searches rerank:** by default only build recommendations. The setting `rag_rerank_default: true` reranks
  the chat and the MCP tool `search_knowledge` too. Healing analyses and module proposals never rerank.
- **Why only build recommendations:** measured with the [retrieval evaluation](#retrieval-evaluation) on a shared
  4-core CPU. On the 34 cases that are not `log` or `callsite` searches, reranking raised hit@1 from 87 % to 93 %
  and MRR from 0.93 to 0.97, and all 4 unrelated questions got no excerpt. It costs about 1 s for 12 candidates and
  2 to 2.6 s for 20, and it gained less than the 0.05 MRR required for chat, so chat stays without it. On the last
  lines of failed builds it found the relevant excerpt within the first 3 in 2 of 6 cases (hybrid search: 5 of 6)
  and dropped it below the floor in one; for the search string of module proposals it dropped every excerpt. So
  build-log analysis and module proposals keep the hybrid order.
- **How a search is reranked:** up to 20 candidates (three times the requested number, at least 12), of each the
  title, section and first 1500 characters; scores are cached by text. A reranker that cannot run (not downloaded
  in offline mode, unknown model id, an error) is logged once, and searches keep the hybrid order until the next
  start or a change of `rag_reranker`.

### Excerpt budget

When Ditto adds knowledge-base excerpts to a request itself (chat without tools, healing analyses, module proposals,
build recommendations), they take at most a third of `chat_context_limit` (default 4096 tokens, so about 1365
tokens, estimated at three characters per token). The best excerpts come first. One that does not fit is skipped,
so a smaller later one can still come along. If the first excerpt alone is too long, it is shortened at a line and
marked `[…]`; a code block that is open at the cut is closed, so the rest of the prompt does not read as code.

### Keyword search for identifiers

The keyword index (BM25) also knows the parts of identifiers: `-DGGML_CUDA=ON` is found by `GGML_CUDA`, `GGML` or
`CUDA`, `rknn_toolkit2` by `rknn toolkit2`, and `rknnToolkitLite` by `rknn Toolkit Lite`. A leading `-D` is
dropped only from definitions such as `-DNAME=VALUE` or `-DNAME_WITH_UNDERSCORE` (`-DEBUG` keeps it), and parts of
one or two digits are left out (`x86_64` adds `x86`, not `64`). Only the keyword index gets the split forms; the
stored text and the vectors stay as they are. Searches of one or two words use the keyword index when they contain
an identifier (a digit, an underscore, two capital letters in a row, or a dot or hyphen inside a word, such as
`E2041`, `GGML_CUDA`, `RKNN` or `librkllmrt.so`); other short searches use meaning only. In the retrieval
evaluation this raised hit@3 of the identifier questions from 83 % to 100 %; the other results stayed the same.

A document keeps the keyword vectors it was imported with and stays searchable; importing it again (for example a
new Deep Ingest of the same URL) applies the current splitting.

### Large code blocks and tables

Code blocks and tables stay whole as long as the embedding model sees them whole: up to its context minus 96 tokens
for the line with source, title and section that is embedded with every chunk (512 tokens for the default model
`intfloat/multilingual-e5-small`, so 416 tokens). Larger ones
are split at line boundaries into pieces of the normal chunk size (384 tokens for the default model). Each piece
repeats the table header or reopens the code fence, so the later parts of a long table or script can be found at
all (in the retrieval evaluation, answers deep inside such blocks: hit@1 from 50 % to 100 %). Documents in the
knowledge base keep their old chunks: import them again (for example a new Deep Ingest of the same URL) to apply it.

### Models in memory

The embedding models and the reranker are unloaded after `rag_model_idle_minutes` (default 10) without use and
loaded again on the next search, which takes a few seconds. The time counts from the end of the last search or
import, never during one. `0` keeps them loaded; a longer time than 7 days (10080 minutes) counts as 7 days. The
embedded storage is released separately, after 30 idle seconds (see **Storage** above).

---

## Deep Ingest

Deep Ingest reads documentation pages from the web into the knowledge base. Start it with **Deep Ingest (Add Docs)**
in the module wizard (**Tools > Create Hardware Module...**); the knowledge base must be on. There is no CLI command
for it in this version.

Text and data mining of publicly accessible content is permitted unless the rightsholder reserved it in
machine-readable form (EU Directive 2019/790, Art. 4; German UrhG § 44b). So the crawler checks these signals itself
instead of asking you to confirm anything, and none of the checks can be switched off. The former *Respect
robots.txt* option and the setting `crawler_respect_robots` are gone.

### The notice before the first crawl

- Before the first Deep Ingest of each user, a notice in the interface language explains what is read (only
  publicly reachable pages, no logins, starting from the addresses you enter, within the page and depth limits),
  which opt-outs are honoured, that the knowledge stays on this computer (when Ditto answers, matching excerpts go to
  the AI provider you selected; with a local provider nothing leaves the computer), and that sharing it in a target
  or a community pack keeps the source and licence of every page and needs your explicit release.
- It is information, not a legal confirmation: one button, **Understood** (also Esc), nothing to tick.
- It is shown once per user and again when its text changes in any language. The state file `crawl_notice.json` in
  the per-user folder (`~/.llm-framework`) stores a hash of its texts. If that folder cannot be used, the notice is
  shown every time.

### What the crawler honours

| Signal | Where it is read | Effect |
|---|---|---|
| robots.txt (RFC 9309) | `/robots.txt` of each site, once per scheme, host and port | Disallowed paths are not fetched. The groups whose `User-agent` is `LLM-Conversion-Framework-Bot` (any case) apply, otherwise all `*` groups (a group for `bot` is another crawler's); `*` and `$` patterns work; the longest rule wins, `Allow` wins a tie. A robots.txt that cannot be reached or answers a server error, 401 or 403 counts as "disallow everything" (for 401 and 403 stricter than RFC 9309, which allows crawling: a locked robots.txt may hold a reservation). |
| `X-Robots-Tag` header | every response | `noindex`, `none`, `noai` and `noimageai` keep the page out; `nofollow` and `none` stop following its links. Directives for this crawler (`LLM-Conversion-Framework-Bot: noai`) count, those for other crawlers do not. |
| `<meta name="robots">` and `<meta name="llm-conversion-framework-bot">` | HTML pages | the same directives as the header |
| TDM Reservation Protocol (W3C TDMRep 1.0) | `/.well-known/tdmrep.json` per site (the first matching rule decides), the `tdm-reservation` header, `<meta name="tdm-reservation">`, `tdm:reservation` in PDF metadata | A reservation keeps the page out; a `tdm-policy` is named in the log. |

- A reservation from any of these sources wins. TDMRep lets a later source lift an earlier reservation; the crawler
  stays on the cautious side.
- Redirects are not followed directly. Their targets are queued and checked like any other address: security check,
  robots.txt and `tdmrep.json` of their own host. A link that redirects to another host is skipped; only a redirect
  of the start URL (for example `example.com` to `www.example.com`) moves the crawl to that host. At most 5
  redirects per address.
- The crawler names itself `LLM-Conversion-Framework-Bot/<version> (Open Source;
  +https://github.com/Smilez1985/llm_conversion_framework)`, with the real framework version. Site operators can
  address it in robots.txt and in a meta tag with that name.

### Network safety and limits

- Private, loopback and other non-public addresses are blocked (SSRF protection), and each connection is pinned to
  the checked address, so a DNS answer cannot be switched in between.
- The Deep Ingest dialog sets, for each crawl, the link depth (0 to 5; 0 reads only the entered pages), whether PDFs
  are read and the delay between requests (0 to 10 s, fractions of a second allowed). It opens with the settings
  `crawler_max_depth` (default 2), `crawler_download_pdfs` (default on) and `crawler_rate_limit` (default 1 s); a
  larger configured depth or delay is kept and raises the upper end. What you change in the dialog applies to that
  crawl only.
- From the settings: at most `crawler_max_pages` pages per entered URL (default 50), and only pages in the
  languages of `crawler_languages` (default English and German; an empty list keeps all). A crawl also stops after
  five times as many requests as its page limit (redirects and failed fetches count too).
- Every response is read up to a limit (HTML pages 8 MiB, PDFs 64 MiB, robots.txt 500 KiB, `tdmrep.json` 1 MiB)
  and must arrive completely within 60 s (PDFs 180 s), so a site cannot fill the memory or hold the crawl with an
  endless or dripping response. A larger page is skipped, a slower one fails. Of a larger robots.txt the first
  500 KiB count (RFC 9309 allows that limit); a robots.txt that does not arrive in time means "disallow
  everything", and a larger `tdmrep.json` is not used.

### Progress, Stop and the outcome

- The wizard shows "Page N of M: <address>" before each request (M is the page limit) with a progress bar.
- Pages are stored as soon as they are read. **Stop** ends the crawl after the current page (a waiting delay between
  requests is cut short, and so is a page that is still arriving); what is stored stays. Closing the wizard during a
  crawl stops it the same way: the window stays responsive and closes once the current page is stored.
- The outcome over all entered URLs is reported only after the last write has returned:

  | Outcome | When | Message box |
  |---|---|---|
  | success | every entered URL stored pages; nothing failed, nothing was reserved by a site, the crawl was not stopped | information |
  | partial | pages are stored, but others failed, were reserved by the site, or the crawl was stopped | warning |
  | failure | nothing was stored | error |

- The crawl log in the wizard (and the framework's log) names every page that was not stored with its reason, for
  example "disallowed by robots.txt", "X-Robots-Tag: noai", "TDM reservation (...)", "redirect from ... leaves the
  crawled site", "language 'fr' is not one of de, en", "PDF downloads are switched off", "neither HTML nor PDF
  (image/png)", "no text", "response larger than 8 MiB (the limit for HTML pages)" or "PDF support (pypdf) is not
  installed", and every page that could not be fetched or read (HTTP status, "no complete response within 60 s",
  unreadable PDF, too many redirects). Each page that arrives (HTTP 200) counts toward the page
  limit, stored or not. Site reservations and failures make the outcome partial; the other reasons do not.

### Spoken announcements

- The notice ("Please read the notice before your first Deep Ingest.") once it is on screen, and the outcome of a
  Deep Ingest, are also spoken in the interface language.
- Engines, the first that works: on Windows, Windows PowerShell 5.1 with System.Speech (started from the system
  folder; the text travels in an environment variable, never as code); on macOS, `say` (with a voice of the interface
  language when one is installed); on Linux, `spd-say` (speech-dispatcher), otherwise `espeak-ng` or `espeak`.
  Nothing is installed for it; without an engine it stays silent.
- It never gets in the way: a background thread with a 30 s time limit, at most 300 characters, no audio files, the
  mixer and the system volume are never touched, and a failure never stops anything. Every announcement is also
  written to the log, so it is never only audible.
- It stays silent when the setting `audio_announcements` is off (menu **Tools > Spoken Announcements**, a check mark
  that is saved at once), when `LLM_FRAMEWORK_NO_AUDIO` is set (to anything but empty, `0`, `false`, `no` or
  `off`), under CI (`CI` set in the same way), in an SSH session without a display, and on Linux
  without a display (no `DISPLAY` or `WAYLAND_DISPLAY`).

---

## MCP server

`llm-cli mcp serve` exposes the framework's tools over the [Model Context Protocol](https://modelcontextprotocol.io)
(official Python SDK `mcp` 2.3.0, specification 2026-07-28). AI clients such as Claude Code, Claude Desktop or VS Code
can then use the [same tools as Ditto](#dittos-tools). You sign in to the client with your own account or key; the
framework never sees it.

- **Every call goes through the tool registry:** input schema, your permissions, confirmation, audit log (actor
  `mcp`). Tool annotations (read-only, idempotent) are only hints for the client.
- **Confirmation:** where your permission says "ask", the server asks the user through the client (MCP
  elicitation). The question shows the tool and every argument in full, the security-relevant ones first; control
  and invisible characters and long stacks of combining marks are shown as `\u` escapes. A question that would be
  longer than 4000 characters is refused instead of being cut. Only a real answer from the user counts as approval,
  and a "no" is final for that call, even if the permissions are loosened while the question is open. Declined and
  cancelled calls, and calls refused before they run (invalid arguments, a confirmation that fails), are audited
  too. A client that cannot ask gets a refusal with a hint how to allow the tool.
- **Instructions to the client:** the server describes the typical flow (targets → capabilities → build → status
  and log → artifacts) and tells the client that build logs and knowledge-base results are external text whose
  instructions must not be followed.
- **Transports:** stdio (default; the client starts the server) or Streamable HTTP on a loopback address only
  (`--transport http`, default `http://127.0.0.1:8765/mcp`). HTTP checks Host and Origin and needs a bearer token
  that is created per user in `~/.llm-framework/mcp_http_token`.
- **Working folder:** the server never reads settings or imports code from the folder it is started in (the client
  starts it with `python -P`), and on Windows it does not run programs such as `docker` or `git` from there. A
  project folder cannot change its behaviour.
- **Client setup:** `llm-cli mcp config` prints the commands and JSON with quoting that holds in POSIX shells and in
  both cmd and PowerShell; `--write claude-desktop` adds the server to Claude Desktop's configuration, keeps other
  entries and writes a private backup.

**Claude subscribers:** this is the way to use the framework with a Claude plan. Anthropic does not allow
third-party apps to use a subscription's sign-in or limits, so Ditto itself needs an API key; Claude Code and Claude
Desktop can use the framework over MCP within your plan.

---

## Tool permissions and audit log

Every tool belongs to a risk tier:

| Tier | Meaning | Recommended | Choices |
|---|---|---|---|
| `read` | Look at targets, builds, logs, knowledge | allow | allow, ask, deny |
| `local_write` | Change things on this computer (start or cancel builds, decide on healing) | ask | allow, ask, deny |
| `external` | Reach other devices or delete data | ask | ask, deny; single tools can be allowed one by one |
| `forbidden` | Never callable (for example arbitrary shell commands) | deny | deny |

There are no `external` or `forbidden` tools yet; deploying and uploading are not available as tools.

- The permissions are asked once (GUI dialog on the first start, `llm-cli ditto setup`) and stored in
  `~/.llm-framework/tool_policy.json` (readable only by you). Single tools can be set differently in the GUI.
- Until the setup is done, read-only tools are allowed and everything else asks.
- A damaged or unreadable permission file, or a link to a missing one, denies every tool instead of falling back to
  looser defaults; a damaged file is kept as `tool_policy.json.bad`, and `llm-cli doctor`, `llm-cli tools list` and
  `llm-cli mcp serve` say so.

**Audit log** (`~/.llm-framework/audit.db`, SQLite): every tool call is recorded with time, actor, tool, decision,
whether a person confirmed it, and the outcome. Arguments are stored redacted and shortened, plus a hash of the full
text. Each entry contains the hash of the previous one; with the key from your operating system's keyring this is an
HMAC chain that someone who can only edit the database cannot recompute. After every entry the newest one is also
written to an anchor file, so removing the newest entries is detected too. `llm-cli tools audit --verify` checks the
chain and tells a changed entry apart from a wrong key. Each entry is written in its own transaction, so several
processes cannot fork the chain. `llm-cli tools audit --archive` moves the log and its anchor aside (kept with a
timestamp) and starts a new chain, for example after `llm-cli doctor` has reported a problem with the log.

---

## Secrets and personal state

- **Secrets** (API keys, tokens): encrypted (Fernet, each entry with a key id) in the vault
  `~/.llm-framework/secrets.store`. By default its key is kept in your operating system's keyring (Windows
  Credential Manager on the local machine, macOS Keychain, Secret Service on Linux), one entry per vault, never in a
  file next to the store. A keyring that does not answer within 3 seconds, keeps nothing or stores in plain text is
  not used; then the framework stops and names the choices (the GUI shows a chooser, the CLI exits with code 5
  and names `llm-cli ditto auth storage --use ...`) for the setting `secret_storage` (or
  `LLM_SECRET_STORAGE` for one run): `helper` (the command in `secret_helper`, run without a shell), `passphrase`
  (asked once per start), `insecure` (a key file next to the vault, not encrypted at rest) or `ephemeral` (memory
  only). The vault is written atomically under a lock shared by the GUI, the CLI and MCP servers, is readable only
  by you, never lies inside a git working copy, and a damaged vault is never overwritten. `llm-cli doctor` warns
  about the key file and about a keyring that does not protect secrets. `llm-cli ditto auth rotate-key` replaces
  the vault key and re-encrypts every entry.
- **Redaction:** one table of secret formats (`orchestrator/security/secret_patterns.py`) is shared by the
  repository's secret scan and the runtime redaction. Credentials (known key formats, credential headers, tokens
  in URLs and form bodies, `HF_TOKEN=...` lines) are removed from every log line and traceback (the framework's own
  and those of the SDKs and HTTP libraries), from build logs read through the tools, from provider and tool error
  messages, from the arguments in the audit log and from everything sent to cloud providers. The SDKs' debug logs
  stay off unless you start with `--debug-http`.
- **Personal folder** `~/.llm-framework` (`LLM_FRAMEWORK_HOME`), private to your user: tool permissions, audit log,
  SSH key and `known_hosts`, MCP token, chat history, secrets, knowledge base and snapshots, local runtime, the
  [input history](#input-history), the state of the [Deep Ingest notice](#the-notice-before-the-first-crawl), the
  cached [community index](#the-signed-index) with the pinned author keys, and two random values per installation
  (one for the privacy placeholders, one as an anonymous cache ID for OpenAI). Your key pairs for
  [community packs](#encrypted-packs-for-private-sharing) are kept in the encrypted secrets store.
- **Settings** are never read from the working directory: `LLM_FRAMEWORK_CONFIG_DIR`, then `configs/` in the
  installation if you may write there, then the personal folder.
- **Data folders** (`output/`, `cache/`, `models/`) are in the installation if you may write there, otherwise in the
  personal folder (on Windows `C:\Program Files` is read-only for standard users). They are created when a build or
  a download first needs them, not at every start.

---

## Input history

The GUI's input fields offer what you used recently, newest first.

| Field | Where | Filled in | Remembered when |
|---|---|---|---|
| Model | Builder, **Source Model** | the most recent one | a build is submitted |
| Calibration dataset | Builder, **Calibration** | no, only offered | a build with a dataset is submitted |
| Target and board | Builder | the most recently built target and board that the target definitions still offer; a target without declared boards offers recent boards as suggestions | a build is submitted |
| Profile | Deployment | the profile chosen before, else the most recent one | a deployment is started |
| Deep Ingest URL | **Recent** list in the Deep Ingest dialog, added with **Add** | no, only offered | the dialog is confirmed |
| Device (IP address or host name) | Deployment, remote hardware scan (**Scan / Import...**) | the most recent one | its SSH host key is confirmed |

- The history is per user: `input_history.json` in the per-user folder (`~/.llm-framework`, or
  `LLM_FRAMEWORK_HOME`), readable only by you, at most 20 entries per field, without duplicates. It is not part of
  the shared `configs/config.yml`; the former setting `input_history` is gone.
- It never holds secrets. Not remembered are values that look like an API key or a token, URLs with a user name or
  password, URLs with a credential or session parameter in the query, the fragment or a path parameter (such as
  `?token=`, `?api_key=`, `#access_token=`, `;jsessionid=` or `?sid=`), `user:password@host`, text with control
  characters and values longer than 1000 characters. The same rules apply to a file edited by hand.
- A damaged file starts an empty history; the old file is kept as `input_history.json.bad`.
- **Tools > Clear Input History...** asks once, then deletes the file and the `.bad` copy, and the fields are
  refilled at once.

---

## Hardware probe and new targets

- **Probe:** one hardware probe in three implementations writes the same file, `target_hardware_config.txt`
  (schema 2): `scripts/hardware_probe.py` (the main path: standard library only, Python 3.8 or newer, Linux, Windows
  and macOS) and the fallbacks `scripts/hardware_probe.sh` (any POSIX shell on Linux, for boards without Python) and
  `scripts/hardware_probe.ps1` (Windows). It records the operating system (ID, version, family, variant such as
  DietPi, kernel, init system), the architecture of the userland, the C library and its version (glibc, musl), the
  CPU (model, cores, ARM core clusters, the x86 and ARM features ggml builds for, such as AVX-512 or the ARM dot
  product), memory, SoC, NPU and every GPU (NVIDIA compute capability, AMD gfx target, Vulkan); no host name, serial
  number or MAC address. The probes need no network, never prompt, install or ask for administrator rights, and run
  external tools with a time limit. Tests run the `.py` and the `.sh` on the same fixture machines and require the
  same result. The installed package carries a byte-identical copy of `hardware_probe.py`
  (`orchestrator/runtime/hardware_probe.py`), so the framework's own probing also works without the `scripts/`
  folder.
- **Where it runs:** on the machine the build is for. `llm-cli scan` probes this computer with the Python probe and
  keeps the profile as `local`; `llm-cli scan --device` runs it on a board over SSH (the Python probe with `python3
  -I` if the board has Python 3.8 or newer, else the shell probe; nothing is installed on the board) and keeps the
  profile under the board's ID; the SSH user becomes the board's deploy user (never root). In the GUI, **Scan /
  Import...** imports a probe file, scans a board over SSH with the same trust-on-first-use rules as deployment, or
  scans this PC; it keeps scan results in `<cache_dir>/probes/`. The GUI's SSH scan runs the same remote probe as
  `llm-cli scan --device`: the script travels over the SSH channel, so it also works on boards that mount `/tmp`
  with `noexec`, and the profile is kept under the board's ID.
  Machines without network access run a probe by hand ([README](../README.md#probing-the-boards-hardware)).
- **Ditto's local model** uses the same probe: the hardware its build and model choice are based on comes from a
  probe profile, and free RAM and free VRAM are read again at every start.
- **New target by questions:** `llm-cli module create` or the wizard under **Tools > Create Hardware Module...**.
- **New target from a probe:** `llm-cli module generate-ai <probe file>` or the wizard's AI analysis. Ditto proposes
  architecture, SDK, base image, packages, CPU and CMake flags, setup commands and the quantization block of
  `build.sh`; you see the whole proposal first.
- **The device runtime in the module:** every new `target.yml` gets a `runtime:` block, the build recipe's plan for
  the board's llama.cpp runtime from the target's probe (the wizard's imported probe file, the probe file of
  `module generate-ai`, or `llm-cli module create --probe <file>`): architecture, OS, the C library the cross build
  must not exceed, the builder base, the CPU options (for the RK3566 `armv8.2-a+dotprod+fp16`), the backends and the
  programs. It says that llama.cpp is built from source; it is a record, and the runtime is always planned again from
  the device's own probe. CPU and CMake flags from the wizard or Ditto are kept there under `advisory` and never
  applied.
- **llama.cpp in new targets:** the template Dockerfile builds the conversion tools like the Rockchip image
  ([Conversion tools tuned to the build host](#conversion-tools-tuned-to-the-build-host)). The GPU template takes
  CUDA 12.9.1 from NVIDIA's redist archives (manifest pinned by sha256, every archive checked) on Ubuntu 22.04 pinned
  by digest; NVIDIA's prebuilt development image is only an explicit opt-in (a base image you choose yourself; Ditto's
  proposals never carry one), pinned by digest and labelled "not self-built".
- **Checks before anything is written:** names, images, packages and flags are validated strictly; shell code is
  checked against a deny-list and with `bash -n`; files are written only inside `targets/`; an existing target is
  never overwritten. Setup commands run later as an unprivileged user without network. A target that pulls a
  prebuilt llama.cpp image, copies llama.cpp binaries out of another image, downloads release archives or binaries
  or installs a llama.cpp package is refused: the framework shares recipes, not binaries.
- **Knowledge snapshot (opt-in):** on the wizard's last page, **Attach the knowledge crawled for this target** (off
  by default, available after a Deep Ingest in the wizard) writes the pages crawled from the wizard's Deep Ingest
  URLs, privacy-scrubbed, to `knowledge/initial_knowledge.json` in the new target, with a `README.md` that says
  what it holds. While a Deep Ingest is still running, the wizard's **Finish** stays disabled (the page says why),
  so the snapshot holds every crawled page; if no stored page comes from those URLs, the wizard says that no
  snapshot was attached. The rest of your knowledge base never goes into a module, and
  `llm-cli module create` and `module generate-ai` attach nothing. The framework does not import such a snapshot
  into a knowledge base; to share knowledge, export a knowledge pack.
- **Export for community:** the wizard's last page also offers **Export for community after creating** (see
  [Export for community](#export-for-community)).

Details: [Module_Developer_Guide.md](Module_Developer_Guide.md).

---

## Health check (doctor)

`llm-cli doctor` (and the read-only `run_doctor` tool) runs deterministic checks and explains each finding:

| Area | Checks |
|---|---|
| Python | supported version (3.11 to 3.14) |
| Docker | daemon reachable, buildx available |
| Disk | free space for the cache and output folders (warning below 20 GB, error below 5 GB) |
| Targets | targets found, capabilities readable |
| Security | personal folder private, secrets store usable, keyring protects secrets |
| AI | tool permissions set up and readable, audit log intact (changed, removed, wrong key, missing anchor, no key) |
| Data | shipped calibration data intact (checksums) |
| Community | installed community packs against the cached index: revoked (error), changed since installation (warning), trust `local` (information) |
| Runtime | the self-built llama.cpp build in use: changed files (error), older than the pin (warning); the prebuilt archive, labelled not self-built, in use or installed but not in use (information); whether this machine has the toolchain for a native build (information) |

`--fix` applies the automatic fixes up to `--tier` (1: makes the personal folder private; 2: also removes dangling
framework images and the Docker build cache) and checks again. Exit codes: 0 OK, 1 warnings, 2 errors; `--json`
for scripts.

---

## Community zoo

The community zoo shares three kinds of packs: **targets** (hardware modules), **recipes** (how a model was built
for a target) and **knowledge** (pages for the knowledge base). Packs are listed from a signed community index,
installed through a quarantine folder and exported from your own work for a pull request. Commands:
`llm-cli zoo list|show|install|verify|export|keys`; in the GUI the [Community Hub](#community-hub). The framework
never uploads anything and never asks for, reads or shows a repository token.

**State today:** no community repository, index or maintainer key is published yet. `configs/community_keys.yml`
lists no key, and the settings `community_index_url` and `community_repo` are empty by default. So `llm-cli zoo
list` and the hub show only installed packs, only pack files can be installed (trust `local`), and a pull request
has no repository to go to.

### Packs

- A pack is a zip file with `pack.json` (the manifest: kind, id, version, author, licence, minimum framework
  version, creation time, and the sha256, size and mode of every file), an optional `pack.sig` (the author's Ed25519
  signature over the manifest) and exactly the listed files. Reading a pack accepts nothing else: no links, no
  special files, no unlisted files, and every hash is checked. Packs carry text only (scripts, recipes, pages),
  never model weights or binary files; at most 512 MB and 10,000 files.
- **Trusted keys:** `configs/community_keys.yml` lists the publisher keys that sign the community index, each with
  `key_id`, `pubkey_b64`, `valid_until` (the last day, in UTC, on which its signatures are accepted) and an optional
  `name`. The file ships with each release; a key rotation ships the old and the new key for one release. A damaged
  file is an error, never read as "no keys".
- **Author signatures** show where a pack comes from; they do not make it trusted. A signature is checked when the
  pack includes the public key (a broken signature refuses the pack), and the author's public key (the whole key,
  not its short key id) is pinned at the first installation of a pack by that author. A later pack with the same
  author name and another key gets a warning in the summary.

### Encrypted packs for private sharing

For your own machines or a team, a pack can be encrypted instead of published (`llm-cli zoo export ... --to <key
file>` or `--passphrase`):

- It is encrypted in the [age](https://age-encryption.org/v1) format by rage, the Rust implementation of age
  (through `pyrage`): to one or more recipients' age keys (`age1...`, at most 64) or to one passphrase (at least 12
  characters, scrypt), never to both, as age requires. The sender signs the encrypted file with Ed25519, so
  recipients see who sent it, and any change, including an added or removed recipient, is detected.
- The `.lcfpack` file is a zip with the age file `pack.zip.age` and the sender's signature `pack.zip.age.sig`. The
  standard `age` and `rage` tools open it too when it is encrypted to a passphrase or to a recipient made with
  `age-keygen` or `rage-keygen`: unzip it, then `age -d -i key.txt -o pack.zip pack.zip.age`, where `key.txt` is
  that identity file (for a passphrase pack without `-i key.txt`). The age identity the framework creates for you
  never leaves the secrets store, so a pack encrypted to it opens only with `llm-cli zoo install`. The age tools do
  not check the sender's signature; the framework does.
- **Your keys:** on first use every user gets an Ed25519 key (signs packs) and an age identity (receives encrypted
  packs). They are kept only in the encrypted secrets store. `llm-cli zoo keys` prints the public keys as text (an
  `age1...` recipient and an `ed25519:` line) to give to others; a recipient made with `age-keygen` or
  `rage-keygen` works with `--to` as well. A stored key that cannot be read is never replaced automatically.
- **Receiving:** `llm-cli zoo install --file <pack>.lcfpack` decrypts with your key, or with `--passphrase`.
  `--from <key file>` (repeatable) accepts only these senders; otherwise the summary names the sender's key id. A
  passphrase pack whose scrypt work factor is above 2^20 (1 GiB of memory) is refused before anything is decrypted.
  The hub's **Import file...** opens packs encrypted to your key; a passphrase pack needs the CLI. An encrypted pack
  is never submitted to the community.

### Recipes

A recipe is the zoo's "model template": the Hugging Face model at a pinned commit (never a branch or tag), the target
with the fingerprint (sha256 over its files) and version of the target folder, board, format, quantization, options
(importance matrix, context length, name and sha256 of a calibration dataset) and verified results (artifact file,
size and sha256, framework version, toolchain image, optionally a device test). It carries no weights: a recipe is
rebuilt, never downloaded as a model.

- A recipe is generated from a finished build's `build_info.json`, never typed by hand. `build_info.json` records
  what a recipe needs: the model's commit (the download is pinned to it), the target fingerprint and version, the
  toolchain image, the context length, the calibration dataset's hash and the framework version. A build of a local
  model, or one without a target fingerprint, cannot become a recipe.
- At installation a recipe is checked against the capabilities of its target, if that target is installed. A note
  says when the target is missing or when your copy differs from the one the recipe was built with.
- **Build** on an installed recipe in the hub fills in the Builder (model at its pinned commit, target, format, board,
  quantization, importance matrix). You check the settings and start the build; a recipe maps to a normal build
  request.

### The signed index

- `index.json` and `index.json.sig` next to it, at the https address in the setting `community_index_url`, signed
  by a key from `configs/community_keys.yml`. An index whose signature does not verify is not used.
- The last verified index is cached per user in `~/.llm-framework/community/`. It is fetched again when you ask
  (`llm-cli zoo list --refresh`, **Refresh** in the hub), when nothing is cached, or when the cache is older than a
  day; without network the cached index is used. A downloaded index with a lower version than the cached one is
  refused, so an older index never replaces a newer one. The cache is verified again whenever it is used, so a
  removed or expired key invalidates it.
- The index also lists revoked packs (by sha256, or by kind, id and optionally version, each with a reason).

### Installing

`llm-cli zoo install <id> [--kind target|recipe|knowledge]`, `llm-cli zoo install --file <pack>`, or **Install** and
**Import file...** in the hub:

1. An installation from the index always reads the current index first, so a revocation is never missed; if the
   index cannot be updated, nothing is installed. A pack file is looked up in the index (updated when older than a
   day; the cached one without network): if the index lists its sha256, it gets that entry's trust level, otherwise
   `local`.
2. A pack from the index is downloaded pinned by the size and sha256 the index gives.
3. The pack is extracted into a quarantine folder (`.quarantine/` in the install folder) and checked: exactly the
   listed files with their hashes, no links, special or binary files, and the checks of its kind. Targets: a valid
   `capabilities` block (and `runtime` block, if there is one), every image a Dockerfile pulls (`FROM`,
   `COPY --from`, `RUN --mount=...,from=`) pinned by digest unless it is an official base image, no prebuilt
   llama.cpp (images, copied or downloaded binaries, packages), the shell deny-list on every script line that is not
   from the shipped template, `bash -n`, no template placeholders. Recipes: the recipe schema and the target's capabilities.
   Knowledge: a list of pages, each with its source.
4. A summary (kind, id, version, author, licence, trust level, source, files, destination and notes such as the
   author signature) is shown as plain text, so text from a pack cannot hide its trust level. Nothing is installed
   until you confirm; the default answer is no. `--yes` skips the question only for packs whose trust level comes
   from the signed index, also a file whose sha256 the index lists; a pack with trust `local` always asks, and
   without a terminal nothing is installed.
5. The pack moves into place in one rename, never over an existing folder (also when only the case differs), with
   a provenance file `.lcf-provenance.json` (source, trust level, hashes, signer, time). Anything that fails is
   removed.

Targets go to `targets/`, recipes to `community/recipes/`, knowledge to `community/knowledge/`. A knowledge pack is
imported into the knowledge base right after its installation or, if the knowledge base is off, as soon as it is
turned on.

### Trust levels

| Level | Meaning |
|---|---|
| `verified` | in the signed index: reviewed and reproduced independently |
| `community` | in the signed index: reviewed |
| `local` | a file without an entry in the signed index: nobody reviewed it. Installing always asks, and every build of such a target shows a warning. |
| `revoked` | in the revoked list of the signed index: installing is blocked. Installed copies are flagged by `llm-cli zoo list`, `zoo verify`, `llm-cli doctor` and the hub and are not built, also after the key that signed the revocation has expired. |

Targets that ship with the framework, such as Rockchip, have no provenance file; they are not packs and have no
trust level.

### Checking installed packs

- `llm-cli zoo verify` checks every installed pack against the cached index: revoked, or changed since it was
  installed. The exit code is 1 when it finds a problem.
- `llm-cli zoo verify <pack file>` checks a file without installing it: format, hashes, the checks of its kind, an
  included author signature and the privacy scrub (no home folders, addresses, host or user names, keys). It needs
  no keyring; the community repository's CI uses it.
- `llm-cli doctor` reports revoked packs as errors, changed packs as warnings and packs with trust `local` as
  information ([Health check](#health-check-doctor)).

### Community Hub

**Tools > Open Community Hub** lists the index entries and the installed packs with name, kind, version, a coloured
trust column, author and status, with a search field and filters for kind and trust. **Refresh** fetches the index
again, **Install** installs an index entry after its summary, **Import file...** installs a pack file (also one
encrypted to your key), and **Build** on an installed recipe fills in the Builder. A revoked pack shows a disabled
**Revoked** button.

### Export for community

- **Where:** the module wizard's last page (**Export for community after creating**: the new target), the Builder
  log's context menu (**Export a finished build for the community...**: a finished build's `build_info.json` becomes
  a recipe), and `llm-cli zoo export target <id>`, `zoo export recipe <build folder>` or `zoo export knowledge`,
  each with `--author <name>` (a pseudonym is fine). Knowledge packs are exported with the CLI only.
- **Privacy scrub:** every file is scrubbed on this computer. Home folders become `~`, IP and MAC addresses `<ip>`
  and `<mac>`, e-mail addresses `<email>`, JWTs `<jwt>`, the host name `<host>`, the user name `<user>`, and API
  keys are redacted. Loopback addresses, image digests, `git@` remotes and default accounts such as `root` or
  `runner` stay, so a scrubbed target still builds. The changes are shown as a diff before you share anything.
  Binary files are refused.
- The pack is checked like an installation and signed with your key. It is written to `community/contributions/`
  (or `--out`).
- **Licence:** MIT by default for your own target or recipe (`--license`; a list in the GUI). Knowledge packs hold
  other people's pages: their licence is `NOASSERTION`, every page keeps its source address, and a page without a
  source is never exported.
- A target you installed from a community pack is not exported as your own: signing it again would replace its
  author and licence. When a recipe's target is your own, a note reminds you to export the target too. A knowledge
  export takes the pages of your knowledge base, each with its source address; pages imported from installed
  community knowledge packs are left out (their authors share them already), and a note says how many.
- With `--to` or `--passphrase` the pack is [encrypted](#encrypted-packs-for-private-sharing) for private sharing
  instead.

### Pull request handoff

- **Open pull request** in the export dialog, or `--pr` with `llm-cli zoo export`, hands the pack to the repository
  in the setting `community_repo` (`owner/name`). The CLI first shows the summary and asks "Submit this pack to the
  public community repository?" unless `--yes` is given; without a terminal nothing is submitted.
- If the GitHub CLI (`gh`) and `git` are installed and `gh` is logged in, the framework forks the repository,
  commits the pack as `contributions/<kind>/<file>` on a new branch and opens the pull request with `gh pr create`;
  `gh` and `git` use their own stored credentials. Otherwise, or if that fails, the repository's upload page opens in
  the browser: drop the file there, and GitHub forks the repository for you.
- Without `community_repo` (the default today) nothing is handed over; the pack stays ready in its folder.

### Maintainer tools

For the community repository, not for users:

- `scripts/zoo_sign_index.py keygen <name>` creates an Ed25519 signing key in the OS keyring and prints its entry
  for `configs/community_keys.yml` (valid for two years). `entry <pack.zip> --url <https address>` checks a pack and
  prints its index entry. `sign index.json --key <name> [--previous <old index.json>]` checks the index (its version
  must be higher than the previous one) and writes `index.json.sig`. To revoke a pack, add it to `revoked`, raise
  the version and sign again.
- `scripts/community_repo/validate-contribution.yml` is a CI workflow template for the community repository. On
  every pull request under `contributions/` it accepts only `.zip` pack files of at most 5 MB and runs
  `llm-cli zoo verify` on each, whose privacy scrub also rejects a pack with a key in any of its files (the
  repository's secret scan skips binary files, so it does not run on the zips). A maintainer reviews and merges the
  pack, adds its entry to `index.json` and signs the index offline; the signing key never reaches CI. The maintainer
  sets the framework release the packs are checked with (`LCF_REF`); until then the template holds a placeholder,
  so the job fails.

### What was removed

- The "swarm" upload: the XOR "SwarmCipher" (an obfuscation anyone could undo), `llm-cli swarm upload`, the hub's
  "Contribute to Swarm" button and its GitHub token prompt. Old swarm packets in `community/knowledge/` are skipped
  with a warning.
- Installing hand-copied module folders from `community/`. Community packs are installed with `llm-cli zoo install`
  or the hub.

---

## Secret scan for contributors

The repository protects itself against committed credentials. `scripts/secret_scan.py` (standard library only)
recognizes provider API keys, tokens, private keys, certificates and credential files. It runs as a pre-commit and
pre-push hook (`make hooks`) and as a CI job over the whole history. Personal strings such as your e-mail address
or user name go into `.git/info/secret-patterns`, which is never committed. Findings are printed redacted. A value
in a provider's exact token format is reported even when it looks fake, because GitHub's push protection would
refuse the push anyway. Details: [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Known gaps

What does not work yet, so you are not surprised:

- **Docker setup of the framework itself:** `Docker Setup/` and `make setup`, `make build`, `make up`, `make gui`,
  `make test-container` do not work (`scripts/setup_linux.sh` and `orchestrator/Dockerfile` do not exist). A
  hardened headless image for the CLI and the MCP server is being built. Conversions are not affected.
- **Windows hosts:** conversions on a Windows host are not verified.
- **Browser and cloud sign-in:** OAuth flows and the credential chains of AWS, Google Cloud and Azure are not
  available yet; Ditto signs in with API keys, and local engines need none.
- **Device runtime:** the self-built llama.cpp runtime on a board works from the command line and is tested with a
  fake SSH channel, not yet on a real board; there is no GUI or MCP tool for it yet.
- **GUI:** the Auto-Tuning and Models tabs are placeholders; the local runtime is controlled from the CLI only.
- **Deep Ingest** runs only from the module wizard in the GUI; there is no CLI command for it. Restoring a
  knowledge-base snapshot is not available from the GUI or the CLI.
- **Reranking** runs only for build recommendations; `rag_rerank_default` adds the chat and the MCP tool
  `search_knowledge`.
- **Spoken announcements** are tested with simulated speech engines only, not with the real voices of Windows,
  macOS or Linux.
- **Community zoo:** no community repository, index or maintainer key is published yet (`configs/community_keys.yml`
  is empty, `community_index_url` and `community_repo` are empty), so only pack files can be installed and a pull
  request has no repository to go to.
- **Security scan:** the Trivy container gets the Docker socket to read the image; scanning without the socket is
  planned.
- Smaller CLI gaps are listed in [CLI_Usage.md](CLI_Usage.md#known-limitations).

What is planned: [roadmap](BLUEPRINT.md#roadmap). What changed: [CHANGELOG](../CHANGELOG.md).
