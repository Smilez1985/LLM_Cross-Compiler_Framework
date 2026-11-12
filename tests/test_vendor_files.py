"""The guided fetch of vendor files and toolchains (orchestrator/Core/vendor_files.py, `llm-cli vendor`, Build >
Targets... > Vendor Files...): a file a format names that may not be passed on is fetched by the person under their own
licence acceptance. The licence text is shown in full; only the person's own yes in the GUI or at a terminal accepts it
(never an AI, never a run without a terminal); the acceptance is recorded with the text's sha256; the file is checked
by its sha256 and kept in the person's own vendor store, never in the module; a build stages it into the build image
from there and stops, naming the fetch, when it is missing."""

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import POSIX_MODES_ONLY
from orchestrator.Core import vendor_files as vf
from test_general_template import generate, npu_format

SDK = b"vendor sdk wheel bytes, not to be passed on\n"
SDK_SHA = hashlib.sha256(SDK).hexdigest()
LICENCE = "VENDOR SOFTWARE LICENCE\nYou may use this file. You may not pass it on.\n"


def vendor_module(tmp_path, **file_overrides):
    files = [dict({"name": "vendor_sdk-1.0-py3-none-any.whl", "url": "https://vendor.example.com/downloads",
                   "licence_url": "https://vendor.example.com/licence", "purpose": "the compiler of the format",
                   "sha256": SDK_SHA, "dest": "/opt/vendor/vendor_sdk-1.0-py3-none-any.whl"}, **file_overrides),
             {"name": "board-runtime.tar", "url": "https://vendor.example.com/runtime",
              "licence_url": "https://vendor.example.com/licence"}]
    return generate(tmp_path, formats=[npu_format(vendor_files=files)])


def fetch_text(url):
    assert url == "https://vendor.example.com/licence"
    return LICENCE


@pytest.fixture
def module(tmp_path):
    return vendor_module(tmp_path)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state-vendor"


def downloaded(tmp_path, data=SDK, name="download.whl"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- the licence: shown in full, accepted only by the person ---------------------------------------------------------

def test_the_licence_is_shown_and_only_the_persons_yes_accepts_it(module, state):
    asked = vf.question(module, "vendor_sdk-1.0-py3-none-any.whl", fetch_text)
    assert asked.text == LICENCE and asked.text_sha256 == hashlib.sha256(LICENCE.encode()).hexdigest()
    facts = "\n".join(asked.facts())
    assert "download page: https://vendor.example.com/downloads" in facts and "never passes this file on" in facts
    for actor in ("ditto", "mcp", "acp", "ai client"):
        with pytest.raises(PermissionError, match="an AI never accepts a licence"):
            vf.accept(state, asked, actor=actor, accepted=True)
    with pytest.raises(vf.VendorProblem, match="was not accepted"):
        vf.accept(state, asked, actor="cli", accepted=False)
    assert vf.acceptances(state) == {}
    record = vf.accept(state, asked, actor="gui", accepted=True)
    assert record["channel"] == "gui" and record["licence_sha256"] == asked.text_sha256 and record["accepted"]
    entry = vf.find(module, "vendor_sdk-1.0-py3-none-any.whl")
    assert vf.acceptance(state, entry) is not None
    assert vf.acceptance(state, entry, licence_sha256="0" * 64) is None, "a changed licence text is asked again"


def test_without_a_licence_text_nothing_can_be_accepted(module, tmp_path):
    with pytest.raises(vf.VendorProblem, match="nothing to accept"):
        vf.question(module, "vendor_sdk-1.0-py3-none-any.whl", None)
    (module / "licences").mkdir()
    (module / "licences" / "vendor.txt").write_text("THE LICENCE IN THE TARGET FOLDER\n")
    meta = yaml.safe_load((module / "target.yml").read_text())
    entry = vf.VendorEntry("t", "fakenpu", vf.find(module, "vendor_sdk-1.0-py3-none-any.whl").file.model_copy(
        update={"licence_file": "licences/vendor.txt"}))
    assert vf.licence_text(module, entry) == "THE LICENCE IN THE TARGET FOLDER\n"
    assert meta["template_variables"]["formats"][0]["vendor_files"][0]["name"] == "vendor_sdk-1.0-py3-none-any.whl"
    with pytest.raises(vf.VendorProblem, match="could not be read"):
        vf.question(module, "vendor_sdk-1.0-py3-none-any.whl", lambda url: (_ for _ in ()).throw(OSError("offline")))


# --- the file: checked by its sha256, kept in the person's store -----------------------------------------------------

def test_the_file_needs_the_acceptance_and_its_sha256(module, state, tmp_path):
    name = "vendor_sdk-1.0-py3-none-any.whl"
    with pytest.raises(vf.VendorProblem, match="not accepted yet") as e:
        vf.add_file(state, module, name, downloaded(tmp_path), actor="cli")
    assert [s.id for s in e.value.steps] == ["build.vendor_fetch"]
    vf.accept(state, vf.question(module, name, fetch_text), actor="cli", accepted=True)
    with pytest.raises(PermissionError):
        vf.add_file(state, module, name, downloaded(tmp_path), actor="ditto")
    with pytest.raises(vf.VendorProblem, match="not the .* the target names") as e:
        vf.add_file(state, module, name, downloaded(tmp_path, b"another file", "other.whl"), actor="cli")
    assert e.value.code == "mismatch"
    assert sorted(p.name for p in vf.store_dir(state).iterdir()) == ["acceptances.json"], "nothing of it is kept"
    kept = vf.add_file(state, module, name, downloaded(tmp_path), actor="cli")
    assert kept.sha256 == SDK_SHA and kept.path == vf.store_dir(state) / SDK_SHA and kept.path.read_bytes() == SDK
    assert vf.stored(state, vf.find(module, name)) == kept.path
    assert not any(SDK in p.read_bytes() for p in module.rglob("*") if p.is_file()), "never in the module"


@POSIX_MODES_ONLY
def test_the_kept_file_is_private(module, state, tmp_path):
    name = "vendor_sdk-1.0-py3-none-any.whl"
    vf.accept(state, vf.question(module, name, fetch_text), actor="cli", accepted=True)
    kept = vf.add_file(state, module, name, downloaded(tmp_path), actor="cli")
    assert stat.S_IMODE(kept.path.stat().st_mode) == 0o600


def test_without_a_published_sha256_the_person_confirms_the_files_own(module, state, tmp_path):
    name = "board-runtime.tar"
    vf.accept(state, vf.question(module, name, fetch_text), actor="gui", accepted=True)
    with pytest.raises(vf.VendorProblem, match="did not confirm"):
        vf.add_file(state, module, name, downloaded(tmp_path, b"runtime"), actor="gui", confirm_sha256=lambda d: False)
    with pytest.raises(vf.VendorProblem, match="did not confirm"):
        vf.add_file(state, module, name, downloaded(tmp_path, b"runtime"), actor="gui")
    seen = []
    kept = vf.add_file(state, module, name, downloaded(tmp_path, b"runtime"), actor="gui",
                       confirm_sha256=lambda d: seen.append(d) or True)
    assert seen == [hashlib.sha256(b"runtime").hexdigest()] == [kept.sha256]
    index = json.loads((vf.store_dir(state) / vf.INDEX).read_text())
    assert index[f"{vf.target_id(module)}/{name}"]["confirmed_by_person"] is True
    row = next(r for r in vf.status(state, module) if r["name"] == name)
    assert row["in_store"] and row["accepted"] and row["for"] == "board"


def test_llmcf_downloads_only_a_direct_pinned_file_after_the_acceptance(tmp_path, state):
    module = vendor_module(tmp_path, direct=True, size=len(SDK), url="https://vendor.example.com/files/sdk.whl")
    name = "vendor_sdk-1.0-py3-none-any.whl"

    class Downloader:
        def __init__(self):
            self.calls = []

        def fetch(self, url, dest, size, sha256, progress=None):
            self.calls.append((url, size, sha256))
            dest.write_bytes(SDK)
            return dest
    downloader = Downloader()
    with pytest.raises(vf.VendorProblem, match="not accepted yet"):
        vf.download(state, module, name, actor="cli", downloader=downloader)
    vf.accept(state, vf.question(module, name, fetch_text), actor="cli", accepted=True)
    with pytest.raises(PermissionError):
        vf.download(state, module, name, actor="ditto", downloader=downloader)
    kept = vf.download(state, module, name, actor="cli", downloader=downloader)
    assert downloader.calls == [("https://vendor.example.com/files/sdk.whl", len(SDK), SDK_SHA)]
    assert kept.path.read_bytes() == SDK
    vf.accept(state, vf.question(module, "board-runtime.tar", fetch_text), actor="cli", accepted=True)
    with pytest.raises(vf.VendorProblem, match="behind the vendor's page"):
        vf.download(state, module, "board-runtime.tar", actor="cli", downloader=downloader)


def test_a_direct_file_must_be_pinned():
    from orchestrator.schemas.target_template import VendorFile
    with pytest.raises(ValueError, match="direct needs its sha256 and size"):
        VendorFile(name="x.bin", url="https://vendor.example.com/x.bin", licence_url="https://vendor.example.com/l",
                   direct=True)


# --- the build stages it from the store --------------------------------------------------------------------------

def test_the_build_stages_the_file_into_the_image_and_stops_without_it(module, state, tmp_path):
    name = "vendor_sdk-1.0-py3-none-any.whl"
    context = tmp_path / "context"
    with pytest.raises(vf.VendorProblem, match="not in your vendor store yet") as e:
        vf.stage(state, module, context)
    assert e.value.code == "needs_person" and [s.id for s in e.value.steps] == ["build.vendor_fetch"]
    assert not context.exists()
    vf.accept(state, vf.question(module, name, fetch_text), actor="cli", accepted=True)
    kept = vf.add_file(state, module, name, downloaded(tmp_path), actor="cli")
    logged = []
    assert vf.stage(state, module, context, log=logged.append) == 1, "the board's file is not staged"
    staged = context / "sources" / "image-files" / "opt" / "vendor" / name
    assert staged.read_bytes() == SDK and "never passed on" in logged[0]
    kept.path.write_bytes(b"changed in the store")
    with pytest.raises(vf.VendorProblem, match="no longer matches"):
        vf.stage(state, module, tmp_path / "context2")


def test_the_build_engine_stages_vendor_files_before_the_image(module, state, tmp_path):
    from orchestrator.Core.builder import BuildEngine
    engine = SimpleNamespace(resolve_target_dir=lambda name: module, config=SimpleNamespace(user_state_dir=state))
    progress = SimpleNamespace(add_log=lambda line: None)
    dockerfile = tmp_path / "ctx" / "Dockerfile"
    dockerfile.parent.mkdir()
    with pytest.raises(vf.VendorProblem, match="vendor files that are not in your vendor store"):
        BuildEngine._stage_vendor_files(engine, SimpleNamespace(target_arch="x"), progress, dockerfile)
    name = "vendor_sdk-1.0-py3-none-any.whl"
    vf.accept(state, vf.question(module, name, fetch_text), actor="cli", accepted=True)
    vf.add_file(state, module, name, downloaded(tmp_path), actor="cli")
    BuildEngine._stage_vendor_files(engine, SimpleNamespace(target_arch="x"), progress, dockerfile)
    assert (dockerfile.parent / "sources" / "image-files" / "opt" / "vendor" / name).is_file()


def test_the_preload_never_carries_a_vendor_file(module):
    from orchestrator.Core import build_preload
    data = build_preload.recipe(SimpleNamespace(sources=[]), "t", module, "")
    assert any("vendor file vendor_sdk-1.0-py3-none-any.whl" in line and "no preload carries it" in line
               for line in data["not_covered"])


# --- the command line ----------------------------------------------------------------------------------------------

def test_vendor_fetch_refuses_without_a_terminal_and_takes_the_persons_yes(module, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(module.parent))
    monkeypatch.setattr(vf, "default_fetch_text", fetch_text)
    name = "vendor_sdk-1.0-py3-none-any.whl"
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "fetch", module.name, name], input="y\n")
    assert result.exit_code == 2 and "only by your own answer at a terminal" in result.output
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "fetch", module.name, name], input="n\n")
    assert result.exit_code == 1 and "Not accepted" in result.output and LICENCE.splitlines()[1] in result.output
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "list", module.name, "--json"])
    rows = json.loads(result.output)["vendor_files"]
    assert [r["accepted"] for r in rows] == ["", ""]
    bad = downloaded(tmp_path, b"not the file", "bad.whl")
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "fetch", module.name, name, "--file", str(bad)],
                                input="y\n")
    assert result.exit_code == 1 and "not the" in result.output
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "add", module.name, name, str(downloaded(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "is in your vendor store" in result.output
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "list", module.name])
    assert "licence accepted" in result.output and "; in your vendor store" in result.output
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "fetch", module.name, "board-runtime.tar"],
                                input="y\n")
    assert result.exit_code == 0 and "llm-cli vendor add" in result.output and "https://vendor.example.com/runtime" \
        in result.output


def test_vendor_add_without_a_terminal_refuses_an_unpublished_sha256(module, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from orchestrator.Core.config_manager import ConfigManager
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(module.parent))
    state = Path(ConfigManager().user_state_dir)
    vf.accept(state, vf.question(module, "board-runtime.tar", fetch_text), actor="cli", accepted=True)
    result = CliRunner().invoke(cli_module.cli, ["-q", "vendor", "add", module.name, "board-runtime.tar",
                                                 str(downloaded(tmp_path, b"rt"))])
    assert result.exit_code == 1 and "did not confirm" in result.output
    assert not (vf.store_dir(state) / hashlib.sha256(b"rt").hexdigest()).exists()


# --- AI tools read the facts, never act ------------------------------------------------------------------------------

def test_ai_tools_read_the_vendor_facts(make_framework, tmp_path):
    import shutil
    from conftest import REPO_ROOT
    from orchestrator.tools.policy import ToolPolicy
    from orchestrator.tools.registry import ToolContext
    targets = tmp_path / "targets"
    shutil.copytree(REPO_ROOT / "targets", targets)
    (tmp_path / "made").mkdir()
    made = vendor_module(tmp_path / "made")
    shutil.copytree(made, targets / made.name)
    fm = make_framework(initialize=False)
    fm.config.set("targets_dir", str(targets))
    assert fm.initialize()
    fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                     reviewed=[s.name for s in fm.tool_registry.list()]))
    ctx = ToolContext("ditto", session="s")
    out = fm.tool_registry.call("get_target_capabilities", {"target": made.name}, ctx)
    data = out.model_dump() if hasattr(out, "model_dump") else out
    rows = data["vendor_files"]
    assert [r["name"] for r in rows] == ["vendor_sdk-1.0-py3-none-any.whl", "board-runtime.tar"]
    assert rows[0]["used_for"] == "build image" and rows[0]["licence_accepted"] is False
    names = {s.name for s in fm.tool_registry.list()}
    assert not any("vendor" in n or "licence" in n for n in names), "no tool accepts a licence or fetches a file"
    assert os.environ.get("LLM_FRAMEWORK_HOME")
