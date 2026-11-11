#!/usr/bin/env python3
"""
The PC's time for a board without a battery-backed clock (DESIGN, Installer, platforms and dependencies: "Time on a
board without a clock").

A board that is offline and has no working RTC cannot know the time by itself; llmcf never needs the right time for
what it checks (hashes and signatures without validity dates), but the board's logs and files would carry a wrong
date. The PC that reaches the board has the time:

- at every contact over the session that is already open (no second login), the read-only gate operation clock-read
  answers the board's UTC time, its RTC and its time keeper (read(); the PC's time is the middle of that round trip);
- off by more than TOLERANCE_S seconds, the framework sets it only with the person's standing yes for that board (the
  deploy setting clock_from_pc, asked once after a pairing in the person's channels; no AI gives it): the gate
  operation clock-root runs llmcf-install's typed action `clock STAMP` (a checked UTC time stamp, never a shell
  string) as root through its sudo rule only (set_clock). Every set, also one that failed, goes into the audit log.
  Without the yes, or without the board's root readiness, a warning names the offset and nothing is set;
- the person's answer (asked once after a pairing) and the set by hand are the person's steps
  (runtime/device/person_steps.py ask_clock_once, change_clock_answer, set_clock_by_hand), which no AI reaches;
- after a bundle's root apply, within the person's yes for it, `clock keep` sets up keeping the last known time
  across a restart when the board has no working RTC (keep_clock: fake-hwclock, else systemd-timesyncd's clock file);
- the board's clock record (<state>/hardware/devices/<id>.clock.json) keeps the last reading and the last set, for
  the CLI's `device clock` and the Boards window.

on_contact() is what every path that reaches a stored board calls after its login; it never fails the caller's work.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from orchestrator.remote import gate
from orchestrator.remote.gate import define

logger = logging.getLogger(__name__)

TOLERANCE_S = 60
# An RTC that holds a time before this (2025-01-01T00:00:00Z) was reset (no battery): no working RTC
# (rust/llmcf-install/src/clock.rs RTC_FLOOR).
RTC_FLOOR = 1735689600
READ_TIMEOUT = 20.0
ROOT_TIMEOUT = 60.0
AUDIT_TOOL = "board_clock_set"
KEEP_AUDIT_TOOL = "board_clock_keep"
CLOCK_QUESTION = ("May the framework set the clock of board {board} from this computer's time whenever it is off by "
                  "more than a minute? A board without a battery-backed clock that is offline cannot know the time; it "
                  "sets it as root through llmcf-install's clock action only (it needs the board's root readiness), "
                  "and writes every set to the audit log. You can change this in the board's settings.")
SET_QUESTION = ("Set the clock of board {board} to this computer's time now ({stamp} UTC), as root through "
                "llmcf-install's clock action? It is off by {offset}.")

# --- the operation that reads the clock (no root, changes nothing) ----------------------------------------------------

READ_SCRIPT = "\n".join([
    'printf "now=%s\\n" "$(date -u +%s)"',
    "if [ -d /sys/class/rtc/rtc0 ]; then llmcf_e=$(cat /sys/class/rtc/rtc0/since_epoch 2>/dev/null); "
    'case $llmcf_e in ""|*[!0-9]*) echo rtc=unreadable ;; *) echo "rtc=$llmcf_e" ;; esac; else echo rtc=none; fi',
    "if [ -x /usr/sbin/fake-hwclock ] || [ -x /sbin/fake-hwclock ]; then echo keeper=fake-hwclock; "
    "elif [ -d /run/systemd/system ] && systemctl is-active --quiet systemd-timesyncd 2>/dev/null; then "
    "echo keeper=timesyncd; else echo keeper=none; fi",
    'if command -v timedatectl >/dev/null 2>&1; then printf "sync=%s\\n" '
    '"$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"; fi',
])
define("clock-read", (), lambda rest="": READ_SCRIPT,
       doc="the board's UTC time, its RTC (/sys/class/rtc/rtc0) and its time keeper (reads only)")


def stamp(seconds: float) -> str:
    """A UTC time stamp YYYY-MM-DDTHH:MM:SSZ of `seconds` since 1970."""
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def span_text(seconds: int) -> str:
    """A span of time in plain units: "2 d 3 h", "3 h 12 min", "4 min 5 s", "45 s" (the same in both languages)."""
    span = abs(int(seconds))
    days, rest = divmod(span, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def offset_text(seconds: Optional[int]) -> str:
    """An offset in plain words: "3 h 12 min ahead", "45 s behind"."""
    if seconds is None:
        return "unknown"
    return span_text(seconds) + (" ahead" if seconds > 0 else " behind" if seconds < 0 else "")


@dataclass(frozen=True)
class ClockReading:
    """One reading of a board's clock: its time (`board`, seconds since 1970), the PC's at the same moment (`pc`), the
    offset (board minus PC, whole seconds), the RTC (yes: working, reset: holds a time before 2025, none, unreadable),
    its time keeper (fake-hwclock, timesyncd, none) and whether NTP synchronized it (yes, no, "" unknown)."""
    board: int
    pc: float
    rtc: str
    rtc_time: Optional[int] = None
    keeper: str = "none"
    sync: str = ""

    @property
    def offset(self) -> int:
        return int(round(self.board - self.pc))

    @property
    def off(self) -> bool:
        return abs(self.offset) > TOLERANCE_S

    @property
    def working_rtc(self) -> bool:
        return self.rtc == "yes"


def parse_reading(text: str, pc: float) -> Optional[ClockReading]:
    """clock-read's answer as a ClockReading (None when it holds no time)."""
    values: Dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key in ("now", "rtc", "keeper", "sync") and key not in values:
            values[key] = value.strip()
    now = values.get("now", "")
    if not re.fullmatch(r"[0-9]{1,12}", now):
        return None
    rtc_raw, rtc_time = values.get("rtc", "none"), None
    if re.fullmatch(r"[0-9]{1,12}", rtc_raw):
        rtc_time = int(rtc_raw)
        rtc = "yes" if rtc_time >= RTC_FLOOR else "reset"
    else:
        rtc = rtc_raw if rtc_raw in ("none", "unreadable") else "none"
    keeper = values.get("keeper", "none")
    sync = values.get("sync", "")
    return ClockReading(int(now), pc, rtc, rtc_time, keeper if keeper in ("fake-hwclock", "timesyncd") else "none",
                        sync if sync in ("yes", "no") else "")


def read(run: Callable[..., Any], now: Callable[[], float] = time.time) -> Optional[ClockReading]:
    """clock-read over `run` (a board session's run): None when the board did not answer one (an older gate, no date
    program). The PC's time is the middle of the round trip."""
    before = now()
    result = run(gate.script("clock-read"), timeout=READ_TIMEOUT)
    after = now()
    if int(getattr(result, "status", 1)) != 0:
        return None
    return parse_reading(getattr(result, "out", ""), (before + after) / 2.0)


# --- llmcf-install's clock action (as root, through its sudo rule only) -----------------------------------------------

@dataclass(frozen=True)
class ClockRun:
    """What llmcf-install's clock action answered: `result` ok, unavailable, not-ready, refused, failed (or "" when
    nothing came back), `detail` the rest of its last line, `values` its key=value words (set, saved, keep, ...)."""
    result: str
    detail: str = ""
    values: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result == "ok"


def parse_run(text: str) -> ClockRun:
    last = ""
    for line in (text or "").splitlines():
        if line.startswith("llmcf-bundle: result="):
            last = line[len("llmcf-bundle: "):]
    if not last:
        lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
        return ClockRun("", (lines[-1] if lines else "llmcf-install gave no answer")[:200])
    words = last.split(" ")
    result = words[0].partition("=")[2]
    values = {}
    rest = []
    for w in words[1:]:
        key, sep, value = w.partition("=")
        if sep and re.fullmatch(r"[a-z_]{1,20}", key) and key not in values:
            values[key] = value
        else:
            rest.append(w)
    return ClockRun(result, " ".join(rest)[:300], values)


def set_clock(run: Callable[..., Any], when: float) -> ClockRun:
    """llmcf-install `clock STAMP` with the PC's time `when` (clock-root: sudo -n through its sudo rule only)."""
    result = run(gate.script("clock-root", stamp(when)), timeout=ROOT_TIMEOUT)
    return parse_run(getattr(result, "out", "") + "\n" + getattr(result, "err", ""))


def keep_clock(run: Callable[..., Any]) -> ClockRun:
    """llmcf-install `clock keep`: keeping the last known time across a restart on a board without a working RTC."""
    result = run(gate.script("clock-root", "keep"), timeout=ROOT_TIMEOUT)
    return parse_run(getattr(result, "out", "") + "\n" + getattr(result, "err", ""))


# --- the board's clock record and its state ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClockState:
    """What a contact found and did: the reading (None: not read), whether it set the clock (`set_run`) and the
    warning for the person ("" when there is none)."""
    reading: Optional[ClockReading]
    set_run: Optional[ClockRun] = None
    warning: str = ""

    @property
    def set_ok(self) -> bool:
        return self.set_run is not None and self.set_run.ok


def _store(state_dir: Path):
    from orchestrator.runtime.profile import ProfileStore
    return ProfileStore(Path(state_dir))


def load_record(state_dir: Path, device_id: str) -> Dict[str, Any]:
    """The board's clock record ({} when there is none)."""
    try:
        return _store(state_dir).load_clock(device_id)
    except ValueError:
        return {}


def save_record(state_dir: Path, device_id: str, changes: Dict[str, Any]) -> None:
    try:
        store = _store(state_dir)
        record = store.load_clock(device_id)
        record.update({k: v for k, v in changes.items() if v is not None})
        store.save_clock(device_id, record)
    except (OSError, ValueError) as e:
        logger.debug("The clock record of board %s was not written: %s", device_id, e)


def reading_record(reading: ClockReading) -> Dict[str, Any]:
    return {"read_at": stamp(reading.pc), "offset_s": reading.offset, "rtc": reading.rtc,
            "keeper": reading.keeper, "sync": reading.sync or "unknown", "warning": ""}


def clock_yes(state_dir: Path, device_id: str) -> Optional[bool]:
    """The person's standing yes for this board (True), their no (False), or None when never asked or the deploy
    settings cannot be read."""
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    try:
        settings = DeploySettings.from_dict(_store(state_dir).load_deploy_settings(device_id))
    except (SettingsError, ValueError, OSError):
        return None
    if settings.clock_from_pc is None:
        return None
    return settings.clock_yes


def default_audit(state_dir: Path):
    """The per-user audit log (audit.db opened with its key, as the person's channels open it without a booted
    framework); None when it cannot be opened (the set itself is in the framework's log)."""
    try:
        from orchestrator.Core.vault_key import audit_key
        from orchestrator.tools.audit import AuditLog
        return AuditLog(Path(state_dir) / "audit.db", key=audit_key(None, Path(state_dir)))
    except Exception:  # noqa: BLE001 - the record is still written
        return None


def _audit(audit: Any, tool: str, device_id: str, *, via: str, actor: str, outcome: str, arguments: Dict[str, Any],
           detail: str) -> None:
    if audit is None or not callable(getattr(audit, "record", None)):
        return
    try:
        audit.record(actor, tool, "external", "ask", outcome, arguments=dict(arguments, device_id=device_id),
                     approved=True, detail=detail, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
    except Exception:  # noqa: BLE001 - logged by the audit log itself
        pass


def record_set(state_dir: Path, device_id: str, reading: Optional[ClockReading], done: ClockRun, *, via: str,
               audit: Any, actor: str, when: float, reason: str) -> None:
    """Writes a set (also one that failed) into the audit log and the board's clock record."""
    offset = reading.offset if reading is not None else None
    _audit(audit, AUDIT_TOOL, device_id, via=via, actor=actor, outcome="ok" if done.ok else "error",
           arguments={"time": stamp(when), "offset_s": offset, "reason": reason},
           detail=f"llmcf-install clock: result={done.result or 'none'} {done.detail}".strip())
    changes: Dict[str, Any] = {"last_set_result": done.result or "none"}
    if done.ok:
        changes.update({"set_at": stamp(when), "set_by": via, "set_offset_s": offset, "offset_s": 0,
                        "saved": done.values.get("saved", "")})
    save_record(state_dir, device_id, changes)


def warning_text(label: str, reading: ClockReading, *, yes: Optional[bool], why: str = "") -> str:
    """The warning for a board whose clock is off and was not set."""
    base = f"The clock of board {label} is off by {offset_text(reading.offset)} against this computer"
    if why:
        return f"{base}; it was not set: {why}"
    if yes is None:
        return (f"{base}. Allow the framework to set it in the board's settings, or set it once by hand from the "
                "board's clock.")
    return f"{base}; your answer for this board is not to set it (it can still be set once by hand)."


def on_contact(session: Any, state_dir: Path, device_id: str, *, via: str, audit: Any = None,
               label: str = "", now: Callable[[], float] = time.time) -> Optional[ClockState]:
    """At a contact with the stored board `device_id` over the logged-in `session` (anything with run(script,
    timeout=...)): reads its clock, records the reading, and when it is off by more than TOLERANCE_S sets it with
    the person's standing yes for this board (audited; `audit` is the audit log or a function that opens it, called
    only for a set; None: the per-user audit log, default_audit), else logs a warning naming the offset. Never
    raises: a board that does not answer is left as it is (None)."""
    if not device_id or session is None:
        return None
    label = label or device_id
    try:
        reading = read(session.run, now)
    except Exception as e:  # noqa: BLE001 - a contact never fails on the clock
        logger.debug("The clock of board %s was not read: %s", device_id, e)
        return None
    if reading is None:
        return None
    save_record(state_dir, device_id, reading_record(reading))
    if not reading.off:
        return ClockState(reading)
    yes = clock_yes(state_dir, device_id)
    if not yes:
        text = warning_text(label, reading, yes=yes)
        logger.warning(text)
        save_record(state_dir, device_id, {"warning": text})
        return ClockState(reading, warning=text)
    when = now()
    try:
        done = set_clock(session.run, when)
    except Exception as e:  # noqa: BLE001 - the contact goes on; the record says it failed
        done = ClockRun("failed", str(e)[:200])
    if audit is None:
        audit = default_audit(state_dir)
    elif callable(audit) and not hasattr(audit, "record"):
        try:
            audit = audit()
        except Exception:  # noqa: BLE001 - the set itself stands; the framework's log has it
            audit = None
    record_set(state_dir, device_id, reading, done, via=via, audit=audit, actor="framework", when=when,
               reason="standing-yes")
    if done.ok:
        logger.info("The clock of board %s was off by %s; set to %s UTC (the person's standing yes).", label,
                    offset_text(reading.offset), stamp(when))
        save_record(state_dir, device_id, {"warning": ""})
        return ClockState(reading, done)
    why = ("the board's root readiness for llmcf-install is not set up (or its sudo rule is an older one)"
           if done.result == "not-ready" else f"llmcf-install answered {done.result or 'nothing'} {done.detail}")
    text = warning_text(label, reading, yes=True, why=why.strip())
    logger.warning(text)
    save_record(state_dir, device_id, {"warning": text})
    return ClockState(reading, done, text)


KEEP_NOTE = ("This board has no working clock of its own: llmcf-install also sets up keeping its time across a "
             "restart (fake-hwclock where it is installed, else systemd-timesyncd's clock file).")


def needs_keep(state_dir: Optional[Path], device_id: str) -> bool:
    """Whether the board's last clock reading shows no working RTC (False when it has one or was never read)."""
    record = load_record(Path(state_dir), device_id) if state_dir and device_id else {}
    return record.get("rtc") not in (None, "yes")


def keep_after_apply(run: Callable[..., Any], state_dir: Optional[Path], device_id: str, *, via: str,
                     audit: Any = None) -> Optional[ClockRun]:
    """After a bundle's successful root apply (within the person's yes for it, whose question says so: KEEP_NOTE):
    `clock keep` when the board's last clock reading shows no working RTC (None: it has one, or no reading says
    otherwise). Audited (`audit` None: the per-user audit log) and recorded."""
    if not needs_keep(state_dir, device_id):
        return None
    if audit is None:
        audit = default_audit(Path(state_dir))
    try:
        done = keep_clock(run)
    except Exception as e:  # noqa: BLE001 - the apply itself stands; the line says what happened
        done = ClockRun("failed", str(e)[:200])
    _audit(audit, KEEP_AUDIT_TOOL, device_id, via=via, actor="user", outcome="ok" if done.ok else "error",
           arguments={"keep": done.values.get("keep", "")}, detail=f"llmcf-install clock keep: {done.result} "
                                                                  f"{done.detail}".strip())
    if state_dir and device_id:
        save_record(Path(state_dir), device_id, {"keep_result": done.result or "none",
                                                  "keep": done.values.get("keep", "")})
    return done


def keep_line(done: ClockRun) -> str:
    """One line for the person about `clock keep`."""
    keep = done.values.get("keep", "")
    if done.ok and keep == "fake-hwclock":
        return "The board keeps its last known time across a restart now (fake-hwclock)."
    if done.ok and keep == "timesyncd":
        return "The board keeps its last known time across a restart now (systemd-timesyncd's clock file)."
    if done.ok:
        return "The board has a working clock of its own; nothing to set up."
    if done.result == "unavailable":
        return ("The board cannot keep its time across a restart: neither fake-hwclock nor systemd-timesyncd is "
                "there (llmcf-install's install_package brings fake-hwclock after your yes: the board's packages, from "
                "its own sources or a board preload).")
    return f"Keeping the board's time across a restart was not set up: {done.result or 'no answer'} {done.detail}"


def clock_state(state_dir: Path, device_id: str) -> Dict[str, Any]:
    """The board's clock state for the person's channels: offset_s (int, None: never read), rtc (yes, reset, none,
    unreadable, "" never read), keeper (fake-hwclock, timesyncd, none, ""), read_at, set_at, set_by ("" never),
    from_pc (True, False, None: never asked) and the last warning."""
    record = load_record(state_dir, device_id)
    offset = record.get("offset_s")
    return {"offset_s": offset if isinstance(offset, int) and not isinstance(offset, bool) else None,
            "rtc": str(record.get("rtc") or ""), "keeper": str(record.get("keeper") or ""),
            "read_at": str(record.get("read_at") or ""), "set_at": str(record.get("set_at") or ""),
            "set_by": str(record.get("set_by") or ""), "from_pc": clock_yes(state_dir, device_id),
            "warning": str(record.get("warning") or "")}


def state_lines(state_dir: Path, device_id: str) -> List[str]:
    """The board's clock state in English lines (the CLI's `device clock`)."""
    state = clock_state(state_dir, device_id)
    rtc = {"yes": "yes", "reset": "no (it holds a time before 2025: reset, no battery)", "none": "no",
           "unreadable": "present, but it does not read"}.get(state["rtc"], "not read yet")
    keeper = {"fake-hwclock": "fake-hwclock", "timesyncd": "systemd-timesyncd's clock file",
              "none": "none"}.get(state["keeper"], "not read yet")
    from_pc = {True: "yes (when it is off by more than a minute)", False: "no (only a warning)"}.get(
        state["from_pc"], "not asked yet")
    return [f"Offset against this computer: "
            f"{offset_text(state['offset_s']) if state['offset_s'] is not None else 'not read yet'}",
            f"Working clock of its own (RTC): {rtc}",
            f"Keeps its time across a restart with: {keeper}",
            f"Last read: {state['read_at'] or 'never'}",
            f"Last set: {state['set_at'] + ' (' + state['set_by'] + ')' if state['set_at'] else 'never'}",
            f"Set from this computer's time: {from_pc}"]


__all__ = ["AUDIT_TOOL", "CLOCK_QUESTION", "ClockReading", "ClockRun", "ClockState", "KEEP_AUDIT_TOOL", "KEEP_NOTE",
           "READ_SCRIPT", "RTC_FLOOR", "SET_QUESTION", "TOLERANCE_S", "clock_state", "clock_yes", "default_audit",
           "keep_after_apply", "keep_clock", "keep_line", "load_record", "needs_keep", "offset_text", "on_contact",
           "parse_reading", "parse_run", "read", "reading_record", "record_set", "save_record", "set_clock",
           "span_text", "stamp", "state_lines", "warning_text"]
