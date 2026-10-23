#!/usr/bin/env python3
"""
Hugging Face and local files for AI callers (MCP design 2.4, work package T6; DECISIONS round 2 items 7 and 8).

- `search_hf_models`: the gated filter `free|any|gated`, default `free` (only models that download without accepting
  conditions or asking for access), so a small model only sees what it can use; the note says what was left out.
- `get_hf_model_info`: licence (allowlist, `other` with its name and link, the base model's licence and gating),
  gating, whether the token stored in the framework has access (checked here; the token never leaves), parameters,
  commit, files with sizes, total size.
- `download_hf_file`: one file of one repository into the models folder, as a job. Two-phase: `prepare` pins the
  commit, size and sha256 the Hub lists, checks access before the question and the disk (no fixed size cap: refused
  only when the disk cannot hold the file plus a reserve); the job fetches exactly that through the framework's own
  fetcher (resumable `.part`, stopped by cancel_job, sha256 checked before the file gets its name).
- `list_local_files`: the model and dataset files the person put into the folders the AI may use, by id and label.

No input takes a token or a path; repository names and file names from the Hub are external text.
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.facts_catalog import (LARGE_DOWNLOAD_BYTES, blocked, facts_unknown, gb_number, size_class,
                                              warn)
from orchestrator.tools.grants import DOWNLOAD_RULE
from orchestrator.tools.output import ToolOutput, count, fact, untrusted_text
from orchestrator.tools.policy import ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, CallWarning, Fact, FieldMeta, GrantRule, Rate, ToolLimits

REPO_PATTERN = r"^[A-Za-z0-9][\w.-]*/[\w.-]+$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
GATED_NOTE = "Gated models were left out; search again with gated=any to include them."
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
SEARCH_RATE = Rate(30, 3600.0)   # Hugging Face searches per client and hour (the limits table)
INFO_RATE = Rate(60, 3600.0)
GB = 1000 ** 3
LICENCE_WORDS = {"false": "permissive", "true": "restrictive"}


def _size(n: Optional[int]) -> str:
    if n is None:
        return "size unknown"
    return f"{n / GB:.2f} GB" if n >= 10 ** 8 else f"{n / 1000 ** 2:.1f} MB"


def download_warnings(facts: Any, name: str, size: int, disk: Dict[str, Any]) -> tuple:
    """The warnings before a single-file download (design 4.3): a tight disk and a large file (situation); pickle
    weights and facts that could not be checked (security); a gated model or base model and a licence that is not on
    the permissive list (legal). A licence or base model that could not be read is unknown, never "no problem"."""
    found: List[CallWarning] = []
    if disk.get("tight"):
        found.append(warn("disk_tight", free_after_gb=gb_number(disk.get("after")),
                          reserve_gb=gb_number(disk.get("reserve"))))
    if size >= LARGE_DOWNLOAD_BYTES:
        found.append(warn("download_large", download_gb=gb_number(size)))
    if name.lower().endswith(PICKLE_SUFFIXES):
        found.append(warn("pickle_weights"))
    if facts.gated != "false":
        found.append(warn("gated_model"))
    unknown = []
    if facts.base_model:
        if facts.base_gated is None:
            unknown.append("base_model")
        elif facts.base_gated != "false":
            found.append(warn("base_model_gated"))
    if facts.license_restrictive == "true":
        found.append(warn("license_restrictive"))
    elif facts.license_restrictive != "false":
        if (facts.license or "unknown").strip().lower() == "unknown":
            unknown.append("licence")
        else:
            found.append(warn("license_unknown"))
    if unknown:
        found.append(facts_unknown(*unknown))
    return tuple(found)


# --- models ---------------------------------------------------------------------------------------------------------

class SearchHfIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200, description="What to search for, e.g. 'qwen2.5 0.5b gguf'")
    limit: int = Field(default=20, ge=1, le=50, description="Most models to return (1-50)")
    gated: Literal["free", "any", "gated"] = Field(
        default="free", description="free (default): only models that download without accepting conditions; "
                                    "any: all; gated: only gated models (they need the person's token and access)")


class HfModelOut(BaseModel):
    id: untrusted_text(200)
    downloads: int
    likes: int
    gated: Literal["false", "auto", "manual"]
    license: untrusted_text(200)


class SearchHfOut(ToolOutput):
    models: List[HfModelOut]
    gated_filter: str


class ModelInfoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=3, max_length=200, pattern=REPO_PATTERN,
                       description="A Hugging Face repository id (owner/name)")
    revision: Optional[str] = Field(default=None, pattern=COMMIT_PATTERN,
                                    description="A 40-character commit; leave it out for the current commit")


class HfFileOut(BaseModel):
    name: untrusted_text(300)
    size: Optional[int] = None
    lfs: bool = Field(description="Stored with LFS: the Hub lists its sha256, which a download is checked against")


class ModelInfoOut(ToolOutput):
    model: untrusted_text(200)
    commit: str
    license: untrusted_text(200)
    license_restrictive: Literal["true", "false", "unknown"] = Field(
        description="false only for a licence on the permissive allowlist")
    license_name: untrusted_text(200) = ""
    license_link: untrusted_text(500) = ""
    gated: Literal["false", "auto", "manual"]
    token_access: Literal["yes", "no", "no_token", "not_needed"] = Field(
        description="Whether the token the person stored in the framework can read this repository")
    parameters: Optional[int] = None
    base_model: untrusted_text(200) = ""
    base_license: untrusted_text(200) = ""
    base_license_restrictive: str = ""
    base_gated: Optional[str] = None
    files: List[HfFileOut]
    files_truncated: bool = False
    total_bytes: int


class DownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=3, max_length=200, pattern=REPO_PATTERN,
                       description="A Hugging Face repository id (owner/name)")
    filename: str = Field(min_length=1, max_length=300,
                          description="A file name exactly as get_hf_model_info lists it, e.g. model-Q4_K_M.gguf")
    revision: Optional[str] = Field(default=None, pattern=COMMIT_PATTERN,
                                    description="A 40-character commit; leave it out for the current commit")


class DownloadStartedOut(ToolOutput):
    job_id: str
    state: str
    commit: str
    size_bytes: Optional[int] = None
    note: str = ("Poll get_job_status with wait_seconds=50 until the job ends; its result has the file id for "
                 "start_build. cancel_job stops it and keeps the part already downloaded.")


class LocalFilesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["model", "dataset", "any"] = Field(default="any", description="Only model or only dataset files")
    query: Optional[str] = Field(default=None, max_length=100, description="Only files whose name contains this")
    page: int = Field(default=1, ge=1, le=10000, description="Page of 50 files, from 1")


class LocalFileOut(BaseModel):
    file_id: str
    ref: untrusted_text(400) = Field(description="<label>:<relative path>, usable instead of the id")
    kind: Literal["model", "dataset"]
    format: str
    pickle: bool = Field(description="PyTorch pickle weights: they can run code when loaded")
    size_bytes: int
    modified: str


class FolderOut(BaseModel):
    label: str
    kind: str


class LocalFilesOut(ToolOutput):
    files: List[LocalFileOut]
    folders: List[FolderOut]
    total: int
    page: int
    more: bool


# --- the tools -------------------------------------------------------------------------------------------------------

_ACTIVE: Dict[str, threading.Thread] = {}   # download threads that still run (also after a cancel)
_ACTIVE_LOCK = threading.Lock()


def active_downloads() -> int:
    with _ACTIVE_LOCK:
        for key in [k for k, t in _ACTIVE.items() if not t.is_alive()]:
            _ACTIVE.pop(key, None)
        return len(_ACTIVE)


def register_hf_tools(registry: ToolRegistry, framework, jobs) -> ToolRegistry:
    from orchestrator.Core.hf_hub import HubClient, HubError, download_path, safe_filename

    def hub() -> HubClient:
        factory = getattr(framework, "hf_api_factory", None)  # tests give a stand-in for the Hub
        return HubClient(framework, api_factory=factory)

    def ledger():
        """The disk reservation ledger every process of this user shares (builds and downloads); None without a
        per-user folder (a stand-in framework)."""
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.disk_plan import ReservationLedger
        folder = state_dir(framework)
        return ReservationLedger(folder) if folder is not None else None

    def models_dir() -> Path:
        from orchestrator.tools.local_files import LocalFiles
        try:
            folder = LocalFiles.for_framework(framework).builtin["models"]
        except Exception as e:  # noqa: BLE001 - no per-user folder (a stand-in framework)
            raise ToolError("The models folder is not available here.", code="not_supported",
                            next="Tell the person; run_mojo_oracle (or 'llmcf mojo' in a terminal) checks the "
                                 "installation.") from e
        return Path(folder)

    def search_hf_models(inp: SearchHfIn) -> SearchHfOut:
        found = hub().search(inp.query, inp.limit, inp.gated)
        return SearchHfOut(models=[HfModelOut(**m) for m in found], gated_filter=inp.gated)

    def get_hf_model_info(inp: ModelInfoIn) -> ModelInfoOut:
        f = hub().info(inp.model, inp.revision)
        return ModelInfoOut(
            model=f.repo, commit=f.commit, license=f.license, license_restrictive=f.license_restrictive,
            license_name=f.license_name, license_link=f.license_link, gated=f.gated, token_access=f.token_access,
            parameters=f.parameters, base_model=f.base_model, base_license=f.base_license,
            base_license_restrictive=f.base_license_restrictive, base_gated=f.base_gated,
            files=[HfFileOut(name=x.name, size=x.size, lfs=x.sha256 is not None) for x in f.files],
            files_truncated=f.files_truncated, total_bytes=f.total_bytes)

    def info_notes(o: ModelInfoOut) -> List[str]:
        notes = []
        if o.gated != "false" and o.token_access != "yes":
            notes.append("This repository is gated and the token stored in the framework cannot read it: "
                         + ("no token is stored. " if o.token_access == "no_token" else "")
                         + "The person accepts the model's conditions on huggingface.co and stores a token with "
                           "'llm-cli secrets set hf_token' or in the GUI.")
        if o.base_gated not in (None, "false"):
            notes.append("The base model is gated: this repository may be a re-upload of a gated model; its licence "
                         "applies.")
        if any(x.name.lower().endswith(PICKLE_SUFFIXES) for x in o.files):
            notes.append("Some weights are PyTorch pickle files (.bin, .pt, .pth): they can run code when loaded; "
                         "prefer safetensors or GGUF.")
        if o.files_truncated:
            notes.append("Only the first 2000 files are listed.")
        return notes

    # -- download_hf_file: two-phase ---------------------------------------------------------------------------------

    def prepare_download(inp: DownloadIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.tools.normalize import NormalizeError, match_filename
        client = hub()
        facts = client.info(inp.model, inp.revision)
        if facts.gated != "false" and facts.token_access != "yes":
            raise blocked("gated_no_access", f"{inp.model} is gated and the token stored in the framework "
                          + ("is missing." if facts.token_access == "no_token" else "has no access to it."),
                          "Ask the person to accept the model's conditions on huggingface.co with their account, then "
                          "to store a token with 'llm-cli secrets set hf_token' or in the GUI.",
                          reason="needs_person" if facts.token_access == "no_token" else "permission_denied")
        names = [x.name for x in facts.files]
        try:
            name, repair = match_filename(inp.filename, names, "filename")
        except NormalizeError as e:
            raise blocked("file_not_in_repo", f"{inp.model} has no file '{inp.filename}' at commit "
                          f"{facts.commit[:12]}.", "Call get_hf_model_info for the file names.") from e
        # The repair (a name in another letter case) is the look-up's: named in the result and in every refusal after
        # it, and audited (CallFacts.repairs; a refusal carries it as `repairs`).
        repairs = (repair,) if repair is not None else ()
        try:
            return _download_facts(inp, facts, name, repairs)
        except (ToolError, ValueError) as e:
            e.repairs = list(repairs)
            raise

    def _download_facts(inp: DownloadIn, facts, name: str, repairs: tuple) -> CallFacts:
        from orchestrator.Core.hf_hub import disk_check
        file = facts.file(name)
        if not safe_filename(name):
            raise blocked("unsafe_filename", "That file name cannot be used as a file on this computer.",
                          "Choose another file of the repository.")
        if file is None or file.size is None:
            raise blocked("size_unknown", f"The Hub lists no size for '{name}'; it is not downloaded.",
                          "Choose another file of the repository.")
        dest = download_path(models_dir(), inp.model, facts.commit, name)
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.is_file() else 0
        disk = disk_check(models_dir(), max(file.size - have, 0), ledger())
        if not disk["fits"]:
            raise HubError(f"Not enough disk space for {name}: {_size(disk['needed'])} needed, {_size(disk['free'])} "
                           f"free, {_size(disk['reserve'])} kept free as a reserve"
                           + (f" and {_size(disk['reserved'])} reserved by other calls (running builds or downloads)"
                              if disk["reserved"] else "")
                           + ".", code="insufficient_resources",
                           next="Tell the person how much space is needed; they can free disk space or choose a "
                                "smaller file" + (", or wait until the running build or download has finished"
                                                  if disk["reserved"] else "") + ".", blocker="disk_full")
        pickle = name.lower().endswith(PICKLE_SUFFIXES)
        source = "repository text, not checked"
        lines = [Fact("File", f"{name} ({_size(file.size)}" + (", sha256 listed by the Hub)" if file.sha256 else
                                                               ", a small file without LFS: pinned by size)"),
                      source),
                 Fact("Commit", facts.commit),
                 # free space changes while a question waits: shown, never part of the decision digest
                 Fact("Disk", f"{_size(disk['free'])} free on the models folder's disk, "
                              f"{_size(disk['free'] - disk['needed'])} after the download"
                              + (" (a part downloaded earlier is resumed)" if have else ""), volatile=True),
                 Fact("Licence", f"{facts.license} ({LICENCE_WORDS.get(facts.license_restrictive, 'unknown: read it')})",
                      source)]
        if facts.license_name or facts.license_link:
            lines.append(Fact("Licence name and link", f"{facts.license_name} {facts.license_link}".strip(), source))
        lines.append(Fact("Gating", "none" if facts.gated == "false" else
                          f"{facts.gated}; your Hugging Face token stored in the framework has access"))
        if facts.base_model:
            lines.append(Fact("Base model", f"{facts.base_model}: licence {facts.base_license or 'unknown'}, gating "
                                            f"{facts.base_gated or 'unknown'}", source))
        if pickle:
            lines.append(Fact("Warning", "PyTorch pickle weights: they can run code when loaded"))
        bound = {"model": inp.model, "filename": name, "commit": facts.commit, "size": file.size,
                 "sha256": file.sha256 or "",
                 # the facts a standing approval is matched against (grants.DOWNLOAD_RULE), never the AI's words
                 "grant": {"repo": inp.model, "filename": name, "size_bytes": file.size,
                           "gated": facts.gated != "false", "base_gated": (facts.base_gated not in ("false", None))
                           if facts.base_model else False, "license": facts.license}}
        if facts.base_model and facts.base_gated is None:
            bound["grant"]["base_gated"] = None  # unknown: outside every standing approval
        if not disk.get("fits_standing"):
            # design 6.5: under a standing approval the larger reserve (20 GiB or 10 %) must stay free, else it asks
            bound["grant"] = None
        consequence = (f"Downloads {name} ({_size(file.size)}) of {inp.model} from huggingface.co (commit "
                       f"{facts.commit[:12]}) into the models folder.")
        bound["size_class"] = size_class(int(file.size))
        bound["commit_resolved"] = bool(facts.commit)
        return CallFacts(consequence=consequence, facts=tuple(lines), bound=bound, repairs=repairs,
                         warnings=download_warnings(facts, name, int(file.size), disk),
                         numbers={"size_class": bound["size_class"], "size_bytes": int(file.size),
                                  "free_after_bytes": int(disk.get("after", 0)), "reserve_bytes": int(disk["reserve"])},
                         scope={"size_bytes": int(file.size)}, consequence_template=consequence,
                         digest_keys=("model", "filename", "commit", "size", "sha256", "size_class"),
                         names={"model": inp.model})

    def download_hf_file(inp: DownloadIn, ctx: ToolContext) -> DownloadStartedOut:
        from orchestrator.Core.confirmed_builds import unattended
        from orchestrator.Core.hf_hub import HubFetcher, HubFile
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if not bound.get("commit") or bound.get("model") != inp.model:
            raise ToolError("The download is not bound to what the person was shown; nothing was downloaded.",
                            code="failed", next="Call download_hf_file again through the framework's tool registry.")
        from orchestrator.tools.tools_builds import require_commit
        require_commit(bound, ctx.confirm_channel)  # unattended: only the commit the look-up resolved
        # The look-up refused in offline mode; the person may have switched it on while the question was open: the
        # one offline refusal again, before anything is reserved or requested.
        hub().check_online()
        repo, name, commit = bound["model"], bound["filename"], bound["commit"]
        file = HubFile(name, int(bound["size"]), bound.get("sha256") or None)
        dest = download_path(models_dir(), repo, commit, name)
        token = hub().token()
        stop = threading.Event()
        reservation = take_reservation(repo, commit, name, file.size, dest, ctx.owner,
                                       standing=unattended(ctx.confirm_channel))

        def work(handle) -> Dict[str, Any]:
            from orchestrator.tools.local_files import LocalFiles
            try:
                fetcher = HubFetcher(token=token, stop=stop)
                path, digest = fetcher.fetch_file(
                    repo, commit, file, dest,
                    progress=lambda _n, done, total: handle.progress(done, total, f"{name}: {done} of {total} bytes"))
            except Exception as e:
                from orchestrator.Core.hf_hub import DownloadStopped
                if isinstance(e, DownloadStopped):
                    from orchestrator.Core.job_service import JobCancelled
                    raise JobCancelled() from None
                if isinstance(e, ValueError):
                    raise
                from orchestrator.tools.tools_knowledge import _no_paths  # never a folder of this computer
                raise HubError(_no_paths(str(e)) if str(e) else "The download failed.", code="failed",
                               next="Start the download again to resume it.") from None
            finally:
                reservation.release()
            ref = f"models:{path.relative_to(models_dir()).as_posix()}"
            pin = LocalFiles.for_framework(framework).resolve(ref)
            return {"file_id": pin.file_id, "ref": pin.ref, "size_bytes": file.size, "sha256": digest,
                    "commit": commit}

        try:
            job = jobs.start("hf_download", ctx.owner, work, on_cancel=stop.set)
        except Exception:
            reservation.release()
            raise
        thread = next((t for t in threading.enumerate() if t.name == f"job-{job.job_id}"), None)
        if thread is not None:
            with _ACTIVE_LOCK:
                _ACTIVE[job.job_id] = thread
        return DownloadStartedOut(job_id=job.job_id, state=job.state, commit=commit, size_bytes=file.size)

    def take_reservation(repo: str, commit: str, name: str, size: int, dest: Path, owner: str,
                         standing: bool = False):
        """The disk space of an accepted download, in the ledger builds use (checked again under its lock: two
        downloads or a download and a build prepared together cannot both take the same space); released when the
        job ends, fails or is cancelled. `standing`: under a standing approval the larger reserve (20 GiB or 10 %)
        must stay free, checked under the same lock (design 6.5). Without a ledger, this process's reservation."""
        from orchestrator.Core.disk_plan import InsufficientSpace
        from orchestrator.Core.hf_hub import reserve_space
        part = dest.with_name(dest.name + ".part")
        need = max(int(size) - (part.stat().st_size if part.is_file() else 0), 0)
        try:
            return reserve_space(ledger(), models_dir(), need, owner, f"{repo}/{name}", need_label="models",
                                 what=f"download {name}", key=f"{repo}/{commit}/{name}", standing=standing)
        except InsufficientSpace as e:
            if standing:
                from orchestrator.tools.tools_builds import STANDING_RESERVE_TEXT
                raise HubError(f"{STANDING_RESERVE_TEXT}; nothing was downloaded. {e}",
                               code="insufficient_resources",
                               next="Call it again once: outside the standing approval the person is asked, with the "
                                    "disk numbers.") from None
            raise HubError(f"Not enough disk space for {name} any more: {e}", code="insufficient_resources",
                           next=e.next) from None

    def download_grant_facts(inp, ctx) -> Optional[Dict[str, Any]]:
        bound = getattr(getattr(ctx, "facts", None), "bound", None) or {}
        grant = bound.get("grant")
        return dict(grant) if isinstance(grant, dict) else None

    # -- list_local_files --------------------------------------------------------------------------------------------

    def list_local_files(inp: LocalFilesIn) -> LocalFilesOut:
        from orchestrator.tools.local_files import LIST_PAGE, LocalFileError, LocalFiles
        try:
            files = LocalFiles.for_framework(framework)
            entries, folders, total = files.list(inp.kind, inp.query or "", inp.page)
        except LocalFileError as e:
            raise ToolError(str(e), code=e.code, next=e.next_step) from None
        return LocalFilesOut(files=[LocalFileOut(**e) for e in entries],
                             folders=[FolderOut(label=f.label, kind=f.kind) for f in folders], total=total,
                             page=inp.page, more=inp.page * LIST_PAGE < total)

    model_field = FieldMeta(kind="id")
    specs = [
        ToolSpec("search_hf_models", "Search Hugging Face models",
                 "Searches Hugging Face models, most downloaded first. By default (gated: free) only models that "
                 "download without accepting conditions are listed; gated=any includes gated models. Model ids are "
                 "external text.",
                 ToolTier.READ, SearchHfIn, SearchHfOut, search_hf_models, idempotent=True, open_world=True,
                 fields={"limit": FieldMeta(kind="amount"),
                         "gated": FieldMeta(kind="choice", choices=("free", "any", "gated"))},
                 examples=({"query": "qwen2.5 0.5b instruct gguf"}, {"query": "llama 3.2 1b", "gated": "any"}),
                 limits=ToolLimits(rate=SEARCH_RATE),
                 notes=lambda o: ([GATED_NOTE] if o.gated_filter == "free" else [])
                 + ([] if o.models else ["Nothing found on Hugging Face for this search."]),
                 summary=lambda o: count(len(o.models), "model", "models") + f" (filter {fact(o.gated_filter)}).",
                 paging_hint="Ask for fewer models (the 'limit' argument)."),
        ToolSpec("get_hf_model_info", "Hugging Face model details",
                 "Licence (with the base model's), gating, whether the person's stored Hugging Face token can read it "
                 "(checked inside the framework; the token never leaves it), parameter count, commit, files with sizes "
                 "and the total size of one model. Uses the stored token for any repository id.",
                 ToolTier.READ, ModelInfoIn, ModelInfoOut, get_hf_model_info, idempotent=True, open_world=True,
                 fields={"model": model_field, "revision": FieldMeta(kind="trust")},
                 examples=({"model": "Qwen/Qwen2.5-0.5B-Instruct-GGUF"},), limits=ToolLimits(rate=INFO_RATE),
                 notes=info_notes, paging_hint="The repository has too many files to return.",
                 summary=lambda o: (f"Model details at commit {fact(o.commit[:12])}: "
                                    + count(len(o.files), "file", "files") + f", gating {fact(o.gated)}.")),
        ToolSpec("download_hf_file", "Download a Hugging Face file",
                 "Downloads one file of a Hugging Face repository (a GGUF file, single weights, a tokenizer file) into "
                 "the models folder, pinned to the commit and checked against the Hub's sha256. Runs as a job: poll "
                 "get_job_status; its result has the file id for start_build. No size limit, but it must fit the "
                 "disk. Gated repositories need the person's stored token with access.",
                 ToolTier.LOCAL_WRITE, DownloadIn, DownloadStartedOut, download_hf_file, takes_context=True,
                 open_world=True, idempotent=True, prepare=prepare_download,
                 fields={"model": model_field, "filename": FieldMeta(kind="filename"),
                         "revision": FieldMeta(kind="trust")},
                 examples=({"model": "Qwen/Qwen2.5-0.5B-Instruct-GGUF", "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf"},),
                 limits=ToolLimits(per_day=5, concurrent=1, running=lambda ctx: active_downloads(), what="downloads"),
                 grant=GrantRule(rule=DOWNLOAD_RULE, facts=download_grant_facts,
                                 warning="download single files from Hugging Face (GGUF, safetensors or small text "
                                         "files of ungated repositories with a permissive licence)"),
                 summary=lambda o: f"Download job {fact(o.job_id)}: {fact(o.state)} (commit {fact(o.commit[:12])})."),
        ToolSpec("list_local_files", "List local model and dataset files",
                 "Model and dataset files the person put into the folders the AI may use (the models and datasets "
                 "folders and folders they added), with an id and a <label>:<relative path> reference for start_build. "
                 "Downloaded Hugging Face files show up here too. 50 per page; names are external text.",
                 ToolTier.READ, LocalFilesIn, LocalFilesOut, list_local_files, idempotent=True,
                 fields={"kind": FieldMeta(kind="choice", choices=("model", "dataset", "any"))},
                 examples=({}, {"kind": "model", "query": "qwen"}),
                 notes=lambda o: ([] if o.files else ["No matching file in the folders the AI may use; the person can "
                                                      "copy one into the models folder or add a folder with "
                                                      "'llm-cli tools folders add'."])
                 + (["More files follow: ask for the next page."] if o.more else []),
                 summary=lambda o: f"{count(len(o.files), 'local file', 'local files')} of {fact(o.total)}."),
    ]
    for spec in specs:
        registry.register(spec)
    return registry

