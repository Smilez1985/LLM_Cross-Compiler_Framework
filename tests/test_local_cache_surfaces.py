"""The memory budget of Ditto's local server on every surface (chain ctx-local-mem): `llmcf ditto cache` (show,
--json) and `ditto cache set NAME VALUE [--yes-over-cap]`, the Mojo line for a saved value above the soft cap, and the
Prompt cache group of the GUI (offscreen)."""

import json
import os
from types import SimpleNamespace

import pytest

from test_runtime import fake_runtime  # noqa: F401 - fixture


class Config:
    """Settings as the runtime and the CLI read and write them."""

    def __init__(self, **values):
        self.values = dict(values)
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True

    def save_user_config(self):
        self.saved += 1
        return True


def _cli(rt, monkeypatch, *args, **kw):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
    return CliRunner().invoke(cli_module.cli, ["-q", "ditto", "cache", *args], **kw)


# --- the CLI -------------------------------------------------------------------------------------------------------

def test_show_lists_values_caps_and_reasons(fake_runtime, monkeypatch):  # noqa: F811
    rt = fake_runtime()
    rt.settings = Config()
    out = _cli(rt, monkeypatch, "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    rows = {row["key"]: row for row in data["settings"]}
    assert rows["local_cache_ram_mib"]["saved"] == "auto" and rows["local_cache_ram_mib"]["used"] == 2048
    assert rows["local_cache_ram_mib"]["soft_cap"] == 2048 and rows["local_cache_ram_mib"]["hard_limit"] > 2048
    assert rows["local_kv_cache"]["auto"] == "f16" and data["running"] == {}
    assert json.loads(_cli(rt, monkeypatch, "show", "--json").output) == data
    table = _cli(rt, monkeypatch)
    assert table.exit_code == 0 and "local_cache_keep_minutes" in table.output


def test_set_saves_only_that_value(fake_runtime, monkeypatch):  # noqa: F811
    rt = fake_runtime()
    rt.settings = Config(ai_provider="openai")
    out = _cli(rt, monkeypatch, "set", "kv", "q8_0")
    assert out.exit_code == 0, out.output
    assert rt.settings.values == {"ai_provider": "openai", "local_kv_cache": "q8_0"} and rt.settings.saved == 1


def test_set_refuses_the_hard_limit_and_no_limit(fake_runtime, monkeypatch):  # noqa: F811
    rt = fake_runtime()
    rt.settings = Config()
    for value in ("999999", "-1", "lots"):
        out = _cli(rt, monkeypatch, "set", "local_cache_ram_mib", value)
        assert out.exit_code == 2, (value, out.output)
    assert rt.settings.values == {}


def test_above_the_soft_cap_asks_once_with_no_as_default(fake_runtime, monkeypatch):  # noqa: F811
    rt = fake_runtime()
    rt.settings = Config()
    out = _cli(rt, monkeypatch, "set", "ram", "4096", input="\n")
    assert out.exit_code == 0 and "soft cap" in out.output and "Nothing was changed" in out.output
    assert rt.settings.values == {}
    out = _cli(rt, monkeypatch, "set", "ram", "4096", "--yes-over-cap")
    assert out.exit_code == 0 and rt.settings.values == {"local_cache_ram_mib": "4096"}
    rt.settings.values["local_cache_over_cap_ok"] = True
    out = _cli(rt, monkeypatch, "set", "keep", "0")   # never unload: the person may, without a question now
    assert out.exit_code == 0 and "soft cap" not in out.output and rt.settings.values["local_cache_keep_minutes"] == "0"


def test_set_over_cap_ok_refuses_a_word_it_does_not_know(fake_runtime, monkeypatch):  # noqa: F811
    """Review ctx-local-mem-v: 'tru' or 'auto' silently saved false before; only true/false words are taken."""
    rt = fake_runtime()
    rt.settings = Config(local_cache_over_cap_ok=True)
    for value in ("tru", "auto", "maybe"):
        out = _cli(rt, monkeypatch, "set", "over-cap-ok", value)
        assert out.exit_code == 2, (value, out.output)
    assert rt.settings.values == {"local_cache_over_cap_ok": True}
    out = _cli(rt, monkeypatch, "set", "over-cap-ok", "no")
    assert out.exit_code == 0, out.output
    assert rt.settings.values == {"local_cache_over_cap_ok": False}


# --- Mojo -----------------------------------------------------------------------------------------------------------

def test_mojo_names_a_saved_value_above_the_soft_cap(monkeypatch):
    from orchestrator.Core.mojo.checks import runtime as check
    from orchestrator.runtime import llama_server as ls
    from orchestrator.runtime.hardware import CacheBudget
    budget = CacheBudget(ram_mib=4096, keep_minutes=10, soft_caps={"local_cache_ram_mib": 1024},
                         hard_limits={"local_cache_ram_mib": 8000}, over_soft_cap=["local_cache_ram_mib"])
    monkeypatch.setattr(ls.LocalLLMRuntime, "from_framework",
                        classmethod(lambda cls, fw, **kw: SimpleNamespace(cache_budget=lambda: budget)))
    framework = SimpleNamespace(config=Config(local_cache_ram_mib="4096"))
    found = check._cache_budget(framework)
    assert [f.code for f in found] == ["runtime.cache.over_cap"] and "1024" in found[0].message
    assert found[0].steps[0].id == "cache.auto" and "llm-cli" not in found[0].fix_hint
    framework.config.values["local_cache_over_cap_ok"] = True
    assert check._cache_budget(framework) == []
    assert check._cache_budget(SimpleNamespace(config=Config())) == [], "auto everywhere: nothing to say"


# --- the GUI --------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="the stand-in runtime is the POSIX fixture")
def test_prompt_cache_box_applies_through_the_one_rule_set(fake_runtime, monkeypatch, qapp):  # noqa: F811
    pytest.importorskip("PySide6")
    from orchestrator.gui import prompt_cache_box as box_module
    from orchestrator.runtime.ditto_models import cache_overview
    rt = fake_runtime()
    rt.settings = Config(local_kv_cache="q8_0")
    asked = []
    monkeypatch.setattr(box_module, "ask", lambda parent, title, text: asked.append(text) or False)
    monkeypatch.setattr(box_module, "plain_box", lambda parent, kind, title, text: asked.append((kind, text)))
    box = box_module.PromptCacheBox(SimpleNamespace(config=rt.settings), runtime_factory=lambda: rt, lang="en")
    assert box.objectName() == "grp_prompt_cache" and box.cb_kv.currentData() == "q8_0"
    box.show_view(cache_overview(rt))   # what the worker thread hands back
    assert "local_cache_ram_mib" in box.lbl_info.text()
    box.txt_ram.setText("4096")         # above the soft cap of 2048: asked, "No"
    assert box.apply() is False and asked and "soft cap" in asked[-1]
    assert "local_cache_ram_mib" not in rt.settings.values
    box.txt_ram.setText("999999")       # above the hard limit: refused
    assert box.apply() is False and asked[-1][0] == "warning"
    box.txt_ram.setText("512")
    assert box.apply() is True and rt.settings.values["local_cache_ram_mib"] == "512"
    box.deleteLater()


def test_both_windows_carry_the_group():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "orchestrator" / "gui"
    assert "PromptCacheBox" in (root / "dialogs.py").read_text(encoding="utf-8")
    assert "PromptCacheBox" in (root / "ditto_models_window.py").read_text(encoding="utf-8")
