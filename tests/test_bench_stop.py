"""A cancelled benchmark stops on the board (wave-3 trust-followups open item; stage C wave 3c-2, chain boards).

llama-bench runs on the board on its own (nohup, RemoteSession.run_detached), so a Cancel used to leave it running
there. Now its PID is recorded in its folder (setsid makes it lead its own process group where the board has setsid)
and the board's `timeout` wraps it where one works. A Cancel (also one that closed the connection: then one new login
with the same host-key check), a Ctrl+C and the follower's own time limit stop exactly that PID or group, never a
program found by name. The folder's clean-up stays as it was (the next run's sweep).

The board is tests/test_device_runtime.py's FakeDevice: its commands run for real in a POSIX shell in a temporary
home, so the processes these tests stop are this test's own children.
"""

import os
import time

import pytest
import test_device_runtime as tdr
from test_remote_callers import device_base, source  # noqa: F401 - fixtures
from test_remote_callers_session import board_session, deploy_over  # noqa: F401 - fixtures

from orchestrator.runtime.device.remote import RemoteShell, verify_on_device

POSIX = tdr.POSIX

# A llama-bench that loads a model whose file says SLOW for a long time: it records its own PID and the PID of the
# child it waits for next to the model, so the test sees that both end (a process group, not only one PID).
SLOW_BENCH = tdr.FAKE_BENCH.replace(
    '-m) [ -f "$2" ] || { echo "error: failed to load model $2" >&2; exit 1; }',
    '-m) [ -f "$2" ] || { echo "error: failed to load model $2" >&2; exit 1; }\n'
    '      if grep -q SLOW "$2"; then echo $$ > "$2.bench"; sleep 300 & echo $! > "$2.child"; wait; fi')


def alive(pid: int) -> bool:
    """Whether `pid` still runs (a zombie nobody reaped yet counts as ended)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except OSError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


def started(model, seconds=10.0) -> None:
    """Waits until the slow benchmark runs on the board (it wrote its own PID and its child's)."""
    end = time.monotonic() + seconds
    path = model.with_name(model.name + ".child")
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.05)
    raise AssertionError("the benchmark did not start on the board")


def pids_of(model):
    started(model)
    return [int(model.with_name(model.name + suffix).read_text().strip()) for suffix in (".bench", ".child")]


def ended(pids, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not any(alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def slow_board(tmp_path, source, board_session, monkeypatch):  # noqa: F811 - fixtures
    """(board, session, build, slow model) with a deployed runtime whose llama-bench waits for a SLOW model."""
    from orchestrator.runtime.device import remote
    monkeypatch.setattr(remote, "BENCH_STOP_GRACE", 1)
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source, bench=tdr.fake_program(SLOW_BENCH))
    assert deploy_over(session, build, packaged).activated
    model = board.home / "models" / "slow.gguf"
    model.parent.mkdir(exist_ok=True)
    model.write_text("SLOW")
    return board, session, build, model


def bench(session, board, build, model):
    return verify_on_device(RemoteShell(session), home=str(board.home), plan_verify=build.plan["verify"],
                            pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                            bench_model=str(model), bench_allowed=True)


@POSIX
def test_a_cancel_that_closed_the_connection_stops_the_benchmark_s_process_group(slow_board):
    from orchestrator.remote.session import CancelToken
    from orchestrator.runtime.device.remote import RemoteError
    board, session, build, model = slow_board
    token = CancelToken()
    token.hold(session)
    real_answer, logins = board.answer, len(board.logins)

    def answer(command, stdin):
        if "echo running" in command and "nohup" not in command:
            started(model)
            token.cancel()   # the person presses Cancel while the session follows the benchmark
        return real_answer(command, stdin)

    board.answer = answer
    with pytest.raises(RemoteError) as e:
        bench(session, board, build, model)
    assert e.value.kind == "stopped"
    pids = pids_of(model)
    assert ended(pids), "the benchmark and its child ended on the board"
    assert len(board.logins) == logins + 1, "one new login for the stop (the cancel closed the connection)"
    stops = [c for c in board.commands if "kill -TERM" in c]
    assert len(stops) == 1 and "pkill" not in stops[0] and "killall" not in stops[0]
    (left,) = list(board.root.glob(".bench.*"))
    assert (left / ".lcf-bench.pid").is_file(), "the folder stays to the next run's sweep, as before"


@POSIX
def test_a_ctrl_c_stops_it_over_the_standing_connection(slow_board, monkeypatch):
    board, session, build, model = slow_board
    logins, real_pause = len(board.logins), session.pause
    polls = []

    def pause(seconds):
        polls.append(seconds)
        if len(polls) == 2:
            started(model)
            raise KeyboardInterrupt   # Ctrl+C at the terminal while the session follows the benchmark
        return real_pause(seconds)

    monkeypatch.setattr(session, "pause", pause)
    with pytest.raises(KeyboardInterrupt):
        bench(session, board, build, model)
    assert ended(pids_of(model)) and len(board.logins) == logins, "stopped without a new login"
    assert not list(board.root.glob(".bench.*")), "the standing connection still removes the folder, as before"


@POSIX
def test_the_follower_s_time_limit_stops_it_too(slow_board, monkeypatch):
    from orchestrator.runtime.device import remote
    from orchestrator.runtime.device.remote import RemoteError
    board, session, build, model = slow_board
    monkeypatch.setattr(remote, "BENCH_TIMEOUT", 6)
    with pytest.raises(RemoteError) as e:
        bench(session, board, build, model)
    assert e.value.kind == "job_timeout" and "within 6 s" in str(e.value)
    assert ended(pids_of(model))


@POSIX
def test_the_benchmark_runs_in_its_own_group_under_the_board_s_time_limit(slow_board):
    board, session, build, _model = slow_board
    fast = board.home / "models" / "fast.gguf"
    fast.write_text("GGUF")
    assert bench(session, board, build, fast).check("smoke").ok
    (start,) = [c for c in board.commands if "nohup" in c and "-o json" in c]
    assert "setsid timeout 900 env -i" in start and ".lcf-bench.pid" in start


@POSIX
def test_a_board_without_setsid_or_timeout_still_runs_it_and_stops_the_recorded_pid(slow_board):
    from orchestrator.runtime.device.remote import bench_stop_command
    board, session, build, _model = slow_board
    real_answer = board.answer
    board.answer = lambda command, stdin: ((0, b"nohup=yes\n", b"") if "command -v setsid" in command
                                           else real_answer(command, stdin))
    fast = board.home / "models" / "fast.gguf"
    fast.write_text("GGUF")
    assert bench(session, board, build, fast).check("smoke").ok
    (start,) = [c for c in board.commands if "nohup" in c and "-o json" in c]
    assert "setsid" not in start and "timeout 900" not in start
    assert 'kill -TERM "$p"' in bench_stop_command("/x/.bench.Ab12Cd34", group=False)
    assert 'kill -TERM -"$p"' in bench_stop_command("/x/.bench.Ab12Cd34", group=True)


def test_the_board_s_time_limit_is_named_in_the_check():
    """timeout's exit status 124 says that the board's own limit stopped llama-bench."""
    from orchestrator.runtime.device import remote
    source = open(remote.__file__, encoding="utf-8").read()
    assert "TIMED_OUT = 124" in source and "the board's time limit of" in source
