#!/usr/bin/env python3
"""
The device runtime steps for callers without a terminal (the MCP tools probe_remote_device, deploy_device_runtime and
run_device_benchmark): the same steps as `llm-cli scan --device` and `llm-cli device runtime build|deploy|verify`,
returning results and raising DeviceRuntimeError with a reason code and the next step instead of printing.

Every step reaches only a trusted device (orchestrator/runtime/device/trusted.py): the host key the device presents now
must be exactly the one the person confirmed, and the login uses the framework's own SSH key, never a password. Nothing
here trusts a key, pairs a device or changes the deploy settings (decision D7: the person decides those beforehand).

Each step opens the one board session (orchestrator/remote/session.py RemoteSession with KeyOnly credentials, decision
2: an AI path can never pair): when a Wi-Fi board's network drops, the session waits for the board (N5), logs in again
with the same host key check and goes on; a board that does not answer again ends the step with its SSH reason code.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from orchestrator.remote.errors import RemoteFailure
from orchestrator.runtime.device.trusted import TrustedDevice
from orchestrator.utils.next_steps import WithSteps


class DeviceRuntimeError(WithSteps, RemoteFailure):
    """A device step that cannot be done; `code` is a tool reason code, `next` the step that helps, `reason` one of the
    SSH reason codes of orchestrator/runtime/device/reasons.py when the board could not be reached or logged in to.
    `external_text` holds what the device answered (its stderr, a check's output): never part of the message,
    returned to AI callers marked as external text. `error` is the exception behind a failed contact (the person's
    windows name what failed on this computer from it; an AI never gets it)."""

    def __init__(self, message: str, code: str = "failed", next: str = "",
                 external_text: Optional[List[str]] = None, reason: str = "",
                 error: Optional[BaseException] = None):
        super().__init__(message, reason=reason, error=error)
        self.code, self.next = code, next
        self.external_text = list(external_text or [])


def note_reach(state_dir: Path, device: TrustedDevice, step: str, reason: str) -> None:
    """Records for Mojo what this contact with the board found (W1: Mojo reads it instead of contacting the board): a
    reason code keeps the problem of the step, "" (reached and logged in) ends it. Nothing when `step` is empty; never
    fails the step (trusted.record_reach)."""
    from orchestrator.runtime.device.trusted import record_reach
    record_reach(state_dir, step, reason, device_id=device.device_id)


def unreachable(device: TrustedDevice, reason: str, state_dir: Optional[Path] = None,
                step: str = "", error: Optional[BaseException] = None) -> DeviceRuntimeError:
    """The refusal for a board that could not be reached or logged in to, with its SSH reason code (N6); recorded for
    Mojo when the step is named."""
    from orchestrator.runtime.device import reasons
    if state_dir is not None:
        note_reach(state_dir, device, step, reason)
    return DeviceRuntimeError(f"The device {device.label} cannot be used now ({reason}).",
                              code=reasons.TOOL_CODES[reason], next=reasons.AI_NEXT[reason], reason=reason,
                              error=error)


def by_label(text: str, device: TrustedDevice) -> str:
    """`text` with the board's address (its host, also with its port) named by the board's label: the address, like
    the login, is the person's to see, never an AI's (D0). The board session's own sentences name the host (the
    person's windows show them as they are); this is their copy for an AI."""
    import re
    out, host = str(text), str(device.host or "")
    if not host:
        return out
    port = int(device.port)
    forms = {host, device.address, f"{host}:{port}", f"[{host}]:{port}"}
    for form in sorted(forms, key=len, reverse=True):   # the longest form first, so a port never stays behind
        out = re.sub(r"(?<![\w.-])" + re.escape(form) + r"(?![\w-])", lambda _m: device.label, out)
    return out


def _device_said(what: str, device: TrustedDevice, error: Exception) -> DeviceRuntimeError:
    """A failed remote step: the framework's sentence as the message, the device's words (inside the remote error's
    text) as external text, the board named there by its label only (by_label)."""
    return DeviceRuntimeError(f"{what} on {device.label} failed; the details from the device are kept apart as "
                              "external text.", code="failed", external_text=[by_label(str(error), device)[:2000]])


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


def read_key(device: TrustedDevice, state_dir: Path, step: str = "") -> str:
    """The fingerprint of the key the device presents now (a key read, no login); refused unless it is the trusted
    one. Nothing is written but, when `step` names the contact, a failure with an SSH reason code for Mojo."""
    from orchestrator.remote import reasons
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore
    try:
        check = TrustStore(state_dir).check(Endpoint(device.host, device.port))
    except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko
        reason = reasons.reach_code(e)
        if reason:
            raise unreachable(device, reason, state_dir, step, e) from None
        raise DeviceRuntimeError(f"The host key of {device.label} could not be read ({e.__class__.__name__}).",
                                 code="failed", next="Tell the person; the framework's log has the details.") from None
    fp = check.fingerprint
    if check.status == "changed":
        raise unreachable(device, reasons.HOST_KEY_CHANGED, state_dir, step)
    if not check.known or fp != device.fingerprint:
        raise DeviceRuntimeError(f"The host key of {device.label} is not the one the person trusted ({fp}); nothing "
                                 "was done.", code="needs_person",
                                 next="Tell the person: they must check the device and trust it again in the GUI or "
                                      "the CLI. No tool can accept a changed key.")
    return fp


def _not_trusted(device: TrustedDevice, fp: str) -> DeviceRuntimeError:
    return DeviceRuntimeError(f"The host key of {device.label} is not the one the person trusted ({fp}); nothing "
                              "was done.", code="needs_person",
                              next="Tell the person: they must check the device and trust it again in the GUI or "
                                   "the CLI. No tool can accept a changed key.")


def connect(device: TrustedDevice, state_dir: Path, user: str, expect_fingerprint: str,
            on_client: Optional[Callable[[Any], None]] = None, step: str = "", *, cancel: Any = None,
            on_event: Optional[Callable[[Any], None]] = None, wait_seconds: Optional[float] = None):
    """The board session (orchestrator/remote/session.py RemoteSession, logged in) with the trusted device, as `user`,
    with the framework's key only (KeyOnly: never a password, so this path can never pair; decision 2), after the key
    it presents was compared with the trusted one and with `expect_fingerprint` (the one the person was shown). Every
    login after a network drop checks it again, and while the board's network is gone the session waits for it (N5;
    `on_event` gets its events, `wait_seconds` is how long it waits, the session's default 300 s when None). `step`
    names the contact (probe, runtime_deploy, benchmark) for Mojo's record of the board. `on_client` gets the session
    once (closing it closes its connection); `cancel` (a session.CancelToken) ends any wait at once."""
    from orchestrator.remote import reasons
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.session import KeyOnly, RemoteSession, SessionFailure, Stopped as SessionStopped
    if expect_fingerprint != device.fingerprint:
        # the person was shown another key than the one the record holds now: refused after one key read, no login
        read_key(device, state_dir, step)   # refuses a key that is not the trusted one
        raise DeviceRuntimeError(f"The device {device.label} presents another host key than the one shown; nothing "
                                 "was done.", code="needs_person", next="Call the tool again.")
    options: Dict[str, Any] = {} if wait_seconds is None else {"wait_seconds": wait_seconds}
    session = RemoteSession(Endpoint(device.host, device.port), user, Path(state_dir), credentials=KeyOnly(),
                            expect_fingerprint=expect_fingerprint, cancel=cancel, on_event=on_event, **options)
    try:
        session.open()
    except SessionStopped:
        raise Stopped("The contact with the device") from None
    except SessionFailure as failed:
        check, e = session.last_check, failed.error
        if failed.kind == "key_read":   # unreachable, no SSH server, no paramiko
            if failed.reason:
                raise unreachable(device, failed.reason, state_dir, step, e) from None
            raise DeviceRuntimeError(f"The host key of {device.label} could not be read ({e.__class__.__name__}).",
                                     code="failed", next="Tell the person; the framework's log has the details.") \
                from None
        if failed.kind == "key_changed":
            raise unreachable(device, reasons.HOST_KEY_CHANGED, state_dir, step) from None
        if failed.kind in ("not_trusted", "other_key"):
            raise _not_trusted(device, check.fingerprint if check is not None else "") from None
        reason = reasons.classify(e) if e is not None else None   # the login: authentication or network
        if reason is not None and reason != reasons.AUTH_FAILED:
            raise unreachable(device, reason, state_dir, step, e) from None
        note_reach(state_dir, device, step, reasons.AUTH_FAILED)
        raise DeviceRuntimeError(f"The framework's SSH key does not log in to {device.label} as {user} "
                                 f"({(e or failed).__class__.__name__}).", code="needs_person",
                                 reason=reasons.AUTH_FAILED,
                                 next="Ask the person to pair the device once with 'llm-cli deploy run' or "
                                      "'llm-cli scan --device' (they enter the password there).") from None
    note_reach(state_dir, device, step, "")   # reached and logged in: a recorded problem ends
    if on_client is not None:
        on_client(session)
    return session


def _session_failed(device: TrustedDevice, error: Exception, state_dir: Path, step: str, what: str,
                    cancel: Any = None) -> DeviceRuntimeError:
    """A step on the board that the board session ended: the board did not answer again after a network drop (its
    SSH reason code, recorded for Mojo), a cancel, or the device's own words (kept apart as external text)."""
    if cancel is not None and cancel.cancelled or getattr(error, "kind", "") == "stopped":
        return Stopped(what)
    reason = getattr(error, "reason", "")
    if reason:
        return unreachable(device, reason, state_dir, step, getattr(error, "error", None))
    return _device_said(what, device, error)


# --- probe ----------------------------------------------------------------------------------------------------------

def probe(device: TrustedDevice, state_dir: Path, expect_fingerprint: str, *,
          on_client: Optional[Callable[[Any], None]] = None, cancel: Any = None,
          on_event: Optional[Callable[[Any], None]] = None) -> Dict[str, Any]:
    """Runs the read-only hardware probe on the device (as `llm-cli scan --device`) and stores its profile under the
    device id (the label and address the person gave are kept). Returns what was stored, without device text. A
    network drop makes the session wait for the board and run the probe's command again (N5)."""
    from orchestrator.remote.session import SessionFailure, Stopped as SessionStopped
    from orchestrator.runtime.profile import ProfileStore
    from orchestrator.runtime.remote_probe import RemoteProbeError, probe_over_ssh
    session = connect(device, state_dir, device.user, expect_fingerprint, on_client, step="probe", cancel=cancel,
                      on_event=on_event)
    try:
        try:
            result = probe_over_ssh(session)
        except RemoteProbeError as e:
            raise _device_said("The probe", device, e) from None
        except (SessionFailure, SessionStopped) as e:
            raise _session_failed(device, e, state_dir, "probe", "The probe", cancel) from None
    finally:
        session.close()
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
    from orchestrator.runtime.device.steps import plan_runtime
    try:
        plan = plan_runtime(profile)
    except PlanError as e:
        raise DeviceRuntimeError(f"No device runtime can be planned for this device: {e}", code="not_supported") \
            from None
    return DevicePlanFacts(build_id=plan.build_id, built=BuildStore(state_dir).find(plan) is not None,
                           source_cached=cached_source(pinned_source(), state_dir) is not None,
                           target=dict(plan.target), builder=dict(plan.builder), notes=list(plan.notes))


def ensure_build(state_dir: Path, device: TrustedDevice, profile: Any, expect_build_id: str, *,
                 cancelled: Callable[[], bool] = lambda: False, say: Say = lambda _m: None,
                 jobs: Optional[int] = None, mirror: Optional[str] = None, config: Any = None):
    """(BuildStore, StoredBuild, packaged tarball) of the device's runtime: built in the builder container when it is
    not built yet (the pinned source is fetched when it is not cached, from `mirror` when given; `jobs` parallel
    compile jobs), then packaged; the same step as `llmcf device runtime build` (steps.build_runtime). The plan must
    still be the one the person was shown (`expect_build_id`)."""
    from orchestrator.runtime.build.source import SourceError
    from orchestrator.runtime.build.store import BuildFailed, StoreError
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.package import PackageError
    plan = steps.plan_runtime(profile)
    if plan.build_id != expect_build_id:
        raise DeviceRuntimeError("The device's runtime plan changed after the person was asked (its probe, the pin or "
                                 "the recipe changed); nothing was done.", code="failed", next="Call the tool again.")

    def on_step(kind: str, _build: Any) -> None:
        if kind == "fetch":
            say("fetching the pinned llama.cpp source")
        elif kind == "build":
            say(f"building llama.cpp for {device.label} in the builder container")

    try:
        built = steps.build_runtime(state_dir, device.device_id, profile, plan, pinned_source(), jobs=jobs,
                                    mirror=mirror, on_step=on_step, log=say, cancelled=cancelled, reuse_package=True,
                                    settings=config)
    except SourceError as e:
        raise DeviceRuntimeError(f"The pinned llama.cpp source: {e}", code="failed") from None
    except (BuildFailed, StoreError) as e:
        raise DeviceRuntimeError(f"The device runtime could not be built: {e}", code="failed") from None
    except PackageError as e:
        raise DeviceRuntimeError(f"The device runtime could not be packaged: {e}", code="failed") from None
    except steps.BuildCancelled:
        raise Stopped("The build") from None
    return built.builds, built.build, built.packaged


def deploy(state_dir: Path, device: TrustedDevice, profile: Any, settings: Any, expect_fingerprint: str,
           expect_build_id: str, *, on_client: Optional[Callable[[Any], None]] = None,
           cancelled: Callable[[], bool] = lambda: False, say: Say = lambda _m: None, cancel: Any = None,
           on_event: Optional[Callable[[Any], None]] = None, config: Any = None) -> Dict[str, Any]:
    """Builds (when needed) and installs the device runtime as the deploy settings say, checks the new build on the
    device and activates it only when it passes: the one flow of `llm-cli device runtime deploy` and the Boards window
    (steps.deploy_runtime_flow; steps.install_runtime saves the deployment's record), with this path's login only: the
    trusted key, never a password, and no trusted record, question or benchmark (decision 2). Over the board session:
    the upload resumes after a network drop, the checks run again, and a step that must not run twice ends with the
    board's state named."""
    from orchestrator.remote.session import CancelToken
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.remote import RemoteError
    cancel = cancel if cancel is not None else CancelToken(check=cancelled)
    # config: the framework's settings (offline mode: the source only from the own network, utils/locality.py)
    builds, build, packaged = ensure_build(state_dir, device, profile, expect_build_id, cancelled=cancelled, say=say,
                                           config=config)
    source = pinned_source()

    def open_board():
        session = connect(device, state_dir, settings.ssh_user, expect_fingerprint, on_client, step="runtime_deploy",
                          cancel=cancel, on_event=on_event)
        return session, getattr(session, "key", None)

    def check_cancel() -> None:
        if cancelled():
            raise Stopped("The deploy")

    # the AI's hooks: its own login only; the root refusal stays deploy_runtime's (the device's words, as before)
    hooks = steps.DeployHooks(open_board=open_board, progress=say, check_cancel=check_cancel, refuse_root=False)
    try:
        flow = steps.deploy_runtime_flow(state_dir, device.device_id, profile, settings, hooks=hooks, build=build,
                                         packaged=packaged, source=source,
                                         arch=str(build.plan.get("target", {}).get("arch") or profile.arch))
    except RemoteError as e:
        raise _session_failed(device, e, state_dir, "runtime_deploy", "The deploy", cancel) from None
    outcome = flow.outcome
    result, report, ok = outcome.result, outcome.report, outcome.ok
    # every older build stays on the board (round 5 f): only their number reaches the caller, never their folders
    return {"activated": ok, "build_id": build.build_id, "install": settings.install, "previous": result.previous,
            "older_builds_kept": len(result.older),
            "checks": [c.as_dict() for c in (report.checks if report is not None else [])],
            "notes": list(result.notes), **steps.heat_facts(report)}


def benchmark(state_dir: Path, device: TrustedDevice, profile: Any, settings: Any, expect_fingerprint: str, *,
              allow_untrusted: bool, on_client: Optional[Callable[[Any], None]] = None,
              consent: Optional[Callable[[str], None]] = None,
              output_dirs: Sequence[Path] = (), cancel: Any = None,
              on_event: Optional[Callable[[Any], None]] = None) -> Dict[str, Any]:
    """The checks of the deployed runtime on the device with a short llama-bench run on the model the deploy settings
    name (as `llm-cli device runtime verify --bench-model`). A model that is not a catalog model runs only when
    `allow_untrusted` (the person approved exactly that); `consent(sha256)` writes that consent to the audit log before
    the model is loaded. The measurement runs on the board, never on this computer (seventh round 2'); its result is
    recorded with every build of that model below `output_dirs` as "tested on" the board (tested.py, N3). The
    short llama-bench run runs on the board on its own and is followed again after a network drop (round 16 G4)."""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device import steps
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
    client = connect(device, state_dir, settings.ssh_user, expect_fingerprint, on_client, step="benchmark",
                     cancel=cancel, on_event=on_event)
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
            # the same catalog check as the CLI's and the Boards window's benchmark (steps.choose_bench_model)
            trusted = digest in steps.catalog_model_digests()
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
            raise _session_failed(device, e, state_dir, "benchmark", "The checks", cancel) from None
    finally:
        client.close()
    record.update({"verification": report.as_dict(), "verified_at": report_time()})
    record.pop("format", None)
    devices.save_record(record)
    steps.record_heat(state_dir, device.device_id, report.heat)   # for Mojo, until the next reading
    recorded = record_board_test(output_dirs, device, profile, report, digest, record["build_id"], source,
                                 settings.kv_choice(profile).kind)
    smoke = report.check("smoke")
    bench = (smoke.data.get("bench") if smoke is not None else None) or []
    speeds = {("prompt_ts" if r.get("n_prompt") else "gen_ts"): r.get("avg_ts") for r in bench
              if isinstance(r.get("avg_ts"), (int, float))}
    return {"ok": report.ok, "trusted_model": trusted, "model_sha256": digest, "build_id": record["build_id"],
            "checks": [c.as_dict() for c in report.checks], "recorded_builds": len(recorded), **speeds,
            **steps.heat_facts(report)}


def record_board_test(output_dirs: Sequence[Path], device: Any, profile: Any, report: Any, digest: Optional[str],
                      runtime_build_id: str, source: Any, kv_cache: Optional[str] = None) -> List[Path]:
    """Records a benchmark on the board with the builds of its model ("tested on", N3) when the short llama-bench run
    ran; returns the build folders it was recorded with."""
    from orchestrator.runtime.device.tested import make_entry, record_device_test
    smoke = report.check("smoke") if report is not None else None
    if not digest or smoke is None or smoke.ok is None:
        return []
    entry = make_entry(device=device, profile=profile, report=report, model_sha256=digest,
                       runtime_build_id=runtime_build_id, llama_cpp=f"b{source.build_number} {source.commit[:8]}",
                       kv_cache=kv_cache)
    return record_device_test(output_dirs, entry)


def report_time() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["DevicePlanFacts", "DeviceRuntimeError", "Stopped", "benchmark", "connect", "deploy", "deploy_settings",
           "device_profile", "ensure_build", "note_reach", "pinned_source", "plan_facts", "probe", "read_key",
           "record_board_test", "unreachable"]
