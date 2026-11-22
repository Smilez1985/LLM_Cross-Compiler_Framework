"""The wizard's Propose Dependencies (orchestrator/gui/wizards.py): the button asks Ditto in a worker thread, the field takes
the proposal's YAML with a comment per entry, and the gaps and refusals are told. Skipped without the GUI extra."""

import threading
import time

import pytest

pytest.importorskip("PySide6")   # the install without the 'gui' extra skips this file

from test_dependency_proposal import DOC, PROBE, StandIn, item  # noqa: E402
from test_gui_build import wizard  # noqa: E402, F401 - the fixture

from orchestrator.Core import dependency_proposal as dp  # noqa: E402


class WizardDitto(StandIn):
    def is_configured(self, kind):
        return True

    def _structured(self, task, system, prompt, output_model):
        self.thread_is_main = threading.current_thread() is threading.main_thread()
        return super()._structured(task, system, prompt, output_model)


def test_the_button_exists_and_asks_for_the_probe_first(wizard, monkeypatch):  # noqa: F811
    assert wizard.btn_dependencies_propose.objectName() == "btn_wizard_dependencies_propose"
    problems = []
    monkeypatch.setattr(wizard, "_run_problem", problems.append)
    assert wizard.propose_dependencies() is False and problems and "probe" in problems[0].lower()


def test_the_proposal_fills_the_field_with_comments_and_tells_the_gaps(wizard, qapp, monkeypatch):  # noqa: F811
    problems = []
    monkeypatch.setattr(wizard, "_run_problem", problems.append)
    proposal = dp.build_proposal(dp.DependenciesAnswer.model_validate(
        {"dependencies": [item(), item(name="zlib1g", version="1:9", source="S9")], "summary": "x"}),
        [(DOC, "libusb-1.0-0 2:1.0.26-1")], PROBE)
    wizard.on_dependencies_proposed(proposal)
    text = wizard.dependencies_edit.toPlainText()
    assert text.startswith("# libusb-1.0-0 [") and "name: libusb-1.0-0" in text and "# gap, nothing made of it: zlib1g" in text
    assert problems and "zlib1g" in problems[0]


def test_the_worker_asks_ditto_off_the_gui_thread_and_the_answer_fills_the_field(wizard, qapp, monkeypatch, tmp_path):  # noqa: F811
    ditto = WizardDitto(excerpts=[(DOC, "Install libusb-1.0-0 2:1.0.26-1 with apt.")])
    monkeypatch.setattr(wizard, "_ditto", lambda: ditto)
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    wizard.probe_path = str(probe)
    assert wizard.propose_dependencies() is True and not wizard.btn_dependencies_propose.isEnabled()
    deadline = time.time() + 10
    while wizard._dependencies_request is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert ditto.thread_is_main is False
    assert wizard.btn_dependencies_propose.isEnabled() and "name: libusb-1.0-0" in wizard.dependencies_edit.toPlainText()
    assert wizard.dependency_proposal.entries[0].name == "libusb-1.0-0"


def test_a_failed_proposal_is_told_and_the_button_comes_back(wizard, qapp, monkeypatch, tmp_path):  # noqa: F811
    from orchestrator.Core.ditto_manager import DittoError
    problems = []
    monkeypatch.setattr(wizard, "_run_problem", problems.append)

    class Failing(WizardDitto):
        def _structured(self, *args):
            raise DittoError("the provider is down")

    monkeypatch.setattr(wizard, "_ditto", lambda: Failing())
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    wizard.probe_path = str(probe)
    wizard.propose_dependencies()
    deadline = time.time() + 10
    while wizard._dependencies_request is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert problems and "the provider is down" in problems[0] and wizard.btn_dependencies_propose.isEnabled()
