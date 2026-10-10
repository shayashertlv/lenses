"""WP6_FACETS §3.1: the Prada camera basis with temple pose, as a WP3b-format cameras.json.

Inputs (sha-checked): the pose fits data/wp6/pose_arm_resume_20261010/all_views_validation/fits.json (cameras and
theta per photo; recorded sha), the frozen glossy WP3b cameras.json data/wp6/wp3b_glossy/cameras.json (sha
e20a7529...; its model block — frame, skip, front parts, pivot — is copied verbatim, never recomputed), the six
target photos data/reference/prada/reference-0N.* (manifest sha) and the black variant
data/wp6/new_reference_resume_20261009/references/black-side.png (manifest sha).

Each image gets theta_deg {"+1", "-1"}, theta_observed (false for the side views 01, 05 and black-side, whose theta
was held at 0), hinge_axis_xz_mm (the [64.6, -10.1] axis mirrored per side, checked against the temple-role knuckle
boxes of the delivered GLB), colourway (target | variant) and fit_status. The six target photos keep the glossy-floor
matte their cameras were fitted on; black-side (cropped at the image border, so the border-fitted backdrop model does
not apply) gets the dark-studio matte the transport used (Lab L < 60 or chroma > 8, small holes filled), written to
mattes/black-side.png and supplied. black-oblique is excluded: its camera is a copy of 02's, never fitted.

Writes data/wp6v2/basis/{cameras.json, mattes/black-side.png, report.json} and imports it once into a scratch job
(data/wp6v2/basis/check_job) to prove it freezes and loads with poses.
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_basis
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import glb as G
from glasses3d.g3d_host import matte as M
from glasses3d.g3d_host import pose as P

G3D = Path(__file__).resolve().parents[2]
DATA = G3D / "data"
FITS = DATA / "wp6" / "pose_arm_resume_20261010" / "all_views_validation" / "fits.json"
GLOSSY = DATA / "wp6" / "wp3b_glossy" / "cameras.json"
GLOSSY_SHA = "e20a75299be7cbcbfaacb45cd03ecd60e36024a387f763da602857f730965425"
PHOTOS = DATA / "reference" / "prada"
VARIANT = DATA / "wp6" / "new_reference_resume_20261009" / "references"
OUT = DATA / "wp6v2" / "basis"
AXIS_XZ = (64.6, -10.1)
SIDE_VIEWS = ("01", "05", "black-side")
DARK_L, DARK_CHROMA, DARK_HOLE = 60.0, 8.0, 0.002


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dark_studio_matte(rgb: np.ndarray) -> np.ndarray:
    """Foreground = Lab L < 60 or chroma > 8, holes up to 0.2 % of the image filled (the transport's L60 matte)."""
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)
    fg = (lab[..., 0] < DARK_L) | (np.hypot(lab[..., 1], lab[..., 2]) > DARK_CHROMA)
    holes = ndimage.binary_fill_holes(fg) & ~fg
    lab_, n = ndimage.label(holes)
    if n:
        sizes = np.bincount(lab_.ravel())
        small = np.flatnonzero((sizes <= DARK_HOLE * fg.size) & (np.arange(len(sizes)) != 0))
        fg |= np.isin(lab_, small)
    return fg


def knuckle_check(glb_path) -> dict:
    """Temple-role knuckle boxes of the delivered GLB -> the hinge axis per side (measure.knuckle_axis)."""
    g = G.load(glb_path)
    roles = P.part_roles(glb_path)
    boxes, body_front = [], {}
    for p in g.primitives:
        if p.mode != 4 or not len(p.positions):
            continue
        V = p.positions * 1000.0
        lo, hi = V.min(0), V.max(0)
        role = roles.get(p.node_name)
        boxes.append((lo, hi, role, p.node_name))
        if role == "temple" and hi[2] - lo[2] >= 60.0:
            body_front[1 if lo[0] + hi[0] > 0 else -1] = float(hi[2])
    hinge_z = float(np.mean(list(body_front.values())))
    out = {"hinge_z_mm (temple bodies' front z)": hinge_z}
    for side in (1, -1):
        ax = MS.knuckle_axis([(lo, hi, r) for lo, hi, r, _ in boxes], side, hinge_z)
        used = [n for lo, hi, r, n in boxes if MS.knuckle_axis([(lo, hi, r)], side, hinge_z) is not None]
        out[P.SIDE_KEYS[side]] = {"axis_xz_mm": ax, "knuckles": used,
                                  "delta_mm": [round(ax[0] - AXIS_XZ[0], 4), round(ax[1] - AXIS_XZ[1], 4)]}
    return out


def main() -> dict:
    assert sha(GLOSSY) == GLOSSY_SHA, "the glossy WP3b cameras.json changed"
    fits_sha = sha(FITS)
    fits = json.loads(FITS.read_text(encoding="utf-8"))
    glossy = json.loads(GLOSSY.read_text(encoding="utf-8"))
    man = json.loads((PHOTOS / "manifest.json").read_text(encoding="utf-8"))
    vman = json.loads((VARIANT / "manifest.json").read_text(encoding="utf-8"))
    glb_path = PHOTOS / "model.glb"
    assert sha(glb_path) == glossy["model"]["sha256"] == man["files"]["model.glb"]["sha256"]
    knuckles = knuckle_check(glb_path)
    for k in ("+1", "-1"):
        assert max(abs(v) for v in knuckles[k]["delta_mm"]) <= 0.05, knuckles
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "mattes").mkdir(parents=True)
    axis = {"+1": [AXIS_XZ[0], AXIS_XZ[1]], "-1": [-AXIS_XZ[0], AXIS_XZ[1]]}
    images, photos = {}, {}
    for i in range(1, 7):
        key = f"0{i}"
        name = next(n for n in glossy["images"] if C.ref_id(n) == f"reference-{key}")
        g = glossy["images"][name]
        path = PHOTOS / name
        s = sha(path)
        assert s == man["files"][name]["sha256"] == g["image_sha256"], name
        f = fits[key]
        H, W = M.load_photo(path).shape[:2]
        images[name] = {
            "image": str(path), "image_sha256": s, "shape": [H, W], "view_kind": g["view_kind"], "view": g["view"],
            "camera": f["camera"], "matte_mode": "glossy_floor", "status": "ok",
            "theta_deg": {"+1": f["theta"]["1"], "-1": f["theta"]["-1"]},
            "theta_observed": {"+1": key not in SIDE_VIEWS, "-1": key not in SIDE_VIEWS},
            "hinge_axis_xz_mm": axis, "colourway": "target",
            "fit_status": f"fitted (all_views_validation: iou {f['iou']:.3f}, contour {f['contour_mean_px']:.2f} px, "
                          f"front {f['front_mm']:.2f} mm)",
            "iou": f["iou"], "contour_mean_px": f["contour_mean_px"]}
        photos[name] = path
    bs = VARIANT / "black-side.png"
    rec_v = next(r for r in vman["images"] if r["file"] == "black-side.png")
    assert sha(bs) == rec_v["sha256"]
    rgb = M.load_photo(bs)
    fg = dark_studio_matte(rgb)
    mp = OUT / "mattes" / "black-side.png"
    Image.fromarray((fg * 255).astype(np.uint8)).save(mp)
    f = fits["black-side"]
    images["black-side.png"] = {
        "image": str(bs), "image_sha256": rec_v["sha256"], "shape": list(rgb.shape[:2]), "view_kind": "side",
        "view": "left", "camera": f["camera"], "status": "ok",
        "matte_mode": "supplied", "matte_file": "mattes/black-side.png", "matte_file_sha256": sha(mp),
        "matte_note": f"dark studio: Lab L < {DARK_L:g} or chroma > {DARK_CHROMA:g}, holes <= {DARK_HOLE:.1%} filled",
        "theta_deg": {"+1": f["theta"]["1"], "-1": f["theta"]["-1"]},
        "theta_observed": {"+1": False, "-1": False}, "hinge_axis_xz_mm": axis, "colourway": "variant",
        "fit_status": "camera transported from 05", "iou": f["iou"], "contour_mean_px": f["contour_mean_px"]}
    photos["black-side.png"] = bs
    model = dict(glossy["model"])
    model["pose_source"] = {"fits": str(FITS), "fits_sha256": fits_sha,
                            "note": "cameras and theta from the all-views pose validation (delivered front + v1 "
                                    "preferred temples); frame/skip/front parts/pivot copied verbatim from the glossy "
                                    "WP3b cameras.json",
                            "glossy_cameras_sha256": GLOSSY_SHA, "knuckle_check": knuckles}
    doc = {"created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": model,
           "images": images, "excluded": {"black-oblique": "its camera is a copy of 02's, never fitted"},
           "policy": {"schema_note": "WP3b cameras.json + WP6_FACETS §3.1 pose fields",
                      "theta": "deg, + = tip outward on both sides (g3d_host/pose.rot_about_hinge)"}}
    C.write_json(OUT / "cameras.json", doc)
    check = OUT / "check_job"
    b = C.import_cameras_json(check, OUT / "cameras.json", photos, reason="WP6 v2 pose basis (proof import)")
    poses = {rid: b.pose(rid) for rid in b.refs}
    assert all(poses.values()) and b.colourway("black-side") == "variant", poses
    rep = {"cameras_json": str(OUT / "cameras.json"), "cameras_json_sha256": sha(OUT / "cameras.json"),
           "fits_sha256": fits_sha, "glossy_sha256": GLOSSY_SHA, "knuckle_check": knuckles,
           "refs": {rid: {"theta_deg": b.theta(rid), "colourway": b.colourway(rid), "fg_px": r["fg_px"],
                          "matte_mode": r["matte_mode"], "fit_status": r.get("fit_status")}
                    for rid, r in b.refs.items()},
           "basis_notes": b.meta["notes"]}
    C.write_json(OUT / "report.json", rep)
    return rep


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
