"""scripts/sync_ssot.py: finds configs/detailed_project_sources.yml from any folder, lists differences, and with
--write changes only the values (comments and layout stay); configs/project_sources.yml says who reads it."""

import subprocess
import sys

import yaml

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "sync_ssot.py"

PROD = """\
metadata:
  ssot_version: "2.2.0"
  last_updated: "2026-10-01"
inference_backends:
  llama_cpp:
    repo_url: "https://github.com/ggml-org/llama.cpp"
    commit: "b5000"
    note: "new note that is not synced"
  only_in_production:
    commit: "v1"
hardware_targets:
  rockchip:
    rknn_toolkit2:
      version: 2.3.2
      pypi_package: rknn-toolkit2
"""

DETAILED = """\
# Detailed reference - hand-written comments must survive a sync
metadata:
  ssot_version: "2.1.0"   # bumped by the sync script
  last_synced: '2025-12-28'
  maintainer: "Team"

inference_backends:
  llama_cpp:
    repo_url: "https://github.com/ggml-org/llama.cpp"
    commit: b4009           # pinned for reproducibility
    note: "old detailed note, kept"
    learning_path:
      - "step 1"
hardware_targets:
  rockchip:
    rknn_toolkit2:
      version: 2.3.0
      pypi_package: rknn-toolkit2
"""


def run(configs, *args, cwd):
    return subprocess.run([sys.executable, str(SCRIPT), "--configs", str(configs), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def make_configs(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "project_sources.yml").write_text(PROD, encoding="utf-8")
    (configs / "detailed_project_sources.yml").write_text(DETAILED, encoding="utf-8")
    return configs


def test_check_lists_differences_and_changes_nothing(tmp_path):
    configs = make_configs(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = run(configs, cwd=elsewhere)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "inference_backends.llama_cpp.commit: 'b4009' -> 'b5000'" in result.stdout
    assert "metadata.ssot_version: '2.1.0' -> '2.2.0'" in result.stdout
    assert "metadata.last_synced: '2025-12-28' -> '2026-10-01'" in result.stdout
    assert "hardware_targets.rockchip.rknn_toolkit2.version: 2.3 -> 2.3" not in result.stdout
    assert "missing in detailed_project_sources.yml: inference_backends.only_in_production.commit" in result.stdout
    assert (configs / "detailed_project_sources.yml").read_text(encoding="utf-8") == DETAILED
    assert sorted(p.name for p in configs.iterdir()) == ["detailed_project_sources.yml", "project_sources.yml"]
    assert list(elsewhere.iterdir()) == [], "nothing is created in the working directory"


def test_write_changes_only_the_values(tmp_path):
    configs = make_configs(tmp_path)

    result = run(configs, "--write", cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    text = (configs / "detailed_project_sources.yml").read_text(encoding="utf-8")
    expected = (DETAILED.replace('ssot_version: "2.1.0"', 'ssot_version: "2.2.0"')
                .replace("last_synced: '2025-12-28'", "last_synced: '2026-10-01'")
                .replace("commit: b4009 ", "commit: b5000 ")
                .replace("version: 2.3.0", "version: 2.3.2"))
    assert text == expected, "comments, quoting, order and the unsynced note stay as they were"
    data = yaml.safe_load(text)
    assert "only_in_production" not in data["inference_backends"], "no sections are added"
    assert sorted(p.name for p in configs.iterdir()) == ["detailed_project_sources.yml", "project_sources.yml"]
    assert run(configs, cwd=tmp_path).returncode == 0, "in sync afterwards"


def test_a_missing_detailed_file_is_an_error_not_a_copy(tmp_path):
    configs = make_configs(tmp_path)
    (configs / "detailed_project_sources.yml").unlink()
    result = run(configs, "--write", cwd=tmp_path)
    assert result.returncode == 2 and "detailed_project_sources.yml not found" in result.stderr
    assert sorted(p.name for p in configs.iterdir()) == ["project_sources.yml"]


def test_the_repository_files_are_found_from_any_folder(tmp_path):
    cwd = tmp_path / "somewhere"
    cwd.mkdir()
    before = sorted(p.name for p in (REPO_ROOT / "configs").iterdir())
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True, timeout=60)
    assert result.returncode in (0, 1), result.stderr
    assert "not found" not in result.stderr + result.stdout
    assert "detailed_project_sources.yml" in result.stdout, "compared with the real detailed file"
    assert list(cwd.iterdir()) == []
    assert sorted(p.name for p in (REPO_ROOT / "configs").iterdir()) == before, "no detailed_project.sources.yml"


def test_project_sources_header_says_who_reads_it():
    text = (REPO_ROOT / "configs" / "project_sources.yml").read_text(encoding="utf-8")
    header = text.split("metadata:")[0]
    assert "Reads this file" not in header and "Orchestrator: Reads" not in header
    assert "No part of the framework reads it while it runs" in header
    assert "scripts/validate_ssot.py" in header and "scripts/sync_ssot.py" in header
    data = yaml.safe_load(text)
    assert "roadmap" not in data, "the roadmap lives in docs/BLUEPRINT.md"
    assert "Crawler extracts" not in data["metadata"]["purpose"]
