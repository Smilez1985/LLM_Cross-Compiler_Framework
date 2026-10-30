#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - The person's decision on a module's CMake flags (DECISIONS round 25 Q6)

A valid CMake option outside the allowlist of the build recipe (orchestrator/runtime/build/recipe.py
cmake_flag_verdicts, verdict "decision": -G, -U, --log-level, --trace and the other switches of DECISION_SWITCHES) is
not simply refused: before such a module is built the person sees what each option does and a recommendation that
fits the model and quantization being built (from the target's recipe plan for that hardware), and decides to allow
the options or to go on with the standard flags (the options are left out). The decision is person-only: an AI-started
build with such a flag stops with needs_person (the build plan's blocker module_flags_undecided) unless the person
decided before. It can be remembered per module version and flag set in the store of the person's build
confirmations (Core/confirmed_builds.py, kind module_flags; no second store).

The build hands the decision to the build container (FlagDecision.environment): LLMCF_ALLOWED_FLAGS (the allowed
options as cmake reads them) or LLMCF_FLAG_DECISION=standard, which the package's last check reads
(targets/*/modules/target_module.sh module_flag_problem, module_flags_kept).

Reads only the target's own files; no path reaches an AI (the question names the module and its options).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DECISIONS = ("allow", "standard")
KIND = "module_flags"   # the kind in Core/confirmed_builds.py
# The generator's form of a module's CMake flags in config_module.sh (module_generator._process_template)
_OPTIONS_LINE = re.compile(r"^\s*CMAKE_OPTIONS='([^']*)'\s*$", re.M)
SEPARATOR = "\x1f"   # between the allowed options in LLMCF_ALLOWED_FLAGS (an option can hold a space: -G Ninja)


class FlagDecisionNeeded(ValueError):
    """A build needs the person's decision on a module's CMake flags (the question is `question`)."""

    def __init__(self, question: "FlagQuestion"):
        super().__init__(question.text())
        self.question = question
        self.code = "needs_person"


@dataclass(frozen=True)
class FlagDecision:
    """What the person decided: allow the options (`allowed`, as cmake reads them) or go on with the standard flags."""
    decision: str
    allowed: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"a flag decision is one of {', '.join(DECISIONS)}")

    def environment(self) -> Dict[str, str]:
        """The variables the build container reads (target_module.sh flag_allowed_by_person, module_flags_kept)."""
        if self.decision == "standard":
            return {"LLMCF_FLAG_DECISION": "standard"}
        return {"LLMCF_FLAG_DECISION": "allow", "LLMCF_ALLOWED_FLAGS": SEPARATOR.join(self.allowed)}

    def as_dict(self) -> Dict[str, Any]:
        return {"decision": self.decision, "allowed": list(self.allowed)}


@dataclass
class FlagQuestion:
    """The question to the person: the module, its version, the options outside the allowlist with what each does,
    the recommendation, and what a remembered decision is bound to (`item_id`, `flags_sha256`)."""
    target: str
    version: str
    options: List[Tuple[str, str]] = field(default_factory=list)   # (option as cmake reads it, what it does)
    recommendation: str = ""

    @property
    def item_id(self) -> str:
        return f"{self.target}@{self.version or 'unversioned'}"

    @property
    def flags_sha256(self) -> str:
        return flag_set_sha256([o for o, _ in self.options])

    def text(self) -> str:
        lines = [f"The module {self.target} (version {self.version or 'unknown'}) sets CMake options that are valid "
                 "but outside llmcf's list of allowed flags:"]
        lines += [f"  {option}: {what}" for option, what in self.options]
        if self.recommendation:
            lines.append(f"Recommendation: {self.recommendation}")
        lines.append("You decide: allow these options for this build, or go on with the standard flags (they are "
                     "left out).")
        return "\n".join(lines)

    def ai_text(self) -> str:
        """The refusal an AI gets: the options and that only the person decides (no path, no recommendation to act
        on by itself)."""
        names = ", ".join(o for o, _ in self.options)
        return (f"The module {self.target} sets CMake options outside the allowlist ({names}); only the person "
                "decides whether they are used.")

    def as_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "version": self.version, "options": [list(o) for o in self.options],
                "recommendation": self.recommendation}


def flag_set_sha256(options) -> str:
    canonical = json.dumps(sorted(set(str(o) for o in options)), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def module_cmake_words(target_folder: Optional[Path]) -> List[str]:
    """The words of a module's own CMake flags (CMAKE_OPTIONS='...' of modules/config_module.sh, the generator's form);
    empty for a target that plans its flags itself (the shipped Rockchip target) or has none."""
    if target_folder is None:
        return []
    path = Path(target_folder) / "modules" / "config_module.sh"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = _OPTIONS_LINE.search(text)
    if not m or m.group(1).startswith("["):  # the template's own placeholder
        return []
    return m.group(1).split()


def module_version(target_folder: Optional[Path]) -> str:
    try:
        import yaml
        meta = yaml.safe_load((Path(target_folder) / "target.yml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - no readable target.yml: no version
        return ""
    info = meta.get("metadata") if isinstance(meta, dict) else None
    return str((info or {}).get("version") or "")[:40] if isinstance(info, dict) else ""


def _recommended_quantizations(target_folder: Optional[Path], board: Optional[str]) -> List[str]:
    try:
        import yaml
        meta = yaml.safe_load((Path(target_folder) / "target.yml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    profiles = meta.get("optimization_profiles") if isinstance(meta, dict) else None
    if not isinstance(profiles, dict):
        return []
    board = str(board or "").lower()
    for name, profile in profiles.items():
        if isinstance(profile, dict) and (not board or str(name).lower().startswith(board)):
            quants = profile.get("recommended_quantization")
            if isinstance(quants, list):
                return [str(q) for q in quants]
    return []


def _runtime_summary(target_folder: Optional[Path]) -> str:
    """The target's recipe plan for its hardware, in a few words (the runtime: block of target.yml)."""
    try:
        import yaml
        meta = yaml.safe_load((Path(target_folder) / "target.yml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return ""
    block = meta.get("runtime") if isinstance(meta, dict) else None
    if not isinstance(block, dict):
        return ""
    parts = []
    if block.get("backends"):
        parts.append("backend " + "/".join(str(b) for b in block["backends"]))
    if block.get("cpu"):
        parts.append(f"CPU options {block['cpu']}")
    if block.get("arch"):
        parts.append(str(block["arch"]))
    return ", ".join(parts)


def recommendation(options: List[Tuple[str, str]], *, target_folder: Optional[Path], model: str = "",
                   quantization: Optional[str] = None, board: Optional[str] = None) -> str:
    """A recommendation that fits the model and quantization being built and the target's recipe plan for that
    hardware: options that change only how the programs are built or what CMake prints do not change the model; the
    standard flags are what the recipe plans for this hardware."""
    what = f"{model or 'the model'} as {quantization or 'the default quantization'}" + (f" for {board}" if board else "")
    plan = _runtime_summary(target_folder)
    quants = _recommended_quantizations(target_folder, board)
    only_output = all(any(k in o for k in ("--log", "--debug", "--trace", "--warn", "--no-warn", "--check"))
                      for o, _ in options)
    sentence = (f"for {what}, the standard flags are what the build recipe plans for this hardware"
                + (f" ({plan})" if plan else "") + ". ")
    if only_output:
        sentence += ("These options change only what CMake prints while it configures; allowing them is harmless and "
                     "useful when you look for a build problem.")
    else:
        sentence += ("These options do not make the converted model or its quantization better; go on with the "
                     "standard flags unless the module's author asked you for them.")
    if quantization and quants:
        sentence += (f" {quantization} is " + ("" if quantization in quants else "not ") +
                     f"among the quantizations this target recommends ({', '.join(quants)}).")
    return sentence[:1].upper() + sentence[1:]


def question_for(target: str, target_folder: Optional[Path], *, model: str = "", quantization: Optional[str] = None,
                 board: Optional[str] = None, prefetched=()) -> Optional[FlagQuestion]:
    """The question for this build, or None when the module's flags hold no option outside the allowlist."""
    from orchestrator.runtime.build.recipe import module_flag_decisions
    words = module_cmake_words(target_folder)
    decisions = module_flag_decisions(words, prefetched=prefetched) if words else []
    if not decisions:
        return None
    options = [(v.word, v.reason) for v in decisions]
    return FlagQuestion(target=target, version=module_version(target_folder), options=options,
                        recommendation=recommendation(options, target_folder=target_folder, model=model,
                                                      quantization=quantization, board=board))


def remembered(store: Any, question: FlagQuestion) -> Optional[FlagDecision]:
    """The decision the person remembered for exactly this module version and flag set, or None."""
    if store is None:
        return None
    entry = store.get(KIND, question.item_id)
    if not entry or entry.get("definition_sha256") != question.flags_sha256:
        return None
    decision = entry.get("decision")
    if decision not in DECISIONS:
        return None
    return FlagDecision(decision, tuple(o for o, _ in question.options) if decision == "allow" else ())


def remember(store: Any, question: FlagQuestion, decision: FlagDecision, channel: str) -> bool:
    """Keeps the person's decision for this module version and flag set (only a person's channel counts)."""
    if store is None:
        return False
    return bool(store.record(KIND, question.item_id, definition_sha256=question.flags_sha256, channel=channel,
                             decision=decision.decision))


def decide(question: FlagQuestion, answer: str) -> FlagDecision:
    """The decision of an answer: "allow" (the question's options) or "standard"."""
    if answer == "allow":
        return FlagDecision("allow", tuple(o for o, _ in question.options))
    return FlagDecision("standard")


__all__ = ["DECISIONS", "FlagDecision", "FlagDecisionNeeded", "FlagQuestion", "decide", "flag_set_sha256",
           "module_cmake_words", "module_version", "question_for", "recommendation", "remember", "remembered"]
