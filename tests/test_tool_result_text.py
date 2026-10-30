"""Tool results as compact JSON for the model (llm/types.tool_result_text and json_dumps; consolidation chain ctx-slim,
decision: smaller tool results).

Before, a pydantic tool output went to the model as model_dump_json (fields in model order) and a dict as json.dumps
with ", " and ": " separators. Now both are json_dumps: sorted keys, no spaces, readable non-ASCII text, and every
control character escaped (C0 as JSON does; DEL and C1 too, which ensure_ascii=False alone would let through). Only
new turns change: a stored turn keeps its bytes (the append-only rule of llm/types.py). The vectors live in
tests/fixtures/context_rules/compact_json.json for the board gateway.
"""

import json
from pathlib import Path
from typing import List, Optional

import pytest
from pydantic import BaseModel

from orchestrator.llm.providers import base as providers_base
from orchestrator.llm.types import Message, tool_result_text

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "context_rules" / "compact_json.json").read_text("utf-8"))


class Out(BaseModel):
    summary: str
    notes: List[str] = []
    request_id: Optional[str] = None
    size: int = 0


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_vectors(case):
    assert tool_result_text(case["value"]) == case["output"]
    assert json.loads(tool_result_text(case["value"])) == case["value"]


def test_a_pydantic_output_is_compact_and_sorted():
    out = Out(summary="Build läuft", notes=["b", "a"], request_id="r_1", size=3)
    assert tool_result_text(out) == '{"notes":["b","a"],"request_id":"r_1","size":3,"summary":"Build läuft"}'


def test_same_output_as_json_dumps():
    from orchestrator.llm.types import json_dumps
    value = {"z": [1, {"b": "ü", "a": None}], "a": "x\ny"}
    assert tool_result_text(value) == json_dumps(value)
    out = Out(summary="s")
    assert tool_result_text(out) == json_dumps(out.model_dump(mode="json"))


def test_json_dumps_has_one_home_and_providers_import_it():
    from orchestrator.llm import types
    assert providers_base.json_dumps is types.json_dumps


def test_text_results_pass_unchanged():
    assert tool_result_text("plain text {\"a\": 1}") == "plain text {\"a\": 1}"


def test_a_stored_turn_keeps_its_bytes():
    """An old tool result with the spaced JSON of an earlier version is read and sent back exactly as stored."""
    stored = Message.tool_result("call_1", '{"a": 1, "b": [1, 2]}')
    again = Message.model_validate_json(stored.model_dump_json())
    assert again.content == '{"a": 1, "b": [1, 2]}'


def test_a_file_name_that_is_not_utf8_never_breaks_the_request():
    """Review (ctx-slim-v): a POSIX file name with bytes that are not UTF-8 reaches Python as a lone surrogate
    (os.fsdecode). ensure_ascii=False wrote it raw, and the request to the provider then failed to encode
    ("surrogates not allowed"). It is escaped like the other controls; json.loads reads the same value back."""
    value = {"name": "model\udcff.gguf"}
    text = tool_result_text(value)
    text.encode("utf-8")
    assert text == '{"name":"model\\udcff.gguf"}' and json.loads(text) == value
    assert tool_result_text(Out(summary="a\udcffb")).encode("utf-8")
