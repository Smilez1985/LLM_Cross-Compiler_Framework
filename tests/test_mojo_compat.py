"""Mojo replaces the Doctor without breaking it (sixth round decision 9, round 6 design 7.3, package M1).

The Doctor's code moved into the package orchestrator.Core.mojo unchanged; orchestrator/Core/doctor.py re-exports every
name it had, `Doctor` is Mojo with the tiered `fix(tier_max)`, and finding codes, exit codes and JSON keys stay.
"""

import pytest

from orchestrator.Core import doctor, mojo
from orchestrator.Core.mojo import docker_objects, findings, runner, views

# Every name orchestrator/Core/doctor.py defined at the wave-2 commit (WP7 included, start of wave 3).
DOCTOR_NAMES_OF_WAVE_2 = (
    "Check", "DEFAULT_CHECKS", "DockerUnavailable", "Doctor", "DoctorReport", "FREE_SPACE_HINT", "Finding", "GB",
    "ImageDeletion", "ImagePlanChanged", "Severity", "_FRAMEWORK_TAG", "_IMAGE_ID", "_TARGET_WORD", "_docker_client",
    "_framework_labels", "_image_item", "_ok", "_storage_findings", "ai_findings", "ai_folder_labels",
    "check_calibration", "check_chat_feed", "check_community", "check_disk", "check_docker", "check_knowledge",
    "check_python", "check_runtime", "check_secrets", "check_targets", "check_tool_policy", "check_user_state",
    "delete_framework_images", "docker_storage", "framework_image_plan", "framework_images",
)


def test_doctor_shim_exports_everything():
    """Every name of the old module is still there, and (but Doctor) it is the very object of the Mojo package: one
    implementation, two import paths."""
    missing = [name for name in DOCTOR_NAMES_OF_WAVE_2 if not hasattr(doctor, name)]
    assert not missing, missing
    homes = (mojo, findings, runner, views, docker_objects, mojo.checks)
    for name in DOCTOR_NAMES_OF_WAVE_2:
        if name == "Doctor":
            continue
        same = [home for home in homes if getattr(home, name, None) is getattr(doctor, name)]
        assert same, f"{name} of the doctor module is not Mojo's"
    assert set(doctor.__all__) <= set(DOCTOR_NAMES_OF_WAVE_2)


def test_doctor_is_mojo_with_the_tiered_fix():
    assert issubclass(doctor.Doctor, mojo.Mojo)
    assert doctor.DoctorReport is mojo.MojoReport
    assert doctor.DEFAULT_CHECKS == runner.DEFAULT_CHECKS
    state = {"fixed": False}

    def check(_fw):
        if state["fixed"]:
            return []
        return [mojo.Finding("t", "t.code", mojo.Severity.WARN, "m", "h", fix_tier=1,
                             fix=lambda: state.update(fixed=True))]
    assert doctor.Doctor(None, [check]).fix(1).fixed == ["t.code"]


def test_doctor_reads_its_own_default_checks_when_made(monkeypatch):
    """Code that replaced doctor.DEFAULT_CHECKS (tests do) still decides what a Doctor checks."""
    def only(_fw):
        return [mojo.Finding("only", "only.one", mojo.Severity.OK, "m")]
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", [only])
    assert [f.code for f in doctor.Doctor(None).run().findings] == ["only.one"]
    assert len(mojo.Mojo(None).checks) == len(runner.DEFAULT_CHECKS) > 1


def test_mojo_and_the_doctor_find_the_same_codes(booted_framework):
    """Stable codes: the same checks give the same codes and the same exit code on either path."""
    by_mojo = mojo.Mojo(booted_framework).run()
    by_doctor = doctor.Doctor(booted_framework).run()
    assert [f.code for f in by_mojo.findings] == [f.code for f in by_doctor.findings]
    assert by_mojo.exit_code == by_doctor.exit_code
    assert set(by_mojo.to_dict()) >= {"exit_code", "findings", "fixed", "still_present"}
    row = by_mojo.to_dict()["findings"][0]
    assert set(row) >= {"subsystem", "code", "severity", "message", "fix_hint", "fix_tier", "fixable"}


@pytest.mark.parametrize("check", runner.DEFAULT_CHECKS, ids=lambda c: c.__name__)
def test_each_check_lives_in_its_area_module(check):
    assert check.__module__.startswith("orchestrator.Core.mojo.checks.")
