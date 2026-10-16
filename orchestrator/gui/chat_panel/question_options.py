#!/usr/bin/env python3
"""
"Options for this question" above the composer (W1': `llm-cli ditto ask --no-tools`, `--provider`, `--model` by
mouse). A button shows the row: "Answer without tools", a provider ("As in the AI settings" or one of the catalogue)
and a model (the provider's suggested ones, or a typed name). They apply to the next question only and are reset
after it is sent; the saved AI settings never change (the panel asks a Ditto made with Core/ai_override.ditto_with).
The chat's feed, questions and approvals work as before.
"""

from dataclasses import dataclass
from typing import Any, List, Optional

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from orchestrator.gui.ai_choice import chosen, fill_models, fill_providers
from orchestrator.gui.plain import button_text, one_line, plain_label, plain_tooltip
from orchestrator.utils.localization import ui_text


@dataclass(frozen=True)
class QuestionChoice:
    """What the next question uses: tools or not, and a provider/model for it alone (None: the AI settings)."""
    use_tools: bool = True
    provider: Optional[str] = None
    model: Optional[str] = None

    @property
    def is_default(self) -> bool:
        return self.use_tools and not self.provider and not self.model

    def summary(self, lang: Optional[str] = None) -> str:
        """The choice in the person's words ("without tools, provider ollama, model m")."""
        parts: List[str] = []
        if not self.use_tools:
            parts.append(ui_text("ditto_models.chat.what.no_tools", lang))
        if self.provider:
            parts.append(ui_text("ditto_models.chat.what.provider", lang, provider=one_line(self.provider)))
        if self.model:
            parts.append(ui_text("ditto_models.chat.what.model", lang, model=one_line(self.model)))
        return ", ".join(parts)


class QuestionOptions(QWidget):
    """The toggle button and the row of options for the next question."""

    def __init__(self, lang: str = "en", catalog_source: Any = None, parent: Optional[QWidget] = None):
        """catalog_source: a callable that returns the provider catalogue (read when the row is first shown)."""
        super().__init__(parent)
        self.lang = lang
        self._catalog_source = catalog_source
        self._catalog: Any = None
        self._filled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.btn_toggle = QToolButton(self)
        self.btn_toggle.setObjectName("btn_chat_options")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setAutoRaise(True)
        self.btn_toggle.toggled.connect(self._toggled)
        layout.addWidget(self.btn_toggle)

        self.row = QWidget(self)
        self.row.setObjectName("row_chat_options")
        box = QVBoxLayout(self.row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        line = QHBoxLayout()
        self.chk_no_tools = QCheckBox(self.row)
        self.chk_no_tools.setObjectName("chk_chat_no_tools")
        line.addWidget(self.chk_no_tools)
        line.addStretch(1)
        box.addLayout(line)
        line = QHBoxLayout()
        self.lbl_provider = plain_label("", self.row, wrap=False)
        line.addWidget(self.lbl_provider)
        self.cb_provider = QComboBox(self.row)
        self.cb_provider.setObjectName("cb_chat_provider")
        line.addWidget(self.cb_provider, stretch=1)
        box.addLayout(line)
        line = QHBoxLayout()
        self.lbl_model = plain_label("", self.row, wrap=False)
        line.addWidget(self.lbl_model)
        self.cb_model = QComboBox(self.row)
        self.cb_model.setObjectName("cb_chat_model")
        self.cb_model.setEditable(True)
        line.addWidget(self.cb_model, stretch=1)
        box.addLayout(line)
        self.lbl_once = plain_label("", self.row)
        self.lbl_once.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        box.addWidget(self.lbl_once)
        layout.addWidget(self.row)
        self.row.setVisible(False)
        self.cb_provider.addItem("", "")
        self.cb_provider.currentIndexChanged.connect(
            lambda _i: fill_models(self.cb_model, self._catalog, self.cb_provider.currentData() or ""))
        self.retranslate(lang)

    def _toggled(self, shown: bool) -> None:
        if shown:
            self.fill()
        self.row.setVisible(shown)

    def fill(self) -> None:
        """Reads the provider catalogue once (the same one the CLI checks --provider against)."""
        if self._filled:
            return
        self._filled = True
        try:
            self._catalog = self._catalog_source() if callable(self._catalog_source) else self._catalog_source
        except Exception:  # noqa: BLE001 - no catalogue: only "as in the AI settings" is offered
            self._catalog = None
        fill_providers(self.cb_provider, self._catalog, ui_text("ditto_models.chat.settings", self.lang))
        fill_models(self.cb_model, self._catalog, self.cb_provider.currentData() or "")

    def choice(self) -> QuestionChoice:
        provider, model = chosen(self.cb_provider, self.cb_model)
        return QuestionChoice(use_tools=not self.chk_no_tools.isChecked(), provider=provider, model=model)

    def reset(self) -> None:
        """Back to the AI settings with tools (after a question was sent: the options are for one question)."""
        self.chk_no_tools.setChecked(False)
        self.cb_provider.setCurrentIndex(0)
        self.cb_model.setEditText("")

    def set_busy(self, busy: bool) -> None:
        self.row.setEnabled(not busy)

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.btn_toggle.setText(button_text(ui_text("ditto_models.chat.options", lang)))
        self.btn_toggle.setToolTip(plain_tooltip(ui_text("ditto_models.chat.options_tip", lang)))
        self.btn_toggle.setAccessibleName(ui_text("ditto_models.chat.options", lang))
        self.chk_no_tools.setText(button_text(ui_text("ditto_models.chat.no_tools", lang)))
        self.chk_no_tools.setToolTip(plain_tooltip(ui_text("ditto_models.chat.no_tools_tip", lang)))
        self.lbl_provider.setText(ui_text("ditto_models.chat.provider", lang))
        self.lbl_model.setText(ui_text("ditto_models.chat.model", lang))
        self.cb_provider.setAccessibleName(ui_text("ditto_models.chat.provider", lang))
        self.cb_model.setAccessibleName(ui_text("ditto_models.chat.model", lang))
        self.cb_model.lineEdit().setPlaceholderText(ui_text("ditto_models.chat.model_placeholder", lang))
        self.cb_provider.setItemText(0, ui_text("ditto_models.chat.settings", lang))
        self.lbl_once.setText(ui_text("ditto_models.chat.once", lang))


__all__ = ["QuestionChoice", "QuestionOptions"]
