"""The framework in a container that a person builds on their own computer (DECISIONS round 5 h; task 25).

- No framework image is published or pulled: no workflow, Makefile target, script, compose file or document pushes an
  image or pulls a framework image; the compose file never pulls (pull_policy: never), the CI only builds.
- scripts/build_container_image.sh checks Docker, stages exactly the tracked files (an untracked secret never reaches
  the build), builds with a local tag (a registry name is refused) and prints how to run it.
- "Docker Setup/Dockerfile": base image pinned by digest, the same installation as on bare metal (install.sh), a
  non-root numeric user, the sources only as a read-only build mount, no secret in any layer, headless by default.
- install.sh in a container build leaves out the Docker engine and the docker group, runs as the user with runuser
  where sudo is missing, and its listing without git keeps nested build/ folders (orchestrator/runtime/build/).
- `make clean` deletes no data; the Makefile uses Docker Compose v2.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT

SETUP = REPO_ROOT / "Docker Setup"
DOCKERFILE = SETUP / "Dockerfile"
COMPOSE = SETUP / "docker-compose.yml"
BUILDER = REPO_ROOT / "scripts" / "build_container_image.sh"
INSTALL_SH = REPO_ROOT / "install.sh"
MAKEFILE = REPO_ROOT / "Makefile"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="needs bash")
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

# Names of the framework's own images (built locally, never published).
FRAMEWORK_IMAGE = r"(llm-framework|lcf-framework|smilez1985|orchestrator)"


def _logical_lines(text: str):
    """Dockerfile/Makefile lines with backslash continuations joined, comments dropped."""
    joined, current = [], ""
    for line in text.splitlines():
        if line.lstrip().startswith("#") and not current:
            continue
        if line.rstrip().endswith("\\"):
            current += line.rstrip()[:-1] + " "
            continue
        joined.append(current + line)
        current = ""
    return joined


# --- no published or pulled framework image ------------------------------------------------------------------------

PUSH = re.compile(r"\b(docker|podman|nerdctl)\s+(image\s+)?push\b|\bbuildx\b[^\n]*--push\b|\bcompose\b[^\n]*\bpush\b"
                  r"|\bskopeo\s+copy\b|\bcrane\s+push\b|\bdocker/login-action\b|\bdocker\s+login\b", re.I)
PULL = re.compile(r"\b(docker|podman|nerdctl)\s+(image\s+)?pull\b[^\n]*" + FRAMEWORK_IMAGE
                  + r"|\bimages\.pull\([^)]*" + FRAMEWORK_IMAGE, re.I)


def _checked_files():
    """Workflows, the Makefile, scripts, "Docker Setup/" and every document: tracked ones, and new ones on disk."""
    tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True)
    names = tracked.stdout.splitlines() if tracked.returncode == 0 else []
    on_disk = [p for pattern in (".github/workflows/*.yml", "scripts/**/*", "Docker Setup/*", "docs/*.md", "*.md",
                                 "Makefile") for p in REPO_ROOT.glob(pattern) if p.is_file()]
    wanted = []
    for rel in names + [p.relative_to(REPO_ROOT).as_posix() for p in on_disk]:
        if rel.startswith("tests/"):
            continue
        if (rel.startswith(".github/workflows/") or rel == "Makefile" or rel.startswith("scripts/")
                or rel.startswith("Docker Setup/") or rel.endswith(".md") or rel.endswith(".sh")):
            wanted.append(rel)
    return sorted(set(wanted))


def test_no_workflow_makefile_script_or_doc_pushes_or_pulls_a_framework_image():
    files = _checked_files()
    assert ".github/workflows/framework-container.yml" in files and "Makefile" in files
    found = []
    for rel in files:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if PUSH.search(line) or PULL.search(line):
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not found, "a framework image is pushed or pulled:\n  " + "\n  ".join(found)


def test_every_workflow_build_has_push_false():
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in (data.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = str(step.get("uses", ""))
                if "build-push-action" in uses:
                    assert (step.get("with") or {}).get("push") is False, f"{workflow.name}: push must be false"


def test_the_guard_finds_a_push_and_a_framework_pull():
    for line in ["docker push lcf-framework:local", "docker buildx build --push -t x .", "docker image push x",
                 "docker compose push", "uses: docker/login-action@v3", "docker pull ghcr.io/smilez1985/llm-runtime",
                 "podman pull llm-framework/orchestrator:1.3.0"]:
        assert PUSH.search(line) or PULL.search(line), line
    for line in ["python scripts/secret_scan.py --push", "docker pull debian:13-slim", "git push origin main",
                 "push: false"]:
        assert not (PUSH.search(line) or PULL.search(line)), line


# --- the Dockerfile --------------------------------------------------------------------------------------------------

def test_the_dockerfile_is_pinned_runs_install_sh_as_non_root_and_keeps_secrets_out():
    text = DOCKERFILE.read_text(encoding="utf-8")
    lines = _logical_lines(text)
    froms = [line for line in lines if re.match(r"^\s*FROM\s", line, re.I)]
    assert froms and all(re.search(r"@sha256:[0-9a-f]{64}\b", line) for line in froms), "base pinned by digest"
    assert not [line for line in lines if re.match(r"^\s*(COPY|ADD)\s", line, re.I)], \
        "the sources are never copied into a layer"
    install = [line for line in lines if "install.sh" in line and line.lstrip().upper().startswith("RUN")]
    assert len(install) == 1
    run = install[0]
    assert "--mount=type=bind" in run and ",rw" not in run, "a read-only bind mount of the staged context"
    assert "SUDO_USER=lcf" in run and "LCF_CONTAINER_BUILD=1" in run and "bash /src/install.sh" in run
    assert run.index(".lcf-container-context") < run.index("install.sh"), "the marker is checked first"
    users = [line.split(None, 1)[1].strip() for line in lines if re.match(r"^\s*USER\s", line, re.I)]
    assert users and users[-1] == "${LCF_UID}:${LCF_GID}", "the last USER is the numeric non-root user"
    assert re.search(r"^ARG LCF_UID=1000$", text, re.M) and re.search(r"^ARG LCF_GID=1000$", text, re.M)
    assert re.search(r'"\$LCF_UID" = "0"', text), "a uid of 0 is refused"
    names = re.findall(r"^\s*(?:ARG|ENV)\s+([^\n]+)", text, re.M)
    assert not [n for n in names if re.search(r"TOKEN|PASSWORD|SECRET|API_?KEY|CREDENTIAL", n, re.I)], \
        "no secret in a build argument or environment variable"
    assert "get.docker.com" not in text and not re.search(r"\bdocker(-ce|\.io)\b", text), "no Docker engine"


def test_the_image_is_headless_by_default_and_the_gui_is_an_explicit_option():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG WITH_GUI=0$", text, re.M)
    gui = re.search(r'if \[ "\$WITH_GUI" = "1" \]; then(.*?)\n\s*fi\b', text, re.S)
    assert gui and "libxcb" in gui.group(1) and "libgl1" in gui.group(1)
    outside = text.replace(gui.group(0), "")
    assert "libxcb" not in outside and "libgl1" not in outside, "desktop libraries only with WITH_GUI=1"
    cmd = re.findall(r"^CMD\s+(.+)$", text, re.M)
    assert cmd == ['["llm-cli", "--help"]'], "the default command is the CLI, never the GUI"
    assert re.search(r'^VOLUME \["/data"\]$', text, re.M) and "LLM_FRAMEWORK_HOME=/data/home" in text


def test_the_build_context_ignore_file_keeps_personal_folders_out_but_no_tracked_file():
    patterns = [line.strip() for line in (SETUP / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
    for needed in (".git", "configs/config.yml", "output", "models", ".env"):
        assert needed in patterns
    tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True)
    if tracked.returncode != 0:
        pytest.skip("no git checkout")
    import fnmatch
    hit = [f for f in tracked.stdout.splitlines() for p in patterns
           if fnmatch.fnmatch(f, p) or f.startswith(p.rstrip("/") + "/") or fnmatch.fnmatch(f, "*/" + p.lstrip("*/"))]
    assert not hit, f"tracked files would be left out of the image: {hit[:5]}"


# --- the compose file and the Makefile -------------------------------------------------------------------------------

def test_compose_never_pulls_publishes_nothing_and_keeps_the_docker_socket_out():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "version" not in data, "Compose v2 has no version key"
    services = data["services"]
    assert set(services) == {"framework", "gui"}
    for name, service in services.items():
        assert service.get("pull_policy") == "never", f"{name}: compose must never pull the framework image"
        assert "build" not in service, f"{name}: the builder script builds the image"
        assert not service.get("ports"), f"{name}: no published port"
        assert not service.get("privileged") and service.get("cap_drop") == ["ALL"]
        assert "no-new-privileges:true" in service.get("security_opt", [])
        assert not [v for v in service.get("volumes", []) if "docker.sock" in str(v)]
        assert "lcf-data:/data" in service.get("volumes", [])
    assert services["gui"].get("profiles") == ["gui"], "the GUI only on request"
    assert "llm-framework" not in " ".join(services["framework"]["command"])
    assert data["volumes"]["lcf-data"]["name"] == "lcf-data"


def test_the_makefile_uses_compose_v2_and_the_builder_and_deletes_no_data():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert not re.search(r"docker-compose(?!\.yml)", text) and "docker compose -f" in text, "Compose v2"
    assert "scripts/build_container_image.sh" in text and "setup_linux.sh" not in text
    for script in re.findall(r"(scripts/[\w./-]+)", text):
        assert (REPO_ROOT / script).is_file(), f"the Makefile calls the missing {script}"
    body = re.search(r"^clean:\n((?:\t.*\n)+)", text, re.M).group(1)
    commands = re.sub(r'"[^"]*"', '""', body)  # what is only printed (echo "...") is not run
    assert not re.search(r"\brm\b|\s-v\b|--volumes|\bprune\b", commands), "make clean deletes nothing: it only shows how"
    assert "docker volume rm lcf-data" in body, "it says how to remove the data volume yourself"
    assert "docker.sock" not in text, "make audit scans an archive, never with the Docker socket"


# --- the builder script ----------------------------------------------------------------------------------------------

FAKE_DOCKER = r"""#!/bin/bash
echo "ARGV: $*" >> "$LOG"
case "$1" in
  info) exit "${DOCKER_INFO_EXIT:-0}" ;;
  buildx) exit "${DOCKER_BUILDX_EXIT:-0}" ;;
  build)
    context="${@: -1}"
    (cd "$context" && find . -type f | sort) > "$LOG.context"
    exit 0 ;;
esac
exit 0
"""


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "Docker Setup").mkdir()
    shutil.copy2(BUILDER, repo / "scripts" / "build_container_image.sh")
    (repo / "Docker Setup" / "Dockerfile").write_text("FROM scratch\n")
    (repo / "install.sh").write_text("#!/bin/bash\n")
    (repo / "orchestrator" / "runtime" / "build").mkdir(parents=True)
    (repo / "orchestrator" / "runtime" / "build" / "recipe.py").write_text("x = 1\n")
    git = ["git", "-C", str(repo), "-c", "safe.directory=*", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git[:3] + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "."], check=True)
    # Untracked personal files in the repository folder: they must never reach the build.
    (repo / "configs").mkdir()
    (repo / "configs" / "config.yml").write_text("secret_helper: pass show vault\n")
    (repo / ".env").write_text("LCF_EXAMPLE=untracked\n")
    return repo


def _run_builder(repo: Path, tmp_path: Path, *args: str, docker: bool = True, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if docker:
        (bin_dir / "docker").write_text(FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
    for tool in ("git", "tar", "find", "sort", "mktemp", "rm", "id", "tr", "wc", "sed", "dirname", "cat", "bash"):
        found = shutil.which(tool)
        if found and not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    log = tmp_path / "docker.log"
    run = subprocess.run(["/bin/bash", str(repo / "scripts" / "build_container_image.sh"), *args],
                         env={"PATH": str(bin_dir), "HOME": str(tmp_path), "LOG": str(log),
                              "TMPDIR": str(tmp_path), **env},
                         capture_output=True, text=True, timeout=120)
    calls = log.read_text() if log.exists() else ""
    context = Path(str(log) + ".context").read_text() if Path(str(log) + ".context").exists() else ""
    return run, calls, context


@needs_bash
@needs_git
class TestBuilder:

    def test_it_builds_only_the_tracked_files_with_a_local_tag_and_prints_how_to_run_it(self, tmp_path):
        repo = _fake_repo(tmp_path)
        run, calls, context = _run_builder(repo, tmp_path)
        assert run.returncode == 0, run.stdout + run.stderr
        files = set(context.splitlines())
        assert "./orchestrator/runtime/build/recipe.py" in files and "./Docker Setup/Dockerfile" in files
        assert "./.lcf-container-context" in files
        assert "./configs/config.yml" not in files and "./.env" not in files, "untracked files never reach the build"
        build = next(line for line in calls.splitlines() if line.startswith("ARGV: build "))
        assert "--tag lcf-framework:local" in build and "WITH_GUI=0" in build
        assert re.search(r"LCF_UID=(\d+)", build).group(1) != "0"
        assert not re.search(r"\b(push|pull|login)\b", calls), "nothing is pushed or pulled"
        assert "docker exec -i lcf-framework llm-cli mcp serve" in run.stdout
        assert "Nothing was pushed" in run.stdout
        assert not list(tmp_path.glob("lcf-container-*")), "the staged context is removed"

    def test_the_gui_is_an_option_with_its_own_tag_and_root_becomes_1000(self, tmp_path):
        repo = _fake_repo(tmp_path)
        run, calls, _ = _run_builder(repo, tmp_path, "--gui", "--uid", "0", "--gid", "0")
        assert run.returncode == 0, run.stdout + run.stderr
        build = next(line for line in calls.splitlines() if line.startswith("ARGV: build "))
        assert "WITH_GUI=1" in build and "--tag lcf-framework:local-gui" in build
        assert "LCF_UID=1000" in build and "LCF_GID=1000" in build

    @pytest.mark.parametrize("tag", ["ghcr.io/me/lcf:1", "docker.io/library/lcf", "registry:5000/lcf",
                                     "localhost/lcf", "Upper:1", "lcf:bad tag"])
    def test_a_registry_name_is_refused_before_docker_runs(self, tmp_path, tag):
        repo = _fake_repo(tmp_path)
        run, calls, _ = _run_builder(repo, tmp_path, "--tag", tag)
        assert run.returncode == 2 and "not a local image name" in run.stderr
        assert calls == "", "docker is not called"

    def test_missing_docker_daemon_or_buildkit_stop_with_a_reason(self, tmp_path):
        repo = _fake_repo(tmp_path)
        run, _, _ = _run_builder(repo, tmp_path, docker=False)
        assert run.returncode == 2 and "Docker is not installed" in run.stderr
        run, _, _ = _run_builder(repo, tmp_path, DOCKER_INFO_EXIT="1")
        assert run.returncode == 2 and "daemon is not reachable" in run.stderr
        run, _, _ = _run_builder(repo, tmp_path, DOCKER_BUILDX_EXIT="1")
        assert run.returncode == 2 and "BuildKit" in run.stderr

    def test_the_builder_is_executable_for_make_and_the_ci(self):
        assert os.access(BUILDER, os.X_OK)
        staged = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-s", str(BUILDER)], capture_output=True,
                                text=True)
        if staged.stdout:
            assert staged.stdout.startswith("100755"), "git keeps the executable bit"

    def test_shellcheck_finds_no_problem(self):
        shellcheck = shutil.which("shellcheck")
        if not shellcheck:
            pytest.skip("shellcheck is not installed")
        run = subprocess.run([shellcheck, str(BUILDER)], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout


# --- install.sh in a container build ---------------------------------------------------------------------------------

def _stub(folder: Path, name: str, exit_code: int = 0) -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{folder}/calls.log"\ncat > /dev/null\nexit {exit_code}\n')
    path.chmod(0o755)


@needs_bash
class TestInstallShInAContainer:

    def _setup_system(self, tmp_path, container: bool):
        stubs = tmp_path / "bin"
        for name in ("curl", "usermod", "sh", "groups"):
            _stub(stubs, name)
        python = shutil.which("python3")
        (stubs / "python3").symlink_to(python)
        env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "USER": "lcf"}
        if container:
            env["LCF_CONTAINER_BUILD"] = "1"
        # No docker anywhere on this PATH: outside a container build the installer would fetch get.docker.com.
        script = ('source "$1"; command() { if [ "$2" = docker ]; then return 1; fi; builtin command "$@"; }; '
                  'wait_for_internet() { :; }; setup_system')
        run = subprocess.run(["/bin/bash", "-c", script, "test", str(INSTALL_SH)], env=env, capture_output=True,
                             text=True, timeout=60, cwd=tmp_path)
        calls = (stubs / "calls.log").read_text() if (stubs / "calls.log").exists() else ""
        return run, calls

    def test_no_docker_engine_and_no_docker_group_in_a_container_build(self, tmp_path):
        run, calls = self._setup_system(tmp_path, container=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "get.docker.com" not in calls and "usermod" not in calls

    def test_outside_a_container_build_the_engine_is_still_installed(self, tmp_path):
        run, calls = self._setup_system(tmp_path, container=False)
        assert "get.docker.com" in calls, run.stdout + run.stderr

    @pytest.mark.parametrize("with_sudo", [False, True])
    def test_the_venv_is_made_as_the_user_with_runuser_when_sudo_is_missing(self, tmp_path, with_sudo):
        stubs = tmp_path / "bin"
        _stub(stubs, "runuser")
        if with_sudo:
            _stub(stubs, "sudo")
        for tool in ("bash", "cat", "sed", "date"):
            if shutil.which(tool):
                (stubs / tool).symlink_to(shutil.which(tool))
        run = subprocess.run(["/bin/bash", "-c", 'source "$1"; INSTALL_DIR="$2"; setup_venv', "test", str(INSTALL_SH),
                              str(tmp_path)], env={"PATH": str(stubs), "HOME": str(tmp_path), "SUDO_USER": "lcf"},
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        calls = (stubs / "calls.log").read_text()
        expected = "sudo -u lcf bash" if with_sudo else "runuser -u lcf -- bash"
        assert calls.splitlines() == [expected]

    @needs_git
    def test_the_listing_without_git_keeps_nested_build_folders(self, tmp_path):
        repo = tmp_path / "unpacked"
        for rel in ("orchestrator/runtime/build/recipe.py", "orchestrator/main.py", "build/lib/old.py",
                    "dist/pkg.whl", "docs/dist/keep.md", "configs/config.yml", "configs/llm_providers.yml"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text("x")
        run = subprocess.run(["/bin/bash", "-c", 'source "$1"; REPO_ROOT="$2"; shipped_files', "test",
                              str(INSTALL_SH), str(repo)],
                             env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CEILING_DIRECTORIES":
                                  str(tmp_path)}, capture_output=True, timeout=60)
        assert run.returncode == 0, run.stderr
        listed = set(run.stdout.decode().split("\0")) - {""}
        assert {"orchestrator/runtime/build/recipe.py", "orchestrator/main.py", "docs/dist/keep.md",
                "configs/llm_providers.yml"} <= listed
        assert not {"build/lib/old.py", "dist/pkg.whl", "configs/config.yml"} & listed


# --- the docs --------------------------------------------------------------------------------------------------------

def test_the_target_files_registry_and_tags_are_documented_as_local_names_only():
    for guide, words in (("docs/Module_Developer_Guide.md", "local image names only"),
                         ("docs/Module_Developer_Guide_DE.md", "nur lokale Image-Namen")):
        text = (REPO_ROOT / guide).read_text(encoding="utf-8")
        assert "`docker.registry`" in text and "`docker.tags`" in text and words in text
    for target in ("targets/Rockchip/target.yml", "targets/_template/target.yml"):
        docker = (yaml.safe_load((REPO_ROOT / target).read_text(encoding="utf-8")) or {}).get("docker") or {}
        assert "registry" in docker, "kept (not removed without asking), documented as a local name"
