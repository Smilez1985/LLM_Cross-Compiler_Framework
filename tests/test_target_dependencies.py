"""The dependencies of a target as data (orchestrator/schemas/target_template.py Dependency, Core/target_dependencies.py,
the module generator; docs/DESIGN.md, "Dependencies are data in the wizard"): the entries and what refuses them, "is it
there?" without a shell, the plan for the build image (pinned apt packages, hash-checked pip wheels, never a vendor file),
the module made with them, `llm-cli module deps` and the wizard's list.

Every hash is made at run time from random characters, so this file holds no secret-shaped literal.
"""

import random
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from orchestrator.Core import target_dependencies as deps
from orchestrator.Core.module_generator import ModuleGenerator, template_variable_problems, vendor_blocks
from orchestrator.schemas.target_template import Dependency

HEX = "0123456789abcdef"


def sha(seed):
    return "".join(random.Random(seed).choices(HEX, k=64))


def entry(**fields):
    base = dict(name="libusb-1.0-0", version="2:1.0.26-1", kind="system_package", origin="distribution",
                source="template", check=["dpkg", "-s", "libusb-1.0-0"])
    return Dependency(**{**base, **fields})


PIP = dict(name="numpy", version="2.1.0", kind="pip_package", origin="package_index", sha256=sha("numpy"),
           source="excerpt:12", check=["pip", "show", "numpy"])
VENDOR = dict(name="noe-sdk.tar.gz", version="1.2", kind="vendor_file", origin="vendor_page", source="excerpt:3",
              url="https://vendor.example.com/noe")
DRIVER = dict(name="npu-driver", version="5.0", kind="board_driver", origin="vendor_page", source="probe:NPU",
              url="https://vendor.example.com/driver", note="install it with the vendor package")


# --- the entries ---------------------------------------------------------------------------------------------------

def test_a_valid_entry_of_each_kind_is_taken():
    for fields in (dict(), PIP, VENDOR, DRIVER, dict(name="debian", version="bookworm", kind="base_image",
                                                     origin="distribution")):
        assert entry(**fields).name


@pytest.mark.parametrize("fields,why", [
    ({**PIP, "sha256": None}, "sha256"),
    ({"origin": "package_index"}, "comes from distribution"),
    ({**VENDOR, "url": None}, "needs url"),
    ({**VENDOR, "redistributable": True}, "licence-bound"),
    ({"name": "Libusb"}, "named as Debian names it"),
    ({"check": ["sh", "-c", "true"]}, "check only looks"),
    ({"check": ["curl", "https://x.invalid"]}, "check only looks"),
    ({"check": ["dpkg", "-s", "a b"]}, "plain words"),
    ({"check": ["dpkg", "-s", "x;rm"]}, "plain words"),
    ({"source": "somewhere"}, "String should match"),
    ({"version": "1 2"}, "String should match"),
    ({"url": "http://insecure.example.com"}, "String should match"),
])
def test_what_an_entry_refuses(fields, why):
    with pytest.raises(ValidationError) as refused:
        entry(**fields)
    assert why in str(refused.value)


def test_an_entry_without_a_source_is_a_named_gap():
    assert entry(source=None).is_gap and not entry().is_gap


def test_a_list_names_each_entry_once_per_kind():
    from orchestrator.schemas.target_template import TemplateVariables, gguf_format
    from orchestrator.Core.module_generator import format_problems
    values, problems = format_problems([gguf_format(["q4_0"])])
    with pytest.raises(ValidationError, match="each name once per kind"):
        TemplateVariables(formats=values, dependencies=[entry(), entry()])
    TemplateVariables(formats=values, dependencies=[entry(), entry(name="libusb-1.0-0", kind="board_driver",
                                                                   origin="distribution")])


# --- "is it there?" --------------------------------------------------------------------------------------------------

def test_the_states_of_the_list():
    seen = []

    def run(argv):
        seen.append(list(argv))
        return 0 if argv[-1] == "numpy" else 1

    rows = deps.statuses([entry(), entry(**PIP), entry(**VENDOR), entry(**DRIVER),
                          entry(name="zlib1g", check=[], source=None)], run)
    assert [r.state for r in rows] == ["missing", "present", "waiting", "board", "unchecked"]
    assert [r.gap for r in rows] == [False, False, False, False, True]
    assert seen == [["dpkg", "-s", "libusb-1.0-0"], ["pip", "show", "numpy"]], "a vendor file and a driver run nothing"
    assert "fetch it under your own licence acceptance from https://vendor.example.com/noe" in rows[2].why


def test_a_vendor_file_the_store_has_is_checked_like_any_entry():
    rows = deps.statuses([entry(**{**VENDOR, "check": ["test", "-f", "/opt/vendor/noe"]})], lambda argv: 0,
                         have_vendor_file=lambda e: True)
    assert rows[0].state == "present"


def test_a_check_with_a_program_that_is_not_allowed_is_not_run():
    bad = Dependency.model_construct(**{**entry().model_dump(), "check": ["rm", "-rf", "/"]})
    ran = []
    rows = deps.statuses([bad], lambda argv: ran.append(argv) or 0)
    assert rows[0].state == "unchecked" and ran == [] and "not a program a check may start" in rows[0].why


def test_the_table_counts_the_states():
    rows = deps.statuses([entry(), entry(**VENDOR)], lambda argv: 0)
    lines = deps.table(rows)
    assert lines[-1] == "2 entries: 1 there, 0 missing, 1 waiting for your yes, 0 without a source."
    assert deps.table([]) == []


def test_the_host_runner_runs_a_program_without_a_shell(tmp_path):
    present = tmp_path / "f"
    present.write_text("x")
    run = deps.HostRunner()
    if shutil.which("test"):
        assert run(["test", "-f", str(present)]) == 0 and run(["test", "-f", str(tmp_path / "none")]) == 1
    assert run(["no-such-program-xyz"]) == 127
    run(["test", f"-f;touch {tmp_path / 'made'}"])   # one odd word for the program, never a second command
    assert not (tmp_path / "made").exists()


def test_a_check_that_runs_too_long_is_timed_out(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("x", 1)
    monkeypatch.setattr(subprocess, "run", slow)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert deps.HostRunner()(["dpkg", "-s", "x"]) == 124
    assert deps.ImageRunner("img")(["dpkg", "-s", "x"]) == 124


def test_the_image_runner_asks_inside_the_image_with_no_network(monkeypatch):
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0))
    assert deps.ImageRunner("llm-framework/x")(["dpkg", "-s", "zlib1g"]) == 0
    argv, kw = calls[0]
    assert argv == ["/usr/bin/docker", "run", "--rm", "--network", "none", "--entrypoint", "dpkg", "llm-framework/x",
                    "-s", "zlib1g"] and kw["shell"] is False
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert deps.ImageRunner("img")(["dpkg"]) == 127


# --- the plan for the build image ------------------------------------------------------------------------------------

def test_the_plan_pins_apt_and_pip_and_never_installs_a_vendor_file_or_a_driver():
    plan = deps.install_plan([entry(), entry(**PIP), entry(**VENDOR), entry(**DRIVER), entry(name="zlib1g", source=None)])
    assert plan.packages == ["libusb-1.0-0=2:1.0.26-1", "zlib1g=2:1.0.26-1"]
    assert plan.pip_packages == ["numpy==2.1.0"] and plan.pip_hashes == {"numpy": sha("numpy")}
    assert [e.name for e in plan.waiting] == ["noe-sdk.tar.gz"] and [e.name for e in plan.board] == ["npu-driver"]
    assert [e.name for e in plan.gaps] == ["zlib1g"]


def test_the_plan_merges_into_the_module_once():
    plan = deps.install_plan([entry(), entry(**PIP)])
    data = deps.merge_into({"packages": ["cmake", "libusb-1.0-0"], "pip_packages": ["NumPy==2.1.0"]}, plan)
    assert data["packages"] == ["cmake", "libusb-1.0-0"] and data["pip_packages"] == ["NumPy==2.1.0"]
    assert data["pip_hashes"] == {"numpy": sha("numpy")}


def test_the_dockerfile_steps_are_pinned_and_hash_checked():
    block = vendor_blocks({"pip_packages": ["numpy==2.1.0", "plain==1"], "pip_hashes": {"numpy": sha("numpy")}})
    text = block["# [PIP_PACKAGES]"]
    assert f"'numpy==2.1.0 --hash=sha256:{sha('numpy')}'" in text
    assert "--no-deps --require-hashes" in text and "RUN python3 -m pip install --no-cache-dir 'plain==1'" in text
    assert "--require-hashes" not in vendor_blocks({"pip_packages": ["plain==1"]})["# [PIP_PACKAGES]"]


# --- the module made with them ---------------------------------------------------------------------------------------

DATA = {"module_name": "Deps_Board", "architecture": "aarch64", "sdk": "none", "base_os": "debian:bookworm-slim",
        "packages": ["cmake"], "cpu_flags": "", "cmake_flags": "", "supported_boards": []}


def test_a_module_made_with_dependencies_records_them_and_installs_the_checkable_ones(tmp_path):
    entries = [entry(), entry(**PIP), entry(**VENDOR), entry(**DRIVER)]
    folder = ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": [e.model_dump() for e in entries]})
    meta = yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8"))
    recorded = meta["template_variables"]["dependencies"]
    assert [d["name"] for d in recorded] == ["libusb-1.0-0", "numpy", "noe-sdk.tar.gz", "npu-driver"]
    assert "redistributable" not in recorded[0] and "note" not in recorded[0], "no defaults in the file"
    dockerfile = (folder / "Dockerfile").read_text(encoding="utf-8")
    assert "libusb-1.0-0=2:1.0.26-1" in dockerfile and f"numpy==2.1.0 --hash=sha256:{sha('numpy')}" in dockerfile
    assert "noe-sdk" not in dockerfile and "npu-driver" not in dockerfile
    assert template_variable_problems(folder) == []
    assert [e.name for e in deps.load(folder)] == [e.name for e in entries]


def test_a_module_takes_the_dependencies_as_yaml_text(tmp_path):
    text = yaml.safe_dump([entry().model_dump(exclude_defaults=True, mode="json")])
    folder = ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": text})
    assert "libusb-1.0-0=2:1.0.26-1" in (folder / "Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad,why", [
    ([{"name": "libusb"}], r"dependencies\[1\]\.version"),
    ("- name: [unclosed", "not valid YAML"),
    ([{**entry().model_dump(), "check": ["sh", "-c", "x"]}], "check only looks"),
])
def test_a_module_with_a_bad_entry_is_refused_and_nothing_is_made(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": bad})
    assert not any(tmp_path.iterdir()) or not (tmp_path / "Deps_Board").exists()


def test_the_folder_check_refuses_an_invalid_entry_edited_into_target_yml(tmp_path):
    folder = ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": [entry().model_dump()]})
    path = folder / "target.yml"
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta["template_variables"]["dependencies"][0]["check"] = ["bash", "-c", "x"]
    path.write_text(yaml.safe_dump(meta), encoding="utf-8")
    assert any("check only looks" in p for p in template_variable_problems(folder))


# --- the command -----------------------------------------------------------------------------------------------------

def _cli(monkeypatch, tmp_path, *args, input=None):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli_module, "_target_folders", lambda ctx: (tmp_path, [tmp_path]))
    return CliRunner().invoke(cli_module.cli, ["-q", *args], input=input)


def test_the_command_lists_each_entry_with_its_state_and_exits_1_when_something_is_missing(monkeypatch, tmp_path):
    # a check names a program of the Linux build image (a POSIX path, no backslash), so the host's own programs are
    # replaced: on a Windows host there is no `test`
    monkeypatch.setattr(deps, "HostRunner", lambda: (lambda argv: 0))
    folder = ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": [
        entry(check=["test", "-e", "/tmp"]).model_dump(), entry(**VENDOR).model_dump()]})
    result = _cli(monkeypatch, tmp_path, "module", "deps", folder.name)
    assert result.exit_code == 1, result.output
    assert "libusb-1.0-0 2:1.0.26-1 (system package): there" in result.output
    assert "noe-sdk.tar.gz 1.2 (vendor file): waits for your yes" in result.output
    assert "2 entries: 1 there, 0 missing, 1 waiting for your yes, 0 without a source." in result.output


def test_the_command_says_so_for_a_module_without_dependencies_and_for_an_invalid_list(monkeypatch, tmp_path):
    folder = ModuleGenerator(tmp_path).generate_module(DATA)
    assert "lists no dependencies" in _cli(monkeypatch, tmp_path, "module", "deps", folder.name).output
    path = folder / "target.yml"
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta["template_variables"]["dependencies"] = [{"name": "x"}]
    path.write_text(yaml.safe_dump(meta), encoding="utf-8")
    result = _cli(monkeypatch, tmp_path, "module", "deps", folder.name)
    assert result.exit_code == 2 and "dependencies[1]" in result.output


def test_module_create_takes_a_dependencies_file(monkeypatch, tmp_path):
    from orchestrator import cli as cli_module
    got = []

    class Recorder:
        def __init__(self, targets_dir, *a, **k):
            pass

        def generate_module(self, data, profile=None):
            got.append(data)
            return tmp_path / data["module_name"]

    monkeypatch.setattr(cli_module, "ModuleGenerator", Recorder)
    file = tmp_path / "deps.yml"
    file.write_text(yaml.safe_dump([entry().model_dump(exclude_defaults=True, mode="json")]), encoding="utf-8")
    result = _cli(monkeypatch, tmp_path, "module", "create", "--name", "MyBoard", "--arch", "aarch64", "--sdk", "None",
                  "--dependencies", str(file), input="\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert "libusb-1.0-0" in got[0]["dependencies"]


# --- the wizard's list ---------------------------------------------------------------------------------------------

def test_the_edit_step_lists_the_dependencies_and_checks_them(qapp, tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.Core.module_edit import ModuleEdit
    from orchestrator.gui.module_edit import ModuleEditPanel
    folder = ModuleGenerator(tmp_path).generate_module({**DATA, "dependencies": [
        entry().model_dump(), entry(**PIP).model_dump(), entry(**VENDOR).model_dump(), entry(name="zlib1g",
                                                                                           source=None).model_dump()]})
    panel = ModuleEditPanel(None)
    panel.load(ModuleEdit(folder))
    assert panel.deps_box.isVisibleTo(panel) and panel.deps_list.count() == 4
    assert "not checked yet" in panel.deps_list.item(0).text() and "a gap, it has no source" in panel.deps_list.item(3).text()
    panel.deps_runner = lambda argv: 0 if argv[-1] == "numpy" else 1
    panel.check_dependencies(wait=True)
    texts = [panel.deps_list.item(i).text() for i in range(4)]
    assert "missing" in texts[0] and "there" in texts[1] and "waits for your yes" in texts[2]
    assert panel.lbl_deps.text() == "4 entries: 1 there, 2 missing, 1 waiting for your yes, 1 without a source."
    assert panel.btn_deps_check.objectName() == "btn_module_deps_check"


def test_a_module_without_dependencies_has_no_box(qapp, tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.Core.module_edit import ModuleEdit
    from orchestrator.gui.module_edit import ModuleEditPanel
    panel = ModuleEditPanel(None)
    folder = ModuleGenerator(tmp_path).generate_module(DATA)
    panel.load(ModuleEdit(folder))
    assert not panel.deps_box.isVisibleTo(panel)
