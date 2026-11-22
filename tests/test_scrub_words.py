"""The privacy scrub puts shell-safe words in the files of a target, recipe or block pack (orchestrator/Core/
community_pack.py `scrub(text, words=True)`, community_manager `_scrub_tree`): the marks `<ip>`, `<user>@<host>` are
redirections in a shell script, so a scrubbed script did not run; the words `ip-removed`, `user-removed@host-removed`
do, and are fixed points of the scrub. Prose (a knowledge page) keeps the readable marks.

Every address is built at run time, so this file holds no address of a person.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.Core.community_manager import CommunityManager
from orchestrator.Core.community_pack import PLACEHOLDER_MARKS, PLACEHOLDER_WORDS, scrub

MAIL = "ops" + "@" + "example.org"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop"
SCRIPT = (f'#!/bin/bash\nset -e\nHOST_IP=192.168.1.20\nGW=10.0.0.7\nMAC=aa:bb:cc:dd:ee:ff\nMAIL={MAIL}\n'
          f'# id token {JWT}\nscp model.gguf jenny@devbox:~/models/\necho "built for $HOST_IP via $GW ($MAC, $MAIL)"\n')


def test_the_words_replace_every_mark_of_the_scrub():
    out = scrub(SCRIPT, words=True)
    for leaked in ("192.168.1.20", "10.0.0.7", "aa:bb:cc:dd:ee:ff", MAIL, JWT, "jenny", "devbox"):
        assert leaked not in out, leaked
    for word in ("ip-removed", "mac-removed", "email-removed", "jwt-removed", "user-removed@host-removed"):
        assert word in out, word
    assert "<" not in out and ">" not in out, "no redirection characters"


def test_the_readable_marks_stay_for_prose():
    out = scrub(SCRIPT)
    assert "<ip>" in out and "<mac>" in out and "<email>" in out and "<jwt>" in out and "<user>@<host>" in out
    assert set(PLACEHOLDER_MARKS) == set(PLACEHOLDER_WORDS)


def test_the_words_are_a_fixed_point_of_the_scrub():
    once = scrub(SCRIPT, words=True)
    assert scrub(once, words=True) == once, "a pack that carries the words passes its own check"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_scrubbed_script_is_valid_shell_where_the_marks_were_not(tmp_path):
    safe, readable = tmp_path / "safe.sh", tmp_path / "readable.sh"
    safe.write_text(scrub(SCRIPT, words=True), encoding="utf-8")
    readable.write_text(scrub(SCRIPT), encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(safe)], capture_output=True, text=True).returncode == 0
    assert subprocess.run(["bash", "-n", str(readable)], capture_output=True, text=True).returncode != 0, \
        "the old marks were a syntax error in a script"


def test_the_export_of_a_pack_with_files_uses_the_words_and_knowledge_keeps_the_marks(tmp_path):
    (tmp_path / "script.sh").write_text(SCRIPT, encoding="utf-8")
    CommunityManager._scrub_tree(tmp_path, review=True)
    text = (tmp_path / "script.sh").read_text(encoding="utf-8")
    assert "ip-removed" in text and "<ip>" not in text
    assert scrub(text, words=True) == text

    prose = tmp_path / "pages"
    prose.mkdir()
    (prose / "page.md").write_text(f"Connect to 192.168.1.20 or {MAIL}.\n", encoding="utf-8")
    CommunityManager._scrub_tree(prose, review=False)
    shown = (prose / "page.md").read_text(encoding="utf-8")
    assert "<ip>" in shown and "<email>" in shown and "ip-removed" not in shown


def test_common_names_and_loopback_stay_in_words_mode(monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "root")
    monkeypatch.setattr("socket.gethostname", lambda: "localhost")
    text = "curl http://127.0.0.1:8080 # the root filesystem, on localhost\n"
    assert scrub(text, words=True) == scrub(text)
