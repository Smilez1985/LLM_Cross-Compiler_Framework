#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Chat Window

The former "AI Chat" tab now lives in the chat panel on the right of the main window (gui/chat_panel/, delegation
design 5.9); nothing it did is lost. This module keeps what callers and tests know by name:

- `ChatWorker`: one chat turn of Ditto in a worker thread (no GUI freeze); Ditto may use the framework tools within
  the person's tool permissions, and a tool that needs a confirmation asks in a modal dialog showing every argument.
- `describe_access`: the header text for the provider and sign-in Ditto uses ("Anthropic Claude (API) · API key ·
  wrkspc_...", "Local · Ollama", "key from OPENAI_API_KEY"), read in a worker thread (it opens the credential vault).
- `ChatWindow`: the chat panel under its old name, with the old attribute names (txt_input, btn_send, btn_clear,
  lbl_access, history, confirmation, send_message, _on_ai_response, _on_ai_error, _on_tool_used, refresh_access).

Ditto's conversation stays in chat_history.json in the per-user state directory; what the panel shows comes from the
signed chat feed. "Clear chat" asks with the full list of what it deletes (full path, file name, size, what it is),
with "No" as the default.
"""

import threading  # noqa: F401 - the panel's access check starts its worker through this module's name (tests)

from PySide6.QtCore import QThread, Signal

from orchestrator.Core.ditto_manager import DittoError
from orchestrator.gui.chat_panel.panel import ChatPanel
from orchestrator.llm.auth.signin import access_text
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

logger = get_logger("ChatWindow")


class ChatWorker(QThread):
    """Runs one chat turn in the background. The history is only read here; the GUI thread appends the reply.
    `use_tools` False answers this one question without the framework tools (`llm-cli ditto ask --no-tools`; the
    panel sets it from "Options for this question")."""
    answered = Signal(object)   # ChatReply
    failed = Signal(str)
    tool_used = Signal(object)  # ToolUse, while the turn is running
    use_tools = True

    def __init__(self, ditto, question, history, confirm):
        super().__init__()
        self.ditto = ditto
        self.question = question
        self.history = history
        self.confirm = confirm

    def run(self):
        try:
            if not self.ditto:
                raise DittoError("The AI assistant (Ditto) is not initialized.")
            reply = self.ditto.chat(self.question, self.history, confirm=self.confirm, use_tools=self.use_tools,
                                    on_tool=self.tool_used.emit)
            self.answered.emit(reply)
        except DittoError as e:
            self.failed.emit(str(e))
        except Exception as e:  # a bug, not a provider problem: keep the details in the log
            logger.error(f"Chat turn failed: {e}", exc_info=True)
            self.failed.emit(f"Unexpected error: {redact_secrets(str(e))}")


def describe_access(ditto, translate=None) -> str:
    """The header text for Ditto's current provider and sign-in (no secrets)."""
    if ditto is None:
        return ""
    try:
        return access_text(ditto.describe("chat"), translate)
    except Exception:  # not set up (DittoError) or a broken setting: the chat itself explains it
        return translate("chat.access.not_set_up", "AI not set up") if translate else "AI not set up"


class ChatWindow(ChatPanel):
    """The chat panel under the name of the former "AI Chat" tab (design 5.9): same attributes, same behaviour."""


__all__ = ["ChatWorker", "describe_access", "ChatWindow"]
