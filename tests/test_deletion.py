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
OWN_BRIDGE = ("bookkeeping: the endpoint and key file of the framework's own GUI bridge (W4): only a socket of this "
              "account in the bridge's own folders, its own temporary folder, or the key file it wrote itself")
OWN_NOTE = ("bookkeeping: the note of an open Mojo Oracle run that the framework wrote itself next to Mojo Miracle's "
            "lock, removed when a person's Miracle run ends or after an hour")
NAME_ONLY = "never deletes an image: drops the per-build name once the image carries its last-build name"
MOVED = ("a move the person chose (save dialog, Save..., llm-cli build save): the build folder's copy goes only after "
         "every file arrived and matched its original by sha256; the artifact itself is kept (eighth round, answer 3)")
SORTED = ("a move the person chose (Sort loose knowledge..., llm-cli knowledge sort): the summary names the loose file "
          "and its new folder, No is the default; the file goes only after its checked copy is installed in the pack "
          "folder, and only while both still match the checked file's sha256 (round 23, 1)")
BOARD_MOVED = ("a move the person confirmed (Q5/P6, a board's new host key): the old device ID's data goes only after "
               "its copy under the new ID was checked and the change was made; the undo takes back only the copies "
               "the move itself made")
UNUSED = "OPEN (reported): no caller; must get a plan before it is wired"
IMPORT_MARKS = ("bookkeeping: the framework's own import note of an installed pack (and its snapshot note), removed so "
                "the pack is imported again after the person's yes in Mojo Miracle (knowledge.reimport)")
RELEASED_NOTE = ("bookkeeping: the framework's own note that an uninstalled pack's excerpts left the knowledge base, "
                 "removed once the pack (restored from the trash or installed again) is imported again")
KNOWLEDGE_REPLACED = ("the knowledge base's own storage: a collection replaced by a restore, which saves the current "
                      "content as a snapshot first (round 5 b); clear_knowledge_base, its other caller, has no caller "
                      "(OPEN, reported)")

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
    # the dependency cache of module CMake options: the person's list (`build deps delete`, Build Dependencies...)
    ("orchestrator/runtime/build/deps.py", "DependencyCache.delete"): PERSON,
    ("orchestrator/runtime/bundle/generator.py", "BundleStore.delete"): PERSON,   # stored board bundles
    ("orchestrator/runtime/device/package.py", "DeviceRuntimeStore.delete_packages"): PERSON,
    # every object of the old runtime mode after a switch of Ditto's server (build folders, images, containers, its
    # records and image log), deleted for good: only the list the person confirmed (runtime_mode.remove_leftovers;
    # tests/test_runtime_mode.py covers every actor, a changed list and the new mode that must work first)
    ("orchestrator/runtime/runtime_mode.py", "_remove_item"): PERSON,
    ("orchestrator/runtime/container_server.py", "DittoDocker.remove_image"): PERSON,
    ("orchestrator/runtime/container_server.py", "DittoDocker.remove_container"): PERSON,
    ("orchestrator/utils/input_history.py", "InputHistory.clear"): PERSON_LIST,
    # the old knowledge base storage after its conversion to Qdrant Edge (DECISIONS RG5): the person's list only
    ("orchestrator/rag/migration.py", "delete_old_store"): PERSON,
    # the old Hugging Face download cache after its Take over (A6): the person's list only (tests/test_hf_takeover.py)
    ("orchestrator/Core/hf_takeover.py", "delete_old_copies"): PERSON,
    # what lay under the name an identical earlier build is copied to ("Use", round 23 item 9): only the person's
    # accepted list, built again first, audited first (tests/test_use_earlier_build.py covers no plan, another actor,
    # a changed list and the audit entry)
    ("orchestrator/Core/artifact_output.py", "_replace_with_copy"): PERSON,
    # what an interrupted build left, kept to continue it: only the person's accepted list, built again first,
    # audited first (tests/test_interrupted_build.py covers an AI, a changed list and the honest result)
    ("orchestrator/Core/interrupted_build.py", "discard"): PERSON,
    # a build that cannot be continued: its own working folder of intermediates, at once
    ("orchestrator/Core/interrupted_build.py", "discard_now"): STAGING,
    # the clean-up container the framework started for one build folder, by its id
    ("orchestrator/Core/interrupted_build.py", "run_cleanup"): CONTAINERS,
    ("orchestrator/rag/edge_store.py", "EdgeStorage.delete"): KNOWLEDGE_REPLACED,
    # (the chat: FeedStore.clear_conversation and ChatHistory.clear delete rows and messages, not files; see below)
    ("orchestrator/Core/builder.py", "BuildEngine._keep_as_last_build"): NAME_ONLY,
    ("orchestrator/Core/artifact_output.py", "_remove_moved_source"): MOVED,
    ("orchestrator/Core/community_manager.py", "CommunityManager._remove_sorted"): SORTED,
    # an installed pack's folder and import notes: only the person's accepted plan (tests/test_pack_uninstall.py
    # covers no plan, another actor and a changed list)
    ("orchestrator/Core/community_manager.py", "CommunityManager.uninstall"): PERSON,
    ("orchestrator/Core/community_manager.py", "CommunityManager.import_again"): IMPORT_MARKS,
    ("orchestrator/Core/community_manager.py", "CommunityManager.sync_knowledge_base"): RELEASED_NOTE,
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
    ("orchestrator/utils/atomic.py", "write_private"): TEMP,
    # the helper that deletes the local runtime's state and key files and the lock files (with the Windows retry);
    # its callers are listed (FileLock, LocalLLMRuntime._stop_locked and _cleanup_stale_locked, and the downloaded
    # archives of install_prebuilt)
    ("orchestrator/utils/atomic.py", "remove_file"): TEMP,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime.install_prebuilt.discard"): TEMP,  # an unpacked archive
    ("orchestrator/utils/ssh_trust.py", "save_host_keys"): TEMP,   # known_hosts' temporary file when writing fails
    ("orchestrator/Core/deployment_manager.py", "DeploymentManager.create_package"): TEMP,
    ("orchestrator/Core/knowledge_snapshots.py", "_copy_checked"): TEMP,   # an export's unfinished copy
    ("orchestrator/Core/artifact_output.py", "_copy_verified"): TEMP,      # a save's unfinished copy
    ("orchestrator/Core/hf_hub.py", "HubFetcher.fetch_file"): TEMP,
    ("orchestrator/Core/hf_takeover.py", "_copy_one"): TEMP,   # a take-over's unfinished or unchecked copy
    ("orchestrator/gui/gui_state.py", "GuiState.save"): TEMP,
    ("orchestrator/runtime/device/tested.py", "_write"): TEMP,     # the board test record's unfinished temp file
    ("orchestrator/gui/sign_in.py", "UserConfigSnapshot.restore"): TEMP,   # puts the config back as it was
    ("orchestrator/llm/auth/store.py", "CredentialStore._write"): TEMP,
    ("orchestrator/mcp_server.py", "_write_private"): TEMP,
    ("orchestrator/utils/atomic.py", "write_private_exclusive"): TEMP,
    ("orchestrator/utils/atomic.py", "write_file"): TEMP,   # its own temporary file only (chain followups-3)
    ("orchestrator/runtime/build/source.py", "export_source"): TEMP,
    ("orchestrator/runtime/build/store.py", "BuildStore.export_build"): TEMP,
    ("orchestrator/runtime/device/package.py", "package_runtime"): TEMP,
    # the board bundle: the store's own temporary build folder of a bundle that failed before its rename, and the
    # wizard's new block folder it made itself when writing its two files failed
    ("orchestrator/runtime/bundle/generator.py", "build_bundle"): STAGING,
    ("orchestrator/runtime/bundle/blocks.py", "new_block"): TEMP,
    ("orchestrator/runtime/probe_service.py", "HardwareProbes._script_probe"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_copy"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_link"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._pinned_model"): TEMP,
    ("orchestrator/runtime/bench.py", "BenchRunner._sweep"): TEMP,
    ("orchestrator/runtime/device/board_move.py", "_File.finish"): BOARD_MOVED,
    ("orchestrator/runtime/device/board_move.py", "_File.undo"): BOARD_MOVED,
    ("orchestrator/runtime/device/board_move.py", "_Folder.finish"): BOARD_MOVED,
    ("orchestrator/runtime/device/board_move.py", "_Folder.undo"): BOARD_MOVED,
    ("orchestrator/runtime/device/board_move.py", "_Folder.stage"): STAGING,   # its temporary folder (a failed copy,
                                                                               # what an interrupted move left)
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
    ("orchestrator/rag/edge_store.py", "EdgeStorage.create"): TEMP,   # the half-made shard of a create that failed
    # old records
    ("orchestrator/Core/build_records.py", "prune"): RECORDS,
    ("orchestrator/Core/builder.py", "BuildEngine.build_model"): RECORDS,
    # the export's own .partial file when docker save fails (round 36)
    ("orchestrator/Core/build_image.py", "export_image"): TEMP,
    ("orchestrator/Core/job_service.py", "JobService._prune"): RECORDS,
    ("orchestrator/Core/request_store.py", "RequestStore.prune"): RECORDS,
    ("orchestrator/Core/orchestrator.py", "LLMOrchestrator.submit_build_request"): RECORDS,
    # staging, quarantine and working folders
    ("orchestrator/Core/builder.py", "BuildEngine._create_golden_artifact"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._discard_staged"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._extract_artifacts"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._forget_end_record"): STAGING,   # a stale build.end in its work folder
    ("orchestrator/Core/builder.py", "BuildEngine._publish_artifacts"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine._save_image_for_scan"): STAGING,   # the scan's temporary archive
    ("orchestrator/Core/builder.py", "BuildEngine._stage_dataset"): STAGING,
    ("orchestrator/Core/builder.py", "BuildEngine.cleanup_build"): STAGING,
    # the runtime layer's own folders: its read-only assembled copy (made anew from the shipped copy), the end folder
    # of one stage container, the toolbox's working folder of downloaded tarballs
    ("orchestrator/Core/runtime_layer.py", "_remove_layer_folder"): STAGING,
    ("orchestrator/Core/runtime_layer.py", "prepare_end_folder"): STAGING,
    ("orchestrator/Core/runtime_layer.py", "_remove_work"): STAGING,
    # llmcf-install's working folder in the framework's cache (the build's output before it is kept)
    ("orchestrator/runtime/bundle/root_helper.py", "ensure_helper"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.discard"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.export_pack"): STAGING,
    # the export's staged copy of a target: its licence files are made anew there, the target folder is never touched
    ("orchestrator/Core/community_manager.py", "CommunityManager._share_snapshot"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.finish_install"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.prepare_install"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.prepare_install_file"): STAGING,
    ("orchestrator/Core/community_manager.py", "CommunityManager.prepare_sort"): STAGING,   # its quarantine copy
    ("orchestrator/Core/community_manager.py", "CommunityManager.verify_file"): STAGING,
    ("orchestrator/Core/community_manager.py", "_submit_with_gh"): STAGING,
    # the maintainer's review folder of the framework's own (never a folder the person named)
    ("orchestrator/Core/community_maintainer.py", "Review.discard"): STAGING,
    ("orchestrator/Core/community_pack.py", "read_pack"): STAGING,
    ("orchestrator/Core/module_generator.py", "ModuleGenerator.generate_module"): STAGING,
    ("orchestrator/tools/tools_community.py", "Quarantine._discard"): STAGING,
    ("orchestrator/runtime/build/source.py", "remove_tree"): STAGING,   # the old name, delegates to the helper below
    # the one removal of the framework's own folders (chain win-hygiene); its callers are listed
    ("orchestrator/utils/atomic.py", "remove_tree"): STAGING,
    ("orchestrator/utils/atomic.py", "_retry_writable.retry"): STAGING,
    ("orchestrator/utils/atomic.py", "_one_pass"): STAGING,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._unpack"): STAGING,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime.install_prebuilt"): STAGING,
    # the pinned source and the build store: staging folders, and a damaged copy replaced by a complete one
    ("orchestrator/runtime/build/source.py", "_install"): STAGING,
    ("orchestrator/runtime/build/source.py", "fetch_source"): STAGING,
    ("orchestrator/runtime/build/source.py", "import_source"): STAGING,
    # a dependency's fetch: its working folder, and a cache folder without a recorded pin replaced by the checked copy
    ("orchestrator/runtime/build/deps.py", "DependencyCache.fetch"): STAGING,
    ("orchestrator/runtime/build/deps.py", "_extract"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.build"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore._commit"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.import_build"): STAGING,
    ("orchestrator/runtime/build/store.py", "BuildStore.sweep_leftovers"): STAGING,
    # the conversion of the old knowledge base storage: its own working copy (qdrant_edge.converting), never the old
    # store, which it only reads
    ("orchestrator/rag/migration.py", "convert"): STAGING,
    ("orchestrator/rag/migration.py", "_discard"): STAGING,
    ("orchestrator/rag/migration.py", "_move_into_place"): STAGING,
    ("orchestrator/rag/storage_lock.py", "remove_tree"): STAGING,   # the helper; its callers are listed
    # containers the framework started
    ("orchestrator/Core/builder.py", "BuildEngine._run_container"): CONTAINERS,
    ("orchestrator/Core/builder.py", "BuildEngine._scan_image_security"): CONTAINERS,
    ("orchestrator/Core/runtime_layer.py", "engine_builder.build"): CONTAINERS,   # the toolbox builder, never started
    # llmcf-install's builder container: made only to copy the built program out, never started
    ("orchestrator/runtime/bundle/root_helper.py", "engine_builder.build"): CONTAINERS,
    ("orchestrator/runtime/build/executor.py", "DockerEngine.run"): CONTAINERS,
    ("orchestrator/runtime/container_server.py", "DittoDocker.run_check"): CONTAINERS,   # checks of Ditto's image
    # the framework's own local model server
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._open_log"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._launch"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._launch_container"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._stop_locked"): OWN_SERVER,
    ("orchestrator/runtime/llama_server.py", "LocalLLMRuntime._cleanup_stale_locked"): OWN_SERVER,
    # the CLI's note of an open Mojo Oracle run (Mojo closes the circle)
    ("orchestrator/Core/mojo/oracle_note.py", "close_oracle"): OWN_NOTE,
    # the framework's own GUI bridge for AI apps
    ("orchestrator/tools/gui_bridge.py", "_clear_socket_path"): OWN_BRIDGE,
    ("orchestrator/tools/gui_bridge.py", "_remove_stale_endpoint"): OWN_BRIDGE,
    ("orchestrator/tools/gui_bridge.py", "BridgeServer._stop_bridge"): OWN_BRIDGE,
    ("orchestrator/tools/gui_bridge.py", "BridgeServer._remove_temp_folder"): OWN_BRIDGE,
    # open, reported
    ("orchestrator/runtime/profile.py", "ProfileStore.remove_device"): UNUSED,
}

_DELETING = {"rmtree", "unlink", "rmdir", "removedirs", "prune", "prune_builds", "prune_containers", "prune_volumes",
             "prune_networks", "prune_images"}
# Helpers that delete a folder or a file, called by name (utils/atomic.remove_tree and its old names in
# runtime/build/source.py and rag/storage_lock.py; shutil.rmtree imported by name; utils/atomic.remove_file): their
# callers delete as much as the helper does.
_DELETING_HELPERS = {"remove_tree", "rmtree", "remove_file"}


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

    # The old knowledge base storage after its conversion: only the person, in the GUI or at a terminal, only the
    # unchanged list (tests/test_edge_store.py covers the conversion itself).
    from orchestrator.rag import migration
    state = tmp_path / "kb"
    (state / "qdrant").mkdir(parents=True)
    (state / "qdrant" / "meta.json").write_text("{}", encoding="utf-8")
    (state / "qdrant_edge" / "collections").mkdir(parents=True)
    (state / "qdrant_edge" / "collections" / "c.json").write_text("{}", encoding="utf-8")
    migration.write_record(state, {"state": "converted", "converted_at": "2026-10-16T10:00:00"})
    old_plan = migration.old_store_plan(state)
    with pytest.raises(PermissionError):
        migration.delete_old_store(state, old_plan, audit=Audit())
    with pytest.raises(PermissionError):
        migration.delete_old_store(state, AcceptedDeletion(old_plan, "permanent", "framework"), audit=Audit())
    accepted = accept(old_plan, "cli", "permanent")
    (state / "qdrant" / "more").write_bytes(b"x")
    with pytest.raises(PlanChanged):
        migration.delete_old_store(state, accepted, audit=Audit())
    assert (state / "qdrant" / "meta.json").exists()

    # The old Hugging Face download cache after its Take over (A6): only the person, in the GUI or at a terminal,
    # only the unchanged list (tests/test_hf_takeover.py covers the take-over, the trash and the audit entry).
    from orchestrator.Core import hf_takeover
    models = tmp_path / "models"
    snapshot = models / "models--acme--m" / "snapshots" / ("c" * 40)
    snapshot.mkdir(parents=True)
    (models / "models--acme--m" / "refs").mkdir()
    (snapshot / "config.json").write_bytes(b"{}")
    taken = models / "hf" / "acme--m" / ("c" * 12) / "config.json"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"{}")
    copies = hf_takeover.old_copies_plan(models)
    with pytest.raises(PermissionError):
        hf_takeover.delete_old_copies(models, copies, audit=Audit())
    with pytest.raises(PermissionError):
        hf_takeover.delete_old_copies(models, AcceptedDeletion(copies, "permanent", "framework"), audit=Audit())
    accepted = accept(copies, "gui", "permanent")
    (snapshot / "more.json").write_bytes(b"{}")
    with pytest.raises(PlanChanged):
        hf_takeover.delete_old_copies(models, accepted, audit=Audit())
    assert (snapshot / "config.json").exists()

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
