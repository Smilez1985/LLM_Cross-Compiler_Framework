#!/usr/bin/env python3
"""
How a build ends (DECISIONS round 40): one end reason per build, from a fixed vocabulary, and the end record the
stage runner writes as its very last step. Qt-free and import-light: the build engine, the orchestrator, the build
service, the job service, the chat feed's producer, the MQTT doorbell, the CLI and the GUI all read it from here.

End reasons (END_REASONS):
- done      the stage runner wrote its end record "status done" (after the last stage's marker and "Done."), the
            container's real exit status is 0, the record is sealed with this run's key and names the job's last
            stage (end_problem), and the framework finished the build (artifacts, manifest). The only reason that
            counts as success.
- failed    a stage failed, the end record says failed, the container exited 0 WITHOUT an end record ("the build
            ended without its end record": never a silent success), the record is not the stage runner's (a module
            script wrote it), it says done with an exit status other than 0 or before the last stage, or anything
            else went wrong.
- cancelled a person's or an AI's Cancel.
- soft_cap  an AI-started build ran past its soft cap (utils/step_limits.StepTimedOut kind "soft_cap").
- idle      a step printed nothing for the idle timeout (StepTimedOut kind "idle").

The old request statuses stay for old readers (STATUS_OF): done -> completed; failed, soft_cap, idle -> error;
cancelled -> cancelled.

The end record (targets/*/modules/build.sh): `$BUILD_SCRATCH_DIR/stages/<job>/build.end`, written to a temporary
file and moved into place, one "key value" line each: status (done|failed), exit, stage, job, inputs, time and seal.
A record that cannot be read, or that does not fit, counts as no record.

Only the stage runner writes it (a forged early done record). The module scripts share the work folder (in the build
sandbox under their own user, the stage account, so they cannot read the runner's memory: STAGE_USER_EXIT is the
runner's check of that), so the BuildEngine hands the runner a one-run key (END_KEY, `stages/<job>/end.key`, new_end_key) before
the container starts; the runner reads it into a variable it never exports and removes the file before its first
stage, and seals its record with it (seal_of: SHA-256 over key, record lines, key). A record without that seal was
not written by the runner. The real exit status of the container (the runner is its first process) is the second
check: a module cannot make the runner exit 0 before the last stage returned.
"""

import hashlib
import hmac
import os
import re
import secrets
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
END_KEY = "end.key"             # the BuildEngine's one-run key for the stage runner's seal (taken before any stage)
# The last stage of each job: a done record must name it (build.sh's STAGES; rotate runs before target)
LAST_STAGE = {"build": "target", "imatrix": "convert"}
CANCEL_NOTE = "cancelled"       # the framework's note to the stage runner: the running stage was a Cancel
RUNNING_NOTE = "running"        # the stage runner's own note: the stage that runs now
# A container that wrote "status done" exits by itself within this many seconds; then the framework stops it, and
# the build is still done (DECISIONS round 40: 30 s)
END_GRACE = 30.0
NO_RECORD_TEXT = "The build ended without its end record (build.end), so it is not counted as finished."
FOREIGN_RECORD_TEXT = ("The end record (build.end) was not written by the stage runner (a module script may have "
                       "written it), so the build is not counted as finished.")
EXIT_MISMATCH_TEXT = ("The end record says done, but the container exited with {code}, so the build is not counted "
                      "as finished.")
EARLY_RECORD_TEXT = ("The end record says done after the stage {stage}, before the last stage {last}, so the build is "
                     "not counted as finished.")
FAILED_RECORD_TEXT = "The end record says the build failed (stage {stage}, exit {exit})."

# The stage runner's check of the stage account (targets/*/modules/stage_user.sh stage_preflight): before its first
# stage, in the build sandbox, it refuses the build with one of these reasons as its exit code and an end record
# "failed" at the stage "preflight" (PREFLIGHT_STAGE). The fixed vocabulary, with the plain text the waiters show.
PREFLIGHT_STAGE = "preflight"
STAGE_USER_EXIT = {"rights_missing": 80, "rights_extra": 81, "no_new_privileges": 82, "same_account": 83,
                   "no_setpriv": 84, "switch_failed": 85, "stage_has_rights": 86, "reads_runner": 87, "folders": 88,
                   "inputs": 89, "no_layer": 90}
STAGE_USER_TEXT = {
    "rights_missing": "The stage runner lacked the rights to switch to the stage account (CAP_SETUID, CAP_SETGID).",
    "rights_extra": "The stage runner had rights beyond the switch to the stage account.",
    "no_new_privileges": "The stage runner ran without no-new-privileges.",
    "same_account": "The stage runner ran with the stage account's ids.",
    "no_setpriv": "The framework's runtime layer has no setpriv, so the stage runner cannot switch to the stage "
                  "account.",
    "no_layer": "The framework's runtime layer has no tools, so the stage runner cannot run without the image's "
                "programs.",
    "switch_failed": "The test switch to the stage account did not work.",
    "stage_has_rights": "The stage account still had capabilities after the switch.",
    "reads_runner": "A test child under the stage account could open the stage runner's memory or environment.",
    "folders": "The stage account cannot write a folder the stage modules write.",
    "inputs": "The stage account cannot read an input of the build (the model, the dataset, the importance matrix or "
              "the hardware profile); its files must be readable for your group.",
}
STAGE_USER_REFUSED = ("The build sandbox refused the build before its first stage: {text} No stage module ran; the "
                      "modules never run as the stage runner itself.")


def stage_user_problem(record: Optional["EndRecord"], exit_code: int) -> Optional[str]:
    """The plain reason of a build the stage runner refused in its check of the stage account (STAGE_USER_EXIT), from
    its end record (failed, stage preflight) and the container's exit status; None for any other end."""
    if record is None or record.done or record.stage != PREFLIGHT_STAGE:
        return None
    reason = next((name for name, code in STAGE_USER_EXIT.items() if code == exit_code), None)
    if reason is None:
        return None
    return STAGE_USER_REFUSED.format(text=STAGE_USER_TEXT[reason])


_STATUS = ("done", "failed")
_STAGE = re.compile(r"^(?:[a-z][a-z0-9_-]{0,31}|-)$")
_JOB = re.compile(r"^(?:build|imatrix)$")
_INPUTS = re.compile(r"^(?:[0-9a-f]{64}|-)$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SEAL = re.compile(r"^(?:[0-9a-f]{64}|-)$")
_KEY = re.compile(r"^[0-9a-f]{32,64}$")
MAX_RECORD_BYTES = 4096


@dataclass(frozen=True)
class EndRecord:
    """The stage runner's end record: status done|failed, its exit code, the stage (the failed one; for done the
    last one), the job, the inputs digest and the UTC time; `seal` as written, `sealed` whether it fits the key
    read_end_record was given (False without a key)."""
    status: str
    exit: int
    stage: str = "-"
    job: str = "build"
    inputs: str = "-"
    time: str = ""
    seal: str = "-"
    sealed: bool = False
    # How the end folder was guarded (the runtime layer's end folder): protected, unprotected or none
    end: str = "none"

    @property
    def done(self) -> bool:
        return self.status == "done"


def _linked(path: Path) -> bool:
    """Whether the record, its job folder or the stages folder is a link: the container writes the work folder, so a
    link there may point anywhere on this computer and is never followed."""
    return any(p.is_symlink() for p in (path, path.parent, path.parent.parent))


def record_body(status: str, code: int, stage: str, job: str, inputs: str, when: str) -> str:
    """The sealed part of a record: its six lines in their order, without the last newline (build.sh writes it so)."""
    return f"status {status}\nexit {code}\nstage {stage}\njob {job}\ninputs {inputs}\ntime {when}"


def seal_of(body: str, key: str) -> str:
    """The stage runner's seal of a record body: SHA-256 over key, newline, body, newline, key (build.sh computes
    the same with sha256sum). Without the key nobody can make a seal for another body."""
    return hashlib.sha256(f"{key}\n{body}\n{key}".encode("ascii")).hexdigest()


def new_end_key(work: Path, job: str = "build") -> Optional[str]:
    """Writes a fresh one-run key into the job folder of the work folder (END_KEY), for the stage runner to take
    before its first stage, and returns it; None when the folder is a link or cannot be written (the run then has no
    sealed record and does not count as done)."""
    stages = Path(work) / "stages"
    folder = stages / job
    try:
        if stages.is_symlink() or folder.is_symlink():
            return None
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / END_KEY
        if path.is_symlink() or path.is_dir():
            return None
        path.unlink(missing_ok=True)
        key = secrets.token_hex(16)
        # owner-only (0600): the stage account shares the runner's group in the build sandbox, never this file
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(key + "\n")
        return key
    except OSError:
        return None


def read_end_record(path: Path, key: Optional[str] = None) -> Optional[EndRecord]:
    """The end record at `path`, or None when there is none or it does not fit (a half-written or foreign file is no
    record). Never follows a link (the record, its job folder or the stages folder): the container writes them.
    With `key` (this run's, new_end_key) the record's `sealed` says whether the stage runner wrote it."""
    try:
        path = Path(path)
        if _linked(path) or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            return None
        text = path.read_text(encoding="ascii", errors="strict")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    values = {}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        values[name] = value.strip()
    status, code = values.get("status", ""), values.get("exit", "")
    if status not in _STATUS or not re.fullmatch(r"\d{1,3}", code):
        return None
    stage, job = values.get("stage", "-") or "-", values.get("job", "build") or "build"
    inputs, when = values.get("inputs", "-") or "-", values.get("time", "")
    seal = values.get("seal", "-") or "-"
    end = values.get("end", "none") or "none"
    if end not in ("protected", "unprotected", "none"):
        return None
    if not _STAGE.match(stage) or not _JOB.match(job) or not _INPUTS.match(inputs) or (when and not _TIME.match(when)):
        return None
    if not _SEAL.match(seal):
        return None
    sealed = bool(key and _KEY.match(key) and seal != "-"
                  and hmac.compare_digest(seal, seal_of(record_body(status, int(code), stage, job, inputs, when),
                                                        key)))
    return EndRecord(status, int(code), stage, job, inputs, when, seal, sealed, end)


def read_runner_record(end_dir: Path, owner_uid: Optional[int], *, platform_name: Optional[str] = None
                       ) -> "tuple[Optional[EndRecord], bool, Optional[str]]":
    """The stage runner's end record from the runtime layer's end folder (no key in the container): (record,
    protected, problem). The record counts as the runner's (`sealed`) when the end folder and the record belong to
    the runner alone (runtime_layer.end_folder_state) or, on a host whose mounts keep no owner or mode, as an
    unprotected end (protected False: the exit status and the inspect check decide). A folder or record that is not
    the runner's gives `problem` and no trusted record."""
    from dataclasses import replace
    from orchestrator.Core.runtime_layer import end_folder_state
    protected, problem = end_folder_state(Path(end_dir), owner_uid, platform_name=platform_name)
    record = read_end_record(Path(end_dir) / END_RECORD)
    if record is None:
        return None, protected, problem
    if problem is not None:
        return replace(record, sealed=False), False, problem
    if record.end == "unprotected":
        protected = False
    return replace(record, sealed=True), protected, None


def end_problem(record: Optional[EndRecord], exit_code: int, job: str = "build") -> Optional[str]:
    """None when a stage container ended as done, else the plain reason it did not: done needs the container's real
    exit status 0, a record sealed by the stage runner (read with this run's key), status done, this job and its
    last stage (LAST_STAGE). For an exit status other than 0 with a record that does not claim done (or none) it is
    None as well: the caller reports that exit code."""
    if record is None:
        return NO_RECORD_TEXT if exit_code == 0 else None
    if record.done and exit_code != 0:
        return EXIT_MISMATCH_TEXT.format(code=exit_code)
    if exit_code != 0:
        return None
    if not record.sealed or record.job != job:
        return FOREIGN_RECORD_TEXT
    if not record.done:
        return FAILED_RECORD_TEXT.format(stage=record.stage, exit=record.exit)
    last = LAST_STAGE.get(job, "target")
    if record.stage != last:
        return EARLY_RECORD_TEXT.format(stage=record.stage, last=last)
    return None


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
           "RUNNING_NOTE", "END_GRACE", "NO_RECORD_TEXT", "FOREIGN_RECORD_TEXT", "EXIT_MISMATCH_TEXT",
           "EARLY_RECORD_TEXT", "FAILED_RECORD_TEXT", "END_KEY", "LAST_STAGE", "PREFLIGHT_STAGE", "STAGE_USER_EXIT",
           "STAGE_USER_TEXT", "STAGE_USER_REFUSED", "stage_user_problem", "EndRecord", "read_end_record",
           "record_body", "seal_of", "new_end_key", "read_runner_record", "end_problem", "end_record_path",
           "write_cancel_note", "reason_of_timeout", "status_of", "event_of", "speaking_name", "reason_text",
           "EXIT_CODES", "EXIT_STILL_RUNNING", "exit_code"]
