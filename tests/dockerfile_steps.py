"""Reads the instructions of a Dockerfile the way Docker does, to run single RUN steps on the host in tests.

Only what the framework's Dockerfiles use: line continuations with a backslash (Docker drops the backslash and the
line break), comment lines (also inside a continued instruction), ARG with a default (quotes removed) and ENV KEY=VALUE.
A RUN step in shell form is executed as `sh -c <text>` with the environment the test gives, as Docker would run it
with the declared ARG and ENV values.
"""

import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


def instructions(text: str) -> List[Tuple[str, str]]:
    """(INSTRUCTION, arguments) of every instruction, in order."""
    out: List[Tuple[str, str]] = []
    current: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or (not current and not stripped):
            continue
        if line.rstrip().endswith("\\"):
            current.append(line.rstrip()[:-1])
            continue
        current.append(line)
        joined = "".join(current).strip()
        current = []
        word, _, rest = joined.partition(" ")
        out.append((word.upper(), rest.strip()))
    return out


def defaults(text: str) -> Dict[str, str]:
    """ARG defaults and ENV values in file order (a later one wins), with ${NAME} in ENV values not expanded."""
    values: Dict[str, str] = {}
    for word, rest in instructions(text):
        if word == "ARG" and "=" in rest:
            name, _, value = rest.partition("=")
            parts = shlex.split(value) if value else [""]
            values[name.strip()] = parts[0] if parts else ""
        elif word == "ENV":
            for item in shlex.split(rest):
                if "=" in item:
                    name, _, value = item.partition("=")
                    values[name] = value
    return values


def run_steps(text: str) -> List[str]:
    """The shell text of every RUN step (options such as --mount are not used by the framework's Dockerfiles)."""
    return [rest for word, rest in instructions(text) if word == "RUN"]


def find_run(text: str, marker: str) -> str:
    """The one RUN step that contains `marker`."""
    found = [step for step in run_steps(text) if marker in step]
    assert len(found) == 1, f"{len(found)} RUN steps contain {marker!r}"
    return found[0]


def run(step: str, env: Mapping[str, str], cwd: Optional[Path] = None,
        timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", step], env=dict(env), cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, timeout=timeout)
