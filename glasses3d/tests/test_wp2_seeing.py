"""WP2 stage B on the real Prada scene (WP2_DESIGN §6, §13, §14 criteria 10-11, §15 row B).

One live session on a copy of the accepted Prada Studio scene (8c82.../agent/scene.blend) with the frozen WP3b
cameras imported as basis-01 (data/wp3b/prada/cameras.json + data/reference/prada photos):

* compare_to_photos reproduces the basis: every reference's IoU within 0.001 of cameras.json and contour mean within
  0.02 mm (stop rule: reference-01 within 0.001); the ROI metrics are bit-equal to cameras.native_metrics;
* render_views: 8 canonical + 6 reference views x native + clay = 28 non-empty PNGs (alpha coverage 0.5-90 %) on
  EEVEE in <= 60 s, fingerprint unchanged, contact sheets <= 2048 px; normal, silhouette and parts passes;
* Prada chain: Blender's silhouette at the six frozen cameras vs the host raster of the head export: IoU >= 0.998;
* a crop camera renders the full render's region (silhouette edges within 1 px; k = 1 and k = 2);
* the forced Cycles fallback is recorded per image, and refused outside a --selftest worker;
* pick_surface_point lands within 0.05 mm of the host raster's surface point, on the right object;
* a 3 mm temple splay drops the top IoU by >= 0.05 with model_bytes_identical false; after the restore the metrics
  equal the first compare bit for bit; E_NO_BASIS without a basis;
* output paths are confined to <job>/work (a junction out of it is refused); reads add no checkpoint;
* WP6_FACETS §3.3 on the WP6 v2 pose basis: the photo-matched compare (studio_dome_v1) with its default photos, the
  per-channel white-balanced gain, the text (px/mm of the image shown, the points_px frame, outer-edge offsets beside
  the start model's, the CURRENT compare_id even on a cache hit), no native row for the variant photo, refusals of bad
  points_px recorded; photo_env with a given environment (float renders, gains keyed by the environment, the
  environment in the cache key);
* photo sets in both paths: "default" = the target photos with a posed temple ROI >= 40k px, "all" = every target
  photo (never the variant), None = "all" for photo_v1 and "default" for a matched rig;
* every failure is journaled: malformed hinge_z_mm / gains / boxes / env are E_ARGS, an unexpected exception inside
  either path is E_COMPARE, each with its journal record.
Needs Blender 5.2 and the Prada data (skipped, or failed under G3D_REQUIRE_DATA=1)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.agent.worker_client import WorkerClient, WorkerError
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import cameras, fitmesh
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.camera import Camera

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

G3D = Path(__file__).resolve().parents[1]
PRADA_SCENE = G3D.parent / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
CAMERAS_JSON = G3D / "data" / "wp3b" / "prada" / "cameras.json"
PHOTOS = G3D / "data" / "reference" / "prada"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"
SPLAY = '''
import numpy as np
for ob in bpy.data.objects:
    if ob.type != "MESH" or ob.get("partRole") != "temple":
        continue
    me = ob.data
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    w = np.array(ob.matrix_world)
    wc = co.reshape(-1, 3) @ w[:3, :3].T + w[:3, 3]
    sel = wc[:, 2] < -20.0
    wc[sel, 0] += np.sign(wc[sel, 0]) * 3.0
    inv = np.linalg.inv(w)
    me.vertices.foreach_set("co", (wc @ inv[:3, :3].T + inv[:3, 3]).ravel())
    me.update()
'''


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    need(PRADA_SCENE.is_file(), f"Prada scene missing: {PRADA_SCENE}")
    need(CAMERAS_JSON.is_file() and PHOTOS.is_dir(), "WP3b Prada cameras / reference photos missing")
    root = tmp_path_factory.mktemp("wp2seeing")
    seed = root / "seed.blend"
    shutil.copy2(PRADA_SCENE, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    # This original Blender scene exports unsuffixed names. Only the historical standalone GLB needs the
    # legacy_prada .001 camera adapter used by test_wp2_compare; these exact camera names already match.
    doc = json.loads(CAMERAS_JSON.read_text(encoding="utf-8"))
    basis = C.import_cameras_json(root, CAMERAS_JSON, {n: PHOTOS / n for n in doc["images"]},
                                  reason="WP3b frozen Prada basis (2026-10-05)")
    s = BlenderSession(root, seed_blend=seed, selftest=True, calls_dir=root / "tools")
    s.start()
    yield {"root": root, "session": s, "seeing": Seeing(s), "basis": basis, "doc": doc}
    s.close()
    shutil.rmtree(root / "checkpoints", onerror=_rw)


def head_mesh(job, skip=(), frame=None):
    see = job["seeing"]
    exp = see.export_head()
    assert exp["status"] == "exported", exp
    return see.fit_mesh(exp["path"], exp["sha256"], skip=skip, frame=frame or job["basis"].frame), exp


# ------------------------------------------------------------------------------------------------ compare
def test_compare_reproduces_the_frozen_basis(job):
    t0 = time.perf_counter()
    mesh, _ = head_mesh(job)
    assert set(job["basis"].front_parts) <= set(mesh.node_names)
    r = job["seeing"].compare_to_photos(passes=())
    assert r["status"] == "ok", r
    rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
    assert rep["basis"]["id"] == "basis-01" and rep["previous"] is None and rep["model_bytes_identical"] is None
    assert list(rep["refs"]) == [f"reference-0{i}" for i in range(1, 7)]       # top 03 and rear 06 are in
    rows = {}
    for name, rec in job["doc"]["images"].items():
        m = rep["refs"][C.ref_id(name)]["all"]
        rows[name] = (m["iou"] - rec["iou"], m["contour_mean_mm"] - rec["contour_mean_mm"])
        assert abs(m["iou"] - rec["iou"]) <= 0.001, (name, m["iou"], rec["iou"])
        assert abs(m["contour_mean_mm"] - rec["contour_mean_mm"]) <= 0.02, name
        assert m["contour_p90_px"] == pytest.approx(rec["contour_p90_px"], abs=1e-9)
    assert abs(rows["reference-01.webp"][0]) <= 0.001                            # the stage B stop rule
    print("basis reproduction (dIoU, dcontour mm):", rows, f"{time.perf_counter() - t0:.1f} s")


def test_roi_metrics_are_bit_equal_to_native_metrics(job):
    mesh, _exp = head_mesh(job, skip=job["basis"].skip)
    b = job["basis"]
    for rid in b.refs:
        rec, masks = C.reference_metrics(mesh, b, rid, front=False)
        nm = cameras.native_metrics(mesh.scene(), b.camera(rid), b.matte(rid), b.roi(rid))
        assert rec["all"]["iou"] == nm["iou"] and rec["all"]["contour_mean_px"] == nm["contour_mean_px"], rid
        assert rec["all"]["contour_p95_px"] == nm["contour_p95_px"], rid
        assert rec["all"]["model_only_px"] == nm["render_only_px"] and rec["all"]["photo_only_px"] == \
            nm["photo_only_px"], rid
        x0, y0, x1, y1 = b.roi(rid)
        assert (masks["mask"][y0:y1, x0:x1] == nm["mask"]).all(), rid


def test_compare_sheets_crops_and_report(job):
    r = job["seeing"].compare_to_photos(refs=["reference-03", 6], crops=[
        {"ref": "reference-03", "part": "Temple left"}, {"ref": "reference-06", "box_px": [600, 150, 900, 350]}])
    assert r["status"] == "ok", r
    assert list(r["refs"]) == ["reference-03", "reference-06"]
    for rid, m in r["refs"].items():
        im = Image.open(m["sheet"])
        assert max(im.size) <= 2048 and min(im.size) > 200, (rid, im.size)
    assert len(r["crops"]) == 2 and r["crops"][0]["parts"] and r["crops"][1]["box_px"] == [600, 150, 900, 350]
    for c in r["crops"]:
        assert Path(c["sheet"]).is_file()
    rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
    assert rep["engine"] == "BLENDER_EEVEE" and rep["model_bytes_identical"] is True
    assert rep["geometry_identical"] is True and rep["refs"]["reference-03"]["renders"]["native"]["path"]
    assert set(rep["refs"]["reference-03"]) >= {"all", "front", "delta", "px_per_mm", "roi", "basis_fit"}
    assert rep["refs"]["reference-03"]["delta"]["all"]["iou"] == 0.0


def test_no_basis_is_e_no_basis(job, tmp_path):
    other = Seeing(job["session"])
    other.root = tmp_path
    r = other.compare_to_photos()
    assert r["status"] == "error" and r["code"] == "E_NO_BASIS" and "fit_cameras" in r["message"]
    r = other.render_views(["reference-01"])
    assert r["code"] == "E_NO_BASIS"


# ------------------------------------------------------------------------------------------------ renders
def test_render_views_28_pngs_on_eevee_fingerprint_neutral(job):
    s, see = job["session"], job["seeing"]
    head0, n0 = s.head, len(s.index.checkpoints())
    t0 = time.perf_counter()
    r = see.render_views("all", passes=("native", "clay"), size_px=1024)
    wall = time.perf_counter() - t0
    assert r["status"] == "ok", r
    assert len(r["views"]) == 14 and r["engine"] == "BLENDER_EEVEE" and r["fallback"] is None
    m = json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))
    pngs = [f for v in m["views"].values() for f in v["files"].values()]
    assert len(pngs) == 28
    for f in pngs:
        a = np.asarray(Image.open(f["path"]).convert("RGBA"))[..., 3]
        cover = float((a > 127).mean())
        assert 0.005 <= cover <= 0.90, (f["path"], cover)
        assert f["engine"] == "BLENDER_EEVEE" and f["sha256"]
    assert m["worker_seconds"] <= 60.0, m["worker_seconds"]
    assert len(r["sheets"]) == 3 and all(max(Image.open(p).size) <= 2048 for p in r["sheets"])
    assert s.fingerprint() == head0["fingerprint"] and len(s.index.checkpoints()) == n0
    assert (m["gpu"] or {}).get("renderer"), m["gpu"]                        # EEVEE ran on a real GPU context
    assert (m["views"]["top"]["camera"]["pitch"], m["views"]["rear"]["camera"]["yaw"]) == (90.0, 180.0)
    print(f"render_views all: worker {m['worker_seconds']:.1f} s, wall {wall:.1f} s, gpu {m['gpu']}")


def test_prada_chain_blender_silhouette_matches_the_host_raster(job):
    r = job["seeing"].render_views("reference", passes=("silhouette",), size_px=2048)
    assert r["status"] == "ok", r
    mesh, _exp = head_mesh(job, skip=())
    b = job["basis"]
    ious = {}
    for rid in b.refs:
        v = r["views"][rid]
        assert tuple(v["shape"]) == b.shape(rid)                               # native size
        sil = np.asarray(Image.open(v["files"]["silhouette"])) > 127
        ras = mesh.scene().render(b.camera(rid), b.shape(rid), 1)["mask"]
        ious[rid] = float((sil & ras).sum() / (sil | ras).sum())
    assert min(ious.values()) >= 0.998, ious
    print("Prada chain IoU Blender vs host raster:", {k: round(v, 5) for k, v in ious.items()})


def _edge_distance(a: np.ndarray, b: np.ndarray, border: int = 3) -> float:
    """Max over both directions of the distance from one silhouette edge to the other (px), away from the border."""
    def edge(m):
        return m & ~ndimage.binary_erosion(m)
    ea, eb = edge(a), edge(b)
    keep = np.zeros_like(a)
    keep[border:-border, border:-border] = True
    da = ndimage.distance_transform_edt(~eb)[ea & keep]
    db = ndimage.distance_transform_edt(~ea)[eb & keep]
    return float(max(da.max(initial=0), db.max(initial=0)))


def test_a_crop_camera_renders_the_full_render_region(job):
    s, b = job["session"], job["basis"]
    rid = "reference-03"
    cam, (H, W) = b.camera(rid), b.shape(rid)
    mesh, _exp = head_mesh(job, skip=())
    ys, xs = np.nonzero(mesh.scene().render(cam, (H, W), 1)["mask"])
    cw, ch = 420, 300
    x0, y0 = int(xs.min()) - 20, int(ys.max()) + 20 - ch                     # the front's left end and hinge
    views = [{"name": "full", "camera": BC.to_blender(cam, b.frame, (0, 0, 0), (H, W)), "passes": ["clay"]}]
    for k in (1, 2):
        c = BC.crop_camera(cam, x0, y0, k)
        views.append({"name": f"crop{k}", "camera": BC.to_blender(c, b.frame, (0, 0, 0), (ch * k, cw * k)),
                      "passes": ["clay"]})
    r = s.call_read("test", "render", {"out_dir": str(job["root"] / "work" / "renders" / "crop"), "views": views,
                                       "engine": "eevee"}, deadline_s=300, call_id="crop-test")
    assert r["status"] == "ok", r
    files = {v["name"]: v["files"]["clay"]["path"] for v in r["result"]["views"]}
    full = np.asarray(Image.open(files["full"]).convert("RGBA"), float)[..., 3] / 255
    region = full[y0:y0 + ch, x0:x0 + cw]
    c1 = np.asarray(Image.open(files["crop1"]).convert("RGBA"), float)[..., 3] / 255
    c2 = np.asarray(Image.open(files["crop2"]).convert("RGBA"), float)[..., 3] / 255
    c2_down = c2.reshape(ch, 2, cw, 2).mean(axis=(1, 3))
    d1, d2 = _edge_distance(c1 > 0.5, region > 0.5), _edge_distance(c2_down > 0.5, region > 0.5)
    inner = (slice(3, -3), slice(3, -3))
    mad1 = float(np.abs(c1 - region)[inner].mean())
    xor1 = int(((c1 > 0.5) ^ (region > 0.5))[inner].sum())
    assert region[inner].mean() > 0.05                                         # the crop holds part of the model
    assert d1 <= 1.0 and d2 <= 1.0, (d1, d2)
    assert mad1 < 0.01, mad1
    print(f"crop vs full region: edge distance k=1 {d1:.2f} px, k=2 {d2:.2f} px; k=1 mean |alpha diff| {mad1:.2e}, "
          f"{xor1} of {(c1[inner] > 0.5).sum()} silhouette px differ")


def test_normal_silhouette_and_parts_passes(job):
    r = job["seeing"].render_views(["front"], passes=("normal", "silhouette", "parts"), size_px=768)
    assert r["status"] == "ok", r
    m = json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))
    v = m["views"]["front"]
    nrm = np.asarray(Image.open(v["files"]["normal"]["path"]).convert("RGBA"))
    sil = np.asarray(Image.open(v["files"]["silhouette"]["path"])) > 127
    parts = np.asarray(Image.open(v["files"]["parts"]["path"]).convert("RGBA"))[..., 3] > 0
    iou = float((sil & parts).sum() / (sil | parts).sum())
    assert iou >= 0.99, iou                                    # canonical camera: Blender vs host raster agree
    covered = nrm[..., 3] > 250
    assert np.median(nrm[covered][:, 2]) > 200                 # front view: normals face +Z (blue = n.z x 0.5 + 0.5)
    legend = v["files"]["parts"]["legend"]
    assert any(n.startswith("Lens left") for n in legend) and any(n.startswith("Front") for n in legend)
    assert all(c.startswith("#") and len(c) == 7 for c in legend.values())


def test_parts_only_creates_output_without_a_blender_render(job):
    r = job["seeing"].render_views(["front"], passes=("parts",), size_px=256)
    assert r["status"] == "ok", r
    manifest = json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))
    assert manifest["engine"] is None and manifest["worker_seconds"] == 0
    assert manifest["fingerprint"] == job["session"].head["fingerprint"]
    image = np.asarray(Image.open(r["views"]["front"]["files"]["parts"]).convert("RGBA"))
    assert image[..., 3].max() > 0
    assert r["sheets"] and all(Path(p).is_file() for p in r["sheets"])


@pytest.mark.parametrize("tool, folder", [("render_views", "renders"), ("compare_to_photos", "compare")])
def test_host_only_outputs_refuse_junction_escape(job, tmp_path, tool, folder):
    import _winapi
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("sentinel", encoding="utf-8")
    parent = job["root"] / "work" / folder
    parent.mkdir(parents=True, exist_ok=True)
    link = parent / "escape-host"
    _winapi.CreateJunction(str(outside), str(link))
    try:
        if tool == "render_views":
            r = job["seeing"].render_views(["front"], passes=("parts",), size_px=256, call_id="escape-host")
        else:
            r = job["seeing"].compare_to_photos(passes=(), call_id="escape-host")
        assert r["status"] == "error" and r["code"] == "E_PATH", r
        assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    finally:
        os.rmdir(link)  # Remove only the junction, preserving its target.
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "sentinel"


def test_forced_cycles_fallback_is_recorded_and_refused_outside_selftest(job, tmp_path):
    r = job["seeing"].render_views(["front", "left"], passes=("native", "clay"), size_px=256,
                                   simulate_eevee_failure=True)
    assert r["status"] == "ok", r
    assert r["engine"] == "CYCLES" and "simulated EEVEE failure" in r["fallback"]["reason"]
    m = json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))
    engines = [f["engine"] for vv in m["views"].values() for f in vv["files"].values()]
    assert engines == ["CYCLES"] * 4
    assert job["session"].fingerprint() == job["session"].head["fingerprint"]
    plain = WorkerClient(tmp_path).start()
    try:
        spec = m["views"]["front"]["blender"]
        with pytest.raises(WorkerError, match="selftest"):
            plain.call("render", {"out_dir": str(tmp_path / "work" / "r"), "simulate_eevee_failure": True,
                                  "views": [{"name": "f", "camera": spec, "passes": ["clay"]}]}, deadline_s=120)
    finally:
        plain.close()


def test_pick_lands_on_the_host_raster_surface_point(job):
    see = job["seeing"]
    r = see.render_views(["front", "angled_left"], passes=("clay",), size_px=512)
    exp = see.export_head()
    rng = np.random.default_rng(5)
    worst = 0.0
    for view in ("front", "angled_left"):
        mv = json.loads(Path(r["manifest"]).read_text(encoding="utf-8"))["views"][view]
        cam, frame, shape = Camera.from_dict(mv["camera"]), C.NormFrame.from_dict(mv["frame"]), tuple(mv["shape"])
        mesh = see.fit_mesh(exp["path"], exp["sha256"], frame=frame)
        ras = mesh.scene().render(cam, shape, 1, want_points=True)
        part = np.full(shape, -1)
        part[ras["mask"]] = mesh.part[ras["face_id"][ras["mask"]]]
        same = ras["mask"] & (ndimage.minimum_filter(part, 5) == ndimage.maximum_filter(part, 5))
        ys, xs = np.nonzero(same)
        pick = rng.choice(len(xs), 32, replace=False)
        uv = np.column_stack([xs[pick], ys[pick]]).astype(float)
        p = see.pick_surface_point(view, uv.tolist(), manifest=r["manifest"])
        assert p["status"] == "ok", p
        for (u, v), h in zip(uv.astype(int), p["hits"]):
            assert h["hit"], (view, u, v)
            want = ras["points"][v, u] + np.asarray(mv["origin_mm"])
            err = float(np.linalg.norm(np.asarray(h["point_mm"]) - want))
            worst = max(worst, err)
            assert err <= 0.05, (view, h, want)
            assert h["object"] == mesh.node_names[part[v, u]], (h["object"], mesh.node_names[part[v, u]])
            np.testing.assert_allclose(h["asset_mm"], np.asarray(h["point_mm"]) - np.asarray(mv["origin_mm"]))
    print(f"Prada pick: worst {worst:.5f} mm over 64 picks")
    bad = see.pick_surface_point("front", [[-5, 3]], manifest=r["manifest"])
    assert bad["status"] == "error" and bad["code"] == "E_ARGS"


# ------------------------------------------------------------------------------------------------ edit, compare, restore
def test_splay_drops_the_top_iou_and_the_restore_is_bit_identical(job):
    s, see = job["session"], job["seeing"]
    first = see.compare_to_photos(passes=())
    assert first["status"] == "ok"
    c0 = s.head["id"]
    e = s.execute(SPLAY, soft_timeout_s=60, label="splay temples 3 mm")
    assert e["status"] == "ok" and e["checkpoint"] != c0, e
    splayed = see.compare_to_photos(passes=())
    assert splayed["model_bytes_identical"] is False and splayed["geometry_identical"] is False
    d_top = splayed["refs"]["reference-03"]["delta"]["iou"]
    assert d_top <= -0.05, splayed["refs"]["reference-03"]
    assert splayed["refs"]["reference-03"]["iou"] == pytest.approx(first["refs"]["reference-03"]["iou"] + d_top,
                                                                   abs=1e-4)
    rr = s.restore_checkpoint(c0)
    assert rr["status"] == "ok" and rr["changed"] is True
    back = see.compare_to_photos(passes=())
    r1 = json.loads(Path(first["report"]).read_text(encoding="utf-8"))
    r3 = json.loads(Path(back["report"]).read_text(encoding="utf-8"))
    assert r3["glb"]["sha256"] == r1["glb"]["sha256"] and r3["geometry"] == r1["geometry"]
    for rid in r1["refs"]:
        assert r3["refs"][rid]["all"] == r1["refs"][rid]["all"], rid
        assert r3["refs"][rid]["front"] == r1["refs"][rid]["front"], rid
    print("splay: top IoU delta", round(d_top, 4), {k: v["delta"]["iou"] for k, v in splayed["refs"].items()})


# ------------------------------------------------------------------------------------------------ confinement, receipts
def test_output_paths_are_confined_to_the_work_folder(job, tmp_path):
    import _winapi
    s, root = job["session"], job["root"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("sentinel", encoding="utf-8")
    (root / "work" / "renders").mkdir(parents=True, exist_ok=True)
    link = root / "work" / "renders" / "evil"
    _winapi.CreateJunction(str(outside), str(link))
    spec = BC.to_blender(Camera(0, 0, 0, 0, 500, 100, 100), job["basis"].frame, (0, 0, 0), (200, 200))
    try:
        for out_dir in (link / "x", outside, root / "checkpoints"):
            with pytest.raises(WorkerError, match="PermissionError"):
                s.worker.call("render", {"out_dir": str(out_dir), "views": [
                    {"name": "f", "camera": spec, "passes": ["clay"]}]}, deadline_s=60)
        with pytest.raises(WorkerError, match="PermissionError"):
            s.worker.call("export_glb", {"path": str(root / "work" / "renders" / "x.glb")}, deadline_s=60)
        with pytest.raises(WorkerError, match="PermissionError"):
            s.worker.call("export_glb", {"path": str(link / "x.glb")}, deadline_s=60)
        assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    finally:
        os.rmdir(link)                                          # removes the junction only
    assert (outside / "keep.txt").is_file()


def test_reads_add_no_checkpoint_and_are_journaled(job):
    s = job["session"]
    assert s.fingerprint() == s.head["fingerprint"]
    kinds = [r["kind"] for r in s.index.checkpoints()]
    assert kinds.count("save") == 1 and kinds.count("restore") == 1           # only the splay and its restore
    tools = sorted({p.name.split("-", 1)[1] for p in (job["root"] / "tools").iterdir()})
    assert {"render_views", "compare_to_photos", "pick_surface_point", "execute_blender_code"} <= set(tools)
    assert not s.defects, s.defects


# ------------------------------------------------------------------------------------------------ WP6 v2 photo-matched
WP6_BASIS = G3D / "data" / "wp6v2" / "basis" / "cameras.json"


def test_matched_compare_needs_a_pose_basis(job):
    r = job["seeing"].compare_to_photos(rig="studio_dome_v1", basis="basis-01")
    assert r["status"] == "error" and r["code"] == "E_NO_POSE", r
    r = job["seeing"].compare_to_photos(rig="studio_dome_v1", basis="basis-01", crop="zoom")
    assert r["status"] == "error" and r["code"] == "E_ARGS"


@pytest.fixture(scope="module")
def pose_basis(job):
    need(WP6_BASIS.is_file(), "the WP6 v2 basis is missing (tests/proofs/wp6v2_basis.py makes it)")
    doc = json.loads(WP6_BASIS.read_text(encoding="utf-8"))
    return C.import_cameras_json(job["root"], WP6_BASIS, {n: Path(r["image"]) for n, r in doc["images"].items()},
                                 reason="WP6 v2 pose basis")


def test_matched_compare_on_the_pose_basis(job, pose_basis):
    s, see, b = job["session"], job["seeing"], pose_basis
    head0, n0 = s.head, len(s.index.checkpoints())
    r = see.compare_to_photos("default", passes=("native_linear", "gloss_black"), rig="studio_dome_v1",
                              basis=b.id,
                              points_px={"reference-05": [[600, 330], [5, 5]]})
    assert r["status"] == "ok", r
    assert {"reference-01", "reference-05", "reference-02"} <= set(r["photos"]) and "reference-04" not in \
        r["photos"] and "black-side" not in r["photos"]                    # default: target, temple >= 40k px
    rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
    tg = rep["gains"]["colourways"]["target"]
    assert tg["photo"] == "reference-04" and tg["region"] == "front acetate body"
    assert len(tg["native_linear"]["gain"]) == 3 and tg["native_linear"]["gain"][2] > tg["native_linear"]["gain"][0]
    assert rep["gains"]["colourways"]["variant"]["dark"] and rep["gains"]["gloss_black"]["stretch_from"].startswith(
        "black-side")
    for rid, p in r["photos"].items():
        im = Image.open(p["image"])
        assert max(im.size) <= 1024 and set(p["rows"]) == {"photo", "native_linear", "overlay", "gloss_black"}
        rec = rep["photos"][rid]
        assert rec["crop"]["k"] <= 1.0 and 0.5 < rec["temple_iou"] <= 1.0 and rec["px_per_mm"] > 3
        assert f"compare_id {r['compare_id']}." in p["text"] and "camera error" in p["text"]
        assert f"{rec['px_per_mm'] * rec['crop']['k']:.2f} px/mm in this image" in p["text"]
        assert "points_px are native photo pixels" in p["text"]
        assert rec["creases"]["ridge"] > 0
    assert "outer edge" in r["photos"]["reference-03"]["text"] and "start model in brackets" in \
        r["photos"]["reference-03"]["text"]
    start = json.loads((job["root"] / "work" / "compare" / f"start-{b.id}.json").read_text(encoding="utf-8"))
    assert start["checkpoint"] == head0["id"] and any(k.startswith("reference-03@") for k in start["offsets"])
    pts = r["photos"]["reference-05"]["points"]
    assert pts[0]["hit"] and pts[0]["side"] == 1 and 0 < pts[0]["d_mm"] < 140 and not pts[1]["hit"]
    again = see.compare_to_photos(None, passes=("native_linear", "gloss_black"), rig="studio_dome_v1", basis=b.id)
    assert all(p["cached"] for p in again["photos"].values())
    assert {k: p["image"] for k, p in again["photos"].items()} == {k: p["image"] for k, p in r["photos"].items()}
    for p in again["photos"].values():                                     # a cached record names the NEW compare
        assert f"compare_id {again['compare_id']}." in p["text"] and r["compare_id"] not in p["text"]
    assert s.fingerprint() == head0["fingerprint"] and len(s.index.checkpoints()) == n0


def test_matched_compare_variant_photo_and_recorded_errors(job, pose_basis):
    see, b = job["seeing"], pose_basis
    r = see.compare_to_photos(["black-side"], passes=("native_linear",), rig="studio_dome_v1", basis=b.id)
    assert r["status"] == "ok", r
    p = r["photos"]["black-side"]
    assert set(p["rows"]) == {"photo", "overlay", "gloss_black"} and "no native row" in p["text"]
    calls = len(list((job["root"] / "tools").iterdir()))
    bad = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", basis=b.id,
                                points_px={"reference-04": [[10, 10]]})
    assert bad["status"] == "error" and bad["code"] == "E_ARGS" and "reference-04" in bad["message"]
    bad = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", basis=b.id,
                                points_px={"reference-05": [[10, "x"]]})
    assert bad["status"] == "error" and bad["code"] == "E_ARGS"
    bad = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", basis=b.id,
                                points_px={"reference-05": [[1e6, 10]]})
    assert bad["status"] == "error" and bad["code"] == "E_ARGS" and "inside" in bad["message"]
    assert len(list((job["root"] / "tools").iterdir())) >= calls + 3             # every refusal is journaled


def test_photo_env_compare_with_a_given_environment(job, pose_basis):
    from glasses3d.g3d_host import lighting as L
    see, b = job["seeing"], pose_basis
    env = {"weights": [0.6] * L.N_LOBES}
    env["weights"][L.LOBES.index("front+12")] = 1.4
    r = see.compare_to_photos(["reference-05"], passes=("native_linear", "gloss_black"), rig="photo_env",
                              basis=b.id, env=env)
    assert r["status"] == "ok", r
    rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
    assert rep["rig"] == "photo_env" and rep["environment"]["weights"] == env["weights"]
    rec = rep["photos"]["reference-05"]
    assert rec["renders"]["native_linear"]["path"].endswith(".npz") and rec["render"]["linear_format"] == "float"
    assert rec["render"]["rig_spec"]["sectors"] == ["front", "side", "back"]
    key = f"photo_env:{L.weights_sha(env['weights'])}"
    gains = json.loads((job["root"] / "work" / "compare" / f"gains-{b.id}.json").read_text(encoding="utf-8"))
    assert key in gains and gains[key]["env_sha"] == L.weights_sha(env["weights"])
    again = see.compare_to_photos(["reference-05"], passes=("native_linear", "gloss_black"), rig="photo_env",
                                  basis=b.id, env=env)
    assert again["photos"]["reference-05"]["cached"]
    env2 = {"weights": [0.6] * L.N_LOBES}
    other = see.compare_to_photos(["reference-05"], passes=("native_linear", "gloss_black"), rig="photo_env",
                                  basis=b.id, env=env2)
    assert not other["photos"]["reference-05"]["cached"]                     # the environment is in the cache key


def test_photo_sets_default_and_all_in_both_paths(job, pose_basis):
    from glasses3d.g3d_host import pose as PO
    see, b = job["seeing"], pose_basis
    mesh, exp = head_mesh(job, skip=b.skip, frame=b.frame)
    default, geo = Seeing.default_refs(b, mesh, PO.part_roles(exp["path"]))
    targets = [f"reference-0{i}" for i in range(1, 7)]
    assert [r for r in b.refs if b.colourway(r) == "target"] == targets and b.colourway("black-side") == "variant"
    assert set(geo) == set(targets) and default == [r for r in targets if geo[r]["temple_px"] >= 40_000]
    assert "reference-04" not in default and "reference-03" in default
    assert Seeing._ref_ids(b, "all") == targets and Seeing._ref_ids(b, None) == targets
    r = see.compare_to_photos("default", passes=(), basis=b.id)                          # photo_v1
    assert r["status"] == "ok" and list(r["refs"]) == default, r
    for refs in ("all", None):
        r = see.compare_to_photos(refs, passes=(), basis=b.id)
        assert r["status"] == "ok" and list(r["refs"]) == targets, r
    # matched: the photo set is resolved before any render; a points_px refusal names it
    for refs, want in (("all", targets), (None, default), ("default", default)):
        r = see.compare_to_photos(refs, rig="studio_dome_v1", basis=b.id, points_px={"black-side": [[5, 5]]})
        assert r["status"] == "error" and r["code"] == "E_ARGS" and f"({want})" in r["message"], (refs, r)


def test_every_compare_failure_is_journaled(job, pose_basis, monkeypatch):
    see, b = job["seeing"], pose_basis
    tools = job["root"] / "tools"

    def n_calls():
        return len(list(tools.iterdir()))
    bad_args = [{"hinge_z_mm": "x"}, {"hinge_z_mm": float("nan")}, {"gains": {"colourways": {}}},
                {"boxes": {"reference-05": {"box": [0, 0, 10], "k": 1.0}}},
                {"boxes": {"reference-05": {"box": [0, 0, 10, 10], "k": 2.0}}}, {"boxes": [1, 2]},
                {"env": {"weights": [1.0] * 3}}]
    for kw in bad_args:
        n0 = n_calls()
        rig = "photo_env" if "env" in kw else "studio_dome_v1"
        r = see.compare_to_photos(["reference-05"], rig=rig, basis=b.id, **kw)
        assert r["status"] == "error" and r["code"] == "E_ARGS", (kw, r)
        assert n_calls() == n0 + 1, kw
    r = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", basis=b.id, env={"weights": [1.0] * 19})
    assert r["code"] == "E_ARGS" and "takes none" in r["message"]

    def boom(*_a, **_k):
        raise KeyError("injected")
    monkeypatch.setattr(Seeing, "_matched_body", boom)
    n0 = n_calls()
    r = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", basis=b.id)
    assert r["status"] == "error" and r["code"] == "E_COMPARE" and "injected" in r["message"] and n_calls() == n0 + 1
    monkeypatch.setattr(C, "reference_metrics", boom)
    n0 = n_calls()
    r = see.compare_to_photos(["reference-05"], passes=(), basis=b.id)                     # photo_v1
    assert r["status"] == "error" and r["code"] == "E_COMPARE" and "injected" in r["message"] and n_calls() == n0 + 1
