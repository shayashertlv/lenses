"""Camera-fit speed changes (2026-10-23) return the same numbers.

* A: ``pose.posed`` finds the temple rows once per mesh and its copy's scene takes hull rows from the authored parts:
  vertices, projected bounds and renders equal the former per-call rows and a whole-mesh hull scene;
* B: ``raster.camera_rays`` / ``RasterScene.cast`` write rays column by column with the operations of the former
  whole-array formula (kept below as the reference): bit-identical rays, hits, depths and points;
* C: ``fit_views`` queues the angled photo once the photos that can seed it are fitted, with the same cross winners
  and records as the sequential batches."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import dataclasses
import math
import pickle
import threading

import numpy as np
import open3d as o3d
import pytest

from glasses3d.g3d_host import fit_views as FV, pose as PO, raster
from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.g3d_host.fitmesh import FitMesh

AXIS = (64.6, -10.1)
SHAPE = (240, 320)
CAMERAS = [Camera(0.0, 0.0, 0.0, 0.0, 260.0, 160.0, 120.0),
           Camera(40.0, 10.0, 5.0, 0.2, 250.0, 150.0, 125.0, 0.01, -0.02),
           Camera(90.0, -4.0, -2.0, 0.15, 240.0, 170.0, 118.0),
           Camera(10.0, 70.0, 0.0, 0.1, 255.0, 160.0, 120.0, -0.015, 0.0)]
POSES = [PO.make_pose(theta, AXIS) for theta in
         ({"+1": 8.0, "-1": -3.0}, {"+1": 25.0, "-1": 30.0}, {"+1": 0.0, "-1": 12.0}, {"+1": 15.0})]


def _blob(center, radii, n=8):
    """A closed latitude/longitude ellipsoid (no coplanar facets)."""
    lat = np.linspace(0, math.pi, n + 1)[1:-1]
    lon = np.linspace(0, 2 * math.pi, 2 * n, endpoint=False)
    rings = [[(math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a)) for b in lon] for a in lat]
    V = np.array([(0, 0, 1)] + [p for ring in rings for p in ring] + [(0, 0, -1)]) * radii + center
    m, last = len(lon), len(V) - 1
    F = [(0, 1 + j, 1 + (j + 1) % m) for j in range(m)]
    for i in range(len(lat) - 1):
        for j in range(m):
            a, b = 1 + i * m + j, 1 + i * m + (j + 1) % m
            F += [(a, a + m, b + m), (a, b + m, b)]
    F += [(last, 1 + (len(lat) - 1) * m + (j + 1) % m, 1 + (len(lat) - 1) * m + j) for j in range(m)]
    return V, np.array(F)


def dense_head():
    parts = [("Front", (0, -1, -2), (70, 19, 6), "frame"),
             ("TempleR", (67, 0, -75), (3, 12, 65), "temple"),
             ("TempleL", (-67, 0, -75), (3, 12, 65), "temple"),
             ("KnuckleR", (64.6, 0.75, -10.1), (1.1, 0.8, 1.1), "temple"),
             ("LensR", (34, -3, -1), (24, 16, 3), "lens")]
    Vs, Fs, part, off = [], [], [], 0
    for i, (_name, center, radii, _role) in enumerate(parts):
        V, F = _blob(np.array(center, float), np.array(radii, float))
        Vs.append(V)
        Fs.append(F + off)
        part.append(np.full(len(F), i))
        off += len(V)
    V = np.vstack(Vs + [np.array([[0.0, 0.0, 30.0]])])     # one unreferenced vertex beyond the front
    mesh = FitMesh(V=V, F=np.vstack(Fs), part=np.concatenate(part), names=[p[0] for p in parts],
                   node_names=[p[0] for p in parts], materials=[0] * len(parts), frame=NormFrame.from_bbox(V))
    return mesh, {p[0]: p[3] for p in parts}


def former_camera_rays(camera, u, v, z0=1.0):
    """raster.camera_rays before 2026-10-23, verbatim."""
    right, up, toward = raster.camera_basis(camera)
    roll = math.radians(camera.roll)
    cr, sr = math.cos(roll), math.sin(roll)
    a = (np.asarray(u, float) - camera.center_x) / camera.scale
    b = (camera.center_y - np.asarray(v, float)) / camera.scale
    x = cr * a + sr * b
    y = -sr * a + cr * b
    p = float(camera.perspective)
    if p > 0:
        z0 = min(float(z0), 1.0 / p)
    k = 1.0 - p * z0
    origins = (x * k)[:, None] * right + (y * k)[:, None] * up + z0 * toward
    if camera.eye_x or camera.eye_y:
        origins += camera.eye_x * right + camera.eye_y * up
    directions = p * (x[:, None] * right + y[:, None] * up) - toward
    return origins, directions


def _grid():
    us, vs = raster.pixel_grid(SHAPE, 2)
    uu, vv = np.meshgrid(us, vs)
    return uu.ravel(), vv.ravel()


@pytest.mark.parametrize("camera", CAMERAS)
def test_rays_and_cast_equal_the_former_whole_array_formula(camera):
    u, v = _grid()
    for z0 in (1.0, 2.5):
        for new, old in zip(raster.camera_rays(camera, u, v, z0), former_camera_rays(camera, u, v, z0)):
            assert new.dtype == old.dtype and np.array_equal(new, old)
    mesh, _ = dense_head()
    scene = raster.RasterScene(mesh.V, mesh.F, mesh.frame)
    origins, directions = former_camera_rays(camera, u, v, scene.z0)
    ans = scene._scene.cast_rays(o3d.core.Tensor(np.concatenate([origins, directions], axis=1).astype(np.float32)))
    prim, t = ans["primitive_ids"].numpy(), ans["t_hit"].numpy().astype(np.float64)
    hit = (prim != raster.INVALID) & np.isfinite(t)
    points = origins[hit] + t[hit, None] * directions[hit]
    out = scene.cast(camera, u, v, want_points=True, want_normal=True, want_bary=True)
    assert hit.any() and np.array_equal(out["hit"], hit)
    assert np.array_equal(out["face_id"][hit], prim[hit].astype(np.int32))
    assert np.array_equal(out["depth"][hit],
                          (-(points @ raster.camera_basis(camera)[2]) * scene.frame.extent).astype(np.float32))
    assert np.array_equal(out["points"][hit], scene.frame.to_mm(points))
    assert np.array_equal(out["normal"][hit], ans["primitive_normals"].numpy()[hit])


@pytest.mark.parametrize("pose", POSES)
def test_posed_copy_equals_former_rows_and_whole_mesh_hull_scene(pose):
    mesh, roles = dense_head()
    copy = PO.posed(mesh, pose, roles)
    V = mesh.V.copy()
    for side, (theta, axis) in PO.parse_pose(pose).items():
        rows = np.unique(mesh.F[np.isin(mesh.part, PO.temple_parts(mesh, side, roles))])
        V[rows] = PO.rot_about_hinge(mesh.V[rows], side, theta, axis)
    assert np.array_equal(copy.V, V) and copy.F is mesh.F
    fast, full = copy.scene(), raster.RasterScene(copy.V, copy.F, copy.frame)
    assert fast is copy.scene() and len(copy.V) > 64 and len(fast.hull) < len(copy.V)
    assert set(map(tuple, full.hull)) <= set(map(tuple, fast.hull)) <= set(map(tuple, full.V_norm))
    assert fast.max_radius == full.max_radius and fast.z0 == full.z0
    for camera in CAMERAS:
        a, b = raster._project_norm(fast.hull, camera), raster._project_norm(full.hull, camera)
        assert np.array_equal(a.min(0), b.min(0)) and np.array_equal(a.max(0), b.max(0))
        ra, rb = fast.render(camera, SHAPE), full.render(camera, SHAPE)
        for key in ("mask", "face_id", "depth"):
            assert np.array_equal(ra[key], rb[key]), key


def test_cache_belongs_to_one_object_and_is_not_pickled():
    mesh, roles = dense_head()
    boxes = PO._part_boxes(mesh)
    assert PO._part_boxes(mesh) is boxes
    copy = PO.posed(mesh, POSES[0], roles)
    assert "hull_rows" in copy.cache and "hull_rows" not in mesh.cache and "part_boxes" not in copy.cache
    copy.scene()
    assert dataclasses.replace(copy, V=copy.V + 1.0).cache == {}
    restored = pickle.loads(pickle.dumps(copy))
    assert restored.cache == {} and np.array_equal(restored.V, copy.V) and restored.names == copy.names
    assert "scene" in copy.cache                                  # the original keeps its scene


def _fake_fits(monkeypatch, tmp_path, *, pooled):
    """Explicit-matte photo fits that record what each received; the top photo can only finish after the angled
    photo has started when ``pooled`` (it never seeds the angled photo)."""
    tmp_path.mkdir(parents=True)
    model = tmp_path / "model.glb"
    model.write_bytes(b"synthetic model")
    photos = {}
    for name in ("front.png", "top.png", "angled.png"):
        photos[name] = tmp_path / name
        photos[name].write_bytes(name.encode())
    context = {"model": {"sha256": FV.sha256_file(model)}}
    started, seeds, finished = threading.Event(), [], []
    def fitted(glb, path, kind, **kwargs):
        if path.name == "top.png" and pooled:
            assert started.wait(10), "the angled photo waited for the top photo"
        if path.name == "angled.png":
            started.set()
            seeds.append(kwargs["cross_winners"])
        finished.append(path.name)
        return {"status": "ok", "view": FV.view_candidates(kind)[0], "camera": {"yaw": float(len(path.name))},
                "model": kwargs["_model_context"]["model"], "seconds": 0.0}
    monkeypatch.setattr(FV, "_fit_context", lambda *args: context)
    monkeypatch.setattr(FV, "fit_image", fitted)
    monkeypatch.setattr(FV, "_row", lambda n, r: n)
    monkeypatch.setattr(FV, "fit_workers", lambda requested: requested)
    monkeypatch.setattr(FV, "ProcessPoolExecutor", ThreadPoolExecutor)
    return model, photos, seeds, finished


def test_angled_photo_starts_before_a_photo_that_cannot_seed_it(monkeypatch, tmp_path):
    results = {}
    for pooled, workers in ((False, 1), (True, 3)):
        model, photos, seeds, finished = _fake_fits(monkeypatch, tmp_path / str(workers), pooled=pooled)
        kinds = {"front.png": "front", "top.png": "top", "angled.png": "angled"}
        results[pooled] = FV.fit_views(model, photos, kinds, tmp_path / f"fit{workers}", workers=workers,
                                       matte_modes={name: "default" for name in photos}, log=lambda _: None)
        assert seeds == [{"front": {"yaw": 9.0}}]                 # front seeds; top/topb are never sources
        if pooled:
            assert finished.index("angled.png") < finished.index("top.png")
    sequential, pooled = results[False], results[True]
    assert pooled["cross_winners"] == sequential["cross_winners"] == {"front": {"yaw": 9.0}}
    assert pooled["images"] == sequential["images"]
