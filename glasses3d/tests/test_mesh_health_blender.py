import json
from pathlib import Path
import pytest
from glasses3d.g3d_host import blender
from glasses3d.tests.test_owner_health import box


@pytest.mark.skipif(blender.blender_exe() is None,reason='Blender is unavailable')
def test_native_mesh_health_facade_dry_run_exact_apply_and_refusal_rollback(tmp_path):
    fixture=tmp_path/'fixture.json';receipt=tmp_path/'receipt.json';fixture.write_text(json.dumps(box()[0]))
    script=Path(__file__).parent/'proofs'/'mesh_health_bpy_run.py'
    proc=blender.run(None,script,[str(fixture),str(receipt)],timeout_s=120)
    assert proc.returncode==0 and receipt.exists(),proc.stdout+'\n'+proc.stderr
    report=json.loads(receipt.read_text())
    assert not report['facade']['missing'] and not report['facade']['errors']
    for kind in ['mesh_cleanup','consistent_normals','controlled_decimate']:
        assert report[kind]['dry']['status']=='ok',report[kind]
        assert report[kind]['actual']['status']=='ok',report[kind]
        assert report[kind]['actual']['checks']['ok']
    assert report['controlled_decimate']['vertices']==8
    assert report['mesh_cleanup']['vertices']==9
    assert report['symmetrize']['status']=='ok',report['symmetrize']
    assert report['refusal']['status']=='refused'
