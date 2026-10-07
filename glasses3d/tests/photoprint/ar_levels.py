"""WP5 correction round, task item 1: the AR render's frame levels against the photos' (graded by the independent
instrument ``tests/instruments/print_metrics.py``: ``lstar``, ``ar_frame_mask``, ``level_compare``).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_levels CAPTURE_DIR [CAPTURE_DIR ...]
        [--probe PROBE_DIR] [--probe-for CAPTURE_DIR=PROBE_DIR ...] [--pairs auto|pitch_p20:05,...]
        [--tradeoff auto|none|front:05,...] [--heldout auto|none|pitch_m20:05,...] [--out JSON]

AR frame pixels: an arview capture's (``arview capture --poses all --backgrounds skin`` or ``--poses default``)
part-id render of the pose, the print parts' palette colours (q001: Brow_Left, Brow_Right, Temple_Left,
Temple_Right), eroded 1 px, read from the pose's skin beauty render. Photo frame pixels: the same print parts rendered
(first hit of the whole scene, decals included) at the photo's WP3b camera, eroded 2 px (registration and
anti-aliasing), inside the photo's matte eroded 2 px.

**Pose-photo pairs (fix round 1 of the correction round).** The correction round fitted and graded the level on the
same two pairs (front vs photo 05, yaw -30 vs photo 01), both at zero elevation, while photos 05 and 01 are taken
18 deg from above; at the photos' own elevation (pitch +20 vs 05, yaw -30 pitch +20 vs 01: the review's held-out
pairs) the calibrated print rendered 5-6 L* darker than the photos. The pairs are now derived from the geometry:
each arview pose's camera direction in the model frame (Euler YXZ, poses.ts: yaw > 0 brings the -x side near,
pitch > 0 looks from above: (-sin yaw, cos yaw sin pitch, cos yaw cos pitch)) against each photo's WP3b camera
direction, each pose paired with its nearest photo.

**Roles (fix round 2 of the correction round).** Fix round 1 called every pair within 20 deg "matched" and calibrated
on all four; only two of them are at a photo's own camera. Now:
- ``photo_angle`` (within ``CLOSE_DEG``, 5 deg): the pose IS the photo's view: pitch_p20:05 (1.6 deg) and
  yaw_m30_pitch_p20:01 (1.6). These are the calibration pairs (role ``calibration``).
- ``near`` (within ``MATCH_DEG``, 20 deg): front:05 (18.4) and yaw_m30:01 (18.1), the zero-elevation views the photos
  were taken 18 deg above. They are graded every time as role ``trade-off``: the same photo seen from another
  direction, so their gap to the calibration rows is the view dependence (``view_dependence``), never a photo angle.
- ``loose`` (within ``LOOSE_DEG``, 30 deg): yaw_m45:01 (23.3), yaw_p45:03 (28.8), role ``held-out``.
``--pairs auto`` / ``--tradeoff auto`` / ``--heldout auto`` take them from the geometry; every verdict is reported per
role, never pooled. Per capture, ``view_dependence`` gives, for each photo with a calibration pair, every other pose's
residual minus the calibration pose's (per form; the photo side is the same photo, so in the ``frame`` and ``print``
forms it is the AR side's own change with the view), and the brows' and temples' AR L* (print-form pixels) apart.

Three forms, every one reported (the verdict of task item 1: AR p10 and p50 L* within 2 of the photo's):
- ``frame``: every frame pixel, unweighted (the form of the 10bf932 tool test's numbers);
- ``matched``: the photo's pixels weighted so brows and temples have the AR pose's shares (photo 05 shows the temple
  tops at a grazing angle, 1/3 of its frame pixels and much lighter, L* p50 41.5 against the brows' 22.0; the front pose
  hides them behind the head: 4 % of its frame pixels);
- ``print`` (the CALIBRATION TARGET): ``matched`` with highlights left out on both sides. Photo side: the photo's own
  achromatic pixels (L* > 35 and C*ab < 10: the studio's reflections, fixed per photo). AR side (fix round 1): the
  pixels a PROBE capture shows as achromatic highlights: the same GLB with every print-part base-colour image black
  (``ar_calibrate.black_probe``), captured at the same poses: the renderer's reflections alone, identical for every
  candidate print (the part-id renders must be identical, checked). The correction round took the AR side from the AR
  pixels' own colours and called it the renderer's: it moved with the print (front pose 42.1 % with the 10bf932
  print, 33.7 % with the calibrated one, 28.6 % with Astra's texture; the black probe 29.8 %), so light, grey print
  pixels left the target with the reflections. Without a probe the pixel rule is used and the output says so.
Sides are never named here; the pairs are the geometry's.

**Step 6 (2026-10-07): chroma and contrast beside the level.** Every form's ``level_compare`` now also carries
``appearance``: C*ab p50 / p90 / mean and the L* standard deviation of the same pixels with the same weights (AR -
photo, and the ratios; its own verdict: |C* p50 difference| <= 1, C* p90 and mean ratios 0.85-1.18 (fix round 1 of
step 6) and std ratio 0.85-1.18). Per pair, ``band_energy`` gives the
patch contrast at 0.5-3 mm (masked DoG of L*, ``print_metrics.band_energy``) per group, AR (probe highlights left out)
against the photo (its achromatic highlights left out), each at its own px/mm (the AR's from the capture's own
projection, ``capture_ppm``). ``chroma_residuals`` / ``chroma_units`` / ``contrast_ratios`` give them per pair like
``residuals``.

**Per group (fix round 2 of step 6).** The ``print`` form pools brows and temples, which can hide a part-wise miss at a
calibration pair (fix round 1's level at yaw -30 pitch +20 vs 01: brows alone L* p10 / p50 +0.91 / -2.79, temples
+4.46 / +2.00, pooled +1.66 / -1.62, a pass). Every row now carries ``by_group``: ``level_compare`` of each group alone in the print
form (AR: probe highlights out; photo: its achromatic pixels out), with its L* residuals, ``appearance`` (chroma,
contrast, hue) and verdict; ``pass_brow`` grades the brows alone per role (the region the owner's verdict names), and
``group_residuals`` / ``group_chroma_units`` give them per pair. The pooled ``print`` form stays the calibration target.

**Groups the photo barely shows (fix round 3 of step 6).** The ``matched`` and ``print`` forms weight the photo's
pixels so each group has the AR pose's share: a group the photo shows at a small share has each of its pixels
multiplied up. Fix round 2's held-out row yaw +45 vs photo 03 failed on exactly that: photo 03 (the -x side view)
shows the brows at 0.090 of its print-form pixels, edge-on (median |cos| between surface normal and camera 0.60,
against 0.81-0.97 for the brows of photos 01, 02, 04, 05) and 41 % of its brow frame pixels are the studio's
achromatic reflections, and the AR pose's share (0.486) weighted them x5.4, so half the row was graded on side-view
brow pixels (brows alone C* p90 ratio 0.606 against the temples' 0.914, inside the window); fix round 2 then read
that failure as the owner's -x temple complaint. Rule: a group whose share of the photo's print-form pixels
(``photo_group_shares``) is below ``MIN_PHOTO_GROUP_SHARE`` is reported (its ``by_group`` block is computed as before, with ``graded`` False
and the reason) but left out of both weighted forms (its AR and photo pixels alike), so of every pass / fail verdict
(``pass``, ``appearance``, ``pass_brow``) and of the chroma fit, which reads the ``print`` form
(``ar_calibrate.chroma_stats`` / ``chroma_units``); ``group_residuals`` / ``group_chroma_units`` give None for it.
The ``frame`` form (unweighted) is unchanged. The number, from the data (``group_share_table``; stored in
``data/wp5/step6_fixes/photo_group_shares.json``): the twelve photo-side shares are constants of the job (photo masks
and the photos' own highlights, independent of any print or capture): side views' brows 0.090 (photo 03) and 0.117
(photo 06), photo 05's temple tops 0.174, every other group 0.48-0.91. 0.15 lies between 0.117 and 0.174: it removes
the two side-view brow groups and keeps photo 05's temple tops, which photo 05 shows from its calibration pose's own
direction (1.6 deg; the head hides the arms in AR, hence the AR's 1.1 %) and which the instrument only ever weights
DOWN (x0.065 at pitch +20, x0.25 at the front). At the graded pairs every kept group is weighted by 0.06-1.51;
photo 03's brows were the only group weighted over 1.6. ``--min-photo-share 0`` reproduces fix round 2's instrument.
Every row records ``photo_group_share``, ``groups_excluded`` and the weight each group's photo pixels get
(``photo_weight``: AR share / photo share, print form).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d_host import raster
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.instruments import print_metrics as PM

PRINT_PARTS = tuple(IN.FT1119_PARTS)
GROUPS = {"brow": ("Brow_Left", "Brow_Right"), "temple": ("Temple_Left", "Temple_Right")}
PHOTO_ERODE = 2
FORMS = ("frame", "matched", "print")
TARGET = "print"
MATCH_DEG = 20.0                # within: role 'near' (graded as a view-dependence trade-off)
LOOSE_DEG = 30.0                # within: role 'loose' (held-out)
CLOSE_DEG = 5.0                 # within: role 'photo_angle', the pose IS the photo's view (the calibration pairs)
ROLE_OF = {"photo_angle": "calibration", "near": "trade-off", "loose": "held-out"}
ROLES = ("calibration", "trade-off", "held-out")
# arview's pose ids (arview/qa/capture/poses.ts DEFAULT_POSE_ANGLES + JUNCTION_POSE_ANGLES)
POSE_ANGLES = ((0, 0), (30, 0), (-30, 0), (45, 0), (-45, 0), (0, 20), (0, -20), (30, 20), (30, -20), (-30, 20),
               (-30, -20))
PREVIOUS_PAIRS = (("front", "05"), ("yaw_m30", "01"))      # the correction round's calibration pairs
MIN_PHOTO_GROUP_SHARE = 0.15    # fix round 3 of step 6: a group below this share of the photo's print-form pixels is
                                # reported, never graded (module docstring: side-view brows 0.090 / 0.117, next 0.174)
AR_TEMPLE_Z = (-104.0, -9.0)    # the temples' AR-visible stretch (model z, mm; the head hides the rest of the arm)
BRIDGE_MM = (0.0, 10.0, 5.0)    # capture_ppm's default point: the front, above the bridge
CACHE_KEY = "_cache"            # measure: the key of ``photos`` that holds the per-job cache (occlusion scene, masks)


def pose_id(yaw: float, pitch: float) -> str:
    sg = lambda v: f"{'m' if v < 0 else 'p'}{abs(int(v))}"
    parts = ([f"yaw_{sg(yaw)}"] if yaw else []) + ([f"pitch_{sg(pitch)}"] if pitch else [])
    return "_".join(parts) or "front"


def pose_direction(yaw: float, pitch: float) -> np.ndarray:
    """Unit direction from the glasses toward the camera, in the model frame, of an arview pose (module docstring)."""
    y, p = math.radians(yaw), math.radians(pitch)
    return np.array([-math.sin(y), math.cos(y) * math.sin(p), math.cos(y) * math.cos(p)])


def photo_direction(job, view: str) -> np.ndarray:
    t = np.asarray(raster.camera_basis(job.views[view].camera)[2], float)
    return t / np.linalg.norm(t)


def pair_table(job) -> list[dict]:
    """Per arview pose: the nearest photo, the angle between the two camera directions, and its role (photo_angle /
    near / loose / None)."""
    views = sorted(job.views)
    rows = []
    for yaw, pitch in POSE_ANGLES:
        d = pose_direction(yaw, pitch)
        ang = {v: math.degrees(math.acos(float(np.clip(d @ photo_direction(job, v), -1, 1)))) for v in views}
        v = min(ang, key=ang.get)
        role = ("photo_angle" if ang[v] <= CLOSE_DEG else "near" if ang[v] <= MATCH_DEG
                else "loose" if ang[v] <= LOOSE_DEG else None)
        rows.append({"pose": pose_id(yaw, pitch), "photo": IN.short_name(v), "angle_deg": round(ang[v], 1),
                     "role": role})
    return rows


def auto_pairs(job, role: str) -> tuple:
    """The (pose, photo) pairs of a ``pair_table`` role (photo_angle / near / loose)."""
    if role not in ROLE_OF:
        raise ValueError(f"unknown pair role {role!r} (one of {sorted(ROLE_OF)})")
    return tuple((r["pose"], r["photo"]) for r in pair_table(job) if r["role"] == role)


def _occ(job):
    from glasses3d.g3d_host.photoprint import project as PR
    from glasses3d.g3d_host.photoprint import texels as TX
    return TX.OcclusionScene(job, job.part_ids(PRINT_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))


def photo_frame_mask(job, view: str, occ=None, parts=PRINT_PARTS) -> np.ndarray:
    """(H, W) bool: the photo's pixels of ``parts`` (first hit at the WP3b camera, eroded) inside its matte (eroded)."""
    v = job.views[view]
    occ = occ or _occ(job)
    fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)["face_id"]
    nf = len(job.mesh.F)
    part = np.where((fid >= 0) & (fid < nf), job.mesh.part[np.clip(fid, 0, nf - 1)], -1)
    pr = ndimage.binary_erosion(np.isin(part, job.part_ids(list(parts))), iterations=PHOTO_ERODE)
    return pr & ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=PHOTO_ERODE)


def _cached(cache: dict | None, key, make):
    """``cache[key]``, made by ``make()`` the first time (no cache: made every time)."""
    if cache is None:
        return make()
    if key not in cache:
        cache[key] = make()
    return cache[key]


def photo_mask(job, view: str, parts, cache: dict | None = None) -> np.ndarray:
    """``photo_frame_mask`` of ``parts`` at ``view``, kept in ``cache`` (fix round 3 of step 6: the masks and the
    occlusion scene are constants of the job, and ``measure`` / ``band_energies`` rebuilt them for every row)."""
    occ = _cached(cache, "occ", lambda: _occ(job))
    return _cached(cache, ("photo_mask", view, tuple(parts)), lambda: photo_frame_mask(job, view, occ, parts))


def photo_highlights(job, view: str, cache: dict | None = None) -> np.ndarray:
    """(H, W) bool: the photo's own achromatic highlights (``print_metrics.achromatic_highlight``), in ``cache``."""
    def make():
        img = np.asarray(job.views[view].image)
        return PM.achromatic_highlight(img.reshape(-1, 3)).reshape(img.shape[:2])
    return _cached(cache, ("photo_highlights", view), make)


def photo_pixels(job, view: str, occ=None, cache: dict | None = None):
    """(pixels (N, 3), group label (N,)) of the photo's frame pixels, brows 0 / temples 1."""
    v = job.views[view]
    cache = {} if cache is None else cache
    if occ is not None:
        cache.setdefault("occ", occ)
    px, gr = [], []
    for k, (_g, parts) in enumerate(GROUPS.items()):
        m = photo_mask(job, view, parts, cache)
        px.append(v.image[m])
        gr.append(np.full(int(m.sum()), k))
    return np.concatenate(px), np.concatenate(gr)


def photo_group_shares(pp, gp) -> dict:
    """Fix round 3 of step 6: per group, its share of the photo's print-form pixels (the photo's own achromatic
    highlights left out, as the ``print`` form does). Constants of the job: they depend on the photo and its masks
    only, never on a print or a capture."""
    pp = np.asarray(pp, np.float64).reshape(-1, 3)
    gp = np.asarray(gp)
    keep = ~PM.achromatic_highlight(pp)
    n = max(int(keep.sum()), 1)
    return {g: round(float((keep & (gp == k)).sum() / n), 4) for k, g in enumerate(GROUPS)}


def excluded_groups(shares: dict, min_share: float = MIN_PHOTO_GROUP_SHARE) -> dict:
    """{group: reason} of the groups whose photo-side share is below ``min_share`` (reported, never graded)."""
    return {g: (f"the photo shows it at {s:.3f} of its print-form pixels, below MIN_PHOTO_GROUP_SHARE "
                f"{min_share:g}: reported, left out of every verdict and of the chroma fit")
            for g, s in shares.items() if s < min_share}


def part_points(job, cache: dict | None = None) -> dict:
    """Fix round 3 of step 6: per print part, the point its px/mm is measured at (``band_energies``): the area-weighted
    centroid of the part's faces, the temples' over their AR-visible stretch ``AR_TEMPLE_Z`` only (model mm)."""
    def make():
        V, F = job.mesh.V, job.mesh.F
        T3 = V[F]
        area = 0.5 * np.linalg.norm(np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0]), axis=1)
        cen = T3.mean(1)
        out = {}
        for part in PRINT_PARTS:
            m = np.isin(job.mesh.part, job.part_ids([part]))
            if part in GROUPS["temple"]:
                m &= (cen[:, 2] >= AR_TEMPLE_Z[0]) & (cen[:, 2] <= AR_TEMPLE_Z[1])
            w = area[m]
            out[part] = tuple(round(float(x), 3) for x in (cen[m] * w[:, None]).sum(0) / max(float(w.sum()), 1e-12))
        return out
    return _cached(cache, "part_points", make)


_SIDES: dict = {}


def part_sides(job) -> dict:
    """Fix round 3 of step 6: {'-x': (brow, temple, -1), '+x': (brow, temple, 1)}: each print part's side by the x sign
    of its area-weighted centroid (``part_points``), never by its name (the house rule; q001 names its parts by the
    viewer's side, which happens to agree); raises unless each side has exactly one brow and one temple. Which parts
    are brows or temples is the part class (``GROUPS``)."""
    hit = _SIDES.get(id(job))
    if hit is not None and hit[0] is job:
        return hit[1]
    pts = part_points(job)
    out = {}
    for sg, name in ((-1, "-x"), (1, "+x")):
        b = [p for p in GROUPS["brow"] if np.sign(pts[p][0]) == sg]
        t = [p for p in GROUPS["temple"] if np.sign(pts[p][0]) == sg]
        if len(b) != 1 or len(t) != 1:
            raise ValueError(f"side {name}: brows {b} and temples {t} by the x sign of their centroids {pts}; "
                             f"expected exactly one of each")
        out[name] = (b[0], t[0], sg)
    _SIDES[id(job)] = (job, out)
    return out


def _masks(cap: Path, pose: str):
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    return pid, [PM.ar_frame_mask(pid, rep["id_palette"], parts) for parts in GROUPS.values()]


def capture_pixels(cap: Path, pose: str, probe: Path | None = None):
    """(pixels, group label, highlight flags or None) of a capture's frame pixels at ``pose``; the flags are the
    probe capture's achromatic highlights on the same pixels (its part-id render must equal the capture's)."""
    cap = Path(cap)
    pid, masks = _masks(cap, pose)
    beauty = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB"))
    hl = None
    if probe is not None:
        ppid, pmasks = _masks(Path(probe), pose)
        if not (np.array_equal(ppid, pid) and all(np.array_equal(a, b) for a, b in zip(masks, pmasks))):
            raise ValueError(f"{probe}: part-id render at {pose} differs from {cap}'s: not a probe of the same GLB")
        pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB"))
        hl = np.concatenate([PM.achromatic_highlight(pb[m]) for m in masks])
    px = np.concatenate([beauty[m] for m in masks])
    gr = np.concatenate([np.full(int(m.sum()), k) for k, m in enumerate(masks)])
    return px, gr, hl


def ar_groups(pa, ga, ha) -> dict:
    """Per group (brow / temple): share and L* p10 / p50 of the AR's print-form pixels (highlights left out)."""
    keep = ~(np.asarray(ha, bool) if ha is not None else PM.achromatic_highlight(pa))
    out = {}
    for k, g in enumerate(GROUPS):
        m = keep & (np.asarray(ga) == k)
        L = PM.lstar(pa[m]) if m.any() else np.zeros(0)
        out[g] = {"share": round(float(m.sum() / max(int(keep.sum()), 1)), 4),
                  "p10": round(float(np.percentile(L, 10)), 2) if L.size else None,
                  "p50": round(float(np.percentile(L, 50)), 2) if L.size else None}
    return out


def capture_ppm(cap: Path, pose: str, at_mm=BRIDGE_MM, step_mm: float = 1.0) -> float:
    """The capture's px per model mm at ``at_mm`` (default the front, above the bridge), through the pose's
    asset_to_world and projection: the largest singular value of the 2 x 3 image Jacobian (the scale of displacements
    parallel to the image plane; a span along x alone is foreshortened at yaw). The temples are spread by the runtime
    (a turn about the hinge the report does not carry), so at a temple point this is the rigid model's depth; the
    spread's own change of depth is not measured (the mid-temple point lies 60 mm behind the bridge,
    ``part_points``)."""
    rep = json.loads((Path(cap) / "report.json").read_text(encoding="utf-8"))
    p = next(q for q in rep["poses"] if q["id"] == pose)
    M = np.asarray(p["asset_to_world"], float).reshape(4, 4).T
    P = np.asarray(p["camera"]["projection"], float).reshape(4, 4).T
    W, H = p["camera"]["width"], p["camera"]["height"]
    V = np.asarray(at_mm, float)[None] + np.vstack([np.zeros(3), np.eye(3) * step_mm])
    c = (P @ M @ np.c_[V / 1000.0, np.ones(len(V))].T).T
    xy = np.stack([(c[:, 0] / c[:, 3] + 1) / 2 * W, (1 - c[:, 1] / c[:, 3]) / 2 * H], 1)
    J = (xy[1:] - xy[0]).T / step_mm
    return float(np.linalg.svd(J, compute_uv=False)[0])


def _pooled_energy(rows: list) -> float | None:
    """One group's band energy from its parts' (energy, pixels) rows: the pixel-weighted RMS (pooled variance of the
    zero-mean band image); None when no part has one."""
    rows = [(e, n) for e, n in rows if e is not None and n > 0]
    if not rows:
        return None
    return float(math.sqrt(sum(n * e * e for e, n in rows) / sum(n for _e, n in rows)))


def band_energies(job, cap: Path, pose: str, short: str, probe: Path | None, occ=None,
                  cache: dict | None = None) -> dict:
    """Per group: patch contrast at 0.5-3 mm of the AR (print-form pixels: probe highlights out) and of the photo
    (its achromatic highlights out), and their ratio (step 6). Fix round 3 of step 6: each print part at its OWN px/mm
    (``part_points``: AR through the capture's projection, ``capture_ppm``; photo through its WP3b camera,
    ``inputs.ppm_local``), the group's energy the pixel-weighted RMS of its parts' (``_pooled_energy``). Step 6 measured
    both groups at the bridge's px/mm, which mis-scaled the temples' 0.5-3 mm band: in AR the mid-temple's px/mm is
    24-26 % below the bridge's at pitch +20 and the front, 34 % / 8 % below (far / near temple) at yaw -30 pitch +20,
    36 % below for the far temple and 2 % above for the near one at yaw +-45; in photo 03 the near temple's is 20 %
    above the pivot's (``data/wp5/step6_fixes/ar_levels_corrected.json``: ``ppm_ar`` against ``ppm_ar_bridge``)."""
    cache = {} if cache is None else cache
    if occ is not None:
        cache.setdefault("occ", occ)
    cap = Path(cap)
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    beauty = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB"))
    hl = np.zeros(pid.shape[:2], bool)
    if probe is not None:
        pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB"))
        hl = PM.achromatic_highlight(pb.reshape(-1, 3)).reshape(pb.shape[:2])
    view = f"reference-{short}.png"
    v = job.views[view]
    img = np.asarray(v.image)
    phl = photo_highlights(job, view, cache)
    pts = part_points(job, cache)
    La = PM.lstar(beauty.astype(np.float64))
    Lp = _cached(cache, ("photo_lstar", view), lambda: PM.lstar(img.astype(np.float64)))
    out = {"ppm_at": "each print part's own point (part_points: area-weighted centroid; temples over AR_TEMPLE_Z)",
           "ppm_ar": {}, "ppm_photo": {}, "ppm_ar_bridge": round(capture_ppm(cap, pose), 3),
           "ppm_photo_bridge": round(float(job.ppm(view)), 3), "parts": {}}
    for g, parts in GROUPS.items():
        ra, rp = [], []
        for part in parts:
            ma = PM.ar_frame_mask(pid, rep["id_palette"], (part,)) & ~hl
            mp = photo_mask(job, view, (part,), cache) & ~phl
            ppm_a = capture_ppm(cap, pose, pts[part])
            ppm_p = float(IN.ppm_local(v.camera, job.frame, np.asarray(pts[part], float)[None])[0])
            ea, ep = PM.band_energy(beauty, ma, ppm_a, L=La), PM.band_energy(img, mp, ppm_p, L=Lp)
            out["ppm_ar"][part], out["ppm_photo"][part] = round(ppm_a, 3), round(ppm_p, 3)
            out["parts"][part] = {"ar": None if ea is None else round(ea, 3), "ar_px": int(ma.sum()),
                                  "photo": None if ep is None else round(ep, 3), "photo_px": int(mp.sum())}
            ra.append((ea, int(ma.sum())))
            rp.append((ep, int(mp.sum())))
        ea, ep = _pooled_energy(ra), _pooled_energy(rp)
        out[g] = {"ar": None if ea is None else round(ea, 3), "photo": None if ep is None else round(ep, 3),
                  "ratio": None if (ea is None or not ep) else round(ea / ep, 3)}
    return out


def by_group(pa, ga, ha, pp, gp, excluded: dict | None = None) -> dict:
    """Fix round 2 of step 6: ``level_compare`` of each group (brow / temple) alone in the print form (AR: ``ha`` left
    out, else the AR pixels' own achromatic test; photo: its achromatic pixels left out); a group the AR pose does not
    show (fewer than 30 pixels on either side) is reported as such, never graded. Fix round 3: ``excluded``
    ({group: reason}, ``excluded_groups``): a group the photo barely shows is computed and reported but carries
    ``graded`` False and the reason, so no verdict counts it (``pass_brow``, ``group_residuals``)."""
    pa, pp = np.asarray(pa, np.float64), np.asarray(pp, np.float64)
    ga, gp = np.asarray(ga), np.asarray(gp)
    excluded = excluded or {}
    out = {}
    for k, g in enumerate(GROUPS):
        ma, mp = ga == k, gp == k
        if ma.sum() < 30 or mp.sum() < 30:
            out[g] = {"pass": None, "graded": False, "reason": f"{int(ma.sum())} AR / {int(mp.sum())} photo pixels"}
            continue
        r = PM.level_compare(pa[ma], pp[mp], exclude_highlights=True,
                             ar_highlight=None if ha is None else np.asarray(ha, bool)[ma])
        r["graded"] = g not in excluded
        if g in excluded:
            r["excluded"] = excluded[g]
        out[g] = r
    return out


def compare(ar, photo, min_photo_share: float = MIN_PHOTO_GROUP_SHARE) -> dict:
    """The three forms of one pair, the AR's group shares and the groups alone. Fix round 3 of step 6: a group below
    ``min_photo_share`` of the photo's print-form pixels is left out of the two weighted forms on both sides
    (``groups_excluded``) and reported ungraded in ``by_group``; ``photo_weight`` is the factor each group's photo
    pixels get in the ``print`` form without that rule (AR share / photo share)."""
    (pa, ga, ha), (pp, gp) = ar, photo
    pa, pp = np.asarray(pa, np.float64).reshape(-1, 3), np.asarray(pp, np.float64).reshape(-1, 3)
    ga, gp = np.asarray(ga), np.asarray(gp)
    shares = photo_group_shares(pp, gp)
    excl = excluded_groups(shares, min_photo_share)
    gone = [k for k, g in enumerate(GROUPS) if g in excl]
    ka, kp = ~np.isin(ga, gone), ~np.isin(gp, gone)
    hk = None if ha is None else np.asarray(ha, bool).reshape(-1)[ka]
    arg = ar_groups(pa, ga, ha)
    return {"frame": PM.level_compare(pa, pp),
            "matched": PM.level_compare(pa[ka], pp[kp], ar_group=ga[ka], photo_group=gp[kp]),
            "print": PM.level_compare(pa[ka], pp[kp], ar_group=ga[ka], photo_group=gp[kp], exclude_highlights=True,
                                      ar_highlight=hk),
            "ar_groups": arg, "photo_group_share": shares,
            "photo_weight": {g: (round(arg[g]["share"] / shares[g], 3) if shares[g] > 0 else None) for g in GROUPS},
            "min_photo_group_share": min_photo_share, "groups_excluded": excl,
            "by_group": by_group(pa, ga, ha, pp, gp, excl)}


def pass_brow(rows: dict) -> dict:
    """Fix round 2 of step 6: per role, the brows alone (print form, ``by_group``) pass at every row of the role.
    A missing render fails the role. Fix round 3: a row whose brow group is not graded (the pose shows fewer than 30
    brow pixels, or the photo shows the brows below ``MIN_PHOTO_GROUP_SHARE``) is left out (its numbers stay in
    ``by_group``); a role where no row grades the brows is None, never a vacuous pass."""
    out = {}
    for role in ROLES:
        rr = [r for r in rows.values() if isinstance(r, dict) and r.get("role") == role]
        if any("missing" in r for r in rr):
            out[role] = False
            continue
        graded = [(r.get("by_group") or {}).get("brow") or {} for r in rr]
        graded = [b for b in graded if b.get("graded", "pass" in b and b.get("pass") is not None)]
        out[role] = None if not graded else all(bool(b.get("pass")) for b in graded)
    return out


def view_dependence(rows: dict) -> dict:
    """For each photo with a calibration pair: every other graded pose's residual minus the calibration pose's
    ([p10, p50] per form) and its AR brow / temple L* p50 minus the calibration pose's. The photo is the same, so in
    the ``frame`` and ``print`` forms this is the AR render's own change with the view (``matched`` re-weights the
    photo to each pose's shares)."""
    graded = {k: r for k, r in rows.items() if isinstance(r, dict) and r.get("role") in ROLES and "missing" not in r}
    out = {}
    for kc, rc in graded.items():
        if rc["role"] != "calibration":
            continue
        pc, s = kc.split("_vs_")
        for k, r in graded.items():
            p, s2 = k.split("_vs_")
            if s2 != s or k == kc:
                continue
            out[f"{p} - {pc} (vs {s})"] = {
                "role": r["role"],
                **{f: [round(r[f]["ar_minus_photo"][q] - rc[f]["ar_minus_photo"][q], 2) for q in ("p10", "p50")]
                   for f in FORMS},
                "ar_group_p50": {g: (round(r["ar_groups"][g]["p50"] - rc["ar_groups"][g]["p50"], 2)
                                     if r["ar_groups"][g]["p50"] is not None and rc["ar_groups"][g]["p50"] is not None
                                     else None) for g in GROUPS}}
    return out


def key(pose: str, short: str) -> str:
    return f"{pose}_vs_{short}"


def measure(captures, pairs=None, heldout=None, job=None, probes: dict | None = None, photos: dict | None = None,
            tradeoff=None, cache: dict | None = None, min_photo_share: float = MIN_PHOTO_GROUP_SHARE) -> dict:
    """Rows per capture and pair (``pairs``: role 'calibration', default the photo_angle pairs of ``pair_table``;
    ``tradeoff``: role 'trade-off', default its near pairs; ``heldout``: role 'held-out', default its loose pairs).
    ``probes``: {capture dir: probe dir} (str keys). ``photos``: a cache of ``photo_pixels`` by photo short name
    (filled here); fix round 3 of step 6: its key ``CACHE_KEY`` (or ``cache`` when given) also keeps the occlusion
    scene, the photo-side masks and highlight maps and the part points, constants of the job that were rebuilt for every
    row and every capture, so a caller that keeps ``photos`` across calls (``ar_calibrate``) builds them once.
    ``min_photo_share``: the photo-side group share below which a group is reported, never graded (``compare``; 0 =
    fix round 2's instrument)."""
    job = job or IN.load_job()
    table = pair_table(job)
    pairs = tuple(map(tuple, pairs)) if pairs is not None else auto_pairs(job, "photo_angle")
    tradeoff = tuple(map(tuple, tradeoff)) if tradeoff is not None else auto_pairs(job, "near")
    heldout = tuple(map(tuple, heldout)) if heldout is not None else auto_pairs(job, "loose")
    angle = {(r["pose"], r["photo"]): r["angle_deg"] for r in table}
    photos = {} if photos is None else photos
    cache = photos.setdefault(CACHE_KEY, {}) if cache is None else cache
    for _p, s in pairs + tradeoff + heldout:
        if s not in photos:
            photos[s] = photo_pixels(job, f"reference-{s}.png", cache=cache)
    probes = {str(Path(k)): v for k, v in (probes or {}).items()}
    out = {"pairs": [list(p) for p in pairs], "tradeoff": [list(p) for p in tradeoff],
           "heldout": [list(p) for p in heldout], "pair_table": table, "close_deg": CLOSE_DEG,
           "match_deg": MATCH_DEG, "loose_deg": LOOSE_DEG, "tol": PM.LEVEL_TOL, "target_form": TARGET,
           "groups": list(GROUPS), "min_photo_group_share": min_photo_share,
           "photo_frame_stats": {s: PM.level_stats(photos[s][0]) for _p, s in pairs + tradeoff + heldout},
           "photo_group_share": {s: photo_group_shares(*photos[s]) for _p, s in pairs + tradeoff + heldout},
           "captures": {}}
    for cap in captures:
        cap = Path(cap)
        probe = probes.get(str(cap))
        rows = {}
        for role, plist in (("calibration", pairs), ("trade-off", tradeoff), ("held-out", heldout)):
            for pose, short in plist:
                if not (cap / f"{pose}__part-id.png").is_file():
                    rows[key(pose, short)] = {"role": role, "missing": f"no {pose} render in {cap}"}
                    continue
                r = compare(capture_pixels(cap, pose, Path(probe) if probe else None), photos[short], min_photo_share)
                r["band_energy"] = band_energies(job, cap, pose, short, Path(probe) if probe else None, cache=cache)
                r["role"] = role
                r["angle_deg"] = angle.get((pose, short))
                rows[key(pose, short)] = r
        vd = view_dependence(rows)
        rows["pass"] = {role: {f: all(r[f]["pass"] if f in r else False      # a missing render never passes
                                      for r in rows.values() if r.get("role") == role)
                               for f in FORMS} for role in ROLES}
        rows["pass_brow"] = pass_brow(rows)      # fix round 2 of step 6: the brows alone, print form, per role
        rows["view_dependence"] = vd
        rows["probe"] = str(probe) if probe else None
        out["captures"][str(cap)] = rows
    return out


def chroma_residuals(row: dict, form: str = TARGET, pairs=None) -> list[float]:
    """[C*ab p50 AR - photo] per pair (step 6), in ``pairs`` order (default: the row's calibration pairs)."""
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == "calibration"]
    return [row[key(p, s)][form]["appearance"]["chroma_ar_minus_photo"]["p50"] for p, s in pairs]


def chroma_units(row: dict, form: str = TARGET, pairs=None) -> list[float]:
    """[C*ab p50, p90, mean] per pair in units of the instrument's own windows (``print_metrics.chroma_units``: |x| <= 1
    passes), in ``pairs`` order (fix round 1 of step 6)."""
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == "calibration"]
    return [u for p, s in pairs for u in row[key(p, s)][form]["appearance"]["chroma_units"]]


def _pairs_of(row: dict, pairs=None, role: str = "calibration"):
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == role]
    return pairs


def _graded(g: dict) -> bool:
    """Whether a ``by_group`` block is graded (rows written before fix round 3 carry no flag: graded when computed)."""
    return bool(g.get("graded", "ar_minus_photo" in g))


def group_residuals(row: dict, group: str = "brow", pairs=None) -> list:
    """Fix round 2 of step 6: [p10, p50] AR - photo of one group alone (print form, ``by_group``) per pair; None where
    the pose does not show the group or (fix round 3) the photo shows it below ``MIN_PHOTO_GROUP_SHARE``."""
    out = []
    for p, s in _pairs_of(row, pairs):
        g = row[key(p, s)].get("by_group", {}).get(group, {})
        out += [g["ar_minus_photo"][q] if ("ar_minus_photo" in g and _graded(g)) else None for q in ("p10", "p50")]
    return out


def group_chroma_units(row: dict, group: str = "brow", pairs=None) -> list:
    """Fix round 2 of step 6: [C* p50, p90, mean] units of one group alone per pair (None where not shown, or, fix
    round 3, not graded)."""
    out = []
    for p, s in _pairs_of(row, pairs):
        g = row[key(p, s)].get("by_group", {}).get(group, {})
        out += list(g["appearance"]["chroma_units"]) if ("appearance" in g and _graded(g)) else [None] * 3
    return out


def contrast_ratios(row: dict, form: str = TARGET, pairs=None) -> list[float]:
    """[AR / photo L* standard deviation] per pair (step 6)."""
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == "calibration"]
    return [row[key(p, s)][form]["appearance"]["lstar_std_ratio"] for p, s in pairs]


def residuals(row: dict, form: str = TARGET, pairs=None) -> list[float]:
    """[p10, p50] AR - photo per pair, in ``pairs`` order (default: the row's calibration pairs in insertion order)."""
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == "calibration"]
    return [row[key(p, s)][form]["ar_minus_photo"][q] for p, s in pairs for q in ("p10", "p50")]


def table(res: dict, names: dict | None = None) -> str:
    """A plain-text table: per capture and pair, AR - photo L* p10 / p50 in every form (and the highlight shares),
    then the view dependence (other pose minus the photo-angle pose, same photo)."""
    lines = []
    for cap, rows in res["captures"].items():
        lines.append(f"== {names.get(cap, cap) if names else cap}  (probe: {rows.get('probe')})")
        for k, r in rows.items():
            if not isinstance(r, dict) or "role" not in r or "missing" in r:
                continue
            cells = [f"{f} {r[f]['ar_minus_photo']['p10']:+.2f}/{r[f]['ar_minus_photo']['p50']:+.2f}" for f in FORMS]
            hs = r["print"].get("highlight_share") or {}
            ap = r["print"].get("appearance") or {}
            be = r.get("band_energy") or {}
            lines.append(f"  {r['role']:<11} {k:<26} ({r['angle_deg']} deg)  " + "  ".join(cells)
                         + f"  hl ar {hs.get('ar')} photo {hs.get('photo')}"
                         + (f"  | print C* p50 {ap['ar_chroma']['p50']}/{ap['photo_chroma']['p50']} p90 "
                            f"{ap['ar_chroma']['p90']}/{ap['photo_chroma']['p90']} mean {ap['ar_chroma']['mean']}/"
                            f"{ap['photo_chroma']['mean']} std ratio {ap['lstar_std_ratio']}" if ap else "")
                         + (f"  energy 0.5-3mm brow {be.get('brow', {}).get('ratio')} temple "
                            f"{be.get('temple', {}).get('ratio')}" if be else ""))
            hu = ap.get("hue") if ap else None
            if hu:
                lines.append(f"  {'':<11} {'':<26} hue p50 {hu['ar']['p50']}/{hu['photo']['p50']} magenta "
                             f"{hu['ar']['magenta_share']}/{hu['photo']['magenta_share']} off 20-100 "
                             f"{hu['ar']['off_band_share']}/{hu['photo']['off_band_share']}  appearance pass "
                             f"{ap.get('pass')} {[k for k, v in ap.get('criteria', {}).items() if not v]}")
            if r.get("photo_group_share") is not None:
                lines.append(f"  {'':<11} {'':<26} photo group share {r['photo_group_share']} weight "
                             f"{r.get('photo_weight')} excluded {sorted(r.get('groups_excluded') or {})}")
            for g, gr in (r.get("by_group") or {}).items():
                if "ar_minus_photo" not in gr:
                    lines.append(f"  {'':<11} {'':<26} {g}: {gr.get('reason')}")
                    continue
                ga_ = gr["appearance"]
                lines.append(f"  {'':<11} {'':<26} {g} alone{'' if _graded(gr) else ' (NOT GRADED)'}: L* "
                             f"{gr['ar_minus_photo']['p10']:+.2f}/"
                             f"{gr['ar_minus_photo']['p50']:+.2f} pass {gr['pass']} | C* p50 "
                             f"{ga_['ar_chroma']['p50']}/{ga_['photo_chroma']['p50']} p90 {ga_['ar_chroma']['p90']}/"
                             f"{ga_['photo_chroma']['p90']} mean {ga_['ar_chroma']['mean']}/{ga_['photo_chroma']['mean']} "
                             f"units {ga_['chroma_units']} std ratio {ga_['lstar_std_ratio']}")
        for k, v in (rows.get("view_dependence") or {}).items():
            cells = [f"{f} {v[f][0]:+.2f}/{v[f][1]:+.2f}" for f in FORMS]
            lines.append(f"  view dep.   {k:<40} " + "  ".join(cells) + f"  AR p50 by group {v['ar_group_p50']}")
    return "\n".join(lines)


def _pairs_arg(s: str | None, job, role: str):
    if s is None or s == "auto":
        return auto_pairs(job, role)
    if s == "none":
        return ()
    return tuple(tuple(x.split(":")) for x in s.split(","))


def group_share_table(job, cache: dict | None = None) -> dict:
    """Fix round 3 of step 6, the data behind ``MIN_PHOTO_GROUP_SHARE``: per photo and group, its frame pixels, the
    photo's achromatic highlights among them, the print-form pixels and their share, each part's own (eroded) pixel
    count, and the median |cos| between the surface normal and the photo's camera direction (1 = face on, 0 = edge
    on). Constants of the job (no print, no capture)."""
    cache = {} if cache is None else cache
    out = {"min_photo_group_share": MIN_PHOTO_GROUP_SHARE, "photos": {}}
    occ = _cached(cache, "occ", lambda: _occ(job))
    nf = len(job.mesh.F)
    for view in sorted(job.views):
        v = job.views[view]
        t = np.asarray(raster.camera_basis(v.camera)[2], float)
        t = t / np.linalg.norm(t)
        fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)["face_id"]
        f = np.clip(fid, 0, nf - 1)
        hl = photo_highlights(job, view, cache)
        rows = {}
        for g, parts in GROUPS.items():
            m = photo_mask(job, view, parts, cache)
            cosv = np.abs(job.face_normals[f[m]] @ t)
            rows[g] = {"frame_px": int(m.sum()), "achromatic_highlight_px": int((m & hl).sum()),
                       "print_form_px": int((m & ~hl).sum()),
                       "median_abs_cos_view": round(float(np.median(cosv)), 3) if m.any() else None,
                       "part_px": {p: int(photo_mask(job, view, (p,), cache).sum()) for p in parts}}
        n = max(sum(r["print_form_px"] for r in rows.values()), 1)
        for g, r in rows.items():
            r["share"] = round(r["print_form_px"] / n, 4)
            r["excluded"] = bool(r["share"] < MIN_PHOTO_GROUP_SHARE)
        out["photos"][IN.short_name(view)] = rows
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("captures", nargs="*")
    ap.add_argument("--probe", default=None, help="the black-albedo probe capture of the captures' GLB")
    ap.add_argument("--probe-for", action="append", default=[], help="CAPTURE_DIR=PROBE_DIR (overrides --probe)")
    ap.add_argument("--pairs", default="auto", help="calibration pairs (auto: the photo_angle pairs)")
    ap.add_argument("--tradeoff", default="auto", help="trade-off pairs (auto: the near pairs, 18 deg off)")
    ap.add_argument("--heldout", default="auto", help="held-out pairs (auto: the loose pairs)")
    ap.add_argument("--min-photo-share", type=float, default=MIN_PHOTO_GROUP_SHARE,
                    help="photo-side group share below which a group is reported, never graded (0: fix round 2's "
                         "instrument)")
    ap.add_argument("--share-table", default=None, help="write group_share_table (the data behind the minimum) here")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos
    opt_out_ecoqos()
    prog = Progress("ar_levels")
    job = IN.load_job()
    if a.share_table:
        Path(a.share_table).write_text(json.dumps(group_share_table(job), indent=1), encoding="utf-8")
        print(f"wrote {a.share_table}")
    if not a.captures:
        return 0
    probes = {c: a.probe for c in a.captures} if a.probe else {}
    probes.update(dict(x.split("=", 1) for x in a.probe_for))
    prog(f"grading {len(a.captures)} capture(s)", force=True)
    with prog.heartbeat():           # fix round 3 of step 6: never silent for long (a line at least every 30 s)
        res = measure(a.captures, _pairs_arg(a.pairs, job, "photo_angle"), _pairs_arg(a.heldout, job, "loose"),
                      job=job, probes=probes, tradeoff=_pairs_arg(a.tradeoff, job, "near"),
                      min_photo_share=a.min_photo_share)
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    print(table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
