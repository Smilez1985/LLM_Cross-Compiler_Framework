"""The one-time download of the knowledge base's models (next-fixes 119): the check whether the models are on this
computer reads the model cache only (it never loads or downloads a model), and every AI tool that can start the
download says so in its question with the situation warning embedding_model_download. The crawl, the framework's
documents and a knowledge pack each test their own question (test_crawl_tool, test_framework_docs,
test_community_tools); a prompt never starts the download (test_mcp_prompts). No test reaches the network."""

from types import SimpleNamespace

from orchestrator.Core.rag_manager import RAGManager
from orchestrator.rag.embeddings import BM25_LANGUAGE, SPARSE_MODEL, FastEmbedSparseEmbedder
from orchestrator.tools import facts_catalog as catalog
from orchestrator.tools.tools_knowledge import embedding_download


def cache_file(cache, repo, name, revision="0123abcd"):
    """A model file as huggingface_hub lays it out in a cache folder."""
    folder = cache / ("models--" + repo.replace("/", "--"))
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text(revision)
    snapshot = folder / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / name).write_text("stop words")


class Model:
    """A model with the cache check of the real ones: cached() True, False or None (cannot be told)."""

    def __init__(self, state, size_gb):
        self.state, self.size_gb, self.loaded = state, size_gb, False

    def cached(self):
        return self.state


def rag_with(dense, sparse, tmp_path, **settings):
    settings.setdefault("models_dir", str(tmp_path / "models"))   # never the models folder of this checkout
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                             user_state_dir=str(tmp_path / "state"))
    return RAGManager(SimpleNamespace(config=config), embedder=dense, sparse_embedder=sparse)


class TestTheCacheCheck:
    def test_a_model_file_in_the_cache_counts_as_there_and_a_missing_one_does_not(self, tmp_path):
        model = FastEmbedSparseEmbedder(tmp_path / "models", lambda: True)
        assert model.cached() is False
        cache_file(tmp_path / "models", SPARSE_MODEL, f"{BM25_LANGUAGE}.txt")
        assert model.cached() is True
        assert model._model is None, "the check never loads the model"

    def test_a_model_whose_source_is_not_known_cannot_be_told(self, tmp_path, monkeypatch):
        model = FastEmbedSparseEmbedder(tmp_path / "models", lambda: True)
        monkeypatch.setattr(model, "_source", lambda: None)
        assert model.cached() is None


class TestTheKnowledgeBaseSaysWhatItWouldDownload:
    def test_a_missing_model_is_needed_with_its_size(self, tmp_path):
        assert rag_with(Model(False, 0.47), Model(True, 0.01), tmp_path).embedding_download() == (True, 0.47)
        assert rag_with(Model(False, 0.47), Model(False, 0.01), tmp_path).embedding_download() == (True, 0.48)

    def test_models_on_this_computer_need_nothing(self, tmp_path):
        assert rag_with(Model(True, 0.47), Model(True, 0.01), tmp_path).embedding_download() == (False, 0.0)

    def test_a_model_that_cannot_be_checked_is_not_called_there(self, tmp_path):
        needed, size = rag_with(Model(None, 0.47), Model(True, 0.01), tmp_path).embedding_download()
        assert needed is None and size == 0.48

    def test_models_the_caller_gave_are_there(self, tmp_path):
        assert rag_with(object(), object(), tmp_path).embedding_download() == (False, 0.0)

    def test_the_check_creates_and_loads_no_model(self, tmp_path):
        rag = rag_with(None, None, tmp_path, rag_embedding_model="auto")
        needed, size = rag.embedding_download()
        assert needed is True and size > 0.1, "an empty cache: the recommended models are downloaded first"
        assert rag._embedder is None and rag._sparse is None and rag._store is None
        cache = rag.models_cache_dir
        assert not cache.exists() or not any(cache.iterdir()), "nothing was downloaded into the cache"


class TestTheFactAndTheWarning:
    def test_a_needed_download_is_a_fact_and_the_declared_warning(self):
        fact, warnings = embedding_download(SimpleNamespace(embedding_download=lambda: (True, 0.48)))
        assert "downloaded once first" in fact.value and "0.5 GB" in fact.value and fact.volatile
        assert [w.code for w in warnings] == ["embedding_model_download"]
        assert warnings[0].kind == "situation" and warnings[0].numbers == {"size_gb": 0.5}

    def test_a_download_that_cannot_be_ruled_out_is_warned_about_too(self):
        fact, warnings = embedding_download(SimpleNamespace(embedding_download=lambda: (None, 2.25)))
        assert "if it is not on this computer yet" in fact.value
        assert [w.code for w in warnings] == ["embedding_model_download"]

    def test_models_on_this_computer_are_a_fact_without_a_warning(self):
        fact, warnings = embedding_download(SimpleNamespace(embedding_download=lambda: (False, 0.0)))
        assert "nothing is downloaded" in fact.value and warnings == ()

    def test_no_knowledge_base_no_fact(self):
        assert embedding_download(None) == (None, ())

    def test_every_tool_that_can_start_the_download_declares_the_warning(self):
        for tool in ("crawl_docs_site", "index_framework_docs", "add_pack_from_index"):
            assert "embedding_model_download" in catalog.TOOL_FACTS[tool].situation, tool
