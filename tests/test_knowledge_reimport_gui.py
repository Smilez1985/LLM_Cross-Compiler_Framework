"""The Mojo window asks the person's own yes to a repair of class person_yes (importing installed packs again): on the
GUI thread, with the plan's steps and what they cost, "No" the default; Mojo Miracle's worker waits for the answer."""

import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.mojo.findings import Finding, MojoReport, Severity  # noqa: E402
from orchestrator.Core.mojo.offers import Offer  # noqa: E402
from test_mojo_window import engine, gui_framework, opened, wait_for  # noqa: E402,F401 - the fixture


def reimport_offer():
    finding = Finding("community", "community.knowledge.untagged", Severity.WARN,
                      "1 installed pack(s) were imported without their topic or target: knowledge pack npu-guide",
                      "Import them again.", where="/srv/llm/community/knowledge", why="search misses them",
                      repair_id="knowledge.reimport")
    step = SimpleNamespace(kind="reimport", target_label="knowledge pack npu-guide",
                           full_path="/srv/llm/community/knowledge/npu/npu-guide",
                           before="3 excerpt(s) without topic or target",
                           after="imported again under topic npu: 2 page(s), 1.0 KB to read", undoable=True)
    plan = SimpleNamespace(summary="import 1 installed pack(s) again: 2 page(s), 1.0 KB, about as long as their first "
                                   "import; a knowledge snapshot first", steps=[step], sha256=lambda: "0" * 64)
    return MojoReport([finding]), [Offer(finding, "miracle_yes", plan, "")]


@pytest.mark.parametrize("yes", [False, True])
def test_the_window_asks_the_persons_yes_with_the_plan_and_no_as_the_default(qapp, tmp_path, engine, monkeypatch,  # noqa: F811
                                                                              yes):
    from PySide6.QtWidgets import QMessageBox

    import orchestrator.Core.mojo as mojo_package
    from orchestrator.gui import plain
    engine.report, engine.offered = reimport_offer()
    seen, calls = [], []

    def miracle(**kwargs):
        seen.append(kwargs["confirm"](engine.offered[0]))
        seen.append(threading.current_thread() is threading.main_thread())
        return SimpleNamespace(entries=[], after=engine.report, exit_code=1, repaired=[], already_gone=[],
                               not_repaired=[], new_problems=[])

    def answer(box):   # the first question is "Run Mojo Miracle now?", the second the person's yes to this repair
        calls.append((box.text(), box.defaultButton() is box.button(QMessageBox.No)))
        return QMessageBox.Yes if len(calls) == 1 or yes else QMessageBox.No
    monkeypatch.setattr(plain, "_exec", answer)
    window = opened(qapp, gui_framework(tmp_path))
    try:
        assert window.btn_miracle.isEnabled(), "Mojo Miracle is offered for it"
        monkeypatch.setattr(mojo_package, "Mojo", lambda framework, **kw: SimpleNamespace(miracle=miracle))
        window.btn_miracle.click()
        wait_for(qapp, lambda: window._token is None)
        assert seen == [yes, False], "asked from the worker, answered on the GUI thread"
        question, no_default = calls[-1]
        assert no_default, "No is the default"
        assert "knowledge pack npu-guide (/srv/llm/community/knowledge/npu/npu-guide)" in question
        assert "2 page(s), 1.0 KB" in question and "a knowledge snapshot first" in question
    finally:
        window.close()
