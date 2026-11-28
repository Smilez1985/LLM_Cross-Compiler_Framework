#!/usr/bin/env python3
"""
The question before a value of an unconfirmed entry of Ditto's archive is used (DESIGN, Targets, hardware and
templates: Ditto as the pilot in the module wizard, step 3a). The framework, not the model, opens it: the module
wizard asks once per pending value of Ditto's proposal of the format values (Core/format_proposal.PendingValue) before
it fills the form, so no value of an unconfirmed claim reaches the form without the person.

The dialog shows the field, the value, the entry's sentence, its sources and how many independent domains say it of
how many the archive's security level needs; the answers are FormatProposal.decide's: "use" (the entry stays
unconfirmed), "confirm" (the person checked it: DittoArchive.confirm, then used) and "skip" (the field stays empty;
also the answer of Escape and of closing the window). Every text is plain text.
"""

from typing import Any, Optional

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QVBoxLayout

from orchestrator.gui.plain import button_text, plain_label
from orchestrator.utils.localization import current_language, ui_text


def question_text(pending: Any, lang: str) -> str:
    """The question's text for a PendingValue."""
    entry = pending.entry
    evidence = "\n".join(f"- {address}" for address in entry.evidence) or ui_text("pilot.claim.no_evidence", lang)
    competing = ui_text("pilot.claim.competing", lang, count=len(entry.competing)) if entry.competing else ""
    return ui_text("pilot.claim.text", lang, field=pending.path, format=pending.format_id, value=pending.shown_value,
                   have=entry.independent_sources, needed=pending.needed, competing=competing, entry=entry.key,
                   text=entry.text, evidence=evidence)


class ClaimQuestionDialog(QDialog):
    """One pending value: the person's answer is in `decision` ("use", "confirm" or "skip") once it is closed."""

    def __init__(self, pending: Any, parent: Any = None, lang: Optional[str] = None, *, can_confirm: bool = True):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.decision = "skip"
        self.setObjectName("claim_question_dialog")
        self.setWindowTitle(ui_text("pilot.claim.title", self.lang))
        layout = QVBoxLayout(self)
        self.lbl_claim = plain_label(question_text(pending, self.lang), self)
        self.lbl_claim.setObjectName("lbl_claim_question")
        layout.addWidget(self.lbl_claim)
        buttons = QDialogButtonBox(self)
        self.btn_use = self._button(buttons, "btn_claim_use", "pilot.claim.use", "use")
        self.btn_confirm = self._button(buttons, "btn_claim_confirm", "pilot.claim.confirm", "confirm")
        self.btn_skip = self._button(buttons, "btn_claim_skip", "pilot.claim.skip", "skip")
        self.btn_confirm.setEnabled(can_confirm)   # without the archive there is nothing to confirm in
        self.btn_skip.setDefault(True)   # "No" by default: nothing of an unconfirmed claim without a choice
        layout.addWidget(buttons)

    def _button(self, box: QDialogButtonBox, name: str, key: str, decision: str) -> QPushButton:
        button = QPushButton(button_text(ui_text(key, self.lang)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False, d=decision: self._answer(d))
        box.addButton(button, QDialogButtonBox.ActionRole)
        return button

    def _answer(self, decision: str) -> None:
        self.decision = decision
        self.accept()

    def reject(self) -> None:   # Escape or closing the window: the field stays empty
        self.decision = "skip"
        super().reject()


def ask_claim(parent: Any, pending: Any, lang: Optional[str] = None, *, can_confirm: bool = True) -> str:
    """The person's answer to one pending value ("use", "confirm" or "skip"); `can_confirm` False: Ditto's archive is
    not open, so the entry cannot be confirmed from here."""
    dialog = ClaimQuestionDialog(pending, parent, lang, can_confirm=can_confirm)
    dialog.exec()
    return dialog.decision


__all__ = ["ClaimQuestionDialog", "ask_claim", "question_text"]
