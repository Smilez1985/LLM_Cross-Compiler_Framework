"""Device account names and device paths never reach an AI (study "WOA-L, bus, mojo, recipes, llmcf" 6.2, D0).

The account the framework logs in with on a board ("dietpi", "orangepi", ...) and paths on the board identify the
person. list_devices says "device account"; the login (account and address) is a fact only the person sees, in the
framework's own window; every text from or about a device (its answers, check output, errors, also on success) is
scrubbed of the account's name, its home folder and paths before an AI reads it. A source guard keeps every AI-facing
fact and consequence free of the account name.
"""

import ast
import json

import pytest

from conftest import REPO_ROOT
from orchestrator.tools.confirmation import MAX_ELICITATION
from orchestrator.tools.delegation import ai_view
from orchestrator.tools.registry import ToolContext
from orchestrator.tools.tools_runtime import DEVICE_ACCOUNT, device_text
from test_deploy_tools import (FakeDeploy, asking, build, call, fw, online, package, roomy_disk,  # noqa: F401
                               trust, wait_job)

ACCOUNT = "dietpi"
ADDRESS = "192.0.2.10"   # a documentation address (RFC 5737)
TOOL_FILES = ("orchestrator/tools/tools_deploy.py", "orchestrator/tools/tools_runtime.py")


@pytest.fixture
def fake_deploy(fw, monkeypatch):  # noqa: F811
    fake = FakeDeploy(output=[f"installed /home/{ACCOUNT}/models/qwen.gguf for {ACCOUNT}",
                              f"chown {ACCOUNT}:{ACCOUNT} ~{ACCOUNT}/models"])
    manager = fw.deployment_manager if getattr(fw, "deployment_manager", None) is not None \
        else fw.get_component("deployment_manager")
    monkeypatch.setattr(manager, "deploy_package", fake)
    return fake


def test_list_devices_never_names_the_device_account(fw):  # noqa: F811
    device, _ = trust(fw, host=ADDRESS, user=ACCOUNT)
    out = fw.tool_registry.call("list_devices", {}, ToolContext("mcp"))
    (row,) = out.devices
    assert row.user == DEVICE_ACCOUNT and row.label == "Living room board"
    assert ACCOUNT not in out.model_dump_json(), "the account's name stays with the person"


def test_a_question_a_client_may_read_never_shows_the_login(fw, online):  # noqa: F811
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    online["key"] = key
    pkg = package(fw, build(fw))
    registry = fw.tool_registry
    spec = registry.get("deploy_to_device")
    args = {"package_id": pkg["package_id"], "device_id": device.device_id}
    inp = registry.validate_input(spec, args)
    ctx = ToolContext("mcp")
    facts = registry.look_up(spec, inp, ctx)
    form = registry.question_text(spec, args, ctx, facts, MAX_ELICITATION)
    assert ACCOUNT not in form and "Living room board" in form, "an MCP form may be read by an AI"
    assert ACCOUNT not in facts.consequence and ACCOUNT not in json.dumps(ai_view(facts))
    person = registry.question_text(spec, args, ctx, facts)
    assert f"{ACCOUNT}@{ADDRESS}" in person, "the person's own window shows the login"


def test_device_text_scrubs_the_account_its_home_and_paths():
    text = (f"Permission denied for {ACCOUNT}@{ADDRESS}: /home/{ACCOUNT}/models/qwen.gguf; ~{ACCOUNT}/.ssh/x; "
            f"uid=1000({ACCOUNT}) /data/{ACCOUNT}")
    clean = device_text(text, [ACCOUNT])
    assert ACCOUNT not in clean, clean
    assert f"{DEVICE_ACCOUNT}@{ADDRESS}" in clean and "qwen.gguf" in clean and "/home" not in clean
    assert device_text("Ubuntu 24.04 on a Raspberry Pi", ["pi"]) == "Ubuntu 24.04 on a Raspberry Pi", \
        "a word that only contains the account's letters stays"
    assert device_text("no account here", []) == "no account here"


def test_the_deploy_output_and_reason_never_carry_the_account_also_on_success(fw, online, fake_deploy):  # noqa: F811
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    online["key"] = key
    pkg = package(fw, build(fw))
    started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                   confirm=asking(True))
    assert wait_job(fw, started.job_id).state == "done"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    assert status.external_text and all(ACCOUNT not in line for line in status.external_text), status.external_text
    assert any("qwen.gguf" in line and DEVICE_ACCOUNT in line for line in status.external_text)
    assert ACCOUNT not in status.model_dump_json()


def test_a_board_job_error_never_names_the_account(fw, monkeypatch):  # noqa: F811
    from orchestrator.runtime.device import service
    from orchestrator.utils import ssh_trust
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)

    def refuse(d, s, fp, on_client=None):
        raise service.DeviceRuntimeError(f"The framework's SSH key does not log in as {ACCOUNT}.",
                                         external_text=[f"/home/{ACCOUNT}/.ssh/authorized_keys: denied"])
    monkeypatch.setattr(service, "probe", refuse)
    from orchestrator.tools.policy import Decision, ToolPolicy
    fw.tool_registry.policy_store.replace(ToolPolicy(configured=True, overrides={"probe_remote_device": Decision.ASK},
                                                     reviewed=[s.name for s in fw.tool_registry.list()]))
    confirm = asking(True)
    started = call(fw, "probe_remote_device", {"device_id": device.device_id}, confirm=confirm)
    (question,) = confirm.asked
    assert f"{ACCOUNT}@{ADDRESS}" in question, "the person sees the login in the framework's window"
    job = wait_job(fw, started.job_id)
    assert job.state == "failed"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    assert ACCOUNT not in status.model_dump_json(), status.model_dump_json()


def test_an_unexpected_deploy_error_keeps_its_text_from_the_ai(fw, online, fake_deploy, monkeypatch):  # noqa: F811
    """The device scrub applies to the deploy's own refusals (a ValueError, its words meant for the AI); an unexpected
    exception's text never reaches an AI at all (job service rule), scrubbed or not."""
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    online["key"] = key
    pkg = package(fw, build(fw))
    manager = fw.deployment_manager if getattr(fw, "deployment_manager", None) is not None \
        else fw.get_component("deployment_manager")

    def broken(*args, **kwargs):
        raise RuntimeError("internal state dump: token-cache at /srv/internal/cache, peer 203.0.113.7")
    monkeypatch.setattr(manager, "deploy_package", broken)
    started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                   confirm=asking(True))
    job = wait_job(fw, started.job_id)
    assert job.state == "failed"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    text = status.model_dump_json()
    assert "internal state dump" not in text and "203.0.113.7" not in text, text

    def refused(*args, **kwargs):
        from orchestrator.Core.deployment_manager import DeployError
        raise DeployError(f"The device refused the upload for {ACCOUNT} into /home/{ACCOUNT}/models.",
                          code="hardware_unavailable")
    monkeypatch.setattr(manager, "deploy_package", refused)
    started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                   confirm=asking(True))
    assert wait_job(fw, started.job_id).state == "failed"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    assert status.error is not None and status.error.code == "hardware_unavailable"
    assert "refused the upload" in status.error.message and ACCOUNT not in status.model_dump_json()


SETTINGS_ACCOUNT = "kiosk7"   # an account the person chose in the deploy settings, other than the paired login


@pytest.mark.parametrize("tool", ["deploy_device_runtime", "run_device_benchmark"])
def test_a_board_job_error_never_names_the_deploy_settings_account(fw, monkeypatch, tool):  # noqa: F811
    """The board tools log in with the deploy settings' account ('llm-cli device settings DEVICE --user NAME'),
    which may differ from the account the device was paired with: a failed job never names either."""
    from types import SimpleNamespace
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.utils import ssh_trust
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
    settings = DeploySettings(ssh_user=SETTINGS_ACCOUNT, model="models/qwen.gguf")
    monkeypatch.setattr(service, "device_profile", lambda s, d: SimpleNamespace(arch="aarch64"))
    monkeypatch.setattr(service, "deploy_settings", lambda s, d, p: settings)
    monkeypatch.setattr(service, "plan_facts", lambda s, p: service.DevicePlanFacts(
        "b" * 64, True, True, {"os": "linux", "arch": "aarch64", "libc": "glibc"}, {"base": "debian"}))
    monkeypatch.setattr(DeviceRuntimeStore, "record", lambda self: {"build_id": "b" * 64})

    def refuse(*args, **kwargs):
        raise service.DeviceRuntimeError(f"The framework's SSH key does not log in to the board as "
                                         f"{SETTINGS_ACCOUNT} (AuthenticationException).", code="needs_person",
                                         external_text=[f"/home/{SETTINGS_ACCOUNT}/.ssh/authorized_keys: denied"])
    monkeypatch.setattr(service, "deploy" if tool == "deploy_device_runtime" else "benchmark", refuse)
    from orchestrator.tools.policy import Decision, ToolPolicy
    fw.tool_registry.policy_store.replace(ToolPolicy(configured=True, overrides={tool: Decision.ASK},
                                                     reviewed=[s.name for s in fw.tool_registry.list()]))
    confirm = asking(True)
    started = call(fw, tool, {"device_id": device.device_id}, confirm=confirm)
    (question,) = confirm.asked
    assert ACCOUNT in question, "the person sees the login in the framework's window"
    job = wait_job(fw, started.job_id)
    assert job.state == "failed"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    text = status.model_dump_json()
    assert SETTINGS_ACCOUNT not in text and ACCOUNT not in text, text
    assert DEVICE_ACCOUNT in text, "the AI still reads that the device account could not log in"


def test_older_builds_on_the_board_reach_the_ai_as_a_count_and_stay_with_the_person(fw, monkeypatch):  # noqa: F811
    """Round 5 f: a deploy keeps every older runtime build on the board. The AI reads how many stayed and that only the
    person removes them; never their folders or the account."""
    from types import SimpleNamespace
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.tools.tools_runtime import OLDER_BUILDS_NOTE
    from orchestrator.utils import ssh_trust
    device, key = trust(fw, host=ADDRESS, user=ACCOUNT)
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
    monkeypatch.setattr(service, "device_profile", lambda s, d: SimpleNamespace(arch="aarch64"))
    monkeypatch.setattr(service, "deploy_settings", lambda s, d, p: DeploySettings(ssh_user=ACCOUNT))
    monkeypatch.setattr(service, "plan_facts", lambda s, p: service.DevicePlanFacts(
        "b" * 64, True, True, {"os": "linux", "arch": "aarch64", "libc": "glibc"}, {"base": "debian"}))
    monkeypatch.setattr(DeviceRuntimeStore, "record", lambda self: {})
    monkeypatch.setattr(service, "deploy", lambda *a, **k: {
        "activated": True, "build_id": "b" * 64, "install": "user", "previous": "c" * 16, "older_builds_kept": 2,
        "checks": [], "notes": [f"2 older runtime build(s) stay on the device (50.0 MB); see /home/{ACCOUNT}/.local"]})
    from orchestrator.tools.policy import Decision, ToolPolicy
    fw.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                     overrides={"deploy_device_runtime": Decision.ASK},
                                                     reviewed=[s.name for s in fw.tool_registry.list()]))
    started = call(fw, "deploy_device_runtime", {"device_id": device.device_id}, confirm=asking(True))
    assert wait_job(fw, started.job_id).state == "done"
    status = fw.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto"))
    text = status.model_dump_json()
    assert '"older_builds_kept":2' in text.replace(" ", "") and OLDER_BUILDS_NOTE[:40] in text
    assert ACCOUNT not in text and "/home/" not in text, text


# --- the source guard ------------------------------------------------------------------------------------------------

ACCOUNT_ATTRS = frozenset({"user", "ssh_user"})


def _names_account(node) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr in ACCOUNT_ATTRS for n in ast.walk(node))


def _call_name(node) -> str:
    return getattr(node.func, "id", "") or getattr(node.func, "attr", "")


def test_no_ai_facing_fact_or_consequence_interpolates_the_device_account():
    """Every Fact that names `.user` or `.ssh_user` is person_only; no consequence, speaking name of a device, AI
    result row or device text sent to an AI names it; every device output an AI reads passes through device_text."""
    offenders = []
    for rel in TOOL_FILES:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "Fact":
                person_only = any(k.arg == "person_only" and isinstance(k.value, ast.Constant) and k.value.value
                                  for k in node.keywords)
                if not person_only and any(_names_account(a) for a in node.args):
                    offenders.append(f"{rel}:{node.lineno} Fact names the device account without person_only")
            if isinstance(node, ast.Call) and _call_name(node) == "CallFacts":
                for k in node.keywords:
                    if k.arg in ("consequence", "consequence_template", "names") and _names_account(k.value):
                        offenders.append(f"{rel}:{node.lineno} the {k.arg} names the device account")
            if isinstance(node, ast.Call) and _call_name(node) == "DeviceOut":
                if any(k.arg == "user" for k in node.keywords):
                    offenders.append(f"{rel}:{node.lineno} list_devices hands out the account")
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "consequence" for t in node.targets):
                if _names_account(node.value):
                    offenders.append(f"{rel}:{node.lineno} a consequence names the device account")
            if isinstance(node, ast.FunctionDef) and node.name == "device_name":
                if any(isinstance(n, ast.Return) and n.value is not None and _names_account(n.value)
                       for n in ast.walk(node)):
                    offenders.append(f"{rel}:{node.lineno} a device's speaking name names its account")
        offenders += _unscrubbed_device_output(rel, tree)
    assert not offenders, "\n".join(offenders)


def _scrubbed(node) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n) == "device_text" for n in ast.walk(node))


def _unscrubbed_device_output(rel, tree):
    """Device output (the job result's EXTERNAL_KEY) built without device_text, directly or in a variable of the same
    function."""
    found = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        clean_names = {t.id for n in ast.walk(func) if isinstance(n, ast.Assign) and _scrubbed(n.value)
                       for t in n.targets if isinstance(t, ast.Name)}
        for node in ast.walk(func):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if getattr(key, "id", "") != "EXTERNAL_KEY":
                    continue
                if not (_scrubbed(value) or (isinstance(value, ast.Name) and value.id in clean_names)):
                    found.append(f"{rel}:{node.lineno} device output reaches an AI without device_text")
    return sorted(set(found))
