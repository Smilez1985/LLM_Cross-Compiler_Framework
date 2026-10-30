"""Retrieval cases of the Ditto evaluation (`llm-cli ditto eval --kind retrieval`): the shipped corpus and cases
are valid and consistent, grading and metrics are exact on a scripted knowledge base, the real knowledge base
runs in a temporary folder (never the user's, never a Qdrant server), and the CLI needs no AI model and no
download (fake embedding models; offline mode with a missing model is a clear error)."""

import json
import math
import re
import socket
import zlib
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.rag_manager import SearchResult
from orchestrator.evals import ditto_eval
from orchestrator.evals.ditto_eval import (RETRIEVAL_CASES, RETRIEVAL_DEPTH, Case, CaseResult, EvalReport,
                                           RetrievalEval, RetrievalEvalError, check_retrieval_cases,
                                           corpus_dir, corpus_source, grade_retrieval, load_cases, load_corpus,
                                           retrieval_metrics, temporary_knowledge_base)
from orchestrator.rag.embeddings import SparseVec, approx_tokens
from orchestrator.rag.errors import KnowledgeBaseError

CORPUS = corpus_dir(RETRIEVAL_CASES)


class FakeConfig:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True


class ScriptedKnowledge:
    """RAGManager's ingest/search interface; search answers from a script {query: [(doc id, score), ...]}."""

    def __init__(self, script=None, fail_on=(), ingest_ok=True):
        self.script = script or {}
        self.fail_on = set(fail_on)
        self.ingest_ok = ingest_ok
        self.ingested = []
        self.searches = []
        self.last_error = "" if ingest_ok else "embedding model missing"

    def ingest_document(self, source, content, metadata=None, replace=False, *, outside=False):
        self.ingested.append((source, content, metadata, replace))
        self.outside = getattr(self, "outside", []) + [outside]
        return self.ingest_ok

    def search(self, query, limit=3, score_threshold=None, strict=False, **options):
        self.searches.append({"query": query, "limit": limit, "strict": strict, **options})
        if query in self.fail_on:
            raise KnowledgeBaseError("storage is gone")
        return [SearchResult(score, f"text of {doc}", corpus_source(doc), {"doc_id": doc})
                for doc, score in self.script.get(query, [])][:limit]


def retrieval_case(case_id, query, **expect):
    return Case(id=case_id, kind="retrieval", query=query, tags=expect.pop("tags", []), expect=expect)


DOCS = [ditto_eval.CorpusDoc(id=d, title=d, lang="en", text=f"About {d}.") for d in ("a", "b", "c", "d")]


# --- the shipped corpus and cases ---------------------------------------------------------------------------------

def test_shipped_cases_and_corpus_are_valid_and_consistent():
    cases = load_cases(RETRIEVAL_CASES)
    docs = load_corpus(CORPUS)
    check_retrieval_cases(cases, docs)
    assert all(c.kind == "retrieval" for c in cases) and 28 <= len(cases) <= 50
    assert 25 <= len(docs) <= 40
    assert Counter(d.lang for d in docs)["de"] >= 10 and Counter(d.lang for d in docs)["en"] >= 10
    tags = Counter(t for c in cases for t in c.tags)
    assert tags["de"] >= 10 and tags["en"] >= 10
    assert tags["identifier"] >= 6 and tags["cross-lingual"] >= 3 and tags["oversized"] >= 2
    # Build-log analysis searches with log lines, module proposals and recommendations with keyword strings.
    assert tags["log"] >= 5 and tags["callsite"] >= 2
    negatives = [c for c in cases if c.expect.none_relevant]
    assert len(negatives) >= 4 and all("negative" in c.tags for c in negatives)
    assert all(c.expect.within <= RETRIEVAL_DEPTH for c in cases)


def test_corpus_has_a_large_table_and_a_long_code_block():
    """Blocks larger than the embedding model's context exercise oversized-block handling."""
    texts = [d.text for d in load_corpus(CORPUS)]
    tables = ["\n".join(line for line in t.splitlines() if line.startswith("|")) for t in texts]
    fences = [block for t in texts for block in re.findall(r"```.*?```", t, re.S)]
    # approx_tokens is conservative (3 characters per token); e5 models read 512 tokens at most.
    assert max(map(approx_tokens, tables)) > 512 and max(len(t.splitlines()) for t in tables) >= 25
    assert max(map(approx_tokens, fences)) > 512


def test_cases_do_not_quote_their_documents():
    """A query must not be a sentence of its expected document; that would test string matching, not retrieval."""
    docs = {d.id: d.text.lower() for d in load_corpus(CORPUS)}
    for case in load_cases(RETRIEVAL_CASES):
        if len(case.query.split()) > 3:
            assert not any(case.query.lower().rstrip("?.") in docs[d] for d in case.expect.relevant), case.id


# --- loading ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"id": "x", "kind": "retrieval", "expect": {"relevant": ["a"]}},
    {"id": "x", "kind": "retrieval", "query": "  ", "expect": {"relevant": ["a"]}},
    {"id": "x", "kind": "retrieval", "query": "q"},
    {"id": "x", "kind": "retrieval", "query": "q", "expect": {"relevant": ["a"], "none_relevant": True}},
    {"id": "x", "kind": "retrieval", "query": "q", "expect": {"relevant": ["a"], "within": 0}},
    {"id": "x", "kind": "retrieval", "query": "q", "expect": {"relevant": ["a"], "within": RETRIEVAL_DEPTH + 1}},
    {"id": "x", "kind": "retrieval", "query": "q", "expect": {"relevant": ["a"], "top": 3}},
])
def test_invalid_retrieval_cases_are_refused(tmp_path, bad):
    path = tmp_path / "cases.yml"
    path.write_text(json.dumps({"cases": [bad]}))
    with pytest.raises(ValueError):
        load_cases(path)


def test_cases_naming_unknown_documents_are_refused():
    with pytest.raises(ValueError, match="nope"):
        check_retrieval_cases([retrieval_case("x", "q", relevant=["a", "nope"])], DOCS)


def write_doc(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(text.encode("utf-8"))  # exactly these line endings (text mode adds CR on Windows)
    return path


def test_corpus_loader_reads_front_matter_and_text(tmp_path):
    write_doc(tmp_path, "doc-1.md", "---\r\ntitle: First\r\nlang: de\r\n---\r\n# Heading\r\n\r\nBody text.\r\n")
    (doc,) = load_corpus(tmp_path)
    assert (doc.id, doc.title, doc.lang, doc.text) == ("doc-1", "First", "de", "# Heading\n\nBody text.")


@pytest.mark.parametrize("name, text", [
    ("a.md", "# no front matter\n"),
    ("a.md", "---\ntitle: T\nlang: en\n"),                       # never closed
    ("a.md", "---\ntitle: T\n---\ntext\n"),                      # no lang
    ("a.md", "---\ntitle: T\nlang: english\n---\ntext\n"),       # not a language code
    ("a.md", "---\ntitle: T\nlang: en\nauthor: x\n---\ntext\n"),  # unknown key
    ("a.md", "---\ntitle: T\nlang: en\n---\n   \n"),             # no text
    ("a b.md", "---\ntitle: T\nlang: en\n---\ntext\n"),          # id must be a plain name
])
def test_corpus_loader_refuses_bad_documents(tmp_path, name, text):
    write_doc(tmp_path, name, text)
    with pytest.raises(ValueError):
        load_corpus(tmp_path)


def test_corpus_folder_comes_from_the_cases_file(tmp_path):
    (tmp_path / "cases.yml").write_text("corpus: docs\ncases: []\n")
    assert corpus_dir(tmp_path / "cases.yml") == tmp_path / "docs"
    (tmp_path / "other.yml").write_text("cases: []\n")
    assert corpus_dir(tmp_path / "other.yml") == tmp_path / "corpus"
    with pytest.raises(ValueError, match="no corpus documents"):
        load_corpus(tmp_path / "corpus")


# --- grading and metrics ------------------------------------------------------------------------------------------

def test_grading():
    expect = retrieval_case("x", "q", relevant=["b", "c"], within=2).expect
    assert grade_retrieval(expect, ["b", "a"]) == []
    assert grade_retrieval(expect, ["a", "c"]) == []
    assert grade_retrieval(expect, ["a", "a", "b"]) == ["first relevant excerpt at rank 3, expected within 2"]
    assert grade_retrieval(expect, ["a", "d"]) == ["none of ['b', 'c'] among 2 results (top: a, d)"]
    assert grade_retrieval(expect, []) == ["none of ['b', 'c'] among 0 results"]
    negative = retrieval_case("n", "q", none_relevant=True).expect
    assert grade_retrieval(negative, []) == []
    assert grade_retrieval(negative, ["a"]) == ["1 excerpts for an unrelated question (first: a)"]


def scripted_run():
    cases = [
        retrieval_case("first", "q1", relevant=["a"], tags=["en"]),
        retrieval_case("third", "q2", relevant=["b"], within=3, tags=["de"]),
        retrieval_case("fourth", "q3", relevant=["c"], within=3, tags=["en", "identifier"]),
        retrieval_case("missing", "q4", relevant=["d"], tags=["de"]),
        retrieval_case("neg-ok", "q5", none_relevant=True, tags=["negative"]),
        retrieval_case("neg-bad", "q6", none_relevant=True, tags=["negative"]),
        retrieval_case("broken", "q7", relevant=["a"]),
    ]
    knowledge = ScriptedKnowledge({
        "q1": [("a", 0.9), ("b", 0.2)],
        "q2": [("c", 0.8), ("a", 0.7), ("b", 0.6)],
        "q3": [("a", 0.9), ("b", 0.8), ("d", 0.7), ("c", 0.6)],
        "q4": [("a", 0.5)],
        "q6": [("d", 0.1)],
    }, fail_on={"q7"})
    return knowledge, RetrievalEval(knowledge, DOCS).run(cases)


def test_scripted_run_grades_every_case():
    knowledge, report = scripted_run()
    passed = {r.case.id: r.pass_rate == 1 for r in report.results}
    assert passed == {"first": True, "third": True, "fourth": False, "missing": False, "neg-ok": True,
                      "neg-bad": False, "broken": False}
    broken = report.results[-1].attempts[0]
    assert broken.error == "Knowledge base error: storage is gone" and broken.retrieved == []
    # Every search asks for the full depth and raises storage errors instead of returning nothing.
    assert {(s["limit"], s["strict"]) for s in knowledge.searches} == {(RETRIEVAL_DEPTH, True)}


def test_metrics_are_exact():
    _, report = scripted_run()
    m = retrieval_metrics(report.results)
    # Positives: ranks 1, 3, 4, none and a failed search (none).
    assert (m["positives"], m["negatives"], m["attempts"]) == (5, 2, 7)
    assert m["hit@1"] == 0.2 and m["hit@3"] == 0.4 and m["hit@k"] == 0.4
    assert m["mrr"] == round((1 + 1 / 3 + 1 / 4) / 5, 4)
    assert m["empty_when_irrelevant"] == 0.5
    assert m["positives_empty"] == 0  # the failed search does not count as "nothing found"
    assert m["depth"] == RETRIEVAL_DEPTH and m["latency_p95_ms"] >= m["latency_p50_ms"] >= 0
    assert m["by_tag"]["identifier"]["hit@3"] == 0.0 and m["by_tag"]["en"]["mrr"] == round((1 + 1 / 4) / 2, 4)
    assert m["by_tag"]["negative"]["empty_when_irrelevant"] == 0.5 and m["by_tag"]["negative"]["mrr"] is None


def test_a_failed_search_never_passes_an_unrelated_question():
    knowledge = ScriptedKnowledge(fail_on={"q"})
    report = RetrievalEval(knowledge, DOCS).run([retrieval_case("n", "q", none_relevant=True)])
    assert report.pass_rate == 0 and retrieval_metrics(report.results)["empty_when_irrelevant"] == 0.0


def test_report_json_carries_the_retrieval_block_and_results():
    _, report = scripted_run()
    data = json.loads(json.dumps(report.to_dict()))
    assert data["by_kind"] == {"retrieval": round(3 / 7, 4)}
    assert data["retrieval"]["hit@1"] == 0.2
    first = data["cases"][0]["attempts"][0]
    assert first["retrieved"] == [{"doc": "a", "score": 0.9}, {"doc": "b", "score": 0.2}]
    assert first["answer"].startswith("1. a (0.9000)")


def test_reports_without_retrieval_cases_stay_as_before():
    from orchestrator.evals.ditto_eval import Attempt
    case = Case(id="h", kind="healing", log="Killed")
    data = EvalReport([CaseResult(case, [Attempt(True, 0.1)])]).to_dict()
    assert "retrieval" not in data and "retrieved" not in data["cases"][0]["attempts"][0]


def test_variants_choose_the_rerank_stage_and_a_silent_fallback_is_an_error():
    assert ditto_eval.RAG_VARIANTS == {"fused": {"rerank": False}, "rerank": {"rerank": True}}

    class Reranked(ScriptedKnowledge):
        def search(self, query, limit=3, score_threshold=None, strict=False, **options):
            hits = super().search(query, limit, score_threshold, strict, **options)
            for hit in hits[:1 if query == "fallback" else None]:
                hit.metadata["reranker"] = "fake/reranker"
            return hits

    knowledge = Reranked({"good": [("a", 0.9)], "fallback": [("a", 0.9), ("b", 0.5)]})
    knowledge.last_error = "The reranking model x is not in /cache"
    ev = RetrievalEval(knowledge, DOCS)
    good = ev.run_case(retrieval_case("g", "good", relevant=["a"]), variant="rerank")
    fallback = ev.run_case(retrieval_case("f", "fallback", relevant=["a"]), variant="rerank")
    fused = ev.run_case(retrieval_case("f", "fallback", relevant=["a"]), variant="fused")

    assert [s["rerank"] for s in knowledge.searches] == [True, True, False]
    assert good.passed and fused.passed
    # The search kept the fused order because the reranker could not run: never graded as a reranked result.
    assert not fallback.passed and fallback.error == "The reranker did not run: The reranking model x is not in /cache"


def test_unknown_variant_and_wrong_kind_are_refused():
    ev = RetrievalEval(ScriptedKnowledge(), DOCS)
    with pytest.raises(ValueError, match="variant"):
        ev.run([retrieval_case("x", "q", relevant=["a"])], variant="nope")
    with pytest.raises(ValueError, match="DittoEval"):
        ev.run_case(Case(id="h", kind="healing", log="Killed"))


def test_ingest_uses_opaque_sources_and_reports_failures():
    knowledge = ScriptedKnowledge()
    RetrievalEval(knowledge, DOCS).ingest()
    sources = [source for source, *_ in knowledge.ingested]
    assert sources == [corpus_source(d.id) for d in DOCS] and len(set(sources)) == len(DOCS)
    # The source is embedded with each chunk: it must not give the search the words of the document id.
    assert re.fullmatch(r"eval:[0-9a-f]{12}", corpus_source("llama-cpp-cuda-flags"))
    assert knowledge.ingested[0][2] == {"title": "a", "lang": "en", "doc_id": "a"} and knowledge.ingested[0][3]
    # The corpus is indexed as outside text, like a crawled page: the figures include what that cleaning costs.
    assert knowledge.outside == [True] * len(DOCS)
    with pytest.raises(RetrievalEvalError, match="embedding model missing"):
        RetrievalEval(ScriptedKnowledge(ingest_ok=False), DOCS).ingest()


# --- the real knowledge base, in a temporary folder, with fake embedding models ------------------------------------

class HashedEmbedder:
    """Deterministic bag of hashed words; enough for lexical matches, no model download."""
    model_id = "fake/hashed-words"
    dim = 64

    def __init__(self):
        self.spec = SimpleNamespace(chunk_tokens=200)

    def _vector(self, text):
        vector = [0.0] * self.dim
        vector[-1] = 0.05
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % (self.dim - 1)] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector]

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)

    def count_tokens(self, text):
        return len(text.split())


class TermSparse:
    model_id = "fake/bm25"

    @staticmethod
    def _vec(text, query=False):
        terms = Counter(re.findall(r"\w+", text.lower()))
        return SparseVec([zlib.crc32(t.encode()) & 0x7FFFFFFF for t in terms],
                         [1.0 if query else float(c) for c in terms.values()])

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text, query=True)


@pytest.fixture
def fake_models(monkeypatch):
    import orchestrator.Core.rag_manager as rm
    seen = {}

    def dense(spec, cache_dir, offline, **_):
        seen.update(spec=spec.model_id, cache_dir=cache_dir, offline=offline())
        return HashedEmbedder()

    monkeypatch.setattr(rm, "create_dense_embedder", dense)
    monkeypatch.setattr(rm, "create_sparse_embedder", lambda cache_dir, offline, **_: TermSparse())
    return seen


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def refuse(self, address):
        attempts.append(address)
        raise OSError("network disabled in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


def framework_stand_in(tmp_path, **settings):
    config = FakeConfig(qdrant_url="https://qdrant.example:6333", models_dir="models", **settings)
    return SimpleNamespace(config=config, info=SimpleNamespace(installation_path=tmp_path),
                           resolve_path=lambda p: tmp_path / p)


def test_temporary_knowledge_base_is_private_and_removed(tmp_path, monkeypatch, fake_models, no_network):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    framework = framework_stand_in(tmp_path, rag_embedding_model="intfloat/multilingual-e5-small",
                                   offline_mode=True)
    docs = load_corpus(CORPUS)
    with temporary_knowledge_base(framework) as knowledge:
        storage = knowledge.storage_dir
        ev = RetrievalEval(knowledge, docs)
        ev.ingest()
        status = knowledge.get_status()
        assert status["mode"] == "embedded" and status["vector_count"] >= len(docs)  # the server URL is ignored
        quote = retrieval_case("q", "Invalid RKLLM model version, please use the same version of toolkit and runtime",
                               relevant=["rkllm-version-mismatch"], within=1)
        assert ev.run_case(quote).passed
        report = ev.run(load_cases(RETRIEVAL_CASES))
        assert storage.is_dir() and not storage.is_relative_to(tmp_path / "state")
    assert not storage.exists()
    assert not (tmp_path / "state" / "qdrant").exists() and not (tmp_path / "state" / "qdrant_edge").exists()
    assert not (tmp_path / "state" / "qdrant_edge.lock").exists()
    assert no_network == []
    # The configured model and the framework's models folder (cached there, offline afterwards).
    assert fake_models == {"spec": "intfloat/multilingual-e5-small", "cache_dir": tmp_path / "models" / "embeddings",
                           "offline": True}
    assert len(report.results) == len(load_cases(RETRIEVAL_CASES))
    assert all(a.retrieved for r in report.results for a in r.attempts)  # without a relevance floor


# --- the CLI ------------------------------------------------------------------------------------------------------

def run_cli(monkeypatch, framework, *args):
    from click.testing import CliRunner
    from orchestrator import cli

    def require(self):
        self.framework_manager = framework
        return self

    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    return CliRunner().invoke(cli.cli, ["-q", "ditto", "eval", *args])


def test_cli_retrieval_needs_no_ai_model_and_writes_metrics(tmp_path, monkeypatch, fake_models, no_network):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    out = tmp_path / "report.json"
    # The stand-in has no AI client at all: a retrieval run must not touch one.
    result = run_cli(monkeypatch, framework_stand_in(tmp_path), "--kind", "retrieval", "--json", str(out))
    assert result.exit_code == 0, result.output
    assert "Retrieval" in result.output and "MRR" in result.output
    (run,) = json.loads(out.read_text())["runs"]
    assert run["setup"]["label"] == "retrieval fused" and run["setup"]["variant"] == "fused"
    assert run["setup"]["model"] == HashedEmbedder.model_id
    assert run["setup"]["corpus_documents"] == len(load_corpus(CORPUS))
    assert run["setup"]["cases"] == [c.id for c in load_cases(RETRIEVAL_CASES)]
    assert len(run["setup"]["corpus_sha256"]) == 64 and len(run["setup"]["cases_sha256"]) == 64
    assert run["setup"]["index_seconds"] >= 0 and run["setup"]["rss_mb"] > 0
    assert set(run["retrieval"]) >= {"hit@1", "hit@3", "hit@k", "mrr", "empty_when_irrelevant", "latency_p50_ms",
                                     "by_tag"}
    assert run["by_kind"].keys() == {"retrieval"}
    assert no_network == []


def test_cli_case_filter_finds_retrieval_cases_without_kind(tmp_path, monkeypatch, fake_models):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    out = tmp_path / "report.json"
    result = run_cli(monkeypatch, framework_stand_in(tmp_path), "--case", "retr-id-ggml-cuda", "--json", str(out))
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["runs"][0]["setup"]["cases"] == ["retr-id-ggml-cuda"]


def test_cli_offline_without_the_model_is_a_clear_error(tmp_path, monkeypatch, no_network):
    pytest.importorskip("fastembed")
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    framework = framework_stand_in(tmp_path, offline_mode=True, rag_embedding_model="intfloat/multilingual-e5-small")
    result = run_cli(monkeypatch, framework, "--kind", "retrieval")
    assert result.exit_code == 1
    assert "offline mode is on" in result.output
    assert no_network == []


def test_cli_refuses_a_corpus_that_misses_expected_documents(tmp_path, monkeypatch, fake_models):
    cases = tmp_path / "cases.yml"
    cases.write_text(json.dumps({"cases": [{"id": "x", "kind": "retrieval", "query": "q",
                                            "expect": {"relevant": ["missing-doc"]}}]}))
    write_doc(tmp_path / "corpus", "other.md", "---\ntitle: T\nlang: en\n---\ntext\n")
    result = run_cli(monkeypatch, framework_stand_in(tmp_path), "--cases", str(cases))
    assert result.exit_code == 2 and "missing-doc" in result.output


class WordReranker:
    """Relevance = share of the query's words in the text (no model)."""
    model_id = "fake/word-reranker"
    spec = SimpleNamespace(min_score=0.5)

    def scores(self, query, documents):
        words = set(re.findall(r"\w{4,}", query.lower()))
        return [len(words & set(re.findall(r"\w+", d.lower()))) / max(len(words), 1) for d in documents]


def test_cli_rerank_variant_reports_the_reranker(tmp_path, monkeypatch, fake_models, no_network):
    import orchestrator.Core.rag_manager as rm

    created = []
    monkeypatch.setattr(rm, "create_reranker", lambda spec, cache_dir, offline, **_: created.append(spec.model_id)
                        or WordReranker())
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    out = tmp_path / "report.json"
    framework = framework_stand_in(tmp_path, rag_reranker="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    result = run_cli(monkeypatch, framework, "--kind", "retrieval", "--rag-variant", "fused", "--rag-variant",
                     "rerank", "--json", str(out))

    assert result.exit_code == 0, result.output
    fused, reranked = json.loads(out.read_text())["runs"]
    assert fused["setup"]["variant"] == "fused" and fused["setup"]["reranker"] is None
    assert reranked["setup"]["label"] == "retrieval rerank"
    assert reranked["setup"]["model"] == f"{HashedEmbedder.model_id} + fake/word-reranker"
    assert reranked["setup"]["reranker"] == {"model": "fake/word-reranker", "min_score": 0.5}
    assert created == ["cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"]
    # The floor gives unrelated questions no excerpt; reranked scores are relevances.
    assert reranked["retrieval"]["empty_when_irrelevant"] == 1.0
    scores = [h["score"] for c in reranked["cases"] for h in c["attempts"][0].get("retrieved", [])]
    assert scores and all(0.5 <= x <= 1 for x in scores)
    assert no_network == []


def test_cli_rerank_variant_without_a_reranker_is_refused(tmp_path, monkeypatch, fake_models):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    result = run_cli(monkeypatch, framework_stand_in(tmp_path, rag_reranker="off"), "--kind", "retrieval",
                     "--rag-variant", "rerank")
    assert result.exit_code == 2 and "needs a reranker" in result.output and "turned off" in result.output


def test_cli_variant_choices_match_the_runner():
    from orchestrator import cli
    option = next(p for p in cli.ditto_eval.params if p.name == "rag_variants")
    assert list(option.type.choices) == list(ditto_eval.RAG_VARIANTS)
    kind = next(p for p in cli.ditto_eval.params if p.name == "kinds")
    assert list(kind.type.choices) == list(ditto_eval.KINDS)


# --- hidden-text carriers (recon P1): evals/retrieval/carriers.yml --------------------------------------------------

CARRIER_CASES = RETRIEVAL_CASES.parent / "carriers.yml"


def shipped_carriers():
    return ditto_eval.load_carriers(ditto_eval.carriers_dir(CARRIER_CASES))


def test_shipped_carrier_pages_and_cases_are_valid_and_consistent():
    from orchestrator.rag.html_text import HIDDEN_KINDS, REMOVED_KINDS
    cases = load_cases(CARRIER_CASES)
    carriers = shipped_carriers()
    check_retrieval_cases(cases, [*load_corpus(corpus_dir(CARRIER_CASES)), *carriers])
    assert corpus_dir(CARRIER_CASES) == CORPUS   # the corpus of cases.yml around the carrier pages
    assert ditto_eval.carriers_dir(RETRIEVAL_CASES) is None   # the main cases stay as they were
    # Every way of hiding the knowledge base counts has a page; each page names a kind it counts (or none, for an
    # HTML comment, which the converter never reads).
    assert {c.kind for c in carriers} - {""} <= set(REMOVED_KINDS)
    assert set(HIDDEN_KINDS) | {"invisible_characters"} <= {c.kind for c in carriers}
    assert {"tab-panel", "html-comment"} <= {c.id for c in carriers}
    # One case per page, each asking for its visible text without quoting it.
    assert sorted(d for c in cases for d in c.expect.relevant) == sorted(c.id for c in carriers)
    visible = {c.id: c.visible.lower() for c in carriers}
    assert all("carrier" in c.tags and c.query.lower().rstrip("?.") not in visible[c.expect.relevant[0]]
               for c in cases)
    assert all(c.payload in c.html and c.payload != c.visible for c in carriers)


def test_carrier_pages_need_their_meta_tags(tmp_path):
    (tmp_path / "x.html").write_text('<html><head><meta name="carrier-kind" content="aria_hidden"></head></html>',
                                     encoding="utf-8")
    with pytest.raises(ValueError, match="x.html"):
        ditto_eval.load_carriers(tmp_path)
    with pytest.raises(ValueError, match="no carrier pages"):
        ditto_eval.load_carriers(tmp_path / "missing")
    assert ditto_eval.load_carriers(None) == []


def carrier_docs(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    framework = framework_stand_in(tmp_path, rag_embedding_model="intfloat/multilingual-e5-small")
    return framework, [*load_corpus(CORPUS), *shipped_carriers()]


def test_carrier_pages_are_stored_without_their_payload_and_found_by_their_visible_text(
        tmp_path, monkeypatch, fake_models, no_network):
    framework, docs = carrier_docs(tmp_path, monkeypatch)
    with temporary_knowledge_base(framework) as knowledge:
        ev = RetrievalEval(knowledge, docs)
        ev.ingest()
        rows = {row["id"]: row for row in ev.carrier_checks}
        report = ev.run(load_cases(CARRIER_CASES))
    assert len(rows) == len(shipped_carriers())
    assert not any(row["payload_stored"] for row in rows.values())
    assert all(row["visible_stored"] for row in rows.values())
    assert rows["display-none"]["removed"] == {"display_none": 1}
    assert rows["invisible-characters"]["removed"]["invisible_characters"] > 20   # zero-width and tag characters
    assert rows["html-comment"]["removed"] == {}   # the converter never reads a comment: nothing to count
    assert rows["tab-panel"]["removed"] == {"aria_hidden": 1}   # the inactive tab panel stays
    assert report.pass_rate == 1.0, [r.first_problem() for r in report.results if r.pass_rate < 1]
    assert no_network == []


def test_a_carrier_whose_payload_reaches_the_knowledge_base_stops_the_evaluation(
        tmp_path, monkeypatch, fake_models, no_network):
    import orchestrator.rag.html_text as html_text
    framework, docs = carrier_docs(tmp_path, monkeypatch)
    monkeypatch.setattr(html_text, "hidden_kind", lambda name, attrs, tabs=False: None)   # a broken filter
    with temporary_knowledge_base(framework) as knowledge:
        with pytest.raises(RetrievalEvalError, match="Hidden text check failed") as failed:
            RetrievalEval(knowledge, docs).ingest()
    message = str(failed.value)
    assert "display-none: its hidden payload reached the stored excerpts; display_none was not counted" in message
    assert "invisible-characters" not in message   # the text cleaning still works


def test_cli_carrier_cases_list_every_page_in_the_report(tmp_path, monkeypatch, fake_models, no_network):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    out = tmp_path / "report.json"
    result = run_cli(monkeypatch, framework_stand_in(tmp_path), "--kind", "retrieval", "--cases", str(CARRIER_CASES),
                     "--json", str(out), "--min-pass", "1.0")
    assert result.exit_code == 0, result.output
    (run,) = json.loads(out.read_text())["runs"]
    assert run["setup"]["carrier_documents"] == len(shipped_carriers()) and len(run["setup"]["carriers_sha256"]) == 64
    assert run["setup"]["corpus_documents"] == len(load_corpus(CORPUS))
    assert sorted(row["id"] for row in run["setup"]["carriers"]) == sorted(c.id for c in shipped_carriers())
    assert not any(row["payload_stored"] for row in run["setup"]["carriers"])
    assert run["retrieval"]["by_tag"]["carrier"]["hit@k"] == 1.0


def test_cli_fails_when_a_carrier_leaks(tmp_path, monkeypatch, fake_models, no_network):
    import orchestrator.rag.html_text as html_text
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(html_text, "hidden_kind", lambda name, attrs, tabs=False: None)
    result = run_cli(monkeypatch, framework_stand_in(tmp_path), "--kind", "retrieval", "--cases", str(CARRIER_CASES))
    assert result.exit_code == 1 and "Hidden text check failed" in result.output
