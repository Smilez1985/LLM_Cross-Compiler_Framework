#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - What an AI reads of Mojo's findings

The framework's folders by label and any other path by its file name only, secrets redacted; the person's own
output (the CLI, `--json` included) keeps the full paths. run_doctor reads ai_findings; run_mojo_oracle reads
oracle_rows (plus where, why and what Mojo Miracle can do) and run_mojo_miracle miracle_row.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List

from orchestrator.Core.mojo.findings import MojoReport, Severity


def ai_folder_labels(framework) -> Dict[str, str]:
    """The framework's own folders by the label an AI may read: state (the per-user folder), cache, output, builds
    (the framework's build folder), models, targets, community and framework (the installation)."""
    roots: Dict[str, str] = {}

    def add(path: Any, label: str) -> None:
        try:
            if path:
                roots.setdefault(str(Path(path)), label)
        except (TypeError, ValueError):
            pass

    config = getattr(framework, "config", None)
    try:
        add(getattr(config, "user_state_dir", None), "state")
    except Exception:  # noqa: BLE001 - a config that cannot name it: no label, the file name rule still applies
        pass
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    add(getattr(engine, "cache_dir", None), "cache")
    add(getattr(engine, "output_dir", None), "output")
    resolve = getattr(framework, "resolve_path", None)
    for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("build_dir", "builds"),
                       ("models_dir", "models")):
        try:
            value = config.get(key, None) if config is not None else None
            if value:
                add(resolve(value) if callable(resolve) else value, label)
        except Exception:  # noqa: BLE001
            continue
    add(getattr(getattr(framework, "target_manager", None), "targets_dir", None), "targets")
    add(getattr(getattr(framework, "community_manager", None), "community_dir", None), "community")
    add(getattr(getattr(framework, "info", None), "installation_path", None), "framework")
    add(getattr(framework, "app_root", None), "framework")
    return roots


def ai_findings(framework, report: MojoReport) -> List[Dict[str, Any]]:
    """The findings as an AI may read them (the run_doctor tool): every message and fix hint, a crashed check's
    included, with the framework's folders named by label ('the state folder', 'state:audit.db') and any other path
    by its file name only - no host path, no user name - and secrets redacted. The person's own output (the CLI,
    `--json` included) keeps the full paths."""
    label = labeller(framework)
    return [{"subsystem": f.subsystem, "code": f.code, "severity": f.severity.value, "message": label(f.message),
             "fix_hint": label(f.fix_hint)} for f in report.findings]


def labeller(framework) -> Callable[[Any], str]:
    """How any text of Mojo reaches an AI: the framework's folders by label, any other path by its file name only,
    secrets redacted (the rule of ai_findings, for Mojo's other views)."""
    from orchestrator.tools.tools_runtime import label_paths
    from orchestrator.utils.helpers import redact_secrets
    roots = ai_folder_labels(framework)

    def label(text: Any) -> str:
        return label_paths(redact_secrets(str(text or "")), roots)

    return label


# What an AI does next about each verdict of Mojo Miracle (run_mojo_oracle's repair.next and the refusals of
# run_mojo_miracle).
AI_NEXT = {
    "miracle": 'When the person wants it repaired, call run_mojo_miracle {{"code": "{code}"}}; the framework shows '
               "them the plan first.",
    "deletion_list": "No tool deletes: tell the person they choose what is deleted in the framework (Tools > "
                     "Framework Build Images... in the GUI, or 'llmcf mojo --miracle' in a terminal).",
    "person": "Tell the person what Mojo Oracle found and the fix hint: only they can do it.",
    "admin": "Tell the person this needs an administrator, with the fix hint.",
    "not_automatic": "Say honestly that nothing repairs it automatically, and give the person the fix hint.",
}
# How the summary sentence names each verdict that is no repair: (one, several) for the person and for an AI.
_NOT_REPAIRED = {
    "deletion_list": (("needs your list of what to delete", "need your list of what to delete"),
                      ("needs the person's list of what to delete", "need the person's list of what to delete")),
    "person": (("needs you", "need you"), ("needs the person", "need the person")),
    "admin": (("needs an administrator", "need an administrator"),) * 2,
    "not_automatic": (("cannot be repaired automatically", "cannot be repaired automatically"),) * 2,
}


def oracle_rows(framework, report: MojoReport, offered) -> List[Dict[str, Any]]:
    """run_mojo_oracle's findings: the fields of ai_findings plus where (a label), why it matters and, for each warning
    and error, what Mojo Miracle can do about it (offers.Offer)."""
    label = labeller(framework)
    by_finding = {id(o.finding): o for o in offered}
    rows = []
    for row, finding in zip(ai_findings(framework, report), report.findings):
        offer = by_finding.get(id(finding))
        repair = None if offer is None else {"how": offer.verdict, "summary": label(offer.summary),
                                             "next": AI_NEXT[offer.verdict].format(code=finding.code)}
        rows.append({**row, "where": label(finding.where), "why": label(finding.why), "repair": repair})
    return rows


def oracle_summary(offered, person: bool = False) -> str:
    """One sentence: how many problems Mojo Oracle found and what Mojo Miracle can do about them (every verdict is
    counted, so nothing is left out). `person`: worded for the person ("needs you") instead of for an AI."""
    if not offered:
        return "Mojo Oracle found no problems."
    verdicts = Counter(o.verdict for o in offered)
    errors = sum(1 for o in offered if o.finding.severity == Severity.ERROR)
    parts = [f"Mojo Miracle can repair {verdicts['miracle']}" if verdicts["miracle"] else
             "Mojo Miracle can repair none of them by itself"]
    for verdict, words in _NOT_REPAIRED.items():
        if verdicts[verdict]:
            one, several = words[0 if person else 1]
            parts.append(f"{verdicts[verdict]} {one if verdicts[verdict] == 1 else several}")
    found = f"{len(offered)} problem" + ("" if len(offered) == 1 else "s")
    return (f"Mojo Oracle found {found} ({errors} error{'' if errors == 1 else 's'}, {len(offered) - errors} "
            f"warning{'' if len(offered) - errors == 1 else 's'}): " + "; ".join(parts) + ".")


def miracle_row(framework, result) -> Dict[str, Any]:
    """run_mojo_miracle's result (repairs.engine.RepairResult) as an AI may read it."""
    label = labeller(framework)
    return {"code": result.finding_code, "outcome": result.outcome, "repaired": result.repaired,
            "what": label(result.summary), "reason": label(result.reason)}
