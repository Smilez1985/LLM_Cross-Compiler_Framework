"""The free-space checks outside the build engine (consolidation chain c3-disk-callers, finding 5).

Characterization first: these tests pin each check's margin and verdict text as it is before the checks delegate to
Core/disk_plan.py, and they stay green after the delegation (each margin becomes an explicit parameter there).
- hf_hub.disk_check: reserve_for (2 GiB or 2 %; 20 GiB or 10 % for a standing approval), what other calls reserved
  (the ledger, else this process's downloads), the nearest existing folder's disk, an unreadable disk raises.
- PinnedDownloader.ensure_space (the local runtime's and the community zoo's downloads): 256 MiB beside the download.
- the bench sandbox's private copy: 512 MiB; the knowledge-base conversion: 512 MiB left free afterwards.
- the knowledge snapshot export: no margin at all.
- Mojo's disk check: 5 and 20 "GB", which are binary values (Core/mojo/findings.GB).
- the self-healing diagnosis and the AI's disk fact: below reserve_for is "full".
- the saved model: a rename on the same disk (artifact_output._device), a copy otherwise.
Also: an AI reads labels, never paths, and the disk_usage test seams the suite relies on keep working.
"""

import os
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_hf_tools import REPO as HF_REPO
from test_hf_tools import call as hf_call
from test_hf_tools import env as hf_tools_env

GIB = 1024 ** 3
MIB = 1024 ** 2
GB = 10 ** 9


def usage(free, total=1000 * GB):
    return shutil._ntuple_diskusage(total, total - free, free)


def fixed(free, total=1000 * GB, seen=None):
    def du(path):
        if seen is not None:
            seen.append(str(path))
        return usage(free, total)
    return du


# --- hf_hub.disk_check (download_hf_file, create_deploy_package, build_local_runtime) ------------------------------

class TestHubDiskCheck:

    def test_the_numbers_and_the_reserve(self, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan, hf_hub
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(50 * GB))
        disk = hf_hub.disk_check(tmp_path, 10 * GB)
        reserve = disk_plan.reserve_for(1000 * GB)
        assert reserve == 20 * GB, "2 % of 1000 GB is more than 2 GiB"
        assert disk == {"free": 50 * GB, "reserve": reserve, "reserved": 0, "needed": 10 * GB, "fits": True,
                        "after": 40 * GB, "tight": False, "fits_standing": False}, \
            "40 GB left: fits; the standing reserve (100 GB, 10 %) does not"

    @pytest.mark.parametrize("free, fits, tight", [(40 * GB, True, False), (40 * GB - 1, True, True),
                                                   (20 * GB, True, True), (20 * GB - 1, False, False)])
    def test_the_verdict_at_the_edges(self, tmp_path, monkeypatch, free, fits, tight):
        """fits: at least the reserve (20 GB of 1000 GB) stays free; tight: it fits with less than twice that."""
        from orchestrator.Core import hf_hub
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(free))
        disk = hf_hub.disk_check(tmp_path, 0)
        assert (disk["fits"], disk["tight"]) == (fits, tight)

    def test_a_small_disk_keeps_two_gib_and_a_standing_approval_twenty(self, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(25 * GIB, total=50 * GB))
        disk = hf_hub.disk_check(tmp_path, 5 * GIB)
        assert disk["reserve"] == 2 * GIB and disk["fits"]
        assert disk["fits_standing"] is True, "20 GiB stay free: exactly the standing reserve"
        disk = hf_hub.disk_check(tmp_path, 5 * GIB + 1)
        assert disk["fits_standing"] is False

    def test_without_a_ledger_only_this_process_downloads_count(self, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(50 * GB))
        held = hf_hub.Reservation("test/one", 25 * GB)
        try:
            disk = hf_hub.disk_check(tmp_path, 10 * GB)
            assert disk["reserved"] == 25 * GB and disk["after"] == 15 * GB and not disk["fits"]
        finally:
            held.release()
        assert hf_hub.disk_check(tmp_path, 10 * GB)["reserved"] == 0

    def test_with_the_ledger_every_process_counts_on_that_disk(self, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan, hf_hub
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(50 * GB))
        ledger = disk_plan.ReservationLedger(tmp_path / "state")
        rid, _ = ledger.reserve([disk_plan.DiskNeed("cache", tmp_path, 12 * GB, "a build")], "gui", "a build",
                                disk_usage=fixed(500 * GB))
        held = hf_hub.Reservation("test/two", 7 * GB)   # this process's own: the ledger replaces it
        try:
            disk = hf_hub.disk_check(tmp_path, 1 * GB, ledger)
            assert disk["reserved"] == 12 * GB and disk["after"] == 37 * GB
        finally:
            held.release()
            ledger.release(rid)

    def test_a_folder_not_made_yet_is_measured_on_its_parent(self, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub
        seen = []
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", fixed(50 * GB, seen=seen))
        hf_hub.disk_check(tmp_path / "not" / "yet", 1)
        assert seen == [str(tmp_path)]

    def test_an_unreadable_disk_raises(self, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub

        def broken(path):
            raise PermissionError(13, "denied")
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", broken)
        with pytest.raises(OSError):
            hf_hub.disk_check(tmp_path, 1)


# --- PinnedDownloader.ensure_space (runtime and zoo downloads) ------------------------------------------------------

class TestDownloadSpace:

    def test_256_mib_must_stay_beside_the_download(self, tmp_path, monkeypatch):
        from orchestrator.utils import download
        folder = tmp_path / "new"
        monkeypatch.setattr(download.shutil, "disk_usage", fixed(3 * GIB + 256 * MIB))
        download.PinnedDownloader().ensure_space(folder, 3 * GIB)
        assert folder.is_dir(), "the folder is made first"
        monkeypatch.setattr(download.shutil, "disk_usage", fixed(3 * GIB + 256 * MIB - 1))
        with pytest.raises(download.DownloadError) as e:
            download.PinnedDownloader().ensure_space(folder, 3 * GIB)
        assert str(e.value) == f"Not enough free disk space in {folder} for the download: 3.0 GiB needed, 3.2 GiB free."

    def test_the_runtime_raises_its_own_error_with_what_it_needs_the_space_for(self, tmp_path, monkeypatch):
        from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
        from orchestrator.utils import download
        monkeypatch.setattr(download.shutil, "disk_usage", fixed(1 * GIB))
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        with pytest.raises(LocalRuntimeError) as e:
            rt._ensure_space(rt.root, 2 * GIB, what="unpacking")
        assert str(e.value) == f"Not enough free disk space in {rt.root} for unpacking: 2.0 GiB needed, 1.0 GiB free."

    def test_fetch_checks_what_is_still_to_download_next_to_the_part(self, tmp_path, monkeypatch):
        from orchestrator.utils import download
        asked = []
        loader = download.PinnedDownloader()
        monkeypatch.setattr(loader, "ensure_space", lambda folder, needed, what="the download": asked.append(
            (Path(folder), needed)) or (_ for _ in ()).throw(download.DownloadError("no room")))
        dest = tmp_path / "dl" / "m.gguf"
        dest.parent.mkdir()
        dest.with_name("m.gguf.part").write_bytes(b"x" * 10)
        with pytest.raises(download.DownloadError):
            loader.fetch("https://example.invalid/m.gguf", dest, 100, "0" * 64)
        assert asked == [(dest.parent, 90)]


# --- the bench sandbox's private copy -------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="the sandbox's descriptor links are POSIX")
class TestBenchCopyRoom:

    @staticmethod
    def run(env, free, monkeypatch):
        from test_closing_leftovers import FakeEngine
        monkeypatch.setattr(env.bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        monkeypatch.setattr(env.bench.shutil, "disk_usage", fixed(free))
        engine = FakeEngine()
        result = env.make(engine).run(env.model, kind="artifact_speed", label="output:Rk/m", trusted=False,
                                      sha256=env.digest, sandbox=True)
        return result, engine

    def test_a_copy_leaves_512_mib(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        size = env.model.stat().st_size
        assert env.bench.COPY_RESERVE == 512 * MIB
        result, engine = self.run(env, size + 512 * MIB, monkeypatch)
        assert result.gen_ts == 10.0 and len(engine.runs) == 1

    def test_one_byte_less_is_no_room(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        size = env.model.stat().st_size
        with pytest.raises(env.bench.BenchError) as e:
            self.run(env, size + 512 * MIB - 1, monkeypatch)
        assert e.value.code == "insufficient_resources"
        assert "(about 0.5 GB needed)" in str(e.value), "the copy plus what it leaves, in decimal GB"


@pytest.fixture
def sandbox_bench(tmp_path, monkeypatch):
    from test_closing_leftovers import sandbox_bench as fixture
    return fixture.__wrapped__(tmp_path, monkeypatch)


# --- the knowledge-base conversion (rag/migration.py) ---------------------------------------------------------------

class TestConversionRoom:

    def test_512_mib_stay_free_afterwards(self, tmp_path):
        from orchestrator.rag import migration
        assert migration.FREE_AFTER == 512 * MIB
        migration._check_room(tmp_path, 4 * GB, disk_usage=fixed(4 * GB + 512 * MIB))
        with pytest.raises(migration.ConversionFailed) as e:
            migration._check_room(tmp_path, 4 * GB, disk_usage=fixed(4 * GB + 512 * MIB - 1))
        assert e.value.code == "disk_space"
        assert str(e.value) == ("Not enough disk space to convert the knowledge base to Qdrant Edge: it needs about "
                                "4.0 GB beside the old storage and 537 MB left free, 4.5 GB are free. It keeps working "
                                "in the old storage; the conversion is tried again on the next start.")

    def test_measured_on_the_nearest_existing_folder_and_unreadable_is_unchecked(self, tmp_path):
        from orchestrator.rag import migration
        seen = []
        migration._check_room(tmp_path / "not" / "yet", 1, disk_usage=fixed(10 * GB, seen=seen))
        assert seen == [str(tmp_path)]

        def broken(path):
            raise OSError("unreadable")
        migration._check_room(tmp_path, 10 ** 15, disk_usage=broken)   # nothing raised: not checked


# --- the knowledge snapshot export ----------------------------------------------------------------------------------

class TestSnapshotExportRoom:

    @staticmethod
    def snapshot(tmp_path):
        from orchestrator.Core import knowledge_snapshots as ks
        folder = tmp_path / "state" / ks.FOLDER
        folder.mkdir(parents=True)
        (folder / f"s1{ks.SUFFIX}").write_bytes(b"x" * 1000)
        target = tmp_path / "usb"
        target.mkdir()
        return ks, folder, target

    def test_no_margin_exactly_the_bytes_fit(self, tmp_path, monkeypatch):
        ks, folder, target = self.snapshot(tmp_path)
        name = ks.list_snapshots(folder)[0].name
        monkeypatch.setattr(ks.shutil, "disk_usage", lambda p: SimpleNamespace(free=1000))
        (result,) = ks.export_snapshots(folder, [name], target)
        assert result.ok

    def test_one_byte_short_is_refused_naming_the_destination(self, tmp_path, monkeypatch):
        ks, folder, target = self.snapshot(tmp_path)
        name = ks.list_snapshots(folder)[0].name
        monkeypatch.setattr(ks.shutil, "disk_usage", lambda p: SimpleNamespace(free=999))
        with pytest.raises(ValueError) as e:
            ks.export_snapshots(folder, [name], target)
        assert str(e.value) == f"{target} has 0.0 MB free; the export needs 0.0 MB."


# --- Mojo's disk check ----------------------------------------------------------------------------------------------

class TestMojoDisk:

    @staticmethod
    def framework(tmp_path):
        cache, out = tmp_path / "cache", tmp_path / "out"
        cache.mkdir()
        engine = SimpleNamespace(cache_dir=cache, output_dir=out, build_dir=None)
        return SimpleNamespace(orchestrator=SimpleNamespace(build_engine=engine)), cache, out

    @pytest.mark.parametrize("free_gib, severity", [(4.9, "error"), (5, "warn"), (19.9, "warn"), (20, "ok")])
    def test_five_and_twenty_binary_gb(self, tmp_path, monkeypatch, free_gib, severity):
        from orchestrator.Core.mojo.checks import disk as mojo_disk
        framework, cache, _out = self.framework(tmp_path)
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", fixed(int(free_gib * GIB)))
        (finding,) = mojo_disk.check_disk(framework)   # cache and output (not made yet) share a disk: one line
        assert finding.severity.value == severity and finding.code == "disk.cache"

    def test_the_texts(self, tmp_path, monkeypatch):
        from orchestrator.Core.mojo.checks import disk as mojo_disk
        framework, cache, _out = self.framework(tmp_path)
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", fixed(int(4.25 * GIB)))
        (error,) = mojo_disk.check_disk(framework)
        assert error.message == f"Only 4.2 GB free for the cache folder ({cache})."
        assert error.fix_hint.startswith("Free disk space; large models need 2-3x their size during conversion. ")
        assert error.where == str(cache) and error.fix_tier == 2 and error.cannot == "deletion_list"
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", fixed(int(12.25 * GIB)))
        (warn,) = mojo_disk.check_disk(framework)
        assert warn.message == "12.2 GB free for the cache folder; large models need more."
        assert warn.fix_hint.startswith("Free disk space before converting models larger than a few GB. ")
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", fixed(int(40.4 * GIB)))
        (ok,) = mojo_disk.check_disk(framework)
        assert ok.message == "40 GB free for cache"

    def test_one_line_per_disk_and_an_unreadable_disk_is_left_out(self, tmp_path, monkeypatch):
        from orchestrator.Core.mojo.checks import disk as mojo_disk
        framework, cache, out = self.framework(tmp_path)
        out.mkdir()
        monkeypatch.setattr(mojo_disk.os, "stat", lambda p, *a, **k: SimpleNamespace(
            st_dev=1 if Path(p) == cache else 2))
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", fixed(50 * GIB))
        assert [f.code for f in mojo_disk.check_disk(framework)] == ["disk.cache", "disk.output"]

        def broken(path):
            raise OSError("unreadable")
        monkeypatch.setattr(mojo_disk.shutil, "disk_usage", broken)
        assert mojo_disk.check_disk(framework) == []


# --- the self-healing diagnosis and the AI's disk fact --------------------------------------------------------------

class TestFullDiskVerdicts:

    def test_self_healing_names_the_folders_below_the_reserve_by_label(self, tmp_path, monkeypatch):
        from orchestrator.Core import self_healing_manager
        from orchestrator.Core.self_healing_manager import SelfHealingManager
        cache, models = tmp_path / "cache", tmp_path / "models"
        cache.mkdir()
        values = {"cache_dir": str(cache), "models_dir": str(models)}
        healer = SelfHealingManager.__new__(SelfHealingManager)
        healer.framework = SimpleNamespace(get_component=lambda name: None)
        healer._config = lambda key, default=None: values.get(key, default)
        free = {str(cache): 20 * GB - 1, str(tmp_path): 20 * GB}   # the models folder is not made: its parent
        monkeypatch.setattr(self_healing_manager.shutil, "disk_usage", lambda p: usage(free[str(p)]))
        proposal = healer._disk_full_proposal()
        assert proposal.root_cause == ("A disk the build writes to is full. model cache: 0 MB (its disk has 20.0 GB "
                                       "free).")
        assert str(tmp_path) not in proposal.root_cause + proposal.fix_command

    def test_the_ai_disk_fact_counts_a_disk_below_the_reserve_as_full(self, tmp_path, monkeypatch):
        from orchestrator.tools import tools_status
        cache = tmp_path / "cache"
        cache.mkdir()
        values = {"cache_dir": str(cache), "output_dir": str(tmp_path / "out" / "deep")}
        framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: values.get(key, default)))
        monkeypatch.setattr(tools_status.shutil, "disk_usage", fixed(20 * GB))
        assert tools_status.disk_fact(framework) == ("contradicted", ["The cache and output disk has 20.0 GB free."],
                                                     ["check_build_request"])
        monkeypatch.setattr(tools_status.shutil, "disk_usage", fixed(20 * GB - 1))
        checked, facts, tries = tools_status.disk_fact(framework)
        assert (checked, tries) == ("confirmed", []) and facts == ["The cache and output disk has 20.0 GB free."]

    def test_the_ai_disk_fact_without_a_readable_disk_is_unchecked(self, tmp_path, monkeypatch):
        from orchestrator.tools import tools_status

        def broken(path):
            raise OSError("unreadable")
        values = {"cache_dir": str(tmp_path)}
        framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: values.get(key, default)))
        monkeypatch.setattr(tools_status.shutil, "disk_usage", broken)
        assert tools_status.disk_fact(framework) == ("unchecked", ["The framework could not read the free disk space."],
                                                     [])


# --- saving a finished model: rename on the same disk ---------------------------------------------------------------

def test_the_saved_model_is_renamed_on_its_disk_and_measured_on_the_nearest_existing_folder(tmp_path, monkeypatch):
    from orchestrator.Core import artifact_output
    (tmp_path / "a").mkdir()
    assert artifact_output._device(tmp_path / "a" / "not" / "yet") == artifact_output._device(tmp_path / "a")
    assert artifact_output._device(tmp_path / "a") == artifact_output._device(tmp_path)
    # a folder on another disk (its stat says so) is not the same disk
    other, real_stat = SimpleNamespace(st_dev=os.stat(tmp_path).st_dev + 1), os.stat
    monkeypatch.setattr(os, "stat",
                        lambda p, *a, **k: other if str(p) == str(tmp_path / "a") else real_stat(p, *a, **k))
    assert artifact_output._device(tmp_path / "a") != artifact_output._device(tmp_path)


# --- labels, never paths, toward an AI ------------------------------------------------------------------------------

@pytest.fixture
def hf_env(tmp_path, monkeypatch):
    """The download tool's world of tests/test_hf_tools.py (a stand-in Hub, a registry that allows the download)."""
    yield from hf_tools_env.__wrapped__(tmp_path, monkeypatch)


def test_the_ai_download_refusal_names_no_path(hf_env, monkeypatch, tmp_path):
    """download_hf_file's refusal before the question: sizes and the reserve, no folder of this computer."""
    from orchestrator.Core import hf_hub
    from orchestrator.tools.registry import ToolError
    monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: SimpleNamespace(total=10 ** 9, used=0, free=10))
    with pytest.raises(ToolError) as refused:
        hf_call(hf_env, "download_hf_file", {"model": HF_REPO, "filename": "tiny-q4_k_m.gguf"})
    assert refused.value.code == "insufficient_resources"
    assert str(tmp_path) not in str(refused.value) and "kept free as a reserve" in str(refused.value)


# --- the disk_usage test seams the suite relies on ------------------------------------------------------------------

def test_the_module_seams_are_the_ones_the_checks_read(tmp_path, monkeypatch):
    """Tests replace the free space through these seams; each check must keep reading its own."""
    from orchestrator.Core import artifact_output, build_service, knowledge_snapshots
    from orchestrator.runtime import runtime_mode
    for module in (artifact_output, knowledge_snapshots, runtime_mode):
        assert callable(module._disk_usage)
    assert callable(build_service.BuildService.disk_usage)
    monkeypatch.setattr(knowledge_snapshots, "_disk_usage", fixed(7 * GB, total=100 * GB))
    room = knowledge_snapshots.snapshot_room(tmp_path, 1 * GB)
    assert room.free == 7 * GB, "snapshot_room reads knowledge_snapshots._disk_usage"


def test_hf_hub_reservations_are_released_once_and_thread_safe():
    from orchestrator.Core import hf_hub
    held = [hf_hub.Reservation(f"t/{i}", 1) for i in range(20)]
    threads = [threading.Thread(target=r.release) for r in held]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not any(k.startswith("t/") for k in hf_hub._RESERVED)


# --- disk_plan.room: the one rule the checks above now share --------------------------------------------------------

class TestRoom:

    def test_the_reserve_the_margin_and_the_ledger(self, tmp_path):
        from orchestrator.Core import disk_plan
        check = disk_plan.room(tmp_path, 10 * GB, label="models", what="a download", disk_usage=fixed(50 * GB))
        assert (check.free, check.total, check.needed, check.reserve) == (50 * GB, 1000 * GB, 10 * GB, 20 * GB)
        assert check.labels == ["models"] and check.what == ["a download"] and check.verdict == "enough"
        standing = disk_plan.room(tmp_path, 10 * GB, standing=True, disk_usage=fixed(50 * GB))
        assert standing.reserve == 100 * GB and not standing.fits
        own = disk_plan.room(tmp_path, 10 * GB, margin=512 * MIB, disk_usage=fixed(50 * GB))
        assert own.reserve == 512 * MIB
        ledger = disk_plan.ReservationLedger(tmp_path / "state")
        rid, _ = ledger.reserve([disk_plan.DiskNeed("cache", tmp_path, 25 * GB, "a build")], "gui", "a build",
                                disk_usage=fixed(500 * GB))
        try:
            counted = disk_plan.room(tmp_path, 10 * GB, ledger=ledger, disk_usage=fixed(50 * GB))
            assert counted.reserved_by_others == 25 * GB and not counted.fits
            assert disk_plan.room(tmp_path, 10 * GB, ledger=ledger, exclude=rid,
                                  disk_usage=fixed(50 * GB)).reserved_by_others == 0
        finally:
            ledger.release(rid)

    def test_an_unreadable_disk_raises_while_check_disk_counts_it_full(self, tmp_path):
        from orchestrator.Core import disk_plan

        def broken(path):
            raise OSError("unreadable")
        with pytest.raises(OSError):
            disk_plan.room(tmp_path, 1, disk_usage=broken)
        (check,) = disk_plan.check_disk([disk_plan.DiskNeed("cache", tmp_path, 1, "x")], disk_usage=broken)
        assert (check.free, check.fits) == (0, False)

    def test_check_disk_takes_a_margin_too(self, tmp_path):
        from orchestrator.Core import disk_plan
        (check,) = disk_plan.check_disk([disk_plan.DiskNeed("cache", tmp_path, 1, "x")], margin=7,
                                        disk_usage=fixed(8))
        assert check.reserve == 7 and check.fits

    def test_require_names_the_call(self, tmp_path):
        from orchestrator.Core import disk_plan
        checks = [disk_plan.room(tmp_path, 10 * GB, label="models", disk_usage=fixed(5 * GB))]
        with pytest.raises(disk_plan.InsufficientSpace) as e:
            disk_plan.require(checks, what="this download")
        assert str(e.value).startswith("Not enough disk space for this download: models disk: 10.0 GB needed")
        with pytest.raises(disk_plan.InsufficientSpace) as e:
            disk_plan.require(checks)
        assert str(e.value).startswith("Not enough disk space for this build: ")

    def test_the_downloads_of_the_runtime_and_the_zoo_run_on_the_disk_rule(self, tmp_path, monkeypatch):
        """PinnedDownloader asks its caller's rule (utils imports nothing above it); the local runtime and the
        community zoo give disk_plan's download_rule."""
        from orchestrator.Core import disk_plan
        from orchestrator.Core.community_manager import CommunityManager
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        zoo = CommunityManager(SimpleNamespace(config=None))
        asked = []
        real = disk_plan.room

        def spy(path, needed=0, **kw):
            asked.append((Path(path), needed, kw.get("margin")))
            return real(path, needed, **kw)
        monkeypatch.setattr(disk_plan, "room", spy)
        monkeypatch.setattr(shutil, "disk_usage", fixed(100 * GB))
        rt._ensure_space(tmp_path / "a", 5)
        zoo._downloads().ensure_space(tmp_path / "b", 6)
        assert asked == [(tmp_path / "a", 5, disk_plan.DOWNLOAD_MARGIN), (tmp_path / "b", 6, disk_plan.DOWNLOAD_MARGIN)]
