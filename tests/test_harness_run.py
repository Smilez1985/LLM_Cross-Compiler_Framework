"""The person's harness run end to end (orchestrator/runtime/bundle/harness_run.py): a bundle with a harness component
built and deployed to a fake board behind llmcf's real gate, its root actions applied by the real llmcf-install test
build (which unpacks the tree and checks its checksum), then the dry run, the interactive run with a question answered
live, reading on from the stored offset in a new session (a dropped connection), the records read back and the
report; the scrubbed view an AI may read holds no account, no path and nothing the installer printed.

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

    # an AI reads states, numbers and flag codes only
    view = hr.scrubbed(again, rep)
    flat = repr(view)
    assert view["outcome"] == "findings" and view["exit_code"] == 1
    assert "agent-account" not in flat and str(state_dir) not in flat and "/" not in flat
    # the audit log: starts and reports, never the answer
    outcomes = [row[2] for row in audit.rows]
    assert outcomes == ["started", "done", "started", "findings"]
    assert all(row[1] == "harness_run" and "agent-account" not in repr(row) for row in audit.rows)


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


def test_the_records_are_read_back_into_the_watch_layout(tmp_path):
    text = ("llmcf-bundle: helper llmcf-install 0.1.0\nllmcf-harness: file exit_code\n> 0\n"
            "llmcf-harness: file after/accounts.txt\n> agent:990:990:/usr/sbin/nologin\n"
            "llmcf-harness: file ../escape\n> nothing\nllmcf-harness: truncated\nllmcf-bundle: result=ok changed=0\n")
    assert hr.parse_records(text, tmp_path) is True
    assert (tmp_path / "exit_code").read_text() == "0\n"
    assert (tmp_path / "after" / "accounts.txt").read_text().startswith("agent:990")
    assert not (tmp_path.parent / "escape").exists() and not (tmp_path / "escape").exists()


# --- the person's channels ---------------------------------------------------------------------------------------------

def test_an_ai_never_starts_answers_or_stops_a_harness_run():
    root = Path(__file__).resolve().parents[1] / "orchestrator" / "tools"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in ("start_run", "answer_run", "stop_run", "collect_report", "follow_on_board", "bundle-harness",
                     "harness_window"):
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
