"""Group interface edits affect export-cache keys and remain stable across checkpoints."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    if blender.blender_exe() is None:
        pytest.skip("Blender 5.2 not installed")
    out = tmp_path_factory.mktemp("checkpoint-interfaces")
    result = blender.run(None, Path(__file__).parent / "blender/checkpoint_node_interfaces.py", [out], timeout_s=120)
    report = out / "report.json"
    assert report.is_file(), result.stdout + result.stderr
    return json.loads(report.read_text(encoding="utf-8"))


def test_socket_rename_changes_optics_and_fingerprint(proof):
    assert proof["correct_export"]["iridescence"]["iridescenceFactor"] == 1
    assert proof["renamed_export"]["iridescence"] is None
    assert proof["changes"]["used_socket_name"]
    assert proof["changes"]["restored_socket_name"]
    assert proof["changes"]["socket_name_after_restore"]


def test_unused_group_interface_definitions_are_real_content(proof):
    assert all(proof["changes"][name] for name in ("zero_user_socket_name", "zero_user_socket_default",
                                                 "zero_user_socket_type", "fake_user_socket_default",
                                                 "panel_name", "interface_parent"))


def test_interface_fingerprint_and_coating_survive_checkpoint_roundtrip(proof):
    assert proof["roundtrip"]["matches"] is True
    assert proof["restored_export"]["iridescence"] == proof["correct_export"]["iridescence"]
    assert all(proof[name]["scene_unchanged"] for name in ("correct_export", "renamed_export", "restored_export"))


def test_material_trees_without_interfaces_keep_legacy_hash_input(proof):
    assert proof["legacy_plain_tree_unchanged"] is True
