"""WP2 stage B: picking and canonical views on a scene whose origin is NOT the world origin (glasses3d/plan/archive/20261019/WP2_DESIGN.md §6, §13).

The export fixture's ``offset_origin`` scene (tests/blender/build_export_fixture.py: geometry and
``mdl_bridge_underside`` moved by (0, 5, 3) mm), in a live session:

* canonical views: Blender's silhouette (clay alpha) equals the host raster of the exported GLB (parts pass) at the
  same WP3b camera, so the origin shift is applied the same way on both sides;
* pick_surface_point lands within 0.05 mm of the host raster's surface point (+ the origin) on the right object, and
  ``asset_mm`` = point - origin (GLB millimetres);
* a render-hidden but viewport-visible plane in front of everything changes neither the renders nor the picks (rays
  pass through objects that do not render).
Needs Blender 5.2."""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.camera import Camera, NormFrame

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

FIXTURE = blender.G3D / "tests" / "blender" / "build_export_fixture.py"
OFFSET = np.array([0.0, 5.0, 3.0])
HIDDEN_PLANE = '''
me = bpy.data.meshes.new("Guide_plane")
s = 400.0
me.from_pydata([(-s, -s, 60.0), (s, -s, 60.0), (s, s, 60.0), (-s, s, 60.0)], [], [(0, 1, 2, 3)])
ob = bpy.data.objects.new("Guide_plane", me)
bpy.context.scene.collection.objects.link(ob)
ob.hide_render = True
'''


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    d = tmp_path_factory.mktemp("wp2pickfix")
    proc = blender.run(None, FIXTURE, [d, "offset_origin"], timeout_s=300)
    seed = d / "fixture-offset_origin.blend"
    assert seed.is_file(), proc.stdout[-2000:] + proc.stderr[-2000:]
    root = tmp_path_factory.mktemp("wp2pick")
    s = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    s.start()
    yield {"root": root, "session": s, "seeing": Seeing(s)}
    s.close()
    shutil.rmtree(root / "checkpoints", onerror=_rw)


def _views(job, views=("front", "top", "angled_right")):
    r = job["seeing"].render_views(list(views), passes=("silhouette", "parts"), size_px=640)
    assert r["status"] == "ok", r
    return r, json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))


def test_canonical_silhouettes_match_the_host_raster_with_an_offset_origin(job):
    r, m = _views(job)
    for name, v in m["views"].items():
        assert v["origin_mm"] == OFFSET.tolist()
        sil = np.asarray(Image.open(v["files"]["silhouette"]["path"])) > 127
        parts = np.asarray(Image.open(v["files"]["parts"]["path"]).convert("RGBA"))[..., 3] > 0
        iou = float((sil & parts).sum() / (sil | parts).sum())
        assert iou >= 0.99, (name, iou)
    job["first_silhouettes"] = {n: np.asarray(Image.open(v["files"]["silhouette"]["path"])) for n, v in
                                m["views"].items()}


def _pick_check(job, m, manifest, view, rng):
    see = job["seeing"]
    exp = see.export_head()
    mv = m["views"][view]
    cam, frame, shape = Camera.from_dict(mv["camera"]), NormFrame.from_dict(mv["frame"]), tuple(mv["shape"])
    mesh = see.fit_mesh(exp["path"], exp["sha256"], frame=frame)
    ras = mesh.scene().render(cam, shape, 1, want_points=True)
    part = np.full(shape, -1)
    part[ras["mask"]] = mesh.part[ras["face_id"][ras["mask"]]]
    same = ras["mask"] & (ndimage.minimum_filter(part, 5) == ndimage.maximum_filter(part, 5))
    ys, xs = np.nonzero(same)
    idx = rng.choice(len(xs), min(48, len(xs)), replace=False)
    uv = np.column_stack([xs[idx], ys[idx]]).astype(float)
    p = see.pick_surface_point(view, uv.tolist(), manifest=manifest)
    assert p["status"] == "ok", p
    assert p["origin_mm"] == OFFSET.tolist()
    errs = []
    for (u, v), h in zip(uv.astype(int), p["hits"]):
        assert h["hit"], (view, u, v)
        assert h["object"] == mesh.node_names[part[v, u]], (h, mesh.node_names[part[v, u]])
        want_glb = ras["points"][v, u]
        errs.append(float(np.linalg.norm(np.asarray(h["asset_mm"]) - want_glb)))
        np.testing.assert_allclose(np.asarray(h["point_mm"]) - OFFSET, h["asset_mm"], atol=1e-9)
        assert abs(np.linalg.norm(h["normal"]) - 1.0) < 1e-5 and h["partRole"] in ("frame", "lens", "temple")
    return max(errs)


def test_pick_lands_on_the_raster_surface_point_with_the_origin_applied(job):
    r, m = _views(job)
    rng = np.random.default_rng(11)
    worst = {v: _pick_check(job, m, r["manifest"], v, rng) for v in m["views"]}
    assert max(worst.values()) <= 0.05, worst
    print("pick error vs host raster (mm):", {k: round(v, 5) for k, v in worst.items()})
    # a canonical view resolved without a manifest is the same camera
    p = job["seeing"].pick_surface_point("front", [[320, 100]], size_px=640)
    q = job["seeing"].pick_surface_point("front", [[320, 100]], manifest=r["manifest"])
    assert p["hits"] == q["hits"]


def test_render_hidden_objects_neither_render_nor_catch_rays(job):
    s = job["session"]
    e = s.execute(HIDDEN_PLANE, soft_timeout_s=30)
    assert e["status"] == "ok" and e["scene_delta"]["added"] == ["Guide_plane"], e
    r, m = _views(job)
    for name, v in m["views"].items():
        sil = np.asarray(Image.open(v["files"]["silhouette"]["path"]))
        assert (sil == job["first_silhouettes"][name]).mean() > 0.999, name
    rng = np.random.default_rng(12)
    assert _pick_check(job, m, r["manifest"], "front", rng) <= 0.05
    assert s.fingerprint() == s.head["fingerprint"]
