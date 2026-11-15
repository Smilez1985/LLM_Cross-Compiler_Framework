"""`llm-cli knowledge add-private`, `remove-private` and `knowledge sources --kind private`, and what Ditto's knowledge tools
show of a private page. Real embedded Qdrant with the fake embedders of test_rag; documentation addresses only."""

from types import SimpleNamespace

import pytest

from test_private_pages import ADDRESS, PAGE
from test_rag import make_rag  # noqa: F401 - the fixture


def invoke(*args, stdin=None):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args), input=stdin)


@pytest.fixture
def rag(make_rag, monkeypatch):
    from orchestrator import cli
    rag = make_rag()
    framework = SimpleNamespace(ensure_rag_manager=lambda: rag, config=rag.config, resolve_path=lambda p: p)

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    return rag


@pytest.fixture
def saved(tmp_path):
    file = tmp_path / "guide.html"
    file.write_text(PAGE, encoding="utf-8")
    return file


def labels(rag):
    return {e["source"]: e["kind"] for e in rag.list_sources()}


class TestAdd:

    def test_a_saved_file_is_added_with_its_address_and_marked_private(self, rag, saved):
        result = invoke("knowledge", "add-private", str(saved), "--source", ADDRESS)
        assert result.exit_code == 0, result.output
        assert "Added as a private page: private:" + ADDRESS in result.output and "never shared" in result.output
        assert labels(rag) == {"private:" + ADDRESS: "private"}

    def test_without_an_address_the_file_name_is_the_label_never_its_folder(self, rag, saved):
        assert invoke("knowledge", "add-private", str(saved)).exit_code == 0
        assert list(labels(rag)) == ["private:guide.html"]

    @pytest.mark.parametrize("name, data, reason", [("notes.txt", b"text", "Only a PDF or an HTML page"),
                                                    ("empty.html", b"  ", "holds no text")])
    def test_a_file_that_cannot_be_added_says_why_and_stores_nothing(self, rag, tmp_path, name, data, reason):
        file = tmp_path / name
        file.write_bytes(data)
        result = invoke("knowledge", "add-private", str(file))
        assert result.exit_code == 1 and reason in result.output and rag.list_sources() == []

    def test_a_missing_file_and_a_bad_address_are_refused(self, rag, saved, tmp_path):
        assert invoke("knowledge", "add-private", str(tmp_path / "none.html")).exit_code != 0
        bad = invoke("knowledge", "add-private", str(saved), "--source", "http://user:pw@docs.example.com/x")
        assert bad.exit_code == 1 and "public https address" in bad.output and rag.list_sources() == []


class TestSourcesAndRemove:

    def test_sources_lists_it_as_private_and_not_exported(self, rag, saved):
        invoke("knowledge", "add-private", str(saved), "--source", ADDRESS)
        rag.ingest_document("https://docs.example.com/public", "# Public\n\nA public page about the NPU.",
                            {"content_type": "html"}, outside=True)
        only = invoke("knowledge", "sources", "--kind", "private")
        assert only.exit_code == 0 and "private:" in only.output and "public" not in only.output.replace("Private", "")
        assert "not exported" in only.output

    def test_remove_asks_first_and_no_is_the_default(self, rag, saved):
        invoke("knowledge", "add-private", str(saved), "--source", ADDRESS)
        refused = invoke("knowledge", "remove-private", ADDRESS, stdin="\n")
        assert refused.exit_code == 0 and "Nothing was changed" in refused.output and labels(rag)
        done = invoke("knowledge", "remove-private", "private:" + ADDRESS, "--yes")
        assert done.exit_code == 0 and "file you saved was not touched" in done.output and labels(rag) == {}
        assert saved.exists()

    def test_only_a_private_page_can_be_removed_this_way(self, rag):
        rag.ingest_document("https://docs.example.com/public", "# Public\n\nA public page about the NPU.",
                            {"content_type": "html"}, outside=True)
        result = invoke("knowledge", "remove-private", "https://docs.example.com/public", "--yes")
        assert result.exit_code == 1 and "no private page" in result.output and labels(rag)


class TestTools:

    def test_search_knowledge_shows_the_private_kind_and_trust(self, rag, saved):
        from orchestrator.Core.private_pages import add_private_page
        from orchestrator.tools import tools_knowledge as tk
        add_private_page(rag, saved, ADDRESS)
        assert "private" in tk.KINDS and tk.trust_of("private", "", {}) == "private"
        sources = [e for e in rag.list_sources() if e["kind"] == "private"]
        assert len(sources) == 1 and sources[0]["pack_share"] == "not_exported"
