"""WP2 stage B: fit_cameras wiring (WP2_DESIGN §6 "Basis", §14 criterion 12 on a small case).

The synthetic glasses of tests/test_fit_views_synthetic.py built as a live Blender scene (bridge-underside origin at
(0, 4, 2) mm), three photos rendered at known TRUTH cameras (front, a 'side' that is the glasses' left, angled) and
named like a job's references. ``Seeing.fit_cameras`` exports the head, runs ``fit_views`` with 3 workers (EcoQoS
opted out) and freezes basis-01: every photo IoU > 0.97, contour < 1.5 px, |dyaw| and |dpitch| < 1.5 deg vs TRUTH.
compare_to_photos on the unchanged head then reproduces the fit's own numbers exactly; a refit of one reference is
basis-02 (the newest basis is the default; basis-01 stays frozen and selectable).
Needs Blender 5.2."""
from __future__ import annotations

import json
import os
import shutil
import stat

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent import procs
from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import blender, fitmesh, raster
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import matte as M
from glasses3d.tests.test_fit_views_synthetic import PPM, SHAPE, mesh_glb, photo, synthetic_parts

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

SCRIPT = blender.G3D / "tests" / "blender" / "wp2_synthetic_scene.py"
ORIGIN = [0.0, 4.0, 2.0]
TRUTH = {                                   # job reference name: (kind, yaw, pitch, roll, perspective)
    "reference-01.png": ("front", 4.0, 12.0, 1.0, 0.12),
    "reference-02.png": ("side", 88.0, 8.0, 0.0, 0.15),
    "reference-03.png": ("angled", 33.0, 14.0, 0.0, 0.12),
}
ROLES = {"Front": "frame", "Temple_R": "temple", "Temple_L": "temple"}


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    d = tmp_path_factory.mktemp("wp2fitsrc")
    parts = synthetic_parts()
    (d / "parts.json").write_text(json.dumps({"origin_mm": ORIGIN, "parts": [
        {"name": n, "role": ROLES[n], "V": np.asarray(V).tolist(), "F": np.asarray(F).tolist()}
        for n, V, F in parts]}), encoding="utf-8")
    seed = d / "synthetic.blend"
    proc = blender.run(None, SCRIPT, [d / "parts.json", seed], timeout_s=300)
    assert seed.is_file(), proc.stdout[-2000:] + proc.stderr[-2000:]
    glb = d / "synthetic.glb"
    glb.write_bytes(mesh_glb(parts))
    mesh = fitmesh.load_fit_mesh(glb)
    root = tmp_path_factory.mktemp("wp2fit")
    (root / "inputs" / "images").mkdir(parents=True)
    images, views, true = {}, {}, {}
    for name, (kind, yaw, pitch, roll, persp) in TRUTH.items():
        cam = raster.view_camera(mesh.frame, yaw, pitch, PPM, SHAPE, roll=roll, perspective=persp)
        p = root / "inputs" / "images" / name
        Image.fromarray(photo(mesh.scene().render(cam, SHAPE)["mask"])).save(p)
        images[name], views[name], true[name] = p, kind, cam
    s = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    s.start()
    see = Seeing(s)
    fit = see.fit_cameras("first basis for the synthetic glasses", images, views, front_parts=("Front",),
                          workers=3)
    yield {"root": root, "session": s, "seeing": see, "fit": fit, "images": images, "views": views, "true": true,
           "frame": mesh.frame}
    s.close()
    shutil.rmtree(root / "checkpoints", onerror=_rw)


@pytest.mark.slow
def test_fit_cameras_freezes_basis_01_close_to_the_truth(job):
    fit = job["fit"]
    assert fit["status"] == "ok", fit
    assert fit["basis"] == "basis-01" and list(fit["refs"]) == ["reference-01", "reference-02", "reference-03"]
    bs = C.load_basis(job["root"], "basis-01")
    assert bs.id == "basis-01" and bs.meta["source"] == "fit_cameras" and bs.meta["call_id"] == fit["call_id"]
    assert bs.meta["reason"] == "first basis for the synthetic glasses"
    assert bs.meta["model"]["glb_sha256"] == fit["glb_sha256"] == bs.meta["model"]["sha256"]
    assert bs.front_parts == ("Front",) and bs.meta["notes"] == []
    np.testing.assert_allclose(bs.frame.center, job["frame"].center, atol=1e-3)       # export == the given mm
    assert bs.frame.extent == pytest.approx(job["frame"].extent, abs=1e-3)
    for name, (kind, yaw, pitch, roll, persp) in TRUTH.items():
        rid = C.ref_id(name)
        rec = bs.refs[rid]
        f = rec["fit"]
        assert rec["view_kind"] == kind and f["status"] == "ok", rec
        assert f["iou"] > 0.97 and f["contour_mean_px"] < 1.5, (rid, f)
        assert abs(rec["camera"]["yaw"] - yaw) < 1.5 and abs(rec["camera"]["pitch"] - pitch) < 1.5, rec["camera"]
        fg = M.matte(M.load_photo(job["images"][name]))
        assert (bs.matte(rid) == fg).all()                                        # the frozen matte is the fit's
        assert Image.open(bs.dir / rec["overlay"]).size == (SHAPE[1], SHAPE[0])
        assert fit["refs"][rid]["overlay"] == f"cameras/{bs.id}/{rec['overlay']}"
    assert bs.refs["reference-02"]["view"] == "left"
    assert fit["sheet"] and max(Image.open(fit["sheet"]).size) <= 2048
    if procs.available():                                                        # EcoQoS opted out: host + pool
        assert procs.qos_state() == (1, 0) and fit["ecoqos"]["host_opted_out"]
        assert fit["ecoqos"]["workers_opted_out"] >= 2, fit["ecoqos"]
    print("fit_cameras:", {k: (v["iou"], v["yaw"], v["pitch"]) for k, v in fit["refs"].items()},
          f"fit {fit['fit_seconds']} s, total {fit['seconds']} s")


@pytest.mark.slow
def test_compare_on_the_unchanged_head_reproduces_the_fit_exactly(job):
    r = job["seeing"].compare_to_photos(passes=("native",), basis="basis-01")
    assert r["status"] == "ok", r
    bs = C.load_basis(job["root"], "basis-01")
    for rid, m in r["refs"].items():
        assert m["iou"] == round(bs.refs[rid]["fit"]["iou"], 4), rid
        assert m["contour_mean_mm"] == round(bs.refs[rid]["fit"]["contour_mean_mm"], 3), rid
    rep = json.loads(open(r["report"], encoding="utf-8").read())
    for rid in r["refs"]:
        assert rep["refs"][rid]["all"]["iou"] == bs.refs[rid]["fit"]["iou"], rid
    assert rep["glb"]["sha256"] == bs.meta["model"]["glb_sha256"]                # the export cache: same bytes


@pytest.mark.slow
def test_a_refit_is_a_new_basis_and_the_old_one_stays_selectable(job):
    see = job["seeing"]
    baseline = see.compare_to_photos(passes=(), basis="basis-01")
    assert baseline["status"] == "ok", baseline
    fit2 = see.fit_cameras("refit the front only", job["images"], job["views"], refs=["reference-01"],
                           front_parts=("Front",), workers=3)
    assert fit2["status"] == "ok" and fit2["basis"] == "basis-02" and len(fit2["refs"]) == 3
    assert fit2["refit"]["requested_refs"] == ["reference-01"]
    assert len(fit2["refit"]["retained_refs"]) == 2
    assert C.list_bases(job["root"]) == ["basis-01", "basis-02"]
    r = see.compare_to_photos(passes=())
    assert r["basis"] == "basis-02" and len(r["refs"]) == 3 and r["previous"] is None
    r1 = see.compare_to_photos(passes=(), basis="basis-01")
    assert r1["basis"] == "basis-01" and len(r1["refs"]) == 3 and r1["model_bytes_identical"] is True
    bad = see.fit_cameras("", job["images"], job["views"])
    assert bad["status"] == "error" and bad["code"] == "E_ARGS"
    bad = see.fit_cameras("x", job["images"], job["views"], refs=["reference-09"])
    assert bad["status"] == "error" and "reference-09" in bad["message"]
