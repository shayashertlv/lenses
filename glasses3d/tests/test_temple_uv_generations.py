"""Edits made between facet revisions must survive the next immutable UV generation."""
import json
from pathlib import Path
import pytest
from glasses3d.g3d_host import blender


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
def test_current_uv_edits_become_a_frozen_generation_and_unfacet_restores_original(tmp_path):
    script = Path(__file__).parent / 'blender/temple_uv_generations.py'
    receipt = tmp_path / 'generations.json'
    proc = blender.run(None, script, [receipt], timeout_s=180)
    assert receipt.is_file(), proc.stdout[-6000:] + proc.stderr[-6000:]
    data = json.loads(receipt.read_text())
    assert proc.returncode == 0 and data['status'] == 'ok' and data['sides'] == 2
    assert data['checks'] == {key: True for key in (
        'added_print_layer', 'edited_existing_layer', 'deleted_one_layer', 'active_flags',
        'deleted_all_layers', 'recreated_after_deletion', 'external_transform', 'external_geometry',
        'external_scale_and_z_translation', 'known_uv_after_transform',
        'original_unfacet_exact', 'generations_cleaned', 'dry_run_and_variants_unchanged',
        'new_generation_aba_exact', 'failed_write_sources_cleaned',
        'unsupported_uv_frame_refused_without_mutation', 'empty_uv_generation_has_no_frame_restriction',
        'linked_mesh_and_duplicate_backup_refused_without_mutation', 'mesh_only_part_role_restored')}
