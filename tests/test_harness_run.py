"""The person's harness run end to end (orchestrator/runtime/bundle/harness_run.py): a bundle with a harness component
built and deployed to a fake board behind llmcf's real gate, its root actions applied by the real llmcf-install test
build (which unpacks the tree and checks its checksum), then the dry run, the interactive run with a question answered
live, reading on from the stored offset in a new session (a dropped connection), the records read back in parts
(nothing cut off, a dropped connection read on from the last good offset, every file's sha256 checked, the disk rule
first) and the report, which says whether the installer brought its own account and service unit; the scrubbed view
an AI may read holds no account, no path and nothing the installer printed. A missing root readiness is named, asked
about and set up through the one setup, idempotently, never by an AI.

The fake board is tests/test_llmcf_install.py's (a POSIX sh gate, a fake sudo running the test build with --root);
every name and value here is a test value."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import generator as gen
from orchestrator.runtime.bundle import harness as hz
from orchestrator.runtime.bundle import harness_run as hr
from test_llmcf_install import helper_binary, sudo_on_path  # noqa: F401 - the fake board's fixtures

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                                reason="the fake board runs llmcf's gate and the watch in POSIX sh")

INSTALLER = r'''#!/bin/sh
case ${1-} in --dry-run) echo "would install the demo agent"; exit 0 ;; esac
printf 'Name of the agent account? '
IFS= read -r name || name=""
echo "set up for $name"
# its own service account and unit, as a real harness brings them (the fake board's places below its root)
root=$(pwd); root=${root%/var/lib/llmcf/*}
mkdir -p "$root/watched/etc/systemd/system/multi-user.target.wants"
printf '[Unit]\nDescription=demo\n' > "$root/watched/etc/systemd/system/demo-agent.service"
ln -s ../demo-agent.service "$root/watched/etc/systemd/system/multi-user.target.wants/demo-agent.service"
echo "demo-agent:x:990:990::/nonexistent:/usr/sbin/nologin" >> "$root/etc/passwd"
exit 1
'''


@pytest.fixture
def harness_board(tmp_path, helper_binary, sudo_on_path):
    from test_board_bundle import DEVICE, GateShell, _plan
    from test_llmcf_install import _ready, _root_board
    from orchestrator.runtime.bundle.board import run_bundle, run_root
    board = _root_board(tmp_path, helper_binary)
    (tmp_path / "bin").mkdir(exist_ok=True)
    (tmp_path / "bin" / "sh").symlink_to("/bin/sh")
    (tmp_path / "watched").mkdir()
    src = tmp_path / "agent-src"
    (src / "installer").mkdir(parents=True)
    (src / "installer" / "install.sh").write_text(INSTALLER)
    (src / "installer" / "install.sh").chmod(0o755)
    hz.take_in(hz.HarnessSpec(id="demo", title="Demo agent", kind="folder", location=str(src),
                              installer="installer/install.sh", dry_run_args=("--dry-run",),
                              assume_yes="DEMO_YES=1", exit_codes={0: "done", 1: "findings"}), tmp_path / "state")
    selection = gen.Selection(blocks=["harness-agent"], values={"harness": {"harness": "demo"}})
    plan = _plan(tmp_path, selection, state_dir=tmp_path / "state")
    assert plan.ok, plan.problems
    store = gen.BundleStore(tmp_path / "state")
    from test_board_bundle import _helper
    built = gen.build_bundle(plan, store, board.signer,
                             helper=_helper(tmp_path, plan, helper_binary.read_bytes()))
    shell = GateShell(board)
    assert run_bundle(shell, built, "apply", store=store, device_id=DEVICE).report.result == "stopped-root"
    assert _ready(shell, built).ok
    applied = run_root(shell, built, "apply")
    assert applied.ok and any(line["step"] == "run_harness" for line in applied.report.lines), applied.lines()
    return shell, built, tmp_path / "state"


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kw):
        self.rows.append((actor, tool, outcome, kw))
        return len(self.rows)


def _until(follower, words: str, seconds: float = 60) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        follower.read_once()
        text = follower.log.read_text(errors="replace") if follower.log.exists() else ""
        if words in text or follower.state.status == "ended":
            return text
    raise AssertionError(f"the installer never said {words!r}")


def test_dry_run_then_the_run_with_a_live_answer_and_the_report(harness_board):
    shell, built, state_dir = harness_board
    audit = Audit()
    dry = hr.start_run(shell, built, None, "dry", state_dir=state_dir, device_id="0123456789abcdef", audit=audit)
    seen = []
    hr.Follower(shell, built, dry, state_dir, seen.append).follow()
    assert dry.status == "ended" and dry.exit_code == 0 and b"would install the demo agent" in b"".join(seen)
    rep = hr.collect_report(shell, built, dry, state_dir=state_dir, audit=audit)
    assert rep.mode == "dry" and rep.outcome == "done"
    assert rep.brings == "neither" and dry.brings == "neither" and "A dry run: it brought no account" in \
        "\n".join(hr.report_lines(dry, rep))

    run = hr.start_run(shell, built, None, "run", state_dir=state_dir, audit=audit)
    first = hr.Follower(shell, built, run, state_dir)
    text = _until(first, "Name of the agent account?")
    assert "Name of the agent account?" in text and run.status == "running"
    # a dropped connection: a new session reads on from the stored offset, nothing twice
    again = hr.load_state(state_dir, "demo", built.bundle_id, "harness", run.run)
    assert again is not None and again.offset == run.offset > 0
    hr.answer_run(shell, built, again, "agent-account")
    hr.Follower(shell, built, again, state_dir).follow()
    log = (hr.run_folder(state_dir, again) / hr.LOG_FILE).read_text(errors="replace")
    assert log.count("Name of the agent account?") == 1 and "set up for agent-account" in log
    assert again.exit_code == 1
    rep = hr.collect_report(shell, built, again, state_dir=state_dir, audit=audit)
    assert rep.outcome == "findings" and again.outcome == "findings"
    assert hr.stored_report(state_dir, again).outcome == "findings"
    # the installer brought its own account and service unit: the report says so, the framework adds none
    assert rep.accounts_added == ["demo-agent"] and rep.own_units == ["demo-agent.service"] and rep.brings == "both"
    assert (again.own_accounts, again.own_units, again.brings) == (1, 1, "both")
    text = "\n".join(hr.report_lines(again, rep))
    assert "brought its own accounts (demo-agent) and its own service units (demo-agent.service)" in text
    assert "adds no account and no service of its own" in text
    assert "eigene Konten (demo-agent)" in "\n".join(hr.report_lines(again, rep, "de"))
    assert hr.stored_report(state_dir, again).own_units == ["demo-agent.service"]
    assert "truncated" not in (hr.run_folder(state_dir, again) / hr.REPORT_FILE).read_text()

    # an AI reads states, numbers and flag codes only
    view = hr.scrubbed(again, rep)
    flat = repr(view)
    assert view["outcome"] == "findings" and view["exit_code"] == 1
    assert view["own"] == {"accounts": 1, "units": 1, "brings": "both"}
    assert "agent-account" not in flat and str(state_dir) not in flat and "/" not in flat
    assert "demo-agent" not in flat
    # the audit log: starts and reports, never the answer
    outcomes = [row[2] for row in audit.rows]
    assert outcomes == ["started", "done", "started", "findings"]
    assert all(row[1] == "harness_run" and "agent-account" not in repr(row) for row in audit.rows)
    assert "brought its own accounts 1, service units 1 (both)" in audit.rows[-1][3]["detail"]


def test_a_question_left_open_is_stopped_and_still_recorded(harness_board):
    shell, built, state_dir = harness_board
    run = hr.start_run(shell, built, "harness", "run", state_dir=state_dir)
    follower = hr.Follower(shell, built, run, state_dir)
    _until(follower, "Name of the agent account?")
    with pytest.raises(hr.HarnessRunError, match="going on"):
        hr.start_run(shell, built, "harness", "dry", state_dir=state_dir)
    hr.stop_run(shell, built, run, state_dir=state_dir)
    follower.follow()
    assert run.status == "ended" and run.exit_code not in (0, 1)
    assert hr.collect_report(shell, built, run, state_dir=state_dir).outcome == "failed"


def test_what_a_run_refuses_before_it_starts(harness_board):
    shell, built, state_dir = harness_board
    with pytest.raises(hr.HarnessRunError, match="assume-yes"):
        hr.start_run(shell, built, None, "run-yes", state_dir=state_dir)
    with pytest.raises(hr.HarnessRunError, match="no harness component"):
        hr.start_run(shell, built, "llm", "run", state_dir=state_dir)
    state = hr.start_run(shell, built, None, "dry", state_dir=state_dir)
    hr.Follower(shell, built, state, state_dir).follow()
    with pytest.raises(hr.HarnessRunError, match="one line"):
        hr.answer_run(shell, built, state, "two\nlines")
    with pytest.raises(hr.HarnessRunError, match="takes no answers"):
        hr.answer_run(shell, built, state, "yes")


class FlakyShell:
    """The board session, dropped after `parts` part calls of harness-records (a RemoteError, as a lost connection
    ends a step); it keeps every part call's record number and offset."""

    def __init__(self, shell, parts=None, calls=None):
        self.shell, self.parts, self.calls = shell, parts, calls if calls is not None else []

    def run(self, script, **kw):
        from orchestrator.runtime.device.remote import RemoteError
        args = getattr(script, "args", ())
        if getattr(script, "verb", "") == "bundle-harness" and args[0] == "harness-records" and args[4] != "index":
            if self.parts is not None and self.parts <= 0:
                raise RemoteError("the connection dropped", kind="dropped")
            if self.parts is not None:
                self.parts -= 1
            self.calls.append(tuple(int(x) for x in args[4].split(".")))
        return self.shell.run(script, **kw)


def _ended_dry(shell, built, state_dir):
    run = hr.start_run(shell, built, None, "dry", state_dir=state_dir)
    hr.Follower(shell, built, run, state_dir).follow()
    return run


def test_the_records_come_in_parts_and_a_dropped_connection_reads_on(harness_board, monkeypatch):
    from orchestrator.runtime.device import remote
    shell, built, state_dir = harness_board
    run = _ended_dry(shell, built, state_dir)
    calls = []
    sessions = [FlakyShell(shell, parts=3, calls=calls), FlakyShell(shell, calls=calls)]
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    said = []
    rep = hr.report_on_board(lambda: sessions.pop(0), built, run, state_dir=state_dir, say=said.append,
                             part_size=16)
    assert rep.outcome == "done" and said and "reading on" in said[0]
    # the fourth part continues where the third ended: nothing read twice, nothing left out
    assert len(calls) > 4 and len(set((n, off) for n, off, _len in calls)) == len(calls)
    for n in {c[0] for c in calls}:
        mine = sorted(c for c in calls if c[0] == n)
        assert mine[0][1] == 0 and all(b[1] == a[1] + a[2] for a, b in zip(mine, mine[1:])), mine
    folder = hr.run_folder(state_dir, run) / hr.RECORDS_DIR
    index = json.loads((folder / hr.INDEX_FILE).read_text())
    assert index["complete"] is True and set(index["files"]) >= {"exit_code", "after/files.txt", "trace.txt"}
    from orchestrator.utils.helpers import sha256_file
    for name, entry in index["files"].items():
        assert (folder / name).stat().st_size == entry["size"] and sha256_file(folder / name) == entry["sha256"]
    # read again: everything is here already, no part is fetched twice
    calls.clear()
    hr.collect_report(FlakyShell(shell, calls=calls), built, run, state_dir=state_dir)
    assert calls == []


def test_a_record_that_does_not_arrive_whole_is_read_anew(harness_board, monkeypatch):
    shell, built, state_dir = harness_board
    run = _ended_dry(shell, built, state_dir)
    real = hr.records_index

    def wrong(*a, **kw):
        files = real(*a, **kw)
        return [hr.RecordFile(f.number, f.name, f.size, "0" * 64) if f.name == "exit_code" else f for f in files]
    monkeypatch.setattr(hr, "records_index", wrong)
    with pytest.raises(hr.HarnessRunError, match="did not arrive whole"):
        hr.collect_report(shell, built, run, state_dir=state_dir)
    folder = hr.run_folder(state_dir, run) / hr.RECORDS_DIR
    assert (folder / "exit_code").stat().st_size == 0 and not (hr.run_folder(state_dir, run) / hr.REPORT_FILE).exists()
    monkeypatch.setattr(hr, "records_index", real)
    assert hr.collect_report(shell, built, run, state_dir=state_dir).exit_code == 0


def test_the_disk_rule_comes_before_the_records(harness_board):
    from types import SimpleNamespace
    shell, built, state_dir = harness_board
    run = _ended_dry(shell, built, state_dir)
    calls = []
    full = lambda _path: SimpleNamespace(free=1024, total=8 * 1024 ** 3, used=0)  # noqa: E731
    with pytest.raises(hr.HarnessRunError, match="Not enough disk space") as e:
        hr.collect_report(FlakyShell(shell, calls=calls), built, run, state_dir=state_dir, disk_usage=full)
    assert calls == [] and e.value.result == "disk" and "read on" in e.value.next_step


def test_the_report_reads_a_large_record_line_by_line(tmp_path, monkeypatch):
    from orchestrator.runtime.bundle import harness_report as rr
    (tmp_path / "after").mkdir()
    (tmp_path / "exit_code").write_text("0\n")
    (tmp_path / "watch").write_text("strace\n")
    line = '1 openat(AT_FDCWD, "/opt/agent/lib/file-{0}", O_WRONLY|O_CREAT, 0644) = 3\n'
    with open(tmp_path / "trace.txt", "w") as fh:
        for i in range(120000):
            fh.write(line.format(i))
        fh.write('1 openat(AT_FDCWD, "/etc/systemd/system/agent.timer", O_WRONLY|O_CREAT, 0644) = 3\n')
    (tmp_path / "after" / "accounts.txt").write_text("agent:990:990:/usr/sbin/nologin\n")

    def whole(*_a, **_kw):
        raise AssertionError("a record is never read whole")
    monkeypatch.setattr(Path, "read_text", whole)
    monkeypatch.setattr(Path, "read_bytes", whole)
    rep = rr.read_report(tmp_path)
    assert len(rep.written) == 120001 and rep.own_units == ["agent.timer"] and rep.brings == "both"


def test_whether_the_installer_brought_its_own_account_and_service():
    from orchestrator.runtime.bundle import harness_report as rr
    assert rr.own_units(["/etc/systemd/system/chimera.service", "/etc/systemd/system/chimera-tor.timer",
                         "/etc/systemd/system/multi-user.target.wants/chimera.service", "/etc/init.d/agent",
                         "/etc/systemd/user/x.service", "/etc/systemd/system/x.conf", "/etc/init.d/.hidden"]) == \
        ["agent", "chimera-tor.timer", "chimera.service"]
    rep = rr.HarnessReport(mode="run", watch="compare", exit_code=0, outcome="done", seconds=1)
    assert rep.brings == "neither"
    text = rr.render(rep)
    assert "brought neither an account nor a service unit" in text and "roadmap" in text and "opt-in" in text
    assert "weder ein Konto noch eine Service-Unit" in rr.render(rep, "de")
    rep.own_units = ["agent.service"]
    assert rep.brings == "units" and "its own service units (agent.service) and no account" in rr.render(rep)
    rep.accounts_added, rep.own_units = ["agent"], []
    assert rep.brings == "accounts" and "its own accounts (agent) and no service unit" in rr.render(rep)
    assert set(rr.TEXT["en"]) == set(rr.TEXT["de"])


# --- the board's root readiness, when a harness step finds it missing ---------------------------------------------------

PASSWORD = "board-test-pass"


def _older_rule(root: Path) -> None:
    """The sudo rule as a framework before the harness actions wrote it (the bundle's root actions only), on a board
    whose sudo asks for the account's password otherwise."""
    from orchestrator.runtime.bundle import installer as inst
    fixed = (root / "usr/local/libexec/llmcf/llmcf-install").as_posix()
    rule = root / "etc/sudoers.d/llmcf-install"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text("# llmcf-install (an older rule)\nboard ALL=(root) NOPASSWD: "
                    + ", ".join(f"{fixed} {a} *" for a in inst.ROOT_ACTIONS) + "\n")
    rule.chmod(0o440)
    (root / "sudo-password").write_text(PASSWORD + "\n")


def test_a_missing_readiness_is_named_asked_and_set_up_once(harness_board, monkeypatch):
    from orchestrator.runtime.device import remote
    shell, built, state_dir = harness_board
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    _older_rule(shell.board.root)
    with pytest.raises(hr.HarnessRunError) as e:
        hr.start_run(shell, built, None, "dry", state_dir=state_dir)
    assert e.value.result == "not-ready"
    audit = Audit()
    plan = hr.ready_plan(lambda: shell, built, actor="cli")
    assert not plan.ready and plan.found.dropin == "old" and plan.found.missing() == ["dropin-update"]
    assert any(line.startswith("Replace the older sudo rule") for line in plan.lines) and not plan.problem
    assert hr.ready_setup(lambda: shell, built, plan, approved=False, audit=audit, actor="cli") is None
    assert plan.found.needs_password()
    done = hr.ready_setup(lambda: shell, built, plan, approved=True, password=PASSWORD, audit=audit, actor="gui")
    assert done.ok and done.changed == ("dropin",) and done.updated == ("dropin",)
    assert "older sudo rule" in done.text() and "replaced" in done.text()
    rule = (shell.board.root / "etc/sudoers.d/llmcf-install").read_text()
    assert "harness-records *" in rule and PASSWORD not in repr(audit.rows)
    # a second time: nothing to do, nothing changed, and it says so
    again = hr.ready_plan(lambda: shell, built, actor="cli")
    assert again.ready and hr.ready_setup(lambda: shell, built, again, approved=True, audit=audit).changed == ()
    assert (shell.board.root / "etc/sudoers.d/llmcf-install").read_text() == rule
    assert [row[2] for row in audit.rows] == ["declined", "approved", "set_up", "unchanged"]
    assert all(row[1] == "harness_readiness" for row in audit.rows)
    assert audit.rows[-1][3]["detail"] == hr.READY_THERE
    run = hr.start_run(shell, built, None, "dry", state_dir=state_dir)
    assert run.status == "running"


def test_an_ai_never_sets_up_the_readiness(harness_board):
    shell, built, _state = harness_board
    for call in (lambda: hr.ready_plan(lambda: shell, built, actor="mcp"),
                 lambda: hr.ready_setup(lambda: shell, built, hr.ReadyPlan(None, ()), approved=True, actor="ai")):
        with pytest.raises(hr.HarnessRunError) as e:
            call()
        assert e.value.result == "person-only" and "Tell the person" in e.value.next_step


# --- the person's channels ---------------------------------------------------------------------------------------------

def test_an_ai_never_starts_answers_or_stops_a_harness_run():
    root = Path(__file__).resolve().parents[1] / "orchestrator" / "tools"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in ("start_run", "answer_run", "stop_run", "collect_report", "follow_on_board", "bundle-harness",
                     "harness_window", "ready_plan", "ready_setup", "report_on_board", "fetch_records",
                     "ready_setup_on_board"):
            assert word not in text, f"{path.name} names {word}"
    assert "hr.scrubbed(" in (root / "tools_bundle.py").read_text(encoding="utf-8")


@pytest.fixture
def cli_harness(harness_board, monkeypatch):
    from rich.console import Console
    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import remote
    from test_bundle_ui import _stored_board
    shell, built, state = harness_board
    _stored_board(state)
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(cli_module, "_open_device", lambda *a, **kw: (shell, "key"))
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    monkeypatch.setattr(cli_module, "console", Console(width=1000, color_system=None))
    return built, state


def test_the_cli_asks_runs_with_the_answer_typed_and_reports(cli_harness, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    built, state = cli_harness
    res = invoke_plain("bundle", "harness", "run", built.bundle_id)
    assert res.code == 1 and "needs your answer at a terminal" in res.out and hr.list_runs(state) == []
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "harness", "run", built.bundle_id, input="y\ny\nagent-account\n")
    assert res.code == 0, res.out
    assert "would install the demo agent" in res.out and "set up for agent-account" in res.out
    assert "finished with findings" in res.out
    runs = invoke_plain("bundle", "harness", "report", built.bundle_id)
    assert runs.code == 0 and runs.out.count("findings") == 1 and "done" in runs.out
    run = hr.list_runs(state)[0].run
    one = invoke_plain("bundle", "harness", "report", built.bundle_id, run, "--json")
    assert json.loads(one.stdout)["report"]["outcome"] == "findings"
    res = invoke_plain("bundle", "harness", "run", built.bundle_id, "--assume-yes", "--skip-dry-run", input="y\n")
    assert res.code == 1 and "carries no assume-yes switch" in res.out


def test_the_cli_takes_a_harness_in(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: tmp_path / "state")
    src = tmp_path / "h"
    (src / "installer").mkdir(parents=True)
    (src / "installer" / "install.sh").write_text("#!/bin/sh\nexit 0\n")
    res = invoke_plain("bundle", "harness", "take-in", "mine", str(src), "--kind", "folder", "--installer",
                       "installer/install.sh", "--dry-run-arg=--dry-run", "--exit-code", "0=done",
                       "--exit-code", "2=findings")
    assert res.code == 0 and "Took in harness mine" in res.out, res.out
    record = hz.stored(tmp_path / "state", "mine")[0]
    assert record["spec"]["dry_run_args"] == ["--dry-run"] and record["spec"]["exit_codes"] == {"0": "done",
                                                                                                "2": "findings"}
    bad = invoke_plain("bundle", "harness", "take-in", "mine", str(src), "--kind", "folder", "--installer",
                       "installer/install.sh", "--exit-code", "0=fine")
    assert bad.code == 1 and "CODE=done" in bad.out


def test_the_cli_offers_the_missing_readiness_and_goes_on(cli_harness, harness_board, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    built, state = cli_harness
    shell = harness_board[0]
    _older_rule(shell.board.root)
    res = invoke_plain("bundle", "harness", "dry-run", built.bundle_id)
    assert res.code == 1 and hr.list_runs(state) == [], res.out
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    no = invoke_plain("bundle", "harness", "dry-run", built.bundle_id, input="y\nn\n")
    assert no.code == 1 and "Replace the older sudo rule" in no.out and "Nothing was set up" in no.out, no.out
    res = invoke_plain("bundle", "harness", "dry-run", built.bundle_id, input=f"y\ny\n{PASSWORD}\n")
    assert res.code == 0, res.out
    assert "older sudo rule" in res.out and "replaced" in res.out and hr.READY_AFTER in res.out
    assert "would install the demo agent" in res.out and "brought no account" in res.out
