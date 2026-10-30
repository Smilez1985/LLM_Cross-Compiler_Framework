"""Review of the re-crawl: a crawl whose depth limit or language filter left links unread did not read the whole site,
so a re-crawl with a smaller depth must not remove the deeper pages an earlier crawl stored (they are not gone, only
not reached). The real crawler on a stand-in site; the real knowledge base (Qdrant Edge, the fake embedders of
test_rag)."""

from types import SimpleNamespace

import pytest

from orchestrator.Core.config_manager import ConfigManager
from test_rag import make_rag  # noqa: F401 - the fixture

SITE = "http://93.184.215.14/"
TEXT = ("This guide explains how to install the software development kit on your single board computer "
        "and how to convert a model for the neural processing unit. ") * 3


def _crawler(tmp_path, monkeypatch, pages, **options):
    from orchestrator.Core.crawler_manager import CrawlerManager

    def send(prepared, **kwargs):
        body = pages.get(prepared.url)
        return SimpleNamespace(status_code=200 if body else 404, headers={"Content-Type": "text/html"},
                               text=body or "", content=(body or "").encode(), is_redirect=False)

    crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path / "configs")),
                             {"request_delay": 0, **options}, _skip_robots_txt_for_tests=True)
    monkeypatch.setattr(crawler.session, "send", send)
    return crawler


def _page(name, links=()):
    return (f"<html><body><h1>{name}</h1>" + "".join(f'<a href="/{link}">{link}</a>' for link in links)
            + f"<p>{TEXT}</p></body></html>")


@pytest.mark.parametrize("depth, complete", [(1, False), (2, True)])
def test_a_crawl_cut_by_its_depth_limit_did_not_read_the_whole_site(tmp_path, monkeypatch, depth, complete):
    pages = {SITE: _page("root", ["a"]), f"{SITE}a": _page("a", ["b"]), f"{SITE}b": _page("b")}
    crawler = _crawler(tmp_path, monkeypatch, pages, max_depth=depth)
    docs = crawler.crawl(SITE)
    assert len(docs) == depth + 1
    assert crawler.complete is complete, "links left unread at the depth limit: the rest of the site was not read"


def test_a_page_left_out_for_its_language_leaves_its_links_unread(tmp_path, monkeypatch):
    german = ("Diese Anleitung erklärt, wie Sie das Entwicklungspaket auf Ihrem Einplatinencomputer installieren "
              "und ein Modell für die neuronale Recheneinheit umwandeln. ") * 3
    pages = {SITE: _page("root", ["de"]),
             f"{SITE}de": f'<html><body><a href="/deep">deep</a><p>{german}</p></body></html>',
             f"{SITE}deep": _page("deep")}
    crawler = _crawler(tmp_path, monkeypatch, pages, languages="en")
    crawler.crawl(SITE)
    assert crawler.complete is False


def test_a_shallower_re_crawl_keeps_the_deeper_pages(make_rag, tmp_path, monkeypatch):  # noqa: F811
    """End to end through RAGManager.ingest_url with the real crawler: depth 2, then depth 1 of the same site."""
    from orchestrator.Core import rag_manager as rm
    from orchestrator.Core.crawler_manager import CrawlerManager
    pages = {SITE: _page("root", ["a"]), f"{SITE}a": _page("a", ["b"]), f"{SITE}b": _page("b")}

    def send(prepared, **kwargs):
        body = pages.get(prepared.url)
        return SimpleNamespace(status_code=200 if body else 404, headers={"Content-Type": "text/html"},
                               text=body or "", content=(body or "").encode(), is_redirect=False)

    class Crawler(CrawlerManager):
        def __init__(self, framework, options=None, **kwargs):
            super().__init__(SimpleNamespace(config=ConfigManager(tmp_path / "configs")),
                             {"request_delay": 0, **(options or {})}, _skip_robots_txt_for_tests=True)
            self.session.send = send

    monkeypatch.setattr(rm, "CrawlerManager", Crawler, raising=False)
    rag = make_rag()
    assert rag.ingest_url(SITE, {"max_depth": 2})["outcome"] == "success"
    assert {s["source"] for s in rag.list_sources()} == set(pages)
    result = rag.ingest_url(SITE, {"max_depth": 1})
    assert result["removed"] == [], "page b is still on the site; the shallower crawl only did not reach it"
    assert {s["source"] for s in rag.list_sources()} == set(pages)
