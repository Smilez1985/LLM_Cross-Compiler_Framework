"""The framework's runtime layer (orchestrator/runtime_layer/*.sh, orchestrator/Core/runtime_layer.py): the stage
runner, the stage account's helper and the entrypoint are the framework's, mounted read-only into every build
container, and a target cannot get around the stage-user switch.

Real-kernel tests run the layer's entrypoint as root with exactly CAP_SETUID and CAP_SETGID and no-new-privileges (as
the container does), through the same code path as in the container: the layer's bin/ holds the host's util-linux
setpriv and a stand-in for busybox that runs the host's tools (no downloaded binary is executed here; CI runs the real
static busybox and setpriv). They need root on Linux and are skipped elsewhere with the reason. The test folders live
under /tmp with mode 0755 (a uid switch needs a parent the stage account can walk).
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from orchestrator.Core import build_end
from orchestrator.Core import runtime_layer as layer

# These tests make and check the toolbox themselves: the real ensure_toolbox, never the test toolbox of conftest
pytestmark = pytest.mark.real_toolbox

RUNNER = 64020
STAGE = 64010
STAND_IN_BUSYBOX = """#!/bin/sh
a="$1"; shift
PATH=/usr/sbin:/usr/bin:/sbin:/bin
case "$a" in sh) exec /bin/sh "$@" ;; *) exec "$a" "$@" ;; esac
"""


def _why_not() -> str:
    if not sys.platform.startswith("linux"):
        return "the runtime layer's uid switch needs Linux (setpriv and /proc)"
    if os.geteuid() != 0:
        return "handing the runner exactly CAP_SETUID and CAP_SETGID needs root on this computer"
    if shutil.which("setpriv") is None:
        return "util-linux setpriv is not installed on this computer"
    if shutil.which("bash") is None:
        return "bash is not installed (the stage modules are bash scripts)"
    return ""


WHY_NOT = _why_not()
# The real static busybox and setpriv the CI job "Runtime layer tools" made (scripts/runtime_toolbox.py OUT); without
# it the box uses this computer's setpriv and a busybox stand-in
REAL_TOOLBOX = Path(os.environ["LLMCF_TEST_TOOLBOX"]) if os.environ.get("LLMCF_TEST_TOOLBOX") else None
HOST_SETPRIV = shutil.which("setpriv") or "setpriv"
needs_root = pytest.mark.skipif(bool(WHY_NOT), reason=WHY_NOT or "-")
needs_posix = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                                 reason="the runtime layer's scripts need a POSIX sh")


class Box:
    """A runtime layer (the shipped scripts + bin/ with the host's setpriv and a busybox stand-in), an image folder
    with stub stage modules, a work folder of the runner and an end folder."""

    def __init__(self, tools: bool = True):
        self.root = Path(tempfile.mkdtemp(prefix="llmcf-layer-", dir="/tmp"))  # nosec B108 - test folder
        os.chmod(self.root, 0o755)
        self.layer = self.root / "llmcf"
        shutil.copytree(layer.SHIPPED_DIR, self.layer, ignore=shutil.ignore_patterns("toolbox", "MANIFEST.sha256"))
        if tools:
            (self.layer / "bin").mkdir()
            if REAL_TOOLBOX:   # the real static tools of the way "build" (CI job "Runtime layer tools")
                for tool in layer.TOOLS:
                    shutil.copy2(REAL_TOOLBOX / f"{tool}-{layer.machine()}", self.layer / "bin" / tool)
            else:
                (self.layer / "bin" / "busybox").write_text(STAND_IN_BUSYBOX, encoding="utf-8")
                shutil.copy2(shutil.which("setpriv"), self.layer / "bin" / "setpriv")
        for path in [self.layer, *self.layer.rglob("*")]:
            os.chmod(path, 0o755)
        self.image = self.root / "image"
        self.modules = self.image / "app" / "modules"
        self.modules.mkdir(parents=True)
        (self.image / "app" / "scripts").mkdir()
        self.fakebin = self.image / "bin"
        self.fakebin.mkdir()
        self.cache = self.root / "build-cache"
        self.work = self.cache / "work"
        self.output = self.cache / "output"
        for folder in (self.work / "tmp", self.work / "home", self.output):
            folder.mkdir(parents=True)
        self.end = self.root / "end-build"
        self.end.mkdir()
        for name in layer.FOUR_STAGES:
            self.module(name, "#!/bin/bash\ntrue\n")
        if os.geteuid() == 0:
            for path in [self.cache, *self.cache.rglob("*"), self.end]:
                os.chown(path, RUNNER, RUNNER)
        os.chmod(self.end, 0o755)

    def module(self, name: str, text: str) -> None:
        path = self.modules / f"{name}_module.sh"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o755)

    def env(self, **extra: str) -> dict:
        return {"PATH": f"{self.fakebin}:/usr/sbin:/usr/bin:/sbin:/bin", "BUILD_CACHE_DIR": str(self.cache),
                "BUILD_SCRATCH_DIR": str(self.work), "TMPDIR": str(self.work / "tmp"),
                "HOME": str(self.work / "home"), "LLMCF_MODULES_DIR": str(self.modules),
                "LLMCF_END_DIR": str(self.end), "STAGE_ISOLATION": "user",
                "STAGE_RUNNER_IDS": f"{RUNNER}:{RUNNER}", **extra}

    def run(self, *args: str, caps: str = "+setuid,+setgid", **extra: str) -> subprocess.CompletedProcess:
        """The layer's entrypoint as the container starts it: root with exactly `caps` and no-new-privileges."""
        bounding = "-all," + caps if caps else "-all"
        return subprocess.run([HOST_SETPRIV, f"--bounding-set={bounding}", f"--inh-caps={bounding}",
                               "--no-new-privs", "--", str(self.layer / "bin" / "busybox"), "sh",
                               str(self.layer / "entrypoint.sh"), *args],
                              env=self.env(**extra), capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def record(self):
        return build_end.read_end_record(self.end / build_end.END_RECORD)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture
def box():
    built = Box()
    yield built
    built.remove()


WHOAMI = r"""#!/bin/bash
{ id -u; id -g; grep -E '^Cap(Eff|Prm|Amb):' /proc/self/status | awk '{print $2}' | tr '\n' ' '; echo
  if (: > "$LLMCF_END_DIR/forged") 2>/dev/null; then echo "end writable"; else echo "end closed"; fi
} > "$BUILD_SCRATCH_DIR/whoami" 2>&1
"""


# --- regressions: what used to get around the switch no longer runs as the runner or regains rights -------------

@needs_root
def test_a_targets_own_runner_is_never_run(box):
    """Before, the host ran the target's modules/build.sh as the runner (with CAP_SETUID and CAP_SETGID): a runner
    that skipped run_as_stage ran its code with those rights. Now the layer's runner starts the stage modules and a
    build.sh of the target is never executed."""
    (box.modules / "build.sh").write_text("#!/bin/bash\nid -u > \"$BUILD_SCRATCH_DIR/own-runner\"\n",
                                          encoding="utf-8")
    box.module("source", WHOAMI)

    result = box.run("--job", "build", "--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (box.work / "own-runner").exists(), "the target's own runner ran"
    uid, gid, caps, end = (box.work / "whoami").read_text().splitlines()
    assert (int(uid), int(gid)) == (STAGE, STAGE)
    assert caps.split() == ["0000000000000000"] * 3
    assert end == "end closed", "a stage module could write the end folder"
    record = box.record()
    assert record is not None and record.done and record.end == "protected"


@needs_root
def test_a_fake_setpriv_or_bash_on_the_images_path_is_never_used_by_the_runner(box):
    """Before, the runner ran on the image's bash and called the image's setpriv: a Dockerfile could put a setpriv
    there that ignores --reuid. The layer's runner uses its own setpriv by its path and the layer's sh."""
    trap = "#!/bin/sh\nid -u > \"$BUILD_SCRATCH_DIR/trap-$(basename \"$0\")\"\nexec /usr/bin/env -- \"$@\"\n"
    for name in ("setpriv", "sh", "sed", "mktemp"):
        (box.fakebin / name).write_text(trap, encoding="utf-8")
        os.chmod(box.fakebin / name, 0o755)
    box.module("source", WHOAMI)

    result = box.run("--job", "build", "--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(box.work.glob("trap-*")), "the runner ran a program of the image"
    assert (box.work / "whoami").read_text().splitlines()[0] == str(STAGE)


@needs_root
def test_bash_env_of_the_image_never_reaches_the_runner(box):
    """Before, an image's ENV BASH_ENV=... made the runner's bash source any file as the runner, with its rights.
    The layer's entrypoint forgets BASH_ENV and ENV and runs on busybox sh; the modules get the layer's stage_env.sh."""
    evil = box.image / "evil.sh"
    evil.write_text("id -u > \"$BUILD_SCRATCH_DIR/evil-$$\"\n", encoding="utf-8")
    box.module("source", "#!/bin/bash\ntype stage_output > \"$BUILD_SCRATCH_DIR/env\" 2>&1 || true\n")

    result = box.run("--job", "build", "--to", "source", BASH_ENV=str(evil), ENV=str(evil))

    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(box.work.glob("evil-*")), "the image's BASH_ENV ran"
    assert "function" in (box.work / "env").read_text()


@needs_root
def test_a_setuid_program_gives_a_stage_module_no_rights_back(box):
    """no-new-privileges: a setuid-root program in the image runs with the stage account's ids."""
    program = box.image / "id-suid"
    shutil.copy2("/usr/bin/id", program)
    os.chmod(program, 0o4755)
    box.module("source", f"#!/bin/bash\n{program} -u > \"$BUILD_SCRATCH_DIR/suid\"\n")

    result = box.run("--job", "build", "--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "suid").read_text().strip() == str(STAGE)


@needs_root
def test_an_eval_in_a_module_cannot_reach_the_runner(box):
    """A module that tries the runner's tricks (eval of a function name, kill, ptrace of its parent) has no rights."""
    box.module("source", "#!/bin/bash\neval 'kill -STOP $PPID' 2>/dev/null; "
                         "if (exec 3< /proc/$PPID/mem) 2>/dev/null; then echo open; else echo refused; fi "
                         "> \"$BUILD_SCRATCH_DIR/mem\"\n")

    result = box.run("--job", "build", "--to", "source")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "mem").read_text().strip() == "refused"


@needs_root
def test_a_runner_with_an_extra_right_refuses_the_build(box):
    box.module("source", WHOAMI)

    result = box.run("--job", "build", "--to", "source", caps="+setuid,+setgid,+chown")

    assert result.returncode == build_end.STAGE_USER_EXIT["rights_extra"], result.stdout + result.stderr
    assert not (box.work / "whoami").exists()
    record = box.record()
    assert record is not None and record.status == "failed" and record.stage == "preflight"


@needs_root
def test_a_layer_without_tools_refuses_and_runs_nothing():
    built = Box(tools=False)
    try:
        result = subprocess.run(["setpriv", "--bounding-set=-all,+setuid,+setgid", "--no-new-privs", "--", "sh",
                                 str(built.layer / "entrypoint.sh")], env=built.env(), capture_output=True,
                                text=True, timeout=60)
        assert result.returncode == 90, result.stdout + result.stderr
    finally:
        built.remove()


@needs_root
def test_slot_modules_run_in_the_order_the_host_hands_over(box):
    for name in ("prep", "rotate", "after", *layer.FOUR_STAGES):
        box.module(name, f"#!/bin/bash\necho {name} >> \"$BUILD_SCRATCH_DIR/order\"\n")

    result = box.run("--job", "build", LLMCF_STAGES="source config prep convert rotate target after")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "order").read_text().split() == ["source", "config", "prep", "convert", "rotate", "target",
                                                        "after"]
    assert "=== Stage 7/7: after done" in result.stdout


# --- the host side ------------------------------------------------------------------------------------------------

def test_the_shipped_copy_matches_its_manifest():
    assert layer.shipped_problems() == []
    assert layer.manifest_text() == (layer.SHIPPED_DIR / "MANIFEST.sha256").read_text(encoding="ascii")


def _target(tmp_path: Path, stages: str = "", files=("prep", "after")) -> Path:
    target = tmp_path / "t"
    (target / "modules").mkdir(parents=True)
    for name in (*layer.FOUR_STAGES, *files):
        (target / "modules" / f"{name}_module.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (target / "target.yml").write_text(stages, encoding="utf-8")
    return target


def test_the_stage_order_comes_from_target_yml_with_fixed_slots(tmp_path):
    target = _target(tmp_path, "stages:\n  before_convert: [prep]\n  after_target: [after]\n")
    assert layer.stage_order(target) == (["source", "config", "prep", "convert", "target", "after"], [])
    assert layer.stage_order(target, "imatrix")[0] == ["source", "config", "prep", "convert"]


@pytest.mark.parametrize("block, named", [
    ("stages:\n  before_convert: [Bad-Name]\n", "not a valid module name"),
    ("stages:\n  before_convert: [missing]\n", "missing"),
    ("stages:\n  during_convert: [prep]\n", "no slot"),
    ("stages:\n  after_target: [target]\n", "not a valid module name"),
])
def test_a_slot_that_does_not_fit_is_refused(tmp_path, block, named):
    _stages, problems = layer.stage_order(_target(tmp_path, block))
    assert problems and named in problems[0]


def test_without_a_stages_block_the_rotate_slot_stays(tmp_path):
    target = _target(tmp_path, "", files=("rotate",))
    assert layer.stage_order(target)[0] == ["source", "config", "convert", "rotate", "target"]


def test_a_packs_own_runner_gets_a_plain_note_and_the_shipped_targets_none(tmp_path):
    target = _target(tmp_path)
    (target / "modules" / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    note = layer.own_runner_note(target)
    assert note and "ignored" in note and "modules/build.sh" in note and str(tmp_path) not in note
    shipped = layer.SHIPPED_DIR.parents[1] / "targets"
    assert layer.own_runner_note(shipped / "Rockchip") is None


def _toolbox(cache: Path) -> None:
    folder = layer.toolbox_dir(cache)
    folder.mkdir(parents=True)
    files = {}
    for tool in layer.TOOLS:
        path = cache / f"src-{tool}"
        path.write_bytes(b"\x7fELF-test-" + tool.encode())
        files[tool] = path
    layer.write_toolbox(folder, "build", files, {})


@needs_posix
def test_the_layer_is_assembled_checked_and_a_change_refuses_the_build(tmp_path):
    _toolbox(tmp_path)
    assert layer.layer_check(tmp_path).verdict == "missing"
    assert layer.ready_layer(tmp_path).ok
    script = layer.layer_dir(tmp_path) / "runner.sh"
    os.chmod(layer.layer_dir(tmp_path), 0o755)
    os.chmod(script, 0o644)
    script.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
    os.chmod(script, 0o444)
    check = layer.layer_check(tmp_path)
    assert check.verdict == "changed" and check.names == ["runner.sh"]
    with pytest.raises(layer.LayerRefused) as refused:
        layer.ready_layer(tmp_path)
    assert "Mojo Miracle" in str(refused.value) and str(tmp_path) not in str(refused.value)
    # the way back is a next step (CLI, GUI and AI each get theirs), not a command written into the text
    from orchestrator.utils.next_steps import steps_of
    assert [s.id for s in steps_of(refused.value)] == [layer.RESTORE_STEP] and "llm-cli" not in str(refused.value)
    assert layer.assemble(tmp_path).ok


def test_offline_with_nothing_cached_refuses_naming_both_ways(tmp_path):
    with pytest.raises(layer.ToolboxMissing) as refused:
        layer.ensure_toolbox(tmp_path, "build", offline=True)
    text = str(refused.value)
    assert "offline" in text and "'build'" in text and "'fetch'" in text and "snapshot.debian.org" in text


def test_a_prebuilt_archive_is_checked_by_both_pins(tmp_path):
    pin = dict(layer.PREBUILT["busybox"]["amd64"])
    with pytest.raises(ValueError, match="pinned sha256"):
        layer.extract_prebuilt(b"!<arch>\nnot the pinned file", pin)
    assert layer.PREBUILT["setpriv"] == {} and "setpriv" in layer.PREBUILT_MISSING


@pytest.mark.skipif(os.name == "nt", reason="the end folder's owner check needs POSIX ids")
def test_the_end_folder_counts_only_when_it_is_the_runners_alone(tmp_path):
    end = tmp_path / "end-build"
    end.mkdir()
    os.chmod(end, 0o755)
    (end / "build.end").write_text("status done\nexit 0\nstage target\njob build\ninputs -\nend protected\n",
                                   encoding="ascii")
    me = os.getuid()
    record, protected, problem = build_end.read_runner_record(end, me, platform_name="linux")
    assert record is not None and record.sealed and protected and problem is None
    record, protected, problem = build_end.read_runner_record(end, me + 1, platform_name="linux")
    assert record is not None and not record.sealed and problem
    os.chmod(end, 0o777)
    assert build_end.read_runner_record(end, me, platform_name="linux")[2]
    os.chmod(end, 0o755)
    # Docker Desktop on Windows or macOS: the build runs, marked unprotected
    record, protected, problem = build_end.read_runner_record(end, me, platform_name="win32")
    assert record.sealed and not protected and problem is None


def test_the_inspect_check_wants_the_frameworks_entrypoint():
    from orchestrator.utils.sandbox import sandbox_problem, stage_user_sandbox
    options, _env = stage_user_sandbox((1000, 1000), working_dir="/build-cache/work")
    options["entrypoint"] = list(layer.ENTRYPOINT)
    options["command"] = ["--job", "build"]
    attrs = {"Config": {"User": "0:0", "Entrypoint": ["/app/entrypoint.sh"], "Cmd": ["--job", "build"]},
             "HostConfig": {"CapDrop": ["ALL"], "CapAdd": ["SETUID", "SETGID"], "SecurityOpt": ["no-new-privileges"],
                            "ReadonlyRootfs": True, "NetworkMode": "none"}, "Mounts": []}
    assert "entrypoint" in (sandbox_problem(attrs, options, {}) or "")
    attrs["Config"]["Entrypoint"] = list(layer.ENTRYPOINT)
    assert sandbox_problem(attrs, options, {}) is None


def test_static_elf_check():
    assert not layer.is_static_elf(b"#!/bin/sh\n")
    if sys.platform.startswith("linux") and Path("/bin/ls").is_file():
        assert not layer.is_static_elf(Path("/bin/ls").read_bytes())   # a dynamic program has an interpreter
    assert stat.S_IMODE(0o755)


# --- review regressions (cs-runtime-layer2-v) -----------------------------------------------------------------------

def _layer_attrs(**config):
    return {"Config": {"User": "0:0", "Entrypoint": list(layer.ENTRYPOINT), "Cmd": ["--job", "build"], **config},
            "HostConfig": {"CapDrop": ["ALL"], "CapAdd": ["SETUID", "SETGID"], "SecurityOpt": ["no-new-privileges"],
                           "ReadonlyRootfs": True, "NetworkMode": "none"},
            "Mounts": [{"Type": "bind", "Source": "/cache/runtime-layer/current", "Destination": layer.LAYER_MOUNT,
                        "RW": False}]}


def _layer_options():
    from orchestrator.utils.sandbox import stage_user_sandbox
    options, _env = stage_user_sandbox((1000, 1000), working_dir="/build-cache/work")
    options["entrypoint"] = list(layer.ENTRYPOINT)
    options["command"] = ["--job", "build"]
    return options


def test_an_image_volume_over_the_runtime_layer_refuses_the_build():
    """An image's `VOLUME /llmcf/bin` makes the engine mount an anonymous volume, filled from the image's own
    /llmcf/bin, over the read-only layer (seen with Podman 4.9: the entrypoint then ran the image's busybox as root with
    CAP_SETUID and CAP_SETGID). The inspect check refuses any mount the host did not decide inside a read-only one."""
    from orchestrator.utils.sandbox import sandbox_problem
    volumes = layer.layer_volumes(Path("/cache"), None)
    attrs = _layer_attrs()
    assert sandbox_problem(attrs, _layer_options(), volumes) is None
    attrs["Mounts"].insert(0, {"Type": "volume", "Name": "f00", "Source": "/var/lib/volumes/f00/_data",
                               "Destination": f"{layer.LAYER_MOUNT}/bin", "RW": True})
    problem = sandbox_problem(attrs, _layer_options(), volumes) or ""
    assert f"{layer.LAYER_MOUNT}/bin" in problem and "/var/lib" not in problem
    attrs["Mounts"][0]["Destination"] = "/llmcf-elsewhere"   # a name that only starts the same is no mount inside it
    assert sandbox_problem(attrs, _layer_options(), volumes) is None


def test_an_image_volume_inside_the_end_folder_refuses_the_build(tmp_path):
    """The end folder is the one writable mount the host trusts (only the runner writes it): an image's
    `VOLUME /llmcf-end/build.end` made the engine mount an anonymous volume inside it, so the runner's record landed in
    the image's volume and the host's end folder got a folder of the engine's in its place. Refused like a mount
    inside the read-only layer, before the container starts."""
    from orchestrator.utils.sandbox import sandbox_problem
    end = tmp_path / "end-build"
    volumes = layer.layer_volumes(Path("/cache"), end)
    attrs = _layer_attrs()
    attrs["Mounts"].append({"Type": "bind", "Source": str(end), "Destination": layer.END_MOUNT, "RW": True})
    assert sandbox_problem(attrs, _layer_options(), volumes) is None
    attrs["Mounts"].append({"Type": "volume", "Name": "f01", "Source": "/var/lib/volumes/f01/_data",
                            "Destination": f"{layer.END_MOUNT}/build.end", "RW": True})
    problem = sandbox_problem(attrs, _layer_options(), volumes) or ""
    assert f"{layer.END_MOUNT}/build.end" in problem and "/var/lib" not in problem
    from orchestrator.utils.sandbox import GUARDED_MOUNTS   # utils names the end mount without importing Core
    assert layer.END_MOUNT in GUARDED_MOUNTS


def test_podmans_entrypoint_as_one_string_is_the_frameworks():
    """Podman 4 reports Config.Entrypoint as one string ("/llmcf/bin/busybox sh /llmcf/entrypoint.sh"): the check
    compared it letter by letter with the list and refused every Podman build."""
    from orchestrator.utils.sandbox import sandbox_problem
    volumes = layer.layer_volumes(Path("/cache"), None)
    attrs = _layer_attrs(Entrypoint=" ".join(layer.ENTRYPOINT))
    assert sandbox_problem(attrs, _layer_options(), volumes, "podman") is None
    attrs["Config"]["Entrypoint"] = "/app/entrypoint.sh"
    assert "entrypoint" in (sandbox_problem(attrs, _layer_options(), volumes, "podman") or "")


def test_an_installed_pack_in_the_targets_folder_gets_the_note(tmp_path, monkeypatch):
    """Packs and the wizard's targets live in the same targets/ folder as the framework's own: the folder alone made
    every one of them 'shipped', so a pack's own build.sh was ignored without the decided note."""
    from orchestrator.Core.community_pack import PROVENANCE_NAME
    monkeypatch.setattr(layer, "SHIPPED_DIR", tmp_path / "orchestrator" / "runtime_layer")
    pack = tmp_path / "targets" / "Pack"
    (pack / "modules").mkdir(parents=True)
    (pack / "modules" / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (pack / "target.yml").write_text("capabilities:\n  support_level: official\n", encoding="utf-8")
    (pack / PROVENANCE_NAME).write_text("{}", encoding="utf-8")
    note = layer.own_runner_note(pack)
    assert note and "modules/build.sh" in note
    (pack / PROVENANCE_NAME).unlink()
    (pack / "target.yml").write_text("capabilities:\n  support_level: community\n", encoding="utf-8")
    assert layer.own_runner_note(pack)          # the person's own target (wizard): not the framework's either
    template = tmp_path / "targets" / "_template"
    (template / "modules").mkdir(parents=True)
    (template / "modules" / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    assert layer.own_runner_note(template) is None


@needs_posix
def test_the_runners_path_leads_into_the_read_only_layer(tmp_path):
    """The runner's PATH must name a place the image cannot fill: /llmcf-runner-has-no-path is a folder any image can
    create. A path inside the layer mount is covered by it (layer_check refuses any extra file there, the inspect
    check any mount inside it)."""
    lay = tmp_path / "llmcf"
    shutil.copytree(layer.SHIPPED_DIR, lay, ignore=shutil.ignore_patterns("toolbox", "MANIFEST.sha256"))
    (lay / "bin").mkdir()
    (lay / "bin" / "busybox").write_text(STAND_IN_BUSYBOX, encoding="utf-8")
    os.chmod(lay / "bin" / "busybox", 0o755)
    result = subprocess.run(["sh", "-c", 'LLMCF_LAYER="$1"; . "$1/tools.sh"; printf %s "$PATH"', "_", str(lay)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(str(lay) + "/"), result.stdout


def test_a_cut_off_program_is_not_static_and_raises_nothing():
    for data in (b"\x7fELF", b"\x7fELF\x02\x01\x01" + b"\0" * 20, b""):
        assert layer.is_static_elf(data) is False


# --- the runtime layer wired into every build (chain runtime-layer3) ---------------------------------------------

def test_benchmark_is_refused_as_a_slot_module(tmp_path):
    """Regression: benchmark_module.sh measures the build computer and never runs in a build; a target.yml that put it
    into a slot would have run it."""
    target = _target(tmp_path, "stages:\n  after_target: [benchmark]\n", files=("benchmark",))
    _stages, problems = layer.stage_order(target)
    assert problems and "benchmark" in problems[0] and "never runs in a build" in problems[0]


def test_a_done_record_must_name_the_last_slot_module():
    """Regression: with a module in the slot after_target the runner's last stage is that module; its done record was
    refused as "before the last stage" (the job's last of the four stages was the only one accepted)."""
    record = build_end.EndRecord("done", 0, "after", sealed=True)
    assert build_end.end_problem(record, 0, "build", last="after") is None
    early = build_end.EndRecord("done", 0, "target", sealed=True)
    assert "before the last stage after" in build_end.end_problem(early, 0, "build", last="after")
    assert build_end.end_problem(early, 0, "build") is None   # without slots: target, as before


@needs_root
def test_what_the_host_hands_the_runner_never_reaches_a_module(box):
    """Regression: the host's values for the runner alone (its ids, the end folder, the stage order, the target's
    label) reached every stage module's environment; a module now finds none of them."""
    box.module("source", "#!/bin/bash\nenv > \"$BUILD_SCRATCH_DIR/module-env\"\n")
    result = box.run("--job", "build", "--to", "source", LLMCF_STAGES="source config convert target",
                     LLMCF_TARGET_LABEL="label-only-for-the-runner")
    assert result.returncode == 0, result.stdout + result.stderr
    seen = (box.work / "module-env").read_text(encoding="utf-8")
    for name in ("STAGE_RUNNER_IDS=", "LLMCF_END_DIR=", "LLMCF_STAGES=", "LLMCF_TARGET_LABEL="):
        assert f"\n{name}" not in "\n" + seen, name
    assert "label-only-for-the-runner" in result.stdout   # the runner itself still has it


@needs_root
def test_the_preflight_reads_the_inputs_whatever_the_runners_working_folder(box, tmp_path):
    """Regression: the stage account's test child ran find in the runner's working folder; one the stage account
    cannot enter (a closed folder) made find print nothing, and an unreadable input passed the check."""
    model = box.root / "input-model"
    model.mkdir()
    (model / "weights.bin").write_bytes(b"0")
    for path in (model, model / "weights.bin"):
        os.chown(path, RUNNER, RUNNER)
    os.chmod(model / "weights.bin", 0o600)
    closed = tmp_path / "closed"
    closed.mkdir()
    os.chmod(closed, 0o700)
    bounding = "-all,+setuid,+setgid"
    result = subprocess.run([HOST_SETPRIV, f"--bounding-set={bounding}", f"--inh-caps={bounding}", "--no-new-privs",
                             "--", str(box.layer / "bin" / "busybox"), "sh", str(box.layer / "entrypoint.sh"),
                             "--job", "build", "--to", "source"],
                            env=box.env(MODEL_SOURCE=str(model)), capture_output=True, text=True, timeout=120,
                            stdin=subprocess.DEVNULL, cwd=closed)
    assert result.returncode == build_end.STAGE_USER_EXIT["inputs"], result.stdout + result.stderr


def test_the_builder_hands_the_end_record_check_the_last_stage_of_the_order():
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    assert BuildEngine._last_stage(progress) is None
    progress.runtime_layer = {"stages": ["source", "config", "convert", "target", "after"]}
    assert BuildEngine._last_stage(progress) == "after"


# --- the toolbox: the person's way, the one pinned table of the way "fetch" ---------------------------------------

def test_the_fetch_way_refuses_plainly_while_setpriv_has_no_pinned_entry(tmp_path):
    """Regression: the way "fetch" took busybox from the table and silently built setpriv the other way; while the
    table has no setpriv entry it now refuses, names the way "build" and fetches nothing."""
    fetched = []
    with pytest.raises(layer.ToolboxMissing) as refused:
        layer.ensure_toolbox(tmp_path, "fetch", offline=False, fetch=lambda url: fetched.append(url) or b"",
                             build=lambda work: pytest.fail("the way fetch must not build"))
    assert fetched == [] and "'build'" in str(refused.value) and "setpriv" in str(refused.value)


def test_the_one_pinned_table_is_read_and_a_pin_without_its_sha256_is_left_out(tmp_path):
    table = layer.read_prebuilt()
    assert set(table["busybox"]) == {"amd64", "arm64"} and table["setpriv"] == {}
    assert table == layer.PREBUILT
    bad = tmp_path / "prebuilt.json"
    bad.write_text('{"setpriv": {"amd64": {"kind": "file", "url": "http://example.com/setpriv", "sha256": "'
                   + "a" * 64 + '", "name": "setpriv"}, "arm64": {"kind": "file", "url": "https://example.com/s", '
                   '"name": "setpriv"}}}', encoding="utf-8")
    assert layer.read_prebuilt(bad)["setpriv"] == {}   # not https, and no sha256


def test_a_pinned_program_file_is_checked_by_its_sha256_and_must_be_static():
    from orchestrator.utils.helpers import sha256_bytes
    data = b"\x7fELF\x02\x01" + b"\0" * 58
    pin = {"kind": "file", "name": "setpriv-amd64", "sha256": sha256_bytes(data), "url": "https://example.com/x"}
    with pytest.raises(ValueError, match="pinned sha256"):
        layer.extract_prebuilt(data + b"x", pin)
    with pytest.raises(ValueError, match="statically linked"):   # no program headers: not a whole static program
        layer.extract_prebuilt(data, pin)


def test_only_the_person_chooses_the_toolbox_way(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, AISettingsChange, person_only_text
    assert "runtime_layer_toolbox" in PERSON_ONLY_SETTINGS
    assert "never change" in person_only_text("runtime_layer_toolbox")
    problems = AISettingsChange.problems_of_settings(None, None, {"runtime_layer_toolbox": "fetch"}, by_ai=True)
    assert [p.code for p in problems] == ["person_only"]
    config = ConfigManager(tmp_path / "configs")
    assert layer.toolbox_way(config) == "build"
    assert layer.set_toolbox_way(config, "fetch") == "fetch" and layer.toolbox_way(config) == "fetch"
    assert ConfigManager(tmp_path / "configs").get("runtime_layer_toolbox") == "fetch"   # saved
    with pytest.raises(layer.ToolboxWayError):
        layer.set_toolbox_way(config, "download")


def test_the_layer_status_names_labels_only(tmp_path):
    import json
    status = layer.layer_status(tmp_path, {"runtime_layer_toolbox": "fetch"}, platform_name="win32")
    assert status["verdict"] == "no_toolbox" and status["toolbox_way"] == "fetch" and status["fetch_ready"] is False
    assert status["end_folder"] == "unprotected" and str(tmp_path) not in json.dumps(status)
    assert layer.layer_status(tmp_path, platform_name="linux")["end_folder"] == "protected"


def test_the_cli_shows_the_layer_and_sets_the_way(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from orchestrator.Core.config_manager import ConfigManager
    config = ConfigManager(tmp_path / "configs")
    config.set("cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(cli_module, "_settings", lambda ctx: (config, lambda v: Path(v)))
    result = CliRunner().invoke(cli_module.cli, ["build", "image", "layer", "--toolbox", "fetch"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "runtime_layer_toolbox set to fetch" in text and "not on this computer yet" in text
    assert "Use the way 'build'" in text
    assert config.get("runtime_layer_toolbox") == "fetch"


@pytest.mark.skipif(REAL_TOOLBOX is None, reason="the real tools come from the CI job 'Runtime layer tools'")
def test_the_real_tools_are_static_and_run():
    """The tools the way "build" made: static programs (nothing loads from an image), and they run."""
    for tool in layer.TOOLS:
        path = REAL_TOOLBOX / f"{tool}-{layer.machine()}"
        assert layer.is_static_elf(path.read_bytes()), tool
    assert subprocess.run([str(REAL_TOOLBOX / f"busybox-{layer.machine()}"), "true"], timeout=30).returncode == 0
    version = subprocess.run([str(REAL_TOOLBOX / f"setpriv-{layer.machine()}"), "--version"], capture_output=True,
                             text=True, timeout=30)
    assert version.returncode == 0 and "util-linux" in version.stdout
