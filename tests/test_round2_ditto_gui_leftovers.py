"""Leftovers of the second MCP tool round that are not about Ditto's remote mode itself.

- A damaged authors.json (the author keys pinned on first use) is kept as authors.json.bad and never overwritten by
  the next pin.
- ArtifactStore (delete_artifact) uses only public BuildService methods.
- A deploy's job writes its own end entry to the audit log, linked to the call's `started` entry.
- Board and local runtime tool errors name the framework's folders by label and other paths by file name only: no
  host path, no "~/...".
- The GUI's workers reach the GUI thread through the one WorkerRelay; the Stop texts say a page still arriving is
  abandoned. (Moved here from the removed remote-mode tests.)
"""

import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import REPO_ROOT
from orchestrator.tools.registry import ToolContext

from test_zoo import World  # noqa: F401


# --- authors.json: never lose the trust-on-first-use pins ------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class TestDamagedAuthorKeys:

    def install_signed(self, world, tmp_path, name="a.zip"):
        alice = Ed25519PrivateKey.generate()
        (tmp_path / name).write_bytes(world.target_pack(key=alice))
        prepared = world.cm.prepare_install_file(tmp_path / name)
        return prepared

    def test_a_damaged_store_is_kept_and_the_next_pin_does_not_overwrite_it(self, world, tmp_path):
        authors = world.cm.cache_dir / "authors.json"
        authors.parent.mkdir(parents=True, exist_ok=True)
        damaged = '{"bob": "Ym9i", "carol": "Y2Fyb2w=",'  # cut off mid-write: every earlier pin is in there
        authors.write_text(damaged, encoding="utf-8")
        prepared = self.install_signed(world, tmp_path)
        assert any("store of pinned author keys is damaged" in n for n in prepared.notes), prepared.notes
        world.cm.finish_install(prepared)
        assert authors.read_text(encoding="utf-8") == damaged, "the pins of bob and carol are not overwritten"
        assert (world.cm.cache_dir / "authors.json.bad").read_text(encoding="utf-8") == damaged

    def test_a_store_that_is_not_a_map_counts_as_damaged(self, world, tmp_path):
        authors = world.cm.cache_dir / "authors.json"
        authors.parent.mkdir(parents=True, exist_ok=True)
        authors.write_text(json.dumps(["bob", "Ym9i"]), encoding="utf-8")
        world.cm.finish_install(self.install_signed(world, tmp_path))
        assert json.loads(authors.read_text(encoding="utf-8")) == ["bob", "Ym9i"]
        assert (world.cm.cache_dir / "authors.json.bad").exists()

    def test_an_older_different_copy_is_kept_too(self, world, tmp_path):
        authors = world.cm.cache_dir / "authors.json"
        authors.parent.mkdir(parents=True, exist_ok=True)
        (world.cm.cache_dir / "authors.json.bad").write_text("older damage", encoding="utf-8")
        authors.write_text("new damage", encoding="utf-8")
        world.cm.finish_install(self.install_signed(world, tmp_path))
        copies = sorted(p.name for p in world.cm.cache_dir.glob("authors.json*.bad"))
        assert len(copies) == 2 and (world.cm.cache_dir / "authors.json.bad").read_text() == "older damage"

    @staticmethod
    def clock(monkeypatch, step):
        """community_manager's clock: each call `step` seconds after the previous one (0: the same second)."""
        from datetime import datetime, timedelta, timezone

        from orchestrator.Core import community_manager
        start = datetime(2026, 10, 17, 1, 2, 3, tzinfo=timezone.utc)
        calls = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                calls.append(tz)
                return start + timedelta(seconds=step * (len(calls) - 1))
        monkeypatch.setattr(community_manager, "datetime", Clock)
        return calls

    def test_the_same_damage_is_kept_once_when_the_second_read_falls_into_the_next_second(self, world, tmp_path,
                                                                                            monkeypatch):
        """Root cause of the flaky test above: an install reads the damaged store twice (its summary, then the pin);
        the copy was named by the time alone, so a second read in the next second kept the same damage again."""
        calls = self.clock(monkeypatch, step=1)
        authors = world.cm.cache_dir / "authors.json"
        authors.parent.mkdir(parents=True, exist_ok=True)
        (world.cm.cache_dir / "authors.json.bad").write_text("older damage", encoding="utf-8")
        authors.write_text("new damage", encoding="utf-8")
        world.cm.finish_install(self.install_signed(world, tmp_path))
        copies = {p.name: p.read_text(encoding="utf-8") for p in world.cm.cache_dir.glob("authors.json*.bad")}
        assert sorted(copies.values()) == ["new damage", "older damage"], copies
        assert copies["authors.json.20261017T010203Z.bad"] == "new damage" and len(calls) == 1
        assert authors.read_text(encoding="utf-8") == "new damage", "the damaged store itself is never replaced"

    def test_a_different_damage_in_the_same_second_does_not_overwrite_a_kept_copy(self, world, monkeypatch):
        self.clock(monkeypatch, step=0)
        authors = world.cm.cache_dir / "authors.json"
        authors.parent.mkdir(parents=True, exist_ok=True)
        for damage in ("first", "second", "third", "second"):
            authors.write_text(damage, encoding="utf-8")
            world.cm._keep_damaged(authors)
        copies = {p.name: p.read_text(encoding="utf-8") for p in world.cm.cache_dir.glob("authors.json*.bad")}
        assert copies == {"authors.json.bad": "first", "authors.json.20261017T010203Z.bad": "second",
                          "authors.json.20261017T010203Z-2.bad": "third"}

    def test_a_missing_store_still_pins_on_first_use(self, world, tmp_path):
        world.cm.finish_install(self.install_signed(world, tmp_path))
        assert set(json.loads((world.cm.cache_dir / "authors.json").read_text(encoding="utf-8"))) == {"alice"}
        assert not (world.cm.cache_dir / "authors.json.bad").exists()


# --- ArtifactStore on public BuildService methods ------------------------------------------------------------------

def test_artifact_store_uses_only_public_build_service_methods():
    for rel in ("orchestrator/Core/artifact_store.py", "orchestrator/tools/tools_optional.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        private = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                          and isinstance(n.value, ast.Name) and n.value.id == "service" and n.attr.startswith("_")})
        assert private == [], f"{rel} reaches into BuildService: {private}"


def test_artifact_store_works_with_the_public_methods_alone(tmp_path):
    from orchestrator.Core.artifact_store import ArtifactStore
    out = tmp_path / "output"
    folder = out / "Rockchip" / "m"
    folder.mkdir(parents=True)
    (folder / "model.gguf").write_bytes(b"GGUF")
    entries = [("a_0123456789abcdef", "output:Rockchip/m", {"path": str(folder), "primary": "model.gguf"})]
    service = SimpleNamespace(output_folder=lambda: out, artifact_entries=lambda: entries,
                              busy_output_folders=lambda: [])  # no private attribute exists here
    store = ArtifactStore.for_service(service)
    target = store.resolve("a_0123456789abcdef", expect_sha256=__import__("hashlib").sha256(b"GGUF").hexdigest())
    assert target.label == "output:Rockchip/m"


# --- the deploy's end in the audit log ------------------------------------------------------------------------------

paramiko = pytest.importorskip("paramiko")
from test_deploy_tools import (asking, build, call, fake_deploy, fw, online, package, roomy_disk,  # noqa: E402,F401
                               trust, wait_job)


class TestDeployEndAudit:

    def ready(self, fw, online):
        device, key = trust(fw)
        online["key"] = key
        return device, package(fw, build(fw))

    def entries(self, fw, tool="deploy_to_device"):
        return [e for e in fw.tool_registry.audit.entries(500) if e.get("tool") == tool]

    def wait_end(self, fw):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ends = [e for e in self.entries(fw) if str(e.get("detail") or "").startswith("job end:")]
            if ends:
                return ends
            time.sleep(0.05)
        return []

    def test_a_finished_deploy_writes_its_end_linked_to_the_start(self, fw, online, fake_deploy):
        device, pkg = self.ready(fw, online)
        started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                       confirm=asking(True))
        assert wait_job(fw, started.job_id).state == "done"
        (end,) = self.wait_end(fw)
        (start,) = [e for e in self.entries(fw) if e["outcome"] == "started"]
        assert end["outcome"] == "ok" and end["links"] == [start["id"]]
        assert started.job_id in end["detail"] and "Living room board" in end["detail"] and "deployed" in end["detail"]

    def test_a_failed_deploy_ends_as_error(self, fw, online, fake_deploy):
        fake_deploy.success = False
        device, pkg = self.ready(fw, online)
        started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                       confirm=asking(True))
        wait_job(fw, started.job_id)
        (end,) = self.wait_end(fw)
        assert end["outcome"] == "error" and "not deployed" in end["detail"] and end["reason_code"] == "failed"

    def test_a_cancelled_deploy_ends_as_cancelled(self, fw, online, fake_deploy):
        fake_deploy.block = True
        device, pkg = self.ready(fw, online)
        started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                       confirm=asking(True))
        for _ in range(100):
            if fake_deploy.client is not None:
                break
            time.sleep(0.02)
        call(fw, "cancel_job", {"job_id": started.job_id})
        (end,) = self.wait_end(fw)
        (start,) = [e for e in self.entries(fw) if e["outcome"] == "started"]
        assert end["outcome"] == "cancelled" and end["links"] == [start["id"]] and "unknown" in end["detail"]

    def test_the_audit_chain_stays_intact(self, fw, online, fake_deploy):
        device, pkg = self.ready(fw, online)
        started = call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                       confirm=asking(True))
        wait_job(fw, started.job_id)
        assert self.wait_end(fw)
        assert fw.tool_registry.audit.check().intact


# --- board and runtime tool errors without host paths ---------------------------------------------------------------

class TestNoPathsInRuntimeErrors:

    def test_label_paths(self):
        from orchestrator.tools.tools_runtime import label_paths
        roots = {"/home/u/.llm-framework": "state", "/srv/out": "output"}
        assert label_paths("Cannot read /home/u/.llm-framework/devices/ab/profile.json.", roots) == \
            "Cannot read state:devices/ab/profile.json."
        assert label_paths("No model at ~/models/x.gguf on the device", roots) == "No model at x.gguf on the device"
        assert label_paths("See /home/u/private/notes.txt (or not)", roots) == "See notes.txt (or not)"
        assert label_paths(r"Bad file C:\Users\bob\x.bin", roots) == "Bad file x.bin"
        assert label_paths("ok (/srv/out/Rk/m.gguf)", roots) == "ok (output:Rk/m.gguf)"
        kept = "https://github.com/ggml-org/llama.cpp and 127.0.0.1:8080/health, os/arch, and/or"
        assert label_paths(kept, roots) == kept, "addresses and words are not paths"

    def test_a_path_after_a_colon_or_in_a_file_url_is_no_host_path_either(self, monkeypatch):
        """Review: 'file:/home/u/...' and 'file:///home/u/...' kept the whole path (the colon guard meant for URLs),
        and the home-folder shortening then made it '~/...'."""
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", "/home/u")
        roots = {"/home/u/.llm-framework": "state"}
        assert label_paths("cannot open file:/home/u/private/a.bin", roots) == "cannot open file:a.bin"
        assert label_paths("bad uri file:///home/u/private/a.bin", roots) == "bad uri file://a.bin"
        assert label_paths("scp to dietpi@board:/home/u/x.tar failed", roots) == "scp to dietpi@board:x.tar failed"
        kept = "https://example.org/a/b and http://127.0.0.1:8080/health"
        assert label_paths(kept, roots) == kept

    def test_a_board_tool_error_names_no_home_path(self, make_framework, monkeypatch):
        import test_runtime_tools as rtt
        from orchestrator.runtime.device import service
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        fm = make_framework()
        state = Path(fm.config.user_state_dir)
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
        device = TrustedDevices(state).record("192.168.1.50", 22, "dietpi", key, via="cli", paired=True,
                                              label="Board")
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        home = str(Path.home())

        def failing(*a, **k):
            raise service.DeviceRuntimeError(f"The probe of Board could not be stored in {state}/devices/x/probe.txt "
                                             f"nor in {home}/.cache/llm/probe.txt nor ~/probe.txt.")
        monkeypatch.setattr(service, "probe", failing)
        rtt.allow(fm, "probe_remote_device")
        started = rtt.call(fm, "probe_remote_device", {"device_id": device.device_id}, confirm=rtt.asking(True))
        job = rtt.wait_job(fm, started.job_id)
        message = job.error["message"]
        assert job.state == "failed" and "Board" in message
        assert "~/" not in message and home not in message and str(state) not in message
        assert "state:devices/x/probe.txt" in message and "probe.txt" in message
        status = fm.tool_registry.call("get_job_status", {"job_id": started.job_id}, ToolContext("ditto", session="s"))
        assert "~/" not in json.dumps(status.model_dump(mode="json"))


# --- one relay for the GUI's workers ---------------------------------------------------------------------------------

def test_no_gui_worker_emits_on_a_qobject_of_its_own():
    """The module wizard's AI import used a WizardSignals object it created and a plain thread emitted on (a closed
    wizard could take it with it); the AI settings kept a relay of their own. Both use the one WorkerRelay now."""
    root = REPO_ROOT / "orchestrator" / "gui"
    wizards = (root / "wizards.py").read_text(encoding="utf-8")
    dialogs = (root / "dialogs.py").read_text(encoding="utf-8")
    assert "WizardSignals" not in wizards and "worker_relay()" in wizards
    assert "_ModelListRelay" not in dialogs and "worker_relay()" in dialogs
    tree = ast.parse(dialogs)
    signals = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Signal"]
    assert signals == [], "dialogs.py defines no signal of its own for workers"


def test_the_ai_import_answer_reaches_the_gui_thread_through_the_relay(qapp, tmp_path, monkeypatch):
    import threading
    import time
    from orchestrator.gui import wizards
    arrived = []
    monkeypatch.setattr(wizards.ModuleCreationWizard, "on_ai_finished",
                        lambda self, config: arrived.append((threading.current_thread() is threading.main_thread(),
                                                             config)))
    monkeypatch.setattr(wizards.ModuleCreationWizard, "on_ai_error", lambda self, err: arrived.append(("error", err)))
    wizard = wizards.ModuleCreationWizard(tmp_path)
    other = wizards.ModuleCreationWizard(tmp_path)

    class Ditto:
        def generate_module(self, path):
            return SimpleNamespace(model_dump=lambda: {"module_name": "X"})
    token = wizard._ai_request = object()
    from orchestrator.gui.worker_relay import worker_relay
    worker = threading.Thread(target=wizards.ModuleCreationWizard._ai_worker,
                              args=(Ditto(), tmp_path / "p.txt", token, worker_relay()))
    worker.start()
    worker.join(5)
    deadline = time.monotonic() + 5
    while len(arrived) < 1 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert arrived == [(True, {"module_name": "X"})], "only the wizard that asked, in the GUI thread"
    wizard.deleteLater()
    other.deleteLater()


# --- Stop texts ----------------------------------------------------------------------------------------------------

def test_stop_texts_say_a_page_still_arriving_is_abandoned():
    from orchestrator.utils.localization import ui_text
    assert "abandoned" in ui_text("crawl.stopping", "en") and "after the current page" not in ui_text(
        "crawl.stopping", "en")
    assert "verworfen" in ui_text("crawl.stopping", "de") and "nach der aktuellen Seite" not in ui_text(
        "crawl.stopping", "de")
    wizards = (REPO_ROOT / "orchestrator" / "gui" / "wizards.py").read_text(encoding="utf-8")
    assert "after the current page" not in wizards
