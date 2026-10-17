"""The core functions behind the knowledge and Community Hub mouse paths (DECISIONS W1', wave 5 chain "knowledge").

Logic the command line kept inline moved into core functions; the CLI calls them with its output unchanged, and the
GUI calls the same functions:

- knowledge_snapshots.snapshot_disk: what the snapshots use against the free space, and whether the next one fits
  (`llm-cli knowledge snapshots`, Tools > Knowledge Snapshots...);
- migration.storage_status / storage_lines / convert_refusal: the storage in every state and when a conversion is
  offered (`llm-cli knowledge storage [show|convert]`);
- knowledge_sources: every source with its licence and what a pack carries of it (`llm-cli knowledge sources`);
- CommunityManager.details / exportable_targets, community_pack.read_public_key_files / needs_passphrase (`llm-cli
  zoo show`, `zoo export target`, `zoo install --from` and `--passphrase`).
"""

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pyrage
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from orchestrator.Core import community_pack as cp
from orchestrator.Core import knowledge_snapshots as ks
from orchestrator.Core import knowledge_sources as sources_mod
from test_doctor_fixes import run_cli
from test_edge_store import build_old_store, failed_state
from test_rag import ingest_all, make_rag  # noqa: F401 - the fixture
from test_zoo import World, invoke, make_target, pack_bytes

GB = 1000 ** 3


def usage(free, total=100 * GB):
    return lambda path: shutil._ntuple_diskusage(total, total - free, free)


def snapshot(state: Path, name: str, size: int) -> None:
    folder = state / "knowledge_snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.jsonl.gz").write_bytes(b"x" * size)


def framework_for(state: Path, **extra):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d), **extra)


# --- the snapshot disk line ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("free, verdict", [(50 * GB, "enough"), (int(3.2 * GB), "tight"),
                                           (int(2.0 * GB), "does_not_fit")])
def test_the_disk_line_says_whether_the_next_snapshot_fits(tmp_path, monkeypatch, free, verdict):
    state = tmp_path / "state"
    snapshot(state, "pre_ingest_1", 10)
    snapshot(state, "pre_ingest_2", 4_000_000)   # the newest one: the estimate of the next (small files only)
    monkeypatch.setattr(ks, "_disk_usage", usage(free))
    disk = ks.snapshot_disk(state)
    assert disk is not None and disk.verdict == verdict and disk.room.estimate == 4_000_000
    assert disk.total == 4_000_010 and disk.line() == ks.DISK_LINE.format(size="4.0 MB",
                                                                             free=ks.size_text(free))
    warning = disk.warning()
    if verdict == "enough":
        assert warning == ""
    elif verdict == "tight":
        assert warning == ks.NEXT_TIGHT.format(size="4.0 MB") and "little free space" in warning
    else:
        assert warning.startswith(ks.NEXT_DOES_NOT_FIT.format(size="4.0 MB")) and ks.FREE_SPACE_STEP in warning
    _, out = run_cli(framework_for(state), monkeypatch, "knowledge", "snapshots")
    assert disk.line() in out
    if warning:
        assert " ".join(warning.split()) in " ".join(out.split()), "the CLI says the same words"
    else:
        assert "The next snapshot" not in out
    assert sorted(p.name for p in (state / "knowledge_snapshots").iterdir()) == ["pre_ingest_1.jsonl.gz",
                                                                                 "pre_ingest_2.jsonl.gz"]


def test_the_disk_line_is_left_out_when_the_disk_cannot_be_read(tmp_path, monkeypatch):
    def broken(path):
        raise OSError("no disk")
    monkeypatch.setattr(ks, "_disk_usage", broken)
    assert ks.snapshot_disk(tmp_path) is None
    assert ks.snapshot_disk(None) is None


# --- the storage in every state --------------------------------------------------------------------------------------

def cli_storage(state: Path, monkeypatch, *args) -> str:
    return run_cli(framework_for(state), monkeypatch, "knowledge", "storage", *args)[1]


def assert_cli_says(status, out):
    from orchestrator.rag import migration
    flat = " ".join(out.split())
    for _, line in migration.storage_lines(status):
        assert " ".join(line.split()) in flat, line


def test_a_fresh_knowledge_base_is_in_qdrant_edge_and_nothing_is_offered(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    status = migration.storage_status(tmp_path)
    assert (status.used, status.reason_code, status.can_convert) == ("qdrant-edge", "", False)
    assert status.folder == str(tmp_path / "qdrant_edge") and migration.convert_refusal(tmp_path) == "nothing"
    assert migration.storage_lines(status) == [("", f"Storage: Qdrant Edge in {tmp_path / 'qdrant_edge'}.")]
    assert_cli_says(status, cli_storage(tmp_path, monkeypatch))


def test_an_old_store_not_converted_yet_can_be_converted_now(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    status = migration.storage_status(state)
    assert (status.used, status.reason_code, status.state, status.can_convert) == ("old-local", "not_converted", "",
                                                                                   True)
    assert status.folder == str(state / "qdrant") and status.old_folder == str(state / "qdrant")
    lines = [text for _, text in migration.storage_lines(status)]
    assert f"{migration.NOT_CONVERTED}." in lines and lines[-1].startswith("Old storage folder:")
    assert migration.convert_refusal(state) == ""
    assert_cli_says(status, cli_storage(state, monkeypatch))
    assert not (state / "qdrant_edge").exists(), "showing the status converts nothing"


def test_a_failed_conversion_shows_why_and_how_it_is_tried_again(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    state = failed_state(tmp_path)
    status = migration.storage_status(state)
    assert (status.used, status.reason_code, status.state, status.failure, status.code) == (
        "old-local", "failed", "failed", "a reason", "verify_mismatch")
    styles = dict((text[:20], style) for style, text in migration.storage_lines(status))
    assert "yellow" in styles.values() and status.can_convert
    assert any(migration.retry_sentence("verify_mismatch") == text for _, text in migration.storage_lines(status))
    assert_cli_says(status, cli_storage(state, monkeypatch))


def test_a_converted_knowledge_base_names_the_kept_old_folder(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    migration.run(state)
    status = migration.storage_status(state)
    assert (status.used, status.state, status.can_convert) == ("qdrant-edge", "converted", False)
    assert status.points > 0 and status.old_folder == str(state / "qdrant") and status.old_folder_bytes > 0
    assert "is kept until you delete it" in migration.storage_lines(status)[-1][1]
    assert_cli_says(status, cli_storage(state, monkeypatch))
    out = cli_storage(state, monkeypatch, "convert")
    assert "There is no old storage to convert." in out


def test_without_the_engine_the_old_storage_is_used_and_not_offered(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    monkeypatch.setitem(sys.modules, "qdrant_edge", None)
    status = migration.storage_status(state)
    assert (status.used, status.reason_code, status.can_convert) == ("old-local", "engine_missing", False)
    assert migration.convert_refusal(state) == "engine_missing"
    assert_cli_says(status, cli_storage(state, monkeypatch))
    result, out = run_cli(framework_for(state), monkeypatch, "knowledge", "storage", "convert")
    assert result.exit_code == 1 and "nothing can be converted" in out


def test_qdrant_edge_that_cannot_be_loaded_is_named_and_a_server_is_named(tmp_path, monkeypatch):
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    migration.run(state)
    monkeypatch.setitem(sys.modules, "qdrant_edge", None)
    status = migration.storage_status(state)
    assert (status.used, status.reason_code, status.folder) == ("", "edge_unavailable", "")
    assert migration.storage_lines(status)[0] == ("red", migration.EDGE_UNAVAILABLE + ".")
    server = migration.storage_status(state, used="server")
    assert migration.storage_lines(server)[0][1].startswith("Storage: a Qdrant server")
    given = migration.storage_status(state, used="old-local", reason="why")
    assert (given.reason, given.folder) == ("why", str(state / "qdrant"))


# --- the knowledge sources -------------------------------------------------------------------------------------------

SOURCES = [
    {"source": "https://docs.example/a", "kind": "web", "licence": "MIT", "copyright": "(c) A",
     "pack_share": "full_text", "share_reason": "MIT allows it"},
    {"source": "https://docs.example/b", "kind": "web", "licence": "", "copyright": "",
     "pack_share": "link_and_facts", "share_reason": "no licence"},
    {"source": "pack:notes/x", "kind": "pack", "licence": "", "copyright": "", "pack_share": "not_exported",
     "share_reason": "its author shares it"},
]


class FakeRag:
    def __init__(self, entries=SOURCES):
        self.entries, self.calls = entries, 0

    def list_sources(self):
        self.calls += 1
        return [dict(e) for e in self.entries]


def test_sources_are_filtered_by_kind_and_carry_the_same_words(tmp_path, monkeypatch):
    rag = FakeRag()
    assert [e["source"] for e in sources_mod.sources(rag, "web")] == ["https://docs.example/a",
                                                                       "https://docs.example/b"]
    assert len(sources_mod.sources(rag)) == 3 and sources_mod.sources(rag, "docs") == []
    with pytest.raises(ValueError):
        sources_mod.sources(rag, "nonsense")
    assert [sources_mod.carried(e) for e in SOURCES] == ["full text", "link and short facts", "not exported"]
    assert sources_mod.carried({}) == "-" and sources_mod.totals(SOURCES) == (1, 1)
    assert sources_mod.totals_sentence(SOURCES).startswith("3 source(s): a knowledge pack would carry 1 with")
    assert sources_mod.empty_sentence("docs") == "The knowledge base holds no source of kind docs."
    framework = framework_for(tmp_path, ensure_rag_manager=lambda: rag)
    _, out = run_cli(framework, monkeypatch, "knowledge", "sources", "--kind", "web")
    assert "full text" in out and "link and short facts" in out and "pack:notes/x" not in out
    assert " ".join(sources_mod.totals_sentence(SOURCES[:2]).split()) in " ".join(out.split())
    _, out = run_cli(framework, monkeypatch, "knowledge", "sources", "--kind", "docs")
    assert sources_mod.empty_sentence("docs") in out


def test_hidden_text_left_out_reads_the_same_in_the_cli(tmp_path, monkeypatch):
    """Recon P1: how much hidden text was left out of a source before it was stored, kind by kind in a fixed order;
    "none" when the check found nothing, "-" for a source stored without it (the framework's documents, older pages)."""
    entries = [dict(SOURCES[0], hidden_removed={"invisible_characters": 3, "display_none": 1, "aria_hidden": 2}),
               dict(SOURCES[1], hidden_removed={}), SOURCES[2]]
    assert [sources_mod.removed_text(e) for e in entries] == [
        "aria-hidden 2, display:none 1, invisible characters 3", "none", "-"]
    assert sources_mod.removed_text({"hidden_removed": {"display_none": 0, "x": "2"}}) == "none"
    _, out = run_cli(framework_for(tmp_path, ensure_rag_manager=lambda: FakeRag(entries)), monkeypatch,
                     "knowledge", "sources")
    assert "Hidden text left out" in out and "aria-hidden 2, display:none 1, invisible characters 3" in out


def test_the_cli_kind_choices_are_the_core_ones():
    from orchestrator.cli import cli
    option = next(p for p in cli.commands["knowledge"].commands["sources"].params if p.name == "kind")
    assert tuple(option.type.choices) == sources_mod.KIND_CHOICES


def test_a_knowledge_base_that_is_off_is_refused_with_the_same_words(tmp_path, monkeypatch):
    with pytest.raises(sources_mod.KnowledgeOff) as exc:
        sources_mod.knowledge_base(framework_for(tmp_path, ensure_rag_manager=lambda: None))
    assert str(exc.value) == sources_mod.OFF
    with pytest.raises(sources_mod.KnowledgeOff):
        sources_mod.knowledge_base(framework_for(tmp_path))
    result, out = run_cli(framework_for(tmp_path, ensure_rag_manager=lambda: None), monkeypatch,
                          "knowledge", "sources")
    assert result.exit_code == 1 and " ".join(sources_mod.OFF.split()) in " ".join(out.split())


def test_a_real_knowledge_base_lists_its_sources(make_rag):  # noqa: F811
    rag = make_rag()
    ingest_all(rag, {"https://docs.example/page": "# Page\n\nSome text about the board."})
    listed = sources_mod.sources(rag, "web")
    assert [e["source"] for e in listed] == ["https://docs.example/page"]
    assert sources_mod.carried(listed[0]) in sources_mod.CARRIED.values()


# --- the zoo ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_details_show_an_index_entry_and_an_installed_recipe(world, tmp_path):
    import json

    from test_recipes import recipe_data
    world.add(world.target_pack())
    world.publish()
    world.cm.load_index(refresh=True)
    (entry,) = world.cm.details("DEMO-BOARD")
    assert entry.recipe_text is None and entry.item.entry is not None
    lines = entry.lines()
    assert lines[0].startswith("target demo-board 1.2.0 by alice") and "Trust: community" in lines
    assert any(line.startswith(f"Download: {entry.item.entry.url} (") and entry.item.entry.sha256 in line
               for line in lines)
    root = tmp_path / "r"
    root.mkdir()
    data = recipe_data()
    data.update(id="demo-recipe", target={"id": "demo-board", "pack_sha256": "a" * 64}, format="gguf",
                board="demo", quantization="Q8_0")
    (root / "recipe.yml").write_text(json.dumps(data))
    (tmp_path / "r.zip").write_bytes(pack_bytes(root, kind="recipe", pack_id="demo-recipe", version="1.0.0"))
    world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "r.zip"))
    (recipe,) = world.cm.details("demo-recipe", "recipe")
    assert recipe.recipe_text == (recipe.item.path / "recipe.yml").read_text(encoding="utf-8")
    assert f"Installed: 1.0.0 in {recipe.item.path}" in recipe.lines()
    assert world.cm.details("demo-recipe", "target") == [] and world.cm.details("nothing") == []
    out = invoke(world, "show", "demo-recipe").output
    assert "demo-recipe" in out and "Installed: 1.0.0" in out


def test_exportable_targets_are_the_target_folders(world):
    make_target(world.root / "targets" / "my-board")
    make_target(world.root / "targets" / "_template")
    (world.root / "targets" / "no-yml").mkdir()
    (world.root / "targets" / ".hidden").mkdir()
    assert world.cm.exportable_targets() == ["my-board"]


def test_key_files_give_the_keys_of_the_kind_asked_for(world, tmp_path):
    exported = invoke(world, "keys").output
    (tmp_path / "me.keys").write_text(exported, encoding="utf-8")
    (senders,) = cp.read_public_key_files([tmp_path / "me.keys"], Ed25519PublicKey)
    (recipient,) = cp.read_public_key_files([str(tmp_path / "me.keys")], pyrage.x25519.Recipient)
    assert isinstance(senders, Ed25519PublicKey) and isinstance(recipient, pyrage.x25519.Recipient)
    assert cp.read_public_key_files([], Ed25519PublicKey) == []
    (tmp_path / "bin.keys").write_bytes(b"\xff\xfe\x00\x81")
    with pytest.raises(cp.KeyFileError) as exc:
        cp.read_public_key_files([tmp_path / "bin.keys"], Ed25519PublicKey)
    assert exc.value.code == "not_text" and exc.value.file == str(tmp_path / "bin.keys")
    assert isinstance(exc.value, cp.PackError)
    (tmp_path / "empty.keys").write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(cp.KeyFileError) as exc:
        cp.read_public_key_files([tmp_path / "empty.keys"], Ed25519PublicKey)
    assert exc.value.code == "no_keys"


def test_a_passphrase_pack_is_told_apart_without_decrypting(world, tmp_path):
    data = world.target_pack()
    sender = Ed25519PrivateKey.generate()
    by_pass = cp.encrypt_pack(data, sender=sender, passphrase="correct horse battery")
    to_key = cp.encrypt_pack(data, sender=sender, recipients=[pyrage.x25519.Identity.generate().to_public()])
    (tmp_path / "p.lcfpack").write_bytes(by_pass)
    assert cp.needs_passphrase(tmp_path / "p.lcfpack") is True and cp.needs_passphrase(by_pass) is True
    assert cp.needs_passphrase(to_key) is False and cp.needs_passphrase(data) is False
    assert cp.needs_passphrase(b"not a zip") is False
