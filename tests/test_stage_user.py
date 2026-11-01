"""The stage modules run under their own user (the stage account), and the stage runner keeps only the rights that
switch needs (the framework's runtime layer: orchestrator/runtime_layer/runner.sh with its stage_user.sh, entrypoint.sh,
utils/sandbox.py).

A module that ran as the same user as the runner could read the runner's memory (/proc/<runner>/mem) wherever the host
kernel allows it (Yama ptrace_scope 0, or no Yama); dropped capabilities and seccomp do not stop it. Every stage module
is started through one helper that switches to the stage account (another uid and gid, no capabilities), so the kernel
refuses that read whatever Yama says; the runner checks at its start that it has exactly the rights the switch needs
and that a test child cannot read it, and refuses the build otherwise.

Changed on purpose with the runtime layer: the runner and its helper are the framework's own (a target's build.sh and
stage_user.sh are never run), and no key exists in the container (the end record lives in the runner's own end
folder). The tests below run the layer's runner; those that pinned the target's runner and its key now pin that the
target's runner is ignored and that the end folder stays the runner's.

The real tests run the stage runner as an unprivileged user with exactly CAP_SETUID and CAP_SETGID (setpriv, as the
layer's entrypoint does in the container); they need root on Linux to hand out those two rights and are skipped
elsewhere.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell

from test_review_build_engine import client, engine, make_engine  # noqa: F401 - fixtures

from orchestrator.Core import build_end

KEY = "0123456789abcdef0123456789abcdef"   # a marker the runner holds in its memory (its environment)
LAYER = REPO_ROOT / "orchestrator" / "runtime_layer"
RUNNER = 64020          # a made-up runner account for the tests (the container's runner is the person)
STAGE = 64010           # the image's stage account (modules/stage_user.sh STAGE_UID/STAGE_GID)
TARGETS = ("Rockchip", "_template")

needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason=NO_POSIX_BASH)


def _can_hand_out_rights() -> str:
    """'' when this test process can start a runner with exactly CAP_SETUID and CAP_SETGID, else why not."""
    if not sys.platform.startswith("linux"):
        return "the stage account needs Linux (setpriv and /proc)"
    if os.geteuid() != 0:
        return "handing the runner exactly CAP_SETUID and CAP_SETGID needs root on this computer"
    if shutil.which("setpriv") is None:
        return "setpriv (util-linux) is not installed"
    probe = subprocess.run(["setpriv", "--bounding-set=-all,+setuid,+setgid", "--reuid", str(RUNNER), "--regid",
                            str(RUNNER), "--clear-groups", "--inh-caps=-all,+setuid,+setgid",
                            "--ambient-caps=-all,+setuid,+setgid", "--no-new-privs", "--", "true"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return f"this system does not let setpriv hand out the two rights: {probe.stderr.strip()}"
    return ""


WHY_NOT = _can_hand_out_rights()
needs_rights = pytest.mark.skipif(bool(WHY_NOT), reason=WHY_NOT or "-")


def _may(st: os.stat_result, uid: int, gid: int, bits: int) -> bool:
    """Whether the mode of `st` gives an account uid:gid (without supplementary groups) the rwx `bits` (4/2/1)."""
    if st.st_uid == uid:
        return st.st_mode >> 6 & bits == bits
    if st.st_gid == gid:
        return st.st_mode >> 3 & bits == bits
    return st.st_mode & bits == bits


def _person_cannot_read_the_repo(uid: int = RUNNER, gid: int = RUNNER) -> str:
    """'' when the person of the tests (uid:gid, no groups) can import orchestrator.utils from this checkout and start
    this Python, else why not. Root ignores the modes, so they are read: a private home folder (0750 or 0700) above
    the checkout is no failure of the stage account, only of this computer."""
    if not sys.platform.startswith("linux"):
        return "the person's check needs Linux"
    for start in (REPO_ROOT, Path(os.path.realpath(sys.executable))):
        for parent in start.parents:
            try:
                if not _may(os.stat(parent), uid, gid, 1):
                    return (f"the test account {uid} cannot pass through a folder above the checkout or this Python "
                            f"({parent.name or '/'}, mode {oct(os.stat(parent).st_mode & 0o777)}): a private home")
            except OSError:
                return "a folder above the checkout cannot be read"
    for folder in (REPO_ROOT, REPO_ROOT / "orchestrator", REPO_ROOT / "orchestrator" / "utils"):
        if not _may(os.stat(folder), uid, gid, 5):
            return f"the test account {uid} cannot read the checkout's folder {folder.name}"
        for module in folder.glob("*.py") if folder != REPO_ROOT else ():
            if not _may(os.stat(module), uid, gid, 4):
                return f"the test account {uid} cannot read {module.name} of the checkout"
    return ""


WHY_NOT_PERSON = _person_cannot_read_the_repo()
person_reads_repo = pytest.mark.skipif(bool(WHY_NOT_PERSON), reason=WHY_NOT_PERSON or "-")

# A stage module that walks up its parents (the stage runner among them) and looks for a marker the runner holds in its
# memory. It writes "seen" or "refused" into the work folder.
ATTACK = r'''#!/bin/bash
/usr/bin/python3 - "$BUILD_SCRATCH_DIR/attack" <<'PY'
import os, re, sys
key = b"YEK"[::-1]  # written backwards: the module's own shell holds this text
result = "refused"
pid = os.getppid()
while pid > 1:
    try:
        with open(f"/proc/{pid}/maps") as maps, open(f"/proc/{pid}/mem", "rb", 0) as mem:
            for line in maps:
                m = re.match(r"([0-9a-f]+)-([0-9a-f]+) r", line)
                if not m:
                    continue
                start, end = int(m.group(1), 16), int(m.group(2), 16)
                try:
                    mem.seek(start)
                    if key in mem.read(end - start):
                        result = "seen"
                except (OSError, ValueError, OverflowError):
                    pass
    except OSError:
        pass
    try:
        pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        break
with open(sys.argv[1], "w") as out:
    out.write(result + "\n")
PY
'''.replace("YEK", KEY[::-1])

# A stage module that reports who it is and what it may do
WHOAMI = r'''#!/bin/bash
tamper() {  # every way a module could change, move or remove the runner's own files
    : > "$STAGE_DIR/running" 2>/dev/null && return 0
    mv -f "$STAGE_DIR/running" "$STAGE_DIR/moved" 2>/dev/null && return 0
    rm -f "$STAGE_DIR/running" 2>/dev/null; [ -e "$STAGE_DIR/running" ] || return 0
    mv "$BUILD_SCRATCH_DIR/stages" "$BUILD_SCRATCH_DIR/moved" 2>/dev/null && return 0
    return 1
}
{ id -u; id -g; grep -E '^Cap(Eff|Prm|Amb):' /proc/self/status | awk '{print $2}' | tr '\n' ' '; echo;
  if [ -n "${TAMPER:-}" ] && tamper 2>/dev/null; then echo "runner files open"; else echo "runner files closed"; fi
  if (: > "$BUILD_SCRATCH_DIR/intermediate") 2>/dev/null; then echo "work writable"; else echo "work closed"; fi
  if (: > "$BUILD_CACHE_DIR/output/result.gguf") 2>/dev/null; then echo "output writable"; else echo "output closed"; fi
} > "$BUILD_SCRATCH_DIR/whoami" 2>&1
'''


STAND_IN_BUSYBOX = """#!/bin/sh
a="$1"; shift
PATH=/usr/sbin:/usr/bin:/sbin:/bin
case "$a" in sh) exec /bin/sh "$@" ;; *) exec "$a" "$@" ;; esac
"""


class RunnerBox:
    """The framework's stage runner (the runtime layer's runner.sh with its stage_user.sh, bin/ with this computer's
    setpriv and a busybox stand-in) over a target's stage modules with stub modules, in folders the unprivileged runner
    account owns, and the runner's own end folder."""

    def __init__(self, target: str, modules: Path = None):
        self.root = Path(tempfile.mkdtemp(prefix="llmcf-stage-user-", dir="/tmp"))  # nosec B108 - test folder
        os.chmod(self.root, 0o755)
        self.layer = self.root / "llmcf"
        shutil.copytree(LAYER, self.layer, ignore=shutil.ignore_patterns("toolbox", "MANIFEST.sha256"))
        (self.layer / "bin").mkdir()
        (self.layer / "bin" / "busybox").write_text(STAND_IN_BUSYBOX, encoding="utf-8")
        shutil.copy2(shutil.which("setpriv") or "/usr/bin/setpriv", self.layer / "bin" / "setpriv")
        for path in [self.layer, *self.layer.rglob("*")]:
            os.chmod(path, 0o755)
        self.app = self.root / "app"
        shutil.copytree(modules or REPO_ROOT / "targets" / target / "modules", self.app / "modules")
        shutil.copytree(REPO_ROOT / "targets" / target / "scripts", self.app / "scripts")
        self.cache = self.root / "build-cache"
        self.work = self.cache / "work"
        self.output = self.cache / "output"
        for folder in (self.work / "tmp", self.work / "home", self.output):
            folder.mkdir(parents=True)
        self.stages = self.work / "stages" / "build"
        self.stages.mkdir(parents=True)
        self.end = self.root / "end-build"
        self.end.mkdir()
        for path in [self.cache, *self.cache.rglob("*"), self.end]:
            os.chown(path, RUNNER, RUNNER)
        os.chmod(self.end, 0o755)
        for path in self.app.rglob("*"):
            os.chmod(path, 0o755)

    def module(self, name: str, text: str) -> None:
        path = self.app / "modules" / f"{name}_module.sh"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o755)

    def run(self, *args: str, caps: str = "+setuid,+setgid", isolation: str = "user",
            **extra: str) -> subprocess.CompletedProcess:
        """The runner as the layer's entrypoint leaves it: the runner account with exactly `caps` (ambient) and
        no-new-privileges, on the layer's sh."""
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "BUILD_CACHE_DIR": str(self.cache),
               "BUILD_SCRATCH_DIR": str(self.work), "TMPDIR": str(self.work / "tmp"),
               "HOME": str(self.work / "home"), "JOB_TYPE": "build", "STAGE_ISOLATION": isolation,
               "LLMCF_MODULES_DIR": str(self.app / "modules"), "LLMCF_END_DIR": str(self.end),
               "LLMCF_TARGET_LABEL": KEY, **extra}
        bounding = "-all," + caps if caps else "-all"
        return subprocess.run(["setpriv", f"--bounding-set={bounding}", "--reuid", str(RUNNER), "--regid",
                               str(RUNNER), "--clear-groups", f"--inh-caps={bounding}", f"--ambient-caps={bounding}",
                               "--no-new-privs", "--", str(self.layer / "bin" / "busybox"), "sh",
                               str(self.layer / "runner.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def record(self):
        return build_end.read_end_record(self.end / build_end.END_RECORD)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture(params=TARGETS)
def box(request):
    built = RunnerBox(request.param)
    yield built
    built.remove()


# --- the regression: a module cannot read the runner's memory ------------------------------------------------------

@needs_rights
def test_a_stage_module_cannot_read_the_runners_memory(box):
    """Before, the module ran as the runner's own user and found the runner's secret (then: the end record's key) in
    the runner's memory (/proc/<pid>/mem) on a host without Yama; now it runs as the stage account and the kernel
    refuses the read. The runner's record lands in its own end folder, protected."""
    box.module("source", ATTACK)

    result = box.run("--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "attack").read_text().strip() == "refused", "a stage module read the runner's memory"
    record = box.record()
    assert record is not None and record.done and record.end == "protected"


@needs_rights
def test_a_stage_module_runs_as_the_stage_account_without_rights(box):
    box.module("source", "#!/bin/bash\nexport TAMPER=1\n" + WHOAMI.split("\n", 1)[1])

    result = box.run("--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    uid, gid, caps, stage_dir, work, output = (box.work / "whoami").read_text().splitlines()
    assert (int(uid), int(gid)) == (STAGE, STAGE)
    assert caps.split() == ["0000000000000000"] * 3
    assert stage_dir == "runner files closed", "a stage module could change, move or remove the runner's files"
    assert (work, output) == ("work writable", "output writable")
    assert "stage modules run under their own user" in result.stdout


# --- the runner's preflight on the real kernel ---------------------------------------------------------------------

@needs_rights
def test_a_runner_with_an_extra_right_refuses_the_build(box):
    box.module("source", WHOAMI)

    result = box.run("--to", "source", caps="+setuid,+setgid,+chown")

    assert result.returncode == build_end.STAGE_USER_EXIT["rights_extra"], result.stdout + result.stderr
    assert "CAP_CHOWN" in result.stderr
    assert not (box.work / "whoami").exists(), "a module ran although the runner had more rights than the switch"
    record = box.record()
    assert record is not None and record.status == "failed" and record.stage == "preflight"


@needs_rights
def test_a_runner_without_the_switch_rights_refuses_and_never_runs_a_module_as_itself(box):
    box.module("source", WHOAMI)

    result = box.run("--to", "source", caps="")

    assert result.returncode == build_end.STAGE_USER_EXIT["rights_missing"], result.stdout + result.stderr
    assert "CAP_SETUID" in result.stderr
    assert not (box.work / "whoami").exists(), "the runner fell back to running the module as itself"


@needs_rights
def test_by_hand_the_runner_still_runs_the_modules_itself(box):
    """Without the sandbox's request (a run started by hand, the legacy mode) nothing is switched."""
    box.module("source", WHOAMI)

    result = box.run("--to", "source", isolation="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "whoami").read_text().splitlines()[0] == str(RUNNER)


# --- the preflight's reading of the runner's rights (faked /proc/<pid>/status) -------------------------------------

def _status(eff="00000000000000c0", prm="00000000000000c0", bnd="00000000000000c0", inh="00000000000000c0",
            amb="00000000000000c0", uid=1000, gid=1000, nnp=1) -> str:
    return (f"Name:\tbash\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nGid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
            f"CapInh:\t{inh}\nCapPrm:\t{prm}\nCapEff:\t{eff}\nCapBnd:\t{bnd}\nCapAmb:\t{amb}\nNoNewPrivs:\t{nnp}\n")


def _rights_problem(tmp_path, text: str, target: str = "Rockchip") -> str:
    status = tmp_path / "status"
    status.write_text(text, encoding="ascii")
    # the framework's helper (the runtime layer's), which the target's deprecated stage_user.sh only sources
    helper = LAYER / "stage_user.sh" if target == "layer" else REPO_ROOT / "targets" / target / "modules" / "stage_user.sh"
    return subprocess.run([posix_shell("bash"), "-c", 'source "$1"; stage_rights_problem "$2"', "_", str(helper),
                           str(status)], capture_output=True, text=True, timeout=60, check=True).stdout.strip()


@needs_bash
@pytest.mark.parametrize("target", TARGETS + ("layer",))
def test_exactly_the_two_rights_pass_the_preflight(tmp_path, target):
    assert _rights_problem(tmp_path, _status(), target) == ""
    # a runner that stays root (a rootless engine): no ambient or inheritable set, the same two rights
    assert _rights_problem(tmp_path, _status(uid=0, gid=0, inh="0" * 16, amb="0" * 16), target) == ""


@needs_bash
@pytest.mark.parametrize("fields, reason, named", [
    (dict(eff="0000000000000040", prm="0000000000000040"), "rights_missing", "CAP_SETUID"),
    (dict(eff="0" * 16, prm="0" * 16, amb="0" * 16, inh="0" * 16), "rights_missing", "CAP_SETGID"),
    (dict(eff="00000000000000c1", prm="00000000000000c1"), "rights_extra", "CAP_CHOWN"),
    (dict(bnd="000001ffffffffff"), "rights_extra", "CAP_SYS_ADMIN"),
    (dict(amb="00000000000800c0"), "rights_extra", "CAP_SYS_PTRACE"),
    (dict(nnp=0), "no_new_privileges", "no-new-privileges"),
    (dict(uid=64010), "same_account", "64010"),
])
def test_missing_or_extra_rights_refuse_the_build(tmp_path, fields, reason, named):
    problem = _rights_problem(tmp_path, _status(**fields))
    assert problem.split(" ", 1)[0] == reason and named in problem
    assert reason in build_end.STAGE_USER_EXIT


@needs_bash
def test_the_test_child_must_be_the_stage_account_without_rights_and_blind_to_the_runner():
    helper = LAYER / "stage_user.sh"

    def verdict(lines):
        return subprocess.run([posix_shell("bash"), "-c", 'source "$1"; stage_switch_problem "$2"', "_", str(helper),
                               "\n".join(lines)], capture_output=True, text=True, timeout=60).stdout.split(" ", 1)[0]
    # the layer's test child also answers whether it could write the end folder (end-refused: protected)
    good = ["64010", "64010", "0" * 16, "0" * 16, "0" * 16, "0" * 16, "mem-refused", "environ-refused", "end-refused"]
    assert verdict(good) == ""
    assert verdict(["1000"] + good[1:]) == "switch_failed"
    assert verdict([]) == "switch_failed"
    assert verdict(good[:2] + ["00000000000000c0"] + good[3:]) == "stage_has_rights"
    assert verdict(good[:6] + ["mem-open", "environ-refused", "end-refused"]) == "reads_runner"
    assert verdict(good[:7] + ["environ-open", "end-refused"]) == "reads_runner"
    assert verdict(good + ["read-fail input-model"]) == "inputs"
    assert verdict(good + ["write-fail home"]) == "folders"


def test_both_targets_carry_the_same_stage_account_helper_and_the_images_create_its_account():
    """Changed on purpose (the runtime layer): the helper is the framework's (orchestrator/runtime_layer/
    stage_user.sh); both targets keep only the same deprecated stage_user.sh that sources it. Its account, its fixed
    vocabulary of refusals and the one way a module starts are the layer's; the images still create the account."""
    rockchip = (REPO_ROOT / "targets" / "Rockchip" / "modules" / "stage_user.sh").read_text(encoding="utf-8")
    template = (REPO_ROOT / "targets" / "_template" / "modules" / "stage_user.sh").read_text(encoding="utf-8")
    assert rockchip == template and "DEPRECATED" in rockchip and "run_as_stage()" not in rockchip
    helper = (LAYER / "stage_user.sh").read_text(encoding="utf-8")
    from orchestrator.utils.sandbox import STAGE_ACCOUNT
    assert f"STAGE_UID={STAGE_ACCOUNT[0]}" in helper and f"STAGE_GID={STAGE_ACCOUNT[1]}" in helper
    for name, code in build_end.STAGE_USER_EXIT.items():
        assert f"        {name}) echo {code} ;;\n" in helper
    for dockerfile in ("targets/Rockchip/dockerfile", "targets/_template/Dockerfile", "targets/_template/Dockerfile.gpu"):
        text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
        assert f"groupadd -g {STAGE_ACCOUNT[1]} llmstage" in text and f"-u {STAGE_ACCOUNT[0]} -g llmstage" in text
        assert "command -v setpriv" in text
    runner = (LAYER / "runner.sh").read_text(encoding="utf-8")
    assert "run_as_stage " in runner and runner.count("_module.sh\"") >= 1
    assert "run_module \"$name\" || rc=$?" in runner   # the one way a module starts


# --- the host's check of the created container (docker / podman inspect) -------------------------------------------

def _decided():
    from orchestrator.utils.sandbox import stage_user_sandbox
    options, env = stage_user_sandbox((1000, 1000), working_dir="/build-cache/work")
    volumes = {"/srv/work": {"bind": "/build-cache/work", "mode": "rw"},
               "/srv/model": {"bind": "/build-cache/input-model", "mode": "ro"}}
    return options, volumes, env


def _docker_inspect(**changes):
    attrs = {"Config": {"User": "0:0"},
             "HostConfig": {"CapAdd": ["CAP_SETUID", "CAP_SETGID"], "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True, "Privileged": False,
                            "NetworkMode": "none"},
             "Mounts": [{"Destination": "/build-cache/work", "RW": True},
                        {"Destination": "/build-cache/input-model", "RW": False}]}
    for key, value in changes.items():
        part, _, name = key.partition("__")
        if name:
            attrs[part][name] = value
        else:
            attrs[part] = value
    return attrs


def test_a_container_as_decided_passes_the_host_check():
    from orchestrator.utils.sandbox import sandbox_problem
    options, volumes, _ = _decided()
    assert sandbox_problem(_docker_inspect(), options, volumes, "docker") is None
    assert sandbox_problem(_docker_inspect(HostConfig__CapAdd=["SETGID", "SETUID"]), options, volumes) is None


@pytest.mark.parametrize("changes, named", [
    (dict(Config={"User": "1000:1000"}), "user 1000:1000"),
    (dict(HostConfig__CapAdd=["SETUID", "SETGID", "SYS_PTRACE"]), "extra CAP_SYS_PTRACE"),
    (dict(HostConfig__CapAdd=["SETUID"]), "missing CAP_SETGID"),
    (dict(HostConfig__CapDrop=None, HostConfig__CapAdd=None), "CAP_CHOWN"),
    (dict(HostConfig__SecurityOpt=None), "no-new-privileges missing"),
    (dict(HostConfig__ReadonlyRootfs=False), "the image is writable"),
    (dict(HostConfig__Privileged=True), "privileged mode"),
    (dict(HostConfig__NetworkMode="bridge"), "has a network"),
    (dict(Mounts=[{"Destination": "/build-cache/work", "RW": True},
                  {"Destination": "/build-cache/input-model", "RW": True}]), "/build-cache/input-model is writable"),
    (dict(Mounts=[{"Destination": "/build-cache/work", "RW": True}]), "is not listed"),
])
def test_a_container_that_differs_is_refused_with_what_differs(changes, named):
    from orchestrator.utils.sandbox import SANDBOX_REFUSED, sandbox_problem
    options, volumes, _ = _decided()
    problem = sandbox_problem(_docker_inspect(**changes), options, volumes, "docker")
    assert problem and named in problem and problem.startswith(SANDBOX_REFUSED.split("{")[0])
    assert sandbox_problem({}, options, volumes) is not None


def test_podmans_inspect_relative_to_its_defaults_and_its_effective_set():
    """Podman's Docker-compatible inspect lists the capabilities relative to its default set (CapAdd empty, CapDrop
    the defaults it dropped), or gives the effective set; both read as exactly CAP_SETUID and CAP_SETGID."""
    from orchestrator.utils.sandbox import PODMAN_DEFAULT_CAPS, sandbox_problem
    options, volumes, _ = _decided()
    dropped = sorted("CAP_" + c for c in PODMAN_DEFAULT_CAPS - {"SETUID", "SETGID"})
    relative = _docker_inspect(HostConfig__CapAdd=[], HostConfig__CapDrop=dropped,
                               HostConfig__SecurityOpt=["no-new-privileges"], Config={"User": "0"})
    assert sandbox_problem(relative, options, volumes, "podman") is None
    effective = dict(_docker_inspect(HostConfig__CapAdd=[], HostConfig__CapDrop=[]),
                     EffectiveCaps=["CAP_SETGID", "CAP_SETUID"])
    assert sandbox_problem(effective, options, volumes, "podman") is None
    too_many = _docker_inspect(HostConfig__CapAdd=[], HostConfig__CapDrop=dropped[1:])
    assert "extra CAP_" in sandbox_problem(too_many, options, volumes, "podman")


def test_the_build_engine_never_starts_a_container_that_differs(engine, client, tmp_path):
    import test_review_build_engine as fakes
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    options, volumes, env = _decided()
    container = fakes.FakeContainer(client, "llm-build-b1", volumes=volumes)
    container.attrs = _docker_inspect(HostConfig__CapAdd=["SETUID", "SETGID", "DAC_OVERRIDE"])
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    with pytest.raises(RuntimeError, match="extra CAP_DAC_OVERRIDE"):
        engine._run_container(fakes.config_for(tmp_path), progress, container, "CONT: ",
                              decided=(options, volumes, env))
    assert ("llm-build-b1", "start-requested") not in client.events and container.removed
    assert progress.isolation == "stage_user" and any("their own user" in line for line in progress.logs)


def test_the_legacy_mode_says_that_the_modules_run_as_the_runner(engine, client, tmp_path):
    import test_review_build_engine as fakes
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    container = fakes.FakeContainer(client, "llm-build-b1")
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._run_container(fakes.config_for(tmp_path), progress, container, "CONT: ",
                          decided=({"user": "0:0"}, {}, {"BUILD_SCRATCH_DIR": "/build-cache/work"}))
    assert progress.isolation == "same_user"
    assert any("Legacy mode" in line and "own user" in line for line in progress.logs)


def test_a_refusal_of_the_preflight_reads_as_its_plain_reason():
    record = build_end.EndRecord("failed", build_end.STAGE_USER_EXIT["rights_extra"], build_end.PREFLIGHT_STAGE)
    text = build_end.stage_user_problem(record, build_end.STAGE_USER_EXIT["rights_extra"])
    assert text and build_end.STAGE_USER_TEXT["rights_extra"] in text and "never run as the stage runner" in text
    assert build_end.stage_user_problem(record, 1) is None
    assert build_end.stage_user_problem(build_end.EndRecord("failed", 81, "convert"), 81) is None


def test_the_end_folder_is_the_runners_only(tmp_path):
    """Changed on purpose (the runtime layer): there is no key file any more; what the key protected, the end record,
    lives in the runner's own end folder, which nobody else may write (the stage account shares the runner's group, so
    the group gets no write either)."""
    from orchestrator.Core import runtime_layer
    folder = runtime_layer.end_folder(tmp_path / "builds" / "b1", "build")
    runtime_layer.prepare_end_folder(folder, None)
    assert folder.is_dir() and not list(folder.iterdir())
    if os.name != "nt":
        assert folder.stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / "builds" / "b1" / "work" / "stages" / "build" / build_end.END_KEY).exists()


# --- rootless engines and the doctor -------------------------------------------------------------------------------

class _Engine:
    def __init__(self, name="docker", rootless=False):
        self.name, self.rootless = name, rootless

    def version(self):
        return {"Components": [{"Name": "Podman Engine" if self.name == "podman" else "Engine"}]}

    def info(self):
        return {"SecurityOptions": ["name=rootless"] if self.rootless else ["name=seccomp,profile=default"]}


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="sub-ids are a POSIX thing")
@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_a_rootless_engine_needs_sub_ids_that_reach_the_stage_account(tmp_path, engine):
    from orchestrator.utils import sandbox
    enough, short = tmp_path / "enough", tmp_path / "short"
    enough.write_text(f"{os.getuid()}:100000:65536\n", encoding="ascii")
    short.write_text(f"{os.getuid()}:100000:1000\n", encoding="ascii")
    client = _Engine(engine, rootless=True)
    assert sandbox.stage_user_support(client, platform="linux", subuid=enough, subgid=enough)[0] is True
    ok, text = sandbox.stage_user_support(client, platform="linux", subuid=enough, subgid=short)
    assert ok is False and "sub-ids" in text and str(tmp_path) not in text
    assert sandbox.runner_ids(client) == (0, 0)
    assert sandbox.stage_user_support(_Engine(engine), platform="linux", subuid=short, subgid=short)[0] is True
    assert sandbox.stage_user_support(client, sandbox_on=False)[0] is None


def test_the_doctor_tells_whether_the_stage_account_can_run_here(monkeypatch):
    import types
    from orchestrator.Core.mojo.checks import docker as docker_check
    from orchestrator.utils import sandbox

    def findings(on, verdict):
        monkeypatch.setattr(sandbox, "stage_user_support", lambda client, sandbox_on=True: verdict(sandbox_on))
        framework = types.SimpleNamespace(config=types.SimpleNamespace(get=lambda key, default=None: on))
        return docker_check._stage_user_findings(framework, object())

    ok = findings(True, lambda on: (True, sandbox.STAGE_USER_OK))[0]
    assert (ok.code, ok.severity.value) == ("docker.stage_user", "ok")
    no = findings(True, lambda on: (False, sandbox.STAGE_USER_NO_SUBIDS.format(uid=64010)))[0]
    assert no.severity.value == "error" and no.fix_hint == sandbox.STAGE_USER_NO_SUBIDS_WAY and no.why
    off = findings(False, lambda on: (None, sandbox.STAGE_USER_OFF))[0]
    assert off.severity.value == "warn" and "build_sandbox" in off.fix_hint
    for finding in (ok, no, off):
        assert "/" not in finding.message   # plain words: no path


# --- the entrypoint hands the runner exactly the two rights ---------------------------------------------------------

@needs_rights
def test_the_entrypoint_makes_the_runner_the_person_with_only_the_two_rights():
    """The runtime layer's entrypoint (the host sets it as the container's entrypoint): started as root with only the
    two rights and no-new-privileges, it starts the runner as STAGE_RUNNER_IDS with exactly those two rights. Changed
    on purpose: before, the image's entrypoint.sh did this with the image's setpriv; now it only hands over to the
    layer's entrypoint and refuses without the layer."""
    built = RunnerBox("Rockchip")
    try:
        runner = built.layer / "runner.sh"
        os.chmod(runner, 0o644)
        runner.write_text('grep -E "^(Uid|Gid|CapEff|CapPrm|CapAmb|NoNewPrivs):" /proc/self/status\n', encoding="utf-8")
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "STAGE_ISOLATION": "user",
               "STAGE_RUNNER_IDS": f"{RUNNER}:{RUNNER}"}
        start = ["setpriv", "--bounding-set=-all,+setuid,+setgid", "--inh-caps=-all", "--no-new-privs", "--",
                 str(built.layer / "bin" / "busybox"), "sh", str(built.layer / "entrypoint.sh")]
        result = subprocess.run(start, env=env, capture_output=True, text=True, timeout=60, cwd="/")
        assert result.returncode == 0, result.stdout + result.stderr
        status = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line and line[0].isupper())
        assert status["Uid"].split()[0] == str(RUNNER) and status["Gid"].split()[0] == str(RUNNER)
        assert {status[k].strip() for k in ("CapEff", "CapPrm", "CapAmb")} == {"00000000000000c0"}
        assert status["NoNewPrivs"].strip() == "1"

        bad = subprocess.run(start, capture_output=True, text=True, timeout=60, cwd="/",
                             env=dict(env, STAGE_RUNNER_IDS="nobody"))
        assert bad.returncode == build_end.STAGE_USER_EXIT["switch_failed"]
    finally:
        built.remove()
    # the image's own entrypoint never switches users itself any more: without the layer it refuses
    alone = subprocess.run(["bash", str(REPO_ROOT / "entrypoint.sh"), "true"], capture_output=True, text=True,
                           timeout=60, cwd="/", env=dict(env, LLAMA_CPP_PATH="/nonexistent"))
    if not os.path.exists("/llmcf/entrypoint.sh"):
        assert alone.returncode == build_end.STAGE_USER_EXIT["no_layer"], alone.stdout + alone.stderr


@needs_rights
def test_an_input_the_stage_account_cannot_read_refuses_the_build_before_any_module(box):
    """The model (or dataset) is read by the stage account through the runner's group: a file only its owner may read
    refuses the build with the reason inputs, instead of a module failing somewhere in the conversion."""
    box.module("source", WHOAMI)
    model = box.root / "input-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="ascii")
    (model / "weights.bin").write_bytes(b"0")
    for path in (model, model / "config.json", model / "weights.bin"):
        os.chown(path, RUNNER, RUNNER)
    os.chmod(model / "weights.bin", 0o600)

    refused = box.run("--to", "source", MODEL_SOURCE=str(model))

    assert refused.returncode == build_end.STAGE_USER_EXIT["inputs"], refused.stdout + refused.stderr
    assert "cannot read input-model" in refused.stderr and str(box.root) not in refused.stderr.split("read ")[-1]
    assert not (box.work / "whoami").exists()

    os.chmod(model / "weights.bin", 0o640)
    assert box.run("--to", "source", MODEL_SOURCE=str(model)).returncode == 0


@needs_rights
def test_a_folder_the_stage_account_cannot_write_refuses_the_build(box):
    box.module("source", WHOAMI)
    os.chown(box.output, 0, 0)          # not the runner's: its mode stays, and the stage account cannot write it
    os.chmod(box.output, 0o775)

    refused = box.run("--to", "source")

    # the runner itself cannot write it either: it is not one of the folders the modules write, nothing is refused
    assert refused.returncode == 0, refused.stdout + refused.stderr
    os.chmod(box.output, 0o777)
    # owned by another account: the runner writes it through its group, the stage account as its owner may not
    os.chown(box.work / "home", STAGE, RUNNER)
    os.chmod(box.work / "home", 0o070)

    refused = box.run("--to", "source")

    assert refused.returncode == build_end.STAGE_USER_EXIT["folders"], refused.stdout + refused.stderr
    assert "cannot write the folder home" in refused.stderr


# --- a target's own stage runner (review cs-stage-user-v, then the runtime layer) -----------------------------------
#
# Before the runtime layer, the host ran the target's modules/build.sh as the runner, with CAP_SETUID and CAP_SETGID as
# AMBIENT capabilities: a runner that started its modules without run_as_stage ran them as itself WITH those rights,
# so such a runner was refused before its container was made. Changed on purpose: a target's own runner is never run
# at all (the layer's runner is the runner of every build); the build goes on with a plain note, shown and audited
# (runtime_layer.own_runner_note), and the old check of a runner's text stays as a description (runner_text_problem).

OLD_RUNNER = '''#!/bin/bash
set -euo pipefail
MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for name in source config convert target; do
    STAGE_NAME="$name" bash "$MODULES_DIR/${name}_module.sh"
done
'''
# A runner of the shape the targets shipped before the runtime layer: it sources the helper, runs its preflight and
# starts every module through run_as_stage
MARKED_RUNNER = '''#!/bin/bash
set -euo pipefail
MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$MODULES_DIR/stage_user.sh"
stage_preflight "$BUILD_SCRATCH_DIR"
for name in source config convert target; do
    run_as_stage bash "$MODULES_DIR/${name}_module.sh" || exit $?
done
'''


def _target_with_runner(root: Path, runner: str, helper: bool = True) -> Path:
    modules = root / "modules"
    modules.mkdir(parents=True)
    (modules / "build.sh").write_text(runner, encoding="utf-8")
    if helper:
        shutil.copy2(REPO_ROOT / "targets" / "_template" / "modules" / "stage_user.sh", modules / "stage_user.sh")
    return root


@pytest.mark.parametrize("helper", [True, False])
def test_a_targets_own_runner_is_ignored_with_a_note_and_never_run(engine, tmp_path, helper):
    from test_pipeline import make_config
    from orchestrator.Core import runtime_layer
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    from orchestrator.utils.sandbox import STAGE_RUNNER_OLD, runner_text_problem, stage_runner_problem
    target = _target_with_runner(tmp_path / "old", OLD_RUNNER, helper)
    assert runner_text_problem(target / "modules") == STAGE_RUNNER_OLD   # the old verdict, a description only
    with pytest.warns(DeprecationWarning):
        assert stage_runner_problem(target / "modules") is None
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    kwargs, env, _ = engine._sandbox(make_config(), tmp_path / "builds" / "b1", target_path=target, job="build",
                                     progress=progress)
    assert kwargs["entrypoint"] == runtime_layer.ENTRYPOINT and kwargs["command"] == ["--job", "build"]
    assert env["LLMCF_STAGES"] == "source config convert target"
    note = runtime_layer.OWN_RUNNER_NOTE.format(names="modules/build.sh" + (", modules/stage_user.sh" if helper
                                                                             else ""))
    assert note in progress.warnings and str(tmp_path) not in note   # no host path
    # the legacy mode: the framework's runner too, on the layer's own sh
    engine.config.set("build_sandbox", False)
    assert engine._sandbox(make_config(), tmp_path / "builds" / "b2", target_path=target)[0]["command"][:3] == \
        ["/llmcf/bin/busybox", "sh", "/llmcf/runner.sh"]


def test_the_shipped_targets_get_no_note_and_the_layer_runs_them(engine, tmp_path):
    from test_pipeline import make_config
    from orchestrator.Core import runtime_layer
    for target in TARGETS:
        assert runtime_layer.own_runner_note(REPO_ROOT / "targets" / target) is None
        kwargs, env, _ = engine._sandbox(make_config(), tmp_path / "builds" / target,
                                         target_path=REPO_ROOT / "targets" / target)
        assert kwargs["user"] == "0:0" and env["STAGE_ISOLATION"] == "user"
        assert kwargs["entrypoint"] == runtime_layer.ENTRYPOINT
    # a target the module generator makes from the template carries no runner of its own at all
    from orchestrator.Core.module_generator import ModuleGenerator
    from test_ai_safety import TestModuleGenerator
    (tmp_path / "gen" / "Rockchip").mkdir(parents=True)
    generator = ModuleGenerator(tmp_path / "gen")
    generator.template_dir = REPO_ROOT / "targets" / "_template"
    generated = generator.generate_module(TestModuleGenerator().data())
    assert not (generated / "modules" / "build.sh").exists() and runtime_layer.own_runner_note(generated) is None


def test_both_conversion_containers_get_the_runtime_layer():
    import inspect as _inspect
    from orchestrator.Core.builder import BuildEngine
    for stage, job in ((BuildEngine._generate_imatrix, "imatrix"), (BuildEngine._execute_build_modules, "build")):
        source = _inspect.getsource(stage)
        assert f'self._sandbox(config, build_temp, target_path=target_path, job="{job}"' in source
        assert f'end_record=end_folder(build_temp, "{job}") / END_RECORD' in source


# --- the framework's own helper in every build context (round 44 item 1) -------------------------------------------
#
# Before, the build context carried the target's modules/stage_user.sh: a target whose stage runner was the template's
# but whose helper was hollow (run_as_stage running the module as it is, a preflight that checks nothing) passed
# stage_runner_problem, and its modules ran as the runner with the runner's two ambient rights. Now every build context
# carries the framework's helper (utils/build_context.FRAMEWORK_STAGE_HELPER), whatever the target ships.

HOLLOW_HELPER = '''#!/bin/bash
stage_isolated() { [ "${STAGE_ISOLATION:-}" = "user" ]; }
run_as_stage() { "$@"; }
stage_preflight() { :; }
'''


def _app_folder(root: Path) -> Path:
    app = root / "app"
    app.mkdir(exist_ok=True)
    (app / "entrypoint.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return app


def _target_with_hollow_helper(root: Path, helper=HOLLOW_HELPER) -> Path:
    target = root / "targets" / "Hollow"
    shutil.copytree(REPO_ROOT / "targets" / "_template" / "modules", target / "modules")
    shutil.copytree(REPO_ROOT / "targets" / "_template" / "scripts", target / "scripts")
    shutil.copy2(REPO_ROOT / "targets" / "_template" / "Dockerfile", target / "Dockerfile")
    helper_path = target / "modules" / "stage_user.sh"
    helper_path.unlink()
    if helper == "folder":
        helper_path.mkdir()
        (helper_path / "run_as_stage").write_text("hollow\n", encoding="utf-8")
    elif helper is not None:
        helper_path.write_text(helper, encoding="utf-8")
    return target


@pytest.mark.parametrize("helper", [HOLLOW_HELPER, None, "folder"], ids=["hollow", "missing", "folder"])
def test_every_build_context_carries_the_frameworks_stage_helper(tmp_path, helper):
    from orchestrator.utils.build_context import FRAMEWORK_STAGE_HELPER, stage_build_context
    from orchestrator.utils.sandbox import stage_runner_problem
    target = _target_with_hollow_helper(tmp_path, helper)

    dockerfile = stage_build_context(target, _app_folder(tmp_path), tmp_path / "ctx")

    staged = dockerfile.parent / "modules" / "stage_user.sh"
    assert staged.is_file()
    assert staged.read_bytes() == FRAMEWORK_STAGE_HELPER.read_bytes(), "the target's helper reached the build"
    # and no build reads it anyway: the layer's runner sources the layer's own helper (the target's runner files only
    # earn the plain note)
    with pytest.warns(DeprecationWarning):
        assert stage_runner_problem(target / "modules") is None


def test_the_framework_helper_is_the_layers_and_both_shipped_targets_only_source_it():
    """Changed on purpose (the runtime layer): the one helper is the layer's; the targets keep a deprecated file that
    sources it."""
    from orchestrator.utils.build_context import FRAMEWORK_STAGE_HELPER
    assert FRAMEWORK_STAGE_HELPER == LAYER / "stage_user.sh"
    for target in TARGETS:
        text = (REPO_ROOT / "targets" / target / "modules" / "stage_user.sh").read_text(encoding="utf-8")
        assert "DEPRECATED" in text and '/stage_user.sh"' in text


def test_the_image_inputs_follow_the_frameworks_helper_not_the_targets(tmp_path):
    """The build container image's inputs label covers the staged context: a target's own helper never changes it."""
    from types import SimpleNamespace
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.utils.build_context import stage_build_context
    app = _app_folder(tmp_path)
    config = SimpleNamespace(target_arch="Hollow", target_pack_sha256="")
    labels = []
    for n, helper in enumerate((HOLLOW_HELPER, None)):
        target = _target_with_hollow_helper(tmp_path / str(n), helper)
        dockerfile = stage_build_context(target, app, tmp_path / f"ctx{n}")
        labels.append(BuildEngine._image_inputs(config, dockerfile, {}))
    assert labels[0] == labels[1]


@needs_rights
def test_a_target_with_a_hollow_helper_still_runs_its_modules_as_the_stage_account(tmp_path):
    from orchestrator.utils.build_context import stage_build_context
    target = _target_with_hollow_helper(tmp_path)
    context = stage_build_context(target, _app_folder(tmp_path), tmp_path / "ctx").parent
    built = RunnerBox("_template", modules=context / "modules")
    try:
        built.module("source", WHOAMI)
        result = built.run("--to", "source")
        assert result.returncode == 0, result.stdout + result.stderr
        uid, gid, caps = (built.work / "whoami").read_text().splitlines()[:3]
        assert (int(uid), int(gid)) == (STAGE, STAGE), "the target's hollow helper ran the module as the runner"
        assert caps.split() == ["0000000000000000"] * 3
        assert "stage modules run under their own user" in result.stdout
    finally:
        built.remove()


def test_the_runner_check_judges_the_runner_not_the_targets_helper(tmp_path):
    """runner_text_problem (the description of a runner, formerly stage_runner_problem) judges only the target's
    runner: a runner of the old shape without a helper of its own passes, one that starts its modules another way
    does not."""
    from orchestrator.utils.sandbox import STAGE_RUNNER_OLD, runner_text_problem
    assert runner_text_problem(_target_with_runner(tmp_path / "new", MARKED_RUNNER, helper=False) / "modules") is None
    assert runner_text_problem(_target_with_runner(tmp_path / "old", OLD_RUNNER, helper=False) / "modules") == \
        STAGE_RUNNER_OLD


# --- one shared group for the runner and the stage account (round 44 item 2) ---------------------------------------
#
# Before, what the stage account wrote kept the mode its tools chose: a tool that writes a 0600 file or a 0555 folder
# left files the runner and the person (another uid) could not read and folders they could not clear. Now the stage
# account gives its own files in the folders the modules write to the runner's group (the person's outside the
# container), readable and writable for it and its folders setgid, after every module, also a failed one.

TIGHT = r'''#!/bin/bash
out="$BUILD_CACHE_DIR/output"
umask 077
printf 'weights' > "$out/model.bin"
mkdir -p "$out/locked/inner"
printf 'x' > "$out/locked/inner/part.bin"
chmod 0600 "$out/locked/inner/part.bin"
chmod 0555 "$out/locked/inner" "$out/locked"
mkdir -p "$BUILD_SCRATCH_DIR/cache/closed"
printf 'c' > "$BUILD_SCRATCH_DIR/cache/closed/entry"
chmod 0000 "$BUILD_SCRATCH_DIR/cache/closed"
mkdir "$out/plain"
chmod 0755 "$out/plain"
ln -s "$LLMCF_END_DIR/build.end" "$out/key-link"
exit "${TIGHT_EXIT:-0}"
'''

PERSON_CHECK = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from orchestrator.utils.atomic import remove_tree
out, cache = sys.argv[2], sys.argv[3]
with open(os.path.join(out, "model.bin")) as f:
    assert f.read() == "weights"
with open(os.path.join(out, "locked", "inner", "part.bin")) as f:
    assert f.read() == "x"
left = remove_tree(os.path.join(out, "locked")) + remove_tree(os.path.join(cache, "closed"))
print("left", len(left))
'''


@needs_rights
@person_reads_repo
@pytest.mark.parametrize("exit_code", [0, 3])
def test_what_a_module_writes_the_person_can_read_and_remove(box, exit_code):
    box.module("source", TIGHT)
    key = box.end / build_end.END_RECORD

    result = box.run("--to", "source", TIGHT_EXIT=str(exit_code))

    assert result.returncode == exit_code, result.stdout + result.stderr
    out = box.output
    for path in (out / "model.bin", out / "locked", out / "locked" / "inner", out / "locked" / "inner" / "part.bin",
                 out / "plain", box.work / "cache" / "closed"):
        st = os.lstat(path)
        assert st.st_uid == STAGE, path.name
        assert st.st_gid == RUNNER, f"{path.name} is not in the shared group"
        assert st.st_mode & 0o060 == 0o060, f"{path.name} is not readable and writable for the shared group"
        if path.is_dir():
            assert st.st_mode & 0o2000, f"{path.name} is not setgid"
    # the share step runs as the stage account and never follows a link: the runner's end record stays the runner's
    assert os.path.islink(out / "key-link")
    if key.exists():
        assert os.stat(key).st_uid == RUNNER and not os.stat(key).st_mode & 0o022
    for marker in box.stages.iterdir():
        if not marker.name.endswith(".outputs"):
            assert os.stat(marker).st_uid == RUNNER, marker.name
    # the person: another uid than the stage account, with the shared group only
    person = subprocess.run(["setpriv", "--reuid", str(RUNNER), "--regid", str(RUNNER), "--clear-groups", "--",
                             sys.executable, "-I", "-c", PERSON_CHECK, str(REPO_ROOT), str(out),
                             str(box.work / "cache")], capture_output=True, text=True, timeout=120)
    assert person.returncode == 0, person.stdout + person.stderr
    assert person.stdout.strip() == "left 0"
    assert not (out / "locked").exists() and not (box.work / "cache" / "closed").exists()


# --- review: a runner that replaces the helper's parts, a sticky folder a module made ------------------------------
#
# The framework's helper in the context is worth nothing when the target's runner sources it and then defines its own
# run_as_stage (or setpriv, or as_stage), or switches the sandbox off for its modules (STAGE_ISOLATION): its modules ran
# as the runner with the runner's two ambient rights, and the build still counted as one in the sandbox.

_TEMPLATE_SOURCE_LINE = 'source "$MODULES_DIR/stage_user.sh"\n'   # in a runner of the old shape (MARKED_RUNNER)


@pytest.mark.parametrize("override", [
    'run_as_stage() { "$@"; }',
    'function as_stage { "$@"; }',
    'stage_preflight () { :; }',
    'setpriv() { shift 6; "$@"; }',
    'STAGE_ISOLATION=',
    'export STAGE_ISOLATION=legacy',
    'unset STAGE_ISOLATION',
    'STAGE_UID=64020',
    'STAGE_SHARED_FOLDERS=()',
], ids=["run_as_stage", "as_stage", "preflight", "setpriv", "isolation", "export", "unset", "uid", "share"])
def test_a_runner_that_replaces_a_part_of_the_helper_is_refused(tmp_path, override):
    from orchestrator.utils.sandbox import STAGE_RUNNER_REPLACES_HELPER, runner_text_problem
    runner = MARKED_RUNNER
    assert _TEMPLATE_SOURCE_LINE in runner
    changed = runner.replace(_TEMPLATE_SOURCE_LINE, f"{_TEMPLATE_SOURCE_LINE}{override}\n", 1)
    name = re.search(r"[A-Za-z_]{4,}", override.replace("function ", "").replace("export ", "")
                     .replace("unset ", "")).group(0)
    assert runner_text_problem(_target_with_runner(tmp_path / "t", changed, helper=False) / "modules") == \
        STAGE_RUNNER_REPLACES_HELPER.format(name=name)


def test_the_runner_check_knows_every_part_of_the_helper():
    from orchestrator.utils import sandbox
    helper = (LAYER / "stage_user.sh").read_text(encoding="utf-8")   # the one helper: the runtime layer's
    functions = set(re.findall(r"(?m)^([a-z_]+)\(\)", helper))
    variables = set(re.findall(r"(?m)^(?:declare -A )?([A-Z_]+)=", helper)) | {"STAGE_ISOLATION"}
    assert functions and variables
    assert functions | {"setpriv"} <= set(sandbox._HELPER_FUNCTIONS)
    assert variables <= set(sandbox._HELPER_VARIABLES)
    # a comment that names a part is no change of it
    assert sandbox._helper_part_replaced("# STAGE_ISOLATION=user\n  # run_as_stage() is the helper's\n") is None


@pytest.mark.parametrize("target", TARGETS)
def test_the_shipped_runners_replace_no_part_of_the_helper(tmp_path, target):
    """The shipped build.sh is only the deprecated hand-over to the framework's runner: it defines no part of the
    helper, and no build runs it."""
    from orchestrator.Core.runtime_layer import own_runner_note
    from orchestrator.utils.sandbox import _helper_part_replaced
    text = (REPO_ROOT / "targets" / target / "modules" / "build.sh").read_text(encoding="utf-8")
    assert _helper_part_replaced(text) is None and own_runner_note(REPO_ROOT / "targets" / target) is None


STICKY = r'''#!/bin/bash
out="$BUILD_CACHE_DIR/output"
mkdir "$out/spool" "$BUILD_SCRATCH_DIR/tmp/spool"
printf 'p' > "$out/spool/part"
printf 't' > "$BUILD_SCRATCH_DIR/tmp/spool/part"
chmod 1777 "$out/spool" "$BUILD_SCRATCH_DIR/tmp/spool"
'''

STICKY_CHECK = r'''
import sys
sys.path.insert(0, sys.argv[1])
from orchestrator.utils.atomic import remove_tree
print("left", sum(len(remove_tree(p)) for p in sys.argv[2:]))
'''


@needs_rights
@person_reads_repo
def test_a_sticky_folder_a_module_made_the_person_can_still_clear(box):
    box.module("source", STICKY)
    result = box.run("--to", "source")
    assert result.returncode == 0, result.stdout + result.stderr
    spools = [box.output / "spool", box.work / "tmp" / "spool"]
    for spool in spools:
        assert not os.lstat(spool).st_mode & 0o1000, f"{spool} kept its sticky bit"
    # the runner's own folders keep theirs: the stage account can change nothing of the runner's
    assert os.lstat(box.work).st_mode & 0o1000
    person = subprocess.run(["setpriv", "--reuid", str(RUNNER), "--regid", str(RUNNER), "--clear-groups", "--",
                             sys.executable, "-I", "-c", STICKY_CHECK, str(REPO_ROOT), *map(str, spools)],
                            capture_output=True, text=True, timeout=120)
    assert person.returncode == 0, person.stdout + person.stderr
    assert person.stdout.strip() == "left 0"
