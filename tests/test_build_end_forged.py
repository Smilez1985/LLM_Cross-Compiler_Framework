"""A module script cannot end a build early as "done" (the forged end record).

Only the stage runner (targets/*/modules/build.sh, the command the container entrypoint starts) writes the end record,
after its last stage returned, sealed with a one-run key that the BuildEngine hands it in the job folder and that the
runner takes away before the first stage starts; the BuildEngine counts a build as done only when the container's
real exit status is 0 AND the record is sealed with that run's key AND it names the job's last stage. A record a module
wrote, a done record with an exit status other than 0, or a done record before the last stage is failed, with a plain
reason (orchestrator/Core/build_end.py).

The first half runs the real build.sh of both targets in the stub image of tests/test_build_stages.py; the second half
the BuildEngine on the fake Docker client of tests/test_review_build_engine.py."""

import shutil
import time
from pathlib import Path

import pytest
import requests

import test_review_build_engine as fakes
from conftest import REPO_ROOT
from test_build_stages import Image, needs_bash
from test_review_build_engine import FakeContainer, client, config_for, engine, make_engine  # noqa: F401

from orchestrator.Core import build_end
from orchestrator.Core import builder as builder_module
from orchestrator.Core.builder import BuildProgress, BuildStatus
from orchestrator.utils.step_limits import StepTimedOut

KEY = "0123456789abcdef0123456789abcdef"
FORGED = "status done\nexit 0\nstage target\njob build\ninputs -\ntime 2000-01-01T00:00:00Z\n"


@pytest.fixture(params=["Rockchip", "_template"])
def image(tmp_path, request):
    built = Image(tmp_path)
    if request.param == "_template":
        shutil.copy(REPO_ROOT / "targets" / "_template" / "modules" / "build.sh", built.app / "modules" / "build.sh")
    return built


def _module_appends(image, module, text):
    """Adds shell lines to the start of a stage module of the image (after its first line)."""
    path = image.app / "modules" / module
    first, rest = path.read_text(encoding="utf-8").split("\n", 1)
    path.write_text(f"{first}\n{text}\n{rest}", encoding="utf-8")


def _hand_key(image, job="build"):
    folder = image.work / "stages" / job
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "end.key").write_text(KEY + "\n", encoding="ascii")
    return folder


# --- the stage runner --------------------------------------------------------------------------------------------

@needs_bash
def test_a_module_never_sees_the_runs_key(image):
    folder = _hand_key(image)
    seen = image.work / "key-seen"
    _module_appends(image, "source_module.sh",
                    f'if [ -e "$STAGE_DIR/end.key" ] || env | grep -q {KEY}; then echo seen > "{seen}"; fi')

    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert not seen.exists(), "a module script could read the key that seals the end record"
    assert not (folder / "end.key").exists()
    record = build_end.read_end_record(folder / "build.end", key=KEY)
    assert record is not None and record.done and record.sealed
    assert build_end.read_end_record(folder / "build.end", key="f" * 32).sealed is False


@needs_bash
def test_a_module_that_blocks_the_runners_write_does_not_leave_its_forged_record(image):
    """A module writes "status done" and puts a folder where the runner writes its temporary file: the runner's own
    record still replaces the forgery (before, the write failed silently and the forged done stayed with exit 0)."""
    folder = _hand_key(image)
    _module_appends(image, "convert_module.sh",
                    f'mkdir -p "$STAGE_DIR/build.end.tmp"; printf "{FORGED.encode("unicode_escape").decode()}" '
                    '> "$STAGE_DIR/build.end"')

    result = image.run()

    text = (folder / "build.end").read_text(encoding="ascii")
    assert "2000-01-01T00:00:00Z" not in text, "the forged end record stayed in place"
    record = build_end.read_end_record(folder / "build.end", key=KEY)
    assert record is not None and record.sealed
    assert (record.done, result.returncode) in [(True, 0), (False, record.exit)]


@needs_bash
def test_a_build_started_by_hand_still_writes_its_record(image):
    """Without a key (build.sh or a module started by hand through the entrypoint's modes) the record is unsealed."""
    result = image.run()
    assert result.returncode == 0, result.stdout + result.stderr
    record = build_end.read_end_record(image.work / "stages" / "build" / "build.end")
    assert record is not None and record.done and record.sealed is False


# --- the BuildEngine -----------------------------------------------------------------------------------------------

def _finish(engine, build_id="b1", seconds=10):
    deadline = time.monotonic() + seconds
    while engine.is_active(build_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    return engine.get_build_status(build_id)


def _forger(text):
    """A module script that wrote `text` as the end record (whatever the runner did after it)."""
    def write(volumes, name, exit_code, **_):
        for host, bind in (volumes or {}).items():
            if bind.get("bind") == "/build-cache/work":
                folder = Path(host) / "stages" / ("imatrix" if "imatrix" in str(name) else "build")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "build.end").write_text(text, encoding="ascii")
    return write


def _runner_record(stage):
    """The stage runner's own (sealed) done record, but naming `stage` as its last one."""
    def write(volumes, name, exit_code, **_):
        for host, bind in (volumes or {}).items():
            if bind.get("bind") == "/build-cache/work":
                folder = Path(host) / "stages" / "build"
                folder.mkdir(parents=True, exist_ok=True)
                key_file = folder / "end.key"
                key = key_file.read_text(encoding="ascii").strip() if key_file.is_file() else ""
                key_file.unlink(missing_ok=True)
                body = f"status done\nexit 0\nstage {stage}\njob build\ninputs -\ntime 2026-10-31T12:00:00Z"
                seal = getattr(build_end, "seal_of", None)
                text = body + (f"\nseal {seal(body, key)}" if seal and key else "") + "\n"
                (folder / "build.end").write_text(text, encoding="ascii")
    return write


def test_a_done_record_with_an_exit_status_other_than_0_is_failed(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _forger(FORGED))
    client.stage_options["llm-build-"] = {"exit_code": 1}
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "exited with 1" in progress.errors[-1]


def test_a_record_a_module_wrote_is_not_the_runners(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _forger(FORGED))
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "not written by the stage runner" in progress.errors[-1]
    assert not (tmp_path / "out" / "b1" / "build_info.json").exists()


def test_a_done_record_before_the_last_stage_is_failed(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _runner_record("convert"))
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "before the last stage" in progress.errors[-1]


def test_a_forged_done_record_while_running_does_not_switch_the_clock_off(engine, client, tmp_path, monkeypatch):
    """Before, a module that wrote "status done" and then hung switched the step's clock off and, after the grace,
    the framework counted the build as done; now the record is not the runner's, so the idle timeout still stops it."""
    monkeypatch.setattr(builder_module, "END_RECORD_POLL", 0.05)
    monkeypatch.setattr(build_end, "END_GRACE", 0.3)
    work = tmp_path / "cache" / "builds" / "b1" / "work"
    container = FakeContainer(client, "llm-build-b1", auto_exit=False, lines=["=== Stage 3/4: convert ==="],
                              volumes={str(work): {"bind": "/build-cache/work", "mode": "rw"}})
    record = build_end.end_record_path(work, "build")

    def hang():
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(FORGED, encoding="ascii")
        time.sleep(0.05)
        return requests.exceptions.ReadTimeout("Read timed out.")

    container.wait_error = hang
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    cfg = config_for(tmp_path, build_idle_timeout=1.0)

    with pytest.raises(StepTimedOut) as stopped:
        engine._run_container(cfg, progress, container, "CONT: ", end_record=record)
    assert stopped.value.kind == "idle"
    assert not any("The build is done." in line for line in progress.logs)
