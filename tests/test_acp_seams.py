"""The seams between the two directions of the Agent Client Protocol: Ditto as an ACP agent (orchestrator/acp_agent.py,
Core/acp_editor_servers.py) and the ACP client that runs agents the person picks (Core/acp_hosts.py,
Core/acp_client.py).

- one helper set: both sides clean outside text, recognise secret names and redact secret arguments with the same
  helpers (Core/acp_common.py); the old names delegate;
- one launch command: the MCP stdio entry the client hands an agent is the framework's one launch command
  (utils/stdio_programs.launch_command), the same as every MCP client entry;
- an editor MCP server the person enabled is asked again when its command or the names of its variables change;
- the first start of a freshly added npx/uvx agent may take longer (its package may be downloaded), later starts not;
- a closed agent leaves no zombie behind (the GUI's sign-in closing off the GUI thread: tests/test_acp_gui.py).

Qt-free: they run headless too."""

import os
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

from orchestrator.Core import acp_common
from orchestrator.Core.acp_editor_servers import EditorServers, describe_entry


# --- one helper set --------------------------------------------------------------------------------------------------

def test_both_directions_use_one_secret_name_rule():
    from orchestrator.Core import acp_client, acp_editor_servers
    assert acp_client.looks_secret("--pin") == acp_common.names_a_secret("--pin")
    assert acp_client.SECRET_WORDS is acp_common.SECRET_WORDS
    assert acp_client.SHORT_SECRET_WORDS is acp_common.SHORT_SECRET_WORDS
    assert acp_editor_servers.REDACTED == acp_common.REDACTED
    # what the client declines as a secret form field, the agent side redacts as a secret argument
    for flag in ("--pin", "--otp", "--passphrase", "--api-key", "--session"):
        assert acp_client.looks_secret(flag), flag
        assert describe_entry({"name": "x", "command": "srv", "args": [flag, "s3cr3t-value"]})["args"] == \
            [flag, "REDACTED"], flag


def test_secret_names_inside_words_and_whole_words():
    names = acp_common.names_a_secret
    for secret in ("accessToken", "api_key", "API-KEY", "Your Password", "db.passwd", "PIN", "one-time OTP",
                   "2FA code", "--pass", "PWD", "Authorization", "auth", "session cookie", "private key",
                   "passWord"):
        assert names(secret), secret
    for harmless in ("Board mapping", "Shipping", "Spinner speed", "Footprint", "Opinion", "author", "board",
                     "Board name", "--port", "--root", "bypass", "compass"):
        assert not names(harmless), harmless


def test_redacted_arguments_keep_what_is_no_secret():
    args = ["-y", "pkg", "--token", "abc", "--port", "8080", "--password=hunter2", "GITHUB_TOKEN=xyz", "--verbose",
            "--api-key", "--root", "/srv"]
    assert acp_common.redact_secret_arguments(args) == [
        "-y", "pkg", "--token", "REDACTED", "--port", "8080", "--password=REDACTED", "GITHUB_TOKEN=REDACTED",
        "--verbose", "--api-key", "--root", "/srv"]


def test_an_argument_name_with_a_short_secret_word_inside_stays_redacted():
    """Before the two rules became one, the editor-server record redacted the value after any flag or variable whose
    name held pass, pwd, auth or session anywhere (--dbpass, MYSQLPWD, --basicauth, --sessionid). The one rule counts
    those short words only as words of their own for a form field; for a command-line value that would keep a
    password in the record (and show it in the settings), so arguments keep the old, wider rule as well."""
    for flag in ("--dbpass", "--basicauth", "--proxyauth", "--sessionid", "--sshpass", "--dbpwd", "--adminpass"):
        assert acp_common.redact_secret_arguments([flag, "hunter2"]) == [flag, "REDACTED"], flag
        assert describe_entry({"name": "x", "command": "srv", "args": [flag, "hunter2"]})["args"] == \
            [flag, "REDACTED"], flag
    for assigned in ("DBPASS=hunter2", "MYSQLPWD=hunter2", "--httpauth=user:hunter2", "SESSIONKEY=hunter2"):
        name = assigned.split("=", 1)[0]
        assert acp_common.redact_secret_arguments([assigned]) == [f"{name}=REDACTED"], assigned
    # the whole-word rule of a form field is unchanged: "bypass" is no secret field
    assert not acp_common.names_a_secret("bypass") and not acp_common.names_a_secret("author")
    assert acp_common.redact_secret_arguments(["--port", "8080", "--root", "/srv"]) == ["--port", "8080", "--root",
                                                                                         "/srv"]


def test_one_line_and_label_clean_outside_text():
    key = "ghp_" + "A1b2" * 9          # built at runtime: the repository holds no matching literal
    cleaned = acp_common.one_line(f"two\nlines\tand\x07 a key {key}", 200)
    assert cleaned.startswith("two lines and a key ") and key not in cleaned and "REDACTED" in cleaned
    assert "\n" not in acp_common.one_line("a\nb") and acp_common.one_line(None) == ""
    assert acp_common.label("Zed <script>", 64) == "Zed ?script?"
    assert acp_common.label("Zed\n<1.0>", 64) == "Zed ?1.0?"
    assert len(acp_common.label("x" * 500, 40)) == 40


def test_the_old_helper_names_delegate():
    from orchestrator import acp_agent
    from orchestrator.Core import acp_editor_servers, acp_hosts
    assert acp_agent._label_text("Zed\n<1.0>", 64) == acp_common.label("Zed\n<1.0>", 64)
    assert acp_editor_servers._clean("a\x00b\nc", 80) == acp_common.one_line("a\x00b\nc", 80)
    assert acp_hosts._text("  Fake\tAgent  ", 100) == acp_common.one_line("  Fake\tAgent  ", 100)


# --- one launch command ----------------------------------------------------------------------------------------------

def test_the_agents_mcp_entry_is_the_one_launch_command(tmp_path):
    from orchestrator import mcp_server
    from orchestrator.Core.acp_hosts import AgentEntry, AgentStore
    from orchestrator.tools import mcp_env
    from orchestrator.utils import stdio_programs
    root = stdio_programs.installation_root()
    one = stdio_programs.launch_command(root)
    assert mcp_env.launch_command(root) == one == mcp_server.launch_command(root)
    assert mcp_env.installation_root() == root == mcp_server.installation_root() == REPO_ROOT.resolve()
    store = AgentStore(tmp_path / "home", config={}, environ={"PATH": ""})
    entry = store.mcp_entry(AgentEntry(id="fake", name="Fake", kind="command"))
    assert entry["command"] == one["command"] and entry["args"] == one["args"]
    assert entry["env"]["PYTHONPATH"] == one["env"]["PYTHONPATH"]
    # the framework home goes along only when the person runs the framework from another one than the default
    assert "LLM_FRAMEWORK_HOME" not in entry["env"]
    other = AgentStore(tmp_path / "home", config={}, environ={"PATH": "", "LLM_FRAMEWORK_HOME": str(tmp_path / "h")})
    assert other.mcp_entry(AgentEntry(id="fake", name="Fake", kind="command"))["env"]["LLM_FRAMEWORK_HOME"] == \
        str(tmp_path / "h")


# --- editor MCP servers: a changed entry is asked again --------------------------------------------------------------

def test_an_enabled_editor_server_is_asked_again_when_its_command_or_variable_names_change(tmp_path):
    servers = EditorServers(tmp_path / "state")
    base = {"name": "gh", "command": "npx", "args": ["-y", "gh-mcp@1.0.0"], "env": [{"name": "GH_TOKEN", "value": "x"}]}
    (first,) = servers.record([base], editor="Zed")
    assert servers.set_enabled(first.id, True).enabled
    (same,) = servers.record([dict(base, env=[{"name": "GH_TOKEN", "value": "another value"}])], editor="Zed")
    assert same.id == first.id and same.enabled          # only a value changed: the person's choice holds
    (more_env,) = servers.record([dict(base, env=[{"name": "GH_TOKEN", "value": "x"},
                                                  {"name": "LD_PRELOAD", "value": "evil.so"}])], editor="Zed")
    assert more_env.id != first.id and not more_env.enabled
    assert more_env.env_names == ["GH_TOKEN", "LD_PRELOAD"]
    (other_command,) = servers.record([dict(base, command="/usr/local/bin/other")], editor="Zed")
    assert other_command.id != first.id and not other_command.enabled
    assert servers.find(first.id).enabled and servers.find(first.id).env_names == ["GH_TOKEN"]


def test_an_entry_without_variables_keeps_its_id():
    from orchestrator.Core.acp_editor_servers import entry_id
    assert entry_id("stdio", "a", "srv", ["--x"], "") == entry_id("stdio", "a", "srv", ["--x"], "", env_names=[])


# --- the first start of an npx/uvx agent -----------------------------------------------------------------------------

def _store(tmp_path, which):
    from orchestrator.Core.acp_hosts import AgentStore

    class Audit:
        def record(self, *a, **k):
            return 1

    return AgentStore(tmp_path / "home", config={"offline_mode": False}, audit=Audit(),
                      environ={"PATH": ""}, which=which)


def test_the_first_start_of_a_fresh_npx_agent_may_take_five_minutes_later_starts_one(tmp_path):
    from orchestrator.Core import acp_client
    from orchestrator.Core.acp_hosts import AgentEntry
    assert acp_client.START_TIMEOUT == 60.0 and acp_client.FIRST_START_TIMEOUT == 300.0
    runner = tmp_path / "npx"
    runner.write_bytes(b"")
    store = _store(tmp_path, lambda name, path=None: str(runner))
    entry = AgentEntry(id="gem", name="Gem", kind="npx", package="@example/gem-cli@1.2.3", source="registry")
    store._save([entry])
    spec = store.launch_spec("Gem", "gui")
    assert spec.first_start
    session = acp_client.AgentSession(spec, store=store, actor="gui")
    assert session.start_timeout == 300.0
    store.mark_started(spec.entry)
    again = store.launch_spec("Gem", "gui")
    assert not again.first_start
    assert acp_client.AgentSession(again, store=store, actor="gui").start_timeout == 60.0
    own = AgentEntry(id="own", name="Own", kind="command", command=sys.executable)
    store._save([store.get("Gem"), own])
    assert not store.launch_spec("Own", "gui").first_start      # an own program downloads nothing


def test_a_started_agent_is_marked_so_its_next_start_is_quick(tmp_path):
    from orchestrator.Core import acp_client
    calls = []

    class Store:
        def record_event(self, *a, **k):
            pass

        def mark_started(self, entry):
            calls.append(entry.id)

    class Spec:
        class entry:
            id, name, kind = "gem", "Gem", "npx"
        argv = (sys.executable, str(REPO_ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"),
                str(tmp_path / "report.json"))
        env = dict(os.environ)
        cwd = REPO_ROOT
        first_start = True
        mcp_server = {}
        channel = ""

    acp = pytest.importorskip("acp")
    Spec.argv = Spec.argv + ("--path", str(Path(acp.__file__).resolve().parent.parent))
    Spec.cwd = tmp_path
    session = acp_client.AgentSession(Spec, store=Store(), actor="cli", log_dir=tmp_path / "logs")
    try:
        session.start()
    finally:
        session.close()
    assert calls == ["gem"]


# --- closing ---------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="zombie processes are a POSIX matter")
def test_a_kill_after_a_stuck_stop_reaps_the_process(tmp_path, monkeypatch):
    """When the orderly stop does not finish in time, close() kills the agent by its own PID and reaps it: no zombie
    stays until the framework exits."""
    from orchestrator.Core import acp_client
    pytest.importorskip("acp")
    from orchestrator.Core.acp_hosts import AgentEntry

    class Spec:
        entry = AgentEntry(id="stuck", name="Stuck", kind="command")
        argv = (sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(120)")
        env = {"PATH": os.environ.get("PATH", "")}
        cwd = tmp_path
        first_start = False
        mcp_server = {}
        channel = ""

    monkeypatch.setattr(acp_client, "STOP_GRACE", 0.2)
    session = acp_client.AgentSession(Spec, log_dir=tmp_path / "logs")

    async def spawn():
        import asyncio
        session.process = await asyncio.create_subprocess_exec(*Spec.argv, stdin=asyncio.subprocess.PIPE,
                                                               env=Spec.env, cwd=str(tmp_path))

    session._run(spawn(), 10)
    pid = session.pid

    async def stuck_stop():
        import asyncio
        await asyncio.sleep(60)

    monkeypatch.setattr(session, "_stop", stuck_stop)
    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 10
    with pytest.raises(ChildProcessError):     # reaped already: nothing left to wait for
        os.waitpid(pid, os.WNOHANG)


def test_the_fake_agent_refuses_a_report_path_that_is_no_regular_file(tmp_path):
    """The fixture replaces its report file on every write; given a device such as os.devnull (as root) it would
    replace the device for the whole machine. It refuses any existing path that is no regular file (here a folder,
    which works the same on every system) before it writes anything."""
    import subprocess
    folder = tmp_path / "not-a-file"
    folder.mkdir()
    done = subprocess.run([sys.executable, str(REPO_ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"),
                           str(folder)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and "no regular file" in done.stderr
    assert folder.is_dir() and not list(tmp_path.glob("not-a-file.*.tmp"))
