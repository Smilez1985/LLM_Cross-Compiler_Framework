"""The model list of the source catalogue (configs/project_sources.yml, sections tiny_models and models) is current,
checked by scripts/validate_ssot.py and wired as the model suggestions of the Builder, `llm-cli models suggestions`
and the MCP resource lcf://models/suggestions (DECISIONS round 23 item 10)."""

import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core import doc_sources

CATALOGUE = REPO_ROOT / "configs" / "project_sources.yml"


def catalogue_copy(tmp_path, change):
    data = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "project_sources.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_the_shipped_model_list_is_valid_current_and_ungated_by_default():
    data = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    assert doc_sources.model_problems(data) == []
    ids = [m.repo_id for m in doc_sources.model_suggestions()]
    assert ids[0] == "Qwen/Qwen3.5-0.8B" and "HuggingFaceTB/SmolLM2-135M-Instruct" in ids   # small models first
    assert "meta-llama/Llama-3.2-3B-Instruct" not in ids                                    # gated: never a default
    assert "meta-llama/Llama-3.2-3B-Instruct" in [m.repo_id for m in doc_sources.model_suggestions(include_gated=True)]
    # the stale entries of 2025 are gone (TinyLlama 1.1B, Qwen1.5-0.5B, h2o-danube 1.8B, Phi-3-mini)
    assert not {"TinyLlama/TinyLlama-1.1B-Chat-v1.0", "Qwen/Qwen1.5-0.5B-Chat"} & set(ids)
    assert all(m.checked != "unchecked" for m in doc_sources.load_models())
    assert "nothing reads" not in CATALOGUE.read_text(encoding="utf-8").split("models:")[0][-2000:]


def test_validate_ssot_checks_the_model_sections(tmp_path):
    good = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "validate_ssot.py")], capture_output=True,
                          text=True, cwd=tmp_path)
    assert good.returncode == 0, good.stdout + good.stderr
    assert "Model suggestions valid" in good.stdout

    def broken(data):
        data["tiny_models"]["qwen3_5_0_8b"]["repo_id"] = "not an id"
        data["models"]["phi_4_mini"]["size_mb"] = 0
        data["models"]["smollm3_3b"]["gated"] = "no"
    path = catalogue_copy(tmp_path, broken)
    bad = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "validate_ssot.py"), str(path)],
                         capture_output=True, text=True)
    assert bad.returncode == 1
    assert "tiny_models.qwen3_5_0_8b: repo_id" in bad.stdout and "models.phi_4_mini: size_mb" in bad.stdout
    assert "models.smollm3_3b: gated" in bad.stdout


def test_a_broken_entry_is_never_suggested_and_a_missing_gated_counts_as_gated(tmp_path):
    def change(data):
        data["tiny_models"]["qwen3_5_0_8b"]["repo_id"] = "../etc"
        del data["tiny_models"]["qwen3_5_2b"]["gated"]
    path = catalogue_copy(tmp_path, change)
    ids = [m.repo_id for m in doc_sources.model_suggestions(path=path)]
    assert "../etc" not in ids and "Qwen/Qwen3.5-2B" not in ids
    assert doc_sources.model_suggestions(path=tmp_path / "missing.yml") == []


def test_the_builder_offers_the_catalogue_suggestions():
    pytest.importorskip("PySide6")
    from orchestrator.gui import builder_tab
    assert builder_tab.MODEL_SUGGESTIONS == [m.repo_id for m in doc_sources.model_suggestions()]


def test_cli_models_suggestions_lists_them():
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    result = CliRunner().invoke(cli_module.cli, ["-q", "models", "suggestions"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "Qwen/Qwen3.5-0.8B (1746 MB, Q4_K_M, apache-2.0; checked 2026-10-29)" in out
    assert "meta-llama" not in out
    as_json = CliRunner().invoke(cli_module.cli, ["-q", "models", "suggestions", "--json"])
    import json
    rows = json.loads(as_json.output)
    assert rows[0]["repo_id"] == "Qwen/Qwen3.5-0.8B" and rows[0]["small"] is True


def test_the_mcp_resource_lists_them_read_only():
    from types import SimpleNamespace
    from orchestrator.tools.framework_resources import doc_source_resources
    spec = next(s for s in doc_source_resources(SimpleNamespace()) if s.uri == "lcf://models/suggestions")
    text = spec.read({})
    assert "HuggingFaceTB/SmolLM2-135M-Instruct" in text and "meta-llama" not in text
