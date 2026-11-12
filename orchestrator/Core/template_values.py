#!/usr/bin/env python3
"""
The general template's variables one value at a time (docs/DESIGN.md, Targets, hardware and templates: the form per
value): the fields of a format, generated from the schema in orchestrator/schemas/target_template.py (each variable's
path, the kind of value it takes, its choices, whether it is required, its explanation and its default), how a value
is read from the text a person types and written back, and the checks of one format with each reason placed at the
field it belongs to.

One source for every way a value is asked: the wizard's form and the edit step's form (gui/format_form.py), the
terminal's questions and `--set FORMAT.PATH=VALUE` of `llm-cli module create|edit`, `llm-cli module fields --json`
and Ditto's proposal of the values (Core/format_proposal.py), which names its values by the same paths.
"""

from __future__ import annotations

import copy
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

KINDS = ("text", "list", "choice", "multi", "bool", "records")

# Help texts of the fields whose model gives none (the SDK needs of orchestrator/schemas/targets.py).
EXTRA_HELP = {
    "sdk.toolkit": "The vendor's toolkit that converts, e.g. HailoRT DFC",
    "sdk.version": "Its version, e.g. 3.27.0",
    "sdk.runtime_key": "The probe key of the board's runtime version, e.g. HAILO_RUNTIME_VERSION",
    "sdk.runtime_name": "The runtime's name as the person reads it, e.g. libhailort",
    "sdk.driver_key": "The probe key of the board's NPU driver version",
    "sdk.driver_min": "The driver's minimum version (below it the build stops)",
    "sdk.driver_recommended": "The driver's recommended version (below it a warning)",
    "id": "The format's id as builds name it, lower case, e.g. hef",
    "input": "The model formats the converter takes: hf (a Hugging Face model), onnx, gguf",
}


@dataclass
class FieldSpec:
    """One variable of a format: `path` (dotted, e.g. converter.args), `kind` (text, list, choice, multi, bool or
    records), whether it is required, its explanation, its choices, its default, the optional group it belongs to
    (sdk) and, for records (runtime_libraries, vendor_files), the fields of one record."""
    path: str
    kind: str
    required: bool
    help: str
    choices: Tuple[str, ...] = ()
    default: Any = None
    group: str = ""
    columns: List["FieldSpec"] = field(default_factory=list)
    choices_from: str = ""

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    def as_dict(self) -> Dict[str, Any]:
        out = {"path": self.path, "kind": self.kind, "required": self.required, "help": self.help}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.choices_from:
            out["choices_from"] = self.choices_from
        if self.default not in (None, "", [], False):
            out["default"] = self.default
        if self.group:
            out["group"] = self.group
        if self.columns:
            out["columns"] = [c.as_dict() for c in self.columns]
        return out


def _unwrap_optional(annotation: Any) -> Tuple[Any, bool]:
    if typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _fields_of(model: type, prefix: str = "", group: str = "", in_group: bool = False) -> List[FieldSpec]:
    out: List[FieldSpec] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        annotation, optional = _unwrap_optional(info.annotation)
        help_text = info.description or EXTRA_HELP.get(path, "")
        help_text = EXTRA_HELP.get(path, help_text) if path in ("id", "input") else help_text
        required = info.is_required()
        default = None
        if info.default is not PydanticUndefined and not _is_model(type(info.default)):
            default = info.default
        if _is_model(annotation):
            sub_group = path if optional else group
            out += _fields_of(annotation, path + ".", sub_group, in_group or optional)
            continue
        origin = typing.get_origin(annotation)
        if origin in (list, List):
            (item,) = typing.get_args(annotation) or (str,)
            if _is_model(item):
                out.append(FieldSpec(path, "records", required, help_text, group=group,
                                     columns=_fields_of(item), default=[]))
            elif typing.get_origin(item) is typing.Literal:
                out.append(FieldSpec(path, "multi", required, help_text, tuple(typing.get_args(item)), [], group))
            else:
                out.append(FieldSpec(path, "list", required, help_text, default=list(default or []), group=group))
            continue
        if typing.get_origin(annotation) is typing.Literal:
            out.append(FieldSpec(path, "choice", required, help_text, tuple(typing.get_args(annotation)), default,
                                 group))
        elif annotation is bool:
            out.append(FieldSpec(path, "bool", False, help_text, default=bool(default), group=group))
        else:
            out.append(FieldSpec(path, "text", required, help_text, default=default, group=group))
    for spec in out:
        if spec.path == "default_quantization":
            spec.choices_from = "quantizations"
    return out


def format_fields() -> List[FieldSpec]:
    """The fields of one format, in the schema's order, generated from FormatVariables."""
    from orchestrator.schemas.target_template import FormatVariables
    return _fields_of(FormatVariables)


def field_by_path(path: str, fields: Optional[Sequence[FieldSpec]] = None) -> Optional[Tuple[FieldSpec, Optional[int],
                                                                                            Optional[FieldSpec]]]:
    """(the field, the record's index, the record's column) a path names: `converter.args`, or a value of one record,
    `runtime_libraries.0.version`; None for a path the template does not have."""
    fields = list(fields or format_fields())
    for spec in fields:
        if spec.path == path:
            return spec, None, None
        if spec.kind == "records" and path.startswith(spec.path + "."):
            rest = path[len(spec.path) + 1:]
            index, _, column = rest.partition(".")
            if not index.isdigit() or int(index) > 63:
                return None
            if not column:
                return spec, int(index), None
            col = next((c for c in spec.columns if c.path == column), None)
            return (spec, int(index), col) if col is not None else None
    return None


# --- values from text and back -------------------------------------------------------------------------------------

def parse_text(spec: FieldSpec, text: Any) -> Any:
    """A value of `spec` from what a person typed (a list as words separated by spaces or commas; a bool as yes or
    no). Empty text is None (the value is left out). ValueError for a bool that is neither."""
    if not isinstance(text, str):
        return text
    raw = text.strip()
    if spec.kind in ("list", "multi"):
        words = [w for w in re.split(r"[\s,]+", raw) if w]
        return words or None
    if spec.kind == "bool":
        if not raw:
            return None
        if raw.lower() in ("yes", "y", "true", "1", "on", "ja", "j"):
            return True
        if raw.lower() in ("no", "n", "false", "0", "off", "nein"):
            return False
        raise ValueError(f"{spec.path}: yes or no")
    return raw or None


def value_text(spec: FieldSpec, value: Any) -> str:
    """The text a field shows for a value (the inverse of parse_text)."""
    if value is None:
        return ""
    if spec.kind in ("list", "multi"):
        return " ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    if spec.kind == "bool":
        return "yes" if value else "no"
    return str(value)


def get_value(values: Dict[str, Any], path: str) -> Any:
    """The value at a dotted path of a format's values (a record's value by its index); None when it is not there."""
    node: Any = values
    for part in path.split("."):
        if isinstance(node, list) and part.isdigit():
            node = node[int(part)] if int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node


def set_value(values: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    """Sets (None: removes) the value at a dotted path of a format's values, making the groups and records on the way;
    an empty optional group is removed. Returns the values."""
    parts = path.split(".")
    node: Any = values
    trail = []
    for i, part in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        trail.append((node, part))
        if isinstance(node, list):
            index = int(part)
            while len(node) <= index:
                node.append({})
            node = node[index]
            continue
        if node.get(part) is None:
            node[part] = [] if nxt.isdigit() else {}
        node = node[part]
    last = parts[-1]
    if isinstance(node, list):
        index = int(last)
        while len(node) <= index:
            node.append({})
        node[index] = value
    elif value is None or value == [] and path not in ("runtime_libraries", "vendor_files"):
        node.pop(last, None)
    else:
        node[last] = value
    for parent, key in reversed(trail):   # an emptied group (sdk, board_paths) goes
        child = parent[int(key)] if isinstance(parent, list) else parent.get(key)
        if isinstance(child, dict) and not child and isinstance(parent, dict):
            parent.pop(key, None)
    return values


def apply_sets(formats: List[Dict[str, Any]], assignments: Sequence[str]) -> List[Dict[str, Any]]:
    """`--set FORMAT.PATH=VALUE` on a list of formats: FORMAT is the format's id (a new id adds a format), PATH a field
    (records by index: runtime_libraries.0.version), VALUE its text (a list as words). ValueError for a path the
    template does not have or a line that is no assignment."""
    out = copy.deepcopy([dict(f) for f in formats])
    fields = format_fields()
    for line in assignments:
        key, sep, text = str(line).partition("=")
        fmt_id, dot, path = key.strip().partition(".")
        if not sep or not dot or not fmt_id or not path:
            raise ValueError(f"--set {line[:80]!r}: FORMAT.PATH=VALUE, e.g. hef.converter.command=/opt/vendor/bin/conv")
        found = field_by_path(path, fields)
        if found is None:
            raise ValueError(f"--set {key[:80]}: the template has no variable {path!r} "
                             "(the template's fields list them)")
        spec, _index, column = found
        target = next((f for f in out if f.get("id") == fmt_id), None)
        if target is None:
            target = {"id": fmt_id}
            out.append(target)
        set_value(target, path, parse_text(column or spec, text))
    return out


# --- checks per field ----------------------------------------------------------------------------------------------

_HINTS = (("default_quantization", "default_quantization"), ("llama.cpp", "converter.command"),
          ("gguf is the template", "converter.command"), ("extension", "output_extension"),
          ("input:", "input"), ("runtime_libraries", "runtime_libraries"), ("runtime library", "runtime_libraries"),
          ("quantizations", "quantizations"), ("driver_", "sdk.driver_key"))


def _place(loc: Tuple[Any, ...], message: str) -> str:
    """The field a schema error belongs to: its location, else (a check of the whole format) the field it names."""
    parts = [str(p) for p in loc if not str(p).startswith("function-") and p not in ("str", "list")]
    if parts:
        path = ".".join(parts)
        top = parts[0]
        if top in ("runtime_libraries", "vendor_files"):
            return top
        return path
    for marker, path in _HINTS:
        if marker in message:
            return path
    return ""


def field_problems(values: Dict[str, Any]) -> Dict[str, List[str]]:
    """The checks of one format's values (the schema, then the module's checks of every word), each reason under the
    path of its field ("" for the format as a whole). Empty: every check passed."""
    from orchestrator.Core.module_generator import format_problems
    from orchestrator.schemas.target_template import FormatVariables
    out: Dict[str, List[str]] = {}
    try:
        FormatVariables.model_validate(values)
    except ValidationError as e:
        for err in e.errors():
            message = str(err.get("msg", "invalid")).removeprefix("Value error, ")
            loc = tuple(err.get("loc") or ())
            where = _place(loc, message)
            if loc and str(loc[0]) in ("runtime_libraries", "vendor_files") and len(loc) > 1:
                message = f"row {int(loc[1]) + 1}: " + (f"{loc[2]}: " if len(loc) > 2 else "") + message
            for part in message.split("; ") if not loc else [message]:
                out.setdefault(_place(loc, part) if not loc else where, []).append(part)
        return out
    _values, problems = format_problems([values])
    for problem in problems:
        out.setdefault("", []).append(problem)
    return out


def missing_required(values: Dict[str, Any], fields: Optional[Sequence[FieldSpec]] = None) -> List[str]:
    """The required fields a format's values leave empty (an optional group's required fields only when the group has
    a value)."""
    missing = []
    for spec in fields or format_fields():
        if not spec.required:
            continue
        if spec.group and not get_value(values, spec.group):
            continue
        value = get_value(values, spec.path)
        if value in (None, "", []):
            missing.append(spec.path)
    return missing


def catalogue_text(fields: Optional[Sequence[FieldSpec]] = None) -> str:
    """The fields as a model reads them: one line per path with its kind, choices and explanation (records by their
    columns, PATH.N.COLUMN)."""
    lines = []
    for spec in fields or format_fields():
        if spec.kind == "records":
            for col in spec.columns:
                lines.append(_line(f"{spec.path}.N.{col.path}", col))
            continue
        lines.append(_line(spec.path, spec))
    return "\n".join(lines)


def _line(path: str, spec: FieldSpec) -> str:
    bits = [spec.kind + (" (required)" if spec.required else "")]
    if spec.choices:
        bits.append("one of " + ", ".join(spec.choices))
    if spec.choices_from:
        bits.append("one of the " + spec.choices_from)
    if spec.default not in (None, "", [], False):
        bits.append(f"template default {spec.default}")
    return f"- {path}: {'; '.join(bits)}. {spec.help}".rstrip()
