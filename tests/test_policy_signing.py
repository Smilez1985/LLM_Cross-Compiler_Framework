"""Policy changes need a person (MCP design S4, gaps G11 and G45).

The tool permissions and the named MCP tokens are signed with a key derived from the audit key; a file that is
edited by hand, damaged, unsigned or signed with another key denies everything (the token store accepts no token).
Only a person changes them, at a terminal or in the GUI; every save is audited with the old and new decisions before
it takes effect; each save raises the revision, and a save based on an older revision is refused.
"""

import json
import threading
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

# cli first: importing pydantic's BaseModel before the framework's CLI trips PySide's import hook in this environment
from orchestrator import cli as cli_module
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.mcp_tokens import TokenStore, TokenStoreError, token_digest
from orchestrator.tools.output import ToolOutput
from orchestrator.tools.policy import (CallerPolicy, Decision, PolicyConflict, PolicyStore, PolicyStoreError,
                                       ToolPolicy, ToolTier, policy_changes)
from orchestrator.tools.registry import ToolContext, ToolDenied, ToolRegistry, ToolSpec
from orchestrator.tools.signing import RevisionAnchor, SignatureError, sign, verify
from pydantic import BaseModel  # noqa: E402

KEY = b"k" * 32          # stands in for the audit key from the keyring (tests build no real credential)
OTHER_KEY = b"o" * 32


class In(BaseModel):
    text: str = "x"


class Out(ToolOutput):
    echoed: str = ""


def store_at(tmp_path, key=KEY, audit=None, **kwargs):
    return PolicyStore(tmp_path, key=key, audit=audit, **kwargs)


def hand_edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def denies_everything(policy: ToolPolicy) -> bool:
    return not policy.configured and all(policy.decide("x", tier) == Decision.DENY for tier in ToolTier)


# --- the signature ---------------------------------------------------------------------------------------------

class TestSignature:

    def test_round_trip_keyed_and_unkeyed(self, tmp_path):
        for key, folder in ((KEY, tmp_path / "keyed"), (None, tmp_path / "unkeyed")):
            store = store_at(folder, key=key)
            store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
            policy = store.load()
            assert store.problem is None and policy.configured
            assert policy.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW
            signature = json.loads(store.path.read_text())["signature"]
            assert signature["key_id"] == ("unkeyed" if key is None else signature["key_id"])
            assert KEY.decode() not in store.path.read_text(), "the key itself is never written"

    @pytest.mark.parametrize("edit", [
        lambda d: d["tiers"].__setitem__("local_write", "allow"),
        lambda d: d["overrides"].__setitem__("start_build", "allow"),
        lambda d: d.__setitem__("callers", {"mcp": {"tiers": {"read": "allow", "local_write": "allow",
                                                              "external": "ask"}}}),
        lambda d: d.__setitem__("revision", d["revision"] + 100),
        lambda d: d["signature"].__setitem__("mac", "0" * 64),
    ], ids=["tier", "override", "caller-section", "revision", "mac"])
    def test_a_hand_edited_signed_file_denies_everything(self, tmp_path, edit):
        store = store_at(tmp_path)
        store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}))
        hand_edit(store.path, edit)
        assert denies_everything(store.load())
        assert "cannot be trusted" in store.problem and "denied" in store.problem
        assert (tmp_path / "tool_policy.json.bad").exists(), "the edited file is kept for the person"

    def test_an_unsigned_file_denies_everything(self, tmp_path):
        """A 3.0.0 file, or one written by hand: everything is denied until the person sets the permissions up."""
        store = store_at(tmp_path)
        store.path.write_text(json.dumps({"version": 1, "configured": True, "tiers": {"local_write": "allow"}}))
        assert denies_everything(store.load())
        assert "no signature" in store.problem and "set the permissions up again" in store.problem

    def test_a_file_signed_with_another_key_denies_everything(self, tmp_path):
        store_at(tmp_path, key=OTHER_KEY).save(ToolPolicy(configured=True))
        store = store_at(tmp_path)
        assert denies_everything(store.load()) and "another key" in store.problem

    def test_an_unkeyed_signature_is_refused_when_a_key_exists(self, tmp_path):
        store_at(tmp_path, key=None).save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        store = store_at(tmp_path, key=KEY)
        assert denies_everything(store.load()) and "unkeyed" in store.problem

    def test_a_keyed_signature_is_refused_without_the_key(self, tmp_path):
        store_at(tmp_path).save(ToolPolicy(configured=True))
        store = store_at(tmp_path, key=None)
        assert denies_everything(store.load()) and "keyring" in store.problem

    def test_without_the_keyring_an_unkeyed_file_is_refused_while_the_audit_log_has_a_key(self, tmp_path):
        """A process started without the keyring must not accept a file someone rewrote with a plain hash."""
        keyed_audit = AuditLog(tmp_path / "audit.db", key=KEY)
        keyed_audit.record("cli", "x", "read", "allow", "ok")
        keyless_audit = AuditLog(tmp_path / "audit.db", key=None)
        store_at(tmp_path, key=None).save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        store = store_at(tmp_path, key=None, refuse_unkeyed=keyless_audit.kept_with_key)
        assert denies_everything(store.load()) and "audit log is kept with a key" in store.problem
        # On a computer whose audit log has no key either, the unkeyed file is used.
        plain = tmp_path / "plain"
        plain.mkdir()
        AuditLog(plain / "audit.db", key=None).record("cli", "x", "read", "allow", "ok")
        store_at(plain, key=None).save(ToolPolicy(configured=True))
        assert store_at(plain, key=None, refuse_unkeyed=AuditLog(plain / "audit.db").kept_with_key).load().configured

    def test_the_framework_signs_with_the_audit_key_and_checks_unkeyed_files(self, make_framework, monkeypatch):
        from orchestrator.tools import framework_tools
        fm = make_framework()
        monkeypatch.setattr(framework_tools, "_audit_key", lambda framework, state_dir=None: KEY)
        keyed = framework_tools.build_tool_registry(fm)
        assert keyed.policy_store.key == KEY and keyed.tokens.key == KEY and keyed.audit.keyed
        assert keyed.policy_store.audit is keyed.audit, "every save is audited"
        keyed.audit.record("cli", "x", "read", "allow", "ok")  # the log is kept with a key from now on
        monkeypatch.setattr(framework_tools, "_audit_key", lambda framework, state_dir=None: None)
        keyless = framework_tools.build_tool_registry(fm)
        PolicyStore(keyless.policy_store.path.parent, key=None).replace(ToolPolicy(configured=True))
        assert keyless.policy().configured is False
        assert "audit log is kept with a key" in keyless.policy_store.problem
        keyless.tokens.path.unlink(missing_ok=True)
        TokenStore(keyless.tokens.path.parent, key=None).add("laptop")
        with pytest.raises(TokenStoreError, match="audit log is kept with a key"):
            keyless.tokens.list()

    def test_kept_with_key_never_creates_the_log(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        assert audit.kept_with_key() is False
        assert not (tmp_path / "audit.db").exists()

    def test_signatures_are_bound_to_the_kind_of_file(self):
        data = sign("tool_policy", {"version": 1}, KEY)
        assert verify("tool_policy", data, KEY) == {"version": 1}
        with pytest.raises(SignatureError):
            verify("mcp_tokens", data, KEY)

    def test_the_registry_denies_a_tool_a_hand_edit_allowed(self, tmp_path):
        ran = []
        registry = ToolRegistry(store_at(tmp_path), AuditLog(tmp_path / "audit.db", key=KEY))
        registry.register(ToolSpec("write", "Write", "", ToolTier.LOCAL_WRITE, In, Out,
                                   lambda i: ran.append(i) or Out(echoed="w")))
        registry.policy_store.replace(ToolPolicy(configured=True, overrides={"write": Decision.DENY}))
        hand_edit(registry.policy_store.path, lambda d: d["overrides"].__setitem__("write", "allow"))
        with pytest.raises(ToolDenied):
            registry.call("write", {}, ToolContext("mcp"))
        assert ran == []


# --- the token store -------------------------------------------------------------------------------------------

class TestTokenStore:

    def test_tokens_work_with_a_signed_store(self, tmp_path):
        tokens = TokenStore(tmp_path, key=KEY)
        token = tokens.add("laptop")
        assert tokens.find(token).name == "laptop"
        assert "signature" in json.loads(tokens.path.read_text())

    def test_a_token_added_by_hand_is_never_accepted(self, tmp_path):
        tokens = TokenStore(tmp_path, key=KEY)
        tokens.add("laptop")
        forged = "lcf_mcp_" + "f" * 43
        hand_edit(tokens.path, lambda d: d["tokens"].append(
            {"name": "evil", "sha256": token_digest(forged), "created": "2026-01-01T00:00:00+00:00",
             "policy": {"version": 1, "configured": True}}))
        fresh = TokenStore(tmp_path, key=KEY)
        assert fresh.find(forged) is None
        with pytest.raises(TokenStoreError, match="cannot be trusted"):
            fresh.list()

    def test_a_hand_edited_token_policy_is_never_used(self, tmp_path):
        tokens = TokenStore(tmp_path, key=KEY)
        tokens.add("laptop")
        hand_edit(tokens.path, lambda d: d["tokens"][0]["policy"].__setitem__(
            "tiers", {"read": "allow", "local_write": "allow", "external": "ask"}))
        # decided where every call is decided (ToolRegistry._policy_of; TokenStore.policy_of, its first version, is gone)
        registry = ToolRegistry(store_at(tmp_path), AuditLog(tmp_path / "audit.db", key=KEY),
                                tokens=TokenStore(tmp_path, key=KEY))
        spec = ToolSpec("start_build", "Start build", "", ToolTier.LOCAL_WRITE, In, Out, lambda i: Out(echoed="b"))
        assert registry.decide(spec, caller="mcp:laptop") == Decision.DENY

    def test_an_unsigned_token_file_accepts_no_token(self, tmp_path):
        tokens = TokenStore(tmp_path, key=KEY)
        token = tokens.add("laptop")
        hand_edit(tokens.path, lambda d: d.pop("signature"))
        assert TokenStore(tmp_path, key=KEY).find(token) is None


# --- revision and lock (G45) -----------------------------------------------------------------------------------

class TestRevision:

    def test_each_save_raises_the_revision(self, tmp_path):
        store = store_at(tmp_path)
        first = store.save(ToolPolicy(configured=True))
        second = store.save(first.model_copy(update={"tiers": {ToolTier.READ: Decision.DENY}}))
        assert (first.revision, second.revision) == (1, 2) and store.load().revision == 2

    def test_a_stale_save_is_refused_and_changes_nothing(self, tmp_path):
        """Window A read the permissions; window B saved a change meanwhile; A's save must not undo B's change."""
        store = store_at(tmp_path)
        store.save(ToolPolicy(configured=True))
        window_a = store.load()
        other = store_at(tmp_path)
        other.save(other.load().model_copy(update={"overrides": {"start_build": Decision.DENY}}))
        with pytest.raises(PolicyConflict, match="changed elsewhere"):
            store.save(window_a.model_copy(update={"tiers": {ToolTier.LOCAL_WRITE: Decision.ALLOW}}))
        stored = store.load()
        assert stored.overrides == {"start_build": Decision.DENY}
        assert stored.tiers[ToolTier.LOCAL_WRITE] == Decision.ASK

    def test_update_loads_changes_and_saves_under_the_lock(self, tmp_path):
        """Two processes changing different callers at the same time: no change is lost."""
        store_at(tmp_path).save(ToolPolicy(configured=True))
        errors = []

        def worker(caller):
            store = store_at(tmp_path)  # own instance: its own lock file handle, as another process has
            try:
                for i in range(10):
                    store.update(lambda current: current.model_copy(update={"callers": {
                        **current.callers, f"mcp:{caller}-{i}": CallerPolicy()}}))
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        stored = store_at(tmp_path).load()
        assert len(stored.callers) == 20 and stored.revision == 21

    def test_a_save_over_an_unusable_file_is_allowed(self, tmp_path):
        store = store_at(tmp_path)
        store.path.write_text("{broken")
        store.load()
        assert store.save(ToolPolicy(configured=True)).revision == 1 and store.problem is None


# --- an older copy or a removed file (revision anchor) ---------------------------------------------------------

def memory_anchor(slots=None):
    """A revision anchor whose value lives in a dict (as the OS keyring keeps it, outside the per-user folder)."""
    slots = {} if slots is None else slots
    return slots, RevisionAnchor(lambda: slots.get("rev"), lambda v: slots.__setitem__("rev", v))


class TestOlderCopiesAndRemovedFiles:
    """A valid signature only says that the framework wrote a file once. An older signed copy put back over the
    current permissions (or tokens) would bring back a revoked standing approval, a revoked token or a looser
    permission, and a removed permission file would fall back to the defaults ("ask" where the person had denied a
    tool). The newest saved revision is kept in the keyring; an older copy and a removed file deny everything."""

    def test_an_older_signed_copy_of_the_permissions_denies_everything(self, tmp_path):
        slots, anchor = memory_anchor()
        store = store_at(tmp_path, anchor=anchor)
        first = store.save(ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW}))
        older = store.path.read_bytes()
        store.save(first.model_copy(update={"overrides": {"start_build": Decision.DENY}}))
        assert slots["rev"] == "2"
        store.path.write_bytes(older)  # put back by hand: its signature is genuine
        assert denies_everything(store.load())
        assert "older copy" in store.problem and (tmp_path / "tool_policy.json.bad").exists()
        # Another process (no value cached yet) refuses it too, and a person's save goes past the anchor.
        other = store_at(tmp_path, anchor=memory_anchor(slots)[1])
        assert denies_everything(other.load())
        saved = other.save(ToolPolicy(configured=True))
        assert saved.revision == 3 and slots["rev"] == "3" and store.load().configured and store.problem is None

    def test_a_removed_permission_file_denies_everything_instead_of_the_defaults(self, tmp_path):
        slots, anchor = memory_anchor()
        store = store_at(tmp_path, anchor=anchor)
        store.save(ToolPolicy(configured=True, overrides={"start_build": Decision.DENY}))
        store.path.unlink()
        policy = store.load()
        assert denies_everything(policy) and "was removed" in store.problem
        fresh = store_at(tmp_path, anchor=memory_anchor(slots)[1])
        assert denies_everything(fresh.load())
        assert store.update(lambda p: ToolPolicy(configured=True)).revision == 2

    def test_without_a_saved_revision_a_missing_file_still_gives_the_defaults(self, tmp_path):
        store = store_at(tmp_path, anchor=memory_anchor()[1])
        policy = store.load()
        assert store.problem is None and not policy.configured and policy.tiers == ToolPolicy().tiers

    def test_a_newer_save_by_another_process_is_accepted(self, tmp_path):
        slots, anchor = memory_anchor()
        mine = store_at(tmp_path, anchor=anchor)
        mine.save(ToolPolicy(configured=True))
        assert mine.load().revision == 1
        theirs = store_at(tmp_path, anchor=memory_anchor(slots)[1])  # e.g. the GUI
        theirs.update(lambda p: p.model_copy(update={"overrides": {"start_build": Decision.DENY}}))
        assert mine.load().overrides == {"start_build": Decision.DENY} and mine.problem is None

    def test_a_keyring_that_cannot_be_read_never_lowers_the_anchor(self, tmp_path):
        slots, anchor = memory_anchor()
        store = store_at(tmp_path, anchor=anchor)
        store.save(ToolPolicy(configured=True))
        older = store.path.read_bytes()
        store.save(ToolPolicy(configured=True, revision=1))
        broken = RevisionAnchor(lambda: (_ for _ in ()).throw(RuntimeError("locked")), lambda v: None)
        assert store_at(tmp_path, anchor=broken).load().configured  # no value known: nothing to compare with
        slots.clear()  # the entry removed from the keyring
        store.path.write_bytes(older)
        assert denies_everything(store.load()), "this process still knows revision 2"

    def test_an_older_copy_of_the_token_store_brings_no_revoked_token_back(self, tmp_path):
        slots, anchor = memory_anchor()
        tokens = TokenStore(tmp_path, key=KEY, anchor=anchor)
        token = tokens.add("laptop")
        older = tokens.path.read_bytes()
        tokens.revoke("laptop")
        assert tokens.find(token) is None
        tokens.path.write_bytes(older)
        assert tokens.find(token) is None, "the revoked token stays revoked"
        with pytest.raises(TokenStoreError, match="older copy"):
            tokens.list()
        fresh = TokenStore(tmp_path, key=KEY, anchor=memory_anchor(slots)[1])
        assert fresh.find(token) is None

    def test_the_framework_keeps_the_revisions_in_the_keyring(self, make_framework, memory_keyring):
        fm = make_framework()
        registry = fm.tool_registry
        assert registry.policy_store.key is not None, "the test framework has a keyring"
        registry.policy_store.replace(ToolPolicy(configured=True))
        registry.tokens.add("laptop")
        stored = {name: value for (service, name), value in memory_keyring.store.items()}
        assert stored.get("tool_policy-revision") == str(registry.policy().revision)
        assert stored.get("mcp_tokens-revision") == "1"
        older = registry.policy_store.path.read_bytes()
        registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.DENY}))
        registry.policy_store.path.write_bytes(older)
        assert not registry.policy().configured and "older copy" in registry.policy_store.problem


# --- audit (G11) -----------------------------------------------------------------------------------------------

class TestAudit:

    def test_a_save_is_audited_with_old_and_new_decisions(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db", key=KEY)
        store = store_at(tmp_path, audit=audit)
        first = store.save(ToolPolicy(configured=True), actor="gui")
        store.save(first.model_copy(update={"tiers": {**first.tiers, ToolTier.LOCAL_WRITE: Decision.ALLOW},
                                            "overrides": {"start_build": Decision.DENY}}), actor="cli")
        newest, older = audit.entries(2)
        assert (older["actor"], older["outcome"], older["tool"]) == ("gui", "policy_changed", "tool_policy")
        assert newest["actor"] == "cli" and newest["approved"] == 1
        assert "shared: tier local_write ask -> allow" in newest["detail"]
        assert "shared: start_build tier setting -> deny" in newest["detail"]
        assert audit.check().intact

    def test_nothing_changes_when_the_audit_cannot_record_it(self, tmp_path):
        class BrokenAudit:
            def record(self, *args, **kwargs):
                raise OSError("disk full")
        store = store_at(tmp_path)
        store.save(ToolPolicy(configured=True))
        before = store.path.read_bytes()
        store.audit = BrokenAudit()
        with pytest.raises(PolicyStoreError, match="not changed"):
            store.replace(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        assert store.path.read_bytes() == before
        assert not list(tmp_path.glob(".tool_policy.*.tmp")), "no temporary file is left behind"

    def test_changes_name_callers_tiers_and_tools_only(self):
        before = ToolPolicy(configured=True)
        after = before.model_copy(update={"callers": {"ditto": CallerPolicy(
            tiers={ToolTier.READ: Decision.ALLOW, ToolTier.LOCAL_WRITE: Decision.ALLOW,
                   ToolTier.EXTERNAL: Decision.ASK})}})
        lines = policy_changes(before, after)
        assert "ditto: own permissions added" in lines and "ditto: tier local_write - -> allow" in lines
        assert policy_changes(None, after)[0].startswith("set up anew")


# --- a person at a terminal, or the GUI --------------------------------------------------------------------------

def make_registry(tmp_path):
    audit = AuditLog(tmp_path / "audit.db", key=KEY)
    registry = ToolRegistry(store_at(tmp_path, audit=audit), audit)
    registry.register(ToolSpec("start_build", "Start build", "", ToolTier.LOCAL_WRITE, In, Out,
                               lambda i: Out(echoed="b")))
    return registry


def invoke(registry, *args, input=None, terminal=True):
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    with mock.patch.object(cli_module, "_interactive", lambda: terminal):
        return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=input)


class TestPerson:

    @pytest.mark.parametrize("args", [("tools", "permissions"), ("tools", "permissions", "--reset"),
                                      ("tools", "permissions", "--caller", "ditto"),
                                      ("tools", "permissions", "--caller", "mcp", "--reset"), ("ditto", "setup")])
    def test_piped_answers_are_refused(self, tmp_path, args):
        registry = make_registry(tmp_path)
        registry.policy_store.replace(ToolPolicy(configured=True, callers={"mcp": CallerPolicy()}))
        before = registry.policy_store.path.read_bytes()
        result = invoke(registry, *args, input="allow\nallow\nask\n", terminal=False)
        assert result.exit_code == 2 and "needs a terminal" in " ".join(result.output.split())
        assert registry.policy_store.path.read_bytes() == before

    def test_at_a_terminal_the_change_is_saved_and_audited(self, tmp_path):
        registry = make_registry(tmp_path)
        result = invoke(registry, "tools", "permissions", input="allow\nallow\nask\n")
        assert result.exit_code == 0, result.output
        assert registry.policy().decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["outcome"]) == ("cli", "policy_changed")
        assert "tier local_write" in entry["detail"]

    def test_the_cli_reports_a_change_made_meanwhile(self, tmp_path, monkeypatch):
        registry = make_registry(tmp_path)
        registry.policy_store.replace(ToolPolicy(configured=True))
        real_ask = cli_module._ask_tool_policy

        def ask_while_the_gui_saves(reg, current, *args, **kwargs):
            answer = real_ask(reg, current, *args, **kwargs)
            other = store_at(tmp_path)
            other.save(other.load().model_copy(update={"overrides": {"start_build": Decision.DENY}}), actor="gui")
            return answer
        monkeypatch.setattr(cli_module, "_ask_tool_policy", ask_while_the_gui_saves)
        result = invoke(registry, "tools", "permissions", input="allow\nallow\nask\n")
        assert result.exit_code == 1 and "changed elsewhere" in " ".join(result.output.split())
        assert registry.policy().overrides == {"start_build": Decision.DENY}

    def test_the_gui_refuses_a_stale_save_and_audits_its_own(self, tmp_path, qapp, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from orchestrator.gui.dialogs import ToolPermissionsDialog
        registry = make_registry(tmp_path)
        registry.policy_store.replace(ToolPolicy(configured=True))
        shown = []
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
        dialog = ToolPermissionsDialog(registry)
        other = store_at(tmp_path)
        other.save(other.load().model_copy(update={"overrides": {"start_build": Decision.DENY}}))
        dialog.save()
        assert shown and "changed elsewhere" in shown[0]
        assert registry.policy().overrides == {"start_build": Decision.DENY}
        fresh = ToolPermissionsDialog(registry)
        fresh.save()
        assert registry.audit.entries(1)[0]["actor"] == "gui"

    def test_revoking_in_the_gui_never_makes_a_window_opened_earlier_save_over_a_change(self, tmp_path, qapp,
                                                                                         monkeypatch):
        """The window shows the permissions as they were when it opened. Revoking a standing approval there must not
        let its later save overwrite a change made elsewhere meanwhile (here: the CLI denied start_build)."""
        from PySide6.QtWidgets import QMessageBox

        from orchestrator.gui.dialogs import ToolPermissionsDialog
        from orchestrator.tools.grants import Grant
        registry = make_registry(tmp_path)
        grant = Grant(id="g_" + "1" * 16, tool="start_build", scope={"tool": "start_build"}, summary="every call",
                      rule_version=1, created="2026-10-11T00:00:00+00:00", created_via="gui")
        registry.policy_store.replace(ToolPolicy(configured=True, grants={"mcp": [grant]}))
        shown = []
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]))
        dialog = ToolPermissionsDialog(registry)
        other = store_at(tmp_path)
        other.update(lambda p: p.model_copy(update={"overrides": {"start_build": Decision.DENY}}), actor="cli")
        assert dialog.revoke_grant(grant.id)
        dialog.save()
        assert shown and "changed elsewhere" in shown[0]
        stored = registry.policy()
        assert stored.overrides == {"start_build": Decision.DENY} and stored.grants == {}
        # Without a change elsewhere, revoking and then saving works as before.
        registry.policy_store.update(lambda p: p.model_copy(update={"grants": {"mcp": [grant]}}))
        calm = ToolPermissionsDialog(registry)
        assert calm.revoke_grant(grant.id)
        shown.clear()
        calm.save()
        assert not shown and registry.policy().grants == {}

    def test_the_gui_shows_why_everything_is_denied(self, tmp_path, qapp):
        from PySide6.QtWidgets import QLabel

        from orchestrator.gui.dialogs import ToolPermissionsDialog
        registry = make_registry(tmp_path)
        registry.policy_store.path.write_text(json.dumps({"version": 1, "configured": True}))
        dialog = ToolPermissionsDialog(registry, first_run=True)
        assert any("no signature" in label.text() for label in dialog.findChildren(QLabel))


class TestDoctor:

    def test_a_hand_edit_and_a_damaged_token_store_are_findings(self, make_framework):
        from orchestrator.Core.doctor import Doctor, Severity, check_tool_policy
        fm = make_framework()
        registry = fm.tool_registry
        registry.policy_store.replace(ToolPolicy(configured=True))
        hand_edit(registry.policy_store.path, lambda d: d["tiers"].__setitem__("local_write", "allow"))
        registry.tokens.add("laptop")
        hand_edit(registry.tokens.path, lambda d: d["tokens"][0].__setitem__("name", "other"))
        findings = {f.code: f for f in Doctor(fm, [check_tool_policy]).run().findings}
        assert findings["tools.permissions"].severity == Severity.ERROR
        assert "cannot be trusted" in findings["tools.permissions"].message
        assert findings["tools.tokens"].severity == Severity.ERROR
