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
import sys
import tarfile
import textwrap
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

PUSH = re.compile(r"\b(docker|podman|nerdctl|buildah)\s+(image\s+)?push\b|\bbuildx\b[^\n]*--push\b"
                  r"|\bcompose\b[^\n]*\bpush\b|\bskopeo\s+copy\b|\bcrane\s+push\b|\boras\s+push\b"
                  r"|\bregctl\s+image\s+copy\b|\bctr\b[^\n]*\bimages?\s+push\b|\bdocker/login-action\b"
                  r"|\b(docker|podman|buildah)\s+login\b", re.I)
PULL = re.compile(r"\b(docker|podman|nerdctl)\s+(image\s+)?pull\b[^\n]*" + FRAMEWORK_IMAGE
                  + r"|\bimages\.pull\([^)]*" + FRAMEWORK_IMAGE, re.I)
# `docker run` and `docker create` pull a missing image by themselves. A framework image is local only, and a name
# such as llm-framework/rockchip:ci or acme/lcf:local also names a repository of someone else on Docker Hub, so every
# run of a framework image says --pull never.
RUNS_IMAGE = re.compile(r"\b(docker|podman|nerdctl)\s+(container\s+)?(run|create)\b[^\n`|;&]*?"
                        r"(\blcf-framework\b|\bllm-framework/|\bsmilez1985/|\$tag\b)", re.I)
PULL_NEVER = re.compile(r"--pull[= ]never\b")


def _implicit_pull(line: str) -> bool:
    return bool(RUNS_IMAGE.search(line)) and not PULL_NEVER.search(line)


def _command_lines(text: str):
    """(first line number, line) with backslash continuations joined: a `docker run` whose image or --pull option
    stands on a continuation line is checked as one command."""
    start, current = None, ""
    for number, line in enumerate(text.splitlines(), 1):
        if start is None:
            start = number
        if line.rstrip().endswith("\\"):
            current += line.rstrip()[:-1] + " "
            continue
        yield start, current + line
        start, current = None, ""
    if current:
        yield start, current


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
        for number, line in _command_lines(text):
            if PUSH.search(line) or PULL.search(line) or _implicit_pull(line):
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not found, ("a framework image is pushed or pulled (a docker run of it needs --pull never):\n  "
                       + "\n  ".join(found))


def test_every_workflow_build_has_push_false():
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in (data.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = str(step.get("uses", ""))
                if "build-push-action" in uses:
                    assert (step.get("with") or {}).get("push") is False, f"{workflow.name}: push must be false"


# A step that starts the framework (not pytest, which fakes the vault) on a runner without a system keyring stops
# with exit code 5 unless the workflow says how the vault is kept (the nightly retrieval evaluation failed so).
FRAMEWORK_RUN = re.compile(r"orchestrator\.cli\b|\bllm-cli\b|\bllmcf\b|\bllm-gui\b")


def framework_steps_without_secret_storage(data: dict) -> list:
    """The steps that start the framework without LLM_SECRET_STORAGE in the workflow's, the job's or the step's env
    (or a docker run -e)."""
    missing = []
    workflow_env = data.get("env") or {}
    for job_name, job in (data.get("jobs") or {}).items():
        job_env = {**workflow_env, **(job.get("env") or {})}
        for step in job.get("steps") or []:
            run = str(step.get("run") or "")
            if not FRAMEWORK_RUN.search(run):
                continue
            env = {**job_env, **(step.get("env") or {})}
            if "LLM_SECRET_STORAGE" not in env and "LLM_SECRET_STORAGE=" not in run:
                missing.append(f"{job_name}: {step.get('name') or run.strip()[:60]}")
    return missing


def test_every_workflow_that_starts_the_framework_says_how_the_vault_is_kept():
    found = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        found += [f"{workflow.name}: {step}" for step in framework_steps_without_secret_storage(data)]
    assert not found, ("set LLM_SECRET_STORAGE (e.g. ephemeral) for these steps, a runner has no keyring:\n  "
                       + "\n  ".join(found))


def test_the_vault_guard_finds_a_framework_step_without_secret_storage():
    step = {"name": "eval", "run": "python -m orchestrator.cli -q ditto eval --kind retrieval"}
    assert framework_steps_without_secret_storage({"jobs": {"j": {"steps": [step]}}}) == ["j: eval"]
    assert framework_steps_without_secret_storage(
        {"jobs": {"j": {"env": {"LLM_SECRET_STORAGE": "ephemeral"}, "steps": [step]}}}) == []
    assert framework_steps_without_secret_storage(
        {"env": {"LLM_SECRET_STORAGE": "ephemeral"}, "jobs": {"j": {"steps": [step]}}}) == []
    assert framework_steps_without_secret_storage({"jobs": {"j": {"steps": [
        {"run": "docker run --rm -e LLM_SECRET_STORAGE=ephemeral lcf-framework:local llm-cli targets list"},
        {"run": "python -m pytest -q"}]}}}) == []


def test_the_guard_finds_a_push_and_a_framework_pull():
    for line in ["docker push lcf-framework:local", "docker buildx build --push -t x .", "docker image push x",
                 "docker compose push", "uses: docker/login-action@v3", "docker pull ghcr.io/smilez1985/llm-runtime",
                 "podman pull llm-framework/orchestrator:1.3.0", "buildah push lcf-framework:local docker://x",
                 "oras push registry.example/x:1 file", "regctl image copy a b", "ctr -n k8s.io images push x",
                 "podman login ghcr.io", "docker run --rm lcf-framework:local llm-cli --version",
                 "docker run --rm llm-framework/rockchip:ci status", "  docker run --rm -v lcf-data:/data $tag llm-cli"]:
        assert PUSH.search(line) or PULL.search(line) or _implicit_pull(line), line
    for line in ["python scripts/secret_scan.py --push", "docker pull debian:13-slim", "git push origin main",
                 "push: false", "docker run --rm --pull never lcf-framework:local id -u",
                 "docker run --pull=never --rm llm-framework/rockchip:ci test", "docker run --rm aquasec/trivy:0.75.0"]:
        assert not (PUSH.search(line) or PULL.search(line) or _implicit_pull(line)), line
    # A command over several lines is one command: the image on a continuation line is found, and so is the option.
    missing = "docker run -d --name box \\\n    -v lcf-data:/data $tag sleep infinity\n"
    assert [line for _, line in _command_lines(missing) if _implicit_pull(line)]
    given = "docker run -d --pull never --name box \\\n    -v lcf-data:/data $tag sleep infinity\n"
    assert not [line for _, line in _command_lines(given) if _implicit_pull(line)]
    assert [number for number, _ in _command_lines("a\nb \\\nc\nd")] == [1, 2, 4]


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
    # Changed on purpose (chain qt, DECISIONS round 11, A3): the headless image has no Qt, so not even libglib, and
    # installs without the 'gui' extra
    always, gui_only = _apt_packages(text)
    assert "libglib2.0-0t64" in gui_only and "libglib2.0-0t64" not in always, (always, gui_only)
    assert 'LCF_HEADLESS="$headless"' in text and 'headless=1 && if [ "$WITH_GUI" = "1" ]; then headless=0; fi' in text
    cmd = re.findall(r"^CMD\s+(.+)$", text, re.M)
    assert cmd == ['["llm-cli", "--help"]'], "the default command is the CLI, never the GUI"
    assert re.search(r'^VOLUME \["/data"\]$', text, re.M) and "LLM_FRAMEWORK_HOME=/data/home" in text


# Which Debian package gives a system library the Qt wheel does not bundle: directly, or as a hard dependency of a
# package the Dockerfile installs (checked on packages.debian.org for trixie; no recommended packages are installed).
# "base" is in debian:13-slim itself.
BASE = "base"
SYSTEM_LIBRARIES = {
    "libc.so.6": BASE, "libm.so.6": BASE, "libdl.so.2": BASE, "libpthread.so.0": BASE, "librt.so.1": BASE,
    "ld-linux-x86-64.so.2": BASE, "ld-linux-aarch64.so.1": BASE, "libstdc++.so.6": BASE, "libgcc_s.so.1": BASE,
    "libz.so.1": BASE, "libzstd.so.1": BASE,
    "libglib-2.0.so.0": "libglib2.0-0t64", "libgthread-2.0.so.0": "libglib2.0-0t64",
    "libEGL.so.1": "libegl1", "libGL.so.1": "libgl1", "libOpenGL.so.0": "libopengl0",
    "libX11-xcb.so.1": "libx11-xcb1", "libX11.so.6": "libx11-xcb1", "libxcb.so.1": "libx11-xcb1",
    "libdbus-1.so.3": "libdbus-1-3", "libfontconfig.so.1": "fontconfig", "libfreetype.so.6": "fontconfig",
    "libxcb-cursor.so.0": "libxcb-cursor0", "libxcb-icccm.so.4": "libxcb-icccm4", "libxcb-image.so.0": "libxcb-image0",
    "libxcb-shm.so.0": "libxcb-image0", "libxcb-util.so.1": "libxcb-image0", "libxcb-keysyms.so.1": "libxcb-keysyms1",
    "libxcb-randr.so.0": "libxcb-randr0", "libxcb-render-util.so.0": "libxcb-render-util0",
    "libxcb-render.so.0": "libxcb-render-util0", "libxcb-shape.so.0": "libxcb-shape0", "libxcb-sync.so.1": "libxcb-sync1",
    "libxcb-xfixes.so.0": "libxcb-xfixes0", "libxcb-xkb.so.1": "libxcb-xkb1", "libxcb-xinerama.so.0": "libxcb-xinerama0",
    "libxkbcommon-x11.so.0": "libxkbcommon-x11-0", "libxkbcommon.so.0": "libxkbcommon0",
}


def _apt_packages(text: str):
    """The packages of the Dockerfile's apt step: (always installed, only with WITH_GUI=1)."""
    run = next(line for line in _logical_lines(text) if "apt-get install" in line and "WITH_GUI" in line)
    always, gui = run.split('if [ "$WITH_GUI" = "1" ]', 1)
    pick = re.compile(r"apt-get install -y --no-install-recommends ([^;&]+)")
    return ({p for m in pick.findall(always) for p in m.split()}, {p for m in pick.findall(gui) for p in m.split()})


def _qt_modules(gui: bool):
    """The Qt modules the code imports: without the GUI (orchestrator/gui), or all of them."""
    found = set()
    for path in (REPO_ROOT / "orchestrator").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not gui and rel.startswith("orchestrator/gui/"):
            continue
        found |= set(re.findall(r"^\s*(?:from|import)\s+PySide6\.(Qt\w+)", path.read_text(encoding="utf-8"), re.M))
    return found


def _system_libraries(files):
    """The libraries the given ELF files need that the PySide6 wheel does not bundle (readelf, closure in the wheel)."""
    import PySide6
    import shiboken6
    package = Path(PySide6.__file__).parent
    bundled = {p.name: p for root in (package, package / "Qt" / "lib", Path(shiboken6.__file__).parent)
               for p in root.glob("*.so*")}
    seen, system, todo = set(), set(), list(files)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        dynamic = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
        for name in re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", dynamic):
            if name in bundled:
                todo.append(bundled[name])
            else:
                system.add(name)
    return system


needs_qt_elf = pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("readelf"),
                                  reason="reads the Linux Qt wheel's libraries with readelf")


@needs_qt_elf
@pytest.mark.parametrize("gui", [False, True], ids=["headless", "gui"])
def test_the_image_installs_every_system_library_qt_needs(gui):
    """Changed on purpose (chain qt): the code outside the GUI imports no Qt any more (it imported PySide6.QtCore, whose
    library needs libglib, in every llm-cli command), so the headless image needs no Qt library at all. The GUI image
    needs everything QtCore, QtGui, QtWidgets and the xcb platform plugin load, or Qt stops with "could not load the
    Qt platform plugin xcb"."""
    modules = _qt_modules(gui)
    if not gui:
        assert modules == set(), f"the CLI and the MCP server import Qt: {modules}"
        return
    pytest.importorskip("PySide6")
    import PySide6
    package = Path(PySide6.__file__).parent
    assert {"QtCore", "QtGui", "QtWidgets"} <= modules, modules
    files = [package / f"{module}.abi3.so" for module in sorted(modules)]
    if gui:
        files.append(package / "Qt" / "plugins" / "platforms" / "libqxcb.so")
    files = [f for f in files if f.exists()]
    if not files:
        pytest.skip("this PySide6 wheel has no Linux libraries")
    always, gui_only = _apt_packages(DOCKERFILE.read_text(encoding="utf-8"))
    installed = always | (gui_only if gui else set())
    unknown = sorted(name for name in _system_libraries(files) if name not in SYSTEM_LIBRARIES)
    assert not unknown, f"add the Debian package of {unknown} to SYSTEM_LIBRARIES and to the Dockerfile"
    missing = sorted({SYSTEM_LIBRARIES[name] for name in _system_libraries(files)} - installed - {BASE})
    assert not missing, f"the {'GUI' if gui else 'headless'} image lacks {missing}"


def _build_check(install_dir: Path) -> str:
    """The Dockerfile's check after install.sh, as a bash script for a fake installation in install_dir."""
    lines = _logical_lines(DOCKERFILE.read_text(encoding="utf-8"))
    run = next((line for line in lines if line.startswith("RUN ") and "import orchestrator.cli" in line), None)
    assert run, "the Dockerfile checks the installation after install.sh"
    install_at = next(i for i, line in enumerate(lines) if "/src/install.sh" in line)
    assert lines.index(run) > install_at
    return "set -o pipefail\n" + run[4:].replace("/opt/llm-cross-compiler-framework", str(install_dir))


@needs_bash
@pytest.mark.parametrize("case", ["ok", "ok-gui", "import-fails", "library-missing", "gui-library-missing",
                                  "plugin-missing", "headless-with-qt"])
def test_the_build_fails_when_the_installation_does_not_work(tmp_path, case):
    """Before, a failed pip install or a missing system library still gave a "built" image whose llm-cli failed at
    the first command. The build now imports the CLI and, for the GUI, resolves every library Qt loads (ldd), and
    stops if not. Changed on purpose (chain qt): the headless image has no Qt to check, and a headless image that
    installed PySide6 anyway is refused."""
    install = tmp_path / "opt"
    gui = "1" if case in ("ok-gui", "library-missing", "gui-library-missing", "plugin-missing") else "0"
    site = install / ".venv" / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    qt = site / "PySide6"
    if gui == "1" or case == "headless-with-qt":
        (qt / "Qt" / "plugins" / "platforms").mkdir(parents=True)
        for rel in ("QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so", "Qt/plugins/platforms/libqxcb.so"):
            if not (case == "plugin-missing" and rel.endswith("libqxcb.so")):
                (qt / rel).write_text("elf")
    python = install / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n" + ('echo "ModuleNotFoundError: yaml" >&2; exit 1\n' if case == "import-fails"
                                        else "exit 0\n"))
    python.chmod(0o755)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    missing_in = {"library-missing": "QtCore", "gui-library-missing": "libqxcb"}.get(case, "NOTHING_IS_MISSING")
    (stubs / "ldd").write_text('#!/bin/sh\necho "\tlibc.so.6 => /lib/libc.so.6"\n'
                               f'case "$1" in *{missing_in}*) echo "\tlibglib-2.0.so.0 => not found" ;; esac\n')
    (stubs / "ldd").chmod(0o755)
    run = subprocess.run(["/bin/bash", "-c", _build_check(install)], capture_output=True, text=True, timeout=60,
                         env={"PATH": f"{stubs}:/usr/bin:/bin", "WITH_GUI": gui, "HOME": str(tmp_path)})
    if case.startswith("ok"):
        assert run.returncode == 0, run.stdout + run.stderr
        return
    assert run.returncode != 0, f"{case}: the image would count as built"
    if "library-missing" in case:
        assert "libglib-2.0.so.0 => not found" in run.stderr and "apt step" in run.stderr
    if case == "plugin-missing":
        assert "libqxcb.so" in run.stderr
    if case == "headless-with-qt":
        assert "must not install Qt" in run.stderr


def test_the_container_user_reuses_a_group_that_exists_already():
    """The builder passes your ids. A primary group such as 100 (users, openSUSE) or 20 (Docker Desktop on a Mac)
    exists in Debian already, and groupadd --gid would stop the build: the existing group is used instead."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    run = next(line for line in _logical_lines(text) if "useradd" in line)
    assert re.search(r'getent group "\$LCF_GID"', run) and "groupadd" in run
    assert run.index("getent group") < run.index("groupadd"), "groupadd only when the group does not exist yet"
    assert '--gid "$LCF_GID"' in run.split("useradd", 1)[1], "the user gets the group by its id, whatever its name"
    assert not re.search(r"-g lcf\b|:lcf\b|--gid lcf\b", text), "no command relies on a group named lcf"


def test_every_data_folder_lives_in_the_data_volume():
    """Folders the framework writes the person's data to must be in /data: what lies in the container's own layer is
    lost when the container is replaced. targets_dir stays in the program folder (it holds the shipped targets too)."""
    from orchestrator.tools.tools_status import FOLDER_KEYS
    text = DOCKERFILE.read_text(encoding="utf-8")
    env = dict(re.findall(r"\b(LLM_FRAMEWORK_[A-Z_]+)=(\S+)", text))
    for key in sorted(k for k in FOLDER_KEYS if k.endswith("_dir") and k != "targets_dir"):
        assert env.get(f"LLM_FRAMEWORK_{key.upper()}", "").startswith("/data/"), f"{key} is not in /data"
    assert env.get("LLM_FRAMEWORK_HOME") == "/data/home" and env.get("LLM_FRAMEWORK_CONFIG_DIR") == "/data/config"


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
    # No rm -rf of folders, no volume removal; a container goes only through compose rm after the check of
    # scripts/container_kept_files.sh (TestMakeNeverRemovesYourFiles runs it).
    assert not re.search(r"\brm\s+-[A-Za-z]|\s-v\b|--volumes|\bprune\b", commands), "make clean deletes no data"
    assert "$(KEPT)" in commands and commands.index("$(KEPT)") < commands.index("rm --stop --force")
    # An orphan (a container of this project from an earlier LCF_COMPOSE_OVERRIDE) was never checked for your files.
    assert "--remove-orphans" not in commands and "--rmi" not in commands
    assert "docker volume rm lcf-data" in body, "it says how to remove the data volume yourself"
    assert "docker.sock" not in text, "make audit scans an archive, never with the Docker socket"


def _recipe(text: str, target: str) -> str:
    return re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n)+)", text, re.M).group(1)


def test_make_down_stops_the_containers_and_removes_none():
    """docker compose down removes the containers, and with them what they hold outside the data volume (targets
    you created, packs you installed): make down only stops them."""
    body = _recipe(MAKEFILE.read_text(encoding="utf-8"), "down")
    assert re.search(r"\$\(COMPOSE\)[^\n]* stop\b", body) and not re.search(r"\bdown\b|\brm\b", body)


# --- what a container holds outside the data volume --------------------------------------------------------------

KEPT = REPO_ROOT / "scripts" / "container_kept_files.sh"
PROGRAM = "/opt/llm-cross-compiler-framework"
# The program folder of images built before the repository was renamed.
OLD_PROGRAM = "/opt/llm-conversion-framework"

FAKE_ENGINE = r"""#!/bin/bash
# A fake docker for the containers lcf-framework and lcf-framework-gui; $FAKE holds their state.
echo "ARGV: $*" >> "$LOG"
case "$1" in
  info) exit "${DOCKER_INFO_EXIT:-0}" ;;
  container)
    name="${@: -1}"
    [ -f "$FAKE/exists-$name" ] || exit 1
    if [ "$3" = "-f" ]; then cat "$FAKE/image-$name"; fi
    exit 0 ;;
  image) echo "${FAKE_IMAGE_ID:-sha256:new}"; exit 0 ;;
  diff) cat "$FAKE/diff-$2"; exit 0 ;;
  cp) key=$(printf '%s' "${2#*:}" | tr '/' '_'); cat "$FAKE/cp$key.tar" ;;
  compose) exit 0 ;;
esac
exit 0
"""


def _tar(path: Path, members):
    """A tar stream as `docker cp CONTAINER:SRC -` writes it: members relative to the parent of SRC."""
    import io
    with tarfile.open(path, "w") as archive:
        for name, size in members:
            info = tarfile.TarInfo(name.rstrip("/"))
            if name.endswith("/"):
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            else:
                info.size, info.mode = size, 0o644
                archive.addfile(info, io.BytesIO(b"x" * size))


def _engine(tmp_path: Path, holds_files: bool, image: str = "sha256:old", program: str = PROGRAM) -> dict:
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_ENGINE)
    (bin_dir / "docker").chmod(0o755)
    (fake / "exists-lcf-framework").write_text("")
    (fake / "image-lcf-framework").write_text(image + "\n")
    noise = [f"C {program}", f"A {program}/orchestrator/__pycache__", f"A {program}/orchestrator/__pycache__/x.pyc",
             "A /tmp/build.lock", "C /home/lcf", "A /home/lcf/.bash_history"]
    mine = [f"C {program}/targets", f"A {program}/targets/my_board", f"A {program}/targets/my_board/target.yml",
            f"A {program}/targets/my_board/modules", f"A {program}/targets/my_board/modules/build.sh",
            f"C {program}/targets/Rockchip", f"C {program}/targets/Rockchip/target.yml", "A /home/lcf/notes.txt"]
    (fake / "diff-lcf-framework").write_text("\n".join(noise + (mine if holds_files else [])) + "\n")
    _tar(fake / f"cp{program.replace('/', '_')}_targets_my_board.tar",
         [("my_board/", 0), ("my_board/target.yml", 2048), ("my_board/modules/", 0),
          ("my_board/modules/build.sh", 300)])
    _tar(fake / f"cp{program.replace('/', '_')}_targets_Rockchip_target.yml.tar", [("target.yml", 5000)])
    _tar(fake / "cp_home_lcf_notes.txt.tar", [("notes.txt", 12)])
    log = tmp_path / "docker.log"
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE": str(fake), "LOG": str(log), "HOME": str(tmp_path)}


def _kept(env, container="lcf-framework"):
    return subprocess.run(["/bin/bash", str(KEPT), container], env=env, capture_output=True, text=True, timeout=60)


@needs_bash
class TestKeptFiles:

    def test_it_lists_every_file_of_yours_with_full_path_name_size_and_what_it_is(self, tmp_path):
        env = _engine(tmp_path, holds_files=True)
        run = _kept(env)
        assert run.returncode == 3, run.stdout + run.stderr
        lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
        assert len(lines) == 4, lines
        target = next(line for line in lines if line.startswith(f"{PROGRAM}/targets/my_board/target.yml "))
        assert "target.yml" in target and "2.0KB" in target and "target" in target.split(":", 1)[1]
        assert any(line.startswith(f"{PROGRAM}/targets/my_board/modules/build.sh ") and "300B" in line
                   for line in lines)
        assert any(line.startswith(f"{PROGRAM}/targets/Rockchip/target.yml ") and "4.9KB" in line for line in lines)
        assert any(line.startswith("/home/lcf/notes.txt ") and "home folder" in line for line in lines)
        assert not [line for line in lines if "__pycache__" in line or "/tmp/" in line or "bash_history" in line]
        assert f"cp lcf-framework:{PROGRAM} " not in Path(env["LOG"]).read_text(), \
            "a changed folder is never copied whole"

    def test_a_container_from_an_image_built_before_the_rename_keeps_its_files_too(self, tmp_path):
        """Images built before the repository was renamed have their program folder at /opt/llm-conversion-framework.
        Targets and packs of yours in a container made from such an image are listed as well, so make keeps it."""
        (tmp_path / "with").mkdir()
        env = _engine(tmp_path / "with", holds_files=True, program=OLD_PROGRAM)
        run = _kept(env)
        assert run.returncode == 3, run.stdout + run.stderr
        lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
        assert len(lines) == 4, lines
        target = next(line for line in lines if line.startswith(f"{OLD_PROGRAM}/targets/my_board/target.yml "))
        assert "2.0KB" in target and "target" in target.split(":", 1)[1]
        assert not [line for line in lines if "__pycache__" in line]
        (tmp_path / "without").mkdir()
        run = _kept(_engine(tmp_path / "without", holds_files=False, program=OLD_PROGRAM))
        assert run.returncode == 0 and run.stdout == "", run.stdout + run.stderr

    def test_what_cannot_be_read_counts_as_yours_and_a_folder_detail_change_does_not(self, tmp_path):
        """Fail closed: an entry of yours that docker cp cannot read is listed (size unknown), so make keeps the
        container; a folder whose own details changed (a file made and removed again) holds nothing to lose."""
        env = _engine(tmp_path, holds_files=False)
        fake = Path(env["FAKE"])
        with (fake / "diff-lcf-framework").open("a") as diff:
            diff.write(f"C {PROGRAM}/community\nA {PROGRAM}/targets/broken\n")
        _tar(fake / f"cp{PROGRAM.replace('/', '_')}_community.tar",
             [("community/", 0), ("community/knowledge/", 0), ("community/knowledge/.gitkeep", 0)])
        run = _kept(env)
        assert run.returncode == 3, run.stdout + run.stderr
        assert f"{PROGRAM}/targets/broken (broken, size unknown" in run.stdout
        assert "community" not in run.stdout
        (fake / "diff-lcf-framework").write_text(f"C {PROGRAM}/community\n")
        assert _kept(env).returncode == 0

    def test_nothing_of_yours_or_no_container_means_exit_0(self, tmp_path):
        env = _engine(tmp_path, holds_files=False)
        run = _kept(env)
        assert run.returncode == 0 and run.stdout == "", run.stdout + run.stderr
        run = _kept(env, "lcf-framework-gui")
        assert run.returncode == 0 and run.stdout == ""

    def test_an_unreachable_docker_is_an_error_not_an_empty_list(self, tmp_path):
        env = _engine(tmp_path, holds_files=True)
        run = _kept({**env, "DOCKER_INFO_EXIT": "1"}, "lcf-framework-gui")
        assert run.returncode == 2 and "not reachable" in run.stderr

    def test_shellcheck_finds_no_problem(self):
        shellcheck = shutil.which("shellcheck")
        if not shellcheck:
            pytest.skip("shellcheck is not installed")
        run = subprocess.run([shellcheck, str(KEPT)], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout


def _make(env, *targets):
    return subprocess.run(["make", "--no-print-directory", "-C", str(REPO_ROOT), *targets], env=env,
                          capture_output=True, text=True, timeout=120)


needs_make = pytest.mark.skipif(not shutil.which("make"), reason="needs make")


@needs_bash
@needs_make
class TestMakeNeverRemovesYourFiles:

    def test_clean_keeps_a_container_that_holds_files_of_yours_and_lists_them(self, tmp_path):
        env = _engine(tmp_path, holds_files=True)
        run = _make(env, "clean")
        assert run.returncode == 0, run.stdout + run.stderr
        calls = Path(env["LOG"]).read_text()
        assert not re.search(r"compose .*\brm\b.*\bframework\b", calls), "the container with your files stays"
        assert not re.search(r"compose .* down\b", calls)
        assert f"{PROGRAM}/targets/my_board/target.yml" in run.stdout and "docker rm -f lcf-framework" in run.stdout

    def test_clean_removes_containers_that_hold_nothing_of_yours(self, tmp_path):
        env = _engine(tmp_path, holds_files=False)
        run = _make(env, "clean")
        assert run.returncode == 0, run.stdout + run.stderr
        calls = Path(env["LOG"]).read_text()
        assert re.search(r"compose .*\brm --stop --force framework\b", calls)
        assert re.search(r"compose .* down$", calls, re.M), "only the project network goes; no orphan is removed"

    def test_up_never_replaces_a_container_that_holds_files_of_yours(self, tmp_path):
        env = _engine(tmp_path, holds_files=True)
        run = _make(env, "up")
        assert run.returncode == 0, run.stdout + run.stderr
        calls = Path(env["LOG"]).read_text()
        assert re.search(r"compose .* up -d --no-recreate framework\b", calls)
        assert f"{PROGRAM}/targets/my_board/target.yml" in run.stdout and "docker rm -f lcf-framework" in run.stdout

    def test_up_replaces_a_container_that_holds_nothing_of_yours(self, tmp_path):
        env = _engine(tmp_path, holds_files=False)
        run = _make(env, "up")
        assert run.returncode == 0, run.stdout + run.stderr
        calls = Path(env["LOG"]).read_text()
        assert re.search(r"compose .* up -d framework\b", calls) and "--no-recreate" not in calls

    def test_down_only_stops(self, tmp_path):
        env = _engine(tmp_path, holds_files=True)
        run = _make(env, "down")
        assert run.returncode == 0, run.stdout + run.stderr
        calls = Path(env["LOG"]).read_text()
        assert re.search(r"compose .* stop\b", calls) and not re.search(r"\b(down|rm)\b", calls)


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
        runs = [line for line in run.stdout.splitlines() if "docker run" in line]
        assert runs and all("--pull never" in line for line in runs), "a missing image is never pulled by docker run"
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

    @pytest.mark.parametrize("headless", ["0", "1"], ids=["gui", "headless"])
    @pytest.mark.parametrize("failing", ["pip", "venv", None])
    def test_a_failed_python_environment_stops_the_installation(self, tmp_path, failing, headless):
        """Before, a pip install that could not reach the index (or a missing venv module) still ended with
        "Installation abgeschlossen", and the container builder reported a built image with an empty .venv. With the
        GUI (the default) the installer installs requirements-gui.txt, with --headless (LCF_HEADLESS=1, the
        framework image without --gui) requirements.txt (chain qt)."""
        wanted = "requirements.txt" if headless == "1" else "requirements-gui.txt"
        stubs = tmp_path / "bin"
        stubs.mkdir()
        # runuser -u lcf -- bash: runs the script it gets on stdin, as the installer's own user here.
        (stubs / "runuser").write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
        (stubs / "python3").write_text(
            '#!/bin/sh\n'
            f'[ "{failing}" = venv ] && {{ echo "No module named venv" >&2; exit 1; }}\n'
            'mkdir -p .venv/bin\n'
            f'printf \'#!/bin/sh\\necho "$*" >> pip.log\\ncase "$*" in *"-r {wanted}"*) [ "{failing}" = pip ] && '
            '{ echo "No matching distribution found" >&2; exit 1; } ;; esac\\nexit 0\\n\' > .venv/bin/pip\n'
            'chmod +x .venv/bin/pip\n')
        for name in ("runuser", "python3"):
            (stubs / name).chmod(0o755)
        install_dir = tmp_path / "opt"
        install_dir.mkdir()
        (install_dir / "requirements.txt").write_text("pyyaml==6.0.3\n")
        (install_dir / "requirements-gui.txt").write_text("pyyaml==6.0.3\npyside6-essentials==6.12.0\n")
        env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "SUDO_USER": "lcf", "LCF_HEADLESS": headless}
        # No sudo on this PATH, so the installer takes runuser.
        script = ('source "$1"; INSTALL_DIR="$2"; command() { if [ "$2" = sudo ]; then return 1; fi; '
                  'builtin command "$@"; }; setup_venv; echo "AFTER setup_venv"')
        run = subprocess.run(["/bin/bash", "-c", script, "test", str(INSTALL_SH), str(install_dir)], env=env,
                             capture_output=True, text=True, timeout=60)
        if failing is None:
            assert run.returncode == 0 and "AFTER setup_venv" in run.stdout, run.stdout + run.stderr
            installs = [line for line in (install_dir / "pip.log").read_text().splitlines() if "--upgrade" not in line]
            # --no-deps: the complete export as it is, never resolved again (pyproject.toml's Pillow override)
            assert installs == [f"install --require-hashes --no-deps -r {wanted}"]
            return
        assert run.returncode != 0, run.stdout + run.stderr
        assert "AFTER setup_venv" not in run.stdout, "the installer went on as if the environment existed"
        assert ".venv ließ sich nicht einrichten" in run.stdout

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



# --- ports: only the loopback ------------------------------------------------------------------------------------

PUBLISH = re.compile(r"(?:(?<![\w-])-p|--publish)[ =]\"?([^\s\"`)]+:[0-9]+)\b")


def _published_beyond_loopback(line: str):
    return [value for value in PUBLISH.findall(line)
            if not re.match(r"(127\.0\.0\.1|\[::1\]|localhost):", value)]


def test_no_document_script_or_compose_file_publishes_a_port_beyond_the_loopback():
    """The MCP server's HTTP transport inside a container (--container) binds the container's interfaces: what the
    framework tells you to publish must stay on the host's loopback (-p 127.0.0.1:PORT:PORT), and compose publishes
    nothing."""
    found = []
    for rel in _checked_files():
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _published_beyond_loopback(line):
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not found, "a port is published beyond the loopback:\n  " + "\n  ".join(found)
    for doc in ("docs/Container_Setup.md", "docs/Container_Setup_DE.md"):
        assert "-p 127.0.0.1:8765:8765" in (REPO_ROOT / doc).read_text(encoding="utf-8")


def test_the_port_guard_finds_a_publish_beyond_the_loopback():
    for line in ["docker run -p 8765:8765 lcf", "  --publish 0.0.0.0:8765:8765", "docker run -p=8765:8765 x",
                 "use `-p 192.0.2.10:8765:8765`"]:
        assert _published_beyond_loopback(line), line
    for line in ["publish to the host's loopback (`-p 127.0.0.1:8765:8765`)", "llama-bench -p 64 -n 16",
                 "docker run -p [::1]:8765:8765 x", "ssh -N -L 8765:127.0.0.1:8765 user@build-box"]:
        assert not _published_beyond_loopback(line), line


def test_the_guide_keeps_the_vault_secret_for_every_make_call():
    """The Docker secret with the vault key comes from your own compose file: a make up without it replaces the
    container by one without the secret, and the framework stops. The guide says to set LCF_COMPOSE_OVERRIDE once in
    the shell profile and gives the secret to the GUI service too."""
    for doc in ("docs/Container_Setup.md", "docs/Container_Setup_DE.md"):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        assert 'export LCF_COMPOSE_OVERRIDE="$HOME/lcf-compose.override.yml"' in text, doc
        block = re.search(r"```yaml\n(.*?)```", text, re.S).group(1)
        example = yaml.safe_load(textwrap.dedent(block))
        assert set(example["services"]) == {"framework", "gui"}, doc
        assert all(service["secrets"] == ["lcf_vault_key"] for service in example["services"].values()), doc
        assert "lcf_vault_key" in example["secrets"], doc

# --- the docs --------------------------------------------------------------------------------------------------------

def test_the_target_files_registry_and_tags_are_documented_as_local_names_only():
    for guide, words in (("docs/Module_Developer_Guide.md", "local image names only"),
                         ("docs/Module_Developer_Guide_DE.md", "nur lokale Image-Namen")):
        text = (REPO_ROOT / guide).read_text(encoding="utf-8")
        assert "`docker.registry`" in text and "`docker.tags`" in text and words in text
    for target in ("targets/Rockchip/target.yml", "targets/_template/target.yml"):
        docker = (yaml.safe_load((REPO_ROOT / target).read_text(encoding="utf-8")) or {}).get("docker") or {}
        assert "registry" in docker, "kept (not removed without asking), documented as a local name"
