"""The one log cleaner (orchestrator/utils/logtext.py, consolidation chain ctx-slim; decision: smaller tool results and
cleaner logs to the model).

The vectors live in tests/fixtures/context_rules/clean_log.json, so the board gateway can check its own cleaner
against the very same cases.
"""

import json
import random
from pathlib import Path

import pytest

from orchestrator.utils import hadolint, logtext
from orchestrator.utils.logtext import ANSI_RE, clean_lines, clean_log

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "context_rules" / "clean_log.json").read_text("utf-8"))


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_vectors(case):
    assert clean_log(case["input"]) == case["output"]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_idempotent_on_every_vector(case):
    once = clean_log(case["input"])
    assert clean_log(once) == once


def test_idempotent_on_random_logs():
    pieces = ["step 1", "step 2", "step 3", "same", "same", "", "error: boom", "warning: w 1", "\x1b[32mok\x1b[0m",
              "50%\r100%", "Traceback (most recent call last):", "x 1.2.3", "x 1.2.4", "fatal: 9", "\x1b]0;t\x07t"]
    rng = random.Random(7)
    for _ in range(300):
        text = "\n".join(rng.choice(pieces) for _ in range(rng.randint(0, 30)))
        once = clean_log(text)
        assert clean_log(once) == once, repr(text)


def test_no_escape_survives():
    text = "\x1b[31mred\x1b]0;title\x07 \x1b]8;;https://example.com\x1b\\x\x1b]8;;\x1b\\ \x1b(B\x1bM\x1b7 \x9b1m\x1b"
    assert "\x1b" not in clean_log(text) and "\x9b" not in clean_log(text)


def test_an_open_osc_never_swallows_later_lines():
    out = clean_log("\x1b]0;title without end\nerror: the real cause\nnext")
    assert "error: the real cause" in out and "next" in out


@pytest.mark.parametrize("word", ["error", "ERROR", "Warning", "fatal", "FAILED", "Traceback"])
def test_a_line_with_a_keyword_is_never_collapsed(word):
    lines = [f"{word}: step {i}" for i in range(5)] + [f"{word}: same"] * 3
    assert clean_log("\n".join(lines)).splitlines() == lines


def test_a_redrawn_error_is_kept():
    assert clean_log("compiling 10%\rerror: out of memory\rcompiling 11%").splitlines() == [
        "error: out of memory", "compiling 11%"]


def test_clean_lines_keeps_the_source_index_of_each_line():
    lines = ["a 1", "a 2", "a 3", "b", "b", "error: c"]
    assert clean_lines(lines) == [(0, "a 1"), (0, logtext.SIMILAR.format(count=3)), (2, "a 3"),
                                  (3, "b (repeated 2 times)"), (5, "error: c")]


def test_hadolint_uses_the_one_escape_pattern():
    assert hadolint._ANSI is ANSI_RE
    # Its own cleaning is unchanged for the CSI colours hadolint writes.
    assert hadolint._clean("\x1b[33mDockerfile:3 DL3008 warning: Pin versions\x1b[0m") == \
        "Dockerfile:3 DL3008 warning: Pin versions"
