#!/usr/bin/env python3
"""
Building blocks of the board bundle (schema llmcf-bundle-block/1): one folder per block with `block.yml` and its guide
`README.md`, published and installed as community packs of kind "block" like target modules (Core/community_manager:
signed, scrubbed, checked by check_block_folder before it is installed).

A block says what it is (`kind`: llm, stt, tts, vision, gateway, harness), whether it can be built (`status` ready) or
is a documented template only (`status` template: its guide says what is missing), its typed variables with defaults
and bounds (manifest.VARIABLE_TYPES), the files it needs with their role, where they come from (`from` person: a file
the person picks or a pinned online item; runtime: the board's self-built llama.cpp runtime package of deploy way 2;
framework: a program the framework builds itself for the board, root_helper.FRAMEWORK_PROGRAMS such as llmcf-gateway)
and the formats and licence its models must have, its memory needs, how it starts (a program inside its folder with
argument templates: `{variable}` for a checked value, `@FILE:role@` for one of its files, `{when: variable, args:
[...]}` for arguments only when the variable is set) and whether its unit needs root (`unit`: autostart through
llmcf-install).

Where blocks come from, and their trust (decided once, here): the framework's own blocks below this package's
`blocks/` folder (trust "framework"); blocks installed from the signed community index ("indexed": the index's trust
verified or community); a block pack installed from a file and signed by its author ("signed"); and the person's own
blocks, made with new_block and never signed ("own"). Enterprise mode takes only "framework" and "indexed"; private
mode takes every block, an own unsigned one after a plain warning (generator.plan_bundle).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from orchestrator.runtime.bundle.manifest import (KINDS, RE_ARG, RE_FILE, RE_ID, RE_VAR, RE_VERSION, ROLES,
                                                  VARIABLE_TYPES, ManifestError, check_value)

BLOCK_SCHEMA = "llmcf-bundle-block/1"
BLOCK_FILE = "block.yml"
GUIDE_FILE = "README.md"
BUILTIN_DIR = Path(__file__).resolve().parent / "blocks"
COMMUNITY_SUBDIR = ("community", "blocks")
STATUSES = ("ready", "template")
SOURCES = ("person", "runtime", "framework")
# The formats a block may name for a file, and how the generator recognizes each by its first bytes (or by its name
# where a format has no magic number).
FORMATS = ("gguf", "mmproj-gguf", "ggml-whisper", "onnx", "sherpa-onnx", "elf", "tar.gz", "text")
TRUSTS = ("framework", "indexed", "signed", "own")
ENTERPRISE_TRUSTS = ("framework", "indexed")
MAX_BLOCK_BYTES = 64 * 1024


class BlockError(ValueError):
    """A block that is not what the block schema takes; the message names the field."""


@dataclass(frozen=True)
class Variable:
    name: str
    type: str
    default: Any = None
    required: bool = False
    choices: Tuple[str, ...] = ()
    min: Optional[int] = None
    max: Optional[int] = None
    help: str = ""

    def check(self, value: Any) -> Any:
        return check_value(self.type, value, choices=self.choices, low=self.min, high=self.max, name=self.name)


@dataclass(frozen=True)
class FileNeed:
    """A file a block needs: its role, where it comes from, the name it gets on the board, its mode, the formats it
    may have, whether its licence must be named, and its size bounds."""
    role: str
    source: str
    name: str = ""
    mode: str = "0644"
    formats: Tuple[str, ...] = ()
    licence: str = "optional"   # required: the person names the model's licence; optional
    min_bytes: int = 0
    max_bytes: int = 0
    note: str = ""
    # a runtime file: the project of the framework's self-build path whose board package it is
    # (runtime/build/projects.py: llama.cpp, whisper.cpp)
    project: str = "llama.cpp"


@dataclass(frozen=True)
class Terms:
    """The licence terms of a part a block brings in (a program, a runtime, a model family) that the person reads and
    accepts before the block is used: shown in full before anything is built or downloaded. `free` false: terms that
    are not a free licence; such a block is never part of a preset (opt-in only)."""
    part: str
    licence: str
    free: bool
    text: str

    @property
    def sha256(self) -> str:
        from orchestrator.utils.helpers import sha256_bytes
        return sha256_bytes(f"{self.part}\n{self.licence}\n{self.free}\n{self.text}".encode("utf-8"))


@dataclass(frozen=True)
class Block:
    """A loaded block: its spec, folder, trust and the sha256 of its folder (community_pack.tree_sha256)."""
    id: str
    version: str
    kind: str
    title: str
    status: str
    summary: str
    variables: Tuple[Variable, ...]
    files: Tuple[FileNeed, ...]
    program: str
    args: Tuple[Any, ...]
    memory_base_mb: int
    memory_model_factor: float
    keygen: bool
    unit: bool
    folder: Path
    trust: str
    sha256: str
    notes: Tuple[str, ...] = field(default_factory=tuple)
    terms: Tuple[Terms, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def terms_sha256(self) -> str:
        """One digest of all its terms (what an acceptance is bound to); empty when the block has none."""
        if not self.terms:
            return ""
        from orchestrator.utils.helpers import sha256_bytes
        return sha256_bytes("\n".join(t.sha256 for t in self.terms).encode("ascii"))

    @property
    def opt_in(self) -> bool:
        """Terms that are not a free licence: never part of a preset, used only after the person accepted them."""
        return any(not t.free for t in self.terms)

    def terms_text(self) -> str:
        """The terms in full, as the person reads them before accepting (CLI and window)."""
        return "\n\n".join(f"{t.part} ({t.licence}{'' if t.free else ', not a free licence'}):\n{t.text}"
                           for t in self.terms)

    def variable(self, name: str) -> Optional[Variable]:
        return next((v for v in self.variables if v.name == name), None)

    def guide(self) -> str:
        path = self.folder / GUIDE_FILE
        try:
            return path.read_text(encoding="utf-8")[:20000] if path.is_file() else ""
        except (OSError, UnicodeDecodeError):
            return ""

    def label(self) -> Dict[str, Any]:
        """The block as labels only (an AI may read them): id, version, kind, status, trust, title, variable names
        and types, file roles and formats."""
        return {"id": self.id, "version": self.version, "kind": self.kind, "status": self.status, "trust": self.trust,
                "title": self.title, "summary": self.summary,
                "variables": [{"name": v.name, "type": v.type, "default": v.default,
                               **({"choices": list(v.choices)} if v.choices else {})} for v in self.variables],
                "files": [{"role": f.role, "from": f.source, "formats": list(f.formats), "licence": f.licence,
                           **({"project": f.project} if f.source == "runtime" else {})} for f in self.files],
                # the terms as labels (the person reads their text in the CLI or the window and accepts them there)
                "terms": [{"part": t.part, "licence": t.licence, "free": t.free} for t in self.terms]}


def _str(spec: dict, key: str, limit: int = 200, default: str = "") -> str:
    value = spec.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n" for c in value):
        raise BlockError(f"{key}: a plain text of at most {limit} characters")
    return value


def _variable(raw: Any) -> Variable:
    if not isinstance(raw, dict):
        raise BlockError("a variable is a mapping")
    unknown = set(raw) - {"name", "type", "default", "required", "choices", "min", "max", "help"}
    if unknown:
        raise BlockError(f"variable: unknown fields {', '.join(sorted(unknown))}")
    name, kind = raw.get("name"), raw.get("type")
    if not isinstance(name, str) or not RE_VAR.fullmatch(name):
        raise BlockError(f"variable name {name!r} is not a plain name"[:200])
    if kind not in VARIABLE_TYPES:
        raise BlockError(f"variable {name}: type must be one of {', '.join(VARIABLE_TYPES)}")
    choices = tuple(raw.get("choices") or ())
    if kind == "choice" and (not choices or not all(isinstance(c, str) and re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}", c, re.ASCII) for c in choices)):
        raise BlockError(f"variable {name}: a choice lists its plain-word choices")
    for bound in ("min", "max"):
        if raw.get(bound) is not None and (isinstance(raw[bound], bool) or not isinstance(raw[bound], int)):
            raise BlockError(f"variable {name}: {bound} is a whole number")
    var = Variable(name=name, type=kind, default=raw.get("default"), required=bool(raw.get("required", False)),
                   choices=choices, min=raw.get("min"), max=raw.get("max"), help=_str(raw, "help", 300))
    if var.default is not None and not (kind == "user" and var.default == ""):
        try:
            object.__setattr__(var, "default", var.check(var.default))
        except ManifestError as e:
            raise BlockError(f"variable {name}: its default does not pass its own check ({e})") from None
    return var


def _file(raw: Any) -> FileNeed:
    if not isinstance(raw, dict):
        raise BlockError("a file is a mapping")
    unknown = set(raw) - {"role", "from", "name", "mode", "formats", "licence", "min_bytes", "max_bytes", "note",
                          "project"}
    if unknown:
        raise BlockError(f"file: unknown fields {', '.join(sorted(unknown))}")
    role, source = raw.get("role"), raw.get("from", "person")
    if role not in ROLES:
        raise BlockError(f"file role must be one of {', '.join(ROLES)}")
    if source not in SOURCES:
        raise BlockError(f"file {role}: from must be person, runtime or framework")
    from orchestrator.runtime.build.projects import PROJECTS
    project = raw.get("project", "llama.cpp")
    if project not in PROJECTS or (project != "llama.cpp" and source != "runtime"):
        raise BlockError(f"file {role}: project names the framework's self-built runtime package "
                         f"({', '.join(PROJECTS)}), only for a runtime file")
    name = raw.get("name", "") or ""
    if name and not RE_FILE.fullmatch(str(name)):
        raise BlockError(f"file {role}: name must be a plain file name")
    if source == "runtime" and role != "runtime":
        raise BlockError("only the runtime role comes from the board's runtime package")
    if source == "framework":
        # a program the framework builds itself from its Rust workspace (root_helper.FRAMEWORK_PROGRAMS)
        from orchestrator.runtime.bundle.root_helper import FRAMEWORK_PROGRAMS
        if role != "program" or name not in FRAMEWORK_PROGRAMS:
            raise BlockError(f"file {role}: from framework is only a program the framework builds "
                             f"({', '.join(FRAMEWORK_PROGRAMS)})")
    mode = str(raw.get("mode", "0755" if role == "program" else "0644"))
    if mode not in ("0600", "0644", "0700", "0755"):
        raise BlockError(f"file {role}: mode must be 0600, 0644, 0700 or 0755")
    formats = tuple(raw.get("formats") or ())
    if any(f not in FORMATS for f in formats):
        raise BlockError(f"file {role}: formats must be among {', '.join(FORMATS)}")
    licence = raw.get("licence", "optional")
    if licence not in ("required", "optional"):
        raise BlockError(f"file {role}: licence is required or optional")
    sizes = []
    for key in ("min_bytes", "max_bytes"):
        value = raw.get(key, 0) or 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BlockError(f"file {role}: {key} is a whole number")
        sizes.append(value)
    return FileNeed(role=role, source=source, name=str(name), mode=mode, formats=formats, licence=licence,
                    min_bytes=sizes[0], max_bytes=sizes[1], note=_str(raw, "note", 300), project=project)


MAX_TERMS_TEXT = 4000


def _terms(raw: Any) -> Tuple[Terms, ...]:
    """The block's `terms` list: each {part, licence (an SPDX identifier or a short word), free (yes/no), text}."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > 8:
        raise BlockError("terms is a list of at most 8 parts")
    out = []
    from orchestrator.runtime.bundle.manifest import RE_LICENCE
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"part", "licence", "free", "text"} or \
                not {"part", "licence", "free", "text"} <= set(item):
            raise BlockError("a terms entry is {part, licence, free, text}")
        licence = item.get("licence")
        if not isinstance(licence, str) or not RE_LICENCE.fullmatch(licence):
            raise BlockError("terms licence is an SPDX identifier or a short word")
        if not isinstance(item.get("free"), bool):
            raise BlockError("terms free is yes or no")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TERMS_TEXT or \
                any(ord(c) < 32 and c not in "\n" for c in text):
            raise BlockError(f"terms text is a plain text of at most {MAX_TERMS_TEXT} characters")
        out.append(Terms(part=_str(item, "part", 120), licence=licence, free=item["free"], text=text.strip()))
    return tuple(out)


def _check_args(args: Any, variables: Dict[str, Variable], roles: set, depth: int = 0) -> Tuple[Any, ...]:
    if not isinstance(args, list) or len(args) > 64:
        raise BlockError("start.args is a list of at most 64 items")
    out: List[Any] = []
    for item in args:
        if isinstance(item, dict):
            if depth or set(item) != {"when", "args"} or item["when"] not in variables:
                raise BlockError("a conditional argument is {when: VARIABLE, args: [...]} (not nested)")
            out.append({"when": item["when"], "args": _check_args(item["args"], variables, roles, 1)})
            continue
        if not isinstance(item, str):
            raise BlockError("an argument is a text")
        m = re.fullmatch(r"\{([a-z][a-z0-9_]{0,31})\}", item)
        f = re.fullmatch(r"@FILE:([a-z]+)@", item)
        if m:
            if m.group(1) not in variables:
                raise BlockError(f"argument {item}: no such variable")
            if variables[m.group(1)].type in ("bool", "file"):
                raise BlockError(f"argument {item}: a yes/no or file variable is no argument value")
        elif f:
            if f.group(1) not in roles:
                raise BlockError(f"argument {item}: the block has no file of that role")
        elif not RE_ARG.fullmatch(item) or item.startswith("@"):
            raise BlockError(f"argument {item!r} holds characters a program argument never takes"[:200])
        out.append(item)
    return tuple(out)


def parse_block(text: str, folder: Path, trust: str = "own", sha256: str = "") -> Block:
    """A block from its block.yml text; BlockError with the field that is wrong."""
    if len(text.encode("utf-8")) > MAX_BLOCK_BYTES:
        raise BlockError("block.yml is larger than 64 KiB")
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise BlockError(f"block.yml is no YAML: {str(e)[:200]}") from None
    if not isinstance(spec, dict):
        raise BlockError("block.yml is not a mapping")
    allowed = {"schema", "id", "version", "kind", "title", "status", "summary", "variables", "files", "start",
               "memory", "keygen", "unit", "notes", "terms"}
    unknown = set(spec) - allowed
    if unknown:
        raise BlockError(f"unknown fields: {', '.join(sorted(map(str, unknown)))}")
    if spec.get("schema") != BLOCK_SCHEMA:
        raise BlockError(f"schema must be {BLOCK_SCHEMA}")
    block_id, version = spec.get("id"), str(spec.get("version", ""))
    if not isinstance(block_id, str) or not RE_ID.fullmatch(block_id):
        raise BlockError("id is lower-case letters, digits, '.', '_' and '-'")
    if not RE_VERSION.fullmatch(version):
        raise BlockError("version is MAJOR.MINOR.PATCH")
    kind, status = spec.get("kind"), spec.get("status", "ready")
    if kind not in KINDS:
        raise BlockError(f"kind must be one of {', '.join(KINDS)}")
    if status not in STATUSES:
        raise BlockError("status is ready or template")
    variables = tuple(_variable(v) for v in (spec.get("variables") or []))
    names = [v.name for v in variables]
    if len(names) != len(set(names)):
        raise BlockError("a variable name twice")
    files = tuple(_file(f) for f in (spec.get("files") or []))
    roles = [f.role for f in files]
    if len(roles) != len(set(roles)):
        raise BlockError("a file role twice")
    start = spec.get("start") or {}
    if not isinstance(start, dict) or set(start) - {"program", "args"}:
        raise BlockError("start is {program, args}")
    program = str(start.get("program", ""))
    if status == "ready":
        if not program:
            raise BlockError("a ready block names its program")
        parts = program.replace("@RUNTIME@", "0" * 16).split("/")
        if not 1 <= len(parts) <= 4 or any(not RE_FILE.fullmatch(p) for p in parts):
            raise BlockError("start.program is a plain relative path (runtime/@RUNTIME@/... or a file of the block)")
        if parts[0] == "runtime" and "runtime" not in roles:
            raise BlockError("start.program lies in the runtime package, but the block needs none")
    args = _check_args(start.get("args") or [], {v.name: v for v in variables}, set(roles))
    memory = spec.get("memory") or {}
    if not isinstance(memory, dict):
        raise BlockError("memory is {base_mb, model_factor}")
    base = memory.get("base_mb", 64)
    factor = memory.get("model_factor", 1.0)
    if isinstance(base, bool) or not isinstance(base, int) or not 0 <= base <= 1 << 20:
        raise BlockError("memory.base_mb is a whole number of MB")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not 0 <= factor <= 8:
        raise BlockError("memory.model_factor is a number from 0 to 8")
    notes = spec.get("notes") or []
    if not isinstance(notes, list) or not all(isinstance(n, str) and len(n) <= 300 for n in notes):
        raise BlockError("notes is a list of short texts")
    return Block(id=block_id, version=version, kind=kind, title=_str(spec, "title", 120, block_id), status=status,
                 summary=_str(spec, "summary", 500), variables=variables, files=files, program=program, args=args,
                 memory_base_mb=base, memory_model_factor=float(factor), keygen=bool(spec.get("keygen", False)),
                 unit=bool(spec.get("unit", False)), folder=Path(folder), trust=trust, sha256=sha256,
                 notes=tuple(notes), terms=_terms(spec.get("terms")))


def _tree_sha256(folder: Path) -> str:
    from orchestrator.Core.community_pack import tree_sha256
    return tree_sha256(folder)


def check_block_folder(folder: Path) -> List[str]:
    """The problems of a block folder ([] when there are none): exactly block.yml and README.md (the guide), no
    other file, block.yml valid, a guide that names the block's model needs. The community pack check of kind
    "block" and the wizard use it."""
    folder = Path(folder)
    problems: List[str] = []
    try:
        from orchestrator.Core.community_pack import RESERVED_NAMES, list_files
        names = {rel for rel, _ in list_files(folder)} - set(RESERVED_NAMES)
    except Exception as e:  # noqa: BLE001 - a link, a special file: the pack check's own words
        return [str(e)[:300]]
    for extra in sorted(names - {BLOCK_FILE, GUIDE_FILE}):
        problems.append(f"{extra}: not allowed in a block (only {BLOCK_FILE} and {GUIDE_FILE})")
    if BLOCK_FILE not in names:
        return problems + [f"{BLOCK_FILE} is missing"]
    if GUIDE_FILE not in names:
        problems.append(f"{GUIDE_FILE} is missing: every block has a guide naming the models it needs")
    try:
        block = parse_block((folder / BLOCK_FILE).read_text(encoding="utf-8"), folder)
    except (BlockError, OSError, UnicodeDecodeError) as e:
        return problems + [f"{BLOCK_FILE}: {e}"]
    guide = (folder / GUIDE_FILE).read_text(encoding="utf-8", errors="replace") if GUIDE_FILE in names else ""
    if guide and "## Models" not in guide:
        problems.append(f"{GUIDE_FILE} has no '## Models' section naming the models, formats, memory and licences "
                        "the block needs")
    if block.status == "ready" and not block.files:
        problems.append("a ready block names the files it needs")
    return problems


def _provenance_trust(folder: Path, index: Any = None) -> Tuple[str, str]:
    """(trust, note) of a block in community/blocks/ from its install record (.lcf-provenance.json, written by
    CommunityManager.finish_install): "revoked" when the verified cached index `index`
    (CommunityManager.cached_index(revocations=True)) revokes it; indexed or signed only while the folder is exactly
    what was installed (the rule of Provenance.installed_unchanged: tree_sha256 of the folder is the record's
    content_sha256 or local_sha256); a block changed since its install is the person's own, with a note.
    The record is read here as plain JSON, not through community_manager.read_provenance: community_manager imports
    this module for its block check, and the layering test counts that loop."""
    from orchestrator.Core.community_pack import PROVENANCE_NAME
    path = folder / PROVENANCE_NAME
    if path.is_symlink() or not path.is_file():
        return "own", ""
    try:
        prov = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "own", ""
    if not isinstance(prov, dict) or prov.get("kind") != "block" or prov.get("id") != folder.name:
        return "own", ""
    if index is not None and index.revocation("block", prov.get("id"), prov.get("version"),
                                              prov.get("pack_sha256")) is not None:
        return "revoked", f"block {folder.name}: revoked in the signed index; not used"
    digest = _tree_sha256(folder)
    if digest not in {prov.get("content_sha256"), prov.get("local_sha256")} - {None, ""}:
        return "own", (f"block {folder.name}: changed since it was installed; it counts as your own block (install "
                       "it again to use it as installed)")
    if prov.get("trust") in ("verified", "community"):
        return "indexed", ""
    return ("signed" if prov.get("signer") else "own"), ""


def community_dir(app_root: Optional[Path]) -> Optional[Path]:
    return Path(app_root).joinpath(*COMMUNITY_SUBDIR) if app_root else None


def block_revoked(app_root: Optional[Path], block_id: str, version: Optional[str], index: Any) -> bool:
    """Whether the verified cached index `index` (CommunityManager.cached_index(revocations=True)) revokes the
    community block `block_id` in `version`: by its id and version, or, while the block is still installed in
    community/blocks/, through its install record (_provenance_trust: by the pack's sha256 too). False without an
    index (nothing is known as revoked)."""
    if index is None:
        return False
    if index.revocation("block", block_id, version, None) is not None:
        return True
    root = community_dir(app_root)
    folder = root / block_id if root is not None and RE_ID.fullmatch(block_id or "") else None
    return folder is not None and folder.is_dir() and _provenance_trust(folder, index)[0] == "revoked"


@dataclass
class BlockCatalog:
    """Every block this framework knows: its own, then those in community/blocks/ (an installed pack or the person's
    own). A community block never replaces one of the framework's (same id: listed with a note, not used)."""
    blocks: Dict[str, Block]
    notes: List[str]

    def get(self, block_id: str) -> Optional[Block]:
        return self.blocks.get(block_id)

    def ready(self) -> List[Block]:
        return [b for b in self.blocks.values() if b.ready]


def load_catalog(app_root: Optional[Path] = None, *, builtin: Path = BUILTIN_DIR, index: Any = None) -> BlockCatalog:
    """The block catalog (BlockCatalog). `index`: the verified cached community index with its revocations, when the
    caller has one (CommunityManager.cached_index(revocations=True)); a block it revokes is listed in the notes and
    not used."""
    blocks: Dict[str, Block] = {}
    notes: List[str] = []
    for root, trust in ((builtin, "framework"), (community_dir(app_root), None)):
        if root is None or not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                if trust is None and check_block_folder(folder):
                    notes.append(f"block {folder.name}: " + "; ".join(check_block_folder(folder))[:300])
                    continue
                found, note = (trust, "") if trust else _provenance_trust(folder, index)
                if note:
                    notes.append(note)
                if found == "revoked":
                    continue
                block = parse_block((folder / BLOCK_FILE).read_text(encoding="utf-8"), folder, found,
                                    _tree_sha256(folder))
            except (BlockError, OSError, UnicodeDecodeError, ValueError) as e:
                notes.append(f"block {folder.name}: {str(e)[:300]}")
                continue
            if block.id != folder.name:
                notes.append(f"block {folder.name}: its id is {block.id}; the folder must carry the id")
                continue
            if block.id in blocks:
                notes.append(f"block {block.id}: the framework has a block of that id; the other one is not used")
                continue
            blocks[block.id] = block
    return BlockCatalog(blocks, notes)


GUIDE_TEMPLATE = """# {title}

Block `{id}` (kind {kind}) for the board bundle of the LLM Cross-Compiler Framework.

{summary}

## Models

Name exactly which models this block needs: the format (for example GGUF for llama-server, a ggml whisper model for
whisper.cpp, an ONNX model in the layout sherpa-onnx reads, a GGUF `--mmproj` projector next to a vision model), the
size and the memory it needs on the board, and the licences you accept.

## Variables

Describe each variable of `block.yml`, its default and when to change it.
"""


def new_block(dest_root: Path, block_id: str, *, kind: str, title: str, summary: str = "",
              template: Optional[str] = None, builtin: Path = BUILTIN_DIR) -> Path:
    """The person's own block (the wizard): a new folder dest_root/<id> with block.yml (from the framework block
    `template` when given, with the new id, title and version 1.0.0, else an empty template block) and the guide
    skeleton. It is not signed: private mode warns before it is used, enterprise mode refuses it until it is
    published and installed from the signed index. BlockError when the id is taken or not valid."""
    if not RE_ID.fullmatch(block_id or ""):
        raise BlockError("the block id is lower-case letters, digits, '.', '_' and '-'")
    if kind not in KINDS:
        raise BlockError(f"kind must be one of {', '.join(KINDS)}")
    if (builtin / block_id).exists():
        raise BlockError(f"the framework has a block {block_id}; choose another id")
    dest = Path(dest_root) / block_id
    if dest.exists():
        raise BlockError(f"a block {block_id} exists already")
    clean_title = " ".join(str(title or block_id).split())[:120]
    clean_summary = " ".join(str(summary or "").split())[:500]
    if template:
        source = builtin / template / BLOCK_FILE
        if not source.is_file():
            raise BlockError(f"no framework block {template} to start from")
        spec = yaml.safe_load(source.read_text(encoding="utf-8"))
        spec.update({"id": block_id, "version": "1.0.0", "title": clean_title, "kind": kind})
        if clean_summary:
            spec["summary"] = clean_summary
    else:
        spec = {"schema": BLOCK_SCHEMA, "id": block_id, "version": "1.0.0", "kind": kind, "title": clean_title,
                "status": "template", "summary": clean_summary, "variables": [], "files": []}
    text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=False)
    parse_block(text, dest)   # never writes a block its own check refuses
    dest.mkdir(parents=True)
    try:
        (dest / BLOCK_FILE).write_text(text, encoding="utf-8")
        (dest / GUIDE_FILE).write_text(GUIDE_TEMPLATE.format(title=clean_title, id=block_id, kind=kind,
                                                             summary=clean_summary or "What this block runs."),
                                       encoding="utf-8")
    except OSError:
        from orchestrator.utils.atomic import remove_tree
        remove_tree(dest)   # the framework's own unfinished folder (utils/atomic.remove_tree)
        raise
    return dest


__all__ = ["BLOCK_FILE", "BLOCK_SCHEMA", "BUILTIN_DIR", "Block", "BlockCatalog", "BlockError", "ENTERPRISE_TRUSTS",
           "FORMATS", "FileNeed", "GUIDE_FILE", "TRUSTS", "Terms", "Variable", "block_revoked", "check_block_folder",
           "community_dir", "load_catalog", "new_block", "parse_block"]
