"""Mojo Oracle's finding of what the targets' dependency lists still lack (orchestrator/Core/mojo/checks/dependencies.py;
docs/DESIGN.md, "Dependencies are data in the wizard"): a list that is not valid, a vendor file that waits for the
person's yes, an entry nobody read; it only reads and starts no program. The hashes are made at run time."""

from pathlib import Path
from types import SimpleNamespace

import yaml

from orchestrator.Core.mojo.checks import check_dependencies
from orchestrator.Core.mojo.findings import Severity
from orchestrator.Core.mojo.offers import offers
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS, Mojo
from orchestrator.Core.mojo.views import oracle_rows

VENDOR = dict(name="noe-sdk.tar.gz", version="1.2", kind="vendor_file", origin="vendor_page", source="excerpt:3",
              url="https://vendor.example.com/noe", licence_url="https://vendor.example.com/licence")
PACKAGE = dict(name="libusb-1.0-0", version="2:1.0.26-1", kind="system_package", origin="distribution",
               source="template", check=["dpkg", "-s", "libusb-1.0-0"])
GAP = dict(PACKAGE, name="libfoo", source=None, check=[])


def framework_with(tmp_path: Path, **targets):
    """A framework whose registry holds the targets (id -> list of dependency entries, or the raw value)."""
    folders = {}
    for tid, dependencies in targets.items():
        folder = tmp_path / "targets" / tid
        folder.mkdir(parents=True)
        (folder / "target.yml").write_text(yaml.safe_dump(
            {"metadata": {"id": tid}, "template_variables": {"dependencies": dependencies}}), encoding="utf-8")
        folders[tid] = folder
    state = tmp_path / "state"
    state.mkdir()
    registry = SimpleNamespace(list_targets=lambda: [{"id": t} for t in folders], folder_of=folders.get)
    return SimpleNamespace(target_manager=registry, config=SimpleNamespace(user_state_dir=state))


def codes(findings):
    return {f.code: f for f in findings}


def test_no_list_anywhere_is_one_ok_line(tmp_path):
    (found,) = check_dependencies(framework_with(tmp_path, plain=[]))
    assert found.severity is Severity.OK and found.code == "dependencies"


def test_a_list_without_a_gap_or_waiting_file_is_ok(tmp_path):
    found = codes(check_dependencies(framework_with(tmp_path, pi=[PACKAGE])))
    assert found["dependencies.pi"].severity is Severity.OK


def test_a_vendor_file_that_waits_is_a_warning_for_the_person(tmp_path):
    found = codes(check_dependencies(framework_with(tmp_path, npu=[PACKAGE, VENDOR])))
    finding = found["dependencies.npu.vendor"]
    assert finding.severity is Severity.WARN and finding.cannot == "person"
    assert "noe-sdk.tar.gz" in finding.message and finding.where.endswith("npu") and finding.why
    assert [(step.id, dict(step.values)) for step in finding.steps] == [
        ("build.vendor_fetch", {"target": "npu", "name": "noe-sdk.tar.gz"})]


def test_an_entry_without_a_source_is_named_as_a_gap(tmp_path):
    found = codes(check_dependencies(framework_with(tmp_path, pi=[PACKAGE, GAP])))
    assert found["dependencies.pi.gaps"].severity is Severity.INFO and "libfoo" in found["dependencies.pi.gaps"].message


def test_an_invalid_list_is_a_warning_and_the_next_target_is_still_read(tmp_path):
    framework = framework_with(tmp_path, broken=[dict(PACKAGE, name="Not Valid")], fine=[PACKAGE])
    found = codes(check_dependencies(framework))
    assert found["dependencies.broken.invalid"].severity is Severity.WARN
    assert [step.id for step in found["dependencies.broken.invalid"].steps] == ["targets.check"]
    assert found["dependencies.fine"].severity is Severity.OK


def test_oracle_starts_no_program(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("started a program")))
    assert check_dependencies(framework_with(tmp_path, pi=[PACKAGE, VENDOR]))


def test_the_check_is_one_of_oracles_and_its_rows_are_readable(tmp_path):
    assert check_dependencies in DEFAULT_CHECKS
    framework = framework_with(tmp_path, npu=[VENDOR])
    report = Mojo(framework, checks=[check_dependencies]).run()
    assert report.exit_code == 1
    offered = offers(framework, report)
    (offer,) = offered
    assert offer.verdict == "person"
    (row,) = [r for r in oracle_rows(framework, report, offered) if r["code"] == "dependencies.npu.vendor"]
    assert row["repair"]["how"] == "person"
