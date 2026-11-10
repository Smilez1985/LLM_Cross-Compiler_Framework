#!/usr/bin/env python3
"""
Auto-Tuning: which quantization of a model, and whether with an importance matrix, gives the best quality that still
fits the board, measured instead of guessed (DESIGN "Targets, hardware and templates", the row "Auto-Tuning").

The whole rule in one place, Qt-free and without the build engine, so the build service, the build engine, the CLI,
the Auto-Tuning tab and the AI tools share it:

- plan_candidates: the board profile's recommended types (in its order, as far as the format offers them) whose memory
  estimate fits, is tight or unknown, plus the person's own types whatever their estimate (nothing is refused); without
  a profile DEFAULT_TYPES; at most MAX_CANDIDATES. A type below 5 bits per weight is measured with the importance
  matrix (build_defaults' rule); imatrix "both" measures those types also without one.
- reference_type: the F16, or a Q8_0 made from it when the F16's weights do not fit into the tight share of this
  computer's memory.
- logits_bytes and disk_need: what the run puts on disk (the F16, the Q8_0 reference, the logits file, the largest
  candidate), checked by the disk rule before the start.
- read_results / parse_statistics: the tuning job's files (the runtime layer's tune.sh) into numbers; llama-perplexity's
  output is untrusted text, and only numbers are taken from it.
- choose: the choice rule. quality_verdict: the KLD verdict the Ditto rows reuse (KLD_GOOD, KLD_FLOOR, SPEED_FLOOR).
- make_report, with_speed, report_lines, ai_facts: the report (llm-framework.tuning-report/1), the measured board
  speeds merged in whenever it is shown, the person's lines and the AI's facts (numbers and words, never a path).
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "llm-framework.tuning-report/1"
REPORT_FILE = "tuning_report.json"
KIND = "tuning"                 # build_info.json "kind" of a tuning run (no model file)
RESULTS_FOLDER = "tuning"       # tune.sh's results in the tuning job's output folder

# The thresholds (the ROADMAP row "Start thresholds for Ditto's local model"): a KLD up to KLD_GOOD is good, up to
# KLD_FLOOR usable (the quality floor of the choice rule), above it below the floor; a measured speed below SPEED_FLOOR
# tokens per second is not recommended.
KLD_GOOD = 0.05
KLD_FLOOR = 0.10
SPEED_FLOOR = 8.0

# The cost of a run: llama-perplexity's own context, 16 chunks (16 x 255 scored tokens) by default
CTX_DEFAULT = 512
CTX_CHOICES = (256, 512, 1024)
CHUNKS_DEFAULT = 16
CHUNKS_MIN = 4
CHUNKS_MAX = 48
MAX_CANDIDATES = 8
DEFAULT_TYPES = ("Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "IQ4_XS", "Q4_0")
IMATRIX_MODES = ("auto", "both")
REFERENCES = ("F16", "Q8_0")
# Verdicts of the memory estimate a candidate may have without the person naming it
FITTING = ("fits", "tight", "unknown")
GIB = 1024 ** 3


class TuningError(ValueError):
    """A tuning run that cannot be planned; the message says why and what to do."""


# --- the candidates --------------------------------------------------------------------------------------------------

@dataclass
class Candidate:
    """One type to measure. `origin`: board (the profile recommends it), yours (the person named it) or default.
    `estimate`: the memory estimate's verdict and numbers on the board (state, need_mib, ram_mib, weights_bytes)."""
    quantization: str
    imatrix: bool
    origin: str = "board"
    estimate: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.quantization}-imatrix" if self.imatrix else self.quantization

    @property
    def spec(self) -> str:
        """The candidate as tune.sh reads it (TUNE_CANDIDATES): TYPE:i with the importance matrix, TYPE:- without."""
        return f"{self.quantization}:{'i' if self.imatrix else '-'}"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _estimate_dict(est: Any) -> Dict[str, Any]:
    if est is None:
        return {"state": "unknown", "reason": "no board"}
    out = {"state": getattr(est, "state", "unknown")}
    for key in ("need_mib", "ram_mib", "weights_bytes", "reason"):
        value = getattr(est, key, None)
        if value not in (None, ""):
            out[key] = value
    return out


def plan_candidates(*, offered: Sequence[str], recommended: Sequence[str], yours: Sequence[str],
                    estimate_for: Callable[[str, bool], Any], reads_imatrix: Callable[[str], bool],
                    imatrix: str = "auto") -> Tuple[List[Candidate], List[str]]:
    """(candidates, notes). `offered`: the types the target's gguf format offers; `recommended`: the board profile's
    order (empty without a profile: DEFAULT_TYPES); `yours`: the person's own types, always measured (a type the format
    does not offer is a TuningError, never left out silently); `estimate_for(type, imatrix)`: the memory estimate
    (MemoryEstimate or None); `reads_imatrix(type)`: whether a build of the type gets the importance matrix by default;
    `imatrix`: auto (that rule) or both (those types with and without)."""
    if imatrix not in IMATRIX_MODES:
        raise TuningError(f"The importance-matrix choice must be one of {', '.join(IMATRIX_MODES)}.")
    by_fold = {str(q).casefold(): str(q) for q in offered}
    notes: List[str] = []
    wanted: List[Tuple[str, str]] = []
    for q in yours:
        name = by_fold.get(str(q).strip().casefold())
        if name is None:
            raise TuningError(f"{q} is not a GGUF type this target builds; its types: {', '.join(offered)}.")
        wanted.append((name, "yours"))
    source = [q for q in recommended if q.casefold() in by_fold] or [q for q in DEFAULT_TYPES if q.casefold() in by_fold]
    origin = "board" if any(q.casefold() in by_fold for q in recommended) else "default"
    for q in source:
        wanted.append((by_fold[q.casefold()], origin))
    out: List[Candidate] = []
    seen = set()
    for name, how in wanted:
        modes = [reads_imatrix(name)]
        if imatrix == "both" and modes[0]:
            modes.append(False)
        for with_matrix in modes:
            key = (name, with_matrix)
            if key in seen:
                continue
            est = _estimate_dict(estimate_for(name, with_matrix))
            state = est.get("state", "unknown")
            if how != "yours" and state not in FITTING:
                notes.append(f"{name} is left out: the memory estimate says it does not fit the board "
                             f"({est.get('need_mib', '?')} of {est.get('ram_mib', '?')} MB); name it to measure it "
                             "anyway.")
                seen.add(key)
                continue
            note = ""
            if state == "tight":
                note = "tight on the board's memory"
            elif state == "does_not_fit":
                note = "does not fit the board's memory (your choice)"
            elif state == "unknown":
                note = f"memory estimate unknown ({est.get('reason') or 'no board memory or model size'})"
            seen.add(key)
            out.append(Candidate(name, with_matrix, how, est, note))
    if len(out) > MAX_CANDIDATES:
        dropped = [c.label for c in out[MAX_CANDIDATES:] if c.origin != "yours"]
        keep = [c for c in out if c.origin == "yours"]
        keep += [c for c in out if c.origin != "yours"][:max(0, MAX_CANDIDATES - len(keep))]
        out = [c for c in out if c in keep]
        if dropped:
            notes.append(f"At most {MAX_CANDIDATES} candidates are measured in one run; left out: "
                         f"{', '.join(dropped)}.")
    if not out:
        raise TuningError("No type to measure: none of the board's recommended types fits its memory. Name the "
                          "types to measure (they are measured whatever the estimate says).")
    return out, notes


# --- the reference and the cost --------------------------------------------------------------------------------------

def reference_type(f16_bytes: Optional[int], host_ram_mib: Optional[float]) -> Tuple[str, str]:
    """(F16 or Q8_0, why): the F16 when its weights fit into the tight share of this computer's memory (or either is
    unknown), else a Q8_0 made from it (its own distance from F16 is a thirtieth of KLD_GOOD in llama.cpp's tables)."""
    from orchestrator.Core.memory_estimate import TIGHT_SHARE
    if not f16_bytes or not host_ram_mib:
        return "F16", "the F16 model (this computer's memory or the model's size is not known)"
    if f16_bytes / (1024 * 1024) <= host_ram_mib * TIGHT_SHARE:
        return "F16", "the F16 model"
    return "Q8_0", (f"a Q8_0 made from the F16, whose {f16_bytes / GIB:.1f} GB do not fit into three quarters of "
                    f"this computer's {host_ram_mib / 1024:.1f} GB")


def logits_bytes(vocab: int, chunks: int, ctx: int) -> int:
    """The size of llama-perplexity's --kl-divergence-base file (tools/perplexity/perplexity.cpp at the pin): the
    magic and three numbers, the tokens of every chunk, and per chunk (ctx - 1 - ctx/2) rows of 2*((vocab+1)/2)+4
    16-bit values."""
    nv = 2 * ((int(vocab) + 1) // 2) + 4
    return 8 + 12 + int(chunks) * int(ctx) * 4 + int(chunks) * (int(ctx) - 1 - int(ctx) // 2) * nv * 2


def disk_need(*, f16_bytes: int, reference: str, vocab: Optional[int], chunks: int, ctx: int,
              largest_candidate: int) -> int:
    """Bytes the run puts on disk at once: the F16, the Q8_0 reference (about half of it), the logits, the largest
    candidate (each candidate is deleted after its measurement). Without a vocabulary 152,064 tokens are assumed (the
    largest common one)."""
    ref = int(f16_bytes * 34 / 64) if reference == "Q8_0" else 0
    return int(f16_bytes) + ref + logits_bytes(vocab or 152064, chunks, ctx) + int(largest_candidate)


def check_limits(chunks: int, ctx: int) -> None:
    if not CHUNKS_MIN <= int(chunks) <= CHUNKS_MAX:
        raise TuningError(f"The chunks must be between {CHUNKS_MIN} and {CHUNKS_MAX} (default {CHUNKS_DEFAULT}).")
    if int(ctx) not in CTX_CHOICES:
        raise TuningError(f"The context must be one of {', '.join(str(c) for c in CTX_CHOICES)} (default "
                          f"{CTX_DEFAULT}).")


@dataclass
class TuningPlan:
    """What a tuning run measures and with what: carried by the build request and the build configuration
    (BuildConfiguration.tuning, as as_dict) to the build engine, which hands env() to the tuning job."""
    candidates: List[Candidate]
    reference: str = "F16"
    reference_reason: str = ""
    chunks: int = CHUNKS_DEFAULT
    ctx: int = CTX_DEFAULT
    imatrix: str = "auto"
    disk_bytes: int = 0
    vocab: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    model: str = ""
    board: str = ""
    target: str = ""

    @property
    def uses_imatrix(self) -> bool:
        return any(c.imatrix for c in self.candidates)

    def env(self) -> Dict[str, str]:
        return {"TUNE_REFERENCE": self.reference, "TUNE_CANDIDATES": " ".join(c.spec for c in self.candidates),
                "TUNE_CHUNKS": str(int(self.chunks)), "TUNE_CTX": str(int(self.ctx))}

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["candidates"] = [c.as_dict() for c in self.candidates]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TuningPlan":
        values = dict(data)
        values["candidates"] = [Candidate(**c) for c in values.get("candidates") or []]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in values.items() if k in known})

    def lines(self) -> List[str]:
        """The plan as the person and the AI's question see it (labels and numbers)."""
        out = [f"Candidates: {', '.join(c.label + (f' ({c.note})' if c.note else '') for c in self.candidates)}",
               f"Reference: {self.reference_reason or self.reference}",
               f"Evaluation: {self.chunks} chunks of {self.ctx} tokens of the fixed evaluation text (English and German)",
               f"Disk at once: about {self.disk_bytes / GIB:.1f} GB"]
        return out + list(self.notes)


# --- the results of the tuning job -----------------------------------------------------------------------------------

_NUM = r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"
_PATTERNS = {
    "kld": re.compile(r"^Mean\s+KLD:\s*" + _NUM + r"\s*±\s*" + _NUM, re.M),
    "top1": re.compile(r"^Same top p:\s*" + _NUM + r"\s*±\s*" + _NUM + r"\s*%", re.M),
    "ppl": re.compile(r"^Mean PPL\(Q\)\s*:\s*" + _NUM + r"\s*±\s*" + _NUM, re.M),
    "ppl_base": re.compile(r"^Mean PPL\(base\)\s*:\s*" + _NUM + r"\s*±\s*" + _NUM, re.M),
    "ppl_ratio": re.compile(r"^Mean PPL\(Q\)/PPL\(base\)\s*:\s*" + _NUM + r"\s*±\s*" + _NUM, re.M),
    "final": re.compile(r"Final estimate:\s*PPL\s*=\s*" + _NUM + r"(?:\s*\+/-\s*" + _NUM + r")?"),
    "chunks": re.compile(r"(?:computing over|calculating perplexity over)\s+(\d+)\s+chunks"),
}


def _finite(value: str) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_statistics(text: str) -> Dict[str, Any]:
    """The numbers of llama-perplexity's statistics (its --kl-divergence summary, or the final estimate of a
    --kl-divergence-base run): kld and kld_unc, top1 and top1_unc (percent), ppl, ppl_base, ppl_ratio, chunks. Only
    numbers are kept; text is never carried on."""
    out: Dict[str, Any] = {}
    for key, pattern in _PATTERNS.items():
        match = pattern.search(text or "")
        if not match:
            continue
        if key == "chunks":
            out["chunks"] = int(match.group(1))
            continue
        value = _finite(match.group(1))
        if value is None:
            continue
        out[key] = value
        unc = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        if unc is not None and _finite(unc) is not None:
            out[f"{key}_unc"] = _finite(unc)
    if "final" in out:
        out["ppl_reference"] = out.pop("final")
        out.pop("final_unc", None)
    return out


def _info(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return values
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if re.fullmatch(r"[a-z_]{1,20}", key):
            values[key] = value.strip()[:128]
    return values


def _int(value: Any) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def read_results(folder: Path, plan: TuningPlan) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(reference, rows) from tune.sh's results folder: one row per candidate of the plan, in its order; a candidate
    without results is "not_measured", one whose output has no KLD "measure_failed"."""
    folder = Path(folder)
    ref_info = _info(folder / "reference.info")
    reference = {"type": ref_info.get("type") or plan.reference, "bytes": _int(ref_info.get("bytes")),
                 "logits_bytes": _int(ref_info.get("logits_bytes")), "seconds": _int(ref_info.get("seconds"))}
    try:
        reference.update(parse_statistics((folder / "reference.log").read_text(encoding="utf-8", errors="replace")))
    except OSError:
        pass
    rows: List[Dict[str, Any]] = []
    for cand in plan.candidates:
        info = _info(folder / f"{cand.label}.info")
        row: Dict[str, Any] = {"quantization": cand.quantization, "imatrix": cand.imatrix, "label": cand.label,
                               "origin": cand.origin, "estimate": dict(cand.estimate), "note": cand.note}
        if not info:
            row["status"] = "not_measured"
            rows.append(row)
            continue
        row.update({"bytes": _int(info.get("bytes")), "seconds": _int(info.get("seconds")),
                    "status": info.get("status") if info.get("status") in ("done", "quantize_failed", "measure_failed")
                    else "measure_failed"})
        try:
            stats = parse_statistics((folder / f"{cand.label}.log").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            stats = {}
        row.update({k: v for k, v in stats.items() if k != "ppl_reference"})
        if row["status"] == "done" and "kld" not in row:
            row["status"] = "measure_failed"   # llama-perplexity ends with 0 even when it could not compare
        if row["status"] == "done":
            row["verdict"] = quality_verdict(row["kld"])
        rows.append(row)
    return reference, rows


# --- the choice rule -------------------------------------------------------------------------------------------------

def quality_verdict(kld: Optional[float]) -> str:
    """good (up to KLD_GOOD), usable (up to KLD_FLOOR), below_floor, or unknown (not measured)."""
    if kld is None:
        return "unknown"
    if kld <= KLD_GOOD:
        return "good"
    return "usable" if kld <= KLD_FLOOR else "below_floor"


@dataclass
class Choice:
    """The recommendation (None when no candidate qualifies), why, and the best measured candidate."""
    label: Optional[str]
    reason: str
    best_measured: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fit_rank(row: Mapping[str, Any]) -> int:
    state = (row.get("estimate") or {}).get("state", "unknown")
    return {"fits": 0, "tight": 1, "unknown": 1}.get(state, 2)


def choose(rows: Iterable[Mapping[str, Any]], *, kld_floor: float = KLD_FLOOR,
           speed_floor: float = SPEED_FLOOR) -> Choice:
    """The choice rule: of the measured rows those that fit (none fits: tight or unknown; never one that does not fit)
    with a KLD at most `kld_floor` and, where a board speed was measured, at least `speed_floor` tokens per second; the
    lowest KLD wins, the smaller file when two are within their uncertainties. Only a recommendation: the person may
    build any measured type."""
    measured = [r for r in rows if r.get("status") == "done" and r.get("kld") is not None]
    if not measured:
        return Choice(None, "No candidate could be measured; the report lists why for each.")
    best = min(measured, key=lambda r: r["kld"])
    reasons = []
    eligible = []
    for r in measured:
        if _fit_rank(r) > 1:
            reasons.append(f"{r['label']} does not fit the board's memory")
            continue
        if r["kld"] > kld_floor:
            reasons.append(f"{r['label']} is below the quality floor (KLD {r['kld']:.4f} > {kld_floor})")
            continue
        speed = r.get("tokens_s")
        if speed is not None and speed < speed_floor:
            reasons.append(f"{r['label']} runs {speed:.1f} tokens/s on the board, below {speed_floor:g}")
            continue
        eligible.append(r)
    if not eligible:
        return Choice(None, "No type qualifies: " + "; ".join(reasons) + ".", best["label"])
    rank = min(_fit_rank(r) for r in eligible)
    pool = [r for r in eligible if _fit_rank(r) == rank]
    top = min(pool, key=lambda r: r["kld"])
    margin = float(top.get("kld_unc") or 0)
    close = [r for r in pool if r["kld"] - top["kld"] <= margin + float(r.get("kld_unc") or 0)]
    pick = min(close, key=lambda r: (int(r.get("bytes") or 0) or math.inf, r["kld"]))
    where = "fits the board" if rank == 0 else "is tight on the board (nothing measured fits with room)"
    why = (f"{pick['label']}: the lowest KLD ({pick['kld']:.4f}, {quality_verdict(pick['kld'])}) among the measured "
           f"types that qualify; it {where}")
    if pick is not top:
        why += f"; {top['label']} measured {top['kld']:.4f}, within the uncertainty, in a larger file"
    speed = pick.get("tokens_s")
    why += f"; {speed:.1f} tokens/s measured on the board" if speed is not None else \
        "; its speed on the board is not measured yet"
    return Choice(pick["label"], why + ".", best["label"])


# --- the report ------------------------------------------------------------------------------------------------------

def make_report(plan: TuningPlan, reference: Mapping[str, Any], rows: List[Dict[str, Any]], *,
                build_id: str, text_sha256: str, llama_cpp: Optional[str] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """The report a tuning run writes next to its build_info.json."""
    choice = choose(rows)
    return {
        "schema": SCHEMA, "build_id": build_id,
        "created": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "model": plan.model, "target": plan.target, "board": plan.board,
        "reference": {**dict(reference), "reason": plan.reference_reason},
        "evaluation": {"text_sha256": text_sha256, "chunks": plan.chunks, "ctx": plan.ctx},
        "imatrix": plan.imatrix, "llama_cpp": llama_cpp,
        "thresholds": {"kld_good": KLD_GOOD, "kld_floor": KLD_FLOOR, "speed_floor": SPEED_FLOOR},
        "candidates": rows, "recommendation": choice.as_dict(), "notes": list(plan.notes),
    }


def with_speed(report: Mapping[str, Any], measured: Iterable[Any]) -> Dict[str, Any]:
    """The report with tokens per second from the board tests (board_profiles.newest_measured rows: model, board,
    quantization, gen_ts) of its model and board, and the recommendation chosen again with them. Only measured
    numbers; a candidate without a row keeps none."""
    out = json.loads(json.dumps(report))
    board = str(out.get("board") or "").casefold()
    model = str(out.get("model") or "")
    speeds: Dict[str, float] = {}
    for row in measured:
        if str(getattr(row, "board", "")).casefold() == board and getattr(row, "model", None) == model \
                and getattr(row, "gen_ts", None) is not None:
            speeds.setdefault(str(row.quantization).casefold(), float(row.gen_ts))
    for cand in out.get("candidates") or []:
        speed = speeds.get(str(cand.get("quantization") or "").casefold())
        if speed is not None:
            cand["tokens_s"] = speed
        else:
            cand.pop("tokens_s", None)
    out["recommendation"] = choose(out.get("candidates") or []).as_dict()
    return out


def load_report(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise TuningError(f"{Path(path).name} is not a tuning report of this framework.")
    return data


def write_report(folder: Path, report: Mapping[str, Any]) -> Path:
    path = Path(folder) / REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def _mb(value: Any) -> str:
    return f"{int(value or 0) / (1024 * 1024):.0f} MB" if value else "-"


def row_text(row: Mapping[str, Any]) -> str:
    """One candidate as a line: label, size, estimate, KLD, top-1 agreement, speed."""
    est = row.get("estimate") or {}
    state = {"fits": "fits", "tight": "tight", "does_not_fit": "does not fit"}.get(est.get("state"), "unknown")
    if row.get("status") != "done":
        what = {"quantize_failed": "could not be quantized", "measure_failed": "could not be measured",
                "not_measured": "not measured (the run stopped before it)"}.get(row.get("status"), "not measured")
        return f"{row.get('label')}: {what}; memory {state}"
    speed = row.get("tokens_s")
    return (f"{row['label']}: {_mb(row.get('bytes'))}, memory {state}, KLD {row['kld']:.4f}"
            f" ± {float(row.get('kld_unc') or 0):.4f} ({quality_verdict(row['kld']).replace('_', ' ')}), top-1 "
            f"{float(row.get('top1') or 0):.1f} %, "
            + (f"{speed:.1f} tokens/s on the board" if speed is not None else "speed not measured on the board"))


def report_lines(report: Mapping[str, Any]) -> List[str]:
    rec = report.get("recommendation") or {}
    ref = report.get("reference") or {}
    ev = report.get("evaluation") or {}
    lines = [f"Auto-Tuning of {report.get('model')} for {report.get('board') or report.get('target')}",
             f"Reference: {ref.get('reason') or ref.get('type')}; {ev.get('chunks')} chunks of {ev.get('ctx')} tokens"
             " of the fixed evaluation text"]
    lines += [f"  {row_text(r)}" for r in report.get("candidates") or []]
    if rec.get("label"):
        lines.append(f"Recommended: {rec.get('reason')}")
    else:
        lines.append(f"No recommendation: {rec.get('reason')}"
                     + (f" The best measured type is {rec['best_measured']}." if rec.get("best_measured") else ""))
    if any(r.get("status") == "done" and r.get("tokens_s") is None for r in report.get("candidates") or []):
        lines.append("Speed: build a type, then run the board benchmark (llm-cli device runtime verify --bench-model);"
                     " this report shows the measured tokens/s from then on.")
    lines += [f"Note: {n}" for n in report.get("notes") or []]
    return lines


def ai_facts(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Numbers and words for an AI caller, never a path."""
    rows = []
    for r in report.get("candidates") or []:
        rows.append({k: r.get(k) for k in ("label", "quantization", "imatrix", "status", "bytes", "kld", "kld_unc",
                                           "top1", "ppl_ratio", "tokens_s", "verdict") if r.get(k) is not None}
                    | {"memory": (r.get("estimate") or {}).get("state", "unknown")})
    return {"model": report.get("model"), "board": report.get("board"), "target": report.get("target"),
            "reference": (report.get("reference") or {}).get("type"), "candidates": rows,
            "recommendation": report.get("recommendation"), "thresholds": report.get("thresholds")}


def build_choice(report: Mapping[str, Any], label: Optional[str] = None) -> Tuple[str, bool]:
    """(quantization, imatrix) of the recommended candidate, or of `label` (any measured candidate: the person may pick
    every one). TuningError when there is none."""
    wanted = label or (report.get("recommendation") or {}).get("label")
    if not wanted:
        raise TuningError("The report recommends no type; name the one to build.")
    for r in report.get("candidates") or []:
        if str(r.get("label")).casefold() == str(wanted).casefold():
            return str(r["quantization"]), bool(r.get("imatrix"))
    raise TuningError(f"{wanted} is not a candidate of this report.")


__all__ = ["CHUNKS_DEFAULT", "CHUNKS_MAX", "CHUNKS_MIN", "CTX_CHOICES", "CTX_DEFAULT", "Candidate", "Choice",
           "DEFAULT_TYPES", "IMATRIX_MODES", "KIND", "KLD_FLOOR", "KLD_GOOD", "MAX_CANDIDATES", "REPORT_FILE",
           "RESULTS_FOLDER", "SCHEMA", "SPEED_FLOOR", "TuningError", "TuningPlan", "ai_facts", "build_choice",
           "check_limits", "choose", "disk_need", "load_report", "logits_bytes", "make_report", "parse_statistics",
           "plan_candidates", "quality_verdict", "read_results", "reference_type", "report_lines", "row_text",
           "with_speed", "write_report"]
