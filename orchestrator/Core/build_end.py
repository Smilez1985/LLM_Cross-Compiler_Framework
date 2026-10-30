#!/usr/bin/env python3
"""
How a build ends (DECISIONS round 40): one end reason per build, from a fixed vocabulary, and the end record the
stage runner writes as its very last step. Qt-free and import-light: the build engine, the orchestrator, the build
service, the job service, the chat feed's producer, the MQTT doorbell, the CLI and the GUI all read it from here.

End reasons (END_REASONS):
- done      the stage runner wrote its end record "status done" (after the last stage's marker and "Done."), and
            the framework finished the build (artifacts, manifest). The only reason that counts as success.
- failed    a stage failed, the end record says failed, the container exited 0 WITHOUT an end record ("the build
            ended without its end record": never a silent success), or anything else went wrong.
- cancelled a person's or an AI's Cancel.
- soft_cap  an AI-started build ran past its soft cap (utils/step_limits.StepTimedOut kind "soft_cap").
- idle      a step printed nothing for the idle timeout (StepTimedOut kind "idle").

The old request statuses stay for old readers (STATUS_OF): done -> completed; failed, soft_cap, idle -> error;
cancelled -> cancelled.

The end record (targets/*/modules/build.sh): `$BUILD_SCRATCH_DIR/stages/<job>/build.end`, written to a temporary
file and moved into place, one "key value" line each: status (done|failed), exit, stage, job, inputs, time. A
record that cannot be read, or that does not fit, counts as no record.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

END_REASONS = ("done", "failed", "cancelled", "soft_cap", "idle")
# The request status an old reader sees for each end reason (OrchestrationStatus values)
STATUS_OF = {"done": "completed", "failed": "error", "soft_cap": "error", "idle": "error", "cancelled": "cancelled"}
# The announced event of each end reason (the chat feed's row, the MQTT doorbell's payload) and its speaking name
EVENT_OF = {"done": "build_ready", "failed": "build_error", "soft_cap": "build_error", "idle": "build_error",
            "cancelled": "build_stopped"}
EVENTS = ("build_ready", "build_error", "build_stopped")
NAME_OF_EVENT = {"build_ready": "Build ready", "build_error": "Error while building", "build_stopped": "Build stopped"}
NAME_OF_EVENT_DE = {"build_ready": "Build fertig", "build_error": "Fehler beim Bauen", "build_stopped": "Build gestoppt"}

END_RECORD = "build.end"
CANCEL_NOTE = "cancelled"       # the framework's note to the stage runner: the running stage was a Cancel
RUNNING_NOTE = "running"        # the stage runner's own note: the stage that runs now
# A container that wrote "status done" exits by itself within this many seconds; then the framework stops it, and
# the build is still done (DECISIONS round 40: 30 s)
END_GRACE = 30.0
NO_RECORD_TEXT = "The build ended without its end record (build.end), so it is not counted as finished."

_STATUS = ("done", "failed")
_STAGE = re.compile(r"^(?:[a-z][a-z0-9_-]{0,31}|-)$")
_JOB = re.compile(r"^(?:build|imatrix)$")
_INPUTS = re.compile(r"^(?:[0-9a-f]{64}|-)$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_RECORD_BYTES = 4096


@dataclass(frozen=True)
class EndRecord:
    """The stage runner's end record: status done|failed, its exit code, the stage (the failed one; for done the
    last one), the job, the inputs digest and the UTC time."""
    status: str
    exit: int
    stage: str = "-"
    job: str = "build"
    inputs: str = "-"
    time: str = ""

    @property
    def done(self) -> bool:
        return self.status == "done"


def read_end_record(path: Path) -> Optional[EndRecord]:
    """The end record at `path`, or None when there is none or it does not fit (a half-written or foreign file is no
    record). Never follows a link: the container writes this folder."""
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            return None
        text = path.read_text(encoding="ascii", errors="strict")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        values[key] = value.strip()
    status, code = values.get("status", ""), values.get("exit", "")
    if status not in _STATUS or not re.fullmatch(r"\d{1,3}", code):
        return None
    stage, job = values.get("stage", "-") or "-", values.get("job", "build") or "build"
    inputs, when = values.get("inputs", "-") or "-", values.get("time", "")
    if not _STAGE.match(stage) or not _JOB.match(job) or not _INPUTS.match(inputs) or (when and not _TIME.match(when)):
        return None
    return EndRecord(status, int(code), stage, job, inputs, when)


def end_record_path(work: Path, job: str = "build") -> Path:
    """Where the stage runner of a container with this work folder (mounted as $BUILD_SCRATCH_DIR) writes it."""
    return Path(work) / "stages" / job / END_RECORD


def write_cancel_note(work: Path) -> int:
    """The framework's Cancel note for the stage runner (TASK 7): written into every job folder of the work folder in
    which a stage runs now, before the container is stopped. Returns how many were written; a folder that cannot be
    written is skipped (the stop then counts as an outside stop)."""
    written = 0
    stages = Path(work) / "stages"
    try:
        folders = [f for f in stages.iterdir() if f.is_dir() and not f.is_symlink()]
    except OSError:
        return 0
    for folder in folders:
        if not (folder / RUNNING_NOTE).is_file():
            continue
        try:
            (folder / CANCEL_NOTE).write_text("cancelled\n", encoding="ascii")
            written += 1
        except OSError:
            continue
    return written


def reason_of_timeout(error: BaseException) -> Optional[str]:
    """soft_cap or idle for a StepTimedOut anywhere in the exception's chain, else None."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        kind = getattr(error, "kind", None)
        if error.__class__.__name__ == "StepTimedOut" and kind in ("soft_cap", "idle"):
            return kind
        error = error.__cause__ or error.__context__
    return None


def status_of(reason: Optional[str]) -> Optional[str]:
    """The old request status of an end reason (None for none)."""
    return STATUS_OF.get(reason or "")


def event_of(reason: Optional[str]) -> Optional[str]:
    return EVENT_OF.get(reason or "")


def speaking_name(reason: Optional[str], lang: str = "en") -> str:
    """'Build ready', 'Error while building' or 'Build stopped' (German with lang 'de'); '' for no end."""
    event = event_of(reason)
    if not event:
        return ""
    return (NAME_OF_EVENT_DE if lang == "de" else NAME_OF_EVENT)[event]


# The plain words of each end reason for the waiters (CLI, GUI); EN and DE
REASON_TEXT = {
    "en": {"done": "Build ready: it finished and its end record says done.",
           "failed": "Error while building: a stage failed or the build ended without its end record.",
           "cancelled": "Build stopped: it was cancelled.",
           "soft_cap": "Error while building: an AI-started build ran past its soft cap (setting build_timeout) and "
                       "was stopped.",
           "idle": "Error while building: a step printed nothing for the idle timeout (setting build_idle_timeout) "
                   "and was stopped."},
    "de": {"done": "Build fertig: Er ist durchgelaufen, und sein Abschluss-Eintrag sagt fertig.",
           "failed": "Fehler beim Bauen: Eine Stufe ist fehlgeschlagen, oder der Build endete ohne seinen "
                     "Abschluss-Eintrag.",
           "cancelled": "Build gestoppt: Er wurde abgebrochen.",
           "soft_cap": "Fehler beim Bauen: Ein von einer KI gestarteter Build lief über seine weiche Grenze "
                       "(Einstellung build_timeout) und wurde gestoppt.",
           "idle": "Fehler beim Bauen: Ein Schritt hat so lange nichts ausgegeben wie die Leerlauf-Grenze "
                   "(Einstellung build_idle_timeout) und wurde gestoppt."},
}


def reason_text(reason: Optional[str], lang: str = "en") -> str:
    return REASON_TEXT.get(lang, REASON_TEXT["en"]).get(reason or "", "")


# Exit codes of the CLI's waits (`build start --follow`, `build status REQUEST --wait`)
EXIT_CODES = {"done": 0, "failed": 1, "soft_cap": 1, "idle": 1, "cancelled": 2}
EXIT_STILL_RUNNING = 3


def exit_code(reason: Optional[str]) -> int:
    return EXIT_CODES.get(reason or "", 1)


__all__ = ["END_REASONS", "STATUS_OF", "EVENT_OF", "EVENTS", "NAME_OF_EVENT", "END_RECORD", "CANCEL_NOTE",
           "RUNNING_NOTE", "END_GRACE", "NO_RECORD_TEXT", "EndRecord", "read_end_record", "end_record_path",
           "write_cancel_note", "reason_of_timeout", "status_of", "event_of", "speaking_name", "reason_text",
           "EXIT_CODES", "EXIT_STILL_RUNNING", "exit_code"]
