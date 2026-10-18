"""Writer fixes exercised in real Blender on synthetic geometry, without product fixtures."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender
from glasses3d.g3d_host.spec import validate_spec


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
@pytest.mark.parametrize('case', ['variant_modifiers', 'nearby_markings', 'edge_decals', 'shared_undo',
                                   'uv_decal_opening', 'float32_ears', 'normal_pass', 'exported_surfaces'])
def test_writer_contract(case, tmp_path):
    script = Path(__file__).parent / 'blender/temple_writer_contract.py'
    receipt = tmp_path / (case + '.json')
    proc = blender.run(None, script, [case, receipt], timeout_s=120)
    assert proc.returncode == 0 and receipt.is_file(), proc.stdout[-5000:] + proc.stderr[-5000:]
    result = json.loads(receipt.read_text(encoding='utf-8'))
    assert result['status'] == 'ok'
    for path in result['checks'].get('exported', []):
        # Use the actual delivery validator, including its float32-coordinate altitude rule.
        report = validate_spec(path)
        assert report['measurements']['triangles'] > 0, report
        assert not any('degenerate' in warning for warning in report['warnings']), report['warnings']
