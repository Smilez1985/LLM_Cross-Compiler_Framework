#!/usr/bin/env python3
"""
Ditto's proposal of a target's dependency entries (docs/DESIGN.md, "Dependencies are data in the wizard": Finding them):
the packages, files and drivers an SDK and toolchain need, one entry at a time, from the hardware probe and the crawled
vendor documentation, each with its reason, its source and its confidence.

The model's answer (DependenciesAnswer) is checked here, by the framework, before anyone sees it. An entry is *read*,
never guessed:

- an entry whose source is no excerpt the model was given and no key the probe holds is not made; it is named as a gap;
- the name and the version must stand in the cited excerpt (or the probe's value), and a wheel's sha256 must be printed
  there: a name, a version or a hash the source does not say is a gap, not a guess that reaches a Dockerfile;
- an entry with a dangerous command in it is refused;
- every entry goes through the schema (schemas/target_template.py Dependency); a failing one is shown with its reasons.

The result (DependencyProposal) is a draft for the person: the wizard's field takes its YAML (each entry with a comment
that says its source, confidence and reason), `llm-cli module propose-dependencies` prints it. An AI never writes a
module or installs anything from it: the entries become steps of a build image only when the person makes the module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from orchestrator.Core.ditto_manager import CannotAnswer
from orchestrator.Core.format_proposal import probe_keys

SOURCE_RE = re.compile(r"^(?:S(\d{1,2})|probe:([A-Z][A-Z0-9_]{1,47}))$")
MAX_ENTRIES = 40


def _limit(value: str, size: int) -> str:
    return str(value or "").strip()[:size]


class ProposedDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=100, description="The package, file, image or driver exactly as the source names it")
    version: str = Field(max_length=64, description="Its version as the source states it")
    kind: str = Field(max_length=20, description="system_package, pip_package, vendor_file, base_image or board_driver")
    origin: str = Field(default="", max_length=20, description="distribution, package_index or vendor_page")
    source: str = Field(default="", description="S1, S2 ... (an excerpt) or probe:KEY")
    url: Optional[str] = Field(default=None, max_length=500)
    sha256: Optional[str] = Field(default=None, max_length=64)
    check: List[str] = Field(default_factory=list, max_length=12)
    redistributable: bool = False
    note: str = Field(default="", max_length=200)
    reason: str = Field(default="", description="Why this entry, in one sentence")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        return _limit(v, 300)


class MissingDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=100)
    why: str = Field(default="", description="What is missing to read it")

    @field_validator("why")
    @classmethod
    def _why(cls, v: str) -> str:
        return _limit(v, 300)


class DependenciesAnswer(BaseModel):
    """The model's answer: the entries with reason, source and confidence, and what it could not read."""
    model_config = ConfigDict(extra="forbid")

    dependencies: List[ProposedDependency] = Field(default_factory=list, max_length=MAX_ENTRIES)
    missing: List[MissingDependency] = Field(default_factory=list, max_length=MAX_ENTRIES)
    summary: str = Field(default="", description="One or two sentences on what was proposed")
    cannot: Optional[CannotAnswer] = Field(default=None, description="Only when there is nothing to base an entry on")

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        return _limit(v, 600)


@dataclass
class EntryNote:
    """One proposed entry as the person sees it."""
    name: str
    reason: str
    source: str        # the excerpt's address, or "probe KEY"
    confidence: float

    def line(self) -> str:
        return f"{self.name}  [{self.source}, confidence {self.confidence:.1f}]" + (f": {self.reason}" if self.reason else "")


@dataclass
class DependencyProposal:
    """Ditto's proposal: the entries (checked against the schema and their sources), what stays a gap, what was refused."""
    entries: List[Any] = field(default_factory=list)                 # schemas Dependency
    notes: Dict[str, EntryNote] = field(default_factory=dict)        # by "kind:name"
    gaps: List[Tuple[str, str]] = field(default_factory=list)        # (name, why): nothing was made of it
    problems: Dict[str, List[str]] = field(default_factory=dict)     # name -> the schema's reasons
    dropped: List[str] = field(default_factory=list)
    summary: str = ""
    sources: List[str] = field(default_factory=list)
    cannot: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.entries) and not self.gaps and not self.problems

    def entries_yaml(self) -> str:
        """The entries as YAML for the wizard's field and `module create --dependencies`; each one under a comment that
        says its source, confidence and reason, and the gaps as comments at the end (a comment is no data)."""
        lines: List[str] = []
        for entry in self.entries:
            note = self.notes.get(f"{entry.kind}:{entry.name}")
            if note is not None:
                lines.append("# " + _one_line(note.line()))
            values = {k: v for k, v in entry.model_dump(mode="json", exclude_none=True).items()
                      if v not in ([], "", False)}
            lines.append(yaml.safe_dump([values], sort_keys=False, allow_unicode=True).rstrip())
        for name, why in self.gaps:
            lines.append(f"# gap, nothing made of it: {_one_line(name)}: {_one_line(why)}")
        return "\n".join(lines) + ("\n" if lines else "")

    def lines(self) -> List[str]:
        """The proposal as the person reads it: each entry with its source, confidence and reason, the gaps, the
        schema's reasons and what was refused."""
        from orchestrator.utils.untrusted import sanitize_untrusted
        out = []
        if self.cannot:
            out.append("Ditto could not propose entries: " + sanitize_untrusted(self.cannot, 300))
        if self.summary:
            out.append(sanitize_untrusted(self.summary, 600))
        for entry in self.entries:
            note = self.notes.get(f"{entry.kind}:{entry.name}")
            out.append(f"{entry.name} {entry.version} ({entry.kind.replace('_', ' ')}): "
                       + sanitize_untrusted(note.line() if note else "", 600))
        for name, why in self.gaps:
            out.append(f"{sanitize_untrusted(name, 100)}: nothing made of it, " + sanitize_untrusted(why, 300))
        for name, reasons in self.problems.items():
            out += [f"check {sanitize_untrusted(name, 100)}: " + sanitize_untrusted(r, 300) for r in reasons]
        if self.dropped:
            out.append("Refused: " + "; ".join(sanitize_untrusted(d, 200) for d in self.dropped))
        if not self.complete and self.entries:
            out.append("The draft has gaps: fill them, or leave the entry out, before the module is made.")
        return out

    def as_json(self) -> Dict[str, Any]:
        return {"summary": self.summary, "complete": self.complete, "cannot": self.cannot, "sources": self.sources,
                "dropped": self.dropped,
                "entries": [{**{k: v for k, v in e.model_dump(mode="json", exclude_none=True).items()
                                if v not in ([], "", False)},
                             "reason": getattr(self.notes.get(f"{e.kind}:{e.name}"), "reason", ""),
                             "confidence": getattr(self.notes.get(f"{e.kind}:{e.name}"), "confidence", None),
                             "cited": getattr(self.notes.get(f"{e.kind}:{e.name}"), "source", "")}
                            for e in self.entries],
                "gaps": [{"name": n, "why": w} for n, w in self.gaps], "problems": self.problems}


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _cited(source: str, excerpts: Sequence[Tuple[str, str]], keys: Dict[str, str]) -> Tuple[str, str, str, str]:
    """(the entry's source as target.yml keeps it, the label the person reads, the text the entry must stand in, why
    the source does not count; the first three are "" then)."""
    m = SOURCE_RE.fullmatch((source or "").strip())
    if not m:
        return "", "", "", "no source given for it (an entry is read from the probe or the documentation, never guessed)"
    if m.group(1):
        n = int(m.group(1))
        if 1 <= n <= len(excerpts):
            address, content = excerpts[n - 1]
            return f"excerpt:{n}", address, content, ""
        return "", "", "", f"it cites S{n}, which was not given"
    key = m.group(2)
    if key in keys:
        return f"probe:{key}", f"probe {key}", keys[key], ""
    return "", "", "", f"it cites the probe key {key}, which the probe does not hold"


def _stands_in(item: ProposedDependency, text: str) -> str:
    """Why the cited text does not say the entry ('' when it does): its name, its version and a wheel's hash must be
    in it."""
    low = text.lower()
    if item.name.lower() not in low:
        return f"the cited source does not name {item.name}"
    if item.version and item.version.lower() not in low:
        return f"the cited source does not say version {item.version}"
    if item.sha256 and item.sha256.lower() not in low:
        return "the cited source does not print that sha256"
    return ""


def build_proposal(answer: DependenciesAnswer, excerpts: Sequence[Tuple[str, str]], probe: str) -> DependencyProposal:
    """The framework's checks of the model's answer (see the module text): the draft the person reviews. `excerpts`:
    (source address, content) of the documentation the model was given, numbered S1, S2 ... in this order."""
    from orchestrator.Core.ditto_manager import dangerous_command
    from orchestrator.schemas.target_template import Dependency
    keys = probe_keys(probe)
    proposal = DependencyProposal(sources=[address for address, _content in excerpts], summary=answer.summary)
    if answer.cannot is not None:
        proposal.cannot = str(answer.cannot.missing or answer.cannot.next_step or answer.cannot.reason)
    seen = set()
    for item in answer.dependencies[:MAX_ENTRIES]:
        name = item.name.strip()
        source, label, text, why = _cited(item.source, excerpts, keys)
        if why:
            proposal.gaps.append((name, why))
            continue
        why = _stands_in(item, text)
        if why:
            proposal.gaps.append((name, why))
            continue
        danger = dangerous_command(" ".join([name, item.url or "", *item.check]))
        if danger:
            proposal.dropped.append(f"{name[:60]}: refused, it holds a dangerous command ({danger})")
            continue
        values = {"name": name, "version": item.version.strip(), "kind": item.kind.strip(),
                  "origin": item.origin.strip(), "source": source, "url": item.url or None,
                  "sha256": (item.sha256 or "").lower() or None, "check": list(item.check),
                  "redistributable": item.redistributable, "note": item.note}
        try:
            entry = Dependency.model_validate({k: v for k, v in values.items() if v not in (None, "")})
        except ValidationError as e:
            proposal.problems[name] = [f"{'.'.join(str(p) for p in err['loc'])}: "
                                       f"{str(err['msg']).removeprefix('Value error, ')}" for err in e.errors()][:5]
            continue
        key = f"{entry.kind}:{entry.name}"
        if key in seen:
            continue
        seen.add(key)
        proposal.entries.append(entry)
        proposal.notes[key] = EntryNote(entry.name, item.reason, label, float(item.confidence))
    for gap in answer.missing:
        if gap.name.strip() and not any(gap.name.strip() == n for n, _w in proposal.gaps):
            proposal.gaps.append((gap.name.strip(), gap.why or "not found in the probe or the documentation"))
    return proposal


def retrieve(rag: Any, probe: str, board: str = "", limit: int = 8, logger: Any = None) -> List[Tuple[str, str]]:
    """The excerpts of the crawled vendor documentation that say what the SDK requires: (source, content) pairs, []
    when the knowledge base is off or holds nothing about it."""
    from orchestrator.llm.task_prompts import module_query
    if rag is None:
        return []
    query = module_query(probe, board).replace(
        "compilation flags build configuration optimization parameters",
        "requires prerequisites dependencies install packages toolchain driver version")
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
        if not content.strip():
            continue
        stated = stated_requirements(hit, content)
        head = ("Statements of requirements on this page:\n" + "\n".join(f"- {s}" for s in stated) + "\n\n") \
            if stated else ""
        out.append((source or "the knowledge base", head + content))
    return out


def stated_requirements(hit: Any, content: str) -> List[str]:
    """What the excerpt says an SDK needs: the statements Deep Ingest kept beside it, else (a page stored before, a
    pack's page: a pack cannot set the field) derived again from the excerpt's own text, so the statements are always
    pieces of that text and an entry that cites them is checked against it."""
    from orchestrator.rag import requirements
    meta = getattr(hit, "metadata", None)
    stored = meta.get("requirements") if isinstance(meta, dict) else None
    if isinstance(stored, list):
        flat = re.sub(r"\s+", " ", content)
        kept = [s for s in stored if isinstance(s, str) and s and (s in content or s in flat)]
        if kept:
            return kept[:requirements.MAX_PER_EXCERPT]
    return requirements.find(content)


def shown(excerpts: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """The excerpts as the model saw them (task_prompts.dependency_parts): each cut at its limit, and only those that fit
    the budget, so an entry can cite nothing the model did not read."""
    from orchestrator.llm.task_prompts import FORMAT_EXCERPT_CHARS, FORMAT_EXCERPTS_CHARS
    out, used = [], 0
    for source, content in excerpts:
        text = str(content or "")[:FORMAT_EXCERPT_CHARS]
        if used + len(text) > FORMAT_EXCERPTS_CHARS:
            break
        used += len(text)
        out.append((source, text))
    return out


def propose(ditto: Any, probe_file: Optional[Any] = None, board: str = "", *, probe_text: str = "") -> DependencyProposal:
    """Ditto's proposal of the dependency entries: from the probe (or the board) and the excerpts the knowledge base
    retrieves for it, asked through Ditto's provider layer (`ditto`: a DittoCoder) and checked by build_proposal.
    Raises DittoError; the result is a draft the person reviews, never a module. The one function of the wizard's
    Propose Dependencies and `llm-cli module propose-dependencies`."""
    from pathlib import Path

    from orchestrator.Core.ditto_manager import DittoCannot
    from orchestrator.llm.task_prompts import DEPENDENCIES_SYSTEM, dependency_parts
    probe = probe_text or ""
    if probe_file is not None:
        probe_file = Path(probe_file)
        if not probe_file.is_file():
            raise FileNotFoundError(f"Probe file not found: {probe_file}")
        probe = probe_file.read_text(encoding="utf-8", errors="ignore")
    board = (board or "").strip()
    if not probe.strip() and not board:
        raise DittoCannot(CannotAnswer(reason="missing_information",
                                       missing="a hardware probe of the board, or the board's name and its SoC",
                                       next_step="Import the board's probe first."), confirmed=True)
    excerpts = shown(retrieve(ditto._rag_manager(), probe, board, logger=getattr(ditto, "logger", None)))
    parts = dependency_parts(probe, board, excerpts)
    parts.append("Propose the dependency entries this hardware's SDK and toolchain need.")
    answer = ditto._structured("json", DEPENDENCIES_SYSTEM, "\n\n".join(parts), DependenciesAnswer)
    return build_proposal(answer, excerpts, probe)


__all__ = ["DependenciesAnswer", "DependencyProposal", "EntryNote", "MissingDependency", "ProposedDependency",
           "build_proposal", "probe_keys", "propose", "retrieve", "shown"]
