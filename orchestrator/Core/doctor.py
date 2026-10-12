#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Doctor

Deterministic health checks, like `flutter doctor` (the idea comes from "Mojo" in the rag4ll project):
every check returns findings with a stable code and a fix hint; nothing asks a model what is wrong.
Fixes are tiered and run only when the user asks for that tier. Tier 1 is harmless (make the personal folder
private) and runs by itself; after fixing, everything is checked again, and a finding that is still there is
reported as not fixable automatically. Tier 2 deletes nothing by itself: it lists the framework's dangling build
images (name or id, size, what each is), and the person's command line deletes exactly that list after the person
confirmed it (round 5 f), never over MCP and never under a delegation. Docker's build cache is never cleared: it
cannot be limited to the framework, so clearing it would delete other projects' cache too.

What an AI reads of the findings (`ai_findings`, used by the run_doctor tool) names the framework's folders by
label and any other path by its file name only; the person's own output keeps the full paths.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Finding:
    subsystem: str
    code: str
    severity: Severity
    message: str
    fix_hint: str = ""
    fix_tier: int = 0  # 0: no automatic fix
    fix: Optional[Callable[[], None]] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict:
        data = asdict(self)
        data.pop("fix")
        data["severity"] = self.severity.value
        data["fixable"] = self.fix is not None
        return data


Check = Callable[[object], List[Finding]]

GB = 1024 ** 3


def _ok(subsystem: str, code: str, message: str) -> Finding:
    return Finding(subsystem, code, Severity.OK, message)


def check_python(_framework) -> List[Finding]:
    version = sys.version_info[:2]
    if (3, 11) <= version < (3, 15):
        return [_ok("python", "python.version", f"Python {version[0]}.{version[1]}")]
    return [Finding("python", "python.version", Severity.ERROR, f"Python {version[0]}.{version[1]} is not supported.",
                    "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13').")]


def check_docker(framework) -> List[Finding]:
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    findings = []
    try:
        if client is None:
            raise RuntimeError("no client")
        client.ping()
        findings.append(_ok("docker", "docker.daemon", "Docker daemon reachable"))
    except Exception:
        return [Finding("docker", "docker.daemon", Severity.ERROR, "The Docker daemon is not reachable; builds cannot run.",
                        "Start Docker (Docker Desktop on Windows/macOS, 'sudo systemctl start docker' on Linux) and make "
                        "sure your user may use it (docker group or rootless Docker).")]
    if shutil.which("docker"):
        try:
            buildx = subprocess.run(["docker", "buildx", "version"], capture_output=True, text=True, timeout=15)
            if buildx.returncode == 0:
                findings.append(_ok("docker", "docker.buildx", "BuildKit (buildx) available"))
            else:
                findings.append(Finding("docker", "docker.buildx", Severity.WARN,
                                        "docker buildx is missing; image builds fall back to the slower legacy builder.",
                                        "Install the Docker buildx plugin."))
        except (OSError, subprocess.SubprocessError):
            pass
    return findings


def check_disk(framework) -> List[Finding]:
    findings = []
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    seen = set()
    for label, path in (("cache", getattr(engine, "cache_dir", None)), ("output", getattr(engine, "output_dir", None))):
        if path is None:
            continue
        path = Path(path)
        # The folders are created by the first build, so the nearest existing parent holds their disk.
        probe = next((p for p in (path, *path.parents) if p.exists()), path)
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            continue
        key = (os.stat(probe).st_dev if probe.exists() else str(probe))
        if key in seen:
            continue
        seen.add(key)
        free = usage.free / GB
        # Tier 2 is a listing, not a fix (fix=None): nothing is deleted before the person confirmed the list.
        if free < 5:
            findings.append(Finding("disk", f"disk.{label}", Severity.ERROR,
                                    f"Only {free:.1f} GB free for the {label} folder ({path}).",
                                    "Free disk space; large models need 2-3x their size during conversion. "
                                    + FREE_SPACE_HINT, fix_tier=2))
        elif free < 20:
            findings.append(Finding("disk", f"disk.{label}", Severity.WARN,
                                    f"{free:.1f} GB free for the {label} folder; large models need more.",
                                    "Free disk space before converting models larger than a few GB. "
                                    + FREE_SPACE_HINT, fix_tier=2))
        else:
            findings.append(_ok("disk", f"disk.{label}", f"{free:.0f} GB free for {label}"))
    return findings


# What `doctor --fix --tier 2` does, as every disk finding says it.
FREE_SPACE_HINT = ("'llm-cli doctor --fix --tier 2' lists the framework's dangling build images (name or id, size "
                   "and what each is) and deletes exactly those you then confirm in a terminal. Docker's build cache "
                   "is not touched: it cannot be limited to the framework.")


# --- tier 2: the framework's dangling build images, deleted only as the person confirmed them ----------------------

class DockerUnavailable(RuntimeError):
    """No Docker client, or the daemon did not answer: nothing can be listed or deleted."""


class ImagePlanChanged(RuntimeError):
    """The framework's dangling images changed since the person saw the list: nothing was deleted."""

    def __init__(self):
        super().__init__("The framework's dangling images changed since the list was shown; nothing was deleted. "
                         "Look at the new list and confirm it.")


@dataclass
class ImageDeletion:
    """What happened to a confirmed list: the images deleted (by speaking name) with their listed size, and the ones
    Docker refused (speaking name and Docker's reason, e.g. a container still uses it)."""
    deleted: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    failed: List[tuple] = field(default_factory=list)


_TARGET_WORD = re.compile(r"^[\w.+-]{1,64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _docker_client(framework):
    return framework.get_component("docker_client") if hasattr(framework, "get_component") else None


def framework_image_plan(framework):
    """The dry run of tier 2: the framework's dangling build images - labelled by the framework's builder
    (builder.IMAGE_LABEL) and without a name - as a deletion plan for the person, each with its name (the short id),
    the full image id, its size and what it is. Docker's build cache is never part of it. Returns None when there is
    nothing to delete; DockerUnavailable without a reachable Docker daemon. Reads only."""
    from orchestrator.Core.builder import IMAGE_LABEL
    from orchestrator.tools.spec import DeletionItem, DeletionPlan
    client = _docker_client(framework)
    if client is None:
        raise DockerUnavailable("Docker is not available, so no framework images can be listed.")
    try:
        images = list(client.images.list(filters={"dangling": True, "label": IMAGE_LABEL}))
    except Exception:  # noqa: BLE001 - the daemon's own words stay out (they can name a socket path)
        raise DockerUnavailable("The Docker daemon did not answer, so no framework images can be listed.") from None
    items = []
    for image in images:
        labels = getattr(image, "labels", None) or {}
        image_id = str(getattr(image, "id", "") or "")
        if IMAGE_LABEL not in labels or getattr(image, "tags", None) or not _IMAGE_ID.match(image_id):
            continue  # only what the framework built and nobody named: never another project's image
        attrs = getattr(image, "attrs", None) or {}
        target = str(labels.get(IMAGE_LABEL) or "")
        target = target if _TARGET_WORD.match(target) else "an unknown target"
        created = str(attrs.get("Created") or "")[:10]
        try:
            size = max(0, int(attrs.get("Size") or 0))
        except (TypeError, ValueError):
            size = 0
        items.append(DeletionItem(
            kind="docker_image", speaking_name=f"Framework build image {image_id[7:19]} ({target})",
            file_name=image_id, size_bytes=size,
            what=f"unused build image of the framework for {target}, without a name"
                 + (f" (built {created})" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created) else "")
                 + "; left over from an earlier build"))
    if not items:
        return None
    items.sort(key=lambda i: i.file_name)
    return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=("permanent",))


def delete_framework_images(framework, plan, actor: str) -> ImageDeletion:
    """Deletes exactly the images of `plan`, which the person accepted in the framework's own window (actor "cli" or
    "gui"; never an AI, never under a delegation). The list is looked up again first: if it changed, nothing is
    deleted (ImagePlanChanged). The deletion is audited before anything is deleted (nothing is deleted when the
    audit cannot be written); each image is removed by its id, without force and without its untagged parents, and
    never by a prune, which could catch images that became dangling after the list was shown."""
    if actor not in ("cli", "gui"):
        raise PermissionError("Only the person deletes the framework's images, in the GUI or at a terminal.")
    current = framework_image_plan(framework)
    if current is None or current.sha256() != plan.sha256():
        raise ImagePlanChanged()
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so no image was deleted.")
    extra = {"answered_by": "person", "confirm_channel": f"framework-{actor}", "deletion_sha256": plan.sha256()}
    try:
        audit.record(actor, "doctor", "framework", "person", "started", approved=True,
                     arguments={"images": len(plan.items), "bytes": plan.total_bytes},
                     detail=f"deleting {len(plan.items)} dangling framework images (doctor --fix --tier 2)",
                     extra=extra)
    except Exception:  # noqa: BLE001
        raise RuntimeError("The audit log cannot record the deletion; no image was deleted.") from None
    from orchestrator.utils.helpers import redact_secrets
    client = _docker_client(framework)
    result = ImageDeletion()
    for item in plan.items:
        try:
            client.images.remove(image=item.file_name, noprune=True)
        except Exception as e:  # noqa: BLE001 - e.g. a container still uses it: kept and reported
            reason = str(getattr(e, "explanation", "") or e)
            result.failed.append((item.speaking_name, redact_secrets(reason)[:300]))
            continue
        result.deleted.append(item.speaking_name)
        result.freed_bytes += item.size_bytes
    try:
        audit.record(actor, "doctor", "framework", "person", "error" if result.failed else "ok", approved=True,
                     arguments={"deleted": len(result.deleted), "kept": len(result.failed)},
                     detail=f"{len(result.deleted)} framework images deleted, {len(result.failed)} kept", extra=extra)
    except Exception:  # noqa: BLE001 - the deletion happened and is shown to the person; its start is recorded
        pass
    return result


def check_targets(framework) -> List[Finding]:
    tm = getattr(framework, "target_manager", None)
    if tm is None:
        return [Finding("targets", "targets.registry", Severity.ERROR, "Target registry not loaded.")]
    findings = []
    targets = tm.list_targets()
    if not targets:
        return [Finding("targets", "targets.none", Severity.ERROR, f"No targets found in {tm.targets_dir}.",
                        "Check the setting targets_dir.")]
    for meta in targets:
        tid = meta.get("id")
        if tm.get_capabilities(tid) is None:
            findings.append(Finding("targets", f"targets.{tid}.capabilities", Severity.WARN,
                                    f"Target {tid} declares no valid capabilities; GUI, CLI and AI clients cannot "
                                    "check its build settings.", "Add a 'capabilities:' block to its target.yml "
                                    "(see targets/Rockchip/target.yml)."))
        else:
            findings.append(_ok("targets", f"targets.{tid}", f"Target {tid}"))
    return findings


def check_user_state(framework) -> List[Finding]:
    config = getattr(framework, "config", None)
    state_dir = Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework"))
    if os.name != "posix":
        return [_ok("security", "state.dir", f"User state in {state_dir}")]
    mode = state_dir.stat().st_mode & 0o777
    if mode & 0o077:
        def tighten():
            state_dir.chmod(0o700)
        return [Finding("security", "state.permissions", Severity.WARN,
                        f"{state_dir} is readable by other users (mode {oct(mode)}); it holds tool permissions, "
                        "the audit log and chat history.", "Restrict it to your user.", fix_tier=1, fix=tighten)]
    return [_ok("security", "state.permissions", f"User state private ({state_dir})")]


def check_secrets(framework) -> List[Finding]:
    sm = getattr(framework, "secrets_manager", None)
    if sm is None:
        return [Finding("security", "secrets.store", Severity.ERROR, "The secrets store is not available.")]
    refresh = getattr(sm, "refresh", None)
    if callable(refresh):
        refresh()  # a long-running GUI or MCP server: another process may have changed the store
    if getattr(sm, "_load_failed", False):
        failed = getattr(sm, "failed_file", None) or sm.secrets_file
        reason = getattr(sm, "load_error", None)
        reason = f" ({reason})" if isinstance(reason, str) and reason else ""
        if failed != sm.secrets_file:
            return [Finding("security", "secrets.store", Severity.ERROR,
                            f"The old secrets store {failed} cannot be read, so its secrets were not taken over "
                            f"into {sm.secrets_file}; secrets cannot be changed.{reason}",
                            "Repair the file (its secrets are then taken over) or move it aside.")]
        return [Finding("security", "secrets.store", Severity.ERROR,
                        f"The secrets store {sm.secrets_file} cannot be read; secrets cannot be changed.{reason}",
                        "Repair the file or move it aside and sign in again (llm-cli ditto login <provider>; "
                        "other tokens: llm-cli secrets set).")]
    findings = [_ok("security", "secrets.store", "Secrets store ready")]
    mode = getattr(sm, "storage_mode", None)
    if mode == "insecure":
        findings.append(Finding("security", "secrets.storage", Severity.WARN,
                                "The vault key is stored in a file next to the vault (secret_storage: insecure): "
                                "credentials are not encrypted at rest.",
                                "Use the OS keyring, a credential helper or a passphrase "
                                "(llm-cli ditto auth storage --use keyring|helper|passphrase)."))
    elif mode == "ephemeral":
        findings.append(Finding("security", "secrets.storage", Severity.INFO,
                                "Credentials are kept in memory only (secret_storage: ephemeral); nothing is stored."))
    if mode not in (None, "keyring"):
        return findings
    try:
        import keyring
        backend = type(keyring.get_keyring()).__module__ + "." + type(keyring.get_keyring()).__name__
        if any(name in backend.lower() for name in ("null", "fail", "plaintext")):
            findings.append(Finding("security", "secrets.keyring", Severity.WARN,
                                    f"The keyring backend ({backend}) does not protect secrets.",
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), or choose a "
                                    "credential helper or a passphrase (llm-cli ditto auth storage --use ...)."))
    except Exception:
        pass
    return findings


def check_tool_policy(framework) -> List[Finding]:
    registry = getattr(framework, "tool_registry", None)
    if registry is None:
        return [Finding("ai", "tools.registry", Severity.WARN, "Tool registry not loaded.")]
    findings = []
    policy = registry.policy()
    problem = getattr(registry.policy_store, "problem", None)
    if problem:
        findings.append(Finding("ai", "tools.permissions", Severity.ERROR, problem,
                                "Run 'llm-cli tools permissions' (or Tools > Tool Permissions in the GUI) to set "
                                "them up again."))
    elif not policy.configured:
        findings.append(Finding("ai", "tools.permissions", Severity.INFO,
                                "Tool permissions are not set up yet (safe defaults apply: read allowed, everything "
                                "else asks).", "Run 'llm-cli ditto setup' or use Tools > Tool Permissions in the GUI."))
    else:
        findings.append(_ok("ai", "tools.permissions", "Tool permissions set up"))
    store = registry.policy_store
    if not problem and getattr(store, "key", b"") is None and store.path.exists():
        findings.append(Finding("ai", "tools.permissions.signature", Severity.WARN,
                                "The tool permissions are signed without a key (no OS keyring), so a hand edit of "
                                f"{store.path.name} by anyone who can write the per-user folder is not detected.",
                                "Make an OS keyring available to the framework; the next save signs with a key."))
    tokens = getattr(registry, "tokens", None)
    if tokens is not None:
        try:
            tokens.list()
        except Exception as e:  # TokenStoreError: damaged, unsigned, open to others
            findings.append(Finding("ai", "tools.tokens", Severity.ERROR, str(e),
                                    "Remove the file and create the tokens again ('llm-cli mcp token add NAME')."))
    if registry.audit is not None:
        archive = ("'llm-cli tools audit --archive' moves it and its anchor aside (kept with a timestamp) and "
                   "starts a new log.")
        try:
            result = registry.audit.check()
        except Exception as e:
            return findings + [Finding("ai", "tools.audit", Severity.ERROR,
                                       f"The tool audit log {registry.audit.path} cannot be opened: {e}",
                                       f"Keep the damaged file for inspection: {archive}")]
        # A log this key cannot verify may just as well have been rewritten, so it is no lesser finding.
        if result.problem in ("modified", "removed", "key_mismatch"):
            findings.append(Finding("ai", "tools.audit", Severity.ERROR, result.message,
                                    f"Inspect {registry.audit.path}; {archive}"))
        elif result.problem:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    f"Inspect {registry.audit.path}; {archive}"))
        elif not result.keyed:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    "The next tool call adds a keyed entry, which protects the whole chain."
                                    if result.key_available else
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), so the audit chain "
                                    "is protected by a key."))
        else:
            findings.append(_ok("ai", "tools.audit", result.message))
    return findings


def check_chat_feed(framework) -> List[Finding]:
    """The chat feed (delegation design 5.3): it can be written - an AI's decision is recorded there before it takes
    effect, or nothing runs - and its newest rows verify. Reads only; a probe write is rolled back."""
    feed = getattr(framework, "chat_feed", None)
    if feed is None:
        return [Finding("ai", "chat_feed.store", Severity.WARN,
                        "The chat feed is not available, so the chat cannot keep messages and AI decisions.",
                        "Check that the per-user folder exists and is writable ('llm-cli doctor' lists it).")]
    result = feed.check()
    # The file by name only: findings also reach AI clients (run_doctor), which never get host paths.
    aside = (f"Move {feed.path.name} (and its -wal and -shm files, if any) out of the per-user folder "
             "(~/.llm-framework or LLM_FRAMEWORK_HOME) and keep it for inspection; the framework starts a new chat "
             "feed with the next message.")
    if result.problem:
        return [Finding("ai", "chat_feed.store", Severity.ERROR, result.problem,
                        f"Free disk space or fix the folder's permissions. If the file is damaged: {aside}")]
    if not result.exists:
        return [_ok("ai", "chat_feed.store", "Chat feed not started yet (its folder is writable)")]
    findings = [_ok("ai", "chat_feed.store", f"Chat feed writable; {result.checked - result.unverified - result.unknown}"
                                             f" of the newest {result.checked} entries verify")]
    if result.unverified:
        findings.append(Finding("ai", "chat_feed.rows", Severity.WARN,
                                f"{result.unverified} chat entries do not verify (changed outside the framework, or "
                                "written with another key or without the keyring); the chat does not show them.",
                                f"If the keyring was reset, they stay hidden. To start a clean feed: {aside}"))
    if result.unknown:
        findings.append(Finding("ai", "chat_feed.version", Severity.INFO,
                                f"{result.unknown} chat entries were written by a newer version of the framework; "
                                "this version does not show them.", "Update the framework."))
    if not result.keyed and result.exists:
        findings.append(Finding("ai", "chat_feed.signature", Severity.WARN,
                                "The chat feed is signed without a key (no OS keyring), so an entry changed by anyone "
                                f"who can write {feed.path.name} is not detected.",
                                "Make an OS keyring available to the framework; new entries are then signed with a "
                                "key."))
    return findings


def check_calibration(_framework) -> List[Finding]:
    from orchestrator.utils.calibration import CALIBRATION_SETS, CalibrationDataError, load_set
    try:
        for name in CALIBRATION_SETS:
            load_set(name)
    except CalibrationDataError as e:
        return [Finding("data", "data.calibration", Severity.WARN, str(e),
                        "Restore data/calibration from the repository (git checkout -- data/calibration).")]
    return [_ok("data", "data.calibration", "Calibration data intact")]


def check_community(framework) -> List[Finding]:
    """Installed community packs against the cached signed index (no network): revoked or changed packs."""
    cm = getattr(framework, "community_manager", None)
    if cm is None:
        return []
    findings = []
    for folder, prov, trust, problems in cm.verify_installed():
        name = f"{prov.kind} {prov.id} {prov.version}"
        if trust is not None and trust.value == "revoked":
            findings.append(Finding("community", f"community.{prov.id}.revoked", Severity.ERROR,
                                    f"Community {name} was revoked: {'; '.join(problems)}",
                                    f"Delete the folder {folder}; it is not built any more."))
        elif problems:
            findings.append(Finding("community", f"community.{prov.id}.changed", Severity.WARN,
                                    f"Community {name}: {'; '.join(problems)}",
                                    f"Check the changes, or delete {folder} and install it again (llm-cli zoo "
                                    "install)."))
        elif trust is not None and trust.value == "local":
            findings.append(Finding("community", f"community.{prov.id}.local", Severity.INFO,
                                    f"Community {name} was imported from a file without a signed index entry."))
        else:
            findings.append(_ok("community", f"community.{prov.id}", f"Community {name} ({trust.value if trust else '?'})"))
    return findings


def check_knowledge(framework) -> List[Finding]:
    """Knowledge-base snapshots: more than the usual number is a reminder for the person, never a deletion (the
    framework deletes no snapshot by itself; round 5 f). Reads the folder only, never opens the knowledge base."""
    from orchestrator.Core.knowledge_snapshots import MAX_SNAPSHOTS, list_snapshots, size_text, snapshot_folder
    config = getattr(framework, "config", None)
    state_dir = getattr(config, "user_state_dir", None)
    if state_dir is None:
        return []
    folder = snapshot_folder(state_dir)
    snapshots = list_snapshots(folder)
    if not snapshots:
        return []
    total = size_text(sum(s.size_bytes for s in snapshots))
    if len(snapshots) > MAX_SNAPSHOTS:
        return [Finding("knowledge", "knowledge.snapshots", Severity.INFO,
                        f"The knowledge base keeps {len(snapshots)} snapshots ({total}) in {folder}, more than the "
                        f"usual {MAX_SNAPSHOTS}. None is deleted by itself.",
                        "'llm-cli knowledge snapshots' (in the GUI: Tools > Knowledge Snapshots...) lists each one with "
                        "its full path, name, size and what it is; delete the ones you no longer need yourself (a "
                        "trash and a deletion dialog in the framework follow).")]
    return [_ok("knowledge", "knowledge.snapshots", f"{len(snapshots)} knowledge snapshots ({total})")]


def check_runtime(framework) -> List[Finding]:
    """The local llama.cpp runtime: the self-built build in use (intact, of the pinned commit), a prebuilt download
    in use (not self-built), and whether this machine has the toolchain for a native build. Reads files and asks the
    tools for their versions; compiles nothing."""
    from orchestrator.runtime.build.store import BuildStore, describe
    from orchestrator.runtime.build.toolchain import ToolchainError, detect_toolchain
    from orchestrator.runtime.catalog import load_catalog

    config = getattr(framework, "config", None)
    state_dir = Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework"))
    pin = load_catalog().llama_cpp.source
    store = BuildStore(state_dir)
    findings: List[Finding] = []
    refs = store.references()
    active = store.get(refs["active"]) if "active" in refs else None
    if active is not None:
        problems = store.check_files(active)
        if problems:
            findings.append(Finding("runtime", "runtime.build.damaged", Severity.ERROR,
                                    f"The self-built llama.cpp build {active.build_id[:16]} does not match its "
                                    f"manifest: {'; '.join(problems[:3])}.",
                                    "Build the runtime again; a changed build is never started."))
        elif pin is not None and active.commit != pin.commit:
            findings.append(Finding("runtime", "runtime.build.outdated", Severity.WARN,
                                    f"The self-built llama.cpp build {active.build_id[:16]} is of commit "
                                    f"{active.commit[:8]}, older than the pin {pin.tag} ({pin.commit[:8]}).",
                                    "Build the runtime again from the pinned commit."))
        else:
            findings.append(_ok("runtime", "runtime.build", describe(active)))
    elif "active" in refs:
        findings.append(Finding("runtime", "runtime.build.missing", Severity.WARN,
                                f"The active self-built llama.cpp build {refs['active'][:16]} is missing or "
                                "incomplete.", "Build the runtime again."))
    else:
        stored = store.list_builds()
        findings.append(Finding("runtime", "runtime.build.none", Severity.INFO,
                                "No self-built llama.cpp runtime is in use" +
                                (f" ({len(stored)} stored, none active)." if stored else ".")))
    prebuilt = sorted(p.parent.name for p in store.root.glob("b*-*/runtime.json"))
    if prebuilt:
        try:
            use = json.loads(store.active_file.read_text(encoding="utf-8")).get("use")
        except (OSError, ValueError, AttributeError):
            use = None
        if use == "prebuilt":
            findings.append(Finding("runtime", "runtime.prebuilt", Severity.INFO,
                                    f"Ditto's local model server is the official prebuilt llama.cpp archive "
                                    f"({', '.join(prebuilt)}): not self-built.",
                                    "llm-cli ditto runtime install builds the server from the pinned source."))
        elif "active" not in refs:
            # Downloaded by an earlier version without --prebuilt: used only after the opt-in (decision D1).
            findings.append(Finding("runtime", "runtime.prebuilt", Severity.INFO,
                                    f"An official prebuilt llama.cpp archive ({', '.join(prebuilt)}, not self-built) "
                                    "from an earlier version is installed but not in use: it is used only on "
                                    "explicit request.",
                                    "llm-cli ditto runtime install builds the server from the pinned source; "
                                    "llm-cli ditto runtime install --prebuilt uses the archive instead."))
        else:
            findings.append(Finding("runtime", "runtime.prebuilt", Severity.INFO,
                                    f"An official prebuilt llama.cpp archive ({', '.join(prebuilt)}, not self-built) "
                                    "is installed but not in use: Ditto uses the self-built server."))
    try:
        detected = detect_toolchain(full=False)
    except ToolchainError as e:
        return findings + [Finding("runtime", "runtime.toolchain", Severity.INFO, str(e))]
    if detected.native_ready:
        findings.append(_ok("runtime", "runtime.toolchain", f"Toolchain for a native llama.cpp build: "
                                                           f"{detected.summary()}"))
    else:
        docker = " or build in the framework's builder container (Docker)" if "docker" in detected.tools else ""
        findings.append(Finding("runtime", "runtime.toolchain", Severity.INFO,
                                "No native llama.cpp build on this machine: " + "; ".join(detected.problems) + ".",
                                f"Install the missing tools with your package manager{docker}. The framework never "
                                "installs a toolchain or accepts a licence for you."))
    return findings


DEFAULT_CHECKS: List[Check] = [check_python, check_docker, check_disk, check_targets, check_user_state,
                               check_secrets, check_tool_policy, check_chat_feed, check_calibration, check_community,
                               check_knowledge, check_runtime]


@dataclass
class DoctorReport:
    findings: List[Finding]
    fixed: List[str] = field(default_factory=list)
    still_present: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        severities = {f.severity for f in self.findings}
        return 2 if Severity.ERROR in severities else 1 if Severity.WARN in severities else 0

    def to_dict(self) -> Dict:
        return {"exit_code": self.exit_code, "findings": [f.to_dict() for f in self.findings],
                "fixed": self.fixed, "still_present": self.still_present}


class Doctor:
    def __init__(self, framework, checks: Optional[List[Check]] = None):
        self.framework = framework
        self.checks = checks if checks is not None else list(DEFAULT_CHECKS)

    def run(self) -> DoctorReport:
        findings = []
        for check in self.checks:
            try:
                findings.extend(check(self.framework))
            except Exception as e:  # a broken check is itself a finding, never a crash of the doctor
                name = getattr(check, "__name__", "check").replace("check_", "")
                findings.append(Finding(name, f"{name}.check_crashed", Severity.ERROR, f"The check failed: {e}"))
        return DoctorReport(findings)

    def fix(self, tier_max: int = 1) -> DoctorReport:
        before = self.run()
        fixed = []
        for finding in before.findings:
            if finding.fix is None or finding.fix_tier < 1 or finding.fix_tier > tier_max:
                continue
            if finding.severity not in (Severity.WARN, Severity.ERROR):
                continue
            try:
                finding.fix()
                fixed.append(finding.code)
            except Exception:
                pass
        after = self.run()
        problems = {f.code for f in after.findings if f.severity in (Severity.WARN, Severity.ERROR)}
        after.fixed = [code for code in fixed if code not in problems]
        after.still_present = [code for code in fixed if code in problems]
        return after


# --- what an AI reads of the findings --------------------------------------------------------------------------------

def ai_folder_labels(framework) -> Dict[str, str]:
    """The framework's own folders by the label an AI may read: state (the per-user folder), cache, output, models,
    targets, community and framework (the installation)."""
    roots: Dict[str, str] = {}

    def add(path: Any, label: str) -> None:
        try:
            if path:
                roots.setdefault(str(Path(path)), label)
        except (TypeError, ValueError):
            pass

    config = getattr(framework, "config", None)
    try:
        add(getattr(config, "user_state_dir", None), "state")
    except Exception:  # noqa: BLE001 - a config that cannot name it: no label, the file name rule still applies
        pass
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    add(getattr(engine, "cache_dir", None), "cache")
    add(getattr(engine, "output_dir", None), "output")
    resolve = getattr(framework, "resolve_path", None)
    for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("models_dir", "models")):
        try:
            value = config.get(key, None) if config is not None else None
            if value:
                add(resolve(value) if callable(resolve) else value, label)
        except Exception:  # noqa: BLE001
            continue
    add(getattr(getattr(framework, "target_manager", None), "targets_dir", None), "targets")
    add(getattr(getattr(framework, "community_manager", None), "community_dir", None), "community")
    add(getattr(getattr(framework, "info", None), "installation_path", None), "framework")
    add(getattr(framework, "app_root", None), "framework")
    return roots


def ai_findings(framework, report: DoctorReport) -> List[Dict[str, Any]]:
    """The findings as an AI may read them (the run_doctor tool): every message and fix hint, a crashed check's
    included, with the framework's folders named by label ('the state folder', 'state:audit.db') and any other path
    by its file name only - no host path, no user name - and secrets redacted. The person's own output (the CLI,
    `--json` included) keeps the full paths."""
    from orchestrator.tools.tools_runtime import label_paths
    from orchestrator.utils.helpers import redact_secrets
    roots = ai_folder_labels(framework)

    def label(text: str) -> str:
        return label_paths(redact_secrets(str(text or "")), roots)

    return [{"subsystem": f.subsystem, "code": f.code, "severity": f.severity.value, "message": label(f.message),
             "fix_hint": label(f.fix_hint)} for f in report.findings]
