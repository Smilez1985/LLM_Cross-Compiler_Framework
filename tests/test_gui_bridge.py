"""The per-user bridge from AI apps to the open framework window (W4 of 2026-10-16).

An MCP client's server runs in its own process. While the person has the framework's window open, the GUI tool's calls
of that server go over a private bridge to the GUI process, where the same code that serves Ditto reads and fills the
forms; the server's own registry keeps the person's permissions for that caller, the questions (pending approvals too)
and the audit entry. These tests use the real bridge (an AF_UNIX socket; on Windows a named pipe) with a window made
of plain fields, and check that nobody but the person reaches it: the private folder, the key only this account can
read, the key proved both ways before anything is sent, the peer's account, and JSON instead of pickle.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from multiprocessing.connection import Client, answer_challenge, deliver_challenge
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import POSIX_MODES_ONLY, REPO_ROOT
from orchestrator.tools import gui_bridge, gui_surface
from orchestrator.tools.gui_surface import DITTO, Filler, GuiField, GuiForm, GuiSurface
from orchestrator.tools.registry import ToolContext, ToolError, current_question

FAKE_KEY = "not-a-real-key-0123456789abcdef"
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="AF_UNIX sockets, user ids and file modes are POSIX; on "
                                                       "Windows the bridge is a named pipe (simulated naming below)")


def yes(_spec, _inp):
    return True


def app(name="Claude Code", actor="mcp", confirm=yes, **extra):
    """An MCP client's call as its server process makes it (the client's own name rides along as a label)."""
    return ToolContext(actor, confirm=confirm, session=f"bridge-{actor}", metadata={"client": SimpleNamespace(name=name)},
                       client=f"{name} 2.1 (stdio)", **extra)


class Window:
    """A framework window made of plain fields: the build form with a model, a switch, a secret and a note."""

    def __init__(self):
        self.state = {"model": "", "use_gpu": True, "api_key": FAKE_KEY, "notes": ""}
        self.shown = []
        fields = [
            GuiField("model", "Model", "Modell", "model", lambda: self.state["model"], self._setter("model")),
            GuiField("use_gpu", "Use the GPU for the build", "GPU", "bool", lambda: self.state["use_gpu"],
                     self._setter("use_gpu")),
            GuiField("api_key", "API key", "API-Schlüssel", "secret", lambda: self.state["api_key"],
                     self._setter("api_key")),
            GuiField("notes", "Notes", "Notizen", "text", lambda: self.state["notes"], self._setter("notes")),
        ]
        self.form = GuiForm("builder", "Build form", "Build-Formular", fields)
        self.surface = GuiSurface(lambda fn, timeout: fn())   # this test's thread stands in for the GUI thread
        self.surface.register_form(self.form)
        self.surface.register_window("builder", "Builder tab", "Reiter Builder", lambda: self.shown.append("builder"))

    def _setter(self, key):
        return lambda value: self.state.__setitem__(key, value)


def state_dir(fm) -> Path:
    return Path(fm.config.user_state_dir)


@pytest.fixture
def window(booted_framework):
    """The person's open window, reachable over the real bridge; this process itself has no window attached, so the
    GUI tool goes over the bridge like an MCP client's server does."""
    from orchestrator.tools.tools_gui import bridge_handler
    assert gui_surface.current() is None
    win = Window()
    server = gui_bridge.BridgeServer(state_dir(booted_framework), bridge_handler(booted_framework, lambda: win.surface))
    assert server.start(), server.note
    win.server = server
    yield win
    server.stop()


def call(fm, action, ctx=None, **arguments):
    return fm.tool_registry.call("gui", {"action": action, **arguments}, ctx or app())


def with_token(fm, name="laptop"):
    from orchestrator.tools.policy import ToolPolicy
    registry = fm.tool_registry
    registry.tokens.add(name)
    registry.tokens.set_policy(name, ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return f"mcp:{name}"


# --- the same tool over the bridge ------------------------------------------------------------------------------

def test_an_ai_app_reads_and_fills_the_window_over_the_bridge(booted_framework, window):
    fm = booted_framework
    form = call(fm, "read_form", form="builder").form
    values = {f.id: f.value for f in form.fields}
    assert values["api_key"] == "set" and FAKE_KEY not in form.model_dump_json(), "a secret only as set or not set"
    shown = []
    ctx = app(confirm=lambda spec, inp: shown.append(current_question()) or True)
    out = call(fm, "fill_form", ctx, form="builder", values={"use_gpu": False, "notes": "try Q4_K_M"})
    assert set(out.filled) == {"use_gpu", "notes"} and "your app" in out.marked
    assert window.state["use_gpu"] is False and window.state["notes"] == "try Q4_K_M"
    assert window.form.field("use_gpu").ai_by == Filler("mcp", "Claude Code"), "the mark names the calling app"
    assert len(shown) == 1 and "marked as filled by this AI app" in shown[0], "asked before the fill, as everywhere"
    rows = [e for e in fm.tool_registry.audit.entries(50) if e["tool"] == "gui"]
    assert {r["actor"] for r in rows} == {"mcp"} and "started" in {r["outcome"] for r in rows}
    assert all(FAKE_KEY not in str(r) for r in rows)
    assert window.form.undo_all() == ["use_gpu", "notes"] and window.state["use_gpu"] is True


def test_the_rules_hold_over_the_bridge(booted_framework, window):
    """A value the person chose, a secret and a field the person is editing are never written by an AI app either;
    what can never be filled is refused before anyone is asked."""
    fm = booted_framework
    window.form.person_edited("notes")
    nobody = app(confirm=lambda s, i: pytest.fail("nothing to ask"))
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", nobody, form="builder", values={"notes": "mine?", "api_key": "x"})
    assert exc.value.code == "needs_person" and "person chose" in str(exc.value) and "secret" in str(exc.value)
    assert window.state["api_key"] == FAKE_KEY and window.state["notes"] == ""
    window.form.field("use_gpu").busy = lambda: True
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", nobody, form="builder", values={"use_gpu": False})
    assert "editing" in str(exc.value) and window.state["use_gpu"] is True


def test_a_path_from_an_ai_app_is_never_put_into_the_window(booted_framework, window):
    out = call(booted_framework, "fill_form", form="builder", values={"model": "/etc/ssh/ssh_host_ed25519_key"})
    assert out.filled == [] and "never by a path" in out.refused[0].reason and window.state["model"] == ""


def test_a_named_token_is_named_in_the_mark(booted_framework, window):
    actor = with_token(booted_framework)
    call(booted_framework, "fill_form", app(actor=actor, name="Claude Code"), form="builder", values={"use_gpu": False})
    who = window.form.field("use_gpu").ai_by
    assert who == Filler("mcp:laptop", "Claude Code") and who.token == "laptop"
    assert who.describe("en") == 'the AI app with the MCP token "laptop" (it calls itself "Claude Code")'


def test_a_no_fills_nothing(booted_framework, window):
    with pytest.raises(ToolError) as exc:
        call(booted_framework, "fill_form", app(confirm=lambda s, i: False), form="builder", values={"use_gpu": False})
    assert exc.value.code == "declined" and window.state["use_gpu"] is True


def test_a_headless_client_waits_for_the_persons_approval_as_everywhere(booted_framework, window):
    """A client that cannot ask: the question appears in the framework (pending approval); the fill runs only after the
    person's yes there, with the app's mark."""
    fm = booted_framework
    registry = fm.tool_registry
    ctx = app(confirm=None, connection="headless", pending_approvals=True)
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", ctx, form="builder", values={"use_gpu": False})
    assert exc.value.code == "waiting_for_approval" and window.state["use_gpu"] is True
    (record,) = registry.approvals.store.pending()
    assert record.tool == "gui" and "Claude Code" in record.client
    registry.approvals.store.decide(record.id, True, "gui")
    registry.approvals.poll_once()
    deadline = time.monotonic() + 10
    while fm.job_service.get(record.job_id).state != "done":
        assert time.monotonic() < deadline, fm.job_service.get(record.job_id)
        time.sleep(0.05)
    assert window.state["use_gpu"] is False and window.form.field("use_gpu").ai_by == Filler("mcp", "Claude Code")


def test_show_window_and_the_wizard_over_the_bridge(booted_framework, window):
    out = call(booted_framework, "show_window", window="builder")
    assert out.shown and window.shown == ["builder"]
    assert call(booted_framework, "wizard_state").open is False
    with pytest.raises(ToolError) as exc:   # the window has no wizard to open
        call(booted_framework, "wizard_open")
    assert exc.value.code == "not_supported"


# --- when no window is open ---------------------------------------------------------------------------------------

def test_without_a_window_the_tool_says_gui_not_open(booted_framework):
    fm = booted_framework
    for _ in range(2):
        with pytest.raises(ToolError) as exc:
            call(fm, "read_form")
        assert exc.value.blocker == "gui_not_open" and exc.value.person_action == "Open the framework window."
        win = Window()
        from orchestrator.tools.tools_gui import bridge_handler
        server = gui_bridge.BridgeServer(state_dir(fm), bridge_handler(fm, lambda: win.surface))
        assert server.start()
        assert call(fm, "read_form").open_forms[0].form == "builder"
        server.stop()   # the window closed: told plainly again, and its key file is gone
        assert gui_bridge.read_info(state_dir(fm)) is None


def test_a_window_that_crashed_leaves_nothing_that_answers(booted_framework):
    """A stale key file of a window that is gone (no listener) reads as no window; the next window replaces it."""
    fm = booted_framework
    from orchestrator.tools.tools_gui import bridge_handler
    win = Window()
    server = gui_bridge.BridgeServer(state_dir(fm), bridge_handler(fm, lambda: win.surface))
    assert server.start()
    info = gui_bridge.read_info(state_dir(fm))
    key_file = state_dir(fm) / gui_bridge.BRIDGE_FOLDER / gui_bridge.INFO_FILE
    left_behind = key_file.read_text(encoding="utf-8")
    server.stop()   # the process died: its endpoint is gone, the key file stays behind
    from orchestrator.Core.vault_key import write_private
    write_private(key_file, left_behind)
    assert gui_bridge.read_info(state_dir(fm)) == info and not gui_bridge.gui_open(state_dir(fm))
    with pytest.raises(ToolError) as exc:
        call(fm, "read_form")
    assert exc.value.blocker == "gui_not_open"
    again = gui_bridge.BridgeServer(state_dir(fm), bridge_handler(fm, lambda: win.surface))
    assert again.start(), again.note
    assert gui_bridge.read_info(state_dir(fm)).instance != info.instance
    again.stop()


def test_a_second_window_of_the_same_person_does_not_start_a_second_bridge(booted_framework, window):
    other = gui_bridge.BridgeServer(state_dir(booted_framework), lambda request: {"ok": True})
    assert not other.start() and "another framework window" in other.note
    assert call(booted_framework, "read_form").open_forms, "the first window still serves AI apps"


# --- nobody but this person ------------------------------------------------------------------------------------------

@POSIX_MODES_ONLY
def test_the_bridge_lives_in_a_private_folder_with_a_private_key(booted_framework, window):
    folder = state_dir(booted_framework) / gui_bridge.BRIDGE_FOLDER
    info = gui_bridge.read_info(state_dir(booted_framework))
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700 and os.stat(folder).st_uid == os.geteuid()
    key_file = folder / gui_bridge.INFO_FILE
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    sock = Path(info.address)
    assert stat.S_ISSOCK(os.stat(sock).st_mode) and stat.S_IMODE(os.stat(sock).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sock.parent).st_mode) == 0o700, "the socket's folder is private too"
    os.chmod(key_file, 0o644)   # a key others could read is never used
    assert gui_bridge.read_info(state_dir(booted_framework)) is None
    os.chmod(key_file, 0o600)


def test_a_process_without_the_key_is_refused_and_reaches_nothing(booted_framework, window):
    handled = []
    window.server.handler = lambda request: handled.append(request) or {"ok": True}
    info = gui_bridge.read_info(state_dir(booted_framework))
    guess = gui_bridge.BridgeInfo(info.family, info.address, os.urandom(32), 0, "guess")
    with pytest.raises(gui_bridge.GuiNotOpen):
        gui_bridge._exchange(guess, {"op": "run", "input": {"action": "read_form"}}, 5)
    assert handled == [], "nothing reaches the window before the key was proved"


def test_a_client_never_talks_to_an_endpoint_that_does_not_prove_the_key(booted_framework, tmp_path):
    """A squatter (anyone who put an endpoint where the window's was) cannot prove the key: the client refuses before
    it sends anything."""
    from multiprocessing.connection import Listener
    import threading
    family, address = gui_bridge.new_endpoint(gui_bridge.bridge_folder(tmp_path / "squat"))
    listener = Listener(address, family=family)
    received = []

    def squat():
        conn = listener.accept()
        try:
            deliver_challenge(conn, b"x" * 32)   # its own key, not the window's
        except Exception:  # noqa: BLE001
            pass
        try:
            while conn.poll(1):
                received.append(conn.recv_bytes())
        except (EOFError, OSError):
            pass
        conn.close()
    thread = threading.Thread(target=squat, daemon=True)
    thread.start()
    fake = gui_bridge.BridgeInfo(family, address, os.urandom(32), 0, "squat")
    with pytest.raises(gui_bridge.GuiNotOpen):
        gui_bridge._exchange(fake, {"op": "run", "input": {"action": "fill_form"}}, 5)
    thread.join(5)
    listener.close()
    assert all(b"fill_form" not in r for r in received), "the call never reached the squatter"


@POSIX_ONLY
def test_a_process_of_another_account_is_refused_by_the_window(booted_framework, window):
    """On Linux the window checks the account of every connecting process (SO_PEERCRED) and closes another account's
    connection before the handshake. Here the window pretends to run as another account."""
    if getattr(socket, "SO_PEERCRED", None) is None:
        pytest.skip("this system does not tell a socket's peer account (SO_PEERCRED is Linux)")
    handled = []
    window.server.handler = lambda request: handled.append(request) or {"ok": True}
    assert gui_bridge.call(state_dir(booted_framework), {"op": "run"}, timeout=5) == {"ok": True}
    window.server._owner_uid = os.geteuid() + 4242
    with pytest.raises(gui_bridge.GuiNotOpen):
        gui_bridge.call(state_dir(booted_framework), {"op": "run"}, timeout=5)
    assert len(handled) == 1, "the connection of another account never reached the window"


@POSIX_ONLY
def test_another_person_on_the_same_computer_cannot_reach_the_bridge(booted_framework, window):
    """A real process of another account: it can neither read the key file nor connect to the socket."""
    if os.geteuid() != 0:
        pytest.skip("starting a process as another account needs root (CI runners are no root)")
    python = next((os.path.realpath(p) for p in (sys.executable, getattr(sys, "_base_executable", ""),
                                                 "/usr/bin/python3") if p and _runnable_by_anyone(p)), None)
    if python is None:
        pytest.skip("no Python on this computer that another account may run")
    info = gui_bridge.read_info(state_dir(booted_framework))
    key_file = state_dir(booted_framework) / gui_bridge.BRIDGE_FOLDER / gui_bridge.INFO_FILE
    probe = ("import sys\nfrom multiprocessing.connection import Client\nout = []\n"
             "try:\n    open(sys.argv[1], 'rb').read()\n    out.append('key read')\n"
             "except PermissionError:\n    out.append('key refused')\n"
             "try:\n    Client(sys.argv[2], family='AF_UNIX').close()\n    out.append('connected')\n"
             "except (PermissionError, ConnectionRefusedError):\n    out.append('connect refused')\n"
             "print(' '.join(out))\n")
    done = subprocess.run([python, "-I", "-c", probe, str(key_file), info.address], user=65534, group=65534,
                          capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
    assert done.stdout.split() == ["key", "refused", "connect", "refused"], done.stderr


def _runnable_by_anyone(path: str) -> bool:
    """Whether every account may run this program (the file and every folder above it are open to others)."""
    target = Path(os.path.realpath(path))
    if not target.is_file() or not os.stat(target).st_mode & stat.S_IXOTH:
        return False
    return all(os.stat(folder).st_mode & stat.S_IXOTH for folder in target.parents)


def test_the_bridge_never_unpickles_what_it_receives(booted_framework, window, tmp_path):
    """Messages are JSON: a pickle sent after a proved key is refused as unreadable and never loaded."""
    import pickle
    marker = tmp_path / "unpickled"

    class Boom:
        def __reduce__(self):
            return (Path.touch, (marker,))
    info = gui_bridge.read_info(state_dir(booted_framework))
    conn = Client(info.address, family=info.family)
    try:
        answer_challenge(conn, info.key)
        deliver_challenge(conn, info.key)
        conn.send_bytes(pickle.dumps(Boom()))
        assert not conn.poll(2) or conn.recv_bytes() is not None
    except (EOFError, OSError):
        pass
    finally:
        conn.close()
    time.sleep(0.2)
    assert not marker.exists()


def test_the_key_and_the_address_never_reach_an_ai_or_the_log(booted_framework, window, caplog):
    fm = booted_framework
    info = gui_bridge.read_info(state_dir(fm))
    out = call(fm, "read_form", form="builder").model_dump_json()
    window.server.stop()
    with pytest.raises(ToolError) as exc:
        call(fm, "read_form")
    texts = [out, str(exc.value), caplog.text] + [str(e) for e in fm.tool_registry.audit.entries(50)]
    for text in texts:
        assert info.key.hex() not in text and info.address not in text
        assert str(state_dir(fm)) not in text


def test_a_window_answers_another_framework_process(booted_framework, window):
    """A separate process (as the MCP server is) reaches the window with the key from the private file."""
    code = ("import sys, json\nsys.path.insert(0, sys.argv[1])\nfrom orchestrator.tools import gui_bridge\n"
            "print(json.dumps(gui_bridge.call(sys.argv[2], {'op': 'run', 'input': {'action': 'read_form'}, "
            "'by': {'actor': 'mcp', 'app': 'Other process'}})))\n")
    done = subprocess.run([sys.executable, "-c", code, str(REPO_ROOT), str(state_dir(booted_framework))],
                          capture_output=True, text=True, timeout=120, cwd=tempfile.gettempdir())
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert answer["ok"] and answer["output"]["open_forms"][0]["form"] == "builder", done.stderr


def test_the_window_refuses_a_call_it_does_not_know(booted_framework, window):
    state = state_dir(booted_framework)
    assert gui_bridge.call(state, {"op": "delete_everything"})["ok"] is False
    assert gui_bridge.call(state, {"op": "run", "input": {"action": "read_form"},
                                   "by": {"actor": "gui", "app": ""}})["ok"] is False, "the person is no filler"
    answer = gui_bridge.call(state, {"op": "run", "input": {"action": "fill_form", "form": "builder",
                                                            "values": {"use_gpu": False}, "extra": 1},
                                     "by": {"actor": "mcp", "app": "x"}})
    assert answer["ok"] is False and window.state["use_gpu"] is True


# --- where the bridge listens ---------------------------------------------------------------------------------------

def test_windows_uses_a_named_pipe_with_a_random_name(tmp_path):
    """Simulated Windows naming: a named pipe with a random name for every start (never a file in the per-user
    folder), and a key file that names any other pipe is not used."""
    folder = gui_bridge.bridge_folder(tmp_path / "state")
    first, second = gui_bridge.new_endpoint(folder, windows=True), gui_bridge.new_endpoint(folder, windows=True)
    assert first[0] == second[0] == "AF_PIPE" and first[1] != second[1]
    assert first[1].startswith("\\\\.\\pipe\\llmcf-gui-") and len(first[1]) == len("\\\\.\\pipe\\llmcf-gui-") + 32
    assert str(folder) not in first[1]
    info = gui_bridge.BridgeInfo("AF_PIPE", first[1], os.urandom(32), 1, "abc")
    from orchestrator.Core.vault_key import write_private
    write_private(folder / gui_bridge.INFO_FILE, info.to_json())
    assert gui_bridge.read_info(tmp_path / "state").address == first[1]
    write_private(folder / gui_bridge.INFO_FILE, info.to_json().replace("llmcf-gui-", "someone-else-"))
    assert gui_bridge.read_info(tmp_path / "state") is None


@POSIX_ONLY
def test_a_long_per_user_folder_puts_the_socket_into_a_private_temporary_folder(tmp_path):
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    server = gui_bridge.BridgeServer(deep, lambda request: {"ok": True, "echo": request.get("op")})
    assert server.start(), server.note
    info = gui_bridge.read_info(deep)
    sock = Path(info.address)
    assert len(os.fsencode(info.address)) <= gui_bridge.SOCKET_PATH_MAX and sock.parent.name.startswith("llmcf-gui-")
    assert stat.S_IMODE(os.stat(sock.parent).st_mode) == 0o700
    assert gui_bridge.call(deep, {"op": "x"})["echo"] == "x"
    server.stop()
    assert not sock.parent.exists(), "the temporary folder goes with the window"


@POSIX_ONLY
def test_only_a_stale_socket_is_replaced(tmp_path):
    state = tmp_path / "s"
    folder = gui_bridge.bridge_folder(state)
    planted = folder / gui_bridge.SOCKET_NAME
    planted.write_text("not a socket", encoding="utf-8")
    server = gui_bridge.BridgeServer(state, lambda request: {"ok": True})
    assert not server.start() and planted.read_text(encoding="utf-8") == "not a socket", "never deleted"
    planted.unlink()
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(planted))   # a socket nobody listens on any more (a window that crashed)
    stale.close()
    assert server.start(), server.note
    server.stop()


def test_the_window_starts_and_stops_the_bridge_with_the_gui(booted_framework, qapp):
    """open_gui starts the bridge for AI apps, close_gui stops it (they are told the window is not open)."""
    from orchestrator.gui import form_surface
    from PySide6.QtWidgets import QMainWindow, QTabWidget
    qt = QMainWindow()
    window = SimpleNamespace(framework=booted_framework, tabs=QTabWidget(qt), raise_=lambda: None,
                             builder_tab=None, deployment_window=None)
    surface = form_surface.open_gui(window)
    try:
        assert surface.extra.get("bridge") is not None and gui_bridge.gui_open(state_dir(booted_framework))
    finally:
        form_surface.close_gui(surface)
    assert not gui_bridge.gui_open(state_dir(booted_framework))
    assert gui_bridge.read_info(state_dir(booted_framework)) is None


def test_ditto_in_the_window_never_goes_over_the_bridge(booted_framework, window):
    """Ditto runs in the GUI process: with the window's surface attached its calls go there directly (and are
    marked as Ditto's), never over the bridge."""
    window.server.handler = lambda request: pytest.fail("Ditto's call went over the bridge")
    gui_surface.attach(window.surface)
    try:
        out = call(booted_framework, "fill_form", ToolContext("ditto", confirm=yes), form="builder",
                   values={"use_gpu": False})
    finally:
        gui_surface.detach(window.surface)
    assert out.filled == ["use_gpu"] and window.form.field("use_gpu").ai_by == DITTO


# --- the MCP server, end to end -------------------------------------------------------------------------------------

def test_an_mcp_client_fills_the_window_through_the_real_server(booted_framework, window):
    """The whole path: the MCP SDK, the server's question (elicitation), the registry, the bridge and the window; the
    tool is listed for the client inside the cap, and the window marks the client by the name it gives itself."""
    pytest.importorskip("mcp")
    import asyncio
    from mcp import Client
    from mcp_types import ElicitResult, Implementation
    from orchestrator import mcp_server
    from orchestrator.tools.framework_tools import MAX_LISTED_TOOLS
    asked = []

    async def accept(ctx, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})
    server = mcp_server.build_mcp_server(booted_framework)

    async def go():
        async with Client(server, client_info=Implementation(name="Claude Code", version="2.1"),
                          elicitation_callback=accept) as client:
            tools = await client.list_tools()
            result = await client.call_tool("gui", {"action": "fill_form", "form": "builder",
                                                    "values": {"use_gpu": False}})
            return [t.name for t in tools.tools], result
    names, result = asyncio.run(go())
    assert "gui" in names and len(names) <= MAX_LISTED_TOOLS
    assert not result.is_error, result
    assert result.structured_content["filled"] == ["use_gpu"] and window.state["use_gpu"] is False
    assert len(asked) == 1 and "Claude Code" in asked[0] and "this AI app" in asked[0]
    assert window.form.field("use_gpu").ai_by == Filler("mcp", "Claude Code")


def test_an_mcp_client_is_told_plainly_when_no_window_is_open(booted_framework):
    pytest.importorskip("mcp")
    import asyncio
    from mcp import Client
    from orchestrator import mcp_server
    server = mcp_server.build_mcp_server(booted_framework)

    async def go():
        async with Client(server) as client:
            return await client.call_tool("gui", {"action": "read_form"})
    result = asyncio.run(go())
    assert result.is_error and result.structured_content["error"]["blocker"] == "gui_not_open"
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "framework window" in text
