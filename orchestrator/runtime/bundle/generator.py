#!/usr/bin/env python3
"""
The bundle generator on this computer: the person picks building blocks and their variables (a preset, Ditto may
propose through bundle_plan, the person decides), plan_bundle checks them against the board's probe facts and the
block's needs, and build_bundle writes the bundle into its own store, signed with the framework's key.

plan_bundle (nothing is written):
- every block is known and ready (a template block names what it lacks), its trust fits the framework mode
  (enterprise: only the framework's blocks and blocks installed from the signed index; private: every block, an own
  unsigned one only after the person's yes, `allow_own`);
- every variable is checked against its type and bounds (manifest.check_value); ports are unique;
- every file the block needs is resolved: the board's self-built runtime package (deploy way 2, steps.stored_runtime)
  for a runtime role, else a local file the person picked (hashed here, its format checked by its first bytes, a
  program's ELF architecture against the board's) or a pinned online item (https with its sha256 and size; in offline
  mode only an address the one locality rule counts as local, utils/locality.check_reach);
- the models' licences are named where the block requires it;
- memory, by the board's measured limits (runtime/device/measure.py, `limits`): whether the LLM runs on the board is
  the person's choice first (they pick the llm block or not; a bundle without it notes that the model runs off the
  board); with an llm block the board's measurement decides: its context (ctx_size, unless the person set one) and
  KV-cache type come from it, the server's memory is the measured one (BoardLimits.need_for), the other blocks add
  their base plus model factor, and all of it must fit into the measured usable memory (free at rest less the
  measured reserve). A board without a measurement is refused for an llm block (nothing is guessed) unless the person
  says yes to planning without it (`unmeasured_ok`, never an AI): then, and for bundles without an llm block, the
  declared check of the probe's RAM_TOTAL_MB with a reserve (MEMORY_RESERVE_MB or a tenth, the larger) applies;
- autostart needs a unit, so the root actions create_user, install_unit and set_cgroup (llmcf-install), and a board
  whose access facts name another init system than systemd cannot take it; a board without sudo gets the plain note
  that llmcf-install cannot run there (the unprivileged-only variant still works).

build_bundle writes <state>/bundles/<bundle id>/: manifest.json, steps.tsv, install.sh and their signatures (.sig,
the framework's SSHSIG board signature, board_ops.framework_signer), files/<component>--<name> (hash-checked copies,
the disk rule first), selection.json (what the person chose, with the local paths: never handed to an AI) and
boards.json (where each board's upload stands, for a deploy that continues).
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.runtime.bundle.blocks import ENTERPRISE_TRUSTS, Block, BlockCatalog
from orchestrator.runtime.bundle.installer import (ROOT_HELPER_FILE, SIGNED_FILES, installer_sh, render_steps,
                                                   staged_name)
from orchestrator.runtime.bundle.manifest import (RE_ID, RE_LICENCE, RE_URL, BundleManifest, Component, FileEntry,
                                                  ManifestError, RootAction, StartSpec, dump_manifest, load_manifest)

PRESETS: Dict[str, Tuple[str, ...]] = {
    "llm": ("llm-llama-server",),
    "voice": ("llm-llama-server", "stt-whisper-cpp"),
    "stt": ("stt-whisper-cpp",),
}
MEMORY_RESERVE_MB = 384
ROOT_HELPER_RECORD = "llmcf-install.json"
STORE_DIR = "bundles"
_MAGIC = {"gguf": (b"GGUF",), "mmproj-gguf": (b"GGUF",), "ggml-whisper": (b"lmgg", b"ggjt", b"ggml"),
          "elf": (b"\x7fELF",), "tar.gz": (b"\x1f\x8b",)}
_ELF_ARCH = {0x3E: "x86_64", 0xB7: "arm64", 0x28: "arm", 0xF3: "riscv64", 0x03: "x86"}


class BundleError(ValueError):
    """A bundle that cannot be planned, built or used; the message is the person's."""


@dataclass
class FileChoice:
    """What the person chose for one file of a component: a local file (`path`) or a pinned online item (`url`,
    `sha256`, `size`), and the model's licence."""
    path: Optional[str] = None
    url: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = None
    licence: Optional[str] = None

    def as_json(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Selection:
    """The person's choice: blocks (in order), variables per component, files per component and role, the
    unprivileged-only variant, the preset it started from."""
    blocks: List[str]
    values: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    files: Dict[str, Dict[str, FileChoice]] = field(default_factory=dict)
    unprivileged_only: bool = False
    preset: str = ""
    # the blocks whose licence terms the person read and accepted: block id -> Block.terms_sha256 of the text shown
    accepted_terms: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_preset(cls, name: str, **kw) -> "Selection":
        if name not in PRESETS:
            raise BundleError(f"no preset {name!r} (presets: {', '.join(PRESETS)})")
        return cls(blocks=list(PRESETS[name]), preset=name, **kw)

    def as_json(self) -> Dict[str, Any]:
        return {"blocks": list(self.blocks), "values": self.values, "preset": self.preset,
                "unprivileged_only": self.unprivileged_only, "accepted_terms": dict(self.accepted_terms),
                "files": {c: {r: f.as_json() for r, f in roles.items()} for c, roles in self.files.items()}}


@dataclass(frozen=True)
class RuntimePackage:
    """A board package of the framework's self-build path (runtime/build/projects.py): llama.cpp's runtime tarball of
    deploy way 2, or the board's whisper.cpp build: its path, sha256 and build ID, the project, the architecture it
    was built for and the CPU features of the board's probe its build uses; or why there is none."""
    path: Optional[Path] = None
    sha256: str = ""
    build_id: str = ""
    missing: str = ""
    project: str = "llama.cpp"
    arch: str = ""
    cpu_features: Tuple[str, ...] = ()


@dataclass
class PlannedFile:
    entry: FileEntry
    local: Optional[Path] = None   # a file sent with the bundle (None: online)


@dataclass
class BundlePlan:
    """What plan_bundle found: the manifest (None while there are problems), the files to copy, the problems
    (each refuses the build), the warnings (the person reads them; `own_blocks` need the person's yes), the memory
    estimate and the board's usable memory (MB, None when the probe does not say)."""
    bundle_id: str
    device_id: str
    selection: Selection
    manifest: Optional[BundleManifest] = None
    files: Dict[Tuple[str, str], PlannedFile] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    own_blocks: List[str] = field(default_factory=list)
    # per component, the names of its account variables (type user): never in labels(), whatever the block calls them
    accounts: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    memory_mb: int = 0
    board_memory_mb: Optional[int] = None
    # the checks against the board's probe facts, in the framework's own words: {"check", "result", "detail"} with
    # result passed, failed, warning or unknown (architecture, init system, sudo, offline mode, memory, and one
    # "component <id>" check each: its runtime's architecture and CPU features, its memory)
    checks: List[Dict[str, str]] = field(default_factory=list)
    # the projects of the self-build path whose board package the plan needs and that are not built yet (the person's
    # channels offer to build them: service.build_components)
    missing_runtimes: List[str] = field(default_factory=list)
    # the blocks whose licence terms the person has not accepted yet (block ids; the text: Block.terms_text())
    terms_needed: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.manifest is not None

    @property
    def size(self) -> int:
        return sum(p.entry.size for p in self.files.values() if p.local is not None)

    def labels(self) -> Dict[str, Any]:
        """The plan as labels only (bundle_plan for an AI): block ids, kinds, variable values (numbers, yes/no and
        plain words), file roles, formats, sizes and licences, the root actions by type, memory numbers, problems,
        warnings and notes as the framework's own sentences. Never a local path, an address, an account or a URL."""
        comps = []
        for comp in (self.manifest.components if self.manifest else ()):
            comps.append({"id": comp.id, "block": comp.block, "kind": comp.kind, "trust": comp.block_trust,
                          "variables": {k: v for k, v in comp.variables.items()
                                        if k != "user" and k not in self.accounts.get(comp.id, ())},
                          "files": [{"role": f.role, "format": f.format, "size": f.size, "source": f.source,
                                     "licence": f.licence} for f in comp.files]})
        roots = [a.action for a in (self.manifest.root_actions if self.manifest else ())]
        return {"bundle_id": self.bundle_id, "ok": self.ok, "components": comps, "root_actions": roots,
                "mode": self.manifest.mode if self.manifest else ("unprivileged" if self.selection.unprivileged_only
                                                                   else "full"),
                "memory_mb": self.memory_mb, "board_memory_mb": self.board_memory_mb, "bytes": self.size,
                "problems": [_scrub(p) for p in self.problems], "warnings": [_scrub(w) for w in self.warnings],
                # the notes too: "the root actions need llmcf-install; the installer stops before them" holds even
                # while the files are still to choose (then there is no manifest and root_actions is empty)
                "notes": [_scrub(n) for n in self.notes],
                "checks":[{k: _scrub(str(v)) for k, v in c.items()} for c in self.checks],
                "own_blocks": list(self.own_blocks),
                # built or accepted only by the person (the CLI or the Board bundle window), never through an AI
                "missing_runtimes": list(self.missing_runtimes), "terms_needed": list(self.terms_needed)}

    def lines(self) -> List[str]:
        """The plan in plain words for the person (CLI and window)."""
        out = [f"Bundle {self.bundle_id} for board {self.device_id}"]
        if self.manifest is not None:
            for comp in self.manifest.components:
                values = ", ".join(f"{k}={v}" for k, v in sorted(comp.variables.items()))
                out.append(f"  {comp.id}: block {comp.block} {comp.block_version} ({comp.block_trust}); {values}")
                for f in comp.files:
                    lic = f", licence {f.licence}" if f.licence else ""
                    out.append(f"    {f.role}: {f.name}, {_mb(f.size)} ({self._source(comp.id, f)}{lic})")
            for action in self.manifest.root_actions:
                out.append(f"  root action: {action.action} for {action.component} (llmcf-install)"
                           + (", skipped: unprivileged only" if self.manifest.mode == "unprivileged" else ""))
        out.append(f"  memory: about {self.memory_mb} MB needed" + (
            f" of {self.board_memory_mb} MB usable on the board" if self.board_memory_mb is not None else ""))
        out.append(f"  sends {_mb(self.size)}")
        out += [f"  check {c['check']}: {c['result']} - {c['detail']}" for c in self.checks]
        out += [f"  note: {n}" for n in self.notes]
        out += [f"  warning: {w}" for w in self.warnings]
        out += [f"  problem: {p}" for p in self.problems]
        return out

    def _source(self, comp_id: str, entry: FileEntry) -> str:
        """Where a file comes from, for the person (never in labels()): the local file it is copied from, the board's
        runtime package, or the pinned online address."""
        if entry.source == "online":
            return f"online, pinned: {entry.url}"
        planned = self.files.get((comp_id, entry.name))
        local = f" {planned.local}" if planned is not None and planned.local is not None else ""
        if entry.role == "runtime":
            return "in the bundle, the board's runtime package" + local
        return "in the bundle, from" + (local or " the store")


def _mb(size: int) -> str:
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size} bytes"


def _scrub(text: str) -> str:
    """A sentence for an AI: no path, no URL."""
    text = re.sub(r"https?://\S+", "<address>", text)
    return re.sub(r"(?:[A-Za-z]:)?[\\/][^\s,;)]+", "<path>", text)


def detect_format(path: Path, fmt: str) -> bool:
    """Whether the file at `path` has the format `fmt` by its first bytes (by its name where a format has no magic
    number: onnx, sherpa-onnx, text)."""
    if fmt in ("onnx", "sherpa-onnx"):
        return path.suffix.lower() == ".onnx"
    if fmt == "text":
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return any(head.startswith(m) for m in _MAGIC.get(fmt, ()))


def elf_arch(path: Path) -> Optional[str]:
    """The architecture an ELF program is built for (its e_machine), None when it is no ELF file."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
    except OSError:
        return None
    if len(head) < 20 or not head.startswith(b"\x7fELF"):
        return None
    machine = int.from_bytes(head[18:20], "little" if head[5] == 1 else "big")
    return _ELF_ARCH.get(machine, f"machine-{machine}")


def arch_matches(elf: Optional[str], board: Optional[str]) -> bool:
    if not elf or not board:
        return False
    board = board.lower()
    if elf == "arm":
        return board.startswith("arm") and board not in ("arm64", "aarch64")
    return elf == board or (elf == "arm64" and board == "aarch64")


def component_ids(blocks: List[Block]) -> List[str]:
    """The component ids of `blocks` in order: the kind, a second block of the same kind `<kind>-2` (the plan, the
    window and the labels name components so)."""
    return _component_ids(blocks)


def _component_ids(blocks: List[Block]) -> List[str]:
    ids, seen = [], {}
    for block in blocks:
        n = seen.get(block.kind, 0) + 1
        seen[block.kind] = n
        ids.append(block.kind if n == 1 else f"{block.kind}-{n}")
    return ids


def _args(block: Block, values: Dict[str, Any], files: Dict[str, str]) -> Tuple[str, ...]:
    out: List[str] = []

    def one(item: Any) -> None:
        if isinstance(item, dict):
            if values.get(item["when"]):
                for sub in item["args"]:
                    one(sub)
            return
        m = re.fullmatch(r"\{([a-z][a-z0-9_]{0,31})\}", item)
        f = re.fullmatch(r"@FILE:([a-z]+)@", item)
        if m:
            out.append(str(values[m.group(1)]))
        elif f:
            out.append("@DIR@/" + files[f.group(1)])
        else:
            out.append(item)
    for item in block.args:
        one(item)
    return tuple(out)


def new_bundle_id(device_id: str, preset: str = "", now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    head = re.sub(r"[^a-z0-9-]", "", (preset or "bundle").lower())[:12] or "bundle"
    return f"{head}-{device_id[:8].lower()}-{stamp}"


def plan_bundle(selection: Selection, *, catalog: BlockCatalog, profile: Any, device_id: str,
                runtime: Optional[RuntimePackage] = None, config: Any = None, mode: str = "private",
                offline: Optional[bool] = None, allow_own: bool = False, bundle_id: Optional[str] = None,
                hasher: Optional[Callable[[Path], str]] = None, locality: Optional[Callable[..., Any]] = None,
                now: Optional[datetime] = None,
                programs: Optional[Callable[[str, Optional[str]], Tuple[Path, str]]] = None,
                runtimes: Optional[Dict[str, RuntimePackage]] = None, limits: Any = None,
                unmeasured_ok: bool = False) -> BundlePlan:
    """The plan of `selection` for the board of `profile` (see the module text). `offline` None reads the setting
    from `config` (utils/locality.offline_mode); `locality` stands in for utils/locality.check_reach in tests.
    `runtime` is llama.cpp's board package, `runtimes` every project's (runtime/build/projects.py: whisper.cpp for
    the speech recognition), each a RuntimePackage. `programs(name, arch)` gives (path, sha256) of a program the
    framework builds itself for the board (a block's file "from: framework", service.framework_programs:
    root_helper.ensure_helper); it raises with the plain reason when it cannot. A gateway component takes the llm
    component's port and context size (GATEWAY_FROM_LLM)."""
    from orchestrator.utils.helpers import sha256_file
    from orchestrator.utils import locality as loc
    packages: Dict[str, RuntimePackage] = dict(runtimes or {})
    if runtime is not None:
        packages.setdefault("llama.cpp", runtime)
    hasher = hasher or sha256_file
    if offline is None:
        offline = loc.offline_mode(config)
    check_reach = locality or loc.check_reach
    bid = bundle_id or new_bundle_id(device_id, selection.preset, now)
    plan = BundlePlan(bundle_id=bid, device_id=device_id, selection=selection)
    if not RE_ID.fullmatch(bid):
        plan.problems.append("the bundle id is lower-case letters, digits, '.', '_' and '-'")
        return plan
    if not selection.blocks:
        plan.problems.append("pick at least one building block")
        return plan
    board_arch = getattr(profile, "arch", None)
    access = profile.access() if hasattr(profile, "access") else None
    init = getattr(access, "init_system", None) if access is not None else None
    if init is None and hasattr(profile, "get"):
        init = profile.get("INIT_SYSTEM")
    blocks: List[Block] = []
    for block_id in selection.blocks:
        block = catalog.get(block_id)
        if block is None:
            plan.problems.append(f"no building block {block_id}")
            continue
        if not block.ready:
            plan.problems.append(f"block {block.id} is a documented template only, not buildable yet"
                                 + (f": {block.notes[0]}" if block.notes else ""))
            continue
        if mode == "enterprise" and block.trust not in ENTERPRISE_TRUSTS:
            plan.problems.append(f"block {block.id} is {_trust_words(block.trust)}: enterprise mode takes only the "
                                 "framework's blocks and blocks installed from the signed community index")
            continue
        if block.trust == "own":
            plan.own_blocks.append(block.id)
            plan.warnings.append(f"block {block.id} is your own and not signed; nobody else checked it"
                                 + ("" if allow_own else " (say yes to use it)"))
            if not allow_own:
                plan.problems.append(f"block {block.id} is not signed: it is used only after your yes")
        elif block.trust == "signed":
            plan.notes.append(f"block {block.id} is signed by its author, installed from a file (not from the "
                              "signed index)")
        if block.terms and selection.accepted_terms.get(block.id) != block.terms_sha256:
            # nothing of it is built, sent or fetched before the person read its terms and accepted them
            plan.terms_needed.append(block.id)
            plan.problems.append(f"block {block.id} brings in parts under their own licence terms "
                                 f"({', '.join(t.licence for t in block.terms)}"
                                 + (", not all free licences" if block.opt_in else "")
                                 + "): read them and accept them first")
        blocks.append(block)
    known = set(selection.blocks)
    for comp_id in set(selection.values) | set(selection.files):
        if comp_id not in _component_ids(blocks) and comp_id not in known:
            plan.problems.append(f"values or files for {comp_id}, which is no component of this bundle")
    components: List[Component] = []
    roots: List[RootAction] = []
    ports: Dict[int, str] = {}
    memory = 0
    measured_llm = 0          # MiB the llm servers need by the board's measurement
    has_llm = any(b.kind == "llm" for b in blocks)
    # the memory the board's measurement leaves for the bundle (free at rest less the measured reserve), the same
    # number with and without a language model: every component is checked against it
    measured_usable = int(limits.usable_mib) if limits is not None else None
    pairs = list(zip(_component_ids(blocks), blocks))
    for comp_id, block in pairs:
        given = dict(selection.values.get(comp_id) or {})
        if block.kind == "gateway":
            _gateway_from_llm(plan, comp_id, given, pairs, selection, limits)
        if block.kind == "llm":
            # the board's measurement decides what the person did not set (never a guessed default)
            measured = measured_llm_values(block, given, limits)
            given.update(measured)
            if "ctx_size" in measured:
                plan.notes.append(f"{comp_id}: context {limits.ctx} tokens from the board's measurement")
            if "kv_cache" in measured:
                plan.notes.append(f"{comp_id}: KV cache {limits.kv_cache} from the board's measurement")
        plan.accounts[comp_id] = tuple(v.name for v in block.variables if v.type == "user")
        values: Dict[str, Any] = {}
        for var in block.variables:
            raw = given.pop(var.name, var.default)
            if raw is None:
                if var.required:
                    plan.problems.append(f"{comp_id}: {var.name} is needed")
                continue
            try:
                values[var.name] = var.check(raw)
            except ManifestError as e:
                plan.problems.append(f"{comp_id}: {e}")
        for name in sorted(given):
            plan.problems.append(f"{comp_id}: the block {block.id} has no variable {name}")
        for var in block.variables:
            if var.type == "port" and var.name in values and not (block.kind == "gateway"
                                                                   and var.name in GATEWAY_FROM_LLM):
                other = ports.get(values[var.name])
                if other:
                    plan.problems.append(f"{comp_id} and {other} both want port {values[var.name]}")
                ports[values[var.name]] = comp_id
        entries: List[FileEntry] = []
        names: Dict[str, str] = {}
        model_bytes = 0
        comp_runtime: Optional[RuntimePackage] = None
        picked: Dict[str, Path] = {}
        choices = selection.files.get(comp_id) or {}
        for need in block.files:
            name = need.name or need.role
            names[need.role] = name
            if need.source == "framework":
                try:
                    if programs is None:
                        raise BundleError(f"{need.name} is built by the framework, and no builder was given")
                    path, sha = programs(need.name, board_arch)
                except Exception as e:  # noqa: BLE001 - HelperMissing or BundleError: the person's words
                    plan.problems.append(f"{comp_id}: {str(e)[:400]}")
                    continue
                entry = FileEntry(name=name, sha256=sha, size=Path(path).stat().st_size, mode=need.mode,
                                  role="program", format="elf")
                entries.append(entry)
                plan.files[(comp_id, name)] = PlannedFile(entry, Path(path))
                continue
            if need.source == "runtime":
                pkg = packages.get(need.project)
                if pkg is None or pkg.path is None:
                    if need.project not in plan.missing_runtimes:
                        plan.missing_runtimes.append(need.project)
                    plan.problems.append(f"{comp_id}: " + ((pkg.missing if pkg else "") or (
                        "the board's llama.cpp runtime package is not built yet; build the board's runtime first"
                        if need.project == "llama.cpp" else
                        f"the board's {need.project} package is not built yet; the framework builds it for the "
                        "board when you build the bundle, after your yes")))
                    continue
                comp_runtime = pkg
                entry = FileEntry(name=name, sha256=pkg.sha256, size=Path(pkg.path).stat().st_size,
                                  mode=need.mode, role="runtime", format="tar.gz")
                entries.append(entry)
                plan.files[(comp_id, name)] = PlannedFile(entry, Path(pkg.path))
                continue
            choice = choices.get(need.role)
            if choice is None or not (choice.path or choice.url):
                plan.problems.append(f"{comp_id}: choose the {need.role} file"
                                     + (f" ({', '.join(need.formats)})" if need.formats else ""))
                continue
            licence = choice.licence
            if licence is not None and not RE_LICENCE.fullmatch(licence):
                plan.problems.append(f"{comp_id}: the licence of the {need.role} is an SPDX identifier or a short "
                                     "word")
                licence = None
            if need.licence == "required" and not licence:
                plan.problems.append(f"{comp_id}: name the licence of the {need.role} (for example apache-2.0, mit, "
                                     "llama3)")
            fmt = need.formats[0] if need.formats else None
            if choice.path:
                path = Path(choice.path)
                if not path.is_file():
                    plan.problems.append(f"{comp_id}: the {need.role} file is not there")
                    continue
                size = path.stat().st_size
                if need.formats and not any(detect_format(path, f) for f in need.formats):
                    plan.problems.append(f"{comp_id}: the {need.role} file is no {' or '.join(need.formats)} file")
                if need.role == "program" and "elf" in need.formats:
                    arch = elf_arch(path)
                    if not arch_matches(arch, board_arch):
                        plan.problems.append(f"{comp_id}: the program is built for {arch or 'no known machine'}, "
                                             f"the board runs {board_arch or 'an unknown architecture'}")
                    plan.warnings.append(f"{comp_id}: its program is one you picked: the framework did not build it "
                                         "from a pinned source and cannot tell what it does beyond its architecture")
                picked[need.role] = path
                sha = hasher(path)
                entry_kw: Dict[str, Any] = {"source": "bundle"}
            else:
                url, sha, size = choice.url or "", (choice.sha256 or "").lower(), choice.size
                if not RE_URL.fullmatch(url):
                    plan.problems.append(f"{comp_id}: the {need.role} address is no plain https address")
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or size <= 0:
                    plan.problems.append(f"{comp_id}: an online {need.role} needs its pinned sha256 and size")
                    continue
                verdict = check_reach(url, config) if offline else None
                if offline and not getattr(verdict, "local", False):
                    plan.problems.append(f"{comp_id}: offline mode: "
                                         + (verdict.refusal(f"the {need.role}") if verdict is not None else
                                            "the address is not in the own network"))
                    continue
                if url.startswith("http://") and not getattr(verdict or check_reach(url, None), "local", False):
                    plan.problems.append(f"{comp_id}: plain http only to an address in the own network; use https")
                    continue
                entry_kw = {"source": "online", "url": url}
                path = None
            if need.min_bytes and size < need.min_bytes or need.max_bytes and size > need.max_bytes:
                plan.problems.append(f"{comp_id}: the {need.role} file has {_mb(size)}, outside what the block takes")
            try:
                entry = FileEntry(name=name, sha256=sha, size=size, mode=need.mode, role=need.role, format=fmt,
                                  licence=licence, **entry_kw)
            except Exception as e:  # noqa: BLE001 - pydantic's own reason
                plan.problems.append(f"{comp_id}: the {need.role} file: {str(e).splitlines()[-1][:200]}")
                continue
            entries.append(entry)
            plan.files[(comp_id, name)] = PlannedFile(entry, path)
            if need.role in ("model", "mmproj"):
                model_bytes += size
        if block.kind == "llm" and limits is not None and getattr(limits, "fits", False):
            ctx = int(values.get("ctx_size") or limits.ctx)
            if ctx > limits.ctx:
                plan.problems.append(f"{comp_id}: a context of {ctx} tokens does not fit: the board's measurement "
                                     f"leaves room for {limits.ctx} tokens with this model")
            comp_memory = limits.need_for(model_bytes or limits.model_bytes, ctx,
                                          values.get("kv_cache") or limits.kv_cache)
            measured_llm += comp_memory
        else:
            comp_memory = int(block.memory_base_mb + block.memory_model_factor * model_bytes / 1048576)
        memory += comp_memory
        _component_check(plan, comp_id, block, comp_runtime, board_arch, comp_memory, measured_usable)
        if block.kind == "vision" and comp_runtime is not None:
            _vision_checks(plan, comp_id, comp_runtime, picked)
        program = block.program
        args: Tuple[str, ...] = ()
        startable = True
        if "@RUNTIME@" in program:
            if comp_runtime is None or not comp_runtime.build_id:
                startable = False   # the missing runtime is a problem already; the rest is still planned
            else:
                program = program.replace("@RUNTIME@", comp_runtime.build_id[:16])
        if startable:
            try:
                args = _args(block, values, names)
            except KeyError as e:
                plan.problems.append(f"{comp_id}: the block's start needs {e} that is not chosen")
                continue
            from orchestrator.runtime.server_policy import PolicyError, check_command, check_whisper_command
            try:
                if block.kind in ("llm", "vision"):
                    check_command(list(args))
                elif program.rsplit("/", 1)[-1] == "whisper-server":
                    check_whisper_command(list(args))
            except PolicyError as e:
                plan.problems.append(f"{comp_id}: {e}")
        if block.kind == "gateway" and values.get("bind") == "lan":
            plan.notes.append(f"{comp_id}: the local network only with TLS: the board makes the gateway's own "
                              "certificate on its first start, and the first check over the board's SSH session pins "
                              "its SHA-256 fingerprint; a client on the network sends the gateway's bearer token")
        if values.get("autostart"):
            if init and init != "systemd":
                plan.problems.append(f"{comp_id}: autostart needs systemd; the board runs {init}")
            user = values.get("user") or "llmcf"
            unit = f"llmcf-{comp_id}.service"
            roots += [RootAction(action="create_user", component=comp_id, user=user),
                      RootAction(action="install_unit", component=comp_id, unit=unit, user=user),
                      RootAction(action="set_cgroup", component=comp_id, unit=unit,
                                 memory_max_mb=int(values.get("memory_limit_mb") or 1024))]
        try:
            components.append(Component(id=comp_id, kind=block.kind, block=block.id, block_version=block.version,
                                        block_sha256=block.sha256 or "0" * 64, block_trust=block.trust,
                                        variables=values, files=tuple(entries), keygen=block.keygen,
                                        start=StartSpec(program=program, args=args)
                                        if program and startable else None))
        except Exception as e:  # noqa: BLE001 - pydantic's own reason, never pasted into shell text
            plan.problems.append(f"{comp_id}: {str(e).splitlines()[-1][:300]}")
    plan.memory_mb = memory
    _probe_checks(plan, board_arch, init, access, offline, roots)
    ram = profile.int_value("RAM_TOTAL_MB") if hasattr(profile, "int_value") else None
    if not has_llm:
        plan.notes.append("no language model on the board (your choice): the bundle's parts use a model off the "
                          "board")
    if has_llm and limits is not None and not getattr(limits, "fits", False):
        plan.problems.append(f"the language model does not fit on this board by its measurement: {limits.reason}; "
                             "leave the llm block out to run the model off the board")
        plan.checks.append({"check": "memory", "result": "failed", "detail": f"measured: {limits.reason}"})
    elif limits is not None and has_llm:
        usable = int(limits.usable_mib)
        plan.board_memory_mb = usable
        if memory > usable:
            plan.problems.append(f"the bundle needs about {memory} MB of memory, the board's measurement leaves "
                                 f"{usable} MB for it ({limits.available_mib} MB free at rest less the measured "
                                 f"reserve of {limits.reserve_mib} MB); leave the llm block out to run the model off "
                                 "the board, or choose a smaller model")
        elif memory > usable * 0.9:
            plan.warnings.append(f"the bundle takes about {memory} of the {usable} MB the board's measurement leaves: "
                                 "little room is left")
        plan.checks.append({"check": "memory", "result": "failed" if memory > usable else
                            ("warning" if memory > usable * 0.9 else "passed"),
                            "detail": f"measured: about {memory} MB needed ({measured_llm} MB for the language "
                                      f"model as measured on the board), {usable} MB usable"})
    elif limits is not None:
        # no language model on the board: the components fit by the board's measured memory all the same
        usable = int(limits.usable_mib)
        plan.board_memory_mb = usable
        if memory > usable:
            plan.problems.append(f"the bundle needs about {memory} MB of memory, the board's measurement leaves "
                                 f"{usable} MB for it ({limits.available_mib} MB free at rest less the measured "
                                 f"reserve of {limits.reserve_mib} MB)")
        elif memory > usable * 0.9:
            plan.warnings.append(f"the bundle takes about {memory} of the {usable} MB the board's measurement leaves: "
                                 "little room is left")
        plan.checks.append({"check": "memory", "result": "failed" if memory > usable else
                            ("warning" if memory > usable * 0.9 else "passed"),
                            "detail": f"measured: about {memory} MB needed, {usable} MB usable"})
    elif has_llm and not unmeasured_ok:
        from orchestrator.runtime.device.measure import NOT_MEASURED
        plan.problems.append(f"{NOT_MEASURED}; or plan without the measurement after your own yes (the probe's "
                             "declared memory is used then)")
        plan.checks.append({"check": "memory", "result": "unknown", "detail": "the board was not measured"})
    elif ram:
        if has_llm:
            plan.warnings.append("planned without the board's measurement (your yes): the memory check uses the "
                                 "probe's declared RAM")
        usable = ram - max(MEMORY_RESERVE_MB, ram // 10)
        plan.board_memory_mb = usable
        if memory > usable:
            plan.problems.append(f"the bundle needs about {memory} MB of memory, the board has {usable} MB for it "
                                 f"({ram} MB with a reserve for the system)")
        elif memory > usable * 0.8:
            plan.warnings.append(f"the bundle takes about {memory} of {usable} MB: little room is left")
        plan.checks.append({"check": "memory", "result": "failed" if memory > usable else
                            ("warning" if memory > usable * 0.8 else "passed"),
                            "detail": f"about {memory} MB needed, {usable} MB usable ({ram} MB on the board)"})
    else:
        plan.warnings.append("the board's probe does not say its memory; probe it again to check the bundle fits")
        plan.checks.append({"check": "memory", "result": "unknown",
                            "detail": f"about {memory} MB needed; the probe does not say the board's memory"})
    if roots and not selection.unprivileged_only:
        plan.notes.append("the root actions run only through llmcf-install, the framework's root helper the bundle "
                          "carries, after the person's own yes for them in the CLI or the Board bundle window (never "
                          "through an AI); the installer stops before them (choose the unprivileged-only variant to "
                          "skip them)")
        if access is not None and access.sudo == "absent":
            plan.warnings.append("the board has no sudo: llmcf-install cannot run there; the unprivileged-only "
                                 "variant still works")
    if access is None:
        plan.notes.append("the board's probe is older than its access facts (SSH server, sudo, init system); "
                          "probe it again for a full check")
    if plan.problems or not components:
        return plan
    try:
        plan.manifest = BundleManifest.model_validate({
            "schema": "llmcf-bundle/1", "bundle_id": bid,
            "created": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "framework_version": _version(),
            "board": {"device_id": device_id, "arch": board_arch or "unknown",
                      **({"init_system": init} if init else {})},
            "mode": "unprivileged" if selection.unprivileged_only else "full",
            "components": [c.model_dump(by_alias=True) for c in components],
            "root_actions": [r.model_dump() for r in roots]})
    except Exception as e:  # noqa: BLE001
        plan.problems.append(f"the manifest: {str(e).splitlines()[-1][:300]}")
    return plan


# The gateway's variables the bundle sets from its llm component (gateway variable -> llm variable).
GATEWAY_FROM_LLM = {"upstream_port": "port", "ctx_size": "ctx_size"}


def measured_llm_values(block: Block, given: Dict[str, Any], limits: Any) -> Dict[str, Any]:
    """The values the board's measurement sets for an llm block where the person set none: its measured context and
    KV-cache type (never a guessed default). {} for another block, without a measurement, or when the model does not
    fit. The one rule for the llm component and the gateway that takes its context (GATEWAY_FROM_LLM)."""
    if block.kind != "llm" or limits is None or not getattr(limits, "fits", False):
        return {}
    names = {v.name for v in block.variables}
    out: Dict[str, Any] = {}
    if "ctx_size" in names and "ctx_size" not in given:
        out["ctx_size"] = limits.ctx
    if "kv_cache" in names and "kv_cache" not in given and limits.kv_cache != "f16":
        out["kv_cache"] = limits.kv_cache
    return out


def _gateway_from_llm(plan: BundlePlan, comp_id: str, given: Dict[str, Any], pairs: List[Tuple[str, Block]],
                      selection: Selection, limits: Any = None) -> None:
    """A gateway talks to the bundle's llm component: its upstream_port and ctx_size are that component's port and
    context size (the board's start parameters, with the board's measurement where the person set none:
    measured_llm_values), set here; a different value the person gave is refused."""
    llm = next(((cid, b) for cid, b in pairs if b.kind == "llm"), None)
    if llm is None:
        plan.problems.append(f"{comp_id}: the gateway needs the bundle's llm component (block llm-llama-server)")
        return
    llm_id, llm_block = llm
    llm_values = dict(selection.values.get(llm_id) or {})
    llm_values.update(measured_llm_values(llm_block, llm_values, limits))
    for mine, theirs in GATEWAY_FROM_LLM.items():
        var = llm_block.variable(theirs)
        if var is None:
            continue
        try:
            value = var.check(llm_values.get(theirs, var.default))
        except ManifestError:
            continue   # the llm component reports its own value
        if mine in given and str(given[mine]) != str(value):
            plan.problems.append(f"{comp_id}: {mine} is set by the bundle from {llm_id} ({value})")
        given[mine] = value


def _probe_checks(plan: BundlePlan, board_arch: Optional[str], init: Optional[str], access: Any, offline: bool,
                  roots: List[RootAction]) -> None:
    """plan.checks: what the plan checked against the board's probe facts, one entry each (the memory check follows
    with its numbers). Each result comes from the problems plan_bundle found, never from a second rule."""
    def failed(*words: str) -> bool:
        return any(all(w in p for w in words) for p in plan.problems)
    plan.checks.append({"check": "architecture", "result": "unknown" if not board_arch else
                        ("failed" if failed("the program is built for") else "passed"),
                        "detail": f"the board runs {board_arch or 'an unknown architecture'}; every program of the "
                                  "bundle must be built for it"})
    unit = bool(roots)
    plan.checks.append({"check": "init system",
                        "result": "failed" if failed("autostart needs systemd") else
                                  ("unknown" if unit and not init else "passed"),
                        "detail": f"the board runs {init or 'an init system the probe does not name'}"
                                  + ("; autostart needs systemd" if unit else "; no component starts by itself")})
    sudo = getattr(access, "sudo", None) if access is not None else None
    only = plan.selection.unprivileged_only
    plan.checks.append({"check": "sudo", "result": "passed" if not unit or only else
                        ("warning" if sudo == "absent" else "unknown" if not sudo else "passed"),
                        "detail": "no root action is needed" if not unit else
                                  ("the unprivileged-only variant skips the root actions" if only else
                                   f"sudo on the board: {sudo or 'not in the probe'}; the root actions need "
                                   "llmcf-install")})
    plan.checks.append({"check": "offline mode", "result": "failed" if failed("offline mode") else "passed",
                        "detail": "on: online items only from the own network" if offline else
                                  "off: the board fetches pinned online items itself"})


def _component_check(plan: BundlePlan, comp_id: str, block: Block, pkg: Optional[RuntimePackage],
                     board_arch: Optional[str], memory_mb: int, measured_usable: Optional[int] = None) -> None:
    """plan.checks "component <id>": the probe facts this one component depends on. A framework-built runtime
    (llama.cpp, whisper.cpp) was planned from the board's own probe: its architecture must be the board's, and the
    CPU features its build uses are named; its own memory need is named (the bundle's total is the "memory" check).
    A wrong architecture refuses the plan (one rule: arch_matches, as for a program the person picked)."""
    parts = [f"block {block.id}", f"about {memory_mb} MB"]
    result = "passed"
    if measured_usable is not None:
        # the component fits by the board's measurement (its memory free at rest less the measured reserve)
        parts.append(f"of the {measured_usable} MB the board's measurement leaves")
        if memory_mb > measured_usable:
            plan.problems.append(f"{comp_id}: it needs about {memory_mb} MB, the board's measurement leaves "
                                 f"{measured_usable} MB")
            result = "failed"
    if pkg is not None and pkg.path is not None:
        built_for = pkg.arch or "an architecture its record does not name"
        if pkg.arch and board_arch and not arch_matches(pkg.arch, board_arch):
            plan.problems.append(f"{comp_id}: the board's {pkg.project} package is built for {pkg.arch}, the board "
                                 f"runs {board_arch}; build it again from the board's probe")
            result = "failed"
        elif (not pkg.arch or not board_arch) and result != "failed":
            result = "unknown"
        parts.insert(1, f"{pkg.project} built for {built_for} with the CPU features "
                        + (", ".join(pkg.cpu_features) if pkg.cpu_features else "of its base level"))
    plan.checks.append({"check": f"component {comp_id}", "result": result, "detail": "; ".join(parts)})


# What a GGUF header holds that the vision pair check reads (bounded: a header is read as llama.cpp's gguf reader
# reads it, never more than these many keys or bytes per string).
_GGUF_MAX_KV = 4096
_GGUF_MAX_STRING = 1 << 20


def gguf_header(path: Path) -> Dict[str, Any]:
    """The key-value pairs of a GGUF file's header: scalars and strings as values, arrays by their length. Untrusted
    input: every count and length is bounded; BundleError when the header is damaged or no GGUF."""
    import struct
    scalars = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    size = path.stat().st_size

    with open(path, "rb") as fh:
        def take(n: int) -> bytes:
            if n < 0 or fh.tell() + n > size:
                raise BundleError(f"{path.name}: its GGUF header ends early")
            return fh.read(n)

        def unpack(fmt: str):
            return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

        def string() -> str:
            n = unpack("<Q")
            if n > _GGUF_MAX_STRING:
                raise BundleError(f"{path.name}: a GGUF string is longer than the header allows")
            return take(n).decode("utf-8", errors="replace")

        def value(kind: int, depth: int = 0) -> Any:
            if kind in scalars:
                return unpack(scalars[kind])
            if kind == 8:
                return string()
            if kind == 9 and depth == 0:
                item, count = unpack("<I"), unpack("<Q")
                if count > size:
                    raise BundleError(f"{path.name}: a GGUF array is larger than the file")
                if item in scalars:
                    take(count * struct.calcsize(scalars[item]))
                else:
                    for _ in range(count):
                        value(item, 1)
                return count
            raise BundleError(f"{path.name}: a GGUF value of unknown type {kind}")

        if take(4) != b"GGUF":
            raise BundleError(f"{path.name}: no GGUF file")
        if unpack("<I") not in (2, 3):
            raise BundleError(f"{path.name}: a GGUF version llama.cpp does not read")
        unpack("<Q")
        n_kv = unpack("<Q")
        if n_kv > _GGUF_MAX_KV:
            raise BundleError(f"{path.name}: more GGUF keys than a header has")
        meta: Dict[str, Any] = {}
        for _ in range(n_kv):
            key = string()
            meta[key] = value(unpack("<I"))
        return meta


def start_script_takes(package: Path, option: str) -> Optional[bool]:
    """Whether the llama.cpp start script (llama-server.sh) inside the board's runtime package passes `option` on;
    None when the package holds no start script or cannot be read. A package made before the start script took an
    option is packaged again from the stored build (steps.ensure_package) when the pinned source is cached."""
    import tarfile
    try:
        with tarfile.open(package, mode="r|gz") as tar:
            for member in tar:
                if member.isfile() and member.name.endswith("/llama-server.sh") and member.size < 1 << 20:
                    data = tar.extractfile(member)
                    text = data.read().decode("utf-8", errors="replace") if data is not None else ""
                    # one case line of the script's option check: "      --mmproj|-mm) kind=file ;;"
                    return re.search(rf"^\s+(?:[A-Za-z-]+\|)*{re.escape(option)}(?:\|[A-Za-z-]+)*\) kind=",
                                     text, re.M) is not None
    except (OSError, tarfile.TarError, EOFError):
        return None
    return None


def _vision_checks(plan: BundlePlan, comp_id: str, pkg: RuntimePackage, picked: Dict[str, Path]) -> None:
    """The vision component's own checks: the board's runtime start script passes --mmproj on, and the model and its
    multimodal projector are a pair (the projector is a CLIP GGUF with a vision encoder, the model is not; the same
    base when both name it; the projector's output width is the model's embedding width, which llama-server checks
    again when it loads them and refuses otherwise)."""
    takes = start_script_takes(Path(pkg.path), "--mmproj") if pkg.path is not None else None
    if takes is False:
        plan.problems.append(f"{comp_id}: the board's llama.cpp runtime package was packaged before its start script "
                             "took --mmproj; package it again (the framework does so when the pinned source is "
                             "cached: build the board's runtime)")
    model, mmproj = picked.get("model"), picked.get("mmproj")
    if model is None or mmproj is None:
        plan.notes.append(f"{comp_id}: the model or its projector is not a local file here; the pair is checked by "
                          "llama-server on the board when it loads them")
        return
    before = len(plan.problems)
    headers: Dict[str, Dict[str, Any]] = {}
    for role, path in (("model", model), ("mmproj", mmproj)):
        # the file's role, never the person's file name or its folder (the plan's labels go to an AI)
        try:
            headers[role] = gguf_header(path)
        except BundleError as e:
            reason = str(e)
            reason = reason[len(path.name) + 2:] if reason.startswith(path.name + ": ") else reason
            plan.problems.append(f"{comp_id}: the {role} file: {reason}")
            return
        except OSError as e:
            plan.problems.append(f"{comp_id}: the {role} file cannot be read ({e.strerror or e.__class__.__name__})")
            return
    m, p = headers["model"], headers["mmproj"]
    arch = m.get("general.architecture")
    if arch == "clip" or not isinstance(arch, str):
        plan.problems.append(f"{comp_id}: the model file is no language model (general.architecture "
                             f"{arch or 'missing'}); the projector goes into the mmproj file")
        return
    if p.get("general.architecture") != "clip" or p.get("clip.has_vision_encoder") is not True:
        plan.problems.append(f"{comp_id}: the mmproj file is no multimodal projector with a vision encoder "
                             "(general.architecture clip, clip.has_vision_encoder)")
        return
    base_m, base_p = m.get("general.basename"), p.get("general.basename")
    if isinstance(base_m, str) and isinstance(base_p, str) and base_m.strip().lower() != base_p.strip().lower():
        plan.problems.append(f"{comp_id}: the model ({base_m}) and the projector ({base_p}) are of different bases")
    width, proj = m.get(f"{arch}.embedding_length"), p.get("clip.vision.projection_dim")
    if isinstance(width, int) and isinstance(proj, int) and width != proj:
        plan.warnings.append(f"{comp_id}: the projector's width ({proj}) is not the model's embedding width "
                             f"({width}); llama-server refuses a pair whose widths do not fit")
    if mmproj.stat().st_size >= model.stat().st_size * 4:
        plan.warnings.append(f"{comp_id}: the projector is much larger than the model; check that the two files are "
                             "not swapped")
    plan.checks.append({"check": f"vision pair {comp_id}", "result": "failed" if len(plan.problems) > before else
                        "passed", "detail": f"a {arch} model with its CLIP vision projector"
                                  + (f" of the same base ({base_m})" if isinstance(base_m, str) and base_m == base_p
                                     else "")})


def _trust_words(trust: str) -> str:
    return {"signed": "signed by its author but not from the signed index", "own": "your own and not signed"}.get(
        trust, trust)


def _version() -> str:
    from orchestrator import __version__
    return __version__


@dataclass
class BuiltBundle:
    """A bundle in the store: its folder, manifest and the sha256 of its manifest bytes."""
    path: Path
    manifest: BundleManifest
    manifest_sha256: str

    @property
    def bundle_id(self) -> str:
        return self.manifest.bundle_id

    def file(self, name: str) -> Path:
        return self.path / name

    @property
    def needs_root(self) -> bool:
        """The bundle has root actions to run (the full variant): it carries llmcf-install for them."""
        return needs_root(self.manifest)

    @property
    def root_helper(self) -> Optional[Dict[str, str]]:
        """llmcf-install's record in the bundle ({version, target, sha256}), None when it carries none."""
        try:
            data = json.loads((self.path / ROOT_HELPER_RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return {k: str(data.get(k, "")) for k in ("version", "target", "sha256")} if isinstance(data, dict) else None

    def payload(self) -> List[Tuple[str, Path, FileEntry]]:
        """(staged name on the board, local copy, entry) of every file the bundle sends."""
        out = []
        for comp in self.manifest.components:
            for f in comp.files:
                if f.source == "bundle":
                    name = staged_name(comp.id, f.name)
                    out.append((name, self.path / "files" / name, f))
        return out


class BundleStore:
    """The bundles of this framework: <state>/bundles/<bundle id>/ (see the module text)."""

    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / STORE_DIR

    def folder(self, bundle_id: str) -> Path:
        if not RE_ID.fullmatch(bundle_id or ""):
            raise BundleError("not a bundle id")
        return self.root / bundle_id

    def get(self, bundle_id: str) -> BuiltBundle:
        """The stored bundle; BundleError when it is not there or its manifest does not read."""
        folder = self.folder(bundle_id)
        path = folder / "manifest.json"
        if not path.is_file():
            raise BundleError(f"no bundle {bundle_id} in this framework's store")
        from orchestrator.utils.helpers import sha256_bytes
        data = path.read_bytes()
        try:
            manifest = load_manifest(data)
        except ManifestError as e:
            raise BundleError(f"the manifest of bundle {bundle_id} does not read: {e}") from None
        return BuiltBundle(folder, manifest, sha256_bytes(data))

    def list(self) -> List[BuiltBundle]:
        out = []
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                if folder.is_dir() and RE_ID.fullmatch(folder.name):
                    try:
                        out.append(self.get(folder.name))
                    except BundleError:
                        continue
        return out

    def check(self, built: BuiltBundle) -> List[str]:
        """What is wrong with a stored bundle before it is sent ([]: nothing): every payload file against the
        manifest's sha256 and size, the installer is the framework's own, the steps are the manifest's."""
        from orchestrator.utils.helpers import sha256_file
        problems = []
        for name, path, entry in built.payload():
            if not path.is_file() or path.stat().st_size != entry.size or sha256_file(path) != entry.sha256:
                problems.append(f"{name}: missing or changed since the bundle was built")
        if (built.path / "install.sh").read_text(encoding="utf-8") != installer_sh():
            problems.append("install.sh is not this framework's installer (build the bundle again)")
        if (built.path / "steps.tsv").read_text(encoding="utf-8") != render_steps(built.manifest,
                                                                                 built.manifest_sha256):
            problems.append("steps.tsv is not the one of its manifest")
        for name in SIGNED_FILES:
            if not (built.path / f"{name}.sig").is_file():
                problems.append(f"{name} carries no signature of the framework's signing key")
        if built.needs_root:
            record = built.root_helper
            helper = built.path / ROOT_HELPER_FILE
            if record is None or not helper.is_file():
                problems.append("the bundle's root actions need llmcf-install, which it does not carry (build it "
                                "again)")
            elif sha256_file(helper) != record["sha256"]:
                problems.append("llmcf-install changed since the bundle was built")
            elif not (built.path / f"{ROOT_HELPER_FILE}.sig").is_file():
                problems.append("llmcf-install carries no signature of the framework's signing key")
        return problems

    def boards(self, bundle_id: str) -> Dict[str, Any]:
        path = self.folder(bundle_id) / "boards.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def deletion_plan(self, bundle_ids: Optional[List[str]] = None):
        """What `llmcf bundle delete` and the Board bundle window list for the person's multiple choice: the stored
        bundles (all, or exactly `bundle_ids`), each folder with its full path, size and what it is (a bundle a board
        holds says so: deleting it here leaves the board as it is). None when there is nothing to list. Reads only:
        the framework deletes no bundle by itself."""
        from orchestrator.Core.deletion import plan_of
        from orchestrator.Core.trash import tree_size
        from orchestrator.tools.spec import DeletionItem
        wanted = None if bundle_ids is None else set(bundle_ids)
        items = []
        for built in self.list():
            if wanted is not None and built.bundle_id not in wanted:
                continue
            folder = built.path
            if folder.is_symlink() or not folder.is_dir():
                continue
            boards = sorted(d for d, e in self.boards(built.bundle_id).items()
                            if str((e or {}).get("last", "")).startswith("apply:"))
            what = (f"board bundle of {len(built.manifest.components)} components for board "
                    f"{built.manifest.board.device_id}"
                    + (f"; applied on board {', '.join(boards)}: deleting it here does not uninstall it there, and "
                       "the framework can no longer roll it back or uninstall it from here" if boards else ""))
            items.append(DeletionItem(kind="bundle", speaking_name=built.bundle_id, full_path=str(folder),
                                      file_name=folder.name, size_bytes=tree_size(folder), what=what))
        return plan_of(items, ("trash", "permanent"))

    def delete(self, accepted: Any, state_dir: Path, trash: Any = None, audit: Any = None):
        """Deletes exactly the bundles of deletion_plan() the person ticked and confirmed (Core/deletion.py): into
        the trash of the per-user folder `state_dir` or for good. The list is looked up again first (PlanChanged
        when a chosen bundle changed); the deletion is audited before anything goes (RuntimeError, nothing deleted,
        without the audit log); only the person (PermissionError otherwise). The honest deletion.DeletionResult:
        what stayed is kept with its reason (no path, safe for an AI) and its full paths for the person's own text."""
        from orchestrator.Core.deletion import DeletionResult, note_kept, require_person, require_same, same_items
        from orchestrator.utils.atomic import remove_tree
        accepted = require_person(accepted)
        if accepted.actor not in ("gui", "cli"):
            raise PermissionError("Only the person deletes stored bundles, in the GUI or at a terminal.")
        require_same(accepted, same_items(self.deletion_plan(), accepted.plan), "the list of bundles")
        if accepted.mode == "trash" and trash is None:
            raise RuntimeError("The trash is not available; nothing was deleted.")
        if audit is None or not callable(getattr(audit, "record", None)):
            raise RuntimeError("The audit log is not available, so nothing was deleted.")
        names = [i.speaking_name for i in accepted.plan.items]
        try:
            audit.record(accepted.actor, "bundle_delete", "framework", "person", "started", approved=True,
                         arguments={"bundles": names, "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                         detail=f"{len(names)} stored board bundles "
                                + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                         extra=accepted.audit_extra())
        except Exception:  # noqa: BLE001 - the reason could name a host path
            raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
        result = DeletionResult()
        for item in accepted.plan.items:
            path = Path(item.full_path)
            try:
                if accepted.mode == "trash":
                    entry = trash.move("bundle", path, Path(state_dir), "state", item.speaking_name, accepted,
                                       accepted.actor)
                    result.trash_ids.append(entry.id)
                else:
                    if path.is_symlink() or not path.is_dir():
                        raise OSError("not a bundle folder")
                    if note_kept(result, item.speaking_name, path, remove_tree(path)):
                        continue   # something stayed: kept, with its paths for the person
                    result.freed_bytes += item.size_bytes
            except Exception as e:  # noqa: BLE001 - a TrashError or an OSError: that bundle stays and is reported
                result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
                continue
            result.done.append(item.speaking_name)
        return result

    def record_board(self, bundle_id: str, device_id: str, *, stamp: str = "at", **values: Any) -> None:
        """Merges `values` into the board's record of the bundle (boards.json) and stamps the time in `stamp`: "at"
        is the time of the last installer run (what `bundle list` shows); a check that is no installer run (the
        gateway's) keeps its own time ("gateway_at") and never moves "at"."""
        from orchestrator.utils.atomic import write_private
        data = self.boards(bundle_id)
        entry = dict(data.get(device_id) or {})
        entry.update({k: v for k, v in values.items()})
        entry[stamp] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data[device_id] = entry
        write_private(self.folder(bundle_id) / "boards.json", json.dumps(data, indent=2, sort_keys=True))


def needs_root(manifest: Optional[BundleManifest]) -> bool:
    """Whether a manifest has root actions to run (not the unprivileged-only variant)."""
    return manifest is not None and manifest.mode == "full" and bool(manifest.root_actions)


def build_bundle(plan: BundlePlan, store: BundleStore, signer: Any, *, ledger: Any = None,
                 helper: Any = None) -> BuiltBundle:
    """Writes the planned bundle into the store, signed (see the module text). A bundle with root actions carries
    llmcf-install for the board's architecture (`helper`, a root_helper.HelperBuild: service.build_for makes it),
    signed like the installer, with its record llmcf-install.json. BundleError when the plan has problems, own
    blocks the person did not allow, no signer, no room, no helper for root actions, or a file that changed since
    the plan."""
    from orchestrator.Core.disk_plan import refusal, room
    from orchestrator.utils.helpers import copy_hashed, sha256_bytes
    if not plan.ok:
        raise BundleError("the bundle cannot be built: " + "; ".join(plan.problems[:5]))
    if signer is None:
        raise BundleError("the framework's signing key is needed to sign the bundle (its secrets store did not open)")
    manifest = plan.manifest
    if needs_root(manifest) and helper is None:
        raise BundleError("the bundle's root actions need llmcf-install for the board's architecture, and none was "
                          "made; build it again (or choose the unprivileged-only variant)")
    dest = store.folder(manifest.bundle_id)
    if dest.exists():
        raise BundleError(f"bundle {manifest.bundle_id} exists already")
    store.root.mkdir(parents=True, exist_ok=True)
    check = room(store.root, plan.size, label="bundle store", ledger=ledger)
    refused = refusal([check], "the bundle")
    if refused is not None:
        raise BundleError(str(refused))
    tmp = Path(tempfile.mkdtemp(prefix=f".{manifest.bundle_id}.", dir=store.root))
    try:
        files = tmp / "files"
        files.mkdir()
        for (comp_id, name), planned in plan.files.items():
            if planned.local is None:
                continue
            got = copy_hashed(planned.local, files / staged_name(comp_id, name))
            if got != planned.entry.sha256:
                raise BundleError(f"{comp_id}: the {planned.entry.role} file changed since the plan; plan again")
        data = dump_manifest(manifest)
        msha = sha256_bytes(data)
        texts = {"manifest.json": data, "steps.tsv": render_steps(manifest, msha).encode("ascii"),
                 "install.sh": installer_sh().encode("ascii")}
        for name, body in texts.items():
            (tmp / name).write_bytes(body)
            (tmp / f"{name}.sig").write_text(signer.sign_bytes(body), encoding="ascii")
        if needs_root(manifest):
            from orchestrator.runtime.bundle.root_helper import target_of
            if helper.target != target_of(manifest.board.arch):
                raise BundleError(f"llmcf-install was built for {helper.target}, the board runs "
                                  f"{manifest.board.arch}")
            got = copy_hashed(Path(helper.path), tmp / ROOT_HELPER_FILE)
            if got != helper.sha256:
                raise BundleError("llmcf-install changed since it was built; build the bundle again")
            (tmp / f"{ROOT_HELPER_FILE}.sig").write_text(signer.sign_file(tmp / ROOT_HELPER_FILE), encoding="ascii")
            (tmp / ROOT_HELPER_RECORD).write_text(json.dumps({"version": helper.version, "target": helper.target,
                                                              "sha256": got}, indent=2, sort_keys=True) + "\n",
                                                  encoding="utf-8")
        (tmp / "selection.json").write_text(json.dumps(plan.selection.as_json(), indent=2, sort_keys=True),
                                            encoding="utf-8")
        try:
            os.chmod(tmp, 0o700)
        except OSError:
            pass
        os.replace(tmp, dest)
    except BaseException:
        from orchestrator.utils.atomic import remove_tree
        remove_tree(tmp)   # the framework's own unfinished folder (utils/atomic.remove_tree)
        raise
    return BuiltBundle(dest, manifest, msha)


__all__ = ["BuiltBundle", "BundleError", "BundlePlan", "BundleStore", "FileChoice", "MEMORY_RESERVE_MB", "PRESETS",
           "PlannedFile", "ROOT_HELPER_RECORD", "RuntimePackage", "SIGNED_FILES", "Selection", "arch_matches",
           "build_bundle", "component_ids", "detect_format", "elf_arch", "needs_root", "new_bundle_id", "plan_bundle"]
