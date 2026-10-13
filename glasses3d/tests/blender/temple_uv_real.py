"""Carry FT1119's real SurfaceUV through the general template/builder on a disposable scene copy."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from glasses3d.g3d import temple_bpy as T
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d.temple import template as TT
from glasses3d.tests.blender.temple_uv import closed


def run(output):
    origin = T.asset_origin()
    objects = {side: choices[0][1] for side, choices in T.temple_candidates(bpy.context.scene, origin).items()}
    originals = {side: (ob.data, T.mesh_digest(ob.data)) for side, ob in objects.items()}
    ranges = {}
    V, F, _ = T.mesh_arrays(objects[1], origin)
    spec, _ = TT.template_from_mesh(V, F, 1, float(V[:, 2].max()), None, samples=1000)
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'ok', result
    for side, ob in objects.items():
        closed(ob.data)
        assert [layer.name for layer in ob.data.uv_layers] == ['SurfaceUV']
        uv = np.array([loop.uv[:] for loop in ob.data.uv_layers[0].data])
        assert np.isfinite(uv).all()
        original_uv = np.array([loop.uv[:] for loop in originals[side][0].uv_layers[0].data])
        assert np.all(uv.min(axis=0) >= original_uv.min(axis=0) - 1e-5)
        assert np.all(uv.max(axis=0) <= original_uv.max(axis=0) + 1e-5)
        assert len(np.unique(np.round(uv, 5), axis=0)) > 100
        ranges[str(side)] = {'original': [original_uv.min(axis=0).tolist(), original_uv.max(axis=0).tolist()],
                             'written': [uv.min(axis=0).tolist(), uv.max(axis=0).tolist()]}
        assert sum(edge.use_seam for edge in ob.data.edges) > 0
    export = export_glb(output / 'faceted.glb')
    assert export['status'] == 'exported', export
    for side, ob in objects.items():
        assert T.unfaceted_temple(ob)['status'] == 'ok'
        original, digest = originals[side]
        assert ob.data == original and T.mesh_digest(original) == digest
    return {'status': 'ok', 'build': result, 'export': export, 'uv_ranges': ranges, 'unfacet_exact': True}


if __name__ == '__main__':
    out = Path(sys.argv[-1])
    out.write_text(json.dumps(run(out.parent), indent=2), encoding='utf-8')
