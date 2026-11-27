"""The dead-code step (scripts/dead_code.py, DESIGN No dead code: the second recon of 2026-11-27): every function,
method, class and property vulture reports as unused stands in configs/vulture_allowlist.py with a known reason, and
no entry is stale. The CI's lint job runs the same step."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("vulture")
ROOT = Path(__file__).resolve().parents[1]


def dead_code():
    spec = importlib.util.spec_from_file_location("dead_code", ROOT / "scripts" / "dead_code.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_code_and_the_exception_list_agree():
    new, stale = dead_code().compare()
    assert new == [], f"unused and not listed: {new}"
    assert stale == [], f"listed but no longer unused: {stale}"


def test_every_exception_has_one_of_the_known_reasons():
    module = dead_code()
    spec = importlib.util.spec_from_file_location("allow", ROOT / "configs" / "vulture_allowlist.py")
    allow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(allow)
    reasons = {allow.PROTOCOL, allow.BY_NAME, allow.PREPARATION, allow.TEST_API, allow.KEPT, allow.OPEN}
    assert set(module.allowed().values()) <= reasons
    assert all("::" in key and key.split("::")[0].startswith(("orchestrator/", "scripts/")) for key in allow.ALLOWED)
