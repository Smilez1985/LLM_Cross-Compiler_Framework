"""Assembles the Docker build context that the target Dockerfiles expect.

Target Dockerfiles COPY `modules/`, `scripts/`, `sources/` and `docker/entrypoint.sh` relative to
the context, so the context is staged from the target directory plus the shared entrypoint and an
empty `sources/` folder (an offline build puts its local copy of llama.cpp there).
`modules/stage_user.sh` is always the framework's own (FRAMEWORK_STAGE_HELPER), whatever the target
ships under that name. Deprecated: a build no longer reads it at all, since the stage runner and its
stage account helper are the framework's runtime layer (orchestrator/runtime_layer, mounted read-only
at /llmcf into every build container, orchestrator/Core/runtime_layer.py); the copy stays only for an
image whose own scripts still source it by hand, and it is the layer's one helper.
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

# The one source of the stage account's helper: the runtime layer's (orchestrator/runtime_layer/stage_user.sh).
# Deprecated as a part of the build context: the runner sources the layer's copy at /llmcf, never the image's.
STAGE_HELPER_NAME = "stage_user.sh"
FRAMEWORK_STAGE_HELPER = Path(__file__).resolve().parents[1] / "runtime_layer" / STAGE_HELPER_NAME


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
            ignore = _skip_links
            if sub == "modules":   # never the target's own stage account helper: the framework's comes below
                def ignore(directory, names, top=str(source)):
                    skipped = _skip_links(directory, names)
                    if os.path.samefile(directory, top) and STAGE_HELPER_NAME in names:
                        skipped.append(STAGE_HELPER_NAME)
                    return skipped
            shutil.copytree(source, context_dir / sub, symlinks=True, ignore=ignore)
        else:
            if source.exists():
                logger.warning(f"Not staged into the build context (link or not a folder): {source}")
            (context_dir / sub).mkdir()

    # The framework's stage account helper in place of whatever the target has under that name (a file, a folder or
    # nothing; the target's is never copied). Deprecated: the runtime layer's runner sources the layer's own copy.
    shutil.copy2(FRAMEWORK_STAGE_HELPER, context_dir / "modules" / STAGE_HELPER_NAME)

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
