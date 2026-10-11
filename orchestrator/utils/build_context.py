"""Assembles the Docker build context that the target Dockerfiles expect.

Target Dockerfiles COPY `modules/`, `scripts/` and `docker/entrypoint.sh` relative to the
context, so the context is staged from the target directory plus the shared entrypoint.
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
