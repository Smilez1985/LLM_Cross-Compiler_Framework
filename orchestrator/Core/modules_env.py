#!/usr/bin/env python3
"""
The values a module keeps private to this computer (docs/DESIGN.md, the wizard's local layer for private values).

A module's shell fields (the setup commands, the quantization logic) hold a variable such as ${LOCAL_SDK_DIR}, never the
value; the value lives here, in `modules_env/<target>.env` of the person's state folder, mode 0600, and never in a
module folder, so no export, pack or snapshot of the module can carry it. A build copies the file into its own temporary
folder and mounts it read-only into the stage container, named by LOCAL_ENV_FILE (Core/builder.py, `_local_values_mount`,
as the board's probe is); the stage runner (runtime_layer/runner.sh) reads the file line by line, without eval, and
exports the variables to the stages. Nothing is merged into a module's text and nothing is baked into an image layer
(the module is copied into the image, and its layers are cached and can be exported).

File format: one `LOCAL_NAME=value` per line, the value taken as it stands to the end of the line (no quotes, no
escapes), so it holds no line break. Blank lines and lines that start with # are comments.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from orchestrator.utils import atomic
from orchestrator.utils.modes import SHARED_READ

FOLDER = "modules_env"
FILE_SUFFIX = ".env"
STAGED_NAME = "local.env"
MOUNT = "/build-cache/local"   # where a build container finds the file (read-only)
ENV_NAME = "LOCAL_ENV_FILE"    # names the file inside the container
NAME_RE = re.compile(r"^LOCAL_[A-Z][A-Z0-9_]{0,63}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_VALUE = 4096
MAX_VALUES = 256


class LocalValuesError(ValueError):
    """A name, a value or a target that the file cannot hold; the message says which and why."""


def folder(state_dir: Any) -> Path:
    return Path(state_dir) / FOLDER


def path_for(state_dir: Any, target: str) -> Path:
    """The file of a target. The target id is a folder name of the targets folder; anything else (a path, a dot
    segment) is refused, so the file is always directly in modules_env/."""
    name = str(target or "")
    if not TARGET_RE.fullmatch(name) or name in (".", "..") or ".." in name:
        raise LocalValuesError(f"'{name}' is no target id: letters, digits, '.', '_' and '-' only.")
    return folder(state_dir) / f"{name}{FILE_SUFFIX}"


def check(name: str, value: str) -> None:
    if not NAME_RE.fullmatch(str(name)):
        raise LocalValuesError(f"'{name}' is no name of a local value: LOCAL_ and capital letters, digits or '_' "
                               "(for example LOCAL_SDK_DIR).")
    text = str(value)
    if "\n" in text or "\r" in text or "\0" in text:
        raise LocalValuesError(f"The value of {name} holds a line break or a NUL character; the file takes one line "
                               "per value.")
    if len(text) > MAX_VALUE:
        raise LocalValuesError(f"The value of {name} is longer than {MAX_VALUE} characters.")


def parse(text: str) -> Dict[str, str]:
    """The values of a file's text; a line that is no `LOCAL_NAME=value` is skipped (the runner skips it too)."""
    values: Dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if NAME_RE.fullmatch(name):
            values[name] = value
    return values


def read(state_dir: Any, target: str) -> Dict[str, str]:
    try:
        return parse(atomic.read_text(path_for(state_dir, target)))
    except FileNotFoundError:
        return {}


def write(state_dir: Any, target: str, values: Mapping[str, str]) -> Path:
    """The whole file of a target (mode 0600, atomic); an empty mapping removes it."""
    if len(values) > MAX_VALUES:
        raise LocalValuesError(f"A target holds at most {MAX_VALUES} local values.")
    for name, value in values.items():
        check(name, value)
    path = path_for(state_dir, target)
    if not values:
        remove(state_dir, target)
        return path
    lines = ["# Private to this computer: never in a module folder, a pack or the repository.",
             *(f"{name}={values[name]}" for name in sorted(values))]
    atomic.write_private(path, "\n".join(lines) + "\n")
    return path


def set_value(state_dir: Any, target: str, name: str, value: str) -> Path:
    values = read(state_dir, target)
    values[name] = value
    return write(state_dir, target, values)


def remove(state_dir: Any, target: str) -> bool:
    path = path_for(state_dir, target)
    if not path.is_file():
        return False
    atomic.remove_file(path)
    return True


def names(state_dir: Any, target: str) -> list:
    """The names a target has values for (never the values: they are for the build)."""
    return sorted(read(state_dir, target))


def stage(state_dir: Any, target: str, build_temp: Any) -> Optional[Path]:
    """A build's copy of the target's file in the build's own folder, readable for the container's user through a
    read-only mount (like the probe's); None when the target has no values. The copy goes with the build's folder."""
    source = path_for(state_dir, target)
    if not source.is_file():
        return None
    destination = Path(build_temp) / "local"
    destination.mkdir(parents=True, exist_ok=True)
    copy = destination / STAGED_NAME
    shutil.copyfile(source, copy)
    if os.name != "nt":
        os.chmod(copy, SHARED_READ)
        os.chmod(destination, 0o755)
    return copy
