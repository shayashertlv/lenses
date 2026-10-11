"""WP6_FACETS §3.1: the Prada camera basis with temple pose, as a WP3b-format cameras.json.

Inputs (sha-checked): the pose fits data/wp6/pose_arm_resume_20261010/all_views_validation/fits.json (cameras and
theta per photo; recorded sha), the frozen glossy WP3b cameras.json data/wp6/wp3b_glossy/cameras.json (sha
e20a7529...; its model block — frame, skip, front parts, pivot — is copied verbatim, never recomputed), the six
target photos data/reference/prada/reference-0N.* (manifest sha), the black variant
data/wp6/new_reference_resume_20261009/references/black-side.png (manifest sha) and the owner's black angled and front
photos (OneDrive erroca/images/black_prada, sha256-pinned in TWINS).

Each image gets theta_deg {"+1", "-1"}, theta_observed (false for the side views 01, 05 and black-side, whose theta
was held at 0, and for the transported twins, whose theta is copied), hinge_axis_xz_mm (the [64.6, -10.1] axis
mirrored per side, checked against the temple-role knuckle boxes of the delivered GLB), colourway (target | variant)
and fit_status. The six target photos keep the glossy-floor matte their cameras were fitted on. Each black photo gets a
matte chosen for its background (SUPPLIED_MATTE, each checked by eye), written to mattes/<ref>.png and supplied:
- black-side: the dark-studio matte (Lab L < 60 or chroma > 8, small holes filled). It is cropped at the image border,
  so the border-fitted backdrop model does not apply, and a white-background matte takes in its floor reflection;
- black-angled and black-front (white seamless background, no floor): the white-background matte (Lab L < 92 or
  chroma > 6, specks under 0.2 % of the image dropped, holes up to 0.2 % filled). The dark matte's L < 60 drops the
  lit light-grey faces of the glossy black acetate: on black-front both temple tops, ~6.3k px (review 2026-10-11, P1),
  which biased B3's camera and every temple IoU and photo outline drawn against that matte.

Cameras of the black photos (colourway variant, never fitted to the model):
- black-side: the all-views validation's camera, transported from 05 by a 2D image similarity there (WP6 v1);
- black-angled (B2) from 02 and black-front (B3) from 04 (owner, 2026-10-11: "yes, build 1-3"): the target photo of the
  same pose, registered here by g3d_host/camera_similarity.transport (contour ICP of 02's / 04's glossy-floor matte
  from the masks' bounding-box seed) onto the black photo's matte that agrees best with the source's matte (lowest
  mean bidirectional contour px among the white-background and dark-studio mattes; ``registration``): the source
  matte is fixed, so that is the black-photo matte that segments the object the way the source matte does. 04's matte
  keeps the temple tops, so B3 registers onto its white-background matte; 02's matte drops the same lit strips as the
  dark matte, so B2 registers onto the dark matte and keeps its camera. The source camera is moved through that
  similarity, theta is copied from the source. Their record carries the full registration (``transport``: start,
  parameters, scale, rotation, mean / rms bidirectional contour px, percentiles, mask IoU after the warp, the
  registration matte and every candidate's numbers) and a fit_status naming the source and the two residuals. Those
  residuals are photo to photo. The camera is checked $0 against the original model in ``model_check`` ({iou: full
  model-silhouette IoU, temple_iou: the photo compare's temple-region IoU, against}), computed with the compare's own
  raster (seeing._matched_geometry) on the basis's model against the supplied matte. It is not the record's top-level
  ``iou`` (the fit IoU), so seeing.gain_view_rank still ranks the twins -1 and black-side stays the black gain view.
  report.json repeats the review probe's registration with its speck-filtered matte beside this one (PROBE) and the
  model check of the source photos and black-side.
black-oblique is excluded: its camera is a copy of 02's, never fitted, and it is a crop of black-angled.

Writes data/wp6v2/basis/{cameras.json, mattes/<black ref>.png, report.json}. It imports the basis once into a scratch
job (data/wp6v2/basis/model_check_job, removed afterwards) for the model checks, then the final cameras.json into
data/wp6v2/basis/check_job to prove it freezes and loads with poses and that the gain views are unchanged. Run as a
script it pins BLAS to one thread: a multi-threaded BLAS moves the registration floats in the last digits (~1e-10).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_basis
"""
from __future__ import annotations

import os

if __name__ == "__main__":      # BLAS 1 (AGENTS.md), set before numpy loads: run-to-run identical registration floats
    os.environ.update({k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")})

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
from glasses3d.g3d_host import camera_similarity as CS
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import glb as G
from glasses3d.g3d_host import matte as M
from glasses3d.g3d_host import pose as P
from glasses3d.agent import seeing as SEEING
from glasses3d.g3d_host.camera import Camera

G3D = Path(__file__).resolve().parents[2]
DATA = G3D / "data"
FITS = DATA / "wp6" / "pose_arm_resume_20261010" / "all_views_validation" / "fits.json"
GLOSSY = DATA / "wp6" / "wp3b_glossy" / "cameras.json"
GLOSSY_SHA = "e20a75299be7cbcbfaacb45cd03ecd60e36024a387f763da602857f730965425"
PHOTOS = DATA / "reference" / "prada"
VARIANT = DATA / "wp6" / "new_reference_resume_20261009" / "references"
BLACK_PRADA = Path("C:/Users/Shay/OneDrive/Desktop/erroca/images/black_prada")
OUT = DATA / "wp6v2" / "basis"
AXIS_XZ = (64.6, -10.1)
SIDE_VIEWS = ("01", "05", "black-side")
DARK_L, DARK_CHROMA, DARK_HOLE = 60.0, 8.0, 0.002
WHITE_L, WHITE_CHROMA, WHITE_SPECK, WHITE_HOLE = 92.0, 6.0, 0.002, 0.002
# The black photos of the same pose as a target photo (the owner's files, byte-pinned): basis name, source file, the
# target photo whose camera is transported, and its sha256.
TWINS = ({"image": "black-angled.webp", "source": BLACK_PRADA / "8056262360200.webp", "from": "02",
          "sha256": "5705c2cab03f5e457d2b1dfc58b5155dec5ac03c3450e7672511a10aeb4ac8f1"},
         {"image": "black-front.webp", "source": BLACK_PRADA / "8056262360200-F.webp", "from": "04",
          "sha256": "e89d19f4101d6457a32274dc4a1b8b51a0de525ef09d2179b8657eb6c34621c3"})
# The supplied matte of each black photo, by its background (module doc). Each was checked by eye on 2026-10-11 (review
# and fix): on black-side the white-background matte takes in the floor reflection; on black-angled and black-front its
# outline follows the lit light-grey edges, which the dark matte cuts through.
SUPPLIED_MATTE = {"black-side": "dark_studio", "black-angled": "white_background", "black-front": "white_background"}
# The candidate black-photo mattes of a twin's registration, the supplied one first (it wins a tie).
REGISTRATION_MATTES = ("white_background", "dark_studio")
TEMPLE_REGION_PX = SEEING.TEMPLE_REGION_PX   # the photo compare's temple region (seeing.py)
# The basis's model (data/reference/prada/model.glb, the model block's sha): vertex for vertex the facet lab's
# revision-0 head export (head-b89ad6603d00577f.glb, sha 31988470...), node names aside (".001").
ORIGINAL_MODEL = "original model"
# a transported camera whose temple IoU on the original model falls this far below its source photo's is mis-registered
# (the pre-fix B3: 0.821 against 04's 0.888 on the white-background matte, 0.720 on its own dark matte)
TWIN_TEMPLE_IOU_SLACK = 0.03
# The pre-build review's probe (scratch transport_probe.py: the frozen package mattes, its target matte with specks
# under 0.2 % of the image dropped), for the report's reproduction rows only.
PROBE = {"black-angled": {"scale": 1.0539, "mean_bidirectional_px": 0.83, "mask_iou_after_warp": 0.992},
         "black-front": {"scale": 1.2193, "mean_bidirectional_px": 3.8, "mask_iou_after_warp": 0.970}}
PROBE_SPECK = 0.002


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _lab_foreground(rgb: np.ndarray, max_l: float, min_chroma: float) -> np.ndarray:
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)
    return (lab[..., 0] < max_l) | (np.hypot(lab[..., 1], lab[..., 2]) > min_chroma)


def with_small_holes_filled(fg: np.ndarray, frac: float) -> np.ndarray:
    """fg with its holes of at most frac of the image filled."""
    fg = fg.copy()
    holes = ndimage.binary_fill_holes(fg) & ~fg
    lab_, n = ndimage.label(holes)
    if n:
        sizes = np.bincount(lab_.ravel())
        small = np.flatnonzero((sizes <= frac * fg.size) & (np.arange(len(sizes)) != 0))
        fg |= np.isin(lab_, small)
    return fg


def dark_studio_matte(rgb: np.ndarray) -> np.ndarray:
    """Foreground = Lab L < 60 or chroma > 8, holes up to 0.2 % of the image filled (the transport's L60 matte)."""
    return with_small_holes_filled(_lab_foreground(rgb, DARK_L, DARK_CHROMA), DARK_HOLE)


def without_specks(fg: np.ndarray, frac: float = PROBE_SPECK) -> np.ndarray:
    """fg without its components smaller than frac of the image (the review probe's target matte)."""
    lab_, n = ndimage.label(fg)
    if not n:
        return fg
    sizes = np.bincount(lab_.ravel())
    sizes[0] = 0
    return np.isin(lab_, np.flatnonzero(sizes >= frac * fg.size))


def white_background_matte(rgb: np.ndarray) -> np.ndarray:
    """Foreground on a white seamless background without a floor reflection = Lab L < 92 or chroma > 6; components
    under 0.2 % of the image (specks) dropped, then holes up to 0.2 % filled. Keeps the lit light-grey faces of a glossy
    black frame, which the dark-studio matte drops."""
    return with_small_holes_filled(without_specks(_lab_foreground(rgb, WHITE_L, WHITE_CHROMA), WHITE_SPECK),
                                   WHITE_HOLE)


MATTES = {"dark_studio": (dark_studio_matte, f"dark studio: Lab L < {DARK_L:g} or chroma > {DARK_CHROMA:g}, holes <= "
                                             f"{DARK_HOLE:.1%} filled"),
          "white_background": (white_background_matte,
                               f"white background: Lab L < {WHITE_L:g} or chroma > {WHITE_CHROMA:g}, components < "
                               f"{WHITE_SPECK:.1%} dropped, holes <= {WHITE_HOLE:.1%} filled")}


def matte_label(name: str) -> str:
    """'white_background' -> 'white-background' (as the records and model checks name a matte)."""
    return name.replace("_", "-")


def registration(cam: Camera, src_matte: np.ndarray, candidates: dict) -> tuple[str, dict, dict]:
    """(the registration matte's name, its CS.transport, {candidate: its numbers}): the source photo's matte registered
    onto each candidate matte of the black photo ({name: fg}, in preference order); the one whose contour agrees best
    with the source matte (lowest mean bidirectional px; the first on a tie) is the registration. The source matte is
    fixed, so that candidate segments the object the way the source matte does (module doc)."""
    runs, rows = {}, {}
    for name, fg in candidates.items():
        t = CS.transport(cam, src_matte, fg)
        assert t["termination"] == "parameter_step_converged", (name, t["termination"])
        runs[name] = t
        rows[name] = {k: t[k] for k in ("mean_bidirectional_px", "mask_iou_after_warp", "reverse_percentiles_px",
                                        "scale")} | {"camera": t["camera"].to_dict()}
    best = min(runs, key=lambda n: runs[n]["mean_bidirectional_px"])
    return best, runs[best], rows


def model_check(bs: C.Basis, rid: str, mesh, roles: dict) -> dict:
    """{iou: the full model-silhouette IoU, temple_iou: the temple-region IoU} of the original model posed at the basis
    photo against the basis's matte of it: the photo compare's own raster (seeing._matched_geometry) and temple region
    (seeing._matched_photo: the posed temple pixels dilated by TEMPLE_REGION_PX), at the photo's native size."""
    from glasses3d.agent.seeing import Seeing
    g = Seeing._matched_geometry(bs, rid, mesh, roles)
    mt = bs.matte(rid)
    region = ndimage.binary_dilation(g["temple"], iterations=TEMPLE_REGION_PX)
    a, b = g["mask"] & region, mt & region
    return {"iou": round(float((g["mask"] & mt).sum() / max(1, (g["mask"] | mt).sum())), 4),
            "temple_iou": round(float((a & b).sum() / max(1, (a | b).sum())), 4)}


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


def transported_twin(want: dict, src_name: str, src: dict, axis: dict) -> tuple[dict, np.ndarray, dict]:
    """(the twin's cameras.json record without its matte sha and model check, its supplied matte, the
    probe-reproduction row): the source target photo's camera transported onto the black photo (module doc)."""
    path = Path(want["source"])
    assert sha(path) == want["sha256"], f"{path}: not the pinned bytes"
    rgb = M.load_photo(path)
    rid, key = C.ref_id(want["image"]), want["from"]
    supplied = SUPPLIED_MATTE[rid]
    mattes = {name: MATTES[name][0](rgb) for name in REGISTRATION_MATTES}
    fg = mattes[supplied]
    src_matte = M.matte(M.load_photo(src["image"]), mode=src["matte_mode"])
    cam = Camera.from_dict(src["camera"])
    reg, t, rows = registration(cam, src_matte, {supplied: fg, **{n: m for n, m in mattes.items() if n != supplied}})
    probe = CS.transport(cam, src_matte, without_specks(mattes["dark_studio"]))
    keys = ("scale", "mean_bidirectional_px", "mask_iou_after_warp")
    repro = {"probe": PROBE[rid], **{name: {k: round(r[k], 4) for k in keys} for name, r in rows.items()},
             "probe_matte (dark studio, specks < 0.2 % dropped)": {k: round(probe[k], 4) for k in keys}}
    transport = {k: v for k, v in t.items() if k != "camera"}
    why = ("the supplied matte" if reg == supplied else
           f"not the supplied {matte_label(supplied)} matte: this one agrees best with the source matte")
    transport.update({"method": "g3d_host/camera_similarity.transport (contour ICP from bbox_seed)",
                      "source": src_name, "source_camera": src["camera"],
                      "source_matte": f"{src['matte_mode']} (the matte {key}'s camera was fitted on)",
                      "target_matte": f"{matte_label(reg)} ({why}; {MATTES[reg][1]})",
                      "registration": "the candidate black-photo matte with the lowest mean bidirectional contour px "
                                      "against the source matte",
                      "registration_candidates": rows})
    rec = {"image": str(path), "image_sha256": want["sha256"], "shape": list(rgb.shape[:2]),
           "view_kind": src["view_kind"], "view": src["view"], "camera": t["camera"].to_dict(), "status": "ok",
           "matte_mode": "supplied", "matte_file": f"mattes/{rid}.png", "matte_note": MATTES[supplied][1],
           "theta_deg": dict(src["theta_deg"]), "theta_observed": {"+1": False, "-1": False},
           "hinge_axis_xz_mm": axis, "colourway": "variant",
           "fit_status": f"camera transported from {key} by image similarity (contour {t['mean_bidirectional_px']:.2f} "
                         f"px, IoU {t['mask_iou_after_warp']:.3f}); theta copied from {key}",
           "transport": transport}
    return rec, fg, repro


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
    side_matte, side_note = MATTES[SUPPLIED_MATTE["black-side"]]
    fg = side_matte(rgb)
    mp = OUT / "mattes" / "black-side.png"
    Image.fromarray((fg * 255).astype(np.uint8)).save(mp)
    f = fits["black-side"]
    images["black-side.png"] = {
        "image": str(bs), "image_sha256": rec_v["sha256"], "shape": list(rgb.shape[:2]), "view_kind": "side",
        "view": "left", "camera": f["camera"], "status": "ok",
        "matte_mode": "supplied", "matte_file": "mattes/black-side.png", "matte_file_sha256": sha(mp),
        "matte_note": side_note,
        "theta_deg": {"+1": f["theta"]["1"], "-1": f["theta"]["-1"]},
        "theta_observed": {"+1": False, "-1": False}, "hinge_axis_xz_mm": axis, "colourway": "variant",
        "fit_status": "camera transported from 05", "iou": f["iou"], "contour_mean_px": f["contour_mean_px"]}
    photos["black-side.png"] = bs
    repro, sources = {}, {}
    for want in TWINS:
        src_name = next(n for n in images if C.ref_id(n) == f"reference-{want['from']}")
        rid = C.ref_id(want["image"])
        sources[rid] = C.ref_id(src_name)
        rec, fg, repro[rid] = transported_twin(want, src_name, images[src_name], axis)
        mp = OUT / rec["matte_file"]
        Image.fromarray((fg * 255).astype(np.uint8)).save(mp)
        rec["matte_file_sha256"] = sha(mp)
        images[want["image"]] = rec
        photos[want["image"]] = Path(want["source"])
    model = dict(glossy["model"])
    model["pose_source"] = {"fits": str(FITS), "fits_sha256": fits_sha,
                            "note": "cameras and theta from the all-views pose validation (delivered front + v1 "
                                    "preferred temples); frame/skip/front parts/pivot copied verbatim from the glossy "
                                    "WP3b cameras.json",
                            "glossy_cameras_sha256": GLOSSY_SHA, "knuckle_check": knuckles}
    doc = {"created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": model,
           "images": images,
           "excluded": {"black-oblique": "its camera is a copy of 02's, never fitted; it is a crop of black-angled, "
                                         "which is in the basis with its own transported camera"},
           "policy": {"schema_note": "WP3b cameras.json + WP6_FACETS §3.1 pose fields",
                      "theta": "deg, + = tip outward on both sides (g3d_host/pose.rot_about_hinge)",
                      "colourway_cameras": "each black photo's camera is a target photo's camera of the same pose "
                                           "moved by a 2D image similarity (black-side from 05, black-angled from 02, "
                                           "black-front from 04); never fitted to the model",
                      "colourway_mattes": "chosen per photo by its background: black-side dark studio (a "
                                          "white-background matte takes in its floor reflection); black-angled and "
                                          "black-front white background (the dark matte drops the lit light-grey "
                                          "faces); a twin registers onto the matte that agrees best with its source "
                                          "photo's matte (transport.registration_candidates)",
                      "model_check": "black-angled and black-front: the transported camera checked against the "
                                     "original model (full and temple-region IoU against the supplied matte; tests/"
                                     "proofs/wp6v2_basis.py model_check); not the fit IoU (top-level iou), so the gain "
                                     "views are unchanged (seeing.gain_view_rank)"}}
    # 1. a scratch import for the model checks ($0: the compare's raster on the basis's model, no Blender)
    C.write_json(OUT / "cameras.json", doc)
    scratch = OUT / "model_check_job"
    b0 = C.import_cameras_json(scratch, OUT / "cameras.json", photos, reason="WP6 v2 pose basis (model check)")
    mesh = fitmesh.load_fit_mesh(glb_path, skip=tuple(b0.skip), frame=b0.frame)
    roles = P.part_roles(glb_path)
    checks = {}
    for rid in ("black-side", *sources, *sources.values()):
        matte = SUPPLIED_MATTE.get(rid) or b0.refs[rid]["matte_mode"]
        checks[rid] = {**model_check(b0, rid, mesh, roles), "against": f"{ORIGINAL_MODEL}, {matte_label(matte)} matte"}
    shutil.rmtree(scratch)
    for want in TWINS:
        rid = C.ref_id(want["image"])
        rec = images[want["image"]]
        assert "iou" not in rec                    # the fit IoU stays absent: never the gain view
        rec["model_check"] = checks[rid]
        assert set(rec["model_check"]) == {"iou", "temple_iou", "against"}
        assert rec["model_check"]["against"] == f"{ORIGINAL_MODEL}, white-background matte", rec["model_check"]
        src = checks[sources[rid]]
        assert rec["model_check"]["temple_iou"] >= src["temple_iou"] - TWIN_TEMPLE_IOU_SLACK, (rid, checks)
        t = rec["transport"]
        assert t["mean_bidirectional_px"] <= 1.5 and t["mask_iou_after_warp"] >= 0.98, (rid, t["target_matte"])
    # 2. the basis itself, imported into the check job
    C.write_json(OUT / "cameras.json", doc)
    check = OUT / "check_job"
    b = C.import_cameras_json(check, OUT / "cameras.json", photos, reason="WP6 v2 pose basis (proof import)")
    poses = {rid: b.pose(rid) for rid in b.refs}
    assert all(poses.values()), poses
    assert [rid for rid in b.refs if b.colourway(rid) == "variant"] == ["black-side", "black-angled", "black-front"]
    for rid in sources:
        assert b.refs[rid]["fit"]["iou"] is None and b.refs[rid]["matte_note"] == MATTES["white_background"][1]
        supplied = np.asarray(Image.open(OUT / "mattes" / f"{rid}.png").convert("L")) > 127
        assert np.array_equal(b.matte(rid), supplied), rid
    assert b.refs["black-side"]["matte_note"] == MATTES["dark_studio"][1]
    from glasses3d.agent.seeing import Seeing          # the gain views: unchanged by the twins (seeing.gain_view_rank)
    gain_views = {cw: g["rid"] for cw, g in Seeing.gain_views(b, (0.0, 0.0, 0.0))[0].items()}
    assert gain_views == {"target": "reference-04", "variant": "black-side"}, gain_views
    rep = {"cameras_json": str(OUT / "cameras.json"), "cameras_json_sha256": sha(OUT / "cameras.json"),
           "fits_sha256": fits_sha, "glossy_sha256": GLOSSY_SHA, "knuckle_check": knuckles,
           "refs": {rid: {"theta_deg": b.theta(rid), "colourway": b.colourway(rid), "fg_px": r["fg_px"],
                          "matte_mode": r["matte_mode"], "matte_note": r.get("matte_note"),
                          "fit_status": r.get("fit_status")}
                    for rid, r in b.refs.items()},
           "transports": {C.ref_id(w["image"]): {k: images[w["image"]]["transport"][k] for k in (
               "source", "target_matte", "scale", "rotation_deg", "mean_bidirectional_px", "rms_bidirectional_px",
               "forward_percentiles_px", "reverse_percentiles_px", "mask_iou_after_warp", "termination",
               "iterations")} | {"camera": images[w["image"]]["camera"]} for w in TWINS},
           "model_checks": {"note": f"{ORIGINAL_MODEL} ({glb_path.name}, sha {glossy['model']['sha256'][:12]}) posed "
                                    "at each photo, against the basis's matte of it; the twins' rows are their "
                                    "records' model_check, their source photos' and black-side's are for comparison",
                            **checks},
           "probe_reproduction": repro, "gain_views": gain_views, "basis_notes": b.meta["notes"]}
    C.write_json(OUT / "report.json", rep)
    return rep


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
