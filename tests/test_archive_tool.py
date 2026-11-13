"""Ditto's archive is read through the argument `scope` of search_knowledge (DESIGN round 94): no tool of its own, so the
catalogue keeps its 40 listed tools. Real embedded Qdrant with the fake embedders; documentation addresses only."""

import pytest

from orchestrator.Core.ditto_archive import Candidate, DittoArchive
from orchestrator.rag.errors import EmbeddingModelNotCached
from orchestrator.tools.registry import ToolError
from test_archive_recorder import TABLE  # noqa: F401 - the vendor table of the archive tests
from test_ditto_archive import PAGES, Clock
from test_knowledge_tools import call, fill, make_rag, tools_for  # noqa: F401 - the fixture and the helpers

NPU = "The NPU of the RK3588 speeds up model inference."


@pytest.fixture
def world(make_rag, tmp_path):
    rag = make_rag()
    fill(rag)
    archive = DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())
    return rag, archive, tools_for(tmp_path, rag)


def learn(archive, key="target:rk3588/npu", text=NPU, ground="tested", evidence=("build:a_0123456789abcdef",)):
    return archive.record(Candidate(key, text, ground, list(evidence))).entry


class TestScope:

    def test_the_default_scope_is_the_knowledge_as_before(self, world):
        rag, archive, registry = world
        learn(archive)
        out = call(registry, "search_knowledge", {"query": "npu model"})
        assert out.results and out.archive == []

    def test_scope_archive_returns_the_entries_with_ground_and_evidence(self, world):
        rag, archive, registry = world
        entry = learn(archive)
        out = call(registry, "search_knowledge", {"query": "npu inference", "scope": "archive"})
        assert out.results == []
        (hit,) = out.archive
        assert (hit.entry_id, hit.key, hit.ground, hit.status) == (entry.id, entry.key, "tested", "unconfirmed")
        assert hit.text == NPU and hit.evidence == ["build:a_0123456789abcdef"] and hit.independent_sources == 0
        assert any("not confirmed is a claim" in n for n in out.notes)

    def test_scope_both_returns_excerpts_and_entries(self, world):
        rag, archive, registry = world
        learn(archive)
        out = call(registry, "search_knowledge", {"query": "npu model", "scope": "both", "limit": 3})
        assert out.results and len(out.archive) == 1
        assert "archive entr" in registry.get("search_knowledge").summary(out)

    def test_confirmed_entries_come_first(self, world):
        rag, archive, registry = world
        first = learn(archive, key="target:rk3588/a", text="The NPU runs inference fast on the model.")
        second = learn(archive, key="target:rk3588/b", text="The NPU accelerates model inference too.")
        archive.confirm(second.id)
        out = call(registry, "search_knowledge", {"query": "npu model inference", "scope": "archive"})
        assert [h.entry_id for h in out.archive] == [second.id, first.id]
        assert [h.status for h in out.archive] == ["confirmed", "unconfirmed"]
        assert out.archive[0].confirmed_by == "person"

    def test_a_copy_is_never_returned_and_limit_counts(self, world):
        rag, archive, registry = world
        old = learn(archive, text="The NPU has 6 TOPS for model inference.", ground="sources",
                    evidence=PAGES[:2])
        archive.confirm(old.id)
        learn(archive, text="The NPU has 8 TOPS for model inference.", ground="sources", evidence=PAGES[2:4])
        learn(archive, key="target:rk3588/other", text="The NPU model runs fast.")
        out = call(registry, "search_knowledge", {"query": "npu model inference", "scope": "archive", "limit": 2})
        assert len(out.archive) == 2
        assert all("6 TOPS" not in h.text for h in out.archive)

    def test_an_empty_archive_says_so_through_the_notes_of_the_search(self, world):
        rag, archive, registry = world
        out = call(registry, "search_knowledge", {"query": "npu inference", "scope": "archive"})
        assert out.results == [] and out.archive == []
        assert any("no excerpt matched" in n for n in out.notes)

    def test_a_tool_call_never_downloads_the_model_for_the_archive_either(self, world, monkeypatch):
        rag, archive, registry = world

        def refuse(*args, **kwargs):
            raise EmbeddingModelNotCached("the model is not on this computer")
        monkeypatch.setattr(rag, "models", refuse)
        with pytest.raises(ToolError) as caught:
            call(registry, "search_knowledge", {"query": "npu inference", "scope": "archive"})
        assert caught.value.code == "needs_person"

    def test_the_scope_is_a_closed_choice(self, world):
        rag, archive, registry = world
        with pytest.raises(ToolError):
            call(registry, "search_knowledge", {"query": "npu inference", "scope": "everything"})

    def test_the_catalogue_keeps_its_forty_listed_tools(self, world):
        rag, archive, registry = world
        assert "search_archive" not in {spec.name for spec in registry.list()}
        spec = registry.get("search_knowledge")
        assert "scope" in spec.input_model.model_fields
