"""Ditto's turn budget and compaction with a stand-in model (scripted answers) and fixed token numbers: the newest turns
kept word for word, the older ones summarized into a note in front of them, the original kept in the conversation file
for reading and undo, the did lines of the console, a question that does not fit, the CLI's `ditto context` and the
GUI's Context window box (offscreen, a real QApplication)."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")

from test_ditto import answer, llm, make_ditto  # noqa: E402,F401 - llm is a fixture

from orchestrator.Core.ditto_manager import (COMPACTION_UNDONE_NOTICE, SUMMARY_LEAD, ChatHistory,  # noqa: E402
                                             DittoError, conversation_is_consistent)
from orchestrator.llm import Message  # noqa: E402
from orchestrator.llm.task_prompts import SUMMARY_SYSTEM  # noqa: E402

WINDOW = 2500   # the person's ai_context_window: the scripted provider tells none


def long_history(turns=4, size=500):
    history = ChatHistory()
    for i in range(turns):
        history.messages += [Message.user(f"question {i} " + "x" * size),
                             Message(role="assistant", content=f"answer {i} " + "y" * size)]
    return history


def test_the_newest_turns_stay_word_for_word_and_the_older_ones_become_a_note(tmp_path, llm):
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history()
    earlier = list(history.messages)
    llm.script += [answer("Decisions: none. Open tasks: build for board-a. Facts: request req_0123abcd."),
                   answer("Next step.")]

    reply = ditto.chat("What next?", history)

    summary_request, turn_request = llm.requests
    assert summary_request.system == SUMMARY_SYSTEM and "Open tasks" in SUMMARY_SYSTEM
    budget = reply.budget
    assert budget.window == WINDOW and budget.compact and 0 < budget.keep_turns < 4
    assert reply.new_conversation and reply.new_conversation_reason == "long"
    kept = 2 * budget.keep_turns
    assert reply.kept_messages[1:] == earlier[-kept + 1:], "the kept turns are sent word for word"
    first = reply.kept_messages[0]
    assert first.content[0]["text"].startswith(SUMMARY_LEAD) and "<untrusted_data" in first.content[0]["text"]
    assert first.content[1]["text"] == earlier[-kept].text()
    assert turn_request.messages[:kept] == reply.kept_messages
    assert turn_request.messages[-1].text() == "What next?"
    assert "question 0" in summary_request.messages[0].text() and f"question {4 - budget.keep_turns}" not in \
        summary_request.messages[0].text(), "only the older turns are summarized"
    assert summary_request.max_tokens == budget.summary_tokens
    assert history.messages == earlier, "the conversation itself is not changed by the turn"
    key, params = reply.context_lines[0]
    assert key == "activity.context.compacted"
    assert params == {"window": str(WINDOW), "summarized": str(len(earlier) - kept), "kept": str(kept),
                      "before": str(budget.total_before()), "after": str(budget.after)}

    history.apply(reply)
    assert history.messages == [*reply.kept_messages, *reply.new_messages]
    assert conversation_is_consistent(history.messages)
    record = history.compactions[-1]
    assert record["id"] == "c1" and record["reason"] == "long" and record["made"] == kept
    assert history.compaction_messages(record) == earlier, "the original is kept, never lost"
    assert "Open tasks" in record["summary"]


def test_undo_brings_the_original_back_in_front_of_what_came_after(tmp_path, llm):
    path = tmp_path / ChatHistory.FILE_NAME
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history()
    history.path = path
    earlier = list(history.messages)
    llm.script += [answer("A note."), answer("Next step.")]
    reply = ditto.chat("What next?", history)
    history.apply(reply)
    assert history.save()
    loaded = ChatHistory.load(path)
    assert json.loads(path.read_text())["version"] == 4 and loaded.compactions == history.compactions
    assert loaded.undo_compaction()["id"] == "c1"
    assert loaded.messages == [*earlier, *reply.new_messages]
    assert loaded.compactions == [] and loaded.transcript[-1].text == COMPACTION_UNDONE_NOTICE
    assert loaded.undo_compaction() is None, "nothing left to undo"


def test_when_nothing_is_kept_undo_takes_the_note_out_of_the_question(tmp_path, llm):
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history(turns=2, size=2500)      # even the newest turn does not fit into 40 % of the room
    earlier = list(history.messages)
    llm.script += [answer("A note."), answer("Next step.")]
    reply = ditto.chat("What next?", history)
    assert reply.kept_messages == [] and reply.budget.keep_turns == 0
    opening = reply.new_messages[0]
    assert opening.content[0]["text"].startswith(SUMMARY_LEAD) and opening.content[-1]["text"] == "What next?"
    history.apply(reply)
    assert history.compactions[-1]["summary_in_question"] is True
    assert history.undo_compaction() is not None
    assert history.messages[:len(earlier)] == earlier
    assert history.messages[len(earlier)].content == "What next?", "the note is taken out of the question again"


def test_a_short_conversation_is_sent_as_it_is(tmp_path, llm):
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history(turns=2, size=50)
    llm.script += [answer("ok")]
    reply = ditto.chat("And?", history)
    assert not reply.new_conversation and reply.compaction is None and reply.context_lines == []
    assert len(llm.requests[0].messages) == 5 and not reply.budget.compact


def test_clear_drops_the_kept_originals_with_the_conversation(tmp_path, llm):
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history()
    llm.script += [answer("A note."), answer("Next step.")]
    history.apply(ditto.chat("What next?", history))
    history.clear()
    assert history.compactions == [] and history.messages == []


def test_a_question_too_long_for_the_window_is_refused_before_any_call(tmp_path, llm):
    ditto = make_ditto(tmp_path, ai_context_window=1024)
    with pytest.raises(DittoError) as info:
        ditto.chat("z" * 20000, ChatHistory())
    assert "does not fit the model's context window of 1024 tokens" in str(info.value)
    assert llm.requests == []


def test_excerpts_shrink_to_the_room_and_say_so(tmp_path, llm, monkeypatch):
    ditto = make_ditto(tmp_path, ai_context_window=2600)
    wanted = []

    def knowledge(self, query, limit=5, rerank=None, max_tokens=None):
        wanted.append(max_tokens)
        return "e" * 6000 if max_tokens is None else "a short excerpt"
    monkeypatch.setattr(type(ditto), "_knowledge", knowledge)
    llm.script += [answer("ok")]
    reply = ditto.chat("Which board?", ChatHistory())
    budget = reply.budget
    assert budget.excerpts_wanted > budget.excerpts == budget.room // 3, "R5: a third of the room"
    assert wanted == [None, budget.excerpts], "fetched once more within the turn's share"
    assert "a short excerpt" in llm.requests[0].messages[-1].content[0]["text"]
    assert ("activity.context.excerpts", {"tokens": str(budget.excerpts), "window": "2600"}) in reply.context_lines


def test_the_did_line_reads_in_the_console(tmp_path):
    from orchestrator.Core.chat_feed import FeedSender, FeedStore, FeedWriter, StoredEntry
    from orchestrator.Core.ditto_console import line_of_feed
    writer = FeedWriter(FeedStore(tmp_path / "feed"), "gui")
    entry = writer.entry("ai_action", FeedSender(role="ditto", caller="ditto"), text_key="activity.context.compacted",
                         params={"window": "8192", "summarized": "6", "kept": "2", "before": "7000",
                                 "after": "2500"})
    line = line_of_feed(StoredEntry(1, entry))
    assert line.kind == "did" and line.source == "ditto"
    assert "Compacted the conversation to fit the context window of 8192 tokens: 6 earlier message(s)" in line.text
    german = line_of_feed(StoredEntry(1, entry), lang="de")
    assert "Kontextfenster von 8192 Token" in german.text


# --- the command line ---------------------------------------------------------------------------------------------

@pytest.fixture
def cli_history(tmp_path, llm, monkeypatch):
    """A saved conversation with one compaction, and `llm-cli` booted on a stand-in framework."""
    from orchestrator import cli
    ditto = make_ditto(tmp_path, ai_context_window=WINDOW)
    history = long_history()
    history.path = tmp_path / "state" / ChatHistory.FILE_NAME
    llm.script += [answer("A note of decisions."), answer("Next step.")]
    reply = ditto.chat("What next?", history)
    history.apply(reply)
    assert history.save()
    ditto.config.user_state_dir = tmp_path / "state"
    framework = SimpleNamespace(ditto_manager=ditto, config=ditto.config, resolve_path=lambda p: p)

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    return SimpleNamespace(path=history.path, reply=reply, earlier=long_history().messages, ditto=ditto)


def invoke(*args, stdin=None):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args), input=stdin)


def test_ditto_context_shows_the_window_the_counting_and_the_compactions(cli_history):
    result = invoke("ditto", "context")
    assert result.exit_code == 0, result.output
    assert f"Context window: {WINDOW} tokens, from your setting ai_context_window" in result.output
    assert "Counting: estimate (qwen, 3.31 characters per token" in result.output
    assert "c1 at " in result.output and "message(s) summarized" in result.output
    assert "ditto context undo" in result.output and "ditto context original" in result.output
    data = json.loads(invoke("ditto", "context", "--json").stdout)
    assert data["window"]["tokens"] == WINDOW and data["compactions"][0]["id"] == "c1"


def test_ditto_context_original_prints_the_kept_conversation(cli_history):
    result = invoke("ditto", "context", "original")
    assert result.exit_code == 0 and "A note of decisions." in result.output and "question 0 xxx" in result.output
    assert invoke("ditto", "context", "original", "c9").exit_code == 1


def test_ditto_context_undo_asks_and_restores(cli_history):
    declined = invoke("ditto", "context", "undo", stdin="n\n")
    assert "Nothing was changed." in declined.output
    assert len(ChatHistory.load(cli_history.path).compactions) == 1
    done = invoke("ditto", "context", "undo", "--yes")
    assert done.exit_code == 0 and "Undone" in done.output
    restored = ChatHistory.load(cli_history.path)
    assert restored.compactions == [] and restored.messages[:8] == cli_history.earlier
    assert "No compaction is kept" in invoke("ditto", "context", "undo", "--yes").output


# --- the GUI: Settings > Language model > Context window ----------------------------------------------------------

def test_the_context_box_shows_the_overview_and_undoes_with_a_yes(qapp, cli_history, monkeypatch):
    from orchestrator.gui import context_window_box as module
    from orchestrator.utils.localization import ui_text
    asked = []
    monkeypatch.setattr(module, "ask", lambda parent, title, text: (asked.append(text), True)[1])
    monkeypatch.setattr(module, "plain_box", lambda *a, **k: None)
    framework = SimpleNamespace(ditto_manager=cli_history.ditto,
                                config=SimpleNamespace(user_state_dir=cli_history.path.parent))
    box = module.ContextWindowBox(framework, lang="en", threaded=False)
    try:
        text = box.lbl_info.text()
        assert text.startswith(f"Context window: {WINDOW} tokens, from your setting ai_context_window")
        assert "Compactions kept: 1; the newest, c1" in text and box.btn_undo.isEnabled()
        dialog = box.show_original()
        assert "A note of decisions." in dialog.findChild(type(dialog.children()[1]), "txt_context_original") \
            .toPlainText()
        dialog.close()
        assert box.undo() and "c1" in asked[0]
        assert ChatHistory.load(cli_history.path).compactions == []
        assert ui_text("context.no_compactions", "en") in box.lbl_info.text() and not box.btn_undo.isEnabled()
    finally:
        box.deleteLater()


def test_the_chat_panel_reads_a_conversation_the_terminal_changed(qapp, tmp_path):
    """sync_history: an undo in the terminal is read before the next turn, so the panel never overwrites it."""
    from orchestrator.gui.chat_panel.panel import ChatPanel
    panel = SimpleNamespace(history_file=tmp_path / ChatHistory.FILE_NAME, history=ChatHistory(), _history_stamp=None)
    panel.history.path = panel.history_file
    for name in ("_file_stamp", "save_history", "sync_history"):
        setattr(panel, name, getattr(ChatPanel, name).__get__(panel))
    panel.history.messages = [Message.user("mine")]
    assert panel.save_history() and not panel.sync_history()
    other = ChatHistory.load(panel.history_file)
    other.messages.append(Message(role="assistant", content="from the terminal, longer"))
    assert other.save()
    assert panel.sync_history() and panel.history.messages[-1].text() == "from the terminal, longer"


def test_against_the_conservative_default_a_long_question_is_not_refused(tmp_path, llm):
    """R3 refuses only against a window a server, the table or the person named; the default is a guess."""
    ditto = make_ditto(tmp_path)                    # the scripted provider tells no window: the default
    history = long_history(turns=2, size=50)
    llm.script += [answer("A note."), answer("ok")]
    reply = ditto.chat("z" * 120000, history)
    assert not reply.budget.fits and reply.new_conversation and reply.kept_messages == []
    assert len(llm.requests) == 2, "everything earlier summarized, then the turn went out"
