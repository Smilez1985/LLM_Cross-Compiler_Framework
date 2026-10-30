"""The end record of the stage runner (targets/*/modules/build.sh, DECISIONS round 40 TASK 1) and its Cancel note
(TASK 7): one record per run, written as the very last step; "done" only after the last stage's marker, "failed"
through the same writer for every other end; a person's Cancel is not counted by the loop protection.

Runs the real build.sh of both targets in the stub image of tests/test_build_stages.py."""

import shutil

import pytest

from conftest import REPO_ROOT
from test_build_stages import Image, needs_bash, stage_lines

from orchestrator.Core.build_end import read_end_record


def _record(image, job="build"):
    return read_end_record(image.work / "stages" / job / "build.end")


@pytest.fixture(params=["Rockchip", "_template"])
def image(tmp_path, request):
    built = Image(tmp_path)
    if request.param == "_template":   # the template's runner is the same file but its labels (module generator)
        shutil.copy(REPO_ROOT / "targets" / "_template" / "modules" / "build.sh", built.app / "modules" / "build.sh")
    return built


@needs_bash
def test_a_finished_build_writes_done_as_its_last_step(image):
    result = image.run()
    assert result.returncode == 0, result.stdout + result.stderr
    record = _record(image)
    assert record is not None and record.status == "done" and record.exit == 0
    assert record.stage == "target" and record.job == "build"
    assert len(record.inputs) == 64 and record.time.endswith("Z")
    assert not (image.work / "stages" / "build" / "build.end.tmp").exists()
    assert not (image.work / "stages" / "build" / "running").exists()


@needs_bash
def test_a_failing_stage_writes_failed_with_its_exit_code_and_name(image):
    result = image.run(STUB_FAIL="quantize")
    assert result.returncode == 4
    record = _record(image)
    assert (record.status, record.exit, record.stage) == ("failed", 4, "target")


@needs_bash
def test_a_refused_loop_writes_failed_too(image):
    assert image.run("--to", "convert").returncode == 0
    stages = image.work / "stages" / "build"
    inputs = next(line.split(" ", 1)[1] for line in (stages / "convert.done").read_text().splitlines()
                  if line.startswith("inputs "))
    (stages / "target.starts").write_text(f"inputs {inputs}\ncount 3\n")
    looped = image.run()
    assert looped.returncode == 75
    assert (_record(image).status, _record(image).exit, _record(image).stage) == ("failed", 75, "target")


@needs_bash
def test_a_refusal_before_any_stage_still_writes_failed(image):
    result = image.run(TARGET_FORMAT="nonsense")
    assert result.returncode != 0
    assert _record(image).status == "failed" and _record(image).exit == result.returncode


@needs_bash
def test_a_stale_record_never_ends_the_next_run(image):
    assert image.run().returncode == 0
    marker = image.work / "stages" / "build" / "build.end"
    assert _record(image).status == "done"
    # the next run fails early: the record of the run before is gone, the new one says failed
    result = image.run("--fresh", STUB_FAIL="convert")
    assert result.returncode != 0
    assert marker.is_file() and _record(image).status == "failed"


@needs_bash
def test_the_imatrix_job_has_its_own_record(image):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    image.output.rmdir()
    assert image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset).returncode == 0
    assert _record(image, "imatrix").status == "done" and _record(image, "imatrix").stage == "convert"
    assert _record(image) is None


def _killed_while_running(image, stage, count):
    """What a container stopped from outside while `stage` ran leaves: its .starts count and the running note."""
    stages = image.work / "stages" / "build"
    inputs = next(line.split(" ", 1)[1] for line in (stages / "convert.done").read_text().splitlines()
                  if line.startswith("inputs "))
    (stages / f"{stage}.starts").write_text(f"inputs {inputs}\ncount {count}\n")
    (stages / "running").write_text(f"{stage}\n")
    return stages


@needs_bash
def test_a_cancelled_stage_is_not_counted_as_a_restart(image):
    """TASK 7: three stops, each a person's Cancel (the framework's note), are no loop: the stage runs again."""
    assert image.run("--to", "convert").returncode == 0
    stages = _killed_while_running(image, "target", 3)
    (stages / "cancelled").write_text("cancelled\n")

    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result)[-1] == ("4/4", "target", "done")
    assert not (stages / "cancelled").exists() and not (stages / "target.starts").exists()


@needs_bash
def test_a_cancel_gives_back_only_its_own_start(image):
    assert image.run("--to", "convert").returncode == 0
    stages = _killed_while_running(image, "target", 4)
    (stages / "cancelled").write_text("cancelled\n")
    looped = image.run()
    # 4 starts, one of them a Cancel: 3 count, which is the limit
    assert looped.returncode == 75
    assert not (stages / "cancelled").exists()


@needs_bash
def test_an_outside_stop_without_the_note_still_counts(image):
    assert image.run("--to", "convert").returncode == 0
    _killed_while_running(image, "target", 3)
    looped = image.run()
    assert looped.returncode == 75
    assert "started 3 times with the same inputs" in looped.stderr


def test_the_generated_fallback_runner_writes_the_end_record_too(tmp_path):
    """A module generated without the template folder gets a self-contained runner with the same end record."""
    from orchestrator.Core.module_generator import ModuleGenerator
    gen = ModuleGenerator.__new__(ModuleGenerator)
    gen.template_dir = tmp_path / "no-template"
    written = {}
    gen._write_file = lambda path, content: written.__setitem__(path.name, content)
    gen._make_executable = lambda path: None
    gen._write_build_script(tmp_path, {"module_name": "Example", "quantization_logic": ""})
    text = written["build.sh"]
    assert "build.end" in text and "write_end" in text and "trap on_exit EXIT" in text
