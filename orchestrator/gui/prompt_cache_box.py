#!/usr/bin/env python3
"""
The "Prompt cache" group: the memory budget of Ditto's local server by mouse (`llm-cli ditto cache` and
`ditto cache set`), in the AI settings (its local part) and in Ditto > Local Models... (with the KV-cache choice).

It shows the person's values (auto or a number; the KV-cache type auto, f16, q8_0, q4_0), what auto stands for on this
computer, the soft cap and the hard limit of each value and what the running server got: runtime/ditto_models.
cache_overview, the same call as the CLI, read in a worker thread (it probes the hardware). Apply checks the values with
the one rule set (llm/settings_change.AISettingsChange.cache_change): above the hard limit it is refused, above the soft
cap the person is asked once (No by default) unless the person accepted such values; then only these settings are saved.
A running server takes them at its next start (server.json records them, so the change restarts it once). Every text
is plain text; nothing goes to an AI.
"""

import threading
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

KV_CHOICES = ("auto", "f16", "q8_0", "q4_0")


class PromptCacheBox(QGroupBox):
    """The local server's memory budget with Apply (the person's channel: warnings, never an AI's refusal)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None, lang: Optional[str] = None):
        self.lang = lang or current_language()
        super().__init__(ui_text("prompt_cache.title", self.lang), parent)
        self.setObjectName("grp_prompt_cache")
        self.framework = framework
        self._factory = runtime_factory or self._runtime
        self.view: Any = None
        self._token: Optional[object] = None
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("prompt_cache.intro"), self))
        form = QFormLayout()
        self.txt_ram = QLineEdit(self)
        self.txt_ram.setObjectName("txt_prompt_cache_ram")
        self.txt_ram.setPlaceholderText("auto")
        form.addRow(self._t("prompt_cache.ram"), self.txt_ram)
        self.txt_keep = QLineEdit(self)
        self.txt_keep.setObjectName("txt_prompt_cache_keep")
        self.txt_keep.setPlaceholderText("auto")
        form.addRow(self._t("prompt_cache.keep"), self.txt_keep)
        self.cb_kv = QComboBox(self)
        self.cb_kv.setObjectName("cb_prompt_cache_kv")
        for kind in KV_CHOICES:
            self.cb_kv.addItem(self._t("prompt_cache.kv.auto") if kind == "auto" else kind, kind)
        form.addRow(self._t("prompt_cache.kv"), self.cb_kv)
        layout.addLayout(form)
        self.chk_over_cap_ok = QCheckBox(self._t("prompt_cache.over_cap_ok"), self)
        self.chk_over_cap_ok.setObjectName("chk_prompt_cache_over_cap_ok")
        layout.addWidget(self.chk_over_cap_ok)
        self.lbl_info = plain_label(self._t("prompt_cache.loading"), self)
        self.lbl_info.setObjectName("lbl_prompt_cache_info")
        layout.addWidget(self.lbl_info)
        row = QHBoxLayout()
        self.btn_apply = self._button("prompt_cache.apply", "btn_prompt_cache_apply", self.apply)
        self.btn_refresh = self._button("prompt_cache.refresh", "btn_prompt_cache_refresh", self.refresh)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        layout.addLayout(row)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self._fill_saved()
        self.refresh()

    # --- building blocks ---
    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)

    def _config(self) -> Any:
        return getattr(self.framework, "config", None)

    def _fill_saved(self) -> None:
        get = getattr(self._config(), "get", None)

        def saved(key: str) -> str:
            value = get(key, "auto") if callable(get) else "auto"
            return str(value if value not in (None, "") else "auto").strip().lower()
        self.txt_ram.setText(saved("local_cache_ram_mib"))
        self.txt_keep.setText(saved("local_cache_keep_minutes"))
        index = self.cb_kv.findData(saved("local_kv_cache"))
        self.cb_kv.setCurrentIndex(index if index >= 0 else 0)
        self.chk_over_cap_ok.setChecked(bool(get("local_cache_over_cap_ok", False)) if callable(get) else False)

    # --- reading (`ditto cache`) ---
    def refresh(self) -> None:
        """Measures the budget again in a worker thread (it probes the hardware)."""
        from orchestrator.runtime.ditto_models import cache_overview
        token, relay, factory = object(), worker_relay(), self._factory
        self._token = token
        self.lbl_info.setText(self._t("prompt_cache.loading"))

        def run() -> None:
            try:
                result: Any = (True, cache_overview(factory()))
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            relay.done.emit(token, result)
        threading.Thread(target=run, name="prompt-cache", daemon=True).start()

    def show_view(self, view: Any) -> None:
        self.view = view
        lines = [self._t("prompt_cache.row", name=row.key, auto=one_line(row.auto),
                         soft="-" if row.soft_cap is None else row.soft_cap,
                         hard="-" if row.hard_limit is None else row.hard_limit, reason=one_line(row.reason))
                 for row in view.rows]
        running = view.running or {}
        if running:
            lines.append(self._t("prompt_cache.running", ram=running.get("cache_ram_mib", "-"),
                                 kv=running.get("kv_cache") or "-", ctx=running.get("n_ctx_slot") or
                                 running.get("ctx") or "-"))
        self.lbl_info.setText("\n".join(lines))

    def _on_relay(self, token: object, result: Any) -> None:
        if token is not self._token:
            return
        self._token = None
        ok, value = result
        if ok:
            self.show_view(value)
        else:
            self.lbl_info.setText(self._t("prompt_cache.load_failed", reason=one_line(value)))

    # --- Apply (`ditto cache set`) ---
    def values(self) -> Dict[str, Any]:
        return {"local_cache_ram_mib": self.txt_ram.text().strip() or "auto",
                "local_cache_keep_minutes": self.txt_keep.text().strip() or "auto",
                "local_kv_cache": self.cb_kv.currentData() or "auto",
                "local_cache_over_cap_ok": self.chk_over_cap_ok.isChecked()}

    def apply(self) -> bool:
        """Checks and saves the values; True when they were saved."""
        from orchestrator.llm.settings_change import AISettingsChange
        title = self._t("prompt_cache.title")
        if self.view is None:
            plain_box(self, "information", title, self._t("prompt_cache.not_loaded"))
            return False
        try:
            change = AISettingsChange.cache_change(self.values(), self.view.budget)
        except (KeyError, ValueError) as e:
            plain_box(self, "warning", title, self._t("prompt_cache.refused", reason=one_line(e)))
            return False
        problems = change.cache_problems()
        if problems:
            plain_box(self, "warning", title, self._t("prompt_cache.refused", reason=one_line(problems[0].message)))
            return False
        for warning in change.cache_warnings():
            if not ask(self, title, self._t("prompt_cache.warning", warning=warning)):
                self.lbl_info.setText(self._t("prompt_cache.unchanged"))
                return False
        change.apply_cache(self._config())
        self.lbl_info.setText(self._t("prompt_cache.saved"))
        return True


__all__ = ["KV_CHOICES", "PromptCacheBox"]
