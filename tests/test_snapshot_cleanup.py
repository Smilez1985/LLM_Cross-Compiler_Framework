"""Deleting and exporting knowledge snapshots (DECISIONS round 6: no limit, no automatic deletion; the person deletes
with multiple choice from the full list or exports them; WP7), and where Docker keeps the framework's images
(round 7 item 12).
"""

import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import knowledge_snapshots as ks
from orchestrator.Core.trash import Trash


def snapshots(state, *names):
    folder = state / "knowledge_snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        path = folder / f"{name}.jsonl.gz"
        path.write_bytes(bytes([65 + i]) * 2000)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    return folder


def run_cli(framework, monkeypatch, *args, terminal=False, answer=None):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=400, color_system=None))
    monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
    result = CliRunner().invoke(cli_module.cli, list(args), obj=Ctx(), input=answer)
    return result, printed.getvalue() + result.output


def test_prune_lists_every_snapshot_and_deletes_only_the_picked_ones(booted_framework, monkeypatch):
    state = Path(booted_framework.config.user_state_dir)
    folder = snapshots(state, "pre_ingest_1", "pre_ingest_2", "pre_codebase_3")
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "prune")
    for name in ("pre_ingest_1", "pre_ingest_2", "pre_codebase_3"):
        assert str(folder / f"{name}.jsonl.gz") in out
    assert "Knowledge snapshots (3, 6 kB)" in out and "2 kB" in out and "Nothing was deleted" in out
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "prune", terminal=True, answer="\n")
    assert "Numbers to delete" in out and "Nothing was deleted." in out and len(list(folder.iterdir())) == 3
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "prune", terminal=True,
                     answer="1,3\n\n\n")
    assert "Move exactly 2 items (4 kB) to the trash?" in out and len(list(folder.iterdir())) == 3, "default no"
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "prune", terminal=True,
                     answer="1,3\ntrash\ny\n")
    assert sorted(p.name for p in folder.iterdir()) == ["pre_ingest_2.jsonl.gz"]
    assert "Moved to the trash: pre_ingest_1" in out and "llm-cli trash restore" in out
    # entries() sorts by deletion time first (second resolution): the two moves may fall into different seconds
    assert sorted(e.label for e in Trash.for_framework(booted_framework).entries()) == ["pre_codebase_3", "pre_ingest_1"]
    entries = booted_framework.tool_registry.audit.entries(20)
    pruned = [e for e in entries if e["outcome"] == "snapshots_pruned"]
    assert len(pruned) == 1 and pruned[0]["actor"] == "cli"


def test_delete_names_exactly_those_snapshots_and_can_delete_for_good(booted_framework, monkeypatch):
    state = Path(booted_framework.config.user_state_dir)
    folder = snapshots(state, "s1", "s2")
    result, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "delete", "nope")
    assert result.exit_code == 2 and "No such knowledge snapshot: nope" in out
    _, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "delete", "s2", terminal=True,
                     answer="permanent\ny\n")
    assert "Numbers to delete" not in out and "frees 2 kB now" in out and "Delete exactly 1 items" in out
    assert sorted(p.name for p in folder.iterdir()) == ["s1.jsonl.gz"] and "Deleted: s2" in out
    assert Trash.for_framework(booted_framework).entries() == []


def test_no_channel_prunes_snapshots_automatically(tmp_path):
    """Nothing deletes a snapshot by itself: not a count, not an age, not a new snapshot (the RAG manager has no
    pruning any more), and deleting needs the person's accepted plan."""
    from orchestrator.Core.rag_manager import RAGManager
    assert not hasattr(RAGManager, "_prune_snapshots")
    folder = snapshots(tmp_path / "state", *[f"s{i}" for i in range(30)])
    plan = ks.snapshot_plan(folder)
    assert len(plan.items) == 30 and plan.mode_offered == ("trash", "permanent")
    with pytest.raises(PermissionError):
        ks.delete_snapshots(folder, plan, audit=SimpleNamespace(record=lambda *a, **k: 1))
    assert len(list(folder.iterdir())) == 30


def test_export_copies_checks_by_sha256_and_never_deletes(booted_framework, monkeypatch, tmp_path):
    state = Path(booted_framework.config.user_state_dir)
    folder = snapshots(state, "s1", "s2")
    usb = tmp_path / "usb"
    usb.mkdir()
    result, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "export", "s1", "s2", "--to",
                          str(usb))
    assert result.exit_code == 0 and "2 of 2 snapshots exported" in out and "sha256" in out
    assert (usb / "s1.jsonl.gz").read_bytes() == (folder / "s1.jsonl.gz").read_bytes()
    assert len(list(folder.iterdir())) == 2, "an export never deletes"
    (usb / "s2.jsonl.gz").write_bytes(b"someone else's file")
    result, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "export", "s1", "s2", "--to",
                          str(usb))
    assert result.exit_code == 1 and "already there with the same content" in out
    assert "nothing was overwritten" in out and (usb / "s2.jsonl.gz").read_bytes() == b"someone else's file"
    result, out = run_cli(booted_framework, monkeypatch, "knowledge", "snapshots", "export", "s1", "--to",
                          str(tmp_path / "missing"))
    assert result.exit_code == 2 and "is not a folder" in out


def test_a_copy_that_does_not_match_is_removed_and_reported(tmp_path, monkeypatch):
    folder = snapshots(tmp_path / "state", "s1")
    target = tmp_path / "share"
    target.mkdir()
    real = ks._sha256
    calls = []

    def flaky(path):   # the copy on the share reads back different bytes
        calls.append(Path(path))
        return "0" * 64 if Path(path).parent == target else real(path)
    monkeypatch.setattr(ks, "_sha256", flaky)
    results = ks.export_snapshots(folder, ["s1"], target)
    assert not results[0].ok and "does not match the original (sha256)" in results[0].problem
    assert list(target.iterdir()) == [] and (folder / "s1.jsonl.gz").is_file()
    with pytest.raises(ValueError, match="free"):
        monkeypatch.setattr(ks.shutil, "disk_usage", lambda p: SimpleNamespace(free=10))
        ks.export_snapshots(folder, ["s1"], target)


def test_the_doctor_says_where_docker_keeps_the_images(tmp_path):
    """Round 7 item 12: the person sees where Docker keeps its images and how much the framework's use; the doctor
    never changes a daemon setting. An AI reads the folder by name only."""
    from test_doctor_fixes import FakeClient, FakeImage, fake_framework

    from orchestrator.Core import doctor
    client = FakeClient([FakeImage("a", 2_000_000_000), FakeImage("b", 1_000_000_000,
                                                                  tags=["llm-framework/aarch64:last-build"])])
    client.info = lambda: {"DockerRootDir": str(tmp_path / "docker-data")}
    (tmp_path / "docker-data").mkdir()
    framework = fake_framework(client)
    finding = next(f for f in doctor.check_docker(framework) if f.code == "docker.storage")
    assert finding.severity.value == "info" and str(tmp_path / "docker-data") in finding.message
    assert "2 build images use about 3.0 GB" in finding.message and "never deletes an image" in finding.message
    assert "Container_Setup" in finding.fix_hint and "administrator" in finding.fix_hint
    ai = doctor.ai_findings(framework, doctor.DoctorReport([finding]))[0]
    assert str(tmp_path) not in ai["message"] and "docker-data" in ai["message"]
    assert not [e for e in client.events if e[0] != "audit"], "it only reads"
