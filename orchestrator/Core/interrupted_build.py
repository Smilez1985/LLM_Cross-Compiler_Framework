#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - An interrupted build: continue it, or delete what it left

A build that stopped before its end (a failure, a Cancel, a time limit, a crash of the framework's process) leaves its
folder `builds/<build id>` in the cache folder. When stages had finished there (the stage runner's markers
`work/stages/<job>/<stage>.done`) and its configuration is readable, the build is resumable: started again on the same
work folder, the runner skips every finished stage whose inputs are unchanged. Then the folder is KEPT and the person
is asked "continue the build, or delete what it left?":

- the GUI asks in a dialog (Build > Build History..., and right after the build ended in the Builder),
- the command line at a terminal (`llm-cli build start` after the build ended, `llm-cli build interrupted`),
- an AI caller (MCP, Ditto) never decides: its question becomes a pending decision (record_pending) that only the
  person answers in the GUI or at a terminal; it reads labels only (ai_view), never a path.

A build that is not resumable is discarded at once (discard_now): nothing in it can be used again.

Deleting what a kept build left goes only through Core/deletion.py: a DeletionPlan of one item (its folder, full path
for the person, its label for an AI), accepted by the person, checked again right before (require_same), audited
before anything goes, with an honest result (note_kept: what stayed, with the paths for the person only). Before it
deletes, a short clean-up container runs as the stage account (runtime_layer.CLEANUP_ENTRYPOINT, cleanup.sh) when the
folder holds files only that account could make removable (a module killed in the middle): no network, no
capability at all, no-new-privileges, the layer read-only, and its inspect is checked like every stage container
(utils/sandbox.sandbox_problem with caps=()).
"""

import json
import os
import re
import stat
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

DECISIONS = ("continue", "delete")
KIND = "failed_build"
PENDING_FILE = "interrupted_builds.json"
JOBS = ("build", "imatrix")
# A build id as the orchestrator makes them (the job id, a retry with _r<n>): a folder name, never a path
BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
QUESTION = "The build stopped with {done} kept. Continue it, or delete what it left?"
AI_NEXT = ("Only the person decides whether an interrupted build is continued or what it left is deleted: tell them "
           "the question waits for them in the framework's build history. Their answer is never yours to give.")
KEPT_WARNING = ("The build stopped after {done}; they are kept. Continue the build (its finished stages are not run "
                "again) or delete what it left.")
DISCARDED_NOTE = "Nothing of the build could be used again, so what it left was deleted."


class InterruptedBuildError(RuntimeError):
    """A decision that cannot be made: `code` needs_person (an AI asked), unknown_id, not_resumable, failed."""

    def __init__(self, text: str, code: str = "failed"):
        super().__init__(text)
        self.code = code


@dataclass
class KeptBuild:
    """One kept build: its id, target, the stages that had finished (fixed names only), its folder and size."""
    build_id: str
    target: str
    stages_done: List[str] = field(default_factory=list)
    folder: Path = Path()
    size_bytes: int = 0
    request_id: str = ""

    @property
    def label(self) -> str:
        return f"the interrupted build {self.build_id[:16]} of the target {self.target or 'unknown'}"

    def done_text(self) -> str:
        n = len(self.stages_done)
        return f"{n} finished stage{'s' if n != 1 else ''} ({', '.join(self.stages_done)})" if n else "no stage"

    def ai_view(self) -> Dict[str, Any]:
        """What an AI reads: labels, the fixed stage names and the size; never a path."""
        return {"build_id": self.build_id, "target": self.target, "stages_done": list(self.stages_done),
                "size_bytes": self.size_bytes, "decision": "pending: only the person decides", "next": AI_NEXT}

    def person_view(self) -> Dict[str, Any]:
        return {**self.ai_view(), "folder": str(self.folder), "question": QUESTION.format(done=self.done_text())}


def builds_root(cache_dir: Path) -> Path:
    return Path(cache_dir) / "builds"


def stages_done(folder: Path) -> List[str]:
    """The stages that finished in a build folder, by the runner's markers; only valid module names (a file someone
    put there with another name is not counted, so the names stay the fixed vocabulary)."""
    from orchestrator.Core.runtime_layer import MODULE_NAME
    names: List[str] = []
    for job in JOBS:
        stage_dir = Path(folder) / "work" / "stages" / job
        try:
            entries = sorted(stage_dir.glob("*.done"))
        except OSError:
            continue
        for marker in entries:
            name = marker.name[:-len(".done")]
            if MODULE_NAME.fullmatch(name) and name not in names and marker.is_file() and not marker.is_symlink():
                names.append(name)
    # in the order of a build: the four stages first, any slot module after them by name
    from orchestrator.Core.runtime_layer import FOUR_STAGES
    return sorted(names, key=lambda n: (FOUR_STAGES.index(n) if n in FOUR_STAGES else len(FOUR_STAGES), n))


def read_config(folder: Path) -> Optional[Dict[str, Any]]:
    """The build's configuration (build_config.json, written before its first stage), None when it is unreadable."""
    try:
        data = json.loads((Path(folder) / "build_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("build_id"), str) else None


def resumable(folder: Path) -> bool:
    """Whether a stopped build can be continued: a readable configuration of its own id, at least one finished stage,
    and no pinned local inputs of an AI caller (those are checked once, for the build that read them)."""
    config = read_config(folder)
    return (config is not None and config.get("build_id") == Path(folder).name and not config.get("local_inputs")
            and bool(stages_done(folder)))


def tree_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def kept_build(cache_dir: Path, build_id: str) -> Optional[KeptBuild]:
    if not BUILD_ID.fullmatch(str(build_id or "")) or ".." in str(build_id):
        return None
    folder = builds_root(cache_dir) / build_id
    if folder.is_symlink() or not folder.is_dir() or not resumable(folder):
        return None
    config = read_config(folder) or {}
    return KeptBuild(build_id, str(config.get("target_arch") or ""), stages_done(folder), folder, tree_size(folder),
                     str(config.get("request_id") or ""))


def kept_builds(cache_dir: Path, running: Sequence[str] = ()) -> List[KeptBuild]:
    """Every kept build in the cache folder that is not running now, newest first."""
    root = builds_root(cache_dir)
    found = []
    try:
        folders = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for folder in folders:
        if folder.name in running:
            continue
        item = kept_build(cache_dir, folder.name)
        if item is not None:
            found.append(item)
    return found


# --- the person's question and an AI's pending decision ---------------------------------------------------------

def _pending_path(state_dir: Path) -> Path:
    return Path(state_dir) / PENDING_FILE


def pending(state_dir: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """The decisions an AI caller asked for and only the person answers: build id -> {asked_by, time}."""
    if state_dir is None:
        return {}
    try:
        data = json.loads(_pending_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _write_pending(state_dir: Path, data: Dict[str, Dict[str, Any]]) -> None:
    from orchestrator.utils.atomic import write_private
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    write_private(_pending_path(state_dir), json.dumps(data, indent=2, sort_keys=True) + "\n")


def record_pending(state_dir: Path, item: KeptBuild, actor: str) -> Dict[str, Any]:
    """An AI caller asked about a kept build: the question waits for the person (once per build). Returns what the AI
    reads (labels only)."""
    data = pending(state_dir)
    if item.build_id not in data:
        data[item.build_id] = {"asked_by": str(actor)[:80],
                               "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_pending(state_dir, data)
    return item.ai_view()


def _settled(state_dir: Optional[Path], build_id: str) -> None:
    if state_dir is None:
        return
    data = pending(state_dir)
    if data.pop(build_id, None) is not None:
        _write_pending(state_dir, data)


def require_person(actor: str) -> None:
    from orchestrator.Core.deletion import person_actor
    if not person_actor(actor):
        raise InterruptedBuildError(AI_NEXT, code="needs_person")


# --- deleting what it left (Core/deletion.py) and the clean-up container -----------------------------------------

def deletion_plan(item: KeptBuild):
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    return plan_of([DeletionItem(kind=KIND, speaking_name=item.label, full_path=str(item.folder),
                                 file_name=item.folder.name, size_bytes=item.size_bytes,
                                 what=f"what an interrupted build left: {item.done_text()} and their intermediates"
                                 [:300])],
                   ("permanent",))


def needs_cleanup(folder: Path) -> bool:
    """Whether the folder holds something this account cannot remove by itself: an entry of another owner, or a
    folder it may not write or walk (what a stage module killed in the middle can leave)."""
    if os.name != "posix":
        return False
    me = os.getuid()
    for dirpath, dirs, files in os.walk(folder):
        for name in [*dirs, *files]:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if st.st_uid != me and not stat.S_ISLNK(st.st_mode):
                return True
        try:
            st = os.lstat(dirpath)
        except OSError:
            continue
        if stat.S_IMODE(st.st_mode) & 0o300 != 0o300:
            return True
    return False


def cleanup_container(runner_gid: int, folder: Path, cache_dir: Path) -> Dict[str, Any]:
    """The clean-up container's create() options: the stage account with the runner's group, no network, no
    capability at all, no-new-privileges, the image read-only, the runtime layer read-only at /llmcf, the build's
    work folder the only writable mount, and the framework's entrypoint and command."""
    from orchestrator.Core import runtime_layer as layer
    from orchestrator.utils.sandbox import STAGE_ACCOUNT, container_sandbox
    options = container_sandbox((STAGE_ACCOUNT[0], int(runner_gid)), working_dir="/")
    options["entrypoint"] = list(layer.CLEANUP_ENTRYPOINT)
    options["command"] = [str(int(runner_gid)), "/build-cache/work"]
    volumes = dict(layer.layer_volumes(Path(cache_dir), None))
    volumes[str(Path(folder) / "work")] = {"bind": "/build-cache/work", "mode": "rw"}
    options["volumes"] = volumes
    return options


def run_cleanup(client: Any, image: str, runner_gid: int, folder: Path, cache_dir: Path, engine: str = "docker",
                timeout: int = 300) -> Optional[str]:
    """Runs the clean-up container and removes it; None when it ran as decided and ended 0, else the plain reason
    (a container that differs from the decided sandbox never starts)."""
    from orchestrator.utils.docker_engine import create_container
    from orchestrator.utils.sandbox import sandbox_problem
    options = cleanup_container(runner_gid, folder, cache_dir)
    volumes = options["volumes"]
    container = create_container(client, image=image, detach=True, **options)   # the one helper for every container
    try:
        reload = getattr(container, "reload", None)
        if callable(reload):
            reload()
        problem = sandbox_problem(getattr(container, "attrs", None), options, volumes, engine=engine, caps=())
        if problem:
            return problem
        container.start()
        result = container.wait(timeout=timeout)
        code = int((result or {}).get("StatusCode", 1)) if isinstance(result, dict) else int(result or 0)
        return None if code == 0 else f"The clean-up container ended with exit status {code}."
    finally:
        try:
            container.remove(force=True)
        except Exception:  # noqa: BLE001 - our own container, by its id; nothing else is removed
            pass


def discard(item: KeptBuild, accepted: Any, *, audit: Any, state_dir: Optional[Path] = None,
            cleanup: Optional[Callable[[], Optional[str]]] = None):
    """Deletes what a kept build left, exactly as the person accepted it (Core/deletion.py): the plan built again
    first, audited before anything goes, the clean-up container first when the folder needs it, then an honest
    result (DeletionResult: done, freed bytes, what stayed with its paths for the person only)."""
    from orchestrator.Core.deletion import DeletionResult, note_kept, require_person as accepted_by_person
    from orchestrator.Core.deletion import require_same, same_items
    from orchestrator.utils.atomic import remove_tree
    accepted = accepted_by_person(accepted)
    require_same(accepted, same_items(deletion_plan(item), accepted.plan), item.label)
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    try:
        audit.record(accepted.actor, "interrupted_build", "framework", "person", "started", approved=True,
                     arguments={"build_id": item.build_id, "bytes": item.size_bytes, "mode": accepted.mode},
                     detail=f"Delete what {item.label} left", extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - its words could name a path
        raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
    result = DeletionResult()
    note = None
    if cleanup is not None and needs_cleanup(item.folder):
        note = cleanup()
    left = remove_tree(item.folder, label=item.label)
    if not note_kept(result, item.label, item.folder, left):
        result.done.append(item.label)
        result.freed_bytes += item.size_bytes
    if note and left:
        result.failed.append((item.label, f"the clean-up container could not make everything removable: {note}"))
    try:
        audit.record(accepted.actor, "interrupted_build", "framework", "person", "error" if left else "ok",
                     approved=True, arguments={"build_id": item.build_id},
                     detail=("kept: " + str(len(left)) + " items stayed") if left else "deleted",
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - what happened is in the result; its start is recorded
        pass
    if not left:
        _settled(state_dir, item.build_id)
    return result


def discard_now(folder: Path, cleanup: Optional[Callable[[], Optional[str]]] = None) -> List[Path]:
    """A build that cannot be continued: its own folder of intermediates goes at once (the framework's working
    folder, never a result the person kept), after the clean-up container when it needs one. Returns what stayed."""
    from orchestrator.utils.atomic import remove_tree
    folder = Path(folder)
    if not os.path.lexists(folder):
        return []
    if cleanup is not None and needs_cleanup(folder):
        cleanup()
    return remove_tree(folder, label="the build's working folder")


# --- continuing it ------------------------------------------------------------------------------------------------

def configuration(item: KeptBuild):
    """The BuildConfiguration the kept build ran with (its build id, so its work folder is the same)."""
    from orchestrator.Core.builder import BuildConfiguration
    from orchestrator.Core.build_formats import ModelFormat, OptimizationLevel
    data = read_config(item.folder)
    if data is None:
        raise InterruptedBuildError(f"The configuration of {item.label} is no longer readable.", "not_resumable")
    known = {f.name for f in fields(BuildConfiguration)}
    values = {k: v for k, v in data.items() if k in known and k != "local_inputs"}
    for key, kind in (("target_format", ModelFormat), ("source_format", ModelFormat),
                      ("optimization_level", OptimizationLevel)):
        if isinstance(values.get(key), str):
            raw = values[key]
            values[key] = next((m for m in kind if m.value == raw or str(m) == raw or m.name == raw.split(".")[-1]),
                               raw)
    return BuildConfiguration(**values)


def continue_build(engine: Any, item: KeptBuild, actor: str, state_dir: Optional[Path] = None) -> str:
    """The person's "continue": the same build, on the same work folder (the stage runner resumes every finished
    stage whose inputs are unchanged). Only the person; an AI's request is refused with needs_person."""
    require_person(actor)
    build_id = engine.build_model(configuration(item))
    _settled(state_dir, item.build_id)
    return build_id


def decide(engine: Any, cache_dir: Path, build_id: str, choice: str, actor: str, *,
           state_dir: Optional[Path] = None, accepted: Any = None, audit: Any = None,
           cleanup: Optional[Callable[[], Optional[str]]] = None):
    """The one way to answer the question: `continue` or `delete` (with the person's accepted deletion). An AI
    caller's answer is never taken: its question is recorded as pending and refused with needs_person."""
    if choice not in DECISIONS:
        raise InterruptedBuildError(f"Unknown choice {choice!r}: continue or delete.", "invalid_value")
    item = kept_build(cache_dir, build_id)
    if item is None:
        raise InterruptedBuildError(f"No interrupted build {str(build_id)[:40]} is kept.", "unknown_id")
    from orchestrator.Core.deletion import person_actor
    if not person_actor(actor):
        if state_dir is not None:
            record_pending(state_dir, item, actor)
        raise InterruptedBuildError(AI_NEXT, code="needs_person")
    if choice == "continue":
        return continue_build(engine, item, actor, state_dir)
    if accepted is None:
        raise InterruptedBuildError("Deleting what it left needs your yes to its list first.", "needs_person")
    return discard(item, accepted, audit=audit, state_dir=state_dir, cleanup=cleanup)


def ai_notes(framework: Any, request_id: str, actor: str) -> List[str]:
    """What an AI caller reads about the kept builds of a request (get_build_status): one note per build, labels and
    the fixed stage names only, and its question recorded as a pending decision for the person. Never raises."""
    try:
        from orchestrator.Core.runtime_layer import cache_dir_of
        config = getattr(framework, "config", None)
        if config is None or not request_id:
            return []
        resolve = getattr(framework, "resolve_path", None)
        cache = cache_dir_of(config, resolve if callable(resolve) else None)
        state = getattr(config, "user_state_dir", None)
        notes = []
        for item in kept_builds(cache):
            if item.request_id != request_id:
                continue
            if state:
                record_pending(Path(state), item, actor)
            notes.append(f"{item.label} stopped with {item.done_text()} kept ({item.size_bytes} bytes). {AI_NEXT}")
        return notes
    except Exception:  # noqa: BLE001 - a status read never fails over this
        return []


__all__ = ["ai_notes", "AI_NEXT", "DECISIONS", "DISCARDED_NOTE", "InterruptedBuildError", "KEPT_WARNING", "KIND", "KeptBuild",
           "QUESTION", "cleanup_container", "configuration", "continue_build", "decide", "deletion_plan", "discard",
           "discard_now", "kept_build", "kept_builds", "needs_cleanup", "pending", "read_config", "record_pending",
           "require_person", "resumable", "run_cleanup", "stages_done"]
