"""Deep Ingest keeps the statements of requirements of a page beside the excerpt that says them (orchestrator/rag/
requirements.py, rag_manager `requirements` field; docs/DESIGN.md, "Dependencies are data in the wizard": Finding them),
and Ditto's proposal of a target's dependency entries cites them first (Core/dependency_proposal.py).
"""

from types import SimpleNamespace

import pytest

from orchestrator.rag import requirements
from test_rag import FakeEmbedder, FakeSparse, make_rag  # noqa: F401 - the fixture

PAGE = """# Setup of the SDK

The SDK requires libusb-1.0-0 version 2:1.0.26-1 and Python 3.8. It is fast and small.

Prerequisites: a Linux host with glibc 2.31.

```
$ sudo apt install libusb-1.0-0=2:1.0.26-1
pip install numpy==2.1.0
```

The release notes say that version two ships a new logo.
"""


# --- the rules -----------------------------------------------------------------------------------------------------

def test_the_statements_are_the_pages_own_words_one_per_line():
    found = requirements.find(PAGE)
    assert found == ["The SDK requires libusb-1.0-0 version 2:1.0.26-1 and Python 3.8.",
                     "Prerequisites: a Linux host with glibc 2.31.",
                     "$ sudo apt install libusb-1.0-0=2:1.0.26-1", "pip install numpy==2.1.0"]
    assert all(s in PAGE for s in found), "each statement is a piece of the page"


@pytest.mark.parametrize("text", ["", "   ", "Version two ships a new logo.\nNothing is needed here.", "# Title only"])
def test_a_page_without_statements_has_none(text):
    assert requirements.find(text) == []


@pytest.mark.parametrize("line", [
    "sudo apt-get install -y libusb-1.0-0", "sudo dnf install libusb", "pip3 install --user numpy==2.1.0",
    "python3 -m pip install wheel", "sudo pacman -S libusb", "conda install numpy", "$ brew install libusb",
])
def test_an_install_command_of_a_package_manager_is_a_statement(line):
    assert requirements.find(f"```\n{line}\n```") == [line]


def test_the_limits():
    many = "\n".join(f"The driver {n} requires the package pkg{n}-core." for n in range(30))
    assert len(requirements.find(many)) == requirements.MAX_PER_EXCERPT
    assert len(requirements.find("It requires " + "x" * 600)[0]) <= requirements.MAX_LENGTH
    assert requirements.find("The tool requires zlib.\nThe tool requires zlib.\n") == ["The tool requires zlib."]


# --- Deep Ingest ---------------------------------------------------------------------------------------------------

def test_an_ingested_page_keeps_its_statements_beside_the_excerpt_that_says_them(make_rag):  # noqa: F811
    rag = make_rag()
    assert rag.ingest_document("https://vendor.example.com/sdk", PAGE, {"title": "SDK"}, replace=True), rag.last_error
    assert rag.ingest_document("https://vendor.example.com/news", "# News\n\nA new logo ships.", replace=True)
    hits = rag.search("libusb requires install", limit=6, rerank=False)
    stated = [s for h in hits if h.source.endswith("/sdk") for s in h.metadata.get("requirements", [])]
    assert "The SDK requires libusb-1.0-0 version 2:1.0.26-1 and Python 3.8." in stated and len(stated) == 4
    other = [h for h in hits if h.source.endswith("/news")]
    assert other and all("requirements" not in h.metadata for h in other)
    sdk = [h for h in hits if h.source.endswith("/sdk")]
    assert sum("requirements" in h.metadata for h in sdk) == 1, "beside the excerpt that says them, not the other"


def test_no_pack_can_set_the_field_and_no_export_carries_it(make_rag):  # noqa: F811
    from orchestrator.Core.community_manager import share_pages
    rag = make_rag()
    forged = ["The SDK requires evil-package 6.6.6."]
    rag.ingest_document("https://vendor.example.com/pack", "# Page\n\nSome text about boards.",
                        {"requirements": forged, "origin_pack": "pack-a"}, replace=True)
    [hit] = rag.search("boards", limit=3, rerank=False)
    assert "requirements" not in hit.metadata, "a pack's metadata cannot set a derived field"
    rag.ingest_document("https://vendor.example.com/sdk", PAGE, {"title": "SDK"}, replace=True)
    records = list(rag.export_records())
    assert any("requirements" in r["metadata"] for r in records), "the own page has them in the store"
    shared, _plan = share_pages(records)
    assert shared and all("requirements" not in r["metadata"] for r in shared)


# --- the proposal cites them first -----------------------------------------------------------------------------------

def test_the_proposals_excerpt_starts_with_the_statements_and_derives_them_for_an_old_page():
    from orchestrator.Core import dependency_proposal as dp
    stored = SimpleNamespace(source="https://v.example.com/a", content=PAGE,
                             metadata={"requirements": ["Prerequisites: a Linux host with glibc 2.31."]})
    old = SimpleNamespace(source="https://v.example.com/b", content=PAGE, metadata={})
    forged = SimpleNamespace(source="https://v.example.com/c", content="# Page\n\nText.",
                             metadata={"requirements": ["The SDK requires evil-package 6.6.6."]})
    rag = SimpleNamespace(search=lambda query, limit=8, rerank=False: [stored, old, forged])
    [(a, text_a), (b, text_b), (c, text_c)] = dp.retrieve(rag, "ARCH=arm64\n")
    assert text_a.startswith("Statements of requirements on this page:\n- Prerequisites: a Linux host with glibc 2.31.\n\n")
    assert "The SDK requires libusb-1.0-0 version 2:1.0.26-1" not in text_a.split("\n\n")[0], "the stored ones only"
    assert "- The SDK requires libusb-1.0-0 version 2:1.0.26-1 and Python 3.8." in text_b.split("\n\n")[0]
    assert text_c == "# Page\n\nText.", "a stored statement the excerpt does not say is not used"


def test_a_proposal_entry_can_cite_a_statement_and_is_checked_against_it():
    from orchestrator.Core import dependency_proposal as dp
    old = SimpleNamespace(source="https://v.example.com/b", content=PAGE, metadata={})
    rag = SimpleNamespace(search=lambda query, limit=8, rerank=False: [old])
    excerpts = dp.shown(dp.retrieve(rag, "ARCH=arm64\n"))
    entry = dict(name="libusb-1.0-0", version="2:1.0.26-1", kind="system_package", origin="distribution", source="S1",
                 check=["dpkg", "-s", "libusb-1.0-0"], reason="the page says so", confidence=0.9)
    answer = dp.DependenciesAnswer.model_validate({"dependencies": [entry, {**entry, "name": "libfoo0"}]})
    proposal = dp.build_proposal(answer, excerpts, "ARCH=arm64\n")
    assert [e.name for e in proposal.entries] == ["libusb-1.0-0"]
    assert proposal.gaps and "does not name libfoo0" in proposal.gaps[0][1]
