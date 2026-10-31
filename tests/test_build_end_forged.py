"""A module script cannot end a build early as "done" (the forged end record).

Only the stage runner (the framework's runtime layer, orchestrator/runtime_layer/runner.sh, which the host's
entrypoint starts) writes the end record, after its last stage returned, into the job's end folder of the runtime
layer, which only the runner's uid may write; no key exists in the container. The BuildEngine counts a build as done
only when the container's real exit status is 0 AND the record is the runner's own (its end folder and the record
belong to the runner alone) AND it names the job's last stage. A record a module wrote (in the work folder, or a file
of another owner or a hard link in the end folder), a done record with an exit status other than 0, or a done record
before the last stage is failed, with a plain reason (orchestrator/Core/build_end.py).

Changed on purpose with the runtime layer: before, the runner sealed its record with a one-run key from the work
folder (end.key); the tests of that key became the tests of the end folder.

The first half runs the layer's runner over the modules of both targets in the stub image of
tests/test_build_stages.py; the second half the BuildEngine on the fake Docker client of
tests/test_review_build_engine.py."""

import os
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


def _end_folder(image):
    folder = image.root / "end-build"
    folder.mkdir()
    return folder


# --- the stage runner --------------------------------------------------------------------------------------------

@needs_bash
def test_no_key_exists_for_a_module_to_find(image):
    """Before, a key in the job folder sealed the record; a module that could read it could forge a sealed record.
    Now there is no key at all: the runner writes the record into its end folder and nowhere else."""
    end = _end_folder(image)
    seen = image.work / "key-seen"
    _module_appends(image, "source_module.sh",
                    f'if [ -e "$STAGE_DIR/end.key" ] || env | grep -q {KEY}; then echo seen > "{seen}"; fi')

    result = image.run(LLMCF_END_DIR=end)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not seen.exists()
    assert not (image.work / "stages" / "build" / "end.key").exists()
    assert not (image.work / "stages" / "build" / "build.end").exists(), "the record left the end folder"
    record = build_end.read_end_record(end / "build.end")
    assert record is not None and record.done and record.seal == "-" and record.stage == "target"


@needs_bash
def test_a_module_that_blocks_the_runners_write_does_not_leave_its_forged_record(image):
    """A module writes "status done" and puts a folder where the runner writes its temporary file: the runner's own
    record still replaces the forgery (before, the write failed silently and the forged done stayed with exit 0). By
    hand the module runs as the runner's own user and could write the end folder; in the build sandbox it cannot
    (the stage account, tests/test_runtime_layer.py)."""
    end = _end_folder(image)
    _module_appends(image, "convert_module.sh",
                    f'mkdir -p "$LLMCF_END_DIR/build.end.tmp"; printf "{FORGED.encode("unicode_escape").decode()}" '
                    '> "$LLMCF_END_DIR/build.end"')

    result = image.run(LLMCF_END_DIR=end)

    text = (end / "build.end").read_text(encoding="ascii")
    assert "2000-01-01T00:00:00Z" not in text, "the forged end record stayed in place"
    record = build_end.read_end_record(end / "build.end")
    assert record is not None
    assert (record.done, result.returncode) in [(True, 0), (False, record.exit)]


@needs_bash
def test_a_build_started_by_hand_still_writes_its_record(image):
    """Without an end folder (a run by hand, the entrypoint's modes) the record goes to the work folder, where the host
    never trusts it."""
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
    """A module script that wrote `text` as the end record where it can: the work folder (whatever the runner did
    after it, it wrote no record of its own: the container was a module's)."""
    def write(volumes, name, exit_code, **_):
        for host, bind in (volumes or {}).items():
            if bind.get("bind") == "/build-cache/work":
                folder = Path(host) / "stages" / ("imatrix" if "imatrix" in str(name) else "build")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "build.end").write_text(text, encoding="ascii")
    return write


def _linked_forger(text):
    """A record in the end folder that is a hard link to a file a module wrote: not the runner's plain file."""
    def write(volumes, name, exit_code, **_):
        folder = fakes.end_folder_of(volumes)
        made = folder.parent / "work" / "forged.end"
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text(text, encoding="ascii")
        os.link(made, folder / "build.end")
    return write


def _runner_record(stage, end="protected"):
    """The stage runner's own done record (in its end folder), but naming `stage` as its last one."""
    def write(volumes, name, exit_code, **_):
        body = f"status done\nexit 0\nstage {stage}\njob build\ninputs -\ntime 2026-10-31T12:00:00Z"
        fakes.runner_writes(fakes.end_folder_of(volumes), f"{body}\nend {end}\n")
    return write


def test_a_done_record_with_an_exit_status_other_than_0_is_failed(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _runner_record("target"))
    client.stage_options["llm-build-"] = {"exit_code": 1}
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "exited with 1" in progress.errors[-1]


def test_a_record_a_module_wrote_in_the_work_folder_never_counts(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _forger(FORGED))
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "ended without its end record" in progress.errors[-1]
    assert not (tmp_path / "out" / "b1" / "build_info.json").exists()


@pytest.mark.skipif(not build_end.mounts_keep_owners() or not hasattr(os, "link"),
                    reason="a host whose mounts keep owners and modes (Linux) protects the end folder")
def test_a_record_that_is_not_the_runners_plain_file_is_not_the_runners(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _linked_forger(FORGED))
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "not written by the stage runner" in " ".join(progress.errors + progress.warnings)
    assert not (tmp_path / "out" / "b1" / "build_info.json").exists()


def test_a_done_record_before_the_last_stage_is_failed(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "write_end_record", _runner_record("convert"))
    engine.build_model(config_for(tmp_path))
    progress = _finish(engine)
    assert progress.status == BuildStatus.FAILED and progress.end_reason == "failed"
    assert "before the last stage" in progress.errors[-1]


def test_a_forged_done_record_while_running_does_not_switch_the_clock_off(engine, client, tmp_path, monkeypatch):
    """Before, a module that wrote "status done" and then hung switched the step's clock off and, after the grace,
    the framework counted the build as done; now the record is not the runner's (a module writes where it can: the
    work folder, never the runner's end folder), so the idle timeout still stops it."""
    from orchestrator.Core import runtime_layer
    from orchestrator.utils.sandbox import runner_ids
    monkeypatch.setattr(builder_module, "END_RECORD_POLL", 0.05)
    monkeypatch.setattr(build_end, "END_GRACE", 0.3)
    work = tmp_path / "cache" / "builds" / "b1" / "work"
    end = runtime_layer.end_folder(work.parent, "build")
    runtime_layer.prepare_end_folder(end, runner_ids(engine.docker_client))
    container = FakeContainer(client, "llm-build-b1", auto_exit=False, lines=["=== Stage 3/4: convert ==="],
                              volumes={str(work): {"bind": "/build-cache/work", "mode": "rw"},
                                       str(end): {"bind": "/llmcf-end", "mode": "rw"}})
    record = end / build_end.END_RECORD
    forged = build_end.end_record_path(work, "build")

    def hang():
        forged.parent.mkdir(parents=True, exist_ok=True)
        forged.write_text(FORGED, encoding="ascii")
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


@pytest.mark.parametrize("name, account, limit", [
    ("docs/DESIGN.md", "the stage account", "Limit: a module runs as the same user as the runner"),
    ("docs/DESIGN_DE.md", "dem Stufen-Konto", "Grenze: Ein Modul läuft als derselbe Benutzer wie der Runner")])
def test_the_design_names_the_guarantee_of_the_stage_account_and_where_it_does_not_hold(name, account, limit):
    """Since 3.3.0 (the stage account) the stage modules run under their own user, so the kernel refuses them the
    runner's memory whatever Yama ptrace_scope says; the design says so, names the legacy mode and the run by hand
    where it does not hold, and keeps the old limit as a history line only (this test pinned the old limit text until
    then; it changed with the behaviour)."""
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert "no capabilities to read another process's memory" not in text
    assert "keine Capabilities, um den Speicher eines anderen Prozesses zu lesen" not in text
    assert account in text and "ptrace_scope" in text and "build_sandbox: false" in text
    assert limit not in text
