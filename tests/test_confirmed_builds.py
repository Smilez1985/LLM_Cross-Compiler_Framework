"""The builds the person confirmed (`confirmed_builds.json`; delegation design 3.9, SEC-M14, WP1).

The file opens build standing approvals for packs an AI installed, so it is signed like the permission file: a hand
edit, a missing signature or another key leaves no confirmation that counts, and the person is asked again. A module an
AI created is confirmed for its content only (`definition_sha256`), a model fetched from another computer for its
sha256 (kind `artifact`).
"""

import json
from types import SimpleNamespace

import pytest

from orchestrator.Core.confirmed_builds import FILE_NAME, ConfirmedBuilds

KEY = b"audit-key"
VIA = "grant:g_" + "1" * 16
PACK = "a" * 64


def confirmed_target(store):
    return store.confirmed("target", "demo", installed_via=VIA, pack_sha256=PACK)


@pytest.mark.parametrize("key", [KEY, None])
def test_tampered_file_is_refused(tmp_path, key):
    store = ConfirmedBuilds(tmp_path, key=key)
    assert store.record("target", "Demo", installed_via=VIA, pack_sha256=PACK, channel="framework-gui")
    assert confirmed_target(store) and store.problem is None
    data = json.loads((tmp_path / FILE_NAME).read_text())
    assert data["signature"]["key_id"] != "", "the file is signed"

    # A hand edit that adds a confirmation for another pack: nothing in the file counts any more.
    data["confirmed"]["target:other"] = dict(data["confirmed"]["target:demo"], pack_sha256="b" * 64)
    (tmp_path / FILE_NAME).write_text(json.dumps(data))
    assert not confirmed_target(store) and not store.confirmed("target", "other", installed_via=VIA,
                                                               pack_sha256="b" * 64)
    assert "holds no confirmation" in store.problem

    # An unsigned file (written by hand, or by a version before signing) counts as nothing either.
    del data["signature"]
    del data["confirmed"]["target:other"]
    (tmp_path / FILE_NAME).write_text(json.dumps(data))
    assert not confirmed_target(store)

    # A new confirmation by the person replaces the file with a signed one.
    assert store.record("target", "Demo", installed_via=VIA, pack_sha256=PACK, channel="framework-cli")
    assert confirmed_target(store) and store.problem is None


def test_a_file_signed_with_another_key_or_unkeyed_counts_for_no_keyed_reader(tmp_path):
    ConfirmedBuilds(tmp_path, key=b"other-key").record("target", "Demo", installed_via=VIA, pack_sha256=PACK,
                                                        channel="framework-gui")
    assert not confirmed_target(ConfirmedBuilds(tmp_path, key=KEY))
    ConfirmedBuilds(tmp_path).record("target", "Demo", installed_via=VIA, pack_sha256=PACK, channel="framework-gui")
    assert not confirmed_target(ConfirmedBuilds(tmp_path, key=KEY)), "a keyed reader never accepts an unkeyed file"
    assert not confirmed_target(ConfirmedBuilds(tmp_path, refuse_unkeyed=lambda: True))
    assert confirmed_target(ConfirmedBuilds(tmp_path))


def test_the_framework_store_uses_the_permission_files_key(tmp_path):
    policy_store = SimpleNamespace(key=KEY, refuse_unkeyed=None)
    framework = SimpleNamespace(tool_registry=SimpleNamespace(policy_store=policy_store))
    store = ConfirmedBuilds.for_framework(framework, tmp_path)
    assert store.key == KEY
    store.record("target", "Demo", installed_via=VIA, pack_sha256=PACK, channel="framework-gui")
    assert confirmed_target(ConfirmedBuilds(tmp_path, key=KEY))
    assert ConfirmedBuilds.for_framework(SimpleNamespace(), tmp_path).key is None


def test_recreated_module_needs_new_confirmation(tmp_path):
    """A module an AI created is confirmed for exactly its content: recreated under the same name (another
    definition) it needs the person again (SEC-M14)."""
    store = ConfirmedBuilds(tmp_path, key=KEY)
    first, second = "c" * 64, "d" * 64
    assert not store.record("module", "my-board", channel="framework-gui"), "never without its content hash"
    assert store.record("module", "my-board", definition_sha256=first, channel="framework-gui")
    assert store.confirmed("module", "my-board", definition_sha256=first)
    assert not store.confirmed("module", "my-board", definition_sha256=second)
    assert not store.confirmed("module", "my-board"), "a module is never confirmed without its content"
    assert not store.record("module", "my-board", definition_sha256=second, channel="grant:g_" + "2" * 16)
    assert not store.confirmed("module", "my-board", definition_sha256=second)


def test_the_removed_remote_modes_artifact_kind_is_never_written_or_matched(tmp_path):
    """A model fetched from another computer (kind `artifact`) belonged to the removed remote mode: nothing records
    it any more, and an entry an older version wrote stays in the file but never counts as a confirmation."""
    from orchestrator.Core import confirmed_builds
    store = ConfirmedBuilds(tmp_path, key=KEY)
    assert "artifact" not in confirmed_builds.KINDS
    assert not store.record("artifact", "a_0123456789abcdef", channel="framework-gui")
    store._save({confirmed_builds._key("artifact", "a_0123456789abcdef"): {
        "installed_via": "", "pack_sha256": "", "definition_sha256": "", "sha256": "e" * 64, "base_images": [],
        "channel": "approval:ap_0123456789abcdef", "confirmed": "2026-10-12T00:00:00+00:00"}})
    assert store.get("artifact", "a_0123456789abcdef") is not None, "the old entry stays and still verifies"
    assert not store.confirmed("artifact", "a_0123456789abcdef")
    assert store.record("target", "Rockchip", channel="framework-gui"), "the file stays usable"
    assert store.confirmed("target", "Rockchip")
