"""Review of chain build-done-signal (regression tests of the confirmed findings):

1. A doorbell queued by a process that then ends (every `llm-cli build start`) is still sent: the publisher thread is
   a daemon, so the process's end used to drop it silently (the activity producer flushes at exit; the doorbell did
   not). The CLI also waits for the orchestrator's end announcement before it exits.
2. Plain mqtt (and so the password in clear) only for a broker that is surely this computer: "localhost" or a loopback
   address; a name under .localhost goes to the system resolver, which may answer with any address, so it needs TLS.
3. A build Ditto only cancelled (the person's own build) gives Ditto no turn of its own: it neither started it nor
   waited for it.
4. A job folder in the work folder that is a link (the container writes that folder) is never followed: neither the
   end record behind it counts, nor is a stale record behind it removed.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.build_end import read_end_record
from orchestrator.Core.ditto_build_turns import BuildTurns, note_wait
from orchestrator.Core import ditto_build_turns as turns
from orchestrator.Core.doorbell import DoorbellRefused, broker_of, check_broker

REPO = Path(__file__).resolve().parents[1]
REQ = "req_1a2b3c4d"
RECORD = "status done\nexit 0\nstage target\njob build\ninputs -\ntime 2026-10-30T12:00:00Z\n"


class Config(dict):
    user_state_dir = None

    def get(self, key, default=None):
        return super().get(key, default)


# --- 1. a process that ends still sends its doorbell -------------------------------------------------------------

def test_a_queued_doorbell_is_sent_when_the_process_ends(tmp_path):
    sent = tmp_path / "sent.txt"
    script = textwrap.dedent(f"""
        import asyncio, sys
        sys.path.insert(0, {str(REPO)!r})
        from orchestrator.Core.doorbell import Doorbell

        class Slow:
            def __init__(self, broker, password):
                pass
            async def __aenter__(self):
                await asyncio.sleep(0.5)      # a broker that takes a moment to answer
                return self
            async def __aexit__(self, *exc):
                return False
            async def publish(self, topic, payload, qos, retain, timeout):
                with open({str(sent)!r}, "a", encoding="utf-8") as f:
                    f.write(topic + "\\n")

        class Config(dict):
            user_state_dir = None
            def get(self, key, default=None):
                return super().get(key, default)

        bell = Doorbell(Config(mqtt_broker_url="mqtt://localhost:1883"), client_factory=Slow)
        bell.on_build_end({{"request_id": "{REQ}", "end_reason": "done"}})
        # the process ends right away, as llm-cli does after a build
    """)
    run = subprocess.run([sys.executable, "-c", script], cwd=str(tmp_path), capture_output=True, text=True,
                         timeout=60, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert run.returncode == 0, run.stderr
    assert sent.is_file(), "the doorbell was dropped when the process ended"
    assert f"llmcf/v1/build/{REQ}/end" in sent.read_text(encoding="utf-8")


def test_the_orchestrator_says_when_an_end_was_announced():
    from orchestrator.Core.orchestrator import LLMOrchestrator
    orchestrator = LLMOrchestrator.__new__(LLMOrchestrator)
    import threading
    orchestrator._end_lock = threading.Lock()
    orchestrator._ended = set()
    started = time.monotonic()
    assert orchestrator.wait_end_announced(REQ, timeout=0.3) is False
    assert time.monotonic() - started >= 0.25

    def announce():
        time.sleep(0.2)
        with orchestrator._end_lock:
            orchestrator._ended.add(REQ)

    threading.Thread(target=announce, daemon=True).start()
    assert orchestrator.wait_end_announced(REQ, timeout=5) is True


def test_the_cli_waits_for_the_end_announcement_and_the_doorbell(monkeypatch):
    from orchestrator import cli
    calls = []
    orchestrator = SimpleNamespace(wait_end_announced=lambda rid, timeout: calls.append(("announced", rid)) or True)
    framework = SimpleNamespace(doorbell=SimpleNamespace(flush=lambda timeout: calls.append(("doorbell",)) or True),
                                activity=SimpleNamespace(flush=lambda timeout: calls.append(("feed",)) or True))
    ctx = SimpleNamespace(orchestrator=orchestrator, framework_manager=framework)
    cli._deliver_end_notices(ctx, REQ)
    assert calls == [("announced", REQ), ("feed",), ("doorbell",)]
    cli._deliver_end_notices(SimpleNamespace(), REQ)   # a stand-in context without them: nothing to wait for


# --- 2. plain mqtt only for a broker that is surely this computer -------------------------------------------------

@pytest.mark.parametrize("url", ["mqtt://broker.localhost:1883", "mqtt://evil.localhost."])
def test_a_name_under_localhost_needs_tls(url):
    config = Config(mqtt_broker_url=url)
    with pytest.raises(DoorbellRefused) as refused:
        check_broker(broker_of(config), config, password=True)
    assert refused.value.code == "no_tls"


@pytest.mark.parametrize("url", ["mqtt://localhost:1883", "mqtt://LOCALHOST", "mqtt://127.0.0.1", "mqtt://[::1]"])
def test_this_computer_still_takes_plain_mqtt(url):
    config = Config(mqtt_broker_url=url)
    check_broker(broker_of(config), config, password=True)


# --- 3. a build Ditto only cancelled gives it no turn -------------------------------------------------------------

def _row(reason, caller, by):
    key = {"done": "activity.build.ready", "cancelled": "activity.build.stopped"}.get(
        reason, f"activity.build.error.{reason}")
    return SimpleNamespace(kind="job_result", text_key=key, refs=SimpleNamespace(request_id=REQ),
                           params={"reason": reason, "by": by}, sender=SimpleNamespace(caller=caller))


@pytest.fixture
def no_waits():
    turns._waited.clear()
    yield
    turns._waited.clear()


def test_the_persons_build_that_ditto_cancelled_gives_no_turn(tmp_path, no_waits):
    from orchestrator.Core.activity import row_for_build
    # the producer's row of the person's build that Ditto cancelled: its caller is Ditto (the canceller)
    row = row_for_build({"request_id": REQ, "status": "cancelled", "end_reason": "cancelled", "owner": "",
                         "cancelled_by": "ditto"})
    assert row.caller == "ditto" and row.params["by"] == "ditto"
    assert BuildTurns().offer(_row("cancelled", "ditto", "ditto")) is None


def test_a_build_ditto_waited_for_and_cancelled_still_gives_its_turn(no_waits):
    note_wait("ditto", REQ)
    assert BuildTurns().offer(_row("cancelled", "ditto", "ditto")) is not None


def test_a_build_ditto_started_still_gives_its_turn(no_waits):
    assert BuildTurns().offer(_row("failed", "ditto", "")) is not None
    assert BuildTurns().offer(_row("cancelled", "ditto", "mcp:laptop")) is not None   # an app stopped Ditto's build


# --- 4. a linked job folder is never followed ---------------------------------------------------------------------

def _linked_job_folder(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "build.end").write_text(RECORD, encoding="ascii")
    stages = tmp_path / "work" / "stages"
    stages.mkdir(parents=True)
    try:
        (stages / "build").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a folder link")
    return outside, stages / "build" / "build.end"


def test_an_end_record_behind_a_linked_job_folder_does_not_count(tmp_path):
    _outside, record = _linked_job_folder(tmp_path)
    assert read_end_record(record) is None


def test_a_stale_record_behind_a_linked_job_folder_is_not_removed(tmp_path):
    from orchestrator.Core.builder import BuildEngine
    outside, record = _linked_job_folder(tmp_path)
    BuildEngine._forget_end_record(record)
    assert (outside / "build.end").is_file(), "a file outside the work folder was removed"
    plain = tmp_path / "plain" / "stages" / "build"
    plain.mkdir(parents=True)
    (plain / "build.end").write_text(RECORD, encoding="ascii")
    BuildEngine._forget_end_record(plain / "build.end")
    assert not (plain / "build.end").exists()
