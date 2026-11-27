#!/usr/bin/env python3
"""
The Models tab's Compare Quality...: `llm-cli models compare NAME... [--base NAME]` by mouse.

The dialog lists the GGUF models of the tab with a check box each (nothing ticked) and a base to compare against
("none" by default). Compare measures the ticked models' perplexity on the auto-tuning's fixed evaluation text
(Core/dataset_manager.DatasetManager.compare_models: Ditto's catalogue models natively, every other model only in the
Docker sandbox) in a plain worker thread, so the window stays responsive; the result comes back through
gui/worker_relay.py and is shown as plain text, ranked, each model with its ratio to the base. Every text is plain
text.
"""

import threading
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from orchestrator.gui.plain import button_text, plain_label
from orchestrator.utils.localization import current_language, ui_text


def result_lines(result: dict, lang: str) -> List[str]:
    """The comparison as plain lines: the base, the ranking (perplexity, ratio), the failed ones with their reason
    and the evaluation it was measured on."""
    lines = []
    base = result.get("baseline") or {}
    if base:
        name = str(base.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
        lines.append(ui_text("compare.base_line", lang, name=name, ppl=f"{base['ppl']:.4f}") if "ppl" in base else
                     ui_text("compare.failed_line", lang, name=name, reason=base.get("error", "")))
    for number, entry in enumerate(result.get("ranking") or [], 1):
        if "ppl" in entry:
            ratio = f"{entry['ratio']:.3f}" if entry.get("ratio") else "-"
            lines.append(ui_text("compare.line", lang, n=number, name=entry["name"], ppl=f"{entry['ppl']:.4f}",
                                 ratio=ratio))
        else:
            lines.append(ui_text("compare.failed_line", lang, name=entry["name"], reason=entry["error"]))
    ev = result.get("evaluation") or {}
    if ev:
        lines.append(ui_text("compare.evaluation", lang, chunks=ev["chunks"], ctx=ev["ctx"]))
    return lines


class ModelCompareDialog(QDialog):
    """`rows`: the Models tab's rows (model_library rows); only those with a GGUF file are offered."""

    def __init__(self, framework: Any, rows: List[Any], parent: Any = None, lang: Optional[str] = None,
                 compare: Any = None):
        super().__init__(parent)
        from orchestrator.gui.worker_relay import worker_relay
        self.framework, self.lang = framework, lang or current_language()
        self.rows = [r for r in rows if getattr(r, "gguf", None) is not None]
        self._compare = compare          # tests replace the measurement
        self._token: Optional[object] = None
        self.result: Optional[dict] = None
        self.setObjectName("model_compare_dialog")
        self.setWindowTitle(self._t("compare.title"))
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("compare.intro"), self))
        self.list_compare_models = QListWidget(self)
        self.list_compare_models.setObjectName("list_compare_models")
        for row in self.rows:
            item = QListWidgetItem(row.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list_compare_models.addItem(item)
        layout.addWidget(self.list_compare_models, stretch=1)
        form = QFormLayout()
        self.cmb_compare_base = QComboBox(self)
        self.cmb_compare_base.setObjectName("cmb_compare_base")
        self.cmb_compare_base.addItem(self._t("compare.no_base"), None)
        for index, row in enumerate(self.rows):
            self.cmb_compare_base.addItem(row.name, index)
        form.addRow(self._t("compare.base"), self.cmb_compare_base)
        layout.addLayout(form)
        self.lbl_compare_status = plain_label("", self)
        self.lbl_compare_status.setObjectName("lbl_compare_status")
        layout.addWidget(self.lbl_compare_status)
        self.txt_compare_result = QPlainTextEdit(self)
        self.txt_compare_result.setObjectName("txt_compare_result")
        self.txt_compare_result.setReadOnly(True)
        layout.addWidget(self.txt_compare_result, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_compare_run = QPushButton(button_text(self._t("compare.run")), self)
        self.btn_compare_run.setObjectName("btn_compare_run")
        self.btn_compare_run.setAutoDefault(False)
        self.btn_compare_run.clicked.connect(lambda _=False: self.run())
        buttons.addButton(self.btn_compare_run, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        worker_relay().done.connect(self._done, Qt.QueuedConnection)

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def chosen(self) -> List[Any]:
        return [self.rows[i] for i in range(self.list_compare_models.count())
                if self.list_compare_models.item(i).checkState() == Qt.Checked]

    def base(self) -> Optional[Any]:
        index = self.cmb_compare_base.currentData()
        return self.rows[index] if index is not None else None

    def run(self) -> bool:
        """Compare: the ticked models (at least one) against the base, off the GUI thread."""
        from orchestrator.gui.worker_relay import worker_relay
        if self._token is not None:
            return False
        chosen, base = self.chosen(), self.base()
        if not chosen:
            self.lbl_compare_status.setText(self._t("compare.none"))
            return False
        compare = self._compare
        if compare is None:
            from orchestrator.Core.dataset_manager import DatasetManager
            compare = DatasetManager(self.framework).compare_models
        token, relay = object(), worker_relay()
        self._token = token
        self.btn_compare_run.setEnabled(False)
        self.lbl_compare_status.setText(self._t("compare.running", n=len(chosen) + (1 if base else 0)))

        def work() -> None:
            try:
                outcome: Any = ("ok", compare(base.gguf if base else None, [r.gguf for r in chosen]))
            except Exception as e:  # noqa: BLE001 - shown in the window, never a crash
                outcome = ("error", e)
            relay.done.emit(token, outcome)

        threading.Thread(target=work, name="model-compare", daemon=True).start()
        return True

    def _done(self, token: object, outcome: Any) -> None:
        if token is not self._token:
            return
        self._token = None
        self.btn_compare_run.setEnabled(True)
        kind, value = outcome
        if kind == "ok":
            self.result = value
            self.lbl_compare_status.setText(self._t("compare.done"))
            self.txt_compare_result.setPlainText("\n".join(result_lines(value, self.lang)))
        else:
            self.lbl_compare_status.setText(self._t("compare.error", reason=str(value)))

    @property
    def running(self) -> bool:
        return self._token is not None


__all__ = ["ModelCompareDialog", "result_lines"]
