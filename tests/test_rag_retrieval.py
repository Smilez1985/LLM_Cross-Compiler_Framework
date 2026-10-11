"""Retrieval quality on top of the hybrid search: the cross-encoder reranker (relevance mapping, a cache keyed by
text, degrading when it cannot run), the relevance floor, the excerpt budget, oversized code blocks and tables,
identifier-aware BM25, and idle unloading of models. Fake models only, no download."""

import logging
import math
import re
import socket
import threading
from types import SimpleNamespace

import pytest

from orchestrator.Core.config_manager import ConfigManager
from orchestrator.rag.chunking import chunk_document
from orchestrator.rag.embeddings import SPARSE_MODEL, _FastEmbedBase, expand_identifiers
from orchestrator.rag.errors import EmbeddingError, EmbeddingModelNotCached
from orchestrator.rag.retrieval import (CUT_MARK, DEFAULT_CPU_RERANKER, DEFAULT_GPU_RERANKER, DEFAULT_MIN_SCORE,
                                        RERANKER_REGISTRY, FastEmbedReranker, pack_excerpts, relevance,
                                        select_reranker)
from test_rag import FakeEmbedder, FakeSparse, ingest_all


class FakeReranker:
    """Relevance by scripted words: a text containing one of them gets its score, any other text 0.05."""
    model_id = "fake/reranker"

    def __init__(self, words=None, fail=None):
        self.words = words or {}
        self.fail = fail
        self.spec = SimpleNamespace(min_score=0.2)
        self.calls = []
        self.prepared = self.released = 0

    def scores(self, query, documents):
        self.calls.append((query, list(documents)))
        if self.fail is not None:
            raise self.fail
        return [max([s for w, s in self.words.items() if w in d.lower()], default=0.05) for d in documents]

    def prepare(self):
        self.prepared += 1

    def release(self):
        self.released += 1
        return True


class Releasable(FakeEmbedder):
    released = 0

    def release(self):
        self.released += 1
        return True


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    """RAGManagers on one embedded storage in tmp_path (close one before opening the next)."""
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    from orchestrator.Core.rag_manager import RAGManager

    created = []

    def factory(embedder=None, sparse=None, reranker=None, **settings):
        settings.setdefault("rag_model_idle_minutes", 0)  # no idle release unless a test asks for it
        config = ConfigManager(tmp_path / "configs")
        for key, value in settings.items():
            assert config.set(key, value), key
        framework = SimpleNamespace(config=config, info=SimpleNamespace(installation_path=tmp_path))
        rag = RAGManager(framework, embedder=embedder or FakeEmbedder(), sparse_embedder=sparse or FakeSparse(),
                         reranker=reranker)
        rag.idle_close_seconds = 0
        created.append(rag)
        return rag

    yield factory
    for rag in created:
        rag.close()


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def refuse(self, address):
        attempts.append(address)
        raise OSError("network disabled in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


# --- the reranker model -------------------------------------------------------------------------------------------

class ScriptedCrossEncoder:
    """FastEmbed's TextCrossEncoder interface; logits from a function of the document."""

    def __init__(self, logit=lambda doc: float(len(doc))):
        self.logit = logit
        self.batches = []

    def rerank(self, query, documents, batch_size=64):
        self.batches.append((query, list(documents), batch_size))
        for doc in documents:
            yield self.logit(doc)


def scripted_reranker(tmp_path, model, offline=False):
    reranker = FastEmbedReranker(RERANKER_REGISTRY[DEFAULT_CPU_RERANKER], tmp_path / "cache", lambda: offline)
    reranker._create = lambda offline: model
    return reranker


def test_relevance_is_a_sigmoid_that_cannot_overflow():
    assert relevance(0.0) == 0.5
    assert relevance(4.0) == pytest.approx(1 / (1 + math.exp(-4.0)))
    assert relevance(-1e6) == 0.0 and relevance(1e6) == 1.0
    assert relevance(-5) < relevance(-1) < relevance(1) < relevance(5)


def test_scores_are_relevances_in_small_batches(tmp_path):
    model = ScriptedCrossEncoder(lambda doc: {"good": 6.0, "bad": -6.0}[doc])
    reranker = scripted_reranker(tmp_path, model)

    good, bad = reranker.scores("query", ["good", "bad"])

    assert good > 0.99 and bad < 0.01
    assert model.batches == [("query", ["good", "bad"], FastEmbedReranker.BATCH_SIZE)] and reranker.BATCH_SIZE == 8


@pytest.mark.parametrize("logit, message", [(float("nan"), "unusable"), (float("inf"), "unusable"),
                                            (None, "failed: boom")])
def test_unusable_scores_are_errors(tmp_path, logit, message):
    def score(doc):
        if logit is None:
            raise RuntimeError("boom")
        return logit

    with pytest.raises(EmbeddingError, match=message):
        scripted_reranker(tmp_path, ScriptedCrossEncoder(score)).scores("q", ["a", "b"])


def test_a_wrong_number_of_scores_is_an_error(tmp_path):
    class Short(ScriptedCrossEncoder):
        def rerank(self, query, documents, batch_size=64):
            yield 1.0

    with pytest.raises(EmbeddingError, match="unusable"):
        scripted_reranker(tmp_path, Short()).scores("q", ["a", "b"])


def test_cache_is_keyed_by_text_not_by_position(tmp_path):
    model = ScriptedCrossEncoder()
    reranker = scripted_reranker(tmp_path, model)
    first = dict(zip(["a", "bb", "ccc"], reranker.scores("q", ["a", "bb", "ccc"])))

    # Another order of the same candidates: the same score per document, no model call.
    again = reranker.scores("q", ["ccc", "a", "bb"])
    assert again == [first["ccc"], first["a"], first["bb"]] and len(model.batches) == 1
    # Only new texts are scored, duplicates once; another query is another key.
    reranker.scores("q", ["a", "dddd", "dddd"])
    reranker.scores("other", ["a"])
    assert [b[1] for b in model.batches[1:]] == [["dddd"], ["a"]]


def test_cache_keys_do_not_collide_at_the_separator(tmp_path):
    """Crawled text may hold any control character: query 'a<US>b' with 'c' is another pair than 'a' with 'b<US>c'."""
    model = ScriptedCrossEncoder()
    reranker = scripted_reranker(tmp_path, model)
    reranker.scores("a\x1fb", ["c"])
    reranker.scores("a", ["b\x1fc"])
    assert [b[:2] for b in model.batches] == [("a\x1fb", ["c"]), ("a", ["b\x1fc"])]


def test_cache_is_bounded(tmp_path):
    model = ScriptedCrossEncoder()
    reranker = scripted_reranker(tmp_path, model)
    reranker.CACHE_SIZE = 3
    reranker.scores("q", ["a", "b", "c"])
    reranker.scores("q", ["a"])  # recently used: kept
    reranker.scores("q", ["d"])

    assert len(reranker._scores) == 3
    reranker.scores("q", ["a", "c", "d"])
    reranker.scores("q", ["b"])  # evicted, scored again
    assert [b[1] for b in model.batches] == [["a", "b", "c"], ["d"], ["b"]]


def test_missing_model_offline_is_a_clear_error_and_never_downloaded(tmp_path, monkeypatch, no_network):
    pytest.importorskip("fastembed")
    import fastembed.common.model_management as mm
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    monkeypatch.setattr(mm, "model_info", lambda *a, **k: pytest.fail("online metadata lookup"))
    spec = RERANKER_REGISTRY[DEFAULT_CPU_RERANKER]
    for _ in range(2):  # registered once, also with a second instance
        reranker = FastEmbedReranker(spec, tmp_path / "cache", offline=lambda: True)
        with pytest.raises(EmbeddingModelNotCached, match=f"reranking model {spec.model_id} is not in .*offline"):
            reranker.scores("q", ["d"])

    listed = [m for m in TextCrossEncoder.list_supported_models() if m["model"] == spec.model_id]
    assert len(listed) == 1 and listed[0]["model_file"] == "onnx/model_quint8_avx2.onnx"
    assert no_network == []


@pytest.mark.parametrize("providers, ram, expected", [
    ([], 4, None),
    ([], 8, DEFAULT_CPU_RERANKER),
    (["CUDAExecutionProvider"], 8, DEFAULT_CPU_RERANKER),
    (["CUDAExecutionProvider"], 32, DEFAULT_GPU_RERANKER),
])
def test_auto_picks_by_hardware(providers, ram, expected):
    spec, reason = select_reranker("auto", providers, ram)
    assert (spec.model_id if spec else None) == expected
    assert f"{ram} GB" in reason


def test_reranker_setting_off_and_model_ids():
    assert select_reranker("off", [], 64) == (None, "turned off (rag_reranker: off)")
    spec, reason = select_reranker(DEFAULT_GPU_RERANKER.upper(), [], 4)  # chosen explicitly: no hardware rule
    assert spec is RERANKER_REGISTRY[DEFAULT_GPU_RERANKER] and "configuration" in reason
    with pytest.raises(EmbeddingError, match="Unknown reranker 'nobody/nothing'"):
        select_reranker("nobody/nothing")
    pytest.importorskip("fastembed")
    native, _ = select_reranker("Xenova/ms-marco-MiniLM-L-6-v2")  # shipped by FastEmbed: generic entry
    assert native.model_id == "Xenova/ms-marco-MiniLM-L-6-v2" and native.min_score == DEFAULT_MIN_SCORE


# --- the excerpt budget -------------------------------------------------------------------------------------------

def words(text):
    return len(text.split())


def test_an_excerpt_that_does_not_fit_is_skipped_not_the_end():
    small, big, other = "one two three", " ".join(["word"] * 50), "four five"
    assert pack_excerpts([small, big, other], 10, words) == [small, other]
    assert pack_excerpts([], 10, words) == []


def test_an_over_long_first_excerpt_is_cut_at_a_line():
    lines = [f"line {i} has five words" for i in range(40)]
    cut, later = pack_excerpts(["\n".join(lines), "short one"], 22, words)  # a later excerpt still fits

    assert cut.endswith(CUT_MARK) and words(cut) + words(later) <= 22 and later == "short one"
    kept = cut[:-len(CUT_MARK)].split("\n")
    assert kept == lines[:len(kept)] and len(kept) >= 3  # whole lines only


def test_a_cut_inside_a_code_block_closes_its_fence():
    """An unclosed fence would turn the rest of the prompt, the instruction after the excerpts included, into code."""
    for fence in ("```", "~~~~"):
        code = "\n".join(f"cmake --build build --target step_{i}" for i in range(60))
        excerpt = f"[Source: https://docs.example/build]\nBuild it:\n{fence}bash\n{code}\n{fence}\nDone."

        (cut,) = pack_excerpts([excerpt], 40, words)

        body = cut[:-len(CUT_MARK)]
        assert body.endswith(f"\n{fence}") and body.count(fence) == 2 and words(cut) <= 40, cut
    closed = "Intro.\n```\nshort\n```\n" + "\n".join(f"plain line {i} of prose" for i in range(40))
    (cut,) = pack_excerpts([closed], 30, words)  # a block closed before the cut gets no extra fence
    assert cut[:-len(CUT_MARK)].count("```") == 2 and not cut[:-len(CUT_MARK)].endswith("```")


def test_an_over_long_single_line_is_cut_inside():
    (cut,) = pack_excerpts(["x" * 3000], 100)  # approx_tokens: 3 characters per token
    assert cut.endswith(CUT_MARK) and len(cut) <= 300
    assert pack_excerpts(["anything"], 0, words) == []


# --- the rerank stage in RAGManager.search ------------------------------------------------------------------------

LICENSE_FIRST = {"license": 0.9, "logo": 0.5}


def test_reranking_reorders_drops_irrelevant_results_and_keeps_the_fused_score(knowledge):
    reranker = FakeReranker(LICENSE_FIRST)
    rag = knowledge(reranker=reranker)
    ingest_all(rag)

    fused = rag.search("run the model faster on the npu", limit=3, rerank=False)
    hits = rag.search("run the model faster on the npu", limit=3, rerank=True)

    assert fused[0].source == "https://docs.example/npu" and len(reranker.calls) == 1
    assert [h.source for h in hits] == ["https://docs.example/license", "https://docs.example/notes"]
    assert [h.score for h in hits] == [0.9, 0.5]  # relevance; the npu page (0.05) is below the floor
    assert all(0 < h.metadata["fused_score"] <= 1 and h.metadata["reranker"] == "fake/reranker" for h in hits)
    assert "fused_score" not in fused[0].metadata


def test_the_floor_may_leave_nothing_and_can_be_configured(knowledge):
    rag = knowledge(reranker=FakeReranker(LICENSE_FIRST), rag_rerank_min_score=0.6)
    ingest_all(rag)
    assert [h.source for h in rag.search("model memory", limit=4, rerank=True)] == ["https://docs.example/license"]
    rag.config.set("rag_rerank_min_score", 0.95)
    assert rag.search("model memory", limit=4, rerank=True) == []  # no excerpt beats an irrelevant one


def test_unset_floor_is_the_models_and_zero_turns_it_off(knowledge, tmp_path):
    """0 used to mean "the model's floor", so the floor could not be switched off at all."""
    rag = knowledge(reranker=FakeReranker(LICENSE_FIRST))
    ingest_all(rag)
    assert rag.config.get("rag_rerank_min_score") is None
    assert [h.score for h in rag.search("model memory", limit=4, rerank=True)] == [0.9, 0.5]  # floor 0.2
    rag.close()

    (tmp_path / "configs" / "config.yml").write_text("rag_rerank_min_score: 0\n")  # a config file still loads
    rag = knowledge(reranker=FakeReranker(LICENSE_FIRST))
    assert rag.config.get("rag_rerank_min_score") == 0
    assert [h.score for h in rag.search("model memory", limit=4, rerank=True)] == [0.9, 0.5, 0.05, 0.05]


def test_a_floor_without_a_value_is_unset_not_invalid(knowledge, tmp_path, caplog):
    """`rag_rerank_min_score:` without a value (YAML null) is the documented unset state: no "invalid value"
    warning, and setting None at run time goes back to the model's floor."""
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "config.yml").write_text("rag_rerank_min_score:\n")
    with caplog.at_level(logging.WARNING):
        rag = knowledge(reranker=FakeReranker(LICENSE_FIRST), rag_rerank_min_score=0)
    assert "rag_rerank_min_score" not in caplog.text
    ingest_all(rag)
    assert len(rag.search("model memory", limit=4, rerank=True)) == 4  # 0: no floor
    assert rag.config.set("rag_rerank_min_score", None)
    assert [h.score for h in rag.search("model memory", limit=4, rerank=True)] == [0.9, 0.5]  # the model's floor


def test_default_and_off_switch(knowledge):
    reranker = FakeReranker(LICENSE_FIRST)
    rag = knowledge(reranker=reranker)
    ingest_all(rag)

    rag.search("model memory")  # rerank=None follows rag_rerank_default (off)
    assert reranker.calls == []
    rag.config.set("rag_rerank_default", True)
    assert rag.search("model memory")[0].source == "https://docs.example/license"
    rag.config.set("rag_reranker", "off")
    rag.search("model memory", rerank=True)
    assert len(reranker.calls) == 1


def test_a_failing_reranker_keeps_the_fused_order_until_the_setting_changes(knowledge, monkeypatch):
    import orchestrator.Core.rag_manager as rm

    failing = FakeReranker(LICENSE_FIRST, fail=EmbeddingModelNotCached("The reranking model x is not in /cache"))
    rerankers = iter([failing, FakeReranker(LICENSE_FIRST)])
    monkeypatch.setattr(rm, "create_reranker", lambda spec, cache_dir, offline: next(rerankers))
    rag = knowledge(rag_reranker=DEFAULT_CPU_RERANKER)
    ingest_all(rag)
    fused = [h.source for h in rag.search("model memory", limit=3, rerank=False)]

    assert [h.source for h in rag.search("model memory", limit=3, rerank=True, strict=True)] == fused
    assert "not in /cache" in rag.last_error and "next start" in rag.last_error
    rag.search("model memory", limit=3, rerank=True)
    assert len(failing.calls) == 1 and rag.reranker() is None  # not tried again in this run
    rag.config.set("rag_reranker", DEFAULT_GPU_RERANKER)  # a changed setting tries again
    assert rag.search("model memory", limit=3, rerank=True)[0].source == "https://docs.example/license"


def test_an_unknown_reranker_keeps_the_fused_order(knowledge):
    rag = knowledge(rag_reranker="nobody/nothing")
    ingest_all(rag)
    assert rag.search("model memory", limit=2, rerank=True)
    assert "Unknown reranker" in rag.last_error
    assert rag.reranker() is None and "Unknown reranker" in rag.reranker_reason


def test_candidates_are_deduplicated_and_pooled_before_reranking(knowledge):
    reranker = FakeReranker()
    rag = knowledge(reranker=reranker)
    for i in range(30):
        rag.ingest_document(f"https://docs.example/{i}", f"Model memory note number {i}.")
    rag.ingest_document("https://mirror.example/0", "Model memory note number 0.")

    for limit, pool in ((3, 12), (10, 20), (25, 25)):
        rag.search("model memory note", limit=limit, rerank=True)
        texts = reranker.calls[-1][1]
        assert len(texts) == pool and len(set(texts)) == pool, limit


def test_the_reranker_reads_title_section_and_the_start_of_the_content(knowledge):
    reranker = FakeReranker()
    rag = knowledge(embedder=FakeEmbedder(chunk_tokens=600), reranker=reranker)
    rag.ingest_document("https://docs.example/guide", "# Guide\n\n## Install\n\n" + "Install the model. " * 200,
                        {"title": "Guide"})
    rag.search("install the model", rerank=True)
    texts = reranker.calls[0][1]
    assert all(t.startswith("Guide > Install\n") for t in texts) and any("## Install" in t for t in texts)
    assert max(len(t) for t in texts) == len("Guide > Install\n") + 1500  # long chunks are cut for the model


def test_prepare_models_covers_the_reranker_unless_it_is_off(knowledge):
    reranker = FakeReranker()
    rag = knowledge(reranker=reranker)
    assert rag.prepare_models() and reranker.prepared == 1
    assert rag.reranker() is reranker and rag.reranker(None) is None  # rag_rerank_default is off
    rag.config.set("rag_reranker", "off")
    assert rag.prepare_models() and reranker.prepared == 1
    assert rag.reranker() is None and "off" in rag.reranker_reason


def test_the_configured_reranker_is_created_from_the_registry(knowledge, monkeypatch):
    import orchestrator.Core.rag_manager as rm

    created = []

    def create(spec, cache_dir, offline):
        created.append((spec.model_id, cache_dir, offline()))
        return FakeReranker(LICENSE_FIRST)

    monkeypatch.setattr(rm, "create_reranker", create)
    rag = knowledge(rag_reranker=DEFAULT_CPU_RERANKER, offline_mode=True)
    ingest_all(rag)
    rag.search("model memory", rerank=True)
    rag.search("model memory", rerank=True)
    assert created == [(DEFAULT_CPU_RERANKER, rag.models_cache_dir, True)]
    rag.config.set("rag_reranker", DEFAULT_GPU_RERANKER)  # a changed setting gets its model
    rag.search("model memory", rerank=True)
    assert [c[0] for c in created] == [DEFAULT_CPU_RERANKER, DEFAULT_GPU_RERANKER]


def test_untrusted_metadata_cannot_pose_as_a_reranked_result(knowledge):
    rag = knowledge()
    rag.ingest_document("https://pack.example/x", "Model memory hint.", {"reranker": "fake", "fused_score": 9})
    (hit,) = rag.search("model memory hint", rerank=False)
    assert "reranker" not in hit.metadata and "fused_score" not in hit.metadata


# --- Ditto: rerank at the build call sites, excerpts within the budget --------------------------------------------

def test_ditto_packs_excerpts_into_a_third_of_the_context(tmp_path):
    from test_ditto import make_ditto

    searches = []
    hits = [SimpleNamespace(source=f"https://docs.example/{i}", content="x" * 2000) for i in range(5)]
    rag = SimpleNamespace(search=lambda q, limit, rerank: searches.append(rerank) or hits)
    ditto = make_ditto(tmp_path, framework=SimpleNamespace(ensure_rag_manager=lambda: rag),
                       enable_rag_knowledge=True, chat_context_limit=4096)

    block = ditto._knowledge("question")

    assert "https://docs.example/1" in block and "https://docs.example/2" not in block  # 2 x ~670 of 1365 tokens
    assert searches == [None]


def test_ditto_reranks_only_build_recommendations(tmp_path, monkeypatch):
    """Build-log tails and the module search lost relevant excerpts to the reranker in the retrieval evaluation
    (tags log and callsite), so those keep the hybrid order even with rag_rerank_default (it covers chat and MCP
    searches only); recommendations gained."""
    from test_ditto import make_ditto

    class Stop(Exception):
        pass

    ditto = make_ditto(tmp_path, rag_rerank_default=True)
    seen = []
    monkeypatch.setattr(ditto, "_knowledge", lambda query, limit=5, rerank=None: seen.append(rerank) or "")
    monkeypatch.setattr(ditto, "_structured", lambda *a, **k: (_ for _ in ()).throw(Stop()))
    probe = tmp_path / "target_hardware_config.txt"
    probe.write_text("CPU: rk3588")
    for call in (lambda: ditto.analyze_error_log("error: E2041"), lambda: ditto.generate_module(probe),
                 lambda: ditto.recommend_build_settings("m", "rockchip", {"GGUF": ["Q4_K_M"]}, use_knowledge=True,
                                                        notes={})):
        with pytest.raises(Stop):
            call()
    assert seen == [False, False, True]


# --- oversized code blocks and tables -----------------------------------------------------------------------------

@pytest.fixture
def model_tokens():
    """A model tokenizer's count: words and punctuation runs are tokens (table pipes and dashes count)."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    return lambda text: len(tokenizer.encode(text).ids)


def table_rows(text):
    return [line for line in text.split("\n") if line.startswith("| -f")]


def test_an_oversized_table_repeats_its_header_in_every_piece(model_tokens):
    header = "| flag | meaning |\n|---|---|"
    rows = [f"| -f{i} | option number {i} of the tool |" for i in range(120)]
    md = "# Flags\n\nThe options.\n\n" + header + "\n" + "\n".join(rows) + "\n\nAfter the table."

    chunks = chunk_document(md, count_tokens=model_tokens, max_tokens=80, hard_tokens=100)

    pieces = [c for c in chunks if table_rows(c.text)]
    assert len(pieces) > 5
    for chunk in pieces:
        assert chunk.text[chunk.text.index("| flag"):].startswith(header + "\n| -f"), chunk.text[:80]
        assert chunk.tokens <= 100 and chunk.section == "Flags"
    assert [r for c in pieces for r in table_rows(c.text)] == rows  # every row once, in order


def test_an_oversized_code_block_is_fenced_in_every_piece(model_tokens):
    lines = [f"cmake --build build -j{i} --target step_{i}" for i in range(150)]
    for closing in ("\n```", ""):  # closed, and unclosed (runs to the end of the document)
        md = "## Build\n\n```bash\n" + "\n".join(lines) + closing

        chunks = chunk_document(md, count_tokens=model_tokens, max_tokens=80, hard_tokens=100)

        assert len(chunks) > 5
        for chunk in chunks:
            body = chunk.text[chunk.text.index("```bash"):]
            assert body.startswith("```bash\n") and body.endswith("\n```") and chunk.tokens <= 100
        kept = [line for c in chunks for line in c.text.split("\n") if line.startswith("cmake")]
        assert kept == lines


def test_blocks_within_the_hard_limit_stay_whole(model_tokens):
    code = "```bash\n" + "\n".join(f"make step_{i}" for i in range(20)) + "\n```"
    assert model_tokens(code) > 40
    chunks = chunk_document(f"# Build\n\n{code}", count_tokens=model_tokens, max_tokens=40, hard_tokens=200)
    assert sum(code in c.text for c in chunks) == 1
    big = "```\n" + "\n".join(f"line {i}" for i in range(300)) + "\n```"
    assert len(chunk_document(big, count_tokens=model_tokens, max_tokens=40)) == 1  # no hard limit: as before


def test_ingest_splits_blocks_beyond_the_model_context(knowledge):
    embedder = FakeEmbedder(chunk_tokens=60)
    embedder.spec = SimpleNamespace(chunk_tokens=60, max_tokens=200)  # hard limit 200 - 96 = 104 words
    rag = knowledge(embedder=embedder)
    table = "| flag | meaning |\n|---|---|\n" + "\n".join(f"| -f{i} | option {i} |" for i in range(100))
    assert rag.ingest_document("https://docs.example/flags", f"# Flags\n\n{table}")

    stored = [r["content"] for r in rag.export_records()]
    assert len(stored) > 3 and all("| flag | meaning |\n|---|---|" in text for text in stored)
    assert max(len(text.split()) for text in stored) <= 104


# --- identifiers in BM25 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, added", [
    ("cmake -DGGML_CUDA=ON ..", "GGML_CUDA GGML CUDA"),
    ("pip install rknn_toolkit2", "rknn toolkit2"),
    ("rknnToolkitLite and XMLParser", "rknn Toolkit Lite XML Parser"),
    ("CMAKE_CUDA_ARCHITECTURES=87 and CMAKE_CUDA_ARCHITECTURES", "CMAKE CUDA ARCHITECTURES"),
    ("-DNDEBUG=1", "NDEBUG"),
    ("-DBUILD_SHARED_LIBS", "BUILD_SHARED_LIBS BUILD SHARED LIBS"),
    ("-D_FORTIFY_SOURCE=2 -D_GNU_SOURCE", "_FORTIFY_SOURCE FORTIFY SOURCE _GNU_SOURCE GNU"),  # no stray "D"
    # Version numbers are no search words: no fragments of one or two digits (the identifier itself stays).
    ("x86_64 wheels for manylinux_2_17", "x86 manylinux"),
    ("manylinux2014_x86_64 and gcc_12", "manylinux2014 x86 gcc"),
])
def test_identifiers_get_their_parts(text, added):
    assert expand_identifiers(text) == f"{text}\n{added}"


# Only a CMake-style definition loses its -D: -DEBUG is not "EBUG", -DNS not "NS" (nor is -DNDEBUG told apart).
@pytest.mark.parametrize("text", ["plain words", "E2041 on the RK3588", "GPUs and PDFs", "Build-Container", "",
                                  "link with -DEBUG", "resolver -DNS", "-DNDEBUG"])
def test_plain_text_is_unchanged(text):
    assert expand_identifiers(text) == text


@pytest.fixture
def real_bm25(tmp_path):
    pytest.importorskip("fastembed")
    from orchestrator.rag.embeddings import FastEmbedSparseEmbedder

    # A local model directory without stop word files: no download.
    return FastEmbedSparseEmbedder(tmp_path / "cache", model_dir=tmp_path / "bm25")


def test_real_bm25_matches_identifier_parts(real_bm25, knowledge):
    assert real_bm25.model_id == SPARSE_MODEL
    doc, = real_bm25.embed_passages(["Configure with -DGGML_CUDA=ON and install rknn_toolkit2."])
    for query in ("GGML_CUDA", "ggml cuda", "rknn toolkit2"):
        assert set(real_bm25.embed_query(query).indices) <= set(doc.indices), query

    rag = knowledge(sparse=real_bm25)
    rag.ingest_document("https://docs.example/flags", "Configure llama.cpp with -DGGML_CUDA=ON for NVIDIA GPUs.")
    rag.ingest_document("https://docs.example/install", "Install the model on the NPU board first.")
    rag.ingest_document("https://docs.example/memory", "Model memory and RAM use of the model.")
    assert rag.search("GGML_CUDA")[0].source == "https://docs.example/flags"


def test_keyword_vectors_without_identifier_parts_stay_valid_until_re_imported(real_bm25, knowledge, monkeypatch):
    """Chunks imported before the identifier splitting (or by an earlier build) share the hashed BM25 vocabulary,
    so they stay searchable in the same index without a migration; importing the document again adds the parts."""
    import orchestrator.rag.embeddings as embeddings

    def keyword_ids(rag):
        (record,) = rag._ensure_store().records(with_vectors=True)
        return set(record.vector["bm25"].indices)

    url, text = "https://docs.example/flags", "Configure llama.cpp with -DGGML_CUDA=ON for NVIDIA GPUs."
    parts = set(real_bm25.embed_query("ggml cuda").indices)
    rag = knowledge(sparse=real_bm25)
    with monkeypatch.context() as earlier:
        earlier.setattr(embeddings, "expand_identifiers", lambda value: value)
        rag.ingest_document(url, text)
    assert not parts <= keyword_ids(rag)
    assert rag.search("Configure llama.cpp for NVIDIA")[0].source == url  # mixed with today's query encoding
    rag.ingest_document(url, text, replace=True)
    assert parts <= keyword_ids(rag)
    assert rag.search("GGML_CUDA")[0].source == url


# --- unloading idle models ----------------------------------------------------------------------------------------

class CountingModel(_FastEmbedBase):
    model_id = "fake/model"

    def __init__(self, tmp_path):
        super().__init__(tmp_path, lambda: False, 0.1)
        self.created = 0

    def _create(self, offline):
        self.created += 1
        return object()


def test_release_frees_the_model_and_the_next_use_loads_it_again(tmp_path):
    pytest.importorskip("fastembed")
    model = CountingModel(tmp_path)
    assert model.release() is False  # nothing loaded yet
    first = model._load()
    assert model._load() is first and model.created == 1
    assert model.release() is True and model._model is None
    assert model._load() is not first and model.created == 2


def test_idle_models_are_released_after_the_configured_time(knowledge, monkeypatch):
    released = threading.Event()

    class Signalling(FakeReranker):
        def release(self):
            released.set()
            return super().release()

    embedder, reranker = Releasable(), Signalling(LICENSE_FIRST)
    rag = knowledge(embedder=embedder, reranker=reranker, rag_model_idle_minutes=5)
    ingest_all(rag)
    rag.search("model memory", rerank=True)
    earlier = rag._model_timer
    assert earlier.interval == 300 and earlier.daemon
    rag.search("model memory")
    assert rag._model_timer is not earlier and earlier.finished.is_set()  # each use restarts the countdown

    monkeypatch.setattr(rag, "_model_idle", lambda: 0.05)
    rag.search("model memory", rerank=True)
    assert released.wait(10)
    with rag._model_lock:  # held while the models are released
        assert embedder.released == reranker.released == 1 and rag._model_timer is None
    assert rag.search("model memory", rerank=True)  # loads again on the next use
    latest = rag._model_timer
    rag.close()
    assert rag._model_timer is None and latest.finished.is_set()


def test_models_in_use_are_not_released_and_the_countdown_starts_after_the_last_use(knowledge):
    """A first download or a long document can outlast the idle time; a countdown must never release the models
    (and the tokenizer under count_tokens) while they work, only to load them again."""
    rag, seen = None, []

    class Slow(Releasable):
        def embed_query(self, text):
            seen.append(rag._model_timer)  # no countdown while the search runs
            late = threading.Thread(target=rag._release_if_idle)  # a countdown that fired just before the use
            late.start()
            late.join()
            rag.ingest_document("https://docs.example/b", "Another note.")  # an overlapping use ends first
            seen.append(rag._model_timer)
            return super().embed_query(text)

    embedder, reranker = Slow(), FakeReranker(LICENSE_FIRST)
    rag = knowledge(embedder=embedder, reranker=reranker, rag_model_idle_minutes=10)
    rag.ingest_document("https://docs.example/a", "Model memory note.")
    earlier = rag._model_timer
    rag.search("model memory note", rerank=True)

    assert seen == [None, None] and earlier.finished.is_set()
    assert embedder.released == reranker.released == 0
    timer = rag._model_timer  # started when the search returned
    assert timer is not None and timer.interval == 600 and rag._models_in_use == 0


def test_count_tokens_survives_a_release_between_check_and_use(tmp_path):
    from orchestrator.rag.embeddings import MODEL_REGISTRY, FastEmbedDenseEmbedder

    class Tokenizer:
        def encode(self, text, add_special_tokens=False):
            return SimpleNamespace(ids=text.split())

    class Racing(FastEmbedDenseEmbedder):
        reads = 0

        @property
        def _tokenizer(self):  # released right after the first look, as by the idle timer
            Racing.reads += 1
            return Tokenizer() if Racing.reads == 1 else None

        @_tokenizer.setter
        def _tokenizer(self, value):
            pass

    dense = Racing(MODEL_REGISTRY["intfloat/multilingual-e5-small"], tmp_path)
    dense._model = object()
    assert dense.count_tokens("three short words") == 3


def test_idle_release_can_be_turned_off(knowledge):
    rag = knowledge(embedder=Releasable(), rag_model_idle_minutes=0)
    ingest_all(rag)
    rag.search("model memory")
    assert rag._model_timer is None and rag._embedder.released == 0  # 0 = keep the models loaded


def test_release_reports_what_was_loaded(tmp_path):
    from orchestrator.rag.embeddings import MODEL_REGISTRY, FastEmbedDenseEmbedder

    dense = FastEmbedDenseEmbedder(MODEL_REGISTRY["intfloat/multilingual-e5-small"], tmp_path)
    dense._model, dense._tokenizer = object(), object()
    assert dense.release() is True and dense._tokenizer is None
    assert re.search("e5-small", dense.model_id)
