#!/usr/bin/env python3
"""
Ditto > Local Models... and Ditto > Evaluation...: Ditto's local models and its evaluation by mouse (W1'); the chat
panel's "Options for this question" give `llm-cli ditto ask --no-tools/--provider/--model` by mouse.

install() puts both entries into the menu bar's Ditto menu (shared with Ditto > Local Runtime...: found by its object
name "menu_ditto" or its title, else created before the Security menu) and keeps the actions and the windows on the
main window (act_ditto_models, act_ditto_eval; ditto_models_window, ditto_eval_window). The windows are not modal and
stay open while a download or an evaluation runs; a second click shows the same window again. The windows load when
an entry is first used (lazy loading, RG1).

CLI_PATHS names, for every person-facing `llm-cli ditto models ...`, `ditto ask` and `ditto eval` command and option,
the object the person clicks or fills instead: a menu action, a widget of a window (WINDOW_OBJECTS names the action
that opens it), or a widget of the chat panel (it exists while the main window does). MACHINE_ONLY lists the switches
that only skip a question or set an exit code for scripts, with the reason.
"""

from typing import Any, Dict

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from orchestrator.utils.localization import ui_text

DITTO_MENU = "menu_ditto"
BEFORE_MENU = "Security"   # the Ditto menu comes after File, Build and Boards, before Security (GUI_PARITY.md)
MODELS_ACTION = "act_ditto_models_open"
EVAL_ACTION = "act_ditto_eval_open"

CLI_PATHS: Dict[str, str] = {
    "ditto models": MODELS_ACTION,
    "ditto models list": MODELS_ACTION,                       # the table: tier, file, size, licence, status, note
    "ditto models download": "btn_ditto_models_download",     # the selected tier (the recommended one is preselected)
    "ditto ask": "btn_chat_send",                             # the chat panel's Send
    "ditto ask --no-tools": "chk_chat_no_tools",
    "ditto ask --provider": "cb_chat_provider",
    "ditto ask --model": "cb_chat_model",
    "ditto eval": "btn_ditto_eval_run",
    "ditto eval --cases": "btn_ditto_eval_cases_file",
    "ditto eval --provider": "cb_ditto_eval_provider",
    "ditto eval --model": "cb_ditto_eval_model",
    "ditto eval --tier": "lst_ditto_eval_tiers",
    "ditto eval --kind": "grp_ditto_eval_kinds",
    "ditto eval --rag-variant": "grp_ditto_eval_variants",
    "ditto eval --case": "lst_ditto_eval_cases",
    "ditto eval --repeat": "sp_ditto_eval_repeat",
    "ditto eval --json": "btn_ditto_eval_save",
    "ditto eval --effort": "cb_ditto_eval_effort",
}
# Objects that live in a window, and the menu action that opens it (the chat panel's objects need none).
WINDOW_OBJECTS: Dict[str, str] = {
    "tbl_ditto_models": MODELS_ACTION,
    "btn_ditto_models_download": MODELS_ACTION,
    "btn_ditto_models_download_recommended": MODELS_ACTION,   # `ditto models download` without a tier
    "btn_ditto_models_cancel": MODELS_ACTION,
    **{name: EVAL_ACTION for name in CLI_PATHS.values() if name.startswith(("btn_ditto_eval", "cb_ditto_eval",
                                                                            "lst_ditto_eval", "grp_ditto_eval",
                                                                            "sp_ditto_eval"))},
}
WINDOW_OBJECTS["btn_ditto_eval_cancel"] = EVAL_ACTION
MACHINE_ONLY: Dict[str, str] = {
    "ditto models download --yes": "skips the question; the GUI always asks (No by default)",
    "ditto eval --min-pass": "sets the exit code for scripts and CI; the window shows each run's pass rate",
}


def _title(menu: QMenu) -> str:
    return menu.title().replace("&", "")


def ditto_menu(window: Any, menubar: Any) -> QMenu:
    """The menu bar's Ditto menu: found by its object name or its title, else created before the Security menu."""
    title = ui_text("ditto_models.menu")
    for action in menubar.actions():
        menu = action.menu()
        if menu is not None and (menu.objectName() == DITTO_MENU or _title(menu) == title.replace("&", "")):
            return menu
    menu = QMenu(title, menubar)
    menu.setObjectName(DITTO_MENU)
    before = next((a for a in menubar.actions() if a.menu() is not None and _title(a.menu()) == BEFORE_MENU), None)
    if before is None:
        menubar.addMenu(menu)
    else:
        menubar.insertMenu(before, menu)
    window.menu_ditto = menu
    return menu


def _show(window: Any, attribute: str, make: Any, title_key: str) -> Any:
    """Shows the window kept in `attribute` (made by `make()` on the first click)."""
    from orchestrator.gui.plain import plain_box
    existing = getattr(window, attribute, None)
    try:
        if existing is None:
            existing = make()
            setattr(window, attribute, existing)
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text (SEC-M2)
        plain_box(window, "critical", ui_text(title_key), str(e))
        return None
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


def open_models(window: Any) -> Any:
    """Ditto > Local Models... (the same window again on a second click, with the list read again unless a download
    runs)."""
    def make():
        from orchestrator.gui.ditto_models_window import LocalModelsWindow
        return LocalModelsWindow(window.framework, window)
    existing = getattr(window, "ditto_models_window", None)
    if existing is not None and not existing.busy:
        existing.refresh()
    return _show(window, "ditto_models_window", make, "ditto_models.title")


def open_eval(window: Any) -> Any:
    """Ditto > Evaluation... (the same window again on a second click, with its choices and results)."""
    def make():
        from orchestrator.gui.ditto_eval_window import EvaluationWindow
        return EvaluationWindow(window.framework, window)
    return _show(window, "ditto_eval_window", make, "ditto_eval.title")


def install(window: Any, menubar: Any) -> None:
    """Ditto > Local Models... and Ditto > Evaluation... in the main window's menu bar."""
    menu = ditto_menu(window, menubar)
    models = QAction(ui_text("ditto_models.menu.models"), window)
    models.setObjectName(MODELS_ACTION)
    models.triggered.connect(lambda _=False: open_models(window))
    menu.addAction(models)
    evaluation = QAction(ui_text("ditto_models.menu.eval"), window)
    evaluation.setObjectName(EVAL_ACTION)
    evaluation.triggered.connect(lambda _=False: open_eval(window))
    menu.addAction(evaluation)
    window.act_ditto_models = models
    window.act_ditto_eval = evaluation


__all__ = ["CLI_PATHS", "DITTO_MENU", "EVAL_ACTION", "MACHINE_ONLY", "MODELS_ACTION", "WINDOW_OBJECTS", "ditto_menu",
           "install", "open_eval", "open_models"]
