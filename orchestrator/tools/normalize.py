#!/usr/bin/env python3
"""
The fool-proof layer: one normalizer for every caller (Ditto in process and every MCP client).

Small models get the same tools as large ones; this layer makes them hard to misuse and easy to recover from,
without ever widening a call:

- Tool names: an exact name, or the same name after lowercasing and ignoring `_`, `-`, space and `.`
  (`StartBuild`, `start-build`) runs that tool, with a note. Anything else is refused with up to three suggestions
  from the tools the caller can see; edit distance only suggests, it never picks a tool.
- Argument names: declared aliases (`quant` -> `quantization`) are the only mapping and are renamed, with a note.
  Any other unknown key, also a field name in other letter case (`Board`), is refused with a suggestion (nothing
  is dropped, nothing undeclared is mapped). An alias and its field with different values are a conflict.
- Values: the same coercion on both paths (text to number, "yes" to true, JSON text for lists), units only where a
  field declares one, closed value lists matched exactly, by canonical form if unique, or by a declared alias.
  Never by edit distance: Q4_K_M and Q5_K_M, or rk3566 and rk3568, differ by one character.
- Never repaired: ids (only surrounding whitespace is removed), paths, URLs, values that carry trust (digests,
  commits, hashes, authorizing booleans such as `approve`), file names beyond one unique case-insensitive match.

Every repair is recorded (kind and field; values only for closed-list fields) for the question, the result's notes
and the audit log. Running the normalizer twice changes nothing.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, Union, get_args, get_origin

from annotated_types import Ge, Gt, Le, Lt

from orchestrator.tools.spec import Choices, FieldMeta

# Reason and input codes (design 6.11, D6 and D10). A refusal or error always carries one.
# question_changed: the facts behind a waiting question changed, so it has a new version (delegation design 3.3).
INPUT_CODES = ("unknown_tool", "unknown_argument", "missing_argument", "invalid_value", "unknown_id",
               "waiting_for_approval", "question_changed")
REASON_CODES = ("missing_information", "not_supported", "no_tool", "permission_denied", "declined", "needs_person",
                "hardware_unavailable", "insufficient_resources", "limit_reached", "failed", "repeat_refused")
CODES = INPUT_CODES + REASON_CODES
# A call that waits for a decision has not failed: never counted by the repeat guard or as a failure in a loop.
PENDING_CODES = ("waiting_for_approval", "question_changed")

_NAME_CHARS = re.compile(r"^[A-Za-z0-9_\-. ]{1,80}$")
_NAME_SEPARATORS = re.compile(r"[_\-. ]")
_EMPTY = ("", "null", "none", "default")
_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hour|hours)?\s*$",
                       re.IGNORECASE)
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|kib|mb|mib|gb|gib|tb|tib)?\s*$", re.IGNORECASE)
_SECONDS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
            "minutes": 60, "h": 3600, "hour": 3600, "hours": 3600}
# GB and GiB are defined once, here: GB is 10^9 bytes, GiB 2^30.
_BYTES = {"b": 1, "kb": 10 ** 3, "mb": 10 ** 6, "gb": 10 ** 9, "tb": 10 ** 12,
          "kib": 2 ** 10, "mib": 2 ** 20, "gib": 2 ** 30, "tib": 2 ** 40}


class NormalizeError(ValueError):
    """The call cannot be run as written. `code` is one of CODES; `next` says what to do."""

    def __init__(self, message: str, code: str = "invalid_value", next: str = "",
                 allowed: Optional[Sequence[str]] = None, suggestions: Optional[Sequence[str]] = None,
                 field: str = ""):
        super().__init__(message)
        self.code = code
        self.next = next
        self.allowed = list(allowed or [])
        self.suggestions = list(suggestions or [])
        self.field = field
        # The repairs made before the refusal (a declared alias read before an invalid value): the error shows
        # them as notes, as a successful result would, and the audit records them.
        self.repairs: List["Repair"] = []


@dataclass(frozen=True)
class Repair:
    """One change the normalizer made. `kind`: name, alias, key, canonical, value_alias, type, json, split, unit,
    empty, reduced, strip, filename."""
    kind: str
    field: str
    wrote: Any = None
    now: Any = None
    show_values: bool = True  # False: the audit log records kind and field only (values may be free text)

    def note(self, tool: str = "") -> str:
        if self.kind == "name":
            return f"ran {self.now} (you wrote {_short(self.wrote)})"
        if self.kind in ("alias", "key"):
            return f"argument '{_short(self.wrote)}' read as '{self.now}'"
        if self.kind == "empty":
            return f"{self.field}: {_short(self.wrote)} read as not given (the framework default applies)"
        if self.kind == "reduced":
            return f"{self.field} reduced to {self.now}, the maximum (you asked for {self.wrote})"
        if self.kind == "json":
            return f"{self.field}: JSON text read as {'a list' if isinstance(self.now, list) else 'an object'}"
        if self.kind == "split":
            return f"{self.field}: text split at commas into a list"
        return f"{self.field}: {_short(self.now)} (you wrote {_short(self.wrote)})"

    def audit(self) -> Dict[str, Any]:
        entry = {"kind": self.kind, "field": self.field}
        if self.show_values and self.kind not in ("alias", "key"):
            entry.update({"wrote": _short(self.wrote, 64), "now": _short(self.now, 64)})
        if self.kind in ("name", "alias", "key"):
            entry["wrote"] = _short(self.wrote, 64)
        return entry


def _short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def canonical_name(name: str) -> str:
    """Letter case and the separators `_`, `-`, space and `.` ignored."""
    return _NAME_SEPARATORS.sub("", name.lower())


def canonical_value(value: str) -> str:
    return canonical_name(value)


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (for suggestions only)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def suggest(wrong: str, candidates: Iterable[str], limit: int = 3, first: Iterable[str] = ()) -> List[str]:
    """Up to `limit` candidates for a wrong value: prefix matches first, then the closest by edit distance (on the
    canonical forms). `first`: candidates to prefer (e.g. read tools for a name starting with get/list)."""
    cw = canonical_name(wrong)
    pool = list(dict.fromkeys(candidates))
    preferred = set(first)
    scored = []
    for c in pool:
        cc = canonical_name(c)
        prefix = cc.startswith(cw) or cw.startswith(cc) if cw and cc else False
        dist = _distance(cw, cc)
        if prefix or dist <= max(2, len(cw) // 3):
            scored.append((c not in preferred, not prefix, dist, c))
    return [c for *_, c in sorted(scored)][:limit]


# --- tool names ----------------------------------------------------------------------------------------------------

_READ_PREFIXES = ("get", "list", "read", "search")


def resolve_name(name: Any, registered: Sequence[str], visible: Sequence[str],
                 read_tools: Sequence[str] = ()) -> Tuple[str, Optional[Repair]]:
    """The registered tool a name means: exact, or one spelling variant (letter case and `_ - . space`). A name with
    any other character is never resolved. Anything else raises NormalizeError (code unknown_tool) with up to three
    suggestions from `visible` (the tools this caller is offered)."""
    if isinstance(name, str) and name in registered:
        return name, None
    if isinstance(name, str) and _NAME_CHARS.match(name):
        wanted = canonical_name(name)
        matches = [r for r in registered if canonical_name(r) == wanted]
        if len(matches) == 1:
            return matches[0], Repair("name", "", name, matches[0])
    shown = _short(name if isinstance(name, str) else repr(name), 64)
    first = read_tools if isinstance(name, str) and name.lower().startswith(_READ_PREFIXES) else ()
    suggestions = suggest(name, visible, first=first) if isinstance(name, str) else []
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise NormalizeError(f"Unknown tool '{shown}'.{hint} Call one of these exact names.", code="unknown_tool",
                         next=("Call one of these exact names: " + ", ".join(suggestions)) if suggestions else
                         "Use only the tool names you were given.", suggestions=suggestions)


def check_names_distinct(names: Iterable[str]) -> List[Tuple[str, str]]:
    """Pairs of tool names that a spelling variant could not tell apart (must be empty)."""
    seen: Dict[str, str] = {}
    clashes = []
    for name in names:
        key = canonical_name(name)
        if key in seen:
            clashes.append((seen[key], name))
        seen[key] = name
    return clashes


# --- choices -------------------------------------------------------------------------------------------------------

def match_choice(value: str, choices: Choices, field_name: str) -> Tuple[str, Optional[Repair]]:
    """A value from a closed list: exact, canonical form if it names exactly one value, or a declared alias
    (matched by its canonical form). A canonical form that names more than one value is refused with all of them;
    an unknown value is refused with the allowed list and up to three suggestions, never corrected."""
    if not isinstance(value, str):
        raise NormalizeError(f"'{field_name}' must be one of: {', '.join(choices.values)}.", field=field_name,
                             allowed=choices.values)
    wanted = canonical_value(value)
    targets: Dict[str, str] = {}
    for v in choices.values:
        if canonical_value(v) == wanted:
            targets.setdefault(v, "canonical")
    for alias, v in choices.aliases.items():
        if canonical_value(alias) == wanted and v in choices.values:
            targets.setdefault(v, "value_alias")
    where = f" {choices.where}" if choices.where else ""
    if len(targets) == 1:
        [(resolved, kind)] = targets.items()
        if resolved == value:
            return value, None
        return resolved, Repair(kind, field_name, value, resolved)
    if len(targets) > 1:
        names = sorted(targets)
        raise NormalizeError(f"'{_short(value, 64)}' for '{field_name}' is ambiguous{where}: it could mean "
                             f"{', '.join(names)}.", field=field_name, allowed=choices.values, suggestions=names,
                             next=f"Use one of these exact values: {', '.join(names)}.")
    suggestions = suggest(value, choices.values)
    hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
    raise NormalizeError(f"'{_short(value, 64)}' is not a valid {field_name}{where}. Allowed: "
                         f"{', '.join(choices.values)}.{hint}", field=field_name, allowed=choices.values,
                         suggestions=suggestions, next=f"Use one of the allowed values for '{field_name}'.")


def match_filename(name: str, candidates: Sequence[str], field_name: str) -> Tuple[str, Optional[Repair]]:
    """A file name: exact, or one unique case-insensitive match (never with separators ignored: repositories hold
    near-identical names such as model-q4_k_m.gguf and model.Q4_K_M.gguf)."""
    if name in candidates:
        return name, None
    folded = [c for c in candidates if c.casefold() == name.casefold()]
    if len(folded) == 1:  # the audit log records kind and field only (a file name is free text)
        return folded[0], Repair("filename", field_name, name, folded[0], show_values=False)
    suggestions = suggest(name, candidates)
    raise NormalizeError(f"No file named '{_short(name, 120)}'." + (f" Did you mean {', '.join(suggestions)}?"
                                                                  if suggestions else ""),
                         code="invalid_value", field=field_name, suggestions=suggestions,
                         next="Use a file name exactly as listed.")


# --- types ---------------------------------------------------------------------------------------------------------

def _base(annotation: Any) -> Tuple[Any, bool]:
    """(type without Optional, whether None is allowed)."""
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        optional = len(args) < len(get_args(annotation))
        return (args[0] if len(args) == 1 else annotation), optional
    return annotation, False


def _literal_values(annotation: Any) -> Optional[Tuple[str, ...]]:
    if get_origin(annotation) is Literal:
        values = get_args(annotation)
        if all(isinstance(v, str) for v in values):
            return tuple(values)
    return None


def _bounds(info) -> Tuple[Optional[float], Optional[float]]:
    low = high = None
    for m in info.metadata:
        if isinstance(m, Ge):
            low = m.ge
        elif isinstance(m, Gt):
            low = m.gt
        elif isinstance(m, Le):
            high = m.le
        elif isinstance(m, Lt):
            high = m.lt
    return low, high


def _number(text: str, integer: bool) -> Optional[Union[int, float]]:
    t = text.strip()
    if integer and re.fullmatch(r"[+-]?\d{1,18}", t):
        return int(t)
    if not integer and re.fullmatch(r"[+-]?(\d{1,18}(\.\d*)?|\.\d+)([eE][+-]?\d{1,3})?", t):
        return float(t)
    return None


def _unit(value: Any, unit: str, integer: bool) -> Optional[Union[int, float]]:
    if not isinstance(value, str):
        return None
    pattern, table = (_DURATION, _SECONDS) if unit == "seconds" else (_SIZE, _BYTES)
    m = pattern.match(value)
    if not m:
        return None
    amount = float(m.group(1)) * table[(m.group(2) or ("s" if unit == "seconds" else "b")).lower()]
    return int(round(amount)) if integer else amount


# --- arguments -----------------------------------------------------------------------------------------------------

@dataclass
class Normalized:
    arguments: Dict[str, Any]
    repairs: List[Repair] = field(default_factory=list)


def _field_list(spec) -> str:
    return ", ".join(spec.input_model.model_fields)


def normalize_arguments(spec, raw: Any) -> Normalized:
    """The arguments as the tool means them (canonical names and values) and the repairs made. Raises
    NormalizeError for anything it may not repair; the error carries the repairs made before it (`repairs`), so a
    refusal names a declared alias it read as clearly as a successful result does."""
    repairs: List[Repair] = []
    try:
        return _normalize(spec, raw, repairs)
    except NormalizeError as e:
        e.repairs = list(repairs)
        raise


def _normalize(spec, raw: Any, repairs: List[Repair]) -> Normalized:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise NormalizeError(f"The arguments for {spec.name} must be an object with named fields.",
                             next=f"Call {spec.name} with named arguments ({_field_list(spec)}).")
    fields = spec.input_model.model_fields
    renamed: Dict[str, Tuple[str, Any]] = {}  # field -> (key written, value)
    unknown: List[str] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            unknown.append(repr(key))
            continue
        # Declared aliases are the only mapping (maintainer decision): a key in other letter case or with other
        # separators is refused with a suggestion, never mapped by itself.
        if key in fields:
            target, kind = key, None
        elif key in spec.arg_aliases:
            target, kind = spec.arg_aliases[key], "alias"
        else:
            unknown.append(key)
            continue
        if target in renamed:
            first_key, first_value = renamed[target]
            if first_value != value:
                raise NormalizeError(f"'{_short(first_key, 40)}' and '{_short(key, 40)}' both set '{target}' for "
                                     f"{spec.name}, with different values.", field=target,
                                     next=f"Give '{target}' once.")
            continue
        renamed[target] = (key, value)
        if kind:
            repairs.append(Repair(kind, target, key, target, show_values=False))
    if unknown:
        valid = list(fields)
        hints = []
        for key in unknown:
            close = suggest(key, valid + list(spec.arg_aliases), limit=1)
            if close:
                hints.append(f"Did you mean '{spec.arg_aliases.get(close[0], close[0])}'?")
        names = ", ".join(f"'{_short(k, 40)}'" for k in unknown)
        if not valid:
            raise NormalizeError(f"Unknown argument{'s' if len(unknown) > 1 else ''} {names} for {spec.name}. "
                                 f"{spec.name} takes no arguments.", code="unknown_argument",
                                 next=f"Call {spec.name} without arguments.")
        raise NormalizeError(f"Unknown argument{'s' if len(unknown) > 1 else ''} {names} for {spec.name}. "
                             f"Valid arguments: {', '.join(valid)}." + (f" {' '.join(hints)}" if hints else ""),
                             code="unknown_argument", allowed=valid,
                             next=f"Call {spec.name} with only these arguments: {', '.join(valid)}.")
    args: Dict[str, Any] = {}
    for name, info in fields.items():  # schema order, so a computed choice sees the fields before it
        if name not in renamed:
            continue
        value = renamed[name][1]
        meta: FieldMeta = spec.fields.get(name) or FieldMeta()
        keep, value = _value(spec, name, info, meta, value, args, repairs)
        if keep:
            args[name] = value
    return Normalized(args, repairs)


def _value(spec, name: str, info, meta: FieldMeta, value: Any, args: Dict[str, Any],
           repairs: List[Repair]) -> Tuple[bool, Any]:
    """(keep the field, its value)."""
    annotation, optional = _base(info.annotation)
    if meta.kind in ("path", "url"):
        return True, value
    if meta.kind == "trust":
        if annotation is bool and not isinstance(value, bool):
            raise NormalizeError(f"'{name}' must be true or false (a JSON boolean), not {_short(value, 40)}.",
                                 field=name, next=f"Give '{name}' as true or false.")
        return True, value
    if meta.kind == "id":
        if isinstance(value, str) and value != value.strip():
            return True, value.strip()
        return True, value
    if isinstance(value, str) and value.strip().lower() in _EMPTY and meta.null_means_default and optional \
            and not info.is_required():
        allowed = (meta.choices or ()) + (_literal_values(annotation) or ())
        if value.strip().lower() not in {a.lower() for a in allowed}:
            repairs.append(Repair("empty", name, value, None))
            return False, None
    if value is None:
        return True, value
    # Units, numbers and booleans
    if annotation in (int, float) and not isinstance(value, bool):
        if meta.unit:
            converted = _unit(value, meta.unit, annotation is int)
            if converted is not None and converted != value:
                repairs.append(Repair("unit", name, value, converted))
                value = converted
        if isinstance(value, str):
            number = _number(value, annotation is int)
            if number is not None:
                repairs.append(Repair("type", name, value, number))
                value = number
        if meta.kind == "amount" and isinstance(value, (int, float)):
            low, high = _bounds(info)
            if high is not None and value > high:
                repairs.append(Repair("reduced", name, value, high))
                value = int(high) if annotation is int else high
        return True, value
    if annotation is bool:
        if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
            converted = value.strip().lower() in _TRUE
            repairs.append(Repair("type", name, value, converted))
            return True, converted
        return True, value
    origin = get_origin(annotation)
    if origin in (list, List, tuple) and isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, list):
                repairs.append(Repair("json", name, value, parsed, show_values=False))
                value = parsed
        elif meta.split:
            parts = [p.strip() for p in text.split(",") if p.strip()]
            repairs.append(Repair("split", name, value, parts, show_values=False))
            value = parts
    elif origin in (dict, Dict) and isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            repairs.append(Repair("json", name, value, parsed, show_values=False))
            value = parsed
    # Closed lists: a Literal in the schema, declared choices, or computed ones
    literal = _literal_values(annotation) or list_item_literal(annotation)
    choices = None
    if literal:
        choices = Choices(literal)
    elif meta.choices:
        choices = Choices(tuple(meta.choices), meta.aliases)
    elif meta.choices_from is not None:
        choices = meta.choices_from(dict(args))
        if choices is not None and meta.aliases:
            choices = Choices(choices.values, {**meta.aliases, **choices.aliases}, choices.where)
    if choices is not None and isinstance(value, str):
        resolved, repair = match_choice(value, choices, name)
        if repair is not None:
            repairs.append(repair)
        return True, resolved
    if choices is not None and isinstance(value, list):
        items = []
        for item in value:
            resolved, repair = match_choice(item, choices, name) if isinstance(item, str) else (item, None)
            if repair is not None:
                repairs.append(repair)
            items.append(resolved)
        return True, items
    return True, value


def list_item_literal(annotation: Any) -> Optional[Tuple[str, ...]]:
    """The allowed values of a List[Literal[...]] field (for messages)."""
    annotation, _ = _base(annotation)
    if get_origin(annotation) in (list, List):
        args = get_args(annotation)
        return _literal_values(args[0]) if args else None
    return None
