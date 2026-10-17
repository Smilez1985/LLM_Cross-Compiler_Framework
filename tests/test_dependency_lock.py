"""The dependency lock (sixth round, decision 14): pyproject.toml in PEP 621 form, uv.lock instead of poetry.lock, and
the hash-pinned exports requirements.txt, requirements-gui.txt (with the optional extra 'gui', PySide6; DECISIONS
round 11, A3) and requirements-dev.txt that the installers and the CI install from.

These checks need no uv and no network: they read the files. The CI job "Lock files" runs uv itself."""

import re
import tomllib
from collections import defaultdict

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from conftest import REPO_ROOT

PROJECT = "llm-cross-compiler-framework"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def lock() -> dict:
    return tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))


def exported(name: str) -> dict:
    """{(package, version): hashes} of an exported requirements file."""
    pins, current = {}, None
    for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if match:
            current = (norm(match.group(1)), match.group(2))
            assert current not in pins, f"{name}: {current} twice"
            pins[current] = set()
        elif current is not None:
            pins[current] |= set(re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
    return pins


def locked_closure(data: dict, roots) -> set:
    """Every (package, version) uv.lock reaches from `roots` (markers ignored: the exports keep every platform)."""
    packages = defaultdict(list)
    for package in data["package"]:
        packages[norm(package["name"])].append(package)
    seen, todo = set(), [(norm(r["name"]), tuple(r.get("extra", ()))) for r in roots]
    while todo:
        name, extras = todo.pop()
        for package in packages[name]:
            edges = list(package.get("dependencies", []))
            for extra in extras:
                edges += package.get("optional-dependencies", {}).get(extra, [])
            key = (name, package["version"])
            fresh = key not in seen
            seen.add(key)
            if fresh or extras:
                todo += [(norm(d["name"]), tuple(d.get("extra", ()))) for d in edges]
    return seen


def project_entry(data: dict) -> dict:
    return next(p for p in data["package"] if p["name"] == PROJECT)


def artifact_hashes(data: dict) -> dict:
    hashes = defaultdict(set)
    for package in data["package"]:
        for artifact in package.get("wheels", []) + ([package["sdist"]] if "sdist" in package else []):
            hashes[(norm(package["name"]), package["version"])].add(artifact["hash"].split(":", 1)[1])
    return hashes


def test_pyproject_is_pep621_and_poetry_is_gone():
    data = pyproject()
    assert "poetry" not in data.get("tool", {}) and "poetry" not in data["build-system"]["build-backend"]
    project = data["project"]
    assert project["name"] == PROJECT and project["requires-python"] == ">=3.11,<3.15"
    assert project["scripts"] == {"llm-gui": "orchestrator.main:main", "llm-cli": "orchestrator.cli:cli",
                                  "llmcf": "orchestrator.cli:cli"}
    assert {norm(Requirement(r).name) for r in data["dependency-groups"]["dev"]} == {
        "pytest", "ruff", "bandit", "pip-audit", "shellcheck-py", "actionlint-py"}
    # PySide6 is the optional extra 'gui' (A3): the base install, the CLI and the MCP server run without Qt
    assert {norm(Requirement(r).name) for r in project["optional-dependencies"]["gui"]} == {"pyside6-essentials"}
    assert not [r for r in project["dependencies"] if norm(Requirement(r).name).startswith(("pyside6", "shiboken6"))]
    assert not (REPO_ROOT / "poetry.lock").exists()
    assert not (REPO_ROOT / "Docker Setup" / "pyproject.toml").exists(), "the stale copy must not come back"


def test_uv_lock_was_made_from_this_pyproject():
    """What `uv lock --check` checks, without uv: the lock records the same requirements as pyproject.toml."""
    data, project = lock(), pyproject()
    assert SpecifierSet(data["requires-python"]) == SpecifierSet(project["project"]["requires-python"])
    metadata = project_entry(data)["metadata"]

    def as_set(entries):
        return {(norm(e["name"]), SpecifierSet(e.get("specifier", ""))) for e in entries}

    def wanted(specs):
        return {(norm(Requirement(s).name), Requirement(s).specifier) for s in specs}

    extras = project["project"].get("optional-dependencies", {})
    assert as_set(metadata["requires-dist"]) == wanted(project["project"]["dependencies"]).union(
        *(wanted(specs) for specs in extras.values()))
    assert sorted(metadata.get("provides-extras", [])) == sorted(extras)
    assert as_set(metadata["requires-dev"]["dev"]) == wanted(project["dependency-groups"]["dev"])


def test_exports_pin_exactly_what_uv_lock_pins_with_its_hashes():
    data = lock()
    entry, hashes = project_entry(data), artifact_hashes(data)
    for name, roots in (("requirements.txt", entry["dependencies"]),
                        ("requirements-gui.txt", entry["dependencies"] + entry["optional-dependencies"]["gui"]),
                        ("requirements-dev.txt", entry["dev-dependencies"]["dev"])):
        pins = exported(name)
        assert set(pins) == locked_closure(data, roots), f"{name} is not the export of uv.lock"
        for pin, listed in pins.items():
            assert listed, f"{name}: {pin} has no hash, so pip would not check it"
            assert listed <= hashes[pin], f"{name}: {pin} lists a file uv.lock does not know"


def test_exports_say_how_they_are_made():
    for name, scope in (("requirements.txt", "--no-dev"), ("requirements-gui.txt", "--no-dev --extra gui"),
                        ("requirements-dev.txt", "--only-group dev")):
        head = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()[:2]
        assert head[0] == "# This file was autogenerated by uv via the following command:"
        assert head[1].startswith("#    uv export --frozen --format requirements-txt") and scope in head[1]
        assert "--no-emit-project" in head[1] and head[1].endswith(f"-o {name}")
        assert ("--extra" in head[1]) == (name == "requirements-gui.txt")
    assert not [pin for pin in exported("requirements.txt") if pin[0].startswith(("pyside6", "shiboken6"))], \
        "the base export has no Qt"


def test_installers_install_only_the_hash_pinned_requirements_of_this_checkout():
    """The desktop installers install the GUI too (requirements-gui.txt, A3); install.sh --headless (a server, the
    framework image without --gui) installs requirements.txt, without Qt. Each from the hash-pinned export only."""
    for name in ("install.sh", "install.bat"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        installs = re.findall(r"pip(?:\.exe)?\"?\)? install (?!--upgrade pip)[^\r\n|>]*", text.replace("$Pip", "pip"))
        assert installs and all(re.search(r'--require-hashes -r "?(\$REQUIREMENTS|requirements(-gui)?\.txt)\b', i)
                                for i in installs), (name, installs)
        assert "requirements-gui.txt" in text, f"{name} installs the GUI"
    assert re.search(r'REQUIREMENTS="requirements\.txt"', (REPO_ROOT / "install.sh").read_text(encoding="utf-8")), \
        "install.sh --headless installs the base export"
