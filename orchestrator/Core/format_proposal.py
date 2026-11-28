#!/usr/bin/env python3
"""
Ditto's proposal of a format's values (docs/DESIGN.md, Targets, hardware and templates: Ditto's proposal of a
format's values): the values of the general template's variables for the hardware in front of it, one value at a time,
from the hardware probe, the crawled vendor documentation and the entries of Ditto's archive, each with its reason, its
source and its confidence.

The model's answer (FormatValuesAnswer) is checked here, by the framework, before anyone sees it:

- a value for a path the template does not have is dropped;
- a value whose source is no excerpt the model was given and no key the probe holds loses its value and becomes a
  named gap (a value is read, never guessed); `template` is a source only for what the template itself defines (its
  board paths, the format's id and label);
- an entry of Ditto's archive (A1, A2 ...) is a source of the format's own values only, never of the hardware itself
  (HARDWARE_PATHS and the board paths come from the probe or the template), and the value must be the entry's value
  (or stand in its sentence); a value that rests on an entry that is not confirmed is not put into the draft: it waits
  as a PendingValue until the person decides it (FormatProposal.decide: use it, confirm the entry and use it, or leave
  the field empty; the wizard and `module propose-values` ask, every other path leaves it waiting);
- a value with a dangerous command in it is refused;
- every value goes through the schema and the module's checks (Core/template_values.field_problems), and a failing
  one is shown with its reason at its field, never changed silently;
- the required fields left empty are named as gaps.

The result (FormatProposal) is a draft for the person: the wizard's form shows each value with its reason, source and
confidence beside its field (gui/format_form.py), `llm-cli module generate-ai` and `module propose-values` print it,
and a repair turns its changed values into the variables proposal of Core/module_test.py. An AI never writes or signs a
module from it.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.Core.ditto_manager import CannotAnswer
from orchestrator.Core.template_values import (field_by_path, field_problems, format_fields, get_value,
                                               missing_required, set_value)

PROBE_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{1,47})\s*[=:]\s*(\S.*)?$", re.M)
SOURCE_RE = re.compile(r"^(?:S(\d{1,2})|A(\d{1,2})|probe:([A-Z][A-Z0-9_]{1,47})|template)$")
# What the template itself defines: a value of these may name the template as its source.
TEMPLATE_SOURCED = ("id", "label", "output_extension", "board_paths.install_dir", "board_paths.model_dir",
                    "board_paths.lib_dir")
# The hardware's own values: they come from the probe or the template, never from the archive (the archive holds what
# the person does not know and the probe does not give).
HARDWARE_PATHS = ("sdk.runtime_key", "sdk.driver_key")
# The person's answers to a value that rests on an unconfirmed entry of the archive.
DECISIONS = ("use", "confirm", "skip")
CLAIMS_LIMIT = 6


def _limit(value: str, size: int) -> str:
    return str(value or "").strip()[:size]


class ProposedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=80, description="The variable's path, e.g. converter.command or "
                                                 "runtime_libraries.0.version")
    value: Union[bool, List[str], str] = Field(description="A word, a list of words, or true/false")
    reason: str = Field(default="", description="Why this value, in one sentence")
    source: str = Field(default="", description="S1, S2 ... (an excerpt), A1, A2 ... (an archive entry), probe:KEY, "
                                                "or template")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        return _limit(v, 300)


class MissingValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=80)
    why: str = Field(default="", description="What is missing to read it")

    @field_validator("why")
    @classmethod
    def _why(cls, v: str) -> str:
        return _limit(v, 300)


class ProposedFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=24, description="The format's id, e.g. hef")
    values: List[ProposedValue] = Field(default_factory=list, max_length=120)
    missing: List[MissingValue] = Field(default_factory=list, max_length=60)


class FormatValuesAnswer(BaseModel):
    """The model's answer: per format the values with reason, source and confidence, and what it could not read."""
    model_config = ConfigDict(extra="forbid")

    formats: List[ProposedFormat] = Field(default_factory=list, max_length=4)
    summary: str = Field(default="", description="One or two sentences on what was proposed")
    cannot: Optional[CannotAnswer] = Field(default=None, description="Only when there is nothing to base any value "
                                                                     "on")

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        return _limit(v, 600)


@dataclass
class ValueNote:
    """One proposed value as the person sees it beside its field."""
    path: str
    value: Any
    reason: str
    source: str        # the excerpt's address, "probe KEY", or "the template"
    confidence: float

    def line(self) -> str:
        shown = " ".join(self.value) if isinstance(self.value, list) else str(self.value)
        return (f"{self.path} = {shown}  [{self.source or 'no source'}, confidence {self.confidence:.1f}]"
                + (f": {self.reason}" if self.reason else ""))


@dataclass
class PendingValue:
    """A proposed value that rests on an entry of Ditto's archive that is not confirmed: it is not in the draft until
    the person decides it (FormatProposal.decide)."""
    format_id: str
    path: str
    value: Any
    reason: str
    confidence: float
    entry: Any          # schemas/archive.ArchiveEntry
    needed: int         # the independent domains the archive's security level needs to confirm it

    @property
    def shown_value(self) -> str:
        return " ".join(str(v) for v in self.value) if isinstance(self.value, list) else str(self.value)

    def line(self) -> str:
        entry = self.entry
        return (f"{self.format_id}.{self.path} = {self.shown_value}  [archive {entry.key}, unconfirmed: "
                f"{entry.independent_sources} of {self.needed} independent domains]: waits for your decision")

    def as_json(self) -> Dict[str, Any]:
        entry = self.entry
        return {"format": self.format_id, "path": self.path, "value": self.value, "reason": self.reason,
                "confidence": self.confidence, "entry": {"id": entry.id, "key": entry.key, "text": entry.text,
                                                         "evidence": list(entry.evidence),
                                                         "independent_sources": entry.independent_sources,
                                                         "needed": self.needed,
                                                         "competing": len(entry.competing)}}


@dataclass
class FormatDraft:
    """One format of the proposal: its values (a draft), the notes per path, the gaps, the checks' reasons and the
    values that wait for the person's decision."""
    id: str
    values: Dict[str, Any]
    notes: Dict[str, ValueNote] = field(default_factory=dict)
    gaps: List[Tuple[str, str]] = field(default_factory=list)
    problems: Dict[str, List[str]] = field(default_factory=dict)
    changed: List[str] = field(default_factory=list)   # a repair: the paths whose value changed
    pending: List[PendingValue] = field(default_factory=list)
    reasons: Dict[str, str] = field(default_factory=dict)   # why a path is empty, before the required ones are added

    @property
    def complete(self) -> bool:
        return not self.gaps and not self.problems and not self.pending

    def settle(self, fields: Optional[Sequence[Any]] = None) -> None:
        """The gaps and the checks' reasons from the values now: a waiting value is no gap (it is pending)."""
        fields = fields if fields is not None else format_fields()
        waiting = {p.path for p in self.pending}
        gaps = dict(self.reasons)
        for path in missing_required(self.values, fields):
            gaps.setdefault(path, "not found in the probe or the documentation")
        self.gaps = [(p, w) for p, w in gaps.items()
                     if p not in waiting and get_value(self.values, p) in (None, "", [])]
        self.problems = field_problems(self.values) if not self.gaps and not self.pending else {}


@dataclass
class FormatProposal:
    """Ditto's proposal: the drafts, the summary, the excerpts' sources and the values that were refused."""
    drafts: List[FormatDraft]
    summary: str = ""
    sources: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    cannot: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.drafts) and all(d.complete for d in self.drafts)

    def pending(self) -> List[PendingValue]:
        """The values that wait for the person's decision, in the order of the drafts."""
        return [p for d in self.drafts for p in d.pending]

    def decide(self, pending: PendingValue, decision: str, archive: Any = None) -> None:
        """The person's answer to a value that rests on an unconfirmed entry of the archive: `use` puts it into the
        draft (the entry stays unconfirmed, its note says so), `confirm` confirms the entry first (`archive`:
        DittoArchive.confirm, the person's confirmation; its ArchiveError is raised and nothing changes) and then puts
        it in, `skip` leaves the field empty with why. Only the person's own channels call it."""
        if decision not in DECISIONS:
            raise ValueError(f"a decision is one of {', '.join(DECISIONS)}, not {decision!r}")
        draft = next((d for d in self.drafts if pending in d.pending), None)
        if draft is None:
            raise ValueError("this value does not wait for a decision in this proposal")
        key = pending.entry.key
        if decision == "confirm":
            if archive is None:
                raise ValueError("confirming needs Ditto's archive")
            archive.confirm(pending.entry.id)
        draft.pending.remove(pending)
        if decision == "skip":
            draft.reasons[pending.path] = f"you did not use the unconfirmed archive entry {key}"
        else:
            before = get_value(draft.values, pending.path)
            set_value(draft.values, pending.path, pending.value)
            if before is not None and before != pending.value and pending.path not in draft.changed:
                draft.changed.append(pending.path)   # a repair: the module's value changes
            label = (f"archive {key}, confirmed by you" if decision == "confirm" else
                     f"archive {key}, unconfirmed, used on your decision")
            draft.notes[pending.path] = ValueNote(pending.path, pending.value, pending.reason, label,
                                                  pending.confidence)
            draft.reasons.pop(pending.path, None)
        draft.settle()

    def formats(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(d.values) for d in self.drafts]

    def formats_yaml(self) -> str:
        return yaml.safe_dump(self.formats(), sort_keys=False, allow_unicode=True) if self.drafts else ""

    def notes(self) -> Dict[str, Dict[str, ValueNote]]:
        return {d.id: dict(d.notes) for d in self.drafts}

    def lines(self) -> List[str]:
        """The proposal as the person reads it: per format each value with its source, confidence and reason, the
        gaps and the checks' reasons."""
        from orchestrator.utils.untrusted import sanitize_untrusted
        out = []
        if self.cannot:
            out.append("Ditto could not propose values: " + sanitize_untrusted(self.cannot, 300))
        if self.summary:
            out.append(sanitize_untrusted(self.summary, 600))
        for draft in self.drafts:
            out.append(f"Format {draft.id}:")
            out += ["  " + sanitize_untrusted(note.line(), 600) for note in draft.notes.values()]
            for path, why in draft.gaps:
                out.append(f"  {path}: empty, " + sanitize_untrusted(why or "not found in the probe or the "
                                                                     "documentation", 300))
            for path, reasons in draft.problems.items():
                out += [f"  check {path or 'format'}: " + sanitize_untrusted(r, 300) for r in reasons]
            out += ["  " + sanitize_untrusted(p.line(), 600) for p in draft.pending]
        if self.dropped:
            out.append("Refused: " + "; ".join(sanitize_untrusted(d, 200) for d in self.dropped))
        if not self.complete and self.drafts:
            out.append("The draft has gaps: fill them before the module is made (the wizard's form, or "
                       "the command line's questions per value).")
        if self.pending():
            out.append("A value that rests on an unconfirmed entry of Ditto's archive is used only when you decide "
                       "it: the module wizard asks, and module propose-values asks when you run it yourself.")
        return out

    def as_json(self) -> Dict[str, Any]:
        return {"summary": self.summary, "complete": self.complete, "cannot": self.cannot, "sources": self.sources,
                "dropped": self.dropped,
                "formats": [{"id": d.id, "values": d.values, "complete": d.complete,
                             "proposed": [{"path": n.path, "value": n.value, "reason": n.reason, "source": n.source,
                                           "confidence": n.confidence} for n in d.notes.values()],
                             "gaps": [{"path": p, "why": w} for p, w in d.gaps], "problems": d.problems,
                             "changed": d.changed, "pending": [p.as_json() for p in d.pending]}
                            for d in self.drafts]}


def probe_keys(probe: str) -> Dict[str, str]:
    """The keys of a probe file (KEY=VALUE lines) with their values."""
    return {m.group(1): (m.group(2) or "").strip() for m in PROBE_KEY_RE.finditer(probe or "")}


def accelerator_named(probe: str) -> bool:
    """Whether a probe names an accelerator of its own (a format other than GGUF is then worth proposing)."""
    keys = probe_keys(probe)
    return any(keys.get(k, "").upper() == "ON" for k in ("SUPPORTS_CUDA", "SUPPORTS_RKNN", "SUPPORTS_HAILO",
                                                         "SUPPORTS_INTEL_NPU")) or \
        any(k.startswith(("HAILO_", "NPU_", "TENSORRT", "QNN_", "OPENVINO")) and v for k, v in keys.items())


def hardware_path(path: str) -> bool:
    """Whether `path` is a value of the hardware itself (the probe's or the template's, never the archive's)."""
    return path in HARDWARE_PATHS or path.startswith("board_paths.")


def claim_holds(entry: Any, value: Any) -> bool:
    """Whether the proposed `value` is what the archive's `entry` says: its typed value (normalized as the archive
    normalizes it), or, for an entry without one, every word of the value in its sentence."""
    from orchestrator.schemas.archive import comparable_text, normalize_value
    words = [str(v) for v in value] if isinstance(value, list) else [str(value)]
    if getattr(entry, "value", None):
        return len(words) == 1 and normalize_value(entry.value_type or "text", words[0]) == entry.value
    text = comparable_text(entry.text)
    return bool(words) and all(comparable_text(w) and comparable_text(w) in text for w in words)


def _resolve_source(source: str, sources: Sequence[str], keys: Dict[str, str], path: str,
                    claims: Sequence[Any] = ()) -> Tuple[str, str]:
    """(the source as the person reads it, why it does not count; "" when it counts)."""
    m = SOURCE_RE.fullmatch((source or "").strip())
    if not m:
        return "", "no source given for it (a value is read from the probe or the documentation, never guessed)"
    if m.group(1):
        n = int(m.group(1))
        if 1 <= n <= len(sources):
            return sources[n - 1], ""
        return "", f"it cites S{n}, which was not given"
    if m.group(2):
        n = int(m.group(2))
        if not 1 <= n <= len(claims):
            return "", f"it cites A{n}, which was not given"
        if hardware_path(path):
            return "", ("a value of the hardware itself comes from the probe or the template, never from Ditto's "
                        "archive")
        return f"archive {claims[n - 1].key}", ""
    if m.group(3):
        if m.group(3) in keys:
            return f"probe {m.group(3)}", ""
        return "", f"it cites the probe key {m.group(3)}, which the probe does not hold"
    top = path.split(".")[0] if not path.startswith("board_paths.") else path
    if top in TEMPLATE_SOURCED or path in TEMPLATE_SOURCED:
        return "the template", ""
    return "", "the template does not define it, so it needs the probe or the documentation as its source"


def build_proposal(answer: FormatValuesAnswer, sources: Sequence[str], probe: str,
                   current: Optional[List[Dict[str, Any]]] = None, claims: Sequence[Any] = (),
                   needed: int = 0) -> FormatProposal:
    """The framework's checks of the model's answer (see the module text): the drafts the person reviews. `current`:
    the module's values (a repair: a draft starts from them and records what changed); `claims`: the archive's
    entries given as A1, A2 ..., `needed`: the independent domains its level needs to confirm one."""
    from orchestrator.Core.ditto_manager import dangerous_command
    keys = probe_keys(probe)
    fields = format_fields()
    current_by_id = {str(f.get("id")): f for f in current or [] if isinstance(f, dict)}
    drafts: List[FormatDraft] = []
    dropped: List[str] = []
    cannot = ""
    if answer.cannot is not None:
        cannot = str(answer.cannot.missing or answer.cannot.next_step or answer.cannot.reason)
    for proposed in answer.formats[:4]:
        fid = (proposed.id or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", fid):
            dropped.append(f"a format id {fid[:30]!r} that is no plain id")
            continue
        base = copy.deepcopy(current_by_id.get(fid) or {"id": fid})
        draft = FormatDraft(fid, base)
        gaps: Dict[str, str] = {}
        for item in proposed.values:
            path = item.path.strip()
            found = field_by_path(path, fields)
            if found is None or path == "id":
                if path != "id":
                    dropped.append(f"{fid}.{path[:60]}: the template has no such variable")
                continue
            label, why = _resolve_source(item.source, sources, keys, path, claims)
            if why:
                gaps[path] = why
                continue
            words = item.value if isinstance(item.value, list) else [item.value]
            danger = dangerous_command(" ".join(str(w) for w in words))
            if danger:
                dropped.append(f"{fid}.{path}: refused, it holds a dangerous command ({danger})")
                continue
            spec, _index, column = found
            value = item.value
            kind = (column or spec).kind
            if kind in ("list", "multi") and isinstance(value, str):
                value = [w for w in re.split(r"[\s,]+", value) if w]
            if kind == "bool" and isinstance(value, str):
                value = value.strip().lower() in ("true", "yes", "1")
            if kind in ("text", "choice") and isinstance(value, list):
                value = " ".join(str(v) for v in value)
            claim = _cited_claim(item.source, claims)
            if claim is not None:
                if not claim_holds(claim, value):
                    gaps[path] = f"the value is not what the archive entry {claim.key} says"
                    continue
                if claim.status != "confirmed":   # the person decides it first (FormatProposal.decide)
                    draft.pending.append(PendingValue(fid, path, value, item.reason, float(item.confidence), claim,
                                                      needed))
                    continue
            before = get_value(draft.values, path)
            set_value(draft.values, path, value)
            if fid in current_by_id and before != value:
                draft.changed.append(path)
            draft.notes[path] = ValueNote(path, value, item.reason, label, float(item.confidence))
        for gap in proposed.missing:
            if gap.path.strip() and gap.path.strip() not in draft.notes:
                gaps.setdefault(gap.path.strip(), gap.why or "not found in the probe or the documentation")
        draft.reasons = gaps
        draft.settle(fields)
        drafts.append(draft)
    return FormatProposal(drafts, answer.summary, list(sources), dropped, cannot)


def _cited_claim(source: str, claims: Sequence[Any]) -> Optional[Any]:
    m = SOURCE_RE.fullmatch((source or "").strip())
    if m and m.group(2) and 1 <= int(m.group(2)) <= len(claims):
        return claims[int(m.group(2)) - 1]
    return None


def propose(ditto: Any, probe_file: Optional[Any] = None, board: str = "", *, probe_text: str = "",
            current: Optional[List[Dict[str, Any]]] = None, log_content: str = "") -> FormatProposal:
    """Ditto's proposal of the general template's values: per value the value, its reason, its source (an excerpt of
    the crawled vendor documentation, a key of the probe, the template) and its confidence, from the probe (or the
    board) and the excerpts the knowledge base retrieves for its accelerator, asked through Ditto's provider layer
    (`ditto`: a DittoCoder) and checked by build_proposal (an unsourced value becomes a named gap). `current` and
    `log_content`: a repair of a module made from the general template (only what the log shows is wrong changes).
    Raises DittoError; the result is a draft the person reviews, never a module. The one function of the wizard's AI
    analysis and its Propose the Values, `llm-cli module generate-ai`, `module propose-values` and the repair analysis
    (Core/module_test.py)."""
    from pathlib import Path
    from orchestrator.Core.ditto_manager import DittoCannot
    from orchestrator.Core.template_values import catalogue_text
    from orchestrator.llm.task_prompts import FORMAT_VALUES_SYSTEM, format_value_parts
    probe = probe_text or ""
    if probe_file is not None:
        probe_file = Path(probe_file)
        if not probe_file.is_file():
            raise FileNotFoundError(f"Probe file not found: {probe_file}")
        probe = probe_file.read_text(encoding="utf-8", errors="ignore")
    board = (board or "").strip()
    if not probe.strip() and not board and not current:
        raise DittoCannot(CannotAnswer(reason="missing_information",
                                       missing="a hardware probe of the board, or the board's name and its SoC",
                                       next_step="Import the board's probe first."), confirmed=True)
    logger = getattr(ditto, "logger", None)
    excerpts = retrieve(ditto._rag_manager(), probe, board, logger=logger)
    archive = archive_of(ditto)
    claims = retrieve_claims(archive, probe, board, logger=logger)
    needed = _needed(archive) if claims else 0
    current_yaml = yaml.safe_dump(current, sort_keys=False, allow_unicode=True) if current else ""
    parts = format_value_parts(catalogue_text(), probe, board, excerpts, current_yaml, log_content,
                               roots=ditto._folder_labels() if log_content else None, claims=claims)
    parts.append("Repair the values: propose only the values that change." if current else
                 "Propose the values of the format(s) this hardware's accelerator needs.")
    answer = ditto._structured("json", FORMAT_VALUES_SYSTEM, "\n\n".join(parts), FormatValuesAnswer)
    return build_proposal(answer, [source for source, _content in excerpts], probe, current, claims, needed)


def archive_of(ditto: Any) -> Optional[Any]:
    """Ditto's archive (Core/ditto_archive.DittoArchive) of the framework Ditto runs in, while the knowledge base is
    on; None otherwise."""
    if ditto._rag_manager() is None:
        return None
    return getattr(getattr(ditto, "framework", None), "ditto_archive", None)


def _needed(archive: Any) -> int:
    try:
        return int(archive.needed())
    except Exception:  # noqa: BLE001 - shown only; the decision is the person's either way
        return 0


def claims_query(probe: str, board: str = "") -> str:
    """The archive search for the probe's hardware: its SDK family, the SoC and the NPU as the probe names them, and
    what a format needs."""
    from orchestrator.llm.task_prompts import module_query
    keys = probe_keys(probe)
    named = " ".join(keys.get(k, "") for k in ("SOC_VENDOR", "SOC_MODEL", "NPU_VENDOR", "NPU_MODEL") if keys.get(k))
    hint = module_query(probe, board).split(" ", 1)[0]
    return " ".join(w for w in (hint, named, board, "NPU model format converter quantization runtime SDK") if w)


def retrieve_claims(archive: Any, probe: str, board: str = "", limit: int = CLAIMS_LIMIT,
                    logger: Any = None) -> List[Any]:
    """The archive's entries for the probe's hardware (schemas/archive.ArchiveEntry, confirmed or not; a copy never),
    [] without the archive or when its search fails."""
    if archive is None:
        return []
    try:
        hits = archive.search(claims_query(probe, board), limit=limit)
    except Exception as e:  # noqa: BLE001 - the archive is optional context
        if logger is not None:
            logger.warning(f"Archive search failed: {e}")
        return []
    return [h.entry for h in hits or [] if getattr(h.entry, "kind", "entry") == "entry"]


def retrieve(rag: Any, probe: str, board: str = "", limit: int = 6, logger: Any = None) -> List[Tuple[str, str]]:
    """The excerpts of the crawled vendor documentation for the probe's accelerator: (source, content) pairs, [] when
    the knowledge base is off or holds nothing about it."""
    from orchestrator.llm.task_prompts import module_query
    if rag is None:
        return []
    query = module_query(probe, board).replace("compilation flags build configuration optimization parameters",
                                               "model conversion compiler quantization runtime library format")
    try:
        hits = rag.search(query, limit=limit, rerank=False)
    except Exception as e:  # noqa: BLE001 - the knowledge base is optional context
        if logger is not None:
            logger.warning(f"Knowledge base search failed: {e}")
        return []
    out = []
    for hit in hits or []:
        source = str(getattr(hit, "source", "") or "")
        content = str(getattr(hit, "content", "") or "")
        if content.strip():
            out.append((source or "the knowledge base", content))
    return out

