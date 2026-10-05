import struct

import numpy as np
import pytest

from glasses3d.g3d_host import glb as G
from glasses3d.tests import glbfab


def write(tmp_path, data, name="m.glb"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_reads_positions_in_metres_and_triangles(tmp_path):
    g = G.load(write(tmp_path, glbfab.build()))
    assert len(g.primitives) == 7
    bridge = g.primitives[0]
    assert bridge.node_name == "Bridge" and bridge.mode == 4
    np.testing.assert_allclose(bridge.positions.min(0), [-0.009, 0.0, -0.002], atol=1e-7)
    assert bridge.triangles.shape == (12, 3) and bridge.max_index == 7


def test_node_and_parent_transforms_compose(tmp_path):
    child = np.eye(4); child[:3, 3] = [0.01, 0, 0]
    parent = np.eye(4); parent[:3, 3] = [0, 0.02, 0]
    g = G.load(write(tmp_path, glbfab.build(node_matrix=child, parent_matrix=parent)))
    bridge = g.primitives[0]
    np.testing.assert_allclose(bridge.world[:3, 3], [0.01, 0.02, 0])
    np.testing.assert_allclose(bridge.positions.min(0), [0.001, 0.02, -0.002], atol=1e-7)
    np.testing.assert_allclose(g.primitives[1].world[:3, 3], [0, 0.02, 0])


def test_trs_matrix_matches_rotation():
    m = G.node_local_matrix({"rotation": [0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)], "scale": [2, 2, 2],
                             "translation": [1, 2, 3]})
    np.testing.assert_allclose(m @ [1, 0, 0, 1], [1, 4, 3, 1], atol=1e-12)


def test_strided_and_sparse_accessors(tmp_path):
    a = G.load(write(tmp_path, glbfab.build(strided=True), "s.glb"))
    b = G.load(write(tmp_path, glbfab.build(), "p.glb"))
    for pa, pb in zip(a.primitives, b.primitives):
        np.testing.assert_allclose(pa.positions, pb.positions)
    c = G.load(write(tmp_path, glbfab.build(sparse_shift=[0.5, 0.5, 0.5]), "sp.glb"))
    np.testing.assert_allclose(c.primitives[0].positions[0], [0.5, 0.5, 0.5], atol=1e-7)
    np.testing.assert_allclose(c.primitives[0].positions[1:], b.primitives[0].positions[1:])


def test_data_uri_buffer(tmp_path):
    g = G.load(write(tmp_path, glbfab.build(data_uri_buffer=True)))
    assert len(g.primitives) == 7 and not g.external_uris


def test_normalized_signed_clamps_to_minus_one(tmp_path):
    g = G.Glb(path=tmp_path, doc={"accessors": [{"bufferView": 0, "componentType": 5120, "count": 2,
                                                 "type": "SCALAR", "normalized": True}],
                                  "bufferViews": [{"buffer": 0, "byteLength": 2}], "buffers": [{"byteLength": 2}]},
              bin_chunk=struct.pack("<bb", -128, 127), file_bytes=0)
    np.testing.assert_allclose(g.accessor(0).ravel(), [-1.0, 1.0])


def test_compressed_primitive_is_reported_not_decoded(tmp_path):
    g = G.load(write(tmp_path, glbfab.build(compressed=True)))
    assert g.primitives[0].compressed == "KHR_draco_mesh_compression"
    assert g.primitives[0].positions.shape == (0, 3)
    # its POSITION min/max (required by glTF, kept by Draco) bound it in world space
    np.testing.assert_allclose(g.primitives[0].bounds, [[-0.009, 0.0, -0.002], [0.009, 0.004, 0.002]], atol=1e-7)
    assert g.primitives[1].bounds is None                           # decoded primitives are measured, not bounded
    shifted = G.load(write(tmp_path, glbfab.build(compressed=True, node_matrix=glbfab.trans(0.01, 0, 0)), "t.glb"))
    np.testing.assert_allclose(shifted.primitives[0].bounds[:, 0], [0.001, 0.019], atol=1e-7)


def test_signed_or_negative_indices_are_recorded(tmp_path):
    g = G.load(write(tmp_path, glbfab.build(index_component=5122, index_value=-1)))
    issue = g.primitives[0].index_issue
    assert "componentType 5122" in issue and "index -1 < 0" in issue
    for ct in (5121, 5123, 5125):
        g = G.load(write(tmp_path, glbfab.build(index_component=ct), f"i{ct}.glb"))
        assert g.primitives[0].index_issue is None and g.primitives[0].triangles.shape == (12, 3)


def test_rejects_bad_files(tmp_path):
    with pytest.raises(G.GlbError, match="magic"):
        G.load(write(tmp_path, b"PK\x03\x04" + b"\0" * 40))
    good = glbfab.build()
    with pytest.raises(G.GlbError, match="length"):
        G.load(write(tmp_path, good + b"\0\0\0\0", "long.glb"))
    with pytest.raises(G.GlbError, match="version"):
        G.load(write(tmp_path, good[:4] + struct.pack("<I", 1) + good[8:], "v1.glb"))


def test_image_size_png_and_jpeg():
    assert G.image_size(glbfab.png_bytes(37, 11)) == (37, 11)
    jpeg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
            + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 600, 800) + b"\0" * 10)
    assert G.image_size(jpeg) == (800, 600)
    assert G.image_size(b"nope") is None
    assert G.image_size(None) is None


def _riff(chunk: bytes, body: bytes) -> bytes:
    data = chunk + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(data)) + b"WEBP" + data


def test_image_size_webp_lossy_lossless_and_extended():
    vp8 = _riff(b"VP8 ", b"\x10\x02\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", 5000, 8) + b"\0" * 8)
    assert G.image_size(vp8) == (5000, 8)
    bits = (3000 - 1) | ((20 - 1) << 14)
    vp8l = _riff(b"VP8L", b"\x2f" + bits.to_bytes(4, "little") + b"\0" * 8)
    assert G.image_size(vp8l) == (3000, 20)
    vp8x = _riff(b"VP8X", b"\x10\0\0\0" + (4999).to_bytes(3, "little") + (2047).to_bytes(3, "little") + b"\0" * 4)
    assert G.image_size(vp8x) == (5000, 2048)


def test_strips_and_fans_are_triangulated():
    idx = np.arange(6)
    np.testing.assert_array_equal(G.triangulate(idx, 5), [[0, 1, 2], [1, 3, 2], [2, 3, 4], [3, 5, 4]])
    np.testing.assert_array_equal(G.triangulate(idx, 6), [[1, 2, 0], [2, 3, 0], [3, 4, 0], [4, 5, 0]])
    assert G.triangulate(idx, 1).shape == (0, 3) and G.triangulate(idx[:2], 5).shape == (0, 3)


def test_strip_primitive_reads_as_triangles(tmp_path):
    g = G.load(write(tmp_path, glbfab.build(mode=5)))
    bridge = g.primitives[0]
    assert bridge.mode == 5 and bridge.error is None
    tri = {tuple(sorted(t)) for t in bridge.triangles.tolist() if len(set(t)) == 3}
    assert tri == {tuple(sorted(t)) for t in glbfab.BOX_FACES.tolist()}


def test_deep_hierarchy_walks_iteratively_in_order(tmp_path):
    def deep(doc, add):
        n0 = len(doc["nodes"])
        chain = [{"name": f"e{i}", "children": [n0 + i + 1]} for i in range(1500)]
        chain[-1]["children"] = doc["scenes"][0]["nodes"]
        doc["nodes"] += chain
        doc["scenes"][0]["nodes"] = [n0]
    g = G.load(write(tmp_path, glbfab.build(patch=deep)))
    assert [p.node_name for p in g.primitives] == list(glbfab.default_parts())


def test_scene_problems_and_missing_resources_are_recorded(tmp_path):
    def no_scene(doc, add):
        doc.pop("scenes")
        doc.pop("scene")
        doc["images"] = [{"uri": "tex%20print.png"}]
    g = G.load(write(tmp_path, glbfab.build(patch=no_scene)))
    assert g.scene_issue and "no scenes" in g.scene_issue
    assert len(g.primitives) == 7                                   # measured from the root nodes anyway
    assert g.missing_uris == ["image 0: tex%20print.png"]
    (tmp_path / "tex print.png").write_bytes(glbfab.png_bytes(3, 2))   # percent-encoded URIs resolve
    g = G.load(write(tmp_path, glbfab.build(patch=no_scene)))
    assert g.missing_uris == [] and G.image_size(g.image_bytes(0)) == (3, 2)


def test_undecodable_primitive_does_not_stop_the_load(tmp_path):
    def bad_accessor(doc, add):
        doc["meshes"][1]["primitives"][0]["attributes"]["POSITION"] = 999
    g = G.load(write(tmp_path, glbfab.build(patch=bad_accessor)))
    assert g.primitives[1].error and "999" in g.primitives[1].error
    assert g.primitives[1].positions.shape == (0, 3) and g.primitives[0].error is None
