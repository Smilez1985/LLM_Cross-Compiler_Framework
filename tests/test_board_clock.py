"""The PC's time for a board without a battery-backed clock (runtime/device/board_clock.py): the reading at every
contact, the set only with the person's standing yes through llmcf-install's typed clock action, the warning without
it, keeping the time after a bundle's root apply, the person's answer and the set by hand (person_steps), and the CLI."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.remote import gate
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines the board operations
from orchestrator.runtime.device import board_clock, person_steps
from orchestrator.runtime.profile import ProfileStore

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="needs a POSIX sh")
DEVICE = "0123456789abcdef"


class Audit:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fail:
            raise OSError("audit log full")
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


class Board:
    """A session stand-in: answers clock-read with the board's time (`offset` seconds from now) and clock-root with
    `root_answer`; keeps every operation it was sent."""

    def __init__(self, offset=0, rtc="none", keeper="none", root_answer=None):
        self.offset, self.rtc, self.keeper = offset, rtc, keeper
        self.root_answer = root_answer or "llmcf-bundle: result=ok changed=1 set=X rtc_written=no saved=timesyncd"
        self.sent = []

    def run(self, script, timeout=None, **_kw):
        self.sent.append((script.verb, script.args))
        if script.verb == "clock-read":
            return SimpleNamespace(status=0, out=f"now={int(time.time()) + self.offset}\nrtc={self.rtc}\n"
                                                 f"keeper={self.keeper}\nsync=no\n", err="")
        if script.verb == "clock-root":
            return SimpleNamespace(status=0, out=self.root_answer + "\n", err="")
        raise AssertionError(f"unexpected operation {script.verb}")


def settings(state, **values):
    ProfileStore(state).save_deploy_settings(DEVICE, values)


# --- reading -------------------------------------------------------------------------------------------------------

def test_a_reading_is_parsed_with_its_rtc_and_offset():
    r = board_clock.parse_reading("now=1794313805\nrtc=946684800\nkeeper=fake-hwclock\nsync=no\n", 1794310205.0)
    assert r.offset == 3600 and r.off and r.rtc == "reset" and not r.working_rtc and r.keeper == "fake-hwclock"
    r = board_clock.parse_reading("now=1794313805\nrtc=1794313800\nkeeper=bogus\n", 1794313790.0)
    assert r.offset == 15 and not r.off and r.working_rtc and r.keeper == "none" and r.sync == ""
    assert board_clock.parse_reading("rtc=none\n", 1.0) is None
    assert board_clock.parse_reading("now=$(id)\n", 1.0) is None
    assert board_clock.offset_text(-45) == "45 s behind" and board_clock.offset_text(3720) == "1 h 2 min ahead"


@POSIX
def test_the_read_script_reads_only_and_answers_this_systems_time():
    out = subprocess.run(["sh", "-c", board_clock.READ_SCRIPT], capture_output=True, timeout=30).stdout.decode()
    reading = board_clock.parse_reading(out, time.time())
    assert reading is not None and abs(reading.offset) <= 5
    assert re.search(r"^rtc=", out, re.M) and re.search(r"^keeper=", out, re.M)


def test_the_gate_knows_both_operations_and_the_clock_argument_is_checked():
    assert gate.operation("clock-read").gate and gate.operation("clock-root").gate
    assert gate.script("clock-root", "keep").args == ("keep",)
    assert gate.script("clock-root", "2026-11-10T12:30:05Z")
    for bad in ("now", "2026-11-10 12:30:05", "2026-11-10T12:30:05Z;id", "@1794313805", "1999-01-01T00:00:00Z"):
        with pytest.raises(gate.GateRefused):
            gate.script("clock-root", bad)
    body = gate.script("clock-root", "keep")
    # root only through llmcf-install's sudo rule: sudo -n, never a password, never another program
    assert 'sudo -n "$llmcf_fx" clock keep' in body and "sudo -S" not in body and "llmcf_pw" not in body
    assert "llmcf_dropin_ok clock keep" in body and "llmcf_fixed_ok" in body


@POSIX
def test_the_gates_clock_kind_takes_exactly_keep_and_a_utc_stamp():
    sh = gate.KINDS["clock"].sh
    for value, ok in (("keep", True), ("2026-11-10T12:30:05Z", True), ("2026-11-10T12:30:05", False),
                      ("keep;id", False), ("", False), ("2026-11-10T12:30:05Zx", False)):
        done = subprocess.run(["sh", "-c", f"llmcf_k_clock() {{ {sh}; }}; llmcf_k_clock \"$1\"", "sh", value],
                              capture_output=True, timeout=10)
        assert (done.returncode == 0) == ok, value


def test_the_sudo_rule_names_the_clock_action_and_an_older_rule_is_old():
    from orchestrator.runtime.bundle import gate_ops
    assert "$llmcf_fx clock *" in gate_ops._setup_body("sudo", "apt", "b1", "a" * 64)
    assert "llmcf_dropin_ok clock llmcf-check" in gate_ops._ready_body("a" * 64)


# --- at a contact --------------------------------------------------------------------------------------------------

def test_off_without_the_yes_is_a_warning_naming_the_offset_and_nothing_is_set(tmp_path):
    board = Board(offset=-7200)
    state = board_clock.on_contact(board, tmp_path, DEVICE, via="cli", audit=Audit(), label="pi")
    assert [verb for verb, _ in board.sent] == ["clock-read"]
    assert state.reading.off and not state.set_ok and "2 h 0 min behind" in state.warning
    assert "--clock-from-pc yes" in state.warning
    record = board_clock.load_record(tmp_path, DEVICE)
    assert record["rtc"] == "none" and record["warning"] == state.warning and "set_at" not in record
    settings(tmp_path, clock_from_pc="no", clock_answered_at="2026-11-10T12:00:00Z")
    state = board_clock.on_contact(Board(offset=600), tmp_path, DEVICE, via="cli", audit=Audit())
    assert "your answer for this board is not to set it" in state.warning


def test_off_with_the_standing_yes_is_set_through_the_typed_action_and_audited(tmp_path):
    settings(tmp_path, clock_from_pc="yes", clock_answered_at="2026-11-10T12:00:00Z")
    board, audit = Board(offset=86400 * 3), Audit()
    state = board_clock.on_contact(board, tmp_path, DEVICE, via="tool", audit=audit)
    assert state.set_ok and not state.warning
    verb, args = board.sent[1]
    assert verb == "clock-root" and re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", args[0])
    assert abs(time.mktime(time.strptime(args[0], "%Y-%m-%dT%H:%M:%SZ")) - time.mktime(time.gmtime())) < 5
    row = audit.rows[0]
    assert row["tool"] == board_clock.AUDIT_TOOL and row["outcome"] == "ok"
    assert row["arguments"]["offset_s"] == pytest.approx(86400 * 3, abs=2) and row["arguments"]["device_id"] == DEVICE
    record = board_clock.load_record(tmp_path, DEVICE)
    assert record["set_by"] == "tool" and record["saved"] == "timesyncd" and record["offset_s"] == 0


def test_within_a_minute_nothing_is_set_and_the_last_warning_ends(tmp_path):
    settings(tmp_path, clock_from_pc="yes", clock_answered_at="2026-11-10T12:00:00Z")
    board_clock.save_record(tmp_path, DEVICE, {"warning": "an old one"})
    board = Board(offset=30)
    state = board_clock.on_contact(board, tmp_path, DEVICE, via="cli")
    assert [verb for verb, _ in board.sent] == ["clock-read"] and not state.warning
    assert board_clock.load_record(tmp_path, DEVICE)["warning"] == ""


def test_a_board_without_the_root_readiness_gets_a_warning_and_the_failed_set_is_audited(tmp_path):
    settings(tmp_path, clock_from_pc="yes", clock_answered_at="2026-11-10T12:00:00Z")
    board = Board(offset=-3600, root_answer="llmcf-bundle: result=not-ready changed=0 the board's sudo rule ...")
    audit = Audit()
    state = board_clock.on_contact(board, tmp_path, DEVICE, via="cli", audit=audit)
    assert not state.set_ok and "root readiness" in state.warning and "1 h 0 min behind" in state.warning
    assert audit.rows[0]["outcome"] == "error"
    assert board_clock.load_record(tmp_path, DEVICE)["last_set_result"] == "not-ready"


def test_a_board_that_does_not_answer_is_left_as_it_is():
    class Old:
        def run(self, script, timeout=None):
            return SimpleNamespace(status=126, out="", err="llmcf-gate: refused: unknown operation")

    class Broken:
        def run(self, script, timeout=None):
            raise OSError("gone")
    assert board_clock.on_contact(Old(), Path("."), DEVICE, via="cli") is None
    assert board_clock.on_contact(Broken(), Path("."), DEVICE, via="cli") is None


# --- keeping the time after a bundle's root apply --------------------------------------------------------------------

def test_keeping_the_time_runs_only_on_a_board_without_a_working_rtc(tmp_path):
    board = Board(root_answer="llmcf-bundle: result=ok changed=1 keep=fake-hwclock")
    assert board_clock.keep_after_apply(board.run, tmp_path, DEVICE, via="cli", audit=Audit()) is None   # never read
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "yes"})
    assert board_clock.keep_after_apply(board.run, tmp_path, DEVICE, via="cli", audit=Audit()) is None
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "reset"})
    audit = Audit()
    done = board_clock.keep_after_apply(board.run, tmp_path, DEVICE, via="gui", audit=audit)
    assert done.ok and board.sent == [("clock-root", ("keep",))]
    assert "fake-hwclock" in board_clock.keep_line(done) and audit.rows[0]["tool"] == board_clock.KEEP_AUDIT_TOOL
    gone = board_clock.parse_run("llmcf-bundle: result=unavailable changed=0 keep=none neither fake-hwclock nor ...")
    assert not gone.ok and "neither fake-hwclock nor systemd-timesyncd" in board_clock.keep_line(gone)


def test_the_root_question_of_an_apply_names_keeping_the_time(tmp_path):
    from orchestrator.runtime.bundle import service
    built = SimpleNamespace(bundle_id="b1", manifest=SimpleNamespace(board=SimpleNamespace(device_id=DEVICE)))
    assert board_clock.KEEP_NOTE not in service.root_question("apply", built, "pi", 2, tmp_path)
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "none"})
    assert service.root_question("apply", built, "pi", 2, tmp_path).endswith(board_clock.KEEP_NOTE)
    assert board_clock.KEEP_NOTE not in service.root_question("uninstall", built, "pi", 2, tmp_path)


# --- the person's answer and the set by hand -----------------------------------------------------------------------

def test_the_question_is_asked_once_and_its_answer_needs_its_audit_record(tmp_path):
    store, asked = ProfileStore(tmp_path), []

    def ask():
        asked.append(1)
        return True
    assert person_steps.ask_clock_once(store, DEVICE, ask, audit=Audit(fail=True), via="cli") is None
    assert "clock_from_pc" not in store.load_deploy_settings(DEVICE)   # no record, no answer
    audit = Audit()
    assert person_steps.ask_clock_once(store, DEVICE, ask, audit=audit, via="gui") is True
    assert person_steps.ask_clock_once(store, DEVICE, ask, audit=audit, via="gui") is None
    assert len(asked) == 2
    saved = store.load_deploy_settings(DEVICE)
    assert saved["clock_from_pc"] == "yes" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                                                              saved["clock_answered_at"])
    assert audit.rows[0]["tool"] == person_steps.CLOCK_CONSENT[0] and audit.rows[0]["actor"] == "user"
    assert board_clock.clock_yes(tmp_path, DEVICE) is True


def test_a_changed_answer_in_the_settings_is_recorded_first(tmp_path):
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    store = ProfileStore(tmp_path)
    answers = []
    new = person_steps.change_settings(store, DEVICE, None, DeploySettings(), {"clock_from_pc": "no"},
                                       may_use_sudo=lambda: False, record_sudo_consent=lambda: None,
                                       stamp=lambda: "2026-11-10T12:00:00Z", record_clock_answer=answers.append)
    assert answers == ["no"] and new.clock_answered_at == "2026-11-10T12:00:00Z"
    with pytest.raises(person_steps.BoardStepError):
        person_steps.change_settings(store, DEVICE, None, new, {"clock_from_pc": "yes"},
                                     may_use_sudo=lambda: False, record_sudo_consent=lambda: None,
                                     stamp=lambda: "2026-11-10T12:00:00Z")
    with pytest.raises(SettingsError):
        DeploySettings(clock_from_pc="maybe")


def test_the_set_by_hand_sets_whatever_the_offset_and_is_audited_as_the_persons(tmp_path):
    board, audit = Board(offset=5), Audit()
    state = person_steps.set_clock_by_hand(board, tmp_path, DEVICE, via="cli", audit=audit)
    assert state.set_ok and [verb for verb, _ in board.sent] == ["clock-read", "clock-root"]
    assert audit.rows[0]["actor"] == "user" and audit.rows[0]["arguments"]["reason"] == "by-hand"
    lines = board_clock.state_lines(tmp_path, DEVICE)
    assert any(line.startswith("Last set: 20") and line.endswith("(cli)") for line in lines)


def test_no_tool_of_an_ai_gives_the_answer_or_sets_the_clock_by_hand():
    from test_deploy_tools import AI_REACHABLE, REPO_ROOT
    for entry in AI_REACHABLE:
        path = REPO_ROOT / entry
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            text = file.read_text(encoding="utf-8")
            for name in ("ask_clock_once", "change_clock_answer", "set_clock_by_hand", "clock_from_pc"):
                assert name not in text, f"{file.relative_to(REPO_ROOT)} names {name}"


# --- the CLI -------------------------------------------------------------------------------------------------------

def test_the_cli_shows_the_clock_and_takes_the_answer_in_the_settings(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.runtime.profile import parse_profile
    state = tmp_path / "state"
    store = ProfileStore(state)
    store.save_device(DEVICE, parse_profile("SCHEMA=2\nOS=linux\nARCH=aarch64\n"), label="pi")
    board_clock.save_record(state, DEVICE, {"offset_s": -7300, "rtc": "reset", "keeper": "none",
                                            "read_at": "2026-11-10T12:00:00Z"})
    audit = Audit()
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(cli, "_device_audit", lambda ctx, state_dir: audit)
    runner = CliRunner()
    out = runner.invoke(cli.cli, ["device", "clock", DEVICE])
    assert out.exit_code == 0, out.output
    assert "2 h 1 min behind" in out.output and "reset, no battery" in out.output and "not asked yet" in out.output
    out = runner.invoke(cli.cli, ["device", "settings", DEVICE, "--clock-from-pc", "yes"])
    assert out.exit_code == 0, out.output
    assert "Clock from this computer: yes" in out.output
    assert audit.rows[0]["tool"] == person_steps.CLOCK_CONSENT[0]
    assert store.load_deploy_settings(DEVICE)["clock_from_pc"] == "yes"
