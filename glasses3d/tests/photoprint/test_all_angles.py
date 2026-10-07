"""The all-angles owner sheet's geometry (tests/photoprint/all_angles.py), on synthetic data only:

- ``photo_camera``: the pinhole camera handed to arview projects every point exactly where ``Camera.project`` does
  (perspective, roll, off-centre principal point), and its rotation is proper (no mirror: left stays left);
- the tile mapping: a camera re-expressed in a crop at another scale lands on the crop-resized photo's pixel;
- ``pose_glb``: only the moved group's vertices turn, by ``pose.hinge_matrix`` about the pivot, as NEW accessors (a
  shared accessor never changes for another node), normals turn with them, everything else is byte for byte; a mesh
  shared by a moved and an unmoved node, or by two moved nodes, and a node with its own transform (fix round 1);
- ``node_groups`` (decals to the nearest fit-mesh part) and ``side_check`` (the left / right verdict);
- the try-on photo pairing's viewing directions (from the glasses' front to the camera centre), and where a silhouette
  mismatch lies with the plain note built from it (``mismatch_parts``, ``shape_note``).
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np
import pytest

from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.photoprint.pose import hinge_matrix
from glasses3d.tests.photoprint import all_angles as AA
from glasses3d.tests.photoprint import ar_calibrate as AC


@pytest.mark.parametrize("cam", [Camera(0.0, 18.4, -0.08, 0.0514, 689.4, 424.9, 185.4),
                                 Camera(-73.6, 3.3, 1.6, 0.4457, 1097.1, 785.2, 544.0),
                                 Camera(179.9, 9.8, -0.02, 0.2157, 1283.9, 722.8, 543.3),
                                 Camera(37.0, -25.0, 9.0, 0.3, 900.0, 100.0, 700.0)])
def test_photo_camera_equals_camera_project(cam):
    frame = NormFrame((0.0, -7.567, -71.28), 146.63)
    rng = np.random.default_rng(1)
    P = rng.uniform([-70, -25, -150], [70, 15, 5], (500, 3))
    view, f = AA.photo_camera(cam, frame)
    uv = AA.project_view(view, f, cam.center_x, cam.center_y, P / 1000.0)
    assert np.abs(uv - project_mm(P, cam, frame)).max() < 1e-7
    R = view[:3, :3]
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12) and np.linalg.det(R) > 0.999999


def test_tile_camera_matches_crop_resize():
    cam = Camera(30.0, 18.0, -0.7, 0.07, 681.7, 476.9, 185.0)
    frame = NormFrame((0.0, -7.567, -71.28), 146.63)
    view, f = AA.photo_camera(cam, frame)
    P = np.array([[0.0, 0.0, 0.0], [65.0, 8.0, -10.0], [-60.0, -20.0, -140.0]])
    crop, size = (-24, -26, 974, 484), (1280, 636)
    kx, ky = size[0] / crop[2], size[1] / crop[3]
    cx, cy = (cam.center_x + 0.5 - crop[0]) * kx - 0.5, (cam.center_y + 0.5 - crop[1]) * ky - 0.5
    Q = (P / 1000.0) @ view[:3, :3].T + view[:3, 3]
    tile = np.column_stack([cx + f * kx * Q[:, 0] / -Q[:, 2], cy - f * ky * Q[:, 1] / -Q[:, 2]])
    native = project_mm(P, cam, frame)
    # a native pixel centre u sits at (u + 0.5 - x0) k - 0.5 in the tile (PIL box resize, edge convention)
    assert np.abs(tile[:, 0] - ((native[:, 0] + 0.5 - crop[0]) * kx - 0.5)).max() < 1e-7
    assert np.abs(tile[:, 1] - ((native[:, 1] + 0.5 - crop[1]) * ky - 0.5)).max() < 1e-7
    # crop_resize pads outside the photo with the fill and keeps a marker pixel where the mapping says
    img = np.zeros((420, 976, 3), np.uint8)
    img[200, 500] = 255
    out = AA.crop_resize(img, crop, size, [10, 20, 30], AA.Image.NEAREST)
    assert out.shape == (size[1], size[0], 3) and tuple(out[0, 0]) == (10, 20, 30)
    v, u = np.unravel_index(np.argmax(out.sum(axis=2)), out.shape[:2])
    assert abs(u - ((500 + 0.5 - crop[0]) * kx - 0.5)) <= 1.0 and abs(v - ((200 + 0.5 - crop[1]) * ky - 0.5)) <= 1.0


def _glb(tmp_path):
    """Two mesh nodes sharing one POSITION accessor plus a temple with its own, metres."""
    shared = np.array([[0.06, 0.01, -0.02], [0.07, 0.01, -0.12], [0.065, 0.0, -0.15]], "<f4")
    temple = np.array([[0.065, 0.009, -0.011], [0.066, 0.009, -0.14], [0.064, 0.0, -0.15]], "<f4")
    normals = np.tile(np.array([[1.0, 0.0, 0.0]], "<f4"), (3, 1))
    idx = np.array([0, 1, 2], "<u2").tobytes() + b"\x00\x00"
    blobs = [shared.tobytes(), temple.tobytes(), normals.tobytes(), idx]
    views, binc = [], b""
    for b in blobs:
        views.append({"buffer": 0, "byteOffset": len(binc), "byteLength": len(b)})
        binc += b
    acc = [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": shared.min(0).tolist(), "max": shared.max(0).tolist()},
           {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3", "min": temple.min(0).tolist(), "max": temple.max(0).tolist()},
           {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC3"},
           {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"}]
    prim = lambda pos: {"attributes": {"POSITION": pos, "NORMAL": 2}, "indices": 3}
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 1, 2]}],
           "nodes": [{"name": "Front_a", "mesh": 0}, {"name": "Front_b.001", "mesh": 1}, {"name": "Temple_Right.001", "mesh": 2}],
           "meshes": [{"primitives": [prim(0)]}, {"primitives": [prim(0)]}, {"primitives": [prim(1)]}],
           "accessors": acc, "bufferViews": views, "buffers": [{"byteLength": len(binc)}]}
    js = json.dumps(doc).encode()
    js += b" " * ((-len(js)) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binc), 0x004E4942) + binc
    p = tmp_path / "src.glb"
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return p, shared, temple


def test_pose_glb_turns_only_the_group_about_its_pivot(tmp_path):
    src, shared, temple = _glb(tmp_path)
    R, piv = hinge_matrix(5.0, +1), np.array([0.065, 0.0088, -0.0102])
    dst = tmp_path / "posed.glb"
    info = AA.pose_glb(src, dst, {"temple+x": (R, piv, 5.0)}, {"Front_a": "front", "Front_b": "front", "Temple_Right": "temple+x"})
    assert info["nodes"] == ["Temple_Right.001"]
    doc0, bin0 = AC._chunks(src.read_bytes())
    doc, binc = AC._chunks(dst.read_bytes())
    pos = lambda d, b, node: AA._accessor(d, b, d["meshes"][d["nodes"][node]["mesh"]]["primitives"][0]["attributes"]["POSITION"])
    nrm = lambda d, b, node: AA._accessor(d, b, d["meshes"][d["nodes"][node]["mesh"]]["primitives"][0]["attributes"]["NORMAL"])
    assert np.array_equal(pos(doc, binc, 0), shared.astype(float)) and np.array_equal(pos(doc, binc, 1), shared.astype(float))
    want = (temple.astype(float) - piv) @ R.T + piv
    assert np.abs(pos(doc, binc, 2) - want).max() < 1e-7
    assert np.abs(nrm(doc, binc, 2) - np.array([1.0, 0.0, 0.0]) @ R.T).max() < 1e-7
    assert np.array_equal(nrm(doc, binc, 0), nrm(doc0, bin0, 0)), "the front's (shared) normals are untouched"
    assert binc[:len(bin0)] == bin0, "every original byte is kept; the posed data is appended"
    a = doc["accessors"][doc["meshes"][2]["primitives"][0]["attributes"]["POSITION"]]
    assert np.allclose(a["min"], want.astype("<f4").min(0)) and np.allclose(a["max"], want.astype("<f4").max(0))
    # the tip of a temple opened by a positive angle moves away from x = 0 (pose.hinge_matrix's convention)
    assert pos(doc, binc, 2)[2, 0] > temple[2, 0]


# ----------------------------------------------------------------------------- fix round 1
def _make_glb(path, meshes, nodes):
    """A GLB of float VEC3 POSITION (+ NORMAL) meshes; ``meshes``: list of (N, 3) arrays (metres); ``nodes``: glTF node
    dicts (names, mesh indices, transforms, children)."""
    views, acc, binc, gl_meshes = [], [], b"", []
    for P in meshes:
        P = np.asarray(P, "<f4")
        N = np.tile(np.array([[1.0, 0.0, 0.0]], "<f4"), (len(P), 1))
        ids = []
        for arr, with_box in ((P, True), (N, False)):
            b = arr.tobytes()
            views.append({"buffer": 0, "byteOffset": len(binc), "byteLength": len(b)})
            binc += b
            a = {"bufferView": len(views) - 1, "componentType": 5126, "count": len(arr), "type": "VEC3"}
            if with_box:
                a["min"], a["max"] = arr.min(0).tolist(), arr.max(0).tolist()
            acc.append(a)
            ids.append(len(acc) - 1)
        gl_meshes.append({"primitives": [{"attributes": {"POSITION": ids[0], "NORMAL": ids[1]}}]})
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
           "meshes": gl_meshes, "accessors": acc, "bufferViews": views, "buffers": [{"byteLength": len(binc)}]}
    js = json.dumps(doc).encode()
    js += b" " * ((-len(js)) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binc), 0x004E4942) + binc
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return path


def _positions(path, node):
    d, b = AC._chunks(path.read_bytes())
    return AA._accessor(d, b, d["meshes"][d["nodes"][node]["mesh"]]["primitives"][0]["attributes"]["POSITION"])


TEMPLE = np.array([[0.065, 0.009, -0.011], [0.066, 0.009, -0.14], [0.064, 0.0, -0.15]])
MOVE = {"temple+x": (hinge_matrix(5.0, +1), np.array([0.065, 0.0088, -0.0102]), 5.0)}


def test_pose_glb_mesh_shared_by_a_moved_and_an_unmoved_node(tmp_path):
    """One mesh used by a moved node and an unmoved one: the moved node gets its own copy, the other keeps the rest."""
    src = _make_glb(tmp_path / "s.glb", [TEMPLE], [{"name": "Temple_Right", "mesh": 0}, {"name": "Front_copy", "mesh": 0}])
    dst = tmp_path / "d.glb"
    info = AA.pose_glb(src, dst, MOVE, {"Temple_Right": "temple+x", "Front_copy": "front"})
    assert info["nodes"] == ["Temple_Right"] and info["meshes_copied_for"] == ["Temple_Right"]
    R, piv, _ = MOVE["temple+x"]
    assert np.abs(_positions(dst, 0) - ((TEMPLE - piv) @ R.T + piv)).max() < 1e-7
    assert np.array_equal(_positions(dst, 1), TEMPLE.astype("<f4").astype(float)), "the unmoved node still draws the rest geometry"


def test_pose_glb_mesh_shared_by_two_moved_nodes(tmp_path):
    """One mesh used by two moved nodes: both are rotated once, from the original accessor (no crash, no double turn)."""
    src = _make_glb(tmp_path / "s.glb", [TEMPLE], [{"name": "Temple_Right", "mesh": 0}, {"name": "T_arm_Right", "mesh": 0}])
    dst = tmp_path / "d.glb"
    info = AA.pose_glb(src, dst, MOVE, {"Temple_Right": "temple+x", "T_arm_Right": "temple+x"})
    R, piv, _ = MOVE["temple+x"]
    want = (TEMPLE - piv) @ R.T + piv
    assert info["nodes"] == ["T_arm_Right", "Temple_Right"]
    assert np.abs(_positions(dst, 0) - want).max() < 1e-7 and np.abs(_positions(dst, 1) - want).max() < 1e-7
    d, _ = AC._chunks(dst.read_bytes())
    assert len(d["accessors"]) == 4, "one rotated POSITION and one NORMAL accessor, shared by the two nodes"


def test_pose_glb_refuses_a_node_with_its_own_transform(tmp_path):
    for nodes in ([{"name": "Temple_Right", "mesh": 0, "translation": [0.0, 0.01, 0.0]}],
                  [{"name": "Root", "children": [1], "rotation": [0.0, 0.0, 0.3826834, 0.9238795]}, {"name": "Temple_Right", "mesh": 0}]):
        src = _make_glb(tmp_path / "s.glb", [TEMPLE], nodes)
        with pytest.raises(ValueError, match="own transform"):
            AA.pose_glb(src, tmp_path / "d.glb", MOVE, {"Temple_Right": "temple+x"})
    src = _make_glb(tmp_path / "s.glb", [TEMPLE], [{"name": "Temple_Right", "mesh": 0, "scale": [1.0, 1.0, 1.0]}])
    AA.pose_glb(src, tmp_path / "d.glb", MOVE, {"Temple_Right": "temple+x"})       # an identity transform is fine


class _Mesh:
    def __init__(self, parts):
        self.names = [n for n, _ in parts]
        V, F, part = [], [], []
        for i, (_, P) in enumerate(parts):
            o = len(V)
            V += [list(p) for p in P]
            F.append([o, o + 1, o + 2])
            part.append(i)
        self.V, self.F, self.part = np.array(V, float), np.array(F), np.array(part)


class _Job:
    def __init__(self, parts):
        self.mesh = _Mesh(parts)


def test_node_groups_maps_fit_parts_by_name_and_decals_to_the_nearest_part(tmp_path):
    """Fit-mesh parts by the registration's group lists (base name before '.'); a decal the cameras skipped goes to the
    group of the nearest fit-mesh part, whatever its name says."""
    left_t = np.array([[-65.0, 9.0, -11.0], [-66.0, 9.0, -140.0], [-64.0, 0.0, -150.0]])
    right_t = left_t * [-1, 1, 1]
    brow = np.array([[-60.0, 10.0, 0.0], [60.0, 10.0, 0.0], [0.0, 15.0, 1.0]])
    job = _Job([("Temple_Left", left_t), ("Temple_Right", right_t), ("Brow_Left", brow)])
    groups = [{"name": "front", "parts": ["Brow_Left"]}, {"name": "temple-x", "parts": ["Temple_Left"]},
              {"name": "temple+x", "parts": ["Temple_Right"]}]
    decal_on_minus_x = (left_t + [-0.4, 0.0, 0.0]) / 1000.0           # named 'Right_*' but sitting on the -x temple
    src = _make_glb(tmp_path / "s.glb", [left_t / 1000, right_t / 1000, brow / 1000, decal_on_minus_x],
                    [{"name": "Temple_Left.001", "mesh": 0}, {"name": "Temple_Right", "mesh": 1}, {"name": "Brow_Left", "mesh": 2},
                     {"name": "Right_tip_print", "mesh": 3}, {"name": "Empty"}])
    assert AA.node_groups(src, job, groups) == {"Temple_Left": "temple-x", "Temple_Right": "temple+x", "Brow_Left": "front",
                                                "Right_tip_print": "temple-x"}


def _front_camera_rec(width=200, height=100, f=400.0):
    view = np.eye(4)
    view[2, 3] = -0.5                                         # the camera at z = +0.5 m looking down -Z (at the wearer)
    return {"camera_spec": {"cx": width / 2 - 0.5, "cy": height / 2 - 0.5, "fx": f, "fy": f},
            "pinhole": {"view_row_major_m": view.reshape(-1).tolist()}}


def test_side_check_ok_and_swapped(tmp_path):
    """From the front the wearer's left (+x) is on the image's right: the render agrees ('ok') or is mirrored ('SWAPPED')."""
    minus = np.array([[-0.06, 0.0, 0.0], [-0.05, 0.01, 0.0], [-0.055, -0.01, 0.0]])
    src = _make_glb(tmp_path / "s.glb", [minus, minus * [-1, 1, 1]], [{"name": "Brow_Left", "mesh": 0}, {"name": "Brow_Right", "mesh": 1}])
    report = {"id_palette": [{"rgb": [0, 0, 255], "name": "Brow_Left", "class": "frame", "side": "left", "decode_tolerance": 24},
                             {"rgb": [0, 255, 0], "name": "Brow_Right", "class": "frame", "side": "right", "decode_tolerance": 24},
                             {"rgb": [255, 0, 255], "name": "Lens_Left", "class": "lens", "side": "left", "decode_tolerance": 24}]}
    rec = _front_camera_rec()
    for swap, verdict in ((False, "ok"), (True, "SWAPPED")):
        img = np.zeros((100, 200, 3), np.uint8)
        img[45:55, 45:60] = [0, 255, 0] if swap else [0, 0, 255]       # image left
        img[45:55, 140:155] = [0, 0, 255] if swap else [0, 255, 0]     # image right
        img[45:55, 90:110] = [255, 0, 255]                             # a lens says nothing about a side
        png = tmp_path / f"p{swap}.png"
        AA.Image.fromarray(img).save(png)
        assert AA.side_check(report, png, rec, src)["verdict"] == verdict


def test_viewing_directions_start_at_the_front_not_the_axis():
    """A try-on head turned 0 deg but with the glasses above the camera axis is seen from BELOW (the first build's
    orientation-only angle said 'straight on'); a photo camera's direction is from the front to its centre."""
    front = np.array([0.0, 0.001, -0.002])
    M = np.diag([100.0, 100.0, 100.0, 1.0])                   # asset metres -> scene centimetres, no rotation
    M[:3, 3] = np.array([0.0, 3.36, -16.68]) - 100.0 * front  # the front sits 3.36 cm above the axis, 16.68 cm away
    d, dist = AA.view_toward_tryon(M.T.reshape(-1).tolist(), front)
    assert abs(math.degrees(math.asin(d[1])) + math.degrees(math.atan2(3.36, 16.68))) < 1e-9 and abs(d[0]) < 1e-12
    assert abs(dist - math.hypot(3.36, 16.68) / 100.0) < 1e-12
    assert AA.angle_deg(AA._axis_toward_camera(M.T.reshape(-1).tolist()), np.array([0.0, 0.0, 1.0])) < 1e-6
    view = np.eye(4)
    view[:3, 3] = [0.0, 0.0, -2.0]                            # a photo camera 2 m in front of the origin
    dp, dist_p = AA.view_toward_photo(view.reshape(-1), front)
    want = np.array([0.0, 0.0, 2.0]) - front
    assert np.allclose(dp, want / np.linalg.norm(want)) and abs(dist_p - np.linalg.norm(want)) < 1e-12


def test_mismatch_parts_and_the_shape_note():
    """A side view whose far arm shows below the near arm where the photo has none: the note says so, and names the
    near arm's missing tip plate."""
    H, W = 60, 200
    lab = np.zeros((H, W), np.uint8)
    lab[20:26, 20:180] = AA.ROLE_MINUS                         # near arm (camera on the -x side)
    lab[34:38, 60:150] = AA.ROLE_PLUS                          # far arm, lower in the image
    lab[10:40, 180:195] = AA.ROLE_FRONT
    m = lab > 0
    matte = np.zeros_like(m)
    matte[20:27, 20:182] = True                                # the photo: the near arm one px thicker, no far arm
    matte[10:40, 182:195] = True
    side = AA.view_side(np.array([-0.96, 0.17, 0.2]))
    names = AA.role_names(side)
    assert names == {AA.ROLE_FRONT: "the front", AA.ROLE_MINUS: "the near arm", AA.ROLE_PLUS: "the far arm"}
    mis = AA.mismatch_parts(lab, m, matte, 1.0, 5.0, names)
    far = mis["roles"]["the far arm"]
    assert far["rendered_hidden_in_photo"] == 1.0 and far["model_only_share"] > 0.85     # 360 of 406 model-only px
    note = AA.shape_note(mis, side, {"+x": True, "-x": False}, 0.75)
    assert "far arm below the near arm" in note and "in your photo the far arm is hidden" in note
    assert "the metal plate at the arm's tip only on the wearer's left arm, so this arm's tip is plain" in note
    assert "gold" not in note
    assert "even with the camera adjusted" in note
    # seen from the +x side the near arm carries the plate: no plate sentence
    other = AA.view_side(np.array([0.96, 0.17, 0.2]))
    names2 = AA.role_names(other)
    mis2 = AA.mismatch_parts(np.where(lab == AA.ROLE_MINUS, AA.ROLE_PLUS, np.where(lab == AA.ROLE_PLUS, AA.ROLE_MINUS, lab)).astype(np.uint8),
                             m, matte, 1.0, 5.0, names2)
    note2 = AA.shape_note(mis2, other, {"+x": True, "-x": False}, None)
    assert "plain" not in note2 and "far arm below the near arm" in note2 and "even at" not in note2
    # a front view names the arms by the wearer's side, never near / far
    assert AA.role_names(AA.view_side(np.array([0.1, 0.3, 0.95])))[AA.ROLE_PLUS] == "the wearer's left arm"


def test_difference_words_and_the_from_above_note():
    """The caption under a try-on photo: higher / lower, and for a turned pose 'more from the front' / 'more from the
    side' (never 'the wearer's right' for a photo that is merely less turned); the from-above note only for a straight
    view from above."""
    def d(az, el):
        a, e = math.radians(az), math.radians(el)
        return np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])
    assert AA._difference_words(d(30.0, 18.0), d(42.5, 14.2)) == "your photo, taken more from the front"
    assert AA._difference_words(d(55.0, 14.0), d(42.5, 14.2)) == "your photo, taken more from the side"
    assert AA._difference_words(d(0.0, 18.4), d(12.9, 11.1)) == "your photo, taken a little higher and more from the front"
    assert AA._difference_words(d(-8.0, 0.0), d(2.0, 0.0)) == "your photo, taken more from the wearer's right"
    assert AA._difference_words(d(0.0, 18.4), d(0.0, 19.1)) == "your photo, about the same angle"
    assert AA.tryon_row_notes(d(0.0, 19.1))["note"].startswith("Seen from above")
    assert AA.tryon_row_notes(d(42.5, 14.2))["note"] is None and AA.tryon_row_notes(d(0.0, -8.5))["note"] is None


# ----------------------------------------------------------------------------- fix round 2
def _dir(az, el):
    a, e = math.radians(az), math.radians(el)
    return np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])


def _side_view_mismatch():
    lab = np.zeros((60, 200), np.uint8)
    lab[20:26, 20:180] = AA.ROLE_MINUS
    lab[34:38, 60:150] = AA.ROLE_PLUS
    lab[10:40, 180:195] = AA.ROLE_FRONT
    matte = np.zeros(lab.shape, bool)
    matte[20:27, 20:182] = True
    matte[10:40, 182:195] = True
    side = AA.view_side(np.array([-0.96, 0.17, 0.2]))
    return AA.mismatch_parts(lab, lab > 0, matte, 1.0, 5.0, AA.role_names(side)), side


def test_shape_note_opening_follows_the_refinement_and_carries_no_numbers():
    """Three openings by the refinement result (not run / still under IOU_MIN / lined up), and words only: no degrees,
    no millimetres, no digits at all on the owner sheet."""
    mis, side = _side_view_mismatch()
    plates = {"+x": True, "-x": False}
    none = AA.shape_note(mis, side, plates, None)
    under = AA.shape_note(mis, side, plates, AA.IOU_MIN - 0.05)
    over = AA.shape_note(mis, side, plates, AA.IOU_MIN + 0.02)
    assert none.startswith("The model's outline does not line up with this photo, so judge")
    assert under.startswith("The model's outline does not line up with this photo, even with the camera adjusted")
    assert over.startswith("The model's outline lines up with this photo only when the camera is moved a little")
    for note in (none, under, over):
        assert not any(ch.isdigit() for ch in note), note
        assert "slightly off" in note or "clearly off" in note or "is along" not in note


def test_refinement_sentence_claims_only_what_the_search_found():
    chk = {"iou_best_refined_full_res": 0.755, "evaluations": 900, "rounds": [{"converged": False}], "converged": False,
           "at_or_near_a_bound": ["hinge_temple+x_deg"], "near_bound_rule": "within 5%", "bounds": "b"}
    s = AA.refinement_sentence(chk, "03")
    assert "stopped at its evaluation cap without converging" in s and "hinge_temple+x_deg" in s
    assert "not a proof that none exists" in s and "no admissible camera" not in s
    s2 = AA.refinement_sentence({**chk, "iou_best_refined_full_res": 0.82, "converged": True, "at_or_near_a_bound": []}, "03")
    assert "reaches 0.820, at or above" in s2 and "part of the gap is the camera" in s2 and "converged" in s2
    w = AA.wide_sentence({"iou_best_refined_full_res": 0.791, "evaluations": 3000, "converged": True, "bounds": "WIDE",
                          "refined_minus_fitted": {"magnification_pct": -8.65, "yaw_deg": 0.4, "hinge_temple+x_deg": 14.6},
                          "at_or_near_a_bound": ["hinge_temple+x_deg"]})
    assert w.startswith("Diagnostic only, beyond the pose model's bounds") and "0.791, still under" in w
    assert "magnification_pct -8.7" in w and "hinge_temple+x_deg +14.6" in w and "yaw_deg" not in w.split("by moving")[1].split(";")[0]


def test_part_roles_and_node_groups_refuse_unmapped_names(tmp_path):
    """A palette name with no hinge group is an error, never a silent 'front'; lenses need no mapping."""
    img = np.zeros((10, 10, 3), np.uint8)
    img[2:5, 2:5] = [0, 0, 255]
    png = tmp_path / "id.png"
    AA.Image.fromarray(img).save(png)
    report = {"id_palette": [{"rgb": [0, 0, 255], "name": "Temple_Left.001", "class": "frame"},
                             {"rgb": [255, 0, 255], "name": "Lens_Left", "class": "lens"}]}
    lab = AA.part_roles(report, png, {"Temple_Left": "temple-x"})
    assert (lab[2:5, 2:5] == AA.ROLE_MINUS).all()
    with pytest.raises(ValueError, match="maps to no hinge group"):
        AA.part_roles(report, png, {"Temple_Right": "temple+x"})
    left_t = np.array([[-65.0, 9.0, -11.0], [-66.0, 9.0, -140.0], [-64.0, 0.0, -150.0]])
    job = _Job([("Temple_Left", left_t), ("Loose_part", left_t * [-1, 1, 1])])
    src = _make_glb(tmp_path / "s.glb", [(left_t * [-1, 1, 1] + [0.3, 0, 0]) / 1000.0], [{"name": "Decal", "mesh": 0}])
    with pytest.raises(ValueError, match="in no hinge group"):
        AA.node_groups(src, job, [{"name": "temple-x", "parts": ["Temple_Left"]}])


def test_srgb_to_lab_and_difference_stats():
    lab = AA.srgb_to_lab(np.array([[255, 255, 255], [0, 0, 0], [255, 0, 0]]))
    assert np.allclose(lab[0], [100.0, 0.0, 0.0], atol=0.02) and np.allclose(lab[1], [0.0, 0.0, 0.0], atol=1e-6)
    assert np.allclose(lab[2], [53.24, 80.09, 67.20], atol=0.05)
    ref = np.full((4, 4, 3), 120, np.uint8)
    img = ref.copy()
    img[0, :] = [130, 120, 120]
    st = AA.difference_stats(ref, img, np.ones((4, 4), bool))
    assert st["share_over_levels"] == 0.25 and st["mean_dC"] > 0 and st["px"] == 16


def _per(share, de, dl, dc):
    return {"max_share_over_levels": share, "max_mean_dE76": de, "mean_dL_range": list(dl), "mean_dC_range": list(dc),
            "visible": bool(share >= AA.VISIBLE_SHARE or de >= AA.JND_DE)}


def test_option_labels_follow_the_measurement():
    """B measured clearly darker on every row is 'B (darker)'; C under the visibility thresholds is 'C (almost the same
    as A)' whatever the brief called it, and the legend says so; a visibly more chromatic C is 'C (more colourful)'."""
    lab = AA.option_labels({"per_option": {"B": _per(0.89, 3.7, (-3.4, -0.6), (-1.3, -0.2)), "C": _per(0.0, 1.0, (0.0, 0.01), (0.2, 0.9))}})
    assert lab["labels"]["B"] == "B (darker)" and lab["labels"]["C"] == "C (almost the same as A)"
    assert "C is A with slightly richer colour, so little that it looks almost the same as A" in lab["legend"]
    assert "B is A made darker" in lab["legend"]
    lab2 = AA.option_labels({"per_option": {"B": _per(0.89, 3.7, (-3.4, -0.6), (-1.3, -0.2)), "C": _per(0.3, 3.0, (0.0, 0.01), (1.0, 3.0))}})
    assert lab2["labels"]["C"] == "C (more colourful)"


def test_tryon_empty_text_names_the_closest_photo_in_words():
    """No digits; the closest photo by its title and how it was taken; 'from a little below' for a straight-on pose
    whose title has no height word, nothing extra for a pose titled 'from below'."""
    t = AA.tryon_empty_text("straight on", _dir(0.0, -8.5), "from the front, a little above", _dir(0.0, 18.9))
    assert t == ("No photo from near this angle.\nThe closest is 'from the front, a little above', taken higher.\n"
                 "Here the try-on sees the glasses from a little below.")
    t2 = AA.tryon_empty_text("turned, wearer's left arm near, from below", _dir(42.5, -28.7),
                             "from an angle, wearer's left side, a little above", _dir(31.1, 18.6))
    assert "taken higher and more from the front." in t2 and "Here the try-on" not in t2
    for s in (t, t2):
        assert not any(ch.isdigit() for ch in s)


def test_title_height_words_match_the_view():
    elev = {"05": 18.9, "01": 18.6, "03": 3.4, "06": 18.8, "04": 64.1, "02": 8.9}
    for short, _, title in AA.PHOTO_ROWS:
        AA.check_title_height(title, elev[short])
    with pytest.raises(SystemExit, match="says no height"):
        AA.check_title_height("from the front", 18.9)
    with pytest.raises(SystemExit):
        AA.check_title_height("from a little above", -8.5)
    AA.check_title_height("from below", -33.9)
    AA.check_title_height("straight on", -8.5)


def test_photos_footer_names_only_rows_on_its_sheet():
    main = AA.photos_footer(["05", "01", "04", "02"])
    assert "as in the first row" in main and "edge-on" in main
    side = AA.photos_footer(["03", "06"])
    assert "row" not in side and "edge-on" not in side and side == AA.PHOTOS_FOOTER


def test_capture_warnings_read_every_report(tmp_path):
    """A capture with exit 0 but status 'captured_with_warnings' is listed with each warning, its source and whether
    the capture made before this work had it; a clean capture is not listed."""
    before = tmp_path / "before"
    before.mkdir()
    (before / "report.json").write_text(json.dumps({"status": "captured_with_warnings",
                                                    "warnings": ["model: Lens_Left: 207 degenerate (zero-area) triangles"]}))
    for name, rep in (("w", {"status": "captured_with_warnings", "warnings": ["model: Lens_Left.001: 207 degenerate (zero-area) triangles",
                                                                              "something new"]}),
                      ("clean", {"status": "captured", "warnings": []})):
        (tmp_path / name).mkdir()
        (tmp_path / name / "report.json").write_text(json.dumps(rep))
    state = {"captures": [{"out": "w", "exit": 0}, {"out": "clean", "exit": 0}]}
    cw = AA.capture_warnings(state, root=tmp_path, before=before)
    assert [c["out"] for c in cw] == ["w"] and cw[0]["status"] == "captured_with_warnings"
    assert [w["also_before_this_work"] for w in cw[0]["warnings"]] == [True, False]
    assert cw[0]["warnings"][0]["source"] == "the model's own lens triangles"
    text = AA.capture_warnings_text(cw, 2)
    assert "1 of 2 captures" in text and "1 were NOT in the capture made before this work" in text and "every exit was 0" in text
    assert AA.capture_warnings_text([], 2).startswith("every capture came back clean")


def test_byte_identity_problems():
    ok = {"before_runs_identical": True, "after_identical_to_before": True, "differing_files": []}
    assert AA.byte_identity_problems(ok) == []
    bad = AA.byte_identity_problems({**ok, "after_identical_to_before": False, "differing_files": ["a.png", "b.png"]})
    assert len(bad) == 1 and "2 image(s)" in bad[0]
    assert len(AA.byte_identity_problems({"before_runs_identical": False, "after_identical_to_before": False})) == 2


def test_measure_cli_preserves_worker_limit(tmp_path, monkeypatch):
    state_path = tmp_path / "all_angles.json"
    state_path.write_text(json.dumps({"prepare": {"fixture": True}, "silhouettes": {"prior": True}}))
    calls = []
    def measure(out, prep, previous, *, workers):
        calls.append((out, prep, previous, workers))
        return {}
    monkeypatch.setattr(AA, "measure", measure)
    monkeypatch.setattr(AA, "option_differences", lambda *args: {"per_option": {}})
    monkeypatch.setattr(AA, "byte_identity", lambda *args: {
        "before_runs_identical": True, "after_identical_to_before": True})
    assert AA.main(["--out", str(tmp_path), "--stage", "measure", "--workers", "2"]) == 0
    assert calls == [(tmp_path, {"fixture": True}, {"prior": True}, 2)]
    assert json.loads(state_path.read_text())["silhouettes"] == {}


@pytest.mark.parametrize("workers", ["0", "-1"])
def test_nonpositive_workers_are_refused_before_work(tmp_path, workers):
    out = tmp_path / "must_not_be_created"
    with pytest.raises(SystemExit) as exc:
        AA.main(["--out", str(out), "--workers", workers])
    assert exc.value.code == 2
    assert not out.exists()
