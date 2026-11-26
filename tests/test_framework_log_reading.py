"""Reading the framework's log (step 2 of "One log the person and Ditto read", maintainer 2026-11-26): `llm-cli logs
show` and `llm-cli logs export`, the Help menu's Framework Log window and LoggingManager.export_logs read
framework.log and its old files through one reader (utils/framework_log) with the same filters; an export is
private and writes the home folder as ~USER.
"""

import json
import logging
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from orchestrator.utils import framework_log
from orchestrator.utils.framework_log import FRAMEWORK_LOG


def entry(minutes_ago, level, logger, message, **extra):
    when = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
    return {"timestamp": when, "level": level, "logger_name": logger, "message": message,
            "extra_fields": {"program": "cli", "pid": 42}, **extra}


@pytest.fixture
def log_folder(tmp_path):
    folder = tmp_path / "state" / "logs"
    folder.mkdir(parents=True)
    old = [entry(300, "INFO", "BuildEngine", "an old build"), entry(200, "ERROR", "BuildEngine", "it failed earlier")]
    new = [entry(30, "INFO", "FrameworkKernel", "Booting"), entry(20, "WARNING", "mojo.checks", "disk is tight"),
           entry(10, "ERROR", "BuildEngine", f"stage convert failed in {Path.home()}/models",
                 exception_type="RuntimeError", stack_trace="Traceback\n  line 1")]
    (folder / f"{FRAMEWORK_LOG}.1").write_text("\n".join(json.dumps(e) for e in old) + "\n", encoding="utf-8")
    (folder / FRAMEWORK_LOG).write_text("\n".join(json.dumps(e) for e in new) + "\nnot json\n{\"half\":",
                                        encoding="utf-8")
    return folder


def test_the_reader_reads_old_files_first_and_filters(log_folder):
    every = framework_log.read_entries(log_folder)
    assert [e["message"] for e in every][:2] == ["an old build", "it failed earlier"], "oldest first"
    assert len(every) == 5, "a line that is no JSON object is skipped"
    errors = framework_log.read_entries(log_folder, level="error")
    assert [e["message"] for e in errors] == ["it failed earlier", errors[1]["message"]]
    recent = framework_log.read_entries(log_folder, since=framework_log.minutes_ago(60))
    assert [e["logger_name"] for e in recent] == ["FrameworkKernel", "mojo.checks", "BuildEngine"]
    assert [e["message"] for e in framework_log.read_entries(log_folder, part="MOJO")] == ["disk is tight"]
    assert len(framework_log.read_entries(log_folder, limit=2)) == 2
    assert framework_log.read_entries(log_folder.parent / "none") == []


def test_one_entry_is_one_readable_line(log_folder):
    last = framework_log.read_entries(log_folder)[-1]
    text = framework_log.entry_text(last)
    assert " ERROR BuildEngine [cli 42]: stage convert failed" in text and text.endswith("(RuntimeError)")


def test_an_export_is_private_and_hides_the_home_folder(log_folder, tmp_path):
    entries = framework_log.read_entries(log_folder)
    out = framework_log.export(entries, tmp_path / "out" / "log.json", "json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_entries"] == 5 and str(Path.home()) not in out.read_text(encoding="utf-8")
    text = framework_log.export(entries, tmp_path / "out" / "log.txt", "text").read_text(encoding="utf-8")
    assert "~USER" in text and "    Traceback" in text
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        framework_log.export(entries, tmp_path / "x", "xml")


def run_cli(*args):
    from orchestrator import cli as cli_module
    return CliRunner().invoke(cli_module.cli, ["-q", "logs", *args], obj=cli_module.FrameworkContext())


def test_logs_show_and_export_read_the_per_user_log(log_folder, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)
    result = run_cli("show", "--level", "warning", "--lines", "10")
    assert result.exit_code == 0, result.output
    shown = printed.getvalue()
    assert "disk is tight" in shown and "it failed earlier" in shown and "Booting" not in shown
    as_json = run_cli("show", "--part", "mojo", "--json")
    assert [e["message"] for e in json.loads(as_json.output)] == ["disk is tight"]
    target = tmp_path / "export.json"
    exported = run_cli("export", str(target), "--since", "60")
    assert exported.exit_code == 0 and json.loads(target.read_text(encoding="utf-8"))["total_entries"] == 3
    run_cli("export", str(tmp_path / "export.log"))
    assert "Booting" in (tmp_path / "export.log").read_text(encoding="utf-8"), "a name not .json is text"


def test_export_logs_of_the_logging_manager_exports_the_framework_log(log_folder, tmp_path):
    from orchestrator.utils import crash_report
    from orchestrator.utils.logging import LogFormat, LoggingManager
    handler = crash_report.add_framework_log(log_folder)
    try:
        assert LoggingManager().export_logs(tmp_path / "all.txt", format=LogFormat.STANDARD)
        assert "it failed earlier" in (tmp_path / "all.txt").read_text(encoding="utf-8")
        since = datetime.now() - timedelta(minutes=60)
        assert LoggingManager().export_logs(tmp_path / "recent.json", start_time=since)
        assert json.loads((tmp_path / "recent.json").read_text(encoding="utf-8"))["total_entries"] >= 3
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


# --- the window ------------------------------------------------------------------------------------------------
def test_the_window_shows_the_entries_and_exports_the_choice(qapp, log_folder, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import time
    from orchestrator.gui import log_window
    window = log_window.FrameworkLogWindow(log_folder, lang="en")
    try:
        deadline = time.monotonic() + 10
        while window.reading and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert "Booting" in window.txt_log_entries.toPlainText() and window.lbl_log_status.text() == "5 entries."
        window.cmb_log_level.setCurrentIndex(window.cmb_log_level.findData("error"))
        window.txt_log_part.setText("Build")
        window.refresh()
        while window.reading and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert [e["message"] for e in window.entries][0] == "it failed earlier" and len(window.entries) == 2
        target = tmp_path / "picked.json"
        monkeypatch.setattr(log_window, "choose_export_file", lambda *a: str(target))
        assert window.export() == target
        assert json.loads(target.read_text(encoding="utf-8"))["total_entries"] == 2
        opened = []
        monkeypatch.setattr("orchestrator.gui.crash_dialog.open_folder", lambda folder: opened.append(folder) or True)
        assert window.open_folder() and opened == [log_folder]
    finally:
        window.close()


def test_the_texts_exist_in_both_languages_with_the_same_placeholders():
    import string
    from orchestrator.utils.l10n_logs import TEXTS
    assert TEXTS["en"].keys() == TEXTS["de"].keys()
    for key in TEXTS["en"]:
        names = {f[1] for f in string.Formatter().parse(TEXTS["en"][key]) if f[1]}
        assert names == {f[1] for f in string.Formatter().parse(TEXTS["de"][key]) if f[1]}, key
