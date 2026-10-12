#!/usr/bin/env python3
"""
The fix proposals AI clients made for failed builds (Tools > Fix Proposals from AI Clients..., MCP design T4).

Each proposal says who proposed it (the caller as the framework knows it, and the client's own name marked as
self-reported) and that it is AI-generated. Its texts are the AI's: they are shown as plain text with invisible
characters removed, never as rich text, and nothing here runs them. The person does the manual step themselves.
"""

from typing import List

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from orchestrator.utils.untrusted import sanitize_untrusted


def proposal_text(proposals: List) -> str:
    """The proposals as plain text for the person, newest first."""
    if not proposals:
        return "No AI client has proposed a fix for a failed build."
    blocks = []
    for p in reversed(proposals):
        def clean(value) -> str:
            return sanitize_untrusted(str(value), 2000)
        lines = [f"Build request {p.request_id}" + (f": {clean(p.request)}" if p.request else ""),
                 f"Proposed by {clean(p.who())}, AI-generated" + (f", {p.created}" if p.created else ""),
                 f"Summary: {clean(p.summary)}"]
        if p.root_cause:
            lines.append(f"Cause: {clean(p.root_cause)}")
        lines += [f"Manual step on the {'device' if p.where == 'DEVICE' else 'build computer'} (not run by the "
                  f"framework): {clean(p.manual_step)}",
                  f"Confidence: {p.confidence:.0%}"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class ClientProposalsDialog(QDialog):
    """Read-only list of the kept proposals."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fix Proposals from AI Clients")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Fixes AI clients proposed for failed builds. They are AI-generated manual steps: check "
                                "them yourself; the framework never runs them."))
        self.text = QPlainTextEdit(self)  # plain text: an AI's text can never become markup or a link
        self.text.setReadOnly(True)
        try:
            proposals = store.list()
        except Exception as e:  # noqa: BLE001 - a damaged file is reported, never a crash
            proposals = []
            self.text.setPlainText(f"The proposals cannot be read: {e}")
        else:
            self.text.setPlainText(proposal_text(proposals))
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
