#!/usr/bin/env python3
"""
The GUI's question about an interrupted build (Core/interrupted_build.py): "Continue the build, or delete what it
left?" with [Continue], [Delete...] and [Later]. Opened from Build > Build History... ([Continue or delete...] for the
chosen build) and when a build ended with finished stages kept. [Delete...] shows the framework's deletion dialog with
the one item (its full path, file, size and what it is; No is the default) and deletes only what the person accepted,
through Core/deletion.py; the result says honestly what stayed. Every text is plain text.
"""

from pathlib import Path
from typing import Any, Optional

from PySide6.QtWidgets import QMessageBox, QWidget

from orchestrator.gui.plain import one_line, plain_box
from orchestrator.utils.localization import current_language, ui_text


def _engine(framework: Any):
    return getattr(getattr(framework, "orchestrator", None), "build_engine", None)


def ask_interrupted(parent: Optional[QWidget], framework: Any, build_id: str, lang: Optional[str] = None) -> str:
    """Asks the person about one kept build; returns what happened: continued, deleted, later, none or failed."""
    from orchestrator.Core import interrupted_build
    lang = lang or current_language()
    title = ui_text("bld.interrupted.title", lang)
    engine = _engine(framework)
    if engine is None:
        plain_box(parent, "warning", title, ui_text("bld.interrupted.no_engine", lang))
        return "failed"
    cache = Path(engine.cache_dir)
    item = interrupted_build.kept_build(cache, build_id)
    if item is None:
        plain_box(parent, "information", title, ui_text("bld.interrupted.none", lang))
        return "none"
    box = QMessageBox(QMessageBox.Question, title, "", QMessageBox.NoButton,
                      parent if isinstance(parent, QWidget) else None)
    from PySide6.QtCore import Qt
    box.setTextFormat(Qt.PlainText)
    box.setText(ui_text("bld.interrupted.question", lang, done=one_line(item.done_text()),
                        target=one_line(item.target)))
    box.setObjectName("box_interrupted_build")
    continue_button = box.addButton(ui_text("bld.interrupted.continue", lang), QMessageBox.AcceptRole)
    delete_button = box.addButton(ui_text("bld.interrupted.delete", lang), QMessageBox.DestructiveRole)
    later_button = box.addButton(ui_text("bld.interrupted.later", lang), QMessageBox.RejectRole)
    box.setDefaultButton(later_button)
    box.exec()
    clicked = box.clickedButton()
    state = getattr(getattr(framework, "config", None), "user_state_dir", None)
    state = Path(state) if state else None
    if clicked is continue_button:
        try:
            started = interrupted_build.decide(engine, cache, build_id, "continue", "gui", state_dir=state)
        except interrupted_build.InterruptedBuildError as e:
            plain_box(parent, "warning", title, one_line(str(e)))
            return "failed"
        plain_box(parent, "information", title, ui_text("bld.interrupted.continued", lang, id=one_line(started)))
        return "continued"
    if clicked is delete_button:
        return _delete(parent, framework, engine, item, state, lang)
    return "later"


def _delete(parent, framework, engine, item, state, lang) -> str:
    from orchestrator.Core import interrupted_build
    from orchestrator.Core.deletion import PlanChanged, kept_lines, size_text
    from orchestrator.gui.deletion_dialog import ask_deletion
    title = ui_text("bld.interrupted.title", lang)
    plan = interrupted_build.deletion_plan(item)
    accepted = ask_deletion(parent, plan, title=title, lang=lang)
    if accepted is None:
        return "later"
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    runner = getattr(engine, "_cleanup_runner", None)
    config = interrupted_build.read_config(item.folder) or {}
    cleanup = runner(config.get("toolchain_image"), item.folder) if callable(runner) else None
    try:
        result = interrupted_build.decide(engine, Path(engine.cache_dir), item.build_id, "delete", "gui",
                                          state_dir=state, accepted=accepted, audit=audit, cleanup=cleanup)
    except PlanChanged:
        plain_box(parent, "warning", title, ui_text("deletion.changed", lang))
        return "failed"
    except (interrupted_build.InterruptedBuildError, PermissionError, RuntimeError) as e:
        plain_box(parent, "warning", title, ui_text("deletion.failed", lang, reason=one_line(str(e))))
        return "failed"
    lines = [ui_text("deletion.done.permanent", lang, name=one_line(n)) for n in result.done]
    lines += [one_line(line) for line in kept_lines(result, lang)]   # the person's own text: every path that stayed
    lines.append(ui_text("bld.interrupted.freed", lang, size=size_text(result.freed_bytes, lang)))
    plain_box(parent, "information", title, "\n".join(lines))
    return "deleted" if not result.failed else "failed"


__all__ = ["ask_interrupted"]
