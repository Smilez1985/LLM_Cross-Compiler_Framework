"""The portable model archive llmcf-model/1 (DESIGN, Hugging Face, the row "A model for a computer without a network
travels as a portable model archive"): written from a model at its exact commit, checked in quarantine on import,
accepted by the person, used by a build of repo@commit without any request to Hugging Face, deleted only by the
person, and never uploaded. Hugging Face is a stand-in (its API and its file server); everything else is real."""

import ast
import hashlib
import json
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.hf_lookup import look_up as REAL_LOOK_UP   # before conftest's canned look-up replaces it

REPO = "example-org/tiny-chat"
COMMIT = "a" * 40
BASE = "example-org/tiny-base"


def model_files(folder: Path) -> Path:
    """A small Hugging Face repository as its files: what a conversion needs, and what it does not."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {"config.json": b'{"model_type": "llama"}', "generation_config.json": b"{}",
             "tokenizer.json": b'{"model": {}}', "tokenizer_config.json": b'{"chat_template": "{{x}}"}',
             "chat_template.jinja": b"{{ messages }}", "README.md": b"---\nlicense: apache-2.0\n---\n# card",
             "model.safetensors": bytes(range(256)) * 64, ".gitattributes": b"*.safetensors filter=lfs",
             "onnx/model.onnx": b"onnx", "training_args.bin": b"pickle"}
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    return folder


class Hub:
    """Hugging Face as a stand-in: model_info for the model and its base model, and the file server."""

    def __init__(self, source: Path, license="apache-2.0", gated=False, base_license="apache-2.0"):
        self.source, self.license, self.gated, self.base_license = source, license, gated, base_license
        self.calls = []

    def factory(self, token):
        return self

    def model_info(self, repo, revision=None, files_metadata=False, token=None, **kwargs):
        self.calls.append(repo)
        if repo == BASE:
            return SimpleNamespace(sha="b" * 40, gated=False, card_data={"license": self.base_license}, tags=[],
                                   siblings=[])
        siblings = []
        for path in sorted(p for p in self.source.rglob("*") if p.is_file()):
            name = path.relative_to(self.source).as_posix()
            lfs = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()} if name.endswith(".safetensors") else None
            siblings.append(SimpleNamespace(rfilename=name, size=path.stat().st_size, lfs=lfs))
        return SimpleNamespace(sha=COMMIT, gated=self.gated, card_data={"license": self.license, "base_model": BASE},
                               tags=[], siblings=siblings, safetensors=None)

    def auth_check(self, repo, token=None):
        return True


class Fetcher:
    """The file server: copies the commit's file, as HubFetcher would after its checks."""

    def __init__(self, source: Path):
        self.source, self.fetched = source, []

    def fetch_file(self, repo, commit, file, dest, progress=None, attempts=5):
        assert commit == COMMIT
        self.fetched.append(file.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((self.source / file.name).read_bytes())
        return dest, hashlib.sha256(dest.read_bytes()).hexdigest()


@pytest.fixture
def hub(tmp_path):
    return Hub(model_files(tmp_path / "hub-files"))


@pytest.fixture
def framework(booted_framework, hub, monkeypatch):
    from orchestrator.Core import hf_lookup
    monkeypatch.setattr(hf_lookup, "look_up", REAL_LOOK_UP)   # the real look-up, against the stand-in Hub
    booted_framework.hf_api_factory = hub.factory
    return booted_framework


def write(framework, hub, dest: Path, **kwargs):
    from orchestrator.Core import model_archive as ma
    plan = ma.plan_archive(framework, REPO, None)
    return ma.write_archive(framework, plan, dest, fetcher=Fetcher(hub.source), **kwargs)


def models(framework) -> Path:
    from orchestrator.Core.model_archive import models_dir
    return models_dir(framework)


def nothing_stays(framework):
    from orchestrator.Core import model_archive as ma
    holder = ma.store_root(models(framework)) / ma.QUARANTINE
    assert not holder.exists() or not any(holder.iterdir()), "the quarantine was left behind"
    assert ma.stored(models(framework)) == []


# --- writing ---------------------------------------------------------------------------------------------------

def test_the_archive_carries_what_a_conversion_needs_its_licences_and_a_signature(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    plan = ma.plan_archive(framework, REPO, None)
    assert plan.commit == COMMIT and not plan.ask_licence
    assert plan.licence.license == "apache-2.0" and plan.licence.license_class == "permissive"
    assert [(b.repo, b.license) for b in plan.base_models] == [(BASE, "apache-2.0")]
    written = write(framework, hub, tmp_path / "tiny")
    assert written.path.name == "tiny.llmcfmodel" and written.path.is_file()
    assert written.sha256 == hashlib.sha256(written.path.read_bytes()).hexdigest()
    with zipfile.ZipFile(written.path) as zf:
        names = zf.namelist()
        assert names[:2] == [ma.MANIFEST_NAME, ma.SIGNATURE_NAME]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
        manifest = json.loads(zf.read(ma.MANIFEST_NAME))
    carried = sorted(names[2:])
    assert carried == ["README.md", "chat_template.jinja", "config.json", "generation_config.json",
                       "model.safetensors", "tokenizer.json", "tokenizer_config.json"]   # no ONNX, pickle, dot file
    assert manifest["format"] == "llmcf-model/1" and manifest["kind"] == "model" and manifest["commit"] == COMMIT
    weights = (hub.source / "model.safetensors").read_bytes()
    assert {"path": "model.safetensors", "size": len(weights),
            "sha256": hashlib.sha256(weights).hexdigest()} in manifest["files"]
    assert manifest["files_sha256"] == ma.files_digest(ma.ArchiveFile(**f) for f in manifest["files"])
    # the online computer keeps the Builder's complete copy of the commit (its build can use it too)
    from orchestrator.Core.local_models import model_folder_complete
    assert model_folder_complete(ma._copy_folder(framework, REPO, COMMIT))


def test_a_gated_or_unclear_licence_is_confirmed_before_anything_is_written(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    hub.license = "other"
    plan = ma.plan_archive(framework, REPO, None)
    assert plan.ask_licence and plan.licence.license_class == "unknown"
    with pytest.raises(ma.ArchiveError) as e:
        ma.write_archive(framework, plan, tmp_path / "x", fetcher=Fetcher(hub.source))
    assert e.value.code == "needs_person" and not list(tmp_path.glob("x*"))
    written = ma.write_archive(framework, plan, tmp_path / "x", fetcher=Fetcher(hub.source), licence_confirmed=True)
    assert written.manifest.licence.license == "other"


def test_offline_mode_refuses_writing_before_any_request(framework, hub):
    from orchestrator.Core import model_archive as ma
    framework.config.set("offline_mode", True)
    with pytest.raises(Exception) as e:
        ma.plan_archive(framework, REPO, None)
    assert getattr(e.value, "blocker", "") == "offline_mode" and hub.calls == []


# --- taking in -------------------------------------------------------------------------------------------------

def test_take_in_checks_asks_for_the_licence_and_puts_the_files_in_place(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    from orchestrator.Core.local_models import local_copy, models_here
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    shown = []

    def decide(prepared):
        shown.append(prepared.summary("en"))
        return True
    folder = ma.take_archive(framework, written.path, actor="cli", decide=decide, sha256=written.sha256)
    text = shown[0]
    assert REPO in text and COMMIT in text and "apache-2.0" in text and f"Base model {BASE}" in text
    assert "this computer's own key" in text
    archive = ma.copy_of(models(framework), REPO, COMMIT)
    assert archive is not None and archive.folder == folder and archive.complete()
    assert (archive.model_folder / "model.safetensors").read_bytes() == (hub.source / "model.safetensors").read_bytes()
    assert archive.taken["actor"] == "cli" and archive.taken["licence_accepted"] is True
    from orchestrator.Core.disk_plan import folder as cfg_folder
    copy = local_copy(cfg_folder(framework, "cache_dir", "cache"), models(framework), REPO, COMMIT)
    assert copy.where == "model_archive" and copy.path == archive.model_folder
    labels = {m.label: m for m in models_here(framework, for_ai=True)}
    assert f"{REPO}@{COMMIT}" in labels and labels[f"{REPO}@{COMMIT}"].where == "model_archive"
    nothing_left = ma.store_root(models(framework)) / ma.QUARANTINE
    assert not any(nothing_left.iterdir())
    # the same commit again is refused, never overwritten
    with pytest.raises(ma.ArchiveError) as e:
        ma.prepare_take(framework, written.path)
    assert e.value.code == "exists"


def test_no_to_the_licence_leaves_nothing(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    assert ma.take_archive(framework, written.path, actor="gui", decide=lambda p: False) is None
    nothing_stays(framework)


def test_only_the_person_takes_an_archive_in(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    prepared = ma.prepare_take(framework, written.path)
    for actor in ("mcp", "ai", "ditto"):
        with pytest.raises(PermissionError):
            ma.finish_take(prepared, actor=actor, licence_accepted=True)
    ma.discard(prepared)
    nothing_stays(framework)


def rewrite(src: Path, dest: Path, change) -> Path:
    """A copy of the archive with `change(members)` applied: members is a list of [ZipInfo, bytes]."""
    with zipfile.ZipFile(src) as zf:
        members = [[i, zf.read(i.filename)] for i in zf.infolist()]
    change(members)
    import warnings
    with warnings.catch_warnings(), zipfile.ZipFile(dest, "w") as out:
        warnings.simplefilter("ignore")   # a member twice is the point of one change
        for info, data in members:
            out.writestr(info, data)
    return dest


def _member(members, name):
    return next(m for m in members if m[0].filename == name)


def _flip_weight_byte(members):
    m = _member(members, "model.safetensors")
    data = bytearray(m[1])
    data[100] ^= 0xFF
    m[1] = bytes(data)


def _extra_file(members):
    members.append([zipfile.ZipInfo("extra.py", (1980, 1, 1, 0, 0, 0)), b"print('hi')"])


def _bad_signature(members):
    m = _member(members, "llmcf-model.sig")
    sig = json.loads(m[1])
    sig["sig"] = ("A" if sig["sig"][0] != "A" else "B") + sig["sig"][1:]
    m[1] = json.dumps(sig).encode()


def _manifest_changed(members):
    """The weight replaced and the manifest made to match it, under the old signature."""
    from orchestrator.Core import model_archive as ma
    m = _member(members, "llmcf-model.json")
    manifest = json.loads(m[1])
    w = _member(members, "model.safetensors")
    w[1] = b"\0" * len(w[1])
    for f in manifest["files"]:
        if f["path"] == "model.safetensors":
            f["sha256"] = hashlib.sha256(w[1]).hexdigest()
    manifest["files_sha256"] = ma.files_digest(ma.ArchiveFile(**f) for f in manifest["files"])
    m[1] = json.dumps(manifest).encode()


def _digest_wrong(members):
    m = _member(members, "llmcf-model.json")
    manifest = json.loads(m[1])
    manifest["files_sha256"] = "0" * 64
    m[1] = json.dumps(manifest).encode()


def _unsigned(members):
    members.remove(_member(members, "llmcf-model.sig"))


def _link(members):
    info = zipfile.ZipInfo("link", (1980, 1, 1, 0, 0, 0))
    info.external_attr = (0o120777) << 16
    members.append([info, b"/etc/passwd"])


def _missing_file(members):
    members.remove(_member(members, "config.json"))


def _twice(members):
    members.append([zipfile.ZipInfo("config.json", (1980, 1, 1, 0, 0, 0)), b"{}"])


@pytest.mark.parametrize("change,code", [(_flip_weight_byte, "mismatch"), (_extra_file, "mismatch"),
                                         (_bad_signature, "mismatch"), (_manifest_changed, "mismatch"),
                                         (_digest_wrong, "invalid_value"), (_unsigned, "mismatch"),
                                         (_link, "mismatch"), (_missing_file, "mismatch"), (_twice, "mismatch")])
def test_a_tampered_archive_is_refused_and_nothing_of_it_stays(framework, hub, tmp_path, change, code):
    from orchestrator.Core import model_archive as ma
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    bad = rewrite(written.path, tmp_path / "bad.llmcfmodel", change)
    with pytest.raises(ma.ArchiveError) as e:
        ma.prepare_take(framework, bad)
    assert e.value.code == code, str(e.value)
    nothing_stays(framework)


def test_a_re_signed_archive_needs_the_writers_key_when_it_is_given(framework, hub, tmp_path):
    """Someone changed the weights and signed the manifest with a key of their own: the signature is valid, so the
    summary names a key that is not this computer's; with the writer's public keys given (--from) it is refused."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from orchestrator.Core import model_archive as ma
    from orchestrator.Core.community_pack import sign_model_archive
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    other = Ed25519PrivateKey.generate()

    def resign(members):
        _manifest_changed(members)
        manifest = ma.ModelManifest.model_validate_json(_member(members, "llmcf-model.json")[1])
        _member(members, "llmcf-model.sig")[1] = sign_model_archive(manifest.canonical_bytes(),
                                                                    other).model_dump_json().encode()
    bad = rewrite(written.path, tmp_path / "bad.llmcfmodel", resign)
    prepared = ma.prepare_take(framework, bad)
    assert not prepared.own_key and "not this computer's own key" in prepared.summary("en")
    ma.discard(prepared)
    own = ma.signing_key(framework).public_key()
    with pytest.raises(ma.ArchiveError) as e:
        ma.prepare_take(framework, bad, trusted=[own])
    assert "not one of the keys you gave" in str(e.value)
    nothing_stays(framework)


def test_a_wrong_file_sha256_is_refused_first(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    with pytest.raises(ma.ArchiveError) as e:
        ma.prepare_take(framework, written.path, sha256="0" * 64)
    assert e.value.code == "mismatch"
    nothing_stays(framework)


def test_an_archive_signature_never_verifies_as_a_pack_signature(framework):
    from orchestrator.Core.community_pack import PackError, sign_model_archive, _verify, _MANIFEST_CONTEXT
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    sig = sign_model_archive(b"{}", key)
    with pytest.raises(PackError):
        _verify(_MANIFEST_CONTEXT + b"{}", sig, [key.public_key()], None, "pack")


# --- building from it ------------------------------------------------------------------------------------------

def take(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    # the Builder's own copy on the "online computer" goes: only the archive is left, as on a computer without one
    from orchestrator.utils.atomic import remove_tree
    remove_tree(ma._copy_folder(framework, REPO, COMMIT).parent)
    return ma.take_archive(framework, written.path, actor="gui", decide=lambda p: True)


def test_a_build_of_repo_at_commit_mounts_the_archive_without_any_request(framework, hub, tmp_path, monkeypatch):
    from orchestrator.Core import hf_hub
    from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
    folder = take(framework, hub, tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("Hugging Face was asked")
    monkeypatch.setattr(hf_hub, "hub_api", no_network)
    engine = framework.orchestrator.build_engine
    monkeypatch.setattr(engine, "_hub_fetcher", no_network)
    for offline in (False, True):
        framework.config.set("offline_mode", offline)
        engine.config = framework.config
        config = BuildConfiguration(build_id="b1", timestamp="t", model_source=REPO, target_arch="ARM64",
                                    target_format=ModelFormat.GGUF, output_dir="out", model_branch=COMMIT)
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        inner, volumes = engine._stage_model(config, progress)
        assert inner == "/build-cache/input-model"
        assert volumes == {str(folder / "model"): {"bind": inner, "mode": "ro"}}
        assert config.model_revision == COMMIT
        assert any("model archive" in line for line in progress.logs)


def test_the_build_request_names_the_model_by_repo_at_commit(framework, hub, tmp_path):
    from orchestrator.Core.local_models import choice_for
    from orchestrator.schemas.build import BuildRequestIn
    take(framework, hub, tmp_path)
    request = BuildRequestIn(model=f"{REPO}@{COMMIT}", target="ARM64", format="gguf")
    assert (request.model, request.revision) == (REPO, COMMIT)
    assert choice_for(framework, f"{REPO}@{COMMIT}") == f"{REPO}@{COMMIT}"
    framework.config.set("offline_mode", True)
    service = framework.build_service
    service.check_model_here(REPO, COMMIT)   # offline: the archive is the copy here, so nothing is refused


def test_an_archive_copy_with_a_changed_file_is_not_used(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    folder = take(framework, hub, tmp_path)
    (folder / "model" / "config.json").write_bytes(b"{}")
    assert ma.copy_of(models(framework), REPO, COMMIT) is None
    (folder / "model" / "config.json").unlink()
    assert ma.copy_of(models(framework), REPO, COMMIT) is None


# --- the person's deletion -------------------------------------------------------------------------------------

def test_only_the_persons_accepted_plan_deletes_an_archive(framework, hub, tmp_path):
    from orchestrator.Core import model_archive as ma
    from orchestrator.Core.deletion import accept
    folder = take(framework, hub, tmp_path)
    plan = ma.deletion_plan(models(framework))
    assert [i.full_path for i in plan.items] == [str(folder)] and plan.items[0].kind == "model_archive"
    audit = SimpleNamespace(records=[], record=lambda *a, **k: audit.records.append((a, k)))
    with pytest.raises(PermissionError):
        ma.delete(models(framework), accept(plan, "mcp", "permanent"), audit=audit)
    assert folder.is_dir()
    result = ma.delete(models(framework), accept(plan, "cli", "permanent"), audit=audit)
    assert result.done and not folder.exists() and audit.records


# --- never uploaded --------------------------------------------------------------------------------------------

def test_no_upload_path_exists_for_a_model_archive():
    """The archive is not a pack kind (the community export and pull request never take it), no MCP or Ditto tool
    reaches it, and the only modules that use it are its own, the person's two windows and the local model list."""
    from orchestrator.Core.community_pack import PACK_KINDS
    assert "model" not in PACK_KINDS
    users = set()
    for path in (REPO_ROOT / "orchestrator").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("model_archive"):
                users.add(path.relative_to(REPO_ROOT).as_posix())
            elif isinstance(node, ast.ImportFrom) and node.module == "orchestrator.Core" and \
                    any(a.name == "model_archive" for a in node.names):
                users.add(path.relative_to(REPO_ROOT).as_posix())
    assert users <= {"orchestrator/cli.py", "orchestrator/Core/local_models.py", "orchestrator/Core/builder.py",
                     "orchestrator/Core/model_library.py", "orchestrator/gui/models_tab.py",
                     "orchestrator/gui/model_archive_window.py"}, users
    source = (REPO_ROOT / "orchestrator" / "Core" / "model_archive.py").read_text(encoding="utf-8")
    import re
    for word in (r"\brequests\b", r"\burllib\b", r"\bupload", r"\bhttp\.client\b", r"\bsubprocess\b", r"\bgh\b",
                 r"\.post\(", r"\.put\("):
        assert not re.search(word, source.replace("never uploaded", "")), word
    for path in (REPO_ROOT / "orchestrator" / "tools").glob("tools_*.py"):
        assert "model_archive" not in path.read_text(encoding="utf-8"), path.name


def test_an_archive_is_not_a_community_pack(framework, hub, tmp_path):
    from orchestrator.Core.community_pack import PackError, read_manifest
    written = write(framework, hub, tmp_path / "tiny.llmcfmodel")
    with pytest.raises(PackError):
        read_manifest(written.path.read_bytes())


# --- the command line ------------------------------------------------------------------------------------------

@pytest.fixture
def cli(framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(framework, tmp_path, monkeypatch)


def test_the_cli_writes_takes_lists_and_deletes(cli, framework, hub, tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.Core import hf_hub, model_archive as ma
    monkeypatch.setattr(hf_hub, "HubFetcher", lambda *a, **k: Fetcher(hub.source))
    dest = tmp_path / "out.llmcfmodel"
    result = cli.invoke("models", "archive", "write", REPO, "--to", str(dest))
    assert result.code == 0, result.out
    assert "Model archive written" in result.out and dest.is_file()
    remove = ma._copy_folder(framework, REPO, COMMIT).parent
    from orchestrator.utils.atomic import remove_tree
    remove_tree(remove)
    result = cli.invoke("models", "archive", "take", str(dest))
    assert result.code == 2 and "at a terminal" in result.out and ma.stored(models(framework)) == []
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    from click.testing import CliRunner  # noqa: F401 - the input below goes through make_cli's runner
    monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: True)
    result = cli.invoke("models", "archive", "take", str(dest))
    assert result.code == 0, result.out
    assert "apache-2.0" in result.out and f"Taken in: {REPO}@{COMMIT}" in result.out
    listed = json.loads(cli.invoke("models", "archive", "list", "--json").out)
    assert [(a["label"], a["complete"]) for a in listed] == [(f"{REPO}@{COMMIT}", True)]
    rows = json.loads(cli.invoke("models", "list", "--kind", "archive", "--json").out)
    assert [r["name"] for r in rows] == [f"{REPO}@{COMMIT}"]
    monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: "permanent")
    result = cli.invoke("models", "archive", "delete", f"{REPO}@{COMMIT}")
    assert result.code == 0, result.out
    assert ma.stored(models(framework)) == []


def test_the_cli_refuses_a_gated_licence_without_a_terminal(cli, framework, hub, tmp_path):
    hub.gated = "manual"
    result = cli.invoke("models", "archive", "write", REPO, "--to", str(tmp_path / "g"))
    assert result.code != 0 and not list(tmp_path.glob("g*"))


# --- the window ------------------------------------------------------------------------------------------------

def _wait(dialog):
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + 30
    while dialog.busy() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()
    assert not dialog.busy()


def test_the_models_tab_opens_the_window_that_writes_and_takes_an_archive(qapp, framework, hub, tmp_path,
                                                                          monkeypatch):
    from orchestrator.Core import hf_hub, model_archive as ma
    from orchestrator.gui.models_tab import ModelsTab
    monkeypatch.setattr(hf_hub, "HubFetcher", lambda *a, **k: Fetcher(hub.source))
    tab = ModelsTab(framework)
    dialog = tab.open_archives()
    dialog.txt_repo.setText(REPO)
    dest = tmp_path / "gui.llmcfmodel"
    dialog.write_archive(path=str(dest))
    _wait(dialog)   # the look-up
    _wait(dialog)   # the write
    assert dest.is_file() and "Model archive written" in dialog.txt_result.toPlainText()
    from orchestrator.utils.atomic import remove_tree
    remove_tree(ma._copy_folder(framework, REPO, COMMIT).parent)
    dialog.take_archive(path=str(dest), accept=False)
    _wait(dialog)
    assert "Not taken in" in dialog.txt_result.toPlainText()
    nothing_stays(framework)
    dialog.take_archive(path=str(dest), accept=True)
    _wait(dialog)
    assert f"Taken in: {REPO}@{COMMIT}" in dialog.txt_result.toPlainText()
    assert REPO in dialog.list_archives.item(0).text()
    dialog.close()
    tab.refresh()
    assert any(r.kind == "archive" and r.name == f"{REPO}@{COMMIT}" for r in tab.rows)
    # Delete... in the window: the deletion dialog's accepted plan (the person's "Yes, delete for good")
    from orchestrator.Core.deletion import accept
    monkeypatch.setattr("orchestrator.gui.deletion_dialog.ask_deletion",
                        lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    framework.audit = SimpleNamespace(record=lambda *a, **k: None)
    dialog = tab.open_archives()
    dialog.list_archives.setCurrentRow(0)
    result = dialog.delete_selected()
    assert result is not None and result.done and ma.stored(models(framework)) == []
    dialog.close()


def test_the_window_asks_before_writing_an_unclear_licence(qapp, framework, hub, tmp_path, monkeypatch):
    from orchestrator.Core import hf_hub
    from orchestrator.gui.model_archive_window import ModelArchiveDialog
    monkeypatch.setattr(hf_hub, "HubFetcher", lambda *a, **k: Fetcher(hub.source))
    hub.license = "other"
    dialog = ModelArchiveDialog(framework, lang="en")
    dialog.txt_repo.setText(REPO)
    dialog.write_archive(path=str(tmp_path / "no.llmcfmodel"), confirmed=False)
    _wait(dialog)
    assert not list(tmp_path.glob("no*")) and "not on the list of permissive licences" in \
        dialog.txt_result.toPlainText()
    dialog.close()
