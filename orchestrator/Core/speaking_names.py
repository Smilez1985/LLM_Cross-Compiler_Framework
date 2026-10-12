#!/usr/bin/env python3
"""
Speaking names in what the person sees (DECISIONS round 4 item 1; delegation design 5.2 rule R7).

An AI writes the framework's ids ("I started job_k3j2h1g0f9e8d7c6"); the person reads names ("the Hugging Face
download"). `SpeakingNames.render(text)` splits a text into segments and replaces, outside code, exactly the
framework's ids - a known prefix with exactly its length of lower-case characters, standing alone - and the masked
values it was given by their speaking names. Anything else stays as it was: a near match (a longer or shorter string,
upper case, part of a longer word), an id nobody can name and every id inside code. The text itself is never
changed: each name segment keeps the exact id it stands for, so the original words can always be shown again
("Show the original words"), and `legend(text)` names the ids inside code instead ("job_… = Hugging Face download").

Names are looked up through resolvers (one per id kind), never from the text around the id; a lookup that fails or
names nothing leaves the id as it is. Names are one line and short; the panel shows them as plain text in a style of
their own, never as rich text.
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

# The framework's ids by kind: one prefix and exactly the length and alphabet the framework generates. A RANDOM_ID of
# the hybrid design (6.3) is any of these.
ID_KINDS: Dict[str, str] = {
    "job": r"job_[a-z0-9]{16}",
    "request": r"req_[0-9a-f]{8}",
    "artifact": r"a_[0-9a-f]{16}",
    "file": r"lf_[0-9a-f]{16}",
    "approval": r"ap_[0-9a-f]{16}",
    "grant": r"g_[0-9a-f]{16}",
    "proposal": r"hp_[0-9a-f]{16}",
    "delegation": r"d_[0-9a-f]{16}",
    "trash": r"tr_[0-9a-f]{16}",
    "message": r"msg_[0-9a-f]{16}",
    "entry": r"fe_[0-9a-f]{16}",
}
_BEFORE = r"(?<![A-Za-z0-9_])"   # standing alone: not the end of a longer word ...
_AFTER = r"(?![A-Za-z0-9_])"     # ... and not the start of one
FRAMEWORK_ID_RE = re.compile(_BEFORE + "(?:" + "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in ID_KINDS.items())
                             + ")" + _AFTER)
_FULL = {kind: re.compile(pattern) for kind, pattern in ID_KINDS.items()}
# Code: a fenced block (to its closing fence or the end of the text) or inline code on one line.
CODE_RE = re.compile(r"```[\s\S]*?(?:```|\Z)|`[^`\n]+`")

MAX_NAME = 120
CACHE_SIZE = 512
NAME_TTL = 60.0       # seconds a name is kept (a board may be relabelled)
MISS_TTL = 15.0       # seconds "no name" is kept (an id may become known)

Resolver = Callable[[str], Optional[str]]


def id_kind(value: str) -> Optional[str]:
    """The kind of an exact framework id ("job", "request", ...); None for anything else."""
    for kind, pattern in _FULL.items():
        if pattern.fullmatch(value or ""):
            return kind
    return None


@dataclass(frozen=True)
class Segment:
    """A piece of a rendered text. "text": shown as it is; "name": a speaking name standing for `original` (the exact
    id or masked value); "code": code, shown unchanged and monospace (fences included, as written)."""
    kind: Literal["text", "name", "code"]
    text: str
    original: str = ""


def original_text(segments: List[Segment]) -> str:
    """The text the segments were made from, unchanged."""
    return "".join(s.original if s.kind == "name" else s.text for s in segments)


def _clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    return text if len(text) <= MAX_NAME else text[:MAX_NAME - 1] + "…"


class SpeakingNames:
    """Speaking names for ids and masked values. `resolvers`: id kind -> function giving the name of one exact id
    (None when it has none); `masks`: exact masked value -> its speaking name (from whatever masked it)."""

    def __init__(self, resolvers: Optional[Mapping[str, Resolver]] = None,
                 masks: Optional[Mapping[str, str]] = None):
        unknown = set(resolvers or {}) - set(ID_KINDS)
        if unknown:
            raise ValueError(f"unknown id kinds {sorted(unknown)}")
        self._resolvers: Dict[str, Resolver] = dict(resolvers or {})
        self._masks: Dict[str, str] = {}
        self._mask_re: Optional[re.Pattern] = None
        self._cache: Dict[str, Tuple[float, Optional[str]]] = {}
        self._lock = threading.Lock()
        for value, name in (masks or {}).items():
            self.add_mask(value, name)

    def register(self, kind: str, resolver: Resolver) -> None:
        """Adds or replaces the resolver of one id kind (a package that owns the ids, e.g. delegations)."""
        if kind not in ID_KINDS:
            raise ValueError(f"unknown id kind {kind!r}")
        with self._lock:
            self._resolvers[kind] = resolver
            self._cache = {k: v for k, v in self._cache.items() if id_kind(k) != kind}

    def add_mask(self, value: str, name: str) -> None:
        """A masked value (exact text) and the speaking name the person reads for it."""
        if not value or not value.strip():
            raise ValueError("a masked value is not empty")
        cleaned = _clean_name(name)
        with self._lock:
            if cleaned:
                self._masks[value] = cleaned
            else:
                self._masks.pop(value, None)
            ordered = sorted(self._masks, key=len, reverse=True)   # the longest first: no mask hides inside another
            self._mask_re = (re.compile(_BEFORE + "(?:" + "|".join(re.escape(m) for m in ordered) + ")" + _AFTER)
                             if ordered else None)

    # --- names ---------------------------------------------------------------------------------------------------

    def resolve(self, value: str) -> Optional[str]:
        """The speaking name of one exact id or masked value; None when it has none (then it stays as written)."""
        with self._lock:
            if value in self._masks:
                return self._masks[value]
            kind = id_kind(value)
            resolver = self._resolvers.get(kind) if kind else None
            if resolver is None:
                return None
            cached = self._cache.get(value)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]
        try:
            name = _clean_name(resolver(value)) or None
        except Exception:  # noqa: BLE001 - a name is a help for the person, never a reason to fail
            name = None
        if name == value:
            name = None
        with self._lock:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[value] = (time.monotonic() + (NAME_TTL if name else MISS_TTL), name)
        return name

    def _candidates(self, text: str) -> List[re.Match]:
        """Ids and masked values in `text`, in order, without overlaps (a masked value wins over an id inside it)."""
        found = list(FRAMEWORK_ID_RE.finditer(text))
        with self._lock:
            mask_re = self._mask_re
        if mask_re is not None:
            masks = list(mask_re.finditer(text))
            found = masks + [m for m in found if not any(k.start() < m.end() and m.start() < k.end() for k in masks)]
        return sorted(found, key=lambda m: m.start())

    def _named(self, text: str) -> List[Segment]:
        segments: List[Segment] = []
        pos = 0
        for match in self._candidates(text):
            value = match.group(0)
            name = self.resolve(value)
            if name is None:
                continue
            if match.start() > pos:
                segments.append(Segment("text", text[pos:match.start()]))
            segments.append(Segment("name", name, original=value))
            pos = match.end()
        if pos < len(text):
            segments.append(Segment("text", text[pos:]))
        return segments

    def render(self, text: str) -> List[Segment]:
        """`text` as segments: code unchanged, exact ids and masked values outside code as their speaking names,
        everything else as written. original_text(render(t)) == t."""
        text = text or ""
        segments: List[Segment] = []
        pos = 0
        for code in CODE_RE.finditer(text):
            segments.extend(self._named(text[pos:code.start()]))
            segments.append(Segment("code", code.group(0)))
            pos = code.end()
        segments.extend(self._named(text[pos:]))
        merged: List[Segment] = []
        for segment in segments:  # neighbouring plain text is one segment
            if segment.kind == "text" and merged and merged[-1].kind == "text":
                merged[-1] = Segment("text", merged[-1].text + segment.text)
            elif segment.text or segment.original:
                merged.append(segment)
        return merged

    def legend(self, text: str) -> List[str]:
        """One line per id or masked value inside code that has a name ("job_… = Hugging Face download"), in the
        order they appear, each once."""
        lines, seen = [], set()
        for code in CODE_RE.finditer(text or ""):
            for match in self._candidates(code.group(0)):
                value = match.group(0)
                if value in seen:
                    continue
                seen.add(value)
                name = self.resolve(value)
                if name is not None:
                    lines.append(f"{value} = {name}")
        return lines

    # --- the framework's names -----------------------------------------------------------------------------------

    @classmethod
    def for_framework(cls, framework: Any, lang: str = "en") -> "SpeakingNames":
        """Names from the framework's own records: a job by what it does, a build request by its model and target,
        a built model by its folder label, a local file by its labelled path, a waiting question and a standing
        approval by the tool's title. Each looks up only on demand."""
        words = JOB_WORDS.get(lang, JOB_WORDS["en"])

        def job(value: str) -> Optional[str]:
            jobs = getattr(framework, "job_service", None)
            if jobs is None:
                return None
            kind = jobs.get(value).kind
            return words.get(kind) or kind.replace("_", " ")

        def request(value: str) -> Optional[str]:
            label = getattr(getattr(framework, "build_service", None), "request_label", None)
            return label(value) if callable(label) else None

        def artifact(value: str) -> Optional[str]:
            service = getattr(framework, "build_service", None)
            return service.artifact_entry(value)[1] if service is not None else None

        def local_file(value: str) -> Optional[str]:
            from orchestrator.tools.local_files import LocalFiles
            return LocalFiles.for_framework(framework).ref_of(value)

        def approval(value: str) -> Optional[str]:
            service = getattr(getattr(framework, "tool_registry", None), "approvals", None)
            record = service.store.get(value) if service is not None else None
            return QUESTION_WORDS.get(lang, QUESTION_WORDS["en"]).format(title=record.title) if record else None

        def grant(value: str) -> Optional[str]:
            registry = getattr(framework, "tool_registry", None)
            if registry is None:
                return None
            found = registry.policy_store.load().grant_by_id(value)   # (caller, grant) or None
            if found is None:
                return None
            tool = found[1].tool
            try:
                title = registry.get(tool).title
            except Exception:  # noqa: BLE001 - a tool that is gone: its name
                title = tool
            return GRANT_WORDS.get(lang, GRANT_WORDS["en"]).format(title=title)

        return cls({"job": job, "request": request, "artifact": artifact, "file": local_file, "approval": approval,
                    "grant": grant})


# What a job does, for the person (job_service.JOB_KINDS).
JOB_WORDS: Dict[str, Dict[str, str]] = {
    "en": {"crawl": "Deep Ingest", "deploy": "deploy to a board", "package": "deployment package",
           "probe": "hardware probe", "pack_import": "pack import", "pack_export": "pack export",
           "snapshot_restore": "knowledge base restore", "hf_download": "Hugging Face download",
           "runtime_build": "build of the local model server", "runtime_start": "start of the local model server",
           "benchmark": "benchmark", "approval": "question waiting for you"},
    "de": {"crawl": "Deep Ingest", "deploy": "Aufspielen auf ein Board", "package": "Bereitstellungspaket",
           "probe": "Hardware-Untersuchung", "pack_import": "Paket-Import", "pack_export": "Paket-Export",
           "snapshot_restore": "Wiederherstellen der Wissensbasis", "hf_download": "Hugging-Face-Download",
           "runtime_build": "Bau des lokalen Modellservers", "runtime_start": "Start des lokalen Modellservers",
           "benchmark": "Messung", "approval": "Frage, die auf dich wartet"},
}
QUESTION_WORDS = {"en": "question '{title}'", "de": "Frage „{title}“"}
GRANT_WORDS = {"en": "standing approval for '{title}'", "de": "Dauerfreigabe für „{title}“"}
