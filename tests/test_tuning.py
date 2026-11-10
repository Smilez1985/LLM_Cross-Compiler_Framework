"""Auto-Tuning (orchestrator/Core/tuning.py, the runtime layer's tune.sh, DESIGN "Targets, hardware and templates").

Without real models: the tuning job runs through the framework's stage runner over the Rockchip target's modules with
stub llama.cpp tools (tests/test_build_stages.py's image), whose llama-perplexity prints llama.cpp's statistics with a
KL divergence per type; the choice rule, the candidates, the evaluation text and the report are checked on their own.
"""

import gzip
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell
from test_build_stages import Image, _script, stage_lines

from orchestrator.Core import tuning
from orchestrator.utils import evaluation_text

needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason=NO_POSIX_BASH)

# The KL divergence the stub reports per type (llama.cpp's Llama 3 8B scoreboard, rounded), and with the importance
# matrix a little lower
KLD = {"Q8_0": 0.0014, "Q6_K": 0.0055, "Q5_K_M": 0.0108, "Q4_K_M": 0.0313, "IQ4_XS": 0.0400, "Q4_0": 0.0719,
       "Q3_K_M": 0.1019, "Q2_K": 0.4451}

PERPLEXITY_STUB = r"""
import os, re, sys
a = sys.argv
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('perplexity ' + ' '.join(a[1:]) + '\n')
kld = {KLD!r}
model = a[a.index('-m') + 1]
base = a[a.index('--kl-divergence-base') + 1]
chunks = a[a.index('--chunks') + 1]
if '--kl-divergence' not in a:
    with open(base, 'wb') as f:
        f.write(b'_logits_' + b'\0' * 64)
    print('perplexity: calculating perplexity over %s chunks, n_ctx=512, batch_size=2048, n_seq=4' % chunks)
    print('Final estimate: PPL = 12.3456 +/- 0.40000')
    sys.exit(0)
text = open(model, errors='replace').read()
m = re.search(r'quantized (\S+)', text)
kind = m.group(1) if m else 'F16'
if os.environ.get('STUB_FAIL') == 'measure-' + kind:
    print('kl_divergence: inconsistent vocabulary')   # llama-perplexity ends with 0 here
    sys.exit(0)
value = kld.get(kind, 0.2) * (0.85 if 'with imatrix' in text else 1.0)
print('kl_divergence: computing over %s chunks, n_ctx=512, batch_size=2048, n_seq=4' % chunks)
print('====== Perplexity statistics ======')
print('Mean PPL(Q)                   :  12.700000 ±   0.410000')
print('Mean PPL(base)                :  12.345600 ±   0.400000')
print('Mean PPL(Q)/PPL(base)         :   1.028700 ±   0.000700')
print('====== KL divergence statistics ======')
print('Mean    KLD:   %.6f ±   %.6f' % (value, value / 100))
print('Maximum KLD:   4.000000')
print('====== Token probability statistics ======')
print('Same top p: %6.3f ± %5.3f %%' % (100 - value * 200, 0.07))
""".replace("{KLD!r}", repr(KLD))


class TuneImage(Image):
    def __init__(self, root: Path):
        super().__init__(root)
        _script(self.llama / "build" / "bin" / "llama-perplexity", PERPLEXITY_STUB)
        self.text = root / "evaluation" / "evaluation.txt"
        self.text.parent.mkdir()
        self.text.write_bytes(b"The evaluation text.\n")

    def tune(self, candidates: str, **values):
        values.setdefault("JOB_TYPE", "tune")
        values.setdefault("QUANTIZATION", "F16")
        values.setdefault("TUNE_TEXT", self.text)
        values.setdefault("TUNE_REFERENCE", "F16")
        values.setdefault("TUNE_CHUNKS", "8")
        values.setdefault("TUNE_CTX", "512")
        return self.run(TUNE_CANDIDATES=candidates, **values)

    def plan(self, *specs) -> tuning.TuningPlan:
        cands = [tuning.Candidate(s.split(":")[0], s.endswith(":i"), "board", {"state": "fits"}) for s in specs]
        return tuning.TuningPlan(candidates=cands, model="org/model", board="rk3588", target="Rockchip")


@pytest.fixture
def image(tmp_path):
    return TuneImage(tmp_path)


# --- the tuning job through the stage runner ------------------------------------------------------------------------

@needs_bash
def test_the_tuning_job_measures_every_candidate_against_the_reference_logits(image):
    imatrix = image.imatrix / "imatrix.dat"
    imatrix.write_text("matrix")
    result = image.tune("Q4_K_M:i Q4_K_M:- Q6_K:-", IMATRIX_PATH=imatrix)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [s[1] for s in stage_lines(result) if s[2] == "done"] == ["source", "config", "convert", "tune"]
    calls = image.calls()
    assert calls[0] == "convert <root>/input-model --outfile <root>/build-cache/output/model-F16.gguf --outtype f16"
    # tune.sh's perplexity_pass and tuning.perplexity_args (Ditto's measurement) are the one KLD invocation
    logits = "<root>/build-cache/work/tune/reference.kld"
    assert calls[1] == "perplexity " + " ".join(tuning.perplexity_args(
        model="<root>/build-cache/output/model-F16.gguf", text="<root>/evaluation/evaluation.txt", ctx=512, chunks=8,
        logits=logits))
    assert calls[1] == ("perplexity -m <root>/build-cache/output/model-F16.gguf -f <root>/evaluation/evaluation.txt "
                        "-c 512 -b 512 --chunks 8 --kl-divergence-base <root>/build-cache/work/tune/reference.kld")
    assert calls[2].startswith("quantize --imatrix <root>/build-cache/imatrix/imatrix.dat "
                               "<root>/build-cache/output/model-F16.gguf <root>/build-cache/work/tune/candidate.gguf "
                               "Q4_K_M")
    assert calls[3] == "perplexity " + " ".join(tuning.perplexity_args(
        model="<root>/build-cache/work/tune/candidate.gguf", ctx=512, chunks=8, logits=logits))
    assert calls[3] == ("perplexity -m <root>/build-cache/work/tune/candidate.gguf -c 512 -b 512 --chunks 8 "
                        "--kl-divergence-base <root>/build-cache/work/tune/reference.kld --kl-divergence")
    assert len(calls) == 1 + 1 + 3 * 2
    assert not (image.work / "tune" / "candidate.gguf").exists(), "each candidate is deleted after its measurement"
    plan = image.plan("Q4_K_M:i", "Q4_K_M:-", "Q6_K:-")
    reference, rows = tuning.read_results(image.output / "tuning", plan)
    assert reference["type"] == "F16" and reference["ppl_reference"] == pytest.approx(12.3456)
    assert [r["status"] for r in rows] == ["done"] * 3
    assert rows[0]["kld"] == pytest.approx(KLD["Q4_K_M"] * 0.85, rel=1e-3)
    assert rows[1]["kld"] == pytest.approx(KLD["Q4_K_M"], rel=1e-3)
    assert rows[2]["top1"] == pytest.approx(100 - KLD["Q6_K"] * 200, abs=1e-3)
    assert rows[0]["chunks"] == 8 and rows[0]["ppl_ratio"] == pytest.approx(1.0287)
    assert result.stdout.rstrip().splitlines()[-1] == "Done (tune)."


@needs_bash
def test_a_continued_tuning_run_measures_only_what_is_missing(image):
    first = image.tune("Q6_K:- Q4_0:-", STUB_FAIL="quantize")
    assert first.returncode == 0, first.stdout + first.stderr   # a failing candidate is recorded, the run goes on
    rows = tuning.read_results(image.output / "tuning", image.plan("Q6_K:-", "Q4_0:-"))[1]
    assert [r["status"] for r in rows] == ["quantize_failed", "quantize_failed"]
    # the same inputs again: the stages and the reference's logits resume, and a recorded candidate is not redone
    image.log.unlink()
    again = image.tune("Q6_K:- Q4_0:-", STUB_FAIL="quantize")
    assert again.returncode == 0
    assert image.calls() == []
    # another candidate list is another tuning stage: only the reference resumes, the candidates run
    third = image.tune("Q6_K:- Q4_0:- Q5_K_M:-")
    assert third.returncode == 0, third.stdout + third.stderr
    assert "=== Tuning: the reference's logits resumed ===" in third.stdout
    assert not any(c.startswith("convert") or "--kl-divergence-base" in c and "--kl-divergence " not in c + " "
                   for c in image.calls() if "candidate" not in c)


@needs_bash
def test_a_candidate_without_a_kl_divergence_counts_as_failed(image):
    result = image.tune("Q6_K:- Q4_0:-", STUB_FAIL="measure-Q4_0")
    assert result.returncode == 0, result.stdout + result.stderr
    rows = tuning.read_results(image.output / "tuning", image.plan("Q6_K:-", "Q4_0:-"))[1]
    assert [r["status"] for r in rows] == ["done", "measure_failed"]


@needs_bash
def test_the_q8_0_reference_is_made_from_the_f16_and_removed(image):
    result = image.tune("Q4_0:-", TUNE_REFERENCE="Q8_0")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = image.calls()
    assert calls[1].startswith("quantize <root>/build-cache/output/model-F16.gguf "
                               "<root>/build-cache/work/tune/reference-Q8_0.gguf Q8_0")
    assert calls[2].startswith("perplexity -m <root>/build-cache/work/tune/reference-Q8_0.gguf")
    assert not (image.work / "tune" / "reference-Q8_0.gguf").exists()
    assert "type Q8_0" in (image.output / "tuning" / "reference.info").read_text()


@needs_bash
@pytest.mark.parametrize("values, message", [
    ({"TUNE_CANDIDATES": ""}, "No candidate types to measure"),
    ({"TUNE_REFERENCE": "Q4_0"}, "TUNE_REFERENCE must be F16 or Q8_0"),
    ({"TUNE_TEXT": "/nonexistent/evaluation.txt"}, "The evaluation text is missing"),
    ({"TUNE_CANDIDATES": "Q4_K_M:i"}, "to be measured with the importance matrix, but there is none"),
    ({"TUNE_CANDIDATES": "Q4;rm:-"}, "is not a quantization type"),
])
def test_tune_refusals(image, values, message):
    candidates = values.pop("TUNE_CANDIDATES", "Q4_0:-")
    result = image.tune(candidates, **values)
    assert result.returncode != 0
    assert message in result.stderr


@needs_bash
def test_the_stage_tune_runs_only_in_the_tuning_job(image):
    result = image.run(LLMCF_STAGES="source config convert tune")
    assert result.returncode != 0 and "The stage tune runs only in the tuning job." in result.stderr


def test_the_stage_order_of_the_tuning_job_and_tune_is_no_module_name(tmp_path):
    from orchestrator.Core.runtime_layer import stage_order
    stages, problems = stage_order(REPO_ROOT / "targets" / "ARM64", "tune")
    assert stages == ["source", "config", "convert", "tune"] and problems == []
    target = tmp_path / "t"
    (target / "modules").mkdir(parents=True)
    (target / "modules" / "tune_module.sh").write_text("")
    (target / "target.yml").write_text("stages:\n  after_target: [tune]\n")
    assert any("the stage tune is the framework's own" in p for p in stage_order(target, "build")[1])


def test_the_runtime_layer_manifest_names_tune_sh():
    from orchestrator.Core.runtime_layer import SCRIPTS, shipped_problems
    assert "tune.sh" in SCRIPTS and shipped_problems() == []


# --- the evaluation text ----------------------------------------------------------------------------------------------

def test_the_evaluation_text_is_whole_and_holds_both_languages():
    text = evaluation_text.build_evaluation_text()
    assert len(text) > 120_000
    first = text[:6000]
    assert "Gregor Samsa" in first and "Sherlock Holmes" in first
    assert "Gutenberg" not in text and "\r" not in text


def test_the_evaluation_text_shares_nothing_with_the_calibration_text():
    calibration = "".join(gzip.decompress(p.read_bytes()).decode("utf-8")
                          for p in sorted((REPO_ROOT / "data" / "calibration").glob("*.txt.gz")))
    text = evaluation_text.build_evaluation_text()
    assert not [i for i in range(0, len(text) - 60, 30) if text[i:i + 60] in calibration]


def test_a_changed_evaluation_text_is_refused(monkeypatch, tmp_path):
    for name in evaluation_text.EVALUATION_SETS:
        (tmp_path / f"evaluation_{name}.txt.gz").write_bytes(gzip.compress(b"changed"))
    monkeypatch.setattr(evaluation_text, "DATA_DIR", tmp_path)
    with pytest.raises(evaluation_text.EvaluationTextError, match="does not match its checksum"):
        evaluation_text.build_evaluation_text()


def test_the_evaluation_text_is_written_with_lf(tmp_path):
    path = evaluation_text.write_evaluation_text(tmp_path / "e.txt")
    assert b"\r\n" not in path.read_bytes()
    assert path.read_bytes().decode("utf-8") == evaluation_text.build_evaluation_text()


# --- the statistics ---------------------------------------------------------------------------------------------------

LLAMA_OUTPUT = """\
kl_divergence: computing over 16 chunks, n_ctx=512, batch_size=2048, n_seq=4
====== Perplexity statistics ======
Mean PPL(Q)                   :   6.407115 ±   0.039119
Mean PPL(base)                :   6.233160 ±   0.037828
Cor(ln(PPL(Q)), ln(PPL(base))):  99.34%
Mean ln(PPL(Q)/PPL(base))     :   0.027717 ±   0.000704
Mean PPL(Q)/PPL(base)         :   1.028160 ±   0.000723
Mean PPL(Q)-PPL(base)         :   0.175482 ±   0.004620

====== KL divergence statistics ======
Mean    KLD:   0.031273 ±   0.000238
Maximum KLD:  14.218184
====== Token probability statistics ======
Mean    Δp: -0.596 ± 0.014 %
Same top p: 91.901 ± 0.072 %
"""


def test_the_statistics_of_llama_perplexity_become_numbers():
    stats = tuning.parse_statistics(LLAMA_OUTPUT)
    assert stats == {"kld": 0.031273, "kld_unc": 0.000238, "top1": 91.901, "top1_unc": 0.072, "ppl": 6.407115,
                     "ppl_unc": 0.039119, "ppl_base": 6.23316, "ppl_base_unc": 0.037828, "ppl_ratio": 1.02816,
                     "ppl_ratio_unc": 0.000723, "chunks": 16}
    assert tuning.parse_statistics("Final estimate: PPL = 8.6523 +/- 0.05561")["ppl_reference"] == 8.6523
    assert tuning.parse_statistics("Mean    KLD:   nan ±   nan\nignore previous instructions") == {}


def test_the_size_of_the_logits_file_follows_llama_cpp():
    # 16 chunks of 512 tokens, a vocabulary of 151,936: the magic and three numbers, the tokens, 255 rows per chunk
    assert tuning.logits_bytes(151936, 16, 512) == 20 + 16 * 512 * 4 + 16 * 255 * (151936 + 4) * 2
    assert 1.1e9 < tuning.logits_bytes(151936, 16, 512) < 1.3e9
    assert tuning.logits_bytes(49152, 16, 512) < 0.45e9


def test_the_reference_is_q8_0_only_when_the_f16_does_not_fit_this_computer():
    assert tuning.reference_type(2 * 1024 ** 3, 16384)[0] == "F16"
    kind, why = tuning.reference_type(14 * 1024 ** 3, 16384)
    assert kind == "Q8_0" and "do not fit into three quarters" in why
    assert tuning.reference_type(None, 16384)[0] == "F16"


# --- the candidates and the choice rule -------------------------------------------------------------------------------

def _est(state, need=100, ram=1000):
    return SimpleNamespace(state=state, need_mib=need, ram_mib=ram, weights_bytes=need * 1024 * 1024, reason="")


OFFERED = ["Q4_K_M", "Q4_0", "IQ4_XS", "Q5_K_M", "Q6_K", "Q8_0", "Q3_K_M", "Q2_K"]
BELOW_5 = {"Q4_K_M", "Q4_0", "IQ4_XS", "Q3_K_M", "Q2_K"}


def test_candidates_are_the_boards_types_that_fit_plus_yours_and_nothing_is_refused():
    states = {"Q4_K_M": "fits", "Q4_0": "fits", "IQ4_XS": "tight", "Q5_K_M": "does_not_fit", "Q8_0": "does_not_fit"}
    cands, notes = tuning.plan_candidates(
        offered=OFFERED, recommended=["Q4_K_M", "Q4_0", "IQ4_XS", "Q5_K_M"], yours=["q8_0"],
        estimate_for=lambda q, m: _est(states.get(q, "fits")), reads_imatrix=lambda q: q in BELOW_5)
    assert [c.label for c in cands] == ["Q8_0", "Q4_K_M-imatrix", "Q4_0-imatrix", "IQ4_XS-imatrix"]
    assert cands[0].origin == "yours" and "does not fit" in cands[0].note   # yours is measured anyway
    assert cands[3].note == "tight on the board's memory"
    assert any(n.startswith("Q5_K_M is left out") for n in notes)


def test_both_measures_the_matrix_types_with_and_without_and_the_count_is_capped():
    cands, notes = tuning.plan_candidates(
        offered=OFFERED, recommended=[], yours=["Q3_K_M", "Q2_K"], estimate_for=lambda q, m: None,
        reads_imatrix=lambda q: q in BELOW_5, imatrix="both")
    labels = [c.label for c in cands]
    assert labels[:4] == ["Q3_K_M-imatrix", "Q3_K_M", "Q2_K-imatrix", "Q2_K"]
    assert len(cands) == tuning.MAX_CANDIDATES and any("At most 8" in n for n in notes)
    assert all(c.estimate["state"] == "unknown" for c in cands)


def test_a_type_the_target_does_not_build_is_said_not_dropped():
    with pytest.raises(tuning.TuningError, match="not a GGUF type this target builds"):
        tuning.plan_candidates(offered=OFFERED, recommended=[], yours=["Q9_X"], estimate_for=lambda q, m: None,
                               reads_imatrix=lambda q: False)


def _row(label, kld, state="fits", size=100, speed=None, unc=0.0001):
    q = label.split("-")[0]
    row = {"label": label, "quantization": q, "imatrix": label.endswith("imatrix"), "status": "done", "kld": kld,
           "kld_unc": unc, "bytes": size, "estimate": {"state": state}}
    if speed is not None:
        row["tokens_s"] = speed
    return row


def test_the_choice_is_the_lowest_kld_that_fits():
    rows = [_row("Q8_0", 0.0014, "does_not_fit", 800), _row("Q6_K", 0.0055, "tight", 600),
            _row("Q5_K_M", 0.011, "fits", 500), _row("Q4_K_M-imatrix", 0.027, "fits", 450)]
    choice = tuning.choose(rows)
    assert choice.label == "Q5_K_M" and choice.best_measured == "Q8_0"
    assert "fits the board" in choice.reason and "not measured yet" in choice.reason


def test_tight_is_chosen_only_when_nothing_fits_and_never_does_not_fit():
    rows = [_row("Q8_0", 0.0014, "does_not_fit"), _row("Q6_K", 0.0055, "tight"), _row("Q4_0", 0.07, "tight")]
    assert tuning.choose(rows).label == "Q6_K"
    assert tuning.choose([_row("Q8_0", 0.0014, "does_not_fit")]).label is None


def test_the_quality_floor_and_the_measured_speed_floor():
    rows = [_row("Q3_K_M", 0.11), _row("Q2_K", 0.45)]
    choice = tuning.choose(rows)
    assert choice.label is None and "below the quality floor" in choice.reason and choice.best_measured == "Q3_K_M"
    rows = [_row("Q6_K", 0.0055, speed=5.0), _row("Q4_K_M", 0.031, speed=11.0), _row("Q4_0", 0.07)]
    assert tuning.choose(rows).label == "Q4_K_M"   # Q6_K measured too slow; Q4_0 has no speed and is not excluded
    assert tuning.quality_verdict(0.03) == "good" and tuning.quality_verdict(0.08) == "usable"
    assert tuning.quality_verdict(0.2) == "below_floor" and tuning.quality_verdict(None) == "unknown"


def test_within_the_uncertainty_the_smaller_file_wins():
    rows = [_row("Q4_K_M", 0.0300, size=450, unc=0.0006), _row("IQ4_XS", 0.0305, size=400, unc=0.0006)]
    choice = tuning.choose(rows)
    assert choice.label == "IQ4_XS" and "within the uncertainty" in choice.reason


def test_nothing_measured_is_no_recommendation():
    assert tuning.choose([{"label": "Q4_0", "status": "quantize_failed"}]).label is None


# --- the report -------------------------------------------------------------------------------------------------------

def _report():
    plan = tuning.TuningPlan(candidates=[tuning.Candidate("Q6_K", False, "board", {"state": "fits"}),
                                         tuning.Candidate("Q4_K_M", True, "board", {"state": "fits"})],
                             model="org/model", board="bcm2712", target="ARM64", reference_reason="the F16 model")
    rows = [_row("Q6_K", 0.0055, size=600), _row("Q4_K_M-imatrix", 0.027, size=450)]
    return tuning.make_report(plan, {"type": "F16"}, rows, build_id="b1", text_sha256="0" * 64, llama_cpp="b11429")


def test_the_report_carries_the_recommendation_and_the_board_speed_when_measured():
    report = _report()
    assert report["schema"] == tuning.SCHEMA and report["recommendation"]["label"] == "Q6_K"
    measured = [SimpleNamespace(board="bcm2712", model="org/model", quantization="Q6_K", gen_ts=4.0),
                SimpleNamespace(board="bcm2712", model="org/model", quantization="Q4_K_M", gen_ts=9.5),
                SimpleNamespace(board="bcm2711", model="org/model", quantization="Q4_K_M", gen_ts=1.0)]
    shown = tuning.with_speed(report, measured)
    assert [c.get("tokens_s") for c in shown["candidates"]] == [4.0, 9.5]
    assert shown["recommendation"]["label"] == "Q4_K_M-imatrix"   # Q6_K runs below 8 tokens/s on the board
    assert "tokens_s" not in report["candidates"][0], "the stored report is not changed"
    lines = tuning.report_lines(shown)
    assert any(line.startswith("Recommended: Q4_K_M-imatrix") for line in lines)
    assert tuning.build_choice(shown) == ("Q4_K_M", True)
    assert tuning.build_choice(shown, "q6_k") == ("Q6_K", False)   # any measured type, the person decides
    facts = tuning.ai_facts(shown)
    assert facts["candidates"][1]["memory"] == "fits"
    assert "path" not in json.dumps(facts) and "build-cache" not in json.dumps(facts)


def test_the_report_is_written_and_read_back(tmp_path):
    path = tuning.write_report(tmp_path, _report())
    assert tuning.load_report(path)["build_id"] == "b1"
    (tmp_path / "x.json").write_text('{"schema": "other"}')
    with pytest.raises(tuning.TuningError):
        tuning.load_report(tmp_path / "x.json")


def test_the_plan_round_trips_and_hands_tune_sh_its_values():
    plan = tuning.TuningPlan(candidates=[tuning.Candidate("Q4_K_M", True), tuning.Candidate("Q6_K", False)],
                             reference="Q8_0", chunks=8, ctx=512)
    again = tuning.TuningPlan.from_dict(json.loads(json.dumps(plan.as_dict())))
    assert again.env() == {"TUNE_REFERENCE": "Q8_0", "TUNE_CANDIDATES": "Q4_K_M:i Q6_K:-", "TUNE_CHUNKS": "8",
                           "TUNE_CTX": "512"}
    assert again.uses_imatrix
    with pytest.raises(tuning.TuningError):
        tuning.check_limits(100, 512)
    with pytest.raises(tuning.TuningError):
        tuning.check_limits(16, 333)


def test_the_thresholds_are_the_ditto_rows():
    assert (tuning.KLD_GOOD, tuning.KLD_FLOOR, tuning.SPEED_FLOOR) == (0.05, 0.10, 8.0)
    assert re.search(r"at least 8 tokens per second and a KL divergence of at most 0\.05 \(0\.10 as the outer "
                     r"bound\)", (REPO_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8"))


# --- the build service: a tuning run is one build ---------------------------------------------------------------------

from test_arm64_target import service as arm64_service  # noqa: E402,F401 - the build service with stored probes

SMOL = json.loads((REPO_ROOT / "tests" / "fixtures" / "memory_estimate" / "published_gguf.json")
                  .read_text(encoding="utf-8"))


def _local_model(tmp_path, name):
    model = next(m for m in SMOL["models"] if m["name"] == name)
    folder = tmp_path / "local-model"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps(model["config"]), encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"\0" * 16)
    return folder


def test_a_tuning_run_is_planned_from_the_board_profile_and_queued_as_one_build(arm64_service, tmp_path):  # noqa: F811
    from orchestrator.Core.build_service import BuildService
    folder = _local_model(tmp_path, "Llama 3.2 1B Instruct")
    request = BuildService.request_in(model=str(folder), target="ARM64", format="gguf", board="bcm2837",
                                      probe=arm64_service.zero2w)
    # the Zero 2 W (416 MB measured): none of the profile's types of a 1B model fits, so without the person's own
    # types there is nothing to measure, and the refusal says how to measure them anyway
    with pytest.raises(tuning.TuningError, match="Name the types to measure"):
        arm64_service.plan_tuning(request, local_paths=True, look_up_hub=False)
    plan, req = arm64_service.plan_tuning(request.model_copy(update={"board": "bcm2712", "probe": arm64_service.pi5}),
                                          local_paths=True, look_up_hub=False)
    labels = [c.label for c in plan.candidates]
    assert labels == ["Q4_K_M-imatrix", "Q4_0-imatrix", "IQ4_NL-imatrix", "Q5_K_M", "Q6_K", "Q8_0"]
    assert all(c.estimate["state"] == "fits" for c in plan.candidates)
    assert plan.reference in ("F16", "Q8_0") and plan.disk_bytes > tuning.logits_bytes(128256, 16, 512)
    assert req.use_imatrix is True
    handle = arm64_service.start_tuning(request.model_copy(update={"board": "bcm2712", "probe": arm64_service.pi5}),
                                        local_paths=True, plan=plan)
    (sent,) = arm64_service.framework.orchestrator.requests
    assert handle.request_id and sent.tuning["candidates"][0]["quantization"] == "Q4_K_M"
    assert sent.quantization_options == [] and sent.use_imatrix is True and sent.target_board == "bcm2712"
    assert sent.description.startswith("Auto-Tuning of")


def test_the_persons_types_are_measured_on_a_board_they_do_not_fit(arm64_service, tmp_path):  # noqa: F811
    from orchestrator.Core.build_service import BuildService
    folder = _local_model(tmp_path, "Llama 3.2 1B Instruct")
    request = BuildService.request_in(model=str(folder), target="ARM64", format="gguf", board="bcm2837",
                                      probe=arm64_service.zero2w)
    plan, _ = arm64_service.plan_tuning(request, types=["Q4_0", "Q2_K"], local_paths=True, look_up_hub=False)
    assert [c.label for c in plan.candidates][:2] == ["Q4_0-imatrix", "Q2_K-imatrix"]
    assert plan.candidates[0].origin == "yours" and plan.candidates[0].estimate["state"] == "does_not_fit"


def test_only_gguf_is_tuned(arm64_service, tmp_path):  # noqa: F811
    from orchestrator.Core.build_service import BuildService
    request = BuildService.request_in(model=str(_local_model(tmp_path, "Llama 3.2 1B Instruct")), target="ARM64",
                                      format="rkllm", board="bcm2712")
    with pytest.raises(tuning.TuningError, match="measures GGUF models"):
        arm64_service.plan_tuning(request, local_paths=True, look_up_hub=False)


# --- the build engine: the tuning job's results become the report -----------------------------------------------------

def test_the_engine_writes_the_report_and_a_manifest_without_a_model(tmp_path, monkeypatch):
    from test_pipeline import make_config

    from orchestrator.Core import builder
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    engine = BuildEngine(ConfigManager(tmp_path / "configs"))
    plan = tuning.TuningPlan(candidates=[tuning.Candidate("Q6_K", False, "board", {"state": "fits"}),
                                         tuning.Candidate("Q4_K_M", True, "board", {"state": "fits"})],
                             model="org/model", board="rk3588", target="Rockchip", chunks=8)
    config = make_config(build_id="t1", quantization=None, tuning=plan.as_dict(), use_imatrix=True)
    build_temp = engine.cache_dir / "builds" / "t1"
    (build_temp / "imatrix").mkdir(parents=True)
    (build_temp / "imatrix" / "imatrix.dat").write_bytes(b"m")
    seen = {}

    def fake_sandbox(cfg, temp, target_path=None, job="build", progress=None):
        seen["job"] = job
        return {}, {}, {}

    def fake_run(cfg, progress, container, prefix, end_record=None, decided=None):
        _sandbox, vols, variables = decided
        seen["env"], seen["vols"] = variables, vols
        results = build_temp / "tune" / "tuning"
        results.mkdir(parents=True)
        (results / "reference.info").write_text("type F16\nbytes 10\nseconds 1\nlogits_bytes 5\n")
        (results / "reference.log").write_text("Final estimate: PPL = 9.5 +/- 0.1\n")
        for label, kld in (("Q6_K", 0.0055), ("Q4_K_M-imatrix", 0.027)):
            (results / f"{label}.info").write_text(f"type {label.split('-')[0]}\nbytes 100\nseconds 2\nstatus done\n")
            (results / f"{label}.log").write_text(f"Mean    KLD:   {kld} ±   0.0001\nSame top p: 95.0 ± 0.1 %\n")
        return 0

    monkeypatch.setattr(engine, "_sandbox", fake_sandbox)
    monkeypatch.setattr(engine, "_run_container", fake_run)
    monkeypatch.setattr(engine, "_ended_by_record", lambda progress, code, prefix, job="build": code)
    monkeypatch.setattr(engine, "_hardware_probe_mount", lambda cfg, progress, temp: ({}, {}))
    monkeypatch.setattr(builder, "create_container", lambda client, **kw: SimpleNamespace(kw=kw))
    progress = BuildProgress("t1", BuildStatus.BUILDING, "x")
    engine._execute_tuning(config, progress, SimpleNamespace(id="sha256:" + "0" * 64), REPO_ROOT / "targets" /
                           "Rockchip", ("/build-cache/input-model", {}))
    assert seen["job"] == "tune"
    sent = seen["env"]
    assert sent["JOB_TYPE"] == "tune" and sent["QUANTIZATION"] == "F16"
    assert sent["TUNE_CANDIDATES"] == "Q6_K:- Q4_K_M:i" and sent["TUNE_CHUNKS"] == "8"
    assert sent["IMATRIX_PATH"] == "/build-cache/imatrix/imatrix.dat"
    assert seen["vols"][str(build_temp / "tune")]["bind"] == "/build-cache/output"
    text = build_temp / "tune-text" / "evaluation.txt"
    assert text.read_bytes() == evaluation_text.build_evaluation_text().encode("utf-8")
    report = tuning.load_report(build_temp / "output" / tuning.REPORT_FILE)
    assert report["recommendation"]["label"] == "Q6_K" and report["evaluation"]["chunks"] == 8
    assert report["evaluation"]["text_sha256"] == evaluation_text.text_sha256()
    out = tmp_path / "staged"
    out.mkdir()
    info = engine._write_build_manifest(config, out)
    assert info["kind"] == "tuning" and info["tuning_report"] == tuning.REPORT_FILE and info["primary"] is None


# --- the Auto-Tuning tab ----------------------------------------------------------------------------------------------

class _TabService:
    def __init__(self):
        self.started, self.built = [], []
        report = _report()
        self.reports = [{"id": "a_0123456789abcdef", "label": "output:ARM64/model/tuning", "report": report}]

    def list_targets(self):
        fmt = SimpleNamespace(format="gguf")
        return SimpleNamespace(targets=[SimpleNamespace(id="ARM64", boards=["bcm2711", "bcm2712"], formats=[fmt])])

    def plan_tuning(self, request, **kw):
        self.planned = (request, kw)
        return tuning.TuningPlan(candidates=[tuning.Candidate("Q4_K_M", True, "board", {"state": "fits"})],
                                 model=request.model, board=request.board, target=request.target,
                                 reference_reason="the F16 model", disk_bytes=3 * 1024 ** 3), request

    def start_tuning(self, request, **kw):
        self.started.append((request, kw))
        return SimpleNamespace(request_id="req_tune0001")

    def tuning_reports(self):
        return self.reports

    def build_tuned(self, run, label=None, **kw):
        self.built.append(run)
        return SimpleNamespace(request_id="req_build001")


@pytest.mark.usefixtures("qapp")
def test_the_tab_plans_starts_and_builds_the_recommended(qapp):
    import time

    from orchestrator.gui.tuning_tab import TuningTab
    service = _TabService()
    tab = TuningTab(SimpleNamespace(build_service=service))
    assert tab.cmb_tuning_target.currentText() == "ARM64" and tab.cmb_tuning_board.count() == 2
    assert tab.lst_tuning_runs.count() == 1 and "Recommended: Q6_K" in tab.txt_tuning_report.toPlainText()
    assert tab.btn_tuning_build.isEnabled() and not tab.btn_tuning_start.isEnabled()
    tab.plan()
    assert "model" in tab.txt_tuning_plan.toPlainText().lower()   # no model: said, nothing planned
    tab.txt_tuning_model.setText("HuggingFaceTB/SmolLM2-135M-Instruct")
    tab.cmb_tuning_board.setCurrentIndex(1)
    tab.txt_tuning_types.setText("Q8_0, q6_k")
    tab.plan()
    deadline = time.monotonic() + 10
    while not tab.btn_tuning_start.isEnabled() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert tab.btn_tuning_start.isEnabled(), tab.txt_tuning_plan.toPlainText()
    request, kw = service.planned
    assert request.board == "bcm2712" and kw["types"] == ["Q8_0", "q6_k"] and kw["local_paths"] is True
    assert "Q4_K_M-imatrix" in tab.txt_tuning_plan.toPlainText()
    tab.start()
    assert service.started and "req_tune0001" in tab.txt_tuning_plan.toPlainText()
    tab.build_recommended()
    assert service.built == ["a_0123456789abcdef"] and "req_build001" in tab.txt_tuning_report.toPlainText()
    tab.deleteLater()


# --- the AI tools: a tuning run only with the person's yes, every time ------------------------------------------------

from test_build_tools import ask, env  # noqa: E402,F401 - the build tools' environment (no network, no probe)


def _allow(env, *tools):  # noqa: F811
    from orchestrator.tools.policy import Decision, ToolPolicy
    current = env.registry.policy_store.load()
    env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=current.reviewed,
                                                 overrides={t: Decision.ALLOW for t in tools}))


def test_start_tuning_is_off_until_enabled_and_then_asks_the_person_every_time(env, monkeypatch):  # noqa: F811
    from orchestrator.Core import hf_lookup
    from orchestrator.tools.registry import ToolError
    monkeypatch.setattr(hf_lookup, "model_config", lambda facts, token, fetcher=None: None)
    from test_build_tools import facts
    env.service.hub = facts(size=1024 ** 3)
    arguments = {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "board": "rk3588"}
    with pytest.raises(ToolError):
        ask(env, arguments, name="start_tuning")
    assert env.orch.requests == []
    _allow(env, "start_tuning", "get_tuning_report")   # "allow" is capped at ask: the person still answers
    with pytest.raises(ToolError, match="needs your confirmation"):
        ask(env, {**arguments, "chunks": 8}, answer=False, name="start_tuning")
    assert env.orch.requests == [], "a no starts nothing"
    out, shown = ask(env, arguments, name="start_tuning")
    assert len(shown) == 1 and "Measures" in shown[0] and "Candidates:" in shown[0]
    (sent,) = env.orch.requests
    assert out.request_id == sent.request_id and sent.tuning and sent.owner
    assert sent.target_formats[0].value == "gguf" and sent.quantization_options == []
    report = env.registry.call("get_tuning_report", {}, __import__(
        "orchestrator.tools.registry", fromlist=["ToolContext"]).ToolContext("ditto"))
    assert report.runs == [] and report.report is None


# --- the command line -------------------------------------------------------------------------------------------------

def test_the_cli_plans_starts_lists_and_shows(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    cli = make_cli(booted_framework, tmp_path, monkeypatch)
    folder = _local_model(tmp_path, "SmolLM2 135M Instruct")
    empty = cli.invoke("build", "tune", "list")
    assert empty.code == 0 and "No tuning run yet" in empty.out
    result = cli.invoke("build", "tune", "start", "-m", str(folder), "-t", "arm64", "-b", "bcm2712", "--type", "Q2_K",
                        "--chunks", "8", "--yes")
    assert result.code == 0, result.out
    flat = " ".join(result.out.split())
    assert "Candidates: Q2_K-imatrix" in flat and "Q4_K_M-imatrix" in flat and "8 chunks of 512 tokens" in flat
    sent = cli.submitted[-1]
    assert sent.tuning["chunks"] == 8 and sent.tuning["candidates"][0]["origin"] == "yours"
    refused = cli.invoke("build", "tune", "start", "-m", str(folder), "-t", "arm64", "-b", "bcm2712", "--type",
                         "Q9_X", "--yes")
    assert refused.code == 2 and "not a GGUF type this target builds" in " ".join(refused.out.split())
    missing = cli.invoke("build", "tune", "show", "a_0123456789abcdef")
    assert missing.code == 2 and "Unknown tuning run" in missing.out
