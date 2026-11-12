#!/usr/bin/env python3
"""
A generated module's build test: tested after its first passed target build, marked for rework after a failed one,
and repaired by hand or from its error log (DESIGN, targets: "A generated module is marked tested ...").

The state lives in a record beside the module's target.yml, `.lcf-module-test.json`, never in target.yml itself:
every file of a target folder counts in its content digest (the build image's inputs, the check for an identical
build, a recipe bound to the target, an installed pack's check against its signature), and the record's name is
reserved like the pack provenance file (community_pack.RESERVED_NAMES: never in a pack, never in the digest). The
marks `untested` and `provenance` of target.yml stay as the generator wrote them; only a run on the board changes
`untested`.

Only modules that are not shipped get a record (own, ai-generated, installed packs). Three states:

- untested: no build passed since the module was made or changed;
- tested: a build of exactly these files passed its target stage (the build's id, the date, the framework version
  and the files' digest);
- needs_rework: the newest build of these files failed in the module's own part (its build image, its stage modules).

The state shown is that of the files as they are: when the folder's digest differs from the build's, the module is
untested again (changed since that build). The record keeps the newest failed build's log (its last LOG_LINES lines,
secrets redacted) for the repair, a waiting repair proposal, and the history of results, repairs and edits.

A repair proposal (`propose_repair`, the pattern library first, then Ditto; an AI client's through
propose_healing_fix's `module_diff`, `store_client_proposal`) is a unified diff of the edit step's files, checked to
apply and free of dangerous commands; for a module made from the general template it may be changed values of its
template variables instead (`module_variables`), which become that diff by the edit step's own path
(module_edit.variables_diff) and are kept with it; it waits in the record until the person opens the edit step, where only the
person's Save writes it (Core/module_edit.py records the repair). Nothing here runs a command or saves a module file.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from orchestrator.Core.community_pack import MODULE_TEST_NAME
from orchestrator.schemas.build import BuildTestOut
from orchestrator.utils.logging import get_logger

RECORD_NAME = MODULE_TEST_NAME
FORMAT = "llmcf-module-test/1"
STATES = ("untested", "tested", "needs_rework")
LOG_LINES = 400          # of the newest failed build
LOG_LINE_CHARS = 2000
ERROR_CHARS = 500
HISTORY = 50
MAX_RECORD_BYTES = 4 * 1024 * 1024
# The build's steps that run the module's own files (BuildProgress.current_stage when it failed): its build image from
# its Dockerfile, the importance matrix and the stage modules.
MODULE_STEPS = ("Building Image", "Calculating IMatrix", "Running modules")
HOW = ("by hand", "proposal", "edit")

logger = get_logger(__name__)


# --- which modules, the record ----------------------------------------------------------------------------------------

def gets_marks(folder: Path) -> bool:
    """Whether a target folder gets a build test record: every target that is not shipped (own, ai-generated, an
    installed pack); never the template."""
    from orchestrator.Core.community_manager import own_target_trust, read_provenance
    folder = Path(folder)
    if folder.name.startswith("_") or not (folder / "target.yml").is_file():
        return False
    if read_provenance(folder) is not None:
        return True
    return own_target_trust(folder) != "shipped"


def _path(folder: Path) -> Path:
    return Path(folder) / RECORD_NAME


def read_record(folder: Path) -> Optional[Dict[str, Any]]:
    """The record of a module (None: none, a link, unreadable or not a record of this format)."""
    path = _path(folder)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("state") not in STATES:
        return None
    data.setdefault("history", [])
    return data


def _write(folder: Path, record: Dict[str, Any]) -> None:
    from orchestrator.utils.atomic import write_private
    path = _path(folder)
    if path.is_symlink():
        raise OSError(f"{path.name} is a link; the record is not written through it")
    record["history"] = list(record.get("history") or [])[-HISTORY:]
    write_private(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def _new(folder: Path) -> Dict[str, Any]:
    return {"format": FORMAT, "target": Path(folder).name, "state": "untested", "target_sha256": None,
            "tested": None, "rework": None, "proposal": None, "history": []}


def _now(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def _version() -> str:
    from orchestrator import __version__
    return __version__


def _digest(folder: Path) -> Optional[str]:
    from orchestrator.Core.community_manager import installed_digest
    try:
        return installed_digest(Path(folder))
    except Exception:  # noqa: BLE001 - a link or an unreadable file: the digest is unknown
        return None


def _first_line(text: Any, size: int = ERROR_CHARS) -> str:
    from orchestrator.utils.helpers import redact_secrets
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return redact_secrets(lines[0])[:size] if lines else ""


def _log_lines(lines: Iterable[str]) -> List[str]:
    from orchestrator.utils.helpers import redact_secrets
    out = []
    for line in list(lines or [])[-LOG_LINES:]:
        line = redact_secrets(str(line))
        out.append(line if len(line) <= LOG_LINE_CHARS else line[:LOG_LINE_CHARS] + " [... cut ...]")
    return out


# --- a build's result -------------------------------------------------------------------------------------------------

def module_failure(step: Optional[str], end_reason: Optional[str], log_text: str) -> bool:
    """Whether a failed build counts against its module: it failed (not cancelled, not stopped by a time limit) while
    the module's own part ran (MODULE_STEPS) and the pattern library does not trace its log to the host."""
    from orchestrator.Core.self_healing_manager import host_cause
    if end_reason != "failed" or step not in MODULE_STEPS:
        return False
    return host_cause(log_text) is None


def record_build(folder: Path, *, build_id: str, passed: bool, request_id: Optional[str] = None,
                 stage: Optional[str] = None, error: str = "", log: Iterable[str] = (),
                 target_sha256: Optional[str] = None, settings: Optional[Dict[str, Any]] = None,
                 now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Records a build of the module in `folder`: `passed` (its target stage passed, the board package made) marks
    it tested, otherwise it needs rework with the build's log (only call it for a failure module_failure counts).
    `target_sha256`: the digest of the files the build used (default: the folder now). None for a folder that gets
    no record (a shipped target); the record otherwise."""
    folder = Path(folder)
    if not gets_marks(folder):
        return None
    record = read_record(folder) or _new(folder)
    digest = target_sha256 or _digest(folder)
    date = _now(now)
    event: Dict[str, Any] = {"event": "passed" if passed else "failed", "build_id": str(build_id), "date": date,
                             "framework_version": _version()}
    if passed:
        if record.get("state") != "tested" or record.get("target_sha256") != digest:
            record["tested"] = {"build_id": str(build_id), "date": date, "framework_version": _version(),
                                "target_sha256": digest}
        record["state"] = "tested"
        record["rework"] = None
        record["proposal"] = None
    else:
        event["stage"] = stage or None
        event["error"] = _first_line(error)
        record["state"] = "needs_rework"
        record["rework"] = {"build_id": str(build_id), "request_id": request_id or None, "date": date,
                            "framework_version": _version(), "stage": stage or None, "error": _first_line(error),
                            "log": _log_lines(log), "target_sha256": digest,
                            "settings": {k: v for k, v in (settings or {}).items() if v not in (None, "")}}
        record["proposal"] = None   # a proposal for an older failure no longer fits this log
    record["target_sha256"] = digest
    record["history"].append(event)
    _write(folder, record)
    return record


def record_change(folder: Path, *, files: List[str], how: str = "edit", by: str = "person",
                  summary: str = "") -> Optional[Dict[str, Any]]:
    """Records a repair or an edit saved through the edit step: the module is untested until a build passes (its
    files changed, so the digest no longer matches either). Only for a module that has a record already or is
    repaired; None otherwise."""
    folder = Path(folder)
    if not files or not gets_marks(folder):
        return None
    record = read_record(folder)
    if record is None and how == "edit":
        return None   # a module never built: nothing to record yet, it is untested anyway
    record = record or _new(folder)
    failed = (record.get("rework") or {}).get("build_id")
    record["history"].append({"event": "repair" if how in ("by hand", "proposal") else "edit",
                              "how": how if how in HOW else "edit", "by": str(by or "person")[:80],
                              "files": [str(f) for f in files][:50], "date": _now(),
                              "after_build": failed, "summary": _first_line(summary, 300) or None})
    record["state"] = "untested"
    record["proposal"] = None
    _write(folder, record)
    return record


# --- the state as it is shown ----------------------------------------------------------------------------------------

def state_of(folder: Path) -> Optional[BuildTestOut]:
    """The build test of the module in `folder` as its files are now; None for a folder that gets no record."""
    folder = Path(folder)
    if not gets_marks(folder):
        return None
    record = read_record(folder)
    if record is None:
        out = BuildTestOut(state="untested")
        out.line = line_of(out)
        return out
    state = record["state"]
    tested, rework = record.get("tested") or {}, record.get("rework") or {}
    history = [h for h in record.get("history") or [] if isinstance(h, dict)]
    builds = [h for h in history if h.get("event") in ("passed", "failed")]
    changed_since = None
    if state in ("tested", "needs_rework") and record.get("target_sha256"):
        if _digest(folder) != record["target_sha256"]:
            changed_since = builds[-1].get("build_id") if builds else None
            state = "untested"
    elif state == "untested" and builds:
        changed_since = builds[-1].get("build_id")
    out = BuildTestOut(
        state=state, tested_build=tested.get("build_id"), tested_date=tested.get("date"),
        tested_version=tested.get("framework_version"), failed_build=rework.get("build_id"),
        failed_request=rework.get("request_id"), failed_date=rework.get("date"),
        failed_stage=rework.get("stage"), failed_error=rework.get("error") or None, changed_since=changed_since,
        repairs=sum(1 for h in history if h.get("event") in ("repair", "edit")),
        proposal_waiting=bool(record.get("proposal")) and state == "needs_rework")
    out.line = line_of(out)
    return out


def line_of(test: Optional[BuildTestOut]) -> str:
    """The state as one line (`llm-cli targets list`, the AI's listing); "" for a shipped target."""
    if test is None:
        return ""
    if test.state == "tested":
        return (f"tested: build {test.tested_build} passed on {(test.tested_date or '')[:10]} "
                f"(llmcf {test.tested_version})")
    if test.state == "needs_rework":
        where = f" in stage {test.failed_stage}" if test.failed_stage else ""
        waiting = "; a repair proposal waits" if test.proposal_waiting else ""
        return f"needs rework: build {test.failed_build} failed{where} on {(test.failed_date or '')[:10]}{waiting}"
    if test.changed_since:
        return f"untested: changed since build {test.changed_since}" + (
            f" ({test.repairs} repairs or edits)" if test.repairs else "")
    return "untested: not built yet"


def failed_log(folder: Path) -> List[str]:
    """The newest failed build's log lines kept in the record (secrets redacted; the person's own view)."""
    record = read_record(folder) or {}
    return list((record.get("rework") or {}).get("log") or [])


def retry_settings(folder: Path) -> Dict[str, Any]:
    """The failed build's settings (model, format, quantization, board) to start it again; {} without one."""
    record = read_record(folder) or {}
    settings = (record.get("rework") or {}).get("settings")
    return dict(settings) if isinstance(settings, dict) else {}


def retry_command(target: str, settings: Dict[str, Any]) -> str:
    """The `llm-cli build start` line that builds the failed build's settings again."""
    import shlex
    parts = ["llm-cli", "build", "start", "--target", str(target)]
    for key, flag in (("model", "--model"), ("format", "--format"), ("board", "--board"),
                      ("quantization", "--quantization")):
        if settings.get(key):
            parts += [flag, str(settings[key])]
    return " ".join(shlex.quote(p) for p in parts)


def repair_files(folder: Path) -> List[str]:
    """The edit step's files in the order a repair reads them: the failed stage's module (the Dockerfile for a failed
    build image) first, then target.yml and the Dockerfile, then the rest; a reader with a size limit keeps the
    first ones."""
    from orchestrator.Core.module_edit import editable_files
    files = editable_files(Path(folder))
    stage = str(((read_record(folder) or {}).get("rework") or {}).get("stage") or "")
    first = ["Dockerfile"] if stage in ("Building Image", "image") else [f"modules/{stage}_module.sh"]
    order = first + ["target.yml", "Dockerfile"]
    return sorted(files, key=lambda rel: (order.index(rel) if rel in order else len(order), files.index(rel)))


# --- repair proposals -----------------------------------------------------------------------------------------------

class RepairError(ValueError):
    """No proposal: no failed build to repair from, a diff that does not fit, or no analysis available."""


def check_diff(folder: Path, diff: str) -> List[str]:
    """Checks a proposed diff against the module's files as they are: it must apply (RepairError otherwise) and name
    no dangerous command; returns the problems of the folder's checks with the diff applied (shown with it)."""
    from orchestrator.Core.ditto_manager import MODULE_DIFF_CHARS, dangerous_command
    from orchestrator.Core.module_edit import ModuleEdit
    if len(diff or "") > MODULE_DIFF_CHARS:
        raise RepairError(f"the diff is longer than {MODULE_DIFF_CHARS} characters")
    danger = dangerous_command(diff)
    if danger:
        raise RepairError(f"the diff contains a dangerous command ({danger}); it was discarded")
    edit = ModuleEdit(Path(folder))
    try:
        if not edit.apply_diff(diff):
            raise RepairError("the diff changes nothing in the module's files")
    except ValueError as e:
        raise RepairError(f"the diff does not fit the module's files: {e}") from None
    return edit.problems()


def _store_proposal(folder: Path, record: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    record["proposal"] = proposal
    _write(folder, record)
    return proposal


def _needs_rework(folder: Path) -> Dict[str, Any]:
    record = read_record(folder)
    test = state_of(folder)
    if record is None or test is None or test.state != "needs_rework" or not record.get("rework"):
        raise RepairError(f"{Path(folder).name}: no failed build to repair from "
                          f"({line_of(test) or 'a shipped target'})")
    return record


def propose_repair(framework: Any, folder: Path) -> Dict[str, Any]:
    """The self-healing analysis of the failed build for the module: the pattern library first (a known cause gives
    its step as text, no diff), then, for a module made from the general template, Ditto's proposal of changed
    template values (_values_repair), else Ditto (the log cleaned, scrubbed and wrapped as untrusted text, the module's
    files as untrusted text) with a unified diff, checked by check_diff. The proposal waits in the record for the
    edit step; RepairError when there is none."""
    from orchestrator.Core.ditto_manager import DittoError
    from orchestrator.Core.module_edit import ModuleEdit
    from orchestrator.Core.self_healing_manager import SelfHealingManager
    folder = Path(folder)
    record = _needs_rework(folder)
    rework = record["rework"]
    log_text = "\n".join(rework.get("log") or [])
    base = {"build_id": rework.get("build_id"), "created": _now(), "problems": []}
    known = SelfHealingManager(framework)._analyze_heuristics(log_text)
    if known is not None:
        return _store_proposal(folder, record, dict(base, by="pattern library", summary=known.summary,
                                                     root_cause=known.root_cause, step=known.fix_command, diff="",
                                                     confidence=known.confidence))
    get = getattr(framework, "get_component", None)
    ditto = get("ditto_manager") if callable(get) else getattr(framework, "ditto_manager", None)
    if ditto is None or not hasattr(ditto, "propose_module_repair"):
        raise RepairError("The pattern library knows no cause, and Ditto is not available for an analysis: repair "
                          "the module by hand in the edit step (the failed build's error and log are shown there).")
    edit = ModuleEdit(folder)
    context = (f"Target module {folder.name}; the build {rework.get('build_id')} failed"
               + (f" in stage {rework['stage']}" if rework.get("stage") else "") + ".")
    by_values = _values_repair(ditto, folder, edit, log_text, base)
    if by_values is not None:
        return _store_proposal(folder, record, by_values)
    try:
        answer = ditto.propose_module_repair(log_text, {rel: edit.text(rel) for rel in repair_files(folder)},
                                             context)
    except DittoError as e:
        raise RepairError(str(e)) from None
    problems = check_diff(folder, answer.diff) if answer.diff.strip() else []
    return _store_proposal(folder, record, dict(base, by="ditto", summary=answer.summary,
                                                 root_cause=answer.root_cause, step="", diff=answer.diff,
                                                 confidence=answer.confidence, problems=problems))


def _values_repair(ditto: Any, folder: Path, edit: Any, log_text: str, base: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A module made from the general template is repaired by its variables first: Ditto's proposal of the values
    (Core/format_proposal.py) with the module's current values and the failed build's log; the changed values become
    the variables proposal (the edit step's own path makes them into the diff of its files). None when the module
    records no variables, Ditto cannot propose values, or it changes none (the diff of the files is asked then)."""
    from orchestrator.Core.ditto_manager import DittoError
    from orchestrator.Core.module_edit import variables_diff
    text = edit.variables()
    if text is None or not hasattr(ditto, "_structured"):
        return None
    try:
        record = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    formats = record.get("formats") if isinstance(record, dict) else record
    if not isinstance(formats, list) or not formats:
        return None
    try:
        from orchestrator.Core.format_proposal import propose
        proposal = propose(ditto, current=formats, log_content=log_text)
    except (DittoError, OSError, ValueError):
        return None
    changed = [f"{d.id}.{path}" for d in proposal.drafts for path in d.changed]
    if not changed:
        return None
    by_id = {d.id: d.values for d in proposal.drafts}
    new_formats = [by_id.get(str(f.get("id")), f) if isinstance(f, dict) else f for f in formats]
    new_text = yaml.safe_dump({**record, "formats": new_formats} if isinstance(record, dict) else new_formats,
                              sort_keys=False, allow_unicode=True)
    try:
        diff = variables_diff(folder, new_text)
    except ValueError as e:
        problems = [f"the proposed values do not fit the module: {e}"]
        diff = ""
    else:
        problems = check_diff(folder, diff) if diff.strip() else []
    notes = [note.line() for d in proposal.drafts for path, note in d.notes.items() if path in d.changed]
    return dict(base, by="ditto", summary=proposal.summary or f"Changed template values: {', '.join(changed)}",
                root_cause="", step="", diff=diff, variables=new_text, value_notes=notes[:40],
                confidence=min([n.confidence for d in proposal.drafts for p, n in d.notes.items()
                                if p in d.changed] or [0.0]), problems=problems)


def store_client_proposal(folder: Path, *, request_id: str, diff: str, summary: str, root_cause: str = "",
                          confidence: float = 0.0, by: str = "ai client", variables: str = "") -> Dict[str, Any]:
    """An AI client's repair for the module's failed build (propose_healing_fix's module_diff, or its
    module_variables: changed values of the module's template variables, made into the diff of its files by the edit
    step's own path): the build of `request_id` must be the one the module needs rework for; the diff is checked like
    Ditto's and waits in the record. RepairError otherwise."""
    from orchestrator.Core.ditto_manager import dangerous_command
    folder = Path(folder)
    record = _needs_rework(folder)
    if record["rework"].get("request_id") != request_id:
        raise RepairError(f"{folder.name} needs rework for another build than request {request_id}")
    danger = dangerous_command(summary, root_cause)
    if danger:
        raise RepairError(f"the proposal contains a dangerous command ({danger}); it was discarded")
    if variables and str(variables).strip():
        if diff and diff.strip():
            raise RepairError("propose either changed variables or a diff of the files, not both")
        from orchestrator.Core.module_edit import variables_diff
        try:
            diff = variables_diff(folder, variables)
        except ValueError as e:
            raise RepairError(f"the variables do not fit the module: {e}") from None
    problems = check_diff(folder, diff)
    proposal = {"build_id": record["rework"].get("build_id"), "created": _now(), "by": str(by)[:80],
                "summary": summary, "root_cause": root_cause, "step": "", "diff": diff,
                "confidence": float(confidence), "problems": problems}
    if variables and str(variables).strip():
        proposal["variables"] = str(variables)
    return _store_proposal(folder, record, proposal)


def waiting_proposal(folder: Path) -> Optional[Dict[str, Any]]:
    """The repair proposal waiting for the person (None: none, or the module no longer needs rework)."""
    test = state_of(folder)
    record = read_record(folder) or {}
    proposal = record.get("proposal")
    return dict(proposal) if isinstance(proposal, dict) and test is not None and test.state == "needs_rework" else None


def repair_view(folder: Path) -> str:
    """The failed build as the edit step shows it next to the files: id, date, stage, error, the waiting proposal's
    summary and the end of the log (the person's own view: redacted secrets, real paths)."""
    test = state_of(folder)
    record = read_record(folder) or {}
    rework = record.get("rework") or {}
    if test is None or not rework:
        return ""
    lines = [line_of(test)]
    if rework.get("error"):
        lines.append(f"Error: {rework['error']}")
    proposal = waiting_proposal(folder)
    if proposal:
        lines.append(f"Proposal ({proposal.get('by')}): {proposal.get('summary')}")
        if proposal.get("root_cause"):
            lines.append(f"Cause: {proposal['root_cause']}")
        if proposal.get("step"):
            lines.append(f"Step: {proposal['step']}")
        for problem in proposal.get("problems") or []:
            lines.append(f"Check: {problem}")
    tail = list(rework.get("log") or [])[-40:]
    if tail:
        lines.append("End of the log:")
        lines += tail
    return "\n".join(lines)


def ai_log(folder: Path, roots: Optional[Dict[str, str]] = None) -> str:
    """The failed build's log as an AI reads it (log_for_ai: cleaned, scrubbed, the end only)."""
    from orchestrator.llm.task_prompts import log_for_ai
    return log_for_ai("\n".join(failed_log(folder)), roots=roots)


def safe_record_build(folder: Optional[Path], **kwargs) -> Optional[Dict[str, Any]]:
    """record_build that never stops the caller (a build's end): a record that cannot be written is logged."""
    if folder is None:
        return None
    try:
        return record_build(Path(folder), **kwargs)
    except Exception as e:  # noqa: BLE001 - the build's outcome stands; only its mark is missing
        logger.warning(f"The build test of module {Path(folder).name} was not recorded: {e}")
        return None


__all__ = ["FORMAT", "MODULE_STEPS", "RECORD_NAME", "RepairError", "ai_log", "check_diff", "failed_log",
           "gets_marks", "line_of", "module_failure", "propose_repair", "read_record", "record_build",
           "record_change", "repair_files", "repair_view", "retry_command", "retry_settings", "safe_record_build",
           "state_of",
           "store_client_proposal", "waiting_proposal"]
