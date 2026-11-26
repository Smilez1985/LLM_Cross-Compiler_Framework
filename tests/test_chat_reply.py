"""Reply to a row of the chat (maintainer, 2026-11-24, recon: the composer's reply chip, FeedRefs.reply_to and its
sending existed, nothing set the reference). "Reply to this" in the context menu of a saved row's bubble, on its
texts and on the bubble itself, refers the next message to that row; Escape or "Remove" takes it away.
"""

import pytest

from orchestrator.Core.chat_feed import FeedWriter
from test_chat_panel import FakeWorker, decide, feed, make_panel  # noqa: F401 - the panel's fixtures

pytest.importorskip("PySide6")
pytestmark = pytest.mark.usefixtures("qapp")


def ditto_answer(feed):
    return FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"),
                                           ai_text="The board has an NPU.", ai_text_kind="answer")


def reply_actions(menu):
    return [a for a in menu.actions() if a.objectName() == "act_chat_reply"]


def test_reply_to_a_row_sends_the_next_message_with_its_reference(make_panel, feed):
    ditto_answer(feed)
    panel = make_panel(feed)
    bubble = panel.feed_view.focus_chain()[-1]
    entry_id = bubble.entry_id
    assert bubble.can_reply() and entry_id.startswith("fe_")
    bubble.request_reply()
    assert panel.composer.reply_to == entry_id
    assert panel.composer.lbl_about.isVisibleTo(panel) and "Ditto" in panel.composer.lbl_about.text()
    panel.txt_input.setText("Which formats does it take?")
    panel.send_message()
    sent = panel.framework.chat_feed.after(0).entries[-1].entry
    assert sent.person_text == "Which formats does it take?" and sent.refs.reply_to == entry_id
    assert panel.composer.reply_to is None, "the reference goes with the message it was for"


def test_the_texts_and_the_bubble_offer_reply_to_this(make_panel, feed, monkeypatch):
    from PySide6.QtWidgets import QLabel, QMenu
    from orchestrator.gui.chat_panel import bubbles
    ditto_answer(feed)
    decide(feed)
    panel = make_panel(feed)
    shown = []
    monkeypatch.setattr(QMenu, "exec", lambda menu, *a: shown.append(menu))
    for bubble in panel.feed_view.focus_chain():
        texts = [label for label in bubble.findChildren(QLabel) if label.text()]
        assert texts, bubble
        for label in texts:
            menu = bubbles.label_menu(label, "en", bubble.reply_action())
            assert [a.text() for a in reply_actions(menu)] == ["Reply to this"], label.text()
    bubble = panel.feed_view.focus_chain()[-1]
    reply_actions(bubbles.label_menu(bubble.findChildren(QLabel)[0], "en", bubble.reply_action()))[0].trigger()
    assert panel.composer.reply_to == bubble.entry_id


def test_escape_takes_the_reference_away(make_panel, feed):
    ditto_answer(feed)
    panel = make_panel(feed)
    panel.feed_view.focus_chain()[-1].request_reply()
    panel.composer.btn_remove_about.click()
    assert panel.composer.reply_to is None and not panel.composer.lbl_about.isVisibleTo(panel)


def test_a_row_shown_here_only_is_no_reply(make_panel, feed, monkeypatch):
    from orchestrator.Core.chat_feed import FeedWriteError

    def full(*args, **kwargs):
        raise FeedWriteError("disk_full", "The chat could not be saved: the disk is full.")
    panel = make_panel(feed)
    monkeypatch.setattr(feed, "append", full)
    panel.txt_input.setText("Please build it")
    panel.send_message()
    local = panel.feed_view.focus_chain()[-1]
    assert not local.can_reply() and local.reply_action() is None


def test_the_words_exist_in_both_languages():
    from orchestrator.gui.chat_panel.bubbles import text
    assert text("chat.bubble.reply", "en") == "Reply to this"
    assert text("chat.bubble.reply", "de") == "Darauf antworten"
