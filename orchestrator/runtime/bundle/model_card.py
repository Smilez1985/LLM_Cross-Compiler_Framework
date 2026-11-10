#!/usr/bin/env python3
"""
The model card of an RKNN vision model (schema llmcf-model-card/1): what the board's vision engine llmcf-detect knows
of a model, written next to the `.rknn` by the Rockchip target module at conversion (`<name>.card.json`) and checked
here by the very rules the engine checks it with (rust/llmcf-detect/src/card.rs); tests/data/model_card_vectors.json
holds the vectors both sides are tested with.

The rules: at most 1 MiB, strict JSON (no NaN, no key twice, nothing after the value), every key known, every value
bounded, and the facts that belong together checked together: the task and its post-processing kind, the
parameters of that kind, the roles of its outputs, the labels and where the scores' activation is applied. The
fifteen kinds of the research are known by name; the six detection kinds of step 1 are built, a card of another kind
is refused with the reason that its step has not come yet. A card of another schema is refused, never guessed.

Where the host uses it: when a build's artefact is taken in (Core/builder.py records the card's verdict in
build_info.json), in the bundle plan (generator.py: a file of the format model-card is checked here, and its rknn
sha256 must be the model file's, so card and model are a pair; the plan's check line shows the verdict in the CLI
and in the Board bundle window alike).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "llmcf-model-card/1"
MAX_CARD_BYTES = 1024 * 1024
MAX_LABELS = 20000
MAX_LABEL_BYTES = 64
MAX_OUTPUTS = 32
PLATFORMS = ("rk3562", "rk3566", "rk3568", "rk3576", "rk3588", "rv1103", "rv1106", "rv1126b")
TASKS = ("detect", "segment", "pose", "obb", "classify", "semantic", "depth", "embed", "text", "text-detect")
ROLES = ("boxes", "scores", "score_sum", "objectness", "mask_coeffs", "protos", "keypoints", "angle", "class_logits",
         "embedding", "depth", "text_logits", "prob_map", "auto")
# every post-processing kind with its task; the first six (step 1) are built
KINDS: Tuple[Tuple[str, str], ...] = (
    ("yolo-anchor", "detect"), ("yolo-anchorfree-obj", "detect"), ("yolo-dfl", "detect"),
    ("yolo-nms-free", "detect"), ("device-nms", "detect"), ("ssd-prior", "detect"),
    ("yolo-seg-protos", "segment"), ("yolo-pose", "pose"), ("yolo-obb", "obb"), ("classification", "classify"),
    ("semantic-seg", "semantic"), ("depth-map", "depth"), ("embedding", "embed"), ("ctc-text", "text"),
    ("db-text-det", "text-detect"))
BUILT_KINDS = 6
CARD_SUFFIX = ".card.json"

_RE_LICENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$", re.ASCII)
_RE_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$", re.ASCII)
_RE_HEX40 = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_RE_HEX64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$", re.ASCII)
_RE_DIM = re.compile(r"^[a-z]{1,8}$", re.ASCII)


class CardError(ValueError):
    """A model card that is not what the schema takes; the message names the field."""


@dataclass(frozen=True)
class ModelCard:
    """A checked card: the parsed document and the facts the plan and the person read."""
    data: Dict[str, Any]

    @property
    def rknn_sha256(self) -> str:
        return self.data["rknn"]["sha256"]

    @property
    def kind(self) -> str:
        return self.data["postprocess"]["kind"]

    @property
    def task(self) -> str:
        return self.data["task"]

    @property
    def toolkit(self) -> str:
        return self.data["rknn"]["toolkit"]

    @property
    def platform(self) -> str:
        return self.data["rknn"]["platform"]

    def summary(self) -> str:
        i = self.data["input"]
        return (f"{self.data['model']['name']}: {self.task}, {self.kind}, {len(self.data['labels'])} labels, input "
                f"{i['width']} x {i['height']}, converted with rknn-toolkit2 {self.toolkit} for the {self.platform}")


def _is_text(v: Any, limit: int) -> bool:
    return isinstance(v, str) and 0 < len(v.encode("utf-8")) <= limit and \
        not any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in v)


def _keys(m: Any, what: str, required: Tuple[str, ...], optional: Tuple[str, ...] = ()) -> Dict[str, Any]:
    if not isinstance(m, dict):
        raise CardError(f"{what} is not a mapping")
    for k in m:
        if k not in required and k not in optional:
            raise CardError(f"{what}: unknown key {k}"[:200])
    for k in required:
        if k not in m:
            raise CardError(f"{what}: {k} is missing")
    return m


def _word(m: Dict[str, Any], key: str, what: str, choices: Tuple[str, ...]) -> str:
    v = m.get(key)
    if not isinstance(v, str) or v not in choices:
        raise CardError(f"{what}.{key} must be one of {', '.join(choices)}")
    return v


def _uint(v: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise CardError(f"{what} must be a whole number from {lo} to {hi}")
    return v


def _num(v: Any, what: str, lo: float, hi: float, open_low: bool) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v > hi or v < lo or (open_low and v == lo):
        raise CardError(f"{what} must be a number {'above' if open_low else 'from'} {lo} and at most {hi}")
    return float(v)


def _bool(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise CardError(f"{what} is true or false")
    return v


def _uints(v: Any, what: str, min_len: int, max_len: int, lo: int, hi: int) -> List[int]:
    if not isinstance(v, list) or not min_len <= len(v) <= max_len:
        raise CardError(f"{what} is a list of {min_len} to {max_len} whole numbers")
    return [_uint(x, what, lo, hi) for x in v]


def _three(v: Any, what: str, lo: float, open_low: bool) -> None:
    if not isinstance(v, list) or len(v) != 3:
        raise CardError(f"{what} is three numbers")
    for x in v:
        _num(x, what, lo, 1000.0, open_low)


def _strides(p: Dict[str, Any]) -> List[int]:
    s = _uints(p.get("strides"), "postprocess.strides", 1, 8, 1, 256)
    if len(set(s)) != len(s):
        raise CardError("postprocess.strides names a stride twice")
    return s


_PARAMS = {
    "yolo-anchor": (("kind", "strides", "anchors"), ()),
    "yolo-anchorfree-obj": (("kind", "strides"), ()),
    "yolo-dfl": (("kind", "strides", "reg_max"), ()),
    "yolo-nms-free": (("kind", "box_format", "top_k"), ("strides", "reg_max")),
    "device-nms": (("kind", "box_format", "box_units"), ()),
    "ssd-prior": (("kind", "encoding", "steps", "min_sizes", "background"), ("variances", "clip", "keypoints")),
}


def _post(p: Dict[str, Any], kind: str) -> Dict[str, Any]:
    required, optional = _PARAMS[kind]
    _keys(p, "postprocess", required, optional)
    facts: Dict[str, Any] = {"dfl": False, "keypoints": 0}
    if kind == "yolo-anchor":
        strides = _strides(p)
        anchors = p.get("anchors")
        if not isinstance(anchors, list) or len(anchors) != len(strides):
            raise CardError("postprocess.anchors has one list of anchors per stride")
        for per in anchors:
            if not isinstance(per, list) or not 1 <= len(per) <= 9:
                raise CardError("postprocess.anchors: each stride has 1 to 9 anchors")
            for pair in per:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise CardError("postprocess.anchors: an anchor is [width, height]")
                for x in pair:
                    _num(x, "postprocess.anchors", 0.0, 4096.0, True)
    elif kind == "yolo-anchorfree-obj":
        _strides(p)
    elif kind == "yolo-dfl":
        _strides(p)
        _uint(p.get("reg_max"), "postprocess.reg_max", 1, 64)
        facts["dfl"] = True
    elif kind == "yolo-nms-free":
        fmt = _word(p, "box_format", "postprocess", ("dfl", "xyxy"))
        _uint(p.get("top_k"), "postprocess.top_k", 1, 1000)
        if fmt == "dfl":
            if "strides" not in p or "reg_max" not in p:
                raise CardError("postprocess: box_format dfl needs strides and reg_max")
            _strides(p)
            _uint(p.get("reg_max"), "postprocess.reg_max", 1, 64)
            facts["dfl"] = True
        elif "strides" in p or "reg_max" in p:
            raise CardError("postprocess: box_format xyxy takes no strides or reg_max")
    elif kind == "device-nms":
        _word(p, "box_format", "postprocess", ("xyxy", "yxyx"))
        _word(p, "box_units", "postprocess", ("pixels", "normalized"))
    else:
        encoding = _word(p, "encoding", "postprocess", ("center-variance", "distance"))
        steps = _uints(p.get("steps"), "postprocess.steps", 1, 8, 1, 256)
        sizes = p.get("min_sizes")
        if not isinstance(sizes, list) or len(sizes) != len(steps):
            raise CardError("postprocess.min_sizes has one list of sizes per step")
        for per in sizes:
            if not isinstance(per, list) or not 1 <= len(per) <= 8:
                raise CardError("postprocess.min_sizes: each step has 1 to 8 sizes")
            for x in per:
                _num(x, "postprocess.min_sizes", 0.0, 4096.0, True)
        if "variances" in p:
            v = p["variances"]
            if not isinstance(v, list) or len(v) != 2:
                raise CardError("postprocess.variances is two numbers")
            for x in v:
                _num(x, "postprocess.variances", 0.0, 10.0, True)
        elif encoding == "center-variance":
            raise CardError("postprocess: encoding center-variance needs variances")
        if "clip" in p:
            _bool(p["clip"], "postprocess.clip")
        _bool(p.get("background"), "postprocess.background")
        if "keypoints" in p:
            facts["keypoints"] = _uint(p["keypoints"], "postprocess.keypoints", 0, 32)
    return facts


def _roles(kind: str, dfl: bool) -> Tuple[str, ...]:
    if kind in ("yolo-anchor", "yolo-anchorfree-obj"):
        return ("boxes", "auto")
    if kind == "yolo-dfl" or (kind == "yolo-nms-free" and dfl):
        return ("boxes", "scores", "score_sum", "auto")
    if kind == "yolo-nms-free":
        return ("boxes", "scores", "auto")
    if kind == "device-nms":
        return ("boxes", "scores")
    return ("boxes", "scores", "keypoints")


def _no_twice(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise CardError("the model card is not strict JSON (a key twice)")
        out[k] = v
    return out


def _no_constant(name: str) -> Any:
    raise CardError(f"the model card is not strict JSON ({name})")


def parse(data: Any) -> ModelCard:
    """A card from its bytes, text or parsed mapping, checked (see the module text); CardError with the reason."""
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_CARD_BYTES:
            raise CardError("the model card is larger than 1024 KiB")
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            raise CardError("the model card is not UTF-8") from None
        data = text
    if isinstance(data, str):
        if len(data.encode("utf-8")) > MAX_CARD_BYTES:
            raise CardError("the model card is larger than 1024 KiB")
        try:
            data = json.loads(data, object_pairs_hook=_no_twice, parse_constant=_no_constant)
        except CardError:
            raise
        except ValueError:
            raise CardError("the model card is not strict JSON") from None
    top = _keys(data, "the model card", ("schema", "model", "rknn", "input", "task", "postprocess", "labels",
                                         "thresholds", "score_activation", "outputs"))
    if top.get("schema") != SCHEMA:
        raise CardError(f"schema must be {SCHEMA}")
    model = _keys(top["model"], "model", ("name", "licence"), ("source", "commit"))
    if not _is_text(model["name"], 128):
        raise CardError("model.name is a plain text of 1 to 128 bytes")
    if "source" in model and not _is_text(model["source"], 200):
        raise CardError("model.source is a plain text of 1 to 200 bytes")
    if "commit" in model and not (isinstance(model["commit"], str) and _RE_HEX40.fullmatch(model["commit"])):
        raise CardError("model.commit is a commit (40 lower-case hex digits)")
    if not isinstance(model["licence"], str) or not _RE_LICENCE.fullmatch(model["licence"]):
        raise CardError("model.licence is an SPDX identifier or a short word")

    rknn = _keys(top["rknn"], "rknn", ("file", "sha256", "toolkit", "platform", "quantisation"))
    f = rknn["file"]
    if not isinstance(f, str) or not _RE_FILE.fullmatch(f) or not f.endswith(".rknn"):
        raise CardError("rknn.file is a plain file name ending in .rknn")
    if not isinstance(rknn["sha256"], str) or not _RE_HEX64.fullmatch(rknn["sha256"]):
        raise CardError("rknn.sha256 must be 64 lower-case hex digits")
    if not isinstance(rknn["toolkit"], str) or not _RE_VERSION.fullmatch(rknn["toolkit"]):
        raise CardError("rknn.toolkit is a version MAJOR.MINOR.PATCH")
    _word(rknn, "platform", "rknn", PLATFORMS)
    _word(rknn, "quantisation", "rknn", ("w8a8", "fp16"))

    i = _keys(top["input"], "input", ("width", "height", "layout", "colour", "resize", "mean", "std",
                                      "normalised_in_model", "type"), ("pad",))
    _uint(i["width"], "input.width", 1, 4096)
    _uint(i["height"], "input.height", 1, 4096)
    _word(i, "layout", "input", ("nhwc", "nchw"))
    _word(i, "colour", "input", ("rgb", "bgr"))
    _word(i, "resize", "input", ("letterbox", "stretch", "centre-crop"))
    if "pad" in i:
        _uint(i["pad"], "input.pad", 0, 255)
    _three(i["mean"], "input.mean", -1000.0, False)
    _three(i["std"], "input.std", 0.0, True)
    inside = _bool(i["normalised_in_model"], "input.normalised_in_model")
    kind_in = _word(i, "type", "input", ("uint8", "int8", "float32"))
    if inside and kind_in != "uint8":
        raise CardError("input: a model with its normalisation inside takes uint8 (input.type)")

    task = _word(top, "task", "the model card", TASKS)
    p = top["postprocess"]
    if not isinstance(p, dict):
        raise CardError("postprocess is not a mapping")
    kind = p.get("kind")
    if not isinstance(kind, str):
        raise CardError("postprocess.kind is not text")
    names = [k for k, _ in KINDS]
    if kind not in names:
        raise CardError(f"postprocess.kind must be one of {', '.join(names)}")
    pos = names.index(kind)
    if KINDS[pos][1] != task:
        raise CardError(f"postprocess.kind {kind} belongs to the task {KINDS[pos][1]}, not {task}")
    if pos >= BUILT_KINDS:
        raise CardError(f"postprocess.kind {kind} comes with a later step of the vision engine; it is not built yet")
    facts = _post(p, kind)

    labels = top["labels"]
    if not isinstance(labels, list) or not 1 <= len(labels) <= MAX_LABELS:
        raise CardError(f"labels is a list of 1 to {MAX_LABELS} names")
    if not all(_is_text(x, MAX_LABEL_BYTES) for x in labels):
        raise CardError(f"labels: each name is a plain text of 1 to {MAX_LABEL_BYTES} bytes")

    t = _keys(top["thresholds"], "thresholds", ("score", "nms_iou", "max_results"))
    _num(t["score"], "thresholds.score", 0.0, 1.0, True)
    _num(t["nms_iou"], "thresholds.nms_iou", 0.0, 1.0, True)
    _uint(t["max_results"], "thresholds.max_results", 1, 1000)
    activation = _word(top, "score_activation", "the model card", ("model", "sigmoid", "softmax"))
    if kind == "device-nms" and activation != "model":
        raise CardError("score_activation: a model with NMS inside gives its scores as they are (model)")
    if kind != "ssd-prior" and activation == "softmax":
        raise CardError("score_activation: softmax is for ssd-prior; YOLO scores are model or sigmoid")

    outputs = top["outputs"]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_OUTPUTS:
        raise CardError(f"outputs is a list of 1 to {MAX_OUTPUTS} outputs")
    allowed = _roles(kind, facts["dfl"])
    seen, roles = set(), set()
    for o in outputs:
        _keys(o, "an output", ("index", "role"), ("shape", "dims"))
        index = _uint(o["index"], "outputs.index", 0, MAX_OUTPUTS - 1)
        if index in seen:
            raise CardError(f"outputs: the index {index} twice")
        seen.add(index)
        role = _word(o, "role", "outputs", ROLES)
        if role not in allowed:
            raise CardError(f"outputs: the role {role} is not one of {kind} ({', '.join(allowed)})")
        roles.add(role)
        shape = _uints(o["shape"], "outputs.shape", 1, 6, 1, 1000000) if "shape" in o else None
        if "dims" in o:
            d = o["dims"]
            if shape is None or not isinstance(d, list) or len(d) != len(shape) or \
                    not all(isinstance(x, str) and _RE_DIM.fullmatch(x) for x in d):
                raise CardError("outputs.dims names each dimension of its shape (lower-case words)")
    if kind in ("device-nms", "ssd-prior"):
        for r in ("boxes", "scores"):
            if r not in roles:
                raise CardError(f"outputs: {kind} needs an output of the role {r}")
    if kind == "ssd-prior" and (facts["keypoints"] > 0) != ("keypoints" in roles):
        raise CardError("outputs: an output of the role keypoints exactly when postprocess.keypoints is above 0")
    return ModelCard(data)


def card_path_for(model: Path) -> Path:
    """Where the card of `model` lies: `<name>.card.json` next to `<name>.rknn`."""
    model = Path(model)
    return model.with_name(model.name[:-len(".rknn")] + CARD_SUFFIX if model.name.endswith(".rknn")
                           else model.name + CARD_SUFFIX)


def check_file(path: Path, model_sha256: Optional[str] = None) -> ModelCard:
    """The card at `path`, checked; with `model_sha256` also that it names exactly that model (a pair)."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_CARD_BYTES:
            raise CardError("the model card is larger than 1024 KiB")
        data = path.read_bytes()
    except OSError:
        raise CardError("the model card cannot be read") from None
    card = parse(data)
    if model_sha256 is not None and card.rknn_sha256 != model_sha256.lower():
        raise CardError("the card names another model: its rknn.sha256 is not the model file's SHA256")
    return card


__all__ = ["BUILT_KINDS", "CARD_SUFFIX", "CardError", "KINDS", "MAX_CARD_BYTES", "ModelCard", "PLATFORMS", "ROLES",
           "SCHEMA", "TASKS", "card_path_for", "check_file", "parse"]
