"""Every pinned version of the repository in one list, offline (orchestrator/runtime/build/versions.py,
scripts/versions_report.py; ROADMAP "Step 7"), and Mojo Oracle's information about a base image of the person's own
targets that the catalogue knows a newer release of (orchestrator/Core/mojo/checks/versions.py). Nothing is fetched."""

import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.mojo.checks import check_versions
from orchestrator.Core.mojo.findings import Severity
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
from orchestrator.runtime.build import versions
from orchestrator.runtime.catalog import load_catalog

CATALOG = load_catalog()
DIGEST = "sha256:" + "ab" * 32


def by(places, kind):
    return [p for p in places if p.kind == kind]


class TestTheInventory:
    def test_every_kind_of_place_of_the_repository_is_listed(self):
        places = versions.inventory()
        kinds = {p.kind for p in places}
        assert kinds == set(versions.KINDS)
        paths = {p.path for p in by(places, "base-image")}
        assert {"targets/_template/Dockerfile.gpu", "targets/Rockchip/dockerfile", "Docker Setup/Dockerfile"} <= paths
        assert not any(p.startswith("tests/") for p in paths), "the tests' fixtures are not places of the repository"
        assert {p.what for p in by(places, "action")} >= {"actions/checkout", "actions/setup-python"}
        assert {p.what for p in by(places, "tool")} >= {"zig", "cargo-deny", "cargo-zigbuild"}
        (rust,) = by(places, "rust-toolchain")
        assert rust.pinned and rust.version.count(".") == 2
        assert {p.what for p in by(places, "source-pin")} == {"llama_cpp.source", "whisper_cpp.source"}
        assert {p.what for p in by(places, "build-base")} == {f"build_bases.{b.id}" for b in CATALOG.build_bases}

    def test_every_action_is_pinned_by_a_full_commit_sha(self):
        loose = [(p.path, p.what, p.version) for p in by(versions.inventory(), "action") if not p.pinned]
        assert loose == []

    def test_the_gpu_template_is_on_the_newest_base_of_the_catalogue(self):
        gpu = [p for p in versions.inventory() if p.path == "targets/_template/Dockerfile.gpu"]
        assert gpu and all(p.pinned and p.newer_in_catalogue is None for p in gpu)

    def test_a_base_older_than_the_catalogues_newest_of_its_distribution_is_named(self):
        newest_debian = max((b for b in CATALOG.build_bases if b.id.startswith("debian-")),
                            key=lambda b: int(b.id.split("-")[1]))
        assert versions.newer_base("debian:bookworm-slim", CATALOG) == newest_debian.id
        assert versions.newer_base(f"debian:11-slim@{DIGEST}", CATALOG) == newest_debian.id
        assert versions.newer_base(newest_debian.image, CATALOG) is None
        assert versions.newer_base("ubuntu:22.04", CATALOG) is not None
        # Another image, or a tag without a release, has nothing to compare with.
        for ref in ("rust:1.97.0-slim-trixie", "debian:stable-slim", "alpine:3.22", "nvidia/cuda:13.4.2-devel-ubuntu24.04"):
            assert versions.newer_base(ref, CATALOG) is None, ref

    def test_the_from_lines_are_read_as_docker_reads_them(self, tmp_path):
        folder = tmp_path / "targets" / "board"
        folder.mkdir(parents=True)
        (folder / "Dockerfile").write_text(
            f"ARG BUILDER_BASE=debian:12-slim@{DIGEST}\n"
            "FROM ${BUILDER_BASE} AS builder\n"
            "FROM builder AS tools\n"
            "FROM ${UNSET_BASE}\n"
            "FROM --platform=linux/arm64 ubuntu:24.04 AS runtime\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "Dockerfile").write_text("FROM debian:10\n", encoding="utf-8")
        (tmp_path / "Docker Setup").mkdir()
        (tmp_path / "Docker Setup" / "Dockerfile.dockerignore").write_text("*\n", encoding="utf-8")
        places = by(versions.inventory(tmp_path, CATALOG), "base-image")
        assert [(p.what, p.version, p.pinned, p.note) for p in places] == [
            ("debian:12-slim", "12-slim", True, "the default of ARG BUILDER_BASE"),
            ("${UNSET_BASE}", "", False, "set at build time by the framework (the catalogue's build_bases)"),
            ("ubuntu:24.04", "24.04", False, ""),
        ]
        assert places[0].newer_in_catalogue and places[2].newer_in_catalogue

    def test_actions_and_tools_of_a_workflow(self, tmp_path):
        folder = tmp_path / ".github" / "workflows"
        folder.mkdir(parents=True)
        (folder / "ci.yml").write_text(
            "jobs:\n  a:\n    steps:\n"
            f"      - uses: actions/checkout@{'1' * 40} # v9.0.0\n"
            "      - uses: someone/action@v2\n"
            "      - uses: ./.github/actions/local\n"
            "      - env:\n          ZIG_VERSION: \"0.14.1\"\n"
            "        run: cargo install --locked cargo-deny --version =0.20.0\n", encoding="utf-8")
        places = versions.inventory(tmp_path, CATALOG)
        assert [(p.what, p.version, p.pinned) for p in by(places, "action")] == [
            ("actions/checkout", "v9.0.0", True), ("someone/action", "v2", False)]
        assert {(p.what, p.version) for p in by(places, "tool")} == {("zig", "0.14.1"), ("cargo-deny", "0.20.0")}

    def test_nothing_is_fetched(self, monkeypatch):
        def refuse(*_a, **_k):
            raise AssertionError("the inventory reached for the network")
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket.socket, "connect", refuse)
        assert versions.inventory()


def load_script():
    spec = importlib.util.spec_from_file_location("versions_report", REPO_ROOT / "scripts" / "versions_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_lists_the_places_and_says_it_is_offline(capsys):
    assert load_script().main([]) == 0
    out = capsys.readouterr().out
    assert "base-image (" in out and "targets/_template/Dockerfile.gpu" in out and "Offline" in out
    assert load_script().main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["kind"] for d in data} == set(versions.KINDS)


def framework_with(tmp_path: Path, **dockerfiles):
    """A framework whose own targets folder holds a target per name with that Dockerfile text."""
    own = tmp_path / "targets"
    folders = {}
    for tid, text in dockerfiles.items():
        folder = own / tid
        folder.mkdir(parents=True)
        (folder / "Dockerfile").write_text(text, encoding="utf-8")
        folders[tid] = folder
    registry = SimpleNamespace(list_targets=lambda: [{"id": t} for t in folders], folder_of=folders.get)
    return SimpleNamespace(target_manager=registry, config=SimpleNamespace(user_targets_dir=str(own),
                                                                          targets_dir=str(own)))


class TestMojo:
    def test_the_check_is_one_of_oracles(self):
        assert check_versions in DEFAULT_CHECKS

    def test_an_older_base_of_an_own_target_is_information_for_the_person(self, tmp_path):
        framework = framework_with(tmp_path, board="FROM debian:bookworm-slim AS builder\nFROM debian:bookworm-slim\n")
        (finding,) = check_versions(framework)
        assert finding.code == "versions.board.base" and finding.severity == Severity.INFO
        assert "debian:bookworm-slim" in finding.message and finding.cannot == "person"
        assert finding.where.endswith("board") and finding.why

    def test_a_target_on_the_newest_base_is_one_ok_line(self, tmp_path):
        newest = max((b for b in CATALOG.build_bases if b.id.startswith("debian-")), key=lambda b: int(b.id.split("-")[1]))
        (finding,) = check_versions(framework_with(tmp_path, board=f"FROM {newest.image}\n"))
        assert finding.severity == Severity.OK

    def test_a_shipped_target_is_not_the_persons_to_change(self, tmp_path):
        framework = framework_with(tmp_path, board="FROM debian:bookworm-slim\n")
        framework.config.user_targets_dir = str(tmp_path / "elsewhere")
        (finding,) = check_versions(framework)
        assert finding.severity == Severity.OK

    @pytest.mark.parametrize("framework", [SimpleNamespace(), SimpleNamespace(target_manager=None)])
    def test_without_a_target_registry_there_is_nothing_to_say(self, framework):
        assert check_versions(framework) == []
