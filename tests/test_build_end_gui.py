"""The Builder's progress panel ends on the build's end reason and says it in plain words (DECISIONS round 40,
TASK 3), in the person's language."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from test_board_docs_progress import FakeService  # noqa: E402


@pytest.mark.parametrize("status, reason, words", [
    ("completed", "done", "Build ready"),
    ("error", "idle", "idle timeout"),
    ("error", "soft_cap", "soft cap"),
    ("cancelled", "cancelled", "Build stopped"),
])
def test_the_panel_shows_how_the_build_ended(qapp, status, reason, words):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    service = FakeService()
    panel.start("req_1")
    assert panel.poll(service, "req_1") == "building"
    service.status = SimpleNamespace(status=status, stage="Archiving", progress_percent=100, end_reason=reason)
    assert panel.poll(service, "req_1") == status
    assert words in panel.lbl_stage.text() and "Archiving" in panel.lbl_stage.text()
    panel.deleteLater()


def test_a_running_build_keeps_its_step_line(qapp):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    service = FakeService()
    service.status = SimpleNamespace(status="building", stage="Running modules", progress_percent=60, end_reason=None)
    panel.start("req_2")
    panel.poll(service, "req_2")
    assert "Running modules" in panel.lbl_stage.text() and "60%" in panel.lbl_stage.text()
    panel.deleteLater()
