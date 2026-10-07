"""WP5 step 6 proof (2026-10-07): the brow honey patches, the -x temple at yaw +45 and the +x endpiece marks, measured
step by step from the photo to the AR render, for a bake and (optionally) an earlier bake, with AR captures.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.step6_proof --bake DIR [--before DIR]
        [--capture DIR --probe DIR] [--before-capture DIR --before-probe DIR] [--astra-capture DIR --astra-probe DIR]
        [--extra NAME=CAPTURE,PROBE,BAKE ...] [--radiance DIR] [--workers N] [--out JSON]
    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.step6_proof --provenance JSON
        (fix round 3: the numbers PORT_REVIEW's fix round 2 had quoted from unsaved computations, stored)

Every metric is the independent instrument's (``tests/instruments/print_metrics.py``: ``lstar``, ``band_energy``,
``achromatic_highlight``, ``ar_frame_mask``; CIELAB from the shared colour helper). The photoprint package is used only
to rebuild the bake's layout and texels (as ``correction_proof`` does), never for a number.

**Sides by x sign (fix round 3 of step 6).** Each print part's side is the x sign of its area-weighted centroid
(``sides``; the house rule: never the object name), checked to give exactly one brow and one temple per side. Which
parts are brows and which temples is the part class (``ar_levels.GROUPS``).

1. **Texture at the photo's own camera** (``texture_at_photo``): the bake's print.png (as delivered, and as photo
   radiance: the level map undone, ``correction_proof.unlevel``) rendered UNLIT on q001 at the photo's WP3b camera
   (``printproof.render_atlas``, glTF bilinear), against the photo on the same pixels: the part's faces of one facing
   class (brow fronts: normal z dominant; temple outer: |n_x| > 0.5), eroded 2 px, inside the matte eroded 2 px, on the
   photo's acetate (``print_metrics.acetate_like``). Per zone of |x| (brows: nasal < 25 mm, middle 25-45, endpiece
   >= 45; temples: the AR-visible stretch z -104..-9 mm): L* p10 / p50 / std, the 0.5-3 mm band energy, C*ab p50, the
   honey share (C* > 12 and L* > 30), and what painted those pixels (class and source maps rendered the same way). The
   photo is the rigid model the WP3b camera was fitted on, so its faces line up per pixel. Fix round 3: the band
   energy is at the region's own px/mm (its part's point, ``ar_levels.part_points``; step 6 used the front pivot's).
2. **AR at a pose** (``ar_zones``): the capture's beauty render, the black probe's achromatic highlights (the
   renderer's reflections, ``ar_levels``) left out. Brow fronts: the pixels of the brows' front faces, found by
   rasterising q001 through the pose's own asset_to_world and projection (the frame front is rigid in arview: the
   raster lines up with the capture's part-id there, IoU 0.89-0.95). Temples (fix round 3 of step 6): by the capture's
   own part-id alone (``temple_partid``): the runtime spreads the temples, so the rigid raster is misregistered on
   them (``registration``: IoU 0.40 at yaw +-45, centroid about 40 px off), and fix round 2's raster-and-part-id
   ``temple_outer`` rows are gone. Same statistics, each part at its own px/mm.
3. **Renderer** (``renderer_veil``): per pose, on the probe (every print-part base colour black): the share of the
   region's pixels that are reflections, and the reflection light on the rest (L* p50 / p90): what the renderer adds
   whatever the print. Brow fronts through the raster as in section 2; each temple by the capture's part-id (fix round
   3). ``coat_share`` (fix round 3): for a capture graded with both its black probe and its coat-off probe
   (``COAT_PAIRS``), the coat's share of the light on the rest, 1 - Y(coat off) / Y(coat on) of the L* p50s.
4. **Thin light marks** (``light_marks``): pixels of the endpiece blocks (brow faces at |x| > 50 mm, eroded 2 px) whose
   L* white top-hat (5 px) exceeds 8 (the step-5 verdict's measure): every such pixel (``all``), and (fix round 1 of
   step 6: the step-6 report's numbers came from a scratch filter) those off the renderer's reflections, by two named
   masks of the black probe, each dilated 1 px: ``off_probe_light`` (probe L* > ``PROBE_LIGHT_L``, 12: every
   reflection the probe shows, dim ones included; the step-6 report's filter) and ``off_probe_highlight`` (the
   instrument's achromatic-highlight rule, L* > 35 and C*ab < 10: strong reflections only).
5. **Renderer transfer, pixel-wise** (``renderer_transfer``; fix round 1: the step-6 report quoted these from a scratch
   script): every AR pixel of the brow fronts (probe highlights out, as section 2) against ITS OWN albedo, the
   base-colour texture sampled at the pixel's surface point (glTF bilinear: the bake's print.png through the bake's
   layout, or Astra's texture through q001's UVs): L* p50 of each and the median of AR - albedo, the least-squares
   slope of AR L* on albedo L*, the L* std of each, C*ab p50 / p90 of each and the median per-pixel C* ratio (albedo
   C* > 3). Fix round 2 of step 6: brow fronts only. ``registration`` reports the raster's IoU per part and pose.
6. **Temple, region level** (``temple_region``, fix round 2 of step 6): the near temple at yaw +-45 as a difference of
   medians, never per pixel: AR side, the capture's own part-id pixels of that temple (probe highlights out; no
   raster); albedo side, the base colour sampled at four points of every outer face (|n_x| > 0.5) of the temple over
   the AR-visible stretch, area-weighted (the texture as painted, independent of where the runtime draws the arm).
7. **Texture chroma** (``texture_chroma``, fix round 2 of step 6): the delivered print.png against the level-free
   radiance (``--radiance``: a bake made with ``level=None`` from the same inputs, e.g. ``ft1119_bake_radiance_s6b``;
   else the level undone, ``correction_proof.unlevel``, whose convergence is recorded), on the textured texels: per
   radiance C*ab bin the median per-texel C* ratio delivered / radiance, C* p50 / p90 of each, and the off-hue shares
   (C* > 5: off 20-100 deg; magenta 300-360 deg).

**Rasters (fix round 3 of step 6).** A raster depends on the capture's pose transform as well as the pose: the cache
is keyed by ``raster_key`` (the pose, its asset_to_world, projection and image size), never by the pose alone (Astra's
capture has its own fit: scale 1.00709 against 1.00798, asset_to_world up to 0.088 apart).

**Speed and progress (fix round 3 of step 6).** ``run`` hands every (capture, pose) and every bake's photo-camera rows
to an EcoQoS-opted-out process pool (``procs.pool_map``, ``--workers``), which prints progress at least every 30 s;
each bake's level is undone once (``correction_proof._imgs``: a fixed point over the whole atlas), where step 6 to fix
round 2 undid it again for every row and ran everything serially without a line of output.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos, pool_map
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.photoprint import ar_levels as AL
from glasses3d.tests.photoprint import correction_proof as CP
from glasses3d.tests.photoprint import printproof as PP

G3D = Path(__file__).resolve().parents[2]
WP5 = G3D / "data" / "wp5"
BROW_ZONES = {"nasal": (0.0, 25.0), "middle": (25.0, 45.0), "endpiece": (45.0, 80.0), "all": (0.0, 80.0)}
TEMPLE_Z = AL.AR_TEMPLE_Z       # the temples' AR-visible stretch (model z, mm), shared with ar_levels.part_points
BAND_MM = (0.5, 3.0)
HONEY_C, HONEY_L = 12.0, 30.0
BROWS, TEMPLES = AL.GROUPS["brow"], AL.GROUPS["temple"]   # the part CLASS; each part's side comes from its x sign
PROBE_LIGHT_L = 12.0          # light_marks: a black-probe pixel above this L* is a reflection (dim ones included)
TRANSFER = {"pitch_p20": ("brow_front",), "front": ("brow_front",)}    # fix round 2: brows only (rigid in arview)
TEMPLE_REGION = {"yaw_p45": "-x", "yaw_m45": "+x"}                       # the near temple per turned pose
COAT_PAIRS = (("painted", "painted_coat_off_probe"),)    # (coat on, coat off): the same capture under its two probes
C_BINS = (0.0, 1.0, 2.0, 4.0, 8.0, 15.0, 25.0, 60.0)
HUE_BAND = (20.0, 100.0)
MAGENTA = (300.0, 360.0)
TEXTURED = (1, 2, 3, 4, 5, 6, 7, 8)     # classes.png codes of painted texels (0 empty, 9 padding, 10 no texture)
POSES = ("pitch_p20", "front", "yaw_p45", "yaw_m45")


# ----------------------------------------------------------------------------- sides, px/mm
def sides(job) -> dict:
    """{'-x': (brow, temple, -1), '+x': (brow, temple, 1)}: each print part's side by the x sign of its area-weighted
    centroid, never by its name (``ar_levels.part_sides``); raises unless each side has exactly one brow and one
    temple (a model whose parts sit otherwise must be looked at, not guessed)."""
    return AL.part_sides(job)


def part_ppm_ar(job, cap: Path, pose: str, part: str) -> float:
    """The capture's px/mm at ``part``'s own point (``ar_levels.part_points``, ``ar_levels.capture_ppm``)."""
    return AL.capture_ppm(cap, pose, AL.part_points(job)[part])


def part_ppm_photo(job, view: str, part: str) -> float:
    """The photo's px/mm at ``part``'s own point through its WP3b camera (``inputs.ppm_local``)."""
    return float(IN.ppm_local(job.views[view].camera, job.frame, np.asarray(AL.part_points(job)[part], float)[None])[0])


def _stats(img, E, sup, ppm) -> dict:
    lab = T.lab_of(np.asarray(img, float).reshape(-1, 3)).reshape(np.asarray(img).shape)
    L, C = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2])
    bp = PM.bandpass(L, sup, BAND_MM[0] * ppm, BAND_MM[1] * ppm)
    if E.sum() < 30:
        return {"n": int(E.sum())}
    return {"n": int(E.sum()), "L_p10": round(float(np.percentile(L[E], 10)), 1),
            "L_p50": round(float(np.median(L[E])), 1), "L_p90": round(float(np.percentile(L[E], 90)), 1),
            "L_std": round(float(L[E].std()), 2), "band_energy": round(float(bp[E].std()), 2),
            "C_p50": round(float(np.median(C[E])), 2), "C_p90": round(float(np.percentile(C[E], 90)), 2),
            "honey_share": round(float(((C[E] > HONEY_C) & (L[E] > HONEY_L)).mean()), 3)}


def _face_maps(job, fid):
    nf = len(job.mesh.F)
    ok = (fid >= 0) & (fid < nf)
    f = np.clip(fid, 0, nf - 1)
    return ok, f, job.face_normals, job.mesh.V[job.mesh.F].mean(1)


def _region_masks(job, fid, ok, f, fn, fc, side: str):
    brow, temple, sg = sides(job)[side]
    part = job.mesh.part[f]
    front = ok & np.isin(part, job.part_ids([brow])) & (fn[f, 2] > np.abs(fn[f, 1])) & (fn[f, 2] > 0.5)
    outer = (ok & np.isin(part, job.part_ids([temple])) & (fn[f, 0] * sg > 0.5) & (fc[f, 2] >= TEMPLE_Z[0])
             & (fc[f, 2] <= TEMPLE_Z[1]))
    return {"brow_front": front, "temple_outer": outer}, np.abs(fc[f, 0])


# ----------------------------------------------------------------------------- 1. texture at the photo's camera
def texture_at_photo(job, bake: Path, view: str, side: str, which: str, loaded=None, imgs=None) -> dict:
    """Section 1 for one photo, side and region ('brow_front' or 'temple_outer'; the photo camera renders the rigid
    model it was fitted on, so the faces line up per pixel). ``imgs``: ``correction_proof._imgs(bake)`` when the caller
    has it (fix round 3: it undoes the level, a fixed point over the whole atlas, and was recomputed for every row)."""
    jb, rep, lay, regions, tex, occ, tx = loaded or CP._load(bake)
    imgs = imgs if imgs is not None else CP._imgs(bake)
    v = job.views[view]
    sc = occ.scene(job.mesh.V)
    r = PP.render_atlas(job, lay, imgs["print_levelled"].astype(float), v.camera, v.shape, sc)
    rr = PP.render_atlas(job, lay, imgs["print"].astype(float), v.camera, v.shape, sc)
    cs = PP.render_atlas(job, lay, np.stack([imgs["classes"], imgs["source"], imgs["classes"]], -1).astype(float),
                         v.camera, v.shape, sc)
    fid = sc.render(v.camera, v.shape, 1)["face_id"]
    ok, f, fn, fc = _face_maps(job, fid)
    masks, ax = _region_masks(job, fid, ok, f, fn, fc, side)
    inside = ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=2)
    sup = ndimage.binary_erosion(masks[which], iterations=2) & inside
    photo = v.image.astype(float)
    E0 = sup & PM.acetate_like(photo) & r["mask"]
    brow, temple, _sg = sides(job)[side]
    ppm = part_ppm_photo(job, view, brow if which == "brow_front" else temple)
    zones = BROW_ZONES if which == "brow_front" else {"stretch": (0.0, 999.0)}
    cl, srcm = np.rint(cs["ren"][..., 0]).astype(int), np.rint(cs["ren"][..., 1]).astype(int)
    out = {"view": IN.short_name(view), "side": side, "region": which, "ppm": round(ppm, 3),
           "ppm_at": "the part's own point (ar_levels.part_points)", "ppm_pivot": round(float(job.ppm(view)), 3)}
    for zn, (a, b) in zones.items():
        E = E0 & (ax >= a) & (ax < b)
        row = {"photo": _stats(photo, E, sup, ppm), "texture_radiance": _stats(rr["ren"], E, sup, ppm),
               "texture_delivered": _stats(r["ren"], E, sup, ppm)}
        n = max(int(E.sum()), 1)
        row["classes"] = {"photo": round(float(((cl == 2) & E).sum() / n), 3),
                          "fill": round(float(((cl == 4) & E).sum() / n), 3),
                          "highlight_fill": round(float(((cl == 6) & E).sum() / n), 3),
                          "text_fill": round(float(((cl == 7) & E).sum() / n), 3)}
        row["painted_by"] = {f"{k:02d}": round(float(((cl == 2) & (srcm == k) & E).sum() / n), 3)
                             for k in range(1, 7) if ((cl == 2) & (srcm == k) & E).any()}
        out[zn] = row
    return out


# ----------------------------------------------------------------------------- the AR raster (brows only)
def _pose_of(cap: Path, pose: str) -> dict:
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    return next(q for q in rep["poses"] if q["id"] == pose)


def raster_key(cap: Path, pose: str) -> tuple:
    """What ``ar_raster`` depends on besides the mesh: the pose, its asset_to_world and projection (rounded to 1e-6)
    and the image size. Two captures share a raster only when their keys are equal (fix round 3 of step 6)."""
    p = _pose_of(cap, pose)
    r6 = lambda a: tuple(float(x) for x in np.round(np.asarray(a, float).ravel(), 6))
    return (pose, r6(p["asset_to_world"]), r6(p["camera"]["projection"]), int(p["camera"]["width"]),
            int(p["camera"]["height"]))


def _key_sha(key: tuple) -> str:
    return hashlib.sha256(json.dumps(key).encode("utf-8")).hexdigest()[:12]


def ar_raster(cap: Path, pose: str, job, with_bary: bool = False):
    """(H, W) face id of q001 through the capture pose's asset_to_world and projection (back to front, faces turned
    toward the camera), -1 elsewhere (and the (H, W, 3) barycentrics when ``with_bary``). Rigid: right on the frame
    front, misregistered on the temples the runtime spreads (``registration``)."""
    p = _pose_of(cap, pose)
    M = np.asarray(p["asset_to_world"], float).reshape(4, 4).T
    P = np.asarray(p["camera"]["projection"], float).reshape(4, 4).T
    W, H = p["camera"]["width"], p["camera"]["height"]
    V, F = job.mesh.V, job.mesh.F
    c = (P @ M @ np.c_[V / 1000.0, np.ones(len(V))].T).T
    xy = np.stack([(c[:, 0] / c[:, 3] + 1) / 2 * W, (1 - c[:, 1] / c[:, 3]) / 2 * H], 1)
    tri = xy[F]
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    area = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    ref = np.isin(job.mesh.part, job.part_ids(list(BROWS))) & (job.face_normals[:, 2] > 0.9)
    keep = np.nonzero(area * np.sign(np.median(area[ref])) > 0)[0]
    keep = keep[np.argsort(-c[:, 3][F[keep]].mean(1))]
    fidk, bary = T.rasterize_triangles(tri[keep] - 0.5, (H, W))
    out = np.full((H, W), -1, np.int64)
    m = fidk >= 0
    out[m] = keep[fidk[m]]
    return (out, bary) if with_bary else out


class RasterCache:
    """``ar_raster`` results keyed by ``raster_key`` (fix round 3 of step 6: step 6 keyed them by the pose alone and
    reused one capture's raster for every capture)."""

    def __init__(self):
        self.store, self.hits, self.misses = {}, 0, 0

    def get(self, cap: Path, pose: str, job):
        k = raster_key(cap, pose)
        if k in self.store:
            self.hits += 1
        else:
            self.misses += 1
            self.store[k] = ar_raster(Path(cap), pose, job, with_bary=True)
        return self.store[k], k


def ppm_of(cap: Path, pose: str) -> float:
    return AL.capture_ppm(cap, pose)


def _probe_highlights(probe: Path, pose: str) -> np.ndarray:
    pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB"))
    return PM.achromatic_highlight(pb.reshape(-1, 3)).reshape(pb.shape[:2])


# ----------------------------------------------------------------------------- 2. AR zones
def ar_zones(job, cap: Path, probe: Path, pose: str, fid=None) -> dict:
    """Section 2: per side, the brow front zones (raster: rigid, registered) and the temple by the capture's own
    part-id (the runtime spreads the arms), each at its part's own px/mm."""
    cap, probe = Path(cap), Path(probe)
    fid = ar_raster(cap, pose, job) if fid is None else fid
    ok, f, fn, fc = _face_maps(job, fid)
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    img = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB")).astype(float)
    hl = _probe_highlights(probe, pose)
    out = {"ppm_bridge": round(ppm_of(cap, pose), 3), "ppm": {}}
    for side, (brow, temple, _sg) in sides(job).items():
        masks, ax = _region_masks(job, fid, ok, f, fn, fc, side)
        pb_, pt_ = part_ppm_ar(job, cap, pose, brow), part_ppm_ar(job, cap, pose, temple)
        out["ppm"][brow], out["ppm"][temple] = round(pb_, 3), round(pt_, 3)
        m = masks["brow_front"] & PM.ar_frame_mask(pid, rep["id_palette"], (brow,))
        E0 = m & ~hl
        row = {"brow_front": {"renderer_highlight_share": round(float((m & hl).sum() / max(int(m.sum()), 1)), 3)}}
        for zn, (a, b) in BROW_ZONES.items():
            row["brow_front"][zn] = _stats(img, E0 & (ax >= a) & (ax < b), E0, pb_)
        # the temple by the capture's own part-id alone (no raster; fix round 3: the raster rows are gone)
        tm_all = PM.ar_frame_mask(pid, rep["id_palette"], (temple,))
        tm = tm_all & ~hl
        hs = float((tm_all & hl).sum() / max(int(tm_all.sum()), 1))
        row["temple_partid"] = {"part": temple, "px": int(tm_all.sum()), "renderer_highlight_share": round(hs, 3),
                                "stretch": _stats(img, tm, tm, pt_)}
        out[side] = row
    return out


def registration(job, cap: Path, pose: str, fid=None) -> dict:
    """Fix round 2 of step 6: per print part, how well the rigid q001 raster (``ar_raster``) lines up with the
    capture's own part-id mask at ``pose``: IoU and the centroid offset in px (the temples are spread by the runtime,
    so per-pixel temple comparisons through the raster pair mismatched pixels)."""
    fid = ar_raster(Path(cap), pose, job) if fid is None else fid
    ok, f, _fn, _fc = _face_maps(job, fid)
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(Path(cap) / f"{pose}__part-id.png").convert("RGB"))
    out = {}
    for part in IN.FT1119_PARTS:
        r = ok & np.isin(job.mesh.part[f], job.part_ids([part]))
        m = PM.ar_frame_mask(pid, rep["id_palette"], (part,), erode=0)
        if r.sum() < 50 or m.sum() < 50:
            out[part] = {"raster_px": int(r.sum()), "partid_px": int(m.sum())}
            continue
        cr, cm = np.argwhere(r).mean(0), np.argwhere(m).mean(0)
        out[part] = {"raster_px": int(r.sum()), "partid_px": int(m.sum()),
                     "iou": round(float((r & m).sum() / (r | m).sum()), 3),
                     "centroid_offset_px": round(float(np.linalg.norm(cr - cm)), 1)}
    return out


def _wmedian(x, w):
    o = np.argsort(x)
    x, w = np.asarray(x, float)[o], np.asarray(w, float)[o]
    c = np.cumsum(w) - 0.5 * w
    return float(np.interp(0.5, c / w.sum(), x))


def _wq(x, w, q):
    o = np.argsort(x)
    x, w = np.asarray(x, float)[o], np.asarray(w, float)[o]
    c = np.cumsum(w) - 0.5 * w
    return float(np.interp(q / 100.0, c / w.sum(), x))


def temple_region(job, cap: Path, probe: Path, pose: str, src: dict) -> dict | None:
    """Section 6 at a turned pose (``TEMPLE_REGION``): the near temple, AR part-id pixels (probe highlights out)
    against the base colour over the temple's outer faces in the AR-visible stretch (texel domain, area-weighted):
    medians and their differences only."""
    side = TEMPLE_REGION.get(pose)
    if side is None:
        return None
    _brow, temple, sg = sides(job)[side]
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(Path(cap) / f"{pose}__part-id.png").convert("RGB"))
    img = np.asarray(Image.open(Path(cap) / f"{pose}__skin__beauty.png").convert("RGB")).astype(float)
    hl = _probe_highlights(probe, pose)
    m = PM.ar_frame_mask(pid, rep["id_palette"], (temple,)) & ~hl
    if m.sum() < 100:
        return None
    ar = img[m]
    la = T.lab_of(ar)
    Ca = np.hypot(la[:, 1], la[:, 2])
    fn, fc = job.face_normals, job.mesh.V[job.mesh.F].mean(1)
    faces = np.nonzero(np.isin(job.mesh.part, job.part_ids([temple])) & (fn[:, 0] * sg > 0.5)
                       & (fc[:, 2] >= TEMPLE_Z[0]) & (fc[:, 2] <= TEMPLE_Z[1]))[0]
    V = job.mesh.V[job.mesh.F[faces]]
    area = 0.5 * np.linalg.norm(np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0]), axis=1)
    B = np.array([[1 / 3, 1 / 3, 1 / 3], [2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]])
    fid = np.repeat(faces, len(B))
    bary = np.tile(B, (len(faces), 1))
    alb = albedo_at(job, fid, bary, src)
    w = np.repeat(area / len(B), len(B))
    good = np.isfinite(alb).all(1) & (w > 0)
    alb, w = alb[good], w[good]
    lb = T.lab_of(alb)
    Cb = np.hypot(lb[:, 1], lb[:, 2])
    Lb = PM.lstar(alb)
    La = PM.lstar(ar)
    out = {"side": side, "part": temple, "ar_px": int(m.sum()), "albedo_samples": int(good.sum()),
           "ar_L_p50": round(float(np.median(La)), 1), "albedo_L_p50": round(_wmedian(Lb, w), 1),
           "ar_C_p50": round(float(np.median(Ca)), 2), "albedo_C_p50": round(_wmedian(Cb, w), 2),
           "ar_C_p90": round(float(np.percentile(Ca, 90)), 2), "albedo_C_p90": round(_wq(Cb, w, 90), 2)}
    out["ar_minus_albedo_L_p50"] = round(out["ar_L_p50"] - out["albedo_L_p50"], 2)
    out["ar_over_albedo_C_p50"] = round(out["ar_C_p50"] / out["albedo_C_p50"], 3) if out["albedo_C_p50"] else None
    out["ar_over_albedo_C_p90"] = round(out["ar_C_p90"] / out["albedo_C_p90"], 3) if out["albedo_C_p90"] else None
    return out


def _hue(lab):
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0


# ----------------------------------------------------------------------------- 7. texture chroma
def texture_chroma_arrays(dl: np.ndarray, rad: np.ndarray, cls: np.ndarray) -> dict:
    """Section 7 on arrays: ``dl`` (delivered) against ``rad`` (radiance) on the texels whose class is ``TEXTURED``."""
    m = np.isin(cls, TEXTURED)
    a, b = T.lab_of(np.asarray(dl, float)[m]), T.lab_of(np.asarray(rad, float)[m])
    Ca, Cb = np.hypot(a[:, 1], a[:, 2]), np.hypot(b[:, 1], b[:, 2])
    bins = {}
    for lo, hi in zip(C_BINS[:-1], C_BINS[1:]):
        k = (Cb >= lo) & (Cb < hi) & (Cb > 0.05)
        if k.sum() >= 100:
            bins[f"{lo:g}-{hi:g}"] = {"texels": int(k.sum()), "median_ratio": round(float(np.median(Ca[k] / Cb[k])), 3)}

    def shares(lab, C):
        h = _hue(lab)
        ch = C > 5.0
        off = ch & ~((h >= HUE_BAND[0]) & (h <= HUE_BAND[1]))
        mag = ch & (h >= MAGENTA[0])
        n = max(int(ch.sum()), 1)
        return {"off_band_of_C5": round(float(off.sum() / n), 4), "magenta_of_C5": round(float(mag.sum() / n), 4),
                "off_band_of_all": round(float(off.mean()), 4), "magenta_of_all": round(float(mag.mean()), 4),
                "C5_share": round(float(ch.mean()), 4),
                "C_p50": round(float(np.median(C)), 2), "C_p90": round(float(np.percentile(C, 90)), 2)}

    return {"texels": int(m.sum()), "ratio_by_radiance_C": bins, "delivered": shares(a, Ca),
            "radiance_stats": shares(b, Cb),
            "note": "off-band: C* > 5 and hue outside 20-100 deg; magenta: C* > 5 and hue 300-360 deg; '_of_C5' = "
                    "share of the texels with C* > 5, '_of_all' = share of every textured texel"}


def texture_chroma(bake: Path, radiance: Path | None = None) -> dict:
    """Section 7: the delivered print.png against the level-free radiance on the textured texels."""
    bake = Path(bake)
    dl = np.asarray(Image.open(bake / "print.png").convert("RGB")).astype(float)
    cls = np.asarray(Image.open(bake / "classes.png"))
    unl = None
    if radiance is not None:
        rad = np.asarray(Image.open(Path(radiance) / "print.png").convert("RGB")).astype(float)
        src = f"{radiance} (level-free bake)"
    else:
        imgs = CP._imgs(bake)
        rad, unl = imgs["print"], imgs["unlevel"]
        src = "the level undone (correction_proof.unlevel)"
    return {"bake": str(bake), "radiance": src, **texture_chroma_arrays(dl, rad, cls),
            **({"unlevel": unl} if unl is not None else {})}


# ----------------------------------------------------------------------------- 3. renderer, 4. light marks
def _y_of_l(L: float) -> float:
    """CIE relative luminance Y (white 1) of an L* value."""
    f = (float(L) + 16.0) / 116.0
    return f ** 3 if f > 6.0 / 29.0 else 3 * (6.0 / 29.0) ** 2 * (f - 4.0 / 29.0)


def renderer_veil(job, probe: Path, cap: Path, pose: str, fid=None) -> dict:
    """Section 3: the probe's reflections on the brow fronts (raster: rigid, registered) and on each temple (the
    capture's part-id; fix round 3: the raster's temple faces were misregistered) at ``pose``."""
    fid = ar_raster(Path(cap), pose, job) if fid is None else fid
    ok, f, fn, fc = _face_maps(job, fid)
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(Path(cap) / f"{pose}__part-id.png").convert("RGB"))
    pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB")).astype(float)
    hl = PM.achromatic_highlight(pb.reshape(-1, 3)).reshape(pb.shape[:2])
    L = PM.lstar(pb)
    regions = {}
    m = np.zeros(fid.shape, bool)
    for side, (brow, _temple, _sg) in sides(job).items():
        masks, _ax = _region_masks(job, fid, ok, f, fn, fc, side)
        m |= masks["brow_front"] & PM.ar_frame_mask(pid, rep["id_palette"], (brow,))
    regions["brow_front"] = m
    for side, (_brow, temple, _sg) in sides(job).items():
        regions[f"temple_partid {side}"] = PM.ar_frame_mask(pid, rep["id_palette"], (temple,))
    out = {}
    for name, m in regions.items():
        if m.sum() < 100:
            continue
        rest = m & ~hl
        out[name] = {"px": int(m.sum()), "reflection_share": round(float((m & hl).sum() / m.sum()), 3),
                     "reflection_L_p50_on_the_rest": round(float(np.median(L[rest])), 1),
                     "reflection_L_p90_on_the_rest": round(float(np.percentile(L[rest], 90)), 1)}
    return out


def coat_share(coat: dict, nocoat: dict) -> dict:
    """Per region of two ``renderer_veil`` rows of the same capture (black probe with / without the clearcoat): the
    coat's share of the light on the rest, 1 - Y(L* p50 coat off) / Y(L* p50 coat on) (linear luminance)."""
    out = {}
    for k, a in coat.items():
        b = nocoat.get(k)
        if not b:
            continue
        ya, yb = _y_of_l(a["reflection_L_p50_on_the_rest"]), _y_of_l(b["reflection_L_p50_on_the_rest"])
        out[k] = {"L_p50_coat": a["reflection_L_p50_on_the_rest"], "L_p50_no_coat": b["reflection_L_p50_on_the_rest"],
                  "reflection_share_coat": a["reflection_share"], "reflection_share_no_coat": b["reflection_share"],
                  "coat_share_of_light": round(1.0 - yb / ya, 3) if ya > 0 else None}
    return out


def probe_reflections(probe: Path, pose: str) -> dict:
    """The black probe's reflection masks at ``pose``, each dilated 1 px: 'light' (L* > ``PROBE_LIGHT_L``) and
    'highlight' (the instrument's achromatic-highlight rule)."""
    pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB")).astype(float)
    hl = PM.achromatic_highlight(pb.reshape(-1, 3)).reshape(pb.shape[:2])
    return {"light": ndimage.binary_dilation(PM.lstar(pb) > PROBE_LIGHT_L, iterations=1),
            "highlight": ndimage.binary_dilation(hl, iterations=1)}


def light_marks(job, cap: Path, pose: str, fid=None, probe: Path | None = None) -> dict:
    """Section 4: thin light marks on each endpiece block (white top-hat of L* at 5 px > 8, block eroded 2 px): all of
    them, and (with the capture's black ``probe``) those off its reflections by each named mask."""
    fid = ar_raster(Path(cap), pose, job) if fid is None else fid
    ok, f, fn, fc = _face_maps(job, fid)
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(Path(cap) / f"{pose}__part-id.png").convert("RGB"))
    L = PM.lstar(np.asarray(Image.open(Path(cap) / f"{pose}__skin__beauty.png").convert("RGB")).astype(float))
    refl = probe_reflections(probe, pose) if probe is not None else {}
    out = {"masks": {"off_probe_light": f"black probe L* > {PROBE_LIGHT_L:g}, dilated 1 px",
                     "off_probe_highlight": "black probe achromatic highlight (L* > 35, C*ab < 10), dilated 1 px"}}
    for side, (brow, _t, sg) in sides(job).items():
        blk = (PM.ar_frame_mask(pid, rep["id_palette"], (brow,)) & ok & np.isin(job.mesh.part[f], job.part_ids([brow]))
               & (fc[f, 0] * sg > 50))
        inner = ndimage.binary_erosion(blk, iterations=2)
        th = ndimage.white_tophat(np.where(blk, L, 0.0), size=5)
        mk = inner & (th > 8)
        row = {"block_px": int(inner.sum()), "marks": int(mk.sum())}
        for name, m in refl.items():
            row[f"off_probe_{name}"] = int((mk & ~m).sum())
            row[f"block_px_off_probe_{name}"] = int((inner & ~m).sum())
        out[side] = row
    return out


# ----------------------------------------------------------------------------- 5. transfer
def albedo_at(job, fid, bary, src: dict) -> np.ndarray:
    """(n, 3) base colour (sRGB) at the pixels ``fid >= 0`` ... given as (face ids, barycentrics) rows: ``src`` =
    {'kind': 'bake', 'img', 'layout'} (print.png through the bake's layout, glTF bilinear, written here) or
    {'kind': 'astra'} (Astra's own texture through q001's UVs)."""
    if src["kind"] == "astra":
        return job.astra.colours(job.mesh.part, fid, bary[:, 1:])
    lay = src["layout"]
    rows = lay.face_index(len(job.mesh.F))[fid]
    out = np.full((len(fid), 3), np.nan)
    good = rows >= 0
    uv = np.einsum("kj,kjd->kd", bary[good], np.asarray(lay.uv, float)[rows[good]])
    out[good] = PM.sample_gltf(src["img"], uv)
    return out


def renderer_transfer(job, cap: Path, probe: Path, pose: str, src: dict, raster=None) -> dict:
    """Section 5 at ``pose``: per side and region of ``TRANSFER``, AR pixels against their own albedo."""
    fid, bary = ar_raster(Path(cap), pose, job, with_bary=True) if raster is None else raster
    ok, f, fn, fc = _face_maps(job, fid)
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(Path(cap) / f"{pose}__part-id.png").convert("RGB"))
    img = np.asarray(Image.open(Path(cap) / f"{pose}__skin__beauty.png").convert("RGB")).astype(float)
    hl = _probe_highlights(probe, pose)
    out = {}
    for side, (brow, temple, _sg) in sides(job).items():
        masks, _ax = _region_masks(job, fid, ok, f, fn, fc, side)
        for which in TRANSFER.get(pose, ()):
            m = masks[which] & PM.ar_frame_mask(pid, rep["id_palette"], (brow if which == "brow_front" else temple,)) & ~hl
            ii, jj = np.nonzero(m)
            alb = albedo_at(job, f[ii, jj], np.asarray(bary[ii, jj], float), src)
            good = np.isfinite(alb).all(1)
            if good.sum() < 100:
                continue
            ar, alb = img[ii[good], jj[good]], alb[good]
            La, Lb = PM.lstar(ar), PM.lstar(alb)
            la, lb = T.lab_of(ar), T.lab_of(alb)
            Ca, Cb = np.hypot(la[:, 1], la[:, 2]), np.hypot(lb[:, 1], lb[:, 2])
            slope = np.linalg.lstsq(np.c_[np.ones(len(Lb)), Lb], La, rcond=None)[0]
            cm = Cb > 3
            out[f"{which} {side}"] = {
                "n": int(good.sum()), "albedo_L_p50": round(float(np.median(Lb)), 1),
                "ar_L_p50": round(float(np.median(La)), 1), "ar_minus_albedo_L_p50": round(float(np.median(La - Lb)), 2),
                "ar_L_on_albedo_L": [round(float(x), 3) for x in slope],
                "albedo_L_std": round(float(Lb.std()), 2), "ar_L_std": round(float(La.std()), 2),
                "albedo_C_p50": round(float(np.median(Cb)), 2), "ar_C_p50": round(float(np.median(Ca)), 2),
                "albedo_C_p90": round(float(np.percentile(Cb, 90)), 2), "ar_C_p90": round(float(np.percentile(Ca, 90)), 2),
                "C_ratio_median": round(float(np.median(Ca[cm] / Cb[cm])), 3) if cm.sum() >= 30 else None}
    return out


# ----------------------------------------------------------------------------- the pool jobs
_W: dict = {}       # per worker process: the job, layouts by tx, albedo images by bake, the raster cache


def _wjob():
    if "job" not in _W:
        opt_out_ecoqos()
        _W["job"] = IN.load_job(IN.FT1119["cameras"])
        _W["rasters"] = RasterCache()
    return _W["job"]


def _layout(job, bake: Path):
    """The bake's layout alone (``correction_proof._load``'s first step, without regions, texels or occluders)."""
    from glasses3d.g3d import printuv as PU
    from glasses3d.g3d_host.photoprint import project as PR
    rep = json.loads((Path(bake) / "report.json").read_text(encoding="utf-8"))
    tx = float(rep["layout"]["tx_per_mm"])
    key = ("layout", tx)
    if key not in _W:
        _W[key] = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, list(IN.FT1119_PARTS), tx,
                            atlas_max=PR.ATLAS_MAX, mirror=True)
    return _W[key]


def _src(job, alb) -> dict | None:
    if alb is None:
        return None
    if alb == "astra":
        return {"kind": "astra"}
    b = Path(alb)
    key = ("albedo", str(b))
    if key not in _W:
        _W[key] = np.asarray(Image.open(b / "print.png").convert("RGB")).astype(np.float64)
    return {"kind": "bake", "img": _W[key], "layout": _layout(job, b), "bake": str(b)}


def _bake_job(name: str, bake: str) -> dict:
    """Section 1 for one bake: the level undone ONCE, then the four photo-camera rows."""
    b = Path(bake)
    loaded = CP._load(b)
    job = loaded[0]
    imgs = CP._imgs(b)
    rows = {}
    for side in ("-x", "+x"):
        rows[f"brow_front {side} @05"] = texture_at_photo(job, b, "reference-05.png", side, "brow_front", loaded, imgs)
    rows["temple_outer -x @03"] = texture_at_photo(job, b, "reference-03.png", "-x", "temple_outer", loaded, imgs)
    rows["temple_outer +x @06"] = texture_at_photo(job, b, "reference-06.png", "+x", "temple_outer", loaded, imgs)
    return {"kind": "bake", "name": name, "bake": str(b),
            "level": json.loads((b / "report.json").read_text(encoding="utf-8")).get("level"),
            "unlevel": imgs["unlevel"], "sides": {k: list(v) for k, v in sides(job).items()}, "rows": rows}


def _ar_job(name: str, cap: str, probe: str, alb, pose: str) -> dict:
    """Sections 2-6 for one capture at one pose (the raster from this worker's ``RasterCache``)."""
    job = _wjob()
    cap, probe = Path(cap), Path(probe)
    (fid, bary), key = _W["rasters"].get(cap, pose, job)
    src = _src(job, alb)
    r = {"raster_key": _key_sha(key), "zones": ar_zones(job, cap, probe, pose, fid),
         "light_marks": light_marks(job, cap, pose, fid, probe), "renderer": renderer_veil(job, probe, cap, pose, fid),
         "registration": registration(job, cap, pose, fid)}
    if src is not None and pose in TRANSFER:
        r["transfer"] = renderer_transfer(job, cap, probe, pose, src, (fid, bary))
    if src is not None and pose in TEMPLE_REGION:
        r["temple_region"] = temple_region(job, cap, probe, pose, src)
    return {"kind": "ar", "name": name, "pose": pose, "result": r}


def _job(kind: str, *args) -> dict:
    return _bake_job(*args) if kind == "bake" else _ar_job(*args)


def run(bake: Path, before: Path | None = None, caps: dict | None = None, radiance: Path | None = None,
        workers: int = 1, log=print) -> dict:
    """``caps``: {name: (capture, probe[, albedo])}, albedo = a bake directory (its print.png through its layout),
    'astra', or None (no sections 5 and 6). ``radiance``: the level-free bake for section 7. ``workers``: the process
    pool's size (``procs.pool_map``; 1 = serial, still with a progress line at least every 30 s)."""
    prog = Progress("step6_proof", log=log)
    prog("texture chroma (section 7)", force=True)
    out = {"bakes": {}, "ar": {}, "texture_chroma": {"after": texture_chroma(bake, radiance)}}
    if before is not None:
        out["texture_chroma"]["before"] = texture_chroma(before, radiance)
    jobs = [("bake", name, str(b)) for name, b in (("after", bake), ("before", before)) if b is not None]
    meta = {}
    for name, spec in (caps or {}).items():
        cap, probe = Path(spec[0]), Path(spec[1])
        alb = spec[2] if len(spec) > 2 else None
        alb = None if alb is None else (alb if alb == "astra" else str(alb))
        meta[name] = {"capture": str(cap), "probe": str(probe), "albedo": alb, "poses": {}}
        jobs += [("ar", name, str(cap), str(probe), alb, pose) for pose in POSES
                 if (cap / f"{pose}__part-id.png").is_file()]
    prog(f"{len(jobs)} jobs ({sum(j[0] == 'bake' for j in jobs)} bakes, {sum(j[0] == 'ar' for j in jobs)} capture x "
         f"pose) on {max(1, int(workers))} worker(s)", force=True)
    res = pool_map(_job, jobs, workers=max(1, int(workers)), progress=prog, tag="step6_proof")
    for r in res:
        if r["kind"] == "bake":
            out["bakes"][r["name"]] = {k: r[k] for k in ("bake", "level", "unlevel", "sides", "rows")}
        else:
            meta[r["name"]]["poses"][r["pose"]] = r["result"]
    for name, m in meta.items():
        m["poses"] = {p: m["poses"][p] for p in POSES if p in m["poses"]}
    out["ar"] = meta
    keys = {}
    for name, m in meta.items():
        for p, r in m["poses"].items():
            keys.setdefault(p, {}).setdefault(r["raster_key"], []).append(name)
    out["raster_keys"] = keys                 # which captures shared a raster, per pose (fix round 3)
    out["coat_share"] = {}
    for on, off in COAT_PAIRS:
        if on in meta and off in meta and meta[on]["capture"] == meta[off]["capture"]:
            out["coat_share"][f"{on} / {off}"] = {p: coat_share(meta[on]["poses"][p]["renderer"],
                                                                meta[off]["poses"][p]["renderer"])
                                                  for p in meta[on]["poses"] if p in meta[off]["poses"]}
    prog("done", force=True)
    return out


# ----------------------------------------------------------------------------- provenance (fix round 3 of step 6)
def _decode_png(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def _dark_phase_bins(pa, la_mask, pp, lp_mask) -> dict:
    """C*ab p50 (and pixel counts) of the AR and the photo pixels in the L* bins 15-20 and 20-25."""
    out = {}
    for lo, hi in ((15.0, 20.0), (20.0, 25.0)):
        row = {}
        for nm, px, keep in (("ar", pa, la_mask), ("photo", pp, lp_mask)):
            x = np.asarray(px, np.float64)[keep]
            L = PM.lstar(x)
            lab = T.lab_of(x)
            C = np.hypot(lab[:, 1], lab[:, 2])
            k = (L >= lo) & (L < hi)
            row[nm] = {"n": int(k.sum()), "C_p50": round(float(np.median(C[k])), 2) if k.any() else None}
        out[f"L {lo:g}-{hi:g}"] = row
    return out


def provenance(log=print) -> dict:
    """Fix round 3 of step 6: the numbers PORT_REVIEW's fix round 2 had quoted from unsaved computations, recomputed
    from the stored captures and bakes: fix round 1's hue terms (under fix round 2's instrument and the corrected one),
    the brows' dark-phase chroma at pitch +20, the texture ratios of the delivered L* level without its chroma, whether
    fix round 1's level re-applied to the level-free bake reproduces fix round 1's print.png, and the all-pairs
    candidate's texture C* p90."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    prog = Progress("provenance", log=log)
    job = IN.load_job(IN.FT1119["cameras"])
    rad_bake = WP5 / "ft1119_bake_radiance_s6b"
    caps = {"delivered": (WP5 / "tooltest/ar_painted/capture", WP5 / "tooltest/ar_painted/probe"),
            "fix_round_1": (WP5 / "pre_s6fr2_tooltest/ar_painted/capture", WP5 / "pre_s6fr2_tooltest/ar_painted/probe")}
    out = {"captures": {k: [str(c), str(p)] for k, (c, p) in caps.items()}, "radiance_bake": str(rad_bake)}
    photos = {}
    out["hue"] = {}
    for inst, ms in (("fix_round_2_instrument", 0.0), ("corrected_instrument", AL.MIN_PHOTO_GROUP_SHARE)):
        prog(f"ar_levels, {inst}", force=True)
        m = AL.measure([c for c, _p in caps.values()], job=job, probes={str(c): str(p) for c, p in caps.values()},
                       photos=photos, min_photo_share=ms)
        out["hue"][inst] = {}
        for name, (c, _p) in caps.items():
            row = m["captures"][str(c)]
            rr = {}
            for k, r in row.items():
                if not isinstance(r, dict) or "role" not in r or "missing" in r:
                    continue
                ap = r[AL.TARGET]["appearance"]
                h = ap["hue"]
                rr[k] = {"role": r["role"], "groups_excluded": sorted(r.get("groups_excluded") or {}),
                         "hue_p50_ar": h["ar"]["p50"], "hue_p50_photo": h["photo"]["p50"],
                         "hue_p50_ar_minus_photo": h["p50_ar_minus_photo"],
                         "magenta_ar": h["ar"]["magenta_share"], "magenta_photo": h["photo"]["magenta_share"],
                         "hue_p50_pass": ap["criteria"]["hue_p50"], "hue_magenta_pass": ap["criteria"]["hue_magenta"]}
            out["hue"][inst][name] = rr
    prog("dark-phase chroma bins (brows, pitch +20 vs 05)", force=True)
    pp, gp = AL.photo_pixels(job, "reference-05.png", cache=photos.get(AL.CACHE_KEY))
    p_keep = (gp == 0) & ~PM.achromatic_highlight(pp)
    out["dark_phase_brows_pitch_p20_vs_05"] = {}
    for name, (c, p) in caps.items():
        pa, ga, ha = AL.capture_pixels(c, "pitch_p20", p)
        out["dark_phase_brows_pitch_p20_vs_05"][name] = _dark_phase_bins(pa, (ga == 0) & ~ha, pp, p_keep)
    prog("texture levels (no chroma, fix round 1, all-pairs)", force=True)
    rad = np.asarray(Image.open(rad_bake / "print.png").convert("RGB")).astype(float)
    cls = np.asarray(Image.open(rad_bake / "classes.png"))
    lv_now = json.loads((WP5 / "ft1119_bake/report.json").read_text(encoding="utf-8"))["level"]
    lv_fr1 = json.loads((WP5 / "pre_s6fr2_bake/report.json").read_text(encoding="utf-8"))["level"]
    cal = json.loads((WP5 / "ar_calibration/calibration.json").read_text(encoding="utf-8"))
    lv_all = cal["candidates"]["all_pairs_chroma"]["level"]
    lv_l = {"gain": lv_now["gain"], "gamma": lv_now["gamma"]}
    tex = {}
    for nm, lv in (("delivered", lv_now), ("delivered_L_level_without_chroma", lv_l), ("fix_round_1", lv_fr1),
                   ("all_pairs_candidate", lv_all)):
        img = _decode_png(AC.levelled_png(rad_bake, lv))
        tc = texture_chroma_arrays(img, rad, cls)
        tex[nm] = {"level": {k: lv.get(k) for k in ("gain", "gamma", "sat", "chroma_power", "chroma_hold", "chroma_hue")
                             if k in lv},
                   "ratio_by_radiance_C": tc["ratio_by_radiance_C"], "C_p90": tc["delivered"]["C_p90"],
                   "C_p50": tc["delivered"]["C_p50"], "radiance_C_p90": tc["radiance_stats"]["C_p90"]}
        if nm in ("delivered", "fix_round_1"):
            bk = WP5 / ("ft1119_bake" if nm == "delivered" else "pre_s6fr2_bake")
            ref = np.asarray(Image.open(bk / "print.png").convert("RGB"))
            tex[nm]["reproduces_print_png"] = {
                "bake": str(bk), "pixels_equal": bool(img.shape == ref.shape and np.array_equal(img, ref)),
                "print_png_sha256": hashlib.sha256((bk / "print.png").read_bytes()).hexdigest(),
                "pixels_sha256_recomputed": hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest(),
                "pixels_sha256_print_png": hashlib.sha256(np.ascontiguousarray(ref).tobytes()).hexdigest()}
    out["texture"] = tex
    prog("done", force=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bake", default=None)
    ap.add_argument("--before", default=None)
    for k in ("capture", "probe", "before-capture", "before-probe", "astra-capture", "astra-probe"):
        ap.add_argument(f"--{k}", default=None)
    ap.add_argument("--extra", action="append", default=[],
                    help="NAME=CAPTURE,PROBE[,BAKE|astra]: one more capture (e.g. an earlier level of the same bake)")
    ap.add_argument("--radiance", default=None, help="the level-free bake (section 7)")
    ap.add_argument("--workers", type=int, default=1, help="process-pool workers (size it to the free memory)")
    ap.add_argument("--provenance", default=None, help="write the fix-round-3 provenance JSON here (no --bake needed)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    opt_out_ecoqos()
    if a.provenance:
        res = provenance()
        Path(a.provenance).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps(res, indent=1)[:4000])
        if not a.bake:
            return 0
    if not a.bake:
        ap.error("--bake is required (or --provenance)")
    caps = {}
    for name, c, p, alb in (("painted", a.capture, a.probe, a.bake),
                            ("painted_before", a.before_capture, a.before_probe, a.before),
                            ("astra", a.astra_capture, a.astra_probe, "astra")):
        if c and p:
            caps[name] = (Path(c), Path(p), alb)
    for x in a.extra:
        name, rest = x.split("=", 1)
        parts = rest.split(",")
        caps[name] = (Path(parts[0]), Path(parts[1]), parts[2] if len(parts) > 2 else None)
    res = run(Path(a.bake), Path(a.before) if a.before else None, caps, Path(a.radiance) if a.radiance else None,
              workers=a.workers)
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    print(txt[:20000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
