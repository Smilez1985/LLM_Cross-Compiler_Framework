"""Doctor fixes (delegation design round 5 f; the Doctor study, section 4):

1. `doctor --fix --tier 2` (released in 3.0.0) cleared Docker's whole build cache - other projects' too - and the
   framework's dangling images without a list. Now the build cache is never touched, tier 2 lists the framework's
   dangling images (name or id, size, what each is), and only the person's yes at a terminal deletes exactly that
   list; never over MCP, never under a delegation. The self-healing "Docker cleanup" (docker_prune, also released in
   3.0.0) did the same after a plain yes; it is now a manual step that points to that list.
2. `run_doctor` gave AI clients absolute host paths (the per-user folder, the audit log, pack folders, a crashed
   check's message). An AI now reads folder labels and file names; the person's own output keeps the full paths.
3. A new knowledge snapshot deleted the oldest ones beyond five, on every channel. Now there is no limit at all
   (DECISIONS, sixth round): no snapshot is deleted by itself, no count reminds or warns, and `llm-cli knowledge
   snapshots` (GUI: Tools > Knowledge Snapshots...) lists them for the person, who deletes or exports them.
"""

import io
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import doctor as doctor_module
from orchestrator.Core.builder import IMAGE_LABEL
from orchestrator.Core.doctor import (Doctor, ImagePlanChanged, check_disk, check_knowledge, delete_framework_images,
                                      framework_image_plan)
from orchestrator.tools.registry import ToolContext

REPO = Path(__file__).resolve().parent.parent


# --- a fake Docker that records everything ---------------------------------------------------------------------------

class FakeImage:
    def __init__(self, char, size, target="aarch64", tags=(), labelled=True, created="2026-10-01T10:00:00Z"):
        self.id = "sha256:" + char * 64
        self.tags = list(tags)
        self.labels = {IMAGE_LABEL: target} if labelled else {"org.other.project": "x"}
        self.attrs = {"Size": size, "Created": created}


class FakeImages:
    def __init__(self, events, images):
        self.events = events
        self.images = list(images)
        self.filters = []
        self.removed = []

    def list(self, filters=None):
        self.filters.append(filters)
        return list(self.images)  # every image, whatever the filter: the framework checks on its own

    def get(self, name):
        found = next((i for i in self.images if i.id == name), None)
        if found is None:
            raise RuntimeError(f"No such image: {name}")
        return found

    def remove(self, image=None, force=False, noprune=False, **kwargs):
        self.events.append(("remove", image, force, noprune))
        if image == getattr(self, "refuse", None):
            raise RuntimeError("conflict: image is being used by stopped container 1234")
        self.removed.append(image)
        named = next((i for i in self.images if image in i.tags), None)
        if named is not None:   # a name: Docker drops it, and the image with its last name
            named.tags = [t for t in named.tags if t != image]
            if named.tags:
                return
            image = named.id
        self.images = [i for i in self.images if i.id != image]
        hook = getattr(self, "after_remove", None)
        if hook is not None:
            hook(image)

    def prune(self, *args, **kwargs):
        self.events.append(("prune", args, kwargs))


class FakeClient:
    def __init__(self, images=()):
        self.events = []
        self.images = FakeImages(self.events, images)
        self.api = SimpleNamespace(prune_builds=lambda *a, **k: self.events.append(("prune_builds", a, k)))

    def ping(self):
        return True


class FakeAudit:
    def __init__(self, events, fails=False):
        self.events = events
        self.fails = fails
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fails:
            raise OSError("disk full")
        self.events.append(("audit", outcome))
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def fake_framework(client, audit_fails=False):
    audit = FakeAudit(client.events, fails=audit_fails) if client is not None else None
    return SimpleNamespace(get_component=lambda name: client if name == "docker_client" else None,
                           tool_registry=SimpleNamespace(audit=audit))


def deletions(client):
    return [e for e in client.events if e[0] in ("remove", "prune", "prune_builds")]


def images():
    return [FakeImage("a", 2_000_000_000), FakeImage("b", 300_000_000, target="x86_64"),
            FakeImage("c", 5, tags=["lcf-build:42"]),          # named: not dangling
            FakeImage("d", 7, labelled=False),                 # another project's image
            FakeImage("e", 11, target="evil name; rm -rf")]    # the framework's label, an odd value


# --- 1. tier 2 lists, the person deletes exactly the list ----------------------------------------------------------

def test_the_doctor_never_prunes_docker():
    """No unfiltered build-cache prune and no image prune anywhere in the doctor or its command: a prune cannot be
    limited to the framework's own data, or to the list the person saw."""
    from orchestrator import cli
    import inspect
    # the compatibility module and the Mojo package it re-exports (the Doctor's code moved there unchanged)
    mojo = Path(doctor_module.__file__).parent / "mojo"
    sources = [Path(doctor_module.__file__).read_text(encoding="utf-8"),
               *(p.read_text(encoding="utf-8") for p in sorted(mojo.rglob("*.py"))),
               inspect.getsource(cli.doctor.callback), inspect.getsource(cli._doctor_free_space),
               inspect.getsource(cli._doctor_image_listing)]
    for source in sources:
        assert "prune_builds" not in source and ".prune(" not in source


def test_fix_tier_2_deletes_nothing_by_itself_and_says_what_it_lists(tmp_path, monkeypatch):
    client = FakeClient(images())
    framework = fake_framework(client)
    framework.orchestrator = SimpleNamespace(build_engine=SimpleNamespace(cache_dir=tmp_path, output_dir=None))
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=1 * 1024 ** 3))
    report = Doctor(framework, [check_disk]).fix(tier_max=2)
    assert deletions(client) == [], "tier 2 is a listing; nothing is deleted without the person's yes"
    finding = next(f for f in report.findings if f.code == "disk.cache")
    assert finding.fix is None and finding.fix_tier == 2
    hint = finding.fix_hint
    for words in ("lists the framework's build images", "size", "what it is", "tick and confirm", "in a terminal",
                  "Tools > Framework Build Images...", "nothing is ticked by default", "build cache is not touched"):
        assert words in hint, words
    assert "removes" not in hint


def test_the_plan_lists_only_the_frameworks_images():
    from orchestrator.runtime.build.executor import BUILDER_LABEL
    client = FakeClient(images())
    plan = framework_image_plan(fake_framework(client))
    assert client.images.filters == [{"label": IMAGE_LABEL}, {"label": BUILDER_LABEL}]
    assert [i.file_name for i in plan.items] == ["sha256:" + c * 64 for c in "abe"]
    first = plan.items[0]
    assert first.kind == "docker_image" and first.full_path == "" and first.size_bytes == 2_000_000_000
    assert first.speaking_name == "Framework build image aaaaaaaaaaaa (aarch64)"
    assert "for aarch64, without a name (built 2026-10-01)" in first.what
    assert plan.items[2].speaking_name.endswith("(an unknown target)"), "an odd label value is never shown"
    assert plan.total_bytes == 2_300_000_011 and plan.mode_offered == ("permanent",)
    assert deletions(client) == []
    assert framework_image_plan(fake_framework(FakeClient([FakeImage("c", 5, tags=["x"])]))) is None


def test_the_person_deletes_exactly_the_listed_images_after_the_audit():
    client = FakeClient(images())
    framework = fake_framework(client)
    plan = framework_image_plan(framework)
    result = delete_framework_images(framework, plan, actor="cli")
    assert client.images.removed == [i.file_name for i in plan.items]
    assert all(e[0] == "remove" and e[2] is False and e[3] is True for e in deletions(client)), \
        "by id, never forced, never with untagged parents, never a prune"
    assert client.events[0] == ("audit", "started"), "audited before anything is deleted"
    assert result.freed_bytes == plan.total_bytes and len(result.deleted) == 3 and result.failed == []
    entry = framework.tool_registry.audit.entries[0]
    assert entry["extra"]["deletion_sha256"] == plan.sha256() and entry["extra"]["answered_by"] == "person"


def test_a_changed_list_deletes_nothing():
    client = FakeClient(images())
    framework = fake_framework(client)
    plan = framework_image_plan(framework)
    client.images.images[1].attrs["Size"] = 400_000_000   # a chosen image is not the one the person saw
    with pytest.raises(ImagePlanChanged):
        delete_framework_images(framework, plan, actor="cli")
    client.images.images = [i for i in client.images.images if i.id != "sha256:" + "b" * 64]   # one went away
    with pytest.raises(ImagePlanChanged):
        delete_framework_images(framework, plan, actor="cli")
    assert deletions(client) == []


def test_an_image_that_appeared_after_the_list_is_never_deleted():
    """The person's choice covers exactly the images they ticked: an image that became dangling after the list was
    shown is not part of it and stays."""
    client = FakeClient(images())
    framework = fake_framework(client)
    plan = framework_image_plan(framework)
    client.images.images.append(FakeImage("f", 99))
    delete_framework_images(framework, plan, actor="cli")
    assert "sha256:" + "f" * 64 not in client.images.removed
    assert [i.id for i in client.images.images] == ["sha256:" + c * 64 for c in "cdf"]


def test_a_named_image_kept_half_way_says_which_names_went():
    """A named image is deleted name by name; when Docker keeps it at its last name (a container still uses it), the
    report says which names were removed and that the image stays, never only "kept"."""
    image = FakeImage("f", 9, tags=["llm-framework/aarch64:b7", "llm-framework/aarch64:last-build"])
    client = FakeClient([image])
    framework = fake_framework(client)
    plan = framework_image_plan(framework)
    client.images.refuse = "llm-framework/aarch64:last-build"
    result = delete_framework_images(framework, plan, actor="cli")
    assert result.deleted == [] and len(result.failed) == 1
    reason = result.failed[0][1]
    assert "conflict" in reason and "llm-framework/aarch64:b7" in reason and "image itself stays" in reason
    assert image.tags == ["llm-framework/aarch64:last-build"]


@pytest.mark.parametrize("actor", ["mcp", "mcp:laptop", "ditto", "grant:g_0123456789abcdef",
                                   "delegation:d_0123456789abcdef", ""])
def test_only_the_person_deletes_images(actor):
    client = FakeClient(images())
    framework = fake_framework(client)
    with pytest.raises(PermissionError):
        delete_framework_images(framework, framework_image_plan(framework), actor=actor)
    assert deletions(client) == []


def test_without_an_audit_entry_nothing_is_deleted():
    client = FakeClient(images())
    framework = fake_framework(client, audit_fails=True)
    with pytest.raises(RuntimeError, match="no image was deleted"):
        delete_framework_images(framework, framework_image_plan(framework), actor="gui")
    assert deletions(client) == []


def test_an_image_named_while_the_list_is_deleted_is_kept():
    """Docker removes an image with one name when it is addressed by its id. A build of another framework process can
    name a listed image again (the same layers) after the list was looked up again: each image is looked at right
    before its removal and kept when it has a name or is no longer the framework's."""
    client = FakeClient(images())
    framework = fake_framework(client)
    plan = framework_image_plan(framework)
    by_id = {i.id: i for i in client.images.images}

    def a_build_names_b(removed):
        if removed == "sha256:" + "a" * 64:
            by_id["sha256:" + "b" * 64].tags = ["lcf-build:43"]
            by_id["sha256:" + "e" * 64].labels = {}
    client.images.after_remove = a_build_names_b
    result = delete_framework_images(framework, plan, actor="cli")
    assert client.images.removed == ["sha256:" + "a" * 64], "only the image that was still unnamed and the framework's"
    assert [name for name, _ in result.failed] == ["Framework build image bbbbbbbbbbbb (x86_64)",
                                                    "Framework build image eeeeeeeeeeee (an unknown target)"]
    assert all("kept" in reason for _, reason in result.failed)
    assert result.freed_bytes == 2_000_000_000


def test_an_image_docker_refuses_is_kept_and_reported():
    client = FakeClient(images())
    client.images.refuse = "sha256:" + "b" * 64
    framework = fake_framework(client)
    result = delete_framework_images(framework, framework_image_plan(framework), actor="cli")
    assert len(result.deleted) == 2 and [name for name, _ in result.failed] == \
        ["Framework build image bbbbbbbbbbbb (x86_64)"]
    assert framework.tool_registry.audit.entries[-1]["outcome"] == "error"


def test_no_ai_path_reaches_the_image_deletion():
    """Nothing an AI can call (tools, resources, prompts, the MCP server) reaches the listing or the deletion."""
    names = ("delete_framework_images", "framework_image_plan", "_doctor_free_space")
    for path in [*(REPO / "orchestrator" / "tools").glob("*.py"), REPO / "orchestrator" / "mcp_server.py",
                 *(REPO / "orchestrator" / "llm").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert not any(name in text for name in names), path.name


@pytest.fixture
def docker_framework(booted_framework, monkeypatch):
    client = FakeClient(images())
    real = booted_framework.get_component
    monkeypatch.setattr(booted_framework, "get_component",
                        lambda name: client if name == "docker_client" else real(name))
    return booted_framework, client


def run_cli(framework, monkeypatch, *args, terminal=False, answer=None):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
    result = CliRunner().invoke(cli_module.cli, list(args), obj=Ctx(), input=answer)
    return result, printed.getvalue() + result.output


def test_cli_tier_2_without_a_terminal_lists_and_deletes_nothing(docker_framework, monkeypatch):
    framework, client = docker_framework
    _, out = run_cli(framework, monkeypatch, "doctor", "--fix", "--tier", "2")
    assert "Framework build image aaaaaaaaaaaa (aarch64)" in out and "sha256:" + "a" * 64 in out
    assert "2.0 GB" in out and "without a name" in out and "build cache is not touched" in out
    assert "Nothing was deleted" in out and deletions(client) == []


def test_cli_tier_2_needs_the_persons_yes(docker_framework, monkeypatch):
    """Multiple choice (round 6 10.4): nothing is ticked; the person names the images, then confirms exactly those
    (default no)."""
    framework, client = docker_framework
    _, out = run_cli(framework, monkeypatch, "doctor", "--fix", "--tier", "2", terminal=True, answer="\n")
    assert "Numbers to delete" in out and "Nothing was deleted." in out
    assert deletions(client) == [], "nothing is ticked by default"
    _, out = run_cli(framework, monkeypatch, "doctor", "--fix", "--tier", "2", terminal=True, answer="1,3\n\n")
    assert "Delete exactly 2 items (2.0 GB) for good?" in out and "Nothing was deleted." in out
    assert deletions(client) == [], "the default answer is no"
    _, out = run_cli(framework, monkeypatch, "doctor", "--fix", "--tier", "2", terminal=True, answer="1,3\ny\n")
    assert client.images.removed == ["sha256:" + c * 64 for c in "ae"], "exactly the ticked images"
    assert "Deleted: Framework build image aaaaaaaaaaaa (aarch64)" in out and "2 images deleted" in out


def test_cli_json_and_tier_1_never_delete(docker_framework, monkeypatch):
    framework, client = docker_framework
    result, _ = run_cli(framework, monkeypatch, "doctor", "--json", "--fix", "--tier", "2", terminal=True,
                        answer="y\n")
    data = json.loads(result.stdout)
    listing = data["framework_images"]
    assert listing["deleted"] is False and [i["id"] for i in listing["images"]] == \
        ["sha256:" + c * 64 for c in "abe"]
    _, out = run_cli(framework, monkeypatch, "doctor", "--fix", terminal=True, answer="y\n")
    assert "Tier 2" not in out
    assert deletions(client) == []


def test_the_tool_never_deletes_images(docker_framework):
    framework, client = docker_framework
    framework.tool_registry.call("run_doctor", {}, ToolContext("mcp"))
    assert deletions(client) == []


# --- 1b. the self-healing "Docker cleanup" had the same flaw (released in 3.0.0) -----------------------------------

def test_no_code_of_the_framework_clears_dockers_build_cache():
    """Docker's build cache belongs to every project on the computer and cannot be limited to the framework: nothing
    in the framework clears it (doctor tier 2 and the self-healing strategy docker_prune both did)."""
    hits = [p.relative_to(REPO).as_posix() for p in (REPO / "orchestrator").rglob("*.py")
            if "prune_builds" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_the_docker_cleanup_proposal_deletes_nothing_and_points_to_the_list():
    """A full Docker disk is a manual step: approving it can delete nothing, on any channel. The person frees space
    with the doctor's list (`doctor --fix --tier 2`), which deletes only what they confirmed there."""
    from orchestrator.Core import build_service
    from orchestrator.Core.self_healing_manager import (AUTOMATIC_STRATEGIES, HealingProposal, HealingStrategy,
                                                        SelfHealingManager)
    client = FakeClient(images())
    healer = SelfHealingManager(SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default),
                                                get_component=lambda name: client))
    proposal = HealingProposal("Disk full", "cause", "anything", 1.0, HealingStrategy.DOCKER_PRUNE, "HOST")
    assert healer.apply_fix(proposal) is False
    assert deletions(client) == []
    assert "docker_prune" not in AUTOMATIC_STRATEGIES and "docker_prune" not in build_service.AUTOMATIC_STRATEGIES
    texts = " ".join(text for _, _, text, strategy, _ in healer.known_issues if strategy == HealingStrategy.DOCKER_PRUNE)
    assert "llm-cli doctor --fix --tier 2" in texts and "build cache is not touched" in texts
    assert "Remove dangling" not in texts


def test_the_cli_and_the_gui_show_the_docker_cleanup_as_a_manual_step(monkeypatch, capsys, qapp):
    from orchestrator import cli
    from orchestrator.Core.self_healing_manager import HealingProposal, HealingStrategy
    from orchestrator.gui.dialogs import HealingConfirmDialog
    proposal = HealingProposal("Disk full", "cause", "llm-cli doctor --fix --tier 2", 1.0,
                               HealingStrategy.DOCKER_PRUNE, "HOST")
    decisions, asked = [], []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.click, "confirm", lambda *a, **k: asked.append(a) or True)
    cli._resolve_healing_interactively(SimpleNamespace(resolve_healing=lambda rid, ok: decisions.append(ok)), "r1",
                                       proposal)
    assert asked == [] and decisions == [False], "a person at a terminal is not asked to apply it"
    assert "Manual step" in capsys.readouterr().out
    dialog = HealingConfirmDialog(proposal)
    try:
        assert dialog.automatic is False
    finally:
        dialog.close()


# --- 2. an AI reads labels, the person reads paths ------------------------------------------------------------------

ABSOLUTE = re.compile(r"(?:^|[\s('\"=])(?:/|~/|[A-Za-z]:\\)[^\s'\")]+")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_run_doctor_gives_an_ai_no_host_path_and_the_person_keeps_it(booted_framework, monkeypatch, tmp_path):
    framework = booted_framework
    state = Path(framework.config.user_state_dir)
    audit = framework.tool_registry.audit

    def check_user_state(_fw):  # reading config.user_state_dir makes the folder private again: opened right here
        os.chmod(state, 0o755)
        return doctor_module.check_user_state(SimpleNamespace(config=SimpleNamespace(user_state_dir=state)))

    monkeypatch.setattr(audit, "check", lambda: SimpleNamespace(  # tools.audit names the audit log twice
        problem="modified", message=f"Entry 3 of {audit.path} was changed.", keyed=True, key_available=True))
    pack = Path(framework.target_manager.targets_dir) / "Revoked Pack"
    prov = SimpleNamespace(kind="target", id="revoked-pack", version="1.0.0")
    monkeypatch.setattr(framework.community_manager, "verify_installed",
                        lambda: [(pack, prov, SimpleNamespace(value="revoked"), ["revoked: key leaked"])])

    def check_broken(_fw):
        raise RuntimeError(f"cannot read {Path.home() / 'notes' / 'private.txt'}")
    checks = [check_user_state if c is doctor_module.check_user_state else c for c in doctor_module.DEFAULT_CHECKS]
    monkeypatch.setattr(doctor_module, "DEFAULT_CHECKS", [*checks, check_broken])
    snapshot(state / "knowledge_snapshots", "pre_ingest_1")  # the knowledge line names its folder

    out = framework.tool_registry.call("run_doctor", {}, ToolContext("mcp"))
    found = {f.code: f for f in out.findings}
    texts = [t for f in out.findings for t in (f.message, f.fix_hint)]
    for text in texts:
        for secret in (str(tmp_path), str(Path.home()), str(REPO), str(state)):
            assert secret not in text, (secret, text)
        assert not ABSOLUTE.search(text), text
    assert found["state.permissions"].message.startswith("the state folder is readable by other users")
    assert "state:audit.db" in found["tools.audit"].message and "state:audit.db" in found["tools.audit"].fix_hint
    assert "targets:Revoked Pack" in found["community.revoked-pack.revoked"].fix_hint
    assert "private.txt" in found["broken.check_crashed"].message
    assert "state:knowledge_snapshots" in found["knowledge.snapshots"].message

    # The person's own output keeps every full path.
    from click.testing import CliRunner
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    data = json.loads(CliRunner().invoke(cli_module.cli, ["doctor", "--json"], obj=Ctx()).stdout)
    person = {f["code"]: f for f in data["findings"]}
    assert str(state) in person["state.permissions"]["message"]
    assert str(audit.path) in person["tools.audit"]["message"]
    assert str(pack) in person["community.revoked-pack.revoked"]["fix_hint"]
    assert str(state / "knowledge_snapshots") in person["knowledge.snapshots"]["message"]


# --- 3. no snapshot is deleted by itself ------------------------------------------------------------------------------

def snapshot(folder, name, size=10, when=1_000_000):
    """A snapshot file of `size` bytes, sparse: it takes no disk space (the test disk is small)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl.gz"
    with open(path, "wb") as f:
        f.truncate(size)
    os.utime(path, (when, when))
    return path


def test_new_snapshots_never_delete_older_ones_and_post_no_reminder(tmp_path, monkeypatch):
    """create_snapshot (every ingest, every restore's backup, on every channel) keeps every snapshot, however many
    there are: no limit, no count that deletes, reminds or warns (sixth round: as many as the disk holds)."""
    from test_optional_tools import Store, rag_with
    from orchestrator.Core import request_store
    from orchestrator.Core.chat_feed import FeedStore
    rag = rag_with(tmp_path, Store())
    feed = FeedStore(tmp_path / "feed", key=b"k" * 32)
    rag.framework = SimpleNamespace(chat_feed=feed)
    monkeypatch.setitem(request_store._PROGRAM, "kind", "gui")
    for i in range(40):
        snapshot(rag.snapshot_dir, f"pre_ingest_{i}", when=1_000_000 + i)
    assert rag.create_snapshot("pre_ingest_new")
    assert rag.restore_snapshot("pre_ingest_0")
    names = rag.list_snapshots()
    assert len(names) == 42 and "pre_ingest_0" in names and "pre_ingest_new" in names
    assert feed.after(0).entries == [], "no reminder pushes the person to delete"


def test_there_is_no_snapshot_limit():
    """No constant caps the snapshots and no text speaks of a usual number (sixth round: no limit at all); the
    person deletes with the full list, or exports, when they want to free space."""
    import inspect

    from orchestrator import cli
    from orchestrator.Core import chat_feed, knowledge_snapshots, rag_manager
    from orchestrator.gui import knowledge_snapshots_window
    for module in (knowledge_snapshots, rag_manager, knowledge_snapshots_window):
        assert not hasattr(module, "MAX_SNAPSHOTS"), module.__name__
    assert not hasattr(knowledge_snapshots, "remind_over_cap")
    assert not any("snapshot" in key for texts in chat_feed.TEXTS.values() for key in texts)
    sources = [inspect.getsource(knowledge_snapshots), inspect.getsource(knowledge_snapshots_window),
               inspect.getsource(doctor_module.check_knowledge), inspect.getsource(cli.knowledge_snapshots_list.callback),
               inspect.getsource(rag_manager.RAGManager.create_snapshot)]
    for source in sources:
        assert "usual" not in source and "over_cap" not in source and "MAX_SNAPSHOTS" not in source
    # the old pruning by count is gone: the person's clean-up deletes exactly the snapshots of an accepted plan (WP7)
    assert not hasattr(rag_manager.RAGManager, "_prune_snapshots")
    assert list(inspect.signature(rag_manager.RAGManager.delete_snapshots).parameters)[:2] == ["self", "accepted"]
    assert list(inspect.signature(knowledge_snapshots.delete_snapshots).parameters)[:2] == ["folder", "accepted"]


def test_the_facts_vocabulary_has_no_snapshot_count_and_no_deleting_healing():
    """One facts vocabulary with the doctor's rules (WP4 merged with the doctor fixes): no warning counts knowledge
    snapshots against a cap (sixth round: no limit, none deleted by itself), in the catalogue, in a tool's
    classification or in the person's texts; and since no healing strategy deletes anything, resolve_healing's
    classification carries no deletion plan."""
    from orchestrator.tools import facts_catalog
    from orchestrator.tools.framework_tools import DESTRUCTIVE_STRATEGIES
    from orchestrator.utils import l10n_facts
    assert not any("snapshot" in code for code in facts_catalog.WARNINGS)
    assert not any("snapshot" in code for row in facts_catalog.TOOL_FACTS.values() for code in row.situation)
    assert not any("snapshot" in key and key.startswith("warning.") for texts in l10n_facts.TEXTS.values()
                   for key in texts)
    assert not DESTRUCTIVE_STRATEGIES
    assert facts_catalog.TOOL_FACTS["resolve_healing"].deletes is False
    assert facts_catalog.TOOL_FACTS["delete_artifact"].deletes is True, "a real deletion keeps its mark"


def test_nothing_calls_the_snapshot_pruning():
    """Deleting snapshots is the person's clean-up (WP7): only the CLI's question at a terminal and the GUI's deletion
    dialog call it (the RAG manager's method only hands the accepted plan on); nothing deletes by count or age."""
    callers = sorted(p.relative_to(REPO).as_posix() for p in (REPO / "orchestrator").rglob("*.py")
                     if "delete_snapshots(" in p.read_text(encoding="utf-8").replace("def delete_snapshots(", ""))
    assert callers == ["orchestrator/Core/rag_manager.py", "orchestrator/cli.py",
                       "orchestrator/gui/knowledge_snapshots_window.py"]
    hits = [p.relative_to(REPO).as_posix() for p in (REPO / "orchestrator").rglob("*.py")
            if "_prune_snapshots" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_the_doctor_lists_the_snapshots_and_never_asks_to_delete_them(tmp_path):
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path))
    folder = tmp_path / "knowledge_snapshots"
    assert check_knowledge(framework) == []
    for i in range(40):
        snapshot(folder, f"pre_ingest_{i}", size=1_500_000, when=1_000_000 + i)
    finding = check_knowledge(framework)[0]
    assert finding.code == "knowledge.snapshots" and finding.severity.value == "ok" and finding.fix is None
    assert "40 knowledge snapshots (60.0 MB)" in finding.message and str(folder) in finding.message
    assert "no limit" in finding.message and "llm-cli knowledge snapshots" in finding.message
    assert "more than" not in finding.message.lower() and "delete" not in finding.fix_hint
    assert len(list(folder.iterdir())) == 40


def test_cli_lists_every_snapshot_with_full_path_name_size_and_what(booted_framework, monkeypatch):
    folder = Path(booted_framework.config.user_state_dir) / "knowledge_snapshots"
    for i in range(12):
        snapshot(folder, f"pre_ingest_{i}", size=1_500_000, when=1_000_000 + i)
    snapshot(folder, "pre_codebase_1", size=2_000_000, when=1_000_100)
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots")
    for name in [f"pre_ingest_{i}" for i in range(12)] + ["pre_codebase_1"]:
        assert name in out and str(folder / f"{name}.jsonl.gz") in out
    assert "1.5 MB" in out and "taken before a Deep Ingest" in out and "own documents were indexed" in out
    assert "no limit" in out and "deletes no snapshot by itself" in out and "(export)" in out
    assert "More than" not in out and "usual" not in out
    _, listed = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "list")
    assert str(folder / "pre_codebase_1.jsonl.gz") in listed
    assert len(list(folder.iterdir())) == 13


def test_the_gui_lists_every_snapshot_as_plain_text_and_deletes_nothing(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from orchestrator.gui.knowledge_snapshots_window import COLUMNS, KnowledgeSnapshotsDialog
    folder = tmp_path / "knowledge_snapshots"
    for i in range(12):
        snapshot(folder, f"pre_ingest_{i}", size=1_500_000, when=1_000_000 + i)
    snapshot(folder, "<b>pre_restore_x", size=10, when=1_000_100)  # a name is shown as written, never as markup
    dialog = KnowledgeSnapshotsDialog(tmp_path)
    try:
        rows = [[dialog.table.item(r, c).text() for c in range(len(COLUMNS))] for r in range(dialog.table.rowCount())]
        assert [r[0] for r in rows] == [f"pre_ingest_{i}" for i in range(12)] + ["<b>pre_restore_x"]
        assert rows[0][2] == "1.5 MB" and "before a Deep Ingest" in rows[0][3]
        assert rows[0][4] == str(folder / "pre_ingest_0.jsonl.gz")
        note = dialog.note.text()
        assert "no limit" in note and "deletes no snapshot by itself" in note and "More than" not in note
        assert all(label.textFormat() == Qt.PlainText for label in dialog.findChildren(QLabel))
    finally:
        dialog.close()
    assert len(list(folder.iterdir())) == 13


def test_restoring_a_snapshot_stays_with_the_person_until_the_trash_exists():
    """restore_knowledge_snapshot deletes no snapshot any more, but stays out of every delegation and standing
    approval until the trash and the deletion dialog exist (WP7)."""
    from orchestrator.tools.delegation import DELEGATION_SCOPE
    from orchestrator.tools.grants import NEVER_GRANT
    scope = DELEGATION_SCOPE.get("restore_knowledge_snapshot")
    assert scope is None or scope.who != "delegable"
    assert "restore_knowledge_snapshot" in NEVER_GRANT
