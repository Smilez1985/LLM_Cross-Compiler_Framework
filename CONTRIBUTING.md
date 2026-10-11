Version 3.0.0 + unreleased changes · Last updated 2026-10-11

**English** · [Deutsch](CONTRIBUTING_DE.md)

# Contributing to the LLM Cross-Compiler Framework

Thank you for helping. This guide explains how to set up a development environment, run the same checks as the
CI, keep credentials out of the repository, and submit changes. If you only want to use the framework, start with
the [README](README.md). How the framework is built is described in the [blueprint](docs/BLUEPRINT.md).

Ways to contribute:

- **Report problems:** describe what you did and what happened, and attach the output of `llm-cli doctor --json`
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
- [ ] Entry under `[Unreleased]` in `CHANGELOG.md` and `CHANGELOG_DE.md`
- [ ] Documentation updated in English and German
- [ ] No secrets, personal state, binaries or model files in the diff

---

## 1. Development setup

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

### Option B: Poetry 2

```bash
poetry install                         # runtime and dev dependencies plus the llm-cli and llm-gui commands
poetry run llm-cli --help
poetry run llm-gui
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

- `pyproject.toml` declares the allowed versions, `poetry.lock` pins them (Poetry 2 lock file).
- `requirements.txt` (runtime) and `requirements-dev.txt` (dev tools) are exports of the lock file with hashes.
  The installers and the CI install from them. **Never edit them by hand.**
- `anthropic`, `openai`, `fastembed` and `mcp` are pinned to exact versions on purpose: LLM tooling is a
  supply-chain target (see the comments in `pyproject.toml`). If you change such a pin, explain why in the pull
  request.

After adding, removing or updating a dependency:

```bash
poetry lock                                                    # update poetry.lock
poetry check --lock                                            # pyproject.toml and poetry.lock agree
poetry export -f requirements.txt -o requirements.txt          # runtime packages
poetry export -f requirements.txt --only dev -o requirements-dev.txt
```

Poetry 2 needs the export plugin for this: `poetry self add poetry-plugin-export`. `poetry check --lock` may print
warnings about deprecated `[tool.poetry]` fields; what matters is the exit code. Then run pip-audit
([section 5](#5-lint-and-security-checks)) and the tests, and commit `pyproject.toml`, `poetry.lock` and both
requirements files together.

Dependabot opens weekly pull requests for Python packages, GitHub Actions and the target Dockerfiles. For Python
updates, check that both requirements files still match `poetry.lock`; export them again if not.

---

## 3. Running the tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest      # with Poetry: QT_QPA_PLATFORM=offscreen poetry run pytest
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

---

## 4. The local model and the evaluation

Tests never download models. To try Ditto's offline mode by hand, use a separate `LLM_FRAMEWORK_HOME` (models go
to the `models/ditto/` folder of the installation, which is ignored by git):

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install
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

The Makefile targets `make build`, `make up`, `make gui`, `make test-container` and `make audit` do not work at the
moment: they use `Docker Setup/docker-compose.yml`, which builds from an `orchestrator/Dockerfile` that does not
exist, and `build`, `up` and `gui` also call the missing `scripts/setup_linux.sh`. A hardened headless image is on
the [roadmap](docs/BLUEPRINT.md#roadmap). `make test` (Poetry), `make hooks` and `make secret-scan` work.

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
`--push` and `--history` also report older commits that still contain such a value, because a push of those commits
would be refused as well. It also holds for text files over 5 MB, which the other rules skip. The formats are `EXACT_FORMATS` in `orchestrator/security/secret_patterns.py` (Hugging
Face, GitHub, AWS, Slack, GitLab, Stripe, PyPI, OpenAI, Anthropic, OpenRouter, Groq, xAI, Perplexity, Replicate).
Build such a test value at runtime instead, so the file never contains it as one piece:

```python
TOKEN = "hf_" "abcdefghijklmnopqrstuvwxyzABCDEFGH"  # adjacent string literals: one string at runtime
TOKEN = "hf_" + "x" * 34                            # or concatenation
```

Or use a value that does not match the exact format: another length, or a character the format does not allow
(a digit in a Hugging Face token). In documentation, shorten the value (`hf_...`).

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

1. **Probe the board:** run `scripts/hardware_probe.sh` on the device; it writes `target_hardware_config.txt`.
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
stay under their owners' terms. A target that should ship with the framework (support level `official`) is still
contributed with a normal pull request to this repository.

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
- **AI calls go through the provider layer.** Use `LLMClient` from `orchestrator/llm`; never build an SDK client of
  your own. Answers the framework acts on are pydantic models (structured answers), never parsed free text. New
  providers or models are presets in `configs/llm_providers.yml`.
- **Local runtime downloads are pinned.** A new llama.cpp build or model in `configs/llama_runtime.yml` needs size
  and SHA256 from a complete download, and models a Hugging Face commit as revision. Never pass options to
  `llama-server` that give it tools, an agent mode, MCP clients or a web UI.
- New actions for AI clients and Ditto are tools in `orchestrator/tools/framework_tools.py` with the correct tier
  (`read`, `local_write`, `external`). Never bypass the registry, the permissions or the audit log.
- The framework never asks for, reads or stores a repository token. Hand work over to GitHub through an installed
  GitHub CLI (it keeps its own sign-in) or the browser, as `llm-cli zoo export --pr` does.
- Code adapted from another project keeps a comment that names its source ("Adapted from ...") and gets an entry
  with that project's licence in `THIRD_PARTY_NOTICES.md`.
- Do not weaken the build sandbox, the privacy filter or the SSH trust rules.

---

## 9. Commits and pull requests

- Branch from `main`; one topic per pull request.
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
