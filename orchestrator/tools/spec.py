#!/usr/bin/env python3
"""
Declarations a tool's author adds to its ToolSpec beyond the schemas: how the fool-proof layer may treat each
input field, which argument names are aliases, whether a confirmation may offer a standing approval, and the
tool's limits. The registry and the normalizer (orchestrator/tools/normalize.py) read them; nothing here runs a
tool.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

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
    the framework's own words."""
    label: str
    value: str
    source: str = ""


@dataclass(frozen=True)
class CallFacts:
    """What a tool's `prepare` looked up before the question (design principle 11): the consequence in plain words
    (the question's first line), the facts the person is shown, the values the handler must use (`bound`) and a
    cap the facts call for (the value of a Decision, e.g. "ask" for a destructive healing strategy even when the
    tool is allowed). The handler gets exactly this object and uses `bound` or refuses. `repairs`: what the look-up
    repaired (normalize.Repair; a file name matched in another letter case): the registry names them in the result's
    notes and in every refusal after the look-up, and records them in the audit log, as it does the normalizer's. A
    look-up that refuses after such a repair puts it on its error (`repairs`, a list), so the refusal names it too."""
    consequence: str
    facts: Tuple[Fact, ...] = ()
    bound: Mapping[str, Any] = field(default_factory=dict)
    cap: Optional[str] = None
    repairs: Tuple[Any, ...] = ()  # not part of the fingerprint: what the person is shown is `facts` and `bound`

    def fingerprint(self) -> str:
        """A hash of everything the person was shown and the handler is bound to."""
        data = [self.consequence, [[f.label, f.value, f.source] for f in self.facts], dict(self.bound), self.cap]
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


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
