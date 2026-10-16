"""Copyright and licences of crawled pages (DECISIONS round 8, Q-1 and Q-1'): what a crawl records per page, what
the knowledge base shows, and what a knowledge pack or a target's knowledge snapshot may carry. Fake pages only, no
network: the crawler's session is replaced, and the knowledge base is a stand-in that returns stored records."""

import json
import zipfile
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from orchestrator.rag import licences
from orchestrator.rag.licences import FULL_TEXT, LINK_AND_FACTS
from test_knowledge_tools import make_rag  # noqa: F401  (make_rag: the fixture)

SITE = "http://93.184.215.14"   # the crawl tests: an address literal, so nothing is looked up
DOCS = "https://docs.example"   # the export tests: the privacy scrub keeps a host name, not an address
EN = " This guide explains how to install the software development kit on your single board computer. "
MIT_TEXT = ("MIT License\n\nCopyright (c) 2024 Acme\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy of this software")


def soup(html):
    return BeautifulSoup(html, "html.parser")


# --- what a page shows ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("html, headers, expected", [
    ('<html><head><link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/"></head><body>x</body>'
     '</html>', None, ("CC-BY-SA-4.0", "rel=license link")),
    ('<p>x</p><footer><a rel="license" href="/terms">Terms</a></footer>', None, ("other", "rel=license link")),
    ("<p>x</p>", {"Link": '<https://www.apache.org/licenses/LICENSE-2.0>; rel="license"'},
     ("Apache-2.0", "Link header")),
    ('<head><meta name="dcterms.license" content="CC0 1.0"></head><p>x</p>', None, ("CC0-1.0", "licence metadata")),
    ('<head><script type="application/ld+json">{"@type": "TechArticle", "license": "https://opensource.org/licenses/'
     'MIT"}</script></head><p>x</p>', None, ("MIT", "licence metadata")),
    ('<p>x</p><footer>Text under <a href="https://creativecommons.org/licenses/by/4.0/deed.de">CC BY 4.0</a>'
     '</footer>', None, ("CC-BY-4.0", "Creative Commons link")),
])
def test_a_recognisable_licence_is_recorded_with_where_it_was_found(html, headers, expected):
    meta = licences.from_html(soup(html), f"{SITE}/guide", headers)
    assert (meta.get("licence"), meta.get("licence_found")) == expected


def test_an_unrecognised_licence_keeps_its_words_as_plain_text():
    meta = licences.from_html(soup('<head><meta name="license" content="Internal use\x07 only"></head><p>x</p>'),
                              f"{SITE}/a")
    assert meta["licence"] == "other" and meta["licence_text"] == "Internal use only"


def test_the_copyright_notice_in_the_footer_is_found_before_the_footer_is_dropped():
    page = soup("<main><p>The copyright law of a country is long.</p><p>(c) is the third option.</p></main>"
                "<footer>Copyright 2023 Rockchip Electronics Co., Ltd. All rights reserved.</footer>")
    meta = licences.from_html(page, f"{SITE}/sdk")
    assert meta == {"copyright": "Copyright 2023 Rockchip Electronics Co., Ltd. All rights reserved."}
    assert licences.from_html(soup("<p>(c) is the third option; the copyright law is long.</p>"), SITE) == {}


def test_several_licences_choose_the_cautious_one():
    page = soup('<a href="https://creativecommons.org/licenses/by/4.0/">text</a>'
                '<a href="https://creativecommons.org/licenses/by-nc-nd/4.0/">images</a>')
    assert licences.from_html(page, SITE)["licence"] == "CC-BY-NC-ND-4.0"


@pytest.mark.parametrize("text, expected", [
    ("Apache License\nVersion 2.0, January 2004", "Apache-2.0"), (MIT_TEXT, "MIT"),
    ("GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991", "GPL-2.0"),
    ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007", "GPL-3.0"),
    ("BSD 3-Clause License\n\nRedistribution and use in source and binary forms", "BSD-3-Clause"),
    ("This is free and unencumbered software released into the public domain.", "Unlicense"),
    ("Mozilla Public License Version 2.0", "MPL-2.0"), ("CC BY-NC 4.0", "CC-BY-NC-4.0"),
    ("Creative Commons Attribution-ShareAlike 4.0 International", "CC-BY-SA-4.0"), ("All rights reserved", None),
])
def test_licence_names_and_licence_files(text, expected):
    assert licences.licence_from_text(text) == expected


def test_github_code_counts_only_the_repository_licence_file_and_never_the_site_footer():
    footer = "<footer>© 2026 GitHub, Inc.</footer>"
    code = licences.from_html(soup(f"<pre>int main() {{}}</pre>{footer}"),
                              "https://github.com/Acme/Tool/blob/main/src/main.c")
    assert code == {"code_repo": "github.com/acme/tool"}
    licence_file = licences.from_html(soup(f"<pre>{MIT_TEXT}</pre>{footer}"),
                                      "https://github.com/acme/tool/blob/main/LICENSE")
    assert licence_file == {"code_repo": "github.com/acme/tool", "licence_file": True, "licence": "MIT",
                            "licence_found": "licence file of the repository"}
    assert licences.is_licence_file("https://raw.githubusercontent.com/acme/tool/main/COPYING.txt")
    assert not licences.is_licence_file("https://github.com/acme/tool/blob/main/docs/LICENSE")
    assert licences.code_repo("https://github.com/features/copilot") == ""


def test_a_pdf_licence_in_its_metadata_and_a_notice_in_its_text():
    raw = b"%PDF-1.7 <cc:license rdf:resource='https://creativecommons.org/licenses/by/3.0/'/>"
    meta = licences.from_pdf(raw, "Manual\n© 2022 Example GmbH\n", f"{SITE}/manual.pdf")
    assert meta["licence"] == "CC-BY-3.0" and meta["copyright"] == "© 2022 Example GmbH"


# --- what a pack may carry ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("meta, shared_as, state", [
    ({"licence": "CC-BY-4.0", "copyright": "© 2024 Example"}, FULL_TEXT, "open"),
    ({"licence": "CC0-1.0"}, FULL_TEXT, "open"),
    ({"licence": "MIT"}, FULL_TEXT, "open"),
    ({"licence": "CC-BY-NC-4.0"}, LINK_AND_FACTS, "restricted"),
    ({"licence": "GPL-3.0"}, LINK_AND_FACTS, "restricted"),
    ({"licence": "other", "licence_text": "Internal"}, LINK_AND_FACTS, "restricted"),
    ({"copyright": "Copyright 2023 Rockchip. All rights reserved."}, LINK_AND_FACTS, "copyright"),
    ({"code_repo": "github.com/acme/tool"}, LINK_AND_FACTS, "code_without_licence"),
    ({}, LINK_AND_FACTS, "unknown"),
])
def test_full_text_only_where_the_licence_allows_passing_it_on(meta, shared_as, state):
    plan = licences.share_plan(meta)
    assert (plan.shared_as, plan.state) == (shared_as, state) and plan.reason


def test_code_takes_the_licence_file_only_from_the_same_crawl():
    metas = [{"code_repo": "github.com/acme/tool", "licence_file": True, "licence": "MIT", "root_url": "https://g/a"},
             {"code_repo": "github.com/acme/tool", "root_url": "https://g/a"},
             {"code_repo": "github.com/acme/tool", "root_url": "https://g/other-crawl"}]
    repos = licences.repo_licences(metas)
    assert [licences.plan_for(m, repos).shared_as for m in metas] == [FULL_TEXT, FULL_TEXT, LINK_AND_FACTS]


def test_short_facts_are_the_link_title_sections_and_terms_never_the_prose():
    plan = licences.share_plan({})
    facts = licences.short_facts(f"{SITE}/npu", "NPU guide", ["Install", "Install", "Convert"],
                                 ["Flash the image, then set -DGGML_CUDA=ON and load librkllmrt.so with "
                                  "rknn-toolkit2 2.3.2 on RK3588. Ask me at me@example.org or https://x.example."],
                                 plan)
    assert facts.splitlines()[:4] == [f"Link: {SITE}/npu", "Title: NPU guide", "Sections: Install; Convert",
                                      "Terms: -DGGML_CUDA=ON, librkllmrt.so, rknn-toolkit2, 2.3.2, RK3588"]
    assert "Flash the image" not in facts and "example.org" not in facts and plan.reason in facts


# --- the crawl records it per page ----------------------------------------------------------------------------------

def response(html, headers=None, content_type="text/html"):
    from requests.structures import CaseInsensitiveDict
    return SimpleNamespace(status_code=200, text=html, content=html.encode(), is_redirect=False,
                           headers=CaseInsensitiveDict({"Content-Type": content_type, **(headers or {})}))


def crawl(tmp_path, monkeypatch, pages):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.crawler_manager import CrawlerManager
    crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)), {"request_delay": 0, "max_depth": 0},
                             _skip_robots_txt_for_tests=True)
    missing = SimpleNamespace(status_code=404, headers={}, text="", content=b"", is_redirect=False)
    monkeypatch.setattr(crawler.session, "send", lambda prepared, **kw: pages.get(prepared.url) or missing)
    return {d.metadata["source"]: d for d in crawler.crawl(f"{SITE}/")}


def test_a_crawled_page_keeps_its_licence_and_the_notice_from_its_footer(tmp_path, monkeypatch):
    page = (f"<html><head><title>SDK</title></head><body><main><h1>SDK</h1><p>{EN}</p></main><footer>Text under "
            '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. © 2024 Example Ltd.</footer>'
            "</body></html>")
    docs = crawl(tmp_path, monkeypatch, {f"{SITE}/": response(page)})
    meta = docs[f"{SITE}/"].metadata
    assert meta["licence"] == "CC-BY-4.0" and meta["copyright"] == "Text under CC BY 4.0. © 2024 Example Ltd."
    assert "© 2024" not in docs[f"{SITE}/"].page_content, "the footer is still dropped from the stored text"


def test_a_crawled_page_reads_the_licence_link_header(tmp_path, monkeypatch):
    page = f"<html><body><p>{EN}</p></body></html>"
    link = {"Link": '<https://spdx.org/licenses/MIT.html>; rel="license"'}
    docs = crawl(tmp_path, monkeypatch, {f"{SITE}/": response(page, link)})
    assert docs[f"{SITE}/"].metadata["licence"] == "MIT"


# --- what a pack carries --------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    from test_zoo import World
    return World(tmp_path)


def record(source, content, **meta):
    return {"source": source, "content": content, "metadata": meta}


def exported(result):
    with zipfile.ZipFile(result.path) as zf:
        return json.loads(zf.read("knowledge.json"))


def test_a_knowledge_pack_carries_full_text_only_where_the_licence_allows_it(world):
    repo = "https://github.com/acme/tool"
    world.rag.records = [
        record(f"{DOCS}/open", "Open text about rknn-toolkit2 2.3.2.", title="Open", section="Intro",
               licence="CC-BY-4.0", copyright="© 2024 Example Ltd.", root_url=f"{DOCS}/"),
        record(f"{DOCS}/open", "Second part of the open text.", title="Open", section="Usage", licence="CC-BY-4.0",
               copyright="© 2024 Example Ltd.", root_url=f"{DOCS}/"),
        record(f"{DOCS}/sdk", "Our own words about -DGGML_CUDA=ON and RK3588.", title="SDK guide", section="Build",
               copyright="Copyright 2023 Rockchip. All rights reserved.", root_url=f"{DOCS}/"),
        record(f"{DOCS}/nc", "Text for non-commercial use.", title="NC", licence="CC-BY-NC-4.0", root_url=f"{DOCS}/"),
        record(f"{DOCS}/plain", "A page that names no licence.", title="Plain", root_url=f"{DOCS}/"),
        record(f"{repo}/blob/main/LICENSE", MIT_TEXT, code_repo="github.com/acme/tool", licence_file=True,
               licence="MIT", root_url=repo),
        record(f"{repo}/blob/main/src/main.c", "int main(void) { return 0; }", code_repo="github.com/acme/tool",
               root_url=repo),
        record("https://github.com/other/code/blob/main/a.c", "int other;", code_repo="github.com/other/code",
               root_url="https://github.com/other/code"),
    ]
    result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    by_source = {}
    for entry in exported(result):
        by_source.setdefault(entry["source"], []).append(entry)
    open_page = by_source[f"{DOCS}/open"]
    assert [e["content"] for e in open_page] == ["Open text about rknn-toolkit2 2.3.2.",
                                                 "Second part of the open text."]
    assert all(e["metadata"]["shared_as"] == FULL_TEXT and "© 2024 Example Ltd." in e["metadata"]["attribution"]
               and "CC-BY-4.0" in e["metadata"]["attribution"] for e in open_page), "the attribution is kept"
    for source, state in ((f"{DOCS}/sdk", "copyright"), (f"{DOCS}/nc", "restricted"), (f"{DOCS}/plain", "unknown"),
                          ("https://github.com/other/code/blob/main/a.c", "code_without_licence")):
        (entry,) = by_source[source]
        assert entry["metadata"]["shared_as"] == LINK_AND_FACTS and entry["metadata"]["licence_state"] == state
        assert entry["content"].startswith(f"Link: {source}"), entry["content"]
    sdk = by_source[f"{DOCS}/sdk"][0]
    assert "Our own words" not in sdk["content"] and "-DGGML_CUDA=ON" in sdk["content"]
    assert sdk["metadata"]["copyright"] == "Copyright 2023 Rockchip. All rights reserved.", "the notice is kept"
    assert by_source[f"{repo}/blob/main/src/main.c"][0]["content"] == "int main(void) { return 0; }", \
        "the repository's licence file in the same crawl decides"
    assert [s.plan.shared_as for s in result.shares].count(FULL_TEXT) == 3
    summary = result.summary()
    assert "What the pack carries of each page, and why:" in summary
    assert f"- {DOCS}/sdk: link and short facts (a copyright notice and no licence" in summary
    assert any(n.startswith("3 page(s) with their full text") and "4 page(s) only as their address" in n
               for n in result.notes), result.notes


def test_a_target_snapshot_made_before_the_rule_is_shared_by_it(world):
    from test_zoo import make_target
    folder = make_target(world.root / "targets" / "own-board")
    (folder / "knowledge").mkdir()
    (folder / "knowledge" / "initial_knowledge.json").write_text(json.dumps([
        record(f"{DOCS}/a", "Full vendor manual text.", title="Manual"),
        record(f"{DOCS}/b", "Shareable text.", title="Open", licence="CC0-1.0")]), encoding="utf-8")
    result = world.cm.export_pack("target", "own-board", author="a")
    with zipfile.ZipFile(result.path) as zf:
        snapshot = json.loads(zf.read("knowledge/initial_knowledge.json"))
    assert [e["metadata"]["shared_as"] for e in snapshot] == [LINK_AND_FACTS, FULL_TEXT]
    assert "Full vendor manual text." not in json.dumps(snapshot) and snapshot[1]["content"] == "Shareable text."
    assert any("1 page(s) with their full text" in n for n in result.notes), result.notes
    assert f"- {DOCS}/a: link and short facts (no licence found" in result.summary(), "the person sees each page"
    assert "Full vendor manual text." in (folder / "knowledge" / "initial_knowledge.json").read_text(), \
        "the target folder itself is not changed"


def test_the_knowledge_base_lists_each_source_with_its_licence_and_what_a_pack_carries(make_rag):  # noqa: F811
    rag = make_rag()
    assert rag.ingest_document(f"{DOCS}/open", "# Open\n\nOpen text.", {"content_type": "html", "licence": "MIT",
                                                                        "root_url": f"{DOCS}/"})
    assert rag.ingest_document(f"{DOCS}/sdk", "# SDK\n\nVendor text.", {
        "content_type": "html", "copyright": "© 2023 Rockchip", "root_url": f"{DOCS}/"})
    assert rag.ingest_document("https://pack.example/p", "# Pack\n\nFrom a pack.", {
        "origin_pack": "kp-x", "licence": "<script>alert(1)</script>"})
    sources = {s["source"]: s for s in rag.list_sources()}
    assert (sources[f"{DOCS}/open"]["licence"], sources[f"{DOCS}/open"]["pack_share"]) == ("MIT", FULL_TEXT)
    sdk = sources[f"{DOCS}/sdk"]
    assert (sdk["licence_state"], sdk["pack_share"], sdk["copyright"]) == ("copyright", LINK_AND_FACTS,
                                                                            "© 2023 Rockchip")
    pack = sources["https://pack.example/p"]
    assert (pack["licence"], pack["pack_share"]) == ("other", "not_exported"), "a pack's own words never pass through"


def test_a_pack_page_never_names_the_licence_of_your_own_code(make_rag):  # noqa: F811
    rag = make_rag()
    repo, root = "github.com/acme/tool", "https://github.com/acme/tool"
    assert rag.ingest_document(f"{root}/blob/main/src/a.c", "int a;", {"code_repo": repo, "root_url": root})
    assert rag.ingest_document(f"{root}/blob/main/LICENSE", "MIT License", {
        "origin_pack": "kp-x", "code_repo": repo, "root_url": root, "licence_file": True, "licence": "MIT"})
    code = {s["source"]: s for s in rag.list_sources()}[f"{root}/blob/main/src/a.c"]
    assert (code["licence_state"], code["pack_share"]) == ("code_without_licence", LINK_AND_FACTS)


def test_an_ai_sees_the_licence_in_fixed_words_only(make_rag, tmp_path):  # noqa: F811
    from test_knowledge_tools import call, tools_for
    rag = make_rag()
    assert rag.ingest_document(f"{DOCS}/sdk", "# SDK\n\nVendor text.", {
        "content_type": "html", "root_url": f"{DOCS}/", "copyright": "© 2023 Vendor Secret Corp"})
    out = call(tools_for(tmp_path, rag), "list_knowledge_sources", {})
    (source,) = out.sources
    assert (source.licence, source.licence_state, source.pack_share) == ("", "copyright", LINK_AND_FACTS)
    assert "Vendor Secret Corp" not in out.model_dump_json(), "the notice is site text and never reaches an AI"


def test_the_person_sees_each_source_and_what_a_pack_carries_in_the_cli(monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    entries = [{"source": f"{DOCS}/open", "kind": "web", "licence": "MIT", "copyright": "",
                "pack_share": FULL_TEXT, "share_reason": "the licence MIT allows passing the text on"},
               {"source": f"{DOCS}/sdk", "kind": "web", "licence": "", "copyright": "[red]© 2023 Vendor[/red]",
                "pack_share": LINK_AND_FACTS, "share_reason": "a copyright notice and no licence"},
               {"source": "https://pack.example/p", "kind": "pack", "licence": "", "copyright": "",
                "pack_share": "not_exported", "share_reason": "a page of an installed knowledge pack"}]
    rag = SimpleNamespace(list_sources=lambda: entries)

    def require(self):
        self.framework_manager = SimpleNamespace(ensure_rag_manager=lambda: rag)
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setenv("COLUMNS", "250")
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "sources"])
    assert result.exit_code == 0, result.output
    for words in ("full text", "link and short facts", "not exported", "[red]© 2023 Vendor[/red]",
                  "would carry 1 with their full text and 1 only as their address"):
        assert words in result.output, words
    packs = CliRunner().invoke(cli.cli, ["-q", "knowledge", "sources", "--kind", "pack"])
    assert "pack.example" in packs.output and f"{DOCS}/open" not in packs.output
