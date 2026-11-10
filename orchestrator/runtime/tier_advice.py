#!/usr/bin/env python3
"""
Whether a tier of Ditto's local model is offered on this computer (DESIGN "Ditto, the local runtime and the LLM layer",
the rows "Start thresholds for Ditto's local model" and "A 2-bit (Q2) model must first prove ...").

- verdict(): the start thresholds of Core/tuning.py (SPEED_FLOOR tokens per second, KLD_GOOD and KLD_FLOOR): passed
  (at least the speed floor and a KLD up to KLD_GOOD), usable (up to KLD_FLOOR), failed (anything else).
- TierChecks: the record `<state dir>/evals/tier_checks.json`, the newest measurement of each tier with its
  fingerprint (the file's sha256, this computer, the llama.cpp commit and backend). Only the measurement stage
  (evals/tier_check.py) writes it; nothing an AI can call does.
- Fingerprint / current_fingerprint(): what a record must match to count; another computer, file, pin or backend makes
  it stale, and stale counts as not measured.
- advise(): the one advice every surface shows (the CLI, the windows, get_status, the MCP prompt): the tier rule's pick
  (hardware.choose_model_tier) with the gate for the tiers the catalogue marks `needs_eval` (the 2-bit tiers):
  recommended with a passed record, usable with a usable one, a candidate to evaluate without a matching record, failed
  with a failed one, said with its numbers.

Reads only; numbers and words, never a path.
"""

import hashlib
import json
import os
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "llm-framework.tier-check/1"
RECORD_FILE = "tier_checks.json"
VERDICTS = ("passed", "usable", "failed")
STATES = ("recommended", "usable", "candidate", "failed")
GIB = 1024 ** 3


def measure_command(tier: str) -> str:
    """The CLI's action for a tier to evaluate, from the next-step catalogue (the step models.evaluate; the GUI's is
    "Evaluate first..." in the Local Models window)."""
    from orchestrator.utils.next_steps import command_line, get
    return command_line(get("models.evaluate"), {"tier": tier})


# --- the verdict -----------------------------------------------------------------------------------------------------

def verdict(tokens_s: Optional[float], kld: Optional[float]) -> str:
    """passed, usable or failed by the start thresholds (Core/tuning.py: SPEED_FLOOR, KLD_GOOD, KLD_FLOOR)."""
    from orchestrator.Core.tuning import SPEED_FLOOR, quality_verdict
    quality = quality_verdict(kld)
    if tokens_s is None or float(tokens_s) < SPEED_FLOOR or quality not in ("good", "usable"):
        return "failed"
    return "passed" if quality == "good" else "usable"


def numbers_text(record: Dict[str, Any]) -> str:
    """'9.4 tokens/s, KLD 0.0312 ± 0.0011, top-1 91.0 %' (what was measured)."""
    parts = []
    if record.get("tokens_s") is not None:
        parts.append(f"{float(record['tokens_s']):.1f} tokens/s")
    if record.get("kld") is not None:
        unc = record.get("kld_unc")
        parts.append(f"KLD {float(record['kld']):.4f}" + (f" ± {float(unc):.4f}" if unc is not None else ""))
    if record.get("top1") is not None:
        parts.append(f"top-1 {float(record['top1']):.1f} %")
    return ", ".join(parts) or "nothing measured"


def failure_reasons(record: Dict[str, Any]) -> List[str]:
    """Why a record failed, each with the number and the threshold."""
    from orchestrator.Core.tuning import KLD_FLOOR, SPEED_FLOOR
    out = []
    speed, kld = record.get("tokens_s"), record.get("kld")
    if speed is None:
        out.append("its speed could not be measured")
    elif float(speed) < SPEED_FLOOR:
        out.append(f"{float(speed):.1f} tokens/s, at least {SPEED_FLOOR:g} needed")
    if kld is None:
        out.append("its KLD could not be measured")
    elif float(kld) > KLD_FLOOR:
        out.append(f"KLD {float(kld):.4f}, at most {KLD_FLOOR:.2f}")
    return out


# --- the fingerprint -------------------------------------------------------------------------------------------------

def cpu_name() -> str:
    """The CPU's model name (Linux: /proc/cpuinfo; elsewhere platform.processor()), "" when unknown."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _, value = line.partition(":")
                if key.strip() in ("model name", "Hardware", "Model"):
                    return value.strip()[:120]
    except OSError:
        pass
    return (platform.processor() or "")[:120]


def computer_id(hardware: Any, cpu: Optional[str] = None) -> str:
    """A hash of this computer as the measurement sees it: OS, architecture, CPU and its threads, RAM (to a tenth of
    a GiB) and the GPUs (vendor, name, memory). Only the hash is kept, never the names."""
    facts = {"os": str(getattr(hardware, "os", "")), "arch": str(getattr(hardware, "arch", "")),
             "cpu": cpu_name() if cpu is None else cpu, "threads": os.cpu_count() or 0,
             "ram_gib": round(int(getattr(hardware, "ram_total", 0) or 0) / GIB, 1),
             "gpus": sorted([str(getattr(g, "vendor", "")), str(getattr(g, "name", "")), int(getattr(g, "vram_mib", 0)
                                                                                            or 0)]
                            for g in getattr(hardware, "gpus", None) or [])}
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Fingerprint:
    """What a record must match: the tier's file (its sha256: model and quantization), this computer, the llama.cpp
    commit and the backend of the build that measures (None: not known, not compared)."""
    sha256: str
    computer: str
    llama_cpp: str
    backend: Optional[str] = None

    def matches(self, record: Dict[str, Any]) -> bool:
        if not isinstance(record, dict) or record.get("sha256") != self.sha256 \
                or record.get("computer") != self.computer or record.get("llama_cpp") != self.llama_cpp:
            return False
        return self.backend is None or record.get("backend") == self.backend

    def as_dict(self) -> Dict[str, Any]:
        return {"sha256": self.sha256, "computer": self.computer, "llama_cpp": self.llama_cpp,
                "backend": self.backend}


def active_backend(rt: Any) -> Optional[str]:
    """The backend of the build the measurement would use first (the store's active build), read from its manifest;
    None when there is none or it cannot be read."""
    try:
        store = rt.store
        active = store.references().get("active")
        build = store.get(active) if active else None
        return str(build.backend) if build is not None else None
    except Exception:  # noqa: BLE001 - no build store yet: the backend is not compared
        return None


def current_fingerprint(rt: Any, model: Any, *, backend: Optional[str] = None) -> Fingerprint:
    """The fingerprint a record of `model` must have on this computer now (`rt`: a LocalLLMRuntime)."""
    pin = getattr(rt.catalog.llama_cpp, "source", None)
    commit = getattr(pin, "commit", None) or str(rt.catalog.llama_cpp.build)
    return Fingerprint(sha256=str(model.sha256 or ""), computer=computer_id(rt.hardware()), llama_cpp=str(commit),
                       backend=backend if backend is not None else active_backend(rt))


# --- the record ------------------------------------------------------------------------------------------------------

class TierChecks:
    """`<state dir>/evals/tier_checks.json`: {"schema", "tiers": {tier: record}}, the newest record of each tier."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "evals" / RECORD_FILE

    def load(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("tiers"), dict):
            return {}
        return {str(k): v for k, v in data["tiers"].items() if isinstance(v, dict)}

    def get(self, tier: str) -> Optional[Dict[str, Any]]:
        return self.load().get(tier)

    def lookup(self, tier: str, fingerprint: Fingerprint) -> "RecordView":
        record = self.get(tier)
        if record is None:
            return RecordView(None, stale=False)
        return RecordView(record, stale=not fingerprint.matches(record))

    def save(self, record: Dict[str, Any]) -> Path:
        """Keeps `record` as its tier's newest (owner-only where the system has modes). The measurement stage is the
        only caller."""
        if record.get("verdict") not in VERDICTS or not record.get("tier"):
            raise ValueError("a tier check needs its tier and a verdict")
        tiers = self.load()
        tiers[str(record["tier"])] = dict(record)
        from orchestrator.utils.atomic import write_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_file(self.path, json.dumps({"schema": SCHEMA, "tiers": tiers}, indent=2, sort_keys=True) + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.path


@dataclass
class RecordView:
    """A tier's record and whether it is stale (measured on another computer, file, pin or backend)."""
    record: Optional[Dict[str, Any]]
    stale: bool = False

    @property
    def valid(self) -> Optional[Dict[str, Any]]:
        return self.record if self.record is not None and not self.stale else None

    @property
    def verdict(self) -> Optional[str]:
        valid = self.valid
        return valid.get("verdict") if valid else None


def make_record(*, tier: str, model: Any, fingerprint: Fingerprint, reference: str, chunks: int, ctx: int,
                text_sha256: str, tokens_s: Optional[float], stats: Dict[str, Any], build_id: str = "",
                prompt_ts: Optional[float] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The record of one measurement: the fingerprint, how it was measured, the numbers and the verdict."""
    record: Dict[str, Any] = {
        "schema": SCHEMA, "tier": tier, "file": str(model.file), **fingerprint.as_dict(), "build_id": build_id,
        "reference": reference, "chunks": int(chunks), "ctx": int(ctx), "text_sha256": text_sha256,
        "tokens_s": None if tokens_s is None else round(float(tokens_s), 2),
        "prompt_ts": None if prompt_ts is None else round(float(prompt_ts), 2),
        "at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for key in ("kld", "kld_unc", "top1", "top1_unc", "ppl_ratio", "chunks_measured"):
        if stats.get(key) is not None:
            record[key] = stats[key]
    record["verdict"] = verdict(record.get("tokens_s"), record.get("kld"))
    return record


# --- the advice ------------------------------------------------------------------------------------------------------

@dataclass
class TierAdvice:
    """The tier rule's pick for this computer and what the gate makes of it.

    state: recommended (offered), usable (offered with that word), candidate (a marked tier without a matching
    record: to evaluate first), failed (a marked tier whose record failed). `rule_why` is the tier rule's reason,
    `record` this computer's record of the tier (None without one), `stale` whether an older record no longer
    matches."""
    model: Any
    rule_why: str
    state: str = "recommended"
    record: Optional[Dict[str, Any]] = None
    stale: bool = False
    gated: bool = False

    @property
    def offered(self) -> bool:
        """Whether a plain start (no tier named) may load it."""
        return self.state in ("recommended", "usable")

    @property
    def action(self) -> Optional[str]:
        return measure_command(self.model.tier) if self.state in ("candidate", "failed") else None

    def text(self) -> str:
        """The advice in one English paragraph (`why` of choose_tier, every surface's line)."""
        tier = self.model.tier
        if self.state == "recommended":
            if self.gated and self.record:
                return f"{self.rule_why}; passed the measurement on this computer ({numbers_text(self.record)})"
            return self.rule_why
        if self.state == "usable":
            return (f"{self.rule_why}; usable, not good: measured on this computer {numbers_text(self.record or {})} "
                    "(good up to KLD 0.05, usable up to 0.10)")
        if self.state == "failed":
            return (f"{tier} failed the measurement on this computer: {'; '.join(failure_reasons(self.record or {}))}. "
                    "It is not offered to Ditto; use a cloud provider, or choose a tier by name. "
                    f"(Tier rule: {self.rule_why}.)")
        stale = (" Its earlier measurement was made on another computer, with another file or llama.cpp build, and "
                 "no longer counts." if self.stale else "")
        return (f"{tier} is only a candidate to evaluate: a 2-bit model is offered to Ditto only after it passed the "
                f"measurement on this computer. Evaluate it first: {measure_command(tier)}. Until then use a cloud "
                f"provider, or choose a tier by name.{stale} (Tier rule: {self.rule_why}.)")

    def facts(self) -> Dict[str, Any]:
        """Numbers and words for an AI caller, never a path."""
        out: Dict[str, Any] = {"tier": self.model.tier, "state": self.state, "needs_evaluation": self.gated}
        if self.record:
            out["measured"] = {k: self.record.get(k) for k in ("verdict", "tokens_s", "kld", "kld_unc", "top1",
                                                                 "reference", "at") if self.record.get(k) is not None}
            out["measured"]["stale"] = self.stale
        return out


def tier_state(model: Any, view: RecordView) -> Optional[str]:
    """A marked tier's state from its record (None for a tier without the mark): passed, usable, failed, or None when
    it has no matching record."""
    if not getattr(model, "needs_eval", False):
        return None
    return view.verdict


def advise(model: Any, rule_why: str, view: RecordView) -> TierAdvice:
    """The gate on the tier rule's pick `model` with this computer's record view of it."""
    if not getattr(model, "needs_eval", False):
        return TierAdvice(model=model, rule_why=rule_why, state="recommended", record=view.valid, stale=view.stale)
    state = {"passed": "recommended", "usable": "usable", "failed": "failed"}.get(view.verdict or "", "candidate")
    return TierAdvice(model=model, rule_why=rule_why, state=state, record=view.record, stale=view.stale, gated=True)


def start_notice(model: Any, view: RecordView) -> Optional[str]:
    """For a marked tier started by name without a passed or usable record: the plain notice (None otherwise)."""
    if not getattr(model, "needs_eval", False) or view.verdict in ("passed", "usable"):
        return None
    if view.verdict == "failed":
        return (f"{model.tier} failed the measurement on this computer ({'; '.join(failure_reasons(view.valid or {}))});"
                " it runs because you chose it by name.")
    return (f"{model.tier} is not evaluated on this computer; it runs because you chose it by name. Evaluate it: "
            f"{measure_command(model.tier)}")


__all__ = ["Fingerprint", "RECORD_FILE", "RecordView", "SCHEMA", "STATES", "TierAdvice", "TierChecks", "VERDICTS",
           "active_backend", "advise", "computer_id", "cpu_name", "current_fingerprint", "failure_reasons",
           "make_record", "measure_command", "numbers_text", "start_notice", "tier_state", "verdict"]
