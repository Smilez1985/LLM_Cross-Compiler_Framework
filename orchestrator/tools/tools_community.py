#!/usr/bin/env python3
"""
The community zoo for AI callers (MCP design 2.8, work package T9; DECISIONS round 1 item 3, round 2 item 4, round 4
items 2, 3 and 5).

- `list_packs` (read): the signed index and the installed packs, details of one pack and the verification of the
  installed ones. It reads the cached index; `refresh` fetches the signed index (network, once an hour per client).
  Titles, authors, descriptions and recipes are external text.
- `add_pack_from_index` (external, ask): a knowledge, target or recipe pack from the signed index, trust `verified` or
  `community`, never a revoked one and never a pack from a file. Two-phase: `prepare` downloads the pinned pack into
  quarantine and validates it; the question shows the prepared summary, for a target pack that it adds build scripts
  and a Dockerfile whose image build runs with network, and every base image plainly (registry, image, digest or
  "not pinned", Docker Official Image or another registry). The handler installs exactly the quarantined copy with
  the sha256 shown, as a job. A quarantined copy is reused while the same pack and sha256 are asked about, and removed
  when the person says no, nobody answers, or it is not used within QUARANTINE_TTL. "Yes, and do not ask again"
  (a standing approval, accepted only in the framework's own window) covers one pack kind, one trust level and the
  index signing key: knowledge and recipe packs of both trust levels, target packs only `verified`
  (grants.PACK_RULE). A pack installed under one is marked `installed_via: grant:<id>`.
- `export_pack` (local write, always asked, never a standing approval): a target, recipe or knowledge pack to a local
  file in the contributions folder, signed with the person's key; the question names the key and the author. It
  refuses when no signing key exists yet (creating one is the person's step: `llm-cli zoo keys`). It returns the file
  as a label and how many lines the privacy scrub changed per file, never the removed text. Nothing is uploaded and
  no pull request is opened: there is no tool for that (DECISIONS round 1 item 3).
"""

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.grants import PACK_RULE
from orchestrator.tools.output import ToolOutput, count, fact, untrusted_text
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule, Rate, ToolLimits

PACK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
LICENSE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"
ARTIFACT_PATTERN = r"^a_[0-9a-f]{16}$"
PAGE_SIZE = 50
REFRESH_EVERY = 3600.0          # index refreshes per client (the limits table: 1 per hour)
QUARANTINE_TTL = 20 * 60.0      # a quarantined copy waits this long for the person (a pending approval: 15 minutes)
INDEX_TEXT = "text from the pack index, not checked"
PACK_TEXT = "from the pack's files, not checked"
Kind = Literal["target", "recipe", "knowledge"]


# --- base images of a target pack ---------------------------------------------------------------------------------

def _logical_lines(text: str) -> List[str]:
    lines, current = [], ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not current and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        lines.append(current + stripped)
        current = ""
    if current:
        lines.append(current)
    return lines


def dockerfile_bases(text: str) -> List[str]:
    """The base images a Dockerfile's FROM lines name, in order: build stages named with AS are left out, `scratch`
    too; a build argument with a default declared before the first FROM is filled in, any other stays as written."""
    args: Dict[str, str] = {}
    stages, images = set(), []
    seen_from = False
    for line in _logical_lines(text):
        word, _, rest = line.partition(" ")
        word = word.upper()
        if word == "ARG" and not seen_from:
            name, eq, value = rest.strip().partition("=")
            if eq:
                args[name.strip()] = value.strip().strip('"').strip("'")
        if word != "FROM":
            continue
        seen_from = True
        tokens = [t for t in rest.split() if not t.startswith("--")]
        if not tokens:
            continue
        image = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: args.get(m.group(1), m.group(0)), tokens[0])
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            stages.add(tokens[2].lower())
        if image.lower() in stages or image.lower() == "scratch":
            continue
        if image not in images:
            images.append(image)
    return images


def pack_base_images(folder: Path) -> List[Tuple[str, str]]:
    """(file, image) for every base image of every Dockerfile in a pack folder (Dockerfile, *.dockerfile,
    Dockerfile.*, Containerfile), never following a link."""
    found = []
    for path in sorted(Path(folder).rglob("*")):
        name = path.name.lower()
        if path.is_symlink() or not path.is_file():
            continue
        if not (name in ("dockerfile", "containerfile") or name.endswith(".dockerfile")
                or name.startswith("dockerfile.")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(folder).as_posix()
        found += [(rel, image) for image in dockerfile_bases(text)]
    return found


def base_image_line(image: str) -> str:
    """One base image, plainly (DECISIONS round 3): registry, image, tag, digest, and whether it is a Docker Official
    Image or a prebuilt image from another registry."""
    from orchestrator.llm.task_prompts import split_image
    if "$" in image:
        return (f"{image}: names a build argument; the image is decided when the target is built. The image build runs "
                "with network access on this computer, outside the build sandbox.")
    info = split_image(image)
    parts = [f"{image}: registry {info['registry']}, image {info['repository']}"
             + (f", tag {info['tag']}" if info["tag"] else "")]
    parts.append(f"digest {info['digest']}" if info["digest"] else
                 "not pinned by digest: its publisher can later point the tag at another image")
    parts.append("Docker Official Image" if info["official"] else
                 f"other registry: {info['registry']}; prebuilt image made by its publisher, not checked by the framework")
    parts.append("the image build runs with network access on this computer, outside the build sandbox")
    return "; ".join(parts) + "."


# --- quarantined copies -------------------------------------------------------------------------------------------

@dataclass
class _Held:
    prepared: Any        # community_manager.PreparedInstall
    sha256: str
    expires: float


class Quarantine:
    """Quarantined pack copies waiting for the person, keyed by kind, pack id and sha256 (reused while the same pack is
    asked about again, e.g. by a pending approval's second look). Removed on a "no", without an answer, and at the
    latest QUARANTINE_TTL after the last look."""

    def __init__(self, community, clock=time.monotonic):
        self._community = community
        self._clock = clock
        self._held: Dict[Tuple[str, str, str], _Held] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(kind: str, pack_id: str, sha256: str) -> Tuple[str, str, str]:
        return kind, pack_id.casefold(), sha256

    def sweep(self) -> None:
        now = self._clock()
        with self._lock:
            old = [k for k, h in self._held.items() if h.expires <= now]
            gone = [self._held.pop(k) for k in old]
        for held in gone:
            self._discard(held.prepared)

    def _discard(self, prepared) -> None:
        try:
            self._community().discard(prepared)
        except Exception:  # noqa: BLE001 - the folder may be gone already
            shutil.rmtree(getattr(prepared, "quarantine", ""), ignore_errors=True)

    def get(self, key) -> Optional[Any]:
        self.sweep()
        with self._lock:
            held = self._held.get(key)
            if held is None or not Path(held.prepared.quarantine).is_dir():
                return None
            held.expires = self._clock() + QUARANTINE_TTL
            return held.prepared

    def put(self, key, prepared) -> None:
        with self._lock:
            old = self._held.get(key)
            self._held[key] = _Held(prepared, key[2], self._clock() + QUARANTINE_TTL)
        if old is not None and old.prepared is not prepared:
            self._discard(old.prepared)
        timer = threading.Timer(QUARANTINE_TTL + 1, self.sweep)
        timer.daemon = True   # never keeps the process alive
        timer.start()

    def take(self, key) -> Optional[Any]:
        """The copy for the installation (it leaves the store: finish_install moves it into place)."""
        with self._lock:
            held = self._held.pop(key, None)
        if held is None or not Path(held.prepared.quarantine).is_dir():
            return None
        return held.prepared

    def drop(self, key) -> None:
        with self._lock:
            held = self._held.pop(key, None)
        if held is not None:
            self._discard(held.prepared)

    def held(self) -> List[Tuple[str, str, str]]:
        with self._lock:
            return list(self._held)


# --- input and output models --------------------------------------------------------------------------------------

class ListPacksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Optional[Kind] = Field(default=None, description="Only packs of this kind: target, recipe or knowledge")
    query: Optional[str] = Field(default=None, max_length=100, description="Only packs whose id or title contains this")
    pack_id: Optional[str] = Field(default=None, pattern=PACK_ID_PATTERN,
                                   description="One pack by id: adds its description, download and recipe")
    installed_only: bool = Field(default=False, description="Only installed packs")
    verify: bool = Field(default=False, description="Also check the installed packs (revoked, changed since installed)")
    refresh: bool = Field(default=False, description="Fetch the signed index now (network; once an hour)")
    page: int = Field(default=1, ge=1, le=10000, description="Page of 50 packs, from 1")


class DownloadOut(BaseModel):
    size: int
    sha256: str


class PackOut(BaseModel):
    kind: str
    pack_id: untrusted_text(64)
    version: untrusted_text(64)
    title: untrusted_text(200) = ""
    author: untrusted_text(100) = ""
    license: untrusted_text(64) = ""
    trust: Literal["verified", "community", "local", "revoked", "unknown"]
    installed_version: Optional[untrusted_text(64)] = None
    revoked: Optional[untrusted_text(300)] = Field(default=None, description="Why the signed index revoked it")
    description: Optional[untrusted_text(1000)] = None
    download: Optional[DownloadOut] = None
    recipe: Optional[Dict[str, untrusted_text(500)]] = Field(
        default=None, description="An installed recipe's pinned values (model, commit, target, format, board, "
                                  "quantization)")
    installed_via: Optional[str] = Field(default=None, description="grant:<id> when installed under a standing "
                                                                   "approval")


class VerificationOut(BaseModel):
    kind: str
    pack_id: untrusted_text(64)
    version: untrusted_text(64)
    trust: str
    problems: List[untrusted_text(300)]


class ListPacksOut(ToolOutput):
    index_note: untrusted_text(300)
    items: List[PackOut]
    total: int
    page: int
    more: bool
    verification: Optional[List[VerificationOut]] = None


class AddPackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = Field(description="target, recipe or knowledge")
    pack_id: str = Field(pattern=PACK_ID_PATTERN, description="The pack id exactly as list_packs shows it")


class AddPackStartedOut(ToolOutput):
    job_id: str
    state: str
    kind: str
    pack_id: untrusted_text(64)
    version: untrusted_text(64)
    trust: str
    note: str = ("Poll get_job_status with wait_seconds=50 until the job ends; its result names where the pack was "
                 "installed (a label, never a path).")


class ExportPackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = Field(description="target, recipe or knowledge")
    source: Optional[str] = Field(default=None, max_length=64,
                                  description="target: the target id; recipe: the artifact_id of a finished build "
                                              "(list_artifacts); knowledge: leave it out")
    author: Optional[str] = Field(default=None, min_length=1, max_length=64,
                                  description="Author name in the pack (default: the author of the last export)")
    license: Optional[str] = Field(default=None, pattern=LICENSE_PATTERN,
                                   description="SPDX licence id of the pack (default: MIT; NOASSERTION for knowledge)")
    model_license: str = Field(default="unknown", pattern=LICENSE_PATTERN,
                               description="Recipe only: the model's licence (default unknown)")
    pack_id: Optional[str] = Field(default=None, pattern=PACK_ID_PATTERN,
                                   description="Knowledge only: the pack id (default knowledge-<date>)")


class ScrubOut(BaseModel):
    file: untrusted_text(300)
    changes: int = Field(description="Lines the privacy scrub changed in this file")


class ExportPackOut(ToolOutput):
    path: untrusted_text(300) = Field(description="The file as a label: contributions/<file name>")
    kind: str
    pack_id: untrusted_text(64)
    version: untrusted_text(64)
    signer_key_id: str
    scrub_summary: List[ScrubOut]
    pack_notes: List[untrusted_text(500)] = Field(default_factory=list)


# --- helpers ------------------------------------------------------------------------------------------------------

def _cfg(framework, key: str, default=None):
    get = getattr(getattr(framework, "config", None), "get", None)
    try:
        return get(key, default) if callable(get) else default
    except Exception:  # noqa: BLE001
        return default


def _no_paths(text: str) -> str:
    from orchestrator.tools.tools_knowledge import _no_paths as strip
    return strip(text)


def _scrub_counts(changes) -> List[Dict[str, Any]]:
    """Per file only how many lines the privacy scrub changed: the diff's removed side is the personal data."""
    out = []
    for name, diff in changes:
        lines = [line for line in str(diff).splitlines() if line.startswith("-") and not line.startswith("---")]
        out.append({"file": name, "changes": len(lines)})
    return out


def register_community_tools(registry: ToolRegistry, framework, jobs) -> ToolRegistry:
    from orchestrator.Core.community_pack import PackError

    def community():
        cm = getattr(framework, "community_manager", None)
        if cm is None:
            get = getattr(framework, "get_component", None)
            cm = get("community_manager") if callable(get) else None
        if cm is None:
            raise ToolError("The community zoo is not available in this installation.", code="not_supported",
                            next="Tell the person; 'llm-cli doctor' checks the installation.")
        return cm

    quarantine = Quarantine(community)
    refreshed: Dict[str, float] = {}
    refresh_lock = threading.Lock()

    def pack_error(e: Exception, code: str = "failed", next: str = "") -> ToolError:
        return ToolError(_no_paths(str(e)) or "The community zoo refused it.", code=code,
                         next=next or "Tell the person what failed; nothing was installed.")

    def offline() -> bool:
        return bool(_cfg(framework, "offline_mode", False))

    def label(cm, path: Path) -> str:
        """A folder of the installation as a label (targets/<name>, community/recipes/<name>, ...), never a path."""
        path = Path(path)
        for root, name in ((cm.targets_dir, "targets"), (cm.recipes_dir, "community/recipes"),
                           (cm.knowledge_dir, "community/knowledge"), (cm.contributions_dir, "community/contributions")):
            try:
                return f"{name}/{path.resolve().relative_to(Path(root).resolve()).as_posix()}"
            except (ValueError, OSError):
                continue
        return path.name

    # -- list_packs ------------------------------------------------------------------------------------------------

    def recipe_values(folder: Path) -> Optional[Dict[str, str]]:
        import yaml
        from orchestrator.schemas.recipes import Recipe
        try:
            recipe = Recipe.model_validate(yaml.safe_load((Path(folder) / "recipe.yml").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - an unreadable recipe shows no values; verify reports it
            return None
        data = recipe.model_dump(mode="json")
        model, target = data.get("model") or {}, data.get("target") or {}
        values = {"model": model.get("repo"), "commit": model.get("revision"), "model_license": model.get("license"),
                  "target": target.get("id"), "format": data.get("format"), "board": data.get("board"),
                  "quantization": data.get("quantization")}
        return {k: str(v) for k, v in values.items() if v not in (None, "")}

    def list_packs(inp: ListPacksIn, ctx: ToolContext) -> ListPacksOut:
        from orchestrator.Core.community_manager import read_provenance
        quarantine.sweep()
        cm = community()
        index, note = None, ""
        if inp.refresh:
            now = time.monotonic()
            with refresh_lock:
                last = refreshed.get(ctx.actor)
                allowed = last is None or now - last >= REFRESH_EVERY
                if allowed and not offline():
                    refreshed[ctx.actor] = now
            if offline():
                ctx.notes.append("Offline mode is on: the cached index is used.")
            elif not allowed:
                minutes = max(1, int((REFRESH_EVERY - (now - last)) / 60))
                ctx.notes.append(f"The index was refreshed less than an hour ago by this client; the cached index is "
                                 f"used (refresh again in about {minutes} minutes).")
            else:
                try:
                    state = cm.load_index(refresh=True)
                    index, note = state.index, state.note or f"Signed index version {state.index.version} ({state.source})."
                except PackError as e:
                    note = f"No current index: {_no_paths(str(e))}"
        if index is None:
            index = cm.cached_index()
            if index is not None and not note:
                note = f"Signed index version {index.version} (cached)."
            elif index is None and not note:
                note = "No signed index on this computer yet; refresh=true fetches it."
        items = cm.list_items(index)
        rows = []
        for item in items:
            if inp.kind and item.kind != inp.kind:
                continue
            if inp.pack_id and item.id.casefold() != inp.pack_id.casefold():
                continue
            if inp.installed_only and item.installed_version is None:
                continue
            if inp.query and inp.query.casefold() not in f"{item.id} {item.title}".casefold():
                continue
            trust = item.trust.value if hasattr(item.trust, "value") else str(item.trust or "unknown")
            row = PackOut(kind=item.kind, pack_id=item.id, version=item.version, title=item.title or "",
                          author=item.author or "", license=item.license or "",
                          trust=trust if trust in ("verified", "community", "local", "revoked") else "unknown",
                          installed_version=item.installed_version, revoked=item.revoked)
            if item.path is not None:
                prov = read_provenance(item.path)
                row.installed_via = getattr(prov, "installed_via", None) if prov is not None else None
            if inp.pack_id:
                row.description = item.description or None
                if item.entry is not None:
                    row.download = DownloadOut(size=item.entry.size, sha256=item.entry.sha256)
                if item.kind == "recipe" and item.path is not None:
                    row.recipe = recipe_values(item.path)
            rows.append(row)
        start = (inp.page - 1) * PAGE_SIZE
        out = ListPacksOut(index_note=note, items=rows[start:start + PAGE_SIZE], total=len(rows), page=inp.page,
                           more=len(rows) > start + PAGE_SIZE)
        if inp.verify:
            out.verification = []
            for folder, prov, trust, problems in cm.verify_installed():
                out.verification.append(VerificationOut(
                    kind=prov.kind, pack_id=prov.id, version=prov.version,
                    trust=trust.value if trust is not None else "unknown",
                    problems=[_no_paths(p) for p in problems]))
        return out

    # -- add_pack_from_index ---------------------------------------------------------------------------------------

    def prepare_add(inp: AddPackIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.Core.disk_plan import reserve_for
        cm = community()
        if offline():
            raise ToolError("Offline mode is on: no pack can be downloaded.", code="not_supported",
                            next="Only the person can switch offline mode off (in the GUI or with llm-cli).")
        try:
            state = cm.load_index(refresh=True)
        except PackError as e:
            raise pack_error(e, next="Tell the person: the signed community index could not be loaded.") from None
        if state.source != "fetched":
            raise ToolError(f"The signed community index could not be updated ({_no_paths(state.note)}); nothing is "
                            "installed without the current revocations.", code="failed",
                            next="Try again later; tell the person if it keeps failing.")
        entry = state.index.latest(inp.kind, inp.pack_id)
        if entry is None:
            raise ToolError(f"There is no {inp.kind} pack '{inp.pack_id}' in the signed index.", code="unknown_id",
                            next="Call list_packs (refresh=true for the newest index) for the pack ids.")
        revoked = state.index.revocation(inp.kind, entry.id, entry.version, entry.sha256)
        if revoked is not None:
            raise ToolError(f"The {inp.kind} pack {entry.id} {entry.version} was revoked by the signed index.",
                            code="not_supported", next="Choose another pack; a revoked pack is never installed.")
        root = cm._install_root(inp.kind)
        try:
            usage = shutil.disk_usage(root if Path(root).exists() else Path(root).parent)
        except OSError:
            usage = None
        if usage is not None and usage.free < 2 * entry.size + reserve_for(usage.total):
            raise ToolError(f"Not enough disk space for the pack: {2 * entry.size} bytes needed (quarantine and "
                            f"installed copy) plus a reserve, {usage.free} bytes free.", code="insufficient_resources",
                            next="Tell the person how much space is needed.")
        key = Quarantine.key(inp.kind, entry.id, entry.sha256)
        prepared = quarantine.get(key)
        if prepared is None:
            try:
                prepared = cm.prepare_install(inp.kind, entry.id)
            except PackError as e:
                raise pack_error(e, code="not_supported" if "rejected" in str(e) or "exists" in str(e) else "failed",
                                 next="Tell the person why the pack cannot be installed; nothing was installed.") \
                    from None
            if prepared.provenance.pack_sha256 != entry.sha256 or prepared.trust.value not in ("verified", "community"):
                cm.discard(prepared)
                raise ToolError("The signed index changed while the pack was checked; nothing was installed.",
                                code="failed", next="Call add_pack_from_index again.")
            quarantine.put(key, prepared)
        m = prepared.manifest
        dest = label(cm, prepared.destination)
        consequence = (f"Installs the {m.kind} pack {m.id} {m.version} from the signed community index (trust "
                       f"{prepared.trust.value}) into {dest}.")
        if m.kind == "target":
            consequence += (" It adds build scripts and a Dockerfile to this computer; the image build runs with network "
                            "access the first time the target is built.")
        elif m.kind == "knowledge":
            consequence += " Its pages are added to the knowledge base and later reach AI models as reference data."
        facts = [Fact("Pack", f"{m.kind} {m.id} {m.version} by {m.author} (licence {m.license})", INDEX_TEXT),
                 Fact("Trust", f"{prepared.trust.value} (signed index: "
                               + ("reviewed and reproduced independently)" if prepared.trust.value == "verified"
                                  else "reviewed)")),
                 Fact("Download", f"{entry.size} bytes, sha256 {entry.sha256} (checked)"),
                 Fact("Files", f"{len(m.files)} ({sum(f.size for f in m.files)} bytes)"),
                 Fact("Installs to", dest)]
        if entry.title:
            facts.append(Fact("Title", entry.title, INDEX_TEXT))
        if entry.description:
            facts.append(Fact("Description", entry.description, INDEX_TEXT))
        for n in prepared.notes:
            facts.append(Fact("Note", n, PACK_TEXT))
        images = []
        if m.kind == "target":
            images = pack_base_images(Path(prepared.quarantine) / "pack")
            if not images:
                facts.append(Fact("Base image", "no Dockerfile with a base image found in the pack"))
            for file, image in images:
                facts.append(Fact(f"Base image ({file})", base_image_line(image), PACK_TEXT))
        bound = {"kind": m.kind, "pack_id": m.id, "version": m.version, "sha256": prepared.provenance.pack_sha256,
                 "trust": prepared.trust.value, "base_images": [i for _, i in images],
                 # the facts a standing approval is matched against (grants.PACK_RULE), never the AI's words
                 "grant": {"source": "index", "kind": m.kind, "trust": prepared.trust.value,
                           "signing_key": getattr(state, "signer", "") or ""}}
        return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound)

    def add_pack_from_index(inp: AddPackIn, ctx: ToolContext) -> AddPackStartedOut:
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if not bound.get("sha256") or bound.get("kind") != inp.kind:
            raise ToolError("The installation is not bound to what the person was shown; nothing was installed.",
                            code="failed", next="Call add_pack_from_index again through the framework's tool registry.")
        cm = community()
        key = Quarantine.key(bound["kind"], bound["pack_id"], bound["sha256"])
        prepared = quarantine.take(key)
        if prepared is None or prepared.provenance.pack_sha256 != bound["sha256"]:
            if prepared is not None:
                cm.discard(prepared)
            raise ToolError("The checked copy the person was shown is no longer there; nothing was installed.",
                            code="failed", next="Call add_pack_from_index again; the person is asked again.")
        channel = ctx.confirm_channel or ""
        if channel.startswith("grant:"):
            prepared.provenance.installed_via = channel

        def work(handle) -> Dict[str, Any]:
            handle.progress(0, 1, "installing")
            try:
                dest = cm.finish_install(prepared)
            except PackError as e:
                raise ValueError(_no_paths(str(e))) from None
            if prepared.manifest.kind == "target":
                tm = getattr(framework, "target_manager", None)
                if tm is not None and hasattr(tm, "initialize"):
                    tm.initialize()
            result = {"installed_to": label(cm, dest), "kind": prepared.manifest.kind, "pack_id": prepared.manifest.id,
                      "version": prepared.manifest.version, "trust": prepared.trust.value,
                      "notes": list(prepared.notes)}
            if prepared.manifest.kind == "knowledge":
                try:
                    pages = json.loads((Path(dest) / "knowledge.json").read_text(encoding="utf-8"))
                    result["knowledge_pages"] = len(pages) if isinstance(pages, list) else 0
                except (OSError, ValueError):
                    result["knowledge_pages"] = 0
            if channel.startswith("grant:"):
                result["installed_via"] = channel
            return result

        try:
            job = jobs.start("pack_import", ctx.owner, work)
        except Exception:
            cm.discard(prepared)
            raise
        m = prepared.manifest
        return AddPackStartedOut(job_id=job.job_id, state=job.state, kind=m.kind, pack_id=m.id, version=m.version,
                                 trust=prepared.trust.value)

    def discard_add(inp: AddPackIn, ctx: ToolContext) -> None:
        """The person said no (or nobody answered): the quarantined copy of this call is removed."""
        bound = getattr(getattr(ctx, "facts", None), "bound", None) or {}
        if bound.get("sha256"):
            quarantine.drop(Quarantine.key(bound["kind"], bound["pack_id"], bound["sha256"]))
            return
        for key in quarantine.held():
            if key[0] == inp.kind and key[1] == inp.pack_id.casefold():
                quarantine.drop(key)

    def pack_grant_facts(inp, ctx) -> Optional[Dict[str, Any]]:
        bound = getattr(getattr(ctx, "facts", None), "bound", None) or {}
        grant = bound.get("grant")
        return dict(grant) if isinstance(grant, dict) else None

    # -- export_pack -----------------------------------------------------------------------------------------------

    def last_author_file(cm) -> Path:
        return Path(cm.cache_dir) / "last_export.json"

    def last_author(cm) -> Optional[str]:
        try:
            data = json.loads(last_author_file(cm).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        author = data.get("author") if isinstance(data, dict) else None
        return author if isinstance(author, str) and 0 < len(author) <= 64 else None

    def signer_id(cm) -> str:
        """The key id of the person's existing signing key; a key is never created here."""
        from orchestrator.Core.community_pack import UserKeys, key_id
        secrets = cm._secrets()
        stored = None
        try:
            stored = secrets.get_secret(UserKeys.SIGNING_SECRET) if secrets is not None else None
        except Exception:  # noqa: BLE001 - an unreadable store is no key
            stored = None
        if not stored:
            raise ToolError("No community signing key exists on this computer yet, so no pack can be signed.",
                            code="needs_person",
                            next="Ask the person to create their keys with 'llm-cli zoo keys' (a key is created only "
                                 "by them), then call export_pack again.")
        try:
            return key_id(UserKeys(secrets).signing_key().public_key())
        except PackError as e:
            raise pack_error(e, code="needs_person", next="Ask the person to check their keys with "
                                                          "'llm-cli zoo keys'.") from None

    def export_source(cm, inp: ExportPackIn) -> Tuple[Optional[str], str]:
        """(the source the community manager takes, its speaking name)."""
        from orchestrator.Core.community_manager import read_provenance
        if inp.kind == "knowledge":
            if inp.source:
                raise ToolError("A knowledge pack takes no source: it holds your own pages of the knowledge base.",
                                code="invalid_value", next="Leave source out for kind knowledge.")
            return None, "your own pages of the knowledge base"
        if not inp.source:
            raise ToolError(f"export_pack needs 'source' for kind {inp.kind}: "
                            + ("the target id (list_targets)." if inp.kind == "target" else
                               "the artifact_id of a finished build (list_artifacts)."), code="missing_argument",
                            next="Call it again with source.")
        if inp.kind == "target":
            folder = cm._target_folder(inp.source) if re.fullmatch(PACK_ID_PATTERN, inp.source) else None
            if folder is None:
                raise ToolError(f"Unknown target '{inp.source}'.", code="unknown_id",
                                next="Call list_targets for the target ids.")
            if read_provenance(folder) is not None:
                raise ToolError(f"Target {folder.name} is a community pack of another author; it is shared already.",
                                code="not_supported", next="Export a target of your own.")
            return folder.name, f"target {folder.name}"
        if not re.fullmatch(ARTIFACT_PATTERN, inp.source):
            raise ToolError(f"'{inp.source}' is not an artifact id (a_ and 16 hex characters).", code="invalid_value",
                            next="Call list_artifacts for the artifact ids.")
        service = getattr(framework, "build_service", None)
        finder = getattr(service, "_artifact", None)
        if not callable(finder):
            raise ToolError("Built models cannot be looked up here.", code="not_supported",
                            next="Tell the person; 'llm-cli doctor' checks the installation.")
        try:
            _, art_label, info = finder(inp.source)
        except ValueError as e:
            raise ToolError(str(e), code=getattr(e, "code", "unknown_id") or "unknown_id",
                            next=getattr(e, "next", "") or "Call list_artifacts for the artifact ids.") from None
        info_file = Path(info["path"]) / "build_info.json"
        if not info_file.is_file() or info_file.is_symlink():
            raise ToolError(f"The build {inp.source} has no build record to make a recipe from.", code="not_supported",
                            next="Choose another finished build.")
        return str(info_file), f"the build {inp.source} ({art_label})"

    def prepare_export(inp: ExportPackIn, ctx: ToolContext) -> CallFacts:
        cm = community()
        key = signer_id(cm)
        author = inp.author or last_author(cm)
        if not author:
            raise ToolError("export_pack needs 'author': there is no earlier export to take it from.",
                            code="missing_argument", next="Ask the person which author name the pack should carry.")
        source, named = export_source(cm, inp)
        licence = inp.license or ("NOASSERTION" if inp.kind == "knowledge" else "MIT")
        consequence = (f"Creates a pack signed with your key {key} as author {author}: a {inp.kind} pack of {named}, "
                       "written to the contributions folder (community/contributions) on this computer. Nothing is "
                       "uploaded.")
        facts = [Fact("Signed with", f"your community key {key}"), Fact("Author", author),
                 Fact("Licence", licence),
                 Fact("Privacy scrub", "home folders, network addresses, host and user names and secrets are removed "
                                       "from every file; the result lists only how many lines changed per file")]
        if inp.kind == "recipe":
            facts.append(Fact("Model licence", inp.model_license))
        if inp.kind == "knowledge":
            facts.append(Fact("Pages", "your own crawled pages; pages from installed knowledge packs are left out, and "
                                       "every page keeps its source address"))
        bound = {"kind": inp.kind, "source": source, "author": author, "license": licence,
                 "model_license": inp.model_license, "pack_id": inp.pack_id, "signer": key}
        return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound)

    def run_export(cm, bound: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = cm.export_pack(bound["kind"], bound["source"], author=bound["author"], license=bound["license"],
                                    pack_id=bound.get("pack_id"), model_license=bound["model_license"])
        except PackError as e:
            raise ValueError(_no_paths(str(e))) from None
        if result.signer != bound["signer"]:  # the key changed after the question: not the statement shown
            Path(result.path).unlink(missing_ok=True)
            raise ValueError("The signing key changed after the question; the pack was removed.")
        try:
            last_author_file(cm).parent.mkdir(parents=True, exist_ok=True)
            last_author_file(cm).write_text(json.dumps({"author": bound["author"]}), encoding="utf-8")
        except OSError:
            pass
        m = result.manifest
        return {"path": label(cm, result.path), "kind": m.kind, "pack_id": m.id, "version": m.version,
                "signer_key_id": result.signer, "scrub_summary": _scrub_counts(result.changes),
                "pack_notes": [_no_paths(n) for n in result.notes]}

    def export_pack(inp: ExportPackIn, ctx: ToolContext):
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if not bound.get("signer") or bound.get("kind") != inp.kind:
            raise ToolError("The export is not bound to what the person was shown; nothing was written.",
                            code="failed", next="Call export_pack again through the framework's tool registry.")
        cm = community()
        if signer_id(cm) != bound["signer"]:
            raise ToolError("The signing key changed after the question; nothing was written.", code="failed",
                            next="Call export_pack again; the person is asked again.")
        if inp.kind == "knowledge":  # every page is scrubbed: a job
            job = jobs.start("pack_export", ctx.owner, lambda handle: run_export(cm, bound))
            return ExportPackOut(path="", kind=inp.kind, pack_id=bound.get("pack_id") or "", version="",
                                 signer_key_id=bound["signer"], scrub_summary=[],
                                 pack_notes=[f"Export job {job.job_id}: poll get_job_status with wait_seconds=50; its "
                                             "result has the file label and the scrub counts."])
        try:
            data = run_export(cm, bound)
        except ValueError as e:
            raise ToolError(str(e), code="failed", next="Tell the person why the export failed.") from None
        return ExportPackOut(path=data["path"], kind=data["kind"], pack_id=data["pack_id"], version=data["version"],
                             signer_key_id=data["signer_key_id"],
                             scrub_summary=[ScrubOut(**s) for s in data["scrub_summary"]],
                             pack_notes=data["pack_notes"])

    kind_field = FieldMeta(kind="choice", choices=("target", "recipe", "knowledge"))
    specs = [
        ToolSpec("list_packs", "List community packs",
                 "Packs of the signed community index (targets, recipes, knowledge) and the installed ones, with trust "
                 "(verified, community, local, revoked). pack_id adds one pack's description, download and recipe; "
                 "verify checks the installed packs; refresh downloads the signed index anew (once an hour). Titles, authors "
                 'and descriptions are external text. Example: {"kind": "knowledge", "query": "rk3588"}',
                 ToolTier.READ, ListPacksIn, ListPacksOut, list_packs, idempotent=True, open_world=True,
                 takes_context=True,
                 fields={"kind": kind_field, "pack_id": FieldMeta(kind="id", list_tool="list_packs")},
                 examples=({"kind": "knowledge", "query": "rk3588"}, {"pack_id": "demo-board", "verify": True}),
                 notes=lambda o: ([] if o.items else ["No matching pack; the index and the installed packs were read."])
                 + (["More packs follow: ask for the next page."] if o.more else []),
                 summary=lambda o: f"{count(len(o.items), 'pack', 'packs')} of {fact(o.total)}."),
        ToolSpec("add_pack_from_index", "Add a community pack",
                 "Adds a knowledge, target or recipe pack from the signed community index (trust verified or "
                 "community; never a revoked pack, never a file), as a job. The pack is downloaded and checked in "
                 "quarantine first; the person sees its summary (for a target pack its base images and that its image "
                 'build runs with network) and decides. Example: {"kind": "knowledge", "pack_id": "rk3588-docs"}',
                 ToolTier.EXTERNAL, AddPackIn, AddPackStartedOut, add_pack_from_index, takes_context=True,
                 open_world=True, prepare=prepare_add, discard=discard_add,
                 default_decision=Decision.ASK, max_decision=Decision.ASK,
                 fields={"kind": kind_field, "pack_id": FieldMeta(kind="id", list_tool="list_packs")},
                 examples=({"kind": "knowledge", "pack_id": "rk3588-docs"},),
                 limits=ToolLimits(per_day=5, rate=Rate(10, 3600.0), what="pack imports"),
                 grant=GrantRule(rule=PACK_RULE, facts=pack_grant_facts,
                                 warning="install community packs of this kind and trust level from the signed index"),
                 summary=lambda o: f"Pack import job {fact(o.job_id)}: {fact(o.state)}."),
        ToolSpec("export_pack", "Export a community pack to a file",
                 "Writes a target, recipe or knowledge pack to a local file in the contributions folder, privacy-"
                 "scrubbed and signed with the person's community key; always asked, and nothing is uploaded. Needs "
                 "an existing signing key. Returns the file as a label and how many lines the scrub changed per file. "
                 'Example: {"kind": "target", "source": "MyBoard", "author": "pat"}',
                 ToolTier.LOCAL_WRITE, ExportPackIn, ExportPackOut, export_pack, takes_context=True,
                 prepare=prepare_export, default_decision=Decision.ASK, max_decision=Decision.ASK,
                 fields={"kind": kind_field, "source": FieldMeta(kind="id"), "license": FieldMeta(kind="trust"),
                         "model_license": FieldMeta(kind="trust"), "pack_id": FieldMeta(kind="id"),
                         "author": FieldMeta(kind="trust")},
                 examples=({"kind": "target", "source": "MyBoard", "author": "pat"},),
                 limits=ToolLimits(per_day=20, what="exports"),
                 summary=lambda o: f"Exported {fact(o.kind)} pack, signed with key {fact(o.signer_key_id)}."),
    ]
    for spec in specs:
        registry.register(spec)
    return registry


__all__ = ["register_community_tools", "dockerfile_bases", "pack_base_images", "base_image_line", "Quarantine"]
