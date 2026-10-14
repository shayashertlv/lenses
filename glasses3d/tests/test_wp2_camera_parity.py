"""WP2 stage B stop rule (§15 row B): camera parity in Blender itself, on the 12 frozen WP3b cameras (Prada + FT1119)
and 4 synthetic ones (portrait / landscape / square, perspective / orthographic, pitch 89.5).

* analytic: Blender's ``world_to_camera_view`` with the camera set by the worker's ``g3d.render.apply_camera`` vs
  ``camera.project_mm``: <= 0.01 px (stop rule);
* rendered: small spheres rendered by the worker's ``g3d.render.render`` (temporary scene, EEVEE, clay alpha); each
  blob's alpha centroid vs the projection of its centre: <= 0.1 px (stop rule).
Needs Blender 5.2 and the WP3b cameras (data: skipped, or failed under G3D_REQUIRE_DATA=1)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import blender
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.tests.test_wp2_camera import ORIGIN, PRADA_FRAME, SYNTHETIC, frozen_cameras, sample_points

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

SCRIPT = blender.G3D / "tests" / "blender" / "wp2_camera_parity.py"
RADIUS_MM = 0.9
CANDIDATE_BLOBS_N = ((0.30, 0.05, 0.20), (-0.25, -0.03, -0.10), (0.05, 0.10, -0.35), (-0.05, -0.08, 0.30),
                     (0.22, -0.10, -0.25), (-0.30, 0.08, 0.15))


def blob_px(cam, frame, centre_mm, spec) -> float:
    """Approximate blob radius in px at its centre (for the centroid window)."""
    return RADIUS_MM * BC.px_per_mm_at(cam, frame, centre_mm)


def run_group(name, cams, frame, tmp_path):
    """cams = [(label, Camera, shape)] sharing `frame` (blobs are placed in it). Returns per camera stats."""
    P = sample_points(frame, 500, seed=7)
    blobs = frame.to_mm(np.asarray(CANDIDATE_BLOBS_N))
    job = {"root": str(tmp_path), "points_mm": (P + ORIGIN).tolist(), "blobs_mm": (blobs + ORIGIN).tolist(),
           "radius_mm": RADIUS_MM, "cameras": []}
    specs = {}
    for label, cam, shape in cams:
        spec = BC.to_blender(cam, frame, ORIGIN, shape)
        specs[label] = (cam, shape, spec)
        job["cameras"].append({"name": label.replace("/", "_"), "spec": spec, "render": True})
    jp, rp = tmp_path / f"{name}.json", tmp_path / f"{name}-receipt.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    proc = blender.run(None, SCRIPT, [jp, rp], timeout_s=600)
    assert rp.is_file(), proc.stdout[-3000:] + proc.stderr[-3000:]
    rec = json.loads(rp.read_text(encoding="utf-8"))
    files = {v["name"]: v["files"]["clay"] for v in rec["render"]["views"]}
    assert rec["render"]["unchanged"] is True
    out = {}
    for label, (cam, shape, spec) in specs.items():
        key = label.replace("/", "_")
        uv_b = np.asarray(rec["analytic"][key])
        analytic = float(np.abs(uv_b - project_mm(P, cam, frame)).max())
        H, W = shape
        alpha = np.asarray(Image.open(files[key]["path"]).convert("RGBA"), np.float64)[..., 3] / 255.0
        centres = project_mm(blobs, cam, frame)
        rads = [blob_px(cam, frame, b, spec) for b in blobs]
        errs = []
        for i, ((u, v), r) in enumerate(zip(centres, rads)):
            win = int(math.ceil(r + 4))
            if not (win <= u <= W - 1 - win and win <= v <= H - 1 - win):
                continue                                      # outside this photo's frame
            if any(j != i and np.hypot(*(centres[j] - (u, v))) < rads[j] + r + 6 for j in range(len(blobs))):
                continue                                      # two blobs overlap in this view
            u0, v0 = int(round(u)), int(round(v))
            ys, xs = np.mgrid[v0 - win:v0 + win + 1, u0 - win:u0 + win + 1]
            w = alpha[ys, xs]
            if w.sum() < 1.0:
                continue
            errs.append(float(np.hypot((xs * w).sum() / w.sum() - u, (ys * w).sum() / w.sum() - v)))
        out[label] = {"analytic_px": analytic, "rendered_px": errs, "engine": files[key]["engine"]}
    return out


@pytest.fixture(scope="module")
def parity(tmp_path_factory):
    cams = frozen_cameras()
    groups = {}
    for prod in ("prada", "ft1119"):
        mine = [(n, c, s) for n, c, fr, s in cams if n.startswith(prod)]
        frame = next(fr for n, c, fr, s in cams if n.startswith(prod))
        if prod == "prada":
            assert frame == PRADA_FRAME
            mine += [(f"synthetic/{n}", c, s) for n, (c, s) in SYNTHETIC.items()]
        groups.update(run_group(prod, mine, frame, tmp_path_factory.mktemp(f"parity-{prod}")))
    return groups


def test_blender_world_to_camera_view_matches_wp3b_within_a_hundredth_of_a_pixel(parity):
    assert len(parity) == 16
    worst = {k: v["analytic_px"] for k, v in parity.items()}
    assert max(worst.values()) <= 0.01, worst
    print("analytic parity px (Blender world_to_camera_view):", {k: f"{v:.2e}" for k, v in worst.items()})


def test_rendered_blob_centroids_match_wp3b_within_a_tenth_of_a_pixel(parity):
    measured = {k: v["rendered_px"] for k, v in parity.items()}
    for k, errs in measured.items():
        assert len(errs) >= 2, (k, errs)                       # at least two separated blobs in every view
        assert max(errs) <= 0.1, (k, errs)
    assert {v["engine"] for v in parity.values()} == {"BLENDER_EEVEE"}
    print("rendered parity px:", {k: round(max(v), 4) for k, v in measured.items()})


def test_off_axis_camera_matches_actual_blender_projection_and_render(tmp_path):
    # Signed lateral translations, with perspective and orthographic cameras in one unchanged world frame.
    frame = NormFrame((13, -4, -70), 150)
    cameras = [("offaxis-perspective", Camera(60,20,7,.3,900,640,480,.22,-.12), (960,1280)),
               ("offaxis-ortho", Camera(-35,15,-9,0,900,640,480,-.12,.08), (960,1280))]
    results = run_group("offaxis", cameras, frame, tmp_path)
    for record in results.values():
        assert record["analytic_px"] <= .01, results
        assert len(record["rendered_px"]) >= 2, results
        assert max(record["rendered_px"]) <= .1, results
    print("off-axis actual Blender parity:", results)
