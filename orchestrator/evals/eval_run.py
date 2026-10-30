#!/usr/bin/env python3
"""
One evaluation run as `llm-cli ditto eval` makes it, shared with the GUI's Ditto > Evaluation... (W1': every CLI
action by mouse). The CLI prints what happens and exits with its codes; the GUI shows the same in a window. Both call:

- check_choice(): --tier runs the managed local model and cannot be combined with --provider/--model.
- select_cases(): the cases of a cases file (default: the installation's Ditto cases, plus the retrieval cases when a
  kind or a case id asks for them), filtered by kind and case id.
- run_evaluation(): AI cases against the configured or chosen provider/model, or against the managed local model of
  each chosen tier (the server is started and stopped per tier; a server the person started keeps running, a replaced
  one is reported, also when the run ends early through Cancel or an error); then retrieval cases against a temporary
  knowledge base, one report per search variant.
- summary_table() / retrieval_table(): the result tables (columns and cells, the same strings in both).
- report_json() / write_report(): the full report (`--json FILE`; the GUI's "Save report...").

What happens is told through EvalEvents (a busy step, a run that starts, each case's result, a replaced server, options
that do not apply); a problem raises EvalRunError with the CLI's message and exit code, a failed server start
LocalRuntimeError, a Ditto that is not set up DittoError. A set stop event (threading.Event, the GUI's Cancel) ends the
run with EvalCancelled before the next attempt; the reports complete by then come with it.

Nothing is built or changed. Cloud providers bill every request (about 20 per run and repeat).
"""

import contextlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, List, Optional, Sequence, Tuple

from orchestrator.evals import ditto_eval as de
from orchestrator.utils.locality import offline_mode

TIER_CONFLICT = "--tier runs the managed local model; it cannot be combined with --provider/--model."
TASKS = ("chat", "json", "healing")   # each task may use its own model; the report names each one


class EvalRunError(Exception):
    """An evaluation that cannot run (or not go on), with the CLI's message and exit code (2: the choice or the files,
    1: the run itself). `code` names the reason for a window."""

    def __init__(self, message: str, exit_code: int = 1, code: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code


class EvalEvents:
    """What a run tells while it works; every method may be overridden (the default does nothing)."""

    def busy(self, step: str, **values: Any) -> ContextManager:
        """A step that takes a while: start_tier (tier), index (documents), reranker (model)."""
        return contextlib.nullcontext()

    def run_started(self, label: str, name: Optional[str], model: str, cases: int, repeat: int) -> None:
        """A run starts: its label, the provider's name (None for retrieval), the model, cases and repeats."""

    def result(self, result: Any) -> None:
        """One case is done (an evals.ditto_eval.CaseResult)."""

    def server_replaced(self, model: str) -> None:
        """The person's own llama-server (serving `model`) was replaced for the evaluation and is not running now."""

    def unused_options(self) -> None:
        """--tier, --provider, --model or --effort were given, but only retrieval cases run (they need none)."""


def check_choice(tiers: Sequence[str] = (), provider: Optional[str] = None, model: Optional[str] = None) -> None:
    if tiers and (provider or model):
        raise EvalRunError(TIER_CONFLICT, 2, "tier_with_provider")


def case_files(cases_file: Optional[Path] = None, kinds: Sequence[str] = (),
               case_ids: Sequence[str] = ()) -> List[Path]:
    """The cases files a run reads: the given one, else the Ditto cases and, when asked for, the retrieval cases
    (they need the embedding model, not an AI model)."""
    if cases_file:
        return [Path(cases_file)]
    files = [] if set(kinds) == {"retrieval"} else [de.DEFAULT_CASES]
    if "retrieval" in kinds or (case_ids and not kinds):
        files.append(de.RETRIEVAL_CASES)
    return files


def all_cases(cases_file: Optional[Path] = None) -> List[Any]:
    """Every case a run can choose from (the GUI's case list): the given file's, else the Ditto and retrieval cases.
    Raises EvalRunError (exit code 2) when a file cannot be read."""
    files = [Path(cases_file)] if cases_file else [de.DEFAULT_CASES, de.RETRIEVAL_CASES]
    try:
        return [c for path in files for c in de.load_cases(path)]
    except (OSError, ValueError) as e:
        raise EvalRunError(f"Cannot read the cases: {e}", 2, "cases_unreadable") from None


def select_cases(cases_file: Optional[Path] = None, kinds: Sequence[str] = (),
                 case_ids: Sequence[str] = ()) -> List[Any]:
    """The cases of a run, filtered by kind and case id; EvalRunError (exit code 2) when none match or a file cannot
    be read."""
    try:
        cases = [c for path in case_files(cases_file, kinds, case_ids) for c in de.load_cases(path)]
    except (OSError, ValueError) as e:
        raise EvalRunError(f"Cannot read the cases: {e}", 2, "cases_unreadable") from None
    cases = [c for c in cases if (not kinds or c.kind in kinds) and (not case_ids or c.id in case_ids)
             and opted_in(c, kinds, case_ids)]
    if not cases:
        raise EvalRunError("No case matches the filters.", 2, "no_cases")
    if any(c.kind in de.PAID_KINDS for c in cases) and any(c.kind not in de.PAID_KINDS for c in cases):
        raise EvalRunError("The paid OpenAI test (--kind openai_paid) runs on its own, without other kinds.", 2,
                           "paid_not_alone")
    return cases


def opted_in(case: Any, kinds: Sequence[str] = (), case_ids: Sequence[str] = ()) -> bool:
    """Whether a case of an opt-in kind was asked for: tokens by its kind or its id, openai_paid only by its kind
    (`--kind openai_paid`; a case id alone never starts a paid call). Every other case: True."""
    if case.kind in de.PAID_KINDS:
        return case.kind in kinds
    if case.kind in de.OPT_IN_KINDS:
        return case.kind in kinds or case.id in case_ids
    return True


# --- the paid OpenAI test: who may start it, what it costs, what it leaves ------------------------------------------

CI_VARIABLES = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION")
OPENAI_CHECK_FILE = "openai_cache_check.json"


def in_ci(environ: Optional[Dict[str, str]] = None) -> bool:
    """Whether this process runs in a CI system (its usual variables set to something that is not off)."""
    env = os.environ if environ is None else environ
    return any(str(env.get(name, "")).strip().lower() not in ("", "0", "false", "no") for name in CI_VARIABLES)


def check_paid(framework: Any, cases: Sequence[Any], *, provider: Optional[str], tiers: Sequence[str] = (),
               i_pay: bool = False) -> None:
    """The gate of the paid OpenAI test (round 33 Q2): it runs only when the person said yes (`--i-pay` at a
    terminal, the confirmation of Ditto > Evaluation...), with `--provider openai`, never in CI, never in offline
    mode. EvalRunError (exit code 2) otherwise; nothing when no paid case is among `cases`."""
    if not any(c.kind in de.PAID_KINDS for c in cases):
        return
    if in_ci():
        raise EvalRunError("The paid OpenAI test never runs in CI.", 2, "paid_in_ci")
    if offline_mode(getattr(framework, "config", None)):
        raise EvalRunError("Offline mode is on: the paid OpenAI test needs OpenAI's cloud API and does not run.", 2,
                           "offline")
    if tiers or (provider or "").strip().lower() != "openai":
        raise EvalRunError("The paid OpenAI test runs only with --provider openai (your own OpenAI key).", 2,
                           "paid_needs_openai")
    if not i_pay:
        raise EvalRunError("The paid OpenAI test bills your OpenAI account: start it with --i-pay at a terminal, or "
                           "from the evaluation window after its confirmation.", 2, "paid_not_confirmed")


def paid_estimate(cases: Sequence[Any], repeat: int = 1) -> Dict[str, int]:
    """What the paid cases of a run cost at most: calls (a refused 12h request is sent again without the option, and
    the count is one call more) and input tokens (about four characters per token)."""
    calls = tokens = 0
    for case in cases:
        if case.kind not in de.PAID_KINDS:
            continue
        size = math.ceil((len(case.prefix.strip()) + 2) * case.prefix_repeat / 4) + math.ceil(len(case.question) / 4)
        n = 4 if case.check == "cache_ttl" else 2
        calls += n * max(1, repeat)
        tokens += n * size * max(1, repeat)
    return {"calls": calls, "input_tokens": tokens}


def openai_check_data(runs: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """The input of the cloud-cache work (openai_cache_check.json): the last finished measurement of each paid check,
    booleans and token numbers only (never prompt text, never a key, no model name); None without a measurement."""
    found: Dict[str, Dict[str, Any]] = {}
    for report in runs:
        for result in report.results:
            if result.case.kind not in de.PAID_KINDS:
                continue
            for attempt in result.attempts:
                if attempt.passed and attempt.measurement:
                    found[attempt.measurement.get("check", result.case.check)] = attempt.measurement
    if not found:
        return None

    def numbers(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): numbers(v) for k, v in value.items() if k != "check"}
        return value if isinstance(value, (bool, int, float)) or value is None else None

    data: Dict[str, Any] = {"version": 1, "measured_unix": int(time.time())}
    for check in de.PAID_CHECKS:
        if check in found:
            data[check] = numbers(found[check])
    return data


def openai_check_path(config: Any) -> Path:
    """Where openai_cache_check.json lives: the per-user state folder's evals/ (beside the other per-user state)."""
    base = getattr(config, "user_state_dir", None) or os.environ.get("LLM_FRAMEWORK_HOME") \
        or Path.home() / ".llm-framework"
    return Path(base) / "evals" / OPENAI_CHECK_FILE


def save_openai_check(path: Path, runs: Sequence[Any]) -> Optional[Path]:
    """Writes openai_cache_check.json (owner-only where the system has modes); None when no measurement finished."""
    data = openai_check_data(runs)
    if data is None:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _same_server(a: Any, b: Any) -> bool:
    # pid and port: a server in Ditto's container has no process of this computer (pid 0)
    return (a.pid, getattr(a, "port", None)) == (b.pid, getattr(b, "port", None))


def run_evaluation(framework: Any, cases: Sequence[Any], *, make_ditto: Callable[[], Any],
                   make_runtime: Optional[Callable[[], Any]] = None, tiers: Sequence[str] = (),
                   provider: Optional[str] = None, model: Optional[str] = None, effort: Optional[str] = None,
                   cases_file: Optional[Path] = None, rag_variants: Sequence[str] = (), repeat: int = 1,
                   events: Optional[EvalEvents] = None, stop: Any = None, pause_minutes: Optional[float] = None,
                   i_pay: bool = False, local_ctx: Optional[int] = None) -> List[Any]:
    """Runs `cases` and returns one report per run (AI runs first, then the retrieval variants).

    make_ditto() gives the Ditto for the AI cases with the run's provider ("local" with tiers), model and effort set
    for this run only (called only when there are AI cases); make_runtime() the LocalLLMRuntime for `tiers`. provider,
    model and effort name the run (labels and the report's setup). pause_minutes: a pause between the turns of a
    tokens case and the two calls of the paid cache test (`--pause-minutes`; none by default, refused in CI).
    i_pay: the person's yes for the paid OpenAI test (check_paid). local_ctx: the context a tier's server is started
    with (measurement scripts; None: the server's own choice, as `ditto runtime start`)."""
    events = events or EvalEvents()
    runs: List[Any] = []
    if pause_minutes:
        if in_ci():
            raise EvalRunError("--pause-minutes never runs in CI (it would make a run hours long).", 2,
                               "pause_in_ci")
        if not any(c.kind in ("tokens", *de.PAID_KINDS) for c in cases):
            raise EvalRunError("--pause-minutes applies to tokens and openai_paid cases only.", 2, "pause_unused")
    check_paid(framework, cases, provider=provider, tiers=tiers, i_pay=i_pay)
    ai_cases = [c for c in cases if c.kind != "retrieval"]
    retrieval_cases = [c for c in cases if c.kind == "retrieval"]
    try:
        if ai_cases:
            _run_ai(framework, ai_cases, runs, make_ditto, make_runtime, tiers, provider, model, effort, cases_file,
                    repeat, events, stop, pause_minutes=pause_minutes, local_ctx=local_ctx)
        elif tiers or provider or model or effort:
            events.unused_options()
        if retrieval_cases:
            runs += run_retrieval(framework, retrieval_cases, Path(cases_file or de.RETRIEVAL_CASES),
                                  rag_variants or ("fused",), repeat, events, stop)
    except de.EvalCancelled as e:
        e.runs = list(runs)
        raise
    return runs


def _run_ai(framework, ai_cases, runs, make_ditto, make_runtime, tiers, provider, model, effort, cases_file, repeat,
            events: EvalEvents, stop, pause_minutes=None, local_ctx=None) -> None:
    from orchestrator.Core.ditto_manager import DittoError
    ditto = make_ditto()
    targets_dir = framework.resolve_path(framework.config.get("targets_dir", "targets"))

    def run_once(label: str) -> None:
        ditto.reload()
        setup = ditto.describe("chat")   # DittoError when not set up
        measuring = any(c.kind in ("tokens", *de.PAID_KINDS) for c in ai_cases)
        if measuring and setup.get("local") is False and offline_mode(getattr(framework, "config", None)):
            # the one locality rule decided the provider is not local: a measurement never tries it offline
            raise EvalRunError(f"Offline mode is on: {setup.get('label')} is not local, so the token measurement "
                               "does not run. Choose a local model (--tier).", 2, "offline")
        events.run_started(label, setup["label"], setup["model"], len(ai_cases), repeat)
        options = {"pause_minutes": pause_minutes} if pause_minutes else {}   # run() hands over the stop event
        report = de.DittoEval(ditto, targets_dir, **options).run(ai_cases, repeat=repeat, on_result=events.result,
                                                                 stop=stop)
        models = {}
        for task in TASKS:
            try:
                models[task] = ditto.describe(task).get("model", "")
            except DittoError:
                models[task] = ""
        report.setup = de.run_setup(label, setup.get("provider", ""), setup.get("model", ""), cases_file, ai_cases,
                                    repeat, task_models=models, effort=effort or "settings")
        runs.append(report)

    if not tiers:
        run_once("configured model" if not (provider or model) else f"{provider or ''} {model or ''}".strip())
        return
    rt = make_runtime()
    before = rt.endpoint()  # a server the person started keeps running
    try:
        for tier in tiers:
            de._check_stop(stop)
            try:
                with events.busy("start_tier", tier=tier):
                    rt.start(tier=tier, remember=False, **({"ctx": int(local_ctx)} if local_ctx else {}))
                run_once(f"tier {tier}")
            finally:
                running = rt.endpoint()
                if before is None or running is None or not _same_server(running, before):
                    rt.stop()  # the eval's own server (or what is left of a failed start)
    finally:
        # Also when the run ends early (Cancel, a failed start): the person learns that their server is gone.
        if before is not None:
            try:
                after = rt.endpoint()
            except Exception:  # noqa: BLE001 - the run's own outcome (or error) is what counts
                after = None
            if after is None or not _same_server(after, before):
                events.server_replaced(before.model)


def run_retrieval(framework: Any, cases: Sequence[Any], source: Path, variants: Sequence[str], repeat: int = 1,
                  events: Optional[EvalEvents] = None, stop: Any = None) -> List[Any]:
    """Retrieval cases: the corpus is indexed once into a temporary knowledge base (the person's own is not touched),
    one report per variant. A cases file with a 'carriers' folder adds its hidden-text carrier pages: indexed like a
    Deep Ingest and checked (a payload that reaches the stored excerpts fails the run, exit 1 "index_failed"); each
    report's setup lists them (carrier_documents, carriers)."""
    events = events or EvalEvents()
    try:
        folder = de.corpus_dir(source)
        docs = de.load_corpus(folder)
        carrier_folder = de.carriers_dir(source)
        carriers = de.load_carriers(carrier_folder)
        de.check_retrieval_cases(cases, [*docs, *carriers])
    except (OSError, ValueError) as e:
        raise EvalRunError(f"Cannot read the retrieval corpus: {e}", 2, "corpus_unreadable") from None
    reports = []
    with de.temporary_knowledge_base(framework) as knowledge:
        ev = de.RetrievalEval(knowledge, [*docs, *carriers])
        start = time.monotonic()
        try:
            with events.busy("index", documents=len(docs)):
                ev.ingest()
        except de.RetrievalEvalError as e:
            raise EvalRunError(str(e), 1, "index_failed") from None
        de._check_stop(stop)   # Cancel while indexing: no reranker is loaded (downloaded on first use)
        index_seconds = round(time.monotonic() - start, 1)
        embedding = str(knowledge.get_status().get("embedding_model", ""))
        reranker = knowledge.reranker() if "rerank" in variants else None
        if "rerank" in variants:
            if reranker is None:
                raise EvalRunError(f"The rerank variant needs a reranker: {knowledge.reranker_reason}. Set "
                                   "rag_reranker to a model id to grade one anyway.", 2, "no_reranker")
            with events.busy("reranker", model=reranker.model_id):
                ready = knowledge.prepare_models()
            if not ready:
                raise EvalRunError(str(knowledge.last_error), 1, "reranker_failed")
        for variant in dict.fromkeys(variants):
            label, reranked = f"retrieval {variant}", variant == "rerank"
            model = f"{embedding} + {reranker.model_id}" if reranked else embedding
            events.run_started(label, None, model, len(cases), repeat)
            report = ev.run(cases, variant=variant, repeat=repeat, on_result=events.result, stop=stop)
            try:
                import psutil
                rss_mb = round(psutil.Process().memory_info().rss / 2 ** 20)  # models loaded
            except Exception:
                rss_mb = None
            used = {"model": reranker.model_id, "min_score": knowledge.rerank_floor(reranker)} if reranked else None
            carrier_setup = {"carrier_documents": len(carriers), "carriers": getattr(ev, "carrier_checks", []),
                             "carriers_sha256": de.corpus_digest(carrier_folder, "*.html")} if carriers else {}
            report.setup = de.run_setup(
                label, "knowledge base", model, source, cases, repeat, variant=variant, reranker=used,
                model_reason=knowledge.model_reason, corpus_sha256=de.corpus_digest(folder), corpus_documents=len(docs),
                index_seconds=index_seconds, rss_mb=rss_mb, **carrier_setup)
            reports.append(report)
    return reports


def summary_table(runs: Sequence[Any], kinds: Sequence[str]) -> Tuple[List[str], List[List[str]]]:
    """The "Ditto evaluation" table: one row per run with its pass rate in all, per kind ("-" when the run had none
    of a kind) and the average seconds per case."""
    kinds = list(kinds)
    columns = ["Run", "Model", "Passed", *kinds, "Avg s/case"]
    rows = []
    for report in runs:
        rates = report.by_kind()
        avg = sum(r.seconds for r in report.results) / len(report.results) if report.results else 0.0
        rows.append([str(report.setup["label"]), str(report.setup["model"]), f"{report.pass_rate:.0%}",
                     *[f"{rates[k]:.0%}" if k in rates else "-" for k in kinds], f"{avg:.1f}"])
    return columns, rows


def retrieval_table(runs: Sequence[Any]) -> Optional[Tuple[List[str], List[List[str]]]]:
    """The "Retrieval" table of the runs with retrieval metrics (None when there are none)."""
    metrics = [(report, report.retrieval()) for report in runs if report.retrieval() is not None]
    if not metrics:
        return None

    def share(value):
        return "-" if value is None else f"{value:.0%}"

    columns = ["Run", "hit@1", "hit@3", "hit@k", "MRR", "Unrelated: no excerpt", "p50 ms"]
    rows = [[str(report.setup["label"]), share(m["hit@1"]), share(m["hit@3"]), share(m["hit@k"]),
             "-" if m["mrr"] is None else f"{m['mrr']:.3f}", share(m["empty_when_irrelevant"]),
             f"{m['latency_p50_ms']:.0f}"] for report, m in metrics]
    return columns, rows


def tokens_table(runs: Sequence[Any]) -> Optional[Tuple[List[str], List[List[str]]]]:
    """The "Tokens" table: one row per tokens conversation (case and attempt) with its turns, the provider's input,
    output, cache read and cache write tokens and the cache-read share; None when no run had one."""
    rows = []
    for report in runs:
        for row in report.conversations():
            share = row.get("cache_read_share")
            rows.append([str(report.setup.get("label", "")), str(row["id"]), str(row["turns"]),
                         str(row["input_tokens"]), str(row["output_tokens"]), str(row["cache_read_tokens"]),
                         str(row["cache_write_tokens"]), "-" if share is None else f"{share:.0%}"])
    if not rows:
        return None
    return ["Run", "Case", "Turns", "Input", "Output", "Cache read", "Cache write", "Cache-read share"], rows


def outcome_note(result: Any) -> str:
    """The CLI's and the window's word for a case that is not counted: "expected to fail", "UNEXPECTED PASS" (the
    change it waited for has come: remove expected_fail) or "skipped"; "" for every counted case."""
    return {"xfail": "expected to fail", "xpass": "UNEXPECTED PASS (remove expected_fail)",
            "skipped": "skipped"}.get(result.outcome, "")


def report_data(runs: Sequence[Any]) -> Dict[str, Any]:
    return {"runs": [r.to_dict() for r in runs]}


def report_json(runs: Sequence[Any]) -> str:
    """The full report as `--json FILE` writes it."""
    return json.dumps(report_data(runs), indent=2, ensure_ascii=False)


def write_report(path: Path, runs: Sequence[Any]) -> Path:
    path = Path(path)
    path.write_text(report_json(runs), encoding="utf-8")
    return path


__all__ = ["CI_VARIABLES", "EvalEvents", "EvalRunError", "OPENAI_CHECK_FILE", "TIER_CONFLICT", "all_cases",
           "case_files", "check_choice", "check_paid", "in_ci", "openai_check_data", "openai_check_path", "opted_in",
           "outcome_note", "paid_estimate", "report_data", "report_json", "retrieval_table", "run_evaluation",
           "run_retrieval", "save_openai_check", "select_cases", "summary_table", "tokens_table", "write_report"]
