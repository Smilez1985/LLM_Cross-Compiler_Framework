"""Several people share one installation: the running framework never writes into an installed copy.

An installed copy (install.sh and install.bat copy no .git) is shared by everyone who uses llmcf on the computer and
is read-only for them. Its settings, builds, caches, models, logs, the person's own targets and the community packs
live in the person's own folder (~/.llm-framework, LLM_FRAMEWORK_HOME); the person's targets folder is read after the
shipped targets by the target registry, the Builder, the CLI, the build engine and the AI tools. What an earlier
version wrote into the installation is offered for moving (Core/install_migration.py): asked, never overwritten,
never deleted. A developer checkout (a git checkout the person can write to) keeps its data inside, as before.

The first class runs the real CLI from a read-only copy of the shipped files (no .git), so every path the code
derives from its own location is the copy's; a snapshot of the copy before and after proves that nothing was written
(also where the read-only mode does not stop a write: as root).
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager

# What the copy holds: the files the installer ships, without the tests and the documentation (not run here)
LEFT_OUT = ("tests/", "docs/", "evals/", ".github/")
OWN = "Shop Board"
OWN_ID = "shop_board"


def shipped_files():
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True)
    if listed.returncode != 0:
        pytest.skip("no git checkout to copy the shipped files from")
    return [f for f in listed.stdout.decode("utf-8").split("\0")
            if f and not f.startswith(LEFT_OUT) and (REPO_ROOT / f).is_file()]


def snapshot(root: Path):
    """{relative path: (kind, size, mtime)} of everything below root (nothing is opened)."""
    found = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder, name)
            st = path.lstat()
            kind = "dir" if stat.S_ISDIR(st.st_mode) else "file"
            found[path.relative_to(root).as_posix()] = (kind, 0 if kind == "dir" else st.st_size,
                                                       0 if kind == "dir" else st.st_mtime_ns)
    return found


def set_writable(root: Path, writable: bool):
    if os.name != "posix":
        return
    for folder, dirs, files in os.walk(root):
        for name in files:
            path = Path(folder, name)
            path.chmod(path.stat().st_mode | stat.S_IWUSR if writable else path.stat().st_mode & ~0o222)
        Path(folder).chmod(0o755 if writable else 0o555)


@pytest.fixture(scope="module")
def installed_copy(tmp_path_factory):
    """A read-only installed copy of the shipped files (no .git), and the person's home beside it."""
    base = tmp_path_factory.mktemp("shared-install")
    root = base / "llm-cross-compiler-framework"
    for rel in shipped_files():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dest)
    set_writable(root, False)
    yield root, base
    set_writable(root, True)
    shutil.rmtree(base, ignore_errors=True)


def run(root: Path, home: Path, *args, script=None, stdin=None, timeout=240):
    """The CLI (or a Python script) of the copy, as the person whose home is `home`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "FRAMEWORK_", "PYTHON"))}
    env.update(PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", HOME=str(home), USERPROFILE=str(home),
               LLM_FRAMEWORK_HOME=str(home / ".llm-framework"), LLM_FRAMEWORK_SECRET_STORAGE="ephemeral",
               LLM_FRAMEWORK_NO_AUDIO="1", PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
               XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
               XDG_DATA_HOME=str(home / ".local" / "share"), HF_HOME=str(home / ".cache" / "huggingface"),
               QT_QPA_PLATFORM="offscreen")
    work = home / "work"
    work.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-c", script, *args] if script else [sys.executable, "-m", "orchestrator.cli", "-q", *args]
    return subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=timeout, input=stdin)


INSTALL_SCRIPT = r'''
import sys
from pathlib import Path
import orchestrator
from orchestrator.Core.framework import FrameworkManager
from orchestrator.tools.registry import ToolContext
assert Path(orchestrator.__file__).resolve().parents[1] == Path(sys.argv[1]).resolve(), orchestrator.__file__
fm = FrameworkManager()
assert fm.initialize(), fm.init_error
try:
    cm = fm.community_manager
    folder = cm.finish_install(cm.prepare_install_file(Path(sys.argv[2])))
    print("INSTALLED", folder)
    fm.target_manager.initialize()
    print("LISTED", sorted(fm.target_manager._targets))
    out = fm.tool_registry.call("list_targets", {}, ToolContext("ditto", session="s"))
    print("TOOL", sorted(t.id for t in out.targets))
    from orchestrator.Core.build_service import BuildService
    from orchestrator.schemas.build import BuildRequestIn
    request = BuildRequestIn(model="Qwen/Qwen2.5-0.5B-Instruct", target=sys.argv[3], format="gguf",
                             quantization="Q4_K_M")
    plan = fm.build_service.plan(request, look_up_hub=False)
    print("PLAN", type(plan).__name__)
finally:
    fm.shutdown()
'''


class TestTheInstalledCopyIsNeverWritten:

    def test_cli_module_create_build_check_and_a_pack_install_write_only_into_the_persons_folders(
            self, installed_copy):
        root, base = installed_copy
        home = base / "home-ada"
        before = snapshot(root)

        assert run(root, home, "config", "show").returncode == 0
        listed = run(root, home, "targets", "list")
        assert listed.returncode == 0, listed.stderr[-2000:]
        assert "Rockchip" in listed.stdout and "ARM64" in listed.stdout

        made = run(root, home, "module", "create", "--name", OWN, "--arch", "aarch64", "--sdk", "None",
                   "--description", "the workshop board", "--base-image", "debian:bookworm-slim",
                   "--packages", "cmake", "--cpu-flags", "")
        assert made.returncode == 0, made.stdout[-2000:] + made.stderr[-2000:]
        own_targets = home / ".llm-framework" / "targets"
        assert (own_targets / OWN_ID / "target.yml").is_file(), "the new module is in the person's targets folder"

        listed = run(root, home, "targets", "list")
        assert OWN_ID in listed.stdout and "Rockchip" in listed.stdout, "shipped and own targets, one list"
        check = run(root, home, "build", "preflight", "-t", OWN_ID)
        assert check.returncode == 0, check.stdout[-2000:] + check.stderr[-2000:]
        refused = run(root, home, "module", "create", "--name", "rock-chip", "--arch", "aarch64", "--sdk", "None",
                      "--description", "d", "--base-image", "debian:bookworm-slim", "--packages", "cmake",
                      "--cpu-flags", "")
        assert refused.returncode != 0 and not (own_targets / "rock_chip").exists(), \
            "a person's target never captures a shipped target's name"

        exported = run(root, home, "zoo", "export", "target", OWN_ID, "--author", "Ada", "--out", str(home / "packs"))
        assert exported.returncode == 0, exported.stdout[-2000:] + exported.stderr[-2000:]
        (pack,) = (home / "packs").glob("*.zip")
        shutil.rmtree(own_targets / OWN_ID)   # installed again from its pack below
        installed = run(root, home, str(root), str(pack), OWN_ID, script=INSTALL_SCRIPT)
        assert installed.returncode == 0, installed.stdout[-3000:] + installed.stderr[-3000:]
        assert f"INSTALLED {own_targets / OWN_ID}" in installed.stdout
        assert (own_targets / OWN_ID / ".lcf-provenance.json").is_file()
        for line in ("LISTED", "TOOL"):
            ids = next(x for x in installed.stdout.splitlines() if x.startswith(line))
            assert OWN_ID in ids and "Rockchip" in ids, ids
        assert "PLAN" in installed.stdout

        for args in (("zoo", "list"), ("tools", "list"), ("doctor",), ("config", "take-over")):
            run(root, home, *args)   # their verdicts depend on this computer; what matters is what they write
        assert snapshot(root) == before, "nothing may be written into the installed copy"
        assert (home / ".llm-framework" / "environments").is_dir(), "the config folder is the person's"

    def test_two_people_keep_their_own_targets_apart(self, installed_copy):
        root, base = installed_copy
        before = snapshot(root)
        ada, bob = base / "home-ada2", base / "home-bob"
        assert run(root, ada, "module", "create", "--name", "Ada Board", "--arch", "aarch64", "--sdk", "None",
                   "--description", "d", "--base-image", "debian:bookworm-slim", "--packages", "cmake",
                   "--cpu-flags", "").returncode == 0
        assert "ada_board" in run(root, ada, "targets", "list").stdout
        assert "ada_board" not in run(root, bob, "targets", "list").stdout, "another person's targets are theirs"
        assert snapshot(root) == before


# --- the person's targets folder, read besides the shipped one ----------------------------------------------------

def own_module(folder: Path, name: str = OWN, *others: Path) -> Path:
    from orchestrator.Core.module_generator import ModuleGenerator
    generator = ModuleGenerator(folder, others)
    return generator.generate_module({"module_name": name, "architecture": "aarch64", "sdk": "None",
                                      "description": "d", "base_os": "debian:bookworm-slim", "packages": ["cmake"],
                                      "cpu_flags": "", "supported_boards": [], "detection_commands": "lscpu"})


@pytest.fixture
def framework_with_own_targets(make_framework, tmp_path):
    own = tmp_path / "own-targets"
    fm = make_framework(initialize=False)
    fm.config.set("user_targets_dir", str(own))
    own_module(own, OWN, REPO_ROOT / "targets")
    assert fm.initialize()
    return fm, own


class TestThePersonsTargetsFolder:

    def test_the_target_manager_lists_shipped_and_own_targets_and_resolves_both(self, framework_with_own_targets):
        fm, own = framework_with_own_targets
        tm = fm.target_manager
        assert tm.target_roots == [REPO_ROOT / "targets", own] and tm.user_targets_dir == own
        ids = {t["id"] for t in tm.list_targets()}
        assert {"Rockchip", "ARM64", OWN_ID} <= ids
        assert tm.folder_of(OWN_ID) == own / OWN_ID and tm.folder_of("Rockchip") == REPO_ROOT / "targets" / "Rockchip"
        assert fm.orchestrator.build_engine.resolve_target_dir(OWN_ID) == own / OWN_ID

    def test_an_own_target_with_a_shipped_id_never_replaces_the_shipped_one(self, tmp_path):
        from orchestrator.Core.target_manager import read_target_roots
        shipped, own = tmp_path / "shipped", tmp_path / "own"
        for root in (shipped, own):
            (root / "board").mkdir(parents=True)
            (root / "board" / "target.yml").write_text(yaml.safe_dump({"metadata": {"id": "board", "name": root.name}}))
        found = read_target_roots([shipped, own])
        assert found["board"][0] == shipped / "board"

    def test_a_new_module_never_captures_a_shipped_name(self, tmp_path):
        with pytest.raises(ValueError, match="Rockchip"):
            own_module(tmp_path / "own", "rock-chip", REPO_ROOT / "targets")
        assert not (tmp_path / "own" / "rock_chip").exists()

    def test_the_build_engine_reads_both_folders_without_the_registry(self, tmp_path):
        from orchestrator.Core.builder import BuildEngine
        own = tmp_path / "own"
        own_module(own)
        config = ConfigManager(tmp_path / "configs")
        config.set("targets_dir", str(REPO_ROOT / "targets"))
        config.set("user_targets_dir", str(own))
        engine = BuildEngine(config, base_dir=tmp_path)
        assert engine.target_roots == [REPO_ROOT / "targets", own]
        assert engine.resolve_target_dir(OWN_ID) == own / OWN_ID
        assert engine.resolve_target_dir("rockchip") == REPO_ROOT / "targets" / "Rockchip"

    def test_the_cli_lists_and_creates_in_the_persons_folder(self, framework_with_own_targets):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        fm, own = framework_with_own_targets

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = fm
                return self

        listed = CliRunner().invoke(cli_module.cli, ["-q", "targets", "list", "--format", "json"], obj=Ctx())
        assert listed.exit_code == 0, listed.output
        assert OWN_ID in listed.output and "Rockchip" in listed.output
        ctx = Ctx()
        ctx.framework_manager = fm
        made = CliRunner().invoke(cli_module.cli, ["-q", "module", "create", "--name", "Second Board", "--arch",
                                                   "aarch64", "--sdk", "None", "--description", "d", "--base-image",
                                                   "debian:bookworm-slim", "--packages", "cmake", "--cpu-flags", ""],
                                  obj=ctx)
        assert made.exit_code == 0, made.output
        assert (own / "second_board" / "target.yml").is_file()
        assert not (REPO_ROOT / "targets" / "second_board").exists()

    def test_the_ai_tools_list_it_and_create_modules_in_it(self, framework_with_own_targets):
        from orchestrator.tools.registry import ToolContext, ToolPolicy
        fm, own = framework_with_own_targets
        fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fm.tool_registry.list()]))
        listed = fm.tool_registry.call("list_targets", {}, ToolContext("ditto", session="s"))
        ids = {t.id: t for t in listed.targets}
        assert OWN_ID in ids and "Rockchip" in ids
        assert ids["Rockchip"].trust == "shipped" and ids[OWN_ID].trust == "own"

    def test_the_builder_offers_the_persons_targets(self, framework_with_own_targets, qapp):
        from orchestrator.gui.builder_tab import BuilderTab
        fm, _own = framework_with_own_targets
        tab = BuilderTab(fm)
        try:
            ids = [tab.target_combo.itemData(i) for i in range(tab.target_combo.count())]
            assert OWN_ID in ids and "Rockchip" in ids
        finally:
            tab.deleteLater()

    def test_community_target_packs_go_to_the_persons_folder(self, framework_with_own_targets):
        fm, own = framework_with_own_targets
        cm = fm.community_manager
        assert cm.targets_dir == own and cm.target_roots == [REPO_ROOT / "targets", own]
        assert cm._target_folder(OWN_ID) == own / OWN_ID and cm._target_folder("rockchip") is not None
        assert {OWN_ID, "Rockchip"} <= set(cm.exportable_targets())


# --- installed copy or developer checkout ---------------------------------------------------------------------------

@pytest.fixture
def installation(tmp_path, monkeypatch):
    """A stand-in installation (no .git: an installed copy) and the person's folder."""
    root = tmp_path / "install"
    (root / "configs").mkdir(parents=True)
    (root / "targets").mkdir()
    monkeypatch.setattr(ConfigManager, "installation_config_dir", staticmethod(lambda: root / "configs"))
    monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home" / ".llm-framework"))
    return root


class TestInstalledCopyOrCheckout:

    def test_an_installed_copy_puts_everything_into_the_persons_folder(self, installation, tmp_path):
        state = tmp_path / "home" / ".llm-framework"
        cfg = ConfigManager()
        assert not ConfigManager.developer_checkout()
        assert cfg.config_dir == state
        assert cfg.get("user_targets_dir") == str(state / "targets")
        for key, name in (("build_dir", "output"), ("cache_dir", "cache"), ("models_dir", "models"),
                          ("datasets_dir", "datasets"), ("logs_dir", "logs")):
            assert cfg.get(key) == str(state / name), key
        assert cfg.get("targets_dir") == "targets", "the shipped targets are read from the installation"

    def test_a_developer_checkout_keeps_its_data_inside_as_before(self, installation, tmp_path):
        (installation / ".git").mkdir()
        cfg = ConfigManager()
        assert ConfigManager.developer_checkout()
        assert cfg.config_dir == installation / "configs"
        assert cfg.get("user_targets_dir") == "" and cfg.get("cache_dir") == "cache"

    def test_community_packs_follow_the_same_rule(self, installation, tmp_path):
        from types import SimpleNamespace
        from orchestrator.Core.community_manager import CommunityManager
        fw = SimpleNamespace(config=ConfigManager(), info=SimpleNamespace(installation_path=installation))
        cm = CommunityManager(fw)
        assert cm.community_dir == tmp_path / "home" / ".llm-framework" / "community"
        (installation / ".git").mkdir()
        assert CommunityManager(fw).community_dir == installation / "community"


# --- taking over what an earlier version wrote into the installation ------------------------------------------------

def plant_earlier_data(root: Path):
    (root / "configs" / "config.yml").write_text("language: de\n")
    (root / "configs" / "environments").mkdir()   # an empty folder ConfigManager made: nothing to take over
    (root / "output" / "build-1").mkdir(parents=True)
    (root / "output" / "build-1" / "model.gguf").write_text("weights")
    (root / "targets" / "Rockchip").mkdir()   # shipped
    (root / "targets" / "Rockchip" / "target.yml").write_text("metadata: {id: Rockchip}\n")
    (root / "targets" / "my_board").mkdir()
    (root / "targets" / "my_board" / "target.yml").write_text("metadata: {id: my_board}\n")
    (root / "community" / "recipes" / "r1").mkdir(parents=True)
    (root / "community" / "recipes" / "r1" / "recipe.json").write_text("{}")


class TestTakingOver:

    def test_what_an_earlier_version_left_is_found_with_its_new_place(self, installation, tmp_path):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        state = tmp_path / "home" / ".llm-framework"
        found = {i.relative: i for i in im.found(ConfigManager())}
        assert set(found) == {"configs/config.yml", "output", "targets/my_board", "community/recipes"}
        assert found["configs/config.yml"].destination == state / "config.yml"
        assert found["output"].destination == state / "output"
        assert found["targets/my_board"].destination == state / "targets" / "my_board"
        assert found["community/recipes"].destination == state / "community" / "recipes"
        if os.name == "posix":
            assert all(i.mine for i in found.values())

    def test_a_developer_checkout_has_nothing_to_take_over(self, installation):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        (installation / ".git").mkdir()
        assert im.found(ConfigManager()) == []

    def test_the_installers_manifest_names_the_shipped_targets(self, installation):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        (installation / im.INSTALLER_MANIFEST).write_text("targets/my_board/target.yml\0orchestrator/cli.py\0")
        assert "targets/my_board" not in {i.relative for i in im.found(ConfigManager())}

    def test_taking_over_moves_and_never_overwrites(self, installation, tmp_path):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        state = tmp_path / "home" / ".llm-framework"
        state.mkdir(parents=True)
        (state / "config.yml").write_text("language: en\n")   # the person's own settings already
        report = im.take_over(im.found(ConfigManager()))
        assert set(report.moved) == {"configs/config.yml", "output", "targets/my_board", "community/recipes"}
        assert report.renamed == ["configs/config.yml"] and not report.failed
        assert (state / "config.yml").read_text() == "language: en\n", "never overwritten"
        assert (state / im.CONFLICTS / "configs" / "config.yml").read_text() == "language: de\n"
        assert (state / "output" / "build-1" / "model.gguf").read_text() == "weights"
        assert (state / "targets" / "my_board" / "target.yml").is_file()
        assert not (installation / "output").exists() and (installation / "targets" / "Rockchip").is_dir()
        assert im.found(ConfigManager()) == []

    def test_an_original_that_cannot_be_removed_stays_and_nothing_is_lost(self, installation, tmp_path,
                                                                          monkeypatch):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)

        def read_only(path):
            raise PermissionError(13, "Permission denied", str(path))
        monkeypatch.setattr(im, "_remove", read_only)
        report = im.take_over(im.found(ConfigManager()))
        assert set(report.copied) == {"configs/config.yml", "output", "targets/my_board", "community/recipes"}
        assert (installation / "output" / "build-1" / "model.gguf").is_file(), "the original stays"
        assert (tmp_path / "home" / ".llm-framework" / "output" / "build-1" / "model.gguf").is_file()

    def test_a_no_is_kept_and_the_command_still_offers_it(self, installation):
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        cfg = ConfigManager()
        im.decline(im.pending(cfg))
        assert im.pending(cfg) == [] and len(im.found(cfg)) == 4
        assert im.offered_once() and not im.offered_once()

    def test_another_accounts_items_are_asked_about_on_their_own(self, installation, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from orchestrator.Core import install_migration as im
        plant_earlier_data(installation)
        monkeypatch.setattr(im, "_owner", lambda path: im.OTHER if path.name == "output" else im.YOU)
        out = CliRunner().invoke(cli_module.cli, ["-q", "config", "take-over", "--yes"])
        assert out.exit_code == 0, out.output
        assert "may belong to another account" in out.output and "owned by another account" in out.output
        assert (installation / "output" / "build-1" / "model.gguf").is_file(), "not moved without its own yes"
        assert not (installation / "targets" / "my_board").exists(), "the person's own items are moved"
        out = CliRunner().invoke(cli_module.cli, ["-q", "config", "take-over", "--others"])
        assert out.exit_code == 0, out.output
        assert not (installation / "output").exists()
        assert "Nothing of yours is left" in CliRunner().invoke(cli_module.cli, ["-q", "config", "take-over"]).output

    def test_the_gui_asks_once_per_group_and_keeps_a_no(self, installation, monkeypatch, qapp):
        from types import SimpleNamespace
        from orchestrator.Core import install_migration as im
        from orchestrator.gui import plain
        from orchestrator.gui.menus import update
        plant_earlier_data(installation)
        monkeypatch.setattr(im, "_owner", lambda path: im.OTHER if path.name == "output" else im.YOU)
        asked, shown = [], []
        answers = [True, False]   # yes to the person's own items, no to the other account's
        monkeypatch.setattr(plain, "ask", lambda parent, title, text: asked.append(text) or answers.pop(0))
        monkeypatch.setattr(plain, "plain_box", lambda *a, **k: shown.append(a))
        window = SimpleNamespace(framework=SimpleNamespace(config=ConfigManager()))
        assert update.offer_take_over(window) is True
        assert len(asked) == 2 and "configs/config.yml" in asked[0] and "output" in asked[1]
        assert (installation / "output").is_dir() and not (installation / "targets" / "my_board").exists()
        assert update.offer_take_over(window) is False and len(asked) == 2, "a no is not asked at start again"


def test_the_shipped_target_list_matches_the_repository():
    """The take-over tells shipped targets from the person's by this list where install.sh left no manifest."""
    from orchestrator.Core import install_migration as im
    from orchestrator.Core.community_manager import SHIPPED_TARGET_FOLDERS
    shipped = {p.name for p in (REPO_ROOT / "targets").iterdir() if p.is_dir()}
    assert im.SHIPPED_TARGETS == shipped and SHIPPED_TARGET_FOLDERS | {"_template"} == shipped
