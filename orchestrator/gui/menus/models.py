#!/usr/bin/env python3
"""
The models area's mouse paths: the Models tab (orchestrator/gui/models_tab.py), Build > Benchmark...
(orchestrator/gui/benchmark_window.py) and Build > Model Archives... (orchestrator/gui/model_archive_window.py, also
the Models tab's button), as `llm-cli models list | delete | bench | bench-results | archive` do them (DESIGN "GUI
and the person's path", the row "The Models tab is one list").

install() adds Build > Benchmark... after F16 Intermediates... (MENU_LAYOUT places it) and keeps the window on the main
window (benchmark_window); the Models tab's Benchmark... opens the same window with its model chosen. CLI_PATHS names
the object that does each command by mouse; TAB_OBJECTS the tab an object lives in; WINDOW_OBJECTS the menu action that
opens the window an object lives in; MACHINE_ONLY the switches for scripts, with the reason.
"""

from typing import Any, Dict, Optional

from orchestrator.gui.menus import place
from orchestrator.utils.localization import current_language, ui_text

MODELS_TAB = "models_tab"   # attribute of MainOrchestrator
BENCH_ACTION = "act_build_benchmark"
ARCHIVES_ACTION = "act_build_model_archives"

CLI_PATHS: Dict[str, str] = {
    "models list": "tbl_models",                      # the table of the Models tab
    "models list --kind": "cb_models_kind",           # its Show: filter
    "models list --search": "txt_models_search",      # its Search: field
    "models delete": "btn_models_delete",             # Delete... of the selected row (the deletion dialog)
    "models bench": "btn_bench_run",                  # Build > Benchmark... > Run Benchmark (and the tab's button)
    "models bench --threads": "spin_bench_threads",
    "models bench-results": "tbl_bench_earlier",      # the window's earlier runs
    # the portable model archives (Build > Model Archives..., the Models tab's Model Archives...)
    "models archive write": "btn_archive_write",
    "models archive write --revision": "txt_archive_revision",
    "models archive write --to": "btn_archive_write",   # its save dialog asks for the file
    "models archive take": "btn_archive_take",
    "models archive take --sha256": "txt_archive_sha256",
    "models archive take --from": "txt_archive_keys",
    "models archive list": "list_archives",
    "models archive delete": "btn_archive_delete",
}
TAB_OBJECTS: Dict[str, str] = {name: MODELS_TAB for name in (
    "tbl_models", "cb_models_kind", "txt_models_search", "btn_models_delete", "btn_models_refresh",
    "btn_models_open_folder", "btn_models_info", "btn_models_deploy", "btn_models_bench", "btn_models_archives")}
WINDOW_OBJECTS: Dict[str, str] = {name: BENCH_ACTION for name in (
    "btn_bench_run", "btn_bench_cancel", "spin_bench_threads", "tbl_bench_earlier", "tbl_bench_results",
    "cb_bench_model", "cb_bench_where", "cb_bench_board", "txt_bench_board_model")}
WINDOW_OBJECTS.update({name: ARCHIVES_ACTION for name in (
    "btn_archive_write", "btn_archive_take", "btn_archive_delete", "txt_archive_repo", "txt_archive_revision",
    "txt_archive_sha256", "txt_archive_keys", "list_archives", "txt_archive_result")})
MACHINE_ONLY: Dict[str, str] = {
    "models list --json": "machine-readable output; the Models tab shows the same rows",
    "models bench --json": "machine-readable output; the Benchmark window shows the same table",
    "models bench-results --json": "machine-readable output; the Benchmark window lists the same runs",
    "models archive list --json": "machine-readable output; Model Archives... lists the same archives",
}


def open_benchmark(window: Any, model_key: Optional[str] = None) -> Any:
    """Build > Benchmark... (the same window again on a second click, its lists read again unless a run is going);
    with `model_key` that model is chosen."""
    from orchestrator.gui.plain import plain_box
    existing = getattr(window, "benchmark_window", None)
    try:
        if existing is None:
            from orchestrator.gui.benchmark_window import BenchmarkWindow
            existing = BenchmarkWindow(window.framework, window, model_key=model_key)
            window.benchmark_window = existing
        elif not existing.busy():
            existing.reload(select=model_key)
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text
        plain_box(window, "critical", ui_text("bench.title"), str(e))
        return None
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


def open_archives(window: Any) -> Any:
    """Build > Model Archives...: the window of the portable model archives (one at a time, its list read again)."""
    existing = getattr(window, "model_archives_window", None)
    try:
        if existing is None or not existing.isVisible():
            from orchestrator.gui.model_archive_window import ModelArchiveDialog
            existing = ModelArchiveDialog(window.framework, window)
            window.model_archives_window = existing
        elif not existing.busy():
            existing.refresh()
    except RuntimeError:   # the old window was closed and deleted meanwhile
        from orchestrator.gui.model_archive_window import ModelArchiveDialog
        existing = ModelArchiveDialog(window.framework, window)
        window.model_archives_window = existing
    existing.show()
    existing.raise_()
    return existing


def install(window, menubar) -> None:
    """Build > Benchmark... and Build > Model Archives... (MENU_LAYOUT puts them after F16 Intermediates...)."""
    build = place.find_menu(menubar, place.MENU_BUILD)
    lang = current_language()
    window.act_build_benchmark = place.new_action(window, BENCH_ACTION, ui_text("bench.menu", lang),
                                                  lambda: open_benchmark(window))
    window.act_build_model_archives = place.new_action(window, ARCHIVES_ACTION,
                                                       ui_text("models.archive.button", lang),
                                                       lambda: open_archives(window))
    if build is not None:
        build.addAction(window.act_build_benchmark)
        build.addAction(window.act_build_model_archives)


__all__ = ["ARCHIVES_ACTION", "BENCH_ACTION", "CLI_PATHS", "MACHINE_ONLY", "MODELS_TAB", "TAB_OBJECTS", "WINDOW_OBJECTS",
           "install", "open_archives", "open_benchmark"]
