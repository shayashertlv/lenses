"""Independent small geometries for the task-7 UV and region regressions."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d import printuv as PU
from glasses3d.g3d.printuv import charts as CH
from glasses3d.g3d_host.photoprint import manifest as MF, regions as RG


def box(size=(140, 3, 5), offset=(0, 0, 0)):
    vertices = np.array([[x, y, z] for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)])
    vertices = vertices * size + offset
    faces = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
                      [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                      [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return vertices, faces


def test_mirror_copy_crossing_charts_falls_back_without_claiming_a_partner():
    source = np.array([[10, 0, 0], [20, 0, 0], [20, 10, 0], [10, 10, 10]], float)
    vertices = np.vstack([source, source * [-1, 1, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3], [7, 5, 4], [7, 6, 5]])
    part = np.array([0, 0, 1, 1])
    lay = PU.layout(vertices, faces, part, ["A", "B"], ["A", "B"], 4)
    independent = PU.layout(vertices, faces, part, ["A", "B"], ["A", "B"], 4, mirror=False)
    assert lay.report["pass"] and not lay.partners and not lay.face_corr
    assert lay.report["partners"] == {}
    assert lay.info["B"]["mirror_fallback"]["source"] == "A"
    assert "two charts" in lay.info["B"]["mirror_fallback"]["reason"]
    np.testing.assert_array_equal(lay.faces, independent.faces)
    np.testing.assert_array_equal(lay.uv, independent.uv)


def test_low_polygon_elongated_part_uses_checked_cap_charts():
    vertices, faces = box()
    ids = np.arange(len(faces))
    assert PU.default_spec(vertices, faces, ids, "Arm").kind == "strip"
    lay = PU.layout(vertices, faces, np.zeros(len(faces), int), ["Arm"], ["Arm"], 4)
    independent, _ = CH.part_charts(vertices, faces, ids, kind="caps", tx=4)
    assert lay.report["pass"], lay.report
    assert lay.info["Arm"]["kind"] == "caps" and "strip_fallback" in lay.info["Arm"]
    assert {c["kind"] for c in lay.charts} == {c[0] for c in independent}
    np.testing.assert_array_equal(np.sort(lay.faces), ids)


def test_facing_class_accepts_each_faces_geometric_side():
    normals = np.array([[1, 0, 0], [-1, 0, 0], [-1, 0, 0], [1, 0, 0]], float)
    assert RG.facing_class(normals, np.array([1, -1, 1, -1])).tolist() == ["outer", "outer", "inner", "inner"]
    assert RG.facing_class(normals, 1).tolist() == ["outer", "inner", "inner", "outer"]


def test_one_piece_front_has_separate_sides_and_outward_end_regions():
    vertices, faces = box((100, 20, 6))
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    job = SimpleNamespace(mesh=SimpleNamespace(V=vertices, F=faces, part=np.zeros(len(faces), int), names=["Front"]),
                          face_area=area, face_normals=cross / (2 * area[:, None]), part_ids=lambda _parts: [0])
    regions = RG.build_regions(job, ["Front"], groups=[])
    outer = [r for r in regions if r.facing == "outer"]
    assert {r.side for r in outer} == {-1, 1}
    assert {r.name for r in outer} == {"Front[-x]/outer", "Front[+x]/outer"}
    assert all(np.all(np.sign(triangles[r.faces, :, 0].mean(1)) == r.side) for r in regions)
    assert not any(r.facing == "inner" for r in regions)
    np.testing.assert_array_equal(np.sort(np.concatenate([r.faces for r in regions])), np.arange(len(faces)))


def test_nondefault_layout_options_are_in_identity_without_changing_layout_json():
    vertices, faces = box((20, 16, 8))
    part = np.zeros(len(faces), int)
    lay = PU.layout(vertices, faces, part, ["Front"], ["Front"], 16, mirror=False, atlas_max=128)
    ident = MF.print_identity(vertices, faces, part, ["Front"], lay)
    assert ident["layout_options"] == {"mirror": False, "atlas_max": 128}
    assert "layout_options" not in lay.to_json()
    assert PU.replay_options() == {"mirror": True, "atlas_max": 4096}


@pytest.mark.parametrize("options", [{"mirror": "off"}, {"atlas_max": True}, {"atlas_max": 0}, {"unknown": 1}])
def test_bad_layout_replay_options_refuse(options):
    with pytest.raises(ValueError, match="layout_options"):
        PU.replay_options(options)
