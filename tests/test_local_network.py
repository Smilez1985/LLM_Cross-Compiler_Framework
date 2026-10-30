"""The person's opt-outs of offline mode's locality rule (DECISIONS round 24, "L-NET final", rule d;
orchestrator/Core/local_network.py, `llmcf config local-network`, File > Settings... > Local network).

The person counts a VPN, a host or an address range as local, each only after reading what can then leave the
network. Only the person: AISettingsChange and MCP update_settings refuse the settings, no AI tool or GUI-tool field
reaches them, and no module an AI can reach imports the writer."""

import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from orchestrator.Core import local_network as ln
from orchestrator.utils import locality
from orchestrator.utils.locality import OPT_OUT_SETTINGS, VpnCheck

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    locality.clear_cache()
    monkeypatch.setattr(locality, "_system_lookup",
                        lambda host: {"myhome.duckdns.org": ["198.51.100.7"], "nas.lan": ["192.168.1.20"]}[host])
    monkeypatch.setattr(locality, "tailscale_vpn", lambda *a, **k: VpnCheck(False, "no_cli"))
    monkeypatch.setattr(ln, "tailscale_vpn", lambda *a, **k: VpnCheck(False, "no_cli"))
    yield
    locality.clear_cache()


@pytest.fixture
def config(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    folder = tmp_path / "cfg"
    folder.mkdir()
    return ConfigManager(folder)


def saved(config):
    path = Path(config.config_dir) / "config.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


# --- the core -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, value, stored", [
    ("vpn", "Tailscale", "tailscale"), ("host", "MyHome.DuckDNS.org.", "myhome.duckdns.org"),
    ("host", "nas", "nas"), ("host", "bücher.example", "xn--bcher-kva.example"),
    ("range", "203.0.113.7/24", "203.0.113.0/24"), ("range", "2001:db8::1", "2001:db8::1/128")])
def test_values_are_stored_in_one_form(kind, value, stored):
    assert ln.normalize(kind, value) == stored


@pytest.mark.parametrize("kind, value, code", [
    ("vpn", "wireguard", "vpn"), ("host", "bad host", "host"), ("host", "-x.example", "host"),
    ("host", "192.168.1.5", "host_is_address"), ("range", "10.0.0.0/33", "range"), ("thing", "x", "kind")])
def test_what_is_not_an_opt_out_is_refused(kind, value, code):
    with pytest.raises(ln.LocalNetworkError) as info:
        ln.normalize(kind, value)
    assert info.value.code == code


def test_each_opt_out_says_what_can_then_leave_the_network():
    assert "whatever addresses it resolves to" in ln.explain("host", "myhome.duckdns.org", "en")
    assert "leave your network" in ln.explain("host", "myhome.duckdns.org", "en")
    assert "203.0.113.0/24" in ln.explain("range", "203.0.113.0/24", "en")
    assert "relays outside your network" in ln.explain("vpn", "tailscale", "en")
    assert "verlassen" in ln.explain("host", "x.example", "de")


def test_add_list_and_remove_save_config_yml(config):
    assert ln.opt_outs(config) == []
    added = ln.add_opt_out(config, "host", "MyHome.duckdns.org")
    assert added == ln.OptOut("host", "myhome.duckdns.org")
    ln.add_opt_out(config, "host", "myhome.duckdns.org")      # twice: once
    ln.add_opt_out(config, "range", "203.0.113.0/24")
    ln.add_opt_out(config, "vpn", "tailscale")
    assert [(o.kind, o.value) for o in ln.opt_outs(config)] == [("vpn", "tailscale"), ("host", "myhome.duckdns.org"),
                                                                ("range", "203.0.113.0/24")]
    assert saved(config)["offline_local_hosts"] == ["myhome.duckdns.org"]
    assert saved(config)["offline_local_vpns"] == ["tailscale"]
    assert ln.check(config, "https://myhome.duckdns.org/v1").reason == "opt_out_host"
    assert ln.remove_opt_out(config, "host", "MYHOME.duckdns.org") is True
    assert ln.remove_opt_out(config, "host", "myhome.duckdns.org") is False
    assert saved(config)["offline_local_hosts"] == []
    assert not ln.check(config, "https://myhome.duckdns.org/v1").local


def test_nothing_is_kept_when_config_yml_cannot_be_written(config, monkeypatch):
    monkeypatch.setattr(config, "save_user_config", lambda: False)
    with pytest.raises(ln.LocalNetworkError) as info:
        ln.add_opt_out(config, "host", "nas.example")
    assert info.value.code == "not_saved" and ln.opt_outs(config) == []


def test_an_opt_out_starts_the_checks_again(config, monkeypatch):
    calls = []
    monkeypatch.setattr(locality, "clear_cache", lambda: calls.append(1))
    ln.add_opt_out(config, "vpn", "tailscale")
    assert calls == [1]


def test_check_and_vpn_texts(config):
    text = ln.check_text(ln.check(config, "nas.lan"), "en")
    assert text.startswith("nas.lan counts as local: nas.lan is in your own network.") and "192.168.1.20" in text
    assert "does not count as local" in ln.check_text(ln.check(config, "8.8.8.8"), "en")
    assert "zählt nicht als lokal" in ln.check_text(ln.check(config, "8.8.8.8"), "de")
    assert "not installed here" in ln.vpn_text(VpnCheck(False, "no_cli"), "en")


# --- the command line -----------------------------------------------------------------------------------------------

def invoke(config, *args, input=None):
    from orchestrator import cli as cli_module
    framework = SimpleNamespace(config=config)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", "config", "local-network", *args], obj=Ctx(), input=input)


def test_cli_add_asks_with_the_explanation_and_no_keeps_everything(config):
    result = invoke(config, "--add", "host", "myhome.duckdns.org", input="n\n")
    assert result.exit_code == 0, result.output
    assert "leave your network" in result.output and "Nothing changed" in result.output
    assert ln.opt_outs(config) == []
    result = invoke(config, "--add", "host", "myhome.duckdns.org", input="y\n")
    assert result.exit_code == 0 and "now counts as local" in result.output
    assert ln.opt_outs(config) == [ln.OptOut("host", "myhome.duckdns.org")]


def test_cli_list_check_and_remove(config):
    assert invoke(config, "--add", "vpn", "tailscale", "--yes").exit_code == 0
    listing = invoke(config)
    assert listing.exit_code == 0 and "Tailscale or Headscale" in listing.output
    assert "not installed here" in listing.output, "the VPN check of this computer"
    checked = invoke(config, "--check", "100.64.1.2")
    assert "counts as local" in checked.output and "you count the VPN" in checked.output
    assert "does not count as local" in invoke(config, "--check", "http://8.8.8.8/v1").output
    assert "no longer counts" in invoke(config, "--remove", "vpn", "tailscale").output
    assert "was not counted" in invoke(config, "--remove", "vpn", "tailscale").output


def test_cli_refuses_what_is_no_opt_out_and_two_actions(config):
    bad = invoke(config, "--add", "host", "192.168.1.5", "--yes")
    assert bad.exit_code == 2 and "address range" in bad.output
    assert invoke(config, "--add", "host", "x", "--check", "y").exit_code != 0


# --- only the person ------------------------------------------------------------------------------------------------

def test_ai_settings_change_refuses_the_opt_outs():
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, AISettingsChange
    from orchestrator.utils.locality import BUILD_SOURCE_SETTINGS
    # Round 36 (on purpose): the build sources in the own network are the person's too.
    assert set(PERSON_ONLY_SETTINGS) == set(OPT_OUT_SETTINGS) | set(BUILD_SOURCE_SETTINGS)
    for key in OPT_OUT_SETTINGS:
        problems = AISettingsChange.problems_of_settings({}, None, {key: ["anything"]})
        assert [p.code for p in problems] == ["person_only"] and "Only the person" in problems[0].message
        change = AISettingsChange(SimpleNamespace(base_url_configurable=False))
        with pytest.raises(PermissionError):
            change.with_setting(key, ["x"])


def test_mcp_update_settings_can_neither_write_nor_read_them():
    from orchestrator.tools import tools_status
    from orchestrator.utils.locality import BUILD_SOURCE_SETTINGS
    person = set(OPT_OUT_SETTINGS) | set(BUILD_SOURCE_SETTINGS)
    assert not person & set(tools_status.WRITABLE)
    assert not person & set(tools_status.READABLE), "an AI does not even read the person's hosts or mirrors"
    assert tools_status.oversight_problems() == []
    assert tools_status.oversight_problems(tools_status.WRITABLE + ("offline_local_hosts",)) == ["offline_local_hosts"]
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        tools_status.UpdateSettingsIn(changes=[{"key": "offline_local_hosts", "value": "x"}])


def test_the_mcp_tool_refuses_them_also_when_bound(monkeypatch):
    """Even a bound change that names one (it cannot pass the input model) is refused by the handler."""
    from orchestrator.tools import tools_status
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: d, set=lambda k, v: pytest.fail(k),
                                                       save_user_config=lambda: True))
    specs = tools_status.status_tool_specs(SimpleNamespace(audit=None), framework, [])
    spec = next(s for s in specs if s.name == "update_settings")
    facts = SimpleNamespace(bound={"changes": [{"key": "offline_local_ranges", "old": "-", "new": ["0.0.0.0/0"]}]})
    monkeypatch.setattr(tools_status, "ai_settings_problem", lambda fw, values: None)
    out = spec.handler(SimpleNamespace(changes=[]), SimpleNamespace(facts=facts))
    assert out.applied == [] and out.rejected[0].key == "offline_local_ranges"


AI_ROOTS = ("orchestrator.tools.", "orchestrator.llm.", "orchestrator.mcp.")


def test_no_module_an_ai_can_reach_imports_the_writer():
    import test_layering
    from test_remote_trust import ai_reachable_closure, path_to
    via = ai_reachable_closure(test_layering.build_graph().edges())
    assert "orchestrator.tools.tools_status" in via and "orchestrator.utils.locality" in via, "the rule is shared"
    assert "orchestrator.Core.local_network" not in via, path_to(via, "orchestrator.Core.local_network")


def test_no_ai_reachable_code_writes_or_names_the_opt_out_writers():
    from test_deploy_tools import AI_REACHABLE
    forbidden = {"add_opt_out", "remove_opt_out", "local_network"}
    offenders = []
    for entry in AI_REACHABLE:
        path = REPO_ROOT / entry
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Attribute, ast.Name)):
                    name = node.attr if isinstance(node, ast.Attribute) else node.id
                    if name in forbidden:
                        offenders.append(f"{file.relative_to(REPO_ROOT)}:{node.lineno} {name}")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                    names += [a.name for a in node.names]
                    offenders += [f"{file.relative_to(REPO_ROOT)}:{node.lineno} {n}" for n in names
                                  if n.endswith("local_network")]
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set" \
                        and any(isinstance(a, ast.Constant) and a.value in OPT_OUT_SETTINGS for a in node.args):
                    offenders.append(f"{file.relative_to(REPO_ROOT)}:{node.lineno} sets {node.args[0].value}")
    assert offenders == []


def test_no_gui_tool_field_reaches_the_opt_outs():
    """Ditto's GUI tools fill only bound form fields (gui/form_surface.py); no binding names the local network."""
    for path in sorted((REPO_ROOT / "orchestrator" / "gui").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "FormBinding(" in text:
            assert "net_" not in "".join(line for line in text.splitlines() if ".add(" in line or "b.line(" in line
                                         or "b.check(" in line or "b.combo(" in line), path
    source = (REPO_ROOT / "orchestrator" / "gui" / "settings_window.py").read_text(encoding="utf-8")
    assert "FormBinding" not in source and "gui_form" not in source


# --- the window -----------------------------------------------------------------------------------------------------

def wait_for(predicate, qapp, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_the_window_counts_a_host_as_local_only_after_the_explanation(qapp, config, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import settings_window
    asked = []
    answer = [False]
    monkeypatch.setattr(settings_window, "ask", lambda parent, title, text: asked.append(text) or answer[0])
    dialog = settings_window.SettingsDialog(SimpleNamespace(config=config), lang="en")
    page = dialog.findChild(type(dialog.page_net), "tab_settings_local_network")
    assert page is dialog.page_net and dialog.table_net.rowCount() == 0 and not dialog.lbl_net_empty.isHidden()
    dialog.combo_net_kind.setCurrentIndex(dialog.combo_net_kind.findData("host"))
    dialog.edit_net_value.setText("myhome.duckdns.org")
    dialog.btn_net_add.click()
    assert "leave your network" in asked[-1] and ln.opt_outs(config) == [], "No keeps everything"
    answer[0] = True
    dialog.btn_net_add.click()
    assert ln.opt_outs(config) == [ln.OptOut("host", "myhome.duckdns.org")]
    assert dialog.table_net.rowCount() == 1 and dialog.table_net.item(0, 1).text() == "myhome.duckdns.org"
    assert "whatever addresses" in dialog.table_net.item(0, 2).text()
    dialog.table_net.selectRow(0)
    answer[0] = False
    dialog.btn_net_remove.click()
    assert ln.opt_outs(config) != [] and "no longer count" in asked[-1]
    answer[0] = True
    dialog.btn_net_remove.click()
    assert ln.opt_outs(config) == [] and dialog.table_net.rowCount() == 0
    dialog.close()


def test_the_window_checks_in_a_worker_thread(qapp, config, monkeypatch):
    pytest.importorskip("PySide6")
    import threading
    from orchestrator.gui import settings_window
    threads = []
    real = ln.check

    def check(cfg, target):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(cfg, target)
    monkeypatch.setattr(ln, "check", check)
    dialog = settings_window.SettingsDialog(SimpleNamespace(config=config), lang="en")
    assert not dialog._vpn_checked, "the VPN is checked when the tab is shown, not before"
    dialog.tabs.setCurrentWidget(dialog.page_net)
    assert wait_for(lambda: "not installed here" in dialog.lbl_net_vpn.text(), qapp)
    dialog.edit_net_check.setText("nas.lan")
    dialog.btn_net_check.click()
    assert wait_for(lambda: "counts as local" in dialog.lbl_net_result.text(), qapp)
    assert threads == [False], "the lookup ran in a worker thread"
    dialog.close()
