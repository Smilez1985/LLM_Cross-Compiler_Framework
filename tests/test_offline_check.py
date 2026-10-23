"""The one offline check (consolidation chain offline-lan, item f from the stage C wave 3 reviews).

About ten places read the setting offline_mode, each with its own small getter (hf_hub.offline, crawl_jobs, the
knowledge base, the build service and build plan, Ditto, the community, target and runtime tools, the module wizard,
the LLM client). They delegate to utils/locality.offline_mode now; behaviour and texts are the same. The semantics
pinned here are the ones every reader had: a ConfigManager, a dict or a stand-in with get(); an object with an
offline_mode attribute; anything unreadable counts as "not offline".
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.utils.locality import offline_mode

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "orchestrator"


class Raising:
    def get(self, key, default=None):
        raise RuntimeError("locked")


@pytest.mark.parametrize("config, expected", [
    ({"offline_mode": True}, True), ({"offline_mode": False}, False), ({}, False), ({"offline_mode": None}, False),
    ({"offline_mode": 1}, True), (None, False), (Raising(), False), (SimpleNamespace(offline_mode=True), True),
    (SimpleNamespace(), False), (object(), False)])
def test_the_one_offline_check(config, expected):
    assert offline_mode(config) is expected


def test_hf_hub_offline_is_the_same_check():
    """hf_hub.offline was the best of the copies (R0): its name stays, it delegates."""
    from orchestrator.Core.hf_hub import offline
    for config in ({"offline_mode": True}, {"offline_mode": False}, {}, Raising(), None):
        assert offline(config) is offline_mode(config)


def test_a_config_manager_is_read_live(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    config = ConfigManager(tmp_path)
    assert offline_mode(config) is False
    config.set("offline_mode", True)
    assert offline_mode(config) is True


def test_the_readers_ask_the_one_check(monkeypatch):
    """Ditto's property, the knowledge base's model guard and the LLM client follow the helper (a stand-in config that
    only the helper reads as offline: an object without get() but with the attribute)."""
    from orchestrator.Core.ditto_manager import DittoCoder
    ditto = DittoCoder.__new__(DittoCoder)
    ditto.config = SimpleNamespace(offline_mode=True)
    assert ditto.offline_mode is True
    ditto.config = {"offline_mode": False}
    assert ditto.offline_mode is False


# --- nothing reads the setting on its own any more ------------------------------------------------------------------

READERS = {"get", "_cfg", "_config", "_config_get", "_flag", "_setting", "user_value"}
# Reads that stay: the helper itself; the settings that show or change the value as such (the AI settings dialog's
# change object and its overlay of unsaved choices pass the value through, they do not decide offline behaviour).
ALLOWED = {"orchestrator/utils/locality.py"}


def offline_reads():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in READERS and any(isinstance(a, ast.Constant) and a.value == "offline_mode" for a in node.args):
                found.append(f"{rel}:{node.lineno}")
    return found


def test_no_module_reads_offline_mode_on_its_own():
    """Every reader goes through utils/locality.offline_mode (deprecate-and-delegate); a new getter of its own fails
    here."""
    assert offline_reads() == []


def test_the_guard_bites(tmp_path, monkeypatch):
    sample = PACKAGE / "Core" / "_offline_guard_sample.py"
    sample.write_text("def f(cfg):\n    return bool(cfg.get('offline_mode', False))\n", encoding="utf-8")
    try:
        assert offline_reads() == ["orchestrator/Core/_offline_guard_sample.py:2"]
    finally:
        sample.unlink()
