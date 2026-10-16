"""Deletion plans the person accepts (delegation design 3.10, round 5 f; round 6 10.4, WP7).

Every place in orchestrator/ that can delete something is listed here with how it is covered. The person's objects
(built models, knowledge snapshots, trash entries, the framework's Docker images, the chat, the input history) are
deleted only with the plan the person accepted in the framework's own window; everything else is the framework's
own bookkeeping (temporary and partial files it wrote itself, its staging and quarantine folders, its old records),
which the maintainer exempted (design [Q6], round 5 r Q6). A new place that deletes fails the sweep until it is
listed here, so it cannot slip in without a decision.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import deletion
from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept, choose, same_items
from orchestrator.tools.spec import DeletionItem, DeletionPlan

REPO = Path(__file__).resolve().parent.parent

PERSON = "the person's objects: only an accepted plan, asked in the framework's own window with the full list"
PERSON_LIST = "the person's objects: the GUI's question lists path, file, size and what it is, No is the default (WP6)"
TEMP = "bookkeeping: the framework's own temporary or partial file, or a probe file it just wrote"
RECORDS = "bookkeeping: old records of the framework (build records, requests, jobs) kept to a count (Q6)"
STAGING = "bookkeeping: staging, quarantine or working folders the framework made for one build or install"
CONTAINERS = "bookkeeping: the containers the framework started for one build, removed when it ended"
OWN_SERVER = "bookkeeping: state, key and log files of the framework's own local model server"
NAME_ONLY = "never deletes an image: drops the per-build name once the image carries its last-build name"
MOVED = ("a move the person chose (save dialog, Save..., llm-cli build save): the build folder's copy goes only after "
         "every file arrived and matched its original by sha256; the artifact itself is kept (eighth round, answer 3)")
UNUSED = "OPEN (reported): no caller; must get a plan before it is wired"

DELETION_SITES = {
    # the person's objects
    ("orchestrator/Core/artifact_store.py", "ArtifactStore.delete"): PERSON,
    ("orchestrator/Core/knowledge_snapshots.py", "delete_snapshots"): PERSON,
    ("orchestrator/Core/trash.py", "Trash.empty"): PERSON,
    ("orchestrator/Core/mojo/docker_objects.py", "delete_framework_images"): PERSON,
    # old runtime builds and device packages: the person's list (`ditto runtime prune`, `device runtime prune`); a
    # build or a deployment deletes none of them any more (they were kept to a count without a list)
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime.delete_old_builds.take"): PERSON,
    ("orchestrator/runtime/build/store.py", "BuildStore.remove"): PERSON,
    ("orchestrator/runtime/device/package.py", "DeviceRuntimeStore.delete_packages"): PERSON,
    # every object of the old runtime mode after a switch of Ditto's server (build folders, images, containers, its
    # records and image log), deleted for good: only the list the person confirmed (runtime_mode.remove_leftovers;
    # tests/test_runtime_mode.py covers every actor, a changed list and the new mode that must work first)
    ("orchestrator/runtime/runtime_mode.py", "_remove_item"): PERSON,
    ("orchestrator/runtime/container_server.py", "DittoDocker.remove_image"): PERSON,
    ("orchestrator/runtime/container_server.py", "DittoDocker.remove_container"): PERSON,
    ("orchestrator/utils/input_history.py", "InputHistory.clear"): PERSON_LIST,
    # (the chat: FeedStore.clear_conversation and ChatHistory.clear delete rows and messages, not files; see below)
    ("orchestrator/Core/builder.py", "BuildEngine._keep_as_last_build"): NAME_ONLY,
    ("orchestrator/Core/artifact_output.py", "_remove_moved_source"): MOVED,
    # temporary and partial files
    ("orchestrator/Core/build_records.py", "write_record"): TEMP,
    ("orchestrator/Core/client_proposals.py", "ClientProposalStore._write"): TEMP,
    ("orchestrator/Core/confirmed_builds.py", "ConfirmedBuilds._save"): TEMP,
    ("orchestrator/Core/disk_plan.py", "ReservationLedger._save"): TEMP,
    ("orchestrator/Core/ditto_manager.py", "ChatHistory.save"): TEMP,
    ("orchestrator/Core/job_service.py", "JobService._persist"): TEMP,
    ("orchestrator/Core/request_store.py", "RequestStore.save"): TEMP,
    ("orchestrator/Core/secrets_manager.py", "SecretsManager._write_atomic"): TEMP,
    ("orchestrator/Core/trash.py", "Trash._save"): TEMP,
    ("orchestrator/Core/trash.py", "Trash.move"): TEMP,      # the empty entry folder of a move that failed
    ("orchestrator/Core/trash.py", "Trash.restore"): TEMP,   # the empty entry folder after a restore
    ("orchestrator/Core/vault_key.py", "KeyFileHolder._write"): TEMP,
    ("orchestrator/Core/vault_key.py", "write_private"): TEMP,
    ("orchestrator/Core/deployment_manager.py", "DeploymentManager.create_package"): TEMP,
    ("orchestrator/Core/knowledge_snapshots.py", "_copy_checked"): TEMP,   # an export's unfinished copy
    ("orchestrator/Core/artifact_output.py", "_copy_verified"): TEMP,      # a save's unfinished copy
    ("orchestrator/Core/hf_hub.py", "HubFetcher._download_range"): TEMP,
    ("orchestrator/Core/hf_hub.py", "HubFetcher.fetch_file"): TEMP,
    ("orchestrator/gui/gui_state.py", "GuiState.save"): TEMP,
    ("orchestrator/runtime/device/tested.py", "_write"): TEMP,     # the board test record's unfinished temp file
    ("orchestrator/gui/builder_tab.py", "LocalProbeWorker.run"): TEMP,
    ("orchestrator/gui/sign_in.py", "UserConfigSnapshot.restore"): TEMP,   # puts the config back as it was
    ("orchestrator/llm/auth/store.py", "CredentialStore._write"): TEMP,
    ("orchestrator/mcp_server.py", "_write_private"): TEMP,
    ("orchestrator/runtime/llama_server.py", "_write_private"): TEMP,
    ("orchestrator/runtime/build/source.py", "export_source"): TEMP,
    ("orchestrator/runtime/build/store.py", "BuildStore.export_build"): TEMP,
    ("orchestrator/runtime/device/package.py", "package_runtime"): TEMP,
    ("orchestrator/runtime/probe_service.py", "HardwareProbes._script_probe"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_copy"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_link"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_model"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._sweep"): TEMP,
    ("orchestrator/tools/approvals.py", "ApprovalStore._write"): TEMP,
    ("orchestrator/tools/audit.py", "AuditLog._write_anchor"): TEMP,
    ("orchestrator/tools/audit.py", "AuditLog.record"): TEMP,             # an anchor of an entry never committed
    ("orchestrator/tools/audit.py", "AuditLog._archive_in_place"): TEMP,  # undoes a failed archive
    ("orchestrator/tools/local_files.py", "_write_private_json"): TEMP,
    ("orchestrator/tools/mcp_tokens.py", "TokenStore._write"): TEMP,
    ("orchestrator/tools/policy.py", "PolicyStore._save_locked"): TEMP,
    ("orchestrator/tools/tools_community.py", "register_community_tools.run_export"): TEMP,  # its own new export
    ("orchestrator/tools/tools_deploy.py", "register_deploy_tools.create_deploy_package.work"): TEMP,  # cancelled
    ("orchestrator/utils/download.py", "FileLock.__enter__"): TEMP,
    ("orchestrator/utils/download.py", "FileLock.__exit__"): TEMP,
    ("orchestrator/utils/download.py", "PinnedDownloader._download_range"): TEMP,
    ("orchestrator/utils/download.py", "PinnedDownloader.fetch"): TEMP,
    ("orchestrator/utils/download.py", "PinnedDownloader.verify"): TEMP,
    ("orchestrator/utils/network.py", "NetworkGuard.download_file"): TEMP,
    ("orchestrator/utils/validation.py", "validate_directory_writable"): TEMP,
    # old records
    ("orchestrator/Core/build_records.py", "prune"): RECORDS,
    ("orchestrator/Core/builder.py", "BuildEngine.build_model"): RECORDS,
    ("orchestrator/Core/job_service.py", "JobService._prune"): RECORDS,
    ("orchestrator/Core/request_store.py", "RequestStore.prune"): RECORDS,
    ("orchestrator/Core/orchestrator.py", "LLMOrchestrator.submit_build_request"): RECORDS,
    # staging, quarantine and working folders
    ("orchestrator/Core/builder.py", "BuildEngine._create_golden_artifact"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._discard_staged"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._extract_artifacts"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._publish_artifacts"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._save_image_for_scan"): STAGING,   # the scan's temporary archive
    ("orchestrator/Core/builder.py", "BuildEngine._stage_dataset"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine.cleanup_build"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.discard"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.export_pack"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.finish_install"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.prepare_install"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.prepare_install_file"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.verify_file"): STAGING,
    ("orchestrator/Core/community_manager.py", "_submit_with_gh"): STAGING,
    ("orchestrator/Core/community_pack.py", "read_pack"): STAGING,
    ("orchestrator/Core/module_generator.py", "ModuleGenerator.generate_module"): STAGING,
    ("orchestrator/tools/tools_community.py", "Quarantine._discard"): STAGING,
    ("orchestrator/runtime/build/source.py", "remove_tree"): STAGING,
    ("orchestrator/runtime/build/source.py", "remove_tree.retry"): STAGING,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._unpack"): STAGING,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime.install_prebuilt"): STAGING,
    # the pinned source and the build store: staging folders, and a damaged copy replaced by a complete one
    ("orchestrator/runtime/build/source.py", "_install"): STAGING,
    ("orchestrator/runtime/build/source.py", "fetch_source"): STAGING,
    ("orchestrator/runtime/build/source.py", "import_source"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.build"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore._commit"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.import_build"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.sweep_leftovers"): STAGING,
    # containers the framework started
    ("orchestrator/Core/builder.py", "BuildEngine._run_container"): CONTAINERS,
    ("orchestrator/Core/builder.py", "BuildEngine._scan_image_security"): CONTAINERS,
    ("orchestrator/runtime/build/executor.py", "DockerEngine.run"): CONTAINERS,
    ("orchestrator/runtime/container_server.py", "DittoDocker.run_check"): CONTAINERS,   # checks of Ditto's image
    # the framework's own local model server
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._open_log"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._launch"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._launch_container"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._stop_locked"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._cleanup_stale_locked"): OWN_SERVER,
    # open, reported
    ("orchestrator/runtime/profile.py", "ProfileStore.remove_device"): UNUSED,
}

_DELETING = {"rmtree", "unlink", "rmdir", "removedirs", "prune", "prune_builds", "prune_containers", "prune_volumes",
             "prune_networks", "prune_images"}
# Helpers that delete a folder, called by name (runtime/build/source.py remove_tree; shutil.rmtree imported by name):
# their callers delete as much as the helper does.
_DELETING_HELPERS = {"remove_tree", "rmtree"}


def _deletes(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in _DELETING_HELPERS
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr in _DELETING:
        return True
    if f.attr == "remove":
        base = f.value
        if isinstance(base, ast.Name) and base.id == "os":
            return True
        if isinstance(base, ast.Attribute) and base.attr in ("images", "containers", "volumes", "networks"):
            return True
        return any(k.arg in ("force", "noprune", "v") for k in call.keywords)
    return False


def deletion_sites():
    """(file, function) of every call in orchestrator/ that can delete a file, a folder or a Docker object."""
    found = set()
    for path in sorted((REPO / "orchestrator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack = []

        class Visitor(ast.NodeVisitor):
            def generic_visit(self, node):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    stack.append(node.name)
                    super().generic_visit(node)
                    stack.pop()
                    return
                if isinstance(node, ast.Call) and _deletes(node):
                    found.add((path.relative_to(REPO).as_posix(), ".".join(stack) or "<module>"))
                super().generic_visit(node)
        Visitor().visit(tree)
    return found


ITEM = dict(kind="knowledge_snapshot", speaking_name="pre_ingest_1", full_path="/srv/lcf/knowledge_snapshots/a.gz",
            file_name="a.gz", size_bytes=10, what="backup of the knowledge base")


def plan_of(*names, modes=("trash", "permanent")):
    items = [DeletionItem(**{**ITEM, "speaking_name": n, "full_path": f"/srv/lcf/{n}.gz", "file_name": f"{n}.gz"})
             for n in names]
    return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=modes)


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def test_every_deletion_path_needs_an_accepted_deletion_plan(tmp_path):
    """Every deleting call is listed with how it is covered (new ones fail until listed), and every path that deletes
    the person's objects refuses without the person's accepted plan, for every AI actor and every changed list."""
    found = deletion_sites()
    assert found - set(DELETION_SITES) == set(), "a new place deletes: give it a plan or list it as bookkeeping"
    assert set(DELETION_SITES) - found == set(), "a listed place no longer deletes: remove it from the list"

    # No accepted plan can be made for anyone but the person.
    plan = plan_of("a", "b")
    for actor in ("mcp", "mcp:laptop", "ditto", "grant:g_0123456789abcdef", "delegation:d_0123456789abcdef", ""):
        with pytest.raises(PermissionError):
            accept(plan, actor)
    with pytest.raises(PermissionError):
        AcceptedDeletion(plan, "trash", "framework", "elicitation")   # a client's form is no person's answer
    with pytest.raises(PermissionError):
        deletion.require_person(plan)                                 # a plan nobody accepted

    # Artifacts: no plan, no deletion.
    from orchestrator.Core.artifact_store import ArtifactStore
    out = tmp_path / "out"
    (out / "m").mkdir(parents=True)
    (out / "m" / "build_info.json").write_text('{"primary": null}')
    store = ArtifactStore(out, lambda: [("a_" + "1" * 16, "output:m", {"path": str(out / "m")})], lambda: [])
    target = store.resolve("a_" + "1" * 16)
    with pytest.raises(PermissionError):
        store.delete("a_" + "1" * 16, None, list(target.identity), None)
    with pytest.raises(PlanChanged):   # an accepted plan of something else
        store.delete("a_" + "1" * 16, None, list(target.identity), accept(plan_of("x"), "gui", "permanent"))
    assert (out / "m").is_dir()

    # Knowledge snapshots: only the person, only an unchanged list.
    from orchestrator.Core.knowledge_snapshots import delete_snapshots, snapshot_plan
    folder = tmp_path / "state" / "knowledge_snapshots"
    folder.mkdir(parents=True)
    (folder / "s1.jsonl.gz").write_bytes(b"1")
    accepted = AcceptedDeletion(snapshot_plan(folder), "permanent", "framework")
    with pytest.raises(PermissionError):   # never through a tool, only the person's own window or terminal
        delete_snapshots(folder, accepted, audit=Audit())
    accepted = accept(snapshot_plan(folder), "gui", "permanent")
    (folder / "s1.jsonl.gz").write_bytes(b"12")
    with pytest.raises(PlanChanged):
        delete_snapshots(folder, accepted, audit=Audit())
    with pytest.raises(RuntimeError):      # nothing without the audit log
        delete_snapshots(folder, accept(snapshot_plan(folder), "gui", "permanent"), audit=None)
    assert (folder / "s1.jsonl.gz").exists()

    # The trash: emptying is the person's, in the trash window or at a terminal.
    from orchestrator.Core.trash import Trash
    trash = Trash(tmp_path / "state", audit=Audit())
    snap = accept(snapshot_plan(folder), "gui", "trash")
    delete_snapshots(folder, snap, trash=trash, audit=Audit())
    empty = trash.plan()
    with pytest.raises(PermissionError):
        trash.empty(AcceptedDeletion(empty, "permanent", "framework"))
    assert len(trash.entries()) == 1

    # Docker images: only the person (tests/test_doctor_fixes.py covers every AI actor and every changed list).
    from orchestrator.Core.doctor import delete_framework_images
    with pytest.raises(PermissionError):
        delete_framework_images(SimpleNamespace(), plan, actor="framework")

    # The old runtime mode's leftovers: only the person (tests/test_runtime_mode.py covers every actor).
    from orchestrator.runtime.runtime_mode import remove_leftovers
    with pytest.raises(PermissionError):
        remove_leftovers(SimpleNamespace(), plan, actor="mcp", audit=Audit())

    # The chat: rows and Ditto's conversation (WP6), only the person, only the unchanged list.
    from orchestrator.Core.chat_feed import FeedStore
    feed = FeedStore(tmp_path / "feed", key=b"k" * 32)
    with pytest.raises(PermissionError):
        feed.clear_conversation(feed.clear_plan(), "mcp", Audit())


def test_multiple_choice_rebuilds_the_plan_from_exactly_the_ticked_items():
    plan = plan_of("a", "b", "c")
    assert choose(plan, []) is None, "nothing is ticked by default: nothing to delete"
    chosen = choose(plan, [2, 0])
    assert [i.speaking_name for i in chosen.items] == ["a", "c"] and chosen.total_bytes == 20
    assert chosen.mode_offered == plan.mode_offered and chosen.sha256() != plan.sha256()
    assert chosen.sha256() == plan_of("a", "c").sha256(), "the hash covers exactly the ticked items"
    with pytest.raises(ValueError):
        choose(plan, [3])
    # A look-up again: unchanged items give the same plan; a changed or vanished one never does.
    assert same_items(plan, chosen).sha256() == chosen.sha256()
    changed = DeletionPlan(items=[plan.items[0], plan.items[1], plan.items[2].model_copy(update={"size_bytes": 11})],
                           total_bytes=31, mode_offered=plan.mode_offered)
    assert same_items(changed, chosen).sha256() != chosen.sha256()
    assert same_items(plan_of("a", "b"), chosen) is None
    accepted = accept(chosen, "cli", "permanent")
    assert accepted.audit_extra() == {"deletion_sha256": chosen.sha256(), "answered_by": "person",
                                      "confirm_channel": "framework-cli"}
    with pytest.raises(ValueError):
        accept(plan_of("a", modes=("permanent",)), "gui", "trash"), "a mode the plan does not offer"


def test_a_registry_confirmation_counts_only_from_the_framework_window():
    plan = plan_of("a")
    for channel, actor in (("framework", "framework"), ("framework-gui", "gui"), ("framework-cli", "cli"),
                           ("approval:ap_0123456789abcdef", "framework")):
        accepted = deletion.accepted_through_registry(plan, SimpleNamespace(confirm_channel=channel))
        assert accepted.actor == actor and accepted.channel == channel and accepted.mode == "trash"
    for channel in ("", "elicitation", "grant:g_0123456789abcdef", "delegation:d_0123456789abcdef", "none"):
        with pytest.raises(PermissionError):
            deletion.accepted_through_registry(plan, SimpleNamespace(confirm_channel=channel))
