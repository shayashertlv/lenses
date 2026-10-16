"""Synthetic exact-normal-encoding witness and hostile-byte regressions."""
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import export_glb, owner_normals_bpy as normals
from glasses3d.g3d.lens_surface import quadratic_lens


def main():
    from types import SimpleNamespace
    class NonfiniteNormals(list):
        def foreach_get(self,name,values):values[:]=np.nan
    broken=SimpleNamespace(loops=[0],edges=[],
        corner_normals=NonfiniteNormals([SimpleNamespace(vector=[np.nan,0.,1.])]),
        normals_split_custom_set=lambda values:None,update=lambda:None)
    assert normals._error(broken,np.asarray([[0.,0.,1.]]))==float('inf')
    try:normals._encode(broken,np.asarray([[0.,0.,1.]]))
    except ValueError as exc:assert 'nonfinite' in str(exc)
    else:raise AssertionError('nonfinite decoder output reached integer-angle search')
    # A nondegenerate triangle near an integer-angle basis boundary plus a
    # normally representable control. The first native inverse setter misses the
    # fixed bound although nearby angle pairs represent the same direction well.
    for points,direction,needs_correction in (
        ([[68.64566802978516,.8194597363471985,-107.46917724609375],
          [68.53026580810547,.5461019277572632,-108.],
          [68.58055877685547,.5509055852890015,-108.]],
         [.04558548331260681,-.9627721309661865,.26644277572631836],True),
        ([[71.89436340332031,-1.1066957712173462,-8.227239608764648],
          [71.7833480834961,.19237269461154938,-8.660491943359375],
          [71.98088836669922,.22365958988666534,-8.240310668945312]],
         [.973990797996521,-.06572064012289047,-.2168472558259964],False),
    ):
        narrow=bpy.data.meshes.new('Integer-angle boundary')
        narrow.from_pydata(points,[[0,1],[1,2],[2,0]],[[0,1,2]])
        narrow.update();narrow.shade_smooth()
        for edge in narrow.edges:edge.use_edge_sharp=True
        intent=np.asarray([direction]*3,dtype=np.float32)
        narrow.normals_split_custom_set(intent.tolist());narrow.update()
        native=normals._encoded(narrow).copy()
        assert (normals._error(narrow,intent)>normals.QUANTIZATION_VECTOR_BOUND)==needs_correction
        normals.write(narrow,intent)
        assert np.array_equal(native,normals._encoded(narrow))!=needs_correction
        witness=normals.capture(narrow)
        assert witness['encoded_matches'] and witness['normal_space_matches']
        assert witness['decoded_within_quantization'],witness
        original=export_glb._mesh_digest(narrow);count=len(bpy.data.meshes)
        assert normals.capture(narrow)==witness
        assert export_glb._mesh_digest(narrow)==original and len(bpy.data.meshes)==count
        assert [list(v.co) for v in narrow.vertices]==points
        # Exact intent and the existing bound remain authoritative. An impossible
        # nonunit target refuses without partially mutating the source mesh.
        try:normals.write(narrow,np.asarray([direction]*3)*2)
        except ValueError as exc:assert 'quantization roundoff' in str(exc)
        else:raise AssertionError('unrepresentable direction silently accepted')
        assert export_glb._mesh_digest(narrow)==original and len(bpy.data.meshes)==count
    fan=bpy.data.meshes.new('Selected and unselected fan')
    fan.from_pydata([[0,0,0],[1,0,0],[1,1,0],[0,1,0]],[],[[0,1,2],[0,2,3]])
    fan.update();fan.shade_smooth()
    intent=[[0.,0.,1.]]*3+[[.6,0.,.8]]*3
    original=export_glb._mesh_digest(fan)
    try:normals.write(fan,intent)
    except ValueError as exc:assert 'planned sharp fan' in str(exc)
    else:raise AssertionError('distinct loop normals silently merged into an unplanned fan')
    assert export_glb._mesh_digest(fan)==original
    shared=next(e for e in fan.edges if set(e.vertices)=={0,2});shared.use_edge_sharp=True
    normals.write(fan,intent);w=normals.capture(fan)
    assert w['encoded_matches'] and w['decoded_within_quantization']
    next(e for e in fan.edges if set(e.vertices)=={0,2}).use_edge_sharp=False;fan.update()
    w=normals.capture(fan)
    assert not w['normal_space_matches'] and not w['encoded_matches']
    angle = np.arange(24) * 2 * np.pi / 24
    outline = np.c_[20 * np.cos(angle), 13 * np.sin(angle)]
    shape = quadratic_lens(outline, [1, .03, -.02, .001, .0003, .002], 1.4, max_edge_mm=3)
    mesh = bpy.data.meshes.new("Synthetic optical surface")
    mesh.from_pydata(shape["vertices_mm"].tolist(), [], shape["triangles"].tolist())
    mesh.update()
    mesh.shade_smooth()
    # This direct encoder test supplies all fan boundaries explicitly. The owner
    # planner has separate tests for adding only scoped fan boundaries.
    for edge in mesh.edges:edge.use_edge_sharp=True
    sharp = [edge.use_edge_sharp for edge in mesh.edges]
    assert normals.capture(mesh) is None
    intended = shape["corner_normals"].reshape(-1, 3)
    normals.write(mesh, intended)
    assert sharp == [edge.use_edge_sharp for edge in mesh.edges]
    digest = export_glb._mesh_digest(mesh)
    count = len(bpy.data.meshes)
    witness = normals.capture(mesh)
    assert witness["encoded_matches"] is True
    assert witness['decoded_within_quantization'] is True
    assert witness["max_vector_error"] > 0
    np.testing.assert_array_equal(witness["intent_normals"], intended.astype(np.float32).astype(float))
    assert normals.capture(mesh) == witness
    assert len(bpy.data.meshes) == count
    assert export_glb._mesh_digest(mesh) == digest
    encoded = mesh.attributes["custom_normal"]
    values = np.empty(len(encoded.data) * 2, dtype=np.int32)
    encoded.data.foreach_get("value", values)
    values[0] += 1 if values[0] < 32767 else -1
    encoded.data.foreach_set("value", values)
    mesh.update()
    assert normals.capture(mesh)["encoded_matches"] is False
    assert len(bpy.data.meshes) == count
    malformed = bpy.data.meshes.new("Wrong intent storage")
    malformed.from_pydata([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [], [[0, 1, 2]])
    malformed.attributes.new(normals.INTENT_ATTRIBUTE, "FLOAT", "POINT")
    try:
        normals.write(malformed, [[0, 0, 1]] * 3)
    except ValueError:
        pass
    else:
        raise AssertionError("incompatible authored attribute was overwritten")
    print("OWNER_NORMAL_ENCODING_PASSED")


if __name__ == "__main__":
    main()
