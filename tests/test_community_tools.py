"""The community zoo for AI callers (MCP design T9; DECISIONS round 1 item 3, round 2 item 4, round 4 items 2, 3, 5):
list_packs with verify, add_pack_from_index two-phase with quarantine (every base image of a target pack shown
plainly), standing approvals for packs, and export_pack to a local file, always asked. No pull request, no pack from a
file. Offline: the index and the packs come from the fake network of tests/test_zoo.py."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.Core.community_manager import CommunityManager, read_provenance
from orchestrator.Core.job_service import JobService
from orchestrator.tools import tools_community
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.framework_tools import register_framework_tools
from orchestrator.tools.grants import NEVER_GRANT
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, current_question
from test_zoo import World, make_target, pack_bytes, trusted

L4T = "nvcr.io/nvidia/l4t-base@sha256:" + "c" * 64   # a vendor image, pinned
OFFICIAL = "debian:bookworm-slim"


def knowledge_pack(world, name="rk-docs", text="RK3588 NPU notes", topic="rk3588"):
    """A knowledge pack on a topic (topic=None: a pack made before packs named their topic)."""
    root = world.tmp / "src" / name
    root.mkdir(parents=True)
    (root / "knowledge.json").write_text(json.dumps([{"source": "https://docs.example/rk", "content": text,
                                                      "metadata": {}}]))
    return pack_bytes(root, kind="knowledge", pack_id=name, version="1.0.0", topic=topic)


@pytest.fixture
def env(tmp_path, monkeypatch):
    # the disk check reads the file system; the tests never depend on how full this machine's disk is
    disk = SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12)
    monkeypatch.setattr(tools_community.shutil, "disk_usage", lambda p: disk)
    world = World(tmp_path)
    world.values["offline_mode"] = False
    jobs = JobService()
    fw = SimpleNamespace(config=world.config, community_manager=world.cm, job_service=jobs, build_service=None,
                         target_manager=None, resolve_path=lambda p: Path(p))
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "a.db")),
                                        fw)
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(world=world, registry=registry, jobs=jobs, cm=world.cm, tmp=tmp_path, disk=disk, fw=fw)


def call(env, name, arguments, actor="ditto", confirm=None):
    return env.registry.call(name, arguments, ToolContext(actor, confirm=confirm))


def wait_job(env, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = env.jobs.get(job_id)
        if job.finished:
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not end")


def recording(asked, answer=True):
    def confirm(spec, inp):
        asked.append(current_question())
        return answer
    return confirm


def quarantined(env, kind="target"):
    folder = env.cm._install_root(kind) / ".quarantine"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# --- list_packs ---------------------------------------------------------------------------------------------------

def test_list_packs_reads_the_cache_and_refreshes_once_an_hour(env):
    env.world.add(env.world.target_pack())
    env.world.add(knowledge_pack(env.world), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.publish()
    out = call(env, "list_packs", {})
    assert out.items == [] and env.world.session.calls == []   # no network without refresh
    out = call(env, "list_packs", {"refresh": True})
    assert {(i.kind, i.pack_id, i.trust) for i in out.items} == {("target", "demo-board", "community"),
                                                                   ("knowledge", "rk-docs", "community")}
    calls = len(env.world.session.calls)
    out = call(env, "list_packs", {"refresh": True, "kind": "knowledge"})
    assert len(env.world.session.calls) == calls and any("less than an hour" in n for n in out.notes)
    assert [i.pack_id for i in out.items] == ["rk-docs"]
    schema = type(out).model_json_schema()
    assert schema["$defs"]["PackOut"]["properties"]["title"].get("untrusted") is True


def test_list_packs_details_and_verify(env):
    env.world.add(env.world.target_pack())
    env.world.publish()
    env.world.install()
    out = call(env, "list_packs", {"pack_id": "demo-board", "verify": True})
    assert out.items[0].download.sha256 and out.items[0].installed_version == "1.2.0"
    assert out.verification[0].pack_id == "demo-board" and out.verification[0].problems == []
    (env.cm.targets_dir / "demo-board" / "modules" / "build.sh").write_text("echo changed\n")
    out = call(env, "list_packs", {"verify": True})
    assert "changed since it was installed" in out.verification[0].problems
    assert str(env.tmp) not in out.model_dump_json()   # labels and ids, never a path


# --- add_pack_from_index ------------------------------------------------------------------------------------------

def test_a_target_pack_is_asked_with_its_summary_and_every_base_image(env):
    docker = (f"FROM {L4T} AS build\nRUN make\nFROM {OFFICIAL}\nCOPY --from=build /x /x\n"
              "FROM build AS again\n")
    env.world.add(env.world.target_pack(dockerfile=docker))
    env.world.publish()
    asked = []
    out = call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording(asked))
    question = asked[0]
    assert "build scripts and a Dockerfile" in question and "network" in question
    assert L4T in question and "other registry: nvcr.io" in question and "prebuilt image" in question
    assert OFFICIAL in question and "Docker Official Image" in question and "not pinned by digest" in question
    assert "Trust" in question and "community" in question and str(env.tmp) not in question
    job = wait_job(env, out.job_id)
    assert job.state == "done" and job.result["installed_to"] == "targets/demo-board"
    assert (env.cm.targets_dir / "demo-board" / "target.yml").is_file()
    assert quarantined(env) == []


def test_dockerfile_bases():
    text = "# c\nARG V=1\nFROM --platform=linux/arm64 ubuntu:22.04 AS a\nFROM a\nFROM scratch\nFROM \\\n  nvcr.io/x/y@sha256:" \
           + "b" * 64 + "\n"
    assert tools_community.dockerfile_bases(text) == ["ubuntu:22.04", "nvcr.io/x/y@sha256:" + "b" * 64]
    assert tools_community.dockerfile_bases("ARG B=alpine:3\nFROM ${B}\nFROM $OTHER\n") == ["alpine:3", "$OTHER"]
    assert "build argument" in tools_community.base_image_line("$OTHER")


def test_a_no_removes_the_quarantined_copy(env):
    env.world.add(env.world.target_pack())
    env.world.publish()
    with pytest.raises(ToolError) as e:
        call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording([], False))
    assert e.value.code == "declined"
    assert quarantined(env) == [] and not (env.cm.targets_dir / "demo-board").exists()


def test_the_quarantined_copy_is_reused_for_the_same_pack_and_hash(env):
    url = env.world.add(env.world.target_pack())
    env.world.publish()
    spec = env.registry.get("add_pack_from_index")
    inp = spec.input_model(kind="target", pack_id="demo-board")
    first = env.registry.look_up(spec, inp, ToolContext("ditto"))
    second = env.registry.look_up(spec, inp, ToolContext("ditto"))
    assert first.fingerprint() == second.fingerprint()
    assert env.world.session.calls.count(url) == 1 and len(quarantined(env)) == 1


def test_an_unused_quarantined_copy_is_removed_after_its_time(tmp_path):
    now = [0.0]
    folder = tmp_path / "q" / "demo-board.x"
    folder.mkdir(parents=True)
    discarded = []
    store = tools_community.Quarantine(lambda: SimpleNamespace(discard=lambda p: discarded.append(p)),
                                       clock=lambda: now[0])
    held = SimpleNamespace(quarantine=folder)
    key = store.key("target", "demo-board", "f" * 64)
    store.put(key, held)
    now[0] = tools_community.QUARANTINE_TTL - 1
    assert store.get(key) is held   # a second look keeps it (and its time starts again)
    now[0] += tools_community.QUARANTINE_TTL + 1
    store.sweep()
    assert discarded == [held] and store.get(key) is None


def test_a_pending_approval_declined_in_the_framework_removes_the_quarantined_copy(env):
    from orchestrator.tools.approvals import ApprovalService, ApprovalStore
    env.registry.attach_approvals(ApprovalService(ApprovalStore(env.tmp / "approvals"), env.jobs))
    env.world.add(env.world.target_pack())
    env.world.publish()
    with pytest.raises(ToolError) as e:   # a client that cannot ask: the question waits in the framework
        env.registry.call("add_pack_from_index", {"kind": "target", "pack_id": "demo-board"},
                          ToolContext("mcp", pending_approvals=True, connection="c"))
    assert e.value.code == "waiting_for_approval" and len(quarantined(env)) == 1
    store = env.registry.approvals.store
    store.decide(store.pending()[0].id, False, "gui")
    env.registry.approvals.poll_once()
    assert quarantined(env) == [] and not (env.cm.targets_dir / "demo-board").exists()


@pytest.mark.parametrize("case", ["revoked", "unknown", "offline", "local-file"])
def test_refusals_before_the_question(env, case):
    env.world.add(env.world.target_pack())
    if case == "revoked":
        env.world.revoked.append({"kind": "target", "id": "demo-board", "reason": "broken"})
    if case == "offline":
        env.world.values["offline_mode"] = True
    env.world.publish()
    asked = []
    pack = "nope" if case == "unknown" else "demo-board"
    arguments = {"kind": "target", "pack_id": pack}
    if case == "local-file":
        arguments["file"] = "/tmp/pack.zip"
    with pytest.raises(ToolError) as e:
        call(env, "add_pack_from_index", arguments, confirm=recording(asked))
    assert e.value.code == {"revoked": "not_supported", "unknown": "unknown_id", "offline": "not_supported",
                            "local-file": "unknown_argument"}[case]
    assert asked == [] and quarantined(env) == []


def test_a_pack_the_disk_cannot_hold_is_refused_before_the_download(env):
    url = env.world.add(env.world.target_pack())
    env.world.publish()
    env.disk.free = 10 ** 9   # less than the reserve of 2 % of a 1 TB disk
    with pytest.raises(ToolError) as e:
        call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording([]))
    assert e.value.code == "insufficient_resources" and url not in env.world.session.calls


def test_a_target_pack_never_captures_another_targets_name(env):
    make_target(env.cm.targets_dir / "Demo_Board")
    env.world.add(env.world.target_pack())
    env.world.publish()
    with pytest.raises(ToolError) as e:
        call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording([]))
    assert "same name as the existing target Demo_Board" in e.value.message


def test_the_cap_is_ask_even_when_allowed(env):
    spec = env.registry.get("add_pack_from_index")
    assert spec.cap() == Decision.ASK and spec.tier.value == "external"
    env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"add_pack_from_index": Decision.ALLOW},
                                                 reviewed=[s.name for s in env.registry.list()]))
    env.world.add(env.world.target_pack())
    env.world.publish()
    with pytest.raises(ToolError):   # nobody can be asked: never installed
        call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"})
    assert not (env.cm.targets_dir / "demo-board").exists() and quarantined(env) == []


# --- standing approvals (DECISIONS round 2 item 4, round 4 item 3) --------------------------------------------------

def grant(env, kind, pack, actor="ditto"):
    spec = env.registry.get("add_pack_from_index")
    inp = spec.input_model(kind=kind, pack_id=pack)
    ctx = ToolContext(actor)
    ctx.facts = env.registry.look_up(spec, inp, ctx)
    offer = env.registry.grant_offer(spec, inp, ctx)
    if offer is None:
        return None
    return env.registry.create_grant(offer, "this", "revoke", "gui", "gui")


def test_a_knowledge_standing_approval_installs_the_next_such_pack_without_a_question(env):
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.add(knowledge_pack(env.world, "more-docs"), kind="knowledge", pack_id="more-docs", version="1.0.0")
    env.world.publish()
    made = grant(env, "knowledge", "rk-docs")
    assert made is not None
    asked = []
    out = call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "more-docs"}, confirm=recording(asked))
    assert asked == []
    job = wait_job(env, out.job_id)
    assert job.state == "done" and job.result["installed_via"] == f"grant:{made.id}"
    prov = read_provenance(env.cm.knowledge_dir / "rk3588" / "more-docs")   # in its topic's folder
    assert prov.installed_via == f"grant:{made.id}" and prov.topic == "rk3588"
    entries = [e for e in env.registry.audit.entries(200) if e.get("tool") == "add_pack_from_index"]
    assert any(e.get("confirm_channel") == f"grant:{made.id}" for e in entries)


def test_a_pack_made_before_topics_is_always_asked_and_named_after_its_id(env):
    """DECISIONS round 23 item 1: an older knowledge pack names no topic; its topic comes from its id, and only with
    the person's confirmation, so no standing approval covers it."""
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.add(knowledge_pack(env.world, "old-docs", topic=None), kind="knowledge", pack_id="old-docs",
                  version="1.0.0")
    env.world.publish()
    assert grant(env, "knowledge", "rk-docs") is not None
    asked = []
    out = call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "old-docs"}, confirm=recording(asked))
    assert len(asked) == 1 and "taken from the pack id" in asked[0]
    assert wait_job(env, out.job_id).state == "done"
    assert read_provenance(env.cm.knowledge_dir / "old-docs" / "old-docs").topic == "old-docs"


def test_it_does_not_cover_a_recipe_pack_or_a_community_target_pack(env):
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.add(env.world.target_pack())
    env.world.publish()
    assert grant(env, "knowledge", "rk-docs") is not None
    assert grant(env, "target", "demo-board") is None    # a community target pack always asks
    asked = []
    call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording(asked))
    assert len(asked) == 1


def test_a_verified_target_pack_may_get_one(env):
    env.world.add(env.world.target_pack(), trust="verified")
    env.world.publish()
    assert grant(env, "target", "demo-board") is not None


def test_a_new_index_signing_key_voids_it(env):
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.add(knowledge_pack(env.world, "more-docs"), kind="knowledge", pack_id="more-docs", version="1.0.0")
    env.world.publish()
    assert grant(env, "knowledge", "rk-docs") is not None
    other = Ed25519PrivateKey.generate()
    env.cm._keys = [trusted(env.world.maintainer), trusted(other)]
    env.world.publish(key=other, version=2)
    asked = []
    call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "more-docs"}, confirm=recording(asked))
    assert len(asked) == 1


# --- export_pack --------------------------------------------------------------------------------------------------

def own_target(env, name="MyBoard", script="echo /home/pat/secret-folder\n"):
    make_target(env.cm.targets_dir / name, script=script)


def test_export_refuses_without_a_signing_key_and_never_creates_one(env):
    own_target(env)
    with pytest.raises(ToolError) as e:
        call(env, "export_pack", {"kind": "target", "source": "MyBoard", "author": "pat"}, confirm=recording([]))
    assert e.value.code == "needs_person" and "llm-cli zoo keys" in e.value.next
    assert env.world.secrets.store == {}


def test_export_is_always_asked_names_key_and_author_and_returns_counts_only(env):
    from orchestrator.Core.community_pack import UserKeys, key_id
    own_target(env)
    expected = key_id(UserKeys(env.world.secrets).signing_key().public_key())   # the person made a key
    env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"export_pack": Decision.ALLOW},
                                                 reviewed=[s.name for s in env.registry.list()]))
    asked = []
    out = call(env, "export_pack", {"kind": "target", "source": "MyBoard", "author": "pat"}, confirm=recording(asked))
    assert len(asked) == 1 and f"signed with your key {expected} as author pat" in asked[0]
    assert out.signer_key_id == expected and out.path.startswith("community/contributions/")
    assert (env.cm.contributions_dir / out.path.split("/")[-1]).is_file()
    assert "/home/pat" not in out.model_dump_json() and "secret-folder" not in out.model_dump_json()
    assert any(s.changes >= 1 for s in out.scrub_summary)
    # the author is remembered for the next export
    asked.clear()
    call(env, "export_pack", {"kind": "target", "source": "MyBoard"}, confirm=recording(asked))
    assert "as author pat" in asked[0]


def test_export_never_offers_a_standing_approval_and_takes_no_publishing_argument(env):
    spec = env.registry.get("export_pack")
    assert spec.grant is None and "export_pack" in NEVER_GRANT and spec.cap() == Decision.ASK
    for field in ("recipients", "passphrase", "out", "pr", "repo", "out_dir"):
        assert field not in spec.input_model.model_fields


def test_a_community_target_of_another_author_is_not_exported(env):
    env.world.add(env.world.target_pack())
    env.world.publish()
    env.world.install()
    from orchestrator.Core.community_pack import UserKeys
    UserKeys(env.world.secrets).signing_key()
    with pytest.raises(ToolError) as e:
        call(env, "export_pack", {"kind": "target", "source": "demo-board", "author": "pat"}, confirm=recording([]))
    assert e.value.code == "not_supported"


def test_community_manager_is_the_same_object(env):
    assert isinstance(env.cm, CommunityManager)


# --- adversarial review (mcp-t-ingest-community-v) ----------------------------------------------------------------

def test_an_image_a_dockerfile_copies_from_is_shown_like_a_base_image():
    """COPY --from=<image> and RUN --mount=...,from=<image> pull a prebuilt image like FROM does: the person sees it."""
    text = ("FROM debian:bookworm-slim AS build\nCOPY --from=build /x /x\nCOPY --from=0 /y /y\n"
            "COPY --from=ghcr.io/someone/tools:latest /bin/t /bin/t\n"
            "RUN --mount=type=bind,from=quay.io/vendor/sdk:1,target=/sdk make\n"
            "ADD --chown=1:1 --from=build /z /z\n")
    assert tools_community.dockerfile_bases(text) == ["debian:bookworm-slim", "ghcr.io/someone/tools:latest",
                                                      "quay.io/vendor/sdk:1"]


def test_a_target_pack_question_names_an_image_its_dockerfile_copies_from(env):
    tools = "ghcr.io/someone/tools@sha256:" + "d" * 64   # pinned, so the quarantine check lets it through
    docker = f"FROM {OFFICIAL}\nCOPY --from={tools} /bin/t /bin/t\n"
    env.world.add(env.world.target_pack(dockerfile=docker))
    env.world.publish()
    asked = []
    with pytest.raises(ToolError):
        call(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"}, confirm=recording(asked, False))
    assert tools in asked[0] and "other registry: ghcr.io" in asked[0]


def test_a_standing_approval_asks_when_the_larger_reserve_would_not_stay_free(env):
    """Design 6.5: a call under a standing approval must leave 20 GiB or 10 % free, or it asks instead."""
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.add(knowledge_pack(env.world, "more-docs"), kind="knowledge", pack_id="more-docs", version="1.0.0")
    env.world.publish()
    assert grant(env, "knowledge", "rk-docs") is not None
    env.disk.free = 5 * 10 ** 10   # above the normal reserve (2 % of 1 TB), below the standing one (10 %)
    asked = []
    with pytest.raises(ToolError):
        call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "more-docs"}, confirm=recording(asked, False))
    assert len(asked) == 1 and not (env.cm.knowledge_dir / "rk3588" / "more-docs").exists()


def test_an_mcp_question_without_an_answer_removes_the_quarantined_copy(env, monkeypatch):
    """A timed-out elicitation (not a "no") is refused outside the registry's question: the copy goes at once too."""
    import asyncio
    pytest.importorskip("mcp")
    from mcp import Client
    from mcp_types import ElicitResult
    from orchestrator import mcp_server
    monkeypatch.setattr(mcp_server, "ELICITATION_TIMEOUT", 0.3)
    env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"add_pack_from_index": Decision.ASK},
                                                 reviewed=[s.name for s in env.registry.list()]))
    env.world.add(env.world.target_pack())
    env.world.publish()
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                         tool_registry=env.registry))

    async def never(ctx, params):
        await asyncio.sleep(5)
        return ElicitResult(action="accept", content={"approve": True})

    async def go():
        async with Client(server, elicitation_callback=never, mode="legacy") as client:
            return await client.call_tool("add_pack_from_index", {"kind": "target", "pack_id": "demo-board"})
    result = asyncio.run(go())
    assert result.is_error
    end = [e for e in env.registry.audit.entries(50) if e["tool"] == "add_pack_from_index"][0]
    assert end["outcome"] == "confirmation_timeout"
    assert quarantined(env) == [] and not (env.cm.targets_dir / "demo-board").exists()


# --- N13: the knowledge snapshot taken before a knowledge pack import, with its disk check (eighth round) ------------

def test_a_knowledge_pack_is_refused_when_the_snapshot_taken_first_does_not_fit(env, monkeypatch):
    from orchestrator.Core import knowledge_snapshots
    folder = Path(env.world.config.user_state_dir) / "knowledge_snapshots"
    folder.mkdir(parents=True)
    (folder / "pre_ingest_1.jsonl.gz").write_bytes(b"x" * 1000)
    url = env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.publish()
    free = {"bytes": int(2.1 * 10 ** 9)}
    monkeypatch.setattr(knowledge_snapshots, "_disk_usage",
                        lambda p: SimpleNamespace(total=100 * 10 ** 9, used=0, free=free["bytes"]))
    asked = []
    with pytest.raises(ToolError) as e:
        call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "rk-docs"}, confirm=recording(asked))
    assert e.value.code == "insufficient_resources" and e.value.blocker == "snapshot_no_room"
    assert asked == [] and url not in env.world.session.calls, "refused before the question and the download"
    free["bytes"] = 3 * 10 ** 9   # fits, but tight: a situation warning in the question
    out = call(env, "add_pack_from_index", {"kind": "knowledge", "pack_id": "rk-docs"}, confirm=recording(asked))
    assert "snapshot" in asked[0].lower()
    job = wait_job(env, out.job_id)
    assert job.state == "done" and "snapshot" in job.result


def test_a_knowledge_pack_says_when_its_import_downloads_the_search_model_first(env):
    """next-fixes 119: a knowledge pack's pages are embedded; when the knowledge base's models are not on this computer
    the question says so with the declared situation warning (they are there: a fact, no warning)."""
    env.world.add(knowledge_pack(env.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    env.world.publish()
    spec = env.registry.get("add_pack_from_index")

    def facts():
        inp = env.registry.validate_input(spec, {"kind": "knowledge", "pack_id": "rk-docs"})
        return env.registry.look_up(spec, inp, ToolContext("ditto"))

    env.fw.rag_manager = SimpleNamespace(embedding_download=lambda: (True, 0.48))
    found = facts()
    assert ("embedding_model_download", "situation", {"size_gb": 0.5}) in [
        (w.code, w.kind, dict(w.numbers)) for w in found.warnings]
    assert any(f.label == "Embedding model" and "downloaded once first" in f.value for f in found.facts)
    env.fw.rag_manager = SimpleNamespace(embedding_download=lambda: (False, 0.0))
    found = facts()
    assert "embedding_model_download" not in {w.code for w in found.warnings}
    assert any(f.label == "Embedding model" and "nothing is downloaded" in f.value for f in found.facts)
