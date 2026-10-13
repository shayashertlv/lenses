"""Refuse cross-owner current/retained mesh aliases before mutation or name-based undo."""
import json
from pathlib import Path
import pytest
from glasses3d.g3d_host import blender


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
def test_current_and_retained_temple_mesh_ownership_is_symmetric(tmp_path):
    script = Path(__file__).parent / 'blender/temple_uv_ownership.py'
    receipt = tmp_path / 'ownership.json'
    proc = blender.run(None, script, [receipt], timeout_s=120)
    assert receipt.is_file(), proc.stdout[-6000:] + proc.stderr[-6000:]
    data = json.loads(receipt.read_text())
    assert proc.returncode == 0 and data['status'] == 'ok'
    for source in ('original', 'uv_generation'):
        assert data['checks'][source] == {key: True for key in (
            'current_as_other_backup', 'retained_as_other_live', 'unfacet_refuses_shared_source')}
    assert data['checks']['original_unfacet_exact']
    assert data['checks']['retained_sources_cleaned']
