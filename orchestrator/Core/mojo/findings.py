#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo's findings and its report

A check returns findings with a stable code (`state.permissions`, `targets.Rockchip`, ...), a severity and a fix hint;
nothing asks a model what is wrong. The report's exit code follows the worst finding: 0 ok, 1 warnings, 2 errors.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Finding:
    subsystem: str
    code: str
    severity: Severity
    message: str
    fix_hint: str = ""
    fix_tier: int = 0  # 0: no automatic fix
    fix: Optional[Callable[[], None]] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict:
        data = asdict(self)
        data.pop("fix")
        data["severity"] = self.severity.value
        data["fixable"] = self.fix is not None
        return data


Check = Callable[[object], List[Finding]]

GB = 1024 ** 3


def _ok(subsystem: str, code: str, message: str) -> Finding:
    return Finding(subsystem, code, Severity.OK, message)


@dataclass
class MojoReport:
    findings: List[Finding]
    fixed: List[str] = field(default_factory=list)
    still_present: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        severities = {f.severity for f in self.findings}
        return 2 if Severity.ERROR in severities else 1 if Severity.WARN in severities else 0

    def to_dict(self) -> Dict:
        return {"exit_code": self.exit_code, "findings": [f.to_dict() for f in self.findings],
                "fixed": self.fixed, "still_present": self.still_present}


DoctorReport = MojoReport  # the name of the Doctor's report (3.0.0)
