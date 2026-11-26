"""One log of every process (maintainer, 2026-11-26, design "Mojo, the health check", step 1 of "One log the person
and Ditto read"): framework.log in the log folder, one JSON object per line with the program and the process id,
private, through the redaction of every log, appended by every process and rotated when a process starts.
"""

import json
import logging
import os
import stat

import pytest

from orchestrator.utils import crash_report

TOKEN = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up


@pytest.fixture
def framework_log(tmp_path):
    handlers = []

    def add(folder=None):
        handler = crash_report.add_framework_log(folder or tmp_path / "logs")
        handlers.append(handler)
        return handler
    yield add
    root = logging.getLogger()
    for handler in [h for h in root.handlers if h.get_name() == crash_report.FRAMEWORK_LOG_HANDLER]:
        root.removeHandler(handler)
        handler.close()


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_one_json_object_per_line_with_the_program_and_redacted(framework_log, tmp_path):
    from orchestrator.Core.request_store import set_program
    set_program("cli")
    try:
        handler = framework_log()
        logging.getLogger("llmcf.test").warning("token %s while building", TOKEN)
        try:
            raise ValueError(f"bad {TOKEN}")
        except ValueError:
            logging.getLogger("llmcf.test").exception("it failed")
        handler.flush()
    finally:
        set_program("framework")
    entries = lines(tmp_path / "logs" / crash_report.FRAMEWORK_LOG)
    warning, failure = [e for e in entries if e["logger_name"] == "llmcf.test"]
    assert warning["level"] == "WARNING" and "while building" in warning["message"] and TOKEN not in warning["message"]
    assert warning["extra_fields"]["program"] == "cli" and warning["extra_fields"]["pid"] == os.getpid()
    assert failure["level"] == "ERROR" and failure["exception_type"] == "ValueError"
    assert TOKEN not in json.dumps(failure)
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "logs" / crash_report.FRAMEWORK_LOG).stat().st_mode) == 0o600


def test_once_per_process_and_appended(framework_log, tmp_path):
    path = tmp_path / "logs" / crash_report.FRAMEWORK_LOG
    path.parent.mkdir()
    path.write_text('{"earlier": "process"}\n', encoding="utf-8")
    first = framework_log()
    assert framework_log() is first, "a second call returns the handler there is"
    logging.getLogger("llmcf.test").warning("now")
    first.flush()
    text = path.read_text(encoding="utf-8")
    assert text.startswith('{"earlier": "process"}\n') and '"now"' in text


def test_rotated_when_a_process_starts_and_over_the_size(framework_log, tmp_path, monkeypatch):
    monkeypatch.setattr(crash_report, "FRAMEWORK_LOG_BYTES", 100)
    folder = tmp_path / "logs"
    folder.mkdir()
    path = folder / crash_report.FRAMEWORK_LOG
    for n in (3, 2, 1):
        path.with_name(f"{path.name}.{n}").write_text(f"old {n}\n", encoding="utf-8")
    path.write_text("x" * 200, encoding="utf-8")
    framework_log(folder)
    assert path.with_name(f"{path.name}.1").read_text(encoding="utf-8") == "x" * 200
    assert path.with_name(f"{path.name}.2").read_text(encoding="utf-8") == "old 1\n"
    assert path.with_name(f"{path.name}.3").read_text(encoding="utf-8") == "old 2\n", "the oldest one is dropped"
    assert not path.with_name(f"{path.name}.4").exists()
    assert path.read_text(encoding="utf-8") == "", "a fresh file for the lines from now on"


def test_a_small_log_is_not_rotated(framework_log, tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / crash_report.FRAMEWORK_LOG).write_text("small\n", encoding="utf-8")
    framework_log(folder)
    assert not (folder / f"{crash_report.FRAMEWORK_LOG}.1").exists()


def test_a_folder_that_cannot_be_written_gives_no_log(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    assert crash_report.add_framework_log(blocked / "logs") is None


def test_an_existing_log_folder_keeps_its_modes(framework_log, tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX modes")
    folder = tmp_path / "chosen"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    framework_log(folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    new = tmp_path / "new" / "logs"
    crash_report._private_folder(new)   # a folder the framework makes is private
    assert stat.S_IMODE(new.stat().st_mode) == 0o700


def test_the_framework_writes_its_log_when_it_boots(make_framework, tmp_path):
    fm = make_framework(initialize=False)
    path = crash_report.framework_log_folder(fm.config) / crash_report.FRAMEWORK_LOG
    assert path.parent == tmp_path / "state" / "logs", "the per-user folder, never the working folder"
    handler = next(h for h in logging.getLogger().handlers if h.get_name() == crash_report.FRAMEWORK_LOG_HANDLER)
    handler.flush()
    assert path.is_file() and any(e["logger_name"] == "FrameworkKernel" and "Booting" in e["message"]
                                  for e in lines(path))


def test_the_folder_is_the_persons_choice_or_the_per_user_folder(tmp_path):
    from types import SimpleNamespace
    chosen = tmp_path / "my logs"

    def config(logs_dir, state=tmp_path / "state"):
        return SimpleNamespace(get=lambda key, default=None: logs_dir if key == "logs_dir" else default,
                               user_state_dir=state)
    assert crash_report.framework_log_folder(config(str(chosen))) == chosen, "an absolute folder the person chose"
    assert crash_report.framework_log_folder(config("logs")) == tmp_path / "state" / "logs", \
        "a relative one (a checkout's default) would be the working folder: never there"
    assert crash_report.framework_log_folder(config("")) == tmp_path / "state" / "logs"
    assert crash_report.framework_log_folder(None) == crash_report.fallback_log_folder()
