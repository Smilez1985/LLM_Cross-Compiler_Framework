"""
Ditto as the pilot in the module wizard, step 3a (DESIGN, Targets, hardware and templates: Ditto's switch, and Ditto as
the pilot in the module wizard; the maintainer's decision of 2026-11-28): the archive's entries are sources of the
format values of their own (A1, A2 ...); an entry is never a source of the hardware itself, and a value must be what
the entry says; a value that rests on an unconfirmed entry is held back as a pending value until the person decides
it (use it, confirm the entry and use it, or leave the field empty), so no path uses it without the person.
"""

import json
from types import SimpleNamespace

import pytest

from orchestrator.schemas.archive import ArchiveEntry

pytest.importorskip("anthropic")
pytest.importorskip("openai")

from orchestrator.Core.format_proposal import (  # noqa: E402
    FormatValuesAnswer, PendingValue, build_proposal, claim_holds, propose)
from test_ditto import answer, llm, make_ditto  # noqa: E402,F401
from test_template_values import DOC, GOOD_VALUES, PROBE, FakeRag, value  # noqa: E402

VENDOR = "https://vendor.example.com/docs/quantization"


def entry(key="hailo:hailo8/quantizations", text="The Hailo-8 runs int8 and int16 models.", status="unconfirmed",
          value_type=None, value_=None, sources=1):
    return ArchiveEntry(id="1" * 8 + "-1111-4111-8111-" + "1" * 12, key=key, text=text, ground="sources",
                        evidence=[VENDOR], status=status, independent_sources=sources, value_type=value_type,
                        value=value_)


def answer_of(values):
    return FormatValuesAnswer.model_validate({"formats": [{"id": "hef", "values": values}]})


def without(path):
    return [v for v in GOOD_VALUES if v["path"] != path]


def test_a_confirmed_entry_is_a_source_like_an_excerpt():
    values = without("quantizations") + [value("quantizations", ["int8", "int16"], "A1")]
    proposal = build_proposal(answer_of(values), [DOC], PROBE, claims=[entry(status="confirmed")], needed=5)
    (draft,) = proposal.drafts
    assert draft.values["quantizations"] == ["int8", "int16"] and not draft.pending
    assert draft.notes["quantizations"].source == "archive hailo:hailo8/quantizations"


def test_an_unconfirmed_entry_waits_for_the_person_and_is_not_in_the_draft():
    values = without("quantizations") + [value("quantizations", ["int8", "int16"], "A1")]
    proposal = build_proposal(answer_of(values), [DOC], PROBE, claims=[entry()], needed=5)
    (draft,) = proposal.drafts
    assert "quantizations" not in draft.values and "quantizations" not in dict(draft.gaps)
    (pending,) = proposal.pending()
    assert pending.path == "quantizations" and pending.needed == 5 and not proposal.complete
    lines = "\n".join(proposal.lines())
    assert "unconfirmed: 1 of 5 independent domains" in lines and "waits for your decision" in lines
    data = proposal.as_json()["formats"][0]["pending"][0]
    assert data["entry"]["evidence"] == [VENDOR] and data["entry"]["needed"] == 5


def test_use_confirm_and_skip():
    claims = [entry()]
    values = without("quantizations") + [value("quantizations", ["int8", "int16"], "A1")]

    used = build_proposal(answer_of(values), [DOC], PROBE, claims=claims, needed=5)
    used.decide(used.pending()[0], "use")
    (draft,) = used.drafts
    assert draft.values["quantizations"] == ["int8", "int16"] and not draft.pending
    assert draft.notes["quantizations"].source.endswith("unconfirmed, used on your decision")
    assert used.complete, "nothing waits and nothing is missing"

    confirmed = []
    archive = SimpleNamespace(confirm=lambda eid: confirmed.append(eid))
    kept = build_proposal(answer_of(values), [DOC], PROBE, claims=claims, needed=5)
    kept.decide(kept.pending()[0], "confirm", archive)
    assert confirmed == [claims[0].id]
    assert kept.drafts[0].notes["quantizations"].source.endswith("confirmed by you")

    skipped = build_proposal(answer_of(values), [DOC], PROBE, claims=claims, needed=5)
    skipped.decide(skipped.pending()[0], "skip")
    gaps = dict(skipped.drafts[0].gaps)
    assert "did not use the unconfirmed archive entry" in gaps["quantizations"] and not skipped.complete


def test_a_confirmation_that_fails_changes_nothing():
    values = without("quantizations") + [value("quantizations", ["int8", "int16"], "A1")]
    proposal = build_proposal(answer_of(values), [DOC], PROBE, claims=[entry()], needed=5)

    def refuse(_eid):
        raise RuntimeError("the knowledge base is switched off")
    with pytest.raises(RuntimeError):
        proposal.decide(proposal.pending()[0], "confirm", SimpleNamespace(confirm=refuse))
    assert len(proposal.pending()) == 1 and "quantizations" not in proposal.drafts[0].values
    with pytest.raises(ValueError):
        proposal.decide(proposal.pending()[0], "maybe")
    with pytest.raises(ValueError):
        proposal.decide(PendingValue("hef", "notes", "x", "", 0.5, entry(), 5), "use")


def test_the_hardware_never_comes_from_the_archive_and_a_value_must_be_the_entrys():
    values = [v for v in without("sdk.runtime_key") if v["path"] != "sdk.version"] + [
        value("sdk.runtime_key", "HAILO_RUNTIME_VERSION", "A1"),
        value("board_paths.lib_dir", "/usr/lib", "A1"),
        value("sdk.version", "9.9.9", "A2"),
        value("notes", "x", "A5"),
    ]
    claims = [entry(status="confirmed", text="HAILO_RUNTIME_VERSION /usr/lib"),
              entry(key="hailo:dfc/version", text="The DFC 3.27.0", status="confirmed", value_type="version",
                    value_="3.27.0")]
    proposal = build_proposal(answer_of(values), [DOC], PROBE, claims=claims, needed=5)
    gaps = dict(proposal.drafts[0].gaps)
    assert "never from Ditto's archive" in gaps["sdk.runtime_key"]
    assert "never from Ditto's archive" in gaps["board_paths.lib_dir"]
    assert "not what the archive entry hailo:dfc/version says" in gaps["sdk.version"]
    assert "A5, which was not given" in gaps["notes"]


def test_a_value_holds_by_the_entrys_typed_value_or_its_sentence():
    typed = entry(value_type="version", value_="3.27.0")
    assert claim_holds(typed, "v3.27.0") and not claim_holds(typed, "3.27.1")
    plain = entry(text="Supported: INT8, INT16.")
    assert claim_holds(plain, ["int8", "int16"]) and not claim_holds(plain, ["int8", "int4"])


def test_propose_gives_the_archive_entries_to_the_model_and_holds_back_an_unconfirmed_one(tmp_path, llm):  # noqa: F811
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    ditto = make_ditto(tmp_path)
    rag = FakeRag([(DOC, "hailo compile model.onnx --hw-arch hailo8 --output model.hef ...")])
    ditto._rag_manager = lambda: rag
    searched = []
    archive = SimpleNamespace(
        search=lambda query, limit=5: searched.append(query) or [SimpleNamespace(entry=entry(), score=1.0)],
        needed=lambda: 7)
    ditto.framework = SimpleNamespace(ditto_archive=archive)
    values = without("quantizations") + [value("quantizations", ["int8", "int16"], "A1")]
    llm.script.append(answer(json.dumps({"formats": [{"id": "hef", "values": values}]})))
    proposal = propose(ditto, probe)
    user = llm.requests[0].messages[0].text()
    assert "Archive entry A1 (key: hailo:hailo8/quantizations, unconfirmed)" in user and "<untrusted_data" in user
    assert "A1, A2 ... for an entry of Ditto's archive" in llm.requests[0].system
    assert searched and "hailo" in searched[0]
    (pending,) = proposal.pending()
    assert pending.needed == 7 and "quantizations" not in proposal.drafts[0].values


def test_without_the_knowledge_base_there_is_no_archive_source(tmp_path, llm):  # noqa: F811
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    ditto = make_ditto(tmp_path)
    ditto._rag_manager = lambda: None
    ditto.framework = SimpleNamespace(ditto_archive=SimpleNamespace(search=lambda *a, **k: pytest.fail("searched")))
    llm.script.append(answer(json.dumps({"formats": [{"id": "hef", "values": GOOD_VALUES}]})))
    proposal = propose(ditto, probe)
    assert "Archive entry" not in llm.requests[0].messages[0].text() and not proposal.pending()
