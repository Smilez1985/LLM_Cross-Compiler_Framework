"""Mojo Oracle's finding `dependencies.<target>.steps` and Mojo Miracle's repair `dependencies.add_steps`
(orchestrator/Core/mojo/checks/dependencies.py, repairs/dependencies.py, Core/target_dependencies.py; docs/DESIGN.md,
"Dependencies are data in the wizard").

A package of a module's list that the module's Dockerfile does not name pinned is a finding; for a module of the person's
own targets folder Miracle writes the steps after the person's own yes: the old file is kept first, the write is a
compare-and-swap on the file's sha256, nothing is installed on this computer. Every hash is made at run time.
"""

import hashlib
import random
from pathlib import Path
from types import SimpleNamespace

import yaml

from orchestrator.Core import target_dependencies as deps
from orchestrator.Core.mojo.checks import check_dependencies
from orchestrator.Core.mojo.findings import Severity
from orchestrator.Core.mojo.offers import offers
from orchestrator.Core.mojo.repairs import REPAIRS, repair_for
from orchestrator.Core.mojo.repairs import dependencies as repair
from orchestrator.Core.mojo.repairs.engine import apply_repair
from orchestrator.Core.mojo.runner import Mojo

HEX = "0123456789abcdef"
WHEEL = "".join(random.Random("wheel").choices(HEX, k=64))
APT = dict(name="libusb-1.0-0", version="2:1.0.26-1", kind="system_package", origin="distribution", source="template")
PIP = dict(name="numpy", version="2.1.0", kind="pip_package", origin="package_index", source="template", sha256=WHEEL)
VENDOR = dict(name="noe-sdk.tar.gz", version="1.2", kind="vendor_file", origin="vendor_page", source="template",
              url="https://vendor.example.com/noe", licence_url="https://vendor.example.com/licence")
BEFORE = ("FROM debian:bookworm-slim AS builder\nRUN echo base\n\n"
          "# Vendor Python packages of the module (pip_packages, each pinned name==version; empty when it has none)\n\n"
          "CMD [\"true\"]\n")


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append((actor, tool, decision, outcome))
        return len(self.entries)


def world(tmp_path: Path, dockerfile: str = BEFORE, entries=(APT, PIP), own: bool = True, name: str = "npu"):
    """A framework with one target `name`: in the person's own targets folder, or in the shipped one."""
    shipped, mine = tmp_path / "shipped", tmp_path / "mine"
    shipped.mkdir(), mine.mkdir()
    folder = (mine if own else shipped) / name
    folder.mkdir()
    (folder / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"id": name}, "template_variables": {"dependencies": list(entries)}}), encoding="utf-8")
    (folder / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    registry = SimpleNamespace(list_targets=lambda: [{"id": name}], folder_of=lambda t: folder if t == name else None)
    framework = SimpleNamespace(
        target_manager=registry,
        config=SimpleNamespace(user_state_dir=state, targets_dir=str(shipped), user_targets_dir=str(mine)),
        tool_registry=SimpleNamespace(audit=Audit()))
    return framework, folder


def finding(framework, name="npu"):
    return {f.code: f for f in check_dependencies(framework)}.get(f"dependencies.{name}.steps")


# --- Oracle ------------------------------------------------------------------------------------------------------------

def test_a_listed_package_the_dockerfile_does_not_name_is_a_warning_with_a_repair(tmp_path):
    framework, folder = world(tmp_path)
    found = finding(framework)
    assert found.severity is Severity.WARN and found.repair_id == "dependencies.add_steps"
    assert "libusb-1.0-0" in found.message and "numpy" in found.message and found.where == str(folder)


def test_a_dockerfile_that_names_them_pinned_is_ok(tmp_path):
    named = BEFORE + "RUN apt-get install -y libusb-1.0-0=2:1.0.26-1 \\\n && pip install numpy==2.1.0\n"
    framework, _ = world(tmp_path, dockerfile=named)
    assert finding(framework) is None
    assert {f.code: f for f in check_dependencies(framework)}["dependencies.npu"].severity is Severity.OK


def test_a_mention_in_a_comment_installs_nothing(tmp_path):
    framework, _ = world(tmp_path, dockerfile=BEFORE + "# libusb-1.0-0=2:1.0.26-1 and numpy==2.1.0\n")
    assert finding(framework) is not None


def test_a_vendor_file_a_base_image_and_a_driver_never_count(tmp_path):
    driver = dict(name="npu-driver", version="5.0", kind="board_driver", origin="vendor_page", source="template",
                  url="https://vendor.example.com/driver")
    framework, _ = world(tmp_path, entries=(VENDOR, driver))
    assert finding(framework) is None


def test_a_target_the_framework_ships_is_the_persons_to_change(tmp_path):
    framework, _ = world(tmp_path, own=False)
    found = finding(framework)
    assert found.repair_id == "" and found.cannot == "person"
    assert offers(framework, SimpleNamespaceReport([found]))[0].verdict == "person"


def test_a_dockerfile_without_the_place_is_the_persons_to_change(tmp_path):
    framework, _ = world(tmp_path, dockerfile="FROM debian:bookworm-slim\nCMD [\"true\"]\n")
    found = finding(framework)
    assert found.repair_id == "" and found.cannot == "person"


class SimpleNamespaceReport:
    """The part of a MojoReport `offers` reads."""
    def __init__(self, findings):
        self.findings = findings


# --- the plan ----------------------------------------------------------------------------------------------------------

def test_the_plan_is_one_add_steps_step_per_dockerfile_and_needs_the_persons_yes(tmp_path):
    framework, folder = world(tmp_path)
    (folder / "Dockerfile.gpu").write_text(BEFORE, encoding="utf-8")
    plan = REPAIRS["dependencies.add_steps"].plan(framework, finding(framework))
    assert plan.repair_class() == "person_yes"
    assert [(s.kind, Path(s.full_path).name) for s in plan.steps] == [("add_steps", "Dockerfile"),
                                                                       ("add_steps", "Dockerfile.gpu")]
    assert plan.sha256() == REPAIRS["dependencies.add_steps"].plan(framework, finding(framework)).sha256()


def test_no_plan_when_nothing_is_lacking_or_the_folder_is_not_the_persons(tmp_path):
    framework, _ = world(tmp_path, own=False)
    assert REPAIRS["dependencies.add_steps"].plan(framework, finding(framework)) is None


def test_the_code_with_a_target_name_finds_its_repair():
    assert repair_for("dependencies.npu.steps").repair_id == "dependencies.add_steps"
    assert repair_for("dependencies.Rockchip.v2.steps").repair_id == "dependencies.add_steps"
    assert repair_for("dependencies.npu.vendor") is None


# --- the repair --------------------------------------------------------------------------------------------------------

def test_the_steps_are_pinned_and_the_pip_wheel_is_hash_checked(tmp_path):
    framework, folder = world(tmp_path)
    (gap,) = deps.dockerfile_gaps(folder, deps.load(folder))
    text = repair.with_steps(BEFORE, gap, "2026-11-23")
    assert "libusb-1.0-0=2:1.0.26-1" in text and "--no-install-recommends" in text
    assert f"numpy==2.1.0 --hash=sha256:{WHEEL}" in text and "--require-hashes" in text and "--no-deps" in text
    assert text.index("Vendor Python packages") < text.index("added by Mojo Miracle") < text.index("CMD")


def test_a_yes_writes_the_steps_keeps_the_old_file_and_audits(tmp_path):
    framework, folder = world(tmp_path)
    old = (folder / "Dockerfile").read_bytes()
    code = "dependencies.npu.steps"
    shown = REPAIRS["dependencies.add_steps"].plan(framework, finding(framework))
    result = apply_repair(framework, code, actor="cli", decision="person", answered_by="person",
                          expect_sha256=shown.sha256())
    assert result.outcome == "repaired", result.reason
    text = (folder / "Dockerfile").read_text(encoding="utf-8")
    assert "numpy==2.1.0" in text and "libusb-1.0-0=2:1.0.26-1" in text
    backup = tmp_path / "state" / "mojo" / "backups" / "dependencies" / f"npu-Dockerfile-{hashlib.sha256(old).hexdigest()}"
    assert backup.read_bytes() == old
    assert [e[3] for e in framework.tool_registry.audit.entries] == ["started", "ok"]
    assert finding(framework) is None
    assert apply_repair(framework, code, actor="cli", decision="person", answered_by="person").outcome == "already_fixed"


def test_without_the_persons_own_yes_nothing_is_written(tmp_path):
    framework, folder = world(tmp_path)
    for decision, answered in (("allow", ""), ("ask", ""), ("person", "")):
        result = apply_repair(framework, "dependencies.npu.steps", actor="mcp", decision=decision, answered_by=answered)
        assert result.outcome == "refused", (decision, result.reason)
    assert (folder / "Dockerfile").read_text(encoding="utf-8") == BEFORE


def test_a_file_changed_since_the_plan_is_not_written(tmp_path):
    framework, folder = world(tmp_path)
    shown = REPAIRS["dependencies.add_steps"].plan(framework, finding(framework))
    (folder / "Dockerfile").write_text(BEFORE + "# the person edited it\n", encoding="utf-8")
    result = apply_repair(framework, "dependencies.npu.steps", actor="cli", decision="person", answered_by="person",
                          expect_sha256=shown.sha256())
    assert result.outcome == "plan_changed"
    assert "Mojo Miracle" not in (folder / "Dockerfile").read_text(encoding="utf-8")


def test_miracle_asks_first_and_a_no_changes_nothing(tmp_path):
    framework, folder = world(tmp_path)
    mojo = Mojo(framework, checks=[check_dependencies])
    report = mojo.miracle(actor="cli", at_terminal=True, decision="person", answered_by="person",
                          confirm=lambda offer: False)
    assert [e.outcome for e in report.entries] == ["declined"]
    assert (folder / "Dockerfile").read_text(encoding="utf-8") == BEFORE
    report = mojo.miracle(actor="cli", at_terminal=True, decision="person", answered_by="person",
                          confirm=lambda offer: offer.verdict == "miracle_yes")
    assert [e.outcome for e in report.entries] == ["repaired"]
    assert report.exit_code == 0


def test_an_ai_is_told_the_repair_is_the_persons(tmp_path):
    framework, _ = world(tmp_path)
    (offer,) = offers(framework, SimpleNamespaceReport([finding(framework)]))
    assert offer.verdict == "miracle_yes" and offer.plan.repair_class() == "person_yes"
