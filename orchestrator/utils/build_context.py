"""Assembles the Docker build context that the target Dockerfiles expect.

Target Dockerfiles COPY `modules/`, `scripts/`, `sources/` and `docker/entrypoint.sh` relative to
the context, so the context is staged from the target directory plus the shared entrypoint and an
empty `sources/` folder (an offline build puts its local copy of llama.cpp there).
`modules/stage_user.sh` (run_as_stage, stage_preflight: the stage account of the build sandbox) is
always the framework's own (FRAMEWORK_STAGE_HELPER), whatever the target ships under that name, so a
target can never carry a hollow helper into a sandboxed build.
Symbolic links and special files in a target are never followed into the context (a community
target could otherwise pull any file of the user into an image).
Standard library only, so CI can stage a context without installing the framework.
"""

import logging
import os
import shutil
import stat
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The one source of the stage account's helper (sourced by every stage runner build.sh): the framework's template copy.
# targets/Rockchip/modules/stage_user.sh is the same file byte for byte (tests/test_stage_user.py pins it) so that a
# stage runner started by hand from the repository finds it too; a build context always gets this one.
STAGE_HELPER_NAME = "stage_user.sh"
FRAMEWORK_STAGE_HELPER = Path(__file__).resolve().parents[2] / "targets" / "_template" / "modules" / STAGE_HELPER_NAME


def _is_link(path: Path) -> bool:
    st = os.lstat(path)
    # Reparse points cover Windows symbolic links and junctions.
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _skip_links(directory: str, names):
    """copytree ignore function: leaves out links and anything that is neither a file nor a folder."""
    skipped = []
    for name in names:
        path = Path(directory) / name
        mode = os.lstat(path).st_mode
        if _is_link(path) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            logger.warning(f"Not staged into the build context (link or special file): {path}")
            skipped.append(name)
    return skipped


def find_dockerfile(target_dir: Path, preferred: str = "Dockerfile") -> Path:
    for name in (preferred, "Dockerfile", "dockerfile"):
        candidate = Path(target_dir) / name
        if candidate.is_file():
            if _is_link(candidate):
                raise ValueError(f"{candidate} is a link; a Dockerfile must be a plain file of the target")
            return candidate
    raise FileNotFoundError(f"Dockerfile missing in target {target_dir}")


def stage_build_context(target_dir: Path, app_root: Path, context_dir: Path,
                        dockerfile_name: str = "Dockerfile") -> Path:
    """Creates context_dir (which must not exist yet or be empty) and returns its Dockerfile path."""
    target_dir, context_dir = Path(target_dir), Path(context_dir)
    source_dockerfile = find_dockerfile(target_dir, dockerfile_name)

    if context_dir.exists() and any(context_dir.iterdir()):
        raise FileExistsError(f"Build context directory is not empty: {context_dir}")
    context_dir.mkdir(parents=True, exist_ok=True)

    for sub in ("modules", "scripts"):
        source = target_dir / sub
        if source.is_dir() and not _is_link(source):
            shutil.copytree(source, context_dir / sub, symlinks=True, ignore=_skip_links)
        else:
            if source.exists():
                logger.warning(f"Not staged into the build context (link or not a folder): {source}")
            (context_dir / sub).mkdir()

    # The framework's stage account helper replaces whatever the target has under that name (a file, a folder or
    # nothing): the stage runner sources modules/stage_user.sh, and only the framework's run_as_stage and
    # stage_preflight may switch the stage modules to the stage account and check the runner's rights.
    helper = context_dir / "modules" / STAGE_HELPER_NAME
    if helper.is_dir() and not helper.is_symlink():
        shutil.rmtree(helper)
    elif os.path.lexists(helper):
        helper.unlink()
    shutil.copy2(FRAMEWORK_STAGE_HELPER, helper)

    # Local sources an offline build may use (Core/build_image.py: the local copy of llama.cpp lands in
    # sources/llama.cpp); the target Dockerfiles COPY sources/, so the folder is there for every build, empty by default.
    (context_dir / "sources").mkdir()
    (context_dir / "sources" / ".keep").write_text("Local sources of an offline build (empty unless one is used).\n",
                                                  encoding="utf-8")
    (context_dir / "docker").mkdir()
    shutil.copy2(Path(app_root) / "entrypoint.sh", context_dir / "docker" / "entrypoint.sh")

    dockerfile = context_dir / "Dockerfile"
    shutil.copy2(source_dockerfile, dockerfile)
    return dockerfile


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("usage: python -m orchestrator.utils.build_context <target_dir> <context_dir> [Dockerfile|Dockerfile.gpu]")
    repo_root = Path(__file__).resolve().parents[2]
    print(stage_build_context(Path(sys.argv[1]), repo_root, Path(sys.argv[2]), *sys.argv[3:]))
