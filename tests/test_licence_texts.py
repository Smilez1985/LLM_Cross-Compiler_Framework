"""Licence texts in knowledge packs (DECISIONS, answers of 2026-10-16, W3): a pack that carries pages in full under a
licence that asks for its text or permission notice ships the canonical text of each such licence (LICENSES/, from the
SPDX License List at a pinned version, each text checked by its sha256) and a NOTICE that lists every page with its
source address, its copyright line as found and its SPDX id. The install, `zoo verify` and the community check verify
them; an AI sees licence ids and counts. Fake pages only, no network."""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from orchestrator.Core.community_pack import (PackError, build_manifest, read_pack, sign_manifest, write_pack)
from orchestrator.rag import licence_texts, licences, pack_licences
from orchestrator.rag.licences import FULL_TEXT, LINK_AND_FACTS
from test_community_tools import call, env, recording, wait_job  # noqa: F401  (env: the fixture)
from test_knowledge_tools import make_rag  # noqa: F401  (make_rag: the fixture)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "orchestrator" / "data" / "licenses"
DOCS = "https://docs.example"
TOOL = "https://github.com/acme/tool"
LIB = "https://github.com/acme/lib"
MIT_FILE = ("MIT License\n\nCopyright (c) 2024 Acme Tools\n\nPermission is hereby granted, free of charge, to any "
            "person obtaining a copy of this software")
# The texts of SPDX License List 3.29.0 (spdx/license-list-data, tag v3.29.0, text/<id>.txt), by their sha256: a text
# changes only together with this list, licenses.json and the version.
PINNED = {
    "Apache-2.0": "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff",
    "BSD-2-Clause": "f32fb3b417a194167cfad068223fc975ba96c5960513a10f66a3c28720aec1df",
    "BSD-3-Clause": "5a93d5831e1297ab10fe643e1a631e83be392896da14ee2951285a79012df69d",
    "CC-BY-1.0": "1e4d54d3d9badc97d8bb32861cb3a449903129d02ab4482e142c85afe132b8a3",
    "CC-BY-2.0": "f892996eb4b6f2e54241432ed67b743772224e191ba3a0ae595eb4feca3df4c5",
    "CC-BY-2.5": "82d2eddedf451177a12c9753e518352f7f90fd4ca3542111c499be981e9a9600",
    "CC-BY-3.0": "e6bc9e9c474700b708f568bac9e5a8a9bcb2b1dad53442f5ba449fcb848b8e76",
    "CC-BY-4.0": "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744",
    "CC-BY-SA-1.0": "826ab33293d21891d7119d6d3bc658c0ac25a756df77bb733be910503e753154",
    "CC-BY-SA-2.0": "6d6b3bb586e5f4ae7e6a955556442fbfcc2a6cedb89be5e4e0492b2983e40760",
    "CC-BY-SA-2.5": "dbe7fadf81c75e0cb15a0cb338a6f42a7b34dced43958a4921ca056ca1b87a93",
    "CC-BY-SA-3.0": "3f941b3b89cf7b8370ceb83cc76d2120d471b58735d8ca60238a751a48d7f72f",
    "CC-BY-SA-4.0": "cde7883b9050a1104f4ac19a1572aafd6e5d7323b68351aaf51fbf4beba54966",
    "ISC": "521c6f0ed8e64736684f89843d16a4df6b4b7449acbcfc6ca6630a3037ca8c53",
    "MIT": "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
    "MPL-2.0": "66a3107d5ad6a058aab753eaac2047ccb2ed0e39465dd0fe5844da3e300d5172",
}


def record(source, content, **meta):
    return {"source": source, "content": content, "metadata": meta}


@pytest.fixture
def world(tmp_path):
    from test_zoo import World
    return World(tmp_path)


@pytest.fixture
def without(tmp_path, monkeypatch):
    """The shipped texts without the named ones (a damaged or older installation)."""
    def remove(*names):
        copy = tmp_path / "licenses"
        shutil.copytree(DATA, copy)
        for name in names:
            (copy / f"{name}.txt").unlink()
        monkeypatch.setattr(licence_texts, "DATA_DIR", copy)
        return copy
    return remove


def pack_files(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


# --- the shipped texts ----------------------------------------------------------------------------------------------

def test_every_shipped_licence_text_matches_its_recorded_and_pinned_sha256():
    manifest = json.loads((DATA / "licenses.json").read_text(encoding="utf-8"))
    assert (manifest["licence_list_version"], manifest["tag"], manifest["data_licence"]) == ("3.29.0", "v3.29.0",
                                                                                             "CC0-1.0")
    texts = manifest["texts"]
    assert {k: v["sha256"] for k, v in texts.items()} == PINNED
    for licence, entry in texts.items():
        data = (DATA / entry["file"]).read_bytes()
        assert entry["file"] == f"{licence}.txt" and hashlib.sha256(data).hexdigest() == entry["sha256"], licence
        assert b"\r" not in data and entry["source"].endswith(f"/v3.29.0/text/{licence}.txt"), licence
    assert sorted(p.name for p in DATA.iterdir()) == sorted([*(e["file"] for e in texts.values()), "licenses.json"])
    assert licence_texts.problems() == () and licence_texts.list_version() == "3.29.0"


def test_every_recognised_licence_that_asks_for_attribution_has_its_text():
    asking = {licence for licence, (shareable, attribution) in licences.LICENCES.items() if shareable and attribution}
    assert asking == set(licence_texts.TEXT_REQUIRED) == set(PINNED)
    assert all(licence_texts.canonical(licence) is not None for licence in asking)
    for free in ("CC0-1.0", "0BSD", "Unlicense", "public-domain"):
        assert not licence_texts.needs_text(free), "these licences ask for no text"


def test_git_keeps_the_licence_texts_byte_for_byte():
    rules = [line.split() for line in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    assert ["orchestrator/data/licenses/*.txt", "-text"] in rules, "a CRLF checkout would change every sha256"


def test_a_changed_or_missing_text_counts_as_not_there(tmp_path, monkeypatch):
    copy = tmp_path / "licenses"
    shutil.copytree(DATA, copy)
    (copy / "MIT.txt").write_bytes((copy / "MIT.txt").read_bytes().replace(b"Permission", b"Leave"))
    (copy / "ISC.txt").unlink()
    monkeypatch.setattr(licence_texts, "DATA_DIR", copy)
    assert licence_texts.canonical("MIT") is None and licence_texts.canonical("ISC") is None
    assert licence_texts.canonical("Apache-2.0") is not None
    assert any("MIT.txt" in p for p in licence_texts.problems()) and any("ISC.txt" in p for p in
                                                                         licence_texts.problems())
    (copy / "licenses.json").write_text("{", encoding="utf-8")
    licence_texts._load.cache_clear()
    assert licence_texts.canonical("Apache-2.0") is None and licence_texts.problems()


# --- what a pack may carry ------------------------------------------------------------------------------------------

def test_a_licence_without_its_canonical_text_never_lets_a_page_go_in_full(without):
    without("MIT")
    plan = licences.share_plan({"licence": "MIT"})
    assert (plan.shared_as, plan.state, plan.licence) == (LINK_AND_FACTS, "no_licence_text", "MIT")
    assert "no canonical text" in plan.reason
    assert licences.share_plan({"licence": "Apache-2.0"}).shared_as == FULL_TEXT
    assert licences.share_plan({"licence": "CC0-1.0"}).shared_as == FULL_TEXT, "CC0 asks for no text"


def test_an_export_without_the_canonical_text_says_why(world, without):
    without("MIT")
    world.rag.records = [record(f"{DOCS}/mit", "Text under MIT.", title="MIT page", licence="MIT"),
                         record(f"{DOCS}/apache", "Text under Apache.", title="Apache page", licence="Apache-2.0")]
    result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    files = pack_files(result.path)
    entries = {e["source"]: e for e in json.loads(files["knowledge.json"])}
    assert entries[f"{DOCS}/mit"]["metadata"]["licence_state"] == "no_licence_text"
    assert "Text under MIT." not in entries[f"{DOCS}/mit"]["content"]
    assert "LICENSES/MIT.txt" not in files and "LICENSES/Apache-2.0.txt" in files
    assert f"- {DOCS}/mit: link and short facts, licence MIT (the licence MIT asks for its text" in result.summary()
    assert any(n.startswith("1 page(s) under MIT only as their address and short facts") for n in result.notes)


# --- the export -----------------------------------------------------------------------------------------------------

APACHE_NOTICE = ["Acme Lib\nCopyright 2020 The Acme Foundation.",
                 "Copyright 2020 The Acme Foundation.\nThis product includes software developed at Acme."]


def full_world_records():
    return [
        record(f"{DOCS}/open", "Open text.", title="Open", licence="CC-BY-4.0", copyright="© 2024 Example Ltd.",
               root_url=f"{DOCS}/"),
        record(f"{DOCS}/bsd", "BSD text.", title="BSD", licence="BSD-3-Clause",
               copyright="Copyright (c) 2019, Board Makers", root_url=f"{DOCS}/"),
        record(f"{DOCS}/isc", "ISC text.", title="ISC", licence="ISC", root_url=f"{DOCS}/"),
        record(f"{DOCS}/mpl", "MPL text.", title="MPL", licence="MPL-2.0", root_url=f"{DOCS}/"),
        record(f"{DOCS}/free", "Free text.", title="Free", licence="CC0-1.0", root_url=f"{DOCS}/"),
        record(f"{DOCS}/sdk", "Vendor words.", title="SDK", copyright="Copyright 2023 Vendor. All rights reserved.",
               root_url=f"{DOCS}/"),
        record(f"{TOOL}/blob/main/LICENSE", MIT_FILE, code_repo="github.com/acme/tool", licence_file=True,
               licence="MIT", root_url=TOOL),
        record(f"{TOOL}/blob/main/src/main.c", "int main(void) { return 0; }", code_repo="github.com/acme/tool",
               root_url=TOOL),
        record(f"{LIB}/blob/main/LICENSE", "Apache License\nVersion 2.0, January 2004", code_repo="github.com/acme/lib",
               licence_file=True, licence="Apache-2.0", root_url=LIB),
        record(f"{LIB}/blob/main/NOTICE", APACHE_NOTICE[1], code_repo="github.com/acme/lib", root_url=LIB,
               chunk_index=1),
        record(f"{LIB}/blob/main/NOTICE", APACHE_NOTICE[0], code_repo="github.com/acme/lib", root_url=LIB,
               chunk_index=0),
        record(f"{LIB}/blob/main/lib.py", "print('lib')", code_repo="github.com/acme/lib", root_url=LIB),
    ]


def test_a_knowledge_pack_ships_each_licence_text_and_a_notice_of_every_page(world):
    world.rag.records = full_world_records()
    result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    files = pack_files(result.path)
    shipped = sorted(n for n in files if n.startswith("LICENSES/"))
    assert shipped == [f"LICENSES/{x}.txt" for x in ("Apache-2.0", "BSD-3-Clause", "CC-BY-4.0", "ISC", "MIT",
                                                     "MPL-2.0")], "CC0 asks for no text, and the vendor page is a link"
    for name in shipped:
        assert files[name] == (DATA / name.split("/")[1]).read_bytes(), "the canonical text, byte for byte"
    manifest = json.loads(files["pack.json"])
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    assert all(listed[n] == hashlib.sha256(files[n]).hexdigest() for n in [*shipped, "NOTICE"]), \
        "the manifest lists the licence files, so the signature covers them"
    notice = files["NOTICE"].decode("utf-8")
    mit = notice.split("== MIT (licence text: LICENSES/MIT.txt) ==")[1].split("==")[0]
    assert "Each copyright line below goes with the licence text in LICENSES/MIT.txt." in mit
    assert (f"Source: {TOOL}/blob/main/src/main.c\nCopyright: Copyright (c) 2024 Acme Tools (from the licence file of "
            "the repository)\nSPDX id: MIT") in mit, "code takes the line of its repository's licence file"
    assert f"Source: {TOOL}/blob/main/LICENSE\nCopyright: Copyright (c) 2024 Acme Tools\nSPDX id: MIT" in mit
    assert "Source: https://docs.example/bsd\nCopyright: Copyright (c) 2019, Board Makers\nSPDX id: BSD-3-Clause" \
        in notice
    apache = notice.split("== Apache-2.0")[1].split("\n== ")[0]
    assert (f"NOTICE file of github.com/acme/lib, as the crawl read it ({LIB}/blob/main/NOTICE):\n    Acme Lib\n"
            "    Copyright 2020 The Acme Foundation.\n    This product includes software developed at Acme.") in apache
    assert apache.count("Copyright 2020 The Acme Foundation.") == 1, "the chunks' overlap is not repeated"
    assert "The crawl read no NOTICE file" not in apache
    assert "== CC0-1.0 (asks for no licence text) ==\n\nSource: https://docs.example/free" in notice
    assert (f"Source: {DOCS}/sdk\nCopyright: Copyright 2023 Vendor. All rights reserved.\nSPDX id: none found\nWhy: a "
            "copyright notice") in notice, "a page shared as a link is listed with why"
    entries = json.loads(files["knowledge.json"])
    code = next(e for e in entries if e["source"].endswith("main.c"))["metadata"]
    assert code["licence_text_file"] == "LICENSES/MIT.txt" and code["copyright"] == "Copyright (c) 2024 Acme Tools"
    assert "its text in LICENSES/MIT.txt" in code["attribution"] and "(from the licence file" in code["attribution"]
    summary = result.summary()
    assert "Licence texts in the pack (unchanged from the SPDX License List 3.29.0), and why:" in summary
    assert ("- LICENSES/MIT.txt: 2 page(s) under MIT; MIT asks that the copyright notice and this permission notice go "
            "with every copy") in summary
    assert "- LICENSES/Apache-2.0.txt: 3 page(s) under Apache-2.0; Apache-2.0 asks" in summary
    assert [t.licence for t in result.licence_texts] == ["Apache-2.0", "BSD-3-Clause", "CC-BY-4.0", "ISC", "MIT",
                                                         "MPL-2.0"]
    assert any(n.startswith("licence texts included") and "MIT for 2 page(s)" in n for n in result.notes)
    assert not any(n.startswith("licence texts kept") for n in result.notes), "the export names its texts once"
    contents = read_pack(result.path, world.tmp / "check", keys=[sign_key_of(world)])
    assert contents.signer, "the author signature covers the licence files"


def sign_key_of(world):
    from orchestrator.Core.community_pack import UserKeys
    return UserKeys(world.secrets).signing_key().public_key()


def test_without_a_page_that_asks_for_a_text_the_pack_ships_no_licence_file(world):
    world.rag.records = [record(f"{DOCS}/free", "Free text.", title="Free", licence="CC0-1.0"),
                         record(f"{DOCS}/sdk", "Vendor words.", copyright="© 2023 Vendor")]
    result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    assert sorted(pack_files(result.path)) == ["knowledge.json", "pack.json", "pack.sig"]
    assert "Licence texts in the pack: none; no page carried in full is under a licence that asks for its text." \
        in result.summary() and result.licence_texts == []


def test_an_apache_page_without_a_crawled_notice_file_says_so(world):
    world.rag.records = [record(f"{LIB}/blob/main/LICENSE", "Apache License", code_repo="github.com/acme/lib",
                                licence_file=True, licence="Apache-2.0", root_url=LIB),
                         record(f"{LIB}/blob/main/a.py", "a = 1", code_repo="github.com/acme/lib", root_url=LIB)]
    notice = pack_files(world.cm.export_pack("knowledge", author="a", pack_id="docs").path)["NOTICE"].decode()
    assert "The crawl read no NOTICE file of github.com/acme/lib; if the work has one, its notices apply too" in notice


def test_the_privacy_scrub_never_changes_a_licence_text(world, monkeypatch):
    from orchestrator.Core import community_manager

    def scrub(text):   # as on a computer named "software": the host name is replaced wherever it stands
        return text.replace("SOFTWARE", "<host>").replace("Software", "<host>")
    monkeypatch.setattr(community_manager, "scrub", scrub)
    world.rag.records = [record(f"{DOCS}/mit", "Text.", title="MIT page", licence="MIT")]
    result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    files = pack_files(result.path)
    assert files["LICENSES/MIT.txt"] == (DATA / "MIT.txt").read_bytes()
    assert not any(name == "LICENSES/MIT.txt" for name, _ in result.changes)
    manifest, problems = world.cm.verify_file(result.path)
    assert problems == [], problems


# --- install, verify ------------------------------------------------------------------------------------------------

def test_the_install_keeps_the_licence_texts_and_verify_checks_them(world):
    world.rag.records = full_world_records()
    exported = world.cm.export_pack("knowledge", author="a", pack_id="docs")
    imported = []
    world.rag.ingest_document = lambda s, c, m: imported.append((s, m)) or True
    prepared = world.cm.prepare_install_file(exported.path)
    assert "licence texts kept with the pack: Apache-2.0, BSD-3-Clause, CC-BY-4.0, ISC, MIT, MPL-2.0 (LICENSES/) " \
           "and NOTICE" in prepared.summary()
    folder = world.cm.finish_install(prepared)
    assert (folder / "LICENSES" / "MIT.txt").read_bytes() == (DATA / "MIT.txt").read_bytes()
    assert (folder / "NOTICE").is_file()
    meta = next(m for s, m in imported if s.endswith("main.c"))
    assert meta["licence_text_file"] == "LICENSES/MIT.txt" and meta["origin_pack"] == folder.name
    ((_, _, _, problems),) = world.cm.verify_installed()
    assert problems == []
    (folder / "LICENSES" / "MIT.txt").write_text("MIT License\n\nDo what you like.\n", encoding="utf-8")
    ((_, _, _, problems),) = world.cm.verify_installed()
    assert "changed since it was installed" in problems
    assert "LICENSES/MIT.txt: differs from the canonical text of MIT (SPDX License List 3.29.0)" in problems


def hand_pack(world, files, kind="knowledge", pack_id="hand"):
    root = world.tmp / "hand" / pack_id
    for rel, data in files.items():
        path = root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    manifest = build_manifest(root, kind=kind, id=pack_id, version="1.0.0", author="mallory", license="NOASSERTION")
    out = world.tmp / f"{pack_id}.zip"
    out.write_bytes(write_pack(root, manifest, sign_manifest(manifest, world.maintainer)))
    return out


MIT_PAGE = json.dumps([record(f"{DOCS}/mit", "Text under MIT.", licence="MIT", shared_as=FULL_TEXT)])
GOOD_NOTICE = f"NOTICE\n\nSource: {DOCS}/mit\nCopyright: none found\nSPDX id: MIT\n"
MIT_TEXT = (DATA / "MIT.txt").read_bytes()


@pytest.mark.parametrize("files, problem", [
    ({"knowledge.json": MIT_PAGE, "NOTICE": GOOD_NOTICE},
     "1 page(s) carry their full text under MIT, but LICENSES/MIT.txt is missing"),
    ({"knowledge.json": MIT_PAGE, "NOTICE": GOOD_NOTICE, "LICENSES/MIT.txt": MIT_TEXT.replace(b"MIT", b"Our")},
     "LICENSES/MIT.txt: differs from the canonical text of MIT"),
    ({"knowledge.json": MIT_PAGE, "LICENSES/MIT.txt": MIT_TEXT}, "NOTICE: missing"),
    ({"knowledge.json": MIT_PAGE, "LICENSES/MIT.txt": MIT_TEXT, "NOTICE": "NOTICE\n\nSource: https://other.example\n"},
     "NOTICE does not list 1 page(s)"),
    ({"knowledge.json": MIT_PAGE, "LICENSES/MIT.txt": MIT_TEXT, "NOTICE": GOOD_NOTICE,
      "LICENSES/Made-Up-1.0.txt": "Anything goes."}, "Made-Up-1.0 is not a licence whose canonical text this framework"),
    ({"knowledge.json": MIT_PAGE, "LICENSES/MIT.txt": MIT_TEXT, "NOTICE": GOOD_NOTICE, "LICENSES/notes.md": "x"},
     "LICENSES/notes.md: not a licence text"),
])
def test_a_pack_whose_licence_files_do_not_match_its_pages_is_refused(world, files, problem):
    path = hand_pack(world, files)
    with pytest.raises(PackError) as e:
        world.cm.prepare_install_file(path)
    assert problem in str(e.value)
    _, problems = world.cm.verify_file(path)
    assert any(problem in p for p in problems), problems
    assert f"{DOCS}/mit" not in str(e.value), "a page's address (pack text) is never part of the message"


def test_a_pack_with_matching_licence_files_and_an_older_pack_without_them_install(world):
    good = hand_pack(world, {"knowledge.json": MIT_PAGE, "LICENSES/MIT.txt": MIT_TEXT, "NOTICE": GOOD_NOTICE})
    world.cm.discard(world.cm.prepare_install_file(good))
    older = json.dumps([record(f"{DOCS}/old", "A page of a pack from 3.0.0.")])   # no shared_as: nothing is asked
    world.cm.discard(world.cm.prepare_install_file(hand_pack(world, {"knowledge.json": older}, pack_id="older")))


# --- targets --------------------------------------------------------------------------------------------------------

def test_a_target_export_ships_the_licence_texts_of_its_snapshot(world):
    from test_zoo import make_target
    folder = make_target(world.root / "targets" / "own-board")
    (folder / "knowledge").mkdir()
    (folder / "knowledge" / "initial_knowledge.json").write_text(json.dumps([
        record(f"{DOCS}/mit", "Shareable text.", title="MIT page", licence="MIT", copyright="(c) 2024 Board Co")]),
        encoding="utf-8")
    (folder / "knowledge" / "NOTICE").write_text("an old notice\n", encoding="utf-8")
    result = world.cm.export_pack("target", "own-board", author="a")
    files = pack_files(result.path)
    assert files["knowledge/LICENSES/MIT.txt"] == MIT_TEXT
    assert f"Source: {DOCS}/mit\nCopyright: (c) 2024 Board Co\nSPDX id: MIT" in files["knowledge/NOTICE"].decode()
    assert "- knowledge/LICENSES/MIT.txt: 1 page(s) under MIT" in result.summary()
    assert (folder / "knowledge" / "NOTICE").read_text() == "an old notice\n", "the target folder stays as it is"
    assert not (folder / "knowledge" / "LICENSES").exists()
    assert world.cm.verify_file(result.path)[1] == []
    shutil.rmtree(folder)   # (a test folder) so the pack can be installed under its name
    prepared = world.cm.prepare_install_file(result.path)
    assert any(n.startswith("licence texts kept with the pack: MIT (knowledge/LICENSES/) and knowledge/NOTICE")
               for n in prepared.notes), prepared.notes


def test_a_target_pack_whose_snapshot_lacks_its_licence_text_is_refused(world):
    from test_zoo import TARGET_YML
    files = {"target.yml": TARGET_YML, "Dockerfile": "FROM debian:bookworm-slim\nRUN echo ok\n",
             "modules/build.sh": "#!/bin/bash\necho build\n", "knowledge/initial_knowledge.json": MIT_PAGE}
    with pytest.raises(PackError) as e:
        world.cm.prepare_install_file(hand_pack(world, files, kind="target", pack_id="board"))
    assert "but knowledge/LICENSES/MIT.txt is missing" in str(e.value)


def test_the_module_wizard_writes_the_licence_files_next_to_its_snapshot(world, tmp_path):
    from orchestrator.Core.module_generator import ModuleGenerator, _leftover_placeholders
    world.rag.records = [
        record(f"{LIB}/blob/main/LICENSE", "Apache License", code_repo="github.com/acme/lib", licence_file=True,
               licence="Apache-2.0", root_url=LIB),
        record(f"{LIB}/blob/main/NOTICE", "Acme Lib, release of YYYY-MM-DD", code_repo="github.com/acme/lib",
               root_url=LIB)]
    target = tmp_path / "targets" / "wizard-board"
    target.mkdir(parents=True)
    ModuleGenerator(tmp_path / "targets")._create_knowledge_snapshot(target, world.framework, "wizard-board", [LIB])
    assert (target / "knowledge" / "LICENSES" / "Apache-2.0.txt").read_bytes() == \
        (DATA / "Apache-2.0.txt").read_bytes()
    notice = (target / "knowledge" / "NOTICE").read_text(encoding="utf-8")
    assert "    Acme Lib, release of YYYY-MM-DD" in notice
    assert _leftover_placeholders(target) == [], "the NOTICE quotes crawled text, not template output"
    assert "LICENSES/" in (target / "knowledge" / "README.md").read_text(encoding="utf-8")


# --- pieces ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (f"{LIB}/blob/main/NOTICE", True), (f"{LIB}/blob/v1.2/NOTICE.txt", True), (f"{LIB}/raw/main/notice.md", True),
    ("https://raw.githubusercontent.com/acme/lib/main/NOTICE", True), (f"{DOCS}/legal/NOTICE", True),
    (f"{LIB}/blob/main/docs/NOTICE", False), (f"{LIB}/issues/7", False), (f"{LIB}/blob/main/NOTICES.md", False),
    (f"{DOCS}/notice-board", False),
])
def test_a_notice_file_is_the_works_own(url, expected):
    assert licences.is_notice_file(url) is expected


def test_a_page_is_joined_from_its_chunks_without_their_overlap():
    chunks = [record("s", "Third part, last words here.", chunk_index=2),
              record("s", "First part of the notice. Second sentence of it.", chunk_index=0),
              record("s", "Second sentence of it. Then more words, last words here.", chunk_index=1)]
    assert pack_licences.joined_text(chunks) == ("First part of the notice. Second sentence of it.\nThen more words, "
                                                 "last words here.\nThird part, last words here.")
    assert pack_licences.joined_text([record("s", "a b"), record("s", "b c")]) == "a b\nb c", \
        "a short match is kept twice rather than dropping words of a notice"


def test_an_ai_gets_the_licence_texts_in_fixed_words(env):  # noqa: F811
    from orchestrator.Core.community_pack import UserKeys
    UserKeys(env.world.secrets).signing_key()
    env.world.rag.records = full_world_records()
    asked = []
    out = call(env, "export_pack", {"kind": "knowledge", "author": "pat", "pack_id": "docs"},
               confirm=recording(asked))
    assert "Licence texts" in asked[0] and "LICENSES/" in asked[0]
    job = wait_job(env, out.pack_notes[0].split()[2].rstrip(":"))
    texts = {t["licence"]: t for t in job.result["licence_texts"]}
    assert texts["MIT"] == {"licence": "MIT", "file": "LICENSES/MIT.txt", "pages": 2,
                            "why": licence_texts.TEXT_REQUIRED["MIT"]}
    dumped = json.dumps(job.result)
    for site_text in ("Acme Tools", "Board Makers", "Acme Foundation", "Example Ltd."):
        assert site_text not in dumped, "copyright lines and notices are site text and never reach an AI"


def test_the_knowledge_base_says_when_a_licence_text_is_missing(make_rag, without):  # noqa: F811
    without("ISC")
    rag = make_rag()
    assert rag.ingest_document(f"{DOCS}/isc", "# ISC\n\nText.", {"content_type": "html", "licence": "ISC"})
    (source,) = rag.list_sources()
    assert (source["licence_state"], source["pack_share"]) == ("no_licence_text", LINK_AND_FACTS)

