"""`llm-cli build status/history`: a request summary and a build record print their values and errors through
cli._printable like every other text that may carry text a build log or a tool steered (no control or bidi
characters reach the terminal)."""

import io

from rich.console import Console

from orchestrator import cli

STEERED = "boom\x1b]0;owned\x07 ‮evil‬\r done"


def _capture(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=500, color_system=None))
    return out


def test_a_request_summary_prints_errors_and_values_plainly(monkeypatch):
    out = _capture(monkeypatch)
    cli._print_request_summary({"request_id": "r1", "status": "failed", "errors": [STEERED],
                                "program": "GUI\x1b[2J", "awaiting_decision": True, "stage": STEERED})
    text = out.getvalue()
    assert "\x1b" not in text and "‮" not in text and "\x07" not in text and "\r" not in text
    assert "Error:" in text and "boom" in text and "done" in text


def test_a_build_record_prints_its_values_plainly(monkeypatch):
    out = _capture(monkeypatch)
    cli._print_build_record({"build_id": "b1", "status": "failed", "error": STEERED, "model": STEERED})
    text = out.getvalue()
    assert "\x1b" not in text and "‮" not in text and "\x07" not in text
