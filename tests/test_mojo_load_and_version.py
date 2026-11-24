"""Mojo Oracle's checks of the container engine's version and of this computer's load (maintainer, 2026-11-24, design
"Mojo, the health check"): the checks of 2025 in utils/validation.py, wired. An older Docker Engine than the
framework's minimum is a warning with the way to a newer one; a busy CPU or memory is information; nothing is refused.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Severity
from orchestrator.Core.mojo.checks import check_load
from orchestrator.Core.mojo.checks.docker import _version_findings
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
from orchestrator.utils import validation
from orchestrator.utils.validation import (DOCKER_MIN_VERSION, ValidationSeverity, validate_cpu_usage,
                                           validate_docker_version, validate_memory_usage)


class Client:
    def __init__(self, answer):
        self.answer = answer

    def version(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


DOCKER = {"Version": "27.3.1", "Components": [{"Name": "Engine", "Version": "27.3.1"}]}
PODMAN = {"Version": "4.9.3", "Components": [{"Name": "Podman Engine", "Version": "4.9.3"}]}
OLD = {"Version": "19.03.15", "Components": [{"Name": "Engine", "Version": "19.03.15"}]}


# --- the engine's version ----------------------------------------------------------------------------------------
def test_the_version_is_the_engines_the_framework_builds_with():
    result = validate_docker_version(Client(DOCKER))
    assert result.severity is ValidationSeverity.SUCCESS and result.details["version"] == "27.3.1"
    assert result.details["source"] == "engine" and result.details["engine"] == "docker"


def test_an_older_docker_engine_is_a_warning_against_the_minimum():
    result = validate_docker_version(Client(OLD))
    assert result.severity is ValidationSeverity.WARNING and not result.valid
    assert result.details["minimum"] == DOCKER_MIN_VERSION == "20.10.0"


def test_podman_is_named_without_the_docker_minimum():
    result = validate_docker_version(Client(PODMAN))
    assert result.severity is ValidationSeverity.SUCCESS and result.details["engine"] == "podman"


def test_without_an_answer_the_docker_command_is_asked_through_the_one_runner(monkeypatch):
    from orchestrator.utils import helpers
    calls = []
    monkeypatch.setattr(validation.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(helpers, "execute_command",
                        lambda cmd, timeout=None: calls.append((cmd, timeout)) or (0, "Docker version 24.0.7, b", ""))
    result = validate_docker_version(Client(RuntimeError("no answer")))
    assert calls == [(["docker", "--version"], 5)]
    assert result.severity is ValidationSeverity.SUCCESS and result.details["source"] == "docker command"


def test_no_engine_and_no_docker_command_is_an_error_result_never_an_exception(monkeypatch):
    monkeypatch.setattr(validation.shutil, "which", lambda name: None)
    result = validate_docker_version(None)
    assert result.severity is ValidationSeverity.ERROR and "not on PATH" in result.message


def test_mojo_names_the_version_and_warns_about_an_older_engine_with_the_way():
    ok = _version_findings(Client(DOCKER))
    assert [(f.code, f.severity) for f in ok] == [("docker.version", Severity.OK)]
    assert "27.3.1" in ok[0].message and DOCKER_MIN_VERSION in ok[0].message
    old = _version_findings(Client(OLD))[0]
    assert old.severity is Severity.WARN and "19.03.15" in old.message and old.why and old.cannot == "admin"
    assert "rootless Podman" in old.fix_hint
    podman = _version_findings(Client(PODMAN))[0]
    assert podman.severity is Severity.OK and podman.message == "Podman 4.9.3"


def test_an_unknown_version_is_information(monkeypatch):
    monkeypatch.setattr(validation.shutil, "which", lambda name: None)
    unknown = _version_findings(Client(RuntimeError("no answer")))
    assert [(f.code, f.severity) for f in unknown] == [("docker.version", Severity.INFO)]


# --- this computer's load ----------------------------------------------------------------------------------------
@pytest.fixture
def load(monkeypatch):
    import psutil
    state = SimpleNamespace(cpu=10.0, memory=40.0, available=8 * 2 ** 30, intervals=[])
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: state.intervals.append(interval) or state.cpu)
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(percent=state.memory, available=state.available))
    return state


def test_a_quiet_computer_is_fine(load):
    findings = check_load(None)
    assert [(f.code, f.severity) for f in findings] == [("load.cpu", Severity.OK), ("load.memory", Severity.OK)]
    assert load.intervals == [0.5], "half a second of sampling"


def test_a_busy_computer_is_information_never_a_refusal(load):
    load.cpu, load.memory, load.available = 95.0, 92.0, 10 ** 9
    cpu, memory = check_load(None)
    assert cpu.severity is Severity.INFO and "95%" in cpu.message and "takes longer" in cpu.message
    assert memory.severity is Severity.INFO and "92%" in memory.message and "1.0 GB free" in memory.message


def test_a_load_that_cannot_be_read_is_information(monkeypatch):
    import psutil

    def broken(*_a, **_k):
        raise OSError("no /proc")
    monkeypatch.setattr(psutil, "cpu_percent", broken)
    monkeypatch.setattr(psutil, "virtual_memory", broken)
    findings = check_load(None)
    assert [f.severity for f in findings] == [Severity.INFO, Severity.INFO]
    assert "could not be read" in findings[0].message


def test_the_validation_results_keep_their_thresholds(load):
    load.cpu = 81.0
    assert validate_cpu_usage(80.0, interval=0.1).severity is ValidationSeverity.WARNING
    assert validate_memory_usage(85.0).details["available_bytes"] == load.available


def test_mojo_runs_both_checks_by_default():
    assert check_load in DEFAULT_CHECKS
