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
    page = soup('<footer>Text under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY</a>, everything else '
                'under <a href="https://creativecommons.org/licenses/by-nc-nd/4.0/">CC BY-NC-ND</a>.</footer>')
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


PROSE = " ".join(["The terms below apply to every copy of the work."] * 8)   # a licence text longer than a name


@pytest.mark.parametrize("text, expected", [
    # a word in passing never names the licence: "public domain" in the Artistic Licence or in a refusal
    ('The "Artistic License"\n\nPreamble\n' + PROSE + " You may place your modifications in the Public Domain.",
     None),
    ("This work is not in the public domain. All rights reserved.", None),
    ("Copyright 2024 Vendor Co. All rights reserved.\n" + PROSE + " This product includes components under the MIT "
     "License (https://opensource.org/licenses/MIT) and the Apache License, Version 2.0.", None),
    # the licence's own title decides, also when its text names other licences later
    ("GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n" + PROSE + " If your program is a subroutine library, use "
     "the GNU Lesser General Public License instead of this License.", "GPL-2.0"),
    ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n" + PROSE + "\n13. Use with the GNU Affero General Public "
     "License.\n", "GPL-3.0"),
    ("Mozilla Public License Version 2.0\n" + PROSE + ' "Secondary License" means either the GNU General Public '
     "License, Version 2.0, the GNU Lesser General Public License, Version 2.1, the GNU Affero General Public "
     "License, Version 3.0, or any later versions of those licenses.", "MPL-2.0"),
    ("GNU GENERAL PUBLIC LICENSE\nVersion 1, February 1989\n" + PROSE, "GPL-1.0"),
    ("GNU LIBRARY GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n" + PROSE, "LGPL-2.0"),
    ("GNU Free Documentation License\nVersion 1.2, November 2002\n" + PROSE, "GFDL-1.2"),
    # another version is not upgraded to one that allows passing the text on
    ("MOZILLA PUBLIC LICENSE\nVersion 1.1\n" + PROSE, None), ("Mozilla Public License 1.1", None),
    ("Apache License, Version 1.1", None),
    ("Redistribution and use in source and binary forms, with or without modification, are permitted provided that "
     "the following conditions are met: 3. All advertising materials mentioning features or use of this software "
     "must display the following acknowledgement. 4. Neither the name of the University nor the names of its "
     "contributors may be used to endorse products. " + PROSE, None),
    ("Copyright (c) 2024 Acme\n\nPermission to use, copy, modify, and/or distribute this software for any purpose "
     "with or without fee is hereby granted, provided that the above copyright notice and this permission notice "
     "appear in all copies. " + PROSE, "ISC"),
    ("SPDX-License-Identifier: GPL-2.0-or-later\n" + PROSE, "GPL-2.0"),
    # a component's licence bundled into a text that keeps all rights names nothing; a copyright line does not count
    ("Copyright 2024 Vendor Co. All rights reserved.\n" + PROSE + "\n\nThird-party notices:\n\nMIT License\n\n"
     "Permission is hereby granted, free of charge, to any person obtaining a copy of this software", None),
    ("MIT License\n\nCopyright (c) 2013 Example Inc. or its affiliates. All Rights Reserved.\n\nPermission is hereby "
     "granted, free of charge, to any person obtaining a copy of this software " + PROSE, "MIT"),
    ("Copyright 2012 Example\n\nLicensed under the Apache License, Version 2.0 (the \"License\"); you may not use "
     "this file except in compliance with the License. " + PROSE, "Apache-2.0"),
])
def test_a_licence_is_named_by_its_title_text_or_address_never_by_a_mention(text, expected):
    assert licences.licence_from_text(text) == expected


def test_an_unrecognised_licence_file_makes_the_code_restricted_not_unknown():
    url = "https://github.com/acme/tool/blob/main/LICENSE"
    meta = licences.from_html(soup(f'<pre>The "Artistic License"\n{PROSE} public domain</pre>'), url)
    assert meta["licence"] == "other" and meta["licence_file"]
    code = {"code_repo": "github.com/acme/tool", "root_url": "https://github.com/acme/tool"}
    plan = licences.plan_for(code, licences.repo_licences([{**meta, "root_url": code["root_url"]}]))
    assert (plan.shared_as, plan.state) == (LINK_AND_FACTS, "restricted")
    assert "not part of the crawl" not in plan.reason, plan.reason


def test_the_repository_licence_covers_its_code_never_its_issues_or_wiki():
    root = "https://github.com/acme/tool"
    licence_file = {**licences.from_html(soup(f"<pre>{MIT_TEXT}</pre>"), f"{root}/blob/main/LICENSE"),
                    "root_url": root}
    repos = licences.repo_licences([licence_file])
    page = soup("<p>Bob wrote: here is my long comment about the build.</p><footer>© 2026 GitHub, Inc.</footer>")
    for url in (f"{root}/issues/12", f"{root}/pull/3", f"{root}/discussions/7", f"{root}/wiki/Home",
                f"{root}/releases/tag/v1.0", f"{root}/commit/abc123"):
        plan = licences.plan_for({**licences.from_html(page, url), "root_url": root}, repos)
        assert (plan.shared_as, plan.licence) == (LINK_AND_FACTS, ""), (url, plan)
    for url in (root, f"{root}/tree/main/docs", f"{root}/blob/main/README.md",
                "https://raw.githubusercontent.com/acme/tool/main/src/a.c"):
        plan = licences.plan_for({**licences.from_html(page, url), "root_url": root}, repos)
        assert (plan.shared_as, plan.licence) == (FULL_TEXT, "MIT"), (url, plan)


@pytest.mark.parametrize("html", [
    # an image credit is not the page's licence
    '<article><p>My long article.</p><figure><img src="a.png"><figcaption>Photo by X, <a href="https://creativecommons'
    '.org/licenses/by-sa/2.0/">CC BY-SA 2.0</a></figcaption></figure></article><footer>© 2024 Jane Doe.</footer>',
    '<p>My long article. The photo above is by X (<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0'
    "</a>).</p><footer>© 2024 Jane Doe.</footer>",
    # a page that says "All rights reserved" keeps them, whatever a bare link elsewhere says
    '<p>Text.</p><footer>© 2024 Vendor Ltd. All rights reserved. Icons: <a href="https://creativecommons.org/licenses'
    '/by/4.0/">CC BY 4.0</a></footer>',
])
def test_a_creative_commons_link_that_is_not_the_pages_licence_never_opens_it(html):
    meta = licences.from_html(soup(html), f"{SITE}/post")
    assert licences.share_plan(meta).shared_as == LINK_AND_FACTS, meta


@pytest.mark.parametrize("html, expected", [
    ('<main><p>Guide.</p></main><footer>Raspberry Pi documentation is copyright © 2012-2024 Raspberry Pi Ltd and is '
     'licensed under a <a href="https://creativecommons.org/licenses/by-sa/4.0/">Creative Commons Attribution-'
     "ShareAlike 4.0</a> licence.</footer>", "CC-BY-SA-4.0"),
    ('<p>This page is licensed under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.</p>',
     "CC-BY-4.0"),
    ('<div class="section">Guide.</div><div class="site-footer"><a href="https://creativecommons.org/licenses/by-sa/'
     '4.0/">CC BY-SA 4.0</a></div>', "CC-BY-SA-4.0"),
])
def test_a_creative_commons_link_that_licenses_the_page_counts(html, expected):
    assert licences.from_html(soup(html), f"{SITE}/guide")["licence"] == expected


def test_a_pdf_names_its_licence_in_its_metadata_never_by_a_link_in_its_text():
    raw = (b"%PDF-1.7 1 0 obj << /A << /S /URI /URI (https://opensource.org/licenses/MIT) >> >> endobj "
           b"2 0 obj << /URI (https://creativecommons.org/licenses/by/4.0/) >> endobj")
    meta = licences.from_pdf(raw, "User guide\nCopyright 2023 Vendor Co., Ltd. All rights reserved.\n",
                             f"{SITE}/guide.pdf")
    assert "licence" not in meta and licences.share_plan(meta).shared_as == LINK_AND_FACTS
    xmp = (b"%PDF-1.7 <x:xmpmeta><rdf:Description><xmpRights:WebStatement>https://creativecommons.org/licenses/by-sa/"
           b"4.0/</xmpRights:WebStatement></rdf:Description></x:xmpmeta>")
    assert licences.from_pdf(xmp, "Manual", f"{SITE}/m.pdf")["licence"] == "CC-BY-SA-4.0"


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
    result = world.cm.export_pack("knowledge", topic="docs", author="a", pack_id="docs")
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
