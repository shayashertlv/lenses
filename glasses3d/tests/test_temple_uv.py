"""Per-loop UV transfer through the real Blender builder, including source retention and seam tessellation."""
import json
import hashlib
import shutil
from pathlib import Path
import pytest
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.spec import validate_spec

pytestmark = pytest.mark.timing  # fixed Blender deadlines fail under concurrent render/fit load


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
def test_original_uv_charts_survive_faceting_revisions_and_variants(tmp_path):
    script = Path(__file__).parent / 'blender/temple_uv.py'
    receipt = tmp_path / 'uv.json'
    proc = blender.run(None, script, [receipt], timeout_s=120)
    assert receipt.is_file(), proc.stdout[-4000:] + proc.stderr[-4000:]
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    assert json.loads(receipt.read_text())['status'] == 'ok'


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
def test_real_ft1119_uvs_survive_template_faceting_and_export(tmp_path):
    from glasses3d.tests.proofs.wp4_proof import SOURCES
    source = SOURCES['ft']
    if not source.exists():
        pytest.skip('FT1119 source scene is unavailable')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    copy = tmp_path / 'copy.blend'
    shutil.copy2(source, copy)
    receipt = tmp_path / 'uv.json'
    script = Path(__file__).parent / 'blender/temple_uv_real.py'
    proc = blender.run(copy, script, [receipt], timeout_s=120)
    assert receipt.is_file(), proc.stdout[-4000:] + proc.stderr[-4000:]
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    assert json.loads(receipt.read_text())['status'] == 'ok'
    report = validate_spec(tmp_path / 'faceted.glb')
    assert report['ok'], report
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
