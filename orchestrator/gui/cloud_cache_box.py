#!/usr/bin/env python3
"""
Settings > Language model > Context window, the Cloud cache box: `llm-cli ditto context cache` by mouse.

It shows how the provider of Ditto's chat model caches (Claude's cache points, OpenRouter passing them on for an
anthropic/ model, a provider that caches by itself, or no cloud cache), the lifetime of Claude's cache (ai_cache_ttl,
5 minutes by default or 1 hour) and the soft cap on the estimated cache writes of one conversation
(ai_cache_cost_limit; crossing it brings a notice, caching goes on), with the saved conversation's cache numbers.
Apply saves both through their one writer (llm/cloud_cache.set_cache_settings). Nothing is sent to a provider for it;
every text is plain text.
"""

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text


class CloudCacheBox(QGroupBox):
    """The cloud cache's way, lifetime, soft cap and the conversation's numbers (`llm-cli ditto context cache`)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 history_factory: Optional[Callable[[], Any]] = None):
        self.lang = lang or current_language()
        super().__init__(ui_text("cloud_cache.title", self.lang), parent)
        self.setObjectName("grp_cloud_cache")
        self.framework = framework
        self._history_factory = history_factory or self._history
        self.view: Any = None
        layout = QVBoxLayout(self)
        intro = plain_label(self._t("cloud_cache.intro"), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.cb_ttl = QComboBox(self)
        self.cb_ttl.setObjectName("cb_cloud_cache_ttl")
        for ttl in ("5m", "1h"):
            self.cb_ttl.addItem(self._t(f"cloud_cache.ttl.{ttl}"), ttl)
        form.addRow(self._t("cloud_cache.ttl"), self.cb_ttl)
        self.spn_limit = QDoubleSpinBox(self)
        self.spn_limit.setObjectName("spn_cloud_cache_limit")
        self.spn_limit.setRange(0.0, 100000.0)
        self.spn_limit.setDecimals(2)
        self.spn_limit.setSingleStep(0.5)
        self.spn_limit.setSuffix(" USD")
        form.addRow(self._t("cloud_cache.limit"), self.spn_limit)
        layout.addLayout(form)
        self.lbl_info = plain_label("", self)
        self.lbl_info.setObjectName("lbl_cloud_cache_info")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_info)
        row = QHBoxLayout()
        self.btn_apply = QPushButton(button_text(self._t("cloud_cache.apply")), self)
        self.btn_apply.setObjectName("btn_cloud_cache_apply")
        self.btn_apply.setAutoDefault(False)
        self.btn_apply.clicked.connect(lambda _=False: self.apply())
        row.addWidget(self.btn_apply)
        row.addStretch(1)
        layout.addLayout(row)
        self.refresh()

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _config(self) -> Any:
        return getattr(self.framework, "config", None)

    def _history(self) -> Any:
        from pathlib import Path
        from orchestrator.Core.ditto_manager import ChatHistory
        from orchestrator.runtime.profile import default_state_dir
        config = self._config()
        return ChatHistory.in_state_dir(Path(getattr(config, "user_state_dir", None) or default_state_dir()))

    def refresh(self) -> None:
        """Reads the settings and the saved conversation's numbers again (no request to a provider)."""
        from orchestrator.Core.ditto_context import cloud_cache_view
        from orchestrator.llm.cloud_cache import cost_limit, ttl_setting
        config = self._config()
        self.cb_ttl.setCurrentIndex(max(0, self.cb_ttl.findData(ttl_setting(config))))
        self.spn_limit.setValue(cost_limit(config))
        ditto = getattr(self.framework, "ditto_manager", None)
        if ditto is None:
            self.view = None
            self.lbl_info.setText(self._t("context.no_ditto"))
            return
        try:
            self.view = cloud_cache_view(ditto, self._history_factory(), config)
        except Exception as e:  # noqa: BLE001 - shown to the person as text
            self.view = None
            self.lbl_info.setText(self._t("cloud_cache.failed", reason=one_line(str(e))))
            return
        self.lbl_info.setText(self.text())

    def text(self) -> str:
        """The overview in the person's language."""
        from orchestrator.llm.cloud_cache import dollars
        view = self.view
        if view is None:
            return ""
        lines = [self._t(f"cloud_cache.way.{view.way}")]
        if view.min_tokens:
            lines.append(self._t("cloud_cache.minimum", tokens=view.min_tokens))
        if not view.ttl_applies:
            lines.append(self._t("cloud_cache.ttl_not_here"))
        t = view.totals
        lines.append(self._t("cloud_cache.totals", calls=t.calls, read=t.read, written=t.written,
                             uncached=t.uncached, cost=dollars(t.cost_usd)))
        if t.unpriced_written:
            lines.append(self._t("cloud_cache.unpriced", tokens=t.unpriced_written))
        if view.cost_limit <= 0:
            lines.append(self._t("cloud_cache.cap_off"))
        elif view.cap_reached:
            lines.append(self._t("cloud_cache.cap_reached", limit=dollars(view.cost_limit)))
        return "\n".join(lines)

    def apply(self) -> bool:
        """Saves the lifetime and the soft cap (the person's settings; caching is never turned off by the cap)."""
        from orchestrator.llm.cloud_cache import set_cache_settings
        config = self._config()
        title = self._t("cloud_cache.title")
        if config is None:
            plain_box(self, "warning", title, self._t("cloud_cache.not_saved", reason="-"))
            return False
        try:
            saved = set_cache_settings(config, self.cb_ttl.currentData(), self.spn_limit.value())
        except ValueError as e:
            plain_box(self, "warning", title, self._t("cloud_cache.not_saved", reason=one_line(str(e))))
            return False
        if not saved:
            plain_box(self, "warning", title, self._t("cloud_cache.not_saved", reason="config.yml"))
            return False
        self.refresh()
        return True
