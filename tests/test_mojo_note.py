"""The note of an open Mojo Oracle run: its lifetime and who closes it (the Mojo circle follow-ups).

The note expires after one hour (older, unreadable or in the future = not open, and the note is removed); every
Miracle run of the person closes it, the ones without a logo too (`llmcf mojo --miracle --json`, `llm-cli doctor
--fix` with and without --json, a Mojo Miracle run in the GUI through Mojo.miracle(closes_circle=True)); its functions
never raise for a bad state folder.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Finding, Mojo, Severity, banner, oracle_note, runner
from test_mojo_cli import run_cli


def nothing_to_check(monkeypatch):
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [lambda _fw: [Finding("python", "python.version", Severity.OK,
                                                                        "Python 3.13")]])


def note_at(framework, when) -> Path:
    path = oracle_note.record_path(framework)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mojo_oracle": "open", "via": "cli", "at": when}) + "\n", encoding="utf-8")
    return path


# --- 1. the note expires after one hour --------------------------------------------------------------------------------

def test_a_fresh_note_is_open(booted_framework):
    now = datetime.now(timezone.utc)
    note_at(booted_framework, (now - timedelta(minutes=59)).isoformat(timespec="seconds"))
    assert oracle_note.oracle_open(booted_framework, now=now)
    assert oracle_note.OPEN_FOR == timedelta(hours=1) and banner.oracle_open is oracle_note.oracle_open


@pytest.mark.parametrize("at", [
    lambda now: (now - timedelta(hours=1, seconds=1)).isoformat(timespec="seconds"),
    lambda now: (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
    lambda now: "yesterday",
    lambda now: (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat(timespec="seconds"),
    lambda now: 12345,
], ids=["older-than-an-hour", "in-the-future", "unreadable", "no-time-zone", "not-a-string"])
def test_an_expired_or_unreadable_note_is_not_open_and_is_removed(booted_framework, at):
    now = datetime.now(timezone.utc)
    path = note_at(booted_framework, at(now))
    assert not oracle_note.oracle_open(booted_framework, now=now)
    assert not path.exists(), "the note is removed"


def test_an_old_note_gives_no_logo(booted_framework, monkeypatch):
    nothing_to_check(monkeypatch)
    note_at(booted_framework, (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds"))
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert banner.ART not in out and banner.NOT_FOUND_LINE not in out


# --- 2. the CLI's runs without a logo close the note -------------------------------------------------------------------

@pytest.mark.parametrize("args", [("mojo", "--miracle", "--json"), ("doctor", "--fix"), ("doctor", "--fix", "--json")],
                         ids=["mojo-miracle-json", "doctor-fix", "doctor-fix-json"])
@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "no-terminal"])
def test_the_persons_runs_without_a_logo_close_the_note(booted_framework, monkeypatch, args, terminal):
    nothing_to_check(monkeypatch)
    oracle_note.record_oracle(booted_framework)
    assert oracle_note.oracle_open(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, *args, terminal=terminal)
    assert not oracle_note.oracle_open(booted_framework)
    assert banner.ART not in out and banner.MANAGED_LINE not in out and banner.NOT_FOUND_LINE not in out


# --- 3. the GUI's Miracle run closes it through Core -------------------------------------------------------------------

def test_the_guis_miracle_run_closes_the_note_through_core(booted_framework, monkeypatch):
    nothing_to_check(monkeypatch)
    oracle_note.record_oracle(booted_framework)
    Mojo(booted_framework).miracle(actor="gui", at_terminal=True, closes_circle=True, decision="person",
                                   answered_by="person", confirm_channel="framework-gui")
    assert not oracle_note.oracle_open(booted_framework)


def test_a_failing_miracle_run_still_closes_it(booted_framework, monkeypatch):
    oracle_note.record_oracle(booted_framework)

    def broken(self, **kwargs):
        raise RuntimeError("the image list broke")
    monkeypatch.setattr(runner.Mojo, "_miracle", broken)
    with pytest.raises(RuntimeError):
        Mojo(booted_framework).miracle(actor="gui", at_terminal=True, closes_circle=True)
    assert not oracle_note.oracle_open(booted_framework)


def test_without_closes_circle_the_note_stays(booted_framework, monkeypatch):
    """An AI's run (run_mojo_miracle) is not the person's: the note stays (tests/test_mojo_circle.py checks the tool)."""
    nothing_to_check(monkeypatch)
    oracle_note.record_oracle(booted_framework)
    Mojo(booted_framework).miracle(actor="mcp", at_terminal=False)
    assert oracle_note.oracle_open(booted_framework)


def test_the_mojo_window_asks_core_to_close_it():
    """The GUI passes closes_circle=True and never writes or names the note itself."""
    text = (Path(__file__).resolve().parent.parent / "orchestrator" / "gui" / "mojo_window.py").read_text(
        encoding="utf-8")
    assert "closes_circle=True" in text
    assert "oracle_note" not in text and "oracle_open.json" not in text and "mojo.banner" not in text


# --- 4. never raises for a bad state folder ----------------------------------------------------------------------------

def test_a_folder_in_place_of_the_note(booted_framework):
    path = oracle_note.record_path(booted_framework)
    path.mkdir(parents=True, exist_ok=True)
    oracle_note.record_oracle(booted_framework)
    assert oracle_note.oracle_open(booted_framework) is False
    oracle_note.close_oracle(booted_framework)


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"open"', "null", '{"mojo_oracle": "closed"}'],
                         ids=["bad-json", "a-list", "a-string", "null", "closed"])
def test_bad_contents(booted_framework, text):
    path = oracle_note.record_path(booted_framework)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert oracle_note.oracle_open(booted_framework) is False and not path.exists()


class Raising:
    """A state folder double whose every use raises TypeError (not OSError or ValueError)."""

    def __getattr__(self, name):
        raise TypeError("a broken double")


@pytest.mark.parametrize("fn", ["record_oracle", "oracle_open", "close_oracle"])
def test_a_type_error_raising_double(monkeypatch, fn):
    monkeypatch.setattr(oracle_note, "record_path", lambda framework: Raising())
    result = getattr(oracle_note, fn)(SimpleNamespace())
    assert result in (None, False)


@pytest.mark.parametrize("fn", ["record_oracle", "oracle_open", "close_oracle"])
def test_record_path_itself_raising(monkeypatch, fn):
    def boom(framework):
        raise TypeError("no config")
    monkeypatch.setattr(oracle_note, "record_path", boom)
    assert getattr(oracle_note, fn)(SimpleNamespace()) in (None, False)


@pytest.mark.skipif(os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX modes; root writes a read-only folder anyway")
def test_a_read_only_state_folder(booted_framework):
    oracle_note.record_oracle(booted_framework)
    folder = oracle_note.record_path(booted_framework).parent
    os.chmod(folder, 0o500)
    try:
        oracle_note.record_oracle(booted_framework)
        oracle_note.close_oracle(booted_framework)
        oracle_note.oracle_open(booted_framework)
    finally:
        os.chmod(folder, 0o700)
