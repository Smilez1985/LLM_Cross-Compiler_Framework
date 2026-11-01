#!/usr/bin/env python3
"""
The board bundle's manifest (schema llmcf-bundle/1): what a bundle holds and what its installer does, as data only.

A manifest lists the bundle's components, each made from one building block (blocks.py) with its variables resolved
and checked, the files it needs (each with its sha256 and size, in the bundle or a pinned online item), how it starts
(a program inside its own folder with checked arguments) and, separately, the root actions it needs, each a typed
action (ROOT_ACTIONS), never a shell line. The generator (generator.py) writes it, the framework's signing key signs
its exact bytes, and the on-board installer gets it next to its compiled steps (installer.render_steps).

Every value is checked here by its type (VARIABLE_TYPES): a port is a number from 1024 to 65535, a user an account
name that is not root, a word or a file name only letters, digits, '.', '_' and '-' (never starting with '-' or '.'),
a URL https (or http to an address the locality rule counted as local) without quoting characters. A value that does
not pass is refused before anything is written; nothing a person, a block or an AI typed is ever pasted into shell
text (the installer's steps carry the checked values as tab-separated data).

A manifest is read from JSON or YAML (load_manifest); JSON is what the generator writes and signs (dump_manifest:
sorted keys, two-space indent, one final newline).
"""

import json
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA = "llmcf-bundle/1"
MAX_MANIFEST_BYTES = 1 << 20
MAX_COMPONENTS = 16
MAX_FILES = 64
MAX_ARGS = 48

# Component kinds a building block may have (the ROADMAP's bundle components).
KINDS = ("llm", "stt", "tts", "vision", "gateway", "harness")
# What the installer does with a file of the bundle (its role decides that, never its name).
ROLES = ("model", "mmproj", "program", "runtime", "config", "data")
FILE_MODES = ("0600", "0644", "0700", "0755")
# The root actions a manifest may list (typed; llmcf-install, the Rust root helper, carries them out; the unprivileged
# installer never does, it stops before them or skips them in the "unprivileged only" variant).
ROOT_ACTIONS = ("create_user", "install_unit", "set_cgroup", "place_root_file",
                "remove_user", "remove_unit", "remove_cgroup", "remove_root_file")
# Where place_root_file may put a file (fixed places only; llmcf-install knows each one).
ROOT_PLACES = ("unit-dropin", "tmpfiles", "sysctl")
MODES = ("full", "unprivileged")

RE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$", re.ASCII)
RE_WORD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$", re.ASCII)
RE_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$", re.ASCII)
RE_VAR = re.compile(r"^[a-z][a-z0-9_]{0,31}$", re.ASCII)
RE_SHA = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
RE_VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$", re.ASCII)
# A licence as SPDX writes it (or a short word such as "llama3" or "unknown").
RE_LICENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$", re.ASCII)
# One argument of a component's program: the gate's argument characters (gate.py _RE_ARG), and @DIR@ for the
# component's own folder on the board (the installer puts "$its folder" in its place when it writes start.sh).
RE_ARG = re.compile(r"^(?:@DIR@/)?[A-Za-z0-9._+:=,-][A-Za-z0-9._+/:=,-]{0,255}$", re.ASCII)
# An online item: https (or http, only for an address the locality rule counted as local), no quoting characters,
# no credentials, no spaces.
RE_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~+,=:@%/-]*)?$", re.ASCII)
# A unit llmcf-install writes, a file it puts into one of the ROOT_PLACES (only llmcf's own .conf files) and a word
# of the board facts (os, init system). llmcf-install (rust/llmcf-install/src/manifest.rs) checks the very same rules;
# tests/data/bundle_manifest_vectors.json holds the vectors both sides are tested with.
RE_UNIT = re.compile(r"^llmcf-[a-z0-9-]{1,40}\.service$", re.ASCII)
RE_ROOT_FILE = re.compile(r"^llmcf-[a-z0-9._-]{1,60}\.conf$", re.ASCII)
RE_FACT = re.compile(r"^[a-z0-9_.-]{1,32}$", re.ASCII)
MAX_ROOT_ACTIONS = 64

# The variable types of a building block, with the bounds every value is checked against.
VARIABLE_TYPES = ("port", "uint", "threads", "memory_mb", "bool", "user", "word", "choice", "file")
_BOUNDS = {"port": (1024, 65535), "uint": (0, 2 ** 31 - 1), "threads": (0, 512), "memory_mb": (16, 1 << 20)}


class ManifestError(ValueError):
    """A manifest, a variable or a value that is not what the schema takes; the message says which and why."""


def check_value(kind: str, value: Any, *, choices: Tuple[str, ...] = (), low: Optional[int] = None,
                high: Optional[int] = None, name: str = "value") -> Any:
    """`value` checked against the variable type `kind` and returned in its canonical form (int, bool or str);
    ManifestError otherwise. Text values come from a person, a block or an AI: quotes, newlines, `$(...)`, `../` and
    every other character outside the type's set are refused, never escaped."""
    if kind not in VARIABLE_TYPES:
        raise ManifestError(f"{name}: unknown variable type {kind!r}")
    if kind in _BOUNDS:
        lo, hi = _BOUNDS[kind]
        lo = lo if low is None else max(lo, int(low))
        hi = hi if high is None else min(hi, int(high))
        if isinstance(value, bool):
            raise ManifestError(f"{name}: a {kind} is a number, not yes/no")
        if isinstance(value, str):
            text = value.strip()
            if not re.fullmatch(r"[0-9]{1,10}", text, re.ASCII):
                raise ManifestError(f"{name}: {kind} must be a whole number, not {value!r}"[:200])
            value = int(text)
        if not isinstance(value, int) or not lo <= value <= hi:
            raise ManifestError(f"{name}: {kind} must be from {lo} to {hi}")
        return int(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("yes", "true", "on", "1"):
            return True
        if text in ("no", "false", "off", "0"):
            return False
        raise ManifestError(f"{name}: yes or no, not {value!r}"[:200])
    if not isinstance(value, str):
        raise ManifestError(f"{name}: a {kind} is text")
    if kind == "user":   # the one account check (remote/address.valid_user), lower-case as useradd takes it
        from orchestrator.remote.address import valid_user
        if not valid_user(value) or value != value.lower():
            raise ManifestError(f"{name}: an account name (lower-case letters, digits, '_', '.', '-'; never root)")
        return value
    if kind == "choice":
        if value not in choices:
            raise ManifestError(f"{name}: one of {', '.join(choices)}")
        return value
    pattern = RE_FILE if kind == "file" else RE_WORD
    if not pattern.fullmatch(value) or value in (".", ".."):
        raise ManifestError(f"{name}: only letters, digits, '.', '_' and '-' (not starting with '.' or '-'), not "
                            f"{value!r}"[:200])
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileEntry(_Strict):
    """One file of a component: `name` in the component's folder on the board, its sha256 and size, the file mode it
    gets, its `role`, where it comes from (`source` bundle: sent with the bundle; online: fetched by the installer
    from `url` and checked against the same sha256), and for a model its format and licence."""
    name: str
    sha256: str
    size: int = Field(ge=0, le=1 << 46)
    mode: Literal["0600", "0644", "0700", "0755"] = "0644"
    role: Literal["model", "mmproj", "program", "runtime", "config", "data"]
    source: Literal["bundle", "online"] = "bundle"
    url: Optional[str] = None
    format: Optional[str] = None
    licence: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        check_value("file", v, name="file name")
        return v

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v):
        if not RE_SHA.fullmatch(v):
            raise ValueError("sha256 must be 64 lower-case hex digits")
        return v

    @field_validator("format")
    @classmethod
    def _format(cls, v):
        if v is not None and not RE_WORD.fullmatch(v):
            raise ValueError("format must be a plain word")
        return v

    @field_validator("licence")
    @classmethod
    def _licence(cls, v):
        if v is not None and not RE_LICENCE.fullmatch(v):
            raise ValueError("licence must be an SPDX identifier or a short word")
        return v

    @model_validator(mode="after")
    def _source(self):
        if self.source == "online":
            if not self.url or not RE_URL.fullmatch(self.url):
                raise ValueError("an online item needs a plain https URL (no quotes, spaces or credentials)")
        elif self.url is not None:
            raise ValueError("only an online item has a URL")
        return self


class StartSpec(_Strict):
    """How a component starts: `program` relative to its folder, `args` checked one by one (RE_ARG)."""
    program: str
    args: Tuple[str, ...] = ()

    @field_validator("program")
    @classmethod
    def _program(cls, v):
        parts = v.split("/")
        if not 1 <= len(parts) <= 4 or any(not RE_FILE.fullmatch(p) for p in parts):
            raise ValueError("program must be a plain relative path in the component's folder")
        return v

    @field_validator("args")
    @classmethod
    def _args(cls, v):
        if len(v) > MAX_ARGS:
            raise ValueError(f"at most {MAX_ARGS} arguments")
        for arg in v:
            if not RE_ARG.fullmatch(arg) or ".." in arg.split("/"):
                raise ValueError(f"argument {arg!r} holds characters a program argument never takes"[:200])
        return v


class Component(_Strict):
    """One component of the bundle, made from the block `block` (version, sha256 of its block folder, its trust when
    the bundle was built) with its checked `variables`."""
    id: str
    kind: Literal["llm", "stt", "tts", "vision", "gateway", "harness"]
    block: str
    block_version: str
    block_sha256: str
    block_trust: Literal["framework", "indexed", "signed", "own"] = "framework"
    variables: Dict[str, Any] = Field(default_factory=dict)
    files: Tuple[FileEntry, ...] = ()
    start: Optional[StartSpec] = None
    keygen: bool = False   # the runtime's own key file (llama-server.sh keygen), made on the board

    @field_validator("id", "block")
    @classmethod
    def _id(cls, v):
        if not RE_ID.fullmatch(v):
            raise ValueError("an id is lower-case letters, digits, '.', '_' and '-'")
        return v

    @field_validator("block_version")
    @classmethod
    def _version(cls, v):
        if not RE_VERSION.fullmatch(v):
            raise ValueError("a block version is MAJOR.MINOR.PATCH")
        return v

    @field_validator("block_sha256")
    @classmethod
    def _bsha(cls, v):
        if not RE_SHA.fullmatch(v):
            raise ValueError("block_sha256 must be 64 lower-case hex digits")
        return v

    @field_validator("variables")
    @classmethod
    def _variables(cls, v):
        for key, value in v.items():
            if not RE_VAR.fullmatch(key):
                raise ValueError(f"variable name {key!r} is not a plain name"[:200])
            if isinstance(value, bool) or (isinstance(value, int) and 0 <= value < 2 ** 31):
                continue
            if not isinstance(value, str) or not (value == "" or RE_WORD.fullmatch(value)):
                raise ValueError(f"variable {key}: only numbers, yes/no and plain words")
        return v

    @model_validator(mode="after")
    def _files(self):
        names = [f.name for f in self.files]
        if len(names) != len(set(names)):
            raise ValueError(f"component {self.id}: a file name twice")
        if len(names) > MAX_FILES:
            raise ValueError(f"component {self.id}: at most {MAX_FILES} files")
        if self.start is not None:
            first = self.start.program.split("/")[0]
            runtime = [f for f in self.files if f.role == "runtime"]
            if first not in names and not (runtime and first == "runtime"):
                raise ValueError(f"component {self.id}: its program is none of its files")
        return self


class RootAction(_Strict):
    """A root action, typed: `action` (ROOT_ACTIONS), the component it belongs to and its checked values (user,
    unit, memory_max_mb, cpu_weight, place, file). Never a shell line."""
    action: Literal["create_user", "install_unit", "set_cgroup", "place_root_file",
                    "remove_user", "remove_unit", "remove_cgroup", "remove_root_file"]
    component: str
    user: Optional[str] = None
    unit: Optional[str] = None
    memory_max_mb: Optional[int] = Field(default=None, ge=16, le=1 << 20)
    cpu_weight: Optional[int] = Field(default=None, ge=1, le=10000)
    place: Optional[Literal["unit-dropin", "tmpfiles", "sysctl"]] = None
    file: Optional[str] = None

    @model_validator(mode="after")
    def _values(self):
        if not RE_ID.fullmatch(self.component):
            raise ValueError("component must be a component id")
        if self.user is not None:
            check_value("user", self.user, name="user")
        if self.unit is not None and not RE_UNIT.fullmatch(self.unit):
            raise ValueError("a unit is named llmcf-<name>.service")
        if self.file is not None:
            check_value("file", self.file, name="file")
        need = {"create_user": ("user",), "remove_user": ("user",), "install_unit": ("unit", "user"),
                "remove_unit": ("unit",), "set_cgroup": ("unit", "memory_max_mb"), "remove_cgroup": ("unit",),
                "place_root_file": ("place", "file"), "remove_root_file": ("place", "file")}[self.action]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"{self.action} needs {', '.join(missing)}")
        if self.action.endswith("root_file"):
            if not RE_ROOT_FILE.fullmatch(self.file or ""):
                raise ValueError("a root file is named llmcf-<name>.conf")
            if self.place == "unit-dropin" and self.unit is None:
                raise ValueError("a unit drop-in names its unit")
        return self


class BoardFacts(_Strict):
    """The board the bundle was made for (from its probe: labels and fixed words only, no address, no account)."""
    device_id: str = Field(pattern=r"^[0-9a-f]{8,64}$")
    os: str = Field("linux", pattern=RE_FACT.pattern)
    arch: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    init_system: Optional[str] = Field(None, pattern=RE_FACT.pattern)


class BundleManifest(_Strict):
    """The whole manifest (see the module text). `mode` full: the installer stops before the root actions and says
    they need llmcf-install; unprivileged: the person chose to run the unprivileged part only (root actions skipped
    and reported)."""
    schema_: Literal["llmcf-bundle/1"] = Field(SCHEMA, alias="schema")
    bundle_id: str
    created: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    framework_version: str = Field(pattern=r"^[0-9A-Za-z.+-]{1,32}$")
    board: BoardFacts
    mode: Literal["full", "unprivileged"] = "full"
    components: Tuple[Component, ...]
    root_actions: Tuple[RootAction, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("bundle_id")
    @classmethod
    def _bid(cls, v):
        if not RE_ID.fullmatch(v):
            raise ValueError("bundle_id is lower-case letters, digits, '.', '_' and '-'")
        return v

    @model_validator(mode="after")
    def _whole(self):
        if not 1 <= len(self.components) <= MAX_COMPONENTS:
            raise ValueError(f"a bundle has 1 to {MAX_COMPONENTS} components")
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("a component id twice")
        if len(self.root_actions) > MAX_ROOT_ACTIONS:
            raise ValueError(f"at most {MAX_ROOT_ACTIONS} root actions")
        units = {a.unit for a in self.root_actions if a.action == "install_unit"}
        for action in self.root_actions:
            if action.component not in ids:
                raise ValueError(f"root action {action.action} names no component of the bundle")
            comp = next(c for c in self.components if c.id == action.component)
            if action.action == "install_unit" and comp.start is None:
                raise ValueError(f"root action install_unit: component {comp.id} has no start")
            if action.action == "set_cgroup" and action.unit not in units:
                raise ValueError(f"root action set_cgroup: unit {action.unit} is installed by no install_unit of "
                                 "the bundle")
            if action.action == "place_root_file" and action.file not in {f.name for f in comp.files}:
                raise ValueError(f"root action place_root_file: {action.file} is no file of component {comp.id}")
        return self

    def as_json(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    @property
    def size(self) -> int:
        """The bytes the bundle sends (its files of source bundle)."""
        return sum(f.size for c in self.components for f in c.files if f.source == "bundle")


def dump_manifest(manifest: BundleManifest) -> bytes:
    """The manifest's bytes as the generator writes and signs them."""
    return (json.dumps(manifest.as_json(), indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def load_manifest(data: Any) -> BundleManifest:
    """A manifest from JSON or YAML text/bytes (or an already parsed mapping); ManifestError with the reasons."""
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_MANIFEST_BYTES:
            raise ManifestError("the manifest is larger than 1 MiB")
        data = bytes(data).decode("utf-8", errors="strict")
    if isinstance(data, str):
        if len(data) > MAX_MANIFEST_BYTES:
            raise ManifestError("the manifest is larger than 1 MiB")
        text = data.lstrip()
        try:
            data = json.loads(text) if text.startswith("{") else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError) as e:
            raise ManifestError(f"the manifest is neither JSON nor YAML: {str(e)[:200]}") from None
    if not isinstance(data, dict):
        raise ManifestError("the manifest is not a mapping")
    try:
        return BundleManifest.model_validate(data)
    except ValidationError as e:
        raise ManifestError("; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                                      for err in e.errors()[:10])) from None


def problems_of(data: Any) -> List[str]:
    """Why `data` is no valid manifest ([] when it is one)."""
    try:
        load_manifest(data)
        return []
    except ManifestError as e:
        return [str(e)]


__all__ = ["BoardFacts", "BundleManifest", "Component", "FILE_MODES", "FileEntry", "KINDS", "MODES", "ManifestError",
           "MAX_ROOT_ACTIONS", "RE_ARG", "RE_FACT", "RE_FILE", "RE_ID", "RE_ROOT_FILE", "RE_SHA", "RE_UNIT", "RE_URL",
           "RE_WORD", "ROLES", "ROOT_ACTIONS", "ROOT_PLACES",
           "RootAction", "SCHEMA", "StartSpec", "VARIABLE_TYPES", "check_value", "dump_manifest", "load_manifest",
           "problems_of"]
