"""The defaults of the importance matrix and the calibration text (DESIGN: Build pipeline and target modules;
orchestrator/Core/build_defaults.py).

The settings default_enable_imatrix and default_calibration_dataset reach every way a build is configured: the build
service's one rule, the AI tools (check_build_request, start_build and the question before it), `llm-cli build start`,
the Builder tab and File > Settings...; an explicit choice always wins, and a recipe never takes a default.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import build_defaults
from orchestrator.Core.build_defaults import (dataset_label, default_dataset_path, default_imatrix,
                                              takes_default_dataset)
from orchestrator.tools.registry import ToolContext, ToolError
from test_build_tools import BUILD, ask, env  # noqa: F401 - the AI tools' harness (env is a fixture)
from test_review_capabilities_paths import gui_messages  # noqa: F401 - the Builder tab's recorded message boxes

HF_MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"


def cfg(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


# --- the rule ------------------------------------------------------------------------------------------------------

class TestTheRule:

    @pytest.mark.parametrize("quant, expected", [
        ("Q4_K_M", True), ("Q4_0", True), ("Q3_K_M", True), ("IQ4_XS", True), ("IQ2_XS", True), ("Q2_K", True),
        ("Q5_K_M", False), ("Q6_K", False), ("Q8_0", False), ("F16", False), ("TQ1_0", False), (None, False),
    ])
    def test_on_by_default_below_5_bits(self, quant, expected):
        assert default_imatrix(cfg(), "gguf", quant) is expected

    def test_the_setting_off_leaves_only_the_types_that_need_one(self):
        off = cfg(default_enable_imatrix=False)
        assert default_imatrix(off, "gguf", "Q4_K_M") is False
        assert default_imatrix(off, "gguf", "IQ2_XS", needs=["IQ2_XS"]) is True
        assert default_imatrix(off, "gguf", "IQ4_XS", needs=["IQ2_XS"]) is False

    def test_only_gguf_and_only_where_the_format_uses_it(self):
        assert default_imatrix(cfg(), "rkllm", "w4a16") is False
        assert default_imatrix(cfg(), "rknn", "i8") is False
        assert default_imatrix(cfg(), "gguf", "Q4_K_M", format_uses_imatrix=False) is False

    def test_the_default_file_is_inside_the_datasets_folder_or_absolute(self, tmp_path):
        folder = tmp_path / "datasets"
        assert default_dataset_path(cfg(), folder) is None
        assert default_dataset_path(cfg(default_calibration_dataset="calib.txt"), folder) == folder / "calib.txt"
        absolute = tmp_path / "elsewhere" / "mine.txt"
        assert default_dataset_path(cfg(default_calibration_dataset=str(absolute)), folder) == absolute

    def test_an_ai_sees_the_default_file_by_its_label_only(self, tmp_path):
        assert dataset_label(cfg()) is None
        assert dataset_label(cfg(default_calibration_dataset=str(tmp_path / "mine.txt"))) == "datasets:mine.txt"

    def test_the_default_file_is_read_by_text_formats_only(self):
        assert takes_default_dataset("gguf", True) and not takes_default_dataset("gguf", False)
        assert takes_default_dataset("rkllm", False)
        assert not takes_default_dataset("rknn", False), "RKNN reads a sample list, never a text file"


class TestTheSetter:

    def test_the_defaults_and_values_are_checked_and_saved(self, booted_framework):
        from orchestrator.Core.config_manager import set_build_default
        config = booted_framework.config
        assert config.get("default_enable_imatrix") is True and config.get("default_calibration_dataset") == ""
        assert set_build_default(config, "default_enable_imatrix", "no") is False
        assert set_build_default(config, "default_calibration_dataset", " calib.txt ") == "calib.txt"
        assert set_build_default(config, "calibration_language", "DE") == "de"
        saved = (Path(config.config_dir) / "config.yml").read_text(encoding="utf-8")
        assert "default_enable_imatrix: false" in saved and "default_calibration_dataset: calib.txt" in saved
        for key, value in (("default_enable_imatrix", "maybe"), ("calibration_language", "fr"),
                           ("default_calibration_dataset", "a\nb"), ("framework_mode", "private")):
            with pytest.raises(ValueError):
                set_build_default(config, key, value)
        assert config.get("default_enable_imatrix") is False and config.get("calibration_language") == "de"


# --- the build service and the AI tools ----------------------------------------------------------------------------

def datasets_with(env, name="calib.txt"):  # noqa: F811 - env is the fixture
    folder = env.tmp / "datasets"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("calibration text\n\nmore text\n", encoding="utf-8")
    env.framework.config.values.update(datasets_dir=str(folder), default_calibration_dataset=name)
    return folder / name


class TestAiTools:

    def test_check_shows_the_default_importance_matrix_and_the_shipped_text(self, env):  # noqa: F811
        out = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
        assert out.normalized.use_imatrix is True and out.normalized.dataset is None
        assert "importance matrix yes" in out.normalized.calibration and "shipped text" in out.normalized.calibration

    def test_the_setting_off_and_an_explicit_choice(self, env):  # noqa: F811
        env.framework.config.values["default_enable_imatrix"] = False
        out = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
        assert out.normalized.use_imatrix is False
        env.framework.config.values["default_enable_imatrix"] = True
        out = env.registry.call("check_build_request", {**BUILD, "use_imatrix": False}, ToolContext("ditto"))
        assert out.normalized.use_imatrix is False, "an explicit no wins over the default"
        out = env.registry.call("check_build_request", {**BUILD, "quantization": "Q8_0", "use_imatrix": True},
                                ToolContext("ditto"))
        assert out.normalized.use_imatrix is True, "an explicit yes wins as well"

    def test_start_build_uses_the_defaults_and_the_question_names_them_without_a_path(self, env):  # noqa: F811
        path = datasets_with(env)
        out, shown = ask(env, BUILD)
        request = env.orch.requests[0]
        assert request.use_imatrix is True and request.dataset_path == str(path.resolve())
        assert "Calibration: importance matrix yes; calibration text datasets:calib.txt" in shown[0]
        assert str(env.tmp) not in shown[0], "the person's folder never reaches the AI"
        check = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
        assert check.normalized.dataset == "datasets:calib.txt"
        assert all(str(env.tmp) not in m for m in check.mounts)

    def test_an_empty_dataset_builds_without_the_default_file(self, env):  # noqa: F811
        datasets_with(env)
        ask(env, {**BUILD, "dataset_path": ""})
        request = env.orch.requests[0]
        assert request.use_imatrix is True and request.dataset_path is None  # the shipped text, written later

    def test_the_question_binds_the_importance_matrix_it_showed(self, env):  # noqa: F811
        def switch_off(spec, inp):
            env.framework.config.values["default_enable_imatrix"] = False  # changed while the person reads
            return True
        env.registry.call("start_build", BUILD, ToolContext("ditto", confirm=switch_off))
        assert env.orch.requests[0].use_imatrix is True

    def test_a_missing_default_file_refuses_the_build_naming_the_setting(self, env):  # noqa: F811
        path = datasets_with(env)
        path.unlink()
        with pytest.raises(ToolError) as exc:
            env.registry.call("start_build", BUILD, ToolContext("ditto", confirm=lambda s, i: True))
        assert "default_calibration_dataset" in str(exc.value) and "calib.txt" in str(exc.value)
        assert str(env.tmp) not in str(exc.value) and env.orch.requests == []

    def test_rknn_never_takes_the_default_file_and_rkllm_does(self, env):  # noqa: F811
        path = datasets_with(env)
        rknn = env.service.validate(env.service.request_in(model=HF_MODEL, target="Rockchip", format="rknn",
                                                           board="rk3588"), local_paths=True)
        assert not rknn.dataset_path and rknn.use_imatrix is False
        rkllm = env.service.validate(env.service.request_in(model=HF_MODEL, target="Rockchip", format="rkllm",
                                                            board="rk3588"), local_paths=True)
        assert rkllm.dataset_path == str(path.resolve()) and rkllm.use_imatrix is False
        gguf = env.service.validate(env.service.request_in(model=HF_MODEL, target="Rockchip", format="gguf",
                                                           quantization="Q8_0"), local_paths=True)
        assert gguf.use_imatrix is False and not gguf.dataset_path, "no matrix: GGUF reads no dataset"

    def test_a_recipe_never_takes_a_default(self, env, monkeypatch):  # noqa: F811
        from orchestrator.schemas.recipes import Recipe
        from test_recipes import recipe_data
        datasets_with(env)
        data = recipe_data()
        data.update(target={"id": "Rockchip", "pack_sha256": "a" * 64}, format="gguf", board="rk3588",
                    quantization="Q4_K_M")
        recipe = Recipe.model_validate(data)  # use_imatrix False, no dataset
        monkeypatch.setattr(env.service, "installed_recipe", lambda rid: (recipe, SimpleNamespace(id=recipe.id)))
        request, _recipe, _prov = env.service.recipe_request(recipe.id)
        validated = env.service.validate(request, local_paths=True)
        assert validated.use_imatrix is False and not validated.dataset_path


# --- the CLI -------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


def build_start(cli, *args):
    result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", *args)
    assert result.code == 0, result.out
    return cli.submitted[-1], result.out


def default_file(framework, tmp_path, name="calib.txt"):
    folder = tmp_path / "datasets"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("calibration text\n", encoding="utf-8")
    framework.config.set("datasets_dir", str(folder))
    framework.config.set("default_calibration_dataset", name)
    return folder / name


class TestCli:

    def test_left_out_the_defaults_given_the_choice(self, cli):
        req, _out = build_start(cli, "-f", "gguf", "-b", "rk3588")
        assert req.use_imatrix is True and req.dataset_path is None
        assert build_start(cli, "-f", "gguf", "-b", "rk3588", "--no-imatrix")[0].use_imatrix is False
        assert build_start(cli, "-f", "gguf", "-q", "Q8_0")[0].use_imatrix is False
        assert build_start(cli, "-f", "gguf", "-q", "Q8_0", "--imatrix")[0].use_imatrix is True
        assert build_start(cli, "-f", "gguf", "-q", "IQ2_XS")[0].use_imatrix is True, "a type that needs one"

    def test_the_default_file_and_its_overrides(self, cli, tmp_path):
        path = default_file(cli.framework, tmp_path)
        assert build_start(cli, "-f", "gguf")[0].dataset_path == str(path.resolve())
        assert build_start(cli, "-f", "gguf", "--no-dataset")[0].dataset_path is None
        own = tmp_path / "own.txt"
        own.write_text("mine\n", encoding="utf-8")
        assert build_start(cli, "-f", "gguf", "--dataset", str(own))[0].dataset_path == str(own.resolve())
        result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "--dataset", str(own), "--no-dataset")
        assert result.code == 2 and "exclude each other" in result.out

    def test_config_set_saves_the_defaults(self, cli, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        result = cli.invoke("config", "set", "default_enable_imatrix", "false")
        assert result.code == 0 and "default_enable_imatrix: False" in result.out, result.out
        # the next process (the next `build start`) reads it from config.yml
        assert ConfigManager(str(cli.framework.config.config_dir)).get("default_enable_imatrix") is False
        result = cli.invoke("config", "set", "default_calibration_dataset", "nothing-here.txt")
        assert result.code == 0 and "does not exist yet" in result.out, result.out
        result = cli.invoke("config", "set", "default_enable_imatrix", "maybe")
        assert result.code == 2
        result = cli.invoke("config", "set", "calibration_language", "en")
        assert result.code == 0
        assert ConfigManager(str(cli.framework.config.config_dir)).get("calibration_language") == "en"


# --- the Builder tab and File > Settings... -------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp", "gui_messages")
class TestGui:

    @pytest.fixture
    def tab(self, booted_framework):
        from test_review_capabilities_paths import make_tab
        tab = make_tab(booted_framework)
        yield tab
        tab._build_timer.stop()

    def test_the_box_follows_the_type_until_the_person_ticks_it(self, tab):
        from test_review_capabilities_paths import select
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        assert tab.quant_combo.currentText() == "Q4_K_M" and tab.chk_imatrix.isChecked()
        tab.quant_combo.setCurrentText("Q8_0")
        assert not tab.chk_imatrix.isChecked()
        tab.quant_combo.setCurrentText("IQ4_XS")
        assert tab.chk_imatrix.isChecked()
        tab.chk_imatrix.click()   # the person's choice stays
        tab.quant_combo.setCurrentText("Q4_K_M")
        assert not tab.chk_imatrix.isChecked()

    def test_start_build_submits_the_default_and_the_person_s_choice(self, tab):
        from test_review_capabilities_paths import select, submit
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        req = submit(tab)
        assert req is not None and req.use_imatrix is True and req.dataset_path is None
        tab.chk_imatrix.click()
        assert submit(tab).use_imatrix is False

    def test_the_empty_dataset_field_names_and_takes_the_default_file(self, tab, tmp_path):
        from test_review_capabilities_paths import select, submit
        assert "shipped text" in tab.dataset_combo.lineEdit().placeholderText()
        path = default_file(tab.framework, tmp_path)
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        assert "calib.txt" in tab.dataset_combo.lineEdit().placeholderText()
        assert submit(tab).dataset_path == str(path.resolve())

    def test_the_entry_without_a_dataset_of_ones_own_is_no_dataset(self, tab, tmp_path):
        """`build start --no-dataset` in the Builder: offered while a default file is set."""
        from test_review_capabilities_paths import select, submit
        assert tab.dataset_combo.findText(tab.NO_OWN_DATASET) < 0, "nothing to leave out without a default file"
        default_file(tab.framework, tmp_path)
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        idx = tab.dataset_combo.findText(tab.NO_OWN_DATASET)
        assert idx >= 0
        tab.dataset_combo.setCurrentIndex(idx)
        assert tab.dataset_path == ""
        req = submit(tab)
        assert req.dataset_path is None and req.use_imatrix is True

    def test_the_settings_window_changes_the_build_defaults(self, booted_framework, tmp_path):
        from orchestrator.gui.settings_window import SettingsDialog
        dialog = SettingsDialog(booted_framework, lang="en")
        try:
            assert dialog.chk_default_imatrix.isChecked()
            dialog.chk_default_imatrix.click()
            assert booted_framework.config.get("default_enable_imatrix") is False
            dialog.edit_default_dataset.setText("calib.txt")
            dialog._dataset_edited()
            assert booted_framework.config.get("default_calibration_dataset") == "calib.txt"
            assert "does not exist yet" in dialog.lbl_defaults_result.text()
            dialog.btn_shipped_dataset.click()
            assert booted_framework.config.get("default_calibration_dataset") == ""
            dialog.cmb_calibration_language.setCurrentIndex(dialog.cmb_calibration_language.findData("de"))
            assert booted_framework.config.get("calibration_language") == "de"
            saved = (Path(booted_framework.config.config_dir) / "config.yml").read_text(encoding="utf-8")
            assert "default_enable_imatrix: false" in saved and "calibration_language: de" in saved
        finally:
            dialog.close()
            dialog.deleteLater()


def test_the_rule_lives_in_one_module():
    """The Builder tab, the CLI and the build service read the same rule; none keeps its own list of types."""
    root = Path(build_defaults.__file__).resolve().parents[2]
    for rel in ("orchestrator/gui/builder_tab.py", "orchestrator/cli.py", "orchestrator/Core/build_service.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "IMATRIX_BY_DEFAULT" not in text and "build_defaults" in text, rel
