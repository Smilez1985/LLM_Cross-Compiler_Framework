"""llmcf never runs a CSS selector through Soup Sieve: its descendant combinator takes polynomial time on a deeply
nested untrusted page (CVE-2026-126190), and the fixed Soup Sieve 4.0 needs Python 3.11.5, while Debian 12 and
Raspberry Pi OS bring 3.11.2. The one place that looked up elements on fetched pages (a licence file's view,
rag/licences.py) uses Beautiful Soup's find() and finds exactly what the selectors found."""

import re

import pytest

from conftest import REPO_ROOT

bs4 = pytest.importorskip("bs4")

OLD_SELECTORS = ("textarea#read-only-cursor-text-area", "[data-testid=read-only-cursor-text-area]",
                 "article.markdown-body", "[itemprop=text]", "table.highlight", "pre")

PAGES = {
    "textarea": '<div><textarea id="read-only-cursor-text-area">MIT licence text</textarea><pre>other</pre></div>',
    "testid": '<section><div data-testid="read-only-cursor-text-area">Apache text</div><pre>other</pre></section>',
    "article": '<main><article class="box markdown-body">GPL text</article><pre>other</pre></main>',
    "itemprop": '<div><span itemprop="text">BSD text</span><table class="highlight"><tr><td>x</td></tr></table></div>',
    "table": '<div><table class="highlight"><tr><td>ISC text</td></tr></table><pre>other</pre></div>',
    "pre": '<body><p>intro</p><pre>Zlib text</pre><pre>second</pre></body>',
    "none": '<body><p>only a page</p></body>',
    "empty first": '<body><article class="markdown-body">  </article><pre>Unlicense text</pre></body>',
}


def _with_selectors(soup) -> str:
    for selector in OLD_SELECTORS:
        view = soup.select_one(selector)
        if view is not None and view.get_text(strip=True):
            return view.get_text("\n")
    return soup.get_text("\n")


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_licence_files_view_is_found_as_the_selectors_found_it(name):
    from orchestrator.rag.licences import _file_text
    soup = bs4.BeautifulSoup(PAGES[name], "html.parser")
    assert _file_text(soup) == _with_selectors(soup)


def test_no_framework_code_runs_a_css_selector():
    calls = re.compile(r"\.select(_one)?\(\s*['\"(]|\.css\.select|import soupsieve|from soupsieve")
    found = []
    for folder in ("orchestrator", "scripts", "targets"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if calls.search(line):
                    found.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not found, "a CSS selector (Soup Sieve) in framework code:\n" + "\n".join(found)


def test_the_audit_ignores_the_soupsieve_finding_only_with_its_reason():
    text = (REPO_ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    ignores = re.findall(r"--ignore-vuln\s+(\S+)", text)
    assert set(ignores) == {"CVE-2026-126190"}, ignores
    assert "Soup Sieve" in text and "find()" in text, "the ignore says why it is safe"
