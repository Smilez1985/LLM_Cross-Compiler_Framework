Version 3.0.0 + unreleased changes · Last updated 2026-10-17

**English** · [Deutsch](CONTRIBUTING_DE.md)

# Contributing to the LLM Cross-Compiler Framework

Thank you for helping. This guide explains how to set up a development environment, run the same checks as the
CI, keep credentials out of the repository, and submit changes. If you only want to use the framework, start with
the [README](README.md). How the framework is built is described in the [blueprint](docs/BLUEPRINT.md).

Ways to contribute:

- **Report problems:** describe what you did and what happened, and attach the output of `llm-cli mojo --json`
  and the build log. Remove personal data (host names, user names, keys) first.
- **Add or improve a hardware target** (see [section 7](#7-adding-a-hardware-target)).
- **Share a target, a recipe or knowledge** as a signed community pack (see
  [Contributing to the community zoo](#contributing-to-the-community-zoo)).
- **Fix bugs and write tests.**
- **Improve the documentation**, in English and German.

## Checklist for a pull request

- [ ] Tests pass: `QT_QPA_PLATFORM=offscreen python -m pytest`
- [ ] Lint and security checks pass ([section 5](#5-lint-and-security-checks))
- [ ] The secret scan hooks are enabled (`make hooks`) and the scan is clean ([section 6](#6-secret-scan-and-git-hooks))
- [ ] New behaviour is covered by tests in `tests/`
- [ ] A new CLI command or option has its mouse path in the GUI and a `CLI_PATHS` entry, or a `MACHINE_ONLY` reason
  ([section 8](#8-code-guidelines))
- [ ] Entry under `[Unreleased]` in `CHANGELOG.md` and `CHANGELOG_DE.md`
- [ ] Documentation updated in English and German
- [ ] No secrets, personal state, binaries or model files in the diff

---

## 1. Development setup

Get the code:

```bash
git clone https://github.com/Smilez1985/LLM_Cross-Compiler_Framework.git
cd LLM_Cross-Compiler_Framework
```

Up to release 3.0.0 the repository was called `llm_conversion_framework`. GitHub forwards the old address, so an
existing clone keeps working; to point it at the new address:
`git remote set-url origin https://github.com/Smilez1985/LLM_Cross-Compiler_Framework.git`.

You need **Python 3.11 to 3.14**, Git, and Docker for real builds. On a Linux machine without a desktop, the Qt
GUI tests need a few system libraries; the CI installs
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0`.

### Option A: uv

Installs exactly the pinned, hashed versions from the requirements files:

```bash
uv venv --python 3.13
source .venv/bin/activate              # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt -r requirements-dev.txt

python -m orchestrator.cli --help      # command line
python -m orchestrator.main            # GUI
```

### Option B: uv project environment

Installs exactly the versions pinned in `uv.lock`:

```bash
uv sync                                # runtime and dev dependencies plus the llm-cli, llmcf and llm-gui commands
uv run llm-cli --help
uv run llm-gui
```

### Keep your development setup apart from your real one

- `LLM_FRAMEWORK_CONFIG_DIR=/some/folder` makes the framework use that folder for its settings. Without it, a
  checkout uses its own `configs/` folder and writes into it (`config.yml` and the folders `environments/`,
  `templates/`, `backups/`).
- `LLM_FRAMEWORK_HOME=~/.llm-framework-dev` keeps secrets, tool permissions, audit log, SSH key, known hosts, chat
  history and the local model server of your development setup apart from your real `~/.llm-framework`.
- `LLM_SECRET_STORAGE=ephemeral` lets the CLI start on machines without a system keyring (for example in
  containers or CI): secrets are kept in memory for that run only. A keyring backend that keeps nothing, such as
  `keyring.backends.null`, is refused. `LLM_FRAMEWORK_HOME` must not point into a git working copy, because the
  secrets vault refuses to live there.

---

## 2. Dependencies

- `pyproject.toml` declares the allowed versions (`[project]`, the dev tools in `[dependency-groups]`), `uv.lock`
  pins them (the lock file of [uv](https://docs.astral.sh/uv/)).
- `requirements.txt` (runtime) and `requirements-dev.txt` (dev tools) are exports of `uv.lock` with hashes. Their
  first lines name the command that made them. The installers and the CI install from them. **Never edit them by
  hand.**
- `anthropic`, `openai`, `fastembed` and `mcp` are pinned to exact versions on purpose: LLM tooling is a
  supply-chain target (see the comments in `pyproject.toml`). If you change such a pin, explain why in the pull
  request.

After adding, removing or updating a dependency:

```bash
uv lock                                         # update uv.lock (other pins stay; one package: --upgrade-package NAME)
uv lock --check                                 # pyproject.toml and uv.lock agree
uv export --frozen --format requirements-txt --no-dev --no-emit-project -o requirements.txt
uv export --frozen --format requirements-txt --only-group dev --no-emit-project -o requirements-dev.txt
```

Then run pip-audit ([section 5](#5-lint-and-security-checks)) and the tests, and commit `pyproject.toml`, `uv.lock`
and both requirements files together. The CI job "Lock files" runs the same commands and fails when a file differs.

Dependabot opens weekly pull requests for Python packages (`pyproject.toml` and `uv.lock`), GitHub Actions and the
target Dockerfiles. It changes only `uv.lock`: run the two export commands on its branch and push the requirements
files too.

---

## 3. Running the tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest      # in the uv project environment: QT_QPA_PLATFORM=offscreen uv run pytest
```

Windows (PowerShell):

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
```

- `tests/conftest.py` isolates every test: a temporary home and working directory, `LLM_FRAMEWORK_CONFIG_DIR`
  pointing to a temporary folder, all `LLM_*` and `FRAMEWORK_*` variables and the provider keys removed
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` and every other variable a method in
  `configs/llm_providers.yml` lists under `env`). Tests must not depend on your real `~/.llm-framework`, your
  keyring, real API keys or the network; use the `memory_keyring` fixture and fake servers.
- `tests/test_versioning.py` checks that `orchestrator/__init__.py`, `pyproject.toml` and the newest released
  changelog entry carry the same version.
- `tests/test_foundation.py` and `tests/test_review_capabilities_paths.py` check that the Rockchip capabilities in
  `target.yml` match `modules/build.sh`, `modules/rkllm_module.sh`, `modules/rknn_module.sh` and
  `scripts/export_rkllm.py`.

### The Python 3.11 to 3.14 matrix

The CI runs the tests on Ubuntu with Python 3.11, 3.12, 3.13 and 3.14, and on Windows with Python 3.13
(`.github/workflows/ci.yml`). To run the same matrix locally with uv (it downloads missing Python versions):

```bash
for v in 3.11 3.12 3.13 3.14; do
  uv venv --python "$v" ".venv-$v"
  uv pip install --python ".venv-$v" -r requirements.txt -r requirements-dev.txt
  QT_QPA_PLATFORM=offscreen ".venv-$v/bin/python" -m pytest -q || echo "FAILED on Python $v"
done
```

A change that only works on one Python version is not ready. Typical differences are standard-library behaviour
(for example tar extraction filters) and wheels that are not yet available for the newest version.

### The real-client smoke test

The unit tests drive the MCP server with the SDK's own client. `scripts/mcp_client_smoke.sh` (POSIX sh) checks the
server with a real one, Claude Code, as a manual check before a release or as an optional CI job on a runner where
the claude command is installed and signed in:

```bash
LCF_SMOKE_PYTHON=.venv/bin/python scripts/mcp_client_smoke.sh
```

- It builds a throw-away environment (home, Hugging Face home, XDG folders, `LLM_FRAMEWORK_HOME`,
  `LLM_SECRET_STORAGE=ephemeral`, the null keyring backend, `QT_QPA_PLATFORM=offscreen`, the Hugging Face Hub offline)
  and writes an MCP configuration that starts `python -P -m orchestrator.cli -q mcp serve` in the repository under
  `env -i`, so the server gets only those variables and nothing of your environment.
- It runs `claude -p` with `--mcp-config <file> --strict-mcp-config --allowedTools mcp__lcf --tools ""
  --output-format json --verbose --no-session-persistence` and four scripted prompts, one session each.
- `scripts/mcp_smoke_check.py` then checks the outcomes from the isolated audit database, never from the model's
  answer: the client line, `get_status` ran, an unknown argument was refused with a suggestion, the declared alias
  `quant` was recorded on a call that ran and on one that was refused, and a call that needs a confirmation
  (`update_settings`) did not run without an answer: it was asked, or it ended for want of an answer; a refusal
  before any question (a denied permission, say) fails this check, because it tests nothing. Whether the client was
  shown the repair note in the result and in the refusal is read from the tool results in the client's JSON output.
- Exit codes: 0 all checks passed, 1 a check failed, 2 a setup problem (for example a Python without the
  framework's dependencies), 77 skipped because the claude command is missing or not signed in.
- The claude command signs in by itself: the script never reads, copies or passes Claude credentials or tokens.
  The client's cache, where it logs the server's stderr, and its temporary files (`TMPDIR`) are in the temporary
  folder too, which is removed at the end (`LCF_SMOKE_KEEP=1` keeps it); the claude command updates its own
  settings file as on every run. No Docker is needed. `LCF_SMOKE_CLAUDE` names another claude command,
  `LCF_SMOKE_TIMEOUT` the seconds per prompt (300).
- `tests/test_mcp_smoke_check.py` tests the checker with fixture databases, the isolation of the server's
  environment, the skip rules and the script end to end with a stand-in client.

---

## 4. The local model and the evaluation

Tests never download models, and they never fetch or build llama.cpp: a fixture in `tests/conftest.py` refuses a
network fetch of the llama.cpp source, so a test of the install uses fakes for the toolchain, the executor and the
verifier (see `tests/test_runtime_selfbuilt.py`). To try Ditto's offline mode by hand, use a separate
`LLM_FRAMEWORK_HOME` (models go to the `models/ditto/` folder of the installation, which is ignored by git):

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install      # builds llama-server from the pinned commit (a few minutes for the CPU build)
llm-cli ditto models download small
llm-cli ditto runtime start --model small
llm-cli ditto ask "Which formats can the Rockchip target build?"
llm-cli ditto runtime stop
```

If you change Ditto's prompts, a structured answer model or a tool description, run the evaluation before and
after the change and compare the reports:

```bash
llm-cli ditto eval --tier small --tier small-q2 --json before.json
llm-cli ditto eval --provider ollama --model qwen3.5:4b --kind chat
```

Cases live in `evals/ditto/cases.yml` and are graded by rules; nothing is built. Cloud providers bill every
request (about 20 per run and repeat). New cases are data: add them to the cases file, not to the code.

### Bumping the llama.cpp pin

Every self-built llama.cpp comes from one commit: `llama_cpp.source` in `configs/llama_runtime.yml` (where it lives
and what follows from it: [SSOT_ARCHITECTURE.md](configs/SSOT_ARCHITECTURE.md#llama_runtimeyml)). A commit fixes the
content; a tag can be moved. A bump goes like this:

1. Look up the commit of the new upstream tag: `python scripts/llama_cpp_pin.py show b<N>`.
2. In `configs/llama_runtime.yml` change `llama_cpp.source.commit`, `.tag` and `.build_number`, and
   `llama_cpp.build` (the same label). The official prebuilt archives of that release are labelled by `build`: pin
   their names, sizes and SHA256 values again, as the comment at the top of the file describes.
3. Change the places that cannot read the catalog: `ARG LLAMA_CPP_TAG` and `ARG LLAMA_CPP_COMMIT` in
   `targets/Rockchip/dockerfile`, `targets/_template/Dockerfile` and `targets/_template/Dockerfile.gpu` (the defaults
   for image builds outside the framework; the framework passes the pin itself), `docker.build_args.LLAMA_CPP_TAG`
   and `LLAMA_CPP_COMMIT` in `targets/*/target.yml`, and `inference_backends.llama_cpp.commit` in
   `configs/project_sources.yml` (then `python ../scripts/sync_ssot.py --write` inside `configs/`). The setting
   `llama_cpp_version` follows the catalog by itself.
4. Check that the commit is upstream's: `python scripts/llama_cpp_pin.py check`. It runs
   `git ls-remote https://github.com/ggml-org/llama.cpp.git refs/tags/b<N>`, which must print exactly the new commit
   (GitHub serves commits pushed to any fork through the upstream URL, so the commit alone does not prove its
   origin), and lists every place that names the version: derived and reference places must agree, the legacy
   `modules/source_module.sh` defaults are only reported.
5. Re-check the download-capable options against the new tree. `DOWNLOAD_OPTIONS_OFF` and `NEVER_SET` in
   `orchestrator/runtime/build/recipe.py` name each option with its file and lines; search the new tree for
   `FetchContent_Declare`, `ExternalProject_Add` and `file(DOWNLOAD` and add any new one. Check the CUDA architecture
   rules (`ggml/src/ggml-cuda/CMakeLists.txt`) and which targets `tools/CMakeLists.txt` builds as well. The target
   Dockerfiles repeat both lists in their llama.cpp build step (the options set last, and the ones they refuse);
   `tests/test_target_dockerfiles.py` fails until they match the recipe again.
6. Regenerate the golden plans and review the diff:
   `LLM_FRAMEWORK_UPDATE_GOLDEN=1 QT_QPA_PLATFORM=offscreen python -m pytest tests/test_build_recipe.py`. Without a
   recipe change only the commit, the build number and the build IDs change.
7. Run the whole test suite, add a changelog entry in both languages, and merge only when every check is green.

---

## 5. Lint and security checks

These are the checks from `.github/workflows/`. Except for hadolint, all tools come with
`requirements-dev.txt`.

**Ruff** (`ci.yml`): syntax errors and undefined names only. Rules: `E9` (syntax and I/O errors), `F63` (invalid
comparisons and asserts), `F7` (statements in the wrong place, such as `return` outside a function), `F82`
(undefined names).

```bash
ruff check --select E9,F63,F7,F82 orchestrator scripts tests targets
```

**ShellCheck** (`ci.yml`): errors in all tracked shell scripts. `targets/_template` is skipped because it contains
placeholders that the module generator fills in.

```bash
git ls-files -z '*.sh' ':!:targets/_template/*' | xargs -0 shellcheck --severity=error
```

**actionlint** (`ci.yml`): checks the workflow files.

```bash
actionlint
```

**Bandit** (`security.yml`): static security analysis. The CI fails on findings with high severity and high
confidence, and additionally prints a report from medium severity upwards for information.

```bash
bandit -r orchestrator scripts targets --severity-level high --confidence-level high
bandit -r orchestrator scripts targets --severity-level medium --exit-zero
```

**pip-audit** (`security.yml`): known vulnerabilities. Locally, check your environment and the dev tools:

```bash
pip-audit
pip-audit --requirement requirements-dev.txt
```

The CI also audits every pin in `requirements.txt`, including Windows-only and version-specific ones; the exact
script is in `security.yml`.

**Secret scan** (`security.yml`): every commit of the whole history, see [section 6](#6-secret-scan-and-git-hooks).

**hadolint** (`docker.yml`): lints `targets/Rockchip/dockerfile`, `targets/_template/Dockerfile` and
`targets/_template/Dockerfile.gpu` and fails on errors. Locally with an installed hadolint, or with Docker:

```bash
hadolint --failure-threshold error targets/Rockchip/dockerfile
docker run --rm -i hadolint/hadolint hadolint --failure-threshold error - < targets/Rockchip/dockerfile
```

**Image build** (`docker.yml`): when `targets/**`, `entrypoint.sh`, `orchestrator/utils/build_context.py` or
`orchestrator/Core/builder.py` change, the CI builds the Rockchip image and runs smoke tests (toolchains,
entrypoint, sandboxed container). To build the image locally from the same context the framework uses:

```bash
python3 -m orchestrator.utils.build_context targets/Rockchip /tmp/rockchip-context
docker build -t llm-framework/rockchip:dev /tmp/rockchip-context
```

GitHub Actions are pinned by commit SHA with the version as a comment. Keep it that way for new actions.

**Framework container** (`framework-container.yml`): when `Docker Setup/`, `install.sh`, `requirements.txt`, the
builder or the Makefile change, the CI builds the framework's image with `scripts/build_container_image.sh` (a local
tag, never pushed), lints its Dockerfile and runs smoke tests. `make build`, `make up`, `make down`, `make mojo` and
`make audit` work with it locally ([docs/Container_Setup.md](docs/Container_Setup.md)). No workflow, Makefile target,
script or document pushes or pulls a framework image (`tests/test_container_setup.py`). `make test` (uv),
`make hooks` and `make secret-scan` are unchanged.

---

## 6. Secret scan and git hooks

API keys, tokens, private keys and personal state must never be committed. `scripts/secret_scan.py` (standard
library only) finds them; git hooks and the CI run it for you.

**Enable the hooks once per clone:**

```bash
make hooks          # same as: git config core.hooksPath .githooks
```

- The **pre-commit** hook scans what the next commit would contain (`--staged`) and refuses the commit if it finds
  something.
- The **pre-push** hook scans the commits a push would publish (`--push`), so it also catches commits made with
  `git commit --no-verify`.
- If you use the pre-commit tool (https://pre-commit.com) instead: `pre-commit install -t pre-commit -t pre-push`
  (`.pre-commit-config.yaml`). Its pre-push stage runs the same `--push` scan; pre-commit hands over only the first
  branch of a push with several branches, so push them one at a time or use the git hooks above.
- The CI job `secret-scan` in `security.yml` scans every commit of the whole history.

**Run it by hand:**

```bash
python3 scripts/secret_scan.py               # tracked files of the working tree
python3 scripts/secret_scan.py --staged      # what the next commit would contain
python3 scripts/secret_scan.py --history     # every commit (same as: make secret-scan)
python3 scripts/secret_scan.py path/to/file  # only these files
```

Exit code 0 means clean, 1 findings, 2 a usage or git error. Findings are printed redacted.

**What it looks for:** keys and tokens of common providers (Anthropic, OpenAI, OpenRouter, Hugging Face, GitHub,
GitLab, AWS, Google, Groq, xAI and others), JSON web tokens, private keys, certificates and URLs with a password.
Files are refused by name whatever their content: for example `.env`, `credentials.json`, `secrets.store`,
`audit.db`, `known_hosts`, `id_ed25519`, `chat_history.json`, `tool_policy.json` and `*.pem`. Obvious placeholders
(`EXAMPLE`, `REDACTED`, `xxxxxxxx`, `<...>`, example domains) are ignored. A line that contains
`secret-scan: allow` is skipped; use it only for documented fake values. Neither applies to the exact token
formats below.

**Fake tokens in tests and documentation:** GitHub's push protection refuses a push that contains a value in a
provider's exact token format, however fake the value looks: `hf_` followed by 34 letters (the alphabet too),
`ghp_` followed by 36 letters and digits, a complete `sk-ant-api03-...AA` key, and so on. GitHub does not know the
marker `secret-scan: allow`. So the scan reports such values even when they look like placeholders, and the marker
does not silence these findings; the message says that GitHub will block the push. This holds in every mode:
`--push` also reports older commits that still contain such a value, because a push of those commits would be
refused as well, and so does `--history`, except for the listed test mocks below. It also holds for text files over
5 MB, which the other rules skip. The formats are `EXACT_FORMATS` in `orchestrator/security/secret_patterns.py`
(Hugging Face, GitHub, AWS, Slack, GitLab, Stripe, PyPI, OpenAI, Anthropic, OpenRouter, Groq, xAI, Perplexity,
Replicate). Build such a test value at runtime instead, so the file never contains it as one piece:

```python
TOKEN = "hf_" "abcdefghijklmnopqrstuvwxyzABCDEFGH"  # adjacent string literals: one string at runtime
TOKEN = "hf_" + "x" * 34                            # or concatenation
```

Or use a value that does not match the exact format: another length, or a character the format does not allow
(a digit in a Hugging Face token). In documentation, shorten the value (`hf_...`).

**Test mocks in old commits:** a commit that is already published cannot be changed without rewriting the history.
`.secret-scan-allow` at the repository root lists the documented test mocks that old commits still contain, so that
`--history` (the CI job, `make secret-scan`) passes. It holds two today, from old versions of
`tests/test_security.py` and `tests/test_cli_builds.py`. Each line names one exact value by the sha256 hex digest of
that value, never the value itself (that would be an exact-format literal that GitHub blocks), then two spaces and
a note: the first and last 4 characters, the length, why it is a mock and where it is. Compute the digest without
leaving the value in your shell history:

```bash
python3 -c "import getpass, hashlib; print(hashlib.sha256(getpass.getpass('value: ').encode()).hexdigest())"
```

The list applies to `--history` only, and only to the listed value itself: every other mode still reports it, so
the git hooks stop a new commit that contains it. A listed value hides nothing else: a longer value of another
format around it is still reported, and the other rules (provider keys, local patterns, file names) never drop a
finding because of the list, so a real-looking key is reported even if its digest is listed. They also read the line
without the listed value, so a value next to a mock cannot pass as a placeholder. A malformed line stops the scan in
every mode (exit code 2), so a typo cannot allow more; a control, format or line separator character (a lone carriage
return, a byte order mark, a zero-width space) makes a line malformed too, so a comment cannot hide an entry. Never
list a real credential, however old: rotate it and rewrite the history ([section 12](#12-if-a-secret-leaked)).

**Personal strings** such as your e-mail address, your user name or host names must not be in the repository
either, but they cannot be in a public pattern list. Put them into `.git/info/secret-patterns`, which git never
commits:

```text
# one literal per line (case-insensitive), or a regular expression after "re:"
my-user-name
my-laptop-hostname
re:\bmy\.name@[a-z.]+\b
```

In a git worktree, the file of the main repository's `.git/info/` is used. Alternatively, set
`SECRET_SCAN_PATTERNS_FILE` to a file somewhere else. Local patterns are never echoed in the output.

**If the scan finds something** in a commit you have not pushed yet: remove the value, keep it in the secrets store
or an environment variable instead, and amend the commit. If the value was a real key, treat it as leaked anyway
and follow [section 12](#12-if-a-secret-leaked).

---

## 7. Adding a hardware target

The full guide is the [Module Developer Guide](docs/Module_Developer_Guide.md). In short:

1. **Probe the board:** `llm-cli scan --device <address> --user <user>` runs the probe on the device over SSH (or
   run `scripts/hardware_probe.py` or `scripts/hardware_probe.sh` on the device by hand); it writes
   `target_hardware_config.txt`.
2. **Create the target:** in the GUI under **Tools > Create Hardware Module...**, with `llm-cli module create`, or
   with Ditto: `llm-cli module generate-ai target_hardware_config.txt`. The generator checks names, images,
   packages, flags and shell code, writes only below `targets/` and never overwrites an existing target, and the
   finished folder must pass the same validators as a community pack.
3. **Complete the capabilities:** the generator writes a `capabilities:` block with support level `community`, the
   module name as the only board and GGUF with the quantization types of the `case` branches in the quantization
   logic (without such branches, six common GGUF types). Add your real boards, formats, quantizations per board,
   aliases, defaults and calibration needs to `targets/<name>/target.yml` (schema in
   `orchestrator/schemas/targets.py`; the Rockchip block is the reference).
   GUI, CLI, Ditto and the MCP server offer and validate exactly what is declared there.
4. **Dockerfile and build script:** pin every version (tags, commits, hashes, like `RKLLM_WHEEL_SHA256` in the
   Rockchip Dockerfile). The conversion has to work in the sandbox: as a non-root user, without network, with a
   read-only image filesystem and only the build's own folders writable.
5. **Test with a real build**, for example
   `llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t <name> -f gguf -b <board> -F`, and add the
   Dockerfile to the hadolint matrix in `.github/workflows/docker.yml` and to `.github/dependabot.yml`.
6. **Share it** as a community pack (below), or, if the maintainers take the target over, with a pull request to
   this repository ([section 9](#9-commits-and-pull-requests)).

### Contributing to the community zoo

Targets you maintain yourself, recipes (finished builds that others can rebuild) and knowledge are shared as signed
community packs through the community repository, not as folders copied into `community/` by hand. The commands
and options are in the [CLI guide](docs/CLI_Usage.md#zoo-export); for targets, see also the
[Module Developer Guide](docs/Module_Developer_Guide.md#9-sharing-a-target).

1. **Export a pack:** `llm-cli zoo export target <id> --author "<name>"` for a target,
   `llm-cli zoo export recipe <build folder> --author "<name>"` for a finished build, or
   `llm-cli zoo export knowledge --author "<name>"` for your knowledge base. In the GUI: "Export for community after
   creating" on the last page of the module wizard, or "Export a finished build for the community..." in the
   Builder log's context menu. The pack is privacy-scrubbed, checked and signed with your key, and written to
   `community/contributions/`. Nothing is sent yet.
2. **Review it:** the summary shows a diff for every file the privacy scrub changed (at most 40 lines per file).
   Then check the pack the way the community repository will:
   `llm-cli zoo verify community/contributions/<file>.zip`.
3. **Open the pull request:** add `--pr` to the export command (it asks after the summary unless `--yes` is given),
   or click "Open pull request" in the export dialog. With the GitHub CLI and git installed and `gh` logged in, the
   framework forks the repository named in the setting `community_repo`, commits the pack to `contributions/<kind>/`
   on a new branch and opens the pull request. Otherwise, or if that fails, the repository's upload page opens in
   your browser, and you drop the file there.
4. **The CI check:** on every pull request that touches `contributions/`, the community repository's workflow
   accepts only pack files (`contributions/target/*.zip`, `contributions/recipe/*.zip`,
   `contributions/knowledge/*.zip`) of at most 5 MB, runs `llm-cli zoo verify` on each of them (manifest and hashes,
   no links, extra or binary files, the target validators, the recipe and knowledge formats, an included author
   signature, the privacy scrub). The privacy scrub also covers the framework's table of secret formats, so a pack
   with a key in any of its files is rejected; the repository's secret scan is not run on the zips, because it skips
   binary files and would check nothing inside a pack.
5. **Review and index:** a maintainer reviews the pack, merges it, adds its entry to the signed index and signs the
   index offline. Then it appears in `llm-cli zoo list` and in the Community Hub.

The framework never asks for, reads or shows a token: `gh` and `git` use their own stored sign-in, and the upload
page uses your browser's GitHub session.

**Current state:** the community repository and its signed index are not published yet. `community_repo` and
`community_index_url` are empty and `configs/community_keys.yml` lists no key, so `--pr` stops with "No community
repository is configured" and leaves the pack in `community/contributions/`. Until the repository exists, a pack
can be shared as a file (`llm-cli zoo install --file <pack>` installs it with trust `local`).

Knowledge packs keep the source address of every page and claim no licence for the pages (`NOASSERTION`); the pages
stay under their owners' terms. Pages carried in full under a licence that asks for its text (MIT, BSD, ISC,
Apache-2.0, MPL-2.0, CC BY, CC BY-SA) come with the canonical licence text in `LICENSES/` and a `NOTICE`; the check
(`llm-cli zoo verify <file>`) refuses a pack whose licence files do not match its pages. A target that should ship
with the framework (support level `official`) is still contributed with a normal pull request to this repository.

### Maintaining the community repository

Two files in this repository are tools for the maintainers of the community repository; the framework itself does
not use them.

- `scripts/community_repo/validate-contribution.yml` is the CI workflow template of the community repository. Copy
  it to `.github/workflows/` there (this repository does not run it) and set `LCF_REF` to the framework release the
  packs are checked with: the template holds a placeholder, so the job fails until a published release tag is set.
  It runs the checks of step 4 above with the framework at that tag. The signing key never reaches the CI.
- `scripts/zoo_sign_index.py` signs the index. Its signing key lives only in the operating system's keyring
  (service `lcf-community-index`):

  ```bash
  python scripts/zoo_sign_index.py keygen <key name>
  python scripts/zoo_sign_index.py entry <pack>.zip --url https://<address of the pack> [--trust community|verified] [--title "<title>"]
  python scripts/zoo_sign_index.py sign index.json --key <key name> [--previous <old index.json>]
  ```

  `keygen` creates a key (it refuses a name that exists) and prints its entry for `configs/community_keys.yml`,
  valid for two years; ship that entry with a framework release before the first index the key signs. `entry`
  checks a reviewed pack like `llm-cli zoo verify` and prints its index entry (kind, ID, version, title, author,
  licence, trust, URL, size and sha256; `--trust` defaults to `community`). `sign` checks `index.json` against the
  schema (with `--previous`, its `version` must be higher) and writes `index.json.sig` next to it.

`index.json` holds `schema_version` (1), `version` (a number that grows with every signed index), `generated` (UTC
time in the form `2026-10-11T12:00:00Z`), `entries` (the output of `entry`) and `revoked`. The signature covers the
exact bytes, so publish `index.json` unchanged. Clients refuse an index whose version is lower than the one they
have cached. To revoke a pack, add an entry to `revoked`, raise `version` and sign again. Each entry needs `kind`,
`id` and a `reason`; with `sha256` it revokes exactly that pack file, without it the `version` given or, without a
`version`, every version of that ID.

---

## 8. Code guidelines

- Use type hints for new functions.
- Log with `orchestrator.utils.logging.get_logger`, not `print()`. CLI output goes through `rich`; escape external
  text with `rich.markup.escape` before printing it.
- Start subprocesses with argument lists, never with shell strings that contain user input. Quote values for remote
  shell commands with `shlex.quote`.
- External text (web pages, logs, probe files, knowledge base, tool results) reaches a language model only through
  `orchestrator/utils/untrusted.py`, and never in a system prompt.
- **Imports point down the layers.** Every module of `orchestrator/` has a layer, and it imports only its own layer or
  a lower one: (1) bottom: `utils/`, `security/`, `schemas/`; (2) remote: `remote/` and `utils/ssh_trust.py`; (3)
  kernel: the modules of `tools/` that define no tools (registry, policy, audit, approvals, signing, spec, ...); (4)
  domain: `Core/`, `runtime/`, `rag/`, `llm/`, `evals/`, where `llm` and `rag` never import `Core`; (5) tool
  definitions: `tools/tools_*`, `framework_*`, `gui_bridge`, `gui_surface`; (6) adapters: `cli.py`, `mcp_server.py`,
  `gui/` with `main.py`, and no adapter imports another. PySide6 lives only in the GUI, click and rich only in the CLI,
  mcp and uvicorn only in the MCP server, paramiko only in the SSH transport (`utils/ssh_trust.py`, later
  `remote/transport.py`). `tests/test_layering.py` reads every import (also inside functions, under `TYPE_CHECKING` and
  module names handed to `import_module`, `lazy_attributes` or `LazyModule`). What breaks a rule today stands in
  `tests/data/layering_allowlist.json`, each entry with its reason and the chain that removes it; the test fails on a
  new violation and on an entry that no longer happens (remove it), and the largest import cycle may only shrink (lower
  `largest_cycle` when it does). `python tests/test_layering.py` prints today's lists. Shared code a lower layer needs
  moves down instead of being imported upward; the old place keeps the name as a re-export.
- **Shared helpers have one home below the tools.** Paths in text an AI reads: `orchestrator/security/paths.py`
  (`label_paths`, `shorten_home`, `device_text`, `scrub_for_ai`, and the home-folder patterns `HOME_ROOT` and
  `ACCOUNT_NAME` that other scrubbers build on). Private atomic files: `orchestrator/utils/atomic.py`
  (`replace_file` with the Windows retry, `write_private`, `write_private_exclusive`). One-time notices:
  `orchestrator/utils/notice_store.py` (`OneTimeNotice`). Docker image references:
  `orchestrator/utils/image_ref.py`. HTTP that reaches only public addresses: `orchestrator/security/http.py`
  (`PublicOnlyAdapter`). The audit key: `Core/vault_key.audit_key`; a pack's base images:
  `Core/community_pack.py`; the snapshot check before an index: `Core/knowledge_snapshots.snapshot_space`; the
  audit of a saved module: `Core/module_generator.record_module_saved`.
- **AI calls go through the provider layer.** Use `LLMClient` from `orchestrator/llm`; never build an SDK client of
  your own. Answers the framework acts on are pydantic models (structured answers), never parsed free text. New
  providers or models are presets in `configs/llm_providers.yml`.
- **Local runtime downloads are pinned.** A new llama.cpp build or model in `configs/llama_runtime.yml` needs size
  and SHA256 from a complete download, and models a Hugging Face commit as revision. Never pass options to
  `llama-server` that give it tools, an agent mode, MCP clients or a web UI: every launcher takes the forbidden
  options and the environment rules from `orchestrator/runtime/server_policy.py`, and a llama.cpp program is only
  ever started from a verified build folder, never found on `PATH`.
- **The hardware probe exists twice.** `scripts/hardware_probe.py` is the standalone file that is sent to devices
  and run by hand; `orchestrator/runtime/hardware_probe.py` is its byte-identical copy inside the package. Edit the
  script, then copy it (`cp scripts/hardware_probe.py orchestrator/runtime/hardware_probe.py`); a test fails while
  the two differ.
- **One file hash, one disk watchdog.** Every sha256 of a file goes through `orchestrator/utils/helpers.py`, where
  the first one (`calculate_file_checksum`) was: `sha256_file` (optional cancel and progress callbacks, links refused
  on request), `try_sha256_file` (None for a link, a folder or an unreadable file), `sha256_fileobj` (an open handle,
  or its first N bytes for a resume), `sha256_fd`, `copy_hashed` (copies exactly the bytes it hashes, never through a
  link), `hash_stream`, `digest_matches` (constant-time, case-insensitive), `HashCache`, `write_sha256sums` and the
  `.sha256` sidecar. Old names elsewhere are delegates; `tests/test_hashing.py` fails on a new hash loop beside it.
  A disk watchdog, a low-disk text or a disk-full text comes from `orchestrator/Core/disk_plan.py`
  (`low_disk_text`, `disk_full_text`, `is_disk_full`, `DiskWatchdog`, `free_space`, `nearest_existing`); such a text
  names folders by label and keeps `DISK_FULL_PHRASE`, which the self-healing heuristic matches.
- **Every CLI action has a mouse path.** Many people never use a terminal, so a person can start every command and
  option of `llm-cli` in the GUI as well, without typing a command (typing a value into a field is fine). A new
  command or option needs its mouse path, a menu entry, a button or a field that calls the same core function as the
  CLI, and an entry in `CLI_PATHS` of its area module in `orchestrator/gui/menus/` (the command path, e.g.
  `"deploy devices remove"` or `"mojo --board"`, to the objectName of the QAction or widget; `WINDOW_OBJECTS` or
  `TAB_OBJECTS` names the window or tab it lives in). A switch that is for scripts only (machine output such as
  `--json`, a `--yes` that skips a question the GUI always asks, a compatibility alias) goes into `MACHINE_ONLY`
  with its reason instead. Otherwise `tests/test_cli_gui_parity.py` fails: it walks the whole command tree, opens
  every window from its menu entry offscreen and checks that every named object exists and every action can be
  reached from the menu bar. Menus are found and placed through `orchestrator/gui/menus/place.py`.
- New actions for AI clients and Ditto are tools in `orchestrator/tools/framework_tools.py` with the correct tier
  (`read`, `local_write`, `external`). Never bypass the registry, the permissions or the audit log.
- The framework never asks for, reads or stores a repository token. Hand work over to GitHub through an installed
  GitHub CLI (it keeps its own sign-in) or the browser, as `llm-cli zoo export --pr` does.
- Code adapted from another project keeps a comment that names its source ("Adapted from ...") and gets an entry
  with that project's licence in `THIRD_PARTY_NOTICES.md`.
- Do not weaken the build sandbox, the privacy filter or the SSH trust rules.
- **Code that talks to a board uses `orchestrator/remote/`.** Check a board's address, port or SSH user with
  `remote.address` (`valid_host`, `valid_port`, `valid_user`; the forms come from `Endpoint`), turn a failed contact
  into its reason code with `remote.reasons.reach_code` (`while_waiting=True` inside a wait for the board), record what
  a contact found with `record_reach` (`orchestrator/runtime/device/trusted.py`), derive a board step's error from
  `remote.errors.RemoteFailure`, show a deploy's progress with `remote.text.event_text` and ask
  `remote.transport.available()` whether SSH works; never import paramiko at module level. Never write a second
  regex, reason table or recorder next to them. The package imports only `orchestrator/utils` and
  `orchestrator/security` (`tests/test_remote_layer.py`).
- **Words the person reads.** Every visible text is English and German through `ui_text` (a `TEXTS` table in an
  `orchestrator/utils/l10n_*.py` module named in `TEXT_MODULES`). German says "du"; a module adds `FORMAL = {"de":
  {key: text}}` with the "Sie" form of exactly the texts that address the person (same `{placeholders}`, checked when
  merged). Technical terms stay English; mark them with the glossary of `orchestrator/utils/l10n_glossary.py` (a new
  term: an entry in `TERMS` with an https link, and `term.<id>` in English and German): `explain(widget, "kv_cache")`,
  `term_label("Quantization:", "quantization", buddy=field)`, `help_button(term)` or `mark_terms_in(window)` from
  `orchestrator/gui/explain.py`. A marked label needs a buddy (F1 in its field opens the card) or a `help_button()`;
  `accessibility_problems(window)` names one that no key reaches. `find_terms` reads "KV-Cache" like "KV cache", but
  a German word for a term ("Wissensbasis") is not found: mark it by id. `mark_terms` skips the words of a filled
  `{path:...}` (the menu titles on the way to a place are not terms of the text). Errors for the person go through
  `show_error()` / `show_exception()` (`orchestrator/gui/error_dialog.py`, also `plain.plain_error()`, GUI thread
  only): a localized summary, one next step (a sentence or chain a0's catalogue steps), the English details folded
  away, every text of the box redacted. A0's `error_report(e, lang).dialog_kwargs()` fills it; sentences stay plain
  "Next step:" lines and catalogue steps get a `StepsRow` with [Open]; an empty list shows `EmptyState` (`orchestrator/gui/empty_state.py`); sizes come from
  `orchestrator/utils/sizes.py`; a label with a keyboard mnemonic and inserted values uses `plain.mnemonic_text()`;
  `orchestrator/gui/accessible.py` pairs labels with fields and names icon-only buttons. Plain text everywhere: no
  label, box or tooltip ever draws HTML from data.

### Next steps instead of commands

The window never tells a person to type a command (DECISIONS round 9 U2). A message says what is wrong; what to do is
a **next step** of the catalogue in `orchestrator/utils/next_steps/`, never a command or a hand-written menu path in a
text:

- **A new hint** is a `Step` in the area module of its part (`runtime.py`, `boards.py`, `knowledge.py`, ...; a new
  module is found by itself): its id (`runtime.install`), the CLI command (`cli`, a `CLI_PATHS` key, and the argument
  template `cli_args` with checked `params`), the tool an AI calls or why only the person can do it (`person_only`),
  the object the window's [Open] goes to (`target`, which must be `CLI_PATHS[cli]`), and its texts in English and
  German (`step.<id>`; `step.<id>.what` and `.why` for a command the person may allow). An object the step names needs
  its place in `MOUSE_LABELS` (its parent object and its label's ui_text key, from an `l10n_*` module); a menu entry
  gets it from `MENU_LAYOUT` in `common.py`. Step texts say "du" like every German text; a German text that differs
  in the "Sie" form gets it in the module's `FORMAL = {"de": {...}}` (same placeholders, its own du text; the
  catalogue reads it while the window's form of address is "Sie"). The catalogue never imports PySide6 or
  `localization.py` itself: an MCP
  tool call renders its steps in a worker thread, and the first Qt import there breaks a window made later in the
  same process; without the window it reads the `l10n_*` tables directly.
- **Carry the step, not the words:** raise a core exception with `.with_steps("runtime.install")` (the `WithSteps`
  mixin), give a `ToolError` `steps=[...]`, a result line `Line(text, steps)`, a Mojo finding `steps=(...)`. The
  renderer writes the command for the CLI (`_say_error` in `cli.py`), the menu path for the window (`StepsRow`) and the
  tool or the place for an AI (`ToolContext.channel`). A blocked row of the AI activity carries only step ids the
  catalogue knows (`refs.steps`); a `person_action` key of `facts_catalog` is not a step.
- **A text that names a place** writes `{path:<objectName>}`; `ui_text` fills it from the live labels in the text's
  language.
- **A command the person may allow** (V1) is built only with `next_steps.proposal(step, values)` and run with
  `run_proposal()`: never from free AI text; a deletion opens the deletion list, a secret goes through stdin. Every
  value of every proposal is a closed one (a choice, a label, an id, a number; a label best names its existing
  `choices`), a secret or a path is never a value, and the run refuses a proposal that differs from the catalogue's
  in any word the person read. Mark a step `runnable` only when its command finishes without a terminal and without a
  question, writes no file into the folder it starts in, and does no more than its `.what` text says; never a
  deletion, a licence or a board's, an administrator's or another program's command (the tests check this against
  the real CLI). A board's address or login account is a `Param(..., private=True)`: an AI never reads it, nor a
  path.
- `tests/test_no_command_texts.py` fails on a command or a hand-written path in a person's text (its "Sie" form in
  a module's `FORMAL` included) and on more commands or paths in any other literal than
  `tests/data/command_text_baseline.json` allows (only down; the integration tightens it, a change never raises it).
  `tests/test_cli_gui_parity.py` checks every step against the CLI, the tools and the windows and every mouse label
  against the live menus and buttons.

---

## 9. Commits and pull requests

- Branches: ongoing work happens on `development`; `main` holds the stable state and takes `development` in by a
  pull request once that is stable (CI green on Linux and Windows, reviewed). Branch from `development` and aim
  your pull request at it; one topic per pull request. CI runs on every push to `main` and `development` and on
  every pull request.
- Commit messages: a summary line in English, in the imperative, sentence case, no type prefix, ideally no longer
  than about 72 characters. Examples from the history: `Add a secret scan: pre-commit and pre-push hooks and a CI
  job` and `Pin the provider SDKs and local embeddings, drop LiteLLM and tiktoken`. The body explains why.
- Stage files with explicit paths (`git add path/to/file`), not with `git add -A` or `git add .`, so that no local
  state slips in.
- Code, tests, documentation (English and German) and the changelog entry belong in the same pull request.
- Describe in the pull request how you tested, for example which board and which model.
- Never commit secrets or personal state: `configs/config.yml`, `secrets.store`, API keys, tokens, or files from
  `~/.llm-framework`.
- No binaries: no `.whl`, `.so` or model files. Download them in the Dockerfile and check their hashes.

---

## 10. Changelog

- Format: [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); versions follow
  [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
- **The changelog says what was done.** Every change users notice gets an entry under `## [Unreleased]` in
  `CHANGELOG.md`, and the same entry in German in `CHANGELOG_DE.md`. Use the sections Added, Changed, Deprecated,
  Removed, Fixed and Security, and the matching sections in the German changelog.
- Write for users: what changed and what it means for them.
- **The roadmap says what comes.** Planned or unfinished work belongs in the roadmap
  ([docs/BLUEPRINT.md](docs/BLUEPRINT.md#roadmap)), not in the changelog. When you finish a roadmap item, set its
  status to "done" in both language versions of the blueprint and add the changelog entry.
- For a release, the maintainers turn `[Unreleased]` into `## [x.y.z] - YYYY-MM-DD`, set the same version in
  `orchestrator/__init__.py` (`__version__`) and `pyproject.toml`, and remove the released items from the roadmap.

---

## 11. Documentation

- English is the primary language, German the second. **Every document exists twice:** `<name>.md` (English) and
  `<name>_DE.md` (German), with the same structure and content.
- Write natural German with correct umlauts (ä, ö, ü, ß) and typographic quotes („…“), not a word-by-word
  translation.
- Both files link to each other at the top. Every document starts with a line like
  `Version 3.0.0 · Last updated 2026-10-10` (German: `Version 3.0.0 · Stand 2026-10-10`); update it when you change
  the document. If it also describes unreleased work, say so: `Version 3.0.0 + unreleased changes`.
- Describe only what exists on the current branch; planned features go to the roadmap. Check every command,
  option, path and configuration key against the code or the `--help` output before you write it down.
- Write for newcomers first, then add the details. No marketing language.
- Never put real keys, e-mail addresses or other personal data into examples; use placeholders such as
  `<your key>`.
- Change both language versions in the same pull request.

---

## 12. If a secret leaked

A key that reached a commit, a pull request, an issue, a log you shared or a public fork is leaked, even if you
remove it a minute later. Act in this order:

1. **Revoke or rotate it at the provider first.** This is the only step that makes the key useless.
   - Anthropic: https://platform.claude.com/settings/keys
   - OpenAI: https://platform.openai.com/settings/organization/api-keys
   - OpenRouter: https://openrouter.ai/keys
   - Google (Gemini API keys): https://aistudio.google.com/apikey
   - Hugging Face: https://huggingface.co/settings/tokens (anyone can also invalidate a leaked Hugging Face token,
     see https://huggingface.co/docs/hub/security-tokens)
   - GitHub tokens: https://github.com/settings/tokens
2. **Store the new key only in the secrets store** (`llm-cli ditto login <provider>` for AI provider keys,
   `llm-cli secrets set <name>` for other tokens, or the AI settings in the GUI), never in a file inside the
   repository.
3. **Check what the key was used for** in the provider's usage or billing view, and report misuse to the provider.
4. **Remove the value from the repository** with a normal commit. Rewriting the history is optional and comes
   second: a pushed secret stays in clones, forks and caches, so a rewrite never makes a key safe again.
5. **Tell the maintainers privately** through a security advisory (see [section 13](#13-reporting-security-problems))
   if the key reached the public repository, and add a local pattern ([section 6](#6-secret-scan-and-git-hooks)) if
   the scan missed its format.

Maintainers: GitHub's secret scanning runs on public repositories; also turn on push protection for the
repository (Settings > Advanced Security,
https://docs.github.com/en/code-security/secret-scanning/enabling-secret-scanning-features/enabling-push-protection-for-your-repository).

---

## 13. Reporting security problems

Please do not report vulnerabilities in a public issue. Open a private security advisory on GitHub instead.

Be respectful. We prefer stable and secure over fast and broken.
