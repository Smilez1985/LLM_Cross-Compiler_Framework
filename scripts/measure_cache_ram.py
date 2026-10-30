#!/usr/bin/env python3
"""
Development only: how much memory Ditto's own local server needs for the `tokens` conversations.

    python scripts/measure_cache_ram.py --tier small [--ctx 4096] [--case tokens-build-talk] [--json out.json]

Runs the `tokens` cases of the Ditto evaluation (evals/eval_run.run_evaluation, the one runner of the CLI and the GUI)
against the managed local model of one tier. The server is started by the evaluation through runtime/llama_server.py's
own launcher with today's flags (`--ctx` only passes a context size to that launcher, as `ditto runtime start` would);
this script builds no command line of its own. While the run goes on, a thread samples the resident memory (RSS,
psutil) of that server, and only of the llama-server this process started (its child; a server the person started,
another user's process or anything else is never read). At the end it prints numbers only: the peak RSS in MiB, the
server's --cache-ram value in MiB, the number of samples and each conversation's token sums from the provider's usage.

Nothing is sent to a cloud provider: the run uses the local tier only. It never runs in CI.
"""

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MIB = 2 ** 20


class RssSampler:
    """Peak RSS of the llama-server the evaluation started: the endpoint's pid, read only while that process is this
    process's own child (psutil's ppid), and its --cache-ram value from its command line."""

    def __init__(self, runtime: Any, own_pid: Optional[int] = None, process: Optional[Callable[[int], Any]] = None):
        self.runtime = runtime
        self.own_pid = os.getpid() if own_pid is None else own_pid
        if process is None:
            import psutil
            process = psutil.Process
        self._process = process
        self.peak_mib = 0
        self.cache_ram_mib: Optional[int] = None
        self.samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def sample(self) -> None:
        try:
            endpoint = self.runtime.endpoint()
            pid = int(getattr(endpoint, "pid", 0) or 0) if endpoint is not None else 0
            if not pid:
                return   # no server, or one in a container (no process of this computer)
            proc = self._process(pid)
            if proc.ppid() != self.own_pid:
                return   # not the server this run started
            rss = int(proc.memory_info().rss)
            if self.cache_ram_mib is None:
                argv = list(proc.cmdline() or [])
                if "--cache-ram" in argv[:-1]:
                    self.cache_ram_mib = int(argv[argv.index("--cache-ram") + 1])
        except Exception:  # noqa: BLE001 - the process ended between two looks, or cannot be read: no sample
            return
        self.samples += 1
        self.peak_mib = max(self.peak_mib, rss // MIB)

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.sample()

    def start(self, interval: float = 0.5) -> None:
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="rss-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def numbers(sampler: RssSampler, runs: List[Any]) -> Dict[str, Any]:
    """The script's result: numbers only (no prompt text, no paths)."""
    conversations = [{k: v for k, v in row.items() if k != "id"} for report in runs
                     for row in report.conversations()]
    return {"peak_rss_mib": sampler.peak_mib, "cache_ram_mib": sampler.cache_ram_mib, "samples": sampler.samples,
            "conversations": conversations}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Peak RSS of Ditto's local server during the tokens cases "
                                                 "(development only).")
    parser.add_argument("--tier", required=True, help="the managed local model tier to run, e.g. small")
    parser.add_argument("--ctx", type=int, default=None, help="context size for the server (default: its own)")
    parser.add_argument("--case", action="append", default=[], help="only this tokens case (repeatable)")
    parser.add_argument("--json", type=Path, default=None, help="also write the numbers to this file")
    args = parser.parse_args(argv)

    from orchestrator.evals import eval_run
    if eval_run.in_ci():
        print("measure_cache_ram.py is a development tool; it does not run in CI.", file=sys.stderr)
        return 2
    from orchestrator.cli import FrameworkContext
    from orchestrator.Core.ai_override import ditto_with, override_values
    from orchestrator.runtime.llama_server import LocalLLMRuntime

    ctx = FrameworkContext()
    ctx.initialize()
    framework = ctx.framework_manager
    ditto = framework.get_component("ditto_manager")
    if ditto is None:
        print("Ditto is not available.", file=sys.stderr)
        return 1
    runtime = LocalLLMRuntime.from_framework(framework)
    cases = eval_run.select_cases(kinds=("tokens",), case_ids=tuple(args.case))
    values = override_values("local", None, None, ditto=ditto)
    sampler = RssSampler(runtime)
    sampler.start()
    try:
        runs = eval_run.run_evaluation(framework, cases, make_ditto=lambda: ditto_with(ditto, values),
                                       make_runtime=lambda: runtime, tiers=(args.tier,), local_ctx=args.ctx)
    finally:
        sampler.stop()
    result = numbers(sampler, runs)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
