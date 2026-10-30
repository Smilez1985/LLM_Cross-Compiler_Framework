"""The build container image on every surface (DECISIONS round 36): the CLI (`build image plan / sources / export /
import`), the MCP read-only status (get_target_capabilities with check_image: labels only) and the GUI window (Build >
Build Container Images...). The same Core functions (Core/build_image.py) stand behind all three; every address here
is a documentation address."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from test_offline_builder_image import GOOD, LAN, Docker, archive

APT = "http://192.0.2.10:3142"


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


def no_docker(monkeypatch, framework, client=None):
    """The framework's build engine talks to `client` (a stand-in), never to a Docker daemon."""
    engine = framework.orchestrator.build_engine
    monkeypatch.setattr(engine, "docker_client", client)
    monkeypatch.setattr(engine, "_build_host_profile", lambda progress: (None, "no probe in the test"))
    return engine


def with_entrypoint(engine):
    """The framework's own entrypoint.sh in the engine's app folder (the test framework's is a temporary folder): every
    staged build context copies it, so the labels of a build, and the ones an import expects, need it."""
    from pathlib import Path
    entrypoint = Path(engine.base_dir) / "entrypoint.sh"
    if not entrypoint.is_file():
        entrypoint.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    return entrypoint


def test_the_cli_plan_names_each_source_and_its_json_carries_labels_only(cli, booted_framework, monkeypatch):
    from orchestrator.Core import build_image
    monkeypatch.setattr(build_image, "local_llama_copy", lambda state: None)
    no_docker(monkeypatch, booted_framework, Docker(here={"debian:bookworm-slim"}))
    booted_framework.config.set("offline_local_ranges", [LAN])
    booted_framework.config.set("build_apt_mirror", "http://198.51.100.7/")
    result = cli.invoke("build", "image", "plan", "Rockchip")
    assert result.code == 0, result.out
    assert "cannot make this image yet" in result.out and "apt packages: NOT local" in result.out
    assert "198.51.100.7" in result.out, "the person sees which address is not local"
    machine = cli.invoke("build", "image", "plan", "Rockchip", "--json")
    data = json.loads(machine.out[machine.out.index("{"):])
    assert data["ready"] is False and {s["kind"] for s in data["sources"]} >= {"base_image", "apt", "pip", "llama_cpp"}
    assert "198.51.100.7" not in machine.out


def test_the_cli_sets_and_judges_the_build_sources(cli, tmp_path):
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, offline_local_ranges=[LAN])   # the settings the command reads (config.yml)
    saved = cli.invoke("build", "image", "sources", "--apt-proxy", APT)
    assert saved.code == 0 and "build_apt_proxy saved" in saved.out
    assert f"build_apt_proxy: {APT} - local" in saved.out
    refused = cli.invoke("build", "image", "sources", "--pip-index", "http://user:pw@192.0.2.11/simple")
    assert refused.code == 2 and "not saved" in refused.out
    cleared = cli.invoke("build", "image", "sources", "--apt-proxy", "")
    assert "build_apt_proxy cleared" in cleared.out and "build_apt_proxy: not set" in cleared.out


def test_the_cli_import_refuses_a_file_whose_digest_does_not_match(cli, booted_framework, tmp_path, monkeypatch):
    docker = Docker()
    no_docker(monkeypatch, booted_framework, docker)
    file = archive(tmp_path / "rk.tar", GOOD)
    result = cli.invoke("build", "image", "import", str(file), "--sha256", "0" * 64, "--yes")
    assert result.code == 1 and "does not match its sha256" in result.out and docker.images.loaded == []


def test_the_cli_export_writes_the_sidecar(cli, booted_framework, tmp_path, monkeypatch):
    image = SimpleNamespace(labels=dict(GOOD), attrs={"Created": "2026", "Size": 10}, id="sha256:" + "c" * 64,
                            tags=["llm-framework/rockchip:last-build"])
    no_docker(monkeypatch, booted_framework, Docker(built=[image]))
    result = cli.invoke("build", "image", "export", "Rockchip", "--to", str(tmp_path / "rk.tar"))
    assert result.code == 0, result.out
    digest = hashlib.sha256((tmp_path / "rk.tar").read_bytes()).hexdigest()
    assert (tmp_path / "rk.tar.sha256").read_text().split()[0] == digest


def test_the_import_compares_with_the_labels_a_build_here_looks_for(booted_framework, tmp_path, monkeypatch):
    """expected_labels is the build's own computation (BuildEngine.image_labels): an image whose INPUTS_LABEL is not
    the one a build of the target here would compute is refused."""
    from orchestrator.Core import build_image
    from orchestrator.Core.builder import IMAGE_LABEL, INPUTS_LABEL
    with_entrypoint(no_docker(monkeypatch, booted_framework, Docker()))
    expected = build_image.expected_labels(booted_framework, "Rockchip")
    assert expected[IMAGE_LABEL] == "Rockchip" and len(expected[INPUTS_LABEL]) == 64
    assert build_image.expected_labels(booted_framework, "Rockchip") == expected, "the same settings: the same labels"
    file = archive(tmp_path / "rk.tar", {IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "a" * 64})
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(build_image.ImageTransferError) as refused:
        build_image.import_image(booted_framework, file, actor="cli", sha256=digest)
    assert refused.value.code == "mismatch" and "other settings" in str(refused.value)
    good = archive(tmp_path / "good.tar", dict(expected))
    docker = Docker()
    result = build_image.import_image(booted_framework, good, actor="cli",
                                      sha256=hashlib.sha256(good.read_bytes()).hexdigest(), client=docker)
    assert result.target == "Rockchip" and docker.images.loaded


def test_the_mcp_status_shows_labels_and_never_an_address(booted_framework, monkeypatch):
    from orchestrator.Core import build_image
    from orchestrator.tools.registry import ToolContext
    monkeypatch.setattr(build_image, "local_llama_copy", lambda state: None)
    no_docker(monkeypatch, booted_framework, Docker(here={"debian:bookworm-slim"}))
    booted_framework.config.set("offline_local_ranges", [LAN])
    booted_framework.config.set("build_apt_proxy", APT)
    result = booted_framework.tool_registry.call("get_target_capabilities", {"target": "Rockchip", "check_image": True},
                                                 ToolContext("mcp"))
    status = result.build_image
    assert status is not None and status.ready is False
    apt = next(s for s in status.sources if s.kind == "apt")
    assert apt.local and "build_apt_proxy" in apt.how
    shown = result.model_dump_json()
    assert "192.0.2.10" not in shown and "needs_person" in status.transfer
    plain = booted_framework.tool_registry.call("get_target_capabilities", {"target": "Rockchip"}, ToolContext("mcp"))
    assert plain.build_image is None, "only on request"


# --- the GUI window ---------------------------------------------------------------------------------------------------

def test_the_window_shows_the_plan_and_saves_the_sources(qapp, booted_framework, monkeypatch, tmp_path):
    from orchestrator.Core import build_image
    from orchestrator.gui.build_image_window import BuildImageDialog
    monkeypatch.setattr(build_image, "local_llama_copy", lambda state: None)
    image = SimpleNamespace(labels=dict(GOOD), attrs={"Created": "2026", "Size": 10}, id="sha256:" + "c" * 64,
                            tags=["llm-framework/rockchip:last-build"])
    no_docker(monkeypatch, booted_framework, Docker(here={"debian:bookworm-slim"}, built=[image]))
    booted_framework.config.set("offline_local_ranges", [LAN])
    dialog = BuildImageDialog(booted_framework, lang="en", select="Rockchip")
    assert dialog.target() == "Rockchip" and "llm-framework/rockchip:last-build" in dialog.list_images.item(0).text()
    dialog.source_edits["build_apt_proxy"].setText(APT)
    dialog.save_sources()
    assert booted_framework.config.get("build_apt_proxy") == APT and "Saved" in dialog.txt_result.toPlainText()
    dialog.source_edits["build_pip_index"].setText("http://user:pw@192.0.2.11/simple")
    dialog.save_sources()
    assert "not saved" in dialog.txt_result.toPlainText()
    dialog.source_edits["build_pip_index"].setText("")
    dialog.check_plan()
    import time
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + 20
    while "plan" in dialog._requests and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    text = dialog.txt_result.toPlainText()
    assert "cannot make this image yet" in text and "apt packages: local" in text, text
    dialog.export_image(str(tmp_path / "rk.tar"))
    deadline = time.time() + 20
    while "export" in dialog._requests and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    assert (tmp_path / "rk.tar.sha256").exists() and "Exported to" in dialog.txt_result.toPlainText()
    dialog.close()


def test_the_menu_opens_the_window(qapp, booted_framework, monkeypatch):
    from orchestrator.gui.menus import build_image as menu
    opened = {}
    monkeypatch.setattr("orchestrator.gui.build_image_window.open_build_images",
                        lambda window, select=None: opened.setdefault("window", window) or "dialog")
    window = SimpleNamespace(framework=booted_framework)
    menu.open_window(window)
    assert opened["window"] is window


def test_the_import_expects_the_labels_of_the_build_context_a_build_stages(booted_framework, tmp_path, monkeypatch):
    """Integration seam (offline-builder-image x development's image inputs): a build computes INPUTS_LABEL on its
    staged build context, whose CONTEXT_COPIES include docker/entrypoint.sh, and with the target's installed digest;
    expected_labels must give exactly that, or every imported image built here with the same settings is refused."""
    from orchestrator.Core import build_image
    from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
    from orchestrator.Core.community_manager import installed_digest
    from orchestrator.utils.build_context import stage_build_context
    engine = no_docker(monkeypatch, booted_framework, Docker())
    folder = engine.resolve_target_dir("Rockchip")
    with_entrypoint(engine)
    staged = stage_build_context(folder, engine.base_dir, tmp_path / "context", "Dockerfile")
    config = BuildConfiguration(build_id="b1", timestamp="", model_source="-", target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir="-", use_gpu=False)
    config.target_pack_sha256 = installed_digest(folder)
    built = engine.image_labels(config, BuildProgress("b1", BuildStatus.QUEUED, ""), staged)[1]
    assert build_image.expected_labels(booted_framework, "Rockchip") == built
