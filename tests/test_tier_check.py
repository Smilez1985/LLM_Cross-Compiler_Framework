"""Ditto's start thresholds and the 2-bit gate (DESIGN "Ditto, the local runtime and the LLM layer": the rows "Start
thresholds for Ditto's local model" and "A 2-bit (Q2) model must first prove ..."): the verdict, the record with its
fingerprint, the one advice every surface reads, the measurement stage (evals/tier_check.py) with stand-in programs,
and the CLI, the windows and the AI tools that show it."""

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import tuning
from orchestrator.evals import eval_run, tier_check
from orchestrator.runtime import tier_advice
from orchestrator.runtime.catalog import ModelEntry, load_catalog
from orchestrator.runtime.hardware import HardwareInfo
from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError

GIB = 1024 ** 3


def hw(ram_gib=16.0):
    return HardwareInfo(os="linux", arch="x86_64", ram_total=int(ram_gib * GIB), ram_available=int(ram_gib * GIB * 0.8),
                        libc="glibc", libc_version="2.39")


def runtime(tmp_path, ram_gib=2.9, catalog=None):
    """A runtime with a stored probe and toolchain (no real probe, no compiler run), as tests/test_runtime.py."""
    from orchestrator.runtime.build.recipe import ToolchainInfo
    from orchestrator.runtime.build.toolchain import DetectedToolchain
    from orchestrator.runtime.profile import read_profile
    profile = read_profile(Path(__file__).parent / "fixtures" / "hardware_probe" / "schema2_pc_two_nvidia.txt")
    tools = DetectedToolchain(info=ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="gcc-13.3.0",
                                                 cmake="3.28.3", openmp=True), cpus=4, ram_available_mib=8192)
    return LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models", hardware=hw(ram_gib),
                           profile=profile, toolchain=tools, platform=("linux", "x86_64"),
                           **({"catalog": catalog} if catalog is not None else {}))


def record_for(rt, tier, *, tokens_s=12.0, kld=0.03, **changes):
    model = rt.catalog.model(tier)
    fingerprint = tier_advice.current_fingerprint(rt, model)
    record = tier_advice.make_record(tier=tier, model=model, fingerprint=fingerprint, reference="Q8_0", chunks=16,
                                     ctx=512, text_sha256="0" * 64, tokens_s=tokens_s,
                                     stats={"kld": kld, "kld_unc": 0.001, "top1": 88.0})
    record.update(changes)
    rt.tier_checks().save(record)
    return record


# --- the verdict and the catalogue ----------------------------------------------------------------------------------

def test_the_verdict_follows_the_start_thresholds_of_tuning():
    assert (tuning.SPEED_FLOOR, tuning.KLD_GOOD, tuning.KLD_FLOOR) == (8.0, 0.05, 0.10)
    assert tier_advice.verdict(8.0, 0.05) == "passed"
    assert tier_advice.verdict(30.0, 0.0501) == "usable" and tier_advice.verdict(30.0, 0.10) == "usable"
    assert tier_advice.verdict(30.0, 0.1001) == "failed"
    assert tier_advice.verdict(7.99, 0.01) == "failed", "too slow fails whatever the KLD"
    assert tier_advice.verdict(None, 0.01) == "failed" and tier_advice.verdict(20.0, None) == "failed"
    reasons = tier_advice.failure_reasons({"tokens_s": 5.1, "kld": 0.142})
    assert reasons == ["5.1 tokens/s, at least 8 needed", "KLD 0.1420, at most 0.10"]


def test_the_catalogue_marks_the_two_bit_tiers_and_pins_their_references():
    catalog = load_catalog()
    assert {m.tier for m in catalog.models if m.needs_eval} == {"small-q2", "default-q2", "large-iq2"}
    for m in catalog.models:
        assert set(m.reference) == {"F16", "Q8_0"}, m.tier
        ref = m.reference_entry("Q8_0")
        assert ref.repo == m.repo and ref.revision == m.revision and ref.pinned and "Q8_0" in ref.file
        assert m.reference["F16"].size > m.reference["Q8_0"].size > m.size
    # the 4B and 9B at 2-bit stay outside the tier rules: never recommended, only downloaded by name
    ruled = {r.tier for r in catalog.tier_rules}
    assert "default-q2" not in ruled and "large-iq2" not in ruled and "small-q2" in ruled


# --- the record and its fingerprint ---------------------------------------------------------------------------------

def test_the_record_counts_only_with_its_fingerprint(tmp_path):
    checks = tier_advice.TierChecks(tmp_path)
    fp = tier_advice.Fingerprint("a" * 64, "computer", "commit", "cpu")
    model = SimpleNamespace(file="m.gguf")
    record = tier_advice.make_record(tier="small-q2", model=model, fingerprint=fp, reference="F16", chunks=16, ctx=512,
                                     text_sha256="t", tokens_s=10, stats={"kld": 0.2})
    assert record["verdict"] == "failed"
    checks.save(record)
    assert json.loads(checks.path.read_text(encoding="utf-8"))["schema"] == tier_advice.SCHEMA
    assert checks.lookup("small-q2", fp).verdict == "failed"
    for changed in (fp.__class__("b" * 64, "computer", "commit", "cpu"), fp.__class__("a" * 64, "other", "commit", "cpu"),
                    fp.__class__("a" * 64, "computer", "newpin", "cpu"), fp.__class__("a" * 64, "computer", "commit",
                                                                                       "vulkan")):
        view = checks.lookup("small-q2", changed)
        assert view.stale and view.verdict is None, changed
    assert checks.lookup("small-q2", fp.__class__("a" * 64, "computer", "commit", None)).verdict == "failed", \
        "an unknown backend is not compared"
    with pytest.raises(ValueError):
        checks.save({"tier": "x"})


def test_the_computer_is_kept_as_a_hash_only():
    machine = hw(16)
    first = tier_advice.computer_id(machine, cpu="Some CPU 9000")
    assert len(first) == 32 and "CPU" not in first
    assert first == tier_advice.computer_id(machine, cpu="Some CPU 9000")
    assert first != tier_advice.computer_id(hw(32), cpu="Some CPU 9000")
    assert first != tier_advice.computer_id(machine, cpu="Another CPU")


# --- the advice -----------------------------------------------------------------------------------------------------

def test_three_gib_names_the_two_bit_model_only_as_a_candidate_and_a_plain_start_refuses_it(tmp_path):
    rt = runtime(tmp_path, ram_gib=2.9)
    advice = rt.tier_advice()
    assert advice.model.tier == "small-q2" and advice.state == "candidate" and not advice.offered
    model, why = rt.choose_tier()
    assert model.tier == "small-q2" and "only a candidate to evaluate" in why
    assert "llm-cli ditto eval --measure --tier small-q2" in why and "cloud provider" in why
    with pytest.raises(LocalRuntimeError, match="No model was started: small-q2 is only a candidate"):
        rt.resolve_model(None, for_start=True)
    assert rt.resolve_model("small-q2", for_start=True)[0].tier == "small-q2", "by name the person may start it"
    assert "not evaluated on this computer" in rt.start_notice(model)
    assert rt.start_notice(rt.catalog.model("small")) is None


def test_a_passed_measurement_recommends_it_a_usable_one_says_so_a_failed_one_says_why(tmp_path):
    rt = runtime(tmp_path, ram_gib=2.9)
    record_for(rt, "small-q2", tokens_s=12.0, kld=0.03)
    advice = rt.tier_advice()
    assert advice.state == "recommended" and advice.offered
    assert "passed the measurement on this computer (12.0 tokens/s, KLD 0.0300" in advice.text()
    assert rt.resolve_model(None, for_start=True)[0].tier == "small-q2"
    assert rt.start_notice(advice.model) is None
    record_for(rt, "small-q2", tokens_s=12.0, kld=0.07)
    advice = rt.tier_advice()
    assert advice.state == "usable" and advice.offered and "usable, not good" in advice.text()
    record_for(rt, "small-q2", tokens_s=5.1, kld=0.142)
    advice = rt.tier_advice()
    assert advice.state == "failed" and not advice.offered
    assert ("small-q2 failed the measurement on this computer: 5.1 tokens/s, at least 8 needed; KLD 0.1420, at "
            "most 0.10") in advice.text()
    assert advice.facts()["measured"]["verdict"] == "failed"


def test_a_record_of_another_computer_or_file_counts_as_not_measured(tmp_path):
    rt = runtime(tmp_path, ram_gib=2.9)
    record_for(rt, "small-q2", computer="another computer")
    advice = rt.tier_advice()
    assert advice.state == "candidate" and advice.stale and "no longer counts" in advice.text()
    record_for(rt, "small-q2", sha256="f" * 64)
    assert rt.tier_advice().state == "candidate"


def test_a_tier_without_the_mark_is_recommended_as_before(tmp_path):
    rt = runtime(tmp_path, ram_gib=16)
    advice = rt.tier_advice()
    assert advice.model.tier == "default" and advice.state == "recommended" and not advice.gated
    assert rt.choose_tier()[1] == advice.rule_why


def test_the_models_list_marks_each_two_bit_tier(tmp_path):
    from orchestrator.runtime.ditto_models import models_overview
    rt = runtime(tmp_path, ram_gib=2.9)
    record_for(rt, "default-q2", tokens_s=9.0, kld=0.04)
    record_for(rt, "large-iq2", tokens_s=3.0, kld=0.09)
    view = models_overview(rt)
    assert view.state == "candidate" and not view.offered and view.action.endswith("--tier small-q2")
    assert view.heading().startswith("No local model is recommended yet: small-q2 is only a candidate")
    notes = {row.tier: row.notes() for row in view.rows}
    assert notes["small-q2"][0] == "candidate to evaluate" and "recommended" not in notes["small-q2"]
    assert notes["default-q2"][0] == "passed here" and notes["large-iq2"][0] == "failed here"
    assert "recommended" not in notes["default-q2"], "the 4B at 2-bit is never recommended, also not after a pass"
    assert notes["small"] == [] or "not evaluated here" not in notes["small"]


# --- the one KLD invocation ------------------------------------------------------------------------------------------

def test_the_kld_invocation_holds_one_sequence_per_batch():
    base = tuning.perplexity_args(model="ref.gguf", text="t.txt", ctx=512, chunks=16, logits="l.kld")
    assert base == ["-m", "ref.gguf", "-f", "t.txt", "-c", "512", "-b", "512", "--chunks", "16",
                    "--kl-divergence-base", "l.kld"]
    cmp = tuning.perplexity_args(model="q.gguf", ctx=256, chunks=4, logits="l.kld")
    assert cmp == ["-m", "q.gguf", "-c", "256", "-b", "256", "--chunks", "4", "--kl-divergence-base", "l.kld",
                   "--kl-divergence"]


# --- the measurement stage with stand-in programs ------------------------------------------------------------------

STATS = """====== Perplexity statistics ======
Mean PPL(Q)                   :  31.430816 ±   1.938388
Mean PPL(base)                :  21.800166 ±   1.339313
Mean PPL(Q)/PPL(base)         :   1.441770 ±   0.028350
====== KL divergence statistics ======
Mean    KLD:   {kld:.6f} ±   0.008684
====== Token probability statistics ======
Same top p: 66.078 ± 1.048 %
"""


class FakePerplexity:
    """subprocess.Popen for llama-perplexity: records the arguments, writes the logits on the reference pass and the
    statistics on a comparison pass; `hold` keeps it running until it is terminated (Cancel)."""

    def __init__(self, kld=0.03, hold=False):
        self.calls, self.kld, self.hold = [], kld, hold
        self.returncode = None
        self.terminated = False

    def __call__(self, argv, cwd=None, env=None, stdin=None, stdout=None, stderr=None, **kw):
        self.calls.append(list(argv))
        self.cwd, self.env = cwd, env
        if "--kl-divergence" in argv:
            stdout.write(STATS.format(kld=self.kld).encode())
        else:
            Path(argv[argv.index("--kl-divergence-base") + 1]).write_bytes(b"logits")
            stdout.write(b"Final estimate: PPL = 21.8002 +/- 1.33932\n")
        self.returncode = None if self.hold else 0
        return self

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def stand_in_catalog(tmp_path):
    """The real catalogue with one more 2-bit tier whose files are small stand-ins in the models folder."""
    catalog = load_catalog()
    files = {name: name.encode() * 10 for name in ("tiny-q2.gguf", "tiny-f16.gguf", "tiny-q8.gguf")}
    digest = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    tiny = ModelEntry(tier="tiny-q2", repo="example/Tiny-GGUF", revision="c" * 40, file="tiny-q2.gguf",
                      size=len(files["tiny-q2.gguf"]), sha256=digest["tiny-q2.gguf"], license="apache-2.0",
                      needs_eval=True, reference={
                          "F16": {"file": "tiny-f16.gguf", "size": len(files["tiny-f16.gguf"]),
                                  "sha256": digest["tiny-f16.gguf"]},
                          "Q8_0": {"file": "tiny-q8.gguf", "size": len(files["tiny-q8.gguf"]),
                                   "sha256": digest["tiny-q8.gguf"]}})
    return catalog.model_copy(update={"models": [*catalog.models, tiny]}), files


@pytest.fixture
def stage_rt(tmp_path, monkeypatch):
    catalog, files = stand_in_catalog(tmp_path)
    rt = runtime(tmp_path, ram_gib=16, catalog=catalog)
    rt.models_dir.mkdir(parents=True)
    for name in ("tiny-q2.gguf", "tiny-f16.gguf"):
        (rt.models_dir / name).write_bytes(files[name])   # hashed files: written as bytes
    build = SimpleNamespace(commit=catalog.llama_cpp.source.commit, backend="cpu", build_id="b" * 64)
    monkeypatch.setattr(tier_check, "find_perplexity", lambda _rt: (build, tmp_path / "bin" / "llama-perplexity"))
    return rt


class FakeBench:
    def __init__(self, gen_ts=12.5):
        self.gen_ts, self.calls = gen_ts, []

    def run(self, model, *, kind, label, trusted, sha256, stop=None):
        self.calls.append((Path(model).name, kind, trusted, sha256))
        return SimpleNamespace(gen_ts=self.gen_ts, prompt_ts=120.0)


def test_the_stage_measures_speed_and_kld_and_keeps_the_record(stage_rt, tmp_path):
    rt = stage_rt
    stage = tier_check.plan(rt, ["tiny-q2"], chunks=8, ctx=512, cache_dir=tmp_path / "cache")
    assert [t.reference for t in stage.tiers] == ["F16"] and not stage.missing_references()
    assert "its 16-bit original (tiny-f16.gguf)" in stage.lines()[0]
    fake, bench = FakePerplexity(kld=0.03), FakeBench()
    records = tier_check.measure(rt, stage, cache_dir=tmp_path / "cache", popen=fake, bench=bench)
    assert [r["verdict"] for r in records] == ["passed"] and records[0]["tokens_s"] == 12.5
    assert records[0]["kld"] == pytest.approx(0.03) and records[0]["top1"] == pytest.approx(66.078)
    base, compare = fake.calls
    assert base[1:] == tuning.perplexity_args(model=base[2], text=base[4], ctx=512, chunks=8, logits=base[-1])
    assert compare[1:] == tuning.perplexity_args(model=compare[2], ctx=512, chunks=8, logits=base[-1])
    assert set(fake.env) <= {"HOME", "PATH", "LANG", "LC_ALL", "USERPROFILE", "SYSTEMROOT", "SystemRoot", "TEMP",
                              "TMP", "WINDIR", "COMSPEC", "PATHEXT"}, "a cleared environment"
    assert bench.calls[0][:3] == ("tiny-q2.gguf", "tier_speed", True)
    assert list((tmp_path / "cache").iterdir()) == [], "the logits and the text are deleted when the stage ends"
    saved = rt.tier_checks().get("tiny-q2")
    assert saved["backend"] == "cpu" and saved["llama_cpp"] == rt.catalog.llama_cpp.source.commit
    assert "path" not in json.dumps(saved) and str(tmp_path) not in json.dumps(saved), "numbers and words only"
    again = tier_check.plan(rt, ["tiny-q2"], chunks=8, ctx=512, cache_dir=tmp_path / "cache")
    assert again.tiers[0].kept is not None and not again.to_measure, "a matching record is not measured again"
    assert tier_check.plan(rt, ["tiny-q2"], chunks=8, ctx=512, again=True).to_measure
    assert tier_check.plan(rt, ["tiny-q2"], chunks=16, ctx=512).to_measure, "other chunks measure anew"


def test_one_reference_pass_serves_every_tier_of_the_model(stage_rt, tmp_path):
    rt = stage_rt
    tiny = rt.catalog.model("tiny-q2")
    twin = tiny.model_copy(update={"tier": "tiny-twin"})
    rt.catalog = rt.catalog.model_copy(update={"models": [*rt.catalog.models, twin]})
    fake = FakePerplexity(kld=0.2)
    stage = tier_check.plan(rt, ["tiny-q2", "tiny-twin"], chunks=8, ctx=512)
    records = tier_check.measure(rt, stage, cache_dir=tmp_path / "cache", popen=fake, bench=FakeBench())
    assert [c.count("--kl-divergence") for c in fake.calls] == [0, 1, 1]
    assert [r["verdict"] for r in records] == ["failed", "failed"]
    assert rt.tier_advice().state == "recommended", "the rule's pick here is not a stand-in"


def test_cancel_ends_the_running_program_and_records_nothing(stage_rt, tmp_path):
    rt = stage_rt
    stop = threading.Event()
    fake = FakePerplexity(hold=True)
    stage = tier_check.plan(rt, ["tiny-q2"], chunks=8, ctx=512)
    threading.Timer(0.3, stop.set).start()
    with pytest.raises(tier_check.TierCheckStopped):
        tier_check.measure(rt, stage, cache_dir=tmp_path / "cache", stop=stop, popen=fake, bench=FakeBench())
    assert fake.terminated and rt.tier_checks().get("tiny-q2") is None
    assert list((tmp_path / "cache").iterdir()) == []


def test_the_plan_refuses_before_anything_runs(stage_rt, tmp_path, monkeypatch):
    rt = stage_rt
    with pytest.raises(tier_check.TierCheckError, match="not downloaded; download it first"):
        tier_check.plan(rt, ["small-q2"])
    with pytest.raises(tier_check.TierCheckError, match="chunks must be between 4 and 48"):
        tier_check.plan(rt, ["tiny-q2"], chunks=2)
    with pytest.raises(tier_check.TierCheckError, match="Unknown model tier"):
        tier_check.plan(rt, ["nope"])
    (rt.models_dir / "tiny-f16.gguf").unlink()
    small_ram = runtime(tmp_path / "small", ram_gib=0.0000001, catalog=rt.catalog)
    small_ram.models_dir = rt.models_dir
    stage = tier_check.plan(small_ram, ["tiny-q2"])
    assert stage.tiers[0].reference == "Q8_0", "the F16 does not fit three quarters of this memory"
    assert [e.file for e in stage.missing_references()] == ["tiny-q8.gguf"]
    with pytest.raises(tier_check.TierCheckError, match="reference is not downloaded"):
        tier_check.measure(small_ram, stage, popen=FakePerplexity(), bench=FakeBench())
    monkeypatch.setattr(tier_check.shutil, "disk_usage", lambda _p: SimpleNamespace(free=10 * 1024 * 1024))
    with pytest.raises(tier_check.TierCheckError, match="GB free") as err:
        tier_check.plan(rt, ["tiny-q2"], cache_dir=tmp_path / "cache")
    assert err.value.code == "insufficient_resources"


def test_a_missing_reference_is_downloaded_only_after_a_yes(stage_rt, tmp_path):
    rt = stage_rt
    (rt.models_dir / "tiny-f16.gguf").unlink()
    with pytest.raises(eval_run.EvalRunError) as err:
        eval_run.run_measurement(rt, ["tiny-q2"], chunks=8, ctx=512, confirm_download=lambda m, n: False)
    assert err.value.exit_code == 1 and err.value.code == "reference_declined"
    with pytest.raises(eval_run.EvalRunError) as err:
        eval_run.run_measurement(rt, ["tiny-q2"], chunks=8, ctx=512)
    assert err.value.exit_code == 2 and "--download-reference" in str(err.value)
    asked, fetched = [], []
    rt.download_entry = lambda entry, progress=None, stop=None: (
        fetched.append(entry.file), (rt.models_dir / entry.file).write_bytes(b"tiny-f16.gguf" * 10))
    import orchestrator.evals.tier_check as module
    original = module.measure
    try:
        module.measure = lambda rt_, stage, **kw: original(rt_, stage, popen=FakePerplexity(), bench=FakeBench(), **kw)
        records = eval_run.run_measurement(rt, ["tiny-q2"], chunks=8, ctx=512, cache_dir=tmp_path / "cache",
                                           confirm_download=lambda m, n: asked.append((m[0].file, n)) or True)
    finally:
        module.measure = original
    assert asked == [("tiny-f16.gguf", len(b"tiny-f16.gguf") * 10)] and fetched == ["tiny-f16.gguf"]
    assert records[0]["verdict"] == "passed"
    table = eval_run.measurement_table(records)
    assert table[0] == ["Tier", "Verdict", "Tokens/s", "KLD", "Top-1", "Reference"]
    assert table[1][0][:3] == ["tiny-q2", "passed", "12.5"]


# --- the CLI ---------------------------------------------------------------------------------------------------------

def invoke(monkeypatch, rt, *args):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setattr(cli, "_local_runtime", lambda ctx: rt)
    monkeypatch.setenv("PYTHONUTF8", "1")
    return CliRunner().invoke(cli.cli, ["-q", "ditto", *args], catch_exceptions=False)


def test_the_cli_names_the_candidate_in_the_list_and_the_status(tmp_path, monkeypatch):
    rt = runtime(tmp_path, ram_gib=2.9)
    listing = invoke(monkeypatch, rt, "models", "list")
    assert listing.exit_code == 0 and "candidate to evaluate" in listing.output
    assert "No local model is recommended yet" in " ".join(listing.output.split())
    status = invoke(monkeypatch, rt, "runtime", "status", "--json")
    assert "only a candidate to evaluate" in json.loads(status.output)["tier_reason"]


def test_the_cli_measure_needs_a_tier(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", lambda self: None, raising=False)
    result = CliRunner().invoke(cli.cli, ["ditto", "eval", "--measure"], catch_exceptions=False)
    assert result.exit_code == 2 and "--tier" in result.output


# --- the AI tools ----------------------------------------------------------------------------------------------------

def test_get_status_names_the_state_and_the_numbers(tmp_path, monkeypatch):
    from orchestrator.tools import tools_status
    rt = runtime(tmp_path, ram_gib=2.9)
    record_for(rt, "small-q2", tokens_s=5.0, kld=0.3)
    monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, fw, **kw: rt))
    section = tools_status.host_section(SimpleNamespace(), lambda: rt.hardware())
    assert section.recommended_tier == "small-q2" and section.recommended_tier_state == "failed"
    assert section.tier_check["verdict"] == "failed" and section.tier_check["kld"] == 0.3
    assert "failed the measurement" in section.reason and str(tmp_path) not in section.model_dump_json()


def test_no_tool_writes_the_record():
    import orchestrator.tools as tools_package
    root = Path(tools_package.__file__).parent
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "tier_checks().save" not in text and "TierChecks(" not in text, path
        assert "tier_check.measure" not in text and "run_measurement" not in text, path
