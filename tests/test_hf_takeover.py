"""The optional one-time Take over of the old Hugging Face download cache (DECISIONS round 11, A6;
orchestrator/Core/hf_takeover.py): what earlier versions of the GUI's browser saved in huggingface_hub's cache inside
the models folder is copied into the new folder, each file checked, and the old copies go only with the person's yes
to the exact list (the deletion dialog, trash or for good, audited first). No test reaches the network."""

import hashlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import hf_takeover
from orchestrator.Core.deletion import PlanChanged, accept
from orchestrator.Core.hf_hub import HubFile
from orchestrator.Core.trash import Trash, tree_size
from orchestrator.tools.local_files import LocalFiles
from test_hf_gui_download import CONFIG, OLD_COMMIT, WEIGHTS, WEIGHTS_SHA, git_blob_id, old_cache

REPO = "acme/tiny-GGUF"


class Audit:
    def __init__(self, fails=False):
        self.entries, self.fails = [], fails

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fails:
            raise OSError("disk full")
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


@pytest.fixture
def area(tmp_path):
    models, state = tmp_path / "models", tmp_path / "state"
    models.mkdir()
    state.mkdir()
    from orchestrator.Core.disk_plan import ReservationLedger
    return SimpleNamespace(models=models, state=state, tmp=tmp_path, ledger=ReservationLedger(state))


def two_files(area):
    return old_cache(area.models, REPO, files={"tiny-q4_k_m.gguf": (WEIGHTS, True), "config.json": (CONFIG, False)})


def new_place(area, name, repo="acme--tiny-GGUF"):
    return area.models / "hf" / repo / OLD_COMMIT[:12] / name


def no_temporary_files(root: Path) -> bool:
    return not [p for p in root.rglob("*") if p.name.endswith(".takeover.tmp")]


class TestTheOldCache:

    def test_every_file_with_its_checksum_place_and_state(self, area):
        folder = two_files(area)
        files = hf_takeover.old_cache(area.models)
        assert [(f.repo, f.commit, f.name, f.size, f.state) for f in files] == [
            (REPO, OLD_COMMIT, "config.json", len(CONFIG), "new"),
            (REPO, OLD_COMMIT, "tiny-q4_k_m.gguf", len(WEIGHTS), "new")]
        config, gguf = files
        assert (config.sha256, config.git_id) == (None, git_blob_id(CONFIG))
        assert (gguf.sha256, gguf.git_id) == (WEIGHTS_SHA, None)
        assert gguf.dest == new_place(area, "tiny-q4_k_m.gguf") and gguf.folder == folder

    def test_the_state_follows_the_new_folder(self, area):
        two_files(area)
        new_place(area, "tiny-q4_k_m.gguf").parent.mkdir(parents=True)
        new_place(area, "tiny-q4_k_m.gguf").write_bytes(WEIGHTS)
        new_place(area, "config.json").write_bytes(b"{}")
        states = {f.name: f.state for f in hf_takeover.old_cache(area.models)}
        assert states == {"tiny-q4_k_m.gguf": "taken", "config.json": "conflict"}

    def test_a_cache_without_links_keeps_no_checksum(self, area):
        old_cache(area.models, links=False)
        (file,) = hf_takeover.old_cache(area.models)
        assert (file.sha256, file.git_id, file.checked_here) == (None, None, False)

    def test_other_folders_are_no_cache(self, area):
        two_files(area)
        for rel in ("hf/acme--x/abc/model.gguf", "ditto/model.gguf", "embeddings/models--Qdrant--bm25/x", "own.gguf"):
            (area.models / rel).parent.mkdir(parents=True, exist_ok=True)
            (area.models / rel).write_bytes(b"x")
        old_cache(area.models, "acme/data", files={"train.jsonl": (b"{}", False)})
        (area.models / "models--acme--data").rename(area.models / "datasets--acme--data")
        assert {f.repo for f in hf_takeover.old_cache(area.models)} == {REPO}

    def test_no_models_folder_no_cache(self, tmp_path):
        assert hf_takeover.old_cache(tmp_path / "missing") == []


class TestTakeOver:

    def test_files_are_copied_checked_and_get_their_names(self, area):
        folder = two_files(area)
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models), ledger=area.ledger)
        assert [f.name for f in result.copied] == ["config.json", "tiny-q4_k_m.gguf"] and result.skipped == []
        assert result.bytes_copied == len(CONFIG) + len(WEIGHTS) and not result.stopped
        assert new_place(area, "tiny-q4_k_m.gguf").read_bytes() == WEIGHTS
        assert new_place(area, "config.json").read_bytes() == CONFIG
        assert not new_place(area, "config.json").is_symlink(), "a copy, never a link to the old cache"
        snapshot = folder / "snapshots" / OLD_COMMIT
        assert (snapshot / "tiny-q4_k_m.gguf").read_bytes() == WEIGHTS, "the old cache is never changed"
        assert no_temporary_files(area.models) and area.ledger.entries() == []
        refs = {e["ref"] for e in LocalFiles(area.state, {"models": area.models}).list("model")[0]}
        assert f"models:hf/acme--tiny-GGUF/{OLD_COMMIT[:12]}/tiny-q4_k_m.gguf" in refs
        assert f"models:models--acme--tiny-GGUF/snapshots/{OLD_COMMIT}/tiny-q4_k_m.gguf" in refs, "still listed"

    def test_it_is_one_time(self, area):
        two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        files = hf_takeover.old_cache(area.models)
        assert {f.state for f in files} == {"taken"}
        again = hf_takeover.take_over(area.models, files)
        assert again.copied == [] and again.skipped == []

    @pytest.mark.parametrize("name", ["tiny-q4_k_m.gguf", "config.json"])
    def test_a_damaged_file_is_not_copied(self, area, name):
        folder = two_files(area)
        blob = (folder / "snapshots" / OLD_COMMIT / name).resolve()
        data = bytearray(blob.read_bytes())
        data[3] ^= 0xFF
        blob.write_bytes(bytes(data))
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        assert [(f.name, code) for f, code in result.skipped] == [(name, "mismatch")]
        assert not new_place(area, name).exists() and no_temporary_files(area.models)

    def test_another_file_in_the_new_folder_is_never_replaced(self, area):
        two_files(area)
        place = new_place(area, "config.json")
        place.parent.mkdir(parents=True)
        place.write_bytes(b"mine")
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        assert [(f.name, code) for f, code in result.skipped] == [("config.json", "conflict")]
        assert place.read_bytes() == b"mine" and [f.name for f in result.copied] == ["tiny-q4_k_m.gguf"]

    def test_without_links_the_hub_list_checks_the_file(self, area):
        old_cache(area.models, links=False)
        files = hf_takeover.old_cache(area.models)
        name = "tiny-q4_k_m.gguf"
        assert hf_takeover.take_over(area.models, files).skipped[0][1] == "no_checksum"
        cases = {"hub": lambda repo, commit: (_ for _ in ()).throw(ConnectionError("offline")),
                 "not_listed": lambda repo, commit: {},
                 "size": lambda repo, commit: {name: HubFile(name, len(WEIGHTS) + 1, WEIGHTS_SHA)},
                 "mismatch": lambda repo, commit: {name: HubFile(name, len(WEIGHTS), "0" * 64)}}
        for code, lookup in cases.items():
            result = hf_takeover.take_over(area.models, files, lookup=lookup)
            assert [c for _, c in result.skipped] == [code] and not new_place(area, name).exists(), code
        asked = []
        result = hf_takeover.take_over(area.models, files, lookup=lambda repo, commit: asked.append((repo, commit))
                                       or {name: HubFile(name, len(WEIGHTS), WEIGHTS_SHA)})
        assert [f.name for f in result.copied] == [name] and asked == [(REPO, OLD_COMMIT)]
        assert new_place(area, name).read_bytes() == WEIGHTS

    def test_a_link_out_of_the_models_folder_is_never_followed(self, area):
        outside = area.tmp / "elsewhere" / hashlib.sha256(b"secret").hexdigest()
        outside.parent.mkdir()
        outside.write_bytes(b"secret")
        snapshot = area.models / "models--acme--tiny-GGUF" / "snapshots" / OLD_COMMIT
        snapshot.mkdir(parents=True)
        (area.models / "models--acme--tiny-GGUF" / "refs").mkdir()
        (snapshot / "x.gguf").symlink_to(outside)
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        assert [c for _, c in result.skipped] == ["link"] and not new_place(area, "x.gguf").exists()

    def test_the_disk_rule_comes_first(self, area, monkeypatch):
        import shutil
        from orchestrator.Core.disk_plan import InsufficientSpace, person_reason
        two_files(area)
        monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=100 * 10 ** 9, used=0,
                                                                            free=2 * 1024 ** 3 + 10))
        with pytest.raises(InsufficientSpace) as refused:
            hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models), ledger=area.ledger)
        assert "the take-over" in str(refused.value) and person_reason(refused.value, "de").startswith("Nicht genug")
        assert not (area.models / "hf").exists() and area.ledger.entries() == []

    def test_the_copy_holds_its_reservation_while_it_runs(self, area):
        from orchestrator.Core.disk_plan import device_of
        two_files(area)
        held = []
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models), ledger=area.ledger,
                              progress=lambda done, total, name: held.append(area.ledger.entries()))
        assert [(e["owner"], e["label"], e["bytes"]) for e in held[0]] == [
            ("gui", "Hugging Face take-over", {device_of(area.models): len(CONFIG) + len(WEIGHTS)})]
        assert area.ledger.entries() == []

    def test_a_stop_ends_it_and_leaves_no_half_file(self, area):
        two_files(area)
        stop = threading.Event()
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models), stop=stop,
                                       progress=lambda done, total, name: stop.set())
        assert result.stopped and result.copied == []
        assert not (area.models / "hf" / "acme--tiny-GGUF" / OLD_COMMIT[:12] / "config.json").exists()
        assert no_temporary_files(area.models)

    def test_only_what_the_cache_listed_for_this_folder_is_copied(self, area, tmp_path):
        two_files(area)
        (file, _) = hf_takeover.old_cache(area.models)
        forged = hf_takeover.OldFile(**{**file.__dict__, "dest": tmp_path / "elsewhere.json"})
        assert hf_takeover.take_over(area.models, [forged]).copied == []
        assert not (tmp_path / "elsewhere.json").exists()


def shared_store(area, repos=("acme/tiny-GGUF",)):
    """The layout of newer huggingface_hub versions: each model folder's blobs/<etag> links into the cache's shared
    store <models_dir>/blobs/<2 hex>/<hash>, where the bytes are."""
    store = area.models / "blobs" / "ab" / ("ab" + "1" * 62)
    store.parent.mkdir(parents=True)
    store.write_bytes(WEIGHTS)
    for repo in repos:
        folder = old_cache(area.models, repo, files={"tiny-q4_k_m.gguf": (WEIGHTS, True)})
        blob = folder / "blobs" / WEIGHTS_SHA
        blob.unlink()
        blob.symlink_to(Path(os.path.relpath(store, blob.parent)))
    return store


class TestTheOldCopiesGoOnlyWithTheYes:

    def test_no_plan_before_the_take_over(self, area):
        two_files(area)
        assert hf_takeover.old_copies_plan(area.models) is None

    def test_the_plan_lists_exactly_the_models_taken_over_completely(self, area):
        folder = two_files(area)
        other = old_cache(area.models, "acme/other", files={"a.gguf": (b"GGUF-a", True), "b.json": (b"{}", False)})
        files = hf_takeover.old_cache(area.models)
        hf_takeover.take_over(area.models, [f for f in files if f.repo == REPO or f.name == "a.gguf"])
        plan = hf_takeover.old_copies_plan(area.models)
        assert [(i.kind, i.full_path, i.file_name, i.size_bytes) for i in plan.items] == [
            ("model_file", str(folder), folder.name, tree_size(folder))]
        assert plan.items[0].speaking_name == f"{REPO} (old download cache)" and other.is_dir()
        assert plan.mode_offered == ("trash", "permanent")

    @pytest.mark.parametrize("stray", [
        "not-a-commit/kept.gguf",
        pytest.param(f"{OLD_COMMIT}/kept\\x.gguf", marks=pytest.mark.skipif(
            os.name == "nt", reason="a backslash cannot be part of a file name on Windows"))])
    def test_a_folder_with_a_file_the_take_over_skips_is_not_offered(self, area, stray):
        # Regression (review of chain hf): old_cache leaves out what it cannot place in the new folder (a revision
        # folder that is no commit, a name the downloader would refuse); the folder that holds it was offered for
        # removal as "taken over completely", and that file would have gone with it, never copied.
        folder = two_files(area)
        kept = folder / "snapshots" / stray
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(b"GGUF-mine")
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        assert {f.state for f in hf_takeover.old_cache(area.models)} == {"taken"}
        assert hf_takeover.old_copies_plan(area.models) is None
        assert kept.read_bytes() == b"GGUF-mine"

    def test_a_shared_store_file_only_the_old_copies_use_is_listed_too(self, area):
        store = shared_store(area)
        result = hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        assert [f.name for f in result.copied] == ["tiny-q4_k_m.gguf"], "a link into the shared store is followed"
        plan = hf_takeover.old_copies_plan(area.models)
        assert [i.full_path for i in plan.items] == [str(area.models / "models--acme--tiny-GGUF"), str(store.resolve())]
        assert plan.items[1].size_bytes == len(WEIGHTS)

    def test_a_shared_store_file_another_model_still_uses_stays(self, area):
        shared_store(area, repos=("acme/tiny-GGUF", "acme/twin-GGUF"))
        files = hf_takeover.old_cache(area.models)
        hf_takeover.take_over(area.models, [f for f in files if f.repo == REPO])
        plan = hf_takeover.old_copies_plan(area.models)
        assert [i.full_path for i in plan.items] == [str(area.models / "models--acme--tiny-GGUF")]

    def test_nothing_goes_without_the_persons_accepted_plan(self, area):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        plan = hf_takeover.old_copies_plan(area.models)
        with pytest.raises(PermissionError):
            hf_takeover.delete_old_copies(area.models, plan, audit=Audit())
        with pytest.raises(PermissionError):
            accept(plan, "mcp")
        accepted = accept(plan, "framework", "permanent")
        with pytest.raises(PermissionError):
            hf_takeover.delete_old_copies(area.models, accepted, audit=Audit())
        assert folder.is_dir()

    def test_for_good_after_the_audit_entry(self, area):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        plan = hf_takeover.old_copies_plan(area.models)
        audit = Audit()
        result = hf_takeover.delete_old_copies(area.models, accept(plan, "gui", "permanent"), audit=audit)
        assert result.done == [f"{REPO} (old download cache)"] and result.freed_bytes == plan.total_bytes
        assert not folder.exists() and new_place(area, "tiny-q4_k_m.gguf").read_bytes() == WEIGHTS
        (entry,) = audit.entries
        assert entry["outcome"] == "old_downloads_deleted" and entry["extra"]["deletion_sha256"] == plan.sha256()
        entries, _, _ = LocalFiles(area.state, {"models": area.models}).list("model")
        assert [e["ref"] for e in entries] == [f"models:hf/acme--tiny-GGUF/{OLD_COMMIT[:12]}/tiny-q4_k_m.gguf"], \
            "the new copy stays listed and usable"

    def test_into_the_trash(self, area):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        plan = hf_takeover.old_copies_plan(area.models)
        trash = Trash(area.state, audit=Audit())
        result = hf_takeover.delete_old_copies(area.models, accept(plan, "gui", "trash"), trash=trash,
                                               audit=Audit())
        assert len(result.trash_ids) == 1 and not folder.exists() and result.freed_bytes == 0
        (moved,) = (area.models / ".trash").iterdir()
        assert (moved / folder.name / "snapshots" / OLD_COMMIT).is_dir()

    def test_a_changed_list_deletes_nothing(self, area):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        accepted = accept(hf_takeover.old_copies_plan(area.models), "gui", "permanent")
        (folder / "snapshots" / OLD_COMMIT / "extra.txt").write_text("new")
        with pytest.raises(PlanChanged):
            hf_takeover.delete_old_copies(area.models, accepted, audit=Audit())
        assert folder.is_dir()

    def test_without_the_audit_log_nothing_is_deleted(self, area):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        accepted = accept(hf_takeover.old_copies_plan(area.models), "gui", "permanent")
        for audit in (None, Audit(fails=True)):
            with pytest.raises(RuntimeError, match="nothing was deleted"):
                hf_takeover.delete_old_copies(area.models, accepted, audit=audit)
        assert folder.is_dir()


@pytest.mark.usefixtures("qapp")
class TestTheBrowsersTakeOver:
    """Tools > Hugging Face Model Browser... > Take over old downloads...: the list and the copy in worker threads,
    a question with "No" as the default, then the deletion dialog with the exact list."""

    @pytest.fixture
    def window(self, area, monkeypatch):
        from orchestrator.Core.model_manager import ModelManager
        from orchestrator.gui import deletion_dialog, huggingface_window as hf, plain
        settings = {"models_dir": str(area.models)}
        config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                                 user_state_dir=str(area.state))
        audit = Audit()
        framework = SimpleNamespace(config=config, resolve_path=lambda p: Path(p), secrets_manager=None,
                                    tool_registry=SimpleNamespace(audit=audit, policy_store=None))
        manager = ModelManager(framework)
        threads = []
        real = manager.take_over
        manager.take_over = lambda *a, **k: threads.append(threading.current_thread() is threading.main_thread()) \
            or real(*a, **k)
        framework.model_manager = manager
        boxes, asked = [], []
        answers = {"question": plain.QMessageBox.Yes}

        def box(parent, kind, title, text, **kwargs):
            boxes.append((kind, text, kwargs.get("default")))
            return answers.get(kind, plain.QMessageBox.Ok)

        monkeypatch.setattr(plain, "plain_box", box)
        decision = {"accepted": None}

        def ask(parent, plan, **kwargs):
            asked.append(plan)
            mode = decision["accepted"]
            return accept(plan, "gui", mode) if mode else None

        monkeypatch.setattr(deletion_dialog, "ask_deletion", ask)
        win = hf.HuggingFaceWindow(framework)
        return SimpleNamespace(win=win, boxes=boxes, asked=asked, answers=answers, decision=decision, audit=audit,
                               threads=threads)

    @staticmethod
    def settle(qapp, win):
        for _ in range(3):
            for worker in (win.scan_worker, win.takeover_worker, win.remove_worker):
                if worker is not None:
                    assert worker.wait(10000)
            qapp.processEvents()

    def test_a_no_to_the_list_keeps_the_old_copies(self, qapp, area, window):
        from orchestrator.gui import plain
        folder = two_files(area)
        assert window.win.takeover_btn.objectName() == "btn_hf_take_over"
        window.win.takeover_btn.click()
        self.settle(qapp, window.win)
        (kind, question, default), *rest = window.boxes
        assert kind == "question" and default == plain.QMessageBox.No
        assert f"{REPO} / tiny-q4_k_m.gguf" in question and f"{REPO} / config.json" in question
        assert window.threads == [False], "the copy runs in a worker thread"
        assert new_place(area, "tiny-q4_k_m.gguf").read_bytes() == WEIGHTS
        assert [p.items[0].full_path for p in window.asked] == [str(folder)]
        assert folder.is_dir() and window.audit.entries == [], "No: nothing is removed"

    def test_the_question_says_no_by_default_and_no_copies_nothing(self, qapp, area, window):
        from orchestrator.gui import plain
        two_files(area)
        window.answers["question"] = plain.QMessageBox.No
        window.win.take_over()
        self.settle(qapp, window.win)
        assert window.boxes[0][2] == plain.QMessageBox.No
        assert not (area.models / "hf").exists() and window.asked == []

    def test_a_yes_to_the_list_removes_exactly_the_old_copies(self, qapp, area, window):
        folder = two_files(area)
        window.decision["accepted"] = "permanent"
        window.win.take_over()
        self.settle(qapp, window.win)
        assert not folder.exists() and new_place(area, "config.json").read_bytes() == CONFIG
        assert [e["outcome"] for e in window.audit.entries] == ["old_downloads_deleted"]
        assert any(f"{REPO} (old download cache)" in text for _, text, _ in window.boxes)

    def test_the_stop_button_says_what_a_stopped_take_over_keeps(self, qapp):
        # Regression (review of chain hf): Stop during a take-over said "the part downloaded so far is kept"; a
        # take-over keeps the files copied so far and drops the one it was copying.
        from orchestrator.gui import huggingface_window as hf
        from orchestrator.utils.localization import ui_text
        started = threading.Event()

        class Manager:
            def take_over(self, files, stop=None, progress=None):
                started.set()
                assert stop.wait(10)
                return hf_takeover.TakeOverResult(stopped=True)

            def old_copies_plan(self):
                return None

        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=Manager(), secrets_manager=None))
        win.takeover_worker = hf.TakeOverWorker(win.model_manager, [], lang="en")
        win.takeover_worker.start()
        assert started.wait(10)
        win.stop_running()
        assert win.takeover_worker.wait(10000)
        assert win.status_label.text() == ui_text("hf.takeover.stopping", win.lang)
        assert "downloaded" not in win.status_label.text()

    def test_nothing_to_take_over_says_so(self, qapp, area, window):
        window.win.take_over()
        self.settle(qapp, window.win)
        assert [kind for kind, _, _ in window.boxes] == ["information"] and window.asked == []


class TestTheCommandLinesTakeOver:
    """`llm-cli models take-over` (headless installations): the same core calls as the browser's Take over old
    downloads... (ModelManager.old_downloads, take_over, old_copies_plan, delete_old_copies), the full list and the yes
    question with "No" as the default, then the full deletion list (confirm_deletion: trash or for good, "No" as the
    default); only at a terminal."""

    @pytest.fixture
    def cli(self, area, monkeypatch):
        import io
        from click.testing import CliRunner
        from rich.console import Console
        from orchestrator import cli as cli_module
        from orchestrator.Core.model_manager import ModelManager
        settings = {"models_dir": str(area.models)}
        config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                                 user_state_dir=str(area.state))
        audit = Audit()
        framework = SimpleNamespace(config=config, resolve_path=lambda p: Path(p), secrets_manager=None,
                                    tool_registry=SimpleNamespace(audit=audit))
        framework.model_manager = ModelManager(framework)
        trash = Trash(area.state, audit=Audit())
        monkeypatch.setattr(cli_module, "_cli_trash", lambda ctx: trash)

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = framework
                return self

        def invoke(answer=None, terminal=True):
            printed = io.StringIO()
            # wide enough that the deletion table never folds a full path (Windows temp paths are long)
            monkeypatch.setattr(cli_module, "console", Console(file=printed, width=2000, color_system=None))
            monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
            result = CliRunner().invoke(cli_module.cli, ["models", "take-over"], obj=Ctx(), input=answer)
            return result, printed.getvalue() + result.output

        return SimpleNamespace(invoke=invoke, audit=audit, trash=trash, settings=settings)

    def test_without_a_terminal_nothing_is_copied(self, area, cli):
        folder = two_files(area)
        result, out = cli.invoke(terminal=False)
        assert result.exit_code == 0 and "Nothing was copied: the take-over needs your answer at a terminal" in out
        assert f"{REPO} / tiny-q4_k_m.gguf" in out and not (area.models / "hf").exists() and folder.is_dir()

    def test_the_full_list_and_no_by_default(self, area, cli):
        from orchestrator.utils.localization import ui_text
        names = {f"part-{i:02d}.gguf": (WEIGHTS + bytes([i]), True) for i in range(20)}   # more than the GUI shows
        old_cache(area.models, REPO, files=names)
        result, out = cli.invoke(answer="\n")
        assert all(f"{REPO} / {name}" in out for name in names), "every file is listed"
        assert ui_text("hf.takeover.ask", "en").split("{n}")[0] in out and "[y/N]" in out
        assert "Nothing was copied." in out and not (area.models / "hf").exists()

    def test_a_yes_copies_then_the_old_copies_stay_without_a_second_yes(self, area, cli):
        folder = two_files(area)
        result, out = cli.invoke(answer="y\n\n\n")   # copy; then the trash (default) and No (default)
        assert result.exit_code == 0, out
        assert new_place(area, "tiny-q4_k_m.gguf").read_bytes() == WEIGHTS
        assert new_place(area, "config.json").read_bytes() == CONFIG
        assert "Copied 2 of 2 files" in out and str(folder) in out, "the deletion list names the full path"
        assert "Nothing was deleted." in out and folder.is_dir() and cli.audit.entries == []

    def test_a_yes_to_the_list_removes_exactly_the_old_copies(self, area, cli):
        folder = two_files(area)
        result, out = cli.invoke(answer="y\npermanent\ny\n")
        assert result.exit_code == 0, out
        assert f"Deleted for good: {REPO} (old download cache)" in out and not folder.exists()
        (entry,) = cli.audit.entries
        assert entry["actor"] == "cli" and entry["outcome"] == "old_downloads_deleted"
        assert new_place(area, "config.json").read_bytes() == CONFIG

    def test_into_the_trash(self, area, cli):
        folder = two_files(area)
        result, out = cli.invoke(answer="y\ntrash\ny\n")
        assert f"Moved to the trash: {REPO} (old download cache)" in out and not folder.exists()
        assert len(cli.trash.entries()) == 1

    def test_after_a_take_over_only_the_removal_is_offered(self, area, cli):
        folder = two_files(area)
        hf_takeover.take_over(area.models, hf_takeover.old_cache(area.models))
        result, out = cli.invoke(answer="\n\n")
        assert "Copy exactly" not in out and str(folder) in out and "Nothing was deleted." in out
        assert folder.is_dir()

    def test_nothing_to_take_over_says_so(self, cli):
        from orchestrator.utils.localization import ui_text
        result, out = cli.invoke()
        assert result.exit_code == 0 and ui_text("hf.takeover.none", "en") in out

    def test_offline_it_behaves_as_the_browser(self, area, cli):
        """Offline mode: the take-over copies what the old cache can check itself; a file only Hugging Face could
        check stays, with the reason the GUI shows (hf.takeover.why.offline), and Hugging Face is not asked."""
        from orchestrator.utils.localization import ui_text
        cli.settings["offline_mode"] = True
        old_cache(area.models, REPO, files={"plain.gguf": (WEIGHTS, True)}, links=False)
        result, out = cli.invoke(answer="y\n")
        assert ui_text("hf.takeover.why.offline", "en") in out and "Copied 0 of 1 files" in out
        assert not new_place(area, "plain.gguf").exists()

    def test_the_command_is_in_the_parity_table(self):
        from orchestrator.gui.menus import existing
        assert existing.CLI_PATHS["models take-over"] == "btn_hf_take_over"
        assert existing.WINDOW_OBJECTS["btn_hf_take_over"] == "act_tools_hf_browser"
