"""The "edit all files" step of the module wizard (Core/module_edit.py, gui/module_edit.py, `llm-cli module create
--edit`, `llm-cli module edit`): the files it lists (never the stage runner), the diff of an edit, the checks that
refuse Finish and Save, an AI's proposal that is never saved without the person, and an existing module opened in the
same step."""
from pathlib import Path

import pytest

from orchestrator.Core.module_edit import (EditRefused, ModuleEdit, RUNNER_FILES, commit_staged, discard_staged,
                                           editable_files, stage_folder)
from orchestrator.Core.module_generator import ModuleGenerator

STAGES = ["modules/source_module.sh", "modules/config_module.sh", "modules/convert_module.sh",
          "modules/target_module.sh"]
DATA = {"module_name": "Edit_Board", "architecture": "aarch64", "sdk": "none", "base_os": "debian:bookworm-slim",
        "packages": ["cmake"], "cpu_flags": "", "cmake_flags": "", "supported_boards": []}
VENDOR_LINE = 'VENDOR_SDK_PATH="/opt/vendor/sdk"\n'


def make_module(targets: Path, name: str = "Edit_Board") -> Path:
    return ModuleGenerator(targets).generate_module({**DATA, "module_name": name})


def add_line(text: str, line: str) -> str:
    """The line after the shebang (a place every stage module has)."""
    first, rest = text.split("\n", 1)
    return first + "\n" + line + rest


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """One generated module, copied per test (generating runs bash -n over every script)."""
    return make_module(tmp_path_factory.mktemp("made"))


@pytest.fixture
def module(made, tmp_path):
    import shutil
    folder = tmp_path / "targets" / made.name
    shutil.copytree(made, folder, symlinks=True)
    return folder


# --- the files of the step -----------------------------------------------------------------------------------------

def test_the_step_lists_the_stage_modules_slots_target_yml_and_dockerfile_never_the_runner(module):
    (module / "modules" / "build.sh").write_text("#!/bin/bash\necho own runner\n")
    (module / "modules" / "stage_user.sh").write_text("#!/bin/bash\n")
    (module / "modules" / "runner.sh").write_text("#!/bin/bash\n")
    (module / "modules" / "vendor_module.sh").write_text("#!/bin/bash\necho vendor\n")
    text = (module / "target.yml").read_text()
    (module / "target.yml").write_text(text.replace("before_convert: null", "before_convert:\n  - vendor"))
    files = editable_files(module)
    assert files == [*STAGES, "modules/vendor_module.sh", "target.yml", "Dockerfile"]
    assert not any(Path(f).name in RUNNER_FILES for f in files)
    assert "modules/benchmark_module.sh" not in files and not any(f.startswith("scripts/") for f in files)
    edit = ModuleEdit(module)
    for runner in ("modules/build.sh", "modules/runner.sh", "modules/stage_user.sh"):
        with pytest.raises(ValueError, match="never edited|not a file"):
            edit.set_text(runner, "#!/bin/bash\n")


def test_an_edit_shows_in_the_diff_and_can_be_taken_back(module):
    edit = ModuleEdit(module)
    assert edit.diff() == "" and edit.changed() == []
    rel = "modules/config_module.sh"
    edit.set_text(rel, add_line(edit.text(rel), VENDOR_LINE))
    assert edit.changed() == [rel]
    diff = edit.diff()
    assert diff.startswith(f"--- a/{rel}\n+++ b/{rel}\n") and "+" + VENDOR_LINE in diff
    assert edit.problems() == []
    assert (module / rel).read_text() == edit.generated[rel], "nothing is written before the save"
    edit.reset(rel)
    assert edit.diff() == ""


def test_a_failing_check_refuses_the_save_and_writes_nothing(module):
    edit = ModuleEdit(module)
    rel = "modules/source_module.sh"
    before = (module / rel).read_text()
    edit.set_text(rel, add_line(before, "curl -o /tmp/x https://vendor.example/sdk.tgz\n"))
    edit.set_text("Dockerfile", edit.text("Dockerfile") + "FROM vendor/toolchain:latest AS extra\n")
    with pytest.raises(EditRefused) as refused:
        edit.save()
    text = "\n".join(refused.value.problems)
    assert "downloads at build time" in text and "must be pinned by digest" in text
    assert (module / rel).read_text() == before
    # the marks of target.yml are the framework's: an edit cannot drop "untested"
    edit.reset()
    edit.set_text("target.yml", edit.text("target.yml").replace("untested: true", "untested: false"))
    assert any("metadata.untested" in p for p in edit.problems())


def test_a_passing_edit_is_saved_and_is_the_new_baseline(module):
    edit = ModuleEdit(module)
    rel = "modules/convert_module.sh"
    mode = (module / rel).stat().st_mode
    edit.set_text(rel, add_line(edit.text(rel), VENDOR_LINE))
    assert edit.save() == [rel]
    assert VENDOR_LINE in (module / rel).read_text() and (module / rel).stat().st_mode == mode
    assert edit.diff() == "" and ModuleEdit(module).diff() == ""


def test_a_diff_is_applied_all_or_nothing_and_never_to_the_runner(module):
    edit = ModuleEdit(module)
    rel = "modules/target_module.sh"
    proposal = ModuleEdit(module)
    proposal.set_text(rel, add_line(proposal.text(rel), VENDOR_LINE))
    assert edit.apply_diff(proposal.diff()) == [rel] and edit.texts == proposal.texts
    with pytest.raises(ValueError, match="never edited"):
        ModuleEdit(module).apply_diff("--- a/modules/build.sh\n+++ b/modules/build.sh\n@@ -1 +1 @@\n-a\n+b\n")
    with pytest.raises(ValueError, match="neither adds nor deletes"):
        ModuleEdit(module).apply_diff("--- /dev/null\n+++ b/modules/new_module.sh\n@@ -0,0 +1 @@\n+echo\n")
    fresh = ModuleEdit(module)
    with pytest.raises(ValueError, match="does not match"):
        fresh.apply_diff(f"--- a/{rel}\n+++ b/{rel}\n@@ -1,1 +1,1 @@\n-no such line\n+x\n")
    assert fresh.changed() == []


def test_a_staged_module_moves_into_the_targets_folder_only_on_commit(tmp_path):
    targets = tmp_path / "targets"
    scratch = stage_folder()
    try:
        folder = make_module(scratch)
        assert not targets.exists() or not any(targets.iterdir())
        saved = commit_staged(folder, targets)
        assert saved == targets / "edit_board" and (saved / "target.yml").is_file()
        assert not scratch.exists()
        again = stage_folder()
        with pytest.raises(ValueError, match="exists now"):
            commit_staged(make_module(again), targets)
        discard_staged(again / "edit_board")
        assert not again.exists()
    finally:
        discard_staged(scratch)


# --- the wizard's last page ----------------------------------------------------------------------------------------

@pytest.fixture
def boxes(monkeypatch):
    from orchestrator.gui import plain, wizards
    shown = []

    class Box:
        Critical = Information = Ok = 0

        def __init__(self, *a, **k):
            shown.append(("box",) + a[1:])

        def setTextFormat(self, *a):
            pass

        def exec(self):
            return 0

        @staticmethod
        def information(*a):
            shown.append(("information",) + a[1:])

        @staticmethod
        def critical(*a):
            shown.append(("critical",) + a[1:])

    monkeypatch.setattr(wizards, "QMessageBox", Box)
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append(("plain", box.windowTitle(), box.text())) or 0)
    return shown


@pytest.fixture
def wizard(qapp, tmp_path, boxes):
    from orchestrator.gui import wizards
    w = wizards.ModuleCreationWizard(tmp_path / "targets")
    w.name_edit.setText("Edit_Board")
    w.show()
    yield w
    w.reject()
    w.deleteLater()


def to_edit_page(wizard):
    while wizard.nextId() != -1:
        wizard.next()
    assert wizard.currentId() == wizard.edit_id


def test_the_wizard_page_lists_the_files_and_saves_nothing_before_finish(wizard, tmp_path):
    targets = tmp_path / "targets"
    to_edit_page(wizard)
    panel = wizard.edit_panel
    assert panel.edit is not None and panel.edit.files == [*STAGES, "target.yml", "Dockerfile"]
    assert [panel.file_list.item(i).text() for i in range(panel.file_list.count())] == panel.edit.files
    assert not any("runner" in f or f.endswith("build.sh") for f in panel.edit.files)
    assert not targets.exists() or not any(targets.iterdir()), "the module waits in a scratch folder"
    staged = wizard._staged
    assert staged is not None and staged.is_dir() and targets not in staged.parents


def test_an_edit_on_the_page_shows_in_the_diff_and_a_failing_check_blocks_finish(wizard, tmp_path, boxes):
    targets = tmp_path / "targets"
    to_edit_page(wizard)
    panel = wizard.edit_panel
    editor = panel.editors["modules/config_module.sh"]
    editor.setPlainText(add_line(editor.toPlainText(), VENDOR_LINE))
    assert "+" + VENDOR_LINE.rstrip("\n") in panel.diff_view.toPlainText()
    assert panel.file_list.item(1).text() != "modules/config_module.sh"   # marked as changed
    bad = panel.editors["modules/source_module.sh"]
    bad.setPlainText(add_line(bad.toPlainText(), "wget https://vendor.example/sdk.tgz\n"))
    wizard.accept()
    assert boxes[-1][0] == "plain" and "downloads at build time" in boxes[-1][2]
    assert not targets.exists() or not any(targets.iterdir()), "Finish was refused: nothing saved"
    assert wizard.isVisible() and "downloads at build time" in panel.problems_view.toPlainText()
    bad.setPlainText(panel.edit.generated["modules/source_module.sh"])
    assert panel.run_checks() == []
    wizard.accept()
    saved = targets / "edit_board"
    assert VENDOR_LINE in (saved / "modules" / "config_module.sh").read_text()
    assert not wizard.isVisible() and wizard._staged is None
    assert [b for b in boxes if b[0] == "information"], boxes


def test_going_back_and_changing_a_page_makes_the_files_again(wizard):
    to_edit_page(wizard)
    panel = wizard.edit_panel
    rel = "modules/config_module.sh"
    panel.editors[rel].setPlainText(add_line(panel.editors[rel].toPlainText(), VENDOR_LINE))
    wizard.back()
    wizard.next()
    assert panel.edit.changed() == [rel], "the same values: the edits stay"
    wizard.back()
    wizard.cpu_flags.setText("-mcpu=cortex-a55")
    wizard.next()
    assert panel.edit.changed() == [] and panel.note.isVisibleTo(panel)


def test_an_ai_proposal_is_a_diff_in_the_step_and_never_saved_without_the_person(wizard, tmp_path, boxes):
    from orchestrator.tools.gui_surface import Filler
    targets = tmp_path / "targets"
    to_edit_page(wizard)
    panel = wizard.edit_panel
    form = panel.form
    rel = "modules/config_module.sh"
    # the files are fields an AI reads, never fills
    assert form.field(f"file:{rel}").get() == panel.edit.text(rel)
    refused = form.fill({f"file:{rel}": "#!/bin/bash\n"})
    assert refused.filled == [] and "diff" in refused.refused[0][1]
    proposal = ModuleEdit(panel.edit.folder)
    proposal.set_text(rel, add_line(proposal.text(rel), VENDOR_LINE))
    outcome = form.fill({"proposal": proposal.diff()})
    assert outcome.filled == ["proposal"]
    assert VENDOR_LINE in panel.editors[rel].toPlainText() and panel.edit.changed() == [rel]
    assert "+" + VENDOR_LINE.rstrip("\n") in panel.diff_view.toPlainText()
    assert form.field("proposal").filled_by_ai() and isinstance(form.field("proposal").filler(), Filler)
    assert VENDOR_LINE not in (panel.edit.folder / rel).read_text(), "a proposal changes the step, not the file"
    assert not targets.exists() or not any(targets.iterdir())
    # the banner's Undo takes it back
    panel.binding.banners[0].undo()
    assert panel.edit.changed() == []
    # a file the person edited is theirs: a proposal that would change it is refused
    editor = panel.editors[rel]
    editor.setFocus()
    form.person_edited(f"file:{rel}")
    editor.setPlainText(add_line(editor.toPlainText(), "# mine\n"))
    again = form.fill({"proposal": proposal.diff()})
    assert again.filled == []
    assert "# mine" in panel.editors[rel].toPlainText()
    # only the person's Finish saves, and the GUI tools never click it
    assert wizard.gui_form.pages.next() == "this is the last page: only the person clicks Finish"


def test_a_proposal_that_does_not_apply_is_refused_with_the_reason(wizard):
    to_edit_page(wizard)
    out = wizard.edit_panel.form.fill({"proposal": "--- a/modules/build.sh\n+++ b/modules/build.sh\n"
                                                   "@@ -1 +1 @@\n-a\n+b\n"})
    assert out.filled == [] and "never edited" in out.refused[0][1]


# --- an existing module ----------------------------------------------------------------------------------------------

def test_an_existing_module_opens_in_the_same_step(qapp, module, boxes):
    from orchestrator.gui.module_edit import ModuleEditDialog
    saved = []
    dialog = ModuleEditDialog(module, on_saved=saved.append)
    try:
        panel = dialog.panel
        assert panel.edit.files == [*STAGES, "target.yml", "Dockerfile"]
        rel = "modules/target_module.sh"
        panel.editors[rel].setPlainText(add_line(panel.editors[rel].toPlainText(), "sudo true\n"))
        assert dialog.save() is None and boxes[-1][0] == "plain" and "uses sudo" in boxes[-1][2]
        assert "sudo true" not in (module / rel).read_text()
        panel.editors[rel].setPlainText(add_line(panel.edit.generated[rel], VENDOR_LINE))
        assert dialog.save() == [rel] and saved == [[rel]]
        assert VENDOR_LINE in (module / rel).read_text()
        assert panel.edit.diff() == ""
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_the_targets_window_opens_the_step_for_the_selected_target(qapp, module, monkeypatch):
    from types import SimpleNamespace
    from orchestrator.gui import module_edit, targets_window
    opened = []
    monkeypatch.setattr(module_edit.ModuleEditDialog, "exec", lambda self: opened.append(self.folder) or 0)
    monkeypatch.setattr(targets_window.TargetsDialog, "refresh", lambda self, select=None: None)
    framework = SimpleNamespace(target_manager=SimpleNamespace(folder_of=lambda name: module))
    dialog = targets_window.TargetsDialog(framework)
    try:
        monkeypatch.setattr(dialog, "selected", lambda: module.name)
        dialog._edit()
        assert opened == [module]
        assert dialog.btn_edit.objectName() == "btn_targets_edit"
    finally:
        dialog.deleteLater()


# --- the CLI -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli_targets(tmp_path, module):
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(module.parent))
    return module.parent


def run(args, **kw):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    return CliRunner().invoke(cli_module.cli, ["-q", *args], **kw)


def test_module_edit_refuses_a_failing_check_and_saves_a_passing_edit(cli_targets, module, monkeypatch):
    import click
    rel = "modules/convert_module.sh"
    before = (module / rel).read_text()
    texts = {"text": add_line(before, "sudo make install\n")}
    monkeypatch.setattr(click, "edit", lambda text, **k: texts["text"] if text == before else None)
    result = run(["module", "edit", module.name, "--file", rel], input="n\n")
    assert result.exit_code == 1 and "uses sudo" in result.output and "Not saved" in result.output
    assert (module / rel).read_text() == before
    texts["text"] = add_line(before, VENDOR_LINE)
    result = run(["module", "edit", module.name, "--file", rel], input="y\n")
    assert result.exit_code == 0, result.output
    assert "+" + VENDOR_LINE.rstrip("\n") in result.output and "Every check passed" in result.output
    assert (module / rel).read_text() == texts["text"]


def test_module_edit_applies_a_diff_only_on_the_persons_yes(cli_targets, module, tmp_path):
    rel = "modules/source_module.sh"
    proposal = ModuleEdit(module)
    proposal.set_text(rel, add_line(proposal.text(rel), VENDOR_LINE))
    patch = tmp_path / "proposal.diff"
    patch.write_text(proposal.diff())
    result = run(["module", "edit", module.name, "--apply", str(patch)], input="n\n")
    assert result.exit_code == 1 and VENDOR_LINE not in (module / rel).read_text()
    result = run(["module", "edit", module.name, "--apply", str(patch)], input="")
    assert result.exit_code == 1 and VENDOR_LINE not in (module / rel).read_text(), "no answer: not saved"
    result = run(["module", "edit", module.name, "--apply", str(patch)], input="y\n")
    assert result.exit_code == 0, result.output
    assert VENDOR_LINE in (module / rel).read_text()


def test_module_edit_never_opens_the_runner(cli_targets, module):
    result = run(["module", "edit", module.name, "--file", "modules/build.sh"])
    assert result.exit_code == 1 and "never edited" in result.output


def test_module_create_edit_saves_only_after_the_checks_and_the_persons_yes(cli_targets, tmp_path, monkeypatch):
    import click
    targets = cli_targets
    bad = {"on": True}

    def edit(text, extension=".txt", require_save=True, **_k):
        if text.startswith("#!") and "source_module.sh" in text[:300]:
            line = "wget https://vendor.example/x\n" if bad["on"] else VENDOR_LINE
            return add_line(text, line)
        return None

    monkeypatch.setattr(click, "edit", edit)
    args = ["module", "create", "--name", "Cli Edit", "--arch", "aarch64", "--sdk", "None", "--description", "d",
            "--base-image", "debian:bookworm-slim", "--packages", "cmake", "--cpu-flags", "", "--edit"]
    result = run(args, input="n\n")
    assert result.exit_code == 1, result.output
    assert "downloads at build time" in result.output and not (targets / "cli_edit").exists()
    bad["on"] = False
    result = run(args, input="n\n")
    assert result.exit_code == 1 and not (targets / "cli_edit").exists(), "no yes: not saved"
    result = run(args, input="y\n")
    assert result.exit_code == 0, result.output
    assert VENDOR_LINE in (targets / "cli_edit" / "modules" / "source_module.sh").read_text()
    assert not list(Path(__import__("tempfile").gettempdir()).glob("llmcf-module-edit-*/cli_edit"))
