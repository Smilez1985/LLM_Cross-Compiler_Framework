#!/usr/bin/env python3
"""
A building block proposed from a model (`llm-cli bundle block propose`, Boards > Board bundle... > Propose from
model..., the optional MCP tool propose_bundle_block): pure rules, no network, nothing written here.

The facts of a model (its Hugging Face pipeline task and library, its files, its licence; ModelSummary, made from
hf_hub.ModelFacts or from a local model folder) go through one table (TASKS) to a draft block: its kind, its files
by role, its variables and a `reach` section whose limits come from conservative defaults (LIMITS). Every field of
the draft carries a reason (Reason: where it came from: the task, the library, a default, the model files, or "needs
the person" where nothing tells). A task the table does not know gives a generic http-json draft that needs the
person. The draft is a template block (status template: its program comes from the target module that builds the
engine) and passes the block schema's own check (blocks.parse_block, reach.parse_reach) before anyone sees it.

For a detection-type task (MODEL_CARD_TASKS) the draft notes what the engine needs to know (input size, classes,
pre- and post-processing) and where it should come from (the model's files, else the target module); there is no
engine program here.

Who does what: the rules propose; the person reviews the draft with its reasons, may change it, and writes it as an
own block (unsigned, trust "own") with `--write` after a yes at a terminal or with Save in the window's review
dialog. An AI may read the draft (labels: facts, draft, reasons; no path of this computer, no token) and propose a
text the person sees in the review dialog (ProposalNotes); it never writes or signs a block.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from orchestrator.runtime.bundle.blocks import BLOCK_SCHEMA, Block, BlockError, parse_block
from orchestrator.runtime.bundle.manifest import RE_ID, valid_kind

DRAFT_VERSION = "0.1.0"
RE_TASK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)
MAX_FILES = 2000
MB = 1000 ** 2

# Where a field of the draft came from.
ORIGINS = ("task", "library", "default", "model files", "needs the person")


@dataclass(frozen=True)
class Profile:
    """How an engine of one family is reached and the kind its block gets."""
    kind: str
    profile: str
    port: int


# The Hugging Face pipeline tasks the rules know (https://huggingface.co/tasks), each with the block kind, the route
# family and the engine's default loopback port.
TASKS: Dict[str, Profile] = {
    "text-generation": Profile("llm", "chat", 8080),
    "image-text-to-text": Profile("vision", "vision", 8081),
    "feature-extraction": Profile("embed", "embed", 8094),
    "sentence-similarity": Profile("embed", "embed", 8094),
    "automatic-speech-recognition": Profile("stt", "stt", 8090),
    "text-to-speech": Profile("tts", "tts", 8091),
    "object-detection": Profile("detect", "image", 8093),
    "image-classification": Profile("classify", "image", 8095),
    "image-segmentation": Profile("segment", "image", 8096),
    "keypoint-detection": Profile("keypoints", "image", 8097),
    "video-classification": Profile("video-classify", "video", 8098),
    "video-text-to-text": Profile("video-text", "video-text", 8099),
}
GENERIC_PORT = 8100
# A library that names the task where the model has no pipeline task (library_name on the Hub).
LIBRARY_TASKS: Dict[str, str] = {
    "sentence-transformers": "sentence-similarity",
    "ultralytics": "object-detection",
    "timm": "image-classification",
}
# The tasks whose engine needs facts no route says: the input size, the classes, the pre- and post-processing.
MODEL_CARD_TASKS = ("object-detection", "image-classification", "image-segmentation", "keypoint-detection",
                    "video-classification")

# Conservative limits per route family: (max_body_kb, timeout_s), each with its reason.
LIMITS: Dict[str, Tuple[int, int, str]] = {
    "models": (16, 10, "a listing has no body; 10 s is plenty for an answer from memory"),
    "chat": (1024, 600, "1 MB of chat text; 10 minutes for a long answer on a small board"),
    "images": (8192, 600, "a few compressed images in one chat (the gateway's own image caps still apply)"),
    "embed": (512, 60, "a batch of texts; an embedding answers in seconds"),
    "stt": (25600, 300, "25 MB of audio (the OpenAI limit); 5 minutes to transcribe it on a small board"),
    "tts": (64, 120, "a text to speak; 2 minutes for the audio"),
    "image": (8192, 60, "one camera image of up to 8 MB; one minute for the answer"),
    "events": (16, 30, "an event stream has no body; it ends when the engine is silent for 30 s"),
    "video": (51200, 300, "a short clip of up to 50 MB; 5 minutes for the answer"),
    "video-text": (51200, 600, "a short clip of up to 50 MB and a prompt; 10 minutes for the answer"),
    "generic": (256, 60, "a small JSON object; one minute for the answer"),
}


@dataclass(frozen=True)
class ModelSummary:
    """What the rules read of a model: no path, no token. `source` hub (a Hugging Face repository at a commit),
    folder (a local model folder) or task (a task alone)."""
    repo: str = ""
    commit: str = ""
    task: str = ""
    library: str = ""
    licence: str = "unknown"
    gated: str = "false"
    files: Tuple[Tuple[str, Optional[int]], ...] = ()
    source: str = "task"
    name: str = ""                      # the folder's name (source folder), else the repository's name
    classes: Optional[int] = None       # read from the folder's config.json (id2label), when there
    image_size: Optional[int] = None    # read from the folder's config.json or preprocessor_config.json

    @property
    def file_names(self) -> List[str]:
        return [n for n, _ in self.files]


def summary_from_facts(facts: Any) -> ModelSummary:
    """A ModelSummary from hf_hub.ModelFacts (Hub or folder; duck-typed, so this package imports nothing of Core)."""
    files = tuple((str(f.name), f.size if isinstance(f.size, int) else None)
                  for f in list(getattr(facts, "files", None) or [])[:MAX_FILES])
    repo = str(getattr(facts, "repo", "") or "")
    return ModelSummary(repo=repo, commit=str(getattr(facts, "commit", "") or ""),
                        task=str(getattr(facts, "pipeline_tag", "") or ""),
                        library=str(getattr(facts, "library_name", "") or ""),
                        licence=str(getattr(facts, "license", "") or "unknown"),
                        gated=str(getattr(facts, "gated", "false") or "false"), files=files,
                        source=str(getattr(facts, "source", "") or ("hub" if repo else "folder")),
                        name=str(getattr(facts, "folder_name", "") or repo.rpartition("/")[2]),
                        classes=getattr(facts, "classes", None), image_size=getattr(facts, "image_size", None))


@dataclass(frozen=True)
class Reason:
    """Why a field of the draft has its value: `field` (a dotted name), `origin` (ORIGINS) and the words."""
    field: str
    origin: str
    text: str

    def as_json(self) -> Dict[str, str]:
        return {"field": self.field, "origin": self.origin, "text": self.text}


@dataclass(frozen=True)
class CardHint:
    """What a detection-type engine needs to know, and where it should come from."""
    what: str
    source: str
    found: str = ""     # what the model's files already say (the number of classes, the input size)

    def as_json(self) -> Dict[str, str]:
        return {"what": self.what, "source": self.source, **({"found": self.found} if self.found else {})}


@dataclass
class Draft:
    """A proposed block: its spec (block.yml as data), the reasons, what the person must decide, the model-card hints
    and the facts it came from (labels)."""
    spec: Dict[str, Any]
    reasons: List[Reason]
    needs_person: List[str]
    model_card: List[CardHint]
    facts: Dict[str, Any]
    task: str
    rule: str          # the table entry it came from: the task, "library:<name>" or "unknown"

    @property
    def block_id(self) -> str:
        return str(self.spec.get("id", ""))

    @property
    def kind(self) -> str:
        return str(self.spec.get("kind", ""))

    def yaml_text(self) -> str:
        return yaml_text(self.spec)

    def check(self) -> Block:
        """The draft as the block schema reads it (BlockError when it does not pass: never shown as a proposal)."""
        return parse_block(self.yaml_text(), Path(self.block_id or "draft"))

    def guide_text(self) -> str:
        return guide_text(self)

    def labels(self) -> Dict[str, Any]:
        """The draft for an AI and for --json: the facts, the block, the reasons, the model-card hints."""
        return {"facts": dict(self.facts), "task": self.task, "rule": self.rule, "block": json.loads(json.dumps(
            self.spec)), "reasons": [r.as_json() for r in self.reasons], "needs_person": list(self.needs_person),
            "model_card": [h.as_json() for h in self.model_card]}

    def lines(self) -> List[str]:
        """The draft for the person: the facts, the block, each reason, the model-card hints."""
        out = [f"{k}: {v}" for k, v in self.facts.items() if v not in ("", None)]
        out.append(f"rule: {self.rule}")
        out += ["", "block.yml:"] + ["  " + line for line in self.yaml_text().splitlines()]
        out += ["", "Why:"] + [f"  {r.field}: {r.text} ({r.origin})" for r in self.reasons]
        if self.model_card:
            out += ["", "What the engine needs to know:"] + [
                f"  {h.what}: {h.found + '; ' if h.found else ''}from {h.source}" for h in self.model_card]
        if self.needs_person:
            out += ["", "You decide:"] + [f"  {n}" for n in self.needs_person]
        return out


def yaml_text(spec: Dict[str, Any]) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=False, width=120)


def clean_task(task: Any) -> str:
    """A pipeline task as the rules take it: a plain lower-case word, else none (external text)."""
    text = str(task or "").strip().lower()
    return text if RE_TASK.fullmatch(text) else ""


def _slug(text: str, limit: int = 40) -> str:
    out = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-._")
    return out[:limit].strip("-._")


def block_id_for(kind: str, name: str) -> str:
    """`<kind>-<name>` as a block id (RE_ID), the name made plain."""
    slug = _slug(name)
    out = f"{kind}-{slug}" if slug else kind
    out = out[:48].strip("-._")
    return out if RE_ID.fullmatch(out) else kind


# --- the model's files ---------------------------------------------------------------------------------------------

def _ext(name: str) -> str:
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


def _base(name: str) -> str:
    return name.rsplit("/", 1)[-1]


@dataclass
class _Files:
    formats: Tuple[str, ...]
    size: Optional[int]
    largest: str
    note: str
    mmproj: bool
    origin: str
    reason: str
    config: str = ""     # a file of the model that holds the classes (config.json, labels.txt)


def _model_files(summary: ModelSummary, profile: str) -> _Files:
    files = [(n, s) for n, s in summary.files if not _base(n).startswith(".")]
    gguf = [(n, s) for n, s in files if _ext(n) == "gguf" and "mmproj" not in _base(n).lower()]
    mmproj = [(n, s) for n, s in files if _ext(n) == "gguf" and "mmproj" in _base(n).lower()]
    onnx = [(n, s) for n, s in files if _ext(n) == "onnx"]
    whisper = [(n, s) for n, s in files if _ext(n) == "bin" and _base(n).lower().startswith("ggml")]
    weights = [(n, s) for n, s in files if _ext(n) in ("safetensors", "bin", "pt", "pth", "ckpt", "rknn", "tflite")]
    config = next((n for n, _ in files if _base(n) in ("config.json", "labels.txt", "classes.txt")), "")

    def largest(found):
        sized = [(s or 0, n) for n, s in found]
        size, name = max(sized) if sized else (0, "")
        return (size or None), name

    for found, formats, label in ((gguf, ("gguf",), "GGUF"), (whisper, ("ggml-whisper",), "ggml whisper"),
                                  (onnx, ("onnx",), "ONNX")):
        if found:
            size, name = largest(found)
            many = f"; the model has {len(found)} {label} files: pick one (a smaller quantization needs less " \
                   "memory)" if len(found) > 1 else ""
            return _Files(formats, size, name, f"{label} model{many}"[:300], bool(mmproj) and profile == "vision",
                          "model files", f"the model's files include {label} ({_base(name)}){many}", config)
    if weights:
        total = sum(s or 0 for _, s in weights) or None
        size, name = largest(weights)
        return _Files((), total, name, "converted by the target module that builds the engine",
                      False, "model files",
                      f"the model's weights ({_ext(name)}) are no format a bundle checks; the target module that "
                      "builds the engine converts them", config)
    return _Files((), None, "", "the model file the engine reads", False, "needs the person",
                  "no model file is known; name the format the engine reads", config)


# --- the routes ----------------------------------------------------------------------------------------------------

def _limited(route: Dict[str, Any], family: str, index: int, reasons: List[Reason]) -> Dict[str, Any]:
    body, timeout, why = LIMITS[family]
    route["max_body_kb"], route["timeout_s"] = body, timeout
    reasons.append(Reason(f"reach.routes[{index}].limits", "default",
                          f"{body} KB, {timeout} s: {why}"))
    return route


def _routes(profile: Profile, kind: str, task: str, reasons: List[Reason], origin: str) -> List[Dict[str, Any]]:
    routes: List[Dict[str, Any]] = []

    def add(route: Dict[str, Any], family: str, why: str) -> None:
        index = len(routes)
        routes.append(_limited(route, family, index, reasons))
        reasons.append(Reason(f"reach.routes[{index}]", origin, why))

    p = profile.profile
    if p == "chat":
        add({"path": "/v1/models", "method": "GET", "protocol": "openai-models", "engine_route_key": "models"},
            "models", f"{task}: clients list the model at /v1/models (OpenAI)")
        add({"path": "/v1/chat/completions", "protocol": "openai-chat", "stream": True, "engine_route_key": "chat"},
            "chat", f"{task}: a language model answers OpenAI chats, streamed")
        add({"path": "/v1/completions", "protocol": "openai-completions", "stream": True,
             "engine_route_key": "completions"}, "chat", f"{task}: and plain completions")
    elif p == "vision":
        add({"path": "/v1/chat/completions", "protocol": "openai-chat", "match": "images", "stream": True,
             "engine_route_key": "chat-images"}, "images",
            f"{task}: the chats that carry images go to this engine (match images); the plain chat route comes "
            "from the language model's block on the same path")
    elif p == "embed":
        add({"path": "/v1/embeddings", "protocol": "openai-embeddings", "engine_route_key": "embeddings"}, "embed",
            f"{task}: embeddings the OpenAI way")
    elif p == "stt":
        add({"path": "/v1/audio/transcriptions", "protocol": "openai-audio-transcription",
             "engine_route_key": "transcriptions"}, "stt", f"{task}: transcriptions the OpenAI way")
    elif p == "tts":
        add({"path": "/v1/audio/speech", "protocol": "openai-audio-speech", "engine_route_key": "speech"}, "tts",
            f"{task}: speech the OpenAI way")
    elif p in ("image", "video", "video-text"):
        if p == "image":
            fields = [{"name": "image", "file": True, "types": ["jpeg", "png"], "required": True}]
            what = "an image in as a form (jpeg or png, recognized by its first bytes), the answer as JSON"
        else:
            fields = [{"name": "video", "file": True, "types": ["mp4", "webm"], "required": True}]
            what = "a video clip in as a form (mp4 or webm), the answer as JSON"
            if p == "video-text":
                fields.append({"name": "prompt", "file": False, "required": True})
                what = "a video clip and a prompt in as a form, the answer as JSON"
        add({"path": f"/api/{kind}", "protocol": "http-multipart", "upstream_path": f"/{kind}", "fields": fields,
             "engine_route_key": kind[:32]}, p, f"{task}: {what}")
        add({"path": f"/api/{kind}/events", "method": "GET", "protocol": "http-sse", "upstream_path": "/events",
             "engine_route_key": "events"}, "events",
            "the camera variant: the engine's results for its camera as an event stream; leave it out for an engine "
            "without a camera")
    else:
        add({"path": f"/api/{kind}", "protocol": "http-json", "upstream_path": f"/{kind}",
             "engine_route_key": kind[:32]}, "generic",
            "the task is none the rules know: one JSON object in and out; the person names the real protocol")
    for i, route in enumerate(routes):
        if p not in ("chat", "vision"):
            reasons.append(Reason(f"reach.routes[{i}].upstream_path", "needs the person",
                                  "the engine's own path: the target module that builds the engine says it "
                                  "(whisper-server's is /inference)"))
    return routes


# --- the model card hints ------------------------------------------------------------------------------------------

def _model_card(summary: ModelSummary, task: str) -> List[CardHint]:
    if task not in MODEL_CARD_TASKS:
        return []
    names = {_base(n) for n in summary.file_names}
    config = "config.json" in names
    pre = "preprocessor_config.json" in names
    hints = [
        CardHint("input size (width and height the engine scales an image to)",
                 "the model's preprocessor_config.json (size)" if pre else
                 "the model's config.json (image_size)" if config else "the target module that builds the engine",
                 f"{summary.image_size} px" if summary.image_size else ""),
        CardHint("classes (the label of each output index)",
                 "the model's config.json (id2label)" if config else
                 "a labels file of the model" if names & {"labels.txt", "classes.txt"} else
                 "the target module that builds the engine",
                 f"{summary.classes} classes" if summary.classes else ""),
        CardHint("pre-processing (colour order, mean and standard deviation, letterboxing)",
                 "the model's preprocessor_config.json" if pre else "the target module that builds the engine"),
        CardHint("post-processing (score threshold, non-maximum suppression, box or mask format)"
                 if task != "image-classification" and task != "video-classification" else
                 "post-processing (softmax, top-k)", "the target module that builds the engine"),
    ]
    return hints


# --- the draft -----------------------------------------------------------------------------------------------------

def _rule_for(summary: ModelSummary) -> Tuple[str, Optional[Profile], str, str]:
    """(task, profile or None, rule, origin)."""
    task = clean_task(summary.task)
    if task in TASKS:
        return task, TASKS[task], task, "task"
    library = clean_task(summary.library)
    if not task and library in LIBRARY_TASKS:
        task = LIBRARY_TASKS[library]
        return task, TASKS[task], f"library:{library}", "library"
    return task, None, "unknown", "needs the person"


def propose(summary: ModelSummary) -> Draft:
    """The draft block for `summary` (Draft), checked by the block schema; every field with its reason."""
    reasons: List[Reason] = []
    needs: List[str] = []
    task, profile, rule, origin = _rule_for(summary)
    if profile is None:
        kind = task if task and valid_kind(task) and task not in ("gateway", "harness") and len(task) <= 32 \
            else "custom"
        profile = Profile(kind, "generic", GENERIC_PORT)
        reasons.append(Reason("kind", "needs the person",
                              f"the task {task or '(none)'} is none the rules know: a generic engine of kind {kind}"))
        needs.append("the protocol, paths and limits of the engine (the draft is a generic http-json route)")
    else:
        why = (f"the model's pipeline task is {task}" if origin == "task" else
               f"the model has no pipeline task; its library {clean_task(summary.library)} is used for {task}")
        reasons.append(Reason("kind", origin, f"{why}: an engine of kind {profile.kind}"))
    kind = profile.kind
    name = summary.name or (summary.repo.rpartition("/")[2] if summary.repo else "")
    block_id = block_id_for(kind, name)
    reasons.append(Reason("id", "default", f"the kind and the model's name ({block_id}); change it freely"))
    files = _model_files(summary, profile.profile)
    file_needs: List[Dict[str, Any]] = [
        {"role": "program", "from": "person", "note": "the engine program its target module builds"}]
    reasons.append(Reason("files.program", "needs the person",
                          "the engine program comes from the target module that builds it for the board"))
    if kind == "llm" and "gguf" in files.formats:
        reasons.append(Reason("files.program", "model files",
                              "a GGUF language model also runs with the framework's block llm-llama-server (the "
                              "board's self-built llama.cpp runtime): use that block instead of this draft"))
    model = {"role": "model", "from": "person", "licence": "required", "note": files.note}
    if files.formats:
        model["formats"] = list(files.formats)
    file_needs.append(model)
    reasons.append(Reason("files.model", files.origin, files.reason))
    reasons.append(Reason("files.model.licence", "default",
                          f"the person names the model's licence (the model says: {summary.licence or 'unknown'})"))
    if files.mmproj:
        file_needs.append({"role": "mmproj", "from": "person", "formats": ["mmproj-gguf"], "licence": "required",
                           "note": "the projector of the vision model"})
        reasons.append(Reason("files.mmproj", "model files", "the model's files include a GGUF projector (mmproj)"))
    if files.config and task in MODEL_CARD_TASKS:
        file_needs.append({"role": "config", "from": "person", "name": _base(files.config), "formats": ["text"],
                           "note": "the classes of the model"})
        reasons.append(Reason("files.config", "model files",
                              f"the model's {_base(files.config)} holds its classes; the engine reads them"))
    if files.size:
        limit = max(256, int(math.ceil(files.size * 1.2 / MB)) + 128)
        reasons.append(Reason("variables.memory_limit_mb", "model files",
                              f"{limit} MB: the model file {_base(files.largest)} is "
                              f"{math.ceil(files.size / MB)} MB, times 1.2, plus 128 MB for the engine"))
    else:
        limit = 1024
        reasons.append(Reason("variables.memory_limit_mb", "default",
                              "1024 MB: the model's size is not known; measure the board"))
    variables = [
        {"name": "port", "type": "port", "default": profile.port, "help": "Loopback port of the engine"},
        {"name": "threads", "type": "threads", "default": 0, "help": "CPU threads (0: the engine decides)"},
        {"name": "memory_limit_mb", "type": "memory_mb", "default": min(limit, 1 << 20),
         "help": "Memory limit of its unit (autostart only)"},
        {"name": "user", "type": "user", "default": "llmcf", "help": "Account its unit runs as (autostart only)"},
        {"name": "autostart", "type": "bool", "default": False,
         "help": "Start at boot through its own unit (needs llmcf-install)"},
    ]
    reasons.append(Reason("variables.port", "default",
                          f"{profile.port}: the default port of a {kind} engine; loopback only, the gateway reaches "
                          "it"))
    routes = _routes(profile, kind, task or "the engine", reasons,
                     "needs the person" if profile.profile == "generic" else origin)
    hints = _model_card(summary, task)
    origin_words = (f"the model {summary.repo}" if summary.repo else
                    "a local model folder" if summary.source == "folder" else f"the task {task or 'given'}")
    notes = [f"Proposed from {origin_words} by the framework's rules; reviewed and written by the person."[:300]]
    for hint in hints:
        notes.append(f"Needs: {hint.what}, from {hint.source}"[:300])
    if task in MODEL_CARD_TASKS:
        reasons.append(Reason("notes", "task", "a detection-type engine needs its input size, classes and pre- and "
                                               "post-processing: the guide's '## Models' section names where from"))
    needs.append("the engine program and its start arguments (start.program, start.args): the target module says")
    needs.append("the model file and its licence")
    title = f"{name} ({task})" if name and task else name or f"{task or kind} engine"
    spec: Dict[str, Any] = {
        "schema": BLOCK_SCHEMA, "id": block_id, "version": DRAFT_VERSION, "kind": kind, "title": title[:120],
        "status": "template",
        "summary": (f"A {kind} engine for {summary.repo or name}"
                    + (f" at commit {summary.commit[:12]}" if summary.commit else "")
                    + ", proposed from its "
                    + ("pipeline task" if origin == "task" else "library" if origin == "library" else
                       "facts (no task the rules know)")
                    + "; a template until its program and start arguments are filled in.")[:500],
        "variables": variables, "files": file_needs, "memory": {"base_mb": 64, "model_factor": 1.2},
        "unit": True, "notes": notes, "reach": {"port": "port", "routes": routes}}
    reasons.append(Reason("status", "default",
                          "template: a draft is never ready until its program and start arguments are filled in"))
    reasons.append(Reason("memory", "default", "64 MB for the engine and 1.2 times the model file (the plan's "
                                               "memory check uses it)"))
    facts = {"source": summary.source, "model": summary.repo, "commit": summary.commit, "task": task,
             "library": clean_task(summary.library), "licence": summary.licence, "gated": summary.gated,
             "files": len(summary.files)}
    draft = Draft(spec=spec, reasons=reasons, needs_person=needs, model_card=hints, facts=facts, task=task, rule=rule)
    draft.check()   # a draft its own schema refuses is a programming error, never shown
    return draft


def propose_task(task: str) -> Draft:
    """The draft for a task alone (no model)."""
    return propose(ModelSummary(task=task, source="task"))


GUIDE = """# {title}

Block `{id}` (kind {kind}) for the board bundle of the LLM Cross-Compiler Framework, proposed from {origin} by the
framework's rules and reviewed by the person. Its status is template until its program and start arguments are
filled in.

{summary}

## Models

{models}

## Why the draft says what it says

{reasons}

## Variables

port, threads, memory_limit_mb, user and autostart: see block.yml.
"""


def guide_text(draft: Draft) -> str:
    """The guide (README.md) of a written draft: its '## Models' section with the model, its files and what the
    engine needs to know, and every reason."""
    spec = draft.spec
    facts = draft.facts
    models = [f"- Model: {facts.get('model') or 'a local model folder'}"
              + (f" at commit {facts['commit']}" if facts.get("commit") else "")
              + f"; task {draft.task or 'unknown'}; licence as the model says: {facts.get('licence') or 'unknown'} "
                "(the person names it for the bundle)."]
    for f in spec.get("files", []):
        models.append(f"- File `{f['role']}`" + (f" ({', '.join(f['formats'])})" if f.get("formats") else "")
                      + (f": {f['note']}" if f.get("note") else ""))
    if draft.model_card:
        models.append("")
        models.append("What the engine needs to know, and where it comes from:")
        models += [f"- {h.what}: {h.found + '; ' if h.found else ''}from {h.source}" for h in draft.model_card]
    origin = f"the model {facts['model']}" if facts.get("model") else \
        "a local model folder" if facts.get("source") == "folder" else f"the task {draft.task or 'given'}"
    return GUIDE.format(title=spec.get("title", draft.block_id), id=draft.block_id, kind=draft.kind, origin=origin,
                        summary=spec.get("summary", ""), models="\n".join(models),
                        reasons="\n".join(f"- `{r.field}`: {r.text} ({r.origin})" for r in draft.reasons))


def spec_from_text(text: str) -> Dict[str, Any]:
    """The block spec from the text the person edited in the review dialog, checked by the block schema
    (BlockError)."""
    block = parse_block(text, Path("draft"))
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict) or spec.get("id") != block.id:
        raise BlockError("block.yml is not a mapping")
    return spec


# --- an AI's proposal text for the person ----------------------------------------------------------------------------

NOTES_FILE = "block_proposal_notes.json"
MAX_NOTE = 1000
MAX_NOTES_PER_MODEL = 10
MAX_NOTES = 200


@dataclass(frozen=True)
class ProposalNote:
    """An AI's text on a draft (limits, a model-card note): shown to the person in the review dialog, marked as an
    AI's text, never applied."""
    model: str
    note: str
    proposed_by: str
    created: str

    def as_json(self) -> Dict[str, str]:
        return {"model": self.model, "note": self.note, "proposed_by": self.proposed_by, "created": self.created}


class ProposalNotes:
    """The AI notes on proposed blocks, in the per-user folder (block_proposal_notes.json, mode 0600)."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / NOTES_FILE

    def _read(self) -> List[ProposalNote]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in
                                              ("model", "note", "proposed_by", "created")):
                out.append(ProposalNote(item["model"], item["note"], item["proposed_by"], item["created"]))
        return out

    def add(self, model: str, note: str, proposed_by: str) -> ProposalNote:
        from orchestrator.utils.atomic import write_private
        from orchestrator.utils.untrusted import sanitize_untrusted
        entry = ProposalNote(model=str(model)[:200], note=sanitize_untrusted(str(note), MAX_NOTE),
                             proposed_by=str(proposed_by)[:100],
                             created=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        notes = self._read()
        same = [n for n in notes if n.model == entry.model]
        if len(same) >= MAX_NOTES_PER_MODEL:
            notes.remove(same[0])
        notes = (notes + [entry])[-MAX_NOTES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps([n.as_json() for n in notes], indent=1))
        return entry

    def for_model(self, model: str) -> List[ProposalNote]:
        return [n for n in self._read() if n.model == model]


def tasks_table() -> List[Dict[str, Any]]:
    """The table for the documentation and the tests: task, kind, protocols."""
    rows = []
    for task, profile in TASKS.items():
        draft = propose_task(task)
        rows.append({"task": task, "kind": profile.kind,
                     "protocols": [r["protocol"] + (" (images)" if r.get("match") else "")
                                   for r in draft.spec["reach"]["routes"]]})
    return rows


__all__ = ["CardHint", "Draft", "LIBRARY_TASKS", "LIMITS", "MODEL_CARD_TASKS", "ModelSummary", "ProposalNote",
           "ProposalNotes", "Reason", "TASKS", "block_id_for", "clean_task", "guide_text", "propose", "propose_task",
           "spec_from_text", "summary_from_facts", "tasks_table"]
