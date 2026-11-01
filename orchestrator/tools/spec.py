#!/usr/bin/env python3
"""
Declarations a tool's author adds to its ToolSpec beyond the schemas: how the fool-proof layer may treat each
input field, which argument names are aliases, whether a confirmation may offer a standing approval, and the
tool's limits. The registry and the normalizer (orchestrator/tools/normalize.py) read them; nothing here runs a
tool.
"""

import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.tools.output import MAX_RESULT_BYTES

# What a field holds, which decides what may be repaired (design 6.11, "What is never repaired"):
#   value     - an ordinary value: types are coerced (text to number, "yes" to true, JSON text for lists)
#   choice    - one of a closed list (`choices` / `choices_from`): exact, canonical form if unique, declared alias
#   id        - an id the framework issued: only surrounding whitespace is removed
#   path, url, trust - a path, an address, or a value that carries trust (image digest, commit, hash, authorizing
#               boolean): never changed
#   filename  - a file name: exact, or one unique case-insensitive match
#   amount    - a number of things to return: above the maximum it is reduced with a note (`reduce_to_max`)
FIELD_KINDS = ("value", "choice", "id", "path", "url", "trust", "filename", "amount")


@dataclass(frozen=True)
class Choices:
    """The values a choice field accepts, with the aliases that map to them (alias -> value)."""
    values: Tuple[str, ...]
    aliases: Mapping[str, str] = field(default_factory=dict)
    where: str = ""  # for messages, e.g. "for gguf on Rockchip"


@dataclass(frozen=True)
class FieldMeta:
    """How the fool-proof layer may treat one input field."""
    kind: str = "value"
    choices: Tuple[str, ...] = ()
    # Computed choices; gets the arguments normalized so far (fields earlier in the schema), returns None when the
    # choices cannot be known yet (then the value is only checked later, by the tool itself).
    choices_from: Optional[Callable[[Dict[str, Any]], Optional[Choices]]] = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    unit: Optional[str] = None             # "seconds" or "bytes": "30s", "2 min", "8GB" are converted, with a note
    null_means_default: bool = False       # "", "null", "none", "default" mean "not given" (least-impact default)
    split: bool = False                    # "a, b" becomes ["a", "b"] for a list field
    list_tool: str = ""                    # for ids: the tool that lists the valid ones
    # The speaking name of a value for the person (a build's model and target for its request id, a local file's
    # <label>:<path> for its file id), shown next to the value in a question; None when there is none. Only people
    # see it: a model gets the ids and labels it sent.
    describe: Optional[Callable[[Any], Optional[str]]] = None
    # False: the value never appears in the audit log's argument preview (a data chunk of a transfer); the entry's
    # args_sha256 still covers it, so the recorded call stays bound to exactly what was sent.
    audit: bool = True

    def __post_init__(self):
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"unknown field kind {self.kind!r}")
        if self.unit not in (None, "seconds", "bytes"):
            raise ValueError(f"unknown unit {self.unit!r}")


@dataclass(frozen=True)
class GrantRule:
    """Whether a confirmation may offer "yes, and do not ask again" for a tool, and for which scope (design 6.10).
    `rule` (orchestrator.tools.grants.ScopeRule) computes the scope from the facts the framework looked up for the
    call: `facts(input, ToolContext)` returns them (never what the AI described). Without facts no scope exists, and
    the tool asks. `warning` completes "<caller> may then ... whenever it decides to, without asking you", for example
    "install knowledge packs from the signed index with trust verified or community". A new `version` lets every
    standing approval made under the old rule lapse."""
    rule: Any
    warning: str
    facts: Optional[Callable[[Any, Any], Dict[str, Any]]] = None
    version: int = 1


@dataclass(frozen=True)
class Fact:
    """One line of what the framework looked up for a question. `source` marks text from outside the framework (a
    build log, an AI's proposal, a pack index): it is shown escaped and labelled with where it comes from, never as
    the framework's own words. `volatile`: shown, but outside the decision digest (free space right now), so it alone
    never makes a question new. `person_only`: shown to the person in the framework's own window, never to an AI (a
    login with its address, a host key fingerprint, a full path); a question that can reach an AI client leaves it
    out."""
    label: str
    value: str
    source: str = ""
    volatile: bool = False
    person_only: bool = False


# The three kinds of warnings (design 4.1): an AI with a delegation may decide a situation warning itself; security
# and legal warnings always go to the person.
WARNING_KINDS = ("situation", "security", "legal")
WarningKind = Literal[WARNING_KINDS]  # type: ignore[valid-type]
# A warning code as the catalogue, the audit log (`warnings`) and the chat feed's warning rows write it.
WARNING_CODE_RE = r"^[a-z][a-z0-9_]{1,47}$"
_WARNING_CODE = re.compile(WARNING_CODE_RE)
_NUMBER_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# A closed enum value (a size class, "tight", "does_not_fit", a format), never free text.
_NUMBER_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,39}$")
# What a call's scope may say (design 4.4), matched by a delegation's limits; closed types only, like numbers.
SCOPE_KEYS = ("device_id", "size_bytes", "pack_kind", "pack_trust", "strategy", "own_work", "repair_class")


def number_problems(values: Any, what: str = "numbers") -> List[str]:
    """Why a mapping of numbers (or scope values) is not closed: a key that is not a plain name, or a value that is
    not a bool, a finite number or a short enum word (free text, which could carry external words, never is)."""
    if not isinstance(values, Mapping):
        return [f"{what} must be a mapping"]
    problems = []
    for key, value in values.items():
        if not isinstance(key, str) or not _NUMBER_KEY.fullmatch(key):
            problems.append(f"{what}: {key!r} is not a plain key")
        elif isinstance(value, bool) or (isinstance(value, int) and abs(value) < 2 ** 63):
            continue
        elif isinstance(value, float):
            if not math.isfinite(value):
                problems.append(f"{what}: {key} is not a finite number")
        elif not (isinstance(value, str) and _NUMBER_TEXT.fullmatch(value)):
            problems.append(f"{what}: {key} must be a number, a bool or a short enum word, not free text")
    return problems


def _catalog() -> Any:
    """The facts catalogue (orchestrator.tools.facts_catalog: warning kinds and texts, the stable and volatile numbers
    per tool), or None while this framework version has none."""
    name = "orchestrator.tools.facts_catalog"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name != name:  # the catalogue exists but something it imports is missing: a real error
            raise
        return None


def warning_spec(code: str) -> Any:
    """The catalogue's entry for a warning code (kind, AI template), None for a code the catalogue does not know."""
    catalog = _catalog()
    warnings = getattr(catalog, "WARNINGS", None) if catalog is not None else None
    return warnings.get(code) if isinstance(warnings, Mapping) else None


def person_words(code: str, numbers: Mapping[str, Any], lang: Optional[str] = None) -> Dict[str, str]:
    """The plain words the person reads for closed words in a warning's numbers (the catalogue's person_words: what
    could not be checked, which vendor list), {} without a catalogue or such words: never a code in a person's text."""
    catalog = _catalog()
    hook = getattr(catalog, "person_words", None) if catalog is not None else None
    if not callable(hook):
        return {}
    try:
        return {str(k): str(v) for k, v in dict(hook(code, numbers, lang)).items()}
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


def volatile_numbers(tool: str) -> Tuple[str, ...]:
    """The number keys of a tool that are shown but never part of its decision digest (free space now, exact bytes
    still to download). Only the catalogue names them: a number it does not know is stable, so it can only ever make a
    question new, never hide a change."""
    catalog = _catalog()
    keys = getattr(catalog, "NUMBER_KEYS", None) if catalog is not None else None
    entry = keys.get(tool) if isinstance(keys, Mapping) else None
    try:
        return tuple(sorted(entry[1]))
    except (TypeError, IndexError):
        return ()


@dataclass(frozen=True)
class CallWarning:
    """A warning the framework found before a question (design 4.1). The tool names the code and the numbers; the
    kind comes from the catalogue only, never from the tool. A code the catalogue does not know counts as a security
    warning (unknown is never "no problem": only the person decides it)."""
    code: str
    numbers: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.code, str) or not _WARNING_CODE.fullmatch(self.code):
            raise ValueError(f"warning code {self.code!r} is not a plain code")
        problems = number_problems(self.numbers, f"warning {self.code}")
        if problems:
            raise ValueError("; ".join(problems))

    @property
    def kind(self) -> str:
        kind = getattr(warning_spec(self.code), "kind", None)
        return kind if kind in WARNING_KINDS else "security"

    def ai_text(self) -> str:
        """The English text an AI gets (the catalogue's template filled with the numbers; never external text)."""
        template = getattr(warning_spec(self.code), "ai_template", "")
        if template:
            try:
                return str(template).format(**self.numbers)
            except (KeyError, IndexError, ValueError):
                pass
        return f"Warning {self.code}: the framework has no text for it; only the person can decide it."

    def person_text(self, lang: Optional[str] = None, names: Optional[Mapping[str, str]] = None) -> str:
        """The person's text in their language (ui_text key warning.<code>, filled with the numbers and speaking
        names); the AI text when there is no translation yet."""
        from orchestrator.utils.localization import ui_text
        key = f"warning.{self.code}"
        try:
            text = ui_text(key, lang, **{**dict(names or {}), **dict(self.numbers),
                                         **person_words(self.code, self.numbers, lang)})
        except (KeyError, IndexError, ValueError):
            text = key
        return self.ai_text() if text == key else text


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class CallFacts:
    """What a tool's `prepare` looked up before the question (design principle 11): the consequence in plain words
    (the question's first line), the facts the person is shown, the values the handler must use (`bound`) and a
    cap the facts call for (the value of a Decision, e.g. "ask" for a destructive healing strategy even when the
    tool is allowed). The handler gets exactly this object and uses `bound` or refuses. `repairs`: what the look-up
    repaired (normalize.Repair; a file name matched in another letter case): the registry names them in the result's
    notes and in every refusal after the look-up, and records them in the audit log, as it does the normalizer's. A
    look-up that refuses after such a repair puts it on its error (`repairs`, a list), so the refusal names it too.

    The facts model of the delegation design (4.1): `warnings` (situation, security, legal; the kind from the
    catalogue), `numbers` (closed values for texts and rules), `scope` (what a delegation's limits are matched
    against), `digest_keys` (which keys of `bound` the decision digest covers; none named: all of them),
    `consequence_template` (the consequence without volatile values), `deletion` (what the call would delete, for the
    person; any plan makes the call the person's alone), `names` (speaking names for the person) and `tool` (set by
    the registry when it looks the facts up)."""
    consequence: str
    facts: Tuple[Fact, ...] = ()
    bound: Mapping[str, Any] = field(default_factory=dict)
    cap: Optional[str] = None
    repairs: Tuple[Any, ...] = ()  # not part of the fingerprint: what the person is shown is `facts` and `bound`
    warnings: Tuple[CallWarning, ...] = ()
    numbers: Mapping[str, Any] = field(default_factory=dict)
    scope: Mapping[str, Any] = field(default_factory=dict)
    digest_keys: Tuple[str, ...] = ()
    consequence_template: str = ""
    deletion: Optional["DeletionPlan"] = None
    names: Mapping[str, str] = field(default_factory=dict)
    tool: str = ""

    def __post_init__(self):
        problems = number_problems(self.numbers) + number_problems(self.scope, "scope")
        problems += [f"scope: {k} is not one of {', '.join(SCOPE_KEYS)}" for k in self.scope if k not in SCOPE_KEYS]
        problems += [f"warnings: {w!r} is not a CallWarning" for w in self.warnings if not isinstance(w, CallWarning)]
        problems += [f"digest_keys: {k!r} is not a key of bound" for k in self.digest_keys if k not in self.bound]
        if self.deletion is not None and not isinstance(self.deletion, DeletionPlan):
            problems.append("deletion must be a DeletionPlan")
        if problems:
            raise ValueError("; ".join(problems))

    def fingerprint(self) -> str:
        """A hash of everything the person was shown and the handler is bound to (with the warning codes and the
        deletion plan when there are any)."""
        data = [self.consequence, [[f.label, f.value, f.source] for f in self.facts], dict(self.bound), self.cap]
        if self.warnings:
            data.append(sorted(w.code for w in self.warnings))
        if self.deletion is not None:
            data.append(self.deletion.sha256())
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def decision_digest(self) -> str:
        """What a decision about this call is bound to (design 4.4): the tool, the covered part of `bound`, the
        warning codes with their kinds, the cap and scope, every fact that is not volatile, the stable numbers, the
        consequence template and the deletion plan. Volatile facts and numbers and the rendered consequence stay out,
        so free space that changes without changing a warning never makes the question new."""
        volatile = set(volatile_numbers(self.tool))
        bound = {k: self.bound[k] for k in self.digest_keys} if self.digest_keys else dict(self.bound)
        data = {
            "tool": self.tool,
            "bound": bound,
            "warnings": sorted([w.code, w.kind] for w in self.warnings),
            "cap": self.cap,
            "scope": dict(self.scope),
            "facts": [[f.label, f.value, f.source] for f in self.facts if not f.volatile],
            "numbers": {k: v for k, v in self.numbers.items() if k not in volatile},
            # without a template the look-up never said which part of its consequence is volatile: all of it counts
            "consequence_template": self.consequence_template or self.consequence,
            "deletion": self.deletion.sha256() if self.deletion is not None else None,
        }
        return _sha256(_canonical(data))


# --- deletions (design 3.10, round 5 f) ------------------------------------------------------------------------------

DELETION_KINDS = ("artifact", "model_file", "download", "knowledge_snapshot", "trash_entry", "docker_image",
                  "build_cache", "pack", "module", "deploy_package", "exported_pack",
                  "runtime_build", "docker_container",  # Ditto's runtime modes (runtime/runtime_mode.py),
                  "runtime_record",  # the old mode's entries in Ditto's records and its image build log (W2), and
                  "knowledge_store",  # the old knowledge base storage after its conversion (rag/migration.py)
                  "build_dependency",  # a pinned source of the build dependency cache (runtime/build/deps.py)
                  "failed_build",  # what an interrupted build left, kept to continue it (Core/interrupted_build.py)
                  "bundle")  # a stored board bundle (runtime/bundle/generator.py BundleStore)
# Docker's own objects have no host path; every other item is listed with its full path.
NO_PATH_KINDS = ("docker_image", "build_cache", "docker_container")
DeletionKind = Literal[DELETION_KINDS]  # type: ignore[valid-type]
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _looks_absolute(text: str) -> bool:
    """An absolute path of any system (POSIX, the home folder, a Windows drive or share)."""
    return text.startswith(("/", "~")) or bool(_WINDOWS_ABSOLUTE.match(text))


def _is_full_path(text: str) -> bool:
    """A full path of either system, whatever this computer runs: a Windows host lists a board's POSIX paths, and
    os.path.isabs would refuse them there ("~" is not a full path: it hides whose home it is)."""
    return text.startswith("/") or bool(_WINDOWS_ABSOLUTE.match(text))


class DeletionItem(BaseModel):
    """One thing a call would delete, as the person sees it in the framework's own window: the speaking name, the
    full path (person view only; "" for Docker's own objects), the file name, the size and what it is in framework
    words. An AI sees only DeletionPlan.ai_view()."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DeletionKind
    speaking_name: str = Field(min_length=1, max_length=300)
    full_path: str = Field(default="", max_length=4096)
    file_name: str = Field(min_length=1, max_length=400)
    size_bytes: int = Field(ge=0)
    what: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _path(self) -> "DeletionItem":
        if self.kind in NO_PATH_KINDS:
            if self.full_path:
                raise ValueError(f"a {self.kind} has no host path")
        elif not self.full_path or not _is_full_path(self.full_path):
            raise ValueError(f"a {self.kind} is listed with its full path")
        elif "/" in self.file_name or "\\" in self.file_name:
            raise ValueError("file_name is the item's name, not a path")
        # The speaking name is what an AI sees of the item (ai_view): never its path, nor any absolute path.
        name = self.speaking_name.strip()
        if (self.full_path and self.full_path in name) or _looks_absolute(name):
            raise ValueError("speaking_name is a label an AI may see, never a path")
        return self


class DeletionPlan(BaseModel):
    """Everything one call would delete (design 3.10). The person accepts exactly this plan; its sha256 is audited
    (`deletion_sha256`) and binds the deletion to it. `mode_offered`: "trash" first when a trash exists for the
    kind."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: List[DeletionItem] = Field(min_length=1, max_length=1000)
    total_bytes: int = Field(ge=0)
    mode_offered: Tuple[Literal["trash", "permanent"], ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _consistent(self) -> "DeletionPlan":
        if self.total_bytes != sum(i.size_bytes for i in self.items):
            raise ValueError("total_bytes is not the sum of the items")
        if len(set(self.mode_offered)) != len(self.mode_offered):
            raise ValueError("a mode is offered twice")
        return self

    def ai_view(self) -> List[Dict[str, Any]]:
        """What an AI may see of the plan: label, kind and size of each item, never a path (round 5 f)."""
        return [{"label": i.speaking_name, "kind": i.kind, "size_bytes": i.size_bytes} for i in self.items]

    def sha256(self) -> str:
        return _sha256(_canonical(self.model_dump(mode="json")))


@dataclass(frozen=True)
class Rate:
    """`count` calls per `per` seconds for one client, at most `burst` at once (default `count`)."""
    count: int
    per: float
    burst: int = 0


@dataclass(frozen=True)
class ToolLimits:
    """A tool's limits (design 6.5): the result size, a rate and quotas per client. The registry enforces them
    before the policy and the question; a limit hit is an audited refusal (`rate_limited`, reason code
    `limit_reached`) that says when to try again."""
    max_result_bytes: int = MAX_RESULT_BYTES
    rate: Optional[Rate] = None      # None: the class default (read tools: CallerLimits.read_rate; others: none)
    per_day: int = 0                 # runs per client in 24 hours (0: no quota)
    concurrent: int = 0              # unfinished runs per client at a time (0: no quota), counted by `running`
    running: Optional[Callable[[Any], int]] = None  # running(ctx) -> how many of its runs the caller has unfinished
    what: str = "calls"              # the plural noun of a refusal ("builds")


@dataclass(frozen=True)
class CallerLimits:
    """The registry-wide rows of the limits table (design 6.5), per client (an MCP caller, Ditto)."""
    read_rate: Rate = Rate(20, 10.0, burst=40)  # the read tools together
    questions_per_hour: int = 30                # questions to the person (pending approvals and requests too)
    decline_cooldown: float = 600.0             # after a "no", the same call is refused without asking this long
    declines_to_pause: int = 3                  # this many "no" to one tool ...
    decline_window: float = 1800.0              # ... within this time ...
    decline_pause: float = 1800.0               # ... pause the questions for that tool this long
    repeat_cooldown: float = 600.0              # a call the repeat guard refuses may run again after this long


# The limits table: the registry-wide rows, and the quota of start_build (3 unfinished, 20 starts a day per client).
# The global queue cap (20 unfinished build requests in one framework process, every caller) is the orchestrator's
# own: orchestrator.Core.orchestrator.MAX_UNFINISHED_REQUESTS.
LIMITS = CallerLimits()
BUILD_QUOTA = {"per_day": 20, "concurrent": 3, "what": "builds"}
DAY = 86400.0


CHANNELS = ("framework", "elicitation")  # confirmation channels, strongest first


def example_problems(examples: List[Dict[str, Any]], validate: Callable[[Dict[str, Any]], Any]) -> List[str]:
    """Example calls that do not pass the tool's own input check (each example must be a valid call)."""
    problems = []
    for example in examples:
        try:
            validate(example)
        except Exception as e:  # noqa: BLE001 - every failure is a problem with the example
            problems.append(f"{example}: {e}")
    return problems
