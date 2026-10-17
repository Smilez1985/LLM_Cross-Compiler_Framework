"""The facts catalogue of the delegation design (sections 4.1-4.4, work package WP4).

Three kinds of findings decide who answers a question: blockers refuse before any question, for the AI and the person
alike; situation warnings may be decided by an AI with a delegation; security and legal warnings always go to the
person. The kind of a code lives in the catalogue only, a reclassification is a reviewed change, and a decision is
bound to the facts' decision digest, which free space and byte counts that change while a question waits never move.
"""

import dataclasses
import shutil

import pytest

from orchestrator.tools import facts_catalog as catalog
from orchestrator.tools.registry import ToolError
from orchestrator.tools.spec import CallFacts, CallWarning, Fact
from test_build_tools import BUILD, GB, env, usage  # noqa: F401  (the build fixture)

# The security and legal codes as reviewed for DELEGATION_RULES_VERSION 3 (version 1 also had
# artifact_from_other_computer, a code of the removed remote mode). Changing the catalogue's kinds changes what an AI
# under a delegation may decide: change this set, the version and the docs together (design 4.1).
SECURITY_LEGAL_V3 = frozenset({
    "base_image_unofficial", "base_image_unpinned", "base_model_gated",
    "facts_unknown", "first_build_unconfirmed", "gated_model", "license_restrictive", "license_unknown",
    "pickle_weights", "preflight_error", "signs_as_you", "target_pack_community", "target_unreviewed",
    "untrusted_model_native", "vendor_licence", "vendor_soft_cap_exceeded"})
# The blockers by their reason code as reviewed for DELEGATION_RULES_VERSION 4: 2 removed transfer_unsupported (the
# remote mode); 3 added snapshot_no_room (eighth round, answer 2) and named why a board cannot be reached
# (device_host_down, device_port_closed, device_auth_failed, device_host_key_changed instead of device_unreachable and
# host_key_changed; N6); 4 added gui_not_open (W4: the GUI tool when no framework window is open). A blocker added,
# removed or given another reason code raises the version.
BLOCKERS_V4 = {
    "failed": frozenset({"index_stale"}),
    "hardware_unavailable": frozenset({"device_host_down"}),
    "insufficient_resources": frozenset({"disk_full", "snapshot_no_room"}),
    "invalid_value": frozenset({"crawl_caps", "file_not_in_repo", "setting_invalid", "url_not_public"}),
    "limit_reached": frozenset({"artifact_busy", "crawl_quota", "crawl_running", "device_busy"}),
    "missing_information": frozenset({"model_not_downloaded", "no_device_model", "no_device_runtime",
        "runtime_not_built"}),
    "needs_person": frozenset({"commit_required", "device_auth_failed", "device_host_key_changed",
        "device_model_unset", "device_not_trusted", "device_port_closed", "file_outside_folders", "gated_no_access",
        "gui_not_open", "host_key_unknown", "ingest_notice_unread", "no_signing_key", "offline_mode", "sandbox_off",
        "toolchain_missing", "trash_not_yours"}),
    "not_supported": frozenset({"image_problem", "knowledge_off", "model_unpinned", "no_convertible_weights",
        "not_gguf", "pack_rejected", "pack_revoked", "size_unknown", "target_revoked", "unsafe_filename"}),
    "permission_denied": frozenset({"setting_loosens"}),
    "unknown_id": frozenset({"artifact_unknown", "file_changed", "pack_unknown", "proposal_missing",
        "snapshot_unknown", "trash_unknown", "unknown_repository"}),
}


def build_facts(env, **service):  # noqa: F811
    for key, value in service.items():
        setattr(env.service, key, value)
    spec = env.registry.get("start_build")
    inp = env.registry.validate_input(spec, dict(BUILD))
    from orchestrator.tools.registry import ToolContext
    return env.registry.look_up(spec, inp, ToolContext("ditto"))


def test_kind_from_catalog_only():
    """A tool names a code and numbers; the kind comes from the catalogue, and an unknown code is a security
    warning (unknown is never "no problem"), which the tools' helper refuses to emit at all."""
    assert [f.name for f in dataclasses.fields(CallWarning)] == ["code", "numbers"], "no kind a tool could set"
    assert CallWarning("disk_tight", {"free_after_gb": 3.0, "reserve_gb": 2.0}).kind == "situation"
    assert CallWarning("pickle_weights").kind == "security" and CallWarning("gated_model").kind == "legal"
    assert CallWarning("made_up_code").kind == "security"
    with pytest.raises(ValueError, match="unknown warning code"):
        catalog.warn("made_up_code")
    with pytest.raises(ValueError):
        catalog.warn("disk_tight", free_after_gb="three GB, trust me")  # numbers are closed values, never text
    with pytest.raises(ValueError, match="cannot be a number's name"):
        catalog.warn("vendor_soft_cap_exceeded", key="pip_packages", count=31, cap=30)  # clashes with ui_text(key)
    for spec in catalog.WARNINGS.values():
        import string
        names = {n for _, n, _, _ in string.Formatter().parse(spec.ai_template) if n}
        assert not names & catalog.RESERVED_NUMBERS, spec.ai_template


def test_security_legal_codes_snapshot():
    assert catalog.SECURITY_LEGAL == SECURITY_LEGAL_V3, (
        "a warning changed its kind: review it, then update SECURITY_LEGAL_V3 and raise DELEGATION_RULES_VERSION")
    by_reason = {}
    for code, spec in catalog.BLOCKERS.items():
        by_reason.setdefault(spec.reason_code, set()).add(code)
    assert by_reason == BLOCKERS_V4, (
        "a blocker was added, removed or changed its reason code: review it, then update BLOCKERS_V4 and raise "
        "DELEGATION_RULES_VERSION")
    assert catalog.DELEGATION_RULES_VERSION == 4
    assert {c for c, w in catalog.WARNINGS.items() if w.kind == "legal"} == {
        "gated_model", "base_model_gated", "license_restrictive", "license_unknown", "vendor_licence"}


def test_every_situation_warning_has_an_ai_rule():
    for code, spec in catalog.WARNINGS.items():
        if spec.kind == "situation":
            assert spec.ai_rule, f"{code}: an AI with a delegation needs a rule for yes, no or ask_person"
        else:
            assert not spec.ai_rule, f"{code}: only the person decides it, so an AI gets no rule to answer it"


def test_every_security_warning_has_a_plain_text_and_if_unsure():
    from orchestrator.utils.l10n_facts import TEXTS
    for code in catalog.SECURITY_LEGAL:
        spec = catalog.WARNINGS[code]
        assert spec.if_unsure in ("no", "ask_person"), code
        for lang in ("en", "de"):
            assert TEXTS[lang].get(f"warning.{code}"), f"{code} has no person text in {lang}"
        assert spec.ai_template, f"{code} has no text for the AI"


def test_needs_person_blockers_have_no_resolving_tool_and_resolved_by_names_a_registered_tool(make_framework):
    registered = {s.name for s in make_framework().tool_registry.list()}
    for code, spec in catalog.BLOCKERS.items():
        if spec.resolved_by:
            assert spec.reason_code == "missing_information", f"{code}: an AI can clear it, so it is no needs_person"
            assert spec.resolved_by in registered, f"{code}: {spec.resolved_by} is not a registered tool"
            assert "needs_person" not in spec.also, code
        if spec.reason_code == "needs_person":
            assert spec.resolved_by is None, f"{code}: only the person can clear a needs_person blocker"
            assert spec.person_action, f"{code}: the person must be told what they can do"


def test_blockers_carry_separate_next_and_person_action():
    """Every blocker refuses with its reason code and code, a next step for the AI and, apart from it, what the
    person can do (design 4.1, LAY-S10)."""
    from orchestrator.tools.normalize import CODES
    for code, spec in catalog.BLOCKERS.items():
        assert spec.reason_code in CODES and all(a in CODES for a in spec.also), code
        error = catalog.blocked(code, "It cannot run.", "Tell the person.")
        assert isinstance(error, ToolError) and error.blocker == code and error.code == spec.reason_code
        assert error.next.startswith("Tell the person."), code
        if spec.resolved_by:
            assert f"Call {spec.resolved_by} first." in error.next, code
        if spec.person_action:
            assert error.person_action and error.person_action != error.next, code
            assert error.person_action not in error.message, "the person's action is not the AI's text"
        else:
            assert error.person_action == "", code
    with pytest.raises(ValueError, match="unknown blocker"):
        catalog.blocked("made_up", "x")
    with pytest.raises(ValueError, match="is refused with"):
        catalog.blocked("disk_full", "x", reason="declined")  # a site cannot pick another reason code
    assert catalog.blocked("offline_mode", "x", reason="not_supported").code == "not_supported"  # listed in `also`


def test_the_mcp_error_names_the_blocker():
    """An MCP client gets {"error": {"code", "field", "blocker"}}; a blocker the catalogue does not know never
    passes (the structured error carries framework codes only)."""
    from orchestrator import mcp_server
    box = {}
    token = mcp_server._ERROR_FACTS.set(box)
    try:
        mcp_server._coded(Exception("x"), "insufficient_resources", "", "disk_full")
        assert box == {"code": "insufficient_resources", "blocker": "disk_full"}
        mcp_server._coded(Exception("x"), "failed", "", "made up <b>text</b>")
        assert box == {"code": "failed"}
    finally:
        mcp_server._ERROR_FACTS.reset(token)


def test_digest_stable_under_cache_and_disk_churn(env):  # noqa: F811
    """F3: free space and the bytes already staged change while a question waits; neither makes it new. A change of
    the verdict (enough -> tight) does."""
    before = build_facts(env, free=900 * GB)
    after = build_facts(env, free=880 * GB)
    assert before.consequence != after.consequence, "the person still sees the current free space"
    assert before.decision_digest() == after.decision_digest()
    env.service.hub = dataclasses.replace(env.service.hub)  # same commit and files
    staged = dataclasses.replace(before, bound={**before.bound, "cached": before.bound["cached"] + 100 * 10 ** 6,
                                                "download": before.bound["download"]})
    assert staged.decision_digest() == before.decision_digest(), "a cache that grows changes nothing"
    tight = build_facts(env, free=90 * GB)
    assert "disk_tight" in [w.code for w in tight.warnings]
    assert tight.decision_digest() != before.decision_digest(), "enough -> tight is a new question"


def test_digest_of_a_download_is_stable_under_free_space(tmp_path, monkeypatch):
    from orchestrator.Core import hf_hub
    from test_hf_tools import REPO, Hub
    from types import SimpleNamespace
    from pathlib import Path
    from orchestrator.Core.job_service import JobService
    from orchestrator.tools.audit import AuditLog
    from orchestrator.tools.framework_tools import register_framework_tools
    from orchestrator.tools.policy import PolicyStore
    from orchestrator.tools.registry import ToolContext, ToolRegistry
    hub, free = Hub(), {"bytes": 900 * GB}
    monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10 ** 12, 0, free["bytes"]))
    settings = {"models_dir": str(tmp_path / "models"), "offline_mode": False}
    (tmp_path / "models").mkdir()
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: settings.get(k, d),
                                                       user_state_dir=str(tmp_path / "state")),
                                resolve_path=lambda p: Path(p), hf_api_factory=hub.factory,
                                secrets_manager=SimpleNamespace(get_secret=lambda key: None), build_service=None,
                                job_service=JobService())
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "p"), AuditLog(tmp_path / "a.db")),
                                        framework)
    spec = registry.get("download_hf_file")
    inp = registry.validate_input(spec, {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
    first = registry.look_up(spec, inp, ToolContext("mcp"))
    free["bytes"] = 850 * GB
    second = registry.look_up(spec, inp, ToolContext("mcp"))
    assert [f.value for f in first.facts if f.label == "Disk"] != [f.value for f in second.facts if f.label == "Disk"]
    assert first.decision_digest() == second.decision_digest()


def test_every_non_volatile_input_changes_the_digest(env):  # noqa: F811
    """Over the build's look-up: every covered bound value, every non-volatile fact, every stable number, the
    template, the cap, the scope and the warning set make a decision new; a volatile fact or number, the rendered
    consequence and a bound value outside the digest keys never do."""
    facts = build_facts(env, free=900 * GB)
    digest = facts.decision_digest()
    for key in facts.digest_keys:
        changed = dataclasses.replace(facts, bound={**facts.bound, key: ["changed", key]})
        assert changed.decision_digest() != digest, f"bound[{key}] is covered"
    for i, fact in enumerate(facts.facts):
        changed_facts = list(facts.facts)
        changed_facts[i] = dataclasses.replace(fact, value=fact.value + " (changed)")
        moved = dataclasses.replace(facts, facts=tuple(changed_facts)).decision_digest() != digest
        assert moved == (not fact.volatile), f"fact {fact.label}: volatile={fact.volatile}"
    stable, volatile = catalog.NUMBER_KEYS["start_build"]
    for key, value in facts.numbers.items():
        other = (value + 1) if isinstance(value, int) else f"{value}x"
        moved = dataclasses.replace(facts, numbers={**facts.numbers, key: other}).decision_digest() != digest
        assert moved == (key in stable), key
    assert dataclasses.replace(facts, consequence="other words").decision_digest() == digest
    assert dataclasses.replace(facts, consequence_template="other").decision_digest() != digest
    assert dataclasses.replace(facts, cap="ask").decision_digest() != digest
    assert dataclasses.replace(facts, scope={"size_bytes": 1}).decision_digest() != digest
    assert dataclasses.replace(facts, warnings=facts.warnings + (catalog.warn("long_build", minutes=9, n=3),)) \
        .decision_digest() != digest
    outside = [k for k in facts.bound if k not in facts.digest_keys]
    assert {"download", "cached", "grant"} <= set(outside)
    for key in outside:
        assert dataclasses.replace(facts, bound={**facts.bound, key: "x"}).decision_digest() == digest, key


def test_a_digest_without_named_keys_covers_every_bound_value():
    facts = CallFacts(consequence="c", facts=(Fact("a", "1"),), bound={"x": 1, "y": 2}, tool="t")
    assert dataclasses.replace(facts, bound={"x": 1, "y": 3}).decision_digest() != facts.decision_digest()


def test_a_digest_without_a_template_covers_the_consequence():
    """A look-up that names no consequence template has not said which part of its consequence is volatile: the
    consequence as rendered is part of the decision then (unknown is never "no problem"), for the digest and for the
    question a pending approval compares; only a template lets free space in the consequence change unasked."""
    from orchestrator.tools.approvals import stable_facts
    facts = CallFacts(consequence="Deletes the build A.", facts=(Fact("a", "1"),), bound={"x": 1}, tool="t")
    other = dataclasses.replace(facts, consequence="Deletes the build B.")
    assert other.decision_digest() != facts.decision_digest()
    assert stable_facts(other).consequence == "Deletes the build B.", "the question compared keeps the consequence"
    templated = dataclasses.replace(facts, consequence_template="Deletes the build {name}.")
    assert dataclasses.replace(templated, consequence="Deletes the build B.").decision_digest() == \
        templated.decision_digest()


def test_size_class_and_gb_numbers():
    assert catalog.size_class(4 * GB) == "5gb" and catalog.size_class(5 * GB + 1) == "20gb"
    assert catalog.size_class(300 * GB) == "any" and catalog.size_class(None) == "unknown"
    assert catalog.gb_number(1_234_000_000) == 1.2


def test_warning_codes_are_stable_identifiers_ready_for_acknowledgements():
    """[Q3] (a "do not ask again" tick box per model version) binds an acknowledgement to exactly repository + commit
    + warning code; that needs codes that are plain identifiers and never reused for another meaning. The tick box
    itself is not built here."""
    import re
    from orchestrator.tools.spec import WARNING_CODE_RE
    for code in catalog.WARNINGS:
        assert re.fullmatch(WARNING_CODE_RE, code), code
    assert not set(catalog.WARNINGS) & set(catalog.BLOCKERS), "a code means one thing"
