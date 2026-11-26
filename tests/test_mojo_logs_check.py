"""Mojo Oracle reads the errors in the framework's log (step 4 of "One log the person and Ditto read", maintainer
2026-11-26): errors the self-healing pattern library knows become one warning per cause with the library's step and who
does it; other errors are one piece of information with the newest; nothing older than a day, nothing below error,
and the check only reads.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Severity
from orchestrator.Core.mojo.checks import check_logs
from orchestrator.Core.mojo.offers import REASONS
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
from orchestrator.Core.self_healing_manager import KNOWN_ISSUES, known_issue
from orchestrator.utils.framework_log import FRAMEWORK_LOG


@pytest.fixture
def framework(tmp_path):
    (tmp_path / "state" / "logs").mkdir(parents=True)
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path / "state",
                                                  get=lambda key, default=None: default))


def write(framework, *entries):
    now = datetime.now()
    lines = [json.dumps({"timestamp": (now - timedelta(minutes=m)).isoformat(), "level": level,
                         "logger_name": part, "message": message, **more})
             for m, level, part, message, more in entries]
    (framework.config.user_state_dir / "logs" / FRAMEWORK_LOG).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_no_error_of_the_last_day_is_fine(framework):
    write(framework, (5, "WARNING", "BuildEngine", "slow", {}), (60 * 30, "ERROR", "BuildEngine", "old", {}))
    assert [(f.code, f.severity) for f in check_logs(framework)] == [("logs.errors", Severity.OK)]


def test_known_errors_are_one_warning_per_cause_with_the_step_and_who_does_it(framework):
    write(framework,
          (30, "ERROR", "BuildEngine", "Could not resolve host: huggingface.co", {}),
          (20, "ERROR", "BuildEngine", "Temporary failure in name resolution", {}),
          (10, "ERROR", "BuildEngine", "build failed", {"stack_trace": "OSError: No space left on device"}),
          (5, "ERROR", "BuildEngine", "Got permission denied while trying to connect to the Docker daemon", {}))
    found = {f.code: f for f in check_logs(framework)}
    network = found["logs.known.network_connectivity_issue"]
    assert network.severity is Severity.WARN and "2 times" in network.message and network.cannot == "person"
    assert network.where == "the framework's log" and network.why and network.fix_hint
    disk = next(f for code, f in found.items() if code.startswith("logs.known.disk_full"))
    assert disk.cannot == "deletion_list" and "once" in disk.message, "the trace counts too"
    docker = next(f for code, f in found.items() if "docker_daemon" in code)
    assert docker.cannot == "admin"
    assert all(f.cannot in REASONS for f in found.values() if f.severity is Severity.WARN)
    assert "logs.errors" not in found, "no other error"


def test_other_errors_are_one_information_with_the_newest(framework):
    write(framework, (30, "ERROR", "ditto", "the provider refused", {}),
          (10, "CRITICAL", "BuildEngine", "unexpected\nstate", {}))
    (finding,) = check_logs(framework)
    assert finding.code == "logs.errors" and finding.severity is Severity.INFO
    assert finding.message.startswith("2 other error(s)") and "BuildEngine: unexpected state" in finding.message


def test_the_check_only_reads_and_runs_by_default(framework):
    write(framework, (5, "ERROR", "x", "Could not resolve host", {}))
    folder = framework.config.user_state_dir / "logs"
    before = sorted(p.name for p in folder.iterdir())
    check_logs(framework)
    assert sorted(p.name for p in folder.iterdir()) == before
    assert check_logs in DEFAULT_CHECKS


def test_known_issue_is_the_librarys_first_match():
    assert known_issue("all fine") is None
    assert known_issue("Could not resolve host") is KNOWN_ISSUES[4]
