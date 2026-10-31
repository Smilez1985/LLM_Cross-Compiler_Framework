"""A build ends on its done marker (DECISIONS round 40, TASK 2): the BuildEngine reads the stage runner's end record
from the mounted work folder, switches the step's clock off once it says done (no soft cap or idle stop after the
marker), gives the container a short grace and then stops it (still done); an exit 0 without the record is failed;
every build has one end reason (done, failed, cancelled, soft_cap, idle) in its progress, its record and its
build_info.json.

Docker is the fake of tests/test_review_build_engine.py."""

import time

import pytest
import requests

from orchestrator.Core import build_end
from orchestrator.Core import builder as builder_module
from orchestrator.Core.builder import BuildProgress, BuildStatus
from orchestrator.utils.step_limits import StepClock, StepLimits, StepTimedOut
from test_review_build_engine import (FakeContainer, client, config_for, engine, make_engine,  # noqa: F401
                                      write_end_record)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_finished_clock_never_stops_its_step():
    """The step's clock after the done marker: neither the idle timeout nor an AI build's soft cap fires."""
    clock = FakeClock()
    step = StepClock(StepLimits(soft_cap=10, idle=5, ai_started=True), "build", clock=clock)
    clock.now = 4
    assert step.check() is None
    step.finish()
    clock.now = 1000     # far past both limits, without any output
    assert step.check() is None
    assert step.next_wait(30) == 30


def test_without_finish_the_same_clock_stops_the_step():
    """Characterization: before the marker the limits work as before."""
    clock = FakeClock()
    step = StepClock(StepLimits(soft_cap=10, idle=5, ai_started=True), "build", clock=clock)
    clock.now = 6
    with pytest.raises(StepTimedOut) as stop:
        step.check()
    assert stop.value.kind == "idle"


def _work(tmp_path):
    return tmp_path / "cache" / "builds" / "b1" / "work"


def _idle_after_its_record(client, tmp_path, status="done", code=0):
    """A stage container that writes its end record, then prints nothing and never exits by itself."""
    work = _work(tmp_path)
    container = FakeContainer(client, "llm-build-b1", auto_exit=False, lines=["=== Stage 4/4: target ==="],
                              volumes={str(work): {"bind": "/build-cache/work", "mode": "rw"}})

    def idle():
        if not (work / "stages" / "build" / "build.end").exists():
            write_end_record(container.volumes, container.name, code if status == "failed" else 0)
        time.sleep(0.05)
        return requests.exceptions.ReadTimeout("Read timed out.")

    container.wait_error = idle
    return container, work


@pytest.mark.parametrize("owner", ["", "mcp#s1"], ids=["person", "ai"])
def test_idle_and_soft_cap_never_fire_after_the_done_marker(engine, client, tmp_path, monkeypatch, owner):
    """Regression: a container that idles after its done record ended as an idle (or soft cap) stop before.
    Changed on purpose (the done record is checked against the container's real exit status): after the grace the
    framework stops it, and a stopped container is not done."""
    monkeypatch.setattr(builder_module, "END_RECORD_POLL", 0.05)
    monkeypatch.setattr(build_end, "END_GRACE", 0.6)
    container, work = _idle_after_its_record(client, tmp_path)
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    cfg = config_for(tmp_path, build_timeout=0.1, build_idle_timeout=0.2, owner=owner)

    code = engine._run_container(cfg, progress, container, "CONT: ",
                                 end_record=build_end.end_record_path(work, "build"))

    assert code == 137
    assert progress.end_record is not None and progress.end_record.done and progress.end_record.sealed
    assert any("the framework stopped it, so the build is not counted as finished." in line
               for line in progress.logs)
    assert not [w for w in progress.warnings if "printed nothing" in w]
    with pytest.raises(RuntimeError, match="says done, but the container exited with 137"):
        engine._ended_by_record(progress, code, "CONT: ")


def test_an_exit_0_without_the_end_record_is_a_failure(engine, client, tmp_path):
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    progress.end_record = None
    with pytest.raises(RuntimeError, match="ended without its end record"):
        engine._ended_by_record(progress, 0, "CONT: ")


def test_a_failed_record_keeps_the_exit_code(engine):
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    progress.end_record = build_end.EndRecord("failed", 4, "target")
    assert engine._ended_by_record(progress, 4, "CONT: ") == 4


def test_only_the_runners_done_record_with_exit_0_is_done(engine):
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    progress.end_record = build_end.EndRecord("done", 0, "target", sealed=True)
    assert engine._ended_by_record(progress, 0, "CONT: ") == 0
    progress.end_record = build_end.EndRecord("done", 0, "convert", job="imatrix", sealed=True)
    assert engine._ended_by_record(progress, 0, "IMATRIX: ", job="imatrix") == 0
    progress.end_record = build_end.EndRecord("done", 0, "target", sealed=False)
    with pytest.raises(RuntimeError, match="not written by the stage runner"):
        engine._ended_by_record(progress, 0, "CONT: ")


def test_a_seal_fits_only_its_own_key_and_body(tmp_path):
    key = "ab" * 16
    body = build_end.record_body("done", 0, "target", "build", "-", "2026-10-31T12:00:00Z")
    path = tmp_path / "build.end"
    path.write_text(f"{body}\nseal {build_end.seal_of(body, key)}\n", encoding="ascii")
    assert build_end.read_end_record(path, key=key).sealed
    assert not build_end.read_end_record(path, key="cd" * 16).sealed
    assert not build_end.read_end_record(path).sealed
    forged = body.replace("status done", "status done").replace("exit 0", "exit 0").replace("target", "convert")
    path.write_text(f"{forged}\nseal {build_end.seal_of(body, key)}\n", encoding="ascii")
    assert not build_end.read_end_record(path, key=key).sealed


def test_the_key_is_new_for_each_run_and_never_through_a_link(tmp_path):
    work = tmp_path / "work"
    first = build_end.new_end_key(work)
    second = build_end.new_end_key(work)
    assert first and second and first != second
    assert (work / "stages" / "build" / build_end.END_KEY).read_text(encoding="ascii").strip() == second
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    (linked / "stages").mkdir(parents=True)
    try:
        (linked / "stages" / "build").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a folder link")
    assert build_end.new_end_key(linked) is None
    assert not list(outside.iterdir())


def test_a_stale_record_is_removed_before_the_container_starts(engine, client, tmp_path, monkeypatch):
    work = _work(tmp_path)
    record = build_end.end_record_path(work, "build")
    record.parent.mkdir(parents=True)
    record.write_text("status done\nexit 0\nstage target\njob build\ninputs -\ntime 2026-10-30T12:00:00Z\n")
    container = FakeContainer(client, "llm-build-b1", exit_code=0, volumes={})   # exits 0, writes no record
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    assert engine._run_container(config_for(tmp_path), progress, container, "CONT: ", end_record=record) == 0
    assert progress.end_record is None and not record.exists()
    with pytest.raises(RuntimeError, match="ended without its end record"):
        engine._ended_by_record(progress, 0, "CONT: ")


@pytest.mark.parametrize("kind", ["soft_cap", "idle"])
def test_a_time_limit_is_its_own_end_reason(engine, tmp_path, monkeypatch, kind):
    def stopped(*args, **kwargs):
        raise StepTimedOut("stopped", kind)

    monkeypatch.setattr(engine, "_execute_build_modules", stopped)
    monkeypatch.setattr(engine, "_scan_image_security", lambda *a, **k: None)
    cfg = config_for(tmp_path)
    engine.build_model(cfg)
    progress = _finish(engine, "b1")
    assert progress.status == BuildStatus.FAILED and progress.end_reason == kind
    assert progress.end_facts()["reason"] == kind


def _finish(engine, build_id, seconds=10):
    deadline = time.monotonic() + seconds
    while engine.is_active(build_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    return engine.get_build_status(build_id)


def test_a_completed_build_is_done_with_its_end_in_build_info(engine, client, tmp_path):
    import json
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine, "b1")
    assert progress.status == BuildStatus.COMPLETED and progress.end_reason == "done"
    info = json.loads((tmp_path / "out" / "b1" / "build_info.json").read_text())
    assert info["end"] == {"reason": "done", "exit": 0, "stage": "target"}


def test_a_failed_build_is_failed(engine, client, tmp_path):
    client.stage_options["llm-build-"] = {"exit_code": 4}
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine, "b1")
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert progress.end_facts() == {"reason": "failed", "exit": 4, "stage": "target"}


def test_a_cancel_is_cancelled_and_leaves_the_note_for_the_stage_runner(engine, client, tmp_path):
    """TASK 7: cancel_build writes the note into the job folder whose stage runs, before it stops the container."""
    client.stage_options["llm-build-"] = {"auto_exit": False}
    engine.build_model(config_for(tmp_path))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(n.startswith("llm-build-") and e == "running"
                                                  for n, e in client.events):
        time.sleep(0.02)
    running = engine.cache_dir / "builds" / "b1" / "work" / "stages" / "build"
    running.mkdir(parents=True, exist_ok=True)
    (running / "running").write_text("target\n")
    assert engine.cancel_build("b1")
    assert (running / "cancelled").is_file()
    progress = _finish(engine, "b1")
    assert progress.status == BuildStatus.CANCELLED and progress.end_reason == "cancelled"


def test_the_build_record_names_the_end_reason(engine, client, tmp_path):
    from orchestrator.Core.build_records import read_records
    engine.build_model(config_for(tmp_path))
    _finish(engine, "b1")
    records = [r for r in read_records(engine.records_dir) if r.get("build_id") == "b1"]
    assert records and records[0]["end_reason"] == "done"


@pytest.mark.parametrize("text", ["status done\nexit 0\n", "status done\nexit 0\nstage ../x\n",
                                  "status maybe\nexit 0\n", "status done\nexit abc\n", ""])
def test_only_a_record_that_fits_counts(tmp_path, text):
    path = tmp_path / "build.end"
    path.write_text(text)
    record = build_end.read_end_record(path)
    if text == "status done\nexit 0\n":
        assert record is not None and record.done
    else:
        assert record is None


def test_a_linked_record_is_no_record(tmp_path):
    real = tmp_path / "real"
    real.write_text("status done\nexit 0\n")
    link = tmp_path / "build.end"
    link.symlink_to(real)
    assert build_end.read_end_record(link) is None


def test_the_vocabulary_maps_to_the_old_statuses():
    assert build_end.STATUS_OF == {"done": "completed", "failed": "error", "soft_cap": "error", "idle": "error",
                                   "cancelled": "cancelled"}
    assert set(build_end.END_REASONS) == set(build_end.STATUS_OF)
    assert build_end.exit_code("done") == 0 and build_end.exit_code("idle") == 1 and \
        build_end.exit_code("cancelled") == 2
