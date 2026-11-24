"""The filter notice of the chat panel (orchestrator/gui/chat_panel/filter_hint.py, composer.py; docs/DESIGN.md, "The
person is told, quietly and exactly, what was filtered out"): a quiet symbol with the whole list as its mouse-over, the
person's own typed stretches underlined while they are relevant, the list after the answer with what was put back, and
nothing at all for a local provider or when nothing is replaced.

Every value is made at run time from random characters, so this file holds no secret and no secret-shaped literal.
"""

import pytest

pytest.importorskip("PySide6")   # the run without the GUI extra skips this file
from PySide6.QtCore import Qt  # noqa: E402

from test_chat_panel import feed, make_panel  # noqa: E402, F401 - the fixtures
from test_secret_by_place import value  # noqa: E402

from orchestrator.Core.ditto_manager import ChatReply
from orchestrator.gui.chat_panel.composer import Composer
from orchestrator.llm.filter_report import FilterReport
from orchestrator.llm.privacy import PrivacyFilter
from orchestrator.llm.types import Message

KEY = b"k" * 32
PASSWORD = value("notice")
TYPED = f"please run tool login --password {PASSWORD} on 10.1.2.3 today"


def report_of(text, place="input"):
    report = FilterReport()
    PrivacyFilter("STRICT", KEY).sanitize(text, place, report)
    return report


def previewer(text):
    report = report_of(text)
    return None if report.empty else report


def composer(qapp, lang="en"):
    box = Composer(lang)
    box.previewer = previewer
    return box


def test_the_symbol_is_hidden_until_the_filter_has_something_to_tell(qapp):
    box = composer(qapp)
    assert box.btn_filter.objectName() == "btn_chat_filter" and not box.btn_filter.isVisibleTo(box)
    box.input.setPlainText("how do I build llama.cpp?")
    box._preview()
    assert not box.btn_filter.isVisibleTo(box) and box.input.extraSelections() == []


def test_typing_marks_exactly_the_stretches_that_will_be_replaced_and_lists_them(qapp):
    box = composer(qapp)
    box.input.setPlainText(TYPED)
    box._preview()
    assert box.btn_filter.isVisibleTo(box)
    marked = [TYPED[s.cursor.selectionStart():s.cursor.selectionEnd()] for s in box.input.extraSelections()]
    assert marked == [PASSWORD, "10.1.2.3"]
    assert all(s.format.underlineStyle() != s.format.UnderlineStyle.NoUnderline for s in box.input.extraSelections())
    tip = box.btn_filter.toolTip()
    assert "Before this leaves your computer, the filter replaces" in tip
    assert "your input: 1 IP address; 1 password flag (SECRET_1)" in tip.replace("\n  ", "")
    assert PASSWORD not in tip and "10.1.2.3" not in tip and "<SECRET_" not in tip


def test_marks_sit_right_after_a_character_outside_the_basic_plane(qapp):
    box = composer(qapp)
    text = f"😀 tool login --password {PASSWORD}"
    box.input.setPlainText(text)
    box._preview()
    [selection] = box.input.extraSelections()
    assert selection.cursor.selectedText() == PASSWORD


def test_an_empty_field_a_click_and_the_next_question_take_symbol_and_marks_away(qapp):
    box = composer(qapp)
    box.input.setPlainText(TYPED)
    box._preview()
    box.input.clear()
    assert not box.btn_filter.isVisibleTo(box) and box.input.extraSelections() == []
    box.input.setPlainText(TYPED)
    box._preview()
    box.btn_filter.click()
    assert not box.btn_filter.isVisibleTo(box) and box.input.extraSelections() == []


def test_after_the_answer_the_list_is_by_place_and_says_what_was_put_back(qapp):
    box = composer(qapp)
    report = FilterReport()
    filt = PrivacyFilter("STRICT", KEY)
    _, mapping = filt.sanitize(f"tool login --password {PASSWORD}", "input", report)
    filt.sanitize("mail a@b.invalid", "history", report)
    filt.sanitize("see /home/alice/notes.md", "knowledge", report)
    report.note_restored(next(iter(mapping)), mapping)
    box.show_sent(report)
    tip = box.btn_filter.toolTip()
    assert box.btn_filter.isVisibleTo(box) and "The filter replaced before sending" in tip
    for line in ("your input: 1 password flag (SECRET_1)", "history: 1 e-mail address", "knowledge excerpts: 1 path"):
        assert line in tip.replace("\n  ", ""), line
    assert "Put back in the answer: 1 password flag." in tip
    assert PASSWORD not in tip and "alice" not in tip and "a@b.invalid" not in tip
    box.show_sent(None)
    assert not box.btn_filter.isVisibleTo(box)


def test_the_notice_follows_the_language(qapp):
    box = composer(qapp, "de")
    box.input.setPlainText(TYPED)
    box._preview()
    tip = box.btn_filter.toolTip().replace("\n  ", "")
    assert "Bevor dies den Computer verlässt, ersetzt der Filter" in tip
    assert "Eingabe: 1 IP-Adresse; 1 password flag (SECRET_1)" in tip
    box.retranslate("en")
    assert "Before this leaves your computer" in box.btn_filter.toolTip()


def test_the_symbol_says_nothing_about_the_typed_text_and_is_named_for_screen_readers(qapp):
    box = composer(qapp)
    box.input.setPlainText(TYPED + " <b>bold</b>")
    box._preview()
    tip = box.btn_filter.toolTip()
    assert "bold" not in tip and "please run" not in tip
    assert box.input.document().toPlainText() == TYPED + " <b>bold</b>", "the marks change no character"
    assert box.btn_filter.accessibleName() == "Filter notice"


# --- the panel ------------------------------------------------------------------------------------------------

def test_the_panel_asks_ditto_for_the_preview_and_shows_the_report_after_the_answer(make_panel, feed, qapp):  # noqa: F811
    class Ditto:
        def __init__(self):
            self.asked = []

        def filter_preview(self, text):
            self.asked.append(text)
            return previewer(text)

    ditto = Ditto()
    panel = make_panel(feed, ditto=ditto)
    panel.txt_input.setPlainText(TYPED)
    panel.composer._preview()
    assert ditto.asked == [TYPED] and panel.composer.btn_filter.isVisibleTo(panel.composer)
    panel.send_message()
    assert not panel.composer.btn_filter.isVisibleTo(panel.composer), "the notice of the old text goes with the send"
    sent = report_of(TYPED)
    panel._on_ai_response(ChatReply(question=TYPED, text="Done.", filter=sent,
                                    new_messages=[Message.user(TYPED), Message(role="assistant", content="Done.")]))
    assert panel.composer.btn_filter.isVisibleTo(panel.composer)
    assert "The filter replaced before sending" in panel.composer.btn_filter.toolTip()
    panel._on_ai_response(ChatReply(question="q", text="Again.", new_messages=[Message.user("q"), Message(
        role="assistant", content="Again.")]))
    assert not panel.composer.btn_filter.isVisibleTo(panel.composer), "an answer without a report has no notice"


def test_a_ditto_without_a_preview_gives_no_symbol(make_panel, feed, qapp):  # noqa: F811
    panel = make_panel(feed)
    panel.txt_input.setPlainText(TYPED)
    panel.composer._preview()
    assert not panel.composer.btn_filter.isVisibleTo(panel.composer)


# --- the marks in the bubbles -----------------------------------------------------------------------------------

def _turn(panel, text, answer, report):
    panel.txt_input.setPlainText(text)
    panel.send_message()
    panel._on_ai_response(ChatReply(question=text, text=answer, filter=report,
                                    new_messages=[Message.user(text), Message(role="assistant", content=answer)]))


def _editors(bubble):
    from orchestrator.gui.chat_panel.filter_hint import MarkedText
    return bubble.findChildren(MarkedText)


def test_the_sent_question_and_the_answer_carry_the_marks_of_what_was_replaced(make_panel, feed, qapp):  # noqa: F811
    panel = make_panel(feed)
    report = report_of(TYPED)
    answer = f"Done: I logged in with {PASSWORD} at 10.1.2.3."
    _turn(panel, TYPED, answer, report)
    question_bubble = panel.feed_view.bubble_of(panel._turn_person_id)
    [editor] = _editors(question_bubble)
    # the chat's own protection already shows a password as REDACTED in the person's bubble; the stretch that is
    # still readable there, the address, is the one that gets a mark
    assert editor.marked() == ["10.1.2.3"] and editor.isReadOnly()
    assert PASSWORD not in editor.toPlainText()
    assert all(s.format.underlineStyle() != s.format.UnderlineStyle.NoUnderline for s in editor.extraSelections())
    answer_bubble = panel.feed_view.bubble_of(panel._marked_ids[1])
    assert [m for e in _editors(answer_bubble) for m in e.marked()] == [PASSWORD, "10.1.2.3"]
    shown = TYPED.replace(PASSWORD, "REDACTED")
    assert editor.toPlainText() == shown, "the marks change no character"
    assert question_bubble.plain_text().count(shown) == 1 and answer in answer_bubble.plain_text()


def test_the_marks_go_with_a_click_on_the_symbol_and_with_the_next_question(make_panel, feed, qapp):  # noqa: F811
    panel = make_panel(feed)
    _turn(panel, TYPED, f"Use {PASSWORD}.", report_of(TYPED))
    question = panel.feed_view.bubble_of(panel._turn_person_id)
    assert [m for e in _editors(question) for m in e.marked()]
    panel.composer.btn_filter.click()
    assert [m for e in _editors(question) for m in e.marked()] == [] and panel._marked_ids == []
    _turn(panel, TYPED, f"Use {PASSWORD}.", report_of(TYPED))
    first = panel.feed_view.bubble_of(panel._turn_person_id)
    panel.txt_input.setPlainText("and now something else")
    panel.send_message()
    assert [m for e in _editors(first) for m in e.marked()] == [], "the next question takes the old marks away"


def test_a_bubble_without_a_replaced_stretch_stays_a_plain_label(make_panel, feed, qapp):  # noqa: F811
    from PySide6.QtWidgets import QLabel
    panel = make_panel(feed)
    _turn(panel, "how do I build llama.cpp?", "With cmake.", report_of(TYPED))
    bubble = panel.feed_view.bubble_of(panel._turn_person_id)
    assert _editors(bubble) == [] and any(label.text() == "how do I build llama.cpp?"
                                          for label in bubble.findChildren(QLabel))
    assert all(label.textFormat() == Qt.PlainText for label in bubble.findChildren(QLabel))


def test_an_answer_without_a_report_is_not_marked(make_panel, feed, qapp):  # noqa: F811
    panel = make_panel(feed)
    _turn(panel, TYPED, "Done.", None)
    assert _editors(panel.feed_view.bubble_of(panel._turn_person_id)) == [] and panel._marked_ids == []
