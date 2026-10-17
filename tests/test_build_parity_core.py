"""The core functions the build chain of the GUI parity wave (W1') moved out of orchestrator/cli.py, so the CLI and the
GUI call the same code: TargetManager.validate (targets validate), build_history (build status), settings_view
(config show, config repos) and Ditto's one-run provider/model choice, image pin and save (module generate-ai)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT

ROCKCHIP_YML = REPO_ROOT / "targets" / "Rockchip" / "target.yml"
DIGEST = "sha256:" + "ab" * 32


# --- TargetManager.validate ------------------------------------------------------------------------------------

def targets_folder(tmp_path) -> Path:
    folder = tmp_path / "targets"
    (folder / "Rockchip").mkdir(parents=True)
    (folder / "Rockchip" / "target.yml").write_text(ROCKCHIP_YML.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "MyBoard").mkdir()
    (folder / "MyBoard" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "My board", "architecture_family": "aarch64"}}), encoding="utf-8")
    caps = yaml.safe_load(ROCKCHIP_YML.read_text(encoding="utf-8"))["capabilities"]
    caps["default_board"] = "rk9999"   # not one of its boards
    (folder / "Broken").mkdir()
    (folder / "Broken" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "Broken", "architecture_family": "aarch64"}, "capabilities": caps}), encoding="utf-8")
    return folder


def manager(folder):
    from orchestrator.Core.target_manager import TargetManager
    return TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(folder)), resolve_path=Path))


class TestTargetValidate:

    def test_a_valid_target_by_folder_name_in_any_case(self, tmp_path):
        check = manager(targets_folder(tmp_path)).validate("rockchip")
        assert check.found and check.valid and check.declared and not check.no_capabilities
        assert check.target_id == "Rockchip" and check.boards > 3 and "gguf" in check.formats

    def test_schema_errors_by_field(self, tmp_path):
        check = manager(targets_folder(tmp_path)).validate("Broken")
        assert check.found and not check.valid and check.declared
        assert check.errors and all(field.startswith("capabilities") for field, _ in check.errors)
        assert any("rk9999" in message for _, message in check.errors)

    def test_no_capabilities_is_a_warning_not_an_error(self, tmp_path):
        check = manager(targets_folder(tmp_path)).validate("MyBoard")
        assert check.found and check.valid and check.no_capabilities and not check.errors

    def test_unknown_target(self, tmp_path):
        check = manager(targets_folder(tmp_path)).validate("Nope")
        assert not check.found and not check.valid and not check.no_capabilities

    def test_reread_sees_a_file_edited_after_loading(self, tmp_path):
        folder = targets_folder(tmp_path)
        tm = manager(folder)
        assert tm.validate("MyBoard").no_capabilities
        good = yaml.safe_load(ROCKCHIP_YML.read_text(encoding="utf-8"))
        good["metadata"] = {"name": "My board", "architecture_family": "aarch64"}
        (folder / "MyBoard" / "target.yml").write_text(yaml.safe_dump(good), encoding="utf-8")
        assert tm.validate("MyBoard").no_capabilities, "without reread: the loaded state (as the CLI)"
        fresh = tm.validate("MyBoard", reread=True)
        assert fresh.valid and fresh.declared
        (folder / "MyBoard" / "target.yml").write_text(": not yaml [", encoding="utf-8")
        assert not tm.validate("MyBoard", reread=True).found


# --- build_history ------------------------------------------------------------------------------------------------

def record(**values):
    import os
    data = dict(build_id="req_a_001", request_id="req_a", pid=os.getpid(), process_started=None, status="completed",
                stage="Done", progress=100, model="org/model", target="Rockchip", format="gguf",
                quantization="Q4_K_M", board="rk3588", output_dir="/out/a", started="2026-10-11T10:00:00",
                error=None)
    data.update(values)
    return data


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def read(self):
        return list(self.rows)


class TestBuildHistory:

    @pytest.fixture
    def history(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_history, build_records, request_store
        cache = tmp_path / "cache"
        folder = build_records.records_dir(cache)
        build_records.write_record(folder, record())
        build_records.write_record(folder, record(build_id="req_b_001", request_id="req_b", status="failed",
                                                  error="out of disk", started="2026-10-11T11:00:00"))
        build_records.write_record(folder, record(build_id="req_b_002", request_id="req_b", status="failed",
                                                  error="still out of disk", started="2026-10-11T11:30:00"))
        requests = [{"request_id": "req_q", "status": "queued", "progress": 0, "program": "mcp",
                     "started": "2026-10-11T12:00:00", "summary": {"model": "org/q", "target": "Rockchip"}},
                    {"request_id": "req_b", "status": "error", "program": "cli", "owner": "",
                     "errors": ["out of disk"], "started": "2026-10-11T11:00:00", "summary": {"model": "org/model"}}]
        monkeypatch.setattr(request_store, "for_config", lambda config: FakeStore(requests))
        config = SimpleNamespace(get=lambda key, default=None: str(cache) if key == "cache_dir" else default,
                                 user_state_dir=None)
        return build_history.read_history(config, Path)

    def test_every_build_and_every_waiting_request_newest_first(self, history):
        rows = history.rows()
        assert [(r.kind, r.request_id) for r in rows] == [("request", "req_q"), ("build", "req_b"),
                                                          ("build", "req_b"), ("build", "req_a")]
        queued = rows[0].values()
        assert queued["build"] == "-" and queued["status"] == "queued" and queued["model"] == "org/q"
        assert queued["where"] == "mcp"
        assert rows[1].values()["where"] == "cli", "a build's program comes from its request summary"

    def test_details_with_every_attempt_in_order(self, history):
        details = history.details("req_b")
        assert details.request["status"] == "error"
        assert [a["build_id"] for a in details.attempts] == ["req_b_001", "req_b_002"]
        assert details.proposal_requests == ["req_b"]
        by_build = history.details("req_a_001")
        assert by_build.request is None and [a["build_id"] for a in by_build.attempts] == ["req_a_001"]
        assert history.details("req_nothere") is None

    def test_field_lists_keep_the_cli_order(self, history):
        from orchestrator.Core.build_history import RECORD_FIELDS, REQUEST_FIELDS, record_rows, request_rows
        attempt = history.details("req_b").attempts[-1]
        rows = dict(record_rows(attempt))
        assert [name for name, _ in record_rows(attempt)] == list(RECORD_FIELDS)
        assert rows["output"] == "/out/a" and rows["error"] == "still out of disk" and rows["progress"] == "100%"
        summary = dict(request_rows(history.details("req_b").request))
        assert [name for name, _ in request_rows({})] == list(REQUEST_FIELDS)
        assert summary["where"] == "cli" and summary["model"] == "org/model" and summary["owner"] == ""


# --- settings_view ------------------------------------------------------------------------------------------------

class TestSettingsView:

    def test_effective_settings_redact_secrets_and_name_the_source(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.settings_view import effective_settings
        token = "hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH"
        (tmp_path / "cfg").mkdir()
        (tmp_path / "cfg" / "config.yml").write_text(yaml.safe_dump(
            {"hf_token": token, "my_service": {"password": "plain-pw"}}), encoding="utf-8")
        shown = effective_settings(ConfigManager(tmp_path / "cfg"))
        assert shown["hf_token"]["value"] == "REDACTED" and shown["my_service"]["value"] == {"password": "REDACTED"}
        assert shown["my_service"]["source"].startswith("file ")
        assert shown["ai_provider"]["source"] == "default"
        assert token not in json.dumps(shown, default=str) and "plain-pw" not in json.dumps(shown, default=str)

    def test_add_list_and_remove_repository_overrides(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.settings_view import add_repo_override, remove_repo_override, repo_overrides
        cfg = ConfigManager(tmp_path / "cfg")
        assert add_repo_override(cfg, "llama.cpp", "https://git.example.com/l.git") == "LLAMA_CPP_REPO_OVERRIDE"
        cfg.set("source_repositories", {**cfg.get("source_repositories"), "old": "https://old.example/x.git"})
        assert repo_overrides(cfg) == {"llama.cpp": "https://git.example.com/l.git", "old": "https://old.example/x.git"}
        saved = yaml.safe_load((tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8"))
        assert saved["source_repositories"]["llama.cpp"] == {"url": "https://git.example.com/l.git"}
        assert remove_repo_override(cfg, "LLAMA.CPP") is True
        assert remove_repo_override(cfg, "llama.cpp") is False
        assert "llama.cpp" not in repo_overrides(cfg)

    @pytest.mark.parametrize("name", ["...", "äöü"])
    def test_a_name_without_a_variable_is_refused(self, tmp_path, name):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.settings_view import RepoOverrideError, add_repo_override, repo_overrides
        cfg = ConfigManager(tmp_path / "cfg")
        with pytest.raises(RepoOverrideError) as refused:
            add_repo_override(cfg, name, "https://x.example/x.git")
        assert refused.value.code == "invalid_name" and repo_overrides(cfg) == {}

    def test_a_failed_save_leaves_the_setting_as_it_was(self, tmp_path, monkeypatch):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.settings_view import (RepoOverrideError, add_repo_override, remove_repo_override,
                                                     repo_overrides)
        cfg = ConfigManager(tmp_path / "cfg")
        add_repo_override(cfg, "keep", "https://k.example/k.git")
        monkeypatch.setattr(cfg, "save_user_config", lambda: False)
        for change in (lambda: add_repo_override(cfg, "new", "https://n.example/n.git"),
                       lambda: remove_repo_override(cfg, "keep")):
            with pytest.raises(RepoOverrideError) as failed:
                change()
            assert failed.value.code == "not_saved" and "Not saved" in str(failed.value)
            assert repo_overrides(cfg) == {"keep": "https://k.example/k.git"}, "also unchanged in this program"


# --- Ditto: one-run provider/model, image pin, save -----------------------------------------------------------------

class TestDittoForOneRun:

    def ditto(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.ditto_manager import DittoCoder
        config = ConfigManager(tmp_path / "cfg")
        config.set("ai_provider", "anthropic")
        return DittoCoder(config_manager=config), config

    def test_provider_problem_names_unknown_and_removed_providers(self):
        from orchestrator.Core.ditto_manager import provider_problem
        from orchestrator.llm import load_catalog
        catalog = load_catalog()
        assert provider_problem(catalog, None) is None and provider_problem(catalog, "ollama") is None
        assert "Unknown AI provider 'nope'" in provider_problem(catalog, "nope")
        removed = provider_problem(catalog, "claude_code")
        assert removed and "Unknown AI provider" not in removed

    def test_the_saved_settings_stay_unchanged(self, tmp_path):
        ditto, config = self.ditto(tmp_path)
        run = ditto.for_run("ollama", "small-model")
        assert run is not ditto and run.config.get("ai_provider") == "ollama"
        assert run.config.get("ai_model") == "small-model"
        assert run.config.config_values["ai_provider"].value == "ollama"
        assert run.client.primary_provider().id == "ollama"
        assert config.get("ai_provider") == "anthropic" and ditto.client.primary_provider().id == "anthropic"
        assert config.get("ai_model") != "small-model"
        assert run.config.get("offline_mode") == config.get("offline_mode"), "every other value is the saved one"
        assert ditto.for_run(None, None) is ditto

    def test_a_value_set_during_the_run_stays_in_the_run(self, tmp_path):
        """Review: RunSettings handed set() on to the saved settings, so anything that set a value on the run's
        settings changed the person's own (and a later save wrote it)."""
        ditto, config = self.ditto(tmp_path)
        run = ditto.for_run("ollama", "small-model")
        assert run.config.set("ai_model", "other-model") is True
        assert run.config.set("ai_temperature", 0.1) is True
        assert run.config.get("ai_model") == "other-model" and run.config.get("ai_temperature") == 0.1
        assert config.get("ai_model") != "other-model" and config.get("ai_temperature") != 0.1
        assert config.get("ai_provider") == "anthropic"

    def test_an_unknown_provider_is_refused(self, tmp_path):
        from orchestrator.Core.ditto_manager import DittoError
        ditto, _ = self.ditto(tmp_path)
        with pytest.raises(DittoError, match="Unknown AI provider"):
            ditto.for_run("nope")

    def definition(self, base_os="debian:bookworm-slim", **extra):
        from orchestrator.Core.ditto_manager import ModuleDefinition
        return ModuleDefinition(module_name="Board", architecture="aarch64", sdk="none", base_os=base_os,
                                packages=["cmake"], cpu_flags="", cmake_flags="", setup_commands="",
                                quantization_logic="", **extra)

    def test_pin_base_image_pins_a_tag_and_keeps_a_digest(self, monkeypatch):
        from orchestrator.Core import ditto_manager
        from orchestrator.utils import image_registry
        asked = []

        def inspect(reference, offline=False, client=None):
            asked.append((reference, offline))
            return SimpleNamespace(pinned=f"debian:bookworm-slim@{DIGEST}")

        monkeypatch.setattr(image_registry, "inspect_image", inspect)
        pinned = ditto_manager.pin_base_image(self.definition(), offline=False)
        assert pinned.base_os == f"debian:bookworm-slim@{DIGEST}" and asked == [("debian:bookworm-slim", False)]
        kept = self.definition(base_os=f"debian@{DIGEST}")
        assert ditto_manager.pin_base_image(kept) is kept and len(asked) == 1

    def test_pin_base_image_reports_offline_mode(self):
        from orchestrator.Core.ditto_manager import pin_base_image
        from orchestrator.utils.image_registry import ImageLookupError
        with pytest.raises(ImageLookupError) as refused:
            pin_base_image(self.definition(), offline=True)
        assert "offline" in str(refused.value) and refused.value.next

    def test_save_module_passes_the_attached_knowledge_on(self, tmp_path, monkeypatch):
        from orchestrator.Core import ditto_manager
        ditto, _ = self.ditto(tmp_path)
        seen = {}

        def generate(self, data, framework_manager=None, knowledge_roots=None, profile=None):
            seen.update(data=data, roots=knowledge_roots)
            return tmp_path / "targets" / "Board"

        monkeypatch.setattr(ditto_manager.ModuleGenerator, "generate_module", generate)
        ditto.save_module(self.definition(base_os=f"debian@{DIGEST}"), tmp_path / "targets", module_name="Board",
                          knowledge_roots=["https://docs.example/board"])
        assert seen["roots"] == ["https://docs.example/board"] and seen["data"]["provenance"] == "ai-generated"

    def test_save_module_keeps_refusing_prebuilt_cuda_and_unexplained_images(self, tmp_path):
        ditto, _ = self.ditto(tmp_path)
        with pytest.raises(ValueError, match="nvidia/cuda"):
            ditto.save_module({"module_name": "B", "base_os": f"nvidia/cuda:12.4.1-devel-ubuntu22.04@{DIGEST}"},
                              tmp_path / "targets")
        with pytest.raises(ValueError, match="base_image_reason"):
            ditto.save_module({"module_name": "B", "base_os": f"registry.example/vendor/img@{DIGEST}"},
                              tmp_path / "targets")
