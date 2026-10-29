"""The framework's own OpenSSH settings file (DECISIONS round 25 Q4 and Q5; stage C wave 3c-2, chain boards).

- Every OpenSSH call passes <state>/ssh_config with -F (remote/transport.py openssh_options, the one argv builder), so
  OpenSSH reads neither ~/.ssh/config nor /etc/ssh/ssh_config. The file is created on first use, readable by this
  account only, and holds only what the person adds.
- The trust options stay on the command line, where OpenSSH reads them first (the first value obtained wins):
  StrictHostKeyChecking, UserKnownHostsFile, GlobalKnownHostsFile, BatchMode, IdentitiesOnly, the framework key, and
  now CheckHostIP, UpdateHostKeys and VerifyHostKeyDNS off (OpenSSH neither writes the framework's known_hosts nor
  trusts a key from DNS).
- A line that would change trust or which machine is reached, run a command, read another file or reuse the
  connection is refused when it is written, and again before each use (a hand edit): the call is then a local problem.
  ProxyJump and ProxyCommand need the person's knowing yes. Only the person's channels write the file.
- Q5: a '%' in the framework's own paths is written '%%' where OpenSSH expands tokens (UserKnownHostsFile,
  IdentityFile; the key goes as IdentityFile now, since OpenSSH checks -i's file before it expands -i's tokens); -F's
  path is read literally.
- paramiko reads no configuration file.

Addresses are documentation addresses (RFC 5737) and names example names.
"""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
BOARD = "192.0.2.40"


def state_dir(tmp_path, name="state"):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def pairs(options):
    return [options[i + 1] for i, word in enumerate(options) if word == "-o"]


# --- the file and the argv ----------------------------------------------------------------------------------------

@POSIX_ONLY
def test_the_file_is_made_on_first_use_private_and_holds_only_the_header(tmp_path):
    from orchestrator.remote import ssh_config, transport
    state = state_dir(tmp_path)
    path = ssh_config.config_path(state)
    assert not path.exists()
    options = transport.openssh_options(state)
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    assert path.read_text() == ssh_config.HEADER
    assert options[:2] == ["-F", str(path)], "the one argv builder names the framework's own file first"


def test_the_trust_options_stay_on_the_command_line(tmp_path):
    from orchestrator.remote import transport
    from orchestrator.utils import ssh_trust
    state = state_dir(tmp_path)
    options = transport.openssh_options(state)
    values = pairs(options)
    for pinned in ("BatchMode=yes", "StrictHostKeyChecking=yes", f"GlobalKnownHostsFile={transport.NULL_DEVICE}",
                   "CheckHostIP=no", "UpdateHostKeys=no", "VerifyHostKeyDNS=no", "IdentitiesOnly=yes"):
        assert pinned in values, pinned
    assert f"UserKnownHostsFile={transport.openssh_file(ssh_trust.known_hosts_path(state))}" in values
    private, _ = ssh_trust.client_key(state)
    assert transport.key_file_of(options) == str(private) and "-i" not in options


def test_a_percent_in_the_framework_s_folder_is_written_twice_where_openssh_expands_tokens(tmp_path):
    """Q5: OpenSSH reads '%u' in UserKnownHostsFile and IdentityFile (also -i's path) as the user name; '%%' is one
    '%'. -F's path is read as written."""
    from orchestrator.remote import ssh_config, transport
    from orchestrator.utils import ssh_trust
    state = state_dir(tmp_path, "a%u b/.llm-framework")
    options = transport.openssh_options(state)
    known = next(v for v in pairs(options) if v.startswith("UserKnownHostsFile="))
    key = next(v for v in pairs(options) if v.startswith("IdentityFile="))
    assert "a%%u b" in known and "a%%u b" in key and "a%u b" not in known.replace("%%", "")
    assert options[1] == str(ssh_config.config_path(state)) and "%%" not in options[1]
    private, _ = ssh_trust.client_key(state)
    assert transport.key_file_of(options) == str(private), "OpenSSH names the real file in what it prints"
    assert ssh_config.tokens("50%") == "50%%" and ssh_config.tokens("plain") == "plain"


def test_a_windows_path_of_the_file_goes_with_forward_slashes():
    from pathlib import PureWindowsPath

    from orchestrator.remote import ssh_config, transport
    path = PureWindowsPath(r"C:\Users\Jane Doe\.llm-framework\ssh_config")
    assert ssh_config.argument(path) == "C:/Users/Jane Doe/.llm-framework/ssh_config"
    assert transport.openssh_file(PureWindowsPath(r"C:\Users\x%y\known_hosts")) == '"C:/Users/x%%y/known_hosts"'


# --- what the file may hold ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, kind", [
    ("StrictHostKeyChecking no", "trust"),
    ("UserKnownHostsFile /tmp/elsewhere", "trust"),
    ("GlobalKnownHostsFile /etc/ssh/ssh_known_hosts", "trust"),
    ("KnownHostsCommand /bin/true", "trust"),
    ("HostKeyAlias other.example", "trust"),
    ("UpdateHostKeys yes", "trust"),
    ("CheckHostIP yes", "trust"),
    ("VerifyHostKeyDNS yes", "trust"),
    ("HostName 198.51.100.7", "trust"),
    ("LocalCommand touch /tmp/x", "command"),
    ("PermitLocalCommand yes", "command"),
    ("RemoteCommand id", "command"),
    ("PKCS11Provider /usr/lib/x.so", "command"),
    ("Include ~/.ssh/config", "files"),
    ("ControlMaster auto", "reuse"),
    ("ControlPath /tmp/%C", "reuse"),
    ("ForwardAgent yes", "reuse"),
    ("RequestTTY force", "session"),
    ("Host other.example", "syntax"),
    ("Match exec true", "command"),
    ("no-option-here", "syntax"),
])
def test_a_line_that_would_change_trust_or_run_a_command_is_refused_with_its_reason(line, kind):
    from orchestrator.remote import ssh_config
    check = ssh_config.check_lines([line])
    assert [p.kind for p in check.refused] == [kind], check
    assert check.refused[0].reason and not check.ok


@pytest.mark.parametrize("line", ["User admin", "Port 2222", "ConnectionAttempts 3", "Compression yes",
                                  "IdentityFile ~/.ssh/jump_key", "ServerAliveInterval 5", "AddressFamily inet"])
def test_a_harmless_line_passes(line):
    from orchestrator.remote import ssh_config
    check = ssh_config.check_lines([line])
    assert check.ok and not check.knowingly


@pytest.mark.parametrize("line", ["ProxyJump jump.example", "ProxyCommand ssh -W [%h]:%p jump.example"])
def test_a_jump_or_a_command_needs_the_person_s_knowing_yes(line):
    from orchestrator.remote import ssh_config
    check = ssh_config.check_lines([line], jump_path="/srv/llmcf/ssh_config")
    assert check.ok and [k.line for k in check.knowingly] == [line]
    assert "known_hosts" in check.knowingly[0].what


def test_a_proxyjump_cannot_work_from_a_folder_whose_path_a_shell_splits():
    """OpenSSH turns ProxyJump into `ssh -F <path> -W ...` for a shell, unquoted and %-expanded: from a folder with a
    space (a Windows profile such as 'Jane Doe') or a '%' it cannot work, so it is refused with the way out."""
    from orchestrator.remote import ssh_config
    for path in ("/home/Jane Doe/.llm-framework/ssh_config", "/srv/a%b/ssh_config", "C:/Users/O'Neil/ssh_config"):
        check = ssh_config.check_lines(["ProxyJump jump.example"], jump_path=path)
        assert [p.kind for p in check.refused] == ["jump_path"] and "ProxyCommand" in check.refused[0].reason
    assert ssh_config.check_lines(["ProxyCommand ssh -W [%h]:%p jump.example"],
                                  jump_path="/home/Jane Doe/x").ok


def test_the_whole_file_reads_its_host_lines_and_refuses_match_exec():
    from orchestrator.remote import ssh_config
    text = "# mine\nHost 192.0.2.40\n    ProxyJump jump.example\nHost *.example\n    User admin\n"
    assert ssh_config.check_lines(text.splitlines(), jump_path="/srv/x", whole_file=True).ok
    bad = ssh_config.check_lines(["Match host x exec 'true'"], whole_file=True)
    assert [p.kind for p in bad.refused] == ["command"]


MATCH_EXEC = ['Match exec=true', 'Match "exec" true', 'Match host=x exec=true', 'Match exec="touch ran"',
              'Match e"xec" true', 'Match host x !exec=true', "Match\texec\ttrue", 'Match Exec = true']


@pytest.mark.parametrize("line", MATCH_EXEC)
def test_a_match_exec_is_refused_however_openssh_s_words_are_written(line):
    """OpenSSH splits a Match line's words at a blank, a single '=' and a quote (readconf.c strdelim), so `exec=CMD`,
    `"exec" CMD` and `e"xec" CMD` run CMD on this computer as `exec CMD` does: each is refused."""
    from orchestrator.remote import ssh_config
    check = ssh_config.check_lines([line], whole_file=True)
    assert [p.kind for p in check.refused] == ["command"], line


@pytest.mark.skipif(shutil.which("ssh") is None or sys.platform == "win32", reason="needs OpenSSH's ssh -G")
def test_openssh_really_runs_such_a_match_exec(tmp_path):
    """The refusal above is about real behaviour: `ssh -G` with `Match exec="touch ran"` runs touch."""
    config = tmp_path / "config"
    config.write_text('Match exec="touch ran"\n    User someone\n')
    subprocess.run(["ssh", "-G", "-F", str(config), BOARD], cwd=tmp_path, capture_output=True, timeout=60,
                   stdin=subprocess.DEVNULL)
    assert (tmp_path / "ran").exists()


@pytest.mark.skipif(shutil.which("ssh") is None or sys.platform == "win32", reason="needs OpenSSH's ssh -G")
def test_a_real_openssh_never_reads_a_hostile_home_config(tmp_path, monkeypatch):
    """The person's own ~/.ssh/config sets StrictHostKeyChecking no, a KnownHostsCommand, a ProxyCommand and a port:
    with the framework's -F none of it reaches OpenSSH."""
    from orchestrator.remote import transport
    from orchestrator.remote.address import Endpoint
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "config").write_text("Host *\n  StrictHostKeyChecking no\n  KnownHostsCommand /bin/echo bad\n"
                                          "  ProxyCommand /bin/false\n  Port 2222\n")
    monkeypatch.setenv("HOME", str(home))
    options = transport.openssh_options(state_dir(tmp_path))
    seen = _effective(transport.openssh_argv("ssh", Endpoint(BOARD), "pi", ["-G", *options]))
    assert seen["port"] == "22" and seen["stricthostkeychecking"] == "true"
    assert "proxycommand" not in seen and "knownhostscommand" not in seen


def test_a_hand_edit_with_a_refused_line_stops_every_openssh_call_as_a_local_problem(tmp_path):
    from orchestrator.remote import reasons, ssh_config, transport
    state = state_dir(tmp_path)
    ssh_config.ensure(state)
    ssh_config.config_path(state).write_text(ssh_config.HEADER + "Host *\n    StrictHostKeyChecking no\n")
    with pytest.raises(ssh_config.SshConfigRefused) as e:
        transport.openssh_options(state)
    assert isinstance(e.value, OSError) and reasons.reach_code(e.value) == reasons.LOCAL_PROBLEM
    assert reasons.local_detail(e.value).endswith(ssh_config.CONFIG_NAME)
    assert "StrictHostKeyChecking" in str(e.value)


def test_blocks_are_set_replaced_and_removed_and_the_others_stay():
    from orchestrator.remote import ssh_config
    text = ssh_config.with_block("", BOARD, ["ProxyJump jump.example"])
    assert text.startswith(ssh_config.HEADER) and f"Host {BOARD}\n    ProxyJump jump.example\n" in text
    text = ssh_config.with_block(text, "198.51.100.9", ["User admin"])
    text = ssh_config.with_block(text, BOARD, ["ProxyJump other.example", "User pi"])
    assert ssh_config.block_lines(text, BOARD) == ["ProxyJump other.example", "User pi"]
    assert ssh_config.block_lines(text, "198.51.100.9") == ["User admin"]
    text = ssh_config.with_block(text, BOARD, [])
    assert ssh_config.block_lines(text, BOARD) is None
    assert ssh_config.block_lines(text, "198.51.100.9") == ["User admin"]


# --- the person's writer ------------------------------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


def test_the_person_writes_a_block_and_it_is_audited_without_the_host(tmp_path):
    from orchestrator.remote import ssh_config, transport
    from orchestrator.runtime.device import person_steps
    state = state_dir(tmp_path)
    audit = Audit()
    plan = person_steps.plan_ssh_block(state, BOARD, ["ConnectionAttempts 3"])
    assert plan.old == () and plan.new == ("ConnectionAttempts 3",) and not plan.refused and not plan.knowingly
    person_steps.save_ssh_block(state, plan, via="cli", audit=audit, actor="cli")
    assert ssh_config.block_lines(ssh_config.read(state), BOARD) == ["ConnectionAttempts 3"]
    (row,) = audit.rows
    assert row["tool"] == "ssh_config_changed" and BOARD not in repr(row)
    assert row["extra"]["confirm_channel"] == "framework-cli"
    transport.openssh_options(state)   # the file passes its check before the next call


def test_a_jump_is_written_only_with_the_knowing_yes(tmp_path):
    from orchestrator.runtime.device import person_steps
    state = state_dir(tmp_path)
    plan = person_steps.plan_ssh_block(state, BOARD, ["ProxyJump jump.example"])
    assert [k.keyword for k in plan.knowingly] == ["ProxyJump"]
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.save_ssh_block(state, plan, via="gui")
    assert e.value.kind == "ssh_knowingly"
    person_steps.save_ssh_block(state, plan, via="gui", knowingly=True)


def test_nothing_is_written_with_a_refused_line_by_an_other_channel_or_after_a_change_meanwhile(tmp_path):
    from orchestrator.remote import ssh_config
    from orchestrator.runtime.device import person_steps
    state = state_dir(tmp_path)
    refused = person_steps.plan_ssh_block(state, BOARD, ["StrictHostKeyChecking no"])
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.save_ssh_block(state, refused, via="cli")
    assert e.value.kind == "ssh_refused"
    plan = person_steps.plan_ssh_block(state, BOARD, ["User admin"])
    for via in ("mcp", "ditto", "ai", ""):
        with pytest.raises(PermissionError):
            person_steps.save_ssh_block(state, plan, via=via)
    ssh_config.config_path(state).write_text(ssh_config.HEADER + "Host 198.51.100.9\n    User other\n")
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.save_ssh_block(state, plan, via="cli")
    assert e.value.kind == "ssh_stale"
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.plan_ssh_block(state, "-oProxyCommand=x", ["User admin"])
    assert e.value.kind == "ssh_host"


def test_the_import_copies_one_block_of_the_person_s_config_and_follows_nothing(tmp_path):
    from orchestrator.runtime.device import person_steps
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    config = home / ".ssh" / "config"
    text = ("Include ~/.ssh/config.d/*\nHost *\n    ForwardAgent yes\nHost jump.example\n    User admin\n"
            f"Host {BOARD} board.example\n    ProxyJump jump.example\n    User pi\n")
    config.write_text(text)
    assert person_steps.import_ssh_block(BOARD, config) == ["ProxyJump jump.example", "User pi"]
    assert person_steps.import_ssh_block("board.example", config) == ["ProxyJump jump.example", "User pi"]
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.import_ssh_block("198.51.100.99", config)
    assert e.value.kind == "ssh_import_none"
    assert config.read_text() == text, "the person's own file is only read"


# --- no AI writes it, paramiko reads none -------------------------------------------------------------------------

def test_no_module_an_ai_can_reach_names_the_writer_of_the_file():
    from test_deploy_flow import PERSON_ONLY_NAMES, person_only_references
    from test_deploy_tools import AI_REACHABLE
    assert {"save_ssh_block", "plan_ssh_block", "import_ssh_block"} <= PERSON_ONLY_NAMES
    found = []
    for entry in AI_REACHABLE:
        path = REPO_ROOT / entry
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            found += [(file.name, *ref) for ref in person_only_references(file.read_text(encoding="utf-8"))
                      if ref[1].endswith("ssh_block")]
    assert not found, found


def test_paramiko_reads_no_configuration_file(tmp_path, monkeypatch):
    """paramiko reads ~/.ssh/config only through its SSHConfig, which no framework code uses; the login passes no
    agent and no key search, so only the framework key logs in."""
    paramiko = pytest.importorskip("paramiko")
    for path in (REPO_ROOT / "orchestrator").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not names & {"SSHConfig", "SSHConfigDict"}, path
    from orchestrator.remote import transport
    from orchestrator.utils import ssh_trust
    calls = []

    class Client:
        def load_host_keys(self, path):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, host, **kw):
            calls.append(kw)

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "SSHClient", Client)
    monkeypatch.setattr(paramiko, "SSHConfig", None)
    transport.open_client(BOARD, "pi", state_dir(tmp_path))
    (kw,) = calls
    assert kw["look_for_keys"] is False and kw["allow_agent"] is False
    assert kw["key_filename"] == str(ssh_trust.client_key(state_dir(tmp_path))[0])


# --- a real OpenSSH (skipped where it is missing) -----------------------------------------------------------------

def _effective(argv):
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return {k: v for k, _, v in (line.partition(" ") for line in done.stdout.splitlines())}


@pytest.mark.skipif(shutil.which("ssh") is None or sys.platform == "win32", reason="needs OpenSSH's ssh -G")
def test_a_real_openssh_reads_the_framework_s_file_and_the_command_line_wins(tmp_path):
    """`ssh -G` prints what OpenSSH would use. The framework's file is read (a harmless line of it shows), and a
    hand-made file in its place that sets every pinned option the other way changes none of them."""
    from orchestrator.remote import ssh_config, transport
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device import person_steps
    state = state_dir(tmp_path)
    plan = person_steps.plan_ssh_block(state, BOARD, ["ConnectionAttempts 3"])
    person_steps.save_ssh_block(state, plan, via="cli")
    options = transport.openssh_options(state)
    seen = _effective(transport.openssh_argv("ssh", Endpoint(BOARD), "pi", ["-G", *options]))
    assert seen["connectionattempts"] == "3", "OpenSSH read the framework's own file"
    hostile = tmp_path / "hostile_config"
    hostile.write_text("Host *\n  StrictHostKeyChecking no\n  UserKnownHostsFile /tmp/elsewhere\n"
                       "  GlobalKnownHostsFile /tmp/elsewhere2\n  CheckHostIP yes\n  UpdateHostKeys yes\n"
                       "  VerifyHostKeyDNS yes\n  BatchMode no\n  IdentitiesOnly no\n")
    options[1] = str(hostile)
    seen = _effective(transport.openssh_argv("ssh", Endpoint(BOARD), "pi", ["-G", *options]))
    assert seen["stricthostkeychecking"] == "true" and seen["batchmode"] == "yes"
    assert seen["checkhostip"] == "no" and seen["updatehostkeys"] == "false" and seen["verifyhostkeydns"] == "false"
    assert seen["identitiesonly"] == "yes" and seen["globalknownhostsfile"] == transport.NULL_DEVICE
    assert seen["userknownhostsfile"] == str(Path(state) / "known_hosts")
    assert ssh_config.config_path(state).read_text().count("Host ") == 1


# --- the CLI ------------------------------------------------------------------------------------------------------

def _cli(*args, answer=None):
    from test_cli_builds import invoke_plain
    return invoke_plain("deploy", "ssh-config", *args, input=answer)


@pytest.fixture
def terminal(monkeypatch):
    """The CliRunner's standard input as a terminal, so the CLI asks (tests/test_remote_trust.py terminal)."""
    import click.testing
    from rich.console import Console

    from orchestrator import cli as cli_module

    def make(on=True):
        monkeypatch.setattr(cli_module.console, "_console", Console(color_system=None, width=500))
        monkeypatch.setattr(click.testing._NamedTextIOWrapper, "isatty", lambda self: on, raising=False)
    return make


@pytest.fixture
def cli_state(tmp_path, monkeypatch):
    state = tmp_path / "home" / ".llm-framework"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(state))
    return state


def test_the_cli_shows_the_file(cli_state):
    result = _cli("show")
    out = " ".join(result.out.split())
    assert result.code == 0, result.out
    assert "OpenSSH reads neither ~/.ssh/config nor /etc/ssh/ssh_config" in out
    assert "llmcf's own OpenSSH settings" in out


def test_the_cli_sets_a_jump_after_two_answers_and_refuses_a_trust_line(cli_state, terminal):
    from orchestrator.remote import ssh_config
    terminal()
    result = _cli("edit", BOARD, "--line", "StrictHostKeyChecking no")
    assert result.code == 1 and "Refused: line 1 (StrictHostKeyChecking)" in " ".join(result.out.split())
    assert ssh_config.block_lines(ssh_config.read(cli_state), BOARD) is None
    result = _cli("edit", BOARD, "--line", "ProxyCommand ssh -W [%h]:%p jump.example", answer="y\nn\n")
    assert result.code == 0 and "Nothing was changed." in result.out, result.out
    assert "runs this command on this computer" in " ".join(result.out.split())
    result = _cli("edit", BOARD, "--line", "ProxyCommand ssh -W [%h]:%p jump.example", answer="y\ny\n")
    assert result.code == 0, result.out
    assert ssh_config.block_lines(ssh_config.read(cli_state), BOARD) == ["ProxyCommand ssh -W [%h]:%p jump.example"]
    result = _cli("edit", BOARD, answer="y\n")
    assert result.code == 0 and ssh_config.block_lines(ssh_config.read(cli_state), BOARD) is None


def test_the_cli_imports_one_block_from_the_person_s_config(cli_state, terminal, tmp_path):
    from orchestrator.remote import ssh_config
    terminal()
    ssh = Path.home() / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    (ssh / "config").write_text(f"Host {BOARD}\n    User pi\n    ConnectionAttempts 2\n")
    result = _cli("import", BOARD, answer="y\n")
    assert result.code == 0 and "~/.ssh/config holds for" in result.out, result.out
    assert ssh_config.block_lines(ssh_config.read(cli_state), BOARD) == ["User pi", "ConnectionAttempts 2"]
    result = _cli("import", "198.51.100.77")
    assert result.code == 1 and "no Host block" in result.out


# --- the Boards window --------------------------------------------------------------------------------------------

@pytest.fixture
def boards(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from orchestrator.gui.boards_window import BoardsWindow
    state = state_dir(tmp_path)
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state)),
                                tool_registry=SimpleNamespace(audit=Audit()))
    window = BoardsWindow(framework, lang="en")
    yield window
    window.deleteLater()
    qapp.processEvents()


def test_the_window_writes_a_board_s_block_after_its_questions(boards, monkeypatch):
    from test_boards_window import Boxes

    from orchestrator.gui import boards_window
    from orchestrator.remote import ssh_config
    state = Path(boards.framework.config.user_state_dir)
    shown = []

    def fill(dialog):
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        shown.append(dialog.findChild(QPlainTextEdit, "txt_boards_ssh_file").toPlainText())
        dialog.findChild(QLineEdit, "txt_boards_ssh_host").setText(BOARD)
        lines = dialog.findChild(QPlainTextEdit, "txt_boards_ssh_lines")
        lines.setPlainText("ProxyCommand ssh -W [%h]:%p jump.example")
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    boxes = Boxes(monkeypatch, answer="no")
    boards.buttons["btn_boards_ssh"].click()
    assert shown[0] == ssh_config.HEADER, "the whole file is shown"
    (asked,) = boxes.questions()
    assert asked.default_is_no and "runs this command on this computer" in asked.text
    assert ssh_config.block_lines(ssh_config.read(state), BOARD) is None, "No: nothing is written"
    boxes.answer = "yes"
    boards.buttons["btn_boards_ssh"].click()
    assert ssh_config.block_lines(ssh_config.read(state), BOARD) == ["ProxyCommand ssh -W [%h]:%p jump.example"]
    assert [q.default_is_no for q in boxes.questions()[1:]] == [True, True], "the jump's question, then the change's"
    (row,) = boards.framework.tool_registry.audit.rows
    assert row["tool"] == "ssh_config_changed" and row["actor"] == "gui"


def test_the_window_refuses_a_trust_line_with_its_reason_in_german(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from test_boards_window import Boxes

    from orchestrator.gui.boards_window import BoardsWindow
    from orchestrator.remote import ssh_config
    state = state_dir(tmp_path)
    window = BoardsWindow(SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state))), lang="de")
    try:
        boxes = Boxes(monkeypatch, answer="yes")
        assert window.set_ssh_block(BOARD, ["User pi", "UserKnownHostsFile /tmp/other"]) is False
        assert not boxes.questions() and "UserKnownHostsFile /tmp/other: würde ändern" in boxes.asked[-1].text
        assert ssh_config.block_lines(ssh_config.read(state), BOARD) is None
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_window_imports_from_the_person_s_config_without_writing(boards, monkeypatch):
    from PySide6.QtWidgets import QPlainTextEdit
    from test_boards_window import Boxes

    from orchestrator.remote import ssh_config
    ssh = Path.home() / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    (ssh / "config").write_text(f"Host {BOARD}\n    User pi\n")
    boxes = Boxes(monkeypatch)
    field = QPlainTextEdit()
    boards._import_ssh(BOARD, field)
    assert field.toPlainText() == "User pi" and "nothing is written" in boxes.asked[-1].text
    assert ssh_config.block_lines(ssh_config.read(Path(boards.framework.config.user_state_dir)), BOARD) is None
    boards._import_ssh("198.51.100.77", field)
    assert "no Host block for 198.51.100.77" in boxes.asked[-1].text


# --- the edit's local reason (cli-gui's open item) -----------------------------------------------------------------

def test_a_change_held_up_on_this_computer_shows_the_cli_s_plain_reason(boards):
    """A LockTimeout of known_hosts (replace_key) said 'The step failed: <message>' in the window; the CLI's `deploy
    devices edit` prints the local_problem reason and that another part of the framework changes known_hosts."""
    from orchestrator.runtime.device.person_steps import BoardStepError
    from orchestrator.utils.file_lock import LockTimeout
    text = boards._change_text(LockTimeout("known_hosts.lock is held"), BOARD, 22)
    assert text.startswith("Something on this computer stopped the contact")
    assert "known_hosts is being changed by another part of the framework" in text
    assert "The step failed" not in text
    text = boards._change_text(PermissionError(13, "Permission denied", "/srv/state/known_hosts"), BOARD, 22)
    assert "What failed on this computer: Permission denied: known_hosts" in text and "/srv/state" not in text
    assert boards._change_text(BoardStepError("refused", "stale"), BOARD, 22) is None


def test_the_other_steps_keep_their_shared_error_text(boards):
    """Only the key or address change gets the local reason: every other step's error text stays as it was."""
    from orchestrator.runtime.device.person_steps import BoardStepError
    from orchestrator.utils.file_lock import LockTimeout
    assert boards._error_text(LockTimeout("held")) == "The step failed: held"
    assert boards._error_text(OSError("disk")) == "The step failed: disk"
    assert boards._error_text(BoardStepError("x", "stale")) == boards._t("boards.edit.err.stale")
    assert boards._error_text(BoardStepError("x", "unreachable"), address=BOARD).startswith(
        boards._t("boards.err.unreachable", address=BOARD, reason="x")[:20])


def test_the_window_s_intro_says_that_paramiko_connections_do_not_read_the_file():
    """Only the OpenSSH client gets -F; paramiko (a hard dependency, the usual transport) reads no settings file, so
    the Boards window must not tell the person that every SSH connection reads it."""
    from orchestrator.utils.localization import ui_text
    for lang in ("en", "de"):
        text = ui_text("boards.ssh.intro", lang, file="ssh_config")
        assert "paramiko" in text and "OpenSSH" in text, lang
