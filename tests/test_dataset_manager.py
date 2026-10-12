"""
The perplexity check of converted models without any pulled image (WP11a, decisions D1 and D23): the self-built
llama-perplexity of this machine runs natively for catalog models, asks for consent (written to the audit log) for
every other model, and runs in the Docker sandbox only as the opt-in, in the build's own locally built image.
Failures are explicit errors, never the old sentinel values.
"""

import hashlib
import json
from pathlib import Path

import pytest

from orchestrator.Core import dataset_manager as dm
from orchestrator.Core.dataset_manager import DatasetManager, PerplexityError, parse_perplexity
from orchestrator.runtime import server_policy
from orchestrator.runtime.build.executor import BuildExecutor, BuildResult, ContainerEngine
from orchestrator.runtime.build.recipe import ToolchainInfo, plan_build
from orchestrator.runtime.build.store import BuildStore
from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.profile import read_profile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog()
Z1 = read_profile(ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_linux_z1_extreme.txt")
GCC = ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="gcc-13.3.0", cmake="3.28.3", openmp=True)

# The end of a real llama-perplexity run (tools/perplexity/perplexity.cpp at the pin prints the final line).
OUTPUT = """perplexity: tokenizing the input ..
perplexity: tokenization took 12.3 ms
perplexity: calculating perplexity over 4 chunks, n_ctx=512, batch_size=2048, n_seq=4
perplexity: 1.82 seconds per pass - ETA 0.10 minutes
[1]8.1234,[2]9.0021,[3]8.7710,[4]8.9013,
Final estimate: PPL = 8.9013 +/- 0.41230
"""


class Exec:
    def __init__(self, builder_image=None):
        self.builder_image = builder_image

    def build(self, plan, *, source, work, jobs, toolchain=None, ccache_dir=None):
        dirs = BuildExecutor._prepare(work)
        files = {}
        for name in plan.targets:
            (dirs["bin"] / name).write_text(f"#!/bin/sh\necho {name}\n")
            (dirs["bin"] / name).chmod(0o755)
            files[name] = hashlib.sha256((dirs["bin"] / name).read_bytes()).hexdigest()
        return BuildResult(build_id=plan.build_id, work=Path(work), bin_dir=dirs["bin"], files=files, links={},
                           duration_s=1.0, jobs=1, placement=plan.placement, builder_image=self.builder_image)


class Verified:
    def verify(self, plan, bin_dir, **kw):
        return VerifyReport([Check("format", True), Check("version", True)], ran_binaries=True)


def stored(tmp_path, placement="native", builder_image=None, activate=True):
    store = BuildStore(tmp_path / "state")
    toolchain = GCC if placement == "native" else ToolchainInfo(host_os="linux", host_arch="x86_64")
    plan = plan_build(Z1, purpose="host-runtime", placement=placement, backend="cpu", catalog=CATALOG,
                      toolchain=toolchain)
    build = store.build(plan, source=tmp_path, executor=Exec(builder_image), verifier=Verified(),
                        host=("linux", "x86_64"))
    if activate:
        store.activate(build.build_id)
    return store, build


class Runner:
    def __init__(self, rc=0, out="", err=OUTPUT):
        self.rc, self.out, self.err, self.calls = rc, out, err, []

    def __call__(self, argv, timeout, cwd, env):
        self.calls.append((list(argv), cwd, dict(env)))
        return self.rc, self.out, self.err


class Audit:
    def __init__(self, fail=False):
        self.fail, self.entries = fail, []

    def record(self, actor, tool, tier, decision, outcome, arguments=None, approved=None, detail=""):
        if self.fail:
            raise OSError("disk full")
        self.entries.append({"tool": tool, "outcome": outcome, "approved": approved, "arguments": arguments})


class NoDocker:
    """A docker client that fails the test when anything is pulled or run."""

    def __getattr__(self, name):
        pytest.fail(f"Docker was used ({name}): the native check needs no image")


class Framework:
    config = None

    def __init__(self, registry=None):
        self.registry = registry

    def get_component(self, name):
        if name == "docker_client":
            return NoDocker()
        return self.registry if name == "tool_registry" else None


def model_file(tmp_path, data=b"GGUF untrusted conversion"):
    path = tmp_path / "model-Q4_K_M.gguf"
    path.write_bytes(data)
    text = tmp_path / "eval.txt"
    text.write_text("The quick brown fox. " * 400)
    return path, text


def manager(tmp_path, store, catalog=CATALOG, **kw):
    return DatasetManager(Framework(), store=store, catalog=catalog, platform=("linux", "x86_64"), **kw)


def trusting(model: Path):
    """The catalog with a model whose pin is the file's sha256 (a catalog model, trusted)."""
    data = CATALOG.model_dump()
    data["models"][0]["sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
    return type(CATALOG).model_validate(data)


class TestParsing:

    def test_final_estimate(self):
        assert parse_perplexity(OUTPUT) == 8.9013
        assert parse_perplexity("final estimate: ppl = 12.5 +/- 1") == 12.5

    def test_explicit_errors_instead_of_sentinel_values(self):
        with pytest.raises(PerplexityError, match="at least 1024 tokens"):
            parse_perplexity("perplexity: you need at least 1024 tokens to evaluate perplexity with a context of 512")
        with pytest.raises(PerplexityError, match="no final estimate"):
            parse_perplexity("garbage")
        source = Path(dm.__file__).read_text(encoding="utf-8")
        assert "999.99" not in source and "888.88" not in source


class TestNative:

    def test_a_catalog_model_runs_natively_with_the_self_built_program_and_no_image(self, tmp_path, monkeypatch):
        store, build = stored(tmp_path)
        model, text = model_file(tmp_path)
        runner = Runner()
        monkeypatch.setenv("LLAMA_ARG_MODEL", "/elsewhere.gguf")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
        run = manager(tmp_path, store, catalog=trusting(model), runner=runner).run_perplexity(model, text)
        assert run.ppl == 8.9013 and run.trusted and run.where == "native" and run.build_id == build.build_id
        argv, cwd, env = runner.calls[0]
        assert argv[0] == str(build.bin_dir / "llama-perplexity"), "the self-built program, never one from PATH"
        assert argv[argv.index("-m") + 1] == str(model.resolve()) and argv[argv.index("-c") + 1] == "512"
        assert server_policy.forbidden_in(argv) == []
        assert set(env) <= {"HOME", "PATH", "LANG", "LC_ALL"}, "a cleared environment"
        assert "LLAMA_ARG_MODEL" not in env and "HTTPS_PROXY" not in env

    def test_an_untrusted_model_needs_consent_written_to_the_audit_log_first(self, tmp_path):
        store, _ = stored(tmp_path)
        model, text = model_file(tmp_path)
        runner, audit = Runner(), Audit()
        m = manager(tmp_path, store, runner=runner, audit=audit)
        with pytest.raises(PerplexityError, match="consent"):
            m.measure_perplexity(model, text)
        assert runner.calls == [] and audit.entries == []
        asked = []
        assert m.measure_perplexity(model, text, consent=lambda info: asked.append(info) or True) == 8.9013
        assert asked[0]["sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
        assert "natively" in asked[0]["runs"]
        assert audit.entries[-1]["approved"] is True and audit.entries[-1]["tool"] == dm.AUDIT_TOOL
        assert len(runner.calls) == 1

    def test_declined_or_unrecorded_consent_runs_nothing(self, tmp_path):
        store, _ = stored(tmp_path)
        model, text = model_file(tmp_path)
        runner, audit = Runner(), Audit()
        with pytest.raises(PerplexityError, match="declined"):
            manager(tmp_path, store, runner=runner, audit=audit).measure_perplexity(model, text,
                                                                                    consent=lambda info: False)
        assert audit.entries[-1]["approved"] is False and audit.entries[-1]["outcome"] == "declined"
        with pytest.raises(PerplexityError, match="audit log"):
            manager(tmp_path, store, runner=runner, audit=Audit(fail=True)).measure_perplexity(
                model, text, consent=lambda info: True)
        with pytest.raises(PerplexityError, match="audit log"):
            manager(tmp_path, store, runner=runner).measure_perplexity(model, text, consent=lambda info: True)
        assert runner.calls == []

    def test_the_framework_audit_log_is_used(self, tmp_path):
        from orchestrator.tools.audit import AuditLog
        store, _ = stored(tmp_path)
        model, text = model_file(tmp_path)

        class Registry:
            audit = AuditLog(tmp_path / "audit.db")

        m = DatasetManager(Framework(Registry()), store=store, catalog=CATALOG, platform=("linux", "x86_64"),
                           runner=Runner())
        m.measure_perplexity(model, text, consent=lambda info: True)
        entry = Registry.audit.entries(5)[0]
        assert entry["tool"] == dm.AUDIT_TOOL and entry["approved"] == 1 and entry["outcome"] == "approved"

    def test_no_self_built_build_no_run(self, tmp_path):
        model, text = model_file(tmp_path)
        runner = Runner()
        with pytest.raises(PerplexityError, match="ditto runtime install"):
            manager(tmp_path, BuildStore(tmp_path / "empty"), runner=runner).measure_perplexity(
                model, text, consent=lambda info: True)
        assert runner.calls == []

    def test_a_changed_program_or_another_commit_is_not_used(self, tmp_path):
        store, build = stored(tmp_path)
        model, text = model_file(tmp_path)
        (build.bin_dir / "llama-perplexity").write_text("#!/bin/sh\necho changed\n")
        with pytest.raises(PerplexityError, match="no self-built llama-perplexity"):
            manager(tmp_path, store, catalog=trusting(model), runner=Runner()).measure_perplexity(model, text)

    def test_failures_are_explicit(self, tmp_path):
        store, _ = stored(tmp_path)
        model, text = model_file(tmp_path)
        m = manager(tmp_path, store, catalog=trusting(model), runner=Runner(rc=1, err="error: failed to load model"))
        with pytest.raises(PerplexityError, match="failed to load model"):
            m.measure_perplexity(model, text)
        with pytest.raises(PerplexityError, match="not a GGUF"):
            m.measure_perplexity(text, text)
        with pytest.raises(PerplexityError, match="context length"):
            m.measure_perplexity(model, text, context_length=10)

    def test_comparison_lists_failures_after_the_measured_models(self, tmp_path):
        store, _ = stored(tmp_path)
        good, text = model_file(tmp_path)
        bad = tmp_path / "missing.gguf"
        report = manager(tmp_path, store, catalog=trusting(good), runner=Runner()).compare_quantizations(
            None, [bad, good], text)
        assert [r["name"] for r in report["ranking"]] == [good.name, bad.name]
        assert report["ranking"][1]["error"] and "Best model is" in report["recommendation"]
        assert json.dumps(report)


class Engine(ContainerEngine):
    def __init__(self, reason=None, images=(), rc=0):
        self.reason, self.images, self.rc, self.runs = reason, set(images), rc, []

    def unavailable(self):
        return self.reason

    def image_exists(self, tag):
        return tag in self.images

    def build_image(self, *a, **k):  # pragma: no cover - never called
        pytest.fail("the perplexity check builds no image")

    def pull(self, *a, **k):  # pragma: no cover - never called
        pytest.fail("nothing is ever pulled")

    def run(self, image, command, *, mounts, env, options, log, timeout):
        self.runs.append({"image": image, "command": list(command), "mounts": list(mounts), "env": dict(env),
                          "options": dict(options)})
        log.write_text(OUTPUT)
        return self.rc


class TestSandbox:
    IMAGE = "llm-framework/llama-builder:ubuntu-24.04-0123456789ab"

    def test_the_sandbox_runs_the_build_in_its_own_local_image_without_network(self, tmp_path):
        store, build = stored(tmp_path, placement="container", builder_image=self.IMAGE)
        model, text = model_file(tmp_path)
        engine, runner = Engine(images={self.IMAGE}), Runner()
        run = manager(tmp_path, store, engine=engine, runner=runner).run_perplexity(model, text, sandbox=True)
        assert run.where == "sandbox" and not run.trusted and run.ppl == 8.9013 and runner.calls == []
        call = engine.runs[0]
        assert call["image"] == self.IMAGE and not call["image"].startswith("ghcr.io")
        assert call["options"]["network_mode"] == "none" and call["options"]["read_only"] is True
        assert call["options"]["cap_drop"] == ["ALL"]
        assert all(m.read_only for m in call["mounts"]) and {m.target for m in call["mounts"]} == {
            "/opt/llama/bin", "/models/model.gguf", "/data/text.txt"}
        assert next(m for m in call["mounts"] if m.target == "/opt/llama/bin").source == build.bin_dir

    def test_the_sandbox_needs_a_container_build_and_its_local_image(self, tmp_path):
        model, text = model_file(tmp_path)
        store, _ = stored(tmp_path)  # a native build has no image to run in
        with pytest.raises(PerplexityError, match="--placement container"):
            manager(tmp_path, store, engine=Engine()).measure_perplexity(model, text, sandbox=True)
        store, _ = stored(tmp_path / "c", placement="container", builder_image=self.IMAGE)
        with pytest.raises(PerplexityError, match="never pulled"):
            manager(tmp_path, store, engine=Engine(images=())).measure_perplexity(model, text, sandbox=True)
        with pytest.raises(PerplexityError, match="not available"):
            manager(tmp_path, store, engine=Engine(reason="no daemon")).measure_perplexity(model, text, sandbox=True)

    def test_an_image_name_from_a_manifest_must_be_a_builder_image(self, tmp_path):
        model, text = model_file(tmp_path)
        for forged in ("ghcr.io/smilez1985/llm-runtime:latest", "llm-framework/rockchip:latest",
                       "llm-framework/llama-builder:debian-12-0123456789ab --privileged"):
            store, _ = stored(tmp_path / str(abs(hash(forged))), placement="container", builder_image=forged)
            engine = Engine(images={forged})
            with pytest.raises(PerplexityError, match="never pulled"):
                manager(tmp_path, store, engine=engine).measure_perplexity(model, text, sandbox=True)
            assert engine.runs == []

    def test_no_runtime_image_anywhere(self):
        source = Path(dm.__file__).read_text(encoding="utf-8")
        assert "ghcr.io" not in source and "image_inference_runtime" not in source
        assert "containers.run(" not in source and ".pull(" not in source
