"""orchestrator/remote/ as a layer (consolidation plan, layer 2) and paramiko on first use (decision RG1).

- The package imports only the bottom layer (orchestrator.utils, orchestrator.security) and itself, at module level and
  inside functions; no Qt, no click or rich, no Core, runtime, tools or gui. (tests/test_layering.py of the layering
  chain takes this rule over with the rest of the layers.)
- Importing the package, orchestrator.utils.ssh_trust or the old reasons path loads no paramiko; the first check that
  needs it (transport.available) imports it.
- Without paramiko every place says so the way it did: the deploy falls back to OpenSSH, the person steps refuse with
  "no_paramiko", ssh_trust raises its HostKeyError.
"""

import ast
import json
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

REMOTE = REPO_ROOT / "orchestrator" / "remote"
ALLOWED_INSIDE = ("orchestrator.remote", "orchestrator.utils", "orchestrator.security")
ALLOWED_OUTSIDE = {"paramiko"}   # the SSH library, imported on first use only


def imports_of(path):
    """Every module an import statement in `path` names (module level and inside functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def test_the_remote_layer_imports_only_the_bottom_layer():
    files = sorted(REMOTE.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "address.py", "reasons.py", "transport.py"}
    for path in files:
        for name in imports_of(path):
            top = name.split(".")[0]
            if top == "orchestrator":
                assert name.startswith(ALLOWED_INSIDE), (path.name, name)
            else:
                assert top in sys.stdlib_module_names or top in ALLOWED_OUTSIDE, (path.name, name)


def _fresh(code):
    run = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_importing_the_remote_code_loads_no_paramiko_and_no_qt():
    loaded = _fresh(
        "import json, sys\n"
        "sys.modules['PySide6'] = None\n"   # the remote layer is Qt-free: an import of Qt would fail here
        "import orchestrator.remote, orchestrator.remote.address, orchestrator.remote.reasons\n"
        "import orchestrator.remote.transport\n"
        "import orchestrator.utils.ssh_trust, orchestrator.runtime.device.reasons\n"
        "from orchestrator.remote import reasons\n"
        "reasons.classify(OSError('x')); reasons.reach_code(EOFError(), while_waiting=True)\n"
        "print(json.dumps(sorted(m for m, v in sys.modules.items()\n"
        "                        if v is not None and m.split('.')[0] in ('paramiko', 'PySide6'))))\n")
    assert loaded == []


def test_the_first_check_imports_paramiko():
    pytest.importorskip("paramiko")
    loaded = _fresh(
        "import json, sys\n"
        "from orchestrator.remote import transport\n"
        "before = 'paramiko' in sys.modules\n"
        "ok, why = transport.available()\n"
        "print(json.dumps([before, ok, why, 'paramiko' in sys.modules, bool(transport.PARAMIKO_AVAILABLE)]))\n")
    assert loaded == [False, True, "", True, True]


@pytest.fixture
def no_paramiko(monkeypatch):
    """paramiko cannot be imported (as on an installation without it)."""
    from orchestrator.remote import transport
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(transport, "_loaded", None)
    monkeypatch.setitem(sys.modules, "paramiko", None)
    if "paramiko" in vars(ssh_trust):   # an earlier test's restored patch of ssh_trust.paramiko
        monkeypatch.delattr(ssh_trust, "paramiko")
    monkeypatch.setattr(ssh_trust, "_tofu_class", None)
    yield
    monkeypatch.setattr(transport, "_loaded", None)


def test_without_paramiko_every_place_says_so(no_paramiko, tmp_path):
    from orchestrator.Core import deployment_manager
    from orchestrator.remote import transport
    from orchestrator.runtime.device import person_steps
    from orchestrator.utils import ssh_trust
    assert transport.available() == (False, "the 'paramiko' package is not installed")
    assert not deployment_manager.PARAMIKO_AVAILABLE, "the deploy takes the OpenSSH fallback"
    assert ssh_trust.paramiko is None and not hasattr(ssh_trust, "TofuPolicy")
    with pytest.raises(ssh_trust.HostKeyError, match="paramiko"):
        ssh_trust.fetch_host_key("192.0.2.10", 22)
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.read_host_key(tmp_path, "192.0.2.10", 22)
    assert e.value.kind == "no_paramiko"


def test_without_paramiko_the_builder_tab_says_so(no_paramiko):
    pytest.importorskip("PySide6")
    from orchestrator.gui import builder_tab
    assert not builder_tab.PARAMIKO_AVAILABLE


def test_a_broken_paramiko_counts_as_missing(monkeypatch):
    """The deploy looked for the package on disk (find_spec), ssh_trust imported it: an installation whose import
    fails was 'available' to the deploy and missing to the login. One real import now answers both."""
    import builtins
    from orchestrator.remote import transport
    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "paramiko":
            raise OSError("cannot load a compiled dependency")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(transport, "_loaded", None)
    monkeypatch.delitem(sys.modules, "paramiko", raising=False)
    monkeypatch.setattr(builtins, "__import__", broken)
    assert transport.available() == (False, "the 'paramiko' package cannot be loaded (OSError)")
    monkeypatch.setattr(builtins, "__import__", real_import)
    monkeypatch.setattr(transport, "_loaded", None)


def test_the_tofu_policy_still_refuses_an_unknown_key():
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.utils import ssh_trust
    policy = ssh_trust.TofuPolicy()
    assert isinstance(policy, paramiko.MissingHostKeyPolicy)
    key = paramiko.ECDSAKey.generate()
    with pytest.raises(ssh_trust.UnknownHostKey) as e:
        policy.missing_host_key(None, "[192.0.2.10]:2222", key)
    assert (e.value.host, e.value.port) == ("192.0.2.10", 2222)
    assert ssh_trust.TofuPolicy is ssh_trust.TofuPolicy, "made once"
