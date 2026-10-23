"""hadolint at build time (BuildEngine._validate_dockerfile_hadolint; DECISIONS round 23, item 4).

Before, every outcome was reported as "Hadolint check skipped (tool not found or failed)" and the findings were thrown
away. The check stays advisory (nothing in the settings makes it stop a build): the build always goes on. Three cases
are told apart: hadolint is not installed (a note in the build log says how to get it), hadolint found problems (each
finding in the build log and as a progress warning, in plain words), hadolint found nothing (said in the log). A run
that ends without a finding (a crash, a time-out) is said plainly too.

Characterization (unchanged): `enable_hadolint=False` runs nothing, and no outcome of the check fails the build.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

import orchestrator.Core.builder as builder_module
from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat


def bare_engine(tmp_path):
    engine = BuildEngine.__new__(BuildEngine)
    engine.cache_dir, engine.base_dir = tmp_path / "cache", tmp_path
    return engine


def a_config(tmp_path, **overrides):
    values = dict(build_id="b1", timestamp="t", model_source=str(tmp_path / "model"), target_arch="Rockchip",
                  target_format=ModelFormat.GGUF, output_dir=str(tmp_path / "out"))
    values.update(overrides)
    return BuildConfiguration(**values)


def a_progress():
    return BuildProgress(build_id="b1", status=BuildStatus.BUILDING, current_stage="")


@pytest.fixture
def dockerfile(tmp_path):
    path = tmp_path / "context" / "Dockerfile"
    path.parent.mkdir(parents=True)
    path.write_text("FROM debian:bookworm-slim\nRUN apt-get update && apt-get install -y curl\n")
    return path


@pytest.fixture
def staged(monkeypatch, dockerfile):
    """_generate_dockerfile with the staging of the build context replaced by the prepared Dockerfile."""
    monkeypatch.setattr(builder_module, "stage_build_context", lambda *a, **k: dockerfile)
    return dockerfile


def fake_hadolint(monkeypatch, returncode=0, stdout="", stderr="", raises=None, found=True):
    """hadolint on PATH (or not, found=False) whose run ends with the given result; the calls are recorded."""
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/opt/bin/{name}" if found else None)

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


# --- characterization: what stays -----------------------------------------------------------------------------------

class TestWhatStays:

    def test_the_check_does_not_run_when_it_is_switched_off(self, tmp_path, staged, monkeypatch):
        calls = fake_hadolint(monkeypatch)
        engine, progress = bare_engine(tmp_path), a_progress()
        assert engine._generate_dockerfile(a_config(tmp_path, enable_hadolint=False), progress, tmp_path) == staged
        assert calls == [] and progress.warnings == [] and progress.errors == []

    def test_the_check_runs_by_default(self, tmp_path, staged, monkeypatch):
        calls = fake_hadolint(monkeypatch)
        engine = bare_engine(tmp_path)
        engine._generate_dockerfile(a_config(tmp_path), a_progress(), tmp_path)
        assert len(calls) == 1 and Path(calls[0][0][-1]).name == "Dockerfile"

    @pytest.mark.parametrize("outcome", ["missing", "findings", "crash", "oserror"])
    def test_no_outcome_of_the_check_fails_the_build(self, tmp_path, staged, monkeypatch, outcome):
        if outcome == "missing":
            fake_hadolint(monkeypatch, found=False)
        elif outcome == "findings":
            fake_hadolint(monkeypatch, returncode=1, stdout="Dockerfile:2 DL3008 warning: Pin versions\n")
        elif outcome == "crash":
            fake_hadolint(monkeypatch, returncode=-11)
        else:
            fake_hadolint(monkeypatch, raises=OSError("exec format error"))
        engine, progress = bare_engine(tmp_path), a_progress()
        assert engine._generate_dockerfile(a_config(tmp_path), progress, tmp_path) == staged
        assert progress.errors == []


# --- the three cases (and a run without a result) -------------------------------------------------------------------

TTY = ("Dockerfile:2 DL3008 \x1b[1m\x1b[93mwarning\x1b[0m: Pin versions in apt get install.\n"
       "Dockerfile:2 DL3015 \x1b[92minfo\x1b[0m: Avoid additional packages by specifying `--no-install-recommends`\n"
       "Dockerfile:3 SC2086 \x1b[92minfo\x1b[0m: Double quote to prevent globbing and word splitting.\n")
RELEASES = "https://" + "github.com/hadolint/hadolint/releases"


def check(tmp_path, dockerfile):
    progress = a_progress()
    bare_engine(tmp_path)._validate_dockerfile_hadolint(dockerfile, progress)
    return progress


class TestTheCasesAreToldApart:

    def test_not_installed_is_a_note_that_says_how_to_get_it(self, tmp_path, dockerfile, monkeypatch):
        calls = fake_hadolint(monkeypatch, found=False)
        progress = check(tmp_path, dockerfile)
        assert calls == [] and progress.warnings == [] and progress.errors == []
        (note,) = [line for line in progress.logs if "hadolint" in line]
        assert "[INFO]" in note and "not installed" in note and "advisory" in note
        assert RELEASES in note and "CONTRIBUTING.md, section 5" in note

    def test_findings_are_shown_in_the_log_and_as_warnings(self, tmp_path, dockerfile, monkeypatch):
        fake_hadolint(monkeypatch, returncode=1, stdout=TTY)
        progress = check(tmp_path, dockerfile)
        assert progress.warnings == [
            "hadolint, Dockerfile: line 2, DL3008 (warning): Pin versions in apt get install.",
            "hadolint, Dockerfile: line 2, DL3015 (info): Avoid additional packages by specifying "
            "`--no-install-recommends`",
            "hadolint, Dockerfile: line 3, SC2086 (info): Double quote to prevent globbing and word splitting."]
        summary = next(line for line in progress.logs if "finding(s)" in line)
        assert "3 finding(s) in Dockerfile (1 warning, 2 info)" in summary and "advisory; the build goes on" in summary
        assert "\x1b" not in "".join(progress.logs) and progress.errors == []
        assert all(warning in "".join(progress.logs) for warning in progress.warnings), "each one in the build log"

    def test_findings_are_shown_even_when_hadolint_exits_0(self, tmp_path, dockerfile, monkeypatch):
        """Findings below hadolint's failure level (style) come with exit code 0; they are still findings."""
        fake_hadolint(monkeypatch, returncode=0, stdout="Dockerfile:4 DL3059 style: Multiple consecutive RUN\n")
        assert check(tmp_path, dockerfile).warnings == [
            "hadolint, Dockerfile: line 4, DL3059 (style): Multiple consecutive RUN"]

    def test_no_findings_is_said_in_the_log(self, tmp_path, dockerfile, monkeypatch):
        fake_hadolint(monkeypatch, returncode=0)
        progress = check(tmp_path, dockerfile)
        assert progress.warnings == [] and any("no findings in Dockerfile" in line for line in progress.logs)

    @pytest.mark.parametrize("how, said", [
        ("exit", "exit code 1: hadolint: Dockerfile: withBinaryFile: does not exist"),
        ("crash", "exit code -11 and wrote nothing"),
        ("timeout", "did not finish within 60 s"),
        ("oserror", "could not be started (Exec format error)")])
    def test_a_run_without_a_result_is_said_plainly(self, tmp_path, dockerfile, monkeypatch, how, said):
        if how == "exit":
            fake_hadolint(monkeypatch, returncode=1, stderr="hadolint: Dockerfile: withBinaryFile: does not exist\n"
                                                            "HasCallStack backtrace:\n")
        elif how == "crash":
            fake_hadolint(monkeypatch, returncode=-11)
        elif how == "timeout":
            fake_hadolint(monkeypatch, raises=subprocess.TimeoutExpired(["hadolint"], 60))
        else:
            fake_hadolint(monkeypatch, raises=OSError(8, "Exec format error"))
        progress = check(tmp_path, dockerfile)
        (warning,) = progress.warnings
        assert "gave no result for Dockerfile" in warning and said in warning and "build goes on" in warning

    def test_many_findings_cap_the_warnings_but_the_log_has_all(self, tmp_path, dockerfile, monkeypatch):
        many = "".join(f"Dockerfile:{n} DL3008 warning: Pin versions number {n}\n" for n in range(1, 16))
        fake_hadolint(monkeypatch, returncode=1, stdout=many)
        progress = check(tmp_path, dockerfile)
        assert len(progress.warnings) == BuildEngine.HADOLINT_WARNINGS + 1
        assert progress.warnings[-1] == "hadolint: 5 more finding(s) in Dockerfile, all in the build log."
        assert all(any(line.endswith(f"Pin versions number {n}") for line in progress.logs) for n in range(1, 16))

    def test_it_runs_in_the_dockerfiles_folder_on_its_name_without_colours(self, tmp_path, dockerfile, monkeypatch):
        """Its lines then name the file, never a host path (the build log reaches an AI's log_tail)."""
        calls = fake_hadolint(monkeypatch, returncode=1, stdout=TTY)
        progress = check(tmp_path, dockerfile)
        ((argv, kwargs),) = calls
        assert argv == ["/opt/bin/hadolint", "Dockerfile"] and kwargs["cwd"] == str(dockerfile.parent)
        assert kwargs["env"]["NO_COLOR"] == "1" and kwargs["timeout"] == 60
        assert str(tmp_path) not in "".join(progress.logs)


class TestReadingTheOutput:

    def test_the_tty_format(self):
        from orchestrator.utils.hadolint import HadolintFinding, parse_findings
        assert parse_findings(TTY)[0] == HadolintFinding(2, "DL3008", "warning", "Pin versions in apt get install.")

    def test_a_dockerfile_hadolint_cannot_parse(self):
        from orchestrator.utils.hadolint import HadolintFinding, parse_findings
        (finding,) = parse_findings("Dockerfile:2:4 missing whitespace \n")
        assert finding == HadolintFinding(2, "", "", "missing whitespace")
        assert finding.plain() == "line 2: missing whitespace"

    def test_a_line_in_another_form_is_kept(self):
        from orchestrator.utils.hadolint import parse_findings
        (finding,) = parse_findings("something hadolint said\n\n")
        assert finding.line is None and finding.plain() == "something hadolint said"


@pytest.mark.skipif(not shutil.which("hadolint"), reason="hadolint is not installed")
class TestTheRealHadolint:

    def test_a_finding_and_a_clean_file(self, tmp_path):
        from orchestrator.utils.hadolint import run_hadolint
        dirty = tmp_path / "dirty" / "Dockerfile"
        dirty.parent.mkdir()
        dirty.write_text("FROM debian:bookworm-slim\nRUN apt-get update && apt-get install -y curl\n")
        result = run_hadolint(dirty)
        assert result.outcome == "findings" and "DL3008" in [f.rule for f in result.findings]
        assert all(f.line == 2 for f in result.findings)
        clean = tmp_path / "clean" / "Dockerfile"
        clean.parent.mkdir()
        clean.write_text("FROM debian:bookworm-slim@sha256:" + "0" * 64 + '\nCMD ["true"]\n')
        assert run_hadolint(clean).outcome == "passed"
