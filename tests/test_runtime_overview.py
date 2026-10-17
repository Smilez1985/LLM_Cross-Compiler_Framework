"""
The core functions behind `llm-cli ditto runtime ...` that the GUI's Ditto > Local Runtime... window calls too (W1':
one code path): the status overview, the model offer after ensure, the llama.cpp pin, the container-build switch and
the Cancel of the prebuilt download. The CLI output stays as it was; these tests pin that the CLI takes its values from
the same calls.

No real build, no network and no Docker: the fakes of the self-built runtime tests.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from orchestrator.runtime import runtime_mode
from orchestrator.runtime.build import source as source_module
from orchestrator.runtime.runtime_overview import installed_json, installed_summary, overview
from orchestrator.utils.download import DownloadStopped

from test_runtime_selfbuilt import (APU, PIN, NoNetwork, _legacy_archive, machine, no_source,  # noqa: F401
                                    runtime)


def _cli(monkeypatch, rt, *args):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
    return CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", *args])


class TestOverview:

    def test_the_cli_json_is_the_overview_of_the_same_call(self, tmp_path, monkeypatch, no_source):
        rt = runtime(tmp_path)
        rt.install()
        view = overview(rt)
        result = _cli(monkeypatch, rt, "status", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == json.loads(json.dumps(view.as_json()))
        assert view.installed.origin == "self-built" and view.backend == "vulkan" and view.gpu_after_cpu
        assert view.status.running is False and view.preferred is None and not view.chosen_differs

    def test_the_built_row_names_the_machine_only_for_an_imported_build(self, tmp_path, no_source):
        made = runtime(tmp_path / "pc")
        made.install(backend="cpu")
        facts = overview(made).build_facts()
        assert facts["placement"] == "native" and facts["duration_s"] == 42.0 and facts["built_on"] is None
        archive = made.export_build(tmp_path / "b.tar.gz")
        laptop = runtime(tmp_path / "laptop")
        laptop.import_build(archive, confirm=lambda info: True)
        imported = overview(laptop).build_facts()
        assert imported["built_on"] == "linux/x86_64" and imported["built_at"]

    def test_nothing_installed_and_the_prebuilt_archive_have_no_built_row(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        view = overview(rt)
        assert view.installed is None and view.build_facts() is None
        _legacy_archive(rt)
        rt._update_active(use="prebuilt")
        view = overview(rt)
        assert view.installed.origin == "prebuilt" and view.build_facts() is None
        assert installed_summary(view.installed) == "prebuilt, not self-built: linux-x64-cpu (cpu)"

    def test_problems_already_named_as_notes_are_not_repeated(self, tmp_path):
        rt = runtime(tmp_path)
        view = overview(rt)
        inst = SimpleNamespace(notes=["the active build is broken", "the CPU fallback build is used"])
        view.status = view.status.model_copy(update={"problems": ["the active build is broken", "another"]})
        assert view.extra_problems() == ["the active build is broken", "another"], "nothing in use: every problem"
        view.status = SimpleNamespace(**{**view.status.__dict__, "installed": inst})
        assert view.extra_problems() == ["another"]

    def test_the_chosen_tier_and_the_plan_wording(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        view = overview(rt)
        other = next(m.tier for m in rt.catalog.models if m.tier != view.model.tier)
        rt._update_active(tier=other)
        view = overview(rt)
        assert view.preferred == other and view.chosen_differs
        view.backend, view.backend_reason = "cpu", "no GPU"
        assert not view.gpu_after_cpu
        view.backend, view.backend_reason = "cuda", ""
        assert not view.gpu_after_cpu, "no reason: the plan names the backend alone"

    def test_installed_json_and_summary_of_the_cpu_fallback(self, tmp_path, no_source):
        rt = runtime(tmp_path)
        rt.install(backend="cpu")
        inst = rt.installed().model_copy(update={"role": "fallback"})
        assert ", CPU fallback" in installed_summary(inst) and PIN.commit[:8] in installed_summary(inst)
        data = installed_json(inst)
        assert data["label"] == "self-built" and data["path"] == str(inst.path) and installed_json(None) is None


class TestModelOffer:

    def test_the_offer_is_the_recommendation_or_the_tier_chosen_earlier(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        offer = runtime_mode.model_offer(rt)
        model, why = rt.choose_tier()
        assert offer.model.tier == model.tier and offer.why == why and offer.needed and offer.state == "missing"
        assert offer.folder == rt.models_dir
        other = next(m.tier for m in rt.catalog.models if m.tier != model.tier)
        rt._update_active(tier=other)
        offer = runtime_mode.model_offer(rt)
        assert offer.model.tier == other and offer.why == "chosen earlier"

    def test_a_model_that_is_there_is_not_offered(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        model = runtime_mode.model_offer(rt).model
        rt.models_dir.mkdir(parents=True)
        rt.model_path(model).write_bytes(b"x")
        offer = runtime_mode.model_offer(rt)
        assert offer.state == "unverified" and not offer.needed

    def test_permissive_follows_the_licence_list(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        offer = runtime_mode.model_offer(rt)
        offer.model = offer.model.model_copy(update={"license": "Apache-2.0"})
        assert offer.permissive
        offer.model = offer.model.model_copy(update={"license": "llama3.2"})
        assert not offer.permissive

    def test_the_cli_ensure_offers_what_model_offer_says(self, tmp_path, monkeypatch):
        rt = runtime(tmp_path, hw=machine([]))
        calls = []
        real = runtime_mode.model_offer
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: calls.append(r) or real(r))
        monkeypatch.setattr(runtime_mode, "ensure", lambda *a, **k: runtime_mode.EnsureResult("skipped", "bare-metal",
                                                                                              []))
        result = _cli(monkeypatch, rt, "ensure")
        assert result.exit_code == 0, result.output
        assert calls == [rt] and "Model for Ditto:" in result.output and "Not downloaded without a terminal" in \
            result.output


class TestPinAndSwitches:

    def test_catalog_pin(self, tmp_path):
        assert source_module.catalog_pin() == PIN
        assert source_module.catalog_pin(runtime(tmp_path).catalog) == PIN
        empty = SimpleNamespace(llama_cpp=SimpleNamespace(source=None))
        with pytest.raises(source_module.SourceError, match="no llama_cpp.source"):
            source_module.catalog_pin(empty)

    def test_the_cli_reports_a_missing_pin_as_before(self, tmp_path, monkeypatch):
        monkeypatch.setattr(source_module, "catalog_pin", lambda catalog=None: (_ for _ in ()).throw(
            source_module.SourceError("configs/llama_runtime.yml has no llama_cpp.source (the pinned commit).")))
        result = _cli(monkeypatch, runtime(tmp_path), "import-source", __file__)
        assert result.exit_code == 1 and "has no llama_cpp.source (the pinned commit)." in result.output

    def test_container_build_requested(self, monkeypatch):
        monkeypatch.delenv("LCF_CONTAINER_BUILD", raising=False)
        assert runtime_mode.container_build_requested() is False
        monkeypatch.setenv("LCF_CONTAINER_BUILD", "1")
        assert runtime_mode.container_build_requested() is True

    def test_a_set_stop_event_ends_the_prebuilt_download_before_anything_is_installed(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]), session=NoNetwork())
        stop = threading.Event()
        stop.set()
        with pytest.raises(DownloadStopped):
            rt.install_prebuilt(stop=stop)
        assert rt.installed() is None and not list(rt.root.glob(f"{rt.build}-*"))
