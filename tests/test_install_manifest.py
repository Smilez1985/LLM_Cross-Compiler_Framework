"""The install manifest and what the uninstaller does with it (orchestrator/utils/install_manifest.py,
orchestrator/Core/uninstall_components.py; the maintainer's rule of 2026-11-18): only what the installer installed itself is
written down, only that is ever removed, a system program is asked about on its own with No as the answer, and what was there
before is never touched. The system (winget, the container engine) is played by fakes."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import uninstall_components as un
from orchestrator.utils import install_manifest as im
from orchestrator.utils.install_manifest import Entry

PYTHON = Entry("python", "shared", "winget", "Python.Python.3.13", "Python 3.13")
SEVENZIP = Entry("7zip", "shared", "winget", "7zip.7zip", "7-Zip")
SEARXNG = Entry("searxng", "own", "searxng", "x", "SearXNG, die Suchmaschine")


class TestTheFile:

    def test_it_is_ragfours_shape_a_map_from_a_component_to_its_details(self, tmp_path):
        path = im.person_path(tmp_path)
        assert path.name == "install-manifest.json" and im.machine_path(tmp_path).name == "install-manifest.json"
        assert im.record(path, SEARXNG)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"searxng"} and data["searxng"]["scope"] == "own" and "registered_at" in data["searxng"]
        assert im.read(path) == [SEARXNG]

    def test_recording_again_replaces_the_component_and_keeps_every_other_one(self, tmp_path):
        path = im.machine_path(tmp_path)
        im.record(path, PYTHON)
        im.record(path, SEVENZIP)
        im.record(path, Entry("python", "shared", "winget", "Python.Python.3.13", "Python 3.13 (neu)"))
        assert {e.id: e.title for e in im.read(path)} == {"python": "Python 3.13 (neu)", "7zip": "7-Zip"}

    def test_a_component_another_program_wrote_into_the_file_survives(self, tmp_path):
        path = im.person_path(tmp_path)
        path.write_text(json.dumps({"rag-host": {"install_dir": "/opt/rag", "services": ["rag-api"]}}), encoding="utf-8")
        im.record(path, SEARXNG)
        im.forget(path, "searxng")
        assert json.loads(path.read_text(encoding="utf-8")) == {"rag-host": {"install_dir": "/opt/rag",
                                                                            "services": ["rag-api"]}}

    def test_the_last_component_removes_the_file(self, tmp_path):
        path = im.person_path(tmp_path)
        im.record(path, SEARXNG)
        assert im.forget(path, "searxng") and not path.exists() and not im.forget(path, "searxng")

    def test_a_file_powershell_wrote_with_a_bom_is_read(self, tmp_path):
        path = im.machine_path(tmp_path)
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"7zip": {"scope": "shared", "kind": "winget", "ref": "7zip.7zip",
                                                               "title": "7-Zip"}}).encode())
        assert im.read(path) == [SEVENZIP]

    @pytest.mark.parametrize("text", ["", "not json", "[]", '{"a": 1}', '{"x y": {"scope": "own"}}',
                                      '{"ok": {"scope": "world", "kind": "winget", "ref": "r", "title": "t"}}'])
    def test_damage_or_another_shape_lists_nothing_and_raises_nothing(self, tmp_path, text):
        path = im.person_path(tmp_path)
        path.write_text(text, encoding="utf-8")
        assert im.read(path) == [] and im.read(tmp_path / "missing.json") == []

    @pytest.mark.parametrize("entry", [Entry("Bad Id", "own", "searxng", "r", "t"), Entry("a", "world", "searxng", "r", "t"),
                                       Entry("a", "own", "rm", "r", "t"), Entry("a", "own", "searxng", "", "t")])
    def test_an_entry_of_the_wrong_shape_is_refused(self, tmp_path, entry):
        with pytest.raises(ValueError):
            im.record(im.person_path(tmp_path), entry)


class FakeRun:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        return SimpleNamespace(returncode=self.code, stdout="", stderr="")


def answers(*given):
    queue = list(given)
    asked = []

    def ask(question):
        asked.append(question)
        return queue.pop(0) if queue else ""
    ask.asked = asked
    return ask


WINGET = lambda name: "winget.exe" if name == "winget" else None  # noqa: E731


class TestTheUninstaller:

    def test_with_nothing_written_down_nothing_is_removed_and_nothing_is_asked(self, tmp_path):
        said, run = [], FakeRun()
        ask = answers()
        assert un.uninstall(im.machine_path(tmp_path), im.person_path(tmp_path), ask, said.append, run, WINGET) == 0
        assert ask.asked == [] and run.calls == [] and "nichts aufgeschrieben" in said[0]

    def test_python_that_was_there_before_is_never_removed_because_it_was_never_written_down(self, tmp_path):
        machine = im.machine_path(tmp_path)
        im.record(machine, SEVENZIP)                      # the installer installed 7-Zip; Python was there: no entry
        said, run = [], FakeRun()
        un.uninstall(machine, None, answers("j", "j"), said.append, run, WINGET)
        assert run.calls == [["winget.exe", "uninstall", "-e", "--id", "7zip.7zip"]]
        assert not any("Python" in line for line in said)

    def test_a_system_program_is_asked_on_its_own_with_a_warning_and_no_is_the_answer(self, tmp_path):
        machine = im.machine_path(tmp_path)
        im.record(machine, PYTHON)
        im.record(machine, SEVENZIP)
        said, run, ask = [], FakeRun(), answers("", "")             # Enter twice: No, No
        assert un.uninstall(machine, None, ask, said.append, run, WINGET) == 0
        assert run.calls == [] and len(ask.asked) == 2
        assert any("laufen sie vielleicht nicht mehr" in line for line in said)
        assert {e.id for e in im.read(machine)} == {"python", "7zip"}          # still recorded: still there

    def test_a_yes_for_one_program_removes_only_that_one(self, tmp_path):
        machine = im.machine_path(tmp_path)
        im.record(machine, PYTHON)
        im.record(machine, SEVENZIP)
        run = FakeRun()
        un.uninstall(machine, None, answers("n", "j"), lambda line: None, run, WINGET)     # Python no, 7-Zip yes
        assert run.calls == [["winget.exe", "uninstall", "-e", "--id", "7zip.7zip"]]
        assert [e.id for e in im.read(machine)] == ["python"]

    def test_what_only_the_framework_uses_is_asked_once_for_all(self, tmp_path, monkeypatch):
        person = im.person_path(tmp_path)
        im.record(person, SEARXNG)
        from orchestrator.Core import searxng_engine
        removed = []
        monkeypatch.setattr(searxng_engine, "remove_files", lambda state_dir, **k: removed.append(state_dir) or ["entfernt"])
        ask = answers("j")
        assert un.uninstall(None, person, ask, lambda line: None, FakeRun(), WINGET) == 1
        assert len(ask.asked) == 1 and removed == [tmp_path] and not person.exists()

    def test_dry_run_only_shows_and_removes_nothing(self, tmp_path):
        machine = im.machine_path(tmp_path)
        im.record(machine, SEVENZIP)
        said, run, ask = [], FakeRun(), answers("j")
        assert un.uninstall(machine, None, ask, said.append, run, WINGET, dry_run=True) == 0
        assert run.calls == [] and ask.asked == [] and any("Nur gezeigt" in line for line in said)

    def test_an_end_of_input_is_a_no(self, tmp_path):
        machine = im.machine_path(tmp_path)
        im.record(machine, SEVENZIP)

        def eof(question):
            raise EOFError
        assert un.uninstall(machine, None, eof, lambda line: None, FakeRun(), WINGET) == 0
        assert [e.id for e in im.read(machine)] == ["7zip"]

    def test_winget_missing_or_failing_is_told_with_the_command_and_nothing_is_forgotten_wrongly(self, tmp_path):
        said = []
        assert "winget fehlt" in un.remove_entry(SEVENZIP, im.machine_path(tmp_path), FakeRun(), lambda name: None)
        out = un.remove_entry(SEVENZIP, im.machine_path(tmp_path), FakeRun(code=1), WINGET)
        assert "nicht entfernt" in out and "winget uninstall -e --id 7zip.7zip" in out and said == []

    def test_a_name_that_could_be_a_command_is_never_run(self, tmp_path):
        evil = Entry("x", "shared", "winget", "7zip.7zip & calc", "x")
        run = FakeRun()
        assert "ungueltig" in un.remove_entry(evil, im.machine_path(tmp_path), run, WINGET) and run.calls == []

    def test_a_folder_entry_never_leaves_the_folder_of_its_manifest(self, tmp_path):
        state, outside = tmp_path / "state", tmp_path / "outside"
        (state / "keep").mkdir(parents=True)
        outside.mkdir()
        (outside / "precious.txt").write_text("data")
        manifest = im.person_path(state)
        for ref in (str(outside), str(state), "relative/dir"):
            out = un.remove_entry(Entry("f", "own", "folder", ref, "f"), manifest, FakeRun(), WINGET)
            assert "uebersprungen" in out
        assert (outside / "precious.txt").exists() and (state / "keep").exists()
        inside = state / "gone"
        inside.mkdir()
        assert un.remove_entry(Entry("f", "own", "folder", str(inside), "f"), manifest, FakeRun(), WINGET) == "entfernt."
        assert not inside.exists()

    def test_the_command_line_lists_and_runs_on_a_dry_run(self, tmp_path, capsys):
        machine = im.machine_path(tmp_path)
        im.record(machine, SEVENZIP)
        assert un.main(["list", "--machine", str(machine)]) == 0
        assert "7zip\tshared\twinget\t7zip.7zip\t7-Zip" in capsys.readouterr().out
        assert un.main(["uninstall", "--dry-run", "--machine", str(machine)]) == 0
        assert "Nur gezeigt" in capsys.readouterr().out and [e.id for e in im.read(machine)] == ["7zip"]


class TestTheSearchEngineWritesItselfDown:

    def test_setup_records_and_remove_forgets_the_search_engine(self, tmp_path):
        from orchestrator.Core import searxng_engine as eng

        class Config(dict):
            def set(self, key, value):
                self[key] = value

            def save_user_config(self):
                return True

        def installed(folder, progress=None):
            Path(folder).mkdir(parents=True, exist_ok=True)

        class Running:
            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        config = Config()
        eng.setup("own", tmp_path, config, install_fn=installed, running_cls=Running)
        entries = im.read(im.person_path(tmp_path))
        assert [(e.id, e.scope, e.kind) for e in entries] == [("searxng", "own", "searxng")]
        eng.remove(tmp_path, config, client_factory=lambda: (_ for _ in ()).throw(RuntimeError("no engine")))
        assert im.read(im.person_path(tmp_path)) == []

    def test_a_failed_setup_records_nothing(self, tmp_path):
        from orchestrator.Core import searxng_engine as eng
        from orchestrator.Core.searxng_native import NativeError

        def failing(folder, progress=None):
            raise NativeError("no")
        with pytest.raises(NativeError):
            eng.setup("own", tmp_path, {}, install_fn=failing)
        assert im.read(im.person_path(tmp_path)) == []
