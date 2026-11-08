"""The optional MCP tool propose_bundle_block (orchestrator/tools/tools_bundle.py): read only and hidden until the person
enables it; it returns the model's facts, the draft block and the reason of every field, never a path of this computer
or a token; a note an AI gives is kept as AI-generated text for the person's review dialog, never applied; writing or
signing a block is no tool (report_cannot_do confirms it with where the person does it). The Hub is a stand-in: no
network. Every name is a test value."""

import json
from types import SimpleNamespace

import pytest

from test_runtime_tools import allow, call

from orchestrator.runtime.bundle import propose as prop
from orchestrator.tools.policy import ToolTier
from orchestrator.tools.registry import ToolContext, ToolError

TOKEN = "hf_" + "Tq7" * 12   # built at runtime: never a credential-shaped literal in the repository


class Api:
    def __init__(self, calls, token):
        self.calls, self.token = calls, token

    def model_info(self, repo, **kwargs):
        self.calls.append((repo, kwargs.get("token")))
        return SimpleNamespace(sha="d" * 40, gated=False, card_data={"license": "mit"}, tags=[], safetensors=None,
                               pipeline_tag="image-classification", library_name="timm",
                               siblings=[SimpleNamespace(rfilename="model.safetensors", size=40_000_000, lfs=None),
                                         SimpleNamespace(rfilename="config.json", size=10, lfs=None)])


@pytest.fixture
def fw(make_framework, monkeypatch):
    framework = make_framework()
    calls = []
    framework.hf_api_factory = lambda token: Api(calls, token)
    framework.hub_calls = calls
    return framework


def test_hidden_until_enabled_and_read_only(fw):
    from orchestrator.tools.facts_catalog import TOOL_FACTS
    rows = {r["tool"]: r for r in fw.tool_registry.permissions("mcp")}
    assert rows["propose_bundle_block"]["decision"] == "deny" and not rows["propose_bundle_block"]["listed"]
    spec = fw.tool_registry.get("propose_bundle_block")
    assert spec.tier == ToolTier.READ and "propose_bundle_block" in TOOL_FACTS
    assert not ({"path", "folder", "token", "write"} & set(spec.input_model.model_fields))


def test_the_draft_with_facts_and_reasons_no_path_no_token(fw, tmp_path):
    allow(fw, "propose_bundle_block")
    out = call(fw, "propose_bundle_block", {"model": "example/classifier-tiny"})
    assert out.block["kind"] == "classify" and out.rule == "image-classification"
    assert out.facts["commit"] == "d" * 40 and out.facts["licence"] == "mit"
    assert any(r["field"] == "kind" and r["origin"] == "task" for r in out.reasons)
    assert [h["what"].split(" (")[0] for h in out.model_card][:2] == ["input size", "classes"]
    assert out.note_kept is False and any("no tool writes or signs a block" in n for n in out.notes)
    text = json.dumps(out.model_dump())
    assert str(tmp_path) not in text and TOKEN not in text
    assert fw.hub_calls == [("example/classifier-tiny", False)]
    assert not (tmp_path / "community").exists()


def test_a_task_alone_and_the_argument_rules(fw):
    allow(fw, "propose_bundle_block")
    out = call(fw, "propose_bundle_block", {"task": "translation"})
    assert out.rule == "unknown" and out.block["reach"]["routes"][0]["protocol"] == "http-json"
    for bad in ({}, {"task": "translation", "model": "a/b"}, {"task": "translation", "revision": "e" * 40},
                {"task": "translation", "note": "x"}):
        with pytest.raises(ToolError):
            call(fw, "propose_bundle_block", bad)


def test_an_ai_note_is_kept_for_the_person_and_never_applied(fw):
    from pathlib import Path
    allow(fw, "propose_bundle_block")
    out = call(fw, "propose_bundle_block", {"model": "example/classifier-tiny",
                                            "note": "Use 224 px input; 1000 ImageNet classes"})
    assert out.note_kept and out.block["status"] == "template"
    kept = prop.ProposalNotes(Path(fw.config.user_state_dir)).for_model("example/classifier-tiny")
    assert kept[0].note == "Use 224 px input; 1000 ImageNet classes" and kept[0].proposed_by == "ditto"
    again = call(fw, "propose_bundle_block", {"model": "example/classifier-tiny"})
    assert again.block == out.block   # the note changed nothing of the draft


def test_offline_mode_refuses_before_any_lookup(fw):
    allow(fw, "propose_bundle_block")
    fw.config.set("offline_mode", True)
    with pytest.raises(Exception) as e:
        call(fw, "propose_bundle_block", {"model": "example/classifier-tiny"})
    assert "offline" in str(e.value).lower() and fw.hub_calls == []


def test_writing_a_block_is_no_tool_and_report_cannot_do_says_where_the_person_does_it(fw):
    from orchestrator.tools.framework_tools import NO_TOOL_ACTIONS
    where = dict(NO_TOOL_ACTIONS)["write_bundle_block"]
    assert "Propose from model..." in where and "--write" in where
    allow(fw, "report_cannot_do")
    out = fw.tool_registry.call("report_cannot_do", {"task": "save the proposed bundle block", "reason": "no_tool",
                                                     "missing": "a tool that writes a block",
                                                     "action": "write_bundle_block"}, ToolContext("mcp"))
    assert "No tool does this" in json.dumps(out.model_dump())
