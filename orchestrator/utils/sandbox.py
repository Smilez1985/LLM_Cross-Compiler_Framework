#!/usr/bin/env python3
"""
Container sandbox options shared by the conversion stages (orchestrator/Core/builder.py) and the llama.cpp builder
container (orchestrator/runtime/build/executor.py).

Both run code that is not the framework's own (model tooling, a target's build scripts, upstream's CMake), so the
container gets no network, no capabilities, no privilege escalation, a read-only root file system with a small /tmp,
a process limit, optionally a memory limit, and the host user instead of root.

The conversion stages add the stage account (stage_user_sandbox, below): their container starts as root with only
CAP_SETUID and CAP_SETGID, the stage runner becomes the host user, and every stage module runs under the image's own
stage account without any capability; sandbox_problem is the host's check of the created container, and
stage_user_support says whether this computer's engine can run it (the builder and the doctor).
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FALLBACK_USER = (1000, 1000)  # used when the host user is root or on Windows
# A tmpfs inside the container (not a host path).
TMPFS = {"/tmp": "rw,nosuid,nodev,size=1g"}  # nosec B108


def sandbox_user() -> Tuple[int, int]:
    """The uid:gid a sandboxed container runs as: the host user, or FALLBACK_USER for root and on Windows."""
    if sys.platform != "win32" and os.getuid() != 0:
        return os.getuid(), os.getgid()
    return FALLBACK_USER


def hand_over(path: Path, user: Tuple[int, int]) -> None:
    """A root host owns the folders it created; the container's fallback user needs to write them."""
    if sys.platform == "win32" or os.getuid() != 0:
        return
    for item in [path, *Path(path).rglob("*")]:
        try:
            os.chown(item, *user)
        except OSError:
            pass


def container_sandbox(user: Tuple[int, int], *, working_dir: str, pids_limit: int = 4096,
                      mem_limit: Optional[str] = None) -> Dict[str, Any]:
    """docker-py create() keyword arguments of the sandbox."""
    options: Dict[str, Any] = {
        "user": f"{user[0]}:{user[1]}",
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": dict(TMPFS),
        "pids_limit": int(pids_limit),
        "working_dir": working_dir,
    }
    if mem_limit:
        options["mem_limit"] = mem_limit
    return options


# --- The stage account of the build sandbox (targets/*/modules/stage_user.sh, entrypoint.sh) ---------------------------
#
# The conversion stages' container starts as root with ONLY the two rights RUNNER_CAPS (cap_drop ALL, cap_add,
# no-new-privileges, a read-only image): the entrypoint makes the stage runner the person's user (RUNNER_IDS_ENV) with
# the two rights as ambient capabilities, and the runner starts every stage module under the image's stage account
# (STAGE_ACCOUNT, other ids, no capabilities), so the kernel refuses a module the runner's memory, where the key of the
# end record is. Docker gives a container that starts as a user other than root no usable capabilities at all, so the
# container starts as root and the switch happens inside.
STAGE_ACCOUNT = (64010, 64010)          # the image's llmstage account (modules/stage_user.sh STAGE_UID/STAGE_GID)
RUNNER_CAPS = ("SETUID", "SETGID")      # setresuid and setgroups for the switch, nothing else
STAGE_ISOLATION_ENV = "STAGE_ISOLATION"
RUNNER_IDS_ENV = "STAGE_RUNNER_IDS"
# The capabilities each engine grants a container when none are dropped (Docker's and Podman's defaults), to read an
# inspect that lists only what differs from them
DOCKER_DEFAULT_CAPS = frozenset(("CHOWN", "DAC_OVERRIDE", "FSETID", "FOWNER", "MKNOD", "NET_RAW", "SETGID", "SETUID",
                                 "SETFCAP", "SETPCAP", "NET_BIND_SERVICE", "SYS_CHROOT", "KILL", "AUDIT_WRITE"))
PODMAN_DEFAULT_CAPS = frozenset(("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "NET_BIND_SERVICE", "SETFCAP",
                                 "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"))
SANDBOX_REFUSED = ("The stage container did not run with the build sandbox's options ({what}), so the build is not "
                   "trusted and counts as failed.")
# The plain words of the stage account's support on this computer (stage_user_support: the doctor and the builder)
STAGE_USER_OK = ("The build sandbox runs the stage modules under their own user here: the kernel refuses them the "
                 "stage runner's memory, so the end record's key stays with the runner.")
STAGE_USER_OFF = ("The build sandbox is switched off (setting build_sandbox): the stage modules run as the stage "
                  "runner's own user, so on a host that lets a process read the memory of another of the same user, a "
                  "module could read the key of the end record.")
STAGE_USER_NO_SUBIDS = ("The rootless container engine cannot run the stage modules under their own user: the sub-ids "
                        "of your account (subuid and subgid) do not reach the stage account's id {uid}, so builds are "
                        "refused before they start.")
STAGE_USER_NO_SUBIDS_WAY = ("An administrator gives your account a range of at least 65536 sub-ids for users and for "
                            "groups (the subuid and subgid files) and restarts the container engine.")
STAGE_USER_UNKNOWN = "The container engine did not answer, so it is not known whether the build sandbox can run here."


def stage_user_sandbox(runner: Tuple[int, int], *, working_dir: str, pids_limit: int = 4096,
                       mem_limit: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(docker-py create() keyword arguments, environment) of the conversion stages' sandbox with the stage account:
    container_sandbox's limits, the container started as root with exactly RUNNER_CAPS, and the runner's ids for the
    entrypoint."""
    options = container_sandbox(runner, working_dir=working_dir, pids_limit=pids_limit, mem_limit=mem_limit)
    options["user"] = "0:0"
    options["cap_add"] = list(RUNNER_CAPS)
    env = {STAGE_ISOLATION_ENV: "user", RUNNER_IDS_ENV: f"{int(runner[0])}:{int(runner[1])}"}
    return options, env


def runner_ids(client: Any = None) -> Tuple[int, int]:
    """The ids the stage runner takes in the container: the person's (sandbox_user); 0:0 under a rootless engine,
    where root in the container is the person outside."""
    from orchestrator.utils.docker_engine import rootless_docker, rootless_podman
    if client is not None and (rootless_docker(client) or rootless_podman(client)):
        return 0, 0
    return sandbox_user()


def _sub_ids_reach(path: Path, names: Tuple[str, ...], needed: int) -> bool:
    """Whether a subuid/subgid file gives one of `names` (the account's name or number) a range of at least `needed`
    ids (an id N >= 1 in the container maps to the range's start + N - 1)."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        parts = line.strip().split(":")
        if len(parts) == 3 and parts[0] in names and parts[2].isdigit() and int(parts[2]) >= needed:
            return True
    return False


def _own_names() -> Tuple[str, ...]:
    """The person's account as the subuid/subgid files may name it: its number and its name (read here only, never
    shown)."""
    if not hasattr(os, "getuid"):
        return ()
    names: Tuple[str, ...] = (str(os.getuid()),)
    try:
        import pwd
        names += (pwd.getpwuid(os.getuid()).pw_name,)
    except (ImportError, KeyError):
        pass
    return names


def stage_user_support(client: Any, *, sandbox_on: bool = True, platform: Optional[str] = None,
                       subuid: Path = Path("/etc/subuid"), subgid: Path = Path("/etc/subgid")
                       ) -> Tuple[Optional[bool], str]:
    """Whether the build sandbox can run the stage modules under their own user on this computer: (True, text) it can,
    (False, text) it cannot and builds are refused, (None, text) unknown or the sandbox is off. Plain words without
    paths or account names (they reach an AI through the doctor). A rootless engine (Docker or Podman) maps the
    container's ids to the person's sub-ids, which must reach the stage account's ids; Docker and rootful Podman, and
    Docker Desktop's Linux VM on Windows and macOS, can always."""
    if not sandbox_on:
        return None, STAGE_USER_OFF
    if client is None:
        return None, STAGE_USER_UNKNOWN
    from orchestrator.utils.docker_engine import rootless_docker, rootless_podman
    platform = platform or sys.platform
    if platform.startswith("linux") and (rootless_docker(client) or rootless_podman(client)):
        names = _own_names()
        uid, gid = STAGE_ACCOUNT
        if not (_sub_ids_reach(subuid, names, uid) and _sub_ids_reach(subgid, names, gid)):
            return False, STAGE_USER_NO_SUBIDS.format(uid=uid)
    return True, STAGE_USER_OK


STAGE_RUNNER_OLD = ("This target's stage runner (modules/build.sh) does not start its stage modules under the stage "
                    "account: it was made before the build sandbox had one, so its modules would run as the runner "
                    "itself, with the runner's rights. The build sandbox refuses it. Take modules/build.sh from "
                    "targets/_template (or generate the target again), or switch the "
                    "setting build_sandbox off.")
# What a stage runner that carries the stage account has (targets/*/modules/build.sh): it sources the helper, runs its
# preflight and starts every module through run_as_stage
_RUNNER_MARKS = ('source "$MODULES_DIR/stage_user.sh"', "stage_preflight ",
                 'run_as_stage bash "$MODULES_DIR/${name}_module.sh"')
STAGE_RUNNER_REPLACES_HELPER = ("This target's stage runner (modules/build.sh) replaces a part of the framework's "
                                "stage account helper (modules/stage_user.sh): it defines {name} again or changes it, "
                                "so its modules could run as the runner itself, with the runner's rights. The build "
                                "sandbox refuses it. Take modules/build.sh from targets/_template (or generate the "
                                "target again), or switch the setting build_sandbox off.")
# The parts of the helper a runner must leave as they are (the old bash helper of the targets and the runtime layer's
# orchestrator/runtime_layer/stage_user.sh): every function it defines, setpriv (the switch itself), and every
# variable it sets or reads to decide the switch (tests/test_stage_user.py pins that these cover the layer helper's
# functions and variables)
_HELPER_FUNCTIONS = ("stage_isolated", "cap_list", "status_value", "hex_mask", "stage_rights_problem", "stage_groups",
                     "stage_shared_gid", "as_stage", "stage_share", "run_as_stage", "stage_switch_problem",
                     "stage_folders", "stage_refuse", "stage_preflight", "setpriv", "layer_sh", "refusal_exit", "run",
                     "stage_sh")
_HELPER_VARIABLES = ("STAGE_ISOLATION", "STAGE_UID", "STAGE_GID", "STAGE_RIGHTS_MASK", "CAP_NAMES",
                     "STAGE_REFUSAL_EXIT", "STAGE_SHARED_FOLDERS", "STAGE_SHARE", "STAGE_PROBE",
                     "STAGE_END_PROTECTION")


def _helper_part_replaced(text: str) -> Optional[str]:
    """The first part of the helper the runner's `text` defines again or changes, else None. Comment lines do not
    count. A hand-made check of a shell file: it catches a runner that overrides the helper by mistake or by habit
    (an older template, a copied snippet); it is no proof against a runner written to hide it."""
    code ="\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for name in _HELPER_FUNCTIONS:
        n = re.escape(name)
        if re.search(rf"(?m)(^|[;&|{{(\s])(function\s+{n}\b|{n}\s*\(\s*\))", code) or \
                re.search(rf"\b(unset|alias)\b[^\n;]*\b{n}\b", code):
            return name
    for name in _HELPER_VARIABLES:
        n = re.escape(name)
        if re.search(rf"(?<![\w$]){n}(\[[^\]\n]*\])?\+?=", code) or \
                re.search(rf"\b(unset|export|declare|typeset|local|readonly|read|mapfile|readarray)\b[^\n;]*"
                          rf"(?<![\w$]){n}\b", code):
            return name
    return None


def stage_runner_problem(modules: Path) -> Optional[str]:
    """Deprecated: always None. It refused a target's own stage runner (`modules`/build.sh) that did not start its
    modules under the stage account. A build no longer runs a target's runner at all: the framework's runtime layer
    (orchestrator/Core/runtime_layer.py) is the runner of every build container, and a target's own build.sh is
    ignored with a plain note (runtime_layer.own_runner_note, which the builder shows and audits). So there is nothing
    left to refuse here; the old check of the runner's text stays as runner_text_problem for whoever reads a runner."""
    import warnings
    warnings.warn("sandbox.stage_runner_problem is deprecated: a target's own build.sh never runs (the runtime layer's "
                  "runner does); see runtime_layer.own_runner_note", DeprecationWarning, stacklevel=2)
    return None


def runner_text_problem(modules: Path) -> Optional[str]:
    """STAGE_RUNNER_OLD or STAGE_RUNNER_REPLACES_HELPER for a target runner's text (`modules`/build.sh) that does not
    start its modules under the stage account or replaces a part of the helper, else None (also without a runner).
    Only a description of an old runner now: no build runs it (stage_runner_problem)."""
    runner = Path(modules) / "build.sh"
    if not runner.is_file():
        return None
    try:
        text = runner.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return STAGE_RUNNER_OLD
    if not all(mark in text for mark in _RUNNER_MARKS):
        return STAGE_RUNNER_OLD
    replaced = _helper_part_replaced(text)
    if replaced:
        return STAGE_RUNNER_REPLACES_HELPER.format(name=replaced)
    return None


def _cap_set(values: Any) -> set:
    return {str(v).strip().upper().replace("CAP_", "", 1) for v in values or [] if str(v).strip()}


def sandbox_problem(attrs: Any, options: Dict[str, Any], volumes: Dict[str, Dict[str, str]],
                    engine: str = "docker", caps: Optional[Tuple[str, ...]] = None) -> Optional[str]:
    """The host's check of a created stage container (docker or podman inspect, `attrs`) against the decided sandbox
    `options` (stage_user_sandbox) and `volumes`: None when it runs as decided, else the plain refusal naming what
    differs (SANDBOX_REFUSED). Checked: the user; exactly RUNNER_CAPS granted (Docker: CapDrop ALL and CapAdd those
    two; Podman lists capabilities relative to its defaults, or gives its effective set); no privileged mode;
    no-new-privileges; the read-only image; no network; every mount decided read-only is read-only. `caps`: the
    capabilities the container must have instead of RUNNER_CAPS (the clean-up container of an interrupted build: none,
    `()`, Core/interrupted_build.py)."""
    if not isinstance(attrs, dict) or not attrs:
        return SANDBOX_REFUSED.format(what="the engine did not describe the container")
    config = attrs.get("Config") or {}
    host = attrs.get("HostConfig") or {}
    wrong = []
    user, wanted = str(config.get("User") or ""), str(options.get("user") or "")
    if user.split(":")[0] != wanted.split(":")[0] or (":" in user and user != wanted):
        wrong.append(f"user {user or 'unset'} instead of {wanted}")
    needed = set(RUNNER_CAPS if caps is None else caps)
    drop, add = _cap_set(host.get("CapDrop")), _cap_set(host.get("CapAdd"))
    effective = attrs.get("EffectiveCaps")
    if isinstance(effective, list):
        granted = _cap_set(effective)
    elif "ALL" in drop:
        granted = add
    else:
        defaults = PODMAN_DEFAULT_CAPS if engine == "podman" else DOCKER_DEFAULT_CAPS
        granted = (set(defaults) - drop) | add
    if granted != needed:
        extra, missing = sorted(granted - needed), sorted(needed - granted)
        parts = ([f"extra {', '.join('CAP_' + c for c in extra)}"] if extra else []) + \
                ([f"missing {', '.join('CAP_' + c for c in missing)}"] if missing else [])
        wrong.append("capabilities " + "; ".join(parts))
    elif engine != "podman" and "ALL" not in drop and not isinstance(effective, list):
        wrong.append("capabilities not dropped (CapDrop ALL)")
    # The framework's runtime layer: the host sets the container's entrypoint and command itself
    # (Core/runtime_layer.py ENTRYPOINT); an image cannot have them replaced
    if options.get("entrypoint") is not None and \
            _argv(config.get("Entrypoint")) != _argv(options["entrypoint"]):
        wrong.append("the entrypoint is not the framework's runtime layer")
    if options.get("entrypoint") is not None and options.get("command") is not None and \
            _argv(config.get("Cmd")) != _argv(options["command"]):
        wrong.append("the command is not the framework's")
    if host.get("Privileged"):
        wrong.append("privileged mode")
    security = [str(s).replace("=", ":").lower() for s in host.get("SecurityOpt") or []]
    if not any(s in ("no-new-privileges", "no-new-privileges:true") for s in security):
        wrong.append("no-new-privileges missing")
    if options.get("read_only") and not host.get("ReadonlyRootfs"):
        wrong.append("the image is writable")
    if options.get("network_mode") == "none" and str(host.get("NetworkMode") or "") != "none":
        wrong.append("the container has a network")
    mounts = {str(m.get("Destination") or ""): m for m in attrs.get("Mounts") or [] if isinstance(m, dict)}
    for bind in sorted(v.get("bind", "") for v in (volumes or {}).values() if v.get("mode") == "ro"):
        mount = mounts.get(bind)
        if mount is None:
            wrong.append(f"the read-only mount {bind} is not listed")
        elif mount.get("RW", True):
            wrong.append(f"the mount {bind} is writable")
    # Nothing the host did not decide is mounted inside a read-only mount: an image's VOLUME below one (VOLUME
    # /llmcf/bin) makes the engine mount an anonymous volume there, filled from the image, over the decided files
    decided = {str(v.get("bind", "")) for v in (volumes or {}).values()}
    read_only = sorted(str(v.get("bind", "")) for v in (volumes or {}).values() if v.get("mode") == "ro")
    for destination in sorted(mounts):
        if destination in decided:
            continue
        inside = [b for b in read_only if b and destination.startswith(b.rstrip("/") + "/")]
        if inside:
            wrong.append(f"a mount the framework did not decide ({destination}) lies inside the read-only mount "
                         f"{inside[0]}")
    return SANDBOX_REFUSED.format(what="; ".join(wrong)) if wrong else None


def _argv(value: Any) -> List[str]:
    """An inspect's Entrypoint or Cmd as a list: Docker and Podman 5 give a list, Podman 4 one string joined by
    spaces (the framework's own words hold no spaces, so the split is exact for them)."""
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(v) for v in value]
