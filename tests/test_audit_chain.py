"""Audit chain follow-ups: archiving a log, logs from before per-entry key ids, key ids and unkeyed entries
that were changed, removals that stay detected, a lagging anchor, damaged values, and the lock around
the creation of the audit key."""

import functools
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.doctor import Doctor, Severity, check_tool_policy
from orchestrator.tools import audit as audit_module
from orchestrator.tools import framework_tools
from orchestrator.tools.audit import GENESIS, AuditKeyMismatch, AuditLog
from orchestrator.tools.policy import PolicyStore
from orchestrator.tools.registry import ToolRegistry

KEY = b"audit-key"
FIELDS = ("ts", "actor", "tool", "tier", "decision", "approved", "args_sha256", "args_preview", "outcome", "detail")


def filled_log(path: Path, n=3, key=KEY) -> AuditLog:
    log = AuditLog(path, key=key)
    for i in range(n):
        log.record("mcp", "start_build", "local_write", "ask", "ok", arguments={"i": i}, approved=True)
    return log


def run_sql(path: Path, statement: str, params=()):
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute(statement, params)
    finally:
        db.close()


def query(path: Path, statement: str):
    db = sqlite3.connect(path)
    try:
        return db.execute(statement).fetchall()
    finally:
        db.close()


def audit_finding(log: AuditLog, tmp_path: Path):
    registry = ToolRegistry(PolicyStore(tmp_path), log)
    return next(f for f in Doctor(SimpleNamespace(tool_registry=registry), [check_tool_policy]).run().findings
                if f.code == "tools.audit")


def invoke_cli(log: AuditLog, tmp_path: Path, *args):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module

    registry = ToolRegistry(PolicyStore(tmp_path), log)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx())


def flat(text: str) -> str:
    return " ".join(text.split())


class TestArchive:

    def test_archive_keeps_the_old_log_checkable_and_starts_a_new_chain(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")
        assert log.check().problem == "removed"

        target = log.archive()

        assert target is not None and target.parent == tmp_path and target.name.startswith("audit.db.")
        assert log.entries() == [] and not log.anchor_path.exists()  # emptied in place, not renamed
        archived = AuditLog(target, key=KEY).check()  # its anchor moved along, so the evidence is kept
        assert archived.problem == "removed" and archived.entry == 3
        log.record("cli", "list_targets", "read", "allow", "ok")
        result = log.check()
        assert result.intact and result.entries == 1, result.message

    def test_two_archives_in_the_same_second_do_not_overwrite_each_other(self, tmp_path):
        log = filled_log(tmp_path / "audit.db", n=1)
        first = log.archive()
        log.record("cli", "list_targets", "read", "allow", "ok")
        second = log.archive()
        assert first != second and first.exists() and second.exists()
        assert log.archive() is None

    def test_a_damaged_database_is_moved_as_it_is(self, tmp_path):
        db = tmp_path / "audit.db"
        db.write_bytes(b"garbage, not SQLite" * 64)
        target = AuditLog(db, key=KEY).archive()
        assert target.read_bytes() == b"garbage, not SQLite" * 64 and not db.exists()

    def test_cli_archive(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        result = invoke_cli(log, tmp_path, "tools", "audit", "--archive")
        assert result.exit_code == 0, result.output
        assert "Audit log moved to" in flat(result.output) and log.entries() == []
        assert len(list(tmp_path.glob("audit.db.*.anchor"))) == 1
        result = invoke_cli(log, tmp_path, "tools", "audit", "--archive")
        assert result.exit_code == 0 and "No audit log to archive" in result.output

    @pytest.mark.parametrize("pause", ["connected", "anchor-not-moved-yet"])
    def test_an_archive_while_another_process_writes_leaves_no_false_alarm(self, tmp_path, pause):
        """The GUI or an MCP server writes while 'tools audit --archive' runs. Its entry belongs to the old or
        the new log, and neither of them may report removed entries or a missing anchor afterwards."""
        log = filled_log(tmp_path / "audit.db")
        writer = AuditLog(log.path, key=KEY)  # another process
        reached, go = threading.Event(), threading.Event()
        real_connect, real_move = writer._connect, writer._move_anchor

        def connect_then_wait():
            db = real_connect()
            reached.set()
            go.wait(20)
            return db

        def wait_then_move(*args):
            reached.set()
            go.wait(20)
            return real_move(*args)

        if pause == "connected":
            writer._connect = connect_then_wait
        else:
            writer._move_anchor = wait_then_move
        thread = threading.Thread(target=writer.record, args=("mcp", "start_build", "local_write", "ask", "ok"))
        thread.start()
        assert reached.wait(20)

        target = log.archive()
        log.record("cli", "list_targets", "read", "allow", "ok")  # the new log's first entry
        go.set()
        thread.join(20)
        log.record("cli", "list_targets", "read", "allow", "ok")

        archived, live = AuditLog(target, key=KEY).check(), log.check()
        assert archived.intact, archived.message
        assert live.intact, live.message
        assert archived.entries + live.entries == 6  # every entry is in one of the two logs

    @pytest.mark.parametrize("failure", ["anchor-in-use", "commit-fails"])
    def test_a_failed_archive_changes_nothing(self, tmp_path, monkeypatch, failure):
        log = filled_log(tmp_path / "audit.db")
        real_replace, real_connect = os.replace, sqlite3.connect

        def sharing_violation(src, dst):
            if str(dst).endswith(".anchor"):
                raise PermissionError(13, "The process cannot access the file because it is being used by "
                                          "another process")
            return real_replace(src, dst)

        class CommitFails:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("disk I/O error")
                return self._con.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._con, name)

        def connect(*args, **kwargs):  # only the log's own connection, not the one the copy is read with
            con = real_connect(*args, **kwargs)
            return CommitFails(con) if kwargs.get("isolation_level", "") is None else con

        with monkeypatch.context() as m:
            if failure == "anchor-in-use":
                m.setattr(audit_module.os, "replace", sharing_violation)
            else:
                m.setattr(sqlite3, "connect", connect)
            with pytest.raises((OSError, sqlite3.Error)):
                log.archive()

        assert [p.name for p in tmp_path.glob("audit.db.*") if p != log.anchor_path] == []  # no half copy
        assert log.anchor_path.exists()
        result = log.check()
        assert result.intact and result.entries == 3, result.message

    @pytest.mark.parametrize("reader_key, problem", [(b"old-key", "removed"), (b"new-key", "key_mismatch")])
    def test_doctor_points_to_the_archive_command(self, tmp_path, reader_key, problem):
        log = filled_log(tmp_path / "audit.db", key=b"old-key")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")
        reader = AuditLog(log.path, key=reader_key)
        assert reader.check().problem == problem
        finding = audit_finding(reader, tmp_path)
        assert finding.severity == Severity.ERROR and "tools audit --archive" in finding.fix_hint


def legacy_log(path: Path, key, n=2) -> None:
    """A database as 3.0.0 development builds wrote it: no key_id column, key id only in meta."""
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
                  tool TEXT NOT NULL, tier TEXT NOT NULL, decision TEXT NOT NULL, approved INTEGER,
                  args_sha256 TEXT NOT NULL, args_preview TEXT NOT NULL, outcome TEXT NOT NULL,
                  detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
    db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    key_id = hmac.new(key, b"llm-framework-audit-key-id", hashlib.sha256).hexdigest()[:16] if key else "unkeyed"
    db.execute("INSERT INTO meta (k, v) VALUES ('key_id', ?)", (key_id,))
    prev = GENESIS
    for i in range(n):
        row = {"ts": f"2026-10-0{i + 1}T00:00:00.000+00:00", "actor": "gui", "tool": "start_build",
               "tier": "local_write", "decision": "ask", "approved": 1,
               "args_sha256": hashlib.sha256(b"").hexdigest(), "args_preview": "", "outcome": "ok", "detail": ""}
        payload = (prev + json.dumps([row[f] for f in FIELDS], ensure_ascii=False)).encode("utf-8")
        digest = hmac.new(key, payload, hashlib.sha256).hexdigest() if key else hashlib.sha256(payload).hexdigest()
        db.execute("INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, "
                   "outcome, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [row[f] for f in FIELDS] + [prev, digest])
        prev = digest
    db.commit()
    db.close()
    mac_payload = f"anchor:{n}:{prev}".encode()
    mac = hmac.new(key, mac_payload, hashlib.sha256).hexdigest() if key else hashlib.sha256(mac_payload).hexdigest()
    path.with_name(path.name + ".anchor").write_text(json.dumps({"id": n, "hash": prev, "mac": mac}))


class TestLogsFromBeforePerEntryKeyIds:

    @pytest.mark.parametrize("key", [KEY, None], ids=["keyed", "unkeyed"])
    def test_an_old_log_is_migrated_and_stays_verifiable(self, tmp_path, key):
        legacy_log(tmp_path / "audit.db", key)
        log = AuditLog(tmp_path / "audit.db", key=key)
        assert log.check().intact

        log.record("cli", "list_targets", "read", "allow", "ok")

        result = log.check()
        assert result.intact and result.entries == 3, result.message
        assert [r[0] for r in query(log.path, "SELECT key_id FROM events ORDER BY id")][:2] == [None, None]

    def test_an_old_unkeyed_log_continues_with_a_key(self, tmp_path):
        legacy_log(tmp_path / "audit.db", None)
        keyed = AuditLog(tmp_path / "audit.db", key=KEY)
        keyed.record("gui", "start_build", "local_write", "ask", "ok", approved=True)
        result = keyed.check()
        assert result.intact and result.keyed and result.entries == 3, result.message

    def test_a_changed_old_entry_is_still_modified(self, tmp_path):
        legacy_log(tmp_path / "audit.db", KEY)
        run_sql(tmp_path / "audit.db", "UPDATE events SET approved = 0 WHERE id = 1")
        result = AuditLog(tmp_path / "audit.db", key=KEY).check()
        assert result.problem == "modified" and result.entry == 1

    def test_an_entry_from_a_process_still_running_the_old_version_is_accepted(self, tmp_path):
        """After an update the GUI or an MCP server runs the previous version until it is restarted. Its entries
        have no key id, but they verify with this key."""
        log = filled_log(tmp_path / "audit.db", n=2)
        append_old_format(log.path, KEY)
        result = log.check()
        assert result.intact and result.entries == 3, result.message
        log.record("cli", "list_targets", "read", "allow", "ok")
        result = log.check()
        assert result.intact and result.entries == 4, result.message

    def test_an_unkeyed_old_format_entry_after_keyed_ones_is_still_modified(self, tmp_path):
        log = filled_log(tmp_path / "audit.db", n=2)
        append_old_format(log.path, None)
        result = log.check()
        assert result.problem == "modified" and result.entry == 3


def append_old_format(path: Path, key) -> None:
    """One entry as the previous version's record() wrote it: no key id, anchor written with it."""
    head_id, head = query(path, "SELECT id, hash FROM events ORDER BY id DESC LIMIT 1")[0]
    row = {"ts": "2026-10-10T00:00:00.000+00:00", "actor": "mcp", "tool": "list_targets", "tier": "read",
           "decision": "allow", "approved": None, "args_sha256": hashlib.sha256(b"").hexdigest(), "args_preview": "",
           "outcome": "ok", "detail": ""}
    payload = (head + json.dumps([row[f] for f in FIELDS], ensure_ascii=False)).encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest() if key else hashlib.sha256(payload).hexdigest()
    run_sql(path, "INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, "
                  "outcome, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row[f] for f in FIELDS] + [head, digest])
    mac_payload = f"anchor:{head_id + 1}:{digest}".encode()
    mac = hmac.new(key, mac_payload, hashlib.sha256).hexdigest() if key else hashlib.sha256(mac_payload).hexdigest()
    path.with_name(path.name + ".anchor").write_text(json.dumps({"id": head_id + 1, "hash": digest, "mac": mac}))


class TestKeyIdsAndUnkeyedEntries:

    @pytest.mark.parametrize("entry", [1, 2, 3])
    def test_changing_an_entrys_key_id_is_modified_not_another_key(self, tmp_path, entry):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "UPDATE events SET key_id = 'deadbeefdeadbeef' WHERE id = ?", (entry,))
        result = log.check()
        assert result.problem == "modified" and result.entry == entry, result.message

    def test_an_unkeyed_entry_after_keyed_ones_is_modified(self, tmp_path):
        """Someone without the key appends (or rewrites the newest entries as) unkeyed entries."""
        log = filled_log(tmp_path / "audit.db", n=2)
        head = query(log.path, "SELECT hash FROM events WHERE id = 2")[0][0]
        row = dict(zip(FIELDS, ["2026-10-10T00:00:00.000+00:00", "mcp", "list_targets", "read", "allow", None,
                                hashlib.sha256(b"").hexdigest(), "", "ok", ""]))
        digest = hashlib.sha256((head + json.dumps([row[f] for f in FIELDS] + ["unkeyed"], ensure_ascii=False))
                                .encode()).hexdigest()
        run_sql(log.path, "INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, "
                          "outcome, detail, prev_hash, hash, key_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [row[f] for f in FIELDS] + [head, digest, "unkeyed"])
        result = log.check()
        assert result.problem == "modified" and result.entry == 3

    def test_a_writer_without_the_key_is_refused_and_told_how_to_recover(self, tmp_path):
        log = filled_log(tmp_path / "audit.db", n=1)
        with pytest.raises(AuditKeyMismatch, match="tools audit --archive"):
            AuditLog(log.path).record("mcp", "list_targets", "read", "allow", "ok")
        assert log.check().intact and log.check().entries == 1

    def test_an_unkeyed_log_read_with_a_key_says_the_next_entry_is_keyed(self, tmp_path):
        filled_log(tmp_path / "audit.db", n=2, key=None)
        reader = AuditLog(tmp_path / "audit.db", key=KEY)
        result = reader.check()
        assert result.intact and not result.keyed and result.key_available
        assert "next entry is keyed" in result.message
        finding = audit_finding(reader, tmp_path)
        assert finding.severity == Severity.WARN and "keyring" not in finding.fix_hint.lower()


class TestRemovalsStayDetected:

    def test_newest_entries_removed_with_the_anchor_are_reported_as_removed(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")
        log.anchor_path.unlink()
        result = log.check()
        assert result.problem == "removed" and result.entry == 3

    def test_deleting_the_stored_break_does_not_hide_the_removal(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")
        log.record("mcp", "list_targets", "read", "allow", "ok")
        run_sql(log.path, "DELETE FROM meta WHERE k = 'break'")
        result = log.check()
        assert result.problem == "removed" and result.entry == 3

    def test_a_removed_anchor_stays_reported_after_the_next_call(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        log.anchor_path.unlink()
        log.record("mcp", "list_targets", "read", "allow", "ok")
        result = log.check()
        assert result.problem == "anchor_missing" and result.entry == 4 and "anchor" in result.message
        assert audit_finding(log, tmp_path).severity == Severity.WARN

    def test_a_lagging_anchor_is_no_break(self, tmp_path, monkeypatch):
        log = filled_log(tmp_path / "audit.db", n=1)
        real_replace = os.replace

        def fail_for_anchor(src, dst):
            if str(dst).endswith(".anchor"):
                raise PermissionError(13, "in use")
            return real_replace(src, dst)

        with monkeypatch.context() as m:
            m.setattr(audit_module.os, "replace", fail_for_anchor)
            log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)
        assert json.loads(log.anchor_path.read_text())["id"] == 1 and log.check().intact

        log.record("mcp", "list_targets", "read", "allow", "ok")

        assert json.loads(log.anchor_path.read_text())["id"] == 3
        assert log.check().intact and not query(log.path, "SELECT v FROM meta WHERE k = 'break'")

    def test_a_failed_commit_that_ended_the_transaction_reports_the_real_error(self, tmp_path, monkeypatch):
        """SQLite rolls back by itself after some COMMIT errors; the ROLLBACK in record() must not replace the
        real error with 'cannot rollback - no transaction is active'."""
        log = filled_log(tmp_path / "audit.db", n=1)
        real_connect = sqlite3.connect

        class CommitRollsBack:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    self._con.execute("ROLLBACK")
                    raise sqlite3.OperationalError("database or disk is full")
                return self._con.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._con, name)

        with monkeypatch.context() as m:
            m.setattr(sqlite3, "connect", lambda *a, **kw: CommitRollsBack(real_connect(*a, **kw)))
            with pytest.raises(sqlite3.OperationalError, match="disk is full"):
                log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)
        result = log.check()
        assert result.intact and result.entries == 1, result.message


DIES_AT_COMMIT = textwrap.dedent("""
    import os
    import sqlite3
    import sys
    from pathlib import Path
    from orchestrator.tools.audit import AuditLog
    path, when = sys.argv[1:]
    real_connect = sqlite3.connect

    class Dies:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *args):
            if sql == "COMMIT":
                if when == "after":
                    self._con.execute(sql)
                os._exit(3)  # killed: no except or finally block runs
            return self._con.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._con, name)

    sqlite3.connect = lambda *a, **kw: Dies(real_connect(*a, **kw))
    AuditLog(Path(path), key=b"audit-key").record("mcp", "start_build", "local_write", "ask", "ok", approved=True)
""")


class TestKilledWriter:

    @pytest.mark.parametrize("when, entries", [("before", 2), ("after", 3)])
    def test_a_writer_killed_at_the_commit_leaves_no_false_alarm(self, tmp_path, when, entries):
        log = filled_log(tmp_path / "audit.db", n=2)
        script = tmp_path / "dies.py"
        script.write_text(DIES_AT_COMMIT)
        proc = subprocess.run([sys.executable, str(script), str(log.path), when], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)), timeout=60)
        assert proc.returncode == 3, proc.stderr

        result = log.check()
        assert result.intact and result.entries == entries, result.message
        log.record("cli", "list_targets", "read", "allow", "ok")  # and the next append finds nothing either
        result = log.check()
        assert result.intact and result.entries == entries + 1, result.message


class TestDamagedValues:

    def test_a_binary_value_is_reported_as_modified_not_a_crash(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "UPDATE events SET detail = X'00ff' WHERE id = 2")
        result = log.check()
        assert result.problem == "modified" and result.entry == 2
        cli = invoke_cli(log, tmp_path, "tools", "audit", "--verify")
        assert cli.exit_code == 1 and "Traceback" not in cli.output and "modified" in cli.output

    @pytest.mark.parametrize("anchor", ['{"id": 3, "hash": "\\u00e9", "mac": "x"}',
                                        '{"id": 3, "hash": "\\ud800", "mac": "\\ud800"}'])
    def test_non_ascii_anchor_text_counts_as_unreadable(self, tmp_path, anchor):
        log = filled_log(tmp_path / "audit.db")
        log.anchor_path.write_text(anchor)
        assert log.check().problem == "anchor_missing"

    @pytest.mark.parametrize("anchor", ["[" * 100_000 + "]" * 100_000, '{"id": 3, "hash": "' + "a" * 10**6 + '"}'],
                             ids=["deeply-nested", "huge"])
    def test_a_hostile_anchor_file_counts_as_unreadable_not_a_crash(self, tmp_path, anchor):
        log = filled_log(tmp_path / "audit.db")
        log.anchor_path.write_text(anchor)
        assert log.check().problem == "anchor_missing"
        cli = invoke_cli(log, tmp_path, "tools", "audit", "--verify")
        assert cli.exit_code == 1 and "Traceback" not in cli.output and "anchor" in cli.output

    @pytest.mark.parametrize("damage, problem, entry", [
        ('{"id": 1e30, "hash": "x", "mac": "y"}', "anchor_missing", 4),      # beyond SQLite's integers
        ('{"id": Infinity, "hash": "x", "mac": "y"}', "anchor_missing", 4),  # Python's json accepts it
        ("UPDATE events SET hash = X'00ff' WHERE id = 3", "modified", 3),
    ], ids=["anchor-id-out-of-range", "anchor-id-infinity", "binary-hash-at-the-head"])
    def test_hostile_values_do_not_stop_auditing(self, tmp_path, damage, problem, entry):
        """record() reads the anchor and the newest entry before each append; what an editor put there must
        not make every later tool call fail to be audited, nor crash the check."""
        log = filled_log(tmp_path / "audit.db")
        if damage.startswith("UPDATE"):
            run_sql(log.path, damage)
        else:
            log.anchor_path.write_text(damage)

        log.record("mcp", "list_targets", "read", "allow", "ok")

        assert len(log.entries()) == 4
        result = log.check()
        assert result.problem == problem and result.entry == entry, result.message
        cli = invoke_cli(log, tmp_path, "tools", "audit", "--verify")
        assert cli.exit_code == 1 and "Traceback" not in cli.output, cli.output

    def test_a_hostile_stored_break_is_reported_as_modified(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "INSERT INTO meta (k, v) VALUES ('break', ?)", ("[" * 100_000 + "]" * 100_000,))
        assert log.check().problem == "modified"

    def test_non_ascii_hash_in_the_database_is_reported(self, tmp_path):
        log = filled_log(tmp_path / "audit.db")
        run_sql(log.path, "UPDATE events SET hash = 'é' WHERE id = 3")
        assert log.check().problem == "modified"


HOLDER = textwrap.dedent("""
    import sys
    from pathlib import Path
    from orchestrator.tools.framework_tools import _key_lock
    with _key_lock(Path(sys.argv[1])):
        print("locked", flush=True)
        sys.stdin.read()
""")


class TestAuditKeyLock:

    @pytest.fixture
    def holder(self, tmp_path):
        """Another process holding the audit key lock until its stdin is closed."""
        script = tmp_path / "holder.py"
        script.write_text(HOLDER)
        state = tmp_path / "state"
        proc = subprocess.Popen([sys.executable, str(script), str(state)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
        assert proc.stdout.readline().strip() == "locked"
        yield state, proc
        if proc.poll() is None:
            proc.stdin.close()
            proc.wait(timeout=20)
        proc.stdout.close()

    def test_the_lock_is_held_across_processes(self, holder):
        state, proc = holder
        with pytest.raises(TimeoutError):
            with framework_tools._key_lock(state, timeout=0.3):
                pass
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        with framework_tools._key_lock(state, timeout=5):
            pass

    def test_no_key_is_created_while_another_process_creates_one(self, holder, memory_keyring, monkeypatch):
        state, _proc = holder
        monkeypatch.setattr(framework_tools, "_key_lock", functools.partial(framework_tools._key_lock, timeout=0.3))
        assert framework_tools._audit_key(SimpleNamespace(secrets_manager=None), state) is None
        assert not memory_keyring.store  # an unkeyed process, never a second key

    def test_an_unusable_state_folder_does_not_stop_key_creation(self, tmp_path, memory_keyring):
        blocker = tmp_path / "a-file"
        blocker.write_text("")
        key = framework_tools._audit_key(SimpleNamespace(secrets_manager=None), blocker / "state")
        assert key and len(key) >= 32
