#!/usr/bin/env python3
"""
The person's harness run on a board, from the host (ROADMAP 3.4.0 "An agent harness on the board as an opt-in bundle
component"; DESIGN "A harness on the board is the person's opt-in", "A long action on a board runs detached ...").

A bundle with a harness component carries the taken-in tree (harness.tar) with its tree checksum in the signed manifest
and the root action run_harness: llmcf-install's apply (after the person's yes for the root actions) unpacks it
root-owned and checks the checksum. From then on a run goes through llmcf-install's harness actions (gate operation
bundle-harness, sudo -n through the board's root readiness only, never a password):

- start_run: harness-start (the checksum is computed again on the board first; mode dry: the harness's own dry run,
  run: its questions answered live, run-yes: its own assume-yes switch, only when the bundle carries it after the
  person's opt-in); the run's name and state are kept on this computer (<state>/harnesses/<id>/runs/<bundle>--
  <component>--<run>/run.json), so any later session attaches to it again;
- Follower: harness-read from the byte offset of the state, again and again (the board waits up to 15 s for new
  output); every byte goes to the local installer.log and to on_output(bytes); a dropped connection is waited through
  by the board session (a read may run twice), and follow_on_board opens a new session and reads on from the stored
  offset when the board did not answer again in time: the installer on the board never stopped (harness_watch.sh runs
  detached through setsid);
- answer_run: one line of the person's answer on stdin (never an argument, never kept, never in the audit log);
- stop_run: the person's cancel; the watch still records the end;
- collect_report: harness-records once the run ended (only the lines that differ before and after, the trace lines the
  report reads), written into records/ beside the log, read by harness_report.read_report with the exit codes the
  harness names, kept as report.json.

Only the person starts, answers or stops a run (the CLI and the Board harness window); an AI never answers an
installer question and never starts a run: over MCP it reads only `scrubbed` (outcome, counts and flag codes: no
account name, no path, no address, no file content). Each start, stop and report is a row of the audit log (the
person as actor; never an answer).
"""

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from orchestrator.remote import gate
from orchestrator.runtime.bundle import gate_ops  # noqa: F401 - defines bundle-harness
from orchestrator.runtime.bundle import harness as hz
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.harness_report import HarnessReport, read_report
from orchestrator.runtime.bundle.manifest import Component

RUNS_DIR = "runs"
STATE_FILE = "run.json"
LOG_FILE = "installer.log"
RECORDS_DIR = "records"
REPORT_FILE = "report.json"
READ_TIMEOUT = 90.0          # one harness-read: the board waits up to 15 s for output, a slow board answers later
RECORDS_TIMEOUT = 600.0
RECORDS_LIMIT = 32 * 1024 * 1024
MAX_ANSWER = 4096
RECONNECTS = 5
RE_RUN = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}", re.ASCII)
_RE_STATUS = re.compile(r"status=(running|ended)(?: code=(\d{1,3}))? offset=(\d{1,15}) size=(\d{1,10})$", re.ASCII)
_RECORD_NAMES = {"exit_code", "started", "ended", "watch", "terminal", "trace.txt"} | {
    f"{side}/{name}" for side in ("before", "after") for name in ("packages.txt", "accounts.txt", "ports.txt",
                                                                    "files.txt")}

# The person's questions (the CLI and the Board harness window ask exactly these words; "No" is the default).
RUN_QUESTION = ("Run the installer of harness {harness} on board {board} now, as root through llmcf-install? It may "
                "install packages, add accounts and services and fetch what it needs; the framework does not block it, it "
                "watches and reports what it did. Its questions come to you one by one. The responsibility for "
                "what it does is yours.")
DRY_QUESTION = ("Start the dry run of harness {harness} on board {board} (its own {args}), as root through "
                "llmcf-install? A dry run should change nothing; the framework watches it all the same.")
STOP_QUESTION = "Stop the installer of harness {harness} on board {board} now? What it did so far stays."
NOT_READY_NEXT = ("Set up the board's root readiness for bundle {bundle} first (its Prepare root step), then apply "
                  "the bundle's root actions, which place the harness.")


class HarnessRunError(RuntimeError):
    """A harness run that cannot be started, read, answered or stopped: the person's words, and `next` (what to do)."""

    def __init__(self, message: str, next_step: str = "", result: str = ""):
        super().__init__(message)
        self.next_step = next_step
        self.result = result


@dataclass
class RunState:
    """What this computer keeps of a run: where it is and how far its output was read."""
    bundle_id: str
    component: str
    run: str
    mode: str
    harness: str
    tree_sha256: str
    device_id: str = ""
    offset: int = 0
    status: str = "starting"          # starting, running, ended
    exit_code: Optional[int] = None
    started: str = ""
    ended: str = ""
    outcome: str = ""                 # done, findings or failed, once the report was read
    flags: Dict[str, int] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.bundle_id}--{self.component}--{self.run}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_dir(state_dir: Path, harness_id: str) -> Path:
    return Path(state_dir) / hz.STORE_DIR / harness_id / RUNS_DIR


def run_folder(state_dir: Path, state: RunState) -> Path:
    return runs_dir(state_dir, state.harness) / state.key


def save_state(state_dir: Path, state: RunState) -> None:
    from orchestrator.utils.atomic import write_private
    write_private(run_folder(state_dir, state) / STATE_FILE, json.dumps(asdict(state), indent=2, sort_keys=True))


def load_state(state_dir: Path, harness_id: str, bundle_id: str, component: str, run: str) -> Optional[RunState]:
    path = runs_dir(state_dir, harness_id) / f"{bundle_id}--{component}--{run}" / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunState(**{k: v for k, v in data.items() if k in RunState.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def list_runs(state_dir: Path) -> List[RunState]:
    """Every run this computer knows, newest first."""
    out = []
    base = Path(state_dir) / hz.STORE_DIR
    for path in base.glob(f"*/{RUNS_DIR}/*/{STATE_FILE}") if base.is_dir() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(RunState(**{k: v for k, v in data.items() if k in RunState.__dataclass_fields__}))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda s: (s.started, s.run), reverse=True)


def harness_component(built: Any, component: Optional[str] = None) -> Component:
    """The bundle's harness component (`component`, or its only one); HarnessRunError otherwise."""
    comps = [c for c in built.manifest.components if c.harness is not None]
    if component:
        comps = [c for c in comps if c.id == component]
    if not comps:
        raise HarnessRunError(f"bundle {built.bundle_id} has no harness component"
                              + (f" {component}" if component else ""),
                              "Build a bundle with the block harness-agent and its variable harness set to the "
                              "harness's id.")
    if len(comps) > 1:
        raise HarnessRunError(f"bundle {built.bundle_id} has several harness components; name one: "
                              + ", ".join(c.id for c in comps))
    if built.manifest.mode != "full" or not any(a.action == "run_harness" and a.component == comps[0].id
                                                for a in built.manifest.root_actions):
        raise HarnessRunError(f"bundle {built.bundle_id} lets no harness run (no root action run_harness)")
    return comps[0]


def run_name(mode: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"{'dry' if mode == 'dry' else 'run'}-{stamp}"


def _call(shell: Any, built: Any, action: str, component: str, run: str, value: str = "0", *, stdin: Any = None,
          again: bool = False, timeout: float = 120.0, limit: Optional[int] = None):
    """One harness action on the board (gate operation bundle-harness): (report, output). HarnessRunError with the
    board's own words when llmcf-install refused it or the board's root readiness is not there."""
    from orchestrator.runtime.bundle.board import helper_sha256
    script = gate.script("bundle-harness", action, built.bundle_id, component, run, value, helper_sha256(built))
    kwargs: Dict[str, Any] = {"stdin": stdin if stdin is not None else [b"\n"], "again": again, "timeout": timeout,
                              "step": "bundle"}
    if limit is not None:
        kwargs["limit"] = limit
    res = shell.run(script, **kwargs)
    out = getattr(res, "out", "") or ""
    report = inst.report(out)
    if report.result == "not-ready":
        raise HarnessRunError(report.detail or "the board's root readiness is not set up",
                              NOT_READY_NEXT.format(bundle=built.bundle_id), "not-ready")
    if report.result in ("refused", "failed") or (report.result == "" and getattr(res, "status", 1) != 0):
        raise HarnessRunError(report.detail or f"llmcf-install did not answer (exit {getattr(res, 'status', '?')})",
                              "", report.result or "failed")
    return report, out


def _audit(audit: Any, actor: str, state: RunState, outcome: str, detail: str) -> None:
    """A row of the audit log for the person's start, stop or report (never an answer). A missing audit log does
    not stop the run (the person is there and sees it), but says so in the detail of the next row it can write."""
    if audit is None or not callable(getattr(audit, "record", None)):
        return
    try:
        audit.record(actor, "harness_run", "person", "approved", outcome, approved=True,
                     arguments={"bundle": state.bundle_id, "component": state.component, "run": state.run,
                                "mode": state.mode, "harness": state.harness, "tree": state.tree_sha256[:12],
                                "device": state.device_id},
                     detail=detail[:900])
    except Exception:  # noqa: BLE001 - the audit log's own failure never names a path here
        pass


def start_run(shell: Any, built: Any, component: Optional[str], mode: str, *, state_dir: Path,
              device_id: str = "", audit: Any = None, actor: str = "cli",
              now: Optional[datetime] = None) -> RunState:
    """Starts the harness installer on the board (harness-start, after the person's yes in the CLI or the window):
    the run's state is kept on this computer first, so it can always be attached to again."""
    if mode not in inst.HARNESS_MODES:
        raise HarnessRunError(f"not a mode of a harness run: {mode} (dry, run or run-yes)")
    comp = harness_component(built, component)
    part = comp.harness
    if mode == "dry" and not part.dry_run_args:
        raise HarnessRunError(f"harness {part.id} names no dry run of its own; the report before is what the bundle "
                              "says about it")
    if mode == "run-yes" and not part.assume_yes:
        raise HarnessRunError(f"bundle {built.bundle_id} carries no assume-yes switch of harness {part.id} (that is "
                              "the person's opt-in when the bundle is built); its questions are answered live")
    state = RunState(bundle_id=built.bundle_id, component=comp.id, run=run_name(mode, now), mode=mode,
                     harness=part.id, tree_sha256=part.tree_sha256, device_id=device_id, started=_now())
    save_state(state_dir, state)
    try:
        _call(shell, built, "harness-start", comp.id, state.run, mode, timeout=600.0)
    except HarnessRunError as e:
        state.status, state.ended, state.outcome = "ended", _now(), "failed"
        save_state(state_dir, state)
        _audit(audit, actor, state, "refused", str(e))
        raise
    state.status = "running"
    save_state(state_dir, state)
    _audit(audit, actor, state, "started", f"harness {part.id} {mode} started on the board, tree "
                                           f"{part.tree_sha256[:12]}")
    return state


class Follower:
    """Reads a run's output from the board from the state's offset on, into the local installer.log and to
    `on_output(bytes)`; the state's offset is saved after every read, so a new session reads on from there."""

    def __init__(self, shell: Any, built: Any, state: RunState, state_dir: Path,
                 on_output: Callable[[bytes], None] = lambda _b: None):
        self.shell, self.built, self.state, self.state_dir = shell, built, state, Path(state_dir)
        self.on_output = on_output
        self.log = run_folder(state_dir, state) / LOG_FILE

    def read_once(self) -> bool:
        """One harness-read; True once the installer ended and everything was read."""
        _report, out = _call(self.shell, self.built, "harness-read", self.state.component, self.state.run,
                             str(self.state.offset), again=True, timeout=READ_TIMEOUT)
        status = data = None
        for line in out.splitlines():
            if line.startswith("llmcf-harness: status="):
                status = _RE_STATUS.fullmatch(line[len("llmcf-harness: "):].strip())
            elif line.startswith("llmcf-harness: data"):
                data = line[len("llmcf-harness: data"):].strip()
        if status is None:
            raise HarnessRunError("the board's answer to reading the installer's output did not read")
        try:
            chunk = base64.b64decode(data or "", validate=True)
        except ValueError:
            raise HarnessRunError("the installer's output came back damaged; attach again") from None
        new_offset = int(status.group(3))
        if len(chunk) != int(status.group(4)) or new_offset != self.state.offset + len(chunk):
            # (an offset the board clamped: a log that is shorter than what was read before, never in a real run)
            raise HarnessRunError("the installer's output on the board does not continue where it was read")
        if chunk:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log, "ab") as fh:
                fh.write(chunk)
            self.on_output(chunk)
        self.state.offset = new_offset
        if status.group(1) == "ended":
            self.state.status, self.state.ended = "ended", _now()
            self.state.exit_code = int(status.group(2)) if status.group(2) else None
        else:
            self.state.status = "running"
        save_state(self.state_dir, self.state)
        return self.state.status == "ended"

    def follow(self, cancelled: Callable[[], bool] = lambda: False) -> RunState:
        while not cancelled():
            if self.read_once():
                break
        return self.state


def answer_run(shell: Any, built: Any, state: RunState, line: str) -> None:
    """Passes one line of the person's answer to the installer (on stdin of the gate operation, never as an
    argument; never kept, never in the audit log)."""
    if len(line) > MAX_ANSWER or any(ch in line for ch in "\r\n\0") or \
            any(ord(ch) < 32 and ch != "\t" for ch in line):
        raise HarnessRunError("an answer is one line of plain text")
    _call(shell, built, "harness-answer", state.component, state.run, stdin=[(line + "\n").encode("utf-8")],
          again=False)


def stop_run(shell: Any, built: Any, state: RunState, *, state_dir: Path, audit: Any = None,
             actor: str = "cli") -> None:
    """The person's cancel: ends the installer on the board (the watch still records the end and the comparison
    after)."""
    _call(shell, built, "harness-stop", state.component, state.run, again=True)
    _audit(audit, actor, state, "stopped", f"harness {state.harness} {state.mode} stopped by the person")
    save_state(state_dir, state)


def parse_records(text: str, folder: Path) -> bool:
    """Writes what harness-records sent into `folder` (the watch's layout); True when the board cut a long record."""
    files: Dict[str, List[str]] = {}
    current: Optional[str] = None
    cut = False
    for line in (text or "").splitlines():
        if line.startswith("llmcf-harness: file "):
            name = line[len("llmcf-harness: file "):].strip()
            current = name if name in _RECORD_NAMES else None
            if current:
                files.setdefault(current, [])
        elif line == "llmcf-harness: truncated":
            cut = True
        elif line.startswith("> ") and current:
            files[current].append(line[2:])
        elif line == ">" and current:
            files[current].append("")
    for name, lines in files.items():
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
    return cut


def collect_report(shell: Any, built: Any, state: RunState, *, state_dir: Path, audit: Any = None,
                   actor: str = "cli") -> HarnessReport:
    """The run's records read back once it ended, its report made by the exit codes the harness names, kept as
    report.json beside the log; the report's summary goes to the audit log."""
    comp = harness_component(built, state.component)
    _report, out = _call(shell, built, "harness-records", state.component, state.run, again=True,
                         timeout=RECORDS_TIMEOUT, limit=RECORDS_LIMIT)
    folder = run_folder(state_dir, state) / RECORDS_DIR
    if folder.exists():
        from orchestrator.utils.atomic import remove_tree
        remove_tree(folder, label="the harness run's earlier records")
    folder.mkdir(parents=True, exist_ok=True)
    cut = parse_records(out, folder)
    rep = read_report(folder, comp.harness.outcomes(), "dry" if state.mode == "dry" else "run")
    state.status = "ended"
    state.exit_code, state.outcome = rep.exit_code, rep.outcome
    state.flags = {level: sum(1 for f in rep.flags if f.level == level) for level in ("warn", "notice")}
    data = rep.as_dict()
    data["truncated"] = cut
    from orchestrator.utils.atomic import write_private
    write_private(run_folder(state_dir, state) / REPORT_FILE, json.dumps(data, indent=2, sort_keys=True))
    save_state(state_dir, state)
    codes = sorted({f"{f.level}:{f.code}" for f in rep.flags})
    _audit(audit, actor, state, rep.outcome,
           f"harness {state.harness} {state.mode}: {rep.outcome} (exit code {rep.exit_code}); packages "
           f"+{len(rep.packages_added)}/-{len(rep.packages_removed)}, accounts +{len(rep.accounts_added)}, ports "
           f"+{len(rep.ports_added)}, files +{len(rep.files_added)}/~{len(rep.files_changed)}"
           + (f"; flags {', '.join(codes)}" if codes else ""))
    return rep


def stored_report(state_dir: Path, state: RunState) -> Optional[HarnessReport]:
    """The report kept on this computer for the run (None when it was not read yet)."""
    from orchestrator.runtime.bundle.harness_report import Flag
    try:
        data = json.loads((run_folder(state_dir, state) / REPORT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    flags = [Flag(**f) for f in data.pop("flags", []) if isinstance(f, dict)]
    data.pop("truncated", None)
    try:
        rep = HarnessReport(**{k: v for k, v in data.items() if k in HarnessReport.__dataclass_fields__})
    except TypeError:
        return None
    rep.flags = flags
    return rep


def scrubbed(state: RunState, rep: Optional[HarnessReport]) -> Dict[str, Any]:
    """A run as an AI may read it (MCP harness_report): states, numbers and the fixed flag codes; never an account
    name, a path, an address, a program's arguments or anything the installer printed."""
    out: Dict[str, Any] = {"bundle_id": state.bundle_id, "component": state.component, "run": state.run,
                           "mode": state.mode, "harness": state.harness, "status": state.status,
                           "exit_code": state.exit_code, "outcome": state.outcome or None,
                           "started": state.started, "ended": state.ended or None}
    if rep is not None:
        out.update({"watch": rep.watch, "seconds": rep.seconds,
                    "packages": {"added": len(rep.packages_added), "removed": len(rep.packages_removed),
                                 "changed": len(rep.packages_changed)},
                    "accounts_added": len(rep.accounts_added), "ports_added": len(rep.ports_added),
                    "files": {"added": len(rep.files_added), "changed": len(rep.files_changed),
                              "removed": len(rep.files_removed)},
                    "programs_started": sum(rep.programs.values()), "network_targets": len(rep.network),
                    "flags": [{"level": f.level, "code": f.code} for f in rep.flags]})
    return out


def follow_on_board(open_session: Callable[[], Any], built: Any, state: RunState, *, state_dir: Path,
                    on_output: Callable[[bytes], None] = lambda _b: None,
                    cancelled: Callable[[], bool] = lambda: False,
                    say: Callable[[str], None] = lambda _m: None, reconnects: int = RECONNECTS) -> RunState:
    """Follows the run to its end over the caller's login (`open_session()`), like tmux: when the board did not
    answer again within the session's wait (RemoteError), a new session attaches and reads on from the stored
    offset, up to `reconnects` times; the installer on the board never stopped. Each session is closed."""
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell
    tries = 0
    while True:
        session = open_session()
        try:
            Follower(RemoteShell(session), built, state, state_dir, on_output).follow(cancelled)
            return state
        except RemoteError as e:
            if cancelled() or tries >= reconnects or getattr(e, "kind", "") == "stopped":
                raise
            tries += 1
            say(f"the connection to the board dropped; attaching again from byte {state.offset} "
                f"({tries}/{reconnects}) - the installer goes on there")
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()


def on_board(open_session: Callable[[], Any], step: Callable[[Any], Any]) -> Any:
    """`step(shell)` over a session of its own (an answer or a stop while another session follows the output)."""
    from orchestrator.runtime.device.remote import RemoteShell
    session = open_session()
    try:
        return step(RemoteShell(session))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


_RE_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()*+][0-9A-Za-z]|\x1b[@-_]?")
_RE_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def plain_output(chunk: bytes) -> str:
    """The installer's output as plain text for the person's terminal or window: its terminal's escape sequences
    (colours, cursor moves, a window title, a clipboard write) and other control characters taken out, line ends as
    "\\n". Nothing the installer prints can steer the person's own terminal."""
    text = chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text = _RE_ESCAPES.sub("", text)
    return _RE_CONTROLS.sub("", text)


def report_lines(state: RunState, rep: Optional[HarnessReport], lang: str = "en") -> List[str]:
    """The run for the person: its state, then the report (harness_report.render) when it was read."""
    from orchestrator.runtime.bundle.harness_report import render
    head = (f"Harness {state.harness} ({state.mode}) run {state.run} of bundle {state.bundle_id}, component "
            f"{state.component}: {state.status}" + (f", exit code {state.exit_code}" if state.exit_code is not None
                                                      else ""))
    return [head] + (render(rep, lang).splitlines() if rep is not None else [])


__all__ = ["DRY_QUESTION", "Follower", "HarnessRunError", "NOT_READY_NEXT", "RUN_QUESTION", "RunState",
           "STOP_QUESTION", "answer_run", "plain_output", "collect_report", "follow_on_board", "harness_component", "list_runs",
           "load_state", "on_board", "parse_records", "report_lines", "run_folder", "run_name", "save_state",
           "scrubbed", "start_run", "stop_run", "stored_report"]
