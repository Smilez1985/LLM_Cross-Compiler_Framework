#!/usr/bin/env python3
"""
The words of a chat-feed row as the person reads them, without Qt: the chat panel's bubbles and the Ditto console
(the window's and `llm-cli ditto console`) render a row's framework line through the same functions, so the two can
never say different things about the same row.

* `text(key, lang, **values)`: a framework text, the feed's own (chat_feed.TEXTS) or the GUI's tables (l10n_chat and
  the rest), English as the fallback, a placeholder without a value shown as {name}.
* `param_text(entry, name)`: one parameter on one line, hidden characters escaped, quoted when it came from outside
  the framework (delegation design 5.2 R2).
* `framework_text(entry, lang)`: the row's framework line from its text key and parameters.
"""

from typing import Any

from orchestrator.Core.chat_feed import TEXTS as FEED_TEXTS
from orchestrator.Core.chat_feed import FeedEntry
from orchestrator.tools.confirmation import visible
from orchestrator.utils.localization import text_tables


class _Fill(dict):
    """format_map values: a placeholder without a value stays visible as {name} instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def text(key: str, lang: str = "en", **values: Any) -> str:
    """A framework text: the feed's own (chat_feed.TEXTS) or the GUI's (l10n_chat and the rest), in `lang` with
    English as the fallback, placeholders filled; a key without a text is shown as the key."""
    if key in FEED_TEXTS["en"]:
        template = FEED_TEXTS.get(lang, {}).get(key) or FEED_TEXTS["en"][key]
    else:
        table = text_tables()   # German in the person's form of address, as ui_text reads it
        template = table.get(lang, {}).get(key) or table["en"].get(key) or key
    if "{path:" in template:   # a place in the window, from the live labels in `lang` (wave 6, V2)
        from orchestrator.utils.next_steps import fill_paths
        template = fill_paths(template, lang)
    return template.format_map(_Fill(values)) if values else template


def one_line(value: Any) -> str:
    """`value` on one line: every line break and hidden character shown as an escape (gui/plain.one_line)."""
    return visible(value if isinstance(value, str) else str(value))


def param_text(entry: FeedEntry, name: str) -> str:
    """One parameter of a row as the person reads it: one line, hidden characters escaped, and quoted when it comes
    from outside the framework (R2)."""
    value = one_line(entry.params.get(name, ""))
    return f"'{value}'" if entry.param_origin.get(name) == "external" else value


def framework_text(entry: FeedEntry, lang: str = "en") -> str:
    """The framework's own line of a row: its text key in the person's language with the parameters filled (each by
    param_text), or the generic line of the row's kind."""
    key = entry.text_key or f"chat.kind.{entry.kind}"
    return text(key, lang, **{name: param_text(entry, name) for name in entry.params})


__all__ = ["text", "one_line", "param_text", "framework_text"]
