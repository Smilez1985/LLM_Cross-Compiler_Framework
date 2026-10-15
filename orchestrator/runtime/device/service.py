#!/usr/bin/env python3
"""
The device runtime steps for callers without a terminal (the MCP tools probe_remote_device, deploy_device_runtime and
run_device_benchmark): the same steps as `llm-cli scan --device` and `llm-cli device runtime build|deploy|verify`,
returning results and raising DeviceRuntimeError with a reason code and the next step instead of printing.

Every step reaches only a trusted device (orchestrator/runtime/device/trusted.py): the host key the device presents now
must be exactly the one the person confirmed, and the login uses the framework's own SSH key, never a password. Nothing
here trusts a key, pairs a device or changes the deploy settings (decision D7: the person decides those beforehand).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from orchestrator.runtime.device.trusted import TrustedDevice


class DeviceRuntimeError(RuntimeError):
    """A device step that cannot be done; `code` is a tool reason code, `next` the step that helps. `external_text`
    holds what the device answered (its stderr, a check's output): never part of the message, returned to AI callers
    marked as external text."""

    def __init__(self, message: str, code: str = "failed", next: str = "",
                 external_text: Optional[List[str]] = None):
        super().__init__(message)
        self.code, self.next = code, next
        self.external_text = list(external_text or [])


def _device_said(what: str, device: TrustedDevice, error: Exception) -> DeviceRuntimeError:
    """A failed remote step: the framework's sentence as the message, the device's words (inside the remote error's
    text) as external text."""
    return DeviceRuntimeError(f"{what} on {device.label} failed; the details from the device are kept apart as "
                              "external text.", code="failed", external_text=[str(error)[:2000]])


class Stopped(DeviceRuntimeError):
    def __init__(self, what: str = "The step"):
        super().__init__(f"{what} was cancelled.", code="failed")


Say = Callable[[str], None]


def pinned_source():
    from orchestrator.runtime.catalog import load_catalog
    source = load_catalog().llama_cpp.source
    if source is None:
        raise DeviceRuntimeError("configs/llama_runtime.yml has no llama_cpp.source (the pinned commit).",
                                 code="not_supported", next="Tell the person the installation lacks the pin.")
    return source


def read_key(device: TrustedDevice, state_dir: Path) -> str:
    """The fingerprint of the key the device presents now (a key read, no login); refused unless it is the trusted
    one. Nothing is written."""
    from orchestrator.utils import ssh_trust
    try:
        key = ssh_trust.fetch_host_key(device.host, device.port)
        status = ssh_trust.check_host_key(state_dir, device.host, device.port, key)
    except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko
        raise DeviceRuntimeError(f"The device {device.label} cannot be reached ({e.__class__.__name__}).",
                                 code="hardware_unavailable",
                                 next="Ask the person whether the device is switched on and on the network.") from None
    fp = ssh_trust.fingerprint(key)
    if status != "known" or fp != device.fingerprint:
        raise DeviceRuntimeError(f"The host key of {device.label} is not the one the person trusted ({fp}); nothing "
                                 "was done.", code="needs_person",
                                 next="Tell the person: they must check the device and trust it again in the GUI or "
                                      "the CLI. No tool can accept a changed key.")
    return fp


def connect(device: TrustedDevice, state_dir: Path, user: str, expect_fingerprint: str,
            on_client: Optional[Callable[[Any], None]] = None):
    """An SSH client for the trusted device, as `user`, with the framework's key only (never a password), after the
    key it presents was compared with `expect_fingerprint` (the one the person was shown)."""
    from orchestrator.utils import ssh_trust
    fp = read_key(device, state_dir)
    if fp != expect_fingerprint:
        raise DeviceRuntimeError(f"The device {device.label} presents another host key than the one shown; nothing "
                                 "was done.", code="needs_person", next="Call the tool again.")
    try:
        client = ssh_trust.connect(device.host, user, state_dir, port=device.port, password=None)
    except Exception as e:  # noqa: BLE001 - authentication or network
        raise DeviceRuntimeError(f"The framework's SSH key does not log in to {device.label} as {user} "
                                 f"({e.__class__.__name__}).", code="needs_person",
                                 next="Ask the person to pair the device once with 'llm-cli deploy run' or "
                                      "'llm-cli scan --device' (they enter the password there).") from None
    if on_client is not None:
        on_client(client)
    return client


# --- probe ----------------------------------------------------------------------------------------------------------

def probe(device: TrustedDevice, state_dir: Path, expect_fingerprint: str, *,
          on_client: Optional[Callable[[Any], None]] = None) -> Dict[str, Any]:
    """Runs the read-only hardware probe on the device (as `llm-cli scan --device`) and stores its profile under the
    device id (the label and address the person gave are kept). Returns what was stored, without device text."""
    from orchestrator.runtime.profile import ProfileStore
    from orchestrator.runtime.remote_probe import RemoteProbeError, probe_over_ssh
    client = connect(device, state_dir, device.user, expect_fingerprint, on_client)
    try:
        try:
            result = probe_over_ssh(client)
        except RemoteProbeError as e:
            raise _device_said("The probe", device, e) from None
    finally:
        client.close()
    store = ProfileStore(state_dir)
    store.save_device(device.device_id, result.profile, label=device.label, address=device.address)
    return {"probe_id": device.device_id, "tool": result.tool, "warnings": len(result.profile.warnings)}


# --- the device's runtime -------------------------------------------------------------------------------------------

@dataclass
class DevicePlanFacts:
    build_id: str
    built: bool
    source_cached: bool
    target: Dict[str, Any]
    builder: Dict[str, Any]
    notes: List[str] = field(default_factory=list)


def device_profile(state_dir: Path, device: TrustedDevice):
    from orchestrator.runtime.profile import ProfileError, ProfileStore
    try:
        profile = ProfileStore(state_dir).load_device(device.device_id)
    except ProfileError:
        profile = None
    if profile is None:
        raise DeviceRuntimeError(f"There is no hardware probe of {device.label} yet, so its runtime cannot be "
                                 "planned.", code="missing_information",
                                 next="Probe it first with probe_remote_device (or the person runs 'llm-cli scan "
                                      "--device').")
    return profile


def deploy_settings(state_dir: Path, device: TrustedDevice, profile: Any):
    """The deploy settings the person chose before (decision D7); refused when they cannot be deployed."""
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileStore
    try:
        settings = DeploySettings.from_dict(ProfileStore(state_dir).load_deploy_settings(device.device_id))
    except (SettingsError, ValueError) as e:
        raise DeviceRuntimeError(f"The deploy settings of {device.label} cannot be used: {e}", code="needs_person",
                                 next="Ask the person to set them again with 'llm-cli device settings'.") from None
    if settings.ssh_user is None:
        settings = settings.changed(ssh_user=device.user) if device.user != "root" else settings
    if settings.ssh_user is None:
        raise DeviceRuntimeError(f"No unprivileged SSH user is set for {device.label}.", code="needs_person",
                                 next="Ask the person to set one with 'llm-cli device settings DEVICE --user NAME'.")
    problems = settings.problems(profile)
    if problems:
        raise DeviceRuntimeError(f"The deploy settings of {device.label} cannot be deployed: " + "; ".join(problems),
                                 code="needs_person", next="Ask the person to change them with 'llm-cli device "
                                                           "settings'.")
    return settings


def plan_facts(state_dir: Path, profile: Any) -> DevicePlanFacts:
    from orchestrator.runtime.build.recipe import PlanError
    from orchestrator.runtime.build.source import cached_source
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import plan_device_runtime
    try:
        plan = plan_device_runtime(profile, catalog=load_catalog(), host=host_platform())
    except PlanError as e:
        raise DeviceRuntimeError(f"No device runtime can be planned for this device: {e}", code="not_supported") \
            from None
    return DevicePlanFacts(build_id=plan.build_id, built=BuildStore(state_dir).find(plan) is not None,
                           source_cached=cached_source(pinned_source(), state_dir) is not None,
                           target=dict(plan.target), builder=dict(plan.builder), notes=list(plan.notes))


def ensure_build(state_dir: Path, device: TrustedDevice, profile: Any, expect_build_id: str, *,
                 cancelled: Callable[[], bool] = lambda: False, say: Say = lambda _m: None):
    """(BuildStore, StoredBuild, packaged tarball) of the device's runtime: built in the builder container when it is
    not built yet (the pinned source is fetched when it is not cached), then packaged. The plan must still be the one
    the person was shown (`expect_build_id`)."""
    from orchestrator.runtime.build.executor import BuildExecutor, DockerEngine
    from orchestrator.runtime.build.source import SourceError, cached_source, fetch_source
    from orchestrator.runtime.build.store import BuildFailed, BuildStore, StoreError
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.build.verify import Verifier
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import DeviceRuntimeStore, PackageError, package_runtime, \
        plan_device_runtime
    plan = plan_device_runtime(profile, catalog=load_catalog(), host=host_platform())
    if plan.build_id != expect_build_id:
        raise DeviceRuntimeError("The device's runtime plan changed after the person was asked (its probe, the pin or "
                                 "the recipe changed); nothing was done.", code="failed", next="Call the tool again.")
    source = pinned_source()
    builds = BuildStore(state_dir)
    try:
        src = cached_source(source, state_dir)
        if src is None:
            say("fetching the pinned llama.cpp source")
            src = fetch_source(source, state_dir, log=say)
    except SourceError as e:
        raise DeviceRuntimeError(f"The pinned llama.cpp source: {e}", code="failed") from None
    if cancelled():
        raise Stopped("The build")
    build = builds.find(plan)
    if build is None:
        say(f"building llama.cpp for {device.label} in the builder container")
        executor = BuildExecutor(engine=DockerEngine(), log=say)
        try:
            build = builds.build(plan, source=src, executor=executor, verifier=Verifier(),
                                 perf_key=profile.perf_key(), host=host_platform())
        except (BuildFailed, StoreError) as e:
            raise DeviceRuntimeError(f"The device runtime could not be built: {e}", code="failed") from None
    if cancelled():
        raise Stopped("The build")
    devices = DeviceRuntimeStore(state_dir, device.device_id)
    packaged = devices.tarball(build)
    if packaged is None:
        try:
            packaged = package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)
        except PackageError as e:
            raise DeviceRuntimeError(f"The device runtime could not be packaged: {e}", code="failed") from None
    return builds, build, packaged


def deploy(state_dir: Path, device: TrustedDevice, profile: Any, settings: Any, expect_fingerprint: str,
           expect_build_id: str, *, on_client: Optional[Callable[[Any], None]] = None,
           cancelled: Callable[[], bool] = lambda: False, say: Say = lambda _m: None) -> Dict[str, Any]:
    """Builds (when needed) and installs the device runtime as the deploy settings say, checks the new build on the
    device and activates it only when it passes (as `llm-cli device runtime deploy`)."""
    from datetime import datetime, timezone
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell, deploy_runtime, verify_on_device
    builds, build, packaged = ensure_build(state_dir, device, profile, expect_build_id, cancelled=cancelled, say=say)
    source = pinned_source()
    client = connect(device, state_dir, settings.ssh_user, expect_fingerprint, on_client)
    try:
        shell = RemoteShell(client)

        def check_new_build(home: str, folder: str):
            return verify_on_device(shell, home=home, folder=folder, plan_verify=build.plan.get("verify") or {},
                                    pin_build=source.build_number, pin_commit=source.commit, port=settings.port)

        if cancelled():
            raise Stopped("The deploy")
        try:
            result = deploy_runtime(shell, tarball=packaged.path, tarball_sha256=packaged.sha256,
                                    build_id=build.build_id, arch=str(build.plan.get("target", {}).get("arch") or
                                                                      profile.arch), settings=settings,
                                    check=check_new_build, say=say)
        except RemoteError as e:
            raise _device_said("The deploy", device, e) from None
    finally:
        client.close()
    report = result.report
    ok = bool(result.activated and report is not None and report.ok)
    if ok:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        devices = DeviceRuntimeStore(state_dir, device.device_id)
        devices.save_record({"build_id": build.build_id, "deployed_at": now, "install": settings.install,
                             "ssh_user": settings.ssh_user, "previous": result.previous,
                             "perf_key": profile.perf_key(), "verification": report.as_dict(), "verified_at": now,
                             "unit_started": result.unit_started})
    return {"activated": ok, "build_id": build.build_id, "install": settings.install, "previous": result.previous,
            "checks": [c.as_dict() for c in (report.checks if report is not None else [])],
            "notes": list(result.notes)}


def benchmark(state_dir: Path, device: TrustedDevice, profile: Any, settings: Any, expect_fingerprint: str, *,
              allow_untrusted: bool, on_client: Optional[Callable[[Any], None]] = None,
              consent: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """The checks of the deployed runtime on the device with a short llama-bench run on the model the deploy settings
    name (as `llm-cli device runtime verify --bench-model`). A model that is not a catalog model runs only when
    `allow_untrusted` (the person approved exactly that); `consent(sha256)` writes that consent to the audit log before
    the model is loaded."""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell, model_sha256, read_facts, \
        verify_on_device
    from orchestrator.runtime.device.settings import SettingsError, check_model_path
    devices = DeviceRuntimeStore(state_dir, device.device_id)
    record = devices.record()
    if not record:
        raise DeviceRuntimeError(f"No runtime is deployed to {device.label} yet.", code="missing_information",
                                 next="Deploy it first with deploy_device_runtime.")
    if settings.model is None:
        raise DeviceRuntimeError(f"No model on {device.label} is named for the benchmark.", code="needs_person",
                                 next="Ask the person to name one with 'llm-cli device settings DEVICE --model PATH'.")
    stored = BuildStore(state_dir).get(record["build_id"])
    plan_verify = (stored.plan.get("verify") if stored else None) or {}
    source = pinned_source()
    client = connect(device, state_dir, settings.ssh_user, expect_fingerprint, on_client)
    try:
        shell = RemoteShell(client)
        try:
            facts = read_facts(shell)
            if facts.uid == 0:
                raise DeviceRuntimeError("The SSH user on the device is root; the checks run as an unprivileged user "
                                         "only.", code="needs_person",
                                         next="Ask the person to set an unprivileged user in the deploy settings.")
            try:
                path = check_model_path(settings.model_on_device(facts.home))
            except SettingsError as e:
                raise DeviceRuntimeError(f"The model path in the deploy settings cannot be used: {e}",
                                         code="needs_person") from None
            digest = model_sha256(shell, path)
            if digest is None:
                raise DeviceRuntimeError(f"There is no readable model at the path the deploy settings name on "
                                         f"{device.label}.", code="missing_information",
                                         next="Ask the person to copy the model there or change the setting.")
            catalog = load_catalog()
            pinned = {m.sha256 for m in catalog.models if m.sha256}
            if catalog.smoke_model is not None:
                pinned.add(catalog.smoke_model.sha256)
            trusted = digest in pinned
            if not trusted:
                if not allow_untrusted:
                    raise DeviceRuntimeError("The model on the device is not a catalog model and running it was not "
                                             "approved; nothing was loaded.", code="declined")
                if consent is not None:
                    consent(digest)  # raises when the consent cannot be written: nothing runs then
            report = verify_on_device(shell, home=facts.home, plan_verify=plan_verify,
                                      pin_build=source.build_number, pin_commit=source.commit, port=settings.port,
                                      bench_model=path, bench_allowed=True)
        except RemoteError as e:
            raise _device_said("The checks", device, e) from None
    finally:
        client.close()
    record.update({"verification": report.as_dict(), "verified_at": report_time()})
    record.pop("format", None)
    devices.save_record(record)
    smoke = report.check("smoke")
    bench = (smoke.data.get("bench") if smoke is not None else None) or []
    speeds = {("prompt_ts" if r.get("n_prompt") else "gen_ts"): r.get("avg_ts") for r in bench
              if isinstance(r.get("avg_ts"), (int, float))}
    return {"ok": report.ok, "trusted_model": trusted, "model_sha256": digest, "build_id": record["build_id"],
            "checks": [c.as_dict() for c in report.checks], **speeds}


def report_time() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["DevicePlanFacts", "DeviceRuntimeError", "Stopped", "benchmark", "connect", "deploy", "deploy_settings",
           "device_profile", "ensure_build", "pinned_source", "plan_facts", "probe", "read_key"]
