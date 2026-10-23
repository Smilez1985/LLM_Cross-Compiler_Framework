""""Use" of an identical earlier build (DECISIONS round 23, item 9): `llm-cli build start` asks the Builder tab's
question about a finished build with 100 % the same options, and "Use" copies that build into the chosen folder
(--output, the prompt's answer or the output folder; in the GUI the folder dialog at the output folder), in the CLI and
the GUI alike: artifact_output.plan_copy and copy_artifact, the one disk rule before the copy, every copy checked by
sha256, the earlier build left where it is, and a question with the full list ("No" is the default) before anything
under that name is replaced."""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import artifact_output as ao
from orchestrator.Core import build_identity as bi
from test_artifact_output import GB, HOST_SYSTEM_FOLDER, Cfg, finished_build

OPTIONS = {bi.MANIFEST_KEY: {"version": bi.OPTIONS_VERSION, "model": "org/m", "revision": "a" * 40}}


@pytest.fixture
def fw(tmp_path):
    return SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(str(p)).expanduser())


@pytest.fixture
def earlier(tmp_path):
    """A finished build in the output folder (as list_artifacts labels it: output:Rockchip/m/Q4_K_M)."""
    return finished_build(tmp_path / "out", **OPTIONS)


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the core step --------------------------------------------------------------------------------------------------

def test_use_copies_the_build_and_its_zip_and_leaves_the_earlier_one(fw, tmp_path, earlier):
    plan = ao.plan_copy(fw, earlier, tmp_path / "other", relative="Rockchip/m/Q4_K_M", create=True)
    assert plan.target == (tmp_path / "other" / "Rockchip" / "m" / "Q4_K_M").resolve() or \
        plan.target == tmp_path.resolve() / "other" / "Rockchip" / "m" / "Q4_K_M"
    assert not plan.there and plan.replaces is None and plan.needed > 0
    result = ao.copy_artifact(fw, plan)
    copy = Path(result.folder)
    assert sha(copy / "m.gguf") == sha(earlier / "m.gguf") and (copy / "build_info.json").is_file()
    assert result.zip is not None and Path(result.zip).read_bytes() == b"zip"
    assert (earlier / "m.gguf").is_file() and (earlier.parent / "Q4_K_M.zip").is_file(), "the earlier build stays"
    listed = [str(item.path.resolve()) for item in ao.list_artifacts(fw)]
    assert str(copy.resolve()) in listed, "a copy outside the output folder stays listed"


def test_the_identical_build_there_already_is_not_copied_again(fw, tmp_path, earlier):
    assert ao.plan_copy(fw, earlier, tmp_path / "out", relative="Rockchip/m/Q4_K_M").there, "it lies there itself"
    elsewhere = finished_build(tmp_path / "disk2", **OPTIONS)
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    assert plan.there and plan.replaces is None
    before = sorted(p.name for p in elsewhere.parent.iterdir())
    result = ao.copy_artifact(fw, plan)
    assert result.already and sorted(p.name for p in elsewhere.parent.iterdir()) == before


@pytest.mark.parametrize("damage", ["model file gone", "model file changed"])
def test_a_folder_whose_model_file_is_gone_or_changed_is_not_the_identical_build(fw, tmp_path, earlier, damage):
    """Review cs-cli-gui-v: the identical build "there already" is told from its files too, not from its manifest
    alone: a folder that still holds the same build_info.json but whose model file was deleted or changed is no
    identical build. Use then asks before replacing it, and never says that nothing needs copying."""
    elsewhere = finished_build(tmp_path / "disk2", **OPTIONS)
    if damage == "model file gone":
        (elsewhere / "m.gguf").unlink()
    else:
        (elsewhere / "m.gguf").write_bytes(b"weights, but changed afterwards")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    assert not plan.there, "it does not hold the earlier build's model file"
    assert plan.replaces is not None and plan.replaces.items[0].full_path == str(plan.target)


def test_another_build_under_the_name_is_replaced_only_with_the_persons_yes(fw, tmp_path, earlier):
    from orchestrator.Core.deletion import accept
    other = finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    assert not plan.there and plan.replaces is not None
    assert [i.full_path for i in plan.replaces.items] == [str(plan.target), str(plan.target) + ".zip"]
    assert all("/" not in i.speaking_name.split(":", 1)[0] for i in plan.replaces.items)
    with pytest.raises(ao.DestinationRefused) as refused:
        ao.copy_artifact(fw, plan)
    assert refused.value.code == "needs_person" and (other / "m.gguf").read_bytes() == b"other weights"
    with pytest.raises(PermissionError):
        ao.copy_artifact(fw, plan, replace=object(), audit=Audit())
    with pytest.raises(ao.DestinationRefused, match="audit log"):
        ao.copy_artifact(fw, plan, replace=accept(plan.replaces, "cli", "permanent"))
    assert not any(p.name.startswith("Q4_K_M-") for p in plan.target.parent.iterdir()), "nothing copied without it"
    audit = Audit()
    result = ao.copy_artifact(fw, plan, replace=accept(plan.replaces, "cli", "permanent"), audit=audit)
    assert Path(result.folder) == plan.target and sha(plan.target / "m.gguf") == sha(earlier / "m.gguf")
    assert (plan.target.parent / "Q4_K_M.zip").read_bytes() == b"zip"
    assert not any(p.name.startswith("Q4_K_M-") for p in plan.target.parent.iterdir())
    ((args, kwargs),) = audit.rows
    assert args[0] == "cli" and kwargs["arguments"]["mode"] == "permanent" and kwargs["approved"] is True


def test_a_replacement_into_the_trash_can_be_restored(fw, tmp_path, earlier):
    from orchestrator.Core.deletion import accept
    from orchestrator.Core.trash import Trash
    finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    trash = Trash(Path(fw.config.user_state_dir))
    result = ao.copy_artifact(fw, plan, replace=accept(plan.replaces, "gui", "trash"), trash=trash, audit=Audit())
    assert Path(result.folder) == plan.target and sha(plan.target / "m.gguf") == sha(earlier / "m.gguf")
    entries = trash.entries()
    assert len(entries) == 2 and all(e.kind == "artifact" and e.confirmed_by == "gui" for e in entries)


def test_an_incomplete_copy_replaces_nothing(fw, tmp_path, earlier):
    """Review cs-cli-gui-v: what lies under the name is replaced only once the copy is complete; a copy that left
    something out (a link in the earlier build: links are never followed) keeps its free name, and what lay there
    stays."""
    from orchestrator.Core.deletion import accept
    try:
        (earlier / "tokenizer.json").symlink_to(earlier / "Model_Card.md")
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a link")
    other = finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    audit = Audit()
    result = ao.copy_artifact(fw, plan, replace=accept(plan.replaces, "cli", "permanent"), audit=audit)
    assert (other / "m.gguf").read_bytes() == b"other weights", "what lay there stays"
    assert (other.parent / "Q4_K_M.zip").read_bytes() == b"old zip"
    assert Path(result.folder).name == "Q4_K_M-2" and sha(Path(result.folder) / "m.gguf") == sha(earlier / "m.gguf")
    assert audit.rows == [], "nothing was replaced, so nothing is recorded as replaced"
    assert any("tokenizer.json" in n for n in result.notes) and any("nothing was replaced" in n for n in result.notes)


def test_a_list_that_changed_after_the_question_replaces_nothing(fw, tmp_path, earlier):
    from orchestrator.Core.deletion import accept
    other = finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    accepted = accept(plan.replaces, "cli", "permanent")
    (other / "m.gguf").write_bytes(b"changed meanwhile, now longer")
    result = ao.copy_artifact(fw, plan, replace=accepted, audit=Audit())
    assert (other / "m.gguf").read_bytes() == b"changed meanwhile, now longer", "what was not accepted stays"
    assert Path(result.folder).name == "Q4_K_M-2" and any("changed" in n for n in result.notes)


def test_the_one_disk_rule_comes_before_the_copy(fw, tmp_path, earlier, monkeypatch):
    monkeypatch.setattr(ao, "_disk_usage", lambda p: shutil._ntuple_diskusage(100 * GB, 99 * GB, 1 * GB))
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M", create=True)
    with pytest.raises(ao.DestinationRefused) as refused:
        ao.copy_artifact(fw, plan)
    assert refused.value.code == "insufficient_resources" and not (tmp_path / "disk2" / "Rockchip").exists()


def test_a_refused_folder_or_a_folder_inside_the_build_copies_nothing(fw, tmp_path, earlier):
    with pytest.raises(ao.DestinationRefused, match="system folder"):
        ao.plan_copy(fw, earlier, HOST_SYSTEM_FOLDER)
    with pytest.raises(ao.DestinationRefused, match="inside each other"):
        ao.plan_copy(fw, earlier, earlier, relative="Rockchip/m/Q4_K_M")
    with pytest.raises(ao.DestinationRefused, match="no longer there"):
        ao.plan_copy(fw, tmp_path / "gone", tmp_path)
    plan = ao.plan_copy(fw, earlier, tmp_path / "x", relative="../../escape", create=True)
    assert plan.target.parent == (tmp_path / "x").resolve(), "a relative path never leaves the folder"


# --- llm-cli build start --------------------------------------------------------------------------------------------

@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch, earlier):
    from orchestrator.Core.build_service import BuildService
    from test_review_capabilities_paths import make_cli
    found = [bi.EarlierBuild("a_" + "1" * 16, "output:Rockchip/m/Q4_K_M", {"path": str(earlier),
                                                                          "created": "2026-10-15"}, "identical")]
    monkeypatch.setattr(BuildService, "earlier_builds", lambda self, request, **k: found)
    runner = make_cli(booted_framework, tmp_path, monkeypatch)
    runner.answers = {}

    from orchestrator import cli as cli_module

    def prompt(text, *a, **k):
        for key, answer in runner.answers.items():
            if key in text:
                return answer
        raise AssertionError(f"unexpected question: {text}")

    monkeypatch.setattr(cli_module.click, "prompt", prompt)
    monkeypatch.setattr(cli_module.click, "confirm", lambda text, default=False, **k: runner.answers.get("confirm",
                                                                                                         default))
    return runner


def build(cli, *args):
    from test_review_capabilities_paths import HF_MODEL
    return cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", *args)


def interactive(monkeypatch, on=True):
    from orchestrator import cli as cli_module
    monkeypatch.setattr(cli_module, "_interactive", lambda: on)


def test_use_at_the_terminal_copies_into_the_output_and_starts_nothing(cli, monkeypatch, tmp_path, earlier):
    interactive(monkeypatch)
    cli.answers = {"u = ": "u"}
    result = build(cli, "-o", str(tmp_path / "chosen"))
    assert result.code == 0, result.out
    assert cli.submitted == [] and "Identical build" in result.out and str(earlier) in result.out
    copy = tmp_path / "chosen" / "Rockchip" / "m" / "Q4_K_M"
    assert sha(copy / "m.gguf") == sha(earlier / "m.gguf") and "is copied to" in result.out
    assert (earlier / "m.gguf").is_file()


@pytest.mark.parametrize("answer, started, words", [("a", True, ""), ("c", False, "Nothing was started")])
def test_build_again_or_cancel(cli, monkeypatch, tmp_path, answer, started, words):
    interactive(monkeypatch)
    cli.answers = {"u = ": answer}
    result = build(cli, "-o", str(tmp_path / "chosen"))
    assert result.code == 0, result.out
    assert bool(cli.submitted) == started and words in result.out
    assert not (tmp_path / "chosen" / "Rockchip").exists()


def test_without_a_terminal_the_build_starts_as_given(cli, monkeypatch, tmp_path, earlier):
    interactive(monkeypatch, False)
    result = build(cli, "-o", str(tmp_path / "chosen"))
    assert result.code == 0, result.out
    assert len(cli.submitted) == 1 and str(earlier) in result.out and "nothing is asked" in result.out


def test_use_with_a_dash_names_the_build_and_copies_nothing(cli, monkeypatch, tmp_path, earlier):
    interactive(monkeypatch)
    cli.answers = {"u = ": "u"}
    result = build(cli, "-o", "-")
    assert result.code == 0 and cli.submitted == [] and str(earlier) in result.out
    assert "the existing build is used" in result.out


def test_use_asks_before_replacing_another_build(cli, monkeypatch, tmp_path, earlier):
    interactive(monkeypatch)
    other = finished_build(tmp_path / "chosen", payload=b"other weights", zip_payload=b"old zip")
    cli.answers = {"u = ": "u", "trash or delete": "permanent", "confirm": False}
    declined = build(cli, "-o", str(tmp_path / "chosen"))
    assert declined.code == 0, declined.out
    assert "Nothing was copied or replaced" in declined.out and (other / "m.gguf").read_bytes() == b"other weights"
    assert str(other) in declined.out, "the full list names what would be replaced"
    cli.answers["confirm"] = True
    accepted = build(cli, "-o", str(tmp_path / "chosen"))
    assert accepted.code == 0, accepted.out
    assert sha(other / "m.gguf") == sha(earlier / "m.gguf") and cli.submitted == [], accepted.out


# --- the GUI ----------------------------------------------------------------------------------------------------

def found_for(earlier):
    return bi.EarlierBuild("a_" + "1" * 16, "output:Rockchip/m/Q4_K_M", {"path": str(earlier)}, "identical")


@pytest.mark.usefixtures("qapp")
class TestUseInTheGui:

    def wait(self, worker):
        assert worker.wait(10000)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def test_use_copies_where_the_dialog_points_and_cancel_copies_nothing(self, fw, tmp_path, earlier,
                                                                           monkeypatch):
        from orchestrator.gui import artifact_save
        asked, done = [], []
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: asked.append(start) or "")
        assert artifact_save.ask_and_use(None, fw, found_for(earlier), lambda ok, t: done.append((ok, t))) is None
        assert asked == [str(tmp_path / "out")] and done == [], "cancel: nothing copied, nothing more said"
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "disk2"))
        (tmp_path / "disk2").mkdir()
        worker = artifact_save.ask_and_use(None, fw, found_for(earlier), lambda ok, t: done.append((ok, t)))
        self.wait(worker)
        copy = tmp_path / "disk2" / "Rockchip" / "m" / "Q4_K_M"
        assert done[-1][0] is True and str(copy) in done[-1][1] and sha(copy / "m.gguf") == sha(earlier / "m.gguf")
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "out"))
        assert artifact_save.ask_and_use(None, fw, found_for(earlier), lambda ok, t: done.append((ok, t))) is None
        assert done[-1][0] is True and "already" in done[-1][1]

    def test_replacing_needs_the_deletion_dialogs_yes(self, fw, tmp_path, earlier, monkeypatch):
        from orchestrator.Core.deletion import accept
        from orchestrator.gui import artifact_save, deletion_dialog
        other = finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
        fw.tool_registry = SimpleNamespace(audit=Audit())
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "disk2"))
        shown, done = [], []
        monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **k: shown.append(plan) or None)
        assert artifact_save.ask_and_use(None, fw, found_for(earlier), lambda ok, t: done.append((ok, t))) is None
        assert [i.full_path for i in shown[0].items][0] == str(other.resolve()) or \
            shown[0].items[0].full_path.endswith("Q4_K_M")
        assert done[-1][0] is False and "Nothing was copied or replaced" in done[-1][1]
        assert (other / "m.gguf").read_bytes() == b"other weights"
        monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **k: accept(plan, "gui",
                                                                                              "permanent"))
        worker = artifact_save.ask_and_use(None, fw, found_for(earlier), lambda ok, t: done.append((ok, t)))
        self.wait(worker)
        assert done[-1][0] is True and sha(other / "m.gguf") == sha(earlier / "m.gguf")
        assert len(fw.tool_registry.audit.rows) == 1

    def test_the_builder_tabs_use_starts_the_copy(self, booted_framework, tmp_path, earlier, monkeypatch):
        from orchestrator.gui import artifact_save
        from test_review_capabilities_paths import make_tab, select
        tab = make_tab(booted_framework)
        select(tab.format_combo, "gguf")
        started = []
        monkeypatch.setattr(artifact_save, "ask_earlier", lambda parent, found, lang=None: "use")
        monkeypatch.setattr(artifact_save, "ask_and_use", lambda parent, framework, earlier_build, on_done:
                            started.append(earlier_build) or None)
        assert tab._decide_about_earlier([found_for(earlier)]) is False
        assert started and started[0].info["path"] == str(earlier)


def test_the_real_audit_log_takes_the_replacement_and_the_old_downloads_deletion(fw, tmp_path, earlier):
    """The audit log refuses outcomes it does not know: the replacement of "Use" and the deletion of old Hugging
    Face downloads (hf_takeover, which only fake audit logs had recorded so far) are known outcomes."""
    from orchestrator.Core.deletion import accept
    from orchestrator.tools.audit import AuditLog
    log = AuditLog(tmp_path / "audit.db")
    log.record("cli", "hf_take_over", "framework", "person", "old_downloads_deleted", approved=True)
    finished_build(tmp_path / "disk2", payload=b"other weights", zip_payload=b"old zip")
    plan = ao.plan_copy(fw, earlier, tmp_path / "disk2", relative="Rockchip/m/Q4_K_M")
    result = ao.copy_artifact(fw, plan, replace=accept(plan.replaces, "cli", "permanent"), audit=log)
    assert Path(result.folder) == plan.target and result.notes == []
    assert sha(plan.target / "m.gguf") == sha(earlier / "m.gguf")
