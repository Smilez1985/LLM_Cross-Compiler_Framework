#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the local llama.cpp runtime
"""

import json
from pathlib import Path
from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok
from orchestrator.utils.next_steps import NextStep


RUNTIME_WHY = "Ditto's local model server cannot start without an intact build."
SWITCH = "llm-cli ditto runtime ensure --mode bare-metal --force"


def _ditto_docker():
    from orchestrator.runtime.container_server import DittoDocker
    return DittoDocker()


def _docker_mode(root: Path, pin) -> List[Finding]:
    """Ditto's server in Docker mode (DECISIONS round 7 D): the image recorded in mode.json is there, was built on
    this computer (never pulled), is of the pinned commit and is the build that was verified. Asks Docker only; builds,
    pulls and removes nothing. Nothing in bare-metal mode."""
    from orchestrator.runtime import container_server as cs
    try:
        record = json.loads((root / "mode.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(record, dict) or record.get("mode") != "docker":
        return []
    saved = record.get("docker") if isinstance(record.get("docker"), dict) else {}
    image_id = str(saved.get("image_id") or "")
    name = str(saved.get("tag") or image_id[7:19] or "not recorded")
    try:
        docker = _ditto_docker()
        reason = docker.unavailable()
    except Exception as e:  # noqa: BLE001 - the docker module is missing
        docker, reason = None, f"Docker cannot be used from the framework ({type(e).__name__})"
    if reason:
        return [Finding("runtime", "runtime.docker.unavailable", Severity.WARN,
                        f"Ditto's server runs in Docker mode (image {name}), but {reason}.",
                        f"Start Docker, or switch Ditto to bare metal: {SWITCH} (the models stay).", where=name,
                        why=RUNTIME_WHY, cannot="admin")]
    try:
        image = docker.image(image_id) if image_id else None
    except Exception:  # noqa: BLE001 - the daemon went away meanwhile
        image = None
    if (image is None or image.role != "image" or image.pulled or pin is None
            or image.labels.get(cs.LABEL_COMMIT) != pin.commit
            or image.labels.get(cs.LABEL_BUILD) != saved.get("build_id")):
        what = "is no longer there" if image is None else "is not the image that was verified on this computer"
        return [Finding("runtime", "runtime.docker.image", Severity.WARN,
                        f"Ditto's server runs in Docker mode, but its image {name} {what}.",
                        "llm-cli ditto runtime ensure --mode docker --force makes it again on this computer (the "
                        f"models stay), or switch to bare metal: {SWITCH}.", where=name, why=RUNTIME_WHY,
                        cannot="not_automatic")]
    return [_ok("runtime", "runtime.docker",
                f"Ditto's server runs in Docker mode: image {image.name} ({image.image_id[7:19]}), build "
                f"{str(saved.get('build_id'))[:16]}, built and verified on this computer "
                f"({saved.get('verified_at') or 'time not recorded'}); never pulled from a registry.")]


def _cache_budget(framework) -> List[Finding]:
    """A saved value of the local server's memory budget above this computer's soft cap (runtime/hardware.local_budget,
    measured now: free RAM changes, so a value that was fine may no longer be), unless the person accepted such values
    (local_cache_over_cap_ok). Nothing when every value is auto or within the cap; reads only."""
    config = getattr(framework, "config", None)
    get = getattr(config, "get", None)
    if not callable(get) or bool(get("local_cache_over_cap_ok", False)):
        return []
    from orchestrator.runtime.hardware import CACHE_SETTINGS
    saved = {key: str(get(key, "auto") or "auto").strip().lower() for key in CACHE_SETTINGS}
    if all(value == "auto" for key, value in saved.items() if key != "local_kv_cache"):
        return []
    try:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        budget = LocalLLMRuntime.from_framework(framework).cache_budget()
    except Exception:  # noqa: BLE001 - no catalog or probe: nothing to compare against
        return []
    findings = []
    for key in budget.over_soft_cap:
        value = saved.get(key, "auto")
        cap = budget.soft_caps.get(key)
        what = "0 (never unload the model)" if key == "local_cache_keep_minutes" and value == "0" else value
        findings.append(Finding(
            "runtime", "runtime.cache.over_cap", Severity.WARN,
            f"The saved {key} = {what} is above this computer's soft cap of {cap} (measured now): Ditto's local "
            "server may hold more memory than this computer has to spare.",
            "Set it back to auto (the measured value), or accept such values with local_cache_over_cap_ok.",
            where=key, why="Too much memory for the server makes other programs and the system slow.",
            cannot="person", steps=(NextStep("cache.auto", {"name": key}),)))
    return findings


def _kv_fallback(root: Path) -> List[Finding]:
    """The running server got the f16 KV cache instead of the automatic quantised one because its llama.cpp build has
    no flash attention (server.json kv_fallback, llama_server.needs_flash_attention). Reads one file; nothing else."""
    try:
        state = json.loads((Path(root) / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    wanted = state.get("kv_fallback") if isinstance(state, dict) else None
    if wanted not in ("q8_0", "q4_0"):
        return []
    return [Finding("runtime", "runtime.kv_cache.fallback", Severity.WARN,
                    f"Ditto's server runs with the f16 KV cache instead of the automatic {wanted}: its llama.cpp "
                    "build has no flash attention, which a quantised cache needs, so the same memory holds a shorter "
                    "context.",
                    "Use a llama.cpp build with flash attention, or set the setting local_kv_cache to f16 to make "
                    "it your choice.", where="local_kv_cache",
                    why="A quantised KV cache lets the server keep a longer context in the same memory.",
                    cannot="person")]


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
                                    "Build the runtime again; a changed build is never started.",
                                    where=str(active.path), why=RUNTIME_WHY, cannot="not_automatic"))
        elif pin is not None and active.commit != pin.commit:
            findings.append(Finding("runtime", "runtime.build.outdated", Severity.WARN,
                                    f"The self-built llama.cpp build {active.build_id[:16]} is of commit "
                                    f"{active.commit[:8]}, older than the pin {pin.tag} ({pin.commit[:8]}).",
                                    "Build the runtime again from the pinned commit.", where=str(active.path),
                                    why="It lacks the fixes and checks of the pinned llama.cpp version.",
                                    cannot="not_automatic"))
        else:
            findings.append(_ok("runtime", "runtime.build", describe(active)))
    elif "active" in refs:
        findings.append(Finding("runtime", "runtime.build.missing", Severity.WARN,
                                f"The active self-built llama.cpp build {refs['active'][:16]} is missing or "
                                "incomplete.", "Build the runtime again.", where=str(store.builds_dir),
                                why=RUNTIME_WHY, repair_id="runtime.switch_intact"))
    else:
        stored = store.list_builds()
        findings.append(Finding("runtime", "runtime.build.none", Severity.INFO,
                                "No self-built llama.cpp runtime is in use" +
                                (f" ({len(stored)} stored, none active)." if stored else ".")))
    findings += _docker_mode(store.root, pin)
    findings += _cache_budget(framework)
    findings += _kv_fallback(store.root)
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
