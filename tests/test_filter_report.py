"""What the prompt filter tells the person (orchestrator/llm/filter_report.py; docs/DESIGN.md, "The person is told,
quietly and exactly, what was filtered out"): a list by place and kind with a count, what was put back, and the
stretches of the person's own text that hold a replaced value; never a value in anything the report says.

Every value is made at run time from random characters, so this file holds no secret and no secret-shaped literal.
"""

import json
from types import SimpleNamespace

import pytest
from test_secret_by_place import value

from orchestrator.llm.filter_report import FilterReport, kind_word
from orchestrator.llm.privacy import PrivacyFilter, restore

KEY = b"k" * 32


def filtered(text, place="input", level="STRICT", report=None):
    report = report or FilterReport()
    clean, mapping = PrivacyFilter(level, KEY).sanitize(text, place, report)
    return clean, mapping, report


def test_a_report_counts_by_place_and_kind_and_names_a_secret_by_a_label():
    password = value("pw")
    _, _, report = filtered(f"tool login --password {password} to 10.1.2.3 and 10.1.2.4")
    lines = report.lines()
    assert lines == ["your input: 2 IP addresses; 1 password flag (SECRET_1)"]
    assert report.total == 3 and not report.empty


def test_the_places_are_listed_in_the_order_of_the_request():
    report = FilterReport()
    secret = value("pl")
    filtered("mail a@b.invalid", "history", report=report)
    filtered(f"sshpass -p {secret} ssh dev@h.invalid", "tool_arguments", report=report)
    filtered("see /home/alice/models", "knowledge", report=report)
    filtered("host 10.0.0.5", "system", report=report)
    filtered("host 10.0.0.6", "input", report=report)
    filtered("host 10.0.0.7", "tool_results", report=report)
    heads = [line.split(":")[0] for line in report.lines()]
    assert heads == ["your input", "history", "system prompt", "tool results", "knowledge excerpts", "tool arguments"]


def test_nothing_in_what_the_report_says_holds_a_value():
    password, other = value("a"), value("b")
    text = f"login dev@host.invalid --password {password} token: {other} from /home/alice/x at 192.168.9.9"
    clean, mapping, report = filtered(text)
    report.note_restored(clean, mapping)
    said = json.dumps([report.lines(), report.restored_lines(), report.as_data(), report.summary(), repr(report)])
    for secret in (password, other, "alice", "192.168.9.9", "host.invalid", "<SECRET_"):
        assert secret not in said, secret


def test_a_secret_label_follows_the_first_meeting_and_the_same_secret_keeps_it():
    one, two = value("one"), value("two")
    _, _, report = filtered(f"--password {one} and password: {two} and again --password {one}")
    # the first secret met twice is two of its kind with one label; the second secret has the next label
    assert report.lines() == ["your input: 2 password flags (SECRET_1); 1 password key, unquoted (SECRET_2)"]


def test_what_was_put_back_in_the_answer_is_reported():
    password = value("back")
    clean, mapping, report = filtered(f"tool login --password {password} on 10.1.2.3")
    answer = f"Use {next(t for t in mapping if t.startswith('<SECRET_'))} on {next(t for t in mapping if t.startswith('<IP_'))}"
    report.note_restored(answer, mapping)
    assert report.restored_lines() == ["put back in this answer: 1 IP address; 1 password flag (SECRET_1)"]
    assert restore(answer, mapping) == f"Use {password} on 10.1.2.3"


def test_the_stretches_are_found_in_the_persons_own_text_and_nowhere_else():
    password = value("st")
    text = f"please run tool login --password {password} now, then ping 10.1.2.3"
    clean, mapping, report = filtered(text)
    marks = report.stretches(text)
    assert [text[a:b] for a, b, _ in marks] == [password, "10.1.2.3"] and [k for _, _, k in marks] == [
        "password flag", "IP"]
    assert report.stretches("a text without any of them") == []
    assert report.stretches(clean) == [], "the placeholders are no stretches of the person's text"


def test_a_credential_that_is_hidden_for_good_is_reported_as_such():
    token = value("hdr", 30)
    _, _, report = filtered(f"Authorization: {token}", level="OFF")
    assert report.lines() == ["your input: 1 credential hidden for good"]
    assert report.stretches(f"Authorization: {token}") == []


def test_a_request_without_anything_to_replace_has_an_empty_report():
    _, _, report = filtered("how do I build llama.cpp for aarch64?")
    assert report.empty and report.lines() == [] and report.summary() == "" and report.restored_lines() == []


def test_the_words_for_a_kind():
    assert kind_word("IP", 1) == "IP address" and kind_word("IP", 2) == "IP addresses"
    assert kind_word("password flag", 2) == "password flags" and kind_word("sshpass", 1) == "sshpass"


def test_an_unknown_place_is_refused():
    with pytest.raises(ValueError):
        FilterReport().add("nowhere", "IP")


def test_the_summary_is_one_line_with_both_parts():
    password = value("sum")
    clean, mapping, report = filtered(f"tool login --password {password}")
    report.note_restored(next(t for t in mapping), mapping)
    line = report.summary()
    assert "\n" not in line and line.startswith("Filtered before this left your computer: your input: 1 password flag")
    assert "Put back in this answer: 1 password flag (SECRET_1)." in line


# --- through the client ---------------------------------------------------------------------------------------

def test_the_cli_prints_the_dim_line_on_standard_error_and_the_answer_on_standard_output(monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    password = value("cli")
    clean, mapping, report = filtered(f"tool login --password {password}")
    token = next(t for t in mapping)
    report.note_restored(f"use {token}", mapping)
    ditto = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text=f"use {password}", cache_lines=[], cache_cap=False,
                                                                  filter=report))
    monkeypatch.setattr(cli_module.FrameworkContext, "require_framework", lambda self: None)
    monkeypatch.setattr(cli_module, "_ai_override", lambda ctx, provider, model: ditto)
    result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "ask", "q"])
    assert result.exit_code == 0, result.output
    assert f"use {password}" in result.stdout and "Filtered before this left your computer" in result.stderr
    assert password not in result.stderr
