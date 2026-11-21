"""The values a module keeps private to this computer (orchestrator/Core/modules_env.py; docs/DESIGN.md, the wizard's
local layer for private values): the file of a target, the build's read-only mount of it, and the stage runner that
exports its variables to the stages without evaluating anything.

Every value is made at run time from random characters, so this file holds no secret.
"""

import os
import random
import stat
import string
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import modules_env as me

ALNUM = string.ascii_letters + string.digits


def value(seed, n=14):
    return "".join(random.Random(seed).choices(ALNUM, k=n))


POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


# --- the file of a target ---------------------------------------------------------------------------------------

def test_the_file_is_in_modules_env_of_the_state_folder_and_never_anywhere_else(tmp_path):
    path = me.path_for(tmp_path, "my-board")
    assert path == tmp_path / "modules_env" / "my-board.env"
    for bad in ("", ".", "..", "../x", "a/b", "a\\b", "..hidden", "-x", "x" * 65, "a b"):
        with pytest.raises(me.LocalValuesError):
            me.path_for(tmp_path, bad)


@pytest.mark.parametrize("name", ["LOCAL_SDK_DIR", "LOCAL_A", "LOCAL_X9_Y", "LOCAL_" + "A" * 64])
def test_a_name_is_local_and_capitals(name):
    me.check(name, "v")


@pytest.mark.parametrize("name", ["SDK_DIR", "local_sdk", "LOCAL_", "LOCAL_9", "LOCAL_a", "LOCAL_X;rm", "PATH",
                                  "LOCAL_" + "A" * 65, "LOCAL-X"])
def test_a_name_that_is_no_local_name_is_refused(name):
    with pytest.raises(me.LocalValuesError):
        me.check(name, "v")


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\0b", "x" * 4097])
def test_a_value_holds_one_line(bad):
    with pytest.raises(me.LocalValuesError):
        me.check("LOCAL_X", bad)


def test_write_read_set_and_remove_round_trip(tmp_path):
    secret = value("pw")
    me.write(tmp_path, "t", {"LOCAL_SDK_DIR": "/mnt/nas/x y/sdk", "LOCAL_PW": secret, "LOCAL_QUOTE": "it's \"q\" $(x) `y`"})
    assert me.read(tmp_path, "t") == {"LOCAL_SDK_DIR": "/mnt/nas/x y/sdk", "LOCAL_PW": secret,
                                      "LOCAL_QUOTE": "it's \"q\" $(x) `y`"}
    assert me.names(tmp_path, "t") == ["LOCAL_PW", "LOCAL_QUOTE", "LOCAL_SDK_DIR"]
    me.set_value(tmp_path, "t", "LOCAL_PW", "new" + secret)
    assert me.read(tmp_path, "t")["LOCAL_PW"] == "new" + secret and len(me.read(tmp_path, "t")) == 3
    assert me.remove(tmp_path, "t") is True and me.read(tmp_path, "t") == {} and me.remove(tmp_path, "t") is False


def test_an_empty_mapping_removes_the_file(tmp_path):
    me.write(tmp_path, "t", {"LOCAL_X": "1"})
    me.write(tmp_path, "t", {})
    assert not me.path_for(tmp_path, "t").exists()


@POSIX
def test_the_file_is_private_to_the_person(tmp_path):
    path = me.write(tmp_path, "t", {"LOCAL_X": value("x")})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_parse_skips_what_is_no_value_and_takes_the_rest_of_the_line_as_it_stands():
    text = "# a comment\n\nLOCAL_A=1\nPATH=/evil\nlocal_b=2\nLOCAL_C= spaced = value \nLOCAL_D\nLOCAL_E;rm=3\n"
    assert me.parse(text) == {"LOCAL_A": "1", "LOCAL_C": " spaced = value "}


def test_a_value_is_never_in_what_names_the_values(tmp_path):
    secret = value("n")
    me.write(tmp_path, "t", {"LOCAL_PW": secret})
    assert secret not in " ".join(me.names(tmp_path, "t"))


@POSIX
def test_a_build_gets_its_own_copy_readable_for_the_container_and_the_original_stays_private(tmp_path):
    state, build = tmp_path / "state", tmp_path / "build"
    build.mkdir()
    me.write(state, "t", {"LOCAL_X": value("c")})
    copy = me.stage(state, "t", build)
    assert copy == build / "local" / "local.env" and copy.read_text() == me.path_for(state, "t").read_text()
    assert stat.S_IMODE(copy.stat().st_mode) & 0o044 == 0o044, "the container's user reads it through the mount"
    assert stat.S_IMODE(me.path_for(state, "t").stat().st_mode) == 0o600
    assert me.stage(state, "none", build) is None


def test_no_module_folder_ever_holds_the_file():
    """The folder is `modules_env/` of the state folder, in .gitignore for a checkout that works from the repository."""
    root = Path(__file__).resolve().parents[1]
    assert "modules_env/" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()


# --- the build mounts it ----------------------------------------------------------------------------------------

from test_review_build_engine import client, config_for, engine, make_engine  # noqa: E402,F401


@pytest.mark.parametrize("stage, use_imatrix", [("_execute_build_modules", False), ("_generate_imatrix", True)])
def test_every_build_container_gets_the_targets_private_values_read_only(engine, client, tmp_path, stage, use_imatrix):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    state = Path(engine.config.user_state_dir)
    secret = value("mount")
    me.write(state, "Rockchip", {"LOCAL_SDK_DIR": "/mnt/nas/" + secret})
    dataset = tmp_path / "calib.txt"
    dataset.write_text("text")
    cfg = config_for(tmp_path, use_imatrix=use_imatrix, dataset_path=str(dataset))
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    client.stage_options["llm-"] = {"outputs": {}}
    try:
        getattr(engine, stage)(cfg, progress, SimpleNamespace(id="sha256:img", tags=["img"]),
                               engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
    except RuntimeError:
        pass   # the fake image writes no model: only the container's options matter here
    created = client.created[-1]
    mounts = {bind["bind"]: (host, bind["mode"]) for host, bind in created["volumes"].items()}
    host, mode = mounts[me.MOUNT]
    assert mode == "ro"
    assert (Path(host) / me.STAGED_NAME).read_text(encoding="utf-8") == me.path_for(state, "Rockchip").read_text()
    assert created["environment"][me.ENV_NAME] == f"{me.MOUNT}/{me.STAGED_NAME}"
    # the values are in the file only: not in the container's environment, not in any option, not in the log
    assert secret not in repr(created["environment"]) and secret not in repr(created["volumes"])
    assert secret not in "\n".join(progress.logs)
    assert any("LOCAL_SDK_DIR" in line and "Private values" in line for line in progress.logs)


def test_a_build_of_a_target_without_values_mounts_none(engine, client, tmp_path):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    cfg = config_for(tmp_path)
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    client.stage_options["llm-"] = {"outputs": {}}
    try:
        engine._execute_build_modules(cfg, progress, SimpleNamespace(id="sha256:img", tags=["img"]),
                                      engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
    except RuntimeError:
        pass
    created = client.created[-1]
    assert me.ENV_NAME not in created["environment"]
    assert me.MOUNT not in {bind["bind"] for bind in created["volumes"].values()}


# --- the stage runner exports them ------------------------------------------------------------------------------

from test_runtime_layer import box, needs_root  # noqa: E402,F401


@needs_root
def test_a_stage_module_gets_the_values_as_variables_and_nothing_in_them_runs(box, tmp_path):
    secret = value("run")
    tricky = f"/mnt/nas/a b/$(touch {tmp_path}/pwned) `touch {tmp_path}/pwned2` it's \"q\" {secret}"
    env_file = box.root / "local.env"   # a folder the runner's account can enter (pytest's own is private to root)
    env_file.write_text(f"# comment\nLOCAL_SDK_DIR={tricky}\nLOCAL_PW={secret}\nPATH=/evil\nlocal_lower=1\n"
                        "LOCAL_BAD;touch=1\nLOCAL_EMPTY=\n", encoding="utf-8")
    os.chmod(env_file, 0o644)
    box.module("source", '#!/bin/bash\n{ printf "SDK=%s\\n" "$LOCAL_SDK_DIR"; printf "PW=%s\\n" "$LOCAL_PW";\n'
               '  printf "EMPTY=[%s]\\n" "${LOCAL_EMPTY-unset}"; printf "LOWER=[%s]\\n" "${local_lower-unset}";\n'
               '  printf "PATH_KEPT=%s\\n" "$([ "$PATH" != /evil ] && echo yes)"; } > "$BUILD_SCRATCH_DIR/seen"\n')
    result = box.run("--job", "build", "--to", "source", LLMCF_STAGES="source config convert target",
                     LOCAL_ENV_FILE=str(env_file))
    assert result.returncode == 0, result.stdout + result.stderr
    seen = (box.work / "seen").read_text(encoding="utf-8")
    assert f"SDK={tricky}\n" in seen and f"PW={secret}\n" in seen
    assert "EMPTY=[]" in seen and "LOWER=[unset]" in seen and "PATH_KEPT=yes" in seen
    assert not (tmp_path / "pwned").exists() and not (tmp_path / "pwned2").exists(), "nothing in a value is evaluated"
    assert secret not in result.stdout and secret not in result.stderr, "the runner's own output never holds a value"


@needs_root
def test_a_missing_values_file_stops_the_build_with_the_reason(box, tmp_path):
    result = box.run("--job", "build", "--to", "source", LLMCF_STAGES="source config convert target",
                     LOCAL_ENV_FILE=str(tmp_path / "gone.env"))
    assert result.returncode != 0 and "LOCAL_ENV_FILE" in result.stdout + result.stderr


@needs_root
def test_a_build_without_the_file_runs_as_before(box):
    box.module("source", '#!/bin/bash\nprintf "[%s]" "${LOCAL_SDK_DIR-unset}" > "$BUILD_SCRATCH_DIR/seen"\n')
    result = box.run("--job", "build", "--to", "source", LLMCF_STAGES="source config convert target")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.work / "seen").read_text() == "[unset]"
