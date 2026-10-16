"""Residues decided one by one (DECISIONS round 8, N15: remove what is really no longer needed, use what is useful).

- The Qdrant sidecar in device packages is gone (tests/test_deployment.py proves it on a package), and the deployment
  window no longer offers it.
- transparency_manager.py (RepE) is gone: it needs a transformers model in this process, which neither the managed
  llama-server nor a cloud provider can give.
- scripts/ci_image_audit.sh is gone (unpinned dive with the Docker socket); the docker workflow reports the image size
  with the runner's own docker CLI instead, without a socket inside a container.
- The SSOT catalogue uses one size baseline and carries no unsourced performance numbers.
"""

import re

import yaml

from conftest import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def test_the_deployment_window_offers_no_qdrant_sidecar():
    source = (REPO_ROOT / "orchestrator" / "gui" / "deployment_window.py").read_text(encoding="utf-8")
    assert "Qdrant" not in source and "Sidecar" not in source


def test_transparency_manager_is_gone_and_nothing_names_it():
    assert not (REPO_ROOT / "orchestrator" / "Core" / "transparency_manager.py").exists()
    offenders = [str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "orchestrator").rglob("*.py")
                 if re.search(r"transparency_manager|TransparencyManager|repe_pipeline", p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_no_workflow_mounts_the_docker_socket_or_runs_an_image_by_a_tag():
    assert not (REPO_ROOT / "scripts" / "ci_image_audit.sh").exists()
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "docker.sock" not in text, workflow.name
        for line in text.splitlines():
            if "docker run" in line:
                assert not re.search(r"\s[\w./-]+:latest\b", line), (workflow.name, line)


def test_the_docker_workflow_reports_the_image_size_without_a_new_image():
    steps = yaml.safe_load((WORKFLOWS / "docker.yml").read_text(encoding="utf-8"))["jobs"]["build-rockchip"]["steps"]
    report = next(s for s in steps if s.get("name") == "Image size report")
    assert "docker image inspect" in report["run"] and "GITHUB_STEP_SUMMARY" in report["run"]
    assert "docker run" not in report["run"] and "docker save" not in report["run"]


def detailed():
    return yaml.safe_load((REPO_ROOT / "configs" / "detailed_project_sources.yml").read_text(encoding="utf-8"))


def test_every_size_multiplier_has_the_one_fp16_baseline():
    quantization = detailed()["quantization"]
    checked = 0
    for group in ("floating_point", "integer_formats", "gguf_formats"):
        for name, entry in quantization[group].items():
            if not isinstance(entry, dict) or "size_multiplier" not in entry:
                continue
            multiplier = float(str(entry["size_multiplier"]).rstrip("x"))
            assert abs(multiplier - entry["bits"] / 16) < 0.01, (name, entry)
            if "example_size_7b" in entry:
                size = float(str(entry["example_size_7b"]).strip("~GB"))
                assert abs(size - 7 * entry["bits"] / 8) < 0.06, (name, entry)
            checked += 1
    assert checked >= 15


def test_the_catalogue_carries_no_unsourced_speed_numbers_or_stale_claims():
    text = (REPO_ROOT / "configs" / "detailed_project_sources.yml").read_text(encoding="utf-8")
    production = (REPO_ROOT / "configs" / "project_sources.yml").read_text(encoding="utf-8")
    assert not re.search(r"tokens/sec|x faster", text + production)
    assert "NOT for LLM" not in production and "NOT LLM inference" not in text
    hailo = detailed()["hailo_ai"]["note"]
    hailo_production = yaml.safe_load(production)["hardware_targets"]["hailo"]["hailort"]["note"]
    assert "current documentation" in hailo and "current documentation" in hailo_production, "the note says: check"
    pin = detailed()["nvidia_jetson"]["tensorrt_llm"]["pin_note"]
    trt = yaml.safe_load(production)["hardware_targets"]["nvidia"]["tensorrt_llm"]["note"]
    assert "not checked against current releases" in pin and "not checked against current releases" in trt
