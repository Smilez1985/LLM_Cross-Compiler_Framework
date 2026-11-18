"""The Linux half of the install manifest (utils/install_manifest.py, Core/uninstall_components.py; the maintainer's rule of
2026-11-18, now for the packages install.sh installs): one kind per package manager, package names that are checked, a record
that merges, and the removal of a package only with the tool that REFUSES when another package needs it, never with the
tool that removes the dependents too (something that was there before must never go with it). The system (the package
tools, the user id) is played by fakes."""

import json
from types import SimpleNamespace

import pytest

from orchestrator.Core import uninstall_components as uc
from orchestrator.utils import install_manifest as im
from orchestrator.utils.install_manifest import Entry


def entry(kind="apt", ref="python3 python3-venv", id="python", title="Python", scope="shared"):
    return Entry(id, scope, kind, ref, title)


class Fakes:
    """`run` and `which` for the package tools: records every command; a tool is there unless it is in `missing`."""

    def __init__(self, returncode=0, stderr="", stdout="", missing=(), by_command=None):
        self.calls, self.returncode, self.stderr, self.stdout = [], returncode, stderr, stdout
        self.missing, self.by_command = set(missing), by_command or {}

    def which(self, name):
        return None if name in self.missing else f"/usr/bin/{name}"

    def run(self, command, capture_output=True, text=True):
        self.calls.append(list(command))
        for word, answer in self.by_command.items():
            if word in command:
                return SimpleNamespace(**answer)
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


class TestTheModel:

    @pytest.mark.parametrize("kind", ["apt", "dnf", "zypper", "pacman", "apk"])
    def test_every_package_manager_is_a_kind(self, kind):
        assert kind in im.KINDS and kind in im.PACKAGE_KINDS
        assert im.parse(json.dumps({"python": {"scope": "shared", "kind": kind, "ref": "python3", "title": "Python"}}))

    def test_a_ref_may_name_several_packages_and_every_name_is_checked(self):
        assert im.package_names(entry(ref="podman uidmap slirp4netns")) == ["podman", "uidmap", "slirp4netns"]
        assert im.package_names(entry(ref="libstdc++6 python3.12 docker-ce:amd64")) == ["libstdc++6", "python3.12",
                                                                                         "docker-ce:amd64"]
        for bad in ("python3; rm -rf /", "$(id)", "a b|c", "-rf", "", "pkg/../x"):
            assert im.package_names(entry(ref=bad)) == [], bad

    def test_an_entry_with_a_name_that_is_no_package_name_is_skipped_on_reading(self):
        text = json.dumps({"good": {"scope": "shared", "kind": "apt", "ref": "rsync", "title": "rsync"},
                           "evil": {"scope": "shared", "kind": "apt", "ref": "rsync; reboot", "title": "x"}})
        assert [e.id for e in im.parse(text)] == ["good"]

    def test_only_a_package_kind_has_package_names(self):
        assert im.package_names(entry(kind="winget", ref="Python.Python.3.13")) == []

    def test_describe_says_package_or_packages_and_the_manager(self):
        assert im.describe(entry(ref="rsync", title="rsync")) == "rsync: ein Paket (apt: rsync)"
        assert im.describe(entry(kind="dnf", ref="podman uidmap", title="Podman")) == "Podman: Pakete (dnf: podman uidmap)"


class TestRecordMerge:

    def test_the_package_names_of_the_same_component_are_added_not_replaced(self, tmp_path):
        path = tmp_path / "install-manifest.json"
        assert im.record_merge(path, entry(ref="python3 python3-venv"))
        assert im.record_merge(path, entry(ref="python3-pip python3"))
        found = im.read(path)
        assert len(found) == 1 and found[0].ref == "python3 python3-venv python3-pip"

    def test_another_component_and_another_programs_entries_are_kept(self, tmp_path):
        path = tmp_path / "install-manifest.json"
        path.write_text(json.dumps({"mine": {"scope": "shared", "kind": "winget", "ref": "7zip.7zip", "title": "7-Zip"}}))
        im.record_merge(path, entry(ref="rsync", id="rsync", title="rsync"))
        assert sorted(e.id for e in im.read(path)) == ["mine", "rsync"]

    def test_a_component_of_another_kind_is_replaced_whole(self, tmp_path):
        path = tmp_path / "install-manifest.json"
        im.record_merge(path, entry(kind="dnf", ref="python3"))
        im.record_merge(path, entry(kind="apt", ref="python3-venv"))
        assert [(e.kind, e.ref) for e in im.read(path)] == [("apt", "python3-venv")]

    def test_a_wrong_entry_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            im.record_merge(tmp_path / "m.json", entry(ref="python3; reboot"))

    def test_the_command_install_sh_calls_writes_the_file(self, tmp_path):
        path = tmp_path / "install-manifest.json"
        code = im.main(["record", "--file", str(path), "--id", "podman", "--kind", "apt", "--ref", "podman uidmap",
                        "--title", "Podman"])
        assert code == 0 and im.read(path)[0].ref == "podman uidmap"
        assert im.read(path)[0].scope == "shared"

    def test_the_command_says_2_for_a_wrong_call_and_1_when_it_cannot_write(self, tmp_path):
        assert im.main(["record", "--file", str(tmp_path / "m.json"), "--id", "x", "--kind", "apt", "--ref", "a b;c",
                        "--title", "T"]) == 2
        blocker = tmp_path / "file"
        blocker.write_text("x")
        assert im.main(["record", "--file", str(blocker / "m.json"), "--id", "x", "--kind", "apt", "--ref", "rsync",
                        "--title", "T"]) == 1


class TestRemovingPackages:

    @pytest.mark.parametrize("kind, command", [("apt", ["/usr/bin/dpkg", "-r"]), ("dnf", ["/usr/bin/rpm", "-e"]),
                                               ("zypper", ["/usr/bin/rpm", "-e"]),
                                               ("pacman", ["/usr/bin/pacman", "-R", "--noconfirm"])])
    def test_it_uses_the_tool_that_refuses_when_another_package_needs_the_package(self, kind, command):
        fakes = Fakes()
        said = uc.remove_packages(entry(kind=kind, ref="rsync 7zip"), fakes.run, fakes.which, is_root=lambda: True)
        assert said == "entfernt." and fakes.calls == [[*command, "rsync", "7zip"]]

    def test_it_never_uses_the_commands_that_remove_the_dependents_as_well(self):
        for kind in ("apt", "dnf", "zypper", "pacman", "apk"):
            fakes = Fakes()
            uc.remove_packages(entry(kind=kind, ref="python3"), fakes.run, fakes.which, is_root=lambda: True)
            flat = [" ".join(c) for c in fakes.calls]
            assert not any(w in text for text in flat for w in ("apt-get", "autoremove", "dnf remove", "zypper", "--cascade", "-Rs", "-Rc")), flat

    @pytest.mark.parametrize("kind", ["apt", "dnf", "pacman"])
    def test_a_package_another_one_needs_stays_and_the_reason_and_the_way_by_hand_are_said(self, kind):
        fakes = Fakes(returncode=1, stderr="dpkg: dependency problems prevent removal of python3:\n python3-foo depends on python3.\n")
        said = uc.remove_packages(entry(kind=kind, ref="python3"), fakes.run, fakes.which, is_root=lambda: True)
        assert said.startswith("nicht entfernt: ") and "python3-foo depends on python3." in said
        assert "entfernt nie Pakete, die er nicht aufgeschrieben hat" in said and "sudo " in said and "python3" in said

    def test_apk_asks_for_the_dependents_first_and_refuses_when_there_are_some(self):
        fakes = Fakes(by_command={"info": {"returncode": 0, "stdout": "python3-3.12.8-r0 is required by:\nsome-tool-1.0\nother-tool-2.0\n",
                                           "stderr": ""}})
        said = uc.remove_packages(entry(kind="apk", ref="python3"), fakes.run, fakes.which, is_root=lambda: True)
        assert said.startswith("nicht entfernt: andere Pakete brauchen es (some-tool-1.0, other-tool-2.0)")
        assert not any("del" in call for call in fakes.calls)                     # apk del never ran

    def test_apk_removes_a_package_nothing_needs(self):
        fakes = Fakes(by_command={"info": {"returncode": 0, "stdout": "rsync-3.3.0-r0 is required by:\n", "stderr": ""}})
        said = uc.remove_packages(entry(kind="apk", ref="rsync"), fakes.run, fakes.which, is_root=lambda: True)
        assert said == "entfernt." and fakes.calls[-1] == ["/usr/bin/apk", "del", "rsync"]

    def test_the_packages_of_the_entry_itself_are_no_dependents(self):
        fakes = Fakes(by_command={"info": {"returncode": 0, "stdout": "podman-5.0 is required by:\nuidmap-4.1\n", "stderr": ""}})
        said = uc.remove_packages(entry(kind="apk", ref="podman uidmap"), fakes.run, fakes.which, is_root=lambda: True)
        assert said == "entfernt."

    def test_without_root_rights_nothing_is_run(self):
        fakes = Fakes()
        said = uc.remove_packages(entry(), fakes.run, fakes.which, is_root=lambda: False)
        assert "Root-Rechte" in said and "sudo dpkg -r python3 python3-venv" in said and fakes.calls == []

    def test_a_missing_tool_is_said_with_the_way_by_hand(self):
        fakes = Fakes(missing=["dpkg"])
        said = uc.remove_packages(entry(), fakes.run, fakes.which, is_root=lambda: True)
        assert said.startswith("nicht entfernt: dpkg fehlt.") and fakes.calls == []

    def test_a_name_that_is_no_package_name_is_never_passed_to_a_tool(self):
        fakes = Fakes()
        bad = Entry("x", "shared", "apt", "python3; reboot", "T")
        assert "kein Paketname" in uc.remove_packages(bad, fakes.run, fakes.which, is_root=lambda: True)
        assert fakes.calls == []

    def test_remove_entry_sends_a_package_to_the_package_tool(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(uc, "remove_packages", lambda e, run, which: seen.append(e.ref) or "entfernt.")
        assert uc.remove_entry(entry(ref="rsync"), tmp_path / "m.json") == "entfernt." and seen == ["rsync"]


class TestTheQuestions:

    def manifest(self, tmp_path):
        path = tmp_path / "install-manifest.json"
        im.record_merge(path, entry(ref="python3", id="python", title="Python"))
        im.record_merge(path, entry(ref="rsync", id="rsync", title="rsync"))
        return path

    def test_every_package_is_asked_on_its_own_and_no_is_the_default(self, tmp_path):
        path = self.manifest(tmp_path)
        fakes, said, asked = Fakes(), [], []
        removed = uc.uninstall(path, None, ask=lambda q: asked.append(q) or "", say=said.append, run=fakes.run,
                               which=fakes.which)
        assert removed == 0 and fakes.calls == [] and len(asked) == 2
        assert {e.id for e in im.read(path)} == {"python", "rsync"}                   # nothing is forgotten
        assert sum(uc.SHARED_WARNING in line for line in said) == 2 and any("bleibt." in line for line in said)

    def test_a_yes_removes_that_package_only_and_forgets_it(self, tmp_path, monkeypatch):
        path = self.manifest(tmp_path)
        fakes = Fakes()
        monkeypatch.setattr(uc, "remove_packages", lambda e, run, which, is_root=None: fakes.run(["rm", e.ref]) and "entfernt.")
        answers = iter(["n", "j"])                                           # python: no, rsync: yes
        removed = uc.uninstall(path, None, ask=lambda q: next(answers), say=lambda t: None, run=fakes.run, which=fakes.which)
        assert removed == 1 and fakes.calls == [["rm", "rsync"]]
        assert [e.id for e in im.read(path)] == ["python"]

    def test_the_dry_run_shows_the_packages_and_removes_nothing(self, tmp_path):
        path = self.manifest(tmp_path)
        said = []
        assert uc.uninstall(path, None, ask=lambda q: pytest.fail("no question"), say=said.append, dry_run=True) == 0
        text = "\n".join(said)
        assert "Python: ein Paket (apt: python3)" in text and "rsync: ein Paket (apt: rsync)" in text
        assert "wird vielleicht von anderen Programmen benutzt" in text and {e.id for e in im.read(path)} == {"python", "rsync"}
