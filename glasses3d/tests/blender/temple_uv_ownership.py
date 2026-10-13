"""Current meshes and retained undo/UV meshes cannot cross object ownership domains."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from glasses3d.g3d import temple_bpy as T
from glasses3d.tests.blender.temple_uv import pair, design
from glasses3d.tests.blender.temple_uv_generations import state, record, equal_uv


def exercise_alias(owner, other, mesh, spec):
    alias = bpy.data.objects.new('External live user of retained temple mesh', mesh)
    bpy.context.scene.collection.objects.link(alias)
    alias.matrix_world = owner.matrix_world
    alias['partRole'] = 'temple'
    bpy.context.view_layer.update()
    before = state()
    checks = {}
    for label, target in (('current_as_other_backup', alias), ('retained_as_other_live', owner)):
        result = T.faceted_temple(spec, targets={'+x': target.name, '-x': other.name}, decals=None)
        assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
        assert result['scene_unchanged'] and state() == before, label
        checks[label] = True
    result = T.unfaceted_temple(owner)
    assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
    assert state() == before
    checks['unfacet_refuses_shared_source'] = True
    bpy.data.objects.remove(alias, do_unlink=True)
    return checks


def run():
    objects, _ = pair()
    owner, other = objects
    originals = [(ob.data, T.mesh_digest(ob.data), record(ob.data)) for ob in objects]
    original_meshes = {me.name for me in bpy.data.meshes}
    spec = design()
    targets = {'+x': owner.name, '-x': other.name}
    result = T.faceted_temple(spec, targets=targets, decals=None)
    assert result['status'] == 'ok', result
    backup = json.loads(owner[T.PROP_BACKUP])
    checks = {'original': exercise_alias(owner, other, bpy.data.meshes[backup['mesh']], spec)}

    layer = owner.data.uv_layers['SurfaceUV']
    values = np.array([item.uv[:] for item in layer.data])
    layer.data.foreach_set('uv', np.asarray(values + [.125, -.25], np.float32).ravel())
    result = T.faceted_temple(spec, targets=targets, decals=None)
    assert result['status'] == 'ok', result
    backup = json.loads(owner[T.PROP_BACKUP])
    assert backup['uv_mesh'] != backup['mesh']
    checks['uv_generation'] = exercise_alias(owner, other, bpy.data.meshes[backup['uv_mesh']], spec)

    for ob, (mesh, digest, uv) in zip(objects, originals):
        result = T.unfaceted_temple(ob)
        assert result['status'] == 'ok', result
        assert ob.data == mesh and T.mesh_digest(ob.data) == digest
        equal_uv(record(ob.data), uv)
    assert {me.name for me in bpy.data.meshes} == original_meshes
    checks['original_unfacet_exact'] = True
    checks['retained_sources_cleaned'] = True
    return {'status': 'ok', 'checks': checks}


if __name__ == '__main__':
    result = run()
    destination = Path(sys.argv[sys.argv.index('--') + 1])
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
