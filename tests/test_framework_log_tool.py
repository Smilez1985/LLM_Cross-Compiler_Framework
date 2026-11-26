"""The framework's log for an AI (step 3 of "One log the person and Ditto read", maintainer 2026-11-26): the READ tool
read_framework_log reads framework.log through the CLI's reader with the same filters; paths reach the AI only as the
framework's folder labels or file names, secrets redacted, every text untrusted. It is optional (off until the person
enables it): the clients' tool list holds the 40 core tools.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from orchestrator.tools.tools_mojo import FrameworkLogOut, ReadFrameworkLogIn, mojo_tool_specs
from orchestrator.utils.framework_log import FRAMEWORK_LOG

TOKEN = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up


@pytest.fixture
def framework(tmp_path):
    state = tmp_path / "state"
    folder = state / "logs"
    folder.mkdir(parents=True)
    now = datetime.now()

    def line(minutes, level, logger, message, **more):
        return json.dumps({"timestamp": (now - timedelta(minutes=minutes)).isoformat(), "level": level,
                           "logger_name": logger, "message": message, "extra_fields": {"program": "gui"}, **more})
    (folder / FRAMEWORK_LOG).write_text("\n".join([
        line(3000, "ERROR", "BuildEngine", "an error of three days ago"),
        line(30, "INFO", "FrameworkKernel", "Booting"),
        line(20, "WARNING", "mojo.checks", f"the state folder {state}/audit.db is open, token {TOKEN}"),
        line(10, "ERROR", "BuildEngine", f"convert failed for {tmp_path}/elsewhere/model.gguf",
             exception_type="RuntimeError",
             stack_trace=f"Traceback\n  File \"{tmp_path}/x/convert.py\", line 3\nRuntimeError: no"),
    ]) + "\n", encoding="utf-8")
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda key, default=None: default))


def tool(framework):
    return next(s for s in mojo_tool_specs(framework) if s.name == "read_framework_log")


def call(framework, **arguments) -> FrameworkLogOut:
    return tool(framework).handler(ReadFrameworkLogIn(**arguments))


def test_the_default_is_the_last_day_from_warning_up(framework):
    out = call(framework)
    assert [e.level for e in out.entries] == ["WARNING", "ERROR"] and out.matching == 2
    assert all(e.program == "gui" for e in out.entries)


def test_paths_are_labels_or_file_names_and_secrets_are_redacted(framework, tmp_path):
    out = call(framework)
    shown = json.dumps(out.model_dump())
    assert str(tmp_path) not in shown and TOKEN not in shown
    warning, error = out.entries
    assert "audit.db" in warning.message and "model.gguf" in error.message
    assert error.error == "RuntimeError" and "convert.py" in error.trace


def test_the_filters_are_the_clis(framework):
    assert [e.part for e in call(framework, level="error", since_minutes=0).entries] == ["BuildEngine", "BuildEngine"]
    assert [e.message for e in call(framework, level="debug", part="Kernel").entries] == ["Booting"]
    assert len(call(framework, level="debug", lines=1).entries) == 1 and call(framework, level="debug").matching == 3


def test_it_is_read_only_and_off_until_the_person_enables_it(framework):
    from orchestrator.tools.policy import Decision, ToolTier
    spec = tool(framework)
    assert spec.tier == ToolTier.READ and spec.default_decision == Decision.DENY
    with pytest.raises(Exception):
        ReadFrameworkLogIn(part="../etc")
    with pytest.raises(Exception):
        ReadFrameworkLogIn(lines=501)
