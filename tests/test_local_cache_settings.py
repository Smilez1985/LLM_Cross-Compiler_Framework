"""The settings of the memory budget of Ditto's local server (chain ctx-local-mem): local_cache_ram_mib,
local_cache_keep_minutes, local_kv_cache and the person's local_cache_over_cap_ok.

The size of the prompt cache (local_cache_ram_mib) and the KV-cache type (local_kv_cache) are the person's alone: an AI
(MCP update_settings) cannot change them at all, since pinning "auto" to a number freezes a value that may no longer
fit. The keep time stays tighten-only: an AI may only shorten it, "auto" counting as this computer's probe value,
never above the soft cap and never to 0 minutes (never unload). Above the hard limit is refused for everyone; the
person gets one warning above the soft cap. get_settings shows the values, the probe values and the caps (numbers
only).
"""

import pytest

from orchestrator.llm.settings_change import (AI_SETTINGS, PERSON_ONLY_SETTINGS, TIGHTEN_ONLY, AISettingsChange,
                                              cache_value, direction_problem)
from orchestrator.runtime.hardware import CacheBudget

BUDGET = CacheBudget(ram_mib=1024, keep_minutes=10, kv_cache="f16",
                     auto={"local_cache_ram_mib": 1024, "local_cache_keep_minutes": 10, "local_kv_cache": "f16"},
                     soft_caps={"local_cache_ram_mib": 1024, "local_cache_keep_minutes": 60},
                     hard_limits={"local_cache_ram_mib": 6000, "local_cache_keep_minutes": 1440})
AUTO = dict(BUDGET.auto)


class Config:
    def __init__(self, **values):
        self.values = dict(values)
        self.saved = 0
        self.config_dir = None

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True

    def save_user_config(self):
        self.saved += 1
        return True


def test_the_settings_are_registered_where_the_rules_live():
    for key in ("local_cache_ram_mib", "local_cache_keep_minutes", "local_kv_cache"):
        assert key in AI_SETTINGS
    assert TIGHTEN_ONLY["local_cache_keep_minutes"] == "lower"
    # changed on purpose: the size and the KV-cache type left the tighten-only table, only the person sets them
    for key in ("local_cache_ram_mib", "local_kv_cache", "local_cache_over_cap_ok"):
        assert key in PERSON_ONLY_SETTINGS and key not in TIGHTEN_ONLY


@pytest.mark.parametrize("key, old, new, refused", [
    # changed on purpose: the size and the KV-cache type are refused for an AI in every direction, also downwards
    ("local_cache_ram_mib", "auto", "512", True),
    ("local_cache_ram_mib", "auto", "2048", True),
    ("local_cache_ram_mib", "512", "auto", True),
    ("local_cache_ram_mib", "2048", "auto", True),
    ("local_cache_ram_mib", "512", "0", True),
    ("local_cache_keep_minutes", "auto", "5", False),
    ("local_cache_keep_minutes", "auto", "0", True),      # never unload: the person's alone
    ("local_cache_keep_minutes", "0", "30", False),       # from never to 30 minutes is lower
    ("local_cache_keep_minutes", "5", "30", True),
    ("local_kv_cache", "auto", "q8_0", True),
    ("local_kv_cache", "q4_0", "q8_0", True),
    ("local_kv_cache", "q8_0", "auto", True),
])
def test_an_ai_may_only_make_the_budget_smaller(key, old, new, refused):
    assert bool(direction_problem(key, old, new, AUTO)) is refused


def test_without_the_probe_value_auto_cannot_be_compared():
    assert direction_problem("local_cache_keep_minutes", "auto", "5", None)
    assert direction_problem("local_cache_keep_minutes", "5", "auto", None)
    assert direction_problem("local_cache_keep_minutes", "30", "5", None) is None


def test_values_never_reach_no_limit():
    for value in ("-1", "1.5", "lots", ""):
        if value == "":
            assert cache_value("local_cache_ram_mib", value) == "auto"
            continue
        with pytest.raises(ValueError):
            cache_value("local_cache_ram_mib", value)
    assert cache_value("local_kv_cache", "Q8_0") == "q8_0"
    with pytest.raises(ValueError):
        cache_value("local_kv_cache", "q5_1")


def _change(by_ai, **values):
    return AISettingsChange(preset=None, cache_budget=BUDGET, by_ai=by_ai, **values)


def test_hard_limit_for_everyone_soft_cap_only_for_an_ai():
    over_hard = {"cache_ram_mib": "7000"}
    assert [p.code for p in _change(False, **over_hard).cache_problems()] == ["cache_hard_limit:local_cache_ram_mib"]
    assert [p.code for p in _change(True, **over_hard).cache_problems()] == ["cache_hard_limit:local_cache_ram_mib"]
    over_soft = {"cache_ram_mib": "2048"}
    assert _change(False, **over_soft).cache_problems() == []
    assert [p.code for p in _change(True, **over_soft).cache_problems()] == ["cache_soft_cap:local_cache_ram_mib"]
    warnings = _change(False, **over_soft).cache_warnings()
    assert len(warnings) == 1 and "soft cap of 1024" in warnings[0]
    assert _change(False, cache_over_cap_ok=True, **over_soft).cache_warnings() == []
    never = _change(True, cache_keep_minutes="0").cache_problems()
    assert [p.code for p in never] == ["cache_person_only:local_cache_keep_minutes"]
    assert _change(False, cache_keep_minutes="0").cache_problems() == []
    assert len(_change(False, cache_keep_minutes="0").cache_warnings()) == 1


def test_an_ai_without_the_budget_is_refused():
    change = AISettingsChange(preset=None, cache_ram_mib="256", by_ai=True)
    assert [p.code for p in change.cache_problems()] == ["cache_unchecked:local_cache_ram_mib"]
    assert AISettingsChange(preset=None, cache_ram_mib="auto", by_ai=True).cache_problems() == []


def test_apply_cache_writes_only_the_budget():
    config = Config(ai_provider="openai", ai_model="m")
    saved = AISettingsChange(preset=None, cache_ram_mib="512", kv_cache="q8_0").apply_cache(config)
    assert saved is True and config.values == {"ai_provider": "openai", "ai_model": "m",
                                                "local_cache_ram_mib": "512", "local_kv_cache": "q8_0"}


def test_with_setting_checks_the_value():
    change = AISettingsChange(preset=None)
    assert change.with_setting("local_cache_keep_minutes", " 15 ").cache_keep_minutes == "15"
    with pytest.raises(ValueError):
        change.with_setting("local_cache_keep_minutes", "-1")
    # changed on purpose: the size and the KV-cache type are the person's alone, like the acceptance
    for key, value in (("local_cache_over_cap_ok", True), ("local_cache_ram_mib", "512"), ("local_kv_cache", "q8_0")):
        with pytest.raises(PermissionError):
            change.with_setting(key, value)


# --- over MCP ----------------------------------------------------------------------------------------------------

mcp = pytest.importorskip("mcp")

from orchestrator.tools import tools_status  # noqa: E402
from orchestrator.tools.policy import ToolPolicy  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError  # noqa: E402


@pytest.fixture
def fw(booted_framework, monkeypatch):
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    monkeypatch.setattr(tools_status, "cache_budget", lambda framework: BUDGET)
    return booted_framework


def _call(fw, changes):
    return fw.tool_registry.call("update_settings", {"changes": changes},
                                 ToolContext("ditto", session="s1", confirm=lambda s, i: True))


def test_get_settings_shows_probe_values_and_caps(fw):
    out = fw.tool_registry.call("get_settings", {"keys": ["local_cache_ram_mib", "local_kv_cache"]},
                                ToolContext("ditto", session="s1"))
    rows = {row.key: row for row in out.settings}
    ram = rows["local_cache_ram_mib"]
    assert ram.value == "auto" and ram.probe_default == 1024 and ram.writable == "read_only"
    assert "only the person" in ram.allowed.lower() and "no longer fit" in ram.allowed
    assert ram.soft_cap == 1024 and ram.hard_limit == 6000
    assert rows["local_kv_cache"].probe_default == "f16"


def test_an_ai_shortens_the_keep_time_and_cannot_touch_size_or_kv_type(fw):
    """Changed on purpose: an AI no longer lowers local_cache_ram_mib or local_kv_cache (person-only); it may still
    shorten the keep time."""
    out = _call(fw, [{"key": "local_cache_keep_minutes", "value": "5"}])
    assert [a.key for a in out.applied] == ["local_cache_keep_minutes"]
    assert fw.config.get("local_cache_keep_minutes") == "5"
    for change in ({"key": "local_cache_keep_minutes", "value": "30"},
                   {"key": "local_cache_keep_minutes", "value": "0"}):
        with pytest.raises(ToolError):
            _call(fw, [change])
    for change in ({"key": "local_cache_ram_mib", "value": "512"},
                   {"key": "local_cache_ram_mib", "value": "4096"},
                   {"key": "local_kv_cache", "value": "q8_0"},
                   {"key": "local_kv_cache", "value": "f16"}):
        with pytest.raises(ToolError) as refused:
            _call(fw, [change])
        assert change["key"] in str(refused.value) and "no longer fit" in str(refused.value)
    assert fw.config.get("local_cache_ram_mib") == "auto" and fw.config.get("local_kv_cache") == "auto"


def test_an_ai_cannot_change_the_idle_timeout_over_mcp(fw):
    before = fw.config.get("build_idle_timeout")
    for value in ("600", "7200"):
        with pytest.raises(ToolError) as refused:
            _call(fw, [{"key": "build_idle_timeout", "value": value}])
        assert "build_idle_timeout" in str(refused.value) and "long builds" in str(refused.value)
    assert fw.config.get("build_idle_timeout") == before


def test_the_person_only_acceptance_is_no_key_for_an_ai(fw):
    with pytest.raises(Exception):
        _call(fw, [{"key": "local_cache_over_cap_ok", "value": "true"}])
    assert "local_cache_over_cap_ok" not in tools_status.READABLE
