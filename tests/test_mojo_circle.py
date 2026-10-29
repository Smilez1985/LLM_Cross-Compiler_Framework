"""Mojo closes the circle (DECISIONS round 27): after Mojo Oracle at a terminal, Mojo Miracle ends with the crystal ball
and one line, "[Mojo] Miracle managed. The oracle is pleased." when it repaired everything, "[Mojo] 404 - miracle not
found" when there was nothing to repair. Only at a terminal: never with --json, never without a terminal, never for
an AI tool, not in the GUI. "After Mojo Oracle": the Oracle run's question answered yes, or `llmcf mojo --miracle`
while an Oracle run at a terminal is still open (a private note in the per-user state folder, closed by every Miracle
run at a terminal).
"""

import io
import json
import os
import re
import stat
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Finding, Severity, banner, runner
from orchestrator.Core.mojo.checks import check_user_state
from test_mojo_cli import open_state, run_cli  # noqa: F401 - open_state: a fixture, used by name below

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


def plain(line: str) -> str:
    """What the console shows without colour: an empty line, the drawing, an empty line, the line centred."""
    return "\n" + banner.ART + "\n\n" + " " * ((60 - len(line)) // 2) + line + "\n"


def shown(out: str) -> str:
    """Which line the run showed (the drawing must be there with it), or "" for no logo."""
    has_art = banner.ART in out
    for line in (banner.MANAGED_LINE, banner.NOT_FOUND_LINE):
        if line in out:
            assert has_art, "the line comes only with the drawing"
            return line
    assert not has_art, "the drawing comes only with its line"
    return ""


def mode(path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


@pytest.fixture
def state_only(booted_framework, monkeypatch):
    """The state folder open to other users (one problem Mojo Miracle repairs) and only the state check."""
    state = Path(booted_framework.config.user_state_dir)
    monkeypatch.setattr(type(booted_framework.config), "user_state_dir", property(lambda self: state))
    if os.name == "posix":
        os.chmod(state, 0o755)
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state])
    return state


def nothing_to_check(monkeypatch):
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [lambda _fw: [Finding("python", "python.version", Severity.OK,
                                                                        "Python 3.13")]])


# --- the drawing ------------------------------------------------------------------------------------------------------

def test_the_mask_has_the_shape_of_the_drawing():
    """One mask letter for every character of the drawing and none for a space, so the maintainer can swap the
    drawing in banner.py and this test says where the mask no longer fits."""
    assert len(banner.ART_LINES) == len(banner.MASK_LINES) == 24
    for number, (art, mask) in enumerate(zip(banner.ART_LINES, banner.MASK_LINES), 1):
        assert len(art) == len(mask), f"line {number}: the mask is {len(mask)} wide, the drawing {len(art)}"
        for column, (char, letter) in enumerate(zip(art, mask), 1):
            assert (char == " ") == (letter == " "), f"line {number}, column {column}: {char!r} has mask {letter!r}"
            assert letter == " " or letter in banner.STYLES, f"line {number}, column {column}: no style {letter!r}"
    assert banner.WIDTH == 60, "it fits an 80-column terminal"
    assert all(line == line.rstrip() for line in banner.ART_LINES + banner.MASK_LINES), "no trailing spaces"
    assert set(banner.STYLES) == set("".join(banner.MASK_LINES)) - {" "}, "every style is used"


def test_the_drawing_and_both_lines_are_ascii():
    for text in (banner.ART, banner.MASK, banner.MANAGED_LINE, banner.NOT_FOUND_LINE):
        assert all(ord(char) < 128 for char in text), text
    assert banner.MANAGED_LINE == "[Mojo] Miracle managed. The oracle is pleased."
    assert banner.NOT_FOUND_LINE == "[Mojo] 404 - miracle not found"


def print_circle(monkeypatch, result, **console_options) -> str:
    from rich.console import Console

    from orchestrator import cli as cli_module
    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=120, **console_options))
    cli_module._mojo_print_circle(result)
    return printed.getvalue()


SUCCESS = SimpleNamespace(repaired=["one"], not_repaired=[], new_problems=[])
NOTHING = SimpleNamespace(repaired=[], not_repaired=[], new_problems=[])


@pytest.mark.parametrize("result,line", [(SUCCESS, banner.MANAGED_LINE), (NOTHING, banner.NOT_FOUND_LINE)])
def test_without_colour_the_console_shows_the_drawing_and_the_line_byte_for_byte(monkeypatch, result, line):
    """No markup is read: "[Mojo]", the brackets of the stand and the backslashes stay as they are."""
    out = print_circle(monkeypatch, result, color_system=None)
    assert out == plain(line)
    assert "\x1b" not in out
    assert out.splitlines()[-1].index("[Mojo]") == (60 - len(line)) // 2, "centred under the drawing"


@pytest.mark.parametrize("result,line", [(SUCCESS, banner.MANAGED_LINE), (NOTHING, banner.NOT_FOUND_LINE)])
def test_in_a_pipe_the_console_finds_no_colour_by_itself(monkeypatch, result, line):
    """`llmcf mojo --miracle | tee log` at a terminal: the CLI's console as it is made (rich detects the colours
    itself) writes into something that is no terminal, so the drawing and the line come byte for byte, no escape code."""
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)
    out = print_circle(monkeypatch, result)
    assert out == plain(line) and "\x1b" not in out


def test_no_color_shows_plain_text_also_at_a_terminal(monkeypatch):
    """NO_COLOR (rich's no_color): rich would drop the colours and keep the bold; the drawing is plain text instead."""
    out = print_circle(monkeypatch, NOTHING, force_terminal=True, color_system="truecolor", no_color=True,
                       legacy_windows=False)
    assert out == plain(banner.NOT_FOUND_LINE)
    monkeypatch.setenv("NO_COLOR", "1")
    out = print_circle(monkeypatch, NOTHING, force_terminal=True, color_system="truecolor", legacy_windows=False)
    assert out == plain(banner.NOT_FOUND_LINE), "the console reads NO_COLOR from the environment itself"


def test_with_colour_forced_on_every_character_has_its_style(monkeypatch):
    from rich.color import ColorSystem
    from rich.style import Style
    from rich.text import Text
    out = print_circle(monkeypatch, SUCCESS, force_terminal=True, color_system="truecolor", no_color=False,
                       legacy_windows=False)
    for style in (*banner.STYLES.values(), banner.LINE_STYLE):   # rich's own codes for each style, none by hand
        code = Style.parse(style).render("X", color_system=ColorSystem.TRUECOLOR).split("X")[0]
        assert code and code in out, style
    decoded = Text.from_ansi(out)
    text = decoded.plain.rstrip("\n")
    assert text + "\n" == plain(banner.MANAGED_LINE), "the colours change no character"
    from rich.console import Console
    console = Console()
    lines = text.split("\n")
    offset = len(lines[0]) + 1   # the empty line before the drawing
    for number, (art, mask) in enumerate(zip(banner.ART_LINES, banner.MASK_LINES)):
        for column, letter in enumerate(mask):
            if letter == " ":
                continue
            got, want = decoded.get_style_at_offset(console, offset + column), Style.parse(banner.STYLES[letter])
            assert (got.color.number, bool(got.bold)) == (want.color.number, bool(want.bold)), (number, column)
        offset += len(art) + 1
    last = lines[-1]
    got = decoded.get_style_at_offset(console, len(text) - len(last) + last.index("[Mojo]"))
    assert (got.color.number, bool(got.bold)) == (Style.parse("bright_green").color.number, True)


def test_which_line_a_miracle_earns():
    entry = SimpleNamespace()
    assert banner.circle_line(SimpleNamespace(repaired=[entry], not_repaired=[], new_problems=[])) == \
        banner.MANAGED_LINE
    assert banner.circle_line(SimpleNamespace(repaired=[], not_repaired=[], new_problems=[])) == \
        banner.NOT_FOUND_LINE, "no problem, or every one already gone"
    assert banner.circle_line(SimpleNamespace(repaired=[entry], not_repaired=[entry], new_problems=[])) is None
    assert banner.circle_line(SimpleNamespace(repaired=[], not_repaired=[entry], new_problems=[])) is None
    assert banner.circle_line(SimpleNamespace(repaired=[entry], not_repaired=[], new_problems=[entry])) is None
    assert banner.circle_line(SimpleNamespace(repaired=[], not_repaired=[], new_problems=[entry])) is None


# --- after Mojo Oracle: yes to "Run Mojo Miracle now?" -----------------------------------------------------------------

@POSIX
def test_the_success_logo_after_oracle_and_yes(booted_framework, state_only, monkeypatch):
    result, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n")
    assert "Run Mojo Miracle now? [y/N]" in out and mode(state_only) == 0o700
    assert shown(out) == banner.MANAGED_LINE
    assert plain(banner.MANAGED_LINE) in out, "the drawing and its line, byte for byte"
    assert out.index("Mojo Miracle repaired 1 of 1 problem.") < out.index(banner.ART), "after Mojo Miracle's report"
    assert result.exit_code == 0
    assert not banner.oracle_open(booted_framework), "the Miracle run closed the Oracle run"


@POSIX
def test_the_404_logo_after_oracle_and_yes_when_the_problem_was_gone(booted_framework, state_only, monkeypatch):
    """Mojo Oracle offers Mojo Miracle only when it can act; the problem went away while the person read the plan."""
    import click
    real = click.confirm

    def confirm(text, *args, **kwargs):
        if "Run Mojo Miracle now?" in text:
            os.chmod(state_only, 0o700)
        return real(text, *args, **kwargs)
    monkeypatch.setattr(click, "confirm", confirm)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n")
    assert "✔ Already gone: security (state.permissions)" in out
    assert shown(out) == banner.NOT_FOUND_LINE


@POSIX
def test_no_is_no_miracle_and_no_logo_and_the_oracle_run_stays_open(booted_framework, state_only, monkeypatch):
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="\n")
    assert "Mojo Miracle did not run; nothing was changed." in out and shown(out) == ""
    assert banner.oracle_open(booted_framework), "a later `llmcf mojo --miracle` closes the circle"


# --- after Mojo Oracle: `llmcf mojo --miracle` while the Oracle run is open -------------------------------------------

@POSIX
def test_the_success_logo_for_miracle_while_an_oracle_run_is_open(booted_framework, state_only, monkeypatch):
    run_cli(booted_framework, monkeypatch, "mojo", "--oracle", terminal=True, answer="n\n")
    assert banner.oracle_open(booted_framework) and mode(state_only) == 0o755
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert shown(out) == banner.MANAGED_LINE and mode(state_only) == 0o700
    assert not banner.oracle_open(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert shown(out) == "", "the circle was closed: a second Miracle run shows no logo"


def test_the_404_logo_for_miracle_after_an_oracle_run_that_found_nothing(booted_framework, monkeypatch):
    nothing_to_check(monkeypatch)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True)
    assert "Run Mojo Miracle now?" not in out and shown(out) == "", "nothing to act on: Mojo Oracle asks nothing"
    assert banner.oracle_open(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert "Mojo Oracle found no problems, so Mojo Miracle had nothing to repair." in out
    assert shown(out) == banner.NOT_FOUND_LINE and plain(banner.NOT_FOUND_LINE) in out
    assert not banner.oracle_open(booted_framework)


def test_a_bare_miracle_shows_no_logo(booted_framework, monkeypatch):
    """Without an open Oracle run, `--miracle`'s own check is part of Mojo Miracle: no circle to close."""
    nothing_to_check(monkeypatch)
    assert not banner.oracle_open(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert "had nothing to repair" in out and shown(out) == ""


# --- never: --json, a problem left, a new problem, no terminal, an AI tool ---------------------------------------------

@POSIX
def test_never_with_json(booted_framework, state_only, monkeypatch):
    banner.record_oracle(booted_framework)
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", "--json", terminal=True)
    assert json.loads(result.stdout)["fixed"] == ["state.permissions"], "the JSON stays the JSON"
    assert shown(out) == ""
    banner.close_oracle(booted_framework)
    run_cli(booted_framework, monkeypatch, "mojo", "--json", terminal=True)
    assert not banner.oracle_open(booted_framework), "an Oracle run for a script opens no circle"


@POSIX
@pytest.mark.usefixtures("open_state")
def test_no_logo_when_a_problem_was_left(booted_framework, monkeypatch):
    """open_state: one repair and four problems Mojo Miracle does not repair."""
    _, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n\n")
    assert "Mojo Miracle repaired 1 of 5 problems; 4 were not repaired" in out and shown(out) == ""
    assert not banner.oracle_open(booted_framework), "closed whatever the outcome"


@POSIX
def test_no_logo_when_the_check_afterwards_found_a_new_problem(booted_framework, state_only, monkeypatch):
    calls = {"n": 0}

    def later(_fw):
        calls["n"] += 1
        return [Finding("disk", "disk.output", Severity.WARN, "Only 9.0 GB free.", "Free disk space.",
                        cannot="deletion_list")] if calls["n"] > 1 else []
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state, later])
    banner.record_oracle(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert "the check afterwards found 1 new" in out and shown(out) == ""
    assert not banner.oracle_open(booted_framework)


@POSIX
def test_never_without_a_terminal(booted_framework, state_only, monkeypatch):
    run_cli(booted_framework, monkeypatch, "mojo")
    assert not banner.oracle_open(booted_framework), "an Oracle run without a terminal opens no circle"
    banner.record_oracle(booted_framework)
    _, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle")
    assert "✔ Repaired: security (state.permissions)" in out and shown(out) == ""
    assert banner.oracle_open(booted_framework), "only a Miracle run at a terminal closes the Oracle run"


@POSIX
def test_never_through_the_ai_tools(booted_framework, state_only, monkeypatch, capsys):
    """run_mojo_oracle and run_mojo_miracle neither open nor close the circle and print nothing; nothing AI-facing
    changes."""
    from orchestrator.tools.registry import ToolContext
    registry = booted_framework.tool_registry
    registry.call("run_mojo_oracle", {}, ToolContext("mcp"))
    assert not banner.oracle_open(booted_framework)
    banner.record_oracle(booted_framework)
    out = registry.call("run_mojo_miracle", {"code": "state.permissions"}, ToolContext("mcp", confirm=lambda s, i: True))
    assert out.outcome == "repaired" and banner.oracle_open(booted_framework)
    captured = capsys.readouterr()
    assert shown(captured.out + captured.err) == ""
    assert "[Mojo]" not in json.dumps(out.model_dump())


def test_only_the_cli_shows_it():
    """The drawing is the console's: no AI tool, MCP code or GUI module uses the banner module."""
    root = Path(__file__).resolve().parent.parent / "orchestrator"
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "mojo.banner" in p.read_text(encoding="utf-8") or "from orchestrator.Core.mojo import banner"
                   in p.read_text(encoding="utf-8"))
    assert users == ["cli.py"], users


# --- the open Oracle run ---------------------------------------------------------------------------------------------

def test_every_miracle_run_at_a_terminal_closes_the_oracle_run(booted_framework, monkeypatch):
    nothing_to_check(monkeypatch)
    banner.record_oracle(booted_framework)

    def broken(self, **kwargs):
        raise RuntimeError("the image list broke")
    monkeypatch.setattr(runner.Mojo, "miracle", broken)
    result, _ = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", terminal=True)
    assert isinstance(result.exception, RuntimeError)
    assert not banner.oracle_open(booted_framework), "closed whatever the outcome, also when Miracle failed"


def test_the_record_is_private_and_belongs_to_this_llm_framework_home(booted_framework, monkeypatch, tmp_path):
    from orchestrator.Core.mojo.repairs.engine import lock_path
    banner.record_oracle(booted_framework)
    path = banner.record_path(booted_framework)
    home = Path(os.environ["LLM_FRAMEWORK_HOME"])
    assert path == home / "mojo" / "oracle_open.json" and path.parent == lock_path(booted_framework).parent
    if os.name == "posix":
        assert mode(path) == 0o600 and mode(path.parent) == 0o700
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    # Exactly the two fixed words and the time, so nothing in it can carry a path or an account name. The account
    # name is looked for in the time only: the fixed words contain names an account can have ("oracle", "mojo").
    assert data == {"mojo_oracle": "open", "via": "cli", "at": data["at"]}
    assert re.fullmatch(r"[0-9T:+-]+", data["at"]) and datetime.fromisoformat(data["at"]).utcoffset() == timedelta(0)
    assert str(tmp_path) not in text and "/" not in text and "\\" not in text, "no path"
    import getpass
    for name in {getpass.getuser(), os.environ.get("USERNAME", ""), Path.home().name}:
        assert len(name) < 3 or name not in data["at"], "no account name"
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == [], "no temporary file left"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "other-home"))
    assert not banner.oracle_open(booted_framework), "another LLM_FRAMEWORK_HOME has its own Oracle runs"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    assert banner.oracle_open(booted_framework)
    banner.close_oracle(booted_framework)
    assert not path.exists() and not banner.oracle_open(booted_framework)
    banner.close_oracle(booted_framework)   # closing a closed run is fine


def test_a_damaged_record_opens_no_circle_and_a_failing_one_never_stops_mojo(booted_framework, monkeypatch):
    path = banner.record_path(booted_framework)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert not banner.oracle_open(booted_framework)
    from orchestrator.Core.mojo import oracle_note as module   # the note moved there; banner re-exports it

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")
    monkeypatch.setattr(module, "write_private", refuse)
    monkeypatch.setattr(module, "remove_file", refuse)
    banner.record_oracle(booted_framework)
    banner.close_oracle(booted_framework)
