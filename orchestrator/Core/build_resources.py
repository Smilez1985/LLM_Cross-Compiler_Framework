#!/usr/bin/env python3
"""
The check of this computer's disk and memory before a build (DESIGN "Build pipeline and target modules", the row
"Large models").

One verdict over the two estimates that already exist, so nothing is counted twice:
- the disk: the disk rule's needs (disk_plan.build_needs, which now follows the build: a GGUF input needs no conversion
  margin, a kept F16 intermediate of the model neither) checked against the free space, the reserve and what other
  calls reserved (disk_plan.check_disk);
- the memory of the conversion on this computer: disk_plan.host_fit with the hardware probe's RAM.

The board's memory stays the memory estimate's (Core/memory_estimate.py). The verdict is a warning, never a silent
refusal: the person's CLI asks (No by default; --accept-resource-risk without a terminal) and the Builder asks before
it starts; an AI's build keeps the disk rule's refusal (Core/build_plan.py), and an AI never accepts the risk.

Also here, because the plan, the CLI, the Builder and the AI's question all say it the same way: what a GGUF input
does (gguf_input_note: passthrough or a quantization again, with what that costs in quality) and whether a kept F16
intermediate is reused.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ORDER = {"fits": 0, "unknown": 1, "tight": 2, "does_not_fit": 3}
# llama.cpp's file types (general.file_type, llama_ftype in include/llama.h at the pin) as quantization names
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
              26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
              36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE"}
FLOAT_TYPES = ("F32", "F16", "BF16")


def gguf_file_type(path: Path) -> Optional[str]:
    """The quantization type of a GGUF model file from its header (general.file_type), None when it names none or
    cannot be read."""
    from orchestrator.runtime.bundle.generator import BundleError, gguf_header
    try:
        value = gguf_header(Path(path)).get("general.file_type")
    except (BundleError, OSError, ValueError):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return FILE_TYPES.get(value & 0x3FF)   # LLAMA_FTYPE_GUESSED (1024) marks a guessed type


@dataclass
class GgufInput:
    """What a build does with a GGUF file as its input: passthrough (its own type) or quantize (another type), the
    file's type and whether llama-quantize needs --allow-requantize."""
    file_type: Optional[str]
    wanted: str
    mode: str                   # passthrough | quantize
    requantize: bool = False
    upward: bool = False

    def note(self) -> str:
        if self.mode == "passthrough":
            return (f"GGUF input at {self.file_type}: passed through as it is (nothing is quantized; it is checked, "
                    "packaged and tested).")
        if not self.requantize:
            return f"GGUF input at {self.file_type or 'an unknown type'}: quantized to {self.wanted}."
        text = (f"GGUF input at {self.file_type}: quantized again to {self.wanted}. Quantizing a quantized file loses "
                "more than quantizing its 16-bit original; build from the original model when you have it.")
        if self.upward:
            text += f" {self.wanted} has more bits than {self.file_type}: the file gets larger and gains nothing."
        return text

    def as_dict(self) -> Dict[str, Any]:
        return {"file_type": self.file_type, "mode": self.mode, "requantize": self.requantize,
                "upward": self.upward, "note": self.note()}


def gguf_input(path: Path, wanted: Optional[str]) -> GgufInput:
    """The plan for a GGUF input file at the requested type (`wanted`; None or empty: its own type, a passthrough)."""
    from orchestrator.Core.memory_estimate import bits_per_weight
    own = gguf_file_type(path)
    want = str(wanted or "").strip().upper() or (own or "")
    if own and want == own:
        return GgufInput(own, want, "passthrough")
    requant = bool(own) and own not in FLOAT_TYPES
    up = False
    if requant:
        a, b = bits_per_weight(want), bits_per_weight(own)
        up = a is not None and b is not None and a > b
    return GgufInput(own, want, "quantize", requantize=requant, upward=up)


@dataclass
class ResourceCheck:
    """The disk checks (disk_plan.DiskCheck per file system) and the conversion's memory on this computer
    (disk_plan.Fit), with notes (a GGUF input, a reused F16)."""
    disk: List[Any] = field(default_factory=list)
    host: Any = None
    notes: List[str] = field(default_factory=list)
    size_known: bool = True
    f16_reused: bool = False
    f16_kept_bytes: int = 0

    @property
    def disk_state(self) -> str:
        if not self.disk:
            return "unknown"
        states = ["fits" if c.verdict == "enough" else c.verdict for c in self.disk]
        return max(states, key=lambda v: ORDER.get(v, 1))

    @property
    def memory_state(self) -> str:
        return getattr(self.host, "state", "unknown") or "unknown"

    @property
    def state(self) -> str:
        return max((self.disk_state, self.memory_state), key=lambda v: ORDER.get(v, 1))

    @property
    def risky(self) -> bool:
        """A part does not fit: the person decides before the build starts."""
        return "does_not_fit" in (self.disk_state, self.memory_state)

    def lines(self) -> List[str]:
        """The person's lines (the CLI, the Builder): the disk per file system, this computer's memory, the notes."""
        out = []
        if self.disk:
            out += [f"Disk: {c.sentence()}" for c in self.disk]
        else:
            out.append("Disk: unknown (the model's size is not known before the build)")
        if self.host is not None:
            out.append(self.host.sentence("Memory of this computer for the conversion"))
        if self.f16_kept_bytes and self.disk_state == "does_not_fit":
            from orchestrator.Core.disk_plan import gb
            from orchestrator.utils.next_steps import command_line, get, mouse_path
            step = get("build.f16_delete")
            out.append(f"Kept F16 intermediates use {gb(self.f16_kept_bytes)} of the build cache "
                       f"({command_line(step)}; {mouse_path(step.target, 'en')}).")
        return out + list(self.notes)

    def as_dict(self) -> Dict[str, Any]:
        """Numbers and words (an AI may read it): never a path."""
        return {"state": self.state, "disk": self.disk_state, "memory": self.memory_state,
                "disk_checks": [{"where": " and ".join(c.labels), "needed": c.needed, "free": c.free,
                                 "reserve": c.reserve, "verdict": c.verdict} for c in self.disk],
                "memory_needed": getattr(self.host, "needed", None),
                "memory_usable": getattr(self.host, "usable", None),
                "f16_reused": self.f16_reused, "notes": list(self.notes)}


def check(*, size: int, cached: int, fmt: str, quantization: Optional[str], use_imatrix: bool, cache_dir: Path,
          output_dir: Path, parameters: Optional[int], hardware: Any = None, vram: Optional[int] = None,
          use_gpu: bool = False, basis: str = "", gguf: Optional[GgufInput] = None, f16_cached: bool = False,
          f16_kept_bytes: int = 0, ledger: Any = None, disk_usage: Any = None) -> ResourceCheck:
    """The check of one build: `size` the model's bytes (the download or the local file or folder; 0 when unknown),
    `cached` what of it is already staged, `parameters` the parameter count (None: guessed from the size)."""
    from orchestrator.Core import disk_plan
    out = ResourceCheck(f16_reused=f16_cached, f16_kept_bytes=f16_kept_bytes)
    if size:
        needs = disk_plan.build_needs(size, fmt, quantization, cache_dir, output_dir, cached=cached,
                                      gguf_input=gguf is not None, f16_cached=f16_cached,
                                      passthrough=gguf is not None and gguf.mode == "passthrough")
        out.disk = disk_plan.check_disk(needs, ledger, disk_usage=disk_usage)
    else:
        out.size_known = False
    params, why = disk_plan.parameters_from(size, parameters)
    out.host = disk_plan.host_fit(fmt, use_imatrix, params, getattr(hardware, "ram_total", None), vram, use_gpu,
                                  basis or why)
    if gguf is not None:
        out.notes.append(gguf.note())
    if f16_cached:
        out.notes.append("The F16 intermediate of this model is kept from an earlier build: it is reused, nothing is "
                         "converted again.")
    return out


__all__ = ["FILE_TYPES", "FLOAT_TYPES", "GgufInput", "ResourceCheck", "check", "gguf_file_type", "gguf_input"]
