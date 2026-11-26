"""A proposal saved with `llm-cli module propose-values --json` is taken by `module create --formats FILE`, the wizard
and the tools (maintainer, 2026-11-24, recon: the bridge format_proposal.proposal_from_text was built but the one
reader, module_generator.parse_formats, refused the file's fields id, complete and proposed).
"""

import json

from orchestrator.Core.format_proposal import proposal_from_text
from orchestrator.Core.module_generator import _validate_formats, parse_formats, proposal_formats
from orchestrator.schemas.target_template import gguf_format


def saved_proposal(values):
    """The JSON `module propose-values --json` prints (FormatProposal.as_json), with one format."""
    return json.dumps({"summary": "s", "complete": True, "cannot": "", "sources": [], "dropped": [],
                       "formats": [{"id": values["id"], "values": values, "complete": True,
                                    "proposed": [{"path": "id", "value": values["id"], "reason": "r",
                                                  "source": "template", "confidence": "high"}],
                                    "gaps": [], "problems": [], "changed": []}]})


def test_the_saved_json_is_read_as_its_formats_values():
    values = gguf_format(["Q4_K_M"])
    assert parse_formats(saved_proposal(values)) == [values]
    problems = []
    assert _validate_formats(saved_proposal(values), problems) and problems == [], problems


def test_the_one_unwrapping_is_shared_with_the_bridge():
    values = gguf_format(["Q4_K_M"])
    assert proposal_from_text(saved_proposal(values)) == parse_formats(saved_proposal(values))
    assert proposal_formats(["gguf", {"id": "x"}]) == ["gguf", {"id": "x"}], "values and presets stay as they are"


def test_the_formats_as_before_are_read_as_before():
    values = gguf_format(["Q4_K_M"])
    assert parse_formats([values]) == [values]
    assert parse_formats("gguf") == ["gguf"]
    assert parse_formats({"formats": [values]}) == [values]
    assert parse_formats("") == [] and parse_formats(None) == []
