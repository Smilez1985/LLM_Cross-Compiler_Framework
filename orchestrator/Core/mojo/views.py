#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - What an AI reads of Mojo's findings

The framework's folders by label and any other path by its file name only, secrets redacted; the person's own
output (the CLI, `--json` included) keeps the full paths. run_doctor reads ai_findings; run_mojo_oracle reads
oracle_rows (plus where, why and what Mojo Miracle can do) and run_mojo_miracle miracle_row.

The person's view (at the end of this module) is what `llmcf mojo` prints and what the GUI's Mojo window
(Tools > Mojo Health Check...) shows, with the full paths: each finding with its offer, what Mojo Miracle can do
(miracle_can: a kind and its values; the CLI's English words in MIRACLE_LINES, the GUI fills the same kinds from its
own texts), and what became of each problem after Mojo Miracle (outcome_kind, entry_text, entry_next,
miracle_summary).
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.Core.mojo.findings import Finding, MojoReport, Severity


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
    from orchestrator.security.paths import label_paths
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
    "deletion_list": "No tool deletes: tell the person they choose what is deleted in the framework ({where}).",
    "person": "Tell the person what Mojo Oracle found and the fix hint: only they can do it.",
    "miracle_yes": "No tool starts it: tell the person Mojo Miracle does it after their own yes, at a terminal or in "
                   "the framework's Mojo window, with its plan and what it costs shown first.",
    "admin": "Tell the person this needs an administrator, with the fix hint.",
    "not_automatic": "Say honestly that nothing repairs it automatically, and give the person the fix hint.",
}
# The person's list for a deletion finding (offers.DELETION_LISTS): the framework's build images
IMAGE_LIST = "Tools > Framework Build Images... in the GUI, or 'llmcf mojo --miracle' in a terminal"


def ai_next(verdict: str, code: str) -> str:
    """What an AI does next about a finding's verdict (AI_NEXT). A deletion finding names the person's list that frees
    it: the knowledge snapshots their own list (offers.OTHER_LISTS), never the build images, which free no snapshot
    room."""
    from orchestrator.Core.mojo.offers import OTHER_LISTS
    return AI_NEXT[verdict].format(code=code, where=OTHER_LISTS.get(code, IMAGE_LIST))
# How the summary sentence names each verdict that is no repair: (one, several) for the person and for an AI.
_NOT_REPAIRED = {
    "deletion_list": (("needs your list of what to delete", "need your list of what to delete"),
                      ("needs the person's list of what to delete", "need the person's list of what to delete")),
    "miracle_yes": (("Mojo Miracle does after your yes", "Mojo Miracle does after your yes"),
                    ("Mojo Miracle does only after the person's yes", "Mojo Miracle does only after the person's yes")),
    "person": (("needs you", "need you"), ("needs the person", "need the person")),
    "admin": (("needs an administrator", "need an administrator"),) * 2,
    "not_automatic": (("cannot be repaired automatically", "cannot be repaired automatically"),) * 2,
}


def board_request(name: str) -> str:
    """What an AI may ask for a board a recorded fact points to (W1): the check of that one board, by the name
    run_mojo_oracle takes (its label or device ID, never an address)."""
    request = json.dumps({"board": name}, ensure_ascii=False)
    return (f"Mojo did not contact this board. To check it now (one connection to its SSH port, no login), call "
            f"run_mojo_oracle {request}; tell the person first if they did not ask for it.")


def oracle_rows(framework, report: MojoReport, offered) -> List[Dict[str, Any]]:
    """run_mojo_oracle's findings: the fields of ai_findings plus where (a label), why it matters and, for each warning
    and error, what Mojo Miracle can do about it (offers.Offer); a board a recorded fact points to names the request
    that checks it (check_board)."""
    label = labeller(framework)
    by_finding = {id(o.finding): o for o in offered}
    rows = []
    for row, finding in zip(ai_findings(framework, report), report.findings):
        offer = by_finding.get(id(finding))
        # a repair of class person reads as the person's for an AI (repair.how): it cannot start it
        how = "person" if offer is not None and offer.verdict == "miracle_yes" else getattr(offer, "verdict", "")
        repair = None if offer is None else {"how": how, "summary": label(offer.summary),
                                             "next": ai_next(offer.verdict, finding.code)}
        rows.append({**row, "where": label(finding.where), "why": label(finding.why), "repair": repair,
                     "check_board": board_request(finding.board_name) if finding.board_check else None})
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


# --- the person's view (the CLI and the GUI's Mojo window; full paths, nothing of it goes to an AI) ------------------

# What Mojo Miracle can do about one problem, in the CLI's words (miracle_can gives the kind and its values).
MIRACLE_LINES = {
    "repair": "Mojo Miracle can repair it: {summary}.",
    "list": "Mojo Miracle shows you the framework's build images to choose from (name, id, size, what each is; "
            "nothing is ticked); only what you tick and confirm is deleted.",
    "list_elsewhere": "Mojo Miracle cannot repair it here: {reason}; {where}.",
    "other_list": "Mojo Miracle cannot repair it: {reason}; you choose what is deleted or exported: {where}.",
    "cannot": "Mojo Miracle cannot repair it: {reason}.",
    "ask": "Mojo Miracle can do it after your yes: {summary}. It asks you first (No is the default).",
    "ask_elsewhere": "Mojo Miracle does it only after your yes: {summary}. Run Mojo Miracle with you present to be "
                     "asked.",
}
# How the person reads what became of a problem after Mojo Miracle (outcome_kind); any other outcome: "not_repaired".
OUTCOME_KINDS = ("repaired", "already_fixed", "still_present", "declined", "not_repaired")


def opens_list(offer) -> bool:
    """Whether Mojo Miracle, with the person present (a terminal, the GUI), opens the person's list for this deletion
    finding (offers.DELETION_LISTS: the framework's build images)."""
    from orchestrator.Core.mojo.offers import DELETION_LISTS
    return offer.finding.code in DELETION_LISTS


def miracle_acts(offered) -> List[Any]:
    """The offers Mojo Miracle acts on with the person present: its repairs and the deletion findings whose list it
    opens. Mojo Miracle is offered only when there is one."""
    return [o for o in offered if o.verdict in ("miracle", "miracle_yes")
            or (o.verdict == "deletion_list" and opens_list(o))]


def miracle_can(offer, at_terminal: bool) -> Tuple[str, Dict[str, str]]:
    """What Mojo Miracle can do about one problem: a kind of MIRACLE_LINES and its values. `at_terminal`: the person
    is present to choose from a list (a terminal, the GUI)."""
    from orchestrator.Core.mojo.offers import DELETION_WHERE, OTHER_LISTS
    if offer.verdict == "miracle":
        return "repair", {"summary": offer.plan.summary}
    if offer.verdict == "miracle_yes":
        return ("ask" if at_terminal else "ask_elsewhere"), {"summary": offer.plan.summary}
    if offer.verdict == "deletion_list" and opens_list(offer):
        if at_terminal:
            return "list", {}
        return "list_elsewhere", {"reason": offer.reason, "where": DELETION_WHERE}
    if offer.verdict == "deletion_list" and offer.finding.code in OTHER_LISTS:
        return "other_list", {"reason": offer.reason, "where": OTHER_LISTS[offer.finding.code]}
    return "cannot", {"reason": offer.reason}


def miracle_line(offer, at_terminal: bool) -> str:
    """The person's line about what Mojo Miracle can do for one problem, in the CLI's words."""
    kind, values = miracle_can(offer, at_terminal)
    return MIRACLE_LINES[kind].format(**values)


def plan_steps(offer) -> List[Any]:
    """The steps of the plan Mojo Miracle would apply (target_label, full_path, before, after, undoable); [] when it
    does not repair the problem."""
    if offer is None or offer.verdict not in ("miracle", "miracle_yes") or offer.plan is None:
        return []
    return list(offer.plan.steps)


def person_findings(report: MojoReport, offered) -> List[Tuple[Finding, Optional[Any]]]:
    """Every finding of the report in its order, with its offer (None for what is no problem)."""
    by_finding = {id(o.finding): o for o in offered}
    return [(finding, by_finding.get(id(finding))) for finding in report.findings]


def outcome_kind(entry) -> str:
    """How the person reads what became of one problem (OUTCOME_KINDS)."""
    return entry.outcome if entry.outcome in OUTCOME_KINDS[:-1] else "not_repaired"


def entry_text(entry) -> str:
    """What became of one problem, in the CLI's words: the repair and the check afterwards, or why not."""
    if entry.outcome == "repaired":
        return f"{entry.summary}. Checked again: the problem is gone."
    return entry.reason or entry.summary


def entry_next(entry) -> Tuple[str, str]:
    """The next step after Mojo Miracle for one problem: ("again", "") when its plan changed (a new check shows the
    new plan), ("hint", the fix hint) when it is still there, ("", "") when nothing is left to do."""
    if entry.outcome == "plan_changed":
        return "again", ""
    if entry.outcome not in ("repaired", "already_fixed") and entry.finding.fix_hint:
        return "hint", entry.finding.fix_hint
    return "", ""


def miracle_summary(result) -> str:
    """One sentence: how many problems Mojo Miracle repaired, how many were already gone or not repaired, and how many
    the check afterwards found new."""
    total = len(result.entries)
    if not total:
        return "Mojo Oracle found no problems, so Mojo Miracle had nothing to repair."
    gone, rest = len(result.already_gone), len(result.not_repaired)
    return (f"Mojo Miracle repaired {len(result.repaired)} of {total} problem{'' if total == 1 else 's'}"
            + (f"; {gone} {'was' if gone == 1 else 'were'} already gone" if gone else "")
            + (f"; {rest} {'was' if rest == 1 else 'were'} not repaired (why: above)" if rest else "")
            + (f"; the check afterwards found {len(result.new_problems)} new (above)" if result.new_problems
               else "") + ".")
