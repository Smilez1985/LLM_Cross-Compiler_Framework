"""`build start --format` offers what the installed targets build (DECISIONS round 23, item 7): their target.yml
capabilities, as the Builder tab's format list offers them (Core/build_formats.py offered_formats, one list for both);
a target without capabilities takes every format unchecked, as the Builder tab offers it. A format the chosen target
does not build is refused in plain words naming what each installed target builds; the help text lists them from the
targets, and completion offers them."""

from types import SimpleNamespace

import pytest

from test_review_capabilities_paths import HF_MODEL, framework_with_targets, make_cli


@pytest.fixture
def mixed(make_framework, tmp_path):
    return framework_with_targets(make_framework, tmp_path / "targets_mixed")


# --- the one list ------------------------------------------------------------------------------------------------

def test_what_each_target_builds_comes_from_its_capabilities(mixed, tmp_path):
    from orchestrator.Core.build_formats import formats_in, formats_of, formats_text, unchecked_formats
    found = {t.target: t for t in formats_of(mixed.target_manager)}
    assert found["Rockchip"].formats == ("gguf", "rkllm", "rknn") and found["Rockchip"].declared
    assert not found["MyJetson"].declared
    assert found["MyJetson"].formats == tuple(v for _, v in unchecked_formats()) and "hf" not in found["MyJetson"].formats
    read = {t.target: t for t in formats_in(tmp_path / "targets_mixed")}
    assert read == found, "read without booting: the same as the loaded targets"
    text = formats_text(found.values())
    assert "Rockchip: gguf, rkllm, rknn" in text and "MyJetson: any format (it declares no capabilities" in text


def test_an_invalid_capabilities_block_counts_as_none(tmp_path):
    import yaml

    from orchestrator.Core.build_formats import formats_in
    from test_review_capabilities_paths import ROCKCHIP_DIR
    caps = yaml.safe_load((ROCKCHIP_DIR / "target.yml").read_text(encoding="utf-8"))["capabilities"]
    caps["default_board"] = "rk9999"
    (tmp_path / "Broken").mkdir()
    (tmp_path / "Broken" / "target.yml").write_text(yaml.safe_dump({"metadata": {"name": "Broken"},
                                                                    "capabilities": caps}), encoding="utf-8")
    (broken,) = formats_in(tmp_path)
    assert not broken.declared, "as on the build path: built unchecked"


@pytest.mark.parametrize("fmt, target, refused, words", [
    ("gguf", "Rockchip", False, ""),
    ("GGUF", "Rockchip", False, ""),
    ("tensorrt", "Rockchip", True, "The target Rockchip does not build the format tensorrt."),
    ("onnx", "MyJetson", False, ""),                     # no capabilities: every format, nothing checked
    ("hf", "MyJetson", True, "hf is no format a target builds."),
    ("hf", "Rockchip", True, "no installed target builds it"),
    ("gguf", "NoSuchTarget", False, ""),                 # the target check says so
])
def test_format_problem(mixed, fmt, target, refused, words):
    from orchestrator.Core.build_formats import format_problem, formats_of
    problem = format_problem(fmt, formats_of(mixed.target_manager), target)
    assert (problem is not None) == refused
    if refused:
        assert words in problem
        listing = problem.split("What each installed target builds: ", 1)[1]
        assert "Rockchip: gguf, rkllm, rknn" in listing and "MyJetson: any format" in listing


def test_a_target_without_capabilities_names_the_target_that_builds_it():
    """Review cs-cli-gui-v: a format a target without capabilities does not take (no engine format) but another
    installed target declares is not called "no format a target builds": the refusal names that target."""
    from orchestrator.Core.build_formats import TargetFormats, format_problem, unchecked_formats
    found = [TargetFormats("Hailo8", ("hef",), True),
             TargetFormats("MyJetson", tuple(v for _, v in unchecked_formats()), False)]
    problem = format_problem("hef", found, "MyJetson")
    assert problem is not None and "no format a target builds" not in problem
    assert "The target MyJetson does not build the format hef; Hailo8 builds it." in problem
    assert format_problem("hf", found, "MyJetson").startswith("hf is no format a target builds."), "as before"


def test_another_target_that_builds_it_is_named():
    from orchestrator.Core.build_formats import TargetFormats, format_problem
    found = [TargetFormats("Rockchip", ("gguf", "rkllm", "rknn"), True), TargetFormats("Hailo8", ("hef",), True)]
    problem = format_problem("hef", found, "Rockchip")
    assert "does not build the format hef; Hailo8 builds it" in problem
    assert "Hailo8: hef" in problem


def test_the_builder_tab_offers_the_same_list():
    pytest.importorskip("PySide6")
    from orchestrator.Core import build_formats
    from orchestrator.gui import builder_tab
    assert builder_tab.BUILD_FORMATS == list(build_formats.PIPELINE_FORMATS)
    assert builder_tab.UNCHECKED_FORMATS == build_formats.unchecked_formats()
    assert build_formats.offered_formats(None) == build_formats.unchecked_formats()


# --- the command -------------------------------------------------------------------------------------------------

def test_a_format_the_target_does_not_build_is_refused_naming_what_each_target_builds(mixed, tmp_path, monkeypatch):
    cli = make_cli(mixed, tmp_path, monkeypatch)
    result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "tensorrt")
    assert result.code == 2, result.out
    assert "The target Rockchip does not build the format tensorrt" in result.out
    assert "Rockchip: gguf, rkllm, rknn" in result.out and "MyJetson: any format" in result.out
    assert "is not one of" not in result.out and cli.submitted == []


def test_the_targets_formats_are_accepted(mixed, tmp_path, monkeypatch):
    cli = make_cli(mixed, tmp_path, monkeypatch)
    assert cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "RKLLM").code == 0
    assert cli.invoke("build", "start", "-m", HF_MODEL, "-t", "MyJetson", "-f", "onnx").code == 0
    assert [str(r.target_formats[0].value) for r in cli.submitted] == ["rkllm", "onnx"]
    refused = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "MyJetson", "-f", "hf")
    assert refused.code == 2 and "hf is no format a target builds" in refused.out


def test_the_start_panel_names_the_format_the_build_runs_with(mixed, tmp_path, monkeypatch):
    """The start panel of `build start` names the format as the target spells it (the one the build runs with),
    not the person's spelling: `-f RKLLM` builds rkllm (stage C wave 3c, fix c)."""
    cli = make_cli(mixed, tmp_path, monkeypatch)
    result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "RKLLM", quiet=False)
    assert result.code == 0, result.out
    assert "Format: rkllm" in result.out and "Format: RKLLM" not in result.out
    assert [str(r.target_formats[0].value) for r in cli.submitted] == ["rkllm"]


def test_the_start_panel_never_reads_the_format_as_markup(mixed, tmp_path, monkeypatch):
    """--format is no fixed choice any more: the format comes from a target.yml, whose format ids are free text, so
    the panel prints it Rich-escaped, as every other value there."""
    from orchestrator.Core.build_service import BuildService
    cli = make_cli(mixed, tmp_path, monkeypatch)
    real = BuildService.validate

    def validate(self, request, **kwargs):
        checked = real(self, request, **kwargs)
        return checked.model_copy(update={"format": "[bold]rkllm[/bold]"})
    monkeypatch.setattr(BuildService, "validate", validate)
    monkeypatch.setattr(BuildService, "earlier_builds", lambda self, request, **kw: [])
    monkeypatch.setattr(BuildService, "start_build", lambda self, request, **kw: SimpleNamespace(request_id="r1"))
    monkeypatch.setattr(BuildService, "request_state", lambda self, request_id: None)
    monkeypatch.setattr("orchestrator.cli._pause", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr("orchestrator.cli._cancel_interrupted_build", lambda ctx, request_id: None)
    result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "rkllm", quiet=False)
    assert "Format: [bold]rkllm[/bold]" in result.out, result.out


def test_the_help_lists_what_the_installed_targets_build():
    from test_cli_builds import invoke_plain
    result = invoke_plain("build", "start", "--help")
    text = " ".join(result.out.split())
    assert result.code == 0 and "The installed targets build: Rockchip: gguf, rkllm, rknn" in text
    assert "tensorrt" not in text and "coreml" not in text


def test_the_help_without_readable_targets_still_works(monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    monkeypatch.setattr(cli_module, "_settings", lambda ctx: (_ for _ in ()).throw(RuntimeError("broken settings")))
    result = invoke_plain("build", "start", "--help")
    assert result.code == 0 and "one the target builds ('llm-cli targets list' shows" in " ".join(result.out.split())


def test_completion_offers_the_targets_formats(mixed):
    from orchestrator import cli as cli_module
    ctx = SimpleNamespace(find_object=lambda kind: SimpleNamespace(framework_manager=mixed))
    items = cli_module._TargetFormat().shell_complete(ctx, None, "rk")
    assert [i.value for i in items] == ["rkllm", "rknn"]
    assert "hf" not in [i.value for i in cli_module._TargetFormat().shell_complete(ctx, None, "")]
