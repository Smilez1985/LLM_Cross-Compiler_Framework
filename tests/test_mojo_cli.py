"""`llmcf mojo` and its aliases (round 6 design 7.8; round 7 items 11, 11', M' and T).

Mojo Oracle (the default) only looks and reports what it found; at a terminal it proposes Mojo Miracle and runs it on
"y" without the command typed again. Mojo Miracle repairs, then reports what it repaired, what not and why. Without
a terminal only the harmless repairs run; nothing is ever deleted except from the person's list at a terminal.
`llm-cli doctor` keeps working with a note naming the new command.
"""

import io
import json
import os
import stat
from pathlib import Path

import pytest

from orchestrator.Core.mojo import Finding, Severity, runner
from orchestrator.Core.mojo.checks import check_user_state

REPO = Path(__file__).resolve().parent.parent
POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


def run_cli(framework, monkeypatch, *args, terminal=False, answer=None):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
    result = CliRunner().invoke(cli_module.cli, list(args), obj=Ctx(), input=answer)
    return result, printed.getvalue() + result.output


@pytest.fixture
def open_state(booted_framework, monkeypatch):
    """The state folder open to other users, and the checks of this test: the real state check and four problems Mojo
    Miracle cannot repair, one of each reason."""
    state = Path(booted_framework.config.user_state_dir)
    # ConfigManager.user_state_dir makes the folder private on every read (next-fixes 118); here it stays open, as on a
    # computer where something opened it after the framework read it
    monkeypatch.setattr(type(booted_framework.config), "user_state_dir", property(lambda self: state))
    os.chmod(state, 0o755)

    def others(_fw):
        return [Finding("disk", "disk.cache", Severity.ERROR, "Only 1.0 GB free for the cache folder.",
                        "Free disk space.", where=str(state / "cache"), why="a full disk stops a build",
                        cannot="deletion_list"),
                Finding("ai", "tools.audit", Severity.ERROR, "The audit log was changed.", "Archive it.",
                        why="the record cannot be trusted", cannot="person"),
                Finding("docker", "docker.daemon", Severity.ERROR, "Docker is not reachable.", "Start Docker.",
                        why="builds cannot run", cannot="admin"),
                Finding("data", "data.calibration", Severity.WARN, "Calibration data changed.", "Restore it.",
                        why="damaged data", cannot="not_automatic"),
                Finding("python", "python.version", Severity.OK, "Python 3.13")]
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state, others])
    return state


def mode(path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


def test_llmcf_is_the_same_program_as_llm_cli():
    """Round 7 item 11: the console script llmcf next to llm-cli, both the same CLI group (PEP 621 [project.scripts])."""
    import tomllib
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["llmcf"] == scripts["llm-cli"] == "orchestrator.cli:cli"
    from orchestrator import cli as cli_module
    assert "mojo" in cli_module.cli.commands and "doctor" in cli_module.cli.commands


def test_the_framework_container_has_the_command_mojo_names():
    """Mojo's hints name 'llmcf mojo --miracle'. The framework container is the one installation that puts the
    command line on the PATH; before, it had only llm-cli, so the hint it printed named a command it did not have."""
    import re

    from orchestrator.Core.mojo import FREE_SPACE_HINT
    from orchestrator.Core.mojo.offers import DELETION_WHERE
    assert "llmcf mojo --miracle" in DELETION_WHERE and "llmcf mojo --miracle" in FREE_SPACE_HINT
    text = (REPO / "Docker Setup" / "Dockerfile").read_text(encoding="utf-8")
    assert "> /usr/local/bin/llm-cli" in text
    assert re.search(r"\bln -s llm-cli /usr/local/bin/llmcf\b", text), "llmcf: the same program as llm-cli"


@POSIX
def test_oracle_reports_what_where_why_and_what_miracle_can_do(booted_framework, open_state, monkeypatch):
    result, out = run_cli(booted_framework, monkeypatch, "mojo")
    text = " ".join(out.split())
    assert "Mojo Oracle: checking the installation (read-only, nothing is changed)" in text
    assert f"Where: {open_state}" in text and "Why it matters: Other users of this computer" in text
    assert "Mojo Miracle can repair it: only you can open the state folder then" in text
    assert f"Plan: the state folder ({open_state}): 0755 → 0700, can be undone" in text
    assert "Mojo Miracle cannot repair it here: deleting needs your list" in text
    assert "Mojo Miracle cannot repair it: needs you" in text and "needs an administrator" in text
    assert "not repairable automatically" in text
    assert ("Mojo Oracle found 5 problems (3 errors, 2 warnings): Mojo Miracle can repair 1; 1 needs your list of "
            "what to delete; 1 needs you; 1 needs an administrator; 1 cannot be repaired automatically.") in text
    assert "run 'llmcf mojo --miracle'" in text and "Run Mojo Miracle now?" not in text, "no terminal: no question"
    assert mode(open_state) == 0o755, "Mojo Oracle only looks"
    assert result.exit_code == 2


@POSIX
def test_oracle_proposes_miracle_at_a_terminal_and_runs_it_on_yes(booted_framework, open_state, monkeypatch):
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="\n")
    assert "Run Mojo Miracle now? [y/N]" in out and "Mojo Miracle did not run; nothing was changed." in out
    assert mode(open_state) == 0o755, "No is the default"
    result, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n\n")
    text = " ".join(out.split())
    assert "Mojo Miracle: repairing what can be repaired" in text
    assert ("✔ Repaired: security (state.permissions): only you can open the state folder then (other users of this "
            "computer lose access to it); can be undone. Checked again: the problem is gone.") in text
    assert mode(open_state) == 0o700
    assert ("✘ Not repaired: disk (disk.cache): nothing was deleted: Docker is not available, so no framework images "
            "can be listed") in text, "the list could not be shown: said so, never counted as the person's no"
    assert "✘ Not repaired: ai (tools.audit): needs you" in text and "→ Archive it." in text
    assert "✘ Not repaired: docker (docker.daemon): needs an administrator" in text
    assert "✘ Not repaired: data (data.calibration): not repairable automatically" in text
    assert "Mojo Miracle repaired 1 of 5 problems; 4 were not repaired (why: above)." in text
    assert result.exit_code == 2, "the exit code is the one of the check afterwards"


@POSIX
def test_miracle_applies_only_the_plan_the_person_saw(booted_framework, open_state, monkeypatch):
    """The person said yes to the plan Mojo Oracle printed (0755 -> 0700). When the folder changed before Mojo Miracle
    ran, nothing is changed and the report says so; before, Mojo Miracle made a new plan and applied it unseen."""
    import click
    real = click.confirm

    def confirm(text, *args, **kwargs):
        if "Run Mojo Miracle now?" in text:
            os.chmod(open_state, 0o777)   # something changed the folder while the person read the plan
        return real(text, *args, **kwargs)
    monkeypatch.setattr(click, "confirm", confirm)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n\n")
    text = " ".join(out.split())
    assert f"Plan: the state folder ({open_state}): 0755 → 0700" in text
    assert mode(open_state) == 0o777, "a plan the person never saw was applied"
    assert ("✘ Not repaired: security (state.permissions): nothing was changed: the problem changed since the plan "
            "was shown") in text
    assert "→ Run 'llmcf mojo' again: it shows the new plan." in text


@POSIX
def test_miracle_without_a_terminal_applies_only_harmless_repairs(booted_framework, open_state, monkeypatch):
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle")
    text = " ".join(out.split())
    assert mode(open_state) == 0o700, "a harmless repair runs without a terminal (round 6 Q11)"
    assert "✔ Repaired: security (state.permissions)" in text
    assert ("✘ Not repaired: disk (disk.cache): deleting needs your list: the framework deletes only what you tick "
            "and confirm in its own window; you choose what is deleted: 'llmcf mojo --miracle' in a terminal, or Tools "
            "> Framework Build Images... in the GUI") in text
    assert "Numbers to delete" not in text, "no list without a terminal"
    assert "Mojo Miracle repaired 1 of 5 problems; 4 were not repaired (why: above)." in text


@POSIX
def test_json_never_asks_and_never_deletes(booted_framework, open_state, monkeypatch):
    result, _ = run_cli(booted_framework, monkeypatch, "mojo", "--json", terminal=True, answer="y\n")
    data = json.loads(result.stdout)
    assert data["mode"] == "oracle" and data["exit_code"] == 2 and mode(open_state) == 0o755
    rows = {row["code"]: row for row in data["findings"]}
    assert rows["state.permissions"]["miracle"]["can"] == "miracle"
    assert rows["state.permissions"]["miracle"]["plan"][0]["path"] == str(open_state), "the person's JSON: full path"
    assert rows["state.permissions"]["where"] == str(open_state) and rows["state.permissions"]["why"]
    assert {rows[c]["miracle"]["can"] for c in ("disk.cache", "tools.audit", "docker.daemon", "data.calibration")} \
        == {"deletion_list", "person", "admin", "not_automatic"}
    assert "miracle" not in rows["python.version"], "no verdict for what is no problem"
    assert {"exit_code", "findings", "fixed", "still_present", "summary"} <= set(data)
    result, _ = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", "--json", terminal=True, answer="y\n")
    data = json.loads(result.stdout)
    assert [r["code"] for r in data["repaired"]] == ["state.permissions"] and mode(open_state) == 0o700
    # The keys of doctor --fix --json mean the same here; before, `fixed` stayed empty after a repair.
    assert data["fixed"] == ["state.permissions"] and data["still_present"] == []
    reasons = {r["code"]: r for r in data["not_repaired"]}
    assert reasons["disk.cache"]["outcome"] == "not_done" and "your list" in reasons["disk.cache"]["reason"]
    assert set(reasons) == {"disk.cache", "tools.audit", "docker.daemon", "data.calibration"}


def test_doctor_keeps_working_and_names_the_new_command(booted_framework, monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = booted_framework
            return self

    result = CliRunner().invoke(cli_module.cli, ["doctor", "--json"], obj=Ctx())
    data = json.loads(result.stdout)
    assert result.exit_code == data["exit_code"] and cli_module.DOCTOR_NOTE in result.stderr
    assert "'llmcf mojo' checks (Mojo Oracle)" in cli_module.DOCTOR_NOTE
    assert cli_module.DOCTOR_NOTE not in result.stdout, "the note never breaks the JSON"
    mojo = CliRunner().invoke(cli_module.cli, ["mojo", "--json"], obj=Ctx())
    assert json.loads(mojo.stdout)["exit_code"] == data["exit_code"]
    assert [f["code"] for f in json.loads(mojo.stdout)["findings"]] == [f["code"] for f in data["findings"]]


@POSIX
def test_doctor_fix_uses_the_audited_harmless_repair(booted_framework, open_state, monkeypatch):
    from orchestrator.Core import doctor
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", runner.DEFAULT_CHECKS)
    records = []
    audit = booted_framework.tool_registry.audit
    real = audit.record

    def record(*args, **kwargs):
        records.append((args, kwargs))
        return real(*args, **kwargs)
    monkeypatch.setattr(audit, "record", record)
    result, out = run_cli(booted_framework, monkeypatch, "doctor", "--fix")
    assert "Fixed: state.permissions" in out and mode(open_state) == 0o700
    mojo = [(a, k) for a, k in records if a[1] == "mojo"]
    assert [a[4] for a, _ in mojo] == ["started", "ok"]
    assert mojo[0][1]["extra"]["repair_id"] == "state.tighten" and mojo[0][1]["extra"]["repair_class"] == "delegable"
    assert "Tier 2" not in out and "Numbers to delete" not in out


@POSIX
def test_doctor_fix_says_why_a_repair_was_not_made(booted_framework, open_state, monkeypatch):
    """`doctor --fix` goes through Mojo Miracle, which changes nothing without the audit log. 3.0.0 changed the mode
    anyway; now the alias says why it did not, instead of a bare "still present"."""
    from orchestrator.Core import doctor
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", runner.DEFAULT_CHECKS)
    audit = booted_framework.tool_registry.audit

    def record(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(audit, "record", record)
    result, out = run_cli(booted_framework, monkeypatch, "doctor", "--fix")
    text = " ".join(out.split())
    assert mode(open_state) == 0o755
    assert ("Still present after the fix: state.permissions (the audit log cannot record the repair, so nothing was "
            "changed)") in text
    result, _ = run_cli(booted_framework, monkeypatch, "doctor", "--fix", "--json")
    assert json.loads(result.stdout)["still_present"] == ["state.permissions"], "the 3.0.0 JSON keys stay"


def test_the_ai_is_told_a_command_that_always_lists_the_framework_images(booted_framework, monkeypatch):
    """What an AI tells the person about deleting Docker images names 'llm-cli doctor --fix --tier 2', which always
    shows the list. 'llmcf mojo --miracle' opens it only for a disk problem; naming it there sent the person to a
    command that shows no list when the disk is fine."""
    from orchestrator import cli as cli_module
    from orchestrator.Core import doctor
    from orchestrator.tools.framework_tools import NO_TOOL_ACTIONS
    opened = []
    monkeypatch.setattr(cli_module, "_doctor_free_space", lambda *a, **k: opened.append(a) or (0, ""))
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state])
    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", [check_user_state])
    run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert opened == [], "no disk problem: Mojo Miracle opens no list"
    run_cli(booted_framework, monkeypatch, "doctor", "--fix", "--tier", "2", terminal=True)
    assert len(opened) == 1, "doctor --fix --tier 2 always opens it"
    text = dict(NO_TOOL_ACTIONS)["empty_trash"]
    assert "'llm-cli doctor --fix --tier 2' in a terminal" in text and "mojo --miracle" not in text


@POSIX
def test_miracle_at_a_terminal_opens_the_persons_image_list(booted_framework, monkeypatch, tmp_path):
    """A disk finding at a terminal: Mojo Miracle shows WP7's list of the framework's build images (nothing ticked)
    and deletes exactly what the person ticked and confirmed; No is the default."""
    from test_doctor_fixes import FakeClient, deletions, images
    client = FakeClient(images())
    real = booted_framework.get_component
    monkeypatch.setattr(booted_framework, "get_component",
                        lambda name: client if name == "docker_client" else real(name))
    from orchestrator.Core.mojo.checks import check_disk
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_disk])
    import shutil
    from types import SimpleNamespace
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=1 * 1024 ** 3))
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True, answer="1\n\n")
    assert "Free disk space: the framework's build images" in out and "Numbers to delete" in out
    assert "Nothing was deleted." in out and deletions(client) == [], "the default answer is no"
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True, answer="1\ny\n")
    assert client.images.removed == ["sha256:" + "a" * 64], "exactly the ticked image"
    assert "still finds the problem" in out, "honest: the disk is still tight after one image"


def test_the_mojo_help_says_what_each_mode_does(booted_framework, monkeypatch):
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--help")
    text = " ".join(out.split())
    assert "Only look and report what was found (the default)" in text
    assert "Repair what can be repaired, then report what was repaired, what was not and why" in text
    assert "never asks and never deletes" in text


@POSIX
def test_information_hints_and_problems_found_afterwards_are_shown(booted_framework, open_state, monkeypatch):
    """Nothing is skipped: the hint of an information is printed (as the doctor printed it), and a problem the check
    after Mojo Miracle finds for the first time is listed with the count."""
    calls = {"n": 0}

    def later(_fw):
        calls["n"] += 1
        found = [Finding("knowledge", "knowledge.note", Severity.INFO, "A note.", "Do this when you like.")]
        if calls["n"] > 1:
            found.append(Finding("disk", "disk.output", Severity.WARN, "Only 9.0 GB free for the output folder.",
                                 "Free disk space.", where=str(open_state / "output"), why="a full disk stops a build",
                                 cannot="deletion_list"))
        return found
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state, later])
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle")
    text = " ".join(out.split())
    assert "ℹ knowledge: A note. → Do this when you like." in text
    assert "New after Mojo Miracle: disk (disk.output): Only 9.0 GB free for the output folder." in text
    assert "Mojo Miracle repaired 1 of 1 problem; the check afterwards found 1 new (above)." in text
    assert result.exit_code == 1
    result, _ = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", "--json")
    assert json.loads(result.stdout)["new_problems"] == [], "nothing new on a second run: it was there before"

