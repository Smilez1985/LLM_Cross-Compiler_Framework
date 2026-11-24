#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: how busy this computer is right now (CPU and memory)

Mojo Oracle samples the CPU load (half a second) and the share of memory in use (utils/validation.validate_cpu_usage
and validate_memory_usage) and compares them with LOAD_CPU_PERCENT and LOAD_MEMORY_PERCENT. A computer above them
is information: a build started now takes longer (and with little free memory a large model's conversion can run out
of it); nothing is refused and nothing is changed, since what else runs is the person's.
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

LOAD_CPU_PERCENT = 80.0      # above this the CPU is busy with something else
LOAD_MEMORY_PERCENT = 85.0   # above this little memory is left for a build
CPU_SAMPLE_SECONDS = 0.5


def check_load(_framework) -> List[Finding]:
    from orchestrator.utils.sizes import format_size
    from orchestrator.utils.validation import ValidationSeverity, validate_cpu_usage, validate_memory_usage
    findings: List[Finding] = []
    cpu = validate_cpu_usage(LOAD_CPU_PERCENT, interval=CPU_SAMPLE_SECONDS)
    percent = cpu.details.get("cpu_percent")
    if cpu.severity is ValidationSeverity.SUCCESS:
        findings.append(_ok("load", "load.cpu", f"CPU load {percent:.0f}% (busy above {LOAD_CPU_PERCENT:.0f}%)"))
    elif percent is not None:
        findings.append(Finding("load", "load.cpu", Severity.INFO,
                                f"This computer's CPU is busy: {percent:.0f}% in use (more than "
                                f"{LOAD_CPU_PERCENT:.0f}%). A build started now takes longer.",
                                "Wait until the other work has finished, or end it, before you start a build."))
    else:
        findings.append(Finding("load", "load.cpu", Severity.INFO, "The CPU load could not be read."))
    memory = validate_memory_usage(LOAD_MEMORY_PERCENT)
    percent = memory.details.get("memory_percent")
    free = memory.details.get("available_bytes")
    free_text = f", {format_size(free)} free" if isinstance(free, int) else ""
    if memory.severity is ValidationSeverity.SUCCESS:
        findings.append(_ok("load", "load.memory", f"Memory {percent:.0f}% in use{free_text} (busy above "
                                                   f"{LOAD_MEMORY_PERCENT:.0f}%)"))
    elif percent is not None:
        findings.append(Finding("load", "load.memory", Severity.INFO,
                                f"This computer's memory is {percent:.0f}% in use (more than "
                                f"{LOAD_MEMORY_PERCENT:.0f}%{free_text}). A build started now takes longer, and the "
                                "conversion of a large model can run out of memory.",
                                "Close programs you do not need before you start a build."))
    else:
        findings.append(Finding("load", "load.memory", Severity.INFO, "The memory in use could not be read."))
    return findings
