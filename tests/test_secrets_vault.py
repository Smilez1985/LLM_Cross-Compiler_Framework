"""Credential vault v2: keyring probe, explicit storage choices, v2 format with key ids, rotation, the
cross-process lock, cred/ records and the migration of old stores and names.

Keys and tokens are built at runtime; the keyring is in memory (or a fake backend), nothing leaves the machine.
"""

import io
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet, InvalidToken

from conftest import REPO_ROOT
from orchestrator.Core import secrets_manager as secrets_module
from orchestrator.Core import vault_key
from orchestrator.Core.doctor import Severity, check_secrets
from orchestrator.Core.secrets_manager import SecretsManager, credential_name, git_work_tree, origin_hash
from orchestrator.utils.file_lock import file_lock

KEY = "sk-" + "proj-" + "Vault0123456789abcdefABCDEF"
HF = "hf_" + "VaultTokenAbCdEfGhIjKlMnOpQrSt"
SERVICE = vault_key.KEYRING_SERVICE


@pytest.fixture(autouse=True)
def fresh_vault_state(monkeypatch):
    """Each test probes its own keyring, asks its own passphrase and uses fast scrypt parameters."""
    monkeypatch.setattr(vault_key, "SCRYPT_N", 2 ** 14)
    vault_key.reset_probe()
    vault_key.clear_derived_keys()
    yield
    vault_key.set_passphrase_prompt(None)
    vault_key.reset_probe()
    vault_key.clear_derived_keys()


@pytest.fixture
def use_backend():
    """Installs a keyring backend for one test (restored afterwards)."""
    keyring = pytest.importorskip("keyring")
    previous = keyring.get_keyring()

    def install(backend):
        keyring.set_keyring(backend)
        return backend

    yield install
    keyring.set_keyring(previous)


def backend_class(get=None, set_=None, delete=None, name="ScriptedKeyring", module=None):
    from keyring.backend import KeyringBackend

    class Scripted(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.store = {}

        def get_password(self, service, username):
            return get(self, service, username) if get else self.store.get((service, username))

        def set_password(self, service, username, password):
            if set_:
                return set_(self, service, username, password)
            self.store[(service, username)] = password

        def delete_password(self, service, username):
            if delete:
                return delete(self, service, username)
            self.store.pop((service, username), None)

    Scripted.__name__ = Scripted.__qualname__ = name
    if module:
        Scripted.__module__ = module
    return Scripted


def vault(tmp_path, **kwargs) -> SecretsManager:
    sm = SecretsManager(tmp_path / "state", **kwargs)
    assert sm.initialize(), sm.storage_error
    return sm


def store_json(sm) -> dict:
    return json.loads(sm.secrets_file.read_text(encoding="utf-8"))


def key_slot(sm, previous=False) -> tuple:
    """(service, user) of the vault's key in the keyring: each vault has its own service."""
    service = vault_key.vault_service(store_json(sm).get("vault_id"))
    return service, vault_key.KEY_ENTRY + (vault_key.PREVIOUS_SUFFIX if previous else "")


def all_bytes(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())


def helper_command(*argv) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


# ======================================================================================================
# Keyring
# ======================================================================================================

class TestKeyring:

    def test_keyring_holds_only_a_small_versioned_key(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)

        value = memory_keyring.store[key_slot(sm)]
        assert value.startswith("v2:") and len(value) < 100
        assert (SERVICE, vault_key.KEY_ENTRY) not in memory_keyring.store, "a new vault has its own entry"
        data = store_json(sm)
        assert data["version"] == 2 and data["protection"] == "keyring"
        kid = value.split(":")[1]
        assert data["kid"] == kid and data["entries"]["hf_token"]["kid"] == kid
        assert vault_key.check_matches([vault_key.parse_key(value)], data["check"])
        assert vault(tmp_path).get_secret("hf_token") == HF

    def test_8kb_record_round_trips_while_the_keyring_entry_stays_small(self, memory_keyring, tmp_path):
        token = "eyJ" + "a" * 4000 + "." + "b" * 2000 + "." + "c" * 2200  # synthetic JWT-sized bundle, ~8 KB
        record = {"kind": "oauth", "access_token": token, "refresh_token": "r" * 64, "id_token": token[::-1],
                  "expires_at": 1_900_000_000, "client_id": "app-test-client"}
        name = credential_name("openai", "siwc")
        sm = vault(tmp_path)
        assert sm.set_record(name, record)

        assert vault(tmp_path).get_record(name) == {**record, "v": 1}
        assert all(len(v) < 100 for v in memory_keyring.store.values())
        assert len(sm.secrets_file.read_bytes()) > 8000

    def test_nothing_is_stored_in_plaintext(self, memory_keyring, tmp_path, monkeypatch):
        marker_key = "sk-" + "ant-" + "PlainMarker0123456789abcdef"
        marker_refresh = "rt-" + "PlainRefreshMarker0123456789"
        captured = []
        real_replace = os.replace

        def capture(src, dst):
            captured.append(Path(src).read_bytes())  # the temp file, just before it is put in place
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", capture)
        sm = vault(tmp_path)
        assert sm.set_secret("anthropic_api_key", marker_key)
        assert sm.set_record("cred/openai/siwc", {"kind": "oauth", "refresh_token": marker_refresh})
        assert sm.get_secret("anthropic_api_key") == marker_key

        on_disk = all_bytes(tmp_path) + b"".join(captured)
        assert captured
        for marker in (marker_key, marker_refresh, "refresh_token"):
            assert marker.encode() not in on_disk, marker
        assert all(marker_key not in v and marker_refresh not in v for v in memory_keyring.store.values())

    def test_failing_keyring_gives_a_clear_error_and_writes_nothing(self, use_backend, tmp_path):
        def broken(*args):
            raise RuntimeError("Secret Service not available")

        backend = use_backend(backend_class(get=broken, set_=broken, delete=broken)())
        sm = SecretsManager(tmp_path / "state")

        assert sm.initialize() is False
        error = sm.storage_error
        assert error.code == "storage.keyring_unavailable"
        for choice in ("secret_storage", "helper", "passphrase", "insecure", "ephemeral"):
            assert choice in error.message
        assert sm.set_secret("hf_token", HF) is False and sm.get_secret("hf_token") is None
        assert not (tmp_path / "state").exists() and backend.store == {}

    def test_a_keyring_that_keeps_nothing_is_not_used(self, use_backend, tmp_path):
        """Like keyring.backends.null: before v2 every secret was written under a key that was gone at the
        next start."""
        use_backend(backend_class(get=lambda self, s, u: None, set_=lambda self, s, u, p: None)())
        sm = SecretsManager(tmp_path / "state")
        assert sm.initialize() is False
        assert sm.storage_error.code == "storage.keyring_unavailable"
        assert "does not keep" in sm.storage_error.message
        assert not (tmp_path / "state").exists()

    def test_hung_keyring_probe_times_out_within_three_seconds(self, use_backend, tmp_path):
        release = threading.Event()

        def hang(self, service, username, password):
            release.wait(30)

        backend = use_backend(backend_class(set_=hang)())
        try:
            start = time.monotonic()
            sm = SecretsManager(tmp_path / "state")
            assert sm.initialize() is False
            elapsed = time.monotonic() - start
            assert elapsed <= vault_key.PROBE_TIMEOUT + 1.0, elapsed
            assert sm.storage_error.code == "storage.keyring_unavailable"
            assert "did not answer" in sm.storage_error.message
            assert not (tmp_path / "state").exists() and backend.store == {}
        finally:
            release.set()

    def test_probe_runs_once_per_process(self, memory_keyring, tmp_path, monkeypatch):
        probes = []
        real_set = memory_keyring.set_password

        def counting_set(service, username, password):
            if service == vault_key.PROBE_SERVICE:
                probes.append(username)
            real_set(service, username, password)

        monkeypatch.setattr(memory_keyring, "set_password", counting_set)
        vault(tmp_path)
        vault(tmp_path)
        assert len(probes) == 1
        assert not any(service == vault_key.PROBE_SERVICE for service, _ in memory_keyring.store), \
            "the probe entry is removed"

    def test_windows_credentials_stay_on_the_local_machine(self, use_backend, tmp_path):
        seen = []

        def record_set(self, service, username, password):
            seen.append((service, username, getattr(self, "persist", None)))
            self.store[(service, username)] = password

        cls = backend_class(set_=record_set, name="WinVaultKeyring", module="keyring.backends.Windows")
        cls.persist = "enterprise"  # keyring's default, CRED_PERSIST_ENTERPRISE, roams
        use_backend(cls())

        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)

        assert any(s == vault_key.PROBE_SERVICE for s, _, _ in seen)
        assert any(u == vault_key.KEY_ENTRY for _, u, _ in seen)
        assert all(persist == "local machine" for _, _, persist in seen), seen

    def test_missing_key_for_an_existing_vault_is_reported_and_nothing_is_overwritten(self, memory_keyring,
                                                                                         tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        before = sm.secrets_file.read_bytes()
        memory_keyring.store.clear()  # e.g. a new keyring after a reinstall

        fresh = vault(tmp_path)

        assert fresh._load_failed and "missing" in fresh.load_error
        assert fresh.get_secret("hf_token") is None
        assert fresh.set_secret("openai_api_key", KEY) is False
        assert fresh.secrets_file.read_bytes() == before
        assert not any(user == vault_key.KEY_ENTRY for _, user in memory_keyring.store), \
            "no new key for a vault with entries"
        finding = check_secrets(SimpleNamespace(secrets_manager=fresh))[0]
        assert finding.severity == Severity.ERROR and "missing" in finding.message

    def test_an_empty_vault_whose_key_is_gone_gets_a_new_key(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.delete_secret("hf_token")
        memory_keyring.store.clear()
        fresh = vault(tmp_path)
        assert not fresh._load_failed, "nothing to lose"
        assert fresh.set_secret("openai_api_key", KEY)
        assert vault(tmp_path).get_secret("openai_api_key") == KEY


# ======================================================================================================
# Rotation
# ======================================================================================================

class TestRotation:

    def test_rotation_replaces_the_key_and_leaves_no_old_one(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.set_secret("openai_api_key", KEY)
        old_value = memory_keyring.store[key_slot(sm)]
        old_key = vault_key.parse_key(old_value)

        assert sm.rotate_key()

        new_value = memory_keyring.store[key_slot(sm)]
        assert new_value != old_value
        assert key_slot(sm, previous=True) not in memory_keyring.store
        assert old_value not in memory_keyring.store.values()
        data = store_json(sm)
        new_kid = vault_key.parse_key(new_value).kid
        assert data["kid"] == new_kid and {e["kid"] for e in data["entries"].values()} == {new_kid}
        for entry in data["entries"].values():
            with pytest.raises(InvalidToken):
                old_key.fernet().decrypt(entry["ct"].encode())
        assert not vault_key.check_matches([old_key], data["check"])
        fresh = vault(tmp_path)
        assert fresh.get_secret("hf_token") == HF and fresh.get_secret("openai_api_key") == KEY

    def test_interrupted_rotation_is_completed_on_the_next_start(self, memory_keyring, tmp_path, monkeypatch):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)

        def crash(path, data):
            raise OSError(28, "No space left on device")

        with monkeypatch.context() as m:
            m.setattr(SecretsManager, "_write_atomic", staticmethod(crash))
            assert sm.rotate_key() is False
        previous = key_slot(sm, previous=True)
        assert previous in memory_keyring.store, "the old key is kept until every entry is re-encrypted"
        assert sm.get_secret("hf_token") == HF

        fresh = vault(tmp_path)

        assert fresh.get_secret("hf_token") == HF
        assert previous not in memory_keyring.store
        current = vault_key.parse_key(memory_keyring.store[key_slot(fresh)])
        assert {e["kid"] for e in store_json(fresh)["entries"].values()} == {current.kid}

    def test_a_long_running_process_follows_a_rotation_by_another_one(self, memory_keyring, tmp_path):
        cli = vault(tmp_path)
        mcp = vault(tmp_path)
        assert cli.set_secret("hf_token", HF)
        assert mcp.get_secret("hf_token") == HF

        assert cli.rotate_key()

        assert mcp.get_secret("hf_token") == HF
        assert mcp.set_secret("openai_api_key", KEY), "writes use the new key, never the removed one"
        fresh = vault(tmp_path)
        assert fresh.get_secret("hf_token") == HF and fresh.get_secret("openai_api_key") == KEY
        kids = {e["kid"] for e in store_json(fresh)["entries"].values()}
        assert kids == {store_json(fresh)["kid"]}


# ======================================================================================================
# Location and locking
# ======================================================================================================

class TestLocationAndLock:

    @pytest.mark.parametrize("marker", ["dir", "file"], ids=["repository", "worktree"])
    def test_a_store_inside_a_git_work_tree_is_refused(self, memory_keyring, tmp_path, marker):
        repo = tmp_path / "checkout"
        repo.mkdir()
        if marker == "dir":
            (repo / ".git").mkdir()
        else:
            (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
        sm = SecretsManager(repo / "config")

        assert sm.initialize() is False
        assert sm.storage_error.code == "storage.in_git_tree" and str(repo) in sm.storage_error.message
        assert sm.set_secret("hf_token", HF) is False
        assert not (repo / "config").exists()
        assert git_work_tree(tmp_path / "elsewhere" / "secrets.store") is None

    @pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
    def test_a_store_linked_into_a_git_work_tree_is_refused(self, memory_keyring, tmp_path):
        repo = tmp_path / "dotfiles"
        (repo / ".git").mkdir(parents=True)
        (tmp_path / "state").mkdir()
        os.symlink(repo / "secrets.store", tmp_path / "state" / "secrets.store")
        sm = SecretsManager(tmp_path / "state")
        assert sm.initialize() is False and sm.storage_error.code == "storage.in_git_tree"
        assert not (repo / "secrets.store").exists()

    def test_framework_boot_refuses_a_state_folder_inside_a_repository(self, make_framework, tmp_path,
                                                                          monkeypatch):
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(repo / ".llm-framework"))
        fm = make_framework(initialize=False)
        assert fm.initialize() is False
        assert fm.init_error.code == "storage.in_git_tree"
        assert not (repo / ".llm-framework" / "secrets.store").exists()

    def test_a_writer_waits_for_the_lock_of_another_process(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        held, release = threading.Event(), threading.Event()

        def other_process():
            with file_lock(sm.secrets_file.with_name("secrets.store.lock")):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=other_process)
        holder.start()
        assert held.wait(10)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        start = time.monotonic()
        assert sm.set_secret("hf_token", HF)
        assert time.monotonic() - start >= 0.4
        holder.join(10)
        assert vault(tmp_path).get_secret("hf_token") == HF

    def test_a_lock_held_too_long_fails_the_write_cleanly(self, memory_keyring, tmp_path, monkeypatch):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        before = sm.secrets_file.read_bytes()
        monkeypatch.setattr(secrets_module, "LOCK_TIMEOUT", 0.3)
        held, release = threading.Event(), threading.Event()

        def other_process():
            with file_lock(sm.secrets_file.with_name("secrets.store.lock")):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=other_process)
        holder.start()
        try:
            assert held.wait(10)
            assert sm.set_secret("openai_api_key", KEY) is False
            assert sm.secrets_file.read_bytes() == before
        finally:
            release.set()
            holder.join(10)

    def test_credential_lock_serializes_one_credential(self, memory_keyring, tmp_path):
        from orchestrator.utils.file_lock import LockTimeout
        sm = vault(tmp_path)
        results = []

        def second_refresh(name):
            try:
                with sm.credential_lock(name, timeout=0.2):
                    results.append(name)
            except LockTimeout:
                results.append("waited")

        with sm.credential_lock("openai_api_key"):  # the same credential as cred/openai/api_key
            for name in ("cred/openai/api_key", "cred/anthropic/api_key"):
                worker = threading.Thread(target=second_refresh, args=(name,))
                worker.start()
                worker.join(10)
        assert results == ["waited", "cred/anthropic/api_key"]
        assert len(list((tmp_path / "state" / "locks").glob("*.lock"))) == 2

    def test_two_processes_writing_at_once_lose_nothing(self, tmp_path):
        state = tmp_path / "state"
        assert vault(tmp_path, storage="insecure").set_secret("seed", "0")  # key file and vault exist
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from orchestrator.Core.secrets_manager import SecretsManager\n"
            "sm = SecretsManager(Path(sys.argv[1]), storage='insecure')\n"
            "assert sm.initialize(), sm.storage_error\n"
            "for n in range(200):\n"
            "    assert sm.set_secret(f'{sys.argv[2]}-{n}', f'value-{sys.argv[2]}-{n}'), n\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
        env["PYTHONPATH"] = str(REPO_ROOT)
        procs = [subprocess.Popen([sys.executable, "-c", script, str(state), name], env=env, cwd=tmp_path,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ("gui", "mcp")]
        for proc in procs:
            _, err = proc.communicate(timeout=300)
            assert proc.returncode == 0, err.decode(errors="replace")[-2000:]

        fresh = vault(tmp_path, storage="insecure")
        names = fresh.list_secrets()
        assert len(names) == 401
        for name in ("gui", "mcp"):
            for n in (0, 99, 199):
                assert fresh.get_secret(f"{name}-{n}") == f"value-{name}-{n}"


# ======================================================================================================
# Explicit storage choices
# ======================================================================================================

class TestStorageChoices:

    def test_wrong_passphrase_leaves_the_vault_untouched(self, use_backend, tmp_path):
        use_backend(backend_class(get=lambda *a: None, set_=lambda *a: None)())  # no usable keyring at all
        prompts = []

        def prompt(new):
            prompts.append(new)
            return "correct horse battery staple"

        vault_key.set_passphrase_prompt(prompt)
        sm = vault(tmp_path, storage="passphrase")
        assert sm.set_secret("hf_token", HF)
        assert prompts == [True]
        before = sm.secrets_file.read_bytes()
        assert store_json(sm)["kdf"]["name"] == "scrypt"

        vault_key.clear_derived_keys()
        vault_key.set_passphrase_prompt(lambda new: "wrong horse battery staple")
        wrong = SecretsManager(tmp_path / "state", storage="passphrase")
        assert wrong.initialize() is False
        assert wrong.storage_error.code == "storage.wrong_passphrase"
        assert wrong.set_secret("openai_api_key", KEY) is False and wrong.get_secret("hf_token") is None
        assert sm.secrets_file.read_bytes() == before
        assert sorted(p.name for p in sm.secrets_file.parent.glob(".*")) == []

        vault_key.set_passphrase_prompt(prompt)
        assert vault(tmp_path, storage="passphrase").get_secret("hf_token") == HF

    def test_passphrase_is_asked_once_per_process(self, memory_keyring, tmp_path):
        prompts = []
        vault_key.set_passphrase_prompt(lambda new: prompts.append(new) or "a long enough passphrase")
        assert vault(tmp_path, storage="passphrase").set_secret("hf_token", HF)
        assert vault(tmp_path, storage="passphrase").get_secret("hf_token") == HF
        assert prompts == [True]

    def test_passphrase_without_a_terminal_fails_clearly(self, memory_keyring, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setenv("LLM_SECRET_PASSPHRASE", "never read from the environment")
        sm = SecretsManager(tmp_path / "state", storage="passphrase")
        assert sm.initialize() is False
        assert sm.storage_error.code == "storage.passphrase_needed"
        assert not (tmp_path / "state" / "secrets.store").exists()

    def test_scrypt_parameters_default_to_the_design_values(self, monkeypatch):
        monkeypatch.setattr(vault_key, "SCRYPT_N", 2 ** 17)
        params = vault_key.new_scrypt_params()
        assert (params["n"], params["r"], params["p"]) == (2 ** 17, 8, 1)

    @pytest.mark.parametrize("change", [{"n": 2}, {"n": 2 ** 30}, {"r": 1}, {"name": "pbkdf2"}, {"salt": "!!"}])
    def test_tampered_key_derivation_parameters_are_refused(self, change):
        params = {**vault_key.new_scrypt_params(), **change}
        with pytest.raises(vault_key.StorageError):
            vault_key.derive_key("secret", params)

    def test_credential_helper_runs_without_a_shell(self, use_backend, tmp_path):
        use_backend(backend_class(get=lambda *a: None, set_=lambda *a: None)())
        script = tmp_path / "helper.py"
        script.write_text("print('helper-output-' + 'x' * 40)\n")
        marker = tmp_path / "shell-ran"
        command = helper_command(sys.executable, str(script), "$(touch " + str(marker) + ")", ";", "rm", "-rf", "/")

        sm = vault(tmp_path, storage="helper", helper=command)
        assert sm.set_secret("hf_token", HF)
        assert not marker.exists()
        assert store_json(sm)["protection"] == "helper"
        vault_key.clear_derived_keys()
        assert vault(tmp_path, storage="helper", helper=command).get_secret("hf_token") == HF

        other = tmp_path / "other.py"
        other.write_text("print('another-output-' + 'y' * 40)\n")
        before = sm.secrets_file.read_bytes()
        vault_key.clear_derived_keys()
        mismatch = SecretsManager(tmp_path / "state", storage="helper", helper=helper_command(sys.executable, str(other)))
        assert mismatch.initialize() is False and mismatch.storage_error.code == "storage.helper_mismatch"
        assert sm.secrets_file.read_bytes() == before

    def test_failing_helper_explains_without_leaking(self, memory_keyring, tmp_path):
        leaked = "sk-" + "proj-" + "LeakedFromStderr0123456789"
        script = tmp_path / "fail.py"
        script.write_text(f"import sys\nprint('stdout-secret-value')\nprint('denied {leaked}', file=sys.stderr)\n"
                          "sys.exit(3)\n")
        sm = SecretsManager(tmp_path / "state", storage="helper", helper=helper_command(sys.executable, str(script)))
        assert sm.initialize() is False
        message = sm.storage_error.message
        assert sm.storage_error.code == "storage.helper_failed" and "exit code 3" in message
        assert leaked not in message and "stdout-secret-value" not in message
        assert not (tmp_path / "state" / "secrets.store").exists()

        empty = SecretsManager(tmp_path / "state2", storage="helper", helper="")
        assert empty.initialize() is False and "secret_helper" in empty.storage_error.message

    def test_insecure_key_file_only_when_chosen(self, use_backend, tmp_path):
        use_backend(backend_class(get=lambda *a: None, set_=lambda *a: None)())
        assert SecretsManager(tmp_path / "state").initialize() is False
        key_file = tmp_path / "state" / "secrets.store.insecure-key"
        assert not key_file.exists(), "never a silent fallback"

        sm = vault(tmp_path, storage="insecure")
        assert sm.set_secret("hf_token", HF)
        content = json.loads(key_file.read_text())
        assert "NOT ENCRYPTED AT REST" in content["warning"] and content["current"].startswith("v2:")
        if os.name == "posix":
            assert key_file.stat().st_mode & 0o777 == 0o600
        assert store_json(sm)["protection"] == "insecure"
        findings = check_secrets(SimpleNamespace(secrets_manager=sm))
        assert any(f.code == "secrets.storage" and f.severity == Severity.WARN for f in findings)
        assert vault(tmp_path, storage="insecure").get_secret("hf_token") == HF

    def test_ephemeral_keeps_everything_in_memory(self, use_backend, tmp_path):
        use_backend(backend_class(get=lambda *a: None, set_=lambda *a: None)())
        sm = vault(tmp_path, storage="ephemeral")
        assert sm.set_secret("openai_api_key", KEY) and sm.get_secret("openai_api_key") == KEY
        assert sm.list_secrets() == ["cred/openai/api_key"]
        assert sm.rotate_key() and sm.get_secret("openai_api_key") == KEY
        assert sm.delete_secret("openai_api_key") and sm.get_secret("openai_api_key") is None
        assert not (tmp_path / "state").exists(), "nothing is written, not even the audit log"
        findings = check_secrets(SimpleNamespace(secrets_manager=sm))
        assert any(f.code == "secrets.storage" and f.severity == Severity.INFO for f in findings)

    def test_ephemeral_from_the_environment_boots_without_a_keyring(self, make_framework, memory_keyring,
                                                                    tmp_path, monkeypatch):
        def broken(*args):
            raise RuntimeError("no keyring in CI")

        monkeypatch.setattr(memory_keyring, "set_password", broken)
        monkeypatch.setenv("LLM_SECRET_STORAGE", "ephemeral")
        fm = make_framework()
        assert fm.secrets_manager.storage_mode == "ephemeral"
        assert fm.secrets_manager.set_secret("hf_token", HF)
        assert not (tmp_path / "state" / "secrets.store").exists()

    def test_unknown_storage_choice_is_an_error(self, memory_keyring, tmp_path):
        sm = SecretsManager(tmp_path / "state", storage="cloud")
        assert sm.initialize() is False and sm.storage_error.code == "storage.invalid_mode"

    def test_vault_of_another_protection_is_not_opened(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        before = sm.secrets_file.read_bytes()
        other = SecretsManager(tmp_path / "state", storage="insecure")
        assert other.initialize() is False
        assert other.storage_error.code == "storage.mode_mismatch" and "'keyring'" in other.storage_error.message
        assert sm.secrets_file.read_bytes() == before
        assert not (tmp_path / "state" / "secrets.store.insecure-key").exists()

    def test_change_storage_moves_the_vault_and_removes_the_old_key(self, memory_keyring, tmp_path):
        saved = {}
        config = SimpleNamespace(user_state_dir=tmp_path / "state", config_dir=tmp_path / "configs",
                                 set=lambda k, v: saved.__setitem__(k, v),
                                 save_user_config=lambda: saved.__setitem__("saved", True))
        sm = SecretsManager(SimpleNamespace(config=config))
        assert sm.initialize() and sm.set_secret("hf_token", HF)
        slot = key_slot(sm)

        assert sm.change_storage("insecure")

        assert slot not in memory_keyring.store
        assert saved == {"secret_storage": "insecure", "saved": True}
        assert store_json(sm)["protection"] == "insecure"
        assert vault(tmp_path, storage="insecure").get_secret("hf_token") == HF

        vault_key.set_passphrase_prompt(lambda new: "moving to a passphrase")
        assert sm.change_storage("passphrase")
        assert not (tmp_path / "state" / "secrets.store.insecure-key").exists()
        assert vault(tmp_path, storage="passphrase").get_secret("hf_token") == HF
        with pytest.raises(vault_key.StorageError):
            sm.change_storage("ephemeral")


# ======================================================================================================
# Records, names and migration
# ======================================================================================================

class TestRecordsAndMigration:

    def test_old_store_and_names_are_read_and_moved_on_the_next_write(self, memory_keyring, tmp_path):
        legacy_key = Fernet.generate_key()
        memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] = legacy_key.decode()  # bare key of v1 stores
        fernet = Fernet(legacy_key)
        store = tmp_path / "state" / "secrets.store"
        store.parent.mkdir(parents=True)
        store.write_text(json.dumps({"anthropic_api_key": fernet.encrypt(KEY.encode()).decode(),
                                     "hf_token": fernet.encrypt(HF.encode()).decode()}))
        before = store.read_bytes()

        sm = vault(tmp_path)

        assert sm.get_secret("anthropic_api_key") == KEY
        assert sm.get_secret("cred/anthropic/api_key") == KEY
        assert sm.get_record("anthropic_api_key") == {"v": 1, "kind": "api_key", "secret": KEY}
        assert sm.list_secrets() == ["cred/anthropic/api_key", "hf_token"]
        assert store.read_bytes() == before, "reading changes nothing"

        assert sm.set_secret("github_token", "gh-placeholder-token")

        data = store_json(sm)
        assert data["version"] == 2 and set(data["entries"]) == {"cred/anthropic/api_key", "hf_token",
                                                                 "github_token"}
        record = json.loads(fernet.decrypt(data["entries"]["cred/anthropic/api_key"]["ct"].encode()))
        assert record == {"v": 1, "kind": "api_key", "secret": KEY}
        assert memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] == legacy_key.decode(), "key kept (kid v1)"
        fresh = vault(tmp_path)
        assert fresh.get_secret("anthropic_api_key") == KEY and fresh.get_secret("hf_token") == HF

    def test_provider_keys_are_stored_as_records_under_their_new_names(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("gemini_api_key", "AIza" + "PlaceholderKeyForTests0123456789abc")
        assert sm.list_secrets() == ["cred/google/api_key"]
        assert sm.get_record("cred/google/api_key")["kind"] == "api_key"
        assert sm.delete_secret("gemini_api_key") and sm.list_secrets() == []
        assert sm.get_record("hf_token") is None, "only credentials have records"

    def test_credential_names_and_origins(self):
        assert credential_name("anthropic", "api_key") == "cred/anthropic/api_key"
        bound = credential_name("lmstudio", "key", origin="http://127.0.0.1:1234/v1")
        assert bound.startswith("cred/lmstudio/key@") and len(bound.split("@")[1]) == 16
        assert origin_hash("HTTP://LocalHost/v1") == origin_hash("http://localhost:80")
        assert origin_hash("https://localhost") != origin_hash("http://localhost")
        for bad in (("Anthropic", "api_key"), ("a/b", "api_key"), ("x", "")):
            with pytest.raises(ValueError):
                credential_name(*bad)
        with pytest.raises(ValueError):
            origin_hash("file:///etc/passwd")

    @pytest.mark.parametrize("name, record", [
        ("hf_token", {"kind": "api_key", "secret": "x"}),
        ("cred/openai/api_key", {"kind": "password", "secret": "x"}),
        ("cred/openai/api_key", {"kind": "api_key", "secret": float("nan")}),
        ("cred/openai/api_key", {"kind": "api_key", "secret": object()}),
        ("cred/openai/api_key", {"kind": "api_key", "secret": "x" * 70_000}),
    ], ids=["not-cred", "kind", "nan", "object", "too-large"])
    def test_invalid_records_are_refused(self, memory_keyring, tmp_path, name, record):
        sm = vault(tmp_path)
        assert sm.set_record(name, record) is False
        assert sm.list_secrets() == []

    def test_reads_are_audited_once_per_process(self, memory_keyring, tmp_path):
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        for _ in range(3):
            assert sm.get_secret("hf_token") == HF
        assert vault(tmp_path).get_secret("hf_token") == HF
        log = sm.audit_log_file.read_text()
        assert log.count("ACTION=READ KEY=hf_token") == 1
        assert log.count("ACTION=WRITE KEY=hf_token") == 1
        assert HF not in log


# ======================================================================================================
# Review findings (adversarial pass)
# ======================================================================================================

def lock_is_free(sm) -> bool:
    """True when another process could take the vault lock right now."""
    from orchestrator.utils.file_lock import LockTimeout
    try:
        with file_lock(sm.secrets_file.with_name("secrets.store.lock"), timeout=0.2):
            return True
    except LockTimeout:
        return False


class TestReviewFindings:

    @pytest.mark.parametrize("action", ["rotate", "change_storage"])
    def test_rotating_one_vault_never_removes_the_key_of_another(self, memory_keyring, tmp_path, action):
        """Two LLM_FRAMEWORK_HOME folders of one user: rotating (or moving) one used to delete the keyring
        entry both shared, so the other vault could never be read again."""
        profile_a, profile_b = vault(tmp_path / "a"), vault(tmp_path / "b")
        assert profile_a.set_secret("hf_token", "token-of-a") and profile_b.set_secret("hf_token", "token-of-b")
        assert key_slot(profile_a) != key_slot(profile_b)

        assert profile_a.rotate_key() if action == "rotate" else profile_a.change_storage("insecure")

        other = vault(tmp_path / "b")
        assert not other._load_failed, other.load_error
        assert other.get_secret("hf_token") == "token-of-b"

    def test_a_store_of_an_earlier_version_moves_off_the_shared_entry_at_rotation(self, memory_keyring, tmp_path):
        legacy_key = Fernet.generate_key()
        memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] = legacy_key.decode()
        for folder in ("state", "other-home"):  # e.g. two LLM_FRAMEWORK_HOME folders of version 3.0
            (tmp_path / folder).mkdir()
            (tmp_path / folder / "secrets.store").write_text(json.dumps(
                {"hf_token": Fernet(legacy_key).encrypt(f"{folder}-token".encode()).decode()}))

        sm = vault(tmp_path)
        assert sm.get_secret("hf_token") == "state-token"
        assert sm.rotate_key()

        assert "vault_id" in store_json(sm) and key_slot(sm)[0] != SERVICE
        assert memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] == legacy_key.decode(), \
            "the shared key stays for the other stores"
        assert vault(tmp_path).get_secret("hf_token") == "state-token"
        other = SecretsManager(tmp_path / "other-home")
        assert other.initialize() and other.get_secret("hf_token") == "other-home-token"

    def test_a_passphrase_prompt_never_holds_the_vault_lock(self, memory_keyring, tmp_path):
        """A passphrase prompt left open (or a slow helper) must not make the writes of the GUI or the MCP
        server time out: a lost write of a rotated refresh token signs the user out."""
        passphrase = "a long enough passphrase"
        vault_key.set_passphrase_prompt(lambda new: passphrase)
        assert vault(tmp_path, storage="passphrase").set_secret("hf_token", HF)
        vault_key.clear_derived_keys()

        free_while_asked = []

        def prompt(new):
            free_while_asked.append((new, lock_is_free(sm)))
            return passphrase

        vault_key.set_passphrase_prompt(prompt)
        sm = SecretsManager(tmp_path / "state", storage="passphrase")
        assert sm.initialize()  # opening an existing vault
        assert sm.rotate_key()  # the new passphrase of a rotation
        vault_key.clear_derived_keys()  # as in another process after this rotation
        other = vault(tmp_path, storage="passphrase")
        assert sm.rotate_key()
        assert other.set_secret("github_token", "gh-placeholder-token")  # a write after a rotation elsewhere

        assert len(free_while_asked) >= 4
        assert all(free for _, free in free_while_asked), free_while_asked
        assert vault(tmp_path, storage="passphrase").get_secret("github_token") == "gh-placeholder-token"

    def test_secrets_work_without_a_user_name(self, memory_keyring, tmp_path, monkeypatch):
        """A container with an arbitrary uid has no USER variable and no passwd entry: getpass.getuser()
        raises, and that broke every read and write (the audit log was written outside its try)."""
        import getpass

        def no_user():
            raise OSError("No username set in the environment")

        monkeypatch.setattr(getpass, "getuser", no_user)
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.get_secret("hf_token") == HF
        assert "ACTION=WRITE KEY=hf_token" in sm.audit_log_file.read_text()

    def test_names_and_user_names_never_forge_audit_lines(self, memory_keyring, tmp_path, monkeypatch):
        forged = "hf_token\n[2026-01-01T00:00:00] USER=root ACTION=DELETE KEY=github_token STATUS=SUCCESS"
        monkeypatch.setenv("USER", "alice\nUSER=root")
        monkeypatch.setenv("LOGNAME", "alice\nUSER=root")
        sm = vault(tmp_path)
        assert sm.set_secret(forged, "x") is False, "a name with a line break is refused"
        assert sm.set_secret("hf_token", HF)

        lines = sm.audit_log_file.read_text().splitlines()
        assert len(lines) == 1 and "USER=alice\\nUSER=root" in lines[0]
        assert not any(line.startswith("USER=") or "ACTION=DELETE" in line for line in lines)

    @pytest.mark.parametrize("url", ["http://evil.example\\@127.0.0.1:1234/v1", "http://user:" + "pw-123" + "@127.0.0.1:1234",
                                     "http://127.0.0.1:1234 /v1", "http://127.0.0.1:1234\n/v1", "http://[::1"])
    def test_origins_that_parsers_disagree_on_are_refused(self, url):
        """urllib reads http://evil\\@127.0.0.1 as 127.0.0.1; a client may send it to evil. The key of the
        local engine must never be looked up for it. The error never repeats the URL (it may hold a password)."""
        with pytest.raises(ValueError) as error:
            origin_hash(url)
        assert "pw-123" not in str(error.value) and "evil" not in str(error.value)

    def test_the_same_origin_written_differently_has_one_hash(self):
        assert origin_hash("http://bücher.example") == origin_hash("http://xn--bcher-kva.example:80")
        assert origin_hash("https://Example.COM./v1") == origin_hash("https://example.com:443")

    def test_a_keyring_error_during_rotation_returns_false_and_keeps_the_vault(self, use_backend, tmp_path):
        failing = {"on": False}

        def set_password(self, service, username, password):
            if failing["on"] and username == vault_key.KEY_ENTRY:
                raise RuntimeError("the keyring refused the write")
            self.store[(service, username)] = password

        use_backend(backend_class(set_=set_password)())
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        before = sm.secrets_file.read_bytes()
        failing["on"] = True

        assert sm.rotate_key() is False

        failing["on"] = False
        assert sm.secrets_file.read_bytes() == before
        assert vault(tmp_path).get_secret("hf_token") == HF

    def test_replacing_the_vault_is_retried_while_windows_holds_it_open(self, memory_keyring, tmp_path,
                                                                          monkeypatch):
        """On Windows os.replace fails while a reader (another process, a virus scanner) has the file open."""
        sm = vault(tmp_path)
        assert sm.set_secret("hf_token", HF)
        real_replace, busy = os.replace, {"left": 2}

        def sharing_violation(src, dst):
            if busy["left"]:
                busy["left"] -= 1
                raise PermissionError(13, "The process cannot access the file")
            return real_replace(src, dst)

        monkeypatch.setattr(vault_key, "REPLACE_RETRIES", 3)
        monkeypatch.setattr(os, "replace", sharing_violation)
        assert sm.set_secret("github_token", "gh-placeholder-token")
        busy["left"] = 10
        before = sm.secrets_file.read_bytes()
        assert sm.set_secret("github_token", "another-placeholder") is False
        assert sm.secrets_file.read_bytes() == before
        assert list(sm.secrets_file.parent.glob(".*.tmp")) == []

    def test_a_hung_keyring_backend_lookup_times_out(self, tmp_path, monkeypatch):
        """Choosing the backend asks D-Bus (Secret Service) on Linux, which can hang outside the probe."""
        import keyring
        release = threading.Event()
        monkeypatch.setattr(keyring, "get_keyring", lambda: release.wait(30))
        try:
            start = time.monotonic()
            sm = SecretsManager(tmp_path / "state")
            assert sm.initialize() is False
            assert time.monotonic() - start <= vault_key.PROBE_TIMEOUT + 1.0
            assert sm.storage_error.code == "storage.keyring_unavailable"
            start = time.monotonic()
            assert vault_key.keyring_usable() is False and time.monotonic() - start < 1.0, "cached"
        finally:
            release.set()

    def test_the_audit_key_stays_on_the_local_machine(self, use_backend):
        """The keyring probe sets persist on the backend that _audit_key then writes with."""
        from orchestrator.tools import framework_tools
        seen = []

        def record_set(self, service, username, password):
            seen.append((username, getattr(self, "persist", None)))
            self.store[(service, username)] = password

        cls = backend_class(set_=record_set, name="WinVaultKeyring", module="keyring.backends.Windows")
        cls.persist = "enterprise"
        use_backend(cls())
        assert framework_tools._audit_key(SimpleNamespace(secrets_manager=None))
        assert (framework_tools.AUDIT_KEY_ID, "local machine") in seen, seen

    def test_a_decomposed_passphrase_opens_the_vault(self, memory_keyring, tmp_path):
        import unicodedata
        typed = "Grüße aus Köln, Passphrase"
        vault_key.set_passphrase_prompt(lambda new: unicodedata.normalize("NFC", typed))
        assert vault(tmp_path, storage="passphrase").set_secret("hf_token", HF)
        vault_key.clear_derived_keys()
        vault_key.set_passphrase_prompt(lambda new: unicodedata.normalize("NFD", typed))
        assert vault(tmp_path, storage="passphrase").get_secret("hf_token") == HF

    def test_old_stores_are_re_encrypted_with_the_vault_key(self, memory_keyring, tmp_path):
        legacy_key = Fernet.generate_key()
        memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] = legacy_key.decode()
        old = tmp_path / "configs" / "secrets.store"
        old.parent.mkdir()
        old.write_text(json.dumps({"openai_api_key": Fernet(legacy_key).encrypt(KEY.encode()).decode()}))
        config = SimpleNamespace(user_state_dir=tmp_path / "state", config_dir=tmp_path / "configs")

        sm = SecretsManager(SimpleNamespace(config=config))
        assert sm.initialize()

        data = store_json(sm)
        assert set(data["entries"]) == {"cred/openai/api_key"}
        assert {e["kid"] for e in data["entries"].values()} == {data["kid"]} != {"v1"}
        assert sm.rotate_key() and SecretsManager(SimpleNamespace(config=config)).get_secret("openai_api_key") == KEY

    def test_an_old_store_that_cannot_be_decrypted_is_reported_not_merged(self, memory_keyring, tmp_path):
        old = tmp_path / "configs" / "secrets.store"
        old.parent.mkdir()
        old.write_text(json.dumps({"hf_token": Fernet(Fernet.generate_key()).encrypt(HF.encode()).decode()}))
        config = SimpleNamespace(user_state_dir=tmp_path / "state", config_dir=tmp_path / "configs")

        sm = SecretsManager(SimpleNamespace(config=config))
        assert sm.initialize()

        assert sm.failed_file == old and "cannot be decrypted" in sm.load_error
        assert sm.set_secret("github_token", "gh-placeholder-token") is False
        assert not memory_keyring.store and not sm.secrets_file.exists(), "no key left behind for an unwritten vault"
        finding = check_secrets(SimpleNamespace(secrets_manager=sm))[0]
        assert finding.severity == Severity.ERROR and str(old) in finding.message

    def test_no_old_store_relative_to_the_working_directory(self, memory_keyring, tmp_path):
        """Without a known config folder there is no old location; config/secrets.store in the working
        directory (a foreign checkout) is never merged into the vault."""
        legacy_key = Fernet.generate_key()
        memory_keyring.store[(SERVICE, vault_key.KEY_ENTRY)] = legacy_key.decode()
        stray = tmp_path / "config" / "secrets.store"  # the tests run in tmp_path
        stray.parent.mkdir()
        stray.write_text(json.dumps({"openai_api_key": Fernet(legacy_key).encrypt(KEY.encode()).decode()}))

        sm = SecretsManager(SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path / "state")))
        assert sm.initialize()
        assert sm.get_secret("openai_api_key") is None and sm.list_secrets() == []

    def test_a_storage_change_that_cannot_be_saved_says_what_to_set(self, memory_keyring, tmp_path, caplog):
        config = SimpleNamespace(user_state_dir=tmp_path / "state", config_dir=tmp_path / "configs",
                                 set=lambda k, v: True, save_user_config=lambda: False)
        sm = SecretsManager(SimpleNamespace(config=config))
        assert sm.initialize() and sm.set_secret("hf_token", HF)
        assert sm.change_storage("insecure")
        assert "secret_storage: insecure" in caplog.text

    def test_the_gui_shows_why_the_start_failed(self, qapp, monkeypatch):
        from orchestrator.gui import main_window
        shown = []

        class FailingFramework:
            init_error = vault_key.keyring_unavailable("it did not answer within 3 s")

            def initialize(self):
                return False

        monkeypatch.setattr(main_window, "FrameworkManager", FailingFramework)
        monkeypatch.setattr(main_window.QMessageBox, "critical", lambda parent, title, text: shown.append(text))
        with pytest.raises(SystemExit):
            main_window.MainOrchestrator(REPO_ROOT)
        assert shown and "secret_storage" in shown[0] and "passphrase" in shown[0]

    def test_the_cli_prints_paths_in_the_storage_error_literally(self, tmp_path):
        repo = tmp_path / "[bold]project"
        (repo / ".git").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "FRAMEWORK_"))}
        env.update(PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                   LLM_FRAMEWORK_HOME=str(repo / "state"), LLM_FRAMEWORK_CONFIG_DIR=str(tmp_path / "cfg"))
        result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "secrets", "list"], cwd=tmp_path,
                                env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 1 and "Traceback" not in result.stderr
        shown = result.stderr.replace("\n", "").split("Failed to initialize framework:", 1)[1]
        assert "[bold]project" in shown, "rich markup ate part of the path"
