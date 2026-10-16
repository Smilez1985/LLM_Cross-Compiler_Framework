"""The GUI's deletion dialog, the trash window, the knowledge snapshots window and the framework build images
(delegation design 3.10, round 5 f; round 6 10.4; WP7).

The person sees every item with its speaking name, full path (or Docker id), file name, size and what it is; in
multiple choice nothing is ticked and the plan is rebuilt from exactly the ticked items; "No" is the default; every
text is plain text. An AI sees labels and sizes only.
"""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.deletion import choose  # noqa: E402
from orchestrator.tools.spec import DeletionItem, DeletionPlan  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def plan(modes=("trash", "permanent")):
    items = [DeletionItem(kind="knowledge_snapshot", speaking_name="<b>pre_ingest_1</b>",
                          full_path="/srv/lcf/state/knowledge_snapshots/pre_ingest_1.jsonl.gz",
                          file_name="pre_ingest_1.jsonl.gz", size_bytes=1_500_000_000,
                          what="backup of the knowledge base taken before a Deep Ingest (taken 2026-10-01 10:00)"),
             DeletionItem(kind="docker_image", speaking_name="Framework build image 3f2a3f2a3f2a (aarch64)",
                          file_name="sha256:" + "3" * 64, size_bytes=2_000_000_000,
                          what="unused build image of the framework for aarch64, without a name")]
    return DeletionPlan(items=items, total_bytes=3_500_000_000, mode_offered=modes)


def labels(dialog):
    from PySide6.QtWidgets import QLabel
    return dialog.findChildren(QLabel)


def test_deletion_dialog_shows_full_path_name_size_kind(qapp):
    from PySide6.QtCore import Qt

    from orchestrator.gui.deletion_dialog import DeletionConfirmDialog
    dialog = DeletionConfirmDialog(plan(), lang="en")
    try:
        text = dialog.plain_text()
        for words in ("<b>pre_ingest_1</b>", "Full path: /srv/lcf/state/knowledge_snapshots/pre_ingest_1.jsonl.gz",
                      "File name: pre_ingest_1.jsonl.gz", "Size: 1.5 GB", "What it is: backup of the knowledge base",
                      "Docker id: sha256:" + "3" * 64, "Size: 2.0 GB"):
            assert words in text, words
        assert all(label.textFormat() == Qt.PlainText for label in labels(dialog)), "a name is never markup"
        assert dialog.btn_no.isDefault() and not dialog.btn_yes.isDefault(), "No is the default button"
        assert dialog.radio_trash.isChecked() and not dialog.radio_permanent.isChecked(), "the trash first"
        assert dialog.btn_no.text() == "No, keep everything"
        assert not dialog.btn_yes.isEnabled(), "the yes button reacts only after a moment"
        dialog._arm()
        assert dialog.btn_yes.isEnabled() and dialog.btn_yes.text() == "Move 2 items (3.5 GB) to the trash"
        dialog.radio_permanent.setChecked(True)
        assert dialog.btn_yes.text() == "Delete 2 items (3.5 GB) for good"
        assert "frees 3.5 GB now" in dialog.radio_permanent.text()
        assert dialog.accepted_deletion() is None, "nothing is accepted before the person's click"
        dialog.btn_yes.click()
        accepted = dialog.accepted_deletion()
        assert accepted.actor == "gui" and accepted.mode == "permanent" and accepted.plan == plan()
    finally:
        dialog.close()


def test_multiple_choice_ticks_nothing_and_rebuilds_the_plan_from_the_ticked_items(qapp):
    from orchestrator.gui.deletion_dialog import DeletionConfirmDialog
    dialog = DeletionConfirmDialog(plan(("permanent",)), multiple=True, lang="en")
    try:
        dialog._arm()
        assert [c.isChecked() for c in dialog.checks] == [False, False], "nothing is preselected"
        assert dialog.chosen_plan() is None and not dialog.btn_yes.isEnabled()
        assert dialog.btn_yes.text() == "Nothing ticked"
        assert "This deletes for good" in dialog.plain_text() and dialog.radio_trash is None
        dialog.checks[1].setChecked(True)
        assert dialog.btn_yes.isEnabled() and dialog.btn_yes.text() == "Delete 1 items (2.0 GB) for good"
        dialog.btn_yes.click()
        accepted = dialog.accepted_deletion()
        assert accepted.plan == choose(plan(("permanent",)), [1]) and accepted.sha256 == \
            choose(plan(("permanent",)), [1]).sha256(), "its hash covers exactly the ticked item"
    finally:
        dialog.close()
    dialog = DeletionConfirmDialog(plan(), multiple=True, lang="de")
    try:
        dialog._arm()
        dialog.checks[0].setChecked(True)
        assert dialog.btn_yes.text() == "1 Einträge (1.5 GB) in den Papierkorb"
        dialog.reject()
        assert dialog.accepted_deletion() is None
    finally:
        dialog.close()


def test_ai_view_of_a_deletion_has_labels_not_paths():
    view = plan().ai_view()
    assert view == [{"label": "<b>pre_ingest_1</b>", "kind": "knowledge_snapshot", "size_bytes": 1_500_000_000},
                    {"label": "Framework build image 3f2a3f2a3f2a (aarch64)", "kind": "docker_image",
                     "size_bytes": 2_000_000_000}]
    assert "/srv" not in json.dumps(view)


def test_deletion_texts_exist_in_en_and_de_with_the_same_placeholders():
    from orchestrator.utils.l10n_deletion import TEXTS
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key in TEXTS["en"]:
        fields = [set(re.findall(r"{(\w+)}", TEXTS[lang][key])) for lang in ("en", "de")]
        assert fields[0] == fields[1], key


def test_no_rich_text_in_the_deletion_windows():
    for name in ("deletion_dialog.py", "trash_window.py", "knowledge_snapshots_window.py"):
        source = (REPO / "orchestrator" / "gui" / name).read_text(encoding="utf-8")
        for api in ("setHtml", "RichText", "setToolTip", "QMessageBox.information(", "QMessageBox.warning(",
                    "QMessageBox.question("):
            assert api not in source, (name, api)


# --- the trash window ------------------------------------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def snapshots(state, *names):
    folder = state / "knowledge_snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        path = folder / f"{name}.jsonl.gz"
        path.write_bytes(bytes([65 + i]) * 1000)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    return folder


def boxes(monkeypatch):
    from orchestrator.gui import plain
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    return told


def answer_dialog(monkeypatch, tick=None, mode=None, yes=True):
    """The deletion dialog answered like a person: ticks `tick` (multiple choice), picks `mode`, then yes or no."""
    from orchestrator.gui import deletion_dialog
    shown = []

    def run(dialog):
        shown.append(dialog.plain_text())
        dialog._arm()
        for i in tick or []:
            dialog.checks[i].setChecked(True)
        if mode == "permanent":
            dialog.radio_permanent.setChecked(True)
        if yes:
            dialog.btn_yes.click()
            return dialog.result()
        return 0
    monkeypatch.setattr(deletion_dialog.DeletionConfirmDialog, "exec", run)
    return shown


def test_the_trash_window_restores_and_empties_only_the_ticked_entries(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    from orchestrator.Core.deletion import accept
    from orchestrator.Core.knowledge_snapshots import delete_snapshots, snapshot_plan
    from orchestrator.Core.trash import Trash
    from orchestrator.gui.trash_window import TrashDialog
    state = tmp_path / "state"
    folder = snapshots(state, "s1", "s2", "s3")
    trash = Trash(state, audit=Audit())
    delete_snapshots(folder, accept(snapshot_plan(folder), "gui", "trash"), trash=trash, audit=Audit())
    told = boxes(monkeypatch)
    dialog = TrashDialog(trash, lang="en")
    try:
        assert dialog.table.rowCount() == 3
        assert [dialog.table.item(r, 0).checkState() for r in range(3)] == [Qt.Unchecked] * 3
        assert dialog.table.item(0, 6).text() == str(folder / "s1.jsonl.gz"), "where it came from, full path"
        assert "Nothing leaves the trash by itself" in dialog.intro.text()
        dialog._restore()
        assert told[-1] == "Tick at least one entry first." and len(trash.entries()) == 3
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        dialog._restore()
        assert (folder / "s1.jsonl.gz").is_file() and "Restored s1" in told[-1]
        shown = answer_dialog(monkeypatch, yes=False)
        dialog.table.item(1, 0).setCheckState(Qt.Checked)
        dialog._delete()
        assert len(trash.entries()) == 2 and "s3" in shown[0] and ".trash" in shown[0]
        answer_dialog(monkeypatch)
        dialog._delete()
        assert [e.label for e in trash.entries()] == ["s2"] and "Deleted for good: s3" in told[-1]
        assert [e["outcome"] for e in trash.audit.entries].count("trash_emptied") == 1
    finally:
        dialog.close()


def test_the_snapshots_window_deletes_and_exports_only_the_ticked_snapshots(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    from orchestrator.Core.trash import Trash
    from orchestrator.gui import knowledge_snapshots_window as window
    state = tmp_path / "state"
    folder = snapshots(state, "pre_ingest_1", "pre_ingest_2", "pre_ingest_3")
    audit = Audit()
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d),
                                tool_registry=SimpleNamespace(audit=audit, policy_store=None))
    told = boxes(monkeypatch)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang="en")
    try:
        assert [dialog.table.item(r, 0).checkState() for r in range(3)] == [Qt.Unchecked] * 3
        assert "nothing is ticked" in dialog.intro.text()
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        dialog.table.item(2, 0).setCheckState(Qt.Checked)
        shown = answer_dialog(monkeypatch)
        dialog._delete()
        assert str(folder / "pre_ingest_1.jsonl.gz") in shown[0] and "pre_ingest_2" not in shown[0]
        assert sorted(p.name for p in folder.iterdir()) == ["pre_ingest_2.jsonl.gz"]
        assert [e.label for e in Trash(state).entries()] == ["pre_ingest_1", "pre_ingest_3"]
        assert "Moved to the trash: pre_ingest_1" in told[-1]
        assert audit.entries[0]["outcome"] == "snapshots_pruned"
        target = tmp_path / "usb"
        target.mkdir()
        monkeypatch.setattr(window.QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        dialog._export()
        assert (target / "pre_ingest_2.jsonl.gz").read_bytes() == (folder / "pre_ingest_2.jsonl.gz").read_bytes()
        assert "Copied and checked (sha256): pre_ingest_2" in told[-1] and "were not changed" in told[-1]
    finally:
        dialog.close()
    lister = window.KnowledgeSnapshotsDialog(state, lang="en")   # without the framework: lists and exports only
    try:
        assert not lister.btn_delete.isEnabled() and lister.btn_export.isEnabled()
    finally:
        lister.close()


def test_framework_build_images_window_deletes_only_ticked_images(qapp, monkeypatch):
    from test_doctor_fixes import FakeClient, FakeImage, deletions, fake_framework

    from orchestrator.gui.deletion_dialog import free_framework_images
    client = FakeClient([FakeImage("a", 2_000_000_000), FakeImage("b", 3, tags=["llm-framework/aarch64:last-build"]),
                         FakeImage("c", 5)])
    client.info = lambda: {"DockerRootDir": "/var/lib/docker"}
    framework = fake_framework(client)
    told = boxes(monkeypatch)
    shown = answer_dialog(monkeypatch, yes=False)
    assert free_framework_images(None, framework, lang="en") is None
    assert "Docker keeps its images in /var/lib/docker" in shown[0]
    assert "llm-framework/aarch64:last-build" in shown[0] and "the next build for aarch64 reuses it" in shown[0]
    assert deletions(client) == []
    answer_dialog(monkeypatch, tick=[1, 2])
    result = free_framework_images(None, framework, lang="en")
    assert client.images.removed == ["sha256:" + "c" * 64, "llm-framework/aarch64:last-build"]
    assert len(result.deleted) == 2 and "2 images deleted" in told[-1]
    assert [i.id for i in client.images.images] == ["sha256:" + "a" * 64], "the unticked image stays"


def test_the_healing_dialog_offers_the_images_list_for_a_full_docker_disk(qapp):
    from orchestrator.Core.self_healing_manager import HealingProposal, HealingStrategy
    from orchestrator.gui.dialogs import HealingConfirmDialog
    opened = []
    proposal = HealingProposal("Disk full", "cause", "step", 1.0, HealingStrategy.DOCKER_PRUNE, "HOST")
    dialog = HealingConfirmDialog(proposal, free_docker_space=lambda: opened.append(1))
    try:
        assert dialog.automatic is False and dialog.btn_free_docker.text() == "Free Docker space…"
        dialog.btn_free_docker.click()
        assert opened == [1]
    finally:
        dialog.close()
    other = HealingConfirmDialog(HealingProposal("x", "y", "z", 1.0, HealingStrategy.RETRY_BUILD, "HOST"),
                                 free_docker_space=lambda: opened.append(2))
    try:
        assert other.btn_free_docker is None
    finally:
        other.close()
