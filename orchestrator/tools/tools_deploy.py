#!/usr/bin/env python3
"""
Devices and deploy for AI callers (MCP design 2.7, work package T8, decision 2), through the one tool registry.

- list_devices: the devices a person trusted and paired in the GUI or the CLI (orchestrator/runtime/device/trusted.py),
  with the label the person gave, the host key's fingerprint, whether the key is still the trusted one, and the last
  deploy. No tool trusts a device, pairs one, or writes a device record.
- create_deploy_package: a built model (artifact id) packaged for a target's device profile, as a job: the free disk is
  checked for twice the model's size first, the package is written under a temporary name and gets its id when
  complete. Docker images go into a package only when each is pinned by its digest or was built on this computer.
- deploy_to_device: a package copied to a trusted, paired device and its framework-generated deploy.sh run there, as
  a job. Two-phase: the question shows the device by its label (its login, account and address, only in the person's
  own window: device account names and device paths never reach an AI, study 6.2 D0) and host key fingerprint, the
  package's sha256 and the trust of the target it was built for; before that the device's host key is read (a key
  read, no login) and must be the one the person trusted. The deploy then refuses when the package's hash or the
  device's key differs from what was shown. Always asked (max decision ask), never covered by a standing approval;
  one deploy per device at a time; cancel_job closes the SSH session and reports the device state as unknown.

No input names a host, address, user, port, password, fingerprint, path or command: devices and packages are ids.
"""

import hashlib
import json
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.remote import text as remote_text
from orchestrator.tools.facts_catalog import blocked, gb_number, size_class, warn, with_blocker
from orchestrator.tools.job_audit import audited_job
from orchestrator.tools.output import ToolOutput, count, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule, Rate, ToolLimits
from orchestrator.utils.helpers import HashCache
from orchestrator.utils.logging import get_logger

logger = get_logger("DeployTools")

PACKAGE_ID_PATTERN = r"^pkg_[0-9a-f]{16}$"
DEVICE_ID_PATTERN = r"^[0-9a-f]{16}$"
ARTIFACT_ID_PATTERN = r"^a_[0-9a-f]{16}$"
PACKAGE_GLOB = "deploy_*.zip"
MANIFEST_MAX = 256 * 1024
DEPLOY_RATE = Rate(6, 3600.0)       # deploys per client and hour (the limits table)
PAIR_NEXT = ("Ask the person to trust and pair the device once in the GUI's Deployment window, with "
             "'llm-cli deploy run' or 'llm-cli scan --device', or to add a device they already trusted with "
             "'llm-cli deploy devices add' (a terminal is needed; no tool can do this).")
OLD_SIDECAR_AI_NOTE = ("Tell the person about the old container; only they remove it, on the board. Never try to stop "
                       "or remove it yourself.")


def package_id(name: str) -> str:
    """A package's id: stable for its file name in the output folder, never its path."""
    return "pkg_" + hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def package_folders(framework, output_dir: Path) -> List[Path]:
    """Where packages lie: the output folder (new packages go there), then the framework's build folder (the output
    folder before the output folder setting, eighth round answer 3) and earlier output folders the person had set,
    whose packages stay usable by their id."""
    from orchestrator.Core.artifact_output import SavedIndex, build_folder
    folders = [Path(output_dir), build_folder(framework)]
    state = getattr(getattr(framework, "config", None), "user_state_dir", None)
    if isinstance(state, (str, Path)):
        folders += SavedIndex(Path(state)).folders()
    return folders


def find_packages(framework, output_dir: Path) -> Dict[str, Path]:
    """{package id: file} of every package in package_folders(); the output folder comes first, and a package of the
    same name in a later folder is left out."""
    found: Dict[str, Path] = {}
    for folder in package_folders(framework, output_dir):
        try:
            entries = sorted(folder.glob(PACKAGE_GLOB))
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            found.setdefault(package_id(path.name), path)
    return found


def hide_paths(text: str, labels: Dict[str, str]) -> str:
    """Framework text for an AI caller with host paths replaced: each folder in `labels` by its label (longest
    first, also resolved), then the home folder by ~ (design 6.4: ids and labels, never paths). A folder is found
    with either separator, a Windows folder in any case, and as Python quotes a Windows path (doubled backslashes)."""
    from orchestrator.security.paths import replace_folder, scrub_for_ai, undouble
    pairs = []
    for folder, label in labels.items():
        for form in {str(folder), str(Path(folder).resolve())}:
            if form and form != "/":
                pairs.append((form, label))
    text = undouble(text)
    for form, label in sorted(pairs, key=lambda p: -len(p[0])):
        text = replace_folder(text, form, label)
    return scrub_for_ai(text)


def event_text(event: Any, label: str) -> str:
    """A running deploy's event (DeploymentManager.DeployEvent) as plain English for the job's progress: the device by
    its label, never its address or account (remote/text.py event_text for an AI)."""
    return remote_text.event_text(event, label, audience="ai")


def unreachable_error(device: Any, error: BaseException) -> ToolError:
    """The refusal for a board whose host key could not be read: the SSH reason code of the failure (N6) with the
    AI's next step, never the address, the account or the exception's text."""
    from orchestrator.remote import reasons
    reason = reasons.reach_code(error)
    if not reason:
        return ToolError(f"The host key of {device.label} could not be read ({error.__class__.__name__}).",
                         code="failed", field="device_id", next="Tell the person; the framework's log has the details.")
    if reason == reasons.LOCAL_PROBLEM:   # this computer, not the board: no blocker of the board's
        return ToolError(f"The host key of {device.label} could not be read: a problem on this computer, not on the "
                         f"board ({reason}).", code=reasons.TOOL_CODES[reason], field="device_id",
                         next=reasons.AI_NEXT[reason], steps=reasons.LOCAL_STEPS)
    return blocked(reason, f"The device {device.label} cannot be reached to read its host key ({reason}).",
                   reasons.AI_NEXT[reason], field="device_id")


def _size(n: Optional[int]) -> str:
    return remote_text.ai_size(n)


# --- inputs and outputs ---------------------------------------------------------------------------------------------

class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceOut(BaseModel):
    device_id: str
    label: str = Field(description="The name the person gave the device")
    host: str
    port: int
    # D0: the account the framework logs in with is never handed to an AI; the person's own window shows it
    user: str = Field(default="device account", description="Always 'device account': the account's name stays with "
                                                            "the person")
    key_type: str
    fingerprint: str = Field(description="SHA256 fingerprint of the host key the person confirmed")
    paired: bool = Field(description="The framework's own SSH key works there (no password needed)")
    usable: bool = Field(description="Paired, and the trusted host key is still in the framework's known_hosts")
    problem: str = ""
    trusted_via: str
    last_deploy: Optional[str] = None
    last_deploy_ok: Optional[bool] = None


class DevicesOut(ToolOutput):
    devices: List[DeviceOut]


class CreatePackageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=18, max_length=18, pattern=ARTIFACT_ID_PATTERN,
                             description="Id of a built model from list_artifacts, e.g. a_0123456789abcdef")
    profile: Optional[str] = Field(default=None, min_length=1, max_length=64,
                                   description="Target id whose device profile (NPU/GPU access) the package uses; "
                                               "default: the target the model was built for")
    include_docker_images: bool = Field(default=False,
                                        description="Also pack the inference (and knowledge-base) Docker images for a "
                                                    "device without internet; refused unless each image is pinned by "
                                                    "its digest or was built on this computer")


class DeployIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(min_length=20, max_length=20, pattern=PACKAGE_ID_PATTERN,
                            description="Id of a package from create_deploy_package, e.g. pkg_0123456789abcdef")
    device_id: str = Field(min_length=16, max_length=16, pattern=DEVICE_ID_PATTERN,
                           description="Id of a trusted, paired device from list_devices")


class JobStartedOut(ToolOutput):
    job_id: str
    kind: str
    state: str


# --- registration ---------------------------------------------------------------------------------------------------

def register_deploy_tools(registry: ToolRegistry, framework: Any, *, service: Any, jobs: Any,
                          target_field: FieldMeta) -> ToolRegistry:
    """Registers list_devices, create_deploy_package and deploy_to_device."""
    from orchestrator.runtime.device.trusted import TrustedDevices

    def devices() -> TrustedDevices:
        return TrustedDevices.for_framework(framework)

    def manager():
        dm = getattr(framework, "deployment_manager", None)
        if dm is None:
            get = getattr(framework, "get_component", None)
            dm = get("deployment_manager") if callable(get) else None
        if dm is None:
            from orchestrator.Core.deployment_manager import DeploymentManager
            dm = DeploymentManager(framework)
        return dm

    def output_dir() -> Path:
        config = getattr(framework, "config", None)
        value = config.get("output_dir", "output") if config is not None else "output"
        resolve = getattr(framework, "resolve_path", None)
        return Path(resolve(value)) if callable(resolve) else Path(value)

    def ledger():
        """The disk reservation ledger every process of this user shares (builds, downloads, packages); None without
        a per-user folder (a stand-in framework)."""
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.disk_plan import ReservationLedger
        folder = state_dir(framework)
        return ReservationLedger(folder) if folder is not None else None

    def artifact(aid: str):
        """(label, manifest with its folder) of a built model by its id."""
        try:
            _aid, label, info = service.artifact_entry(aid)
        except Exception as e:  # noqa: BLE001 - BuildServiceError: unknown id with the next step
            error = ToolError(str(e), code=getattr(e, "code", "unknown_id"),
                              next=getattr(e, "next", "") or "Call list_artifacts for the ids you can use.",
                              field="artifact_id")
            if error.code == "unknown_id":
                with_blocker(error, "artifact_unknown")
            raise error from None
        return label, info

    def packages() -> Dict[str, Path]:
        return find_packages(framework, output_dir())

    def package_path(pid: str) -> Path:
        path = packages().get(pid)
        if path is None:
            raise ToolError(f"Unknown package '{pid}'.", code="unknown_id", field="package_id",
                            next="Create one with create_deploy_package; its job result has the package id. Do not "
                                 "guess ids.")
        return path

    def package_manifest(path: Path) -> Dict[str, Any]:
        """The build manifest inside a package (written by the build: external text), {} when it has none."""
        from orchestrator.Core.builder import BUILD_MANIFEST
        try:
            with zipfile.ZipFile(path) as zf:
                info = zf.getinfo(BUILD_MANIFEST)
                if info.file_size > MANIFEST_MAX:
                    return {}
                data = json.loads(zf.read(info).decode("utf-8", "replace"))
        except (KeyError, OSError, ValueError, zipfile.BadZipFile, RecursionError):
            return {}
        return data if isinstance(data, dict) else {}

    hashes = HashCache(64)

    def package_sha256(path: Path) -> str:
        """The package's sha256, cached by path, size and time (a retried question hashes a large package once)."""
        return hashes.sha256(path)

    def device_record(device_id: str):
        device = devices().get(device_id)
        if device is None:
            raise ToolError(f"Unknown device '{device_id}': only devices a person trusted and paired are known.",
                            code="unknown_id", field="device_id",
                            next="Call list_devices for the ids you can use. " + PAIR_NEXT)
        return device

    def device_problem(device, store) -> str:
        return device_blocker(device, store)[1]

    def device_blocker(device, store):
        """(blocker code, problem in words) of a device that cannot be used now; ("", "") when it can."""
        if not device.paired:
            return "device_not_trusted", "not paired: the framework's own SSH key is not installed there yet"
        status = store.key_status(device)
        if status == "unknown":
            return "host_key_unknown", ("its host key is no longer in the framework's known_hosts: the person must "
                                        "trust it again")
        if status == "changed":
            return "device_host_key_changed", ("the framework's known_hosts holds another host key for its address: "
                                               "the person must check it")
        return "", ""

    def trust_of(target: Optional[str]) -> str:
        if not target:
            return "unknown (the package names no target)"
        from orchestrator.tools.tools_targets import target_marks
        try:
            tid = service.resolve_target(target)
        except Exception:  # noqa: BLE001 - a target that is gone
            return "unknown (the target is not installed)"
        marks = target_marks(framework, tid)
        return marks["trust"] + (", untested (AI-generated)" if marks.get("untested") else "")

    # --- list_devices ---

    def list_devices(_inp: NoInput, ctx: ToolContext) -> DevicesOut:
        store = devices()
        last = store.last_deploys()
        rows = []
        for d in store.list():
            problem = device_problem(d, store)
            seen = last.get(d.device_id) or {}
            rows.append(DeviceOut(device_id=d.device_id, label=d.label, host=d.host, port=d.port,
                                  key_type=d.key_type, fingerprint=d.fingerprint, paired=d.paired,
                                  usable=not problem, problem=problem, trusted_via=d.trusted_via,
                                  last_deploy=str(seen["at"]) if seen.get("at") else None,
                                  last_deploy_ok=bool(seen["ok"]) if "ok" in seen else None))
        if not rows:
            ctx.notes.append("No device is trusted and paired yet. " + PAIR_NEXT)
        return DevicesOut(devices=rows)

    # --- create_deploy_package ---

    def resolve_profile(inp: CreatePackageIn, info: Dict[str, Any]) -> str:
        wanted = inp.profile or str(info.get("target") or "")
        if not wanted:
            raise ToolError("The built model names no target, so the device profile is unknown.",
                            code="missing_information", field="profile",
                            next="Give the target id as profile (list_targets shows them).")
        try:
            return service.resolve_target(wanted)
        except Exception as e:  # noqa: BLE001
            raise ToolError(str(e), code=getattr(e, "code", "invalid_value"), field="profile",
                            next="Call list_targets for the target ids.") from None

    def model_size(info: Dict[str, Any]) -> int:
        from orchestrator.Core.deployment_manager import resolve_model_artifact
        try:
            return resolve_model_artifact(Path(info["path"])).stat().st_size
        except (OSError, ValueError, KeyError) as e:
            raise ToolError(str(e).replace(str(info.get("path", "")), "the build folder"), code="not_supported",
                            field="artifact_id", next="Choose another built model (list_artifacts).") from None

    def package_facts(inp: CreatePackageIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE, image_problem
        from orchestrator.Core.hf_hub import disk_check
        label, info = artifact(inp.artifact_id)
        profile = resolve_profile(inp, info)
        size = model_size(info)
        needed = 2 * size  # the copy in a temporary folder and the zip (design 2.7)
        disk = disk_check(output_dir(), needed, ledger())   # the one disk rule (A4): with every process's reservations
        if not disk["fits"]:
            raise blocked("disk_full", f"Not enough disk space for the package: {_size(needed)} needed (twice the "
                          f"model), {_size(disk['free'])} free, {_size(disk['reserve'])} kept free as a reserve"
                          + (f", {_size(disk['reserved'])} reserved by other calls (running builds or downloads)"
                             if disk["reserved"] else "") + ".",
                          "Tell the person how much space is needed; they can free disk space in the output folder"
                          + (" or wait until the running build or download has finished" if disk["reserved"] else "")
                          + ".")
        facts = [Fact("Built model", label), Fact("Device profile", profile),
                 Fact("Disk", f"{_size(disk['free'])} free on the output folder's disk, about {_size(needed)} needed",
                      volatile=True)]
        if inp.include_docker_images:
            config = getattr(framework, "config", None)
            get = config.get if config is not None else (lambda k, d=None: d)
            images = [get("image_inference_runtime", DEFAULT_INFERENCE_IMAGE)]
            get_component = getattr(framework, "get_component", None)
            client = get_component("docker_client") if callable(get_component) else None
            for image in images:
                problem = image_problem(image, client)
                if problem:
                    raise blocked("image_problem", problem[0].upper() + problem[1:] + ".",
                                  "Package without Docker images, or ask the person to set image_inference_runtime "
                                  "to an image@sha256:<digest> reference.", field="include_docker_images")
            facts.append(Fact("Docker images", ", ".join(images) + " (pinned by digest or built on this computer)"))
        consequence = (f"Packages the built model {label} ({_size(size)}) for the device profile {profile} into a "
                       "deployment package in the output folder" + (", with its Docker images" if
                                                                     inp.include_docker_images else "") + ".")
        bound = {"artifact_id": inp.artifact_id, "profile": profile, "docker": bool(inp.include_docker_images),
                 "size": size}
        warnings = ((warn("disk_tight", free_after_gb=gb_number(disk.get("after")),
                          reserve_gb=gb_number(disk["reserve"])),) if disk.get("tight") else ())
        return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound, warnings=warnings,
                         numbers={"size_class": size_class(int(size)), "needed_bytes": int(needed),
                                  "free_after_bytes": int(disk.get("after", 0)), "reserve_bytes": int(disk["reserve"])},
                         scope={"size_bytes": int(size)}, consequence_template=consequence)

    def running(kind: str, ctx: ToolContext) -> int:
        return sum(1 for j in jobs.list(ctx.owner) if j.kind == kind and not j.finished)

    def create_deploy_package(inp: CreatePackageIn, ctx: ToolContext) -> JobStartedOut:
        from orchestrator.Core.deployment_manager import DeployError
        from orchestrator.Core.disk_plan import InsufficientSpace
        from orchestrator.Core.hf_hub import reserve_space
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("artifact_id") != inp.artifact_id or not bound.get("profile"):
            raise ToolError("The package is not bound to what the person was shown; nothing was packaged.",
                            code="failed", next="Call create_deploy_package again through the framework's registry.")
        label, info = artifact(inp.artifact_id)
        folder, out = Path(info["path"]), output_dir()
        stop = threading.Event()
        # the one disk rule (A4): the package's space is held in the ledger every process shares until it ends
        try:
            reservation = reserve_space(ledger(), out, 2 * int(bound.get("size") or 0), ctx.owner, f"package {label}",
                                        need_label="output", what="deployment package",
                                        key=f"package/{inp.artifact_id}/{id(stop)}", call="the deployment package",
                                        estimate=True,   # twice the model: the copy and the zip
                                        disk_usage=getattr(service, "disk_usage", None))   # the build disk seam
        except InsufficientSpace as e:
            raise ToolError(f"Not enough disk space for the package any more; nothing was packaged. {e}",
                            code="insufficient_resources", next=e.next) from None

        def work(handle) -> Dict[str, Any]:
            from orchestrator.Core.job_service import JobCancelled
            handle.progress(None, None, "packaging the built model")
            try:
                path = manager().create_package(folder, bound["profile"], {"use_docker": bound["docker"]}, out,
                                                cancelled=lambda: stop.is_set() or handle.cancelled)
            except DeployError as e:
                if stop.is_set() or handle.cancelled:
                    raise JobCancelled() from None
                err = ValueError(hide_paths(f"{e} Next: {e.next}" if e.next else str(e),
                                            {str(folder): "the build folder", str(out): "the output folder"}))
                err.code = e.code  # type: ignore[attr-defined]
                raise err from None
            finally:
                reservation.release()
            if stop.is_set() or handle.cancelled:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise JobCancelled()
            handle.progress(None, None, "hashing the package")
            return {"package_id": package_id(path.name), "name": path.name, "size_bytes": path.stat().st_size,
                    "sha256": package_sha256(path), "profile": bound["profile"],
                    "next": "Deploy it with deploy_to_device(package_id, device_id); list_devices shows the devices."}

        def packaged(result: Dict[str, Any]):
            return ("ok", f"packaged {result.get('name')}, {_size(result.get('size_bytes'))}, sha256 "
                          f"{result.get('sha256')}", "")

        # the end of the packaging in the audit log, linked to this call's started entry (design 6.7)
        audited = audited_job(registry, "create_deploy_package", inp.model_dump(mode="json"), ctx, work,
                              f"built model {label}, device profile {bound['profile']}", done=packaged,
                              cancelled=lambda: "cancelled; a partial package is removed")
        try:
            job = jobs.start("package", ctx.owner, audited, on_cancel=stop.set)
        except Exception:
            reservation.release()
            raise
        ctx.notes.append("Follow the packaging with get_job_status (wait_seconds=50); its result has the package id.")
        return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

    # --- deploy_to_device ---

    active: Dict[str, str] = {}      # device id -> job id of the deploy running there
    active_lock = threading.Lock()

    def busy(device_id: str) -> Optional[str]:
        with active_lock:
            jid = active.get(device_id)
            if jid is None:
                return None
            try:
                if jobs.get(jid).finished:
                    active.pop(device_id, None)
                    return None
            except Exception:  # noqa: BLE001 - a job that is gone
                active.pop(device_id, None)
                return None
            return jid

    def read_host_key(device):
        """The key the device presents now (a key read, no login) and its state against the framework's known_hosts.
        Nothing is written: an unknown or changed key is refused, never trusted."""
        from orchestrator.remote import reasons
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore
        try:
            check = TrustStore(devices().state_dir).check(Endpoint(device.host, device.port))
        except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko
            code = reasons.reach_code(e)
            if code:  # recorded for Mojo, which reads it instead of contacting the board (W1)
                devices().note_reach(device.device_id, "deploy", code)
            raise unreachable_error(device, e) from None
        fp = check.fingerprint
        if check.status == "unknown":
            raise blocked("host_key_unknown", f"The device {device.label} presents a host key that is not trusted "
                          f"({fp}); nothing was deployed.", PAIR_NEXT, field="device_id")
        if check.status == "changed" or fp != device.fingerprint:
            devices().note_reach(device.device_id, "deploy", reasons.HOST_KEY_CHANGED)
            raise blocked(reasons.HOST_KEY_CHANGED, f"The host key of {device.label} has CHANGED ({fp}, trusted was "
                          f"{device.fingerprint}). This can be a reinstalled device, or someone in between; nothing "
                          "was deployed.", reasons.AI_NEXT[reasons.HOST_KEY_CHANGED], field="device_id")
        return fp

    def deploy_facts(inp: DeployIn, ctx: ToolContext) -> CallFacts:
        store = devices()
        device = device_record(inp.device_id)
        code, problem = device_blocker(device, store)
        if problem:
            raise blocked(code, f"The device {device.label} cannot be deployed to: {problem}.", PAIR_NEXT,
                          field="device_id")
        jid = busy(device.device_id)
        if jid:
            raise blocked("device_busy", f"A deploy to {device.label} is running ({jid}); one deploy per device at a "
                          "time.", f"Wait for job {jid} with get_job_status.")
        path = package_path(inp.package_id)
        fp = read_host_key(device)
        digest = package_sha256(path)
        manifest = package_manifest(path)
        target = str(manifest.get("target") or "") or None
        built = ", ".join(str(manifest[k]) for k in ("model", "format", "quantization", "board")
                          if manifest.get(k)) or "no build manifest in the package"
        src = "from the build manifest, not checked"
        # D0: the device by its label for everyone; its login (account and address) only in the person's window
        facts = (Fact("Device", device.label),
                 Fact("Login", f"{device.user}@{device.address}", person_only=True),
                 Fact("Host key", f"{device.key_type} {fp}, the key you trusted ({device.trusted_via}, "
                                  f"{device.trusted_at}); read from the device just now"),
                 Fact("Package", f"{path.name}, {_size(path.stat().st_size)}, sha256 {digest}"),
                 Fact("Built model", built, src),
                 Fact("Target", f"{target or 'unknown'}: trust {trust_of(target)}"),
                 Fact("On the device", "deploy.sh, written by the framework: checks the package's checksums, then "
                                       "installs the model into the models folder of the device account (or loads the "
                                       "packed Docker images and starts their containers); it runs as the device "
                                       "account"))
        consequence = f"Copies the package {path.name} to the device {device.label} and runs its deploy.sh there."
        bound = {"package_id": inp.package_id, "package": path.name, "sha256": digest, "device_id": device.device_id,
                 "fingerprint": fp, "host": device.host, "port": device.port, "user": device.user}
        return CallFacts(consequence=consequence, facts=facts, bound=bound, cap=Decision.ASK.value,
                         scope={"device_id": device.device_id}, consequence_template=consequence,
                         names={"device": device.label},
                         # what a decision binds to (design 4.4): the device, its host key and the package's hash
                         digest_keys=("package_id", "package", "sha256", "device_id", "fingerprint", "host", "port",
                                      "user"))

    def deploy_to_device(inp: DeployIn, ctx: ToolContext) -> JobStartedOut:
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("device_id") != inp.device_id or bound.get("package_id") != inp.package_id \
                or not bound.get("sha256") or not bound.get("fingerprint"):
            raise ToolError("The deploy is not bound to what the person was shown; nothing was deployed.",
                            code="failed", next="Call deploy_to_device again through the framework's registry.")
        device = device_record(inp.device_id)
        if (device.host, device.port, device.user, device.fingerprint) != \
                (bound["host"], bound["port"], bound["user"], bound["fingerprint"]):
            raise ToolError(f"The record of {device.label} changed after the person was asked; nothing was deployed.",
                            code="failed", next="Call deploy_to_device again, so the person sees the current device.")
        path = package_path(inp.package_id)
        if path.name != bound["package"]:
            raise ToolError("The package changed after the person was asked; nothing was deployed.", code="failed",
                            next="Call deploy_to_device again.")
        # the one cancel of a board step (remote/session.py CancelToken): it holds the deploy's SSH session, so the
        # job's cancel closes it at once (also one handed after the cancel)
        from orchestrator.remote.session import CancelToken
        stop = CancelToken()
        on_client, close_session = stop.hold, stop.cancel

        arguments = inp.model_dump(mode="json")
        seen: Dict[str, Any] = {}

        def work(handle) -> Dict[str, Any]:
            from orchestrator.Core.job_service import EXTERNAL_KEY, JobCancelled
            handle.progress(None, None, f"deploying to {device.label}")
            from orchestrator.security.paths import device_text
            users = [device.user, bound["user"]]
            # the visible state of the deploy (N5), by the device's label only: whole percents of the upload, the
            # wait for a board whose network dropped
            on_event = remote_text.progress_events(handle.progress, device.label)

            try:
                result = manager().deploy_package(path, bound["host"], bound["user"], port=int(bound["port"]),
                                                  password=None, pair=False, expect_fingerprint=bound["fingerprint"],
                                                  expect_sha256=bound["sha256"], on_client=on_client,
                                                  cancelled=lambda: stop.is_set() or handle.cancelled,
                                                  on_event=on_event)
            except JobCancelled:
                raise
            except ValueError as e:
                # a refusal meant for the AI: the device's words, its account and its paths never reach it (D0). Any
                # other exception is left to the job service, which never hands an unexpected error's text to an AI.
                err = ValueError(device_text(str(e), users, {str(path.parent): "output"}))
                err.code = getattr(e, "code", "failed")  # type: ignore[attr-defined]
                err.external_text = [device_text(line, users, {str(path.parent): "output"})  # type: ignore
                                     for line in getattr(e, "external_text", None) or []]
                raise err from None
            finally:
                with active_lock:
                    if active.get(device.device_id) == handle.job.job_id:
                        active.pop(device.device_id, None)
            seen["device_state"] = result.device_state
            if stop.is_set() or handle.cancelled:
                raise JobCancelled()
            out = {"success": result.success, "exit_status": result.exit_status,
                   "device_state": result.device_state, "device_id": device.device_id,
                   # why the board could not be reached or logged in to (N6), and how often the deploy waited for it
                   # and continued (N5): plain codes and numbers
                   "reason_code": result.code, "resumed": result.resumed, "waited_s": result.waited_s,
                   # the framework's reason (the device's own words are in the output lines), without host paths, the
                   # device account's name or its paths (D0), also on success
                   "reason": device_text(hide_paths(result.reason, {str(path.parent): "the output folder"}), users,
                                         {str(path.parent): "output"})[:500] if result.reason else "",
                   EXTERNAL_KEY: [device_text(line, users, {str(path.parent): "output"}) for line in result.output]}
            if result.old_sidecar:
                # an old Qdrant sidecar from an earlier package with Docker images still on the board: the person
                # removes it, never the framework (framework text, no device text)
                out["old_sidecar"] = result.old_sidecar
                out["board_notes"] = list(result.notes) + [OLD_SIDECAR_AI_NOTE]
            try:
                devices().note_deploy(device.device_id, result.success, path.name)
            except Exception as e:  # noqa: BLE001 - the deploy happened; only the device list's last deploy is missing
                logger.warning(f"The last deploy of {device.label} could not be recorded: {type(e).__name__}")
                out["note"] = "The device list could not record this deploy (its last deploy stays as it was)."
            return out

        def deployed(out: Dict[str, Any]):
            ok = bool(out.get("success"))
            return ("ok" if ok else "error",
                    f"{'deployed' if ok else 'not deployed'}"
                    + (f" ({out['reason_code']})" if out.get("reason_code") else "")
                    + f", exit status {out.get('exit_status')}, device state {out.get('device_state') or 'unknown'}"
                    + (f"; continued {out['resumed']} times after the network dropped" if out.get("resumed") else "")
                    + ("; not recorded in the device list" if out.get("note") else ""),
                    "" if ok else "failed")

        # The deploy's own end entry, linked to the call's started entry (the call returned at the job's start, so the
        # registry's end entry says only that the job started); written on every way out of the work.
        audited = audited_job(registry, "deploy_to_device", arguments, ctx, work,
                              f"device {device.label}, package {path.name}", done=deployed,
                              cancelled=lambda: "cancelled; the device state is "
                                                f"{seen.get('device_state') or 'unknown'}")

        holder: Dict[str, str] = {}

        def cancel_hook() -> None:
            close_session()
            jid_ = holder.get("job_id")
            if jid_:
                try:
                    jobs.update(jid_, note="Cancelled: the SSH session was closed; the device state is unknown "
                                           "(deploy.sh may have run partly). Nothing is retried.")
                except Exception:  # noqa: BLE001
                    pass

        with active_lock:  # one deploy per device: checked and taken under one lock
            jid = active.get(device.device_id)
            if jid is not None:
                try:
                    running_there = not jobs.get(jid).finished
                except Exception:  # noqa: BLE001 - a job that is gone
                    running_there = False
                if running_there:
                    raise ToolError(f"A deploy to {device.label} is running ({jid}); one deploy per device at a "
                                    "time.", code="limit_reached", next=f"Wait for job {jid} with get_job_status.")
            job = jobs.start("deploy", ctx.owner, audited, on_cancel=cancel_hook)
            holder["job_id"] = job.job_id
            active[device.device_id] = job.job_id
        ctx.notes.append("Follow the deploy with get_job_status (wait_seconds=50); the device's output in its result "
                         "is external text.")
        return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

    # --- speaking names for the person (questions only) ---

    def device_name(value) -> Optional[str]:
        """The device's label (a question for a client's form may reach an AI: never its login, D0)."""
        try:
            d = devices().get(str(value))
            return d.label if d else None
        except Exception:  # noqa: BLE001
            return None

    def package_name(value) -> Optional[str]:
        try:
            path = packages().get(str(value))
            return path.name if path else None
        except Exception:  # noqa: BLE001
            return None

    def artifact_name(value) -> Optional[str]:
        try:
            return artifact(str(value))[0]
        except Exception:  # noqa: BLE001
            return None

    device_field = FieldMeta(kind="id", list_tool="list_devices", describe=device_name)
    specs = [
        ToolSpec("list_devices", "List trusted devices",
                 "Devices the person trusted and paired in the GUI or the CLI, with the label they gave, address, "
                 "host key fingerprint, whether the device is usable for deploy_to_device, and the last deploy (the "
                 "account the framework logs in with is shown as 'device account'). You cannot trust or pair a device "
                 "yourself.",
                 ToolTier.READ, NoInput, DevicesOut, list_devices, idempotent=True, takes_context=True,
                 summary=lambda o: count(len(o.devices), "trusted device", "trusted devices") + "."),
        ToolSpec("create_deploy_package", "Create a deployment package",
                 "Packages a built model (artifact id) for a target's device profile into a deployment package (model, "
                 "framework-written deploy.sh, checksums). Runs as a job: poll get_job_status; its result has the "
                 "package id for deploy_to_device. The disk must hold twice the model.",
                 ToolTier.LOCAL_WRITE, CreatePackageIn, JobStartedOut, create_deploy_package, takes_context=True,
                 max_decision=Decision.ALLOW, prepare=package_facts,
                 fields={"artifact_id": FieldMeta(kind="id", list_tool="list_artifacts", describe=artifact_name),
                         "profile": target_field, "include_docker_images": FieldMeta(kind="trust")},
                 examples=({"artifact_id": "a_0123456789abcdef"},
                           {"artifact_id": "a_0123456789abcdef", "profile": "Rockchip"}),
                 limits=ToolLimits(concurrent=2, running=lambda ctx: running("package", ctx), per_day=30,
                                   what="packages"),
                 # a standing approval only without Docker images (they come from outside this computer)
                 grant=GrantRule(rule=_package_rule(), facts=lambda i, c: (None if i.include_docker_images else {}),
                                 warning="package built models for a device profile into the output folder (without "
                                         "Docker images)"),
                 summary=lambda o: f"Packaging job {fact(o.job_id)} is {fact(o.state)}."),
        ToolSpec("deploy_to_device", "Deploy to a device",
                 "Copies a package to a device the person trusted and paired (list_devices) and runs its framework-"
                 "written deploy.sh there. The person confirms every deploy and sees the device, its host key and the "
                 "package's hash. Runs as a job; one deploy per device at a time; cancel_job closes the connection.",
                 ToolTier.EXTERNAL, DeployIn, JobStartedOut, deploy_to_device, takes_context=True, open_world=True,
                 destructive=True, max_decision=Decision.ASK, prepare=deploy_facts,
                 fields={"package_id": FieldMeta(kind="id", describe=package_name), "device_id": device_field},
                 examples=({"package_id": "pkg_0123456789abcdef", "device_id": "0123456789abcdef"},),
                 limits=ToolLimits(rate=DEPLOY_RATE, concurrent=2, running=lambda ctx: running("deploy", ctx),
                                   what="deploys"),
                 summary=lambda o: f"Deploy job {fact(o.job_id)} is {fact(o.state)}."),
    ]
    for spec in specs:
        registry.register(spec)
    return registry


def _package_rule():
    from orchestrator.tools.grants import tool_rule
    return tool_rule("create_deploy_package")
