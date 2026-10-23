"""hadolint, the Dockerfile linter, run on one Dockerfile; the result in plain words (DECISIONS round 23, item 4).

`run_hadolint` tells four outcomes apart and never raises for any of them:

- "missing": hadolint is not installed on this computer (INSTALL_HINT says how to get it);
- "passed": hadolint read the Dockerfile and found nothing;
- "findings": hadolint found something; each finding is read into its line, rule (DL..., SC...), level (error,
  warning, info, style) and message, and a line hadolint writes in another form (a Dockerfile it cannot parse) is kept
  as it is;
- "failed": hadolint ran but gave no usable result (a crash, a time-out, a file it could not read); `detail` says what
  it wrote.

The build engine (BuildEngine._validate_dockerfile_hadolint) reports these in the build log; the check is advisory.
hadolint runs in the Dockerfile's folder on its file name, so its lines name the file, never a host path. Standard
library only.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

INSTALL_URL = "https://github.com/hadolint/hadolint/releases"
INSTALL_HINT = (f"To have the Dockerfile of each build checked, install hadolint: {INSTALL_URL} (one file for Linux, "
                "macOS and Windows); CONTRIBUTING.md, section 5, shows the same check with Docker.")
RULES_URL = "https://github.com/hadolint/hadolint#rules"
TIMEOUT_SECONDS = 60
MESSAGE_CHARS = 300  # a finding's message is cut there; hadolint's own messages are shorter

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# "Dockerfile:12 DL3008 warning: Pin versions ..." or, for a Dockerfile hadolint cannot parse, "Dockerfile:2:4 ...".
_LINE = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+)(?::\d+)?\s+"
                   r"(?:(?P<rule>[A-Z]{2}\d{4})\s+(?P<level>error|warning|info|style):\s*)?(?P<message>.*)$")
LEVELS = ("error", "warning", "info", "style")


@dataclass(frozen=True)
class HadolintFinding:
    line: Optional[int]
    rule: str      # "DL3008", "SC2086"; "" for a line in another form (a parse error)
    level: str     # one of LEVELS; "" when hadolint gave none
    message: str

    def plain(self) -> str:
        """'line 12, DL3008 (warning): Pin versions ...', without the parts hadolint did not give."""
        head = ", ".join(part for part in (f"line {self.line}" if self.line is not None else "", self.rule) if part)
        head += f" ({self.level})" if self.level else ""
        return f"{head.strip()}: {self.message}" if head else self.message


@dataclass(frozen=True)
class HadolintResult:
    outcome: str                                   # "missing", "passed", "findings" or "failed"
    findings: Tuple[HadolintFinding, ...] = ()
    detail: str = ""                               # "failed": what hadolint wrote (first line), or the time-out

    def counts(self) -> str:
        """'2 warnings, 1 info' for the findings, in the order of LEVELS (findings without a level: 'other')."""
        parts = []
        for level in LEVELS + ("",):
            n = sum(1 for f in self.findings if f.level == level)
            if n:
                name = level or "other"
                parts.append(f"{n} {name}" + ("s" if n > 1 and name in ("error", "warning") else ""))
        return ", ".join(parts)


def _clean(text: str) -> str:
    return _ANSI.sub("", text or "").strip()


def parse_findings(output: str) -> Tuple[HadolintFinding, ...]:
    """hadolint's tty output (the default format), one finding per non-empty line."""
    findings = []
    for raw in (output or "").splitlines():
        text = _clean(raw)
        if not text:
            continue
        match = _LINE.match(text)
        if match:
            message = match.group("message").strip() or "(no message)"
            findings.append(HadolintFinding(int(match.group("line")), match.group("rule") or "",
                                            match.group("level") or "", message[:MESSAGE_CHARS]))
        else:
            findings.append(HadolintFinding(None, "", "", text[:MESSAGE_CHARS]))
    return tuple(findings)


def run_hadolint(dockerfile: Path, timeout: float = TIMEOUT_SECONDS,
                 executable: Optional[str] = None) -> HadolintResult:
    """Runs hadolint on `dockerfile` (in its folder, on its file name). `executable`: the program to run instead of
    the hadolint found on PATH."""
    dockerfile = Path(dockerfile)
    program = executable or shutil.which("hadolint")
    if not program:
        return HadolintResult("missing")
    env = dict(os.environ, NO_COLOR="1")  # hadolint colours its levels even when its output is not a terminal
    try:
        run = subprocess.run([program, dockerfile.name], cwd=str(dockerfile.parent), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return HadolintResult("failed", detail=f"hadolint did not finish within {int(timeout)} s")
    except OSError as e:
        return HadolintResult("failed", detail=f"hadolint could not be started ({e.strerror or e})")
    findings = parse_findings(run.stdout)
    if findings:
        return HadolintResult("findings", findings)
    if run.returncode == 0:
        return HadolintResult("passed")
    said = next((line for line in (_clean(x) for x in (run.stderr or "").splitlines()) if line), "")
    return HadolintResult("failed", detail=f"hadolint ended with exit code {run.returncode}"
                                           + (f": {said[:MESSAGE_CHARS]}" if said else " and wrote nothing"))
