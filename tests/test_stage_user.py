"""The stage modules run under their own user (the stage account), and the stage runner keeps only the rights that
switch needs (targets/*/modules/build.sh with modules/stage_user.sh, entrypoint.sh, utils/sandbox.py).

The end record is sealed with a one-run key that only the stage runner holds. A module that ran as the same user as
the runner could read that key from the runner's memory (/proc/<runner>/mem) wherever the host kernel allows it (Yama
ptrace_scope 0, or no Yama); dropped capabilities and seccomp do not stop it. Now every stage module is started through
one helper that switches to the stage account (another uid and gid, no capabilities), so the kernel refuses that read
whatever Yama says; the runner checks at its start that it has exactly the rights the switch needs and that a test
child cannot read it, and refuses the build otherwise.

The real tests run the stage runner as an unprivileged user with exactly CAP_SETUID and CAP_SETGID (setpriv, as the
entrypoint does in the container); they need root on Linux to hand out those two rights and are skipped elsewhere.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell

from test_review_build_engine import client, engine, make_engine  # noqa: F401 - fixtures

from orchestrator.Core import build_end

KEY = "0123456789abcdef0123456789abcdef"
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

# A stage module that walks up its parents (the stage runner among them) and looks for the run's key in their
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


class RunnerBox:
    """A stage runner of a target (its real build.sh and stage_user.sh) with stub stage modules, in folders the
    unprivileged runner account owns, and the run's key in the job folder."""

    def __init__(self, target: str):
        self.root = Path(tempfile.mkdtemp(prefix="llmcf-stage-user-", dir="/tmp"))  # nosec B108 - test folder
        os.chmod(self.root, 0o755)
        self.app = self.root / "app"
        shutil.copytree(REPO_ROOT / "targets" / target / "modules", self.app / "modules")
        shutil.copytree(REPO_ROOT / "targets" / target / "scripts", self.app / "scripts")
        self.cache = self.root / "build-cache"
        self.work = self.cache / "work"
        self.output = self.cache / "output"
        for folder in (self.work / "tmp", self.work / "home", self.output):
            folder.mkdir(parents=True)
        self.stages = self.work / "stages" / "build"
        self.stages.mkdir(parents=True)
        key = self.stages / build_end.END_KEY
        key.write_text(KEY + "\n", encoding="ascii")
        for path in [self.cache, *self.cache.rglob("*")]:
            os.chown(path, RUNNER, RUNNER)
        os.chmod(key, 0o600)
        for path in self.app.rglob("*"):
            os.chmod(path, 0o755)

    def module(self, name: str, text: str) -> None:
        path = self.app / "modules" / f"{name}_module.sh"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o755)

    def run(self, *args: str, caps: str = "+setuid,+setgid", isolation: str = "user",
            **extra: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "BUILD_CACHE_DIR": str(self.cache),
               "BUILD_SCRATCH_DIR": str(self.work), "TMPDIR": str(self.work / "tmp"),
               "HOME": str(self.work / "home"), "JOB_TYPE": "build", "STAGE_ISOLATION": isolation, **extra}
        bounding = "-all," + caps if caps else "-all"
        return subprocess.run(["setpriv", f"--bounding-set={bounding}", "--reuid", str(RUNNER), "--regid",
                               str(RUNNER), "--clear-groups", f"--inh-caps={bounding}", f"--ambient-caps={bounding}",
                               "--no-new-privs", "--", "bash", str(self.app / "modules" / "build.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def record(self):
        return build_end.read_end_record(self.stages / build_end.END_RECORD, key=KEY)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture(params=TARGETS)
def box(request):
    built = RunnerBox(request.param)
    yield built
    built.remove()


# --- the regression: a module cannot read the runner's key ---------------------------------------------------------

@needs_rights
def test_a_stage_module_cannot_read_the_runners_key_from_its_memory(box):
    """Before, the module ran as the runner's own user and found the key in the runner's memory (/proc/<pid>/mem)
    on a host without Yama; now it runs as the stage account and the kernel refuses the read."""
    box.module("source", ATTACK)

    result = box.run("--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "attack").read_text().strip() == "refused", "a stage module read the runner's key"
    record = box.record()
    assert record is not None and record.done and record.sealed


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
    helper = REPO_ROOT / "targets" / target / "modules" / "stage_user.sh"
    return subprocess.run([posix_shell("bash"), "-c", 'source "$1"; stage_rights_problem "$2"', "_", str(helper),
                           str(status)], capture_output=True, text=True, timeout=60, check=True).stdout.strip()


@needs_bash
@pytest.mark.parametrize("target", TARGETS)
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
    helper = REPO_ROOT / "targets" / "Rockchip" / "modules" / "stage_user.sh"

    def verdict(lines):
        return subprocess.run([posix_shell("bash"), "-c", 'source "$1"; stage_switch_problem "$2"', "_", str(helper),
                               "\n".join(lines)], capture_output=True, text=True, timeout=60).stdout.split(" ", 1)[0]
    good = ["64010", "64010", "0" * 16, "0" * 16, "0" * 16, "0" * 16, "mem-refused", "environ-refused"]
    assert verdict(good) == ""
    assert verdict(["1000"] + good[1:]) == "switch_failed"
    assert verdict([]) == "switch_failed"
    assert verdict(good[:2] + ["00000000000000c0"] + good[3:]) == "stage_has_rights"
    assert verdict(good[:6] + ["mem-open", "environ-refused"]) == "reads_runner"
    assert verdict(good[:7] + ["environ-open"]) == "reads_runner"


def test_both_targets_carry_the_same_stage_account_helper_and_the_images_create_its_account():
    rockchip = (REPO_ROOT / "targets" / "Rockchip" / "modules" / "stage_user.sh").read_text(encoding="utf-8")
    template = (REPO_ROOT / "targets" / "_template" / "modules" / "stage_user.sh").read_text(encoding="utf-8")
    assert rockchip == template
    from orchestrator.utils.sandbox import STAGE_ACCOUNT
    assert f"STAGE_UID={STAGE_ACCOUNT[0]}" in rockchip and f"STAGE_GID={STAGE_ACCOUNT[1]}" in rockchip
    for name, code in build_end.STAGE_USER_EXIT.items():
        assert f"    [{name}]={code}\n" in rockchip
    for dockerfile in ("targets/Rockchip/dockerfile", "targets/_template/Dockerfile", "targets/_template/Dockerfile.gpu"):
        text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
        assert f"groupadd -g {STAGE_ACCOUNT[1]} llmstage" in text and f"-u {STAGE_ACCOUNT[0]} -g llmstage" in text
        assert "command -v setpriv" in text
    for runner in ("targets/Rockchip/modules/build.sh", "targets/_template/modules/build.sh"):
        text = (REPO_ROOT / runner).read_text(encoding="utf-8")
        assert 'run_as_stage bash "$MODULES_DIR/${name}_module.sh"' in text   # the one way a module starts
        assert text.count('_module.sh" ||') == 1


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


def test_the_key_file_is_the_owners_only(tmp_path):
    key = build_end.new_end_key(tmp_path / "work")
    path = tmp_path / "work" / "stages" / "build" / build_end.END_KEY
    assert key and path.read_text(encoding="ascii").strip() == key
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


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
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "STAGE_ISOLATION": "user",
           "STAGE_RUNNER_IDS": f"{RUNNER}:{RUNNER}", "LLAMA_CPP_PATH": "/nonexistent"}
    result = subprocess.run(["setpriv", "--bounding-set=-all,+setuid,+setgid", "--inh-caps=-all", "--no-new-privs",
                             "--", "bash", str(REPO_ROOT / "entrypoint.sh"), "grep", "-E",
                             "^(Uid|Gid|CapEff|CapPrm|CapAmb|NoNewPrivs):", "/proc/self/status"],
                            env=env, capture_output=True, text=True, timeout=60, cwd="/")
    assert result.returncode == 0, result.stdout + result.stderr
    status = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line and line[0].isupper())
    assert status["Uid"].split()[0] == str(RUNNER) and status["Gid"].split()[0] == str(RUNNER)
    assert {status[k].strip() for k in ("CapEff", "CapPrm", "CapAmb")} == {"00000000000000c0"}
    assert status["NoNewPrivs"].strip() == "1"

    bad = subprocess.run(["bash", str(REPO_ROOT / "entrypoint.sh"), "true"], capture_output=True, text=True,
                         timeout=60, cwd="/", env=dict(env, STAGE_RUNNER_IDS="nobody"))
    assert bad.returncode == build_end.STAGE_USER_EXIT["switch_failed"]


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
