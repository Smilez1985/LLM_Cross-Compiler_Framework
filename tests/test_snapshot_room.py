"""The disk check before a knowledge snapshot (DECISIONS, eighth round, answer 2) and the snapshot before a knowledge
pack import (N13).

Before a snapshot is made, its size is estimated. When it does not fit on the disk (beside the reserve that always
stays free), no snapshot is made and the change that needed it (Deep Ingest, a restore, a knowledge pack import, the
index of the framework's own code) does not run either: there is no "continue anyway". When it fits but leaves little
free space, that is a situation warning. The doctor's knowledge line shows what the snapshots use against the free
space. Nothing is ever deleted to make room.
"""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import knowledge_snapshots as ks
from orchestrator.Core import rag_manager as rag_module
from orchestrator.Core.community_manager import CommunityManager
from orchestrator.tools.registry import ToolContext, ToolError
from test_build_tools import env  # noqa: F401 - the fixture
from test_optional_tools import Store, rag_with
from test_rag import ingest_all, make_rag  # noqa: F401 - the fixture

GB = 1000 ** 3
TOTAL = 100 * GB          # the reserve on this disk is 2 GiB (more than 2 % of it)


def usage(free, total=TOTAL):
    return lambda path: shutil._ntuple_diskusage(total, total - free, free)


@pytest.fixture
def disk(monkeypatch):
    """Sets the free space every snapshot check sees."""
    def set_free(free):
        monkeypatch.setattr(ks, "_disk_usage", usage(free))
    return set_free


def files_of(folder: Path):
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# --- the room itself ---------------------------------------------------------------------------------------------

def test_the_verdict_follows_the_free_space_and_the_reserve(tmp_path):
    folder = tmp_path / "knowledge_snapshots"
    enough = ks.snapshot_room(folder, 1 * GB, tmp_path, disk_usage=usage(50 * GB))
    tight = ks.snapshot_room(folder, 1 * GB, tmp_path, disk_usage=usage(5 * GB))
    short = ks.snapshot_room(folder, 1 * GB, tmp_path, disk_usage=usage(int(2.5 * GB)))
    assert (enough.verdict, tight.verdict, short.verdict) == ("enough", "tight", "does_not_fit")
    assert enough.fits and tight.fits and not short.fits
    with pytest.raises(ks.SnapshotNoRoom) as exc:
        ks.require_room(short, "a Deep Ingest")
    text = str(exc.value)
    assert "a Deep Ingest" in text and "No snapshot was made and nothing was changed" in text
    assert str(tmp_path) not in text, "the refusal reaches AI callers: no host path"
    assert "Knowledge Snapshots" in exc.value.next and exc.value.blocker == "snapshot_no_room"


def test_the_trash_is_never_counted_as_free(tmp_path):
    """What lies in the trash still uses the disk until the person empties it."""
    room = ks.snapshot_room(tmp_path / "knowledge_snapshots", 1 * GB, tmp_path, disk_usage=usage(int(2.5 * GB)))
    assert room.check.trash_bytes == 0 and room.verdict == "does_not_fit"


# --- the root: RAGManager.create_snapshot ------------------------------------------------------------------------

def test_no_snapshot_is_written_when_it_does_not_fit_and_nothing_is_deleted(tmp_path, disk):
    store = Store()
    rag = rag_with(tmp_path, store)
    old = rag.snapshot_dir / "pre_ingest_1.jsonl.gz"
    old.write_bytes(b"old")
    disk(int(2.1 * GB))
    with pytest.raises(ks.SnapshotNoRoom):
        rag.create_snapshot("pre_ingest_2", "a Deep Ingest")
    assert files_of(rag.snapshot_dir) == ["pre_ingest_1.jsonl.gz"], "nothing written, nothing deleted"
    assert rag.last_snapshot_room.verdict == "does_not_fit"


def test_a_snapshot_never_replaces_an_earlier_one_of_the_same_name(tmp_path, disk):
    """Snapshot names carry the second they were taken (pre_ingest_<time>): two in one second (a crawl of two start
    addresses, a pack import right after it) must not write over the first, which holds the state before both."""
    rag = rag_with(tmp_path, Store())
    disk(500 * GB)
    first = rag.snapshot_dir / "pre_ingest_1.jsonl.gz"
    first.write_bytes(b"the state before the crawl")
    assert rag.create_snapshot("pre_ingest_1") == "pre_ingest_1-2"
    assert first.read_bytes() == b"the state before the crawl"
    assert files_of(rag.snapshot_dir) == ["pre_ingest_1-2.jsonl.gz", "pre_ingest_1.jsonl.gz"]


def test_a_tight_disk_still_takes_the_snapshot_and_says_so(tmp_path, disk):
    rag = rag_with(tmp_path, Store())
    disk(int(3 * GB))
    assert rag.create_snapshot("pre_ingest_2") == "pre_ingest_2"
    assert rag.last_snapshot_room.verdict == "tight"


def test_deep_ingest_does_not_crawl_without_its_snapshot(tmp_path, disk, monkeypatch):
    crawled = []

    class Crawler:
        def __init__(self, *args, **kwargs):
            crawled.append(args)

    rag = rag_with(tmp_path, Store())
    rag._connect = lambda: True
    monkeypatch.setattr(rag_module, "CrawlerManager", Crawler)
    disk(int(2.1 * GB))
    result = rag.ingest_url("https://docs.example/")
    assert crawled == [], "no 'continue anyway': nothing is read without the snapshot"
    assert result["outcome"] == "failure" and result["blocker"] == "snapshot_no_room"
    assert result["snapshot"] is None and "Not enough disk space" in result["message"]


def test_a_restore_does_not_start_without_its_backup(tmp_path, disk):
    store = Store()
    rag = rag_with(tmp_path, store)
    (rag.snapshot_dir / "old.jsonl.gz").write_bytes(b"old")
    disk(int(2.1 * GB))
    assert rag.restore_snapshot("old") is False
    assert store.restored == [] and rag.last_restore_blocker == "snapshot_no_room"
    assert files_of(rag.snapshot_dir) == ["old.jsonl.gz"]


def test_the_codebase_index_does_not_run_without_its_snapshot(tmp_path, disk):
    rag = rag_with(tmp_path, Store())
    rag._connect = lambda: True
    rag.app_root = tmp_path
    (tmp_path / "README.md").write_text("# The framework\n", encoding="utf-8")
    rag._ingest = lambda *a, **k: pytest.fail("nothing is indexed without the snapshot")
    disk(int(2.1 * GB))
    result = rag.ingest_codebase(tmp_path)
    assert result["success"] is False and result["blocker"] == "snapshot_no_room"
    assert result["snapshot_failed"] and result["count"] == 0, "the docs index's own fail-closed answer, too"


def test_the_estimate_of_a_real_store_is_not_below_the_snapshot(make_rag, disk):
    """The estimate compresses a sample exactly as the file holds it; it never says less than the file takes."""
    disk(500 * GB)
    rag = make_rag()
    ingest_all(rag)
    estimate = rag.snapshot_estimate()
    name = rag.create_snapshot("measured")
    actual = (rag.snapshot_dir / f"{name}.jsonl.gz").stat().st_size
    assert estimate >= actual > 0


# --- N13: a snapshot before a knowledge pack import ---------------------------------------------------------------

class PackRag:
    def __init__(self, no_room=False):
        self.no_room, self.snapshots, self.ingested = no_room, [], []

    def create_snapshot(self, name, before="the change"):
        if self.no_room:
            room = ks.snapshot_room(Path("/nonexistent"), GB, None, disk_usage=usage(int(2.1 * GB)))
            raise ks.SnapshotNoRoom(room, before)
        assert not self.ingested, "the snapshot comes before the first page"
        self.snapshots.append(name)
        return name

    def ingest_document(self, source, content, meta):
        self.ingested.append(source)
        return True


def installed_pack(cm, name, text, topic="docs"):
    """An installed knowledge pack as finish_install leaves it: community/knowledge/<topic>/<name>/ with its
    knowledge.json and its provenance (a loose file is never imported: DECISIONS round 23 item 1)."""
    from orchestrator.Core.community_manager import Provenance
    from orchestrator.Core.community_pack import PROVENANCE_NAME, tree_sha256, utc_now
    folder = cm.knowledge_dir / topic / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "knowledge.json").write_text(text)
    prov = Provenance(kind="knowledge", id=name, version="1.0.0", trust="local", source="a test", pack_sha256="0" * 64,
                      content_sha256=tree_sha256(folder), author="t", installed_at=utc_now(), topic=topic)
    (folder / PROVENANCE_NAME).write_text(prov.to_json())
    return folder


def pack_manager(tmp_path, rag):
    framework = SimpleNamespace(info=SimpleNamespace(installation_path=tmp_path),
                                get_component=lambda name: rag if name == "rag_manager" else None)
    cm = CommunityManager(framework)
    installed_pack(cm, "docs", '[{"source": "s", "content": "c", "metadata": {}}]')
    return cm


def test_a_knowledge_pack_import_takes_a_snapshot_first(tmp_path):
    rag = PackRag()
    cm = pack_manager(tmp_path, rag)
    assert cm.sync_knowledge_base() == 1
    assert len(rag.snapshots) == 1 and rag.snapshots[0].startswith("pre_pack_")
    assert cm.last_sync_snapshot == rag.snapshots[0] and cm.last_sync_problem == ""
    assert cm.sync_knowledge_base() == 0 and len(rag.snapshots) == 1, "nothing left to import: no snapshot"


def test_a_knowledge_pack_is_not_imported_when_its_snapshot_does_not_fit(tmp_path):
    rag = PackRag(no_room=True)
    cm = pack_manager(tmp_path, rag)
    assert cm.sync_knowledge_base() == 0 and rag.ingested == []
    assert "Not enough disk space" in cm.last_sync_problem
    assert not (cm.knowledge_dir / "docs.imported").exists(), "imported later, once there is room"
    rag.no_room = False
    assert cm.sync_knowledge_base() == 1 and rag.ingested == ["s"]


def test_a_pack_that_stays_pending_is_snapshotted_once_not_at_every_start(tmp_path):
    """A pack that cannot be imported yet (its pages are refused, e.g. offline without the embedding model) stays
    pending, and the framework tries again whenever the knowledge base starts. The snapshot before its import is
    taken once: snapshots are never deleted by themselves, so one per start would fill the disk. A pack installed
    later gets its own snapshot before its first page. Why a pack waits is said, not only logged."""
    rag = PackRag()
    rag.ingest_document = lambda source, content, meta: False  # refused every time
    cm = pack_manager(tmp_path, rag)
    installed_pack(cm, "old", '[{"source": "o", "content": "c", "metadata": {}}]')
    for _ in range(3):
        assert cm.sync_knowledge_base() == 0
        assert "not imported yet" in cm.last_sync_problem
    assert len(rag.snapshots) == 1, "one snapshot for the packs that wait, not one per start"
    assert not (cm.knowledge_dir / "docs.imported").exists(), "still pending: imported once it can be"
    installed_pack(cm, "new", '[{"source": "n", "content": "c", "metadata": {}}]')
    assert cm.sync_knowledge_base() == 0 and len(rag.snapshots) == 2, "a new pack: a snapshot before its import"
    rag.ingest_document = lambda source, content, meta: rag.ingested.append(source) or True
    assert cm.sync_knowledge_base() == 3 and len(rag.snapshots) == 2
    # installed again (an earlier version was removed): its marker goes, and it gets a snapshot of its own again
    rag.ingested.clear()
    (cm.knowledge_dir / "docs.imported").unlink()
    docs = installed_pack(cm, "docs", '[{"source": "s2", "content": "c2", "metadata": {}}]') / "knowledge.json"
    os.utime(docs, ns=(docs.stat().st_atime_ns, docs.stat().st_mtime_ns + 10 ** 9))
    assert cm.sync_knowledge_base() == 1 and len(rag.snapshots) == 3


def test_the_snapshot_list_names_pack_backups(tmp_path):
    folder = tmp_path / "knowledge_snapshots"
    folder.mkdir()
    (folder / "pre_pack_1.jsonl.gz").write_bytes(b"x")
    assert ks.list_snapshots(folder)[0].what == "backup of the knowledge base taken before a knowledge pack was imported"


# --- the tools: a blocker before the question, a situation warning when tight ------------------------------------

def test_crawl_plan_blocks_and_warns(tmp_path, disk):
    from orchestrator.Core.crawl_jobs import CrawlJobService, CrawlRefused
    from orchestrator.Core.job_service import JobService
    from orchestrator.tools.tools_crawl import _plan_facts
    from orchestrator.utils.crawl_notice import CrawlNotice
    state = tmp_path / "state"
    folder = state / "knowledge_snapshots"
    folder.mkdir(parents=True)
    (folder / "pre_ingest_1.jsonl.gz").write_bytes(b"x" * 1000)
    CrawlNotice(state).acknowledge("gui")
    settings = {"enable_rag_knowledge": True}
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: settings.get(k, d),
                                                       user_state_dir=str(state)),
                                get_component=lambda name: object() if name == "rag_manager" else None)
    service = CrawlJobService(framework, JobService())
    disk(int(2.1 * GB))
    with pytest.raises(CrawlRefused) as exc:
        service.plan(["http://93.184.215.14/docs/"], 5, 1, None, None, "mcp")
    assert exc.value.blocker == "snapshot_no_room" and exc.value.code == "insufficient_resources"
    disk(int(3 * GB))
    facts = _plan_facts(service.plan(["http://93.184.215.14/docs/"], 5, 1, None, None, "mcp"))
    assert [w.code for w in facts.warnings] == ["disk_tight"] and facts.warnings[0].kind == "situation"
    disk(500 * GB)
    facts = _plan_facts(service.plan(["http://93.184.215.14/docs/"], 5, 1, None, None, "mcp"))
    assert facts.warnings == ()
    disk_fact = next(f for f in facts.facts if f.label == "Disk")
    assert disk_fact.volatile and str(state) not in disk_fact.value


def test_restore_knowledge_snapshot_is_refused_before_the_question(env, disk):  # noqa: F811
    """The backup taken first does not fit: the blocker with the person's action, before anyone is asked."""
    from test_optional_tools import FakeRag, ask, enable
    rag = FakeRag(env.tmp / "state" / "knowledge_snapshots")
    env.framework.ensure_rag_manager = lambda: rag
    env.framework.build_service = None  # the check reads the snapshot folder's disk itself
    enable(env, "restore_knowledge_snapshot")
    disk(int(2.1 * GB))
    with pytest.raises(ToolError) as exc:
        ask(env, "restore_knowledge_snapshot", {"snapshot": "pre_crawl_1"})
    assert exc.value.code == "insufficient_resources" and exc.value.blocker == "snapshot_no_room"
    assert exc.value.person_action and rag.restored == []
    disk(int(3 * GB))
    spec = env.registry.get("restore_knowledge_snapshot")
    facts = env.registry.look_up(spec, env.registry.validate_input(spec, {"snapshot": "pre_crawl_1"}),
                                 ToolContext("mcp", session="opt"))
    assert [w.code for w in facts.warnings] == ["disk_tight"], "a situation warning when it fits but is tight"


def test_the_crawl_job_stops_when_the_next_snapshot_does_not_fit(tmp_path):
    from orchestrator.Core.crawl_jobs import CrawlJobService, CrawlPlan, CrawlRefused
    from orchestrator.Core.job_service import JobService

    class Rag:
        def ingest_url(self, url, options, stop=None, progress=None):
            return {"outcome": "failure", "success": False, "blocker": "snapshot_no_room", "documents": 0,
                    "message": "Not enough disk space for the knowledge snapshot taken before a Deep Ingest."}

    service = CrawlJobService(SimpleNamespace(config=None), JobService())
    plan = CrawlPlan(("http://93.184.215.14/docs/", "http://93.184.215.15/guide/"), 5, 1, 1.0, ("en",), True)
    handle = SimpleNamespace(progress=lambda *a: None)
    with pytest.raises(CrawlRefused) as exc:
        service._crawl(plan, Rag(), __import__("threading").Event(), handle, "mcp")
    assert exc.value.blocker == "snapshot_no_room"


# --- the doctor's knowledge line ----------------------------------------------------------------------------------

def doctor_framework(state):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state))


def test_the_doctor_shows_the_snapshots_against_the_free_space(tmp_path, disk):
    from orchestrator.Core.doctor import Severity, check_knowledge
    folder = tmp_path / "knowledge_snapshots"
    folder.mkdir()
    (folder / "pre_ingest_1.jsonl.gz").write_bytes(b"x" * 1000)
    disk(500 * GB)
    ok = check_knowledge(doctor_framework(tmp_path))[0]
    assert ok.severity == Severity.OK and "500.0 GB free on that disk" in ok.message
    disk(int(3 * GB))
    tight = check_knowledge(doctor_framework(tmp_path))[0]
    assert tight.severity == Severity.INFO and "little free space" in tight.message
    disk(int(2.1 * GB))
    short = check_knowledge(doctor_framework(tmp_path))[0]
    assert short.severity == Severity.WARN and "does not fit" in short.message
    assert "Knowledge Snapshots" in short.fix_hint and short.fix is None, "the doctor never deletes a snapshot"
    assert files_of(folder) == ["pre_ingest_1.jsonl.gz"]
    # Mojo (round 7 item T): where, why, and that only the person's list frees the room; Mojo Miracle never deletes
    # a snapshot and opens no list of its own for it
    from orchestrator.Core.mojo.offers import DELETION_LISTS, REASONS, offer_for
    assert short.where == str(folder) and "does not run" in short.why and short.cannot == "deletion_list"
    assert tight.where == str(folder) and tight.why
    offer = offer_for(doctor_framework(tmp_path), short)
    assert offer.verdict == "deletion_list" and offer.reason == REASONS["deletion_list"]
    assert short.code not in DELETION_LISTS, "the framework's image list frees no snapshot room"


def test_mojo_watches_the_build_folder_and_labels_it_for_an_ai(tmp_path, monkeypatch):
    """The framework's build folder (golden artifacts not saved yet) gets its own disk line, the build image list as
    the person's way to free space, and the label 'builds' wherever an AI reads its path."""
    from orchestrator.Core.mojo.checks import disk as disk_check
    from orchestrator.Core.mojo.findings import Severity
    from orchestrator.Core.mojo.offers import DELETION_LISTS, offer_for
    from orchestrator.Core.mojo.views import ai_folder_labels
    builds = tmp_path / "builds"
    builds.mkdir()
    engine = SimpleNamespace(cache_dir=None, output_dir=None, build_dir=builds)
    framework = SimpleNamespace(orchestrator=SimpleNamespace(build_engine=engine),
                                config={"build_dir": str(builds)}, resolve_path=lambda v: Path(v))
    monkeypatch.setattr(disk_check.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(TOTAL, TOTAL - GB, GB))
    (finding,) = disk_check.check_disk(framework)
    assert finding.code == "disk.builds" and finding.severity == Severity.ERROR and finding.where == str(builds)
    assert DELETION_LISTS["disk.builds"] == "framework_images"
    assert offer_for(framework, finding).verdict == "deletion_list"
    assert ai_folder_labels(framework)[str(builds)] == "builds"


# --- the person's list in the GUI ---------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
def test_the_gui_offers_the_snapshot_list_and_shows_the_free_space(tmp_path, disk, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import knowledge_snapshots_window as window, plain
    folder = tmp_path / "knowledge_snapshots"
    folder.mkdir()
    (folder / "pre_ingest_1.jsonl.gz").write_bytes(b"x" * 1000)
    disk(500 * GB)
    dialog = window.KnowledgeSnapshotsDialog(tmp_path, framework=None, lang="en")
    assert "500.0 GB are free on that disk" in dialog.disk.text()
    dialog.close()
    boxes, opened = [], []
    monkeypatch.setattr(window.KnowledgeSnapshotsDialog, "exec", lambda self: opened.append(self.folder) or 0)
    monkeypatch.setattr(plain, "_exec", lambda box: boxes.append(box) or QMessageBox.Close)
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path))
    assert window.offer_snapshot_list(None, framework, "Not enough disk space <b>x</b>", "en") is False
    assert opened == [] and boxes[0].defaultButton() == boxes[0].button(QMessageBox.Close), "Close is the default"
    monkeypatch.setattr(plain, "_exec", lambda box: QMessageBox.Open)
    assert window.offer_snapshot_list(None, framework, "Not enough disk space", "en") is True
    assert opened == [folder] and files_of(folder) == ["pre_ingest_1.jsonl.gz"], "nothing is deleted by itself"
