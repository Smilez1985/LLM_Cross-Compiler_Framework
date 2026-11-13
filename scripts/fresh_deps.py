#!/usr/bin/env python3
"""The newest compatible dependencies, resolved fresh (DESIGN round 65).

pyproject.toml gives each dependency a range (from the tested version up to the next major) and a 5-day cooldown
([tool.uv] exclude-newer = "5 days"); uv.lock is the tested set. Every installer run (install.sh, install.bat) and the
scheduled update run (.github/workflows/deps-update.yml) resolve the newest versions inside those ranges again, audit
them and check them; whatever fails falls back to the tested lock.

Commands (standard library only; run with the Python the installer found, Python 3.11 or newer):

  prune-exceptions --project DIR
      Drops each [tool.uv.exclude-newer-package] entry of DIR/pyproject.toml whose date the cooldown has passed (it
      would only hold the package back now). Prints the names it dropped.

  resolve --project DIR --work DIR --out FILE [--extra gui] [--python PYTHON]
      In DIR_work: a tool environment from DIR/requirements-resolve.txt (uv and pip-audit, hash-pinned), a copy of
      pyproject.toml and uv.lock, `uv lock --upgrade` there (cooldown included), the export with hashes into FILE (the
      same `uv export` as requirements.txt / requirements-gui.txt) and pip-audit on it, as CI audits.
      Exit codes: 0 FILE is the fresh, audited set; 3 no fresh set (offline, LCF_OFFLINE=1, the index unreachable or
      no resolution); 4 pip-audit found a known vulnerability; 5 pip-audit could not run. The installers install the
      shipped tested export on any code but 0 and say why.

  audit --requirements FILE --pip-audit PATH [--cache-dir DIR]
      pip-audit over an exported requirements file the way .github/workflows/security.yml does it (markers dropped so
      every platform's pin is audited; a name pinned twice goes to a second file). Exit 0 clean, 4 finding, 5 could
      not run.

  selfcheck --root DIR [--gui]
      Run with the new environment's Python: pip check, every direct dependency imports, the framework's core modules
      import and `python -m orchestrator.cli --help` works. Exit 0 or 1 (the failing step is printed). On Python
      before 3.11.5 pip check may report exactly one line: Soup Sieve missing for Beautiful Soup (KNOWN_MISSING).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

OK, ERROR, NO_FRESH_SET, AUDIT_FINDING, AUDIT_UNAVAILABLE = 0, 2, 3, 4, 5

# The import names of the direct dependencies (pyproject.toml [project] dependencies) and the framework's core modules
# the self-check imports. qdrant_edge has no wheel for Windows on ARM (pyproject marker).
DEPENDENCY_IMPORTS = ["requests", "psutil", "yaml", "packaging", "docker", "click", "rich", "cryptography", "keyring",
                      "filelock", "pyrage", "anthropic", "openai", "fastembed", "qdrant_client", "huggingface_hub",
                      "pydantic", "mcp", "acp", "bs4", "pypdf", "langdetect", "paramiko", "nacl", "aiomqtt",
                      "paho.mqtt.client"]
CORE_IMPORTS = ["orchestrator.cli", "orchestrator.Core.framework", "orchestrator.Core.crawler_manager",
                "orchestrator.Core.rag_manager", "orchestrator.Core.doorbell", "orchestrator.remote.pair_pake"]
# The GUI's Qt (only QtCore: QtWidgets needs the display libraries, which a build machine may lack).
GUI_IMPORTS = ["PySide6.QtCore"]

TIMEOUT = 900   # seconds for one tool step (a slow index, a large wheel)

# The one dependency pip check may report missing, and only on Python before 3.11.5: Beautiful Soup declares Soup Sieve,
# but imports it only for CSS selectors, which llmcf never runs; pyproject.toml's override-dependencies leaves it out
# there because every Soup Sieve that Python can install has CVE-2026-126190 (docs/DESIGN.md, Security model).
KNOWN_MISSING = re.compile(r"^beautifulsoup4 \S+ requires soupsieve, which is not installed\.$")


def python_version() -> tuple:
    """The running Python as (major, minor, micro): the self-check runs with the new environment's Python."""
    return tuple(sys.version_info[:3])


def pip_check_passes(result: subprocess.CompletedProcess, version=None) -> bool:
    """pip check's verdict, with the one known missing dependency allowed on Python before 3.11.5."""
    if result.returncode == 0:
        return True
    if tuple(version or python_version()) >= (3, 11, 5):
        return False
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    return bool(lines) and all(KNOWN_MISSING.match(line) for line in lines)


def say(text: str) -> None:
    print(text, flush=True)


def utc_now() -> datetime:
    """Now in UTC; LCF_DEPS_NOW (RFC 3339) fixes it for tests."""
    fixed = os.environ.get("LCF_DEPS_NOW")
    if fixed:
        return datetime.fromisoformat(fixed.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def cooldown_days(pyproject: dict) -> int:
    """The cooldown from [tool.uv] exclude-newer ("5 days"); 5 when it is missing or not in days."""
    value = str(pyproject.get("tool", {}).get("uv", {}).get("exclude-newer", "5 days"))
    match = re.fullmatch(r"\s*(\d+)\s*days?\s*", value)
    return int(match.group(1)) if match else 5


def cutoff(pyproject: dict) -> datetime:
    """now minus the cooldown, in UTC: what uv computes from exclude-newer = "5 days"."""
    return utc_now() - timedelta(days=cooldown_days(pyproject))


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prune_exceptions(project: Path) -> list[str]:
    """Drops the [tool.uv.exclude-newer-package] entries whose date is at or before the cutoff. The table is edited
    line by line (it holds only `name = "date"` lines and comments), so every other line stays as it is."""
    path = project / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    limit = cutoff(tomllib.loads(text))
    lines, out, dropped, inside = text.splitlines(keepends=True), [], [], False
    for line in lines:
        header = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if header:
            inside = header.group(1).strip() == "tool.uv.exclude-newer-package"
        entry = re.match(r'\s*([A-Za-z0-9._-]+)\s*=\s*"([^"]+)"\s*$', line) if inside else None
        if entry:
            try:
                stamp = datetime.fromisoformat(entry.group(2).replace("Z", "+00:00"))
            except ValueError:
                stamp = None
            if stamp is not None and stamp <= limit:
                dropped.append(entry.group(1))
                continue
        out.append(line)
    if dropped:
        path.write_text("".join(out), encoding="utf-8")
    return dropped


def tool_path(venv: Path, name: str) -> Path:
    folder = venv / ("Scripts" if os.name == "nt" else "bin")
    return folder / (name + (".exe" if os.name == "nt" else ""))


def run(argv: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=TIMEOUT, **kwargs)


def last_line(result: subprocess.CompletedProcess) -> str:
    lines = [line.strip() for line in (result.stderr + "\n" + result.stdout).splitlines() if line.strip()]
    errors = [line for line in lines if line.lower().startswith(("error", "×", "x "))]
    return (errors or lines or ["no output"])[-1][:300]


def pins(requirements: Path) -> dict:
    """{name: [versions]} of an exported requirements file."""
    found: dict = {}
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
            if match:
                found.setdefault(re.sub(r"[-_.]+", "-", match.group(1)).lower(), []).append(match.group(2))
    return found


def audit(requirements: Path, pip_audit: Path, cache_dir: Path | None = None) -> int:
    """pip-audit as .github/workflows/security.yml runs it over the export."""
    work = requirements.parent
    main, dups, seen = [], [], set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^[A-Za-z0-9]", line):
            continue
        pin = re.sub(r"\s*\\$", "", re.sub(r"\s*;.*$", "", line))
        name = pin.split("==", 1)[0]
        (dups if name in seen else main).append(pin)
        seen.add(name)
    files = []
    for name, entries in (("audit-main.txt", main), ("audit-dups.txt", dups)):
        if entries:
            (work / name).write_text("\n".join(entries) + "\n", encoding="utf-8")
            files.append(work / name)
    extra = ["--cache-dir", cache_dir] if cache_dir else []
    for path in files:
        try:
            result = run([pip_audit, "--requirement", path, "--no-deps", "--disable-pip", "--desc", "on",
                          "--progress-spinner", "off", *extra])
        except (OSError, subprocess.SubprocessError) as error:
            say(f"pip-audit could not run: {error}")
            return AUDIT_UNAVAILABLE
        if result.returncode != 0:
            text = result.stdout + result.stderr
            if re.search(r"Found \d+ known vulnerabilit", text):
                say(text.strip()[-2000:])
                return AUDIT_FINDING
            say(f"pip-audit could not run: {last_line(result)}")
            return AUDIT_UNAVAILABLE
    return OK


def uv_environment(work: Path) -> dict:
    env = dict(os.environ)
    env.update({"UV_PYTHON_DOWNLOADS": "never", "UV_CACHE_DIR": str(work / "uv-cache"),
                "UV_NO_PROGRESS": "1"})
    # The cooldown and the index come from pyproject.toml (not --no-config: that would skip [tool.uv] too).
    for name in ("UV_EXCLUDE_NEWER", "UV_FROZEN", "UV_LOCKED", "UV_OFFLINE", "UV_NO_CONFIG"):
        env.pop(name, None)
    # A mirror the person set for pip (PIP_INDEX_URL) is the index for uv too.
    if os.environ.get("PIP_INDEX_URL") and not os.environ.get("UV_DEFAULT_INDEX"):
        env["UV_DEFAULT_INDEX"] = os.environ["PIP_INDEX_URL"]
    return env


def resolve(project: Path, work: Path, out: Path, extra: str | None, python: str) -> int:
    if os.environ.get("LCF_OFFLINE") == "1":
        say("offline (LCF_OFFLINE=1): no fresh resolve")
        return NO_FRESH_SET
    work.mkdir(parents=True, exist_ok=True)
    tools, copy = work / "tools", work / "project"
    # 1. uv and pip-audit, each file checked against its hash (requirements-resolve.txt, the export of the group
    # "resolve" of uv.lock); without the index there is no fresh set.
    try:
        made = run([python, "-m", "venv", tools])
        if made.returncode == 0:
            made = run([tool_path(tools, "python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--require-hashes", "--no-deps", "-r", project / "requirements-resolve.txt"])
    except (OSError, subprocess.SubprocessError) as error:
        say(f"tools for the fresh resolve: {error}")
        return NO_FRESH_SET
    if made.returncode != 0:
        say(f"tools for the fresh resolve (uv, pip-audit) not installed: {last_line(made)}")
        return NO_FRESH_SET
    # 2. the newest compatible set: uv lock --upgrade in a copy (the cooldown comes from pyproject.toml)
    copy.mkdir(exist_ok=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(project / name, copy / name)
    dropped = prune_exceptions(copy)
    if dropped:
        say("cooldown passed, exception dropped: " + ", ".join(dropped))
    env, uv = uv_environment(work), tool_path(tools, "uv")
    try:
        locked = run([uv, "lock", "--upgrade", "--python", python], cwd=copy, env=env)
        if locked.returncode == 0:
            scope = ["--no-dev"] + (["--extra", extra] if extra else [])
            locked = run([uv, "export", "--frozen", "--format", "requirements-txt", *scope, "--no-emit-project",
                          "-o", out.resolve()], cwd=copy, env=env)
    except (OSError, subprocess.SubprocessError) as error:
        say(f"uv: {error}")
        return NO_FRESH_SET
    if locked.returncode != 0 or not out.is_file():
        say(f"uv lock --upgrade: {last_line(locked)}")
        return NO_FRESH_SET
    say(f"resolved with the cooldown up to {rfc3339(cutoff(tomllib.loads((copy / 'pyproject.toml').read_text('utf-8'))))}")
    # 3. pip-audit, as in CI
    code = audit(out, tool_path(tools, "pip-audit"), work / "audit-cache")
    if code != OK:
        return code
    tested = pins(project / ("requirements-gui.txt" if extra == "gui" else "requirements.txt"))
    changed = [f"{name} {'/'.join(tested.get(name, ['-']))} -> {'/'.join(versions)}"
               for name, versions in sorted(pins(out).items()) if sorted(tested.get(name, [])) != sorted(versions)]
    say(f"newer than the tested lock: {len(changed)}")
    for line in changed:
        say(f"  {line}")
    return OK


def has_qdrant_edge() -> bool:
    """Qdrant Edge has no wheel for Windows on ARM (the marker in pyproject.toml)."""
    return not (sys.platform == "win32" and platform.machine().upper() == "ARM64")


def selfcheck(root: Path, gui: bool) -> int:
    imports = list(DEPENDENCY_IMPORTS)
    if has_qdrant_edge():
        imports.append("qdrant_edge")
    imports += GUI_IMPORTS if gui else []
    imports += CORE_IMPORTS
    script = ("import importlib, sys\n"
              f"for name in {imports!r}:\n"
              "    try:\n"
              "        importlib.import_module(name)\n"
              "    except Exception as error:\n"
              "        sys.exit(f'import {name}: {type(error).__name__}: {error}')\n")
    steps = [("pip check", [sys.executable, "-m", "pip", "check", "--disable-pip-version-check"]),
             ("imports", [sys.executable, "-c", script]),
             ("llm-cli --help", [sys.executable, "-m", "orchestrator.cli", "--help"])]
    for label, argv in steps:
        try:
            result = run(argv, cwd=root)
        except (OSError, subprocess.SubprocessError) as error:
            say(f"self-check failed: {label}: {error}")
            return 1
        if not (pip_check_passes(result) if label == "pip check" else result.returncode == 0):
            say(f"self-check failed: {label}: {last_line(result)}")
            return 1
        if result.returncode != 0:
            say("pip check: Soup Sieve is left out on Python before 3.11.5 (CSS selectors only; CVE-2026-126190)")
    say("self-check passed")
    return OK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prune-exceptions")
    p.add_argument("--project", type=Path, default=Path("."))
    p = sub.add_parser("resolve")
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--extra", choices=["gui"])
    p.add_argument("--python", default=sys.executable)
    p = sub.add_parser("audit")
    p.add_argument("--requirements", type=Path, required=True)
    p.add_argument("--pip-audit", type=Path, default=Path("pip-audit"))
    p.add_argument("--cache-dir", type=Path)
    p = sub.add_parser("selfcheck")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--gui", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prune-exceptions":
            for name in prune_exceptions(args.project):
                say(name)
            return OK
        if args.command == "resolve":
            return resolve(args.project.resolve(), args.work.resolve(), args.out, args.extra, args.python)
        if args.command == "audit":
            return audit(args.requirements.resolve(), args.pip_audit, args.cache_dir)
        return selfcheck(args.root.resolve(), args.gui)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        say(f"{args.command}: {error}")
        return NO_FRESH_SET if args.command == "resolve" else ERROR


if __name__ == "__main__":
    sys.exit(main())
