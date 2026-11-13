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

from orchestrator.gui.menus import place
# One helper finds or makes the shared Ditto menu: the one of Ditto > Local Runtime... (wave 5b integration)
from orchestrator.gui.menus.ditto_runtime import BEFORE_MENU, DITTO_MENU, ditto_menu  # noqa: F401
from orchestrator.utils.localization import ui_text

MODELS_ACTION = "act_ditto_models_open"
EVAL_ACTION = "act_ditto_eval_open"
CONTEXT_ACTION = "act_ditto_context_open"   # Settings > Language model > Context window (gui/context_window_box.py)

CLI_PATHS: Dict[str, str] = {
    "ditto models": MODELS_ACTION,
    "ditto models list": MODELS_ACTION,                       # the table: tier, file, size, licence, status, note
    "ditto models download": "btn_ditto_models_download",     # the selected tier (the recommended one is preselected)
    # the local server's memory budget: the Prompt cache group (also in the AI settings)
    "ditto cache": "grp_prompt_cache",
    "ditto cache show": "grp_prompt_cache",                   # values, automatic values, caps, the running server
    "ditto cache set": "btn_prompt_cache_apply",              # the fields, the KV-cache choice and the checkbox
    "ditto ask": "btn_chat_send",                             # the chat panel's Send
    "ditto ask --no-tools": "chk_chat_no_tools",
    "ditto ask --provider": "cb_chat_provider",
    "ditto ask --model": "cb_chat_model",
    # the context window, the counting and the compactions: Settings > Language model > Context window
    "ditto context": CONTEXT_ACTION,
    "ditto context show": "grp_context_window",
    "ditto context --refresh": "btn_context_refresh",
    "ditto context show --refresh": "btn_context_refresh",
    "ditto context original": "btn_context_original",
    "ditto context undo": "btn_context_undo",
    # the cloud cache (the same section's Cloud cache box): the way, the lifetime and the soft cap
    "ditto context cache": "grp_cloud_cache",
    "ditto context cache --ttl": "cb_cloud_cache_ttl",
    "ditto context cache --cost-limit": "spn_cloud_cache_limit",
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
    "ditto eval --pause-minutes": "sp_ditto_eval_pause",
    # the paid OpenAI test's yes: Run evaluation... asks it with the calls and tokens it costs (No by default)
    "ditto eval --i-pay": "btn_ditto_eval_run",
    # the measurement on this computer (evals/tier_check.py); Local Models' "Evaluate first..." presets it
    "ditto eval --measure": "chk_ditto_eval_measure",
    "ditto eval --measure-only": "chk_ditto_eval_measure_only",
    "ditto eval --chunks": "sp_ditto_eval_chunks",
    "ditto eval --ctx": "cb_ditto_eval_ctx",
    "ditto eval --again": "chk_ditto_eval_again",
    # a missing reference: Run evaluation... asks before it is downloaded (the CLI asks the same at a terminal)
    "ditto eval --download-reference": "btn_ditto_eval_run",
}
# Objects that live in a window, and the menu action that opens it (the chat panel's objects need none).
WINDOW_OBJECTS: Dict[str, str] = {
    "tbl_ditto_models": MODELS_ACTION,
    "btn_ditto_models_download": MODELS_ACTION,
    "btn_ditto_models_download_recommended": MODELS_ACTION,   # `ditto models download` without a tier
    "btn_ditto_models_cancel": MODELS_ACTION,
    "grp_prompt_cache": MODELS_ACTION,
    "btn_prompt_cache_apply": MODELS_ACTION,
    **{name: EVAL_ACTION for name in CLI_PATHS.values() if name.startswith(("btn_ditto_eval", "cb_ditto_eval",
                                                                            "lst_ditto_eval", "grp_ditto_eval",
                                                                            "sp_ditto_eval", "chk_ditto_eval"))},
    "btn_ditto_models_evaluate": MODELS_ACTION,               # "Evaluate first...": the evaluation with --measure
    "grp_context_window": CONTEXT_ACTION,
    "btn_context_refresh": CONTEXT_ACTION,
    "btn_context_original": CONTEXT_ACTION,
    "btn_context_undo": CONTEXT_ACTION,
    "grp_cloud_cache": CONTEXT_ACTION,
    "cb_cloud_cache_ttl": CONTEXT_ACTION,
    "spn_cloud_cache_limit": CONTEXT_ACTION,
    "btn_cloud_cache_apply": CONTEXT_ACTION,
}
WINDOW_OBJECTS["btn_ditto_eval_cancel"] = EVAL_ACTION
MACHINE_ONLY: Dict[str, str] = {
    "ditto models download --yes": "skips the question; the GUI always asks (No by default)",
    "ditto cache --json": "machine output for scripts; the Prompt cache group shows the same values",
    "ditto cache show --json": "machine output for scripts; the Prompt cache group shows the same values",
    "ditto cache set --yes-over-cap": "skips the question above the soft cap; the GUI asks (No by default) or takes "
                                      "the checkbox 'Accept values above the soft cap without a warning'",
    "ditto eval --min-pass": "sets the exit code for scripts and CI; the window shows each run's pass rate",
    "ditto context --json": "machine output for scripts; the Context window section shows the same values",
    "ditto context show --json": "machine output for scripts; the Context window section shows the same values",
    "ditto context undo --yes": "skips the question; the GUI always asks (No by default)",
    "ditto context cache --json": "machine output for scripts; the Cloud cache box shows the same values",
}


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


def open_context(window: Any) -> Any:
    """Ditto > Context Window...: the settings window at Language model > Context window."""
    from orchestrator.gui.menus.build import open_settings
    return open_settings(window, "language_model", "context")


def install(window: Any, menubar: Any) -> None:
    """Ditto > Local Models..., Ditto > Evaluation... and Ditto > Context Window... in the main window's menu bar."""
    menu = ditto_menu(window, menubar)
    models = place.new_action(window, MODELS_ACTION, ui_text("ditto_models.menu.models"), lambda: open_models(window))
    menu.addAction(models)
    evaluation = place.new_action(window, EVAL_ACTION, ui_text("ditto_models.menu.eval"), lambda: open_eval(window))
    menu.addAction(evaluation)
    context = place.new_action(window, CONTEXT_ACTION, ui_text("context.menu"), lambda: open_context(window))
    menu.addAction(context)
    window.act_ditto_models = models
    window.act_ditto_eval = evaluation
    window.act_ditto_context = context


__all__ = ["CLI_PATHS", "CONTEXT_ACTION", "DITTO_MENU", "EVAL_ACTION", "MACHINE_ONLY", "MODELS_ACTION", "WINDOW_OBJECTS",
           "ditto_menu", "install", "open_context", "open_eval", "open_models"]
