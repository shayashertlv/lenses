"""g3d/render.py WP6 v2 additions (WP6_FACETS §3.2) in a real worker on a copy of the Prada scene.

* photo_v1 stays the default and its pixels are unchanged: the native / clay / normal passes at the six reference
  cameras match the renders captured with the pre-change code (data/wp6v2/golden_photo_v1, HEAD 62b25e4) on decoded
  pixels, within EEVEE's own run-to-run noise (two captures differed in <= 14 px, max 27 levels);
* studio_dome_v1 renders native_linear and gloss_black as 16-bit Raw PNGs, records its frozen spec, aims its key at
  key_elevation_deg on each camera's azimuth, picks the acetate bodies by role and geometry and darkens only them in
  gloss_black (the lens keeps its material);
* a pose opens the temple-role copies with the host's sign: the Blender silhouette of a posed top view matches the
  host raster of g3d_host/pose.posed(), not the opposite opening;
* mesh_overrides swaps a copy's mesh without touching the scene; the user scene fingerprint is unchanged after
  every render;
* refusals: a malformed pose, an unknown rig, an override of an unknown object or mesh, a rig_spec_override outside
  a --selftest worker;
* the worker's acetate bodies are the host's (one body rule, g3d_host/pose) and the samples used are recorded;
* photo_env: the lobe layout is g3d_host/lighting.py's, the float output is an unclipped .npz, the sectors turn with
  the camera (camera-side lobes light the front at 04 more than three times the back lobes), the render scales with
  the weights; refusals for missing / malformed weights, weights on another rig, an unknown linear_format.
Needs Blender 5.2 and the Prada data (skipped, or failed under G3D_REQUIRE_DATA=1)."""
from __future__ import annotations

import json
import math
import os
import shutil
import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent.worker_client import WorkerClient, WorkerError
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import Camera, NormFrame

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

G3D = Path(__file__).resolve().parents[1]
PRADA_SCENE = G3D.parent / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
GOLDEN = G3D / "data" / "wp6v2" / "golden_photo_v1" / "golden_a.json"
BASIS = G3D / "data" / "wp6v2" / "basis" / "cameras.json"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"
LIGHT = {"center": [0, -5, -70], "radius": 90.0}


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    need(PRADA_SCENE.is_file(), f"Prada scene missing: {PRADA_SCENE}")
    need(BASIS.is_file(), "the WP6 v2 basis is missing (tests/proofs/wp6v2_basis.py makes it)")
    root = tmp_path_factory.mktemp("render_dome")
    seed = root / "seed.blend"
    shutil.copy2(PRADA_SCENE, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    (root / "work").mkdir()
    w = WorkerClient(root, start_blend=seed, selftest=True).start()
    doc = json.loads(BASIS.read_text(encoding="utf-8"))
    yield {"root": root, "w": w, "doc": doc, "frame": NormFrame.from_dict(doc["model"]["frame"])}
    w.close()


def view(job, ref: str, k: float, **extra) -> dict:
    rec = next(r for n, r in job["doc"]["images"].items() if Path(n).stem == ref)
    H, W = rec["shape"]
    cam = BC.crop_camera(Camera.from_dict(rec["camera"]), 0, 0, k)
    shape = (round(H * k), round(W * k))
    return {"name": ref, "camera": BC.to_blender(cam, job["frame"], (0, 0, 0), shape), "cam": cam, "shape": shape,
            "pose": {s: {"theta_deg": rec["theta_deg"][s], "axis_xz_mm": rec["hinge_axis_xz_mm"][s]}
                     for s in ("+1", "-1")}, **extra}


def render(job, views, tag, **params) -> dict:
    vs = [{k: v for k, v in x.items() if k not in ("cam", "shape")} for x in views]
    r = job["w"].call("render", {"out_dir": str(job["root"] / "work" / tag), "views": vs, "engine": "eevee",
                                 "light_frame": LIGHT, **params}, deadline_s=300)
    assert r["unchanged"] and r["fingerprint_before"] == r["fingerprint_after"]
    return r


def head_mesh(job):
    r = job["w"].call("export_glb", {"path": str(job["root"] / "work" / "exports" / "head.glb")}, deadline_s=120)
    assert r["status"] == "exported", r
    return fitmesh.load_fit_mesh(r["path"], frame=job["frame"]), PO.part_roles(r["path"])


def test_photo_v1_pixels_are_unchanged(job):
    need(GOLDEN.is_file(), "golden photo_v1 renders missing (data/wp6v2/golden_photo_v1)")
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    r = render(job, g["views"], "golden")
    assert r["rig"] == "photo_v1" and not r["rig_overridden"]
    worst = {}
    for v in r["views"]:
        for ps, f in v["files"].items():
            a = np.asarray(Image.open(f["path"])).astype(int)
            b = np.asarray(Image.open(g["files"][f"{v['name']}/{ps}"]["path"])).astype(int)
            assert a.shape == b.shape and f["bit_depth"] == 8
            diff = np.abs(a - b)
            n = int((diff.max(axis=2) > 0).sum())
            worst[f"{v['name']}/{ps}"] = (n, int(diff.max()))
            assert n <= 0.0005 * diff.shape[0] * diff.shape[1] and diff.mean() < 0.01, (v["name"], ps, n)
    assert len(worst) == 18
    print("photo_v1 vs golden (differing px, max level):", worst)


def test_dome_passes_key_and_acetate(job):
    vs = [view(job, "reference-05", 0.5, passes=["native_linear", "gloss_black"]),
          view(job, "reference-04", 0.5, passes=["native_linear", "gloss_black"])]
    r = render(job, vs, "dome", rig="studio_dome_v1")
    spec = r["rig_spec"]
    assert r["rig"] == "studio_dome_v1" and spec["up_axis"] == "Y" and spec["raytracing"] is False
    assert spec["key_azimuth"] == "camera" and spec["samples"] == 16 and len(spec["ramp"]) >= 4
    assert sorted(r["acetate_bodies"]) == ["Front | continuous milled acetate", "Temple left | faceted sweep and ear bend",
                                           "Temple right | faceted sweep and ear bend"]
    assert r["gloss_black"] == {"base": 0.01, "roughness": 0.12, "coat": 0.6, "coat_roughness": 0.05, "ior": 1.49}
    mesh, roles = head_mesh(job)
    for v, rv in zip(vs, r["views"]):
        files = rv["files"]
        for ps in ("native_linear", "gloss_black"):
            assert files[ps]["bit_depth"] == 16 and files[ps]["view_transform"] == "Raw"
            import cv2
            assert cv2.imread(files[ps]["path"], cv2.IMREAD_UNCHANGED).dtype == np.uint16
        kd = np.asarray(files["native_linear"]["key_dir"])
        back = np.asarray(v["camera"]["matrix_world"])[:3, 2]
        h = back - np.array([0, 1, 0]) * back[1]
        assert math.degrees(math.asin(kd[1])) == pytest.approx(spec["key_elevation_deg"], abs=1e-4)
        assert np.dot(kd[[0, 2]], h[[0, 2]]) / np.linalg.norm(h[[0, 2]]) / np.linalg.norm(kd[[0, 2]]) > 0.9999
        mp = PO.posed(mesh, v["pose"], roles)
        ras = mp.scene().render(v["cam"], v["shape"], 1)
        part = np.full(v["shape"], -1)
        part[ras["mask"]] = mp.part[ras["face_id"][ras["mask"]]]
        nat, glo = C.read_linear_png(files["native_linear"]["path"]), C.read_linear_png(files["gloss_black"]["path"])
        body = C.erode_px(np.isin(part, [PO.front_body(mp, roles), PO.temple_body(mp, 1, roles),
                                         PO.temple_body(mp, -1, roles)]), 3)
        y_nat, y_glo = nat[0] @ C.LUMA, glo[0] @ C.LUMA
        assert np.median(y_glo[body]) < 0.35 * np.median(y_nat[body]), v["name"]
        if v["name"] == "reference-04":
            lens = C.erode_px(np.isin(part, [i for i, n in enumerate(mp.node_names)
                                             if roles.get(n) == "lens"]), 3)
            assert lens.sum() > 500
            assert abs(np.median(y_glo[lens]) - np.median(y_nat[lens])) < 0.25 * np.median(y_nat[lens]) + 1e-3


def test_pose_matches_the_host_sign(job):
    base = view(job, "reference-03", 0.5, passes=["clay"])
    opened = dict(base, name="open", pose=PO.make_pose({"+1": 10.0, "-1": 10.0}, (64.6, -10.1)))
    r = render(job, [opened], "pose", rig="studio_dome_v1")
    assert len(r["views"][0]["posed_objects"]) == 16                            # bodies, decals, knuckles, leaves, pins
    sil = np.asarray(Image.open(r["views"][0]["files"]["clay"]["path"]).convert("RGBA"))[..., 3] > 127
    mesh, roles = head_mesh(job)

    def iou(theta):
        m = PO.posed(mesh, PO.make_pose({"+1": theta, "-1": theta}, (64.6, -10.1)), roles).scene().render(
            base["cam"], base["shape"], 1)["mask"]
        return float((m & sil).sum() / (m | sil).sum())
    same, opposite = iou(10.0), iou(-10.0)
    assert same >= 0.99 and opposite < same - 0.02, (same, opposite)
    print(f"posed top view: IoU {same:.4f} with the host's +10 deg, {opposite:.4f} with -10 deg")


def test_mesh_overrides_swap_a_copy_only(job):
    s = job["w"].call("scene_summary", {}, deadline_s=60)
    right, left = "Temple right | faceted sweep and ear bend", "Temple left | faceted sweep and ear bend"
    mesh_left = s["objects"][left]["data"]
    v = view(job, "reference-05", 0.5, passes=["clay"])
    v2 = dict(v, name="override", mesh_overrides={right: mesh_left})
    r = render(job, [v, v2], "override")
    a = [np.asarray(Image.open(x["files"]["clay"]["path"]).convert("RGBA"))[..., 3] > 127 for x in r["views"]]
    assert r["views"][1]["mesh_overrides"] == {right: mesh_left} and r["views"][0]["mesh_overrides"] == {}
    assert (a[0] ^ a[1]).sum() > 0.02 * a[0].sum()                            # the near (+X) temple is gone
    assert job["w"].call("scene_summary", {}, deadline_s=60)["fingerprint"] == s["fingerprint"]


@pytest.mark.parametrize("params, match", [
    ({"views_pose": {"+2": {"theta_deg": 1, "axis_xz_mm": [64.6, -10.1]}}}, "'\\+1' or '-1'"),
    ({"views_pose": {"+1": {"theta_deg": 1, "axis_xz_mm": [-64.6, -10.1]}}}, "other side"),
    ({"views_pose": {"-1": {"theta_deg": 60, "axis_xz_mm": [-64.6, -10.1]}}}, "within"),
    ({"rig": "studio_dome_v9"}, "unknown rig"),
    ({"views_overrides": {"no such object": "x"}}, "not a rendered mesh"),
    ({"views_overrides": {"Temple right | faceted sweep and ear bend": "no such mesh"}}, "no mesh datablock"),
    ({"rig": "studio_dome_v1", "rig_spec_override": {"no_such_key": 1}}, "unknown keys"),
])
def test_refusals(job, params, match):
    v = view(job, "reference-05", 0.25, passes=["clay"])
    v.pop("cam"), v.pop("shape")
    v["pose"] = params.pop("views_pose", None)
    v["mesh_overrides"] = params.pop("views_overrides", None)
    with pytest.raises(WorkerError, match=match):
        job["w"].call("render", {"out_dir": str(job["root"] / "work" / "refuse"), "views": [v], **params},
                      deadline_s=120)


def test_rig_override_only_in_a_selftest_worker(job, tmp_path):
    shutil.copy2(job["root"] / "seed.blend", tmp_path / "seed.blend")
    plain = WorkerClient(tmp_path, start_blend=tmp_path / "seed.blend").start()
    try:
        v = view(job, "reference-05", 0.25, passes=["clay"])
        v.pop("cam"), v.pop("shape")
        with pytest.raises(WorkerError, match="selftest"):
            plain.call("render", {"out_dir": str(tmp_path / "work" / "r"), "views": [v], "rig": "studio_dome_v1",
                                  "rig_spec_override": {"key_irradiance": 2.0}}, deadline_s=120)
    finally:
        plain.close()
    v = view(job, "reference-05", 0.25, passes=["native_linear"])
    r = render(job, [v], "override_ok", rig="studio_dome_v1", rig_spec_override={"key_irradiance": 2.0})
    assert r["rig_overridden"] and r["rig_spec"]["key_irradiance"] == 2.0


# ------------------------------------------------------------------------------------------------ photo_env
def test_acetate_bodies_follow_one_rule_on_worker_and_host(job):
    v = view(job, "reference-05", 0.25, passes=["clay"])
    r = render(job, [v], "bodies", rig="studio_dome_v1")
    mesh, roles = head_mesh(job)
    host = [mesh.node_names[i] for i in (PO.front_body(mesh, roles), PO.temple_body(mesh, 1, roles),
                                         PO.temple_body(mesh, -1, roles))]
    assert sorted(r["acetate_bodies"]) == sorted(host)
    assert r["samples"] == 16 and r["rig_spec"]["samples"] == 16


def test_photo_env_float_output_lobes_and_orientation(job):
    from glasses3d.g3d_host import lighting as L
    n = L.N_LOBES

    def lobes(*names):
        w = np.zeros(n)
        for nm in names:
            w[L.LOBES.index(nm)] = 1.0
        return {"weights": w.tolist()}
    front = lobes("front-12", "front+12", "front+35")
    back = lobes("back-12", "back+12", "back+35")
    vs = [view(job, "reference-04", 0.4, name="front_lobes", passes=["native_linear", "gloss_black"], env=front),
          view(job, "reference-04", 0.4, name="back_lobes", passes=["native_linear"], env=back),
          view(job, "reference-04", 0.4, name="uniform", passes=["native_linear"], env={"weights": [1.0] * n}),
          view(job, "reference-04", 0.4, name="half", passes=["native_linear"], env={"weights": [0.5] * n})]
    r = render(job, vs, "env", rig="photo_env", linear_format="float")
    assert r["rig"] == "photo_env" and r["env_lobes"] == L.LOBES and r["linear_format"] == "float"
    assert tuple(r["rig_spec"]["elevation_knots_deg"]) == L.ELEVATION_KNOTS_DEG
    assert tuple(r["rig_spec"]["sectors"]) == L.SECTORS and tuple(r["rig_spec"]["floor_deg"]) == L.FLOOR_DEG
    f = {x["name"]: x["files"] for x in r["views"]}
    assert f["front_lobes"]["native_linear"]["path"].endswith(".npz")
    assert f["front_lobes"]["native_linear"]["bit_depth"] == "float16"
    assert f["front_lobes"]["native_linear"]["env_weights"] == front["weights"]
    az = np.asarray(f["front_lobes"]["native_linear"]["env_azimuth"])
    assert az[2] > 0.99                                                          # the 04 camera looks from +Z
    img = {k: C.read_linear(x["native_linear"]["path"]) for k, x in f.items()}
    mesh, roles = head_mesh(job)
    v = vs[0]
    mp = PO.posed(mesh, v["pose"], roles)
    ras = mp.scene().render(v["cam"], v["shape"], 1)
    part = np.full(v["shape"], -1)
    part[ras["mask"]] = mp.part[ras["face_id"][ras["mask"]]]
    face = C.erode_px((part == PO.front_body(mp, roles)) & (img["uniform"][1] > 0.999), 3)
    y = {k: np.median(im[0][face] @ C.LUMA) for k, im in img.items()}
    assert y["front_lobes"] > 3 * y["back_lobes"]                    # the front faces the camera: camera-side lobes
    u = img["uniform"][0][face] @ C.LUMA
    assert float(u.max()) > 1.0                                       # float output is not clipped at 1
    np.testing.assert_allclose(img["half"][0][face] @ C.LUMA, 0.5 * u, rtol=0.01, atol=2e-3)       # homogeneous


@pytest.mark.parametrize("params, match", [
    ({"rig": "photo_env"}, "needs env weights"),
    ({"rig": "photo_env", "env": {"weights": [1.0] * 5}}, "env weights must be 19"),
    ({"rig": "photo_env", "env": {"weights": [-1.0] + [1.0] * 18}}, "env weights must be"),
    ({"rig": "studio_dome_v1", "env": {"weights": [1.0] * 19}}, "need rig 'photo_env'"),
    ({"rig": "photo_env", "env": {"weights": [1.0] * 19}, "linear_format": "exr"}, "linear_format"),
])
def test_photo_env_refusals(job, params, match):
    v = view(job, "reference-05", 0.25, passes=["native_linear"])
    v.pop("cam"), v.pop("shape")
    with pytest.raises(WorkerError, match=match):
        job["w"].call("render", {"out_dir": str(job["root"] / "work" / "refuse_env"), "views": [v], **params},
                      deadline_s=120)
