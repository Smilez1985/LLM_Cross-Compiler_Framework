"""llmcf never runs a CSS selector through Soup Sieve: its descendant combinator takes polynomial time on a deeply
nested untrusted page (CVE-2026-126190), and the fixed Soup Sieve 4.0 needs Python 3.11.5, while Debian 12 and
Raspberry Pi OS bring 3.11.2. The one place that looked up elements on fetched pages (a licence file's view,
rag/licences.py) uses Beautiful Soup's find() and finds exactly what the selectors found. On Python before 3.11.5 Soup
Sieve is not installed at all (pyproject.toml override-dependencies), so no lock entry or export pins one before 4.0
and pip-audit ignores no finding."""

import os
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

bs4 = pytest.importorskip("bs4")
import bs4.css  # noqa: E402

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


# What the selectors found on each page, written down so that the lookup is checked on Python before 3.11.5 as well,
# where Soup Sieve is not installed and the selectors cannot run.
FOUND = {"textarea": "MIT licence text", "testid": "Apache text", "article": "GPL text", "itemprop": "BSD text",
         "table": "ISC text", "pre": "Zlib text", "none": "only a page", "empty first": "Unlicense text"}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_licence_files_view_is_found_as_the_selectors_found_it(name):
    from orchestrator.rag.licences import _file_text
    soup = bs4.BeautifulSoup(PAGES[name], "html.parser")
    assert _file_text(soup) == FOUND[name]
    if bs4.css.soupsieve is not None:   # the selectors themselves, where Soup Sieve is installed (Python 3.11.5 on)
        assert _with_selectors(soup) == FOUND[name]


def test_no_framework_code_runs_a_css_selector():
    calls = re.compile(r"\.select(_one)?\(\s*['\"(]|\.css\.select|import soupsieve|from soupsieve")
    found = []
    for folder in ("orchestrator", "scripts", "targets"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if calls.search(line):
                    found.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not found, "a CSS selector (Soup Sieve) in framework code:\n" + "\n".join(found)


def test_the_audit_ignores_no_finding():
    text = (REPO_ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    ignores = re.findall(r"--ignore-vuln\s+(\S+)", text)
    assert ignores == [], ignores
    assert "pip-audit" in text and "--ignore-vuln" not in text


def _version(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def test_no_lock_entry_or_export_pins_a_soupsieve_before_4():
    """The override in pyproject.toml leaves Soup Sieve out before Python 3.11.5; nothing pins one before 4.0."""
    pins = {}
    for name in ("requirements.txt", "requirements-gui.txt", "requirements-dev.txt", "requirements-resolve.txt"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        pins[name] = re.findall(r"^soupsieve==(\S+)\s*(;[^\\\n]*)?", text, flags=re.MULTILINE | re.IGNORECASE)
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    pins["uv.lock"] = [(v, "") for v in re.findall(r'\[\[package\]\]\nname = "soupsieve"\nversion = "([^"]+)"', lock)]
    pins["uv.lock dependencies"] = [(v, "") for v in
                                    re.findall(r'name = "soupsieve", version = "([^"]+)"', lock)]
    old = {name: found for name, found in pins.items() if any(_version(v) < (4, 0) for v, _ in found)}
    assert not old, old
    for name in ("requirements.txt", "requirements-gui.txt"):
        # one pin, only for Python from 3.11.5 on (a newer lock may take a newer 4.x)
        assert len(pins[name]) == 1 and pins[name][0][1].strip() == "; python_full_version >= '3.11.5'", pins[name]
    assert len(pins["uv.lock"]) == 1 and _version(pins["uv.lock"][0][0]) >= (4, 0), pins["uv.lock"]


def test_the_override_leaves_soupsieve_out_only_before_3_11_5():
    import tomllib
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    overrides = [o for o in pyproject["tool"]["uv"].get("override-dependencies", []) if o.startswith("soupsieve")]
    assert overrides == ["soupsieve>=4.0 ; python_full_version >= '3.11.5'"], overrides
    assert pyproject["project"]["requires-python"].startswith(">=3.11,"), "the Python minimum stays 3.11"


def test_without_soupsieve_bs4_finds_and_llmcf_shows_no_warning():
    """Python before 3.11.5 has no Soup Sieve: Beautiful Soup still imports and finds, and its one warning about it is
    silenced by llmcf, by its exact text; any other warning from Beautiful Soup still shows."""
    script = (
        "import sys, warnings\n"
        "sys.modules['soupsieve'] = None\n"   # import soupsieve now raises ImportError, as when it is not installed
        "warnings.simplefilter('error')\n"
        "import orchestrator\n"
        "from orchestrator.rag.licences import _file_text\n"
        "import bs4, bs4.css\n"
        "assert bs4.css.soupsieve is None\n"
        "soup = bs4.BeautifulSoup('<div><pre>Zlib text</pre></div>', 'html.parser')\n"
        "assert _file_text(soup) == 'Zlib text', _file_text(soup)\n"
        "try:\n"
        "    warnings.warn_explicit('another warning', UserWarning, 'css.py', 1, module='bs4.css')\n"
        "except UserWarning:\n"
        "    print('other warnings show')\n"
    )
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(REPO_ROOT))
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip() == "other warnings show"
    assert "soupsieve" not in result.stderr.lower(), result.stderr
