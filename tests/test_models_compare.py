"""
`llmcf models compare` and the Models tab's Compare Quality... (DESIGN, No dead code: the second recon's decision C of
2026-11-27): GGUF models of the list compared by their perplexity on the auto-tuning's evaluation text, ranked, with
their ratio to the base; only GGUF files.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPORT = {"baseline": {"path": "/m/model-F16.gguf", "ppl": 7.0},
          "ranking": [{"name": "model-Q8_0.gguf", "path": "/m/model-Q8_0.gguf", "ppl": 7.01, "ratio": 1.0014},
                      {"name": "model-Q4_0.gguf", "path": "/m/model-Q4_0.gguf", "error": "the sandbox is not there"}],
          "evaluation": {"text_sha256": "a" * 64, "chunks": 16, "ctx": 512}}


def row(name, gguf=True):
    return SimpleNamespace(name=name, gguf=Path(f"/m/{name}") if gguf else None)


@pytest.fixture
def cli_env(monkeypatch):
    from orchestrator import cli
    from orchestrator.Core import dataset_manager
    rows = {r.name: r for r in (row("model-F16.gguf"), row("model-Q8_0.gguf"), row("model-Q4_0.gguf"),
                                row("folder", gguf=False))}
    monkeypatch.setattr(cli, "_model_row", lambda ctx, name: rows[name])
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", lambda self: None)
    calls = []

    def compare_models(self, base, models):
        calls.append((base, list(models)))
        return REPORT
    monkeypatch.setattr(dataset_manager.DatasetManager, "compare_models", compare_models)
    return calls


def test_the_command_ranks_with_the_ratio_to_the_base(cli_env):
    from click.testing import CliRunner
    from orchestrator import cli
    out = CliRunner().invoke(cli.cli, ["models", "compare", "model-Q8_0.gguf", "model-Q4_0.gguf",
                                       "--base", "model-F16.gguf"])
    assert out.exit_code == 0, out.output
    assert cli_env == [(Path("/m/model-F16.gguf"), [Path("/m/model-Q8_0.gguf"), Path("/m/model-Q4_0.gguf")])]
    assert "7.0100" in out.output and "1.001" in out.output and "the sandbox is not there" in out.output
    assert "16 chunks of 512 tokens" in out.output
    listed = CliRunner().invoke(cli.cli, ["models", "compare", "model-Q8_0.gguf", "--json"]).output
    assert json.loads(listed[listed.index("{"):]) == REPORT


def test_only_gguf_files_are_compared(cli_env):
    from click.testing import CliRunner
    from orchestrator import cli
    out = CliRunner().invoke(cli.cli, ["models", "compare", "folder"])
    assert out.exit_code == 2 and "no GGUF model file" in out.output and cli_env == []


def test_the_dialog_measures_the_ticked_models_against_the_base(qapp):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from orchestrator.gui.model_compare import ModelCompareDialog, result_lines
    calls = []
    rows = [row("model-F16.gguf"), row("model-Q8_0.gguf"), row("folder", gguf=False)]
    dialog = ModelCompareDialog(None, rows, lang="en", compare=lambda base, models: calls.append((base, models))
                                or REPORT)
    try:
        assert dialog.list_compare_models.count() == 2   # only GGUF files
        assert dialog.run() is False and calls == []      # nothing ticked
        dialog.list_compare_models.item(1).setCheckState(Qt.Checked)
        dialog.cmb_compare_base.setCurrentIndex(1)        # model-F16.gguf
        assert dialog.run()
        import time
        deadline = time.monotonic() + 10
        while dialog.running and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert calls == [(Path("/m/model-F16.gguf"), [Path("/m/model-Q8_0.gguf")])]
        assert dialog.txt_compare_result.toPlainText() == "\n".join(result_lines(REPORT, "en"))
        assert "to the base 1.001" in dialog.txt_compare_result.toPlainText()
    finally:
        dialog.close()
