"""``fit_views`` end to end on a synthetic case (WP3b): the procedural glasses of test_cameras_fit written as a GLB
(named Front / Temple parts, metres), three photos rendered at known cameras on a grey gradient backdrop (a front, a
'side' that is the glasses' LEFT side, an angled view), fitted with the generic driver; cameras.json and the overlays
are checked against the truth. Plus fast checks of the view-kind table, contour statistics and input validation."""
from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import cameras, fit_views, fitmesh, raster
from glasses3d.g3d_host import matte as M
from glasses3d.g3d_host.camera import CAMERA_FIELDS
from glasses3d.tests.test_cameras_fit import WRAP, bar, ring

SHAPE = (420, 640)
TRUTH = {                                   # name: (kind, yaw, pitch, roll, perspective)
    "front.png": ("front", 4.0, 12.0, 1.0, 0.12),
    "side.png": ("side", 88.0, 8.0, 0.0, 0.15),       # yaw +88: the glasses' own left side -> 'left' must win
    "angled.png": ("angled", 33.0, 14.0, 0.0, 0.12),
}
PPM = 3.0


def lens(cx, cy, rx, ry, n=48):
    """A flat elliptical lens disc (triangle fan) at z = 0."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = np.r_[[[cx, cy, 0.0]], np.c_[cx + rx * np.cos(t), cy + ry * np.sin(t), np.zeros(n)]]
    return V, np.array([(0, 1 + k, 1 + (k + 1) % n) for k in range(n)])


def synthetic_parts() -> list[tuple[str, np.ndarray, np.ndarray]]:
    """test_cameras_fit.glasses() plus two lens discs, split into a 'Front' part and two temples (mm). The lenses
    matter: the matte fills the closed rims (matte.py), so rims without lenses would never match the photo (measured:
    front IoU 0.45 at the true camera); with them the photo matte equals the render to IoU 0.998."""
    front = [ring(-32, 0, 26, 19, 4, 4), ring(32, 0, 26, 19, 4, 4), bar((-8, 9, 0), (8, 9, 0), 3, 4),
             bar((-60, 6, 0), (-66, 6, 0), 4, 8), bar((60, 6, 0), (66, 6, 0), 4, 8),
             bar((-12, -10, -3), (-11, -14, -6), 4, 3), lens(-32, 0, 23, 16), lens(32, 0, 23, 16)]
    out, Vs, Fs, off = [], [], [], 0
    for V, F in front:
        V = V.copy()
        V[:, 2] -= WRAP * V[:, 0] ** 2
        Vs.append(V); Fs.append(F + off); off += len(V)
    out.append(("Front", np.vstack(Vs), np.vstack(Fs)))
    zh = -WRAP * 66 ** 2
    for sx, name in ((-1, "Temple_R"), (1, "Temple_L")):
        a = bar((sx * 66, 6, zh - 2), (sx * 69, 5, -110), 3, 6)
        b = bar((sx * 69, 5, -110), (sx * 68, -18, -138), 3, 5)
        out.append((name, np.vstack([a[0], b[0]]), np.vstack([a[1], b[1] + len(a[0])])))
    return out


def mesh_glb(parts) -> bytes:
    """A minimal GLB: one node + mesh + triangle primitive per (name, V_mm, F), positions in metres."""
    blob, views, accessors, meshes, nodes = bytearray(), [], [], [], []
    for name, V, F in parts:
        pos = (np.asarray(V, float) * 0.001).astype("<f4")
        idx = np.asarray(F).astype("<u4").reshape(-1)
        for data, target in ((pos.tobytes(), 34962), (idx.tobytes(), 34963)):
            while len(blob) % 4:
                blob.append(0)
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
            blob.extend(data)
        accessors.append({"bufferView": len(views) - 2, "componentType": 5126, "count": len(pos), "type": "VEC3",
                          "min": pos.min(0).tolist(), "max": pos.max(0).tolist()})
        accessors.append({"bufferView": len(views) - 1, "componentType": 5125, "count": int(idx.size),
                          "type": "SCALAR"})
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": len(accessors) - 2},
                                                     "indices": len(accessors) - 1}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1})
    while len(blob) % 4:
        blob.append(0)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
           "meshes": meshes, "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def photo(mask: np.ndarray) -> np.ndarray:
    """Dark glasses on a light grey vertical gradient (the matte's quadratic background must absorb it)."""
    H, W = mask.shape
    bg = 238.0 - 14.0 * np.arange(H)[:, None] / H + 0.0 * np.arange(W)[None, :]
    img = np.repeat(bg[..., None], 3, axis=2)
    img[mask] = (62, 44, 30)
    return img.round().astype(np.uint8)


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    d = tmp_path_factory.mktemp("fit_views_synth")
    glb = d / "synthetic.glb"
    glb.write_bytes(mesh_glb(synthetic_parts()))
    mesh = fitmesh.load_fit_mesh(glb)
    scene = mesh.scene()
    pivot_mm = mesh.frame.to_mm(mesh.front_pivot(("Front",))[None])[0]
    images, views, true = {}, {}, {}
    for name, (kind, yaw, pitch, roll, persp) in TRUTH.items():
        cam = raster.view_camera(mesh.frame, yaw, pitch, PPM, SHAPE, roll=roll, perspective=persp)
        mask = scene.render(cam, SHAPE)["mask"]
        assert not (mask[:12].any() or mask[-12:].any() or mask[:, :12].any() or mask[:, -12:].any()), name
        fg = M.matte(photo(mask))
        assert (fg & mask).sum() / (fg | mask).sum() > 0.99, name               # the photo is fittable at all
        Image.fromarray(photo(mask)).save(d / name)
        images[name], views[name], true[name] = d / name, kind, cam
    out = d / "out"
    res = fit_views.fit_views(glb, images, views, out, front_parts=("Front",), workers=2, log=lambda *_: None)
    return {"res": res, "out": out, "true": true, "images": images, "frame": mesh.frame, "pivot_mm": pivot_mm}


@pytest.mark.slow
def test_fit_views_writes_cameras_json_for_every_photo(case):
    res, out = case["res"], case["out"]
    assert json.loads((out / fit_views.CAMERAS_JSON).read_text(encoding="utf-8")) == json.loads(
        json.dumps(res, default=fit_views._json))
    assert list(res["images"]) == list(TRUTH)                  # every photo, in the given order
    assert res["model"]["front_parts"] == ["Front"]
    np.testing.assert_allclose(res["model"]["front_pivot_mm"], case["pivot_mm"], atol=1e-9)
    for name, r in res["images"].items():
        kind, yaw, pitch, roll, persp = TRUTH[name]
        true = case["true"][name]
        assert r["view_kind"] == kind and r["status"] == "ok" and set(r["camera"]) == set(CAMERA_FIELDS), name
        assert r["iou"] > 0.97, (name, r["iou"])
        assert r["contour_mean_px"] < 1.5, (name, r["contour_mean_px"])
        assert abs(r["camera"]["yaw"] - yaw) < 1.5 and abs(r["camera"]["pitch"] - pitch) < 1.5, (name, r["camera"])
        true_ppm = cameras.px_per_mm_at(true, case["frame"], case["pivot_mm"])
        assert r["px_per_mm_at_front_pivot"] == pytest.approx(true_ppm, rel=0.03), name
        assert r["contour_mean_mm"] == pytest.approx(r["contour_mean_px"] / r["px_per_mm_at_front_pivot"], rel=1e-12)
        assert r["contour_p90_mm"] == pytest.approx(r["contour_p90_px"] / r["px_per_mm_at_front_pivot"], rel=1e-12)
        assert r["contour_p90_px"] <= r["contour_p95_px"]
        fg = M.matte(M.load_photo(case["images"][name]))
        assert tuple(r["roi"]) == cameras.matte_roi(fg)
        assert r["loss"] == pytest.approx(r["all_candidates"][[c["view"] for c in r["all_candidates"]].index(r["view"])]
                                          ["loss"])
        ov = np.asarray(Image.open(r["overlay"]).convert("RGB"))
        assert ov.shape[:2] == SHAPE
        assert np.all(ov == (255, 0, 255), axis=2).sum() > 500                   # the rendered silhouette edge


@pytest.mark.slow
def test_fit_views_tries_both_sides_and_keeps_the_lower_loss(case):
    r = case["res"]["images"]["side.png"]
    assert r["candidates"] == ["right", "left"]
    assert [c["view"] for c in r["all_candidates"]] == ["right", "left"]
    assert r["view"] == "left"
    assert r["loss_common_level"] == min(c["loss_common_level"] for c in r["all_candidates"])
    loser = r["all_candidates"][0]
    assert loser["loss_common_level"] > r["loss_common_level"] + 0.1 and loser["iou"] < r["iou"]


@pytest.mark.slow
def test_fit_views_gives_the_angled_photo_cross_seeds(case):
    r = case["res"]["images"]["angled.png"]
    assert r["view"] == "angled" and r["cross_seeds"] > 0
    assert r["cross_sources"] == ["front", "left"] == sorted(case["res"]["cross_winners"])


@pytest.mark.slow
def test_contour_summary_matches_native_metrics(case):
    """The p90 comes from the same render and arithmetic as native_metrics' mean and p95."""
    true_cam = case["true"]["front.png"]
    mesh = fitmesh.load_fit_mesh(case["res"]["model"]["path"])
    fg = M.matte(M.load_photo(case["images"]["front.png"]))
    roi = cameras.matte_roi(fg)
    x0, y0, x1, y1 = roi
    nm = cameras.native_metrics(mesh.scene(), true_cam, fg, roi)
    cs = fit_views.contour_summary(nm["mask"], fg[y0:y1, x0:x1])
    assert cs["mean"] == nm["contour_mean_px"] and cs["p95"] == nm["contour_p95_px"]
    assert cs["mean"] == pytest.approx((cs["render_to_photo_mean"] + cs["photo_to_render_mean"]) / 2, rel=1e-6)


def test_contour_summary_equals_contour_stats_and_orders_percentiles():
    rng = np.random.default_rng(3)
    a = np.zeros((80, 120), bool); a[20:60, 30:90] = True
    b = np.zeros_like(a); b[24:66, 26:84] = True; b[rng.integers(0, 80, 30), rng.integers(0, 120, 30)] = True
    cs = fit_views.contour_summary(a, b)
    mean, p95 = cameras.contour_stats(a, b)
    assert cs["mean"] == mean and cs["p95"] == p95 and cs["p90"] <= cs["p95"]
    empty = fit_views.contour_summary(a, np.zeros_like(a))
    assert empty["mean"] == float("inf") and empty["p90"] == float("inf")


def test_view_kinds():
    vc = fit_views.view_candidates
    assert vc("side") == ("right", "left") and vc("top") == ("top", "topb") and vc("rear") == ("back",)
    assert vc("bottom") == ("bottom", "bottomb") and vc("bottomb") == ("bottomb",)
    assert vc("elevated side") == vc("Elevated_Side") == vc("top-oblique") == cameras.TOP_OBLIQUE_VIEWS
    assert vc("back") == ("back",) and vc("left") == ("left",) and vc("topb") == ("topb",)
    assert vc("top_oblique_right") == ("top_oblique_right",) and vc("angled") == ("angled",)
    for kind, views in fit_views.VIEW_KINDS.items():
        assert views and all(v in cameras.VIEWS for v in views), kind
    with pytest.raises(ValueError, match="unknown view kind"):
        vc("diagonal")
    assert fit_views.kind_pitch_sign("top") == fit_views.kind_pitch_sign("top_oblique_left") == 1
    assert fit_views.kind_pitch_sign("side") is None and fit_views.kind_pitch_sign("rear") is None
    assert {k: fit_views.kind_pitch_sign(k) for k in fit_views.LABEL_PITCH_SIGN} == fit_views.LABEL_PITCH_SIGN


def test_cli_parsers():
    images, views = fit_views._parse_images(["side=C:/x/reference-03.png", "elevated side=C:/x/reference-06.png"])
    assert list(images) == ["reference-03.png", "reference-06.png"]
    assert views == {"reference-03.png": "side", "reference-06.png": "elevated side"}
    assert fit_views._parse_images(["bottom=C:/x/a.png"])[1] == {"a.png": "bottom"}
    for bad in (["reference-03.png"], ["diagonal=C:/x/a.png"], ["side=a/r.png", "front=b/r.png"]):
        with pytest.raises(SystemExit):
            fit_views._parse_images(bad)
    names = ["a.png", "b.png"]
    assert fit_views._parse_n_refine(None, names) is None
    assert fit_views._parse_n_refine(["2"], names) == 2
    assert fit_views._parse_n_refine(["a.png=2"], names) == {"a.png": 2, "b.png": cameras.N_COARSE_POWELL}
    assert fit_views._parse_n_refine(["4", "a.png=2"], names) == {"a.png": 2, "b.png": 4}
    for bad in (["c.png=2"], ["x"], ["0"]):
        with pytest.raises(SystemExit):
            fit_views._parse_n_refine(bad, names)


def test_fit_views_validates_inputs_before_fitting(tmp_path):
    glb = tmp_path / "m.glb"
    glb.write_bytes(mesh_glb(synthetic_parts()))
    a, b = tmp_path / "a.png", tmp_path / "sub" / "a.jpg"
    b.parent.mkdir()
    for p in (a, b):
        Image.fromarray(np.full((40, 40, 3), 200, np.uint8)).save(p)
    with pytest.raises(ValueError, match="every image needs exactly one view kind"):
        fit_views.fit_views(glb, {"a.png": a}, {"b.png": "front"}, tmp_path / "o")
    with pytest.raises(ValueError, match="unknown view kind"):
        fit_views.fit_views(glb, {"a.png": a}, {"a.png": "diagonal"}, tmp_path / "o")
    with pytest.raises(ValueError, match="share a stem"):
        fit_views.fit_views(glb, {"a.png": a, "a.jpg": b}, {"a.png": "front", "a.jpg": "side"}, tmp_path / "o")
    with pytest.raises(FileNotFoundError):
        fit_views.fit_views(tmp_path / "none.glb", {"a.png": a}, {"a.png": "front"}, tmp_path / "o")
    with pytest.raises(ValueError, match="unknown images"):
        fit_views.fit_views(glb, {"a.png": a}, {"a.png": "front"}, tmp_path / "o", n_refine={"z.png": 2})
    with pytest.raises(ValueError, match="matte is empty"):                     # a blank photo is an error, not a fit
        fit_views.fit_image(glb, a, "front")
    assert not (tmp_path / "o" / fit_views.CAMERAS_JSON).exists()
