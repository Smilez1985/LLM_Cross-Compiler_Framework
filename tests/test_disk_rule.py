"""One disk rule for every free-space check (decision A4, Core/disk_plan.py).

Every check keeps the reserve free (2 GiB or 2 % of the disk, whichever is larger; 20 GiB or 10 % under a standing
approval) beside what accepted calls of any process reserved in the cross-process ReservationLedger:
- downloads (the local runtime's archives and models, the community zoo's packs, an AI's Hugging Face file) count the
  space a running build reserved and refuse with a plain reason and the next step, in English and German for the
  person; a reservation of a process that has ended never blocks (the ledger's liveness rule);
- a deployment package holds its space in the ledger until it ends, as a download does;
- Mojo's disk verdicts, the AI's disk fact, the bench copy, the knowledge-base conversion and the snapshot export
  count the reservations too.
tests/test_disk_callers.py pins how each check reads the disk and its numbers.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

GB = 10 ** 9
GIB = 1024 ** 3


def fixed(free, total=1000 * GB):
    return lambda path: shutil._ntuple_diskusage(total, total - free, free)


@pytest.fixture
def held(tmp_path):
    """A running build of this process reserved 30 GB on the disk of `tmp_path` (in the ledger of tmp/state)."""
    from orchestrator.Core import disk_plan
    ledger = disk_plan.ReservationLedger(tmp_path / "state")
    rid, _ = ledger.reserve([disk_plan.DiskNeed("cache", tmp_path, 30 * GB, "a build")], "gui", "a build",
                            disk_usage=fixed(900 * GB))
    yield ledger
    ledger.release(rid)


# --- downloads count what a running build reserved ------------------------------------------------------------------

class TestDownloads:

    def test_a_download_the_running_build_leaves_no_room_for_is_refused_with_the_next_step(self, tmp_path, held,
                                                                                          monkeypatch):
        from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
        monkeypatch.setattr(shutil, "disk_usage", fixed(60 * GB))   # 20 GB stay free on 1000 GB
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        rt._ensure_space(tmp_path / "a", 10 * GB)                     # beside the build's 30 GB: fits
        with pytest.raises(LocalRuntimeError) as e:
            rt._ensure_space(tmp_path / "a", 10 * GB + 1)            # one byte more fits only without the build
        text = str(e.value)
        assert text.startswith("Not enough disk space for the download: download disk: 10.0 GB needed, 60.0 GB free, "
                               "30.0 GB reserved by other calls, 20.0 GB must stay free.")
        assert text.endswith("A running build or download has reserved 30.0 GB on that disk: try again once it has "
                             "finished (its reservation ends with it, also when its program has ended), or free disk "
                             "space.")
        assert e.value.code == "insufficient_resources" and str(tmp_path) not in text
        assert [s.id for s in e.value.steps] == ["build.history"], "the person's way to the running build"
        assert "running build or download has reserved 30.0 GB" in e.value.next, "and the AI's next step"

    def test_the_person_reads_why_in_english_and_german(self, tmp_path, held, monkeypatch):
        from orchestrator.Core.disk_plan import person_reason
        from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
        monkeypatch.setattr(shutil, "disk_usage", fixed(60 * GB))
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        with pytest.raises(LocalRuntimeError) as e:
            rt._ensure_space(tmp_path / "a", 12 * GB)
        assert person_reason(e.value, "en") == (
            "Not enough disk space: 12.0 GB needed, 60.0 GB free, 20.0 GB must stay free. A running build or download "
            "has reserved 30.0 GB on that disk: try again once it has finished (its reservation ends with it, also "
            "when its program has ended), or free disk space.")
        assert person_reason(e.value, "de") == (
            "Nicht genug Speicherplatz: 12,0 GB nötig, 60,0 GB frei, 20,0 GB müssen frei bleiben. Ein laufender Build "
            "oder Download hat 30,0 GB auf dieser Festplatte reserviert: noch einmal versuchen, sobald er fertig ist "
            "(seine Reservierung endet mit ihm, auch wenn sein Programm beendet wurde), oder Speicherplatz freigeben.")
        assert person_reason(ValueError("something else"), "de") == "something else", "any other error: its text"

    def test_a_full_disk_without_reservations_names_freeing_space(self, tmp_path, monkeypatch):
        from orchestrator.Core.disk_plan import person_reason
        from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
        monkeypatch.setattr(shutil, "disk_usage", fixed(21 * GB))
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        with pytest.raises(LocalRuntimeError) as e:
            rt._ensure_space(tmp_path / "a", 2 * GB)
        assert str(e.value).endswith("Free disk space (old builds, the model cache, the trash), or choose a smaller "
                                     "model.")
        assert e.value.steps == ()
        assert person_reason(e.value, "de") == ("Nicht genug Speicherplatz: 2,0 GB nötig, 21,0 GB frei, 20,0 GB "
                                                "müssen frei bleiben. Speicherplatz freigeben (alte Builds, den "
                                                "Modell-Cache, den Papierkorb) oder ein kleineres Modell wählen.")

    def test_a_reservation_of_a_process_that_has_ended_never_blocks(self, tmp_path, monkeypatch):
        """R0: the ledger's own liveness rule drops the reservation of an ended program when it is read."""
        from orchestrator.Core import disk_plan
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        ended = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                               text=True, check=True)
        state = tmp_path / "state"
        state.mkdir()
        device = disk_plan.device_of(tmp_path)
        (state / disk_plan.LEDGER_FILE).write_text(json.dumps({"version": 1, "reservations": [{
            "id": "r_ended", "owner": "gui", "label": "a build", "pid": int(ended.stdout), "process_started": 1.0,
            "created": datetime.now().isoformat(timespec="seconds"), "bytes": {device: 900 * GB}, "landed": {}}]}))
        monkeypatch.setattr(shutil, "disk_usage", fixed(45 * GB))
        LocalLLMRuntime(data_dir=state, models_dir=tmp_path / "models")._ensure_space(tmp_path / "a", 20 * GB)
        assert disk_plan.ReservationLedger(state).outstanding(device) == 0

    def test_the_zoo_counts_the_ledger_of_the_per_user_folder(self, tmp_path, held, monkeypatch):
        from orchestrator.Core.community_manager import CommunityManager
        from orchestrator.utils.download import DownloadError
        monkeypatch.setattr(shutil, "disk_usage", fixed(60 * GB))
        zoo = CommunityManager(SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path / "state"))))
        zoo._downloads().ensure_space(tmp_path / "packs", 10 * GB)
        with pytest.raises(DownloadError) as e:
            zoo._downloads().ensure_space(tmp_path / "packs", 10 * GB + 1)
        assert "30.0 GB reserved by other calls" in str(e.value) and e.value.code == "insufficient_resources"

    def test_the_zoo_install_keeps_the_refusal_for_the_community_hub(self, tmp_path, monkeypatch):
        """The Community Hub shows person_reason of a failed install: the pack download's refusal must reach it with
        its disk numbers, reason code and steps (prepare_install wraps the download error in a PackError)."""
        from test_zoo import World

        from orchestrator.Core.community_pack import PackError
        from orchestrator.Core.disk_plan import person_reason
        world = World(tmp_path)
        world.add(world.target_pack())
        world.publish()
        monkeypatch.setattr(shutil, "disk_usage", fixed(1 * GB))   # 20 GB must stay free on 1000 GB
        with pytest.raises(PackError) as e:
            world.cm.prepare_install("target", "demo-board")
        assert str(e.value).startswith("Not enough disk space for the download: download disk: ")
        assert e.value.code == "insufficient_resources"
        assert person_reason(e.value, "de").startswith("Nicht genug Speicherplatz: "), "the window's German text"
        assert list((world.root / "targets" / ".quarantine").iterdir()) == [], "nothing is left in quarantine"

    def test_the_ai_disk_check_counts_the_ledger(self, tmp_path, held, monkeypatch):
        """download_hf_file counted the ledger already (design 6.5); the package and runtime build checks now too."""
        from orchestrator.Core import hf_hub
        monkeypatch.setattr(shutil, "disk_usage", fixed(60 * GB))
        assert hf_hub.disk_check(tmp_path, 10 * GB, held)["fits"]
        disk = hf_hub.disk_check(tmp_path, 10 * GB + 1, held)
        assert not disk["fits"] and disk["reserved"] == 30 * GB


# --- a deployment package holds its space in the ledger -------------------------------------------------------------

class TestPackage:

    @pytest.fixture
    def deploy(self):
        pytest.importorskip("paramiko")
        import test_deploy_tools as dt
        return dt

    def test_the_check_counts_a_running_build_and_the_package_holds_its_space(self, make_framework, deploy,
                                                                             monkeypatch):
        from orchestrator.Core import disk_plan
        from orchestrator.tools.policy import ToolPolicy
        from orchestrator.tools.registry import ToolError
        fw = make_framework()
        fw.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fw.tool_registry.list()]))
        aid = deploy.build(fw, size=10 ** 6)
        ledger = disk_plan.ReservationLedger(deploy.state(fw))
        out = Path(fw.resolve_path(fw.config.get("output_dir", "output")))
        rid, _ = ledger.reserve([disk_plan.DiskNeed("cache", out, 980 * GB, "a build")], "gui", "a build",
                                disk_usage=fixed(1000 * GB, total=1000 * GB))
        try:   # 600 GB free on the roomy test disk: 980 GB reserved leave nothing
            with pytest.raises(ToolError) as e:
                deploy.call(fw, "create_deploy_package", {"artifact_id": aid}, confirm=deploy.asking(True))
            assert e.value.code == "insufficient_resources"
            assert "reserved by other calls (running builds or downloads)" in str(e.value)
        finally:
            ledger.release(rid)
        dm = fw.get_component("deployment_manager")
        release, seen = threading.Event(), []
        real = dm.create_package

        def slow(*a, **kw):
            seen.append([e["label"] for e in ledger.entries()])
            release.wait(10)
            return real(*a, **kw)
        monkeypatch.setattr(dm, "create_package", slow)
        started = deploy.call(fw, "create_deploy_package", {"artifact_id": aid}, confirm=deploy.asking(True))
        while not seen and deploy.wait_job(fw, started.job_id, 0.2).state == "running":
            pass
        release.set()
        assert deploy.wait_job(fw, started.job_id).state == "done"
        assert seen and seen[0] and seen[0][0].startswith("package "), "held in the ledger while it is made"
        assert ledger.entries() == [], "released when it ends"

    def test_space_taken_while_the_person_is_asked_refuses_the_package_by_its_own_name(self, make_framework, deploy):
        """The ledger checks again under its lock when the package takes its reservation: the refusal names the
        package (it said "this build") and its size as an estimate (twice the model)."""
        from orchestrator.Core import disk_plan
        from orchestrator.tools.policy import ToolPolicy
        from orchestrator.tools.registry import ToolError
        fw = make_framework()
        fw.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fw.tool_registry.list()]))
        aid = deploy.build(fw, size=10 ** 6)
        ledger = disk_plan.ReservationLedger(deploy.state(fw))
        out = Path(fw.resolve_path(fw.config.get("output_dir", "output")))
        taken = []

        def confirm(spec, inp, *rest):   # another call reserves the disk while the person is asked
            taken.append(ledger.reserve([disk_plan.DiskNeed("cache", out, 980 * GB, "a build")], "gui", "a build",
                                        disk_usage=fixed(1000 * GB))[0])
            return True
        try:
            with pytest.raises(ToolError) as e:
                deploy.call(fw, "create_deploy_package", {"artifact_id": aid}, confirm=confirm)
        finally:
            for rid in taken:
                ledger.release(rid)
        assert taken and e.value.code == "insufficient_resources"
        assert "Not enough disk space for the deployment package: output disk: 2 MB needed (estimate), " in \
            str(e.value)
        assert "this build" not in str(e.value) and ledger.entries() == []
        assert e.value.message.endswith("A running build or download has reserved 980.0 GB on that disk: try again "
                                        "once it has finished (its reservation ends with it, also when its program "
                                        "has ended), or free disk space.")
        assert "running build or download has reserved 980.0 GB" in e.value.next, "the AI's next step: wait for it"

    def test_a_download_whose_space_went_meanwhile_is_refused_as_the_disk_rule_says(self, tmp_path, held,
                                                                                   monkeypatch):
        """hf_hub.reserve_space (an AI's Hugging Face download): the ledger's second check under its lock refuses
        with the one rule's refusal, not a build's text and the generic next step."""
        from orchestrator.Core import disk_plan, hf_hub
        monkeypatch.setattr(shutil, "disk_usage", fixed(60 * GB))
        with pytest.raises(disk_plan.InsufficientSpace) as e:
            hf_hub.reserve_space(held, tmp_path, 10 * GB + 1, "mcp", "o/r/x.gguf", need_label="models",
                                 what="download x.gguf", key="o/r/c/x.gguf")
        assert str(e.value).startswith("Not enough disk space for the download: models disk: 10.0 GB needed, "
                                       "60.0 GB free, 30.0 GB reserved by other calls, 20.0 GB must stay free.")
        assert e.value.next == disk_plan.RESERVED_NEXT_AI.format(reserved="30.0 GB")
        assert [s.id for s in e.value.steps] == ["build.history"]
        assert len(held.entries()) == 1, "nothing was reserved"

    def test_without_a_ledger_the_space_is_held_in_this_process(self, tmp_path):
        from orchestrator.Core import hf_hub
        held = hf_hub.reserve_space(None, tmp_path, 7, "mcp", "x", need_label="output", what="package", key="t/p")
        assert hf_hub._RESERVED.get("t/p") == 7
        held.release()
        assert "t/p" not in hf_hub._RESERVED


# --- the other checks count the reservations as well ----------------------------------------------------------------

def test_mojo_counts_what_running_builds_and_downloads_reserved(tmp_path, held, monkeypatch):
    from orchestrator.Core.mojo.checks import disk as mojo_disk
    cache = tmp_path / "cache"
    cache.mkdir()
    framework = SimpleNamespace(orchestrator=SimpleNamespace(build_engine=SimpleNamespace(
        cache_dir=cache, output_dir=None, build_dir=None)), config=SimpleNamespace(user_state_dir=str(tmp_path / "state")))
    monkeypatch.setattr(shutil, "disk_usage", fixed(45 * GB))
    (finding,) = mojo_disk.check_disk(framework)
    assert finding.severity.value == "error", "45 GB free, 30 GB of it reserved: less than the 20 GB reserve stays"
    assert finding.message == (f"The disk of the cache folder ({cache}) has only 45.0 GB free (30.0 GB of it reserved "
                               "by running builds or downloads); 20.0 GB must stay free.")


def test_the_ai_disk_fact_counts_them(tmp_path, held, monkeypatch):
    from orchestrator.tools import tools_status
    values = {"cache_dir": str(tmp_path)}
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                                                       user_state_dir=str(tmp_path / "state")))
    monkeypatch.setattr(shutil, "disk_usage", fixed(45 * GB))
    assert tools_status.disk_fact(framework) == (
        "confirmed", ["The cache disk has 45.0 GB free, 30.0 GB of it reserved by running builds or downloads."], [])


def test_the_knowledge_base_conversion_counts_them(tmp_path, held):
    from orchestrator.rag import migration
    migration._check_room(tmp_path / "other", 25 * GB, disk_usage=fixed(70 * GB))   # another state folder: no ledger
    with pytest.raises(migration.ConversionFailed) as e:
        migration._check_room(tmp_path / "state", 25 * GB, disk_usage=fixed(70 * GB))
    assert "70.0 GB are free, 30.0 GB of them reserved by running builds or downloads" in str(e.value)


def test_the_snapshot_export_counts_them(tmp_path, held, monkeypatch):
    from orchestrator.Core import knowledge_snapshots as ks
    folder = tmp_path / "state" / ks.FOLDER
    folder.mkdir(parents=True)
    (folder / f"s1{ks.SUFFIX}").write_bytes(b"x" * 1000)
    target = tmp_path / "usb"
    target.mkdir()
    monkeypatch.setattr(shutil, "disk_usage", fixed(45 * GB))
    with pytest.raises(ValueError) as e:
        ks.export_snapshots(folder, [ks.list_snapshots(folder)[0].name], target)
    assert "30.0 GB of it reserved by running builds or downloads" in str(e.value)


@pytest.mark.skipif(os.name != "posix", reason="the sandbox's descriptor links are POSIX")
def test_the_bench_copy_counts_them(tmp_path, monkeypatch):
    from test_closing_leftovers import FakeEngine
    from test_closing_leftovers import sandbox_bench as fixture

    from orchestrator.Core import disk_plan
    env = fixture.__wrapped__(tmp_path, monkeypatch)
    ledger = disk_plan.ReservationLedger(tmp_path / "state")
    rid, _ = ledger.reserve([disk_plan.DiskNeed("cache", tmp_path, 30 * GB, "a build")], "gui", "a build",
                            disk_usage=fixed(900 * GB))
    try:
        monkeypatch.setattr(env.bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        monkeypatch.setattr(shutil, "disk_usage", fixed(45 * GB))
        with pytest.raises(env.bench.BenchError) as e:
            env.make(FakeEngine()).run(env.model, kind="artifact_speed", label="output:Rk/m", trusted=False,
                                       sha256=env.digest, sandbox=True)
        assert e.value.code == "insufficient_resources"
        assert "and 30.0 GB are reserved by running builds or downloads; 45.0 GB free" in str(e.value)
    finally:
        ledger.release(rid)
