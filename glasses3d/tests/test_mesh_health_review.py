import json
from pathlib import Path

import pytest
from glasses3d.g3d_host import blender
from glasses3d.tests.test_owner_markings import fixture


def test_main_mesh_health_restores_surface_and_marking_after_post_write_failure(tmp_path):
    if blender.blender_exe() is None:pytest.skip('Blender unavailable')
    source,_=fixture()
    # Build a symmetric surface with a small negative-side asymmetry and a
    # supported mark on the moving side. Both copied meshes must be restored.
    obj,mark=source['objects']
    for key in ('vertices_local','vertices_world_mm'):
        # The fixture's coordinates share lists; replace to avoid double edits.
        obj[key]=[[p[0]-3.5,p[1],p[2]] for p in obj[key]]
        mark[key]=[[-(p[0]-3.5),p[1],p[2]] for p in mark[key]]
        for p in obj[key]:
            if p[0]<0:p[0]=-1.48
    mark['polygons']=[p[::-1] for p in mark['polygons']]
    mark['corner_normals']=[[-n[0],n[1],n[2]] for n in mark['corner_normals']]
    obj['role']='frame';mark['role']='frame'
    f=tmp_path/'fixture.json';r=tmp_path/'receipt.json';f.write_text(json.dumps(source))
    result=blender.run(None,Path(__file__).parent/'blender/mesh_health_review.py',[str(f),str(r)],timeout_s=120)
    assert result.returncode==0 and r.exists(),result.stdout+result.stderr
    assert json.loads(r.read_text())=={'ok':True,'copies_restored':2}
