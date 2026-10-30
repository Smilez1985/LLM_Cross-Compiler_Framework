"""Your own crawls: every page belongs to a topic from the start (the person's, or one derived from the site; an AI's
crawl takes the derived one), and a re-crawl of the same site replaces its pages and removes the excerpts of pages that
are gone, only when it read the whole site and only where no pack still holds them. A partial or failed crawl removes
nothing. A stand-in crawler serves the pages (documentation addresses only)."""

from types import SimpleNamespace

import pytest

from orchestrator.Core import rag_manager as rm
from test_rag import make_rag  # noqa: F401 - the fixture

ROOT = "https://docs.example.com/"
TEXT = {"https://docs.example.com/": "Start page of the guide about the NPU.",
        "https://docs.example.com/a": "Page a: install the runtime on the board.",
        "https://docs.example.com/b": "Page b: quantize the model before converting it."}


class SiteCrawler:
    """Serves SiteCrawler.pages; `complete`, `failed` and `stopped` as the real crawler reports them."""
    pages = list(TEXT)
    complete = True
    failed: list = []

    def __init__(self, framework, options=None, **kwargs):
        self.skipped, self.failed, self.stopped = [], list(type(self).failed), False
        self.visited = set()

    def iter_crawl(self, url, stop=None, progress=None):
        for source in type(self).pages:
            self.visited.add(source)
            yield SimpleNamespace(page_content=TEXT[source], metadata={"source": source, "title": source},
                                  hidden_removed=None)
        self.complete = type(self).complete


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(rm, "CrawlerManager", SiteCrawler, raising=False)
    monkeypatch.setattr(SiteCrawler, "pages", list(TEXT))
    monkeypatch.setattr(SiteCrawler, "complete", True)
    monkeypatch.setattr(SiteCrawler, "failed", [])
    return SiteCrawler


def sources(rag):
    return {s["source"]: s for s in rag.list_sources()}


def test_own_crawled_pages_carry_a_topic_from_the_start(make_rag, site):  # noqa: F811
    rag = make_rag()
    assert rag.ingest_url(ROOT, {})["outcome"] == "success"
    assert {s["topic"] for s in sources(rag).values()} == {"docs.example.com"}, "derived from the site"
    assert rag.ingest_url(ROOT, {}, topic="npu-guide")["outcome"] == "success"
    assert {s["topic"] for s in sources(rag).values()} == {"npu-guide"}, "the person's topic replaces it"
    assert rm.site_topic("https://www.example.org/docs") == "example.org"


def test_a_complete_re_crawl_removes_the_pages_that_are_gone(make_rag, site):  # noqa: F811
    rag = make_rag()
    rag.ingest_url(ROOT, {})
    assert set(sources(rag)) == set(TEXT)
    site.pages = [ROOT, "https://docs.example.com/a"]
    result = rag.ingest_url(ROOT, {})
    assert result["removed"] == ["https://docs.example.com/b"] and "1 page(s) no longer on the site" in \
        result["message"]
    assert set(sources(rag)) == {ROOT, "https://docs.example.com/a"}


@pytest.mark.parametrize("how", ["page_limit", "failed_page"])
def test_a_partial_re_crawl_removes_nothing(make_rag, site, how):  # noqa: F811
    rag = make_rag()
    rag.ingest_url(ROOT, {})
    site.pages = [ROOT]
    if how == "page_limit":
        site.complete = False     # the page limit or the request budget ended it: the rest was not read
    else:
        from orchestrator.Core.crawler_manager import SkippedPage
        site.failed = [SkippedPage(url="https://docs.example.com/b", reason="HTTP status 503")]
    result = rag.ingest_url(ROOT, {})
    assert result["removed"] == [] and set(sources(rag)) == set(TEXT)


def test_a_re_crawl_never_removes_what_a_pack_still_holds(make_rag, site):  # noqa: F811
    rag = make_rag()
    rag.ingest_url(ROOT, {})
    gone = "https://docs.example.com/b"
    assert rag.ingest_document(gone, TEXT[gone], {"origin_pack": "pack-a", "origin_sha256": "a" * 64,
                                                  "topic": "npu"})
    site.pages = [ROOT, "https://docs.example.com/a"]
    result = rag.ingest_url(ROOT, {})
    assert result["removed"] == [] and gone in sources(rag)
    held = rag._with_store(lambda store: list(store.source_records(gone)))
    assert held and all(p.payload["holders"] == ["pack-a"] for p in held)


def test_an_ai_crawl_takes_the_derived_topic(make_rag, site, tmp_path):  # noqa: F811
    """crawl_jobs passes no topic: the pages belong to the site's topic."""
    import inspect
    from orchestrator.Core import crawl_jobs
    assert "topic=" not in inspect.getsource(crawl_jobs.CrawlJobService._crawl), "an AI never names the topic"
    rag = make_rag()
    rag.ingest_url(ROOT, {})
    assert {s["topic"] for s in sources(rag).values()} == {"docs.example.com"}
