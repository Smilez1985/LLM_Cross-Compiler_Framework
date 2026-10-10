"""Assembles the Docker build context that the target Dockerfiles expect.

Target Dockerfiles COPY `modules/`, `scripts/` and `docker/entrypoint.sh` relative to the
context, so the context is staged from the target directory plus the shared entrypoint.
Standard library only, so CI can stage a context without installing the framework.
"""

import shutil
import sys
from pathlib import Path


def find_dockerfile(target_dir: Path, preferred: str = "Dockerfile") -> Path:
    for name in (preferred, "Dockerfile", "dockerfile"):
        candidate = Path(target_dir) / name
        if candidate.is_file():
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
        if (target_dir / sub).is_dir():
            shutil.copytree(target_dir / sub, context_dir / sub)
        else:
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
