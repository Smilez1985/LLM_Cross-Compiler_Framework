#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo's findings and its report

A check returns findings with a stable code (`state.permissions`, `targets.Rockchip`, ...), a severity and a fix hint;
nothing asks a model what is wrong. Every warning and error also says where (the full path or object), why it matters
and what Mojo Miracle can do about it. The report's exit code follows the worst finding: 0 ok, 1 warnings, 2 errors.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


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
    # Mojo (round 7 items 11' and T): the full path or object the finding is about (the person sees it, an AI reads its
    # label), why it matters, and the Mojo Miracle repair (repairs.REPAIRS key) or why there is none (offers.REASONS:
    # deletion_list, person, admin, not_automatic). Every warning and error says both.
    where: str = ""
    why: str = ""
    repair_id: str = ""
    cannot: str = ""
    # W1: a recorded fact points to a problem with this trusted board (its device ID), and Mojo did not contact it: the
    # person is offered a check of that board ("Check <board> now?"), an AI the request it can make. `board_name` is
    # the name to give for it (its label, or its device ID when another board has the same label; never an address).
    board_check: str = ""
    board_name: str = ""
    # Wave 6 (chain a0): what to do, as next steps of the catalogue (orchestrator/utils/next_steps), so the Mojo window
    # names the place in the person's language with [Open] (and an allow-to-run proposal where the step allows one),
    # while `fix_hint` stays the command line's text (`llmcf mojo` and --json keep their output). NextStep or step ids.
    steps: Tuple[Any, ...] = ()

    def to_dict(self) -> Dict:
        data = asdict(self)
        data.pop("fix")
        data["severity"] = self.severity.value
        data["fixable"] = self.fix is not None
        data["steps"] = [getattr(s, "id", s) for s in self.steps if isinstance(getattr(s, "id", s), str)]
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
